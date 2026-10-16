from __future__ import annotations

from project_registry_core import project_review

import sqlite3
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from project_registry_core.persistence import database as registry_database
from project_registry_core.transport import http_transport
from project_registry_core.identity import identifiers as identifier_core
from project_registry_core import project_merge
from ..persistence import identifier_storage
from .. import registry as core
from ..persistence import concurrency
from ..persistence import runtime_view
from project_registry_core import project_record
from .. import collection_trust
from ..publications import lexicon_types, publication_registry


def inspect_pair(
    conn: sqlite3.Connection,
    *,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
    target_project_key: str | None = None,
    target_seed: str | None = None,
    target_seed_type: str | None = None,
    include_first_creator_details: bool = False,
) -> dict[str, Any]:
    source = project_record.ensure_project_shape(core.resolve_single_project(conn, project_key=project_key, seed=seed, seed_type=seed_type))
    target = project_record.ensure_project_shape(core.resolve_single_project(conn, project_key=target_project_key, seed=target_seed, seed_type=target_seed_type))
    if source["project_key"] == target["project_key"]:
        raise ValueError("source and target resolve to the same project")
    schema_ids: set[str] = set()
    for project in (source, target):
        schema_ids.update(project_collection_nsids(project) | project_non_record_nsids(project))
        schema_ids.update(item["schema_id"] for item in project.get("publication_schemas", []))
        schema_ids.update(str(item.get("input_nsid") or "") for item in project.get("lexicon_resolution_entries", []))
    current = publication_registry.schemas(conn, now=project_record.iso_now(), schema_ids=schema_ids)
    return inspect_merge_pair_payload(
        source,
        target,
        include_first_creator_details=include_first_creator_details,
        current_publications=current,
    )


