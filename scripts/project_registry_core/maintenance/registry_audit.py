"""Explicit registry audits and repairs using the normal project rules."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any

from .. import project_record, project_merge, registry as core
from .. import service as service_core, trust_graph as trust_graph_core
from ..identity import identifiers as identifier_core, url_alias as url_alias_core
from ..repositories import repo_urls as repo_url_core
from ..persistence import runtime_view



URL_FIELD_INVENTORY: tuple[dict[str, str], ...] = (
    {"field": "website_url", "layer": "canonical", "role": "primary public website"},
    {"field": "introduced_url", "layer": "canonical", "role": "editorial introduction reference"},
    {"field": "project_repository_url", "layer": "canonical", "role": "primary project repository"},
    {"field": "service_urls", "layer": "confirmed_set", "role": "confirmed service surfaces"},
    {"field": "project_repository_urls", "layer": "confirmed_set", "role": "confirmed project repositories"},
    {"field": "service_url_candidates", "layer": "candidate", "role": "unconfirmed service surfaces"},
    {"field": "candidate_project_repository_urls", "layer": "candidate", "role": "unconfirmed project repositories"},
    {"field": "resource_url_records", "layer": "observation", "role": "structured supporting page observations"},
    {"field": "evidence_urls", "layer": "observation", "role": "general supporting URLs"},
    {"field": "failed_service_urls", "layer": "observation", "role": "failed service probes"},
    {"field": "supported_service_urls", "layer": "observation", "role": "supported heuristic service probes"},
)


def normalized_url_field_values(project: dict[str, Any], field_name: str) -> set[str]:
    value = project.get(field_name)
    if field_name == "resource_url_records":
        return {record["url"] for record in project_record.normalize_resource_url_records(value if isinstance(value, list) else [])}
    values = value if isinstance(value, list) else ([value] if value else [])
    if field_name in {"project_repository_url", "project_repository_urls", "candidate_project_repository_urls"}:
        return {normalized for item in values if (normalized := repo_url_core.canonical_repo_url(str(item)))}
    return {normalized for item in values if (normalized := identifier_core.normalize_url(str(item)))}


def build_url_field_audit(conn: sqlite3.Connection, *, example_limit: int = 20) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM projects ORDER BY project_key ASC").fetchall()
    projects = [project_record.project_from_row(conn, row) for row in rows]
    field_summaries: list[dict[str, Any]] = []
    for spec in URL_FIELD_INVENTORY:
        field_name = spec["field"]
        values: list[str] = []
        nonempty_projects = 0
        for project in projects:
            field_values = normalized_url_field_values(project, field_name)
            if field_values:
                nonempty_projects += 1
                values.extend(sorted(field_values))
        field_summaries.append(
            {
                **spec,
                "nonempty_projects": nonempty_projects,
                "value_count": len(values),
                "unique_value_count": len(set(values)),
            }
        )

    alias_pairs = (
        ("project_repository_url", "project_repository_urls", "canonical_in_confirmed_set"),
    )
    alias_summaries: list[dict[str, Any]] = []
    for left, right, relation in alias_pairs:
        left_nonempty = 0
        exact_match = 0
        left_subset = 0
        overlap_values: set[str] = set()
        for project in projects:
            left_values = normalized_url_field_values(project, left)
            right_values = normalized_url_field_values(project, right)
            if not left_values:
                continue
            left_nonempty += 1
            if left_values == right_values:
                exact_match += 1
            if left_values <= right_values:
                left_subset += 1
            overlap_values.update(left_values & right_values)
        alias_summaries.append(
            {
                "left": left,
                "right": right,
                "relation": relation,
                "left_nonempty_projects": left_nonempty,
                "exact_match_projects": exact_match,
                "left_subset_projects": left_subset,
                "overlap_unique_value_count": len(overlap_values),
            }
        )

    anomaly_specs = (
        ("project_repository_url_missing_from_project_repository_urls", lambda p: normalized_url_field_values(p, "project_repository_url") - normalized_url_field_values(p, "project_repository_urls")),
        ("confirmed_candidate_service_overlap", lambda p: normalized_url_field_values(p, "service_urls") & normalized_url_field_values(p, "service_url_candidates")),
        ("observed_url_alias_still_candidate", lambda p: {
            identifier_core.normalize_url(str(item.get("alias") or ""))
            for item in url_alias_core.observed_url_aliases(p)
        } & normalized_url_field_values(p, "service_url_candidates")),
        ("confirmed_candidate_repo_overlap", lambda p: normalized_url_field_values(p, "project_repository_urls") & normalized_url_field_values(p, "candidate_project_repository_urls")),
        ("introduced_url_in_evidence_urls", lambda p: normalized_url_field_values(p, "introduced_url") & normalized_url_field_values(p, "evidence_urls")),
    )
    anomalies: list[dict[str, Any]] = []
    for kind, collect_values in anomaly_specs:
        examples: list[dict[str, Any]] = []
        project_count = 0
        value_count = 0
        for project in projects:
            values = sorted(collect_values(project))
            if not values:
                continue
            project_count += 1
            value_count += len(values)
            if len(examples) < max(0, int(example_limit)):
                examples.append({"project_key": str(project.get("project_key") or ""), "values": values})
        anomalies.append({"kind": kind, "project_count": project_count, "value_count": value_count, "examples": examples})

    return {
        "project_count": len(projects),
        "field_inventory": field_summaries,
        "alias_relationships": alias_summaries,
        "anomalies": anomalies,
    }


def project_storage_projection(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "scalars": {
            field: project.get(field)
            for field in ("name", "website_url", "service_status", "tracking_state", "introduced_url")
        },
        "profile": project_record.project_record_payload(project),
        "state": project_record.project_ops_payload(project),
    }


def project_normalized_child_projection(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "identifiers": [asdict(item) for item in runtime_view.project_identifiers(project)],
        "relations": runtime_view.relation_rows(project),
        "decisions": [asdict(item) for item in runtime_view.project_decisions(project)],
    }


def audit_and_repair_identifiers(conn: sqlite3.Connection, *, execute: bool = False) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT *
        FROM projects
        ORDER BY last_seen_at DESC, project_key ASC
        """
    ).fetchall()
    project_map: dict[str, dict[str, Any]] = {}
    pruned_projects = 0
    normalized_projects = 0
    manual_repo_seed_repairs = 0
    developer_account_repairs = 0
    current_service_projection_changes = 0
    current_service_projection_examples: list[dict[str, Any]] = []
    normalized_child_changes = 0
    normalized_child_change_examples: list[str] = []
    for row in rows:
        stored_payload = project_record.project_from_row_payload(row)
        project_key = str(row["project_key"])
        malformed_developer_accounts = sum(
            1
            for field in ("developer_accounts", "candidate_developer_accounts")
            for item in (stored_payload.get(field) if isinstance(stored_payload.get(field), list) else [])
            if isinstance(item, dict)
            and str(item.get("handle") or "").strip()
            and (
                project_record.DID_RE.fullmatch(str(item.get("handle") or "").strip())
                or not project_record.ATPROTO_HANDLE_RE.fullmatch(str(item.get("handle") or "").strip())
            )
        )
        malformed_developer_identifier_rows = 0
        identifier_rows = conn.execute(
            """
            SELECT kind, value, role, metadata_json
            FROM project_identifiers
            WHERE project_key = ?
              AND role IN ('developer_account', 'developer_handle')
            """,
            (project_key,),
        ).fetchall()
        for identifier_row in identifier_rows:
            kind = str(identifier_row["kind"])
            value = str(identifier_row["value"])
            role = str(identifier_row["role"])
            metadata = project_record.load_json(str(identifier_row["metadata_json"] or "{}")) or {}
            if kind == "handle" and role == "developer_handle" and not project_record.ATPROTO_HANDLE_RE.fullmatch(value):
                malformed_developer_identifier_rows += 1
            elif (
                kind == "account"
                and role == "developer_account"
                and isinstance(metadata, dict)
                and str((metadata or {"handle": value}).get("handle") or "").strip()
                and (
                    project_record.DID_RE.fullmatch(str((metadata or {"handle": value}).get("handle") or "").strip())
                    or not project_record.ATPROTO_HANDLE_RE.fullmatch(str((metadata or {"handle": value}).get("handle") or "").strip())
                )
            ):
                malformed_developer_identifier_rows += 1
        developer_account_repairs += malformed_developer_accounts + malformed_developer_identifier_rows
        raw = project_record.project_from_row(conn, row)
        original = project_record.ensure_project_shape(raw)
        original_repo_urls = set(repo_url_core.normalize_repo_collection(project_record.project_repository_url_values(original)))
        manual_repo_seed_repairs += len(project_record.active_manual_repo_urls(original) - original_repo_urls)
        normalized = project_record.ensure_project_shape(dict(original))
        normalized = core.finalize_project_shape(normalized)
        project_record.reclassify_service_specific_lexicon(normalized)
        normalized = project_record.apply_atproto_contribution_state(normalized)
        current_service_before = {
            "service_status": str(original.get("service_status") or "unknown"),
            "service_status_confidence": str(original.get("service_status_confidence") or "none"),
            "service_status_evidence": list(original.get("service_status_evidence", [])),
        }
        current_service_after = {
            "service_status": str(normalized.get("service_status") or "unknown"),
            "service_status_confidence": str(normalized.get("service_status_confidence") or "none"),
            "service_status_evidence": list(normalized.get("service_status_evidence", [])),
        }
        current_service_projection_changed = current_service_before != current_service_after
        if current_service_projection_changed:
            current_service_projection_changes += 1
            if len(current_service_projection_examples) < 20:
                current_service_projection_examples.append({
                    "project_key": project_key,
                    "before": current_service_before,
                    "after": current_service_after,
                })
        contribution_changed = any(
            project_record.dump_json(stored_payload.get(field, default)) != project_record.dump_json(normalized.get(field, default))
            for field, default in (
                ("atproto_contribution_level", "unknown"),
                ("atproto_contribution_score", 0),
                ("atproto_contribution_evidence", []),
            )
        )
        if project_record.dump_json(project_storage_projection(raw)) != project_record.dump_json(project_storage_projection(original)):
            normalized_projects += 1
        normalized_child_changed = (
            project_record.dump_json(project_normalized_child_projection(original))
            != project_record.dump_json(project_normalized_child_projection(normalized))
        )
        if normalized_child_changed:
            normalized_child_changes += 1
            if len(normalized_child_change_examples) < 20:
                normalized_child_change_examples.append(project_key)
        stored_payload_changed = (
            malformed_developer_accounts
            or malformed_developer_identifier_rows
            or project_record.dump_json(project_storage_projection(raw)) != project_record.dump_json(project_storage_projection(normalized))
            or normalized_child_changed
            or contribution_changed
        )
        if stored_payload_changed:
            pruned_projects += 1
        if execute and (stored_payload_changed or current_service_projection_changed):
            if malformed_developer_accounts or malformed_developer_identifier_rows:
                normalized.pop(runtime_view.RUNTIME_IDENTIFIER_ROWS, None)
                normalized.pop(runtime_view.RUNTIME_IDENTIFIER_BASELINE, None)
            project_record.save_project(conn, normalized, enriched=False)
        project_map[normalized["project_key"]] = normalized

    expected_by_project: dict[str, set[tuple[str, str]]] = {
        project_key: core.project_collision_identifiers(project)
        for project_key, project in project_map.items()
    }

    project_list = [project_map[project_key] for project_key in sorted(project_map)]
    duplicate_profiles, duplicate_index = project_merge.build_duplicate_candidate_index(project_list)
    refreshed_derived_projects = 0
    for project_key in sorted(project_map):
        project = dict(project_map[project_key])
        duplicate_candidates = project_merge.compute_duplicate_candidates(
            conn,
            project,
            projects=project_list,
            duplicate_profiles=duplicate_profiles,
            duplicate_index=duplicate_index,
        )
        merge_recommendation = project_merge.compute_merge_recommendation(
            dict(project, duplicate_candidates=duplicate_candidates)
        )
        if (
            project_record.dump_json(project.get("duplicate_candidates", [])) != project_record.dump_json(duplicate_candidates)
            or project_record.dump_json(project.get("merge_recommendation", {})) != project_record.dump_json(merge_recommendation)
        ):
            project["duplicate_candidates"] = duplicate_candidates
            project["merge_recommendation"] = merge_recommendation
            if execute:
                project_record.save_project(conn, project, enriched=False)
            project_map[project_key] = project
            refreshed_derived_projects += 1

    if execute:
        conn.commit()

    expected_owners: dict[tuple[str, str], list[str]] = {}
    for project_key, identifiers in expected_by_project.items():
        for identifier in identifiers:
            expected_owners.setdefault(identifier, []).append(project_key)

    collisions: list[dict[str, Any]] = []
    colliding_identifiers: set[tuple[str, str]] = set()
    for identifier, owners in expected_owners.items():
        unique_owners = sorted(set(owners))
        if len(unique_owners) > 1:
            colliding_identifiers.add(identifier)
            collisions.append(
                {
                    "identifier_type": identifier[0],
                    "identifier_value": identifier[1],
                    "project_keys": unique_owners,
                }
            )

    return {
        "mode": "execute" if execute else "dry_run",
        "project_count": len(rows),
        "normalized_project_count": normalized_projects,
        "pruned_project_count": pruned_projects,
        "manual_repo_seed_repair_count": manual_repo_seed_repairs,
        "developer_account_repair_count": developer_account_repairs,
        "normalized_child_change_count": normalized_child_changes,
        "normalized_child_change_examples": normalized_child_change_examples,
        "current_service_projection_change_count": current_service_projection_changes,
        "current_service_projection_examples": current_service_projection_examples,
        "refreshed_derived_project_count": refreshed_derived_projects,
        "orphan_count": 0,
        "stale_count": 0,
        "missing_count": 0,
        "collision_count": len(collisions),
        "repaired": {
            "deleted_orphans": 0,
            "deleted_stale": 0,
            "inserted_missing": 0,
        },
        "orphan_rows": [],
        "stale_rows": [],
        "missing_rows": [],
        "collisions": collisions[:100],
    }


