from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from typing import Any

from .. import registry as core
from .. import storage
from . import decisions, merge, projects, review


REMOVED_COMPATIBILITY_FIELDS = {
    "website_url_candidates",
    "github_repo_urls",
    "tangled_repo_urls",
    "resource_urls",
    "introduced",
}
WEB_API_VERSION = 2
CLASSIFICATION_ORDER = ("confirmed", "candidate", "derived", "heuristic")


class StaleProjectError(RuntimeError):
    pass


def _json(value: str, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


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
        "last_seen_at": str(project.get("last_seen_at") or ""),
        "last_enriched_at": str(project.get("last_enriched_at") or ""),
        "has_candidates": bool(project.get("service_url_candidates") or project.get("candidate_source_code_urls")),
        "has_errors": bool(project.get("last_run_errors")),
        "promotion_hold": core.active_promotion_deferral(project) is not None,
    }


def search_projects(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    tracking_states: set[str] | None = None,
    service_statuses: set[str] | None = None,
    contribution_levels: set[str] | None = None,
    technical_interest_levels: set[str] | None = None,
    introduced: str = "any",
    has_candidates: str = "any",
    has_errors: str = "any",
    promotion_hold: str = "any",
    seen_since: str = "",
    enriched_before: str = "",
    sort: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    search = str(query or "").strip()
    projects_list = core.list_projects(conn, tracking_states=tracking_states)

    def matches(project: dict[str, Any]) -> bool:
        if service_statuses and str(project.get("service_status") or "") not in service_statuses:
            return False
        if contribution_levels and str(project.get("atproto_contribution_level") or "unknown") not in contribution_levels:
            return False
        if technical_interest_levels and str(project.get("technical_interest_level") or "unknown") not in technical_interest_levels:
            return False
        checks = (
            (introduced, bool(project.get("introduced_url"))),
            (has_candidates, bool(project.get("service_url_candidates") or project.get("candidate_source_code_urls"))),
            (has_errors, bool(project.get("last_run_errors"))),
            (promotion_hold, core.active_promotion_deferral(project) is not None),
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
        ranked = core.project_search_candidates(conn, search, limit=max(len(filtered), 1), projects=filtered)
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
    family_specs = {
        "service_url": ("service", "Services"),
        "repo_url": ("repository", "Repositories"),
        "nsid": ("schema", "NSIDs"),
        "account": ("identity", "Identities"),
        "did": ("identity", "Identities"),
        "domain": ("identity", "Identities"),
    }
    role_labels = {
        "primary_nsid": "primary", "input_nsid": "input", "owned_nsid": "owned",
        "observed_lexicon": "observed", "recorded_namespace": "namespace",
        "manual_seed": "manual seed", "source_repository": "source",
        "source_repository_record": "source record", "developer_account": "developer",
        "project_account": "project", "authority_domain": "authority",
    }
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in raw:
        family, family_label = family_specs.get(item["kind"], (f"other:{item['kind']}", item["kind"]))
        key = (family, item["value"])
        group = grouped.setdefault(key, {
            "family": family,
            "family_label": family_label,
            "value": item["value"],
            "kinds": [],
            "roles": [],
            "role_labels": [],
            "classifications": [],
            "providers": [],
            "is_primary": False,
            "metadata": [],
            "observed_at": "",
        })
        for field, value in (
            ("kinds", item["kind"]),
            ("roles", item["role"]),
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
        action_kind = next((kind for kind in group["kinds"] if kind in {"service_url", "repo_url", "nsid"}), "")
        candidate_only = "candidate" in group["classifications"] and "confirmed" not in group["classifications"]
        group["actions"] = {
            "identifier_type": action_kind,
            "confirm": candidate_only and action_kind in {"service_url", "repo_url"},
            "reject": candidate_only and action_kind in {"service_url", "repo_url"},
            "block": bool(action_kind),
            "defer": bool(action_kind),
            "split": bool(action_kind),
        }
        output.append(group)
    return output


def _relation_rows(conn: sqlite3.Connection, project_key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM project_relations WHERE project_key = ? ORDER BY ordinal", (project_key,)
    ).fetchall()
    return [
        {
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
        }
        for row in rows
    ]


def _decision_rows(conn: sqlite3.Connection, project_key: str) -> dict[str, list[dict[str, Any]]]:
    now = dt.datetime.now(dt.timezone.utc)
    rows = conn.execute(
        "SELECT * FROM project_decisions WHERE project_key = ? ORDER BY ordinal", (project_key,)
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        expires_at = str(row["expires_at"])
        expires = core.parse_iso(expires_at) if expires_at else None
        cleared_at = str(row["cleared_at"])
        active = not cleared_at and (expires is None or expires > now)
        output.append({
            "kind": str(row["kind"]),
            "target_type": str(row["target_type"]),
            "target_value": str(row["target_value"]),
            "note": str(row["note"]),
            "created_at": str(row["created_at"]),
            "expires_at": expires_at,
            "cleared_at": cleared_at,
            "active": active,
            "metadata": _json(str(row["metadata_json"]), {}),
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
        "failures": [failure.to_dict() for failure in bundle.failures],
    }


def project_revision(conn: sqlite3.Connection, project_key: str) -> str:
    row = conn.execute("SELECT * FROM projects WHERE project_key = ?", (project_key,)).fetchone()
    if row is None:
        raise KeyError(project_key)
    payload = {
        "project": tuple(row),
        "identifiers": [tuple(item) for item in conn.execute(
            "SELECT * FROM project_identifiers WHERE project_key = ? ORDER BY ordinal", (project_key,)
        )],
        "relations": [tuple(item) for item in conn.execute(
            "SELECT * FROM project_relations WHERE project_key = ? ORDER BY ordinal", (project_key,)
        )],
        "decisions": [tuple(item) for item in conn.execute(
            "SELECT * FROM project_decisions WHERE project_key = ? ORDER BY ordinal", (project_key,)
        )],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def require_project_revision(conn: sqlite3.Connection, project_key: str, expected: str) -> None:
    if not expected:
        raise ValueError("expected_revision is required")
    actual = project_revision(conn, project_key)
    if actual != expected:
        raise StaleProjectError(f"project changed since it was loaded: {project_key}")


def project_detail(conn: sqlite3.Connection, *, project_key: str) -> dict[str, Any]:
    resolved = core.resolve_project_key_input(conn, project_key)
    project = core.fetch_project(conn, resolved)
    if project is None:
        raise KeyError(project_key)
    row = conn.execute("SELECT * FROM projects WHERE project_key = ?", (resolved,)).fetchone()
    if row is None:
        raise KeyError(project_key)
    prepared = projects.prepare_project_view(conn, project)
    profile = _json(str(row["profile_json"]), {})
    state = _json(str(row["state_json"]), {})
    raw_project = {
        "project_key": resolved,
        "name": str(row["name"]),
        "website_url": str(row["website_url"]),
        "service_status": str(row["service_status"]),
        "tracking_state": str(row["tracking_state"]),
        "introduced_url": str(row["introduced_url"]),
        "profile": profile,
        "state": state,
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
        "decisions": _decision_rows(conn, resolved),
        "evaluation": evaluation,
        "investigation": _investigation_summary(conn, resolved),
        "review": core.build_review_queue_item(conn, resolved),
        "duplicates": prepared.get("duplicate_candidates", []),
        "merge_recommendation": prepared.get("merge_recommendation", {}),
    }
    merge_target_key = str(payload["merge_recommendation"].get("target_project_key") or "")
    payload["merge_target_revision"] = (
        project_revision(conn, merge_target_key)
        if merge_target_key and conn.execute(
            "SELECT 1 FROM projects WHERE project_key = ?", (merge_target_key,)
        ).fetchone()
        else ""
    )
    identifiers = payload["identifiers"]
    manual_seed = next((item for item in identifiers if "manual_seed" in item["roles"]), None)
    primary_repo = next((item for item in identifiers if item["family"] == "repository" and item["is_primary"]), None)
    primary_account = next((item for item in identifiers if item["family"] == "identity" and "account" in item["kinds"]), None)
    payload["overview"] = {
        "website": raw_project["website_url"],
        "repository": primary_repo["value"] if primary_repo else "",
        "account": primary_account["value"] if primary_account else "",
        "manual_seed": manual_seed,
        "active_guards": payload["decisions"]["active"],
    }
    if REMOVED_COMPATIBILITY_FIELDS.intersection(payload["project"]):
        raise AssertionError("removed compatibility fields leaked into Web payload")
    return payload


def seed_preview(conn: sqlite3.Connection, *, seed: str, seed_type: str | None = None) -> dict[str, Any]:
    resolved_type = core.detect_seed_type(seed, seed_type)
    value = core.canonical_seed_value(resolved_type, seed)
    exact_key = core.find_project_key_by_identifier(conn, resolved_type, value)
    candidates = core.project_search_candidates(conn, value, limit=5)
    return {
        "seed_type": resolved_type,
        "seed_value": value,
        "exact_project_key": exact_key or "",
        "exact": _summary(core.fetch_project(conn, exact_key)) if exact_key and core.fetch_project(conn, exact_key) else None,
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
    if identifier_type == "service_url":
        confirmed = core.confirm_service_seed(project, value, source="web_ui")
    elif identifier_type == "repo_url":
        confirmed = core.confirm_repo_seed(project, value, source="web_ui")
    else:
        raise ValueError("identifier_type must be service_url or repo_url")
    if not confirmed:
        raise ValueError("candidate value is required")
    core.add_confirmed_identifier_decision(
        project,
        identifier_type,
        confirmed,
        source="web_ui",
        note="candidate confirmed by operator",
    )
    core.save_project(
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
) -> dict[str, Any]:
    if queue == "review":
        items = review.review_queue(conn, tracking_states=tracking_states, kinds=kinds, limit=limit)
    elif queue == "promotion":
        items = review.promotion_queue(
            conn,
            tracking_states=tracking_states,
            kinds=kinds,
            limit=limit,
            include_personal_tools=include_personal_tools,
        )
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


def merge_pair(conn: sqlite3.Connection, *, source_project_key: str, target_project_key: str) -> dict[str, Any]:
    result = merge.inspect_pair(
        conn,
        project_key=source_project_key,
        target_project_key=target_project_key,
    )
    return {
        **result,
        "source_revision": project_revision(conn, core.resolve_project_key_input(conn, source_project_key)),
        "target_revision": project_revision(conn, core.resolve_project_key_input(conn, target_project_key)),
    }