def merge_projects(
    conn: sqlite3.Connection,
    *,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
    target_project_key: str | None = None,
    target_seed: str | None = None,
    target_seed_type: str | None = None,
    use_recommendation: bool = False,
    expected_recommendation_target: str | None = None,
    execute: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    source = project_record.ensure_project_shape(core.resolve_single_project(conn, project_key=project_key, seed=seed, seed_type=seed_type))
    resolve_recommendation = use_recommendation or not any([target_project_key, target_seed])
    if resolve_recommendation:
        source["duplicate_candidates"] = project_merge.compute_duplicate_candidates(conn, source)
        source["merge_recommendation"] = project_merge.compute_merge_recommendation(source)
        recommendation = source.get("merge_recommendation") or {}
        resolved_target_key = str(recommendation.get("target_project_key") or "")
        if not resolved_target_key:
            raise ValueError("no merge recommendation available for source project")
        if expected_recommendation_target and resolved_target_key != expected_recommendation_target:
            raise ValueError("merge recommendation changed; reload the project before applying it")
        if execute and not recommendation.get("auto_merge_ready") and not force:
            raise ValueError("merge recommendation is not auto-merge-ready; rerun without --execute or pass --force")
        target = core.resolve_single_project(conn, project_key=resolved_target_key)
    else:
        target = core.resolve_single_project(conn, project_key=target_project_key, seed=target_seed, seed_type=target_seed_type)
    target = project_record.ensure_project_shape(target)
    if source["project_key"] == target["project_key"]:
        raise ValueError("source and target resolve to the same project")
    if project_merge.merge_is_blocked(source, target) and not force:
        raise ValueError("merge is manually blocked for this project pair; pass force only to override that guard")
    current_claims = {
        (item.kind, item.value, item.role)
        for project in (source, target)
        for item in runtime_view.project_identifiers(project)
    }
    for row in conn.execute(
        "SELECT kind, value, role FROM project_noncurrent_identifiers WHERE project_key IN (?, ?)",
        (source["project_key"], target["project_key"]),
    ):
        if (row["kind"], row["value"], row["role"]) in current_claims:
            raise ValueError(f"merge conflicts with a noncurrent identifier: {row['kind']} {row['value']} ({row['role']}); resolve the identifier standing first")
    merged = merge_project_records(target, source)
    merged["separate_project_keys"] = [
        key for key in merged.get("separate_project_keys", [])
        if key not in {source["project_key"], target["project_key"]}
    ]
    next_merge_review: dict[str, str] = {}
    if execute:
        comparison_projects = [
            project_record.ensure_project_shape(item)
            for item in project_record.list_projects(conn)
            if str(item.get("project_key") or "")
            not in {source["project_key"], target["project_key"]}
        ]
        comparison_projects.append(merged)
        duplicate_profiles, duplicate_index = project_merge.build_duplicate_candidate_index(comparison_projects)
        merged["duplicate_candidates"] = project_merge.compute_duplicate_candidates(
            conn,
            merged,
            projects=comparison_projects,
            duplicate_profiles=duplicate_profiles,
            duplicate_index=duplicate_index,
        )
        merged["merge_recommendation"] = project_merge.compute_merge_recommendation(merged)
        next_target_key = str(merged["merge_recommendation"].get("target_project_key") or "")
        next_target = next(
            (
                item
                for item in comparison_projects
                if str(item.get("project_key") or "") == next_target_key
            ),
            None,
        )
        if next_target is not None:
            next_source, next_target = project_merge.choose_batch_merge_direction(merged, next_target)
            next_merge_review = {
                "source_project_key": str(next_source.get("project_key") or ""),
                "target_project_key": str(next_target.get("project_key") or ""),
            }
    merged = project_review.apply_promotion_state(merged)
    summary = {
        "mode": "execute" if execute else "dry_run",
        "source_project_key": source["project_key"],
        "target_project_key": target["project_key"],
        "used_recommendation": resolve_recommendation,
        "source_name": source.get("name", ""),
        "target_name": target.get("name", ""),
        "merged_name": merged.get("name", ""),
        "merged_website_url": merged.get("website_url", ""),
        "merged_service_urls": merged.get("service_urls", []),
        "merged_tagline": merged.get("tagline", ""),
        "merged_summary": merged.get("summary", ""),
        "merged_promotion_priority": project_review.active_promotion_priority_adjustment(merged),
        "merged_tagline_is_provisional": merged.get("tagline_is_provisional", False),
        "merged_service_status": merged.get("service_status", ""),
        "merged_project_repository_urls": merged.get("project_repository_urls", []),
        "introduced": bool(merged.get("introduced_url")),
        "introduced_url": merged.get("introduced_url", ""),
        "merge_recommendation": source.get("merge_recommendation", {}),
        "remaining_merge_candidate_count": len(merged.get("duplicate_candidates", [])),
        "next_merge_review": next_merge_review,
    }
    if execute:
        with concurrency.write_transaction(conn):
            concurrency.check_projects(conn, [source, target], existing_aliases={source["project_key"]: target["project_key"]})
            identifier_storage.merge_project_storage(
                conn,
                str(source["project_key"]),
                str(target["project_key"]),
            )
            # Inputs were checked under this writer lock. Only our own storage
            # transfer has advanced the target revision before the shared save.
            merged[runtime_view.RUNTIME_PROJECT_REVISION] = concurrency.revision(conn, str(target["project_key"]))
            project_record.save_project(
                conn,
                merged,
                enriched=bool(merged.get("last_enriched_at")),
                preserve_timestamps=True,
                commit=False,
            )
            register_project_merge_alias(
                conn,
                source_project_key=source["project_key"],
                target_project_key=target["project_key"],
            )
            delete_project(conn, source["project_key"])
    return summary


def split_identifier(
    conn: sqlite3.Connection,
    *,
    identifier_type: str,
    value: str,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
    note: str = "",
    execute: bool = False,
) -> dict[str, Any]:
    source = project_record.ensure_project_shape(core.resolve_single_project(conn, project_key=project_key, seed=seed, seed_type=seed_type))
    identifier_value = project_record.canonical_seed_value(identifier_type, value)
    if not identifier_value:
        raise ValueError("identifier value is required")
    if identifier_type == "domain":
        return split_project_authority_domain(
            conn,
            source["project_key"],
            identifier_value,
            note=note,
            execute=execute,
        )
    identifier_kind = {"nsid": "nsid", "service_url": "service_url", "repo_url": "repo_url"}.get(identifier_type)
    belongs_to_source = bool(identifier_kind and conn.execute(
        "SELECT 1 FROM project_identifiers WHERE project_key = ? AND kind = ? AND value = ? LIMIT 1",
        (source["project_key"], identifier_kind, identifier_value),
    ).fetchone())
    if not belongs_to_source:
        raise ValueError("identifier does not currently belong to the selected source project")
    target_key = project_record.allocate_new_project_key(conn)
    target = project_record.default_project_record(target_key, identifier_type, identifier_value)
    if identifier_type == "nsid":
        target["resolved_nsids"] = []
    target["tracking_state"] = source.get("tracking_state", "active")
    target["status"] = source.get("status", "draft")
    target["discovery_sources"] = project_record.unique_list(list(source.get("discovery_sources", [])) + ["split_from_project"])
    target["tracking_note"] = f"split from {source['project_key']}"
    if identifier_type == "nsid":
        source_confirmed = identifier_value in source.get("resolved_nsids", [])
        field = "resolved_nsids" if source_confirmed else "input_nsids"
        target[field] = project_record.add_unique(target.get(field, []), identifier_value)
    elif identifier_type == "service_url":
        core.confirm_service_seed(target, identifier_value, source="split")
    elif identifier_type == "repo_url":
        core.confirm_repo_seed(target, identifier_value, source="split")
    target = core.finalize_project_shape(target)
    blocked = project_record.normalize_blocked_identifiers(list(source.get("blocked_identifiers", [])))
    block_key = (identifier_type, identifier_value)
    if not any((str(item.get("type") or ""), str(item.get("value") or "")) == block_key for item in blocked):
        blocked.append({
            "type": identifier_type,
            "value": identifier_value,
            "note": note.strip() or "split out to another project",
            "added_at": project_record.iso_now(),
        })
    source["blocked_identifiers"] = blocked
    core.remove_identifier_from_project(source, identifier_type, identifier_value)
    source["separate_project_keys"] = project_record.unique_list(list(source.get("separate_project_keys", [])) + [target["project_key"]])
    target["separate_project_keys"] = project_record.unique_list(list(target.get("separate_project_keys", [])) + [source["project_key"]])
    note_line = f"split out {identifier_type}:{identifier_value} -> {target['project_key']}"
    if note:
        note_line = f"{note_line} ({note.strip()})"
    project_record.append_system_note(source, note_line)
    source = core.finalize_project_shape(source)
    for item in (source, target):
        item["duplicate_candidates"] = project_merge.compute_duplicate_candidates(conn, item)
        item["merge_recommendation"] = project_merge.compute_merge_recommendation(item)
    summary = {
        "mode": "execute" if execute else "dry_run",
        "source_project_key": source["project_key"],
        "target_project_key": target["project_key"],
        "identifier_type": identifier_type,
        "identifier_value": identifier_value,
        "source_name": source.get("name", ""),
        "target_name": target.get("name", ""),
        "source_seed_type": source.get("seed_type", ""),
        "source_seed_value": source.get("seed_value", ""),
        "target_seed_type": target.get("seed_type", ""),
        "target_seed_value": target.get("seed_value", ""),
        "source_blocked_identifiers": source.get("blocked_identifiers", []),
        "source_input_nsids": source.get("input_nsids", []),
        "source_service_urls": source.get("service_urls", []),
        "source_project_repository_urls": source.get("project_repository_urls", []),
        "target_input_nsids": target.get("input_nsids", []),
        "target_service_urls": target.get("service_urls", []),
        "target_project_repository_urls": target.get("project_repository_urls", []),
    }
    if execute:
        with concurrency.write_transaction(conn):
            concurrency.check_projects(conn, [source, target])
            project_record.save_project(
                conn,
                source,
                enriched=bool(source.get("last_enriched_at")),
                preserve_timestamps=True,
                commit=False,
            )
            project_record.save_project(conn, target, enriched=False, preserve_timestamps=True, commit=False)
    return summary


BSKY_SYNC_API = "https://bsky.network"


_COLLECTION_FIRST_CREATOR_DID_CACHE: dict[str, str | None] = {}


def fetch_collection_first_creator_did(collection: str) -> str:
    normalized = identifier_core.normalize_nsid(collection)
    if not normalized:
        return ""
    if normalized in _COLLECTION_FIRST_CREATOR_DID_CACHE:
        return str(_COLLECTION_FIRST_CREATOR_DID_CACHE[normalized] or "")
    params = urllib.parse.urlencode({"collection": normalized, "limit": 1})
    payload = http_transport.request_json(f"{BSKY_SYNC_API}/xrpc/com.atproto.sync.listReposByCollection?{params}")
    repos = payload.get("repos", []) if isinstance(payload, dict) else []
    did = ""
    if isinstance(repos, list):
        for item in repos:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("did") or "").strip()
            if candidate:
                did = candidate
                break
    _COLLECTION_FIRST_CREATOR_DID_CACHE[normalized] = did or None
    return did