def classify_trust_identifier_issue(item: dict[str, Any]) -> str:
    if item.get("guarded"):
        return "guarded_confirmed_identifier"
    if item.get("manual_seed") or item.get("operator_confirmed"):
        return "operator_confirmed_identifier"
    if item.get("source_confirmed"):
        return "source_confirmed_identifier"
    evaluation = item.get("evaluation") if isinstance(item.get("evaluation"), dict) else {}
    if evaluation.get("auto_confirm"):
        return "supported_by_trust_graph"
    directional = item.get("directional_paths") if isinstance(item.get("directional_paths"), list) else []
    if directional:
        return "needs_reprobe"
    return "should_downgrade"


def build_trust_graph_audit(conn: sqlite3.Connection, *, example_limit: int = 20) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM projects ORDER BY project_key ASC").fetchall()
    issues: dict[str, list[dict[str, Any]]] = {
        "needs_reprobe": [],
        "should_downgrade": [],
        "unsupported_confirmed_identifier": [],
        "guarded_confirmed_identifier": [],
        "unsupported_confirmed_anchor_dependency": [],
    }
    project_count = 0
    edge_count = 0
    seen_issue_keys: set[tuple[str, str, str, str]] = set()
    for row in rows:
        project = project_record.ensure_project_shape(project_record.project_from_row(conn, row))
        project_count += 1
        explanation = core.trust_explanation(project)
        edge_count += int(explanation.get("edge_count") or 0)
        for item in explanation.get("identifier_evaluations", []):
            identifier = item.get("identifier") if isinstance(item.get("identifier"), dict) else {}
            identifier_type = str(identifier.get("kind") or "")
            value = str(identifier.get("value") or "")
            classification = str(item.get("classification") or "")
            if identifier_type not in {"service_url", "repo_url", "nsid", "did", "handle", "domain"} or classification != "confirmed":
                continue
            issue = {
                "project_key": str(project.get("project_key") or ""),
                "identifier_type": identifier_type,
                "value": value,
                "role": str(identifier.get("role") or ""),
                "evaluation_reason": str((item.get("evaluation") or {}).get("reason") or ""),
            }
            classification_kind = classify_trust_identifier_issue(item)
            issue_key = (classification_kind, issue["project_key"], identifier_type, value)
            if issue_key in seen_issue_keys:
                continue
            seen_issue_keys.add(issue_key)
            issue["classification"] = classification_kind
            if classification_kind in issues:
                issues[classification_kind].append(issue)
            if item.get("manual_seed") or item.get("operator_confirmed") or item.get("source_confirmed"):
                continue
            if not bool((item.get("evaluation") or {}).get("auto_confirm")):
                issues["unsupported_confirmed_identifier"].append(issue)
                issues["unsupported_confirmed_anchor_dependency"].append(issue)
    return {
        "mode": "dry_run",
        "policy_version": trust_graph_core.TRUST_POLICY_VERSION,
        "project_count": project_count,
        "trust_edge_count": edge_count,
        "issue_counts": {key: len(value) for key, value in issues.items()},
        "examples": {
            key: value[: max(0, int(example_limit))]
            for key, value in issues.items()
        },
    }


