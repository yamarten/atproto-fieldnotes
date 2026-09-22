from __future__ import annotations

from .. import editorial, service

from .. import concurrency

import datetime as dt
import hashlib
import json
import sqlite3
from typing import Any

from .. import evaluation, relations, registry as core, runtime_view, storage
from . import decisions, explain, investigation, merge, projects, query as query_service, review, review_targets
from project_registry_core import project_record, promotion_attention


WEB_API_VERSION = 2
CLASSIFICATION_ORDER = ("confirmed", "candidate", "derived", "heuristic")
RELATION_ENTITY_TYPES = (
    "account",
    "did",
    "domain",
    "handle",
    "nsid",
    "oauth_client",
    "project",
    "repo",
    "service",
    "website",
)
DECISION_KINDS = (
    "assess_atproto_usage",
    "block_identifier",
    "confirm_identifier",
    "defer_identifier",
    "defer_promotion",
    "lower_promotion_priority",
    "defer_review_target",
    "dismiss_review",
    "dismiss_review_target",
    "separate_project",
)
DECISION_TARGET_TYPES = (
    "atproto_usage",
    "project_key",
    "promotion_priority",
    "review_kind",
    "review_target",
    *tuple(sorted(project_record.IDENTIFIER_DECISION_TYPES)),
)


class StaleProjectError(RuntimeError):
    pass


def project_filter_options(conn: sqlite3.Connection) -> dict[str, list[str]]:
    def distinct(table: str, column: str) -> list[str]:
        rows = conn.execute(
            f"SELECT DISTINCT {column} FROM {table} WHERE {column} != '' ORDER BY {column}"
        ).fetchall()
        return [str(row[0]) for row in rows]

    def merged(*values: list[str] | tuple[str, ...]) -> list[str]:
        return sorted({str(item) for group in values for item in group if str(item)})

    return {
        "tracking_states": sorted(project_record.TRACKING_STATES),
        "service_statuses": sorted(project_record.SERVICE_STATUS_VALUES),
        "contribution_levels": sorted(evaluation.ATPROTO_CONTRIBUTION_LEVELS),
        "identifier_kinds": sorted(project_record.IDENTIFIER_DECISION_TYPES),
        "identifier_classifications": sorted(runtime_view.CLASSIFICATIONS),
        "relation_predicates": sorted(relations.RELATION_PREDICATES),
        "relation_subject_types": merged(RELATION_ENTITY_TYPES, distinct("project_relations", "subject_type")),
        "relation_object_types": merged(RELATION_ENTITY_TYPES, distinct("project_relations", "object_type")),
        "relation_sources": distinct("project_relations", "source"),
        "decision_kinds": merged(DECISION_KINDS, distinct("project_decisions", "kind")),
        "decision_target_types": merged(DECISION_TARGET_TYPES, distinct("project_decisions", "target_type")),
    }