def project_collection_nsids(project: dict[str, Any]) -> set[str]:
    collections: set[str] = set()
    for value in list(project.get("input_nsids", [])) + list(project.get("resolved_nsids", [])) + list(project.get("lexicons", [])):
        normalized = identifier_core.normalize_nsid(str(value or ""))
        if normalized and project_record.namespace_from_collection_nsid(normalized):
            collections.add(normalized)
    for sample in project.get("recent_record_samples", []):
        if not isinstance(sample, dict):
            continue
        normalized = identifier_core.normalize_nsid(str(sample.get("collection") or ""))
        if normalized and project_record.namespace_from_collection_nsid(normalized):
            collections.add(normalized)
    return collections - project_non_record_nsids(project)


def project_non_record_nsids(project: dict[str, Any]) -> set[str]:
    """Unknown legacy types remain eligible; explicit non-records do not."""
    non_records: set[str] = set()
    def observe(nsid: Any, kind: Any) -> None:
        if kind in (lexicon_types.PRIMARY_TYPES - {"record"}) | {"definitions"}:
            non_records.add(identifier_core.normalize_nsid(str(nsid or "")))
    for item in project.get("publication_schemas", []):
        observe(item.get("schema_id"), item.get("schema_type"))
    for item in project.get("lexicon_schema_samples", []):
        schema = item.get("schema") if isinstance(item.get("schema"), dict) else item
        observe(item.get("id") or item.get("nsid") or schema.get("id"), lexicon_types.schema_type(schema))
    for item in project.get("lexicon_resolution_entries", []):
        observe(item.get("input_nsid") or item.get("resolved_nsid"), (item.get("publication_validation") or {}).get("schema_type"))
    return non_records