def repair_trust_graph(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    downgrade_unsupported: bool = False,
    example_limit: int = 20,
) -> dict[str, Any]:
    if downgrade_unsupported and not execute:
        raise ValueError("--downgrade-unsupported requires --execute")
    rows = conn.execute("SELECT * FROM projects ORDER BY project_key ASC").fetchall()
    changed_projects: list[dict[str, Any]] = []
    downgraded_identifiers: list[dict[str, str]] = []
    evaluated_identifier_count = 0
    auto_confirmable_identifier_count = 0
    for row in rows:
        project = project_record.ensure_project_shape(project_record.project_from_row(conn, row))
        project_key = str(project.get("project_key") or "")
        refresh = core.refresh_trust_graph_evaluations(project)
        evaluated_identifier_count += int(refresh.get("evaluated_identifier_count") or 0)
        auto_confirmable_identifier_count += int(refresh.get("auto_confirmable_identifier_count") or 0)

        downgrade_values: list[tuple[str, str]] = []
        seen_downgrade_values: set[tuple[str, str]] = set()
        if downgrade_unsupported:
            while True:
                batch: list[tuple[str, str]] = []
                explanation = core.trust_explanation(project)
                for item in explanation.get("identifier_evaluations", []):
                    identifier = item.get("identifier") if isinstance(item.get("identifier"), dict) else {}
                    identifier_type = str(identifier.get("kind") or "")
                    value = str(identifier.get("value") or "")
                    if identifier_type not in {"service_url", "repo_url", "nsid", "did", "handle", "domain"}:
                        continue
                    if str(item.get("classification") or "") != "confirmed":
                        continue
                    if classify_trust_identifier_issue(item) != "should_downgrade":
                        continue
                    normalized_value = project_record.normalize_blocked_identifier_value(identifier_type, value)
                    key = (identifier_type, normalized_value)
                    if key in seen_downgrade_values:
                        continue
                    seen_downgrade_values.add(key)
                    batch.append(key)
                if not batch:
                    break
                for identifier_type, value in batch:
                    core.downgrade_identifier_to_candidate(project, identifier_type, value)
                    downgraded_identifiers.append({
                        "project_key": project_key,
                        "identifier_type": identifier_type,
                        "value": project_record.normalize_blocked_identifier_value(identifier_type, value),
                    })
                downgrade_values.extend(batch)
                core.refresh_trust_graph_evaluations(project)

        if refresh.get("changed") or downgrade_values:
            changed_projects.append({
                "project_key": project_key,
                "trust_graph_changed": bool(refresh.get("changed")),
                "downgraded_identifier_count": len(downgrade_values),
            })
            if execute:
                project_record.save_project(conn, project, enriched=False, preserve_timestamps=True)

    if execute:
        conn.commit()
    return {
        "mode": "execute" if execute else "dry_run",
        "policy_version": trust_graph_core.TRUST_POLICY_VERSION,
        "project_count": len(rows),
        "evaluated_identifier_count": evaluated_identifier_count,
        "auto_confirmable_identifier_count": auto_confirmable_identifier_count,
        "changed_project_count": len(changed_projects),
        "updated_project_count": len(changed_projects) if execute else 0,
        "downgrade_unsupported": bool(downgrade_unsupported),
        "downgraded_identifier_count": len(downgraded_identifiers) if execute else 0,
        "changed_project_examples": changed_projects[: max(0, int(example_limit))],
        "downgraded_identifier_examples": downgraded_identifiers[: max(0, int(example_limit))],
    }


