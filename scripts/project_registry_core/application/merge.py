from __future__ import annotations

import sqlite3
from typing import Any

from .. import registry as core
from project_registry_core import project_record


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
    return core.inspect_merge_pair_payload(
        conn,
        source,
        target,
        include_first_creator_details=include_first_creator_details,
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
        source["duplicate_candidates"] = core.compute_duplicate_candidates(conn, source)
        source["merge_recommendation"] = core.compute_merge_recommendation(source)
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
    if core.merge_is_blocked(source, target) and not force:
        raise ValueError("merge is manually blocked for this project pair; pass force only to override that guard")
    merged = core.merge_project_records(target, source)
    merged["separate_project_keys"] = [
        key for key in merged.get("separate_project_keys", [])
        if key not in {source["project_key"], target["project_key"]}
    ]
    if execute:
        merged["duplicate_candidates"] = core.compute_duplicate_candidates(conn, merged)
        merged["merge_recommendation"] = core.compute_merge_recommendation(merged)
    merged = core.apply_promotion_state(merged)
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
        "merged_tagline_is_provisional": merged.get("tagline_is_provisional", False),
        "merged_service_status": merged.get("service_status", ""),
        "merged_source_code_urls": merged.get("source_code_urls", []),
        "introduced": bool(merged.get("introduced_url")),
        "introduced_url": merged.get("introduced_url", ""),
        "merge_recommendation": source.get("merge_recommendation", {}),
    }
    if execute:
        with conn:
            project_record.save_project(
                conn,
                merged,
                enriched=bool(merged.get("last_enriched_at")),
                preserve_timestamps=True,
                commit=False,
            )
            core.register_project_merge_alias(
                conn,
                source_project_key=source["project_key"],
                target_project_key=target["project_key"],
            )
            core.delete_project(conn, source["project_key"])
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
        return core.split_project_authority_domain(
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
    target["tracking_state"] = source.get("tracking_state", "active")
    target["status"] = source.get("status", "draft")
    target["discovery_sources"] = project_record.unique_list(list(source.get("discovery_sources", [])) + ["split_from_project"])
    target["tracking_note"] = f"split from {source['project_key']}"
    core.add_manual_seed(target, identifier_type, identifier_value, source="split")
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
    core.append_system_note(source, note_line)
    source = core.finalize_project_shape(source)
    for item in (source, target):
        item["duplicate_candidates"] = core.compute_duplicate_candidates(conn, item)
        item["merge_recommendation"] = core.compute_merge_recommendation(item)
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
        "source_source_code_urls": source.get("source_code_urls", []),
        "target_input_nsids": target.get("input_nsids", []),
        "target_service_urls": target.get("service_urls", []),
        "target_source_code_urls": target.get("source_code_urls", []),
    }
    if execute:
        with conn:
            project_record.save_project(
                conn,
                source,
                enriched=bool(source.get("last_enriched_at")),
                preserve_timestamps=True,
                commit=False,
            )
            project_record.save_project(conn, target, enriched=False, preserve_timestamps=True, commit=False)
    return summary