def project_lexicon_publishers(project: dict[str, Any]) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
    """Keep publication witnesses separate from authenticated publisher matches."""
    verified = collection_trust.verified_publications(project)
    mapping = {sid: str(entry["repo_did"]) for sid, entry in verified.items()}
    details: dict[str, list[dict[str, Any]]] = {}
    latest: dict[str, dict[str, Any]] = {}
    for entry in project.get("lexicon_resolution_entries", []):
        sid = str(entry.get("input_nsid") or entry.get("resolved_nsid") or "")
        if sid and str(entry.get("resolved_at") or "") >= str(latest.get(sid, {}).get("resolved_at") or ""):
            latest[sid] = entry
    for sid, entry in latest.items():
        details[sid] = [{"did": entry.get("repo_did", ""), "source": "lexicon_resolution",
                         "record_uri": entry.get("record_uri", ""), "observed_at": entry.get("resolved_at", ""),
                         "status": "validated_observation" if sid in verified else "unverified",
                         "included_in_comparison": sid in verified,
                         "publication_validation": entry.get("publication_validation", {})}]
    for item in project.get("publication_schemas", []):
        sid = str(item.get("schema_id") or "")
        did = str(item.get("publisher_did") or "")
        try:
            fresh = datetime.fromisoformat(str(item.get("next_verification_at") or "").replace("Z", "+00:00")) > datetime.now(timezone.utc)
        except (ValueError, TypeError):
            fresh = False
        included = bool(did.startswith("did:") and item.get("status") == "verified" and fresh
                        and not item.get("attribution_conflict"))
        if included:
            mapping[sid] = did
        else:
            # A current catalog state supersedes an older resolution witness.
            mapping.pop(sid, None)
        details.setdefault(sid, []).append({"did": did, "source": "publication_schemas",
            "record_uri": f"at://{did}/com.atproto.lexicon.schema/{sid}" if did else "",
            "observed_at": item.get("checked_at", ""), "next_verification_at": item.get("next_verification_at", ""),
            "status": item.get("status", "pending"), "included_in_comparison": included,
            "schema_type": item.get("schema_type", "unknown"), "publishers": item.get("publishers", []),
            "authority_dids": item.get("authority_dids", []), "attribution_conflict": item.get("attribution_conflict", False)})
        if not included:
            for witness in details[sid]:
                witness["included_in_comparison"] = False
    return mapping, details