def _json(value: str, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _identifier_row_is_displayable(item: dict[str, Any]) -> bool:
    kind = str(item.get("kind") or "")
    role = str(item.get("role") or "")
    value = str(item.get("value") or "").strip()
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if role in {"developer_account", "developer_handle"}:
        if kind == "account":
            return bool(project_record.normalize_developer_accounts([metadata or {"handle": value}]))
        if kind == "handle":
            return bool(project_record.ATPROTO_HANDLE_RE.fullmatch(value))
    if role == "project_handle":
        return bool(project_record.ATPROTO_HANDLE_RE.fullmatch(value))
    return True


def _summary(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_key": str(project.get("project_key") or ""),
        "name": str(project.get("name") or ""),
        "website_url": str(project.get("website_url") or ""),
        "service_status": str(project.get("service_status") or "unknown"),
        "tracking_state": str(project.get("tracking_state") or "active"),
        "introduced_url": str(project.get("introduced_url") or ""),
        "tagline": str(project.get("tagline") or ""),
        "summary": str(project.get("summary") or ""),
        "status": str(project.get("status") or ""),
        "category": str(project.get("category") or ""),
        "atproto_contribution_level": str(project.get("atproto_contribution_level") or "unknown"),
        "technical_interest_level": str(project.get("technical_interest_level") or "unknown"),
        "first_seen_at": str(project.get("first_seen_at") or ""),
        "last_seen_at": str(project.get("last_seen_at") or ""),
        "last_enriched_at": str(project.get("last_enriched_at") or ""),
        "has_candidates": bool(project.get("_query_has_candidates", project.get("service_url_candidates") or project.get("candidate_project_repository_urls"))),
        "has_errors": bool(project.get("last_run_errors")),
        "promotion_hold": bool(project.get("_query_promotion_hold", core.active_promotion_deferral(project) is not None)),
    }


def search_projects(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    tracking_states: set[str] | None = None,
    service_statuses: set[str] | None = None,
    contribution_levels: set[str] | None = None,
    technical_interest_levels: set[str] | None = None,
    has_notes: str = "any",
    introduced: str = "any",
    has_candidates: str = "any",
    has_errors: str = "any",
    promotion_hold: str = "any",
    seen_since: str = "",
    enriched_before: str = "",
    sort: str = "",
    limit: int = 50,
    identifier_kind: str = "",
    identifier_value: str = "",
    identifier_classification: str = "",
    relation_predicate: str = "",
    relation_subject_type: str = "",
    relation_subject_value: str = "",
    relation_object_type: str = "",
    relation_object_value: str = "",
    relation_source: str = "",
    decision_kind: str = "",
    decision_target_type: str = "",
    decision_target_value: str = "",
    decision_status: str = "",
) -> dict[str, Any]:
    search = str(query or "").strip()
    projects_list = query_service.search_project_rows(
        conn,
        query=search,
        tracking_states=tracking_states,
        service_statuses=service_statuses,
        contribution_levels=contribution_levels,
        technical_interest_levels=technical_interest_levels,
        has_notes=has_notes,
        identifier_kind=identifier_kind,
        identifier_value=identifier_value,
        identifier_classification=identifier_classification,
        relation_predicate=relation_predicate,
        relation_subject_type=relation_subject_type,
        relation_subject_value=relation_subject_value,
        relation_object_type=relation_object_type,
        relation_object_value=relation_object_value,
        relation_source=relation_source,
        decision_kind=decision_kind,
        decision_target_type=decision_target_type,
        decision_target_value=decision_target_value,
        decision_status=decision_status,
    )

    def matches(project: dict[str, Any]) -> bool:
        checks = (
            (introduced, bool(project.get("introduced_url"))),
            (has_candidates, bool(project.get("_query_has_candidates"))),
            (has_errors, bool(project.get("last_run_errors"))),
            (promotion_hold, bool(project.get("_query_promotion_hold"))),
        )
        for choice, actual in checks:
            if choice in {"yes", "no"} and actual != (choice == "yes"):
                return False
        if seen_since and str(project.get("last_seen_at") or "")[:10] < seen_since:
            return False
        enriched = str(project.get("last_enriched_at") or "")[:10]
        if enriched_before and enriched and enriched >= enriched_before:
            return False
        return True

    filtered = [project for project in projects_list if matches(project)]
    by_key = {str(project.get("project_key") or ""): project for project in filtered}
    if search:
        ranked = query_service.rank_project_rows(conn, filtered, search)
        items = []
        for candidate in ranked:
            item = _summary(by_key[candidate["project_key"]])
            item["score"] = candidate.get("score", 0)
            item["reasons"] = candidate.get("reasons", [])
            items.append(item)
        resolved_sort = sort or "relevance"
    else:
        items = [_summary(project) for project in filtered]
        resolved_sort = sort or "last_seen_desc"
    if resolved_sort == "last_seen_desc":
        items.sort(key=lambda item: (str(item.get("last_seen_at") or ""), str(item["project_key"])), reverse=True)
    elif resolved_sort == "first_seen_desc":
        items.sort(key=lambda item: (str(item.get("first_seen_at") or ""), str(item["project_key"])), reverse=True)
    elif resolved_sort == "first_seen_asc":
        items.sort(key=lambda item: (str(item.get("first_seen_at") or ""), str(item["project_key"])))
    elif resolved_sort == "last_enriched_asc":
        items.sort(key=lambda item: (str(item.get("last_enriched_at") or ""), str(item["project_key"])))
    elif resolved_sort == "name_asc":
        items.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item["project_key"])))
    return {
        "items": items[: max(1, min(int(limit), 500))],
        "count": len(items),
        "returned_count": min(len(items), max(1, min(int(limit), 500))),
        "query": search,
        "sort": resolved_sort,
    }


