from __future__ import annotations

import sqlite3
import datetime as dt
from typing import Any

from .. import registry as core
from . import review_targets
from project_registry_core import project_record


def _refresh_merge_state(conn: sqlite3.Connection, project: dict[str, Any]) -> dict[str, Any]:
    project["duplicate_candidates"] = core.compute_duplicate_candidates(conn, project)
    project["merge_recommendation"] = core.compute_merge_recommendation(project)
    return project


def set_separate(
    conn: sqlite3.Connection,
    *,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
    other_project_key: str | None = None,
    other_seed: str | None = None,
    other_seed_type: str | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    project = project_record.ensure_project_shape(core.resolve_single_project(conn, project_key=project_key, seed=seed, seed_type=seed_type))
    other = project_record.ensure_project_shape(core.resolve_single_project(conn, project_key=other_project_key, seed=other_seed, seed_type=other_seed_type))
    if project["project_key"] == other["project_key"]:
        raise ValueError("project and other project resolve to the same project")
    project_blocked = set(project.get("separate_project_keys", []))
    other_blocked = set(other.get("separate_project_keys", []))
    if clear:
        project_blocked.discard(other["project_key"])
        other_blocked.discard(project["project_key"])
    else:
        project_blocked.add(other["project_key"])
        other_blocked.add(project["project_key"])
    project["separate_project_keys"] = sorted(project_blocked)
    other["separate_project_keys"] = sorted(other_blocked)
    with conn:
        for item in (project, other):
            _refresh_merge_state(conn, item)
            project_record.save_project(
                conn,
                item,
                enriched=bool(item.get("last_enriched_at")),
                preserve_timestamps=True,
                commit=False,
            )
    return {
        "mode": "clear" if clear else "set",
        "project_key": project["project_key"],
        "other_project_key": other["project_key"],
        "project_separate_project_keys": project.get("separate_project_keys", []),
        "other_separate_project_keys": other.get("separate_project_keys", []),
    }


def set_identifier_block(
    conn: sqlite3.Connection,
    *,
    identifier_type: str,
    value: str,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
    note: str = "",
    clear: bool = False,
) -> dict[str, Any]:
    project = project_record.ensure_project_shape(core.resolve_single_project(conn, project_key=project_key, seed=seed, seed_type=seed_type))
    normalized_value = project_record.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized_value:
        raise ValueError("identifier value could not be normalized")
    blocked = project_record.normalize_blocked_identifiers(list(project.get("blocked_identifiers", [])))
    key = (identifier_type, normalized_value)
    if clear:
        blocked = [item for item in blocked if (str(item.get("type") or ""), str(item.get("value") or "")) != key]
    elif not any((str(item.get("type") or ""), str(item.get("value") or "")) == key for item in blocked):
        blocked.append({"type": identifier_type, "value": normalized_value, "note": note.strip(), "added_at": project_record.iso_now()})
    project["blocked_identifiers"] = blocked
    if not clear:
        core.clear_confirmed_identifier_decision(project, identifier_type, normalized_value)
        core.remove_identifier_from_project(
            project,
            identifier_type,
            normalized_value,
            remove_normalized_rows=True,
        )
    project = _refresh_merge_state(conn, core.finalize_project_shape(project))
    project_record.save_project(
        conn,
        project,
        enriched=bool(project.get("last_enriched_at")),
        preserve_timestamps=True,
    )
    conn.commit()
    return {
        "mode": "clear" if clear else "set",
        "project_key": project["project_key"],
        "identifier_type": identifier_type,
        "value": normalized_value,
        "blocked_identifiers": project.get("blocked_identifiers", []),
        "website_url": project.get("website_url", ""),
        "service_urls": project.get("service_urls", []),
        "source_code_urls": project.get("source_code_urls", []),
        "input_nsids": project.get("input_nsids", []),
    }


def set_identifier_defer(
    conn: sqlite3.Connection,
    *,
    identifier_type: str,
    value: str,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
    note: str = "",
    days: int = core.DEFAULT_DEFER_IDENTIFIER_DAYS,
    clear: bool = False,
) -> dict[str, Any]:
    project = project_record.ensure_project_shape(core.resolve_single_project(conn, project_key=project_key, seed=seed, seed_type=seed_type))
    normalized_value = project_record.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized_value:
        raise ValueError("identifier value could not be normalized")
    if clear:
        project["deferred_identifiers"] = [
            item for item in project_record.normalize_deferred_identifiers(list(project.get("deferred_identifiers", [])))
            if (str(item.get("type") or ""), str(item.get("value") or "")) != (identifier_type, normalized_value)
        ]
    else:
        core.clear_confirmed_identifier_decision(project, identifier_type, normalized_value)
        core.defer_identifier_on_project(project, identifier_type, normalized_value, note=note.strip(), days=days)
        project = core.finalize_project_shape(project)
    project = _refresh_merge_state(conn, project)
    project_record.save_project(
        conn,
        project,
        enriched=bool(project.get("last_enriched_at")),
        preserve_timestamps=True,
    )
    conn.commit()
    return {
        "mode": "clear" if clear else "set",
        "project_key": project["project_key"],
        "identifier_type": identifier_type,
        "value": normalized_value,
        "deferred_identifiers": project.get("deferred_identifiers", []),
        "website_url": project.get("website_url", ""),
        "service_urls": project.get("service_urls", []),
        "service_url_candidates": project.get("service_url_candidates", []),
        "source_code_urls": project.get("source_code_urls", []),
        "candidate_source_code_urls": project.get("candidate_source_code_urls", []),
        "input_nsids": project.get("input_nsids", []),
    }


def set_promotion_defer(
    conn: sqlite3.Connection,
    *,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
    reason: str = "",
    days: int | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    project = project_record.ensure_project_shape(core.resolve_single_project(conn, project_key=project_key, seed=seed, seed_type=seed_type))
    if clear:
        core.clear_active_promotion_deferrals(project)
        entry = None
    else:
        entry = core.defer_promotion_on_project(project, reason=reason.strip(), days=days)
    project_record.save_project(
        conn,
        project,
        enriched=bool(project.get("last_enriched_at")),
        preserve_timestamps=True,
    )
    conn.commit()
    return {
        "mode": "clear" if clear else "set",
        "project_key": project["project_key"],
        "promotion_deferral": entry,
        "active_promotion_deferral": core.active_promotion_deferral(project),
        "promotion_deferrals": project.get("promotion_deferrals", []),
    }


def set_promotion_priority(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    lowered: bool,
    reason: str = "",
) -> dict[str, Any]:
    project = project_record.ensure_project_shape(
        core.resolve_single_project(conn, project_key=project_key)
    )
    entry = core.set_promotion_priority_on_project(
        project,
        lowered=lowered,
        reason=reason,
    )
    project_record.save_project(
        conn,
        project,
        enriched=bool(project.get("last_enriched_at")),
        preserve_timestamps=True,
    )
    conn.commit()
    return {
        "project_key": project["project_key"],
        "priority_group": "lowered" if entry else "normal",
        "priority_reason": str((entry or {}).get("reason") or ""),
        "promotion_priority_adjustments": project.get("promotion_priority_adjustments", []),
    }


def dismiss_review_kind(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    kind: str,
    clear: bool = False,
) -> dict[str, Any]:
    project = project_record.fetch_project(conn, project_record.resolve_project_key_input(conn, project_key))
    if project is None:
        raise ValueError(f"project not found: {project_key}")
    kinds = [str(item) for item in project.get("dismissed_review_kinds", []) if str(item)]
    if clear:
        kinds = [item for item in kinds if item != kind]
    else:
        kinds = project_record.unique_list(kinds + [kind])
    project["dismissed_review_kinds"] = kinds
    project_record.save_project(
        conn,
        project,
        enriched=bool(project.get("last_enriched_at")),
        preserve_timestamps=True,
    )
    conn.commit()
    return {"project_key": project["project_key"], "kind": kind, "cleared": clear, "dismissed_review_kinds": kinds}


def set_review_target(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    target_type: str,
    target_value: str,
    action: str,
    note: str = "",
    days: int = 30,
    clear: bool = False,
) -> dict[str, Any]:
    project = core.resolve_single_project(conn, project_key=project_key)
    available = review_targets.available_targets(conn, str(project["project_key"]))
    target = available.get(str(target_value))
    if target is None or target["target_type"] != target_type:
        raise ValueError("review target is stale or does not belong to this project")
    kind = "dismiss_review_target" if action == "dismiss" else "defer_review_target" if action == "defer" else ""
    if not kind:
        raise ValueError("review target action must be dismiss or defer")
    now = project_record.iso_now()
    entries = [dict(item) for item in project.get("review_target_decisions", []) if isinstance(item, dict)]
    for item in entries:
        if item.get("kind") == kind and item.get("target_type") == target_type and item.get("target_value") == target_value and not item.get("cleared_at"):
            item["cleared_at"] = now
    if not clear:
        expires_at = ""
        if kind == "defer_review_target":
            expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=max(1, min(365, int(days))))).isoformat()
        entries.append({
            "kind": kind,
            "target_type": target_type,
            "target_value": target_value,
            "note": note.strip(),
            "created_at": now,
            "expires_at": expires_at,
            "cleared_at": "",
            "metadata": dict(target["metadata"]),
        })
    project["review_target_decisions"] = entries
    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True)
    conn.commit()
    return {"project_key": project["project_key"], "kind": kind, "target_type": target_type, "target_value": target_value, "cleared": clear}