def project_collection_first_creator_dids(project: dict[str, Any]) -> set[str]:
    dids: set[str] = set()
    for collection in sorted(project_collection_nsids(project)):
        try:
            did = fetch_collection_first_creator_did(collection)
        except Exception:
            did = ""
        if did:
            dids.add(did)
    return dids


def project_collection_first_creator_did_map(project: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for collection in sorted(project_collection_nsids(project)):
        try:
            did = fetch_collection_first_creator_did(collection)
        except Exception:
            did = ""
        if did:
            mapping[collection] = did
    return mapping


def project_collection_first_creator_details(project: dict[str, Any]) -> dict[str, dict[str, str]]:
    details: dict[str, dict[str, str]] = {}
    for collection in sorted(project_collection_nsids(project)):
        try:
            did = fetch_collection_first_creator_did(collection)
        except Exception as exc:
            details[collection] = {"status": "error", "did": "", "error": str(exc)}
            continue
        if did:
            details[collection] = {"status": "found", "did": did}
        else:
            details[collection] = {"status": "not_found", "did": ""}
    return details


def inspect_merge_pair_payload(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    include_first_creator_details: bool = False,
    current_publications: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source = project_record.ensure_project_shape(source)
    target = project_record.ensure_project_shape(target)
    source_profile = project_merge.duplicate_profile(source)
    target_profile = project_merge.duplicate_profile(target)
    source_pair_candidate = project_merge.duplicate_candidate_for_pair(
        source,
        target,
        project_profile=source_profile,
        other_profile=target_profile,
    )
    target_pair_candidate = project_merge.duplicate_candidate_for_pair(
        target,
        source,
        project_profile=target_profile,
        other_profile=source_profile,
    )
    strict_overlap = project_merge.batch_strict_overlap(source, target)
    moderate_overlap = project_merge.batch_moderate_overlap(source, target)
    # Refresh only auxiliary evidence, after the normal candidate decisions.
    def auxiliary_project(project: dict[str, Any]) -> dict[str, Any]:
        if current_publications is None:
            return project
        saved = {item["schema_id"]: item for item in project.get("publication_schemas", [])}
        nsids = project_collection_nsids(project) | project_non_record_nsids(project) | set(saved)
        nsids.update(str(item.get("input_nsid") or "") for item in project.get("lexicon_resolution_entries", []))
        return {**project, "publication_schemas": [{**saved.get(sid, {}), **current_publications.get(sid, {})}
                for sid in sorted(set(saved) | (nsids & set(current_publications)))]}

    source = auxiliary_project(source)
    target = auxiliary_project(target)
    source_publishers, source_publisher_details = project_lexicon_publishers(source)
    target_publishers, target_publisher_details = project_lexicon_publishers(target)
    source_details = project_collection_first_creator_details(source) if include_first_creator_details else {}
    target_details = project_collection_first_creator_details(target) if include_first_creator_details else {}
    source_creators = {sid: item["did"] for sid, item in source_details.items() if item["status"] == "found"}
    target_creators = {sid: item["did"] for sid, item in target_details.items() if item["status"] == "found"}
    def overlap(a: dict[str, str], b: dict[str, str]) -> list[str]:
        return sorted(set(a.values()) & set(b.values()))
    heuristic_overlap = dict(moderate_overlap) if include_first_creator_details else {}
    creator_overlap = overlap(source_creators, target_creators)
    if creator_overlap:
        heuristic_overlap["collection_first_record_creator_dids"] = creator_overlap
    summary: dict[str, Any] = {
        "source_project_key": source["project_key"],
        "target_project_key": target["project_key"],
        "source_name": source.get("name", ""),
        "target_name": target.get("name", ""),
        "source_seed_type": source.get("seed_type", ""),
        "source_seed_value": source.get("seed_value", ""),
        "target_seed_type": target.get("seed_type", ""),
        "target_seed_value": target.get("seed_value", ""),
        "merge_blocked": project_merge.merge_is_blocked(source, target),
        "source_pair_candidate": source_pair_candidate,
        "target_pair_candidate": target_pair_candidate,
        "strict_overlap": strict_overlap,
        "moderate_overlap": moderate_overlap,
        "suggested_multi_tenant_suffixes": project_merge.merge_pair_multi_tenant_suffix_suggestions(source, target),
        "first_creator_details_included": bool(include_first_creator_details),
        "first_creator_overlap": heuristic_overlap,
        "source_lexicon_publisher_dids": source_publishers,
        "target_lexicon_publisher_dids": target_publishers,
        "source_lexicon_publisher_details": source_publisher_details,
        "target_lexicon_publisher_details": target_publisher_details,
        "did_comparison": {
            "publisher_publisher": overlap(source_publishers, target_publishers),
            "source_publisher_target_first_observer": overlap(source_publishers, target_creators) if include_first_creator_details else None,
            "source_first_observer_target_publisher": overlap(source_creators, target_publishers) if include_first_creator_details else None,
            "first_observer_first_observer": creator_overlap if include_first_creator_details else None,
        },
        "source_non_record_nsids": sorted(project_non_record_nsids(source)),
        "target_non_record_nsids": sorted(project_non_record_nsids(target)),
    }
    if include_first_creator_details:
        summary["source_collection_first_creator_dids"] = source_creators
        summary["target_collection_first_creator_dids"] = target_creators
        summary["source_collection_first_creator_details"] = source_details
        summary["target_collection_first_creator_details"] = target_details
    return summary


def merge_project_records(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    return core.finalize_project_shape(
        project_merge.combine_records(target, source), restore_manual_repositories=False,
    )


def delete_project(conn: sqlite3.Connection, project_key: str) -> None:
    conn.execute("DELETE FROM projects WHERE project_key = ?", (project_key,))


def register_project_merge_alias(
    conn: sqlite3.Connection,
    *,
    source_project_key: str,
    target_project_key: str,
) -> None:
    registry_database.register_project_key_alias(
        conn,
        alias_project_key=source_project_key,
        canonical_project_key=target_project_key,
        reason="merge",
        created_at=project_record.iso_now(),
    )
    conn.execute(
        """
        UPDATE project_decisions
        SET target_value = ?
        WHERE kind = 'separate_project'
          AND target_type = 'project_key'
          AND target_value = ?
          AND project_key <> ?
        """,
        (target_project_key, source_project_key, target_project_key),
    )
    conn.execute(
        """
        DELETE FROM project_decisions
        WHERE project_key = ?
          AND kind = 'separate_project'
          AND target_type = 'project_key'
          AND target_value IN (?, ?)
        """,
        (target_project_key, source_project_key, target_project_key),
    )


def split_project_identifier_group(
    conn: sqlite3.Connection,
    source_project_key: str,
    *,
    split_label: str,
    group: dict[str, list[str]],
    discovery_source: str,
    seed_source: str,
    note: str = "",
    execute: bool = True,
) -> dict[str, Any]:
    source = project_record.fetch_project(conn, source_project_key)
    if source is None:
        raise ValueError(f"source project not found: {source_project_key}")
    source = project_record.ensure_project_shape(source)
    identifiers = [
        (identifier_type, str(value))
        for identifier_type, values in (
            ("service_url", group.get("service_urls", [])),
            ("nsid", group.get("nsids", [])),
            ("domain", group.get("authority_domains", [])),
        )
        for value in values
        if str(value).strip()
    ]
    if not identifiers:
        raise ValueError(f"split group has no movable identifiers: {split_label}")

    target_key = project_record.allocate_new_project_key(conn)
    first_type, first_value = identifiers[0]
    target = project_record.default_project_record(target_key, first_type, first_value)
    if first_type == "nsid":
        target["resolved_nsids"] = []
    target["tracking_state"] = source.get("tracking_state", "active")
    target["status"] = source.get("status", "draft")
    target["discovery_sources"] = project_record.unique_list(list(source.get("discovery_sources", [])) + [discovery_source])
    target["tracking_note"] = f"split {split_label} from {source['project_key']}"

    blocked = project_record.normalize_blocked_identifiers(list(source.get("blocked_identifiers", [])))
    for identifier_type, identifier_value in identifiers:
        if identifier_type == "service_url":
            core.confirm_service_seed(target, identifier_value, source=seed_source)
        elif identifier_type == "nsid":
            source_confirmed = identifier_value in source.get("resolved_nsids", [])
            field = "resolved_nsids" if source_confirmed else "input_nsids"
            target[field] = project_record.add_unique(target.get(field, []), identifier_value)
        else:
            core.add_manual_seed(target, identifier_type, identifier_value, source=seed_source)
        core.remove_identifier_from_project(source, identifier_type, identifier_value)
        block_key = (identifier_type, identifier_value)
        if not any((str(item.get("type") or ""), str(item.get("value") or "")) == block_key for item in blocked):
            blocked.append(
                {
                    "type": identifier_type,
                    "value": identifier_value,
                    "note": note.strip() or f"split {split_label} to {target_key}",
                    "added_at": project_record.iso_now(),
                }
            )
    source["blocked_identifiers"] = blocked
    source["separate_project_keys"] = project_record.unique_list(list(source.get("separate_project_keys", [])) + [target_key])
    target["separate_project_keys"] = project_record.unique_list(list(target.get("separate_project_keys", [])) + [source["project_key"]])
    note_line = f"split {split_label} -> {target_key}"
    if note:
        note_line = f"{note_line} ({note.strip()})"
    project_record.append_system_note(source, note_line)

    source = core.finalize_project_shape(source)
    target = core.finalize_project_shape(target)
    source["duplicate_candidates"] = project_merge.compute_duplicate_candidates(conn, source)
    source["merge_recommendation"] = project_merge.compute_merge_recommendation(source)
    target["duplicate_candidates"] = project_merge.compute_duplicate_candidates(conn, target)
    target["merge_recommendation"] = project_merge.compute_merge_recommendation(target)
    if execute:
        with concurrency.write_transaction(conn):
            concurrency.check_projects(conn, [source, target])
            project_record.save_project(conn, source, enriched=bool(source.get("last_enriched_at")), preserve_timestamps=True, commit=False)
            project_record.save_project(conn, target, enriched=False, preserve_timestamps=True, commit=False)
    return {
        "mode": "execute" if execute else "dry_run",
        "source_project_key": source["project_key"],
        "target_project_key": target["project_key"],
        "split_label": split_label,
        "moved_identifiers": [
            {"type": identifier_type, "value": identifier_value}
            for identifier_type, identifier_value in identifiers
        ],
        "source_name": source.get("name", ""),
        "target_name": target.get("name", ""),
        "source_seed_type": source.get("seed_type", ""),
        "source_seed_value": source.get("seed_value", ""),
        "target_seed_type": target.get("seed_type", ""),
        "target_seed_value": target.get("seed_value", ""),
        "source_blocked_identifiers": source.get("blocked_identifiers", []),
        "source_input_nsids": source.get("input_nsids", []),
        "source_service_urls": source.get("service_urls", []),
        "source_authority_domains": source.get("authority_domains", []),
        "source_project_repository_urls": source.get("project_repository_urls", []),
        "target_input_nsids": target.get("input_nsids", []),
        "target_service_urls": target.get("service_urls", []),
        "target_authority_domains": target.get("authority_domains", []),
        "target_project_repository_urls": target.get("project_repository_urls", []),
        "target_merge_recommendation": target.get("merge_recommendation", {}),
    }


def split_project_authority_domain(
    conn: sqlite3.Connection,
    source_project_key: str,
    authority_domain: str,
    *,
    note: str = "",
    execute: bool = False,
) -> dict[str, Any]:
    source = project_record.fetch_project(conn, source_project_key)
    if source is None:
        raise ValueError(f"source project not found: {source_project_key}")
    source = project_record.ensure_project_shape(source)
    normalized_domain = str(authority_domain or "").strip().lower().rstrip(".")
    if not normalized_domain:
        raise ValueError("authority domain is required")
    group = project_merge.project_split_identifiers_by_authority_domain(source, normalized_domain)
    return split_project_identifier_group(
        conn,
        source["project_key"],
        split_label=f"authority domain {normalized_domain}",
        group=group,
        discovery_source="split_from_authority_domain",
        seed_source="authority_domain_split",
        note=note,
        execute=execute,
    )


def split_project_tenant_group(
    conn: sqlite3.Connection,
    source_project_key: str,
    tenant: str,
    group: dict[str, list[str]],
) -> dict[str, Any]:
    result = split_project_identifier_group(
        conn,
        source_project_key,
        split_label=f"tenant {tenant}",
        group=group,
        discovery_source="split_from_source_config_audit",
        seed_source="source_config_audit_split",
        execute=True,
    )
    return {
        "source_project_key": result["source_project_key"],
        "target_project_key": result["target_project_key"],
        "tenant": tenant,
        "moved_identifiers": result["moved_identifiers"],
        "target_merge_recommendation": result.get("target_merge_recommendation", {}),
    }