def refresh_current_same_site_evidence(
    conn: sqlite3.Connection,
    *,
    example_limit: int = 20,
) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM projects ORDER BY project_key").fetchall()
    changes: list[dict[str, Any]] = []
    repaired_result_count = 0
    aggregate_evidence_change_count = 0
    projects_to_save: list[dict[str, Any]] = []
    for row in rows:
        project = project_record.project_from_row(conn, row)
        project_repairs = 0
        for field in ("service_verification_results", "investigation_log"):
            for item in project.get(field, []):
                if not isinstance(item, dict) or "redirect_same_site_family" not in item:
                    continue
                normalized = service_core.normalize_redirect_same_site_policy(item)
                corrected = bool(normalized.get("redirect_same_site_family"))
                normalized_evidence = list(normalized.get("service_status_evidence", []))
                if (
                    bool(item.get("redirect_same_site_family")) == corrected
                    and list(item.get("service_status_evidence", [])) == normalized_evidence
                ):
                    continue
                item["redirect_same_site_family"] = corrected
                item["service_status_evidence"] = normalized_evidence
                project_repairs += 1

        aggregate_before = list(project.get("service_status_evidence", []))
        has_current_same_site = any(
            isinstance(item, dict) and item.get("redirect_same_site_family") is True
            for field in ("service_verification_results", "investigation_log")
            for item in project.get(field, [])
        )
        project["service_status_evidence"] = list(aggregate_before)
        if project_repairs and not has_current_same_site:
            project["service_status_evidence"] = [
                value for value in aggregate_before if value != "redirect_same_site_family"
            ]
        aggregate_changed = project["service_status_evidence"] != aggregate_before
        if not project_repairs and not aggregate_changed:
            continue
        repaired_result_count += project_repairs
        aggregate_evidence_change_count += int(aggregate_changed)
        projects_to_save.append(project)
        if len(changes) < max(0, example_limit):
            changes.append({
                "project_key": str(project["project_key"]),
                "repaired_result_count": project_repairs,
                "aggregate_evidence_changed": aggregate_changed,
            })

    if projects_to_save:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for project in projects_to_save:
                project_record.save_project(conn, project, enriched=False, preserve_timestamps=True, commit=False)
            foreign_key_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_issues:
                raise RuntimeError(f"foreign key check failed: {len(foreign_key_issues)} issue(s)")
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise RuntimeError(f"integrity check failed: {integrity}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "project_count": len(rows),
        "changed_project_count": len(projects_to_save),
        "repaired_result_count": repaired_result_count,
        "aggregate_evidence_change_count": aggregate_evidence_change_count,
        "changed_project_examples": changes,
    }