def _identifier_rows(conn: sqlite3.Connection, project_key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM project_identifiers WHERE project_key = ? ORDER BY ordinal", (project_key,)
    ).fetchall()
    raw = [
        {
            "kind": str(row["kind"]),
            "value": str(row["value"]),
            "role": str(row["role"]),
            "classification": str(row["classification"]),
            "is_primary": bool(row["is_primary"]),
            "provider": str(row["provider"]),
            "metadata": _json(str(row["metadata_json"]), {}),
            "observed_at": str(row["observed_at"]),
        }
        for row in rows
    ]
    for item in raw:
        if item["kind"] == "service_url" and item["role"] == "website":
            item["role"] = "service"
            item["is_primary"] = False
    raw = [item for item in raw if _identifier_row_is_displayable(item)]
    family_specs = {
        "site_url": ("site", "Project sites"),
        "service_url": ("service", "Services"),
        "repo_url": ("repository", "Repositories"),
        "nsid": ("schema", "NSIDs"),
        "account": ("identity", "Identities"),
        "handle": ("identity", "Identities"),
        "did": ("identity", "Identities"),
        "domain": ("identity", "Identities"),
    }
    role_labels = {
        "owned_nsid": "owned",
        "project_site": "project site",
        "project_repository": "repository",
        "source_repository_record": "source", "developer_account": "developer",
        "developer_handle": "handle", "developer_did": "DID",
        "project_account": "project", "project_handle": "project handle",
        "authority_domain": "authority",
    }
    display_roles = {
        "source_repository_record": "project_repository",
    }
    confirmed_identifier_values = {
        (item["kind"], item["value"])
        for item in raw
        if item["classification"] == "confirmed"
    }

    def group_value(item: dict[str, Any], family: str) -> str:
        if family == "identity" and isinstance(item.get("metadata"), dict):
            account_value = str(item["metadata"].get("account") or "").strip()
            if account_value:
                return account_value
        return item["value"]

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in raw:
        family, family_label = family_specs.get(item["kind"], (f"other:{item['kind']}", item["kind"]))
        key_value = group_value(item, family)
        key = (family, key_value)
        group = grouped.setdefault(key, {
            "family": family,
            "family_label": family_label,
            "value": key_value,
            "kinds": [],
            "roles": [],
            "role_labels": [],
            "classifications": [],
            "providers": [],
            "is_primary": False,
            "metadata": [],
            "observed_at": "",
            "entries": [],
        })
        group["is_primary"] = group["is_primary"] or bool(item["is_primary"])
        display_role = display_roles.get(item["role"], item["role"])
        entry = dict(item)
        entry["role"] = display_role
        entry["source_roles"] = project_record.unique_list([display_role, item["role"]])
        entry["role_label"] = role_labels.get(item["role"], item["role"].replace("_", " "))
        developer_candidate = item["role"] == "developer_account"
        entry_action_kind = "account" if developer_candidate else item["kind"]
        value_already_confirmed = (item["kind"], item["value"]) in confirmed_identifier_values
        entry["actions"] = {
            "identifier_type": entry_action_kind,
            "confirm": item["classification"] == "candidate" and not value_already_confirmed and (
                entry_action_kind in {"service_url", "repo_url", "account", "nsid"}
            ),
            "reject": item["classification"] == "candidate"
            and not value_already_confirmed
            and entry_action_kind in {"service_url", "repo_url", "account", "did", "handle", "domain", "nsid"},
            "block": family == "identity" and entry_action_kind in {"account", "did", "handle", "domain"},
            "defer": family == "identity" and entry_action_kind in {"account", "did", "handle", "domain"},
            "split": entry_action_kind == "domain",
            "scope": "identifier",
        }
        entry_key = (
            entry["kind"],
            entry["value"],
            entry["role"],
            entry["classification"],
            entry["actions"]["identifier_type"],
        )
        existing_entry = next((
            candidate for candidate in group["entries"]
            if (
                candidate["kind"],
                candidate["value"],
                candidate["role"],
                candidate["classification"],
                candidate["actions"]["identifier_type"],
            ) == entry_key
        ), None)
        if existing_entry is None:
            group["entries"].append(entry)
        else:
            existing_entry["source_roles"] = project_record.unique_list(
                list(existing_entry.get("source_roles", [])) + list(entry.get("source_roles", []))
            )
            existing_entry["is_primary"] = bool(existing_entry.get("is_primary")) or bool(entry.get("is_primary"))
            if not existing_entry.get("provider") and entry.get("provider"):
                existing_entry["provider"] = entry["provider"]
            existing_entry["observed_at"] = max(
                str(existing_entry.get("observed_at") or ""),
                str(entry.get("observed_at") or ""),
            )
        for field, value in (
            ("kinds", item["kind"]),
            ("roles", display_role),
            ("role_labels", role_labels.get(item["role"], item["role"].replace("_", " "))),
            ("classifications", item["classification"]),
            ("providers", item["provider"]),
        ):
            if value and value not in group[field]:
                group[field].append(value)
        if item["metadata"] and item["metadata"] not in group["metadata"]:
            group["metadata"].append(item["metadata"])
        group["is_primary"] = group["is_primary"] or item["is_primary"]
        group["observed_at"] = max(group["observed_at"], item["observed_at"])
    output = []
    for group in grouped.values():
        group["classification"] = next(
            (value for value in CLASSIFICATION_ORDER if value in group["classifications"]),
            group["classifications"][0] if group["classifications"] else "candidate",
        )
        action_kind = next(
            (
                kind for kind in group["kinds"]
                if group["family"] != "identity" and kind in {"service_url", "repo_url", "nsid"}
            ),
            "",
        )
        candidate_only = "candidate" in group["classifications"] and "confirmed" not in group["classifications"]
        group["actions"] = {
            "identifier_type": action_kind,
            "confirm": candidate_only and action_kind in {"service_url", "repo_url"},
            "reject": candidate_only and action_kind in {"service_url", "repo_url"},
            "block": bool(action_kind),
            "defer": bool(action_kind),
            "split": bool(action_kind),
            "scope": "identifier",
        }
        output.append(group)
    return output


def _relation_rows(conn: sqlite3.Connection, project_key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM project_relations WHERE project_key = ? ORDER BY ordinal", (project_key,)
    ).fetchall()
    output = []
    for row in rows:
        target = review_targets.relation_target(row)
        output.append({
            "subject": {"type": str(row["subject_type"]), "value": str(row["subject_value"])},
            "predicate": str(row["predicate"]),
            "object": {"type": str(row["object_type"]), "value": str(row["object_value"])},
            "classification": str(row["classification"]),
            "source": str(row["source"]),
            "locator": str(row["locator"]),
            "method": str(row["method"]),
            "context": _json(str(row["context_json"]), {}),
            "observed_at": str(row["observed_at"]),
            "summary": (
                f"{row['subject_type']}:{row['subject_value']} {row['predicate']} "
                f"{row['object_type']}:{row['object_value']}"
            ),
            "evidence_label": " · ".join(
                value for value in (str(row["source"]), str(row["locator"]), str(row["method"])) if value
            ),
            "review_target": target,
        })
    return output


def _decision_rows(conn: sqlite3.Connection, project_key: str, project: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    now = dt.datetime.now(dt.timezone.utc)
    rows = conn.execute(
        "SELECT * FROM project_decisions WHERE project_key = ? ORDER BY ordinal", (project_key,)
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        expires_at = str(row["expires_at"])
        expires = project_record.parse_iso(expires_at) if expires_at else None
        cleared_at = str(row["cleared_at"])
        active = not cleared_at and (expires is None or expires > now)
        metadata = _json(str(row["metadata_json"]), {})
        recheck = str(row["kind"]) == "defer_promotion" and metadata.get("fingerprint") and metadata["fingerprint"] != promotion_attention.fingerprint(project)
        active = active and not recheck
        output.append({
            "kind": str(row["kind"]),
            "target_type": str(row["target_type"]),
            "target_value": str(row["target_value"]),
            "note": str(row["note"]),
            "created_at": str(row["created_at"]),
            "expires_at": expires_at,
            "cleared_at": cleared_at,
            "active": active,
            "metadata": metadata,
            "evidence_changed": bool(recheck),
        })
    return {"active": [item for item in output if item["active"]], "history": output}


def _investigation_summary(conn: sqlite3.Connection, project_key: str) -> dict[str, Any] | None:
    run = storage.latest_investigation_run(conn, project_key)
    if run is None:
        return None
    bundle = run["bundle"]
    return {
        "run_id": run["run_id"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "evaluation_version": run["evaluation_version"],
        "evaluation": run["evaluation"],
        "metrics": run["metrics"],
        "record_count": len(bundle.records),
        "failure_count": len(bundle.failures),
        "collectors": [
            {
                **result.to_dict(),
                "label": result.source.replace("_", " "),
                "counts_label": f"{result.request_count} requests · {result.result_count} results",
            }
            for result in bundle.collector_results
        ],
        "failures": [
            {**failure.to_dict(), "review_target": review_targets.failure_target(run["run_id"], failure.to_dict())}
            for failure in bundle.failures
        ],
    }


def investigation_run_list(conn: sqlite3.Connection, *, project_key: str, limit: int = 50) -> dict[str, Any]:
    resolved = project_record.resolve_project_key_input(conn, project_key)
    items = storage.list_investigation_run_summaries(conn, project_key=resolved, limit=limit)
    return {"project_key": resolved, "count": len(items), "items": items}


def investigation_run_detail(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    evidence_limit: int = 100,
) -> dict[str, Any]:
    run = storage.load_investigation_run(conn, run_id)
    if run is None:
        raise KeyError("investigation run not found")
    limit = max(1, min(500, int(evidence_limit)))
    bundle = run["bundle"]
    records = [item.to_dict() for item in bundle.records]
    failures = [item.to_dict() for item in bundle.failures]
    return {
        "run_id": run["run_id"],
        "application_status": run["application_status"],
        "conflict_reason": run["conflict_reason"],
        "resolved_at": run["resolved_at"],
        "project_key": run["project_key"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "evidence_schema_version": run["evidence_schema_version"],
        "evaluation_version": run["evaluation_version"],
        "metrics": run["metrics"],
        "collectors": storage.list_collector_run_rows(conn, run_id),
        "record_count": len(records),
        "records": records[:limit],
        "records_truncated": len(records) > limit,
        "failure_count": len(failures),
        "failures": failures[:limit],
        "failures_truncated": len(failures) > limit,
        "input_project": run["input_project"],
        "output_project": run["output_project"],
        "evaluation": run["evaluation"],
    }


def investigation_replay(conn: sqlite3.Connection, *, run_id: str) -> dict[str, Any]:
    payload = investigation.replay(conn, run_id=run_id)
    return {
        "run_id": payload["run_id"],
        "project_key": payload["project_key"],
        "evaluation": payload["evaluation"],
        "diff": payload["diff"],
    }


def _service_ownership_review(
    conn: sqlite3.Connection,
    project_key: str,
    prepared: dict[str, Any],
) -> dict[str, Any]:
    service_rows = conn.execute(
        """
        SELECT c.run_id, r.completed_at, r.application_status, c.source, c.operation, c.status, c.result_json
        FROM collector_runs c
        JOIN investigation_runs r ON r.run_id = c.run_id
        WHERE r.project_key = ? AND c.source LIKE 'service_%'
        ORDER BY r.completed_at DESC, c.ordinal ASC
        LIMIT 20
        """,
        (project_key,),
    ).fetchall()
    service_verifications = [{
        "run_id": str(row["run_id"]),
        "application_status": str(row["application_status"]),
        "completed_at": str(row["completed_at"]),
        "source": str(row["source"]),
        "operation": str(row["operation"]),
        "status": str(row["status"]),
        "result": _json(str(row["result_json"]), {}),
    } for row in service_rows]

    nsids = conn.execute(
        "SELECT value, role, classification FROM project_identifiers "
        "WHERE project_key = ? AND kind = 'nsid' ORDER BY ordinal",
        (project_key,),
    ).fetchall()
    relation_rows = conn.execute(
        "SELECT * FROM project_relations WHERE project_key = ? ORDER BY ordinal", (project_key,)
    ).fetchall()
    ownership = []
    for identifier in nsids:
        value = str(identifier["value"])
        evidence = [{
            "predicate": str(row["predicate"]),
            "subject_type": str(row["subject_type"]),
            "subject_value": str(row["subject_value"]),
            "source": str(row["source"]),
            "locator": str(row["locator"]),
            "classification": str(row["classification"]),
        } for row in relation_rows if str(row["object_value"]) == value and str(row["predicate"]) in {"defines_schema", "owns_namespace", "serves_collection"}]
        ownership.append({
            "nsid": value,
            "role": str(identifier["role"]),
            "classification": str(identifier["classification"]),
            "evidence": evidence,
            "evidence_status": "observed" if evidence else "not_observed",
        })

    permissions = [{
        "nsid": str(row["object_value"]),
        "classification": str(row["classification"]),
        "source": str(row["source"]),
        "locator": str(row["locator"]),
        "method": str(row["method"]),
        "include_unresolved": str(row["method"]) == "oauth_scope:include",
    } for row in relation_rows if str(row["predicate"]) == "requests_permission"]

    aliases = []
    for item in prepared.get("url_aliases", []):
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias") or "")
        aliases.append({
            **dict(item),
            "actions": {"identifier_type": "service_url", "value": alias} if alias else {},
        })
    return {
        "service_verifications": service_verifications,
        "service_verification_status": "observed" if service_verifications else "not_observed",
        "current_service_status": str(prepared.get("service_status") or "unknown"),
        "current_service_status_confidence": str(prepared.get("service_status_confidence") or "none"),
        "current_service_status_evidence": list(prepared.get("service_status_evidence") or []),
        "ownership_signal": str(prepared.get("ownership_signal") or "unknown"),
        "nsid_ownership": ownership,
        "oauth_permissions": permissions,
        "oauth_status": "observed" if permissions else "not_observed",
        "url_aliases": aliases,
        "url_alias_status": "observed" if aliases else "not_observed",
    }


def project_revision(conn: sqlite3.Connection, project_key: str) -> str:
    row = conn.execute("SELECT * FROM projects WHERE project_key = ?", (project_key,)).fetchone()
    if row is None:
        raise KeyError(project_key)
    return str(concurrency.revision(conn, project_key))


def require_project_revision(conn: sqlite3.Connection, project_key: str, expected: str) -> None:
    if not expected:
        raise ValueError("expected_revision is required")
    actual = project_revision(conn, project_key)
    if actual != expected:
        raise StaleProjectError(f"project changed since it was loaded: {project_key}")
    conn.expected_project_revisions[project_key] = int(actual)


@concurrency.consistent_read
def project_detail(conn: sqlite3.Connection, *, project_key: str) -> dict[str, Any]:
    resolved = project_record.resolve_project_key_input(conn, project_key)
    project = project_record.fetch_project(conn, resolved)
    if project is None:
        raise KeyError(project_key)
    row = conn.execute("SELECT * FROM projects WHERE project_key = ?", (resolved,)).fetchone()
    if row is None:
        raise KeyError(project_key)
    prepared = core.apply_promotion_state(project)
    promotion_signals = core.compute_showcase_signals(prepared, include_ineligible=True)
    promotion_policy = core.promotion_policy_projection(prepared, promotion_signals)
    profile = project_record.project_record_payload(prepared)
    state = _json(str(row["state_json"]), {})
    raw_project = {
        "project_key": resolved,
        "name": str(row["name"]),
        "website_url": str(row["website_url"]),
        "service_status": str(row["service_status"]),
        "tracking_state": str(row["tracking_state"]),
        "introduced_url": str(row["introduced_url"]),
        "profile": profile,
        "features": list(prepared.get("features") or []),
        "editorial": {**dict(prepared.get("editorial") or {}), "automatic": editorial.automatic(prepared)},
        "service_observations": [service.observation_summary(item) for item in prepared.get("service_verification_results", [])
                                 if item.get("lane") in {"static", "deep", "authority", "browser"}
                                 and item.get("requested_url") in prepared.get("service_urls", [])],
        "state": state,
        "system_notes": list(prepared.get("system_notes") or []),
        "first_seen_at": str(row["first_seen_at"]),
        "last_seen_at": str(row["last_seen_at"]),
        "last_enriched_at": str(row["last_enriched_at"] or ""),
    }
    evaluation = {
        "service_status": str(prepared.get("service_status") or "unknown"),
        "service_status_confidence": str(prepared.get("service_status_confidence") or "none"),
        "service_status_evidence": list(prepared.get("service_status_evidence") or []),
        "atproto_contribution_level": str(prepared.get("atproto_contribution_level") or "unknown"),
        "atproto_contribution_score": int(prepared.get("atproto_contribution_score") or 0),
        "atproto_contribution_evidence": list(prepared.get("atproto_contribution_evidence") or []),
        "technical_interest_level": str(prepared.get("technical_interest_level") or "unknown"),
        "technical_interest_score": int(prepared.get("technical_interest_score") or 0),
        "technical_interest_reasons": list(prepared.get("technical_interest_reasons") or []),
        "technical_interest_summary": str(prepared.get("technical_interest_summary") or ""),
        "promotion_ready": bool(prepared.get("promotion_ready")),
        "promotion_blockers": list(prepared.get("promotion_blockers") or []),
    }
    payload = {
        "revision": project_revision(conn, resolved),
        "project": raw_project,
        "identifiers": _identifier_rows(conn, resolved),
        "relations": _relation_rows(conn, resolved),
        "decisions": _decision_rows(conn, resolved, prepared),
        "evaluation": evaluation,
        "promotion_policy": promotion_policy,
        "promotion_signals": promotion_signals,
        "investigation": _investigation_summary(conn, resolved),
        "review": core.build_review_queue_item(conn, resolved),
        "duplicates": [],
        "merge_recommendation": {},
        "merge_analysis_pending": True,
        "service_ownership_review": _service_ownership_review(conn, resolved, prepared),
        "explanations": explain.explain_project_state(prepared, merge_pending=True),
    }
    payload["merge_target_revision"] = ""
    identifiers = payload["identifiers"]
    primary_account = next((item for item in identifiers if item["family"] == "identity" and "account" in item["kinds"]), None)
    payload["overview"] = {
        "website": raw_project["website_url"],
        "confirmed_services": list(prepared.get("service_urls") or []),
        "project_sites": list(prepared.get("project_site_urls") or []),
        "repositories": list(prepared.get("project_repository_urls") or []),
        "account": primary_account["value"] if primary_account else "",
        "app_links": project_record.build_project_app_links(prepared),
        "resource_urls": project_record.normalize_resource_url_records(
            list(prepared.get("resource_url_records") or [])
        ),
        "active_guards": payload["decisions"]["active"],
        "promotion_defer": {
            "suggested_days": core.suggested_promotion_defer_days(project),
            "last_reason": core.latest_promotion_deferral_reason(project),
        },
    }
    return payload


def project_merge_analysis(conn: sqlite3.Connection, *, project_key: str) -> dict[str, Any]:
    resolved = project_record.resolve_project_key_input(conn, project_key)
    revision_before = project_revision(conn, resolved)
    project = project_record.fetch_project(conn, resolved)
    if project is None:
        raise KeyError(project_key)
    prepared = projects.prepare_project_view(conn, project)
    revision_after = project_revision(conn, resolved)
    if revision_before != revision_after:
        raise StaleProjectError(f"project changed during merge analysis: {resolved}")
    recommendation = dict(prepared.get("merge_recommendation") or {})
    target_key = str(recommendation.get("target_project_key") or "")
    target_revision = (
        project_revision(conn, target_key)
        if target_key and conn.execute("SELECT 1 FROM projects WHERE project_key = ?", (target_key,)).fetchone()
        else ""
    )
    explanation = explain.explain_project_state(prepared, recommendation=recommendation, topic="merge_candidate")
    return {
        "project_key": resolved,
        "revision": revision_after,
        "duplicates": list(prepared.get("duplicate_candidates") or []),
        "merge_recommendation": recommendation,
        "merge_target_revision": target_revision,
        "explanation": explanation["topics"]["merge_candidate"],
    }


def seed_preview(conn: sqlite3.Connection, *, seed: str, seed_type: str | None = None) -> dict[str, Any]:
    resolved_type = core.detect_seed_type(seed, seed_type)
    value = project_record.canonical_seed_value(resolved_type, seed)
    exact_key = project_record.find_project_key_by_identifier(conn, resolved_type, value)
    candidates = core.project_search_candidates(conn, value, limit=5)
    return {
        "seed_type": resolved_type,
        "seed_value": value,
        "exact_project_key": exact_key or "",
        "exact": _summary(project_record.fetch_project(conn, exact_key)) if exact_key and project_record.fetch_project(conn, exact_key) else None,
        "candidates": candidates,
        "can_create": exact_key is None,
    }


def confirm_candidate(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    identifier_type: str,
    value: str,
) -> dict[str, Any]:
    project = core.resolve_single_project(conn, project_key=project_key)
    # An explicit operator confirmation supersedes an exact block/defer. If the
    # candidate is invalid, the unsaved in-memory change is discarded below.
    projects._clear_exact_identifier_guards(project, identifier_type, value)
    if identifier_type == "service_url":
        confirmed = core.confirm_service_seed(project, value, source="web_ui")
    elif identifier_type == "repo_url":
        confirmed = core.confirm_repo_seed(project, value, source="web_ui")
    elif identifier_type == "nsid":
        normalized = project_record.canonical_seed_value("nsid", value)
        if normalized not in project.get("input_nsids", []):
            confirmed = ""
        else:
            project["input_nsids"] = [item for item in project.get("input_nsids", []) if item != normalized]
            project["resolved_nsids"] = project_record.add_unique(project.get("resolved_nsids", []), normalized)
            confirmed = normalized
    elif identifier_type == "account":
        normalized = str(value or "").strip().lower()
        candidate = next((
            item for item in project_record.normalize_developer_accounts(list(project.get("candidate_developer_accounts", [])))
            if normalized in {str(item.get("handle") or "").lower(), str(item.get("did") or "").lower()}
        ), None)
        if candidate is None:
            confirmed = ""
        else:
            project_record.add_developer_account(
                project,
                str(candidate.get("handle") or ""),
                str(candidate.get("source") or "web_ui"),
                did=str(candidate.get("did") or ""),
            )
            project["candidate_developer_accounts"] = [
                item for item in project_record.normalize_developer_accounts(list(project.get("candidate_developer_accounts", [])))
                if item != candidate
            ]
            confirmed = str(candidate.get("handle") or candidate.get("did") or "")
    else:
        raise ValueError("identifier_type must be service_url, repo_url, nsid, or account")
    if not confirmed:
        raise ValueError("candidate value is required")
    core.add_confirmed_identifier_decision(
        project,
        identifier_type,
        confirmed,
        source="web_ui",
        note="candidate confirmed by operator",
        role={
            "service_url": "service",
            "repo_url": "project_repository",
            "nsid": "owned_nsid",
        }.get(identifier_type, ""),
    )
    project_record.save_project(
        conn,
        project,
        enriched=bool(project.get("last_enriched_at")),
        preserve_timestamps=True,
    )
    conn.commit()
    return {"project_key": project["project_key"], "type": identifier_type, "value": confirmed}


def reject_candidate(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    identifier_type: str,
    value: str,
    note: str = "",
) -> dict[str, Any]:
    return decisions.set_identifier_block(
        conn,
        project_key=project_key,
        identifier_type=identifier_type,
        value=value,
        note=note or "rejected from web UI",
    )


def queue_payload(
    conn: sqlite3.Connection,
    *,
    queue: str,
    tracking_states: set[str],
    kinds: set[str] | None = None,
    limit: int = 50,
    include_personal_tools: bool = False,
    promotion_view: str = "inbox",
    reason: str = "",
    offset: int = 0,
) -> dict[str, Any]:
    if queue == "review":
        items = review.review_queue(conn, tracking_states=tracking_states, kinds=kinds, limit=limit)
    elif queue == "promotion":
        if promotion_view not in {"inbox", "technical", "lowered", "waiting", "reviewed", "all"}:
            raise ValueError("invalid promotion view")
        items = review.promotion_queue(
            conn,
            tracking_states=tracking_states if promotion_view in {"inbox", "technical"} else None,
            kinds=kinds,
            limit=None,
            include_personal_tools=include_personal_tools,
            view=promotion_view,
        )
        reasons: dict[str, int] = {}
        for item in items:
            notes = {item["priority_reason"], item["last_promotion_deferral_reason"]} - {""}
            for note in notes:
                reasons[note] = reasons.get(note, 0) + 1
        if reason.strip():
            needle = reason.strip().casefold()
            items = [item for item in items if needle in item["priority_reason"].casefold()
                     or needle in item["last_promotion_deferral_reason"].casefold()]
        total = len(items)
        offset = max(0, offset)
        items = items[offset:offset + max(1, limit)]
        return {"queue": queue, "items": items, "count": len(items), "total": total,
                "offset": offset, "has_more": offset + len(items) < total,
                "reasons": [{"reason": key, "count": value} for key, value in sorted(reasons.items())]}
    else:
        raise ValueError(f"unknown queue: {queue}")
    return {"queue": queue, "items": items, "count": len(items)}


def merge_batch_plan(
    conn: sqlite3.Connection,
    *,
    mode: str = "moderate",
    limit: int = 50,
) -> dict[str, Any]:
    plans, skipped = core.build_batch_merge_plan(conn, mode=mode)
    return {"mode": mode, "items": plans[:limit], "count": len(plans), "skipped_count": len(skipped)}


def merge_pair(
    conn: sqlite3.Connection,
    *,
    source_project_key: str,
    target_project_key: str,
    include_first_creator_details: bool = False,
) -> dict[str, Any]:
    result = merge.inspect_pair(
        conn,
        project_key=source_project_key,
        target_project_key=target_project_key,
        include_first_creator_details=include_first_creator_details,
    )
    source = core.resolve_single_project(conn, project_key=source_project_key)
    target = core.resolve_single_project(conn, project_key=target_project_key)

    def snapshot(project: dict[str, Any]) -> dict[str, Any]:
        shaped = project_record.ensure_project_shape(project)
        return {
            **_summary(shaped),
            "seed": {"type": shaped.get("seed_type", ""), "value": shaped.get("seed_value", "")},
            "service_urls": list(shaped.get("service_urls") or []),
            "candidate_service_urls": list(shaped.get("candidate_service_urls") or []),
            "project_repository_urls": list(shaped.get("project_repository_urls") or []),
            "candidate_project_repository_urls": list(shaped.get("candidate_project_repository_urls") or []),
            "input_nsids": list(shaped.get("input_nsids") or []),
            "candidate_nsids": list(shaped.get("candidate_nsids") or []),
            "developer_accounts": list(shaped.get("developer_accounts") or []),
            "candidate_developer_accounts": list(shaped.get("candidate_developer_accounts") or []),
            "identifiers": _identifier_rows(conn, shaped["project_key"]),
        }

    preview = merge.merge_projects(
        conn,
        project_key=source_project_key,
        target_project_key=target_project_key,
        execute=False,
        force=True,
    )
    return {
        **result,
        "source": snapshot(source),
        "target": snapshot(target),
        "merged_preview": preview,
        "source_revision": project_revision(conn, project_record.resolve_project_key_input(conn, source_project_key)),
        "target_revision": project_revision(conn, project_record.resolve_project_key_input(conn, target_project_key)),
    }
