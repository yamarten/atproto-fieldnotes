from __future__ import annotations

from project_registry_core import project_review

from project_registry_core import project_merge

import sqlite3
from typing import Any

from .. import registry as core
from ..persistence import concurrency
from project_registry_core import project_record
from project_registry_core import promotion_attention
from project_registry_core import atproto_usage


def set_atproto_usage(conn: sqlite3.Connection, *, project_key: str, role: str = "unknown",
                      reason: str = "", clear: bool = False, focus: str = "auto") -> dict[str, Any]:
    project = core.resolve_single_project(conn, project_key=project_key)
    atproto_usage.set_override(project, role=role, reason=reason,
                               assessed_at=project_record.iso_now(), clear=clear, focus=focus)
    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")),
                                preserve_timestamps=True)
    return {"project_key": project["project_key"], "atproto_usage": atproto_usage.projection(project)}


UNAMBIGUOUS_COMPONENT_ROLES = {
    "service_url": "service",
    "repo_url": "project_repository",
    "nsid": "owned_nsid",
}


def _refresh_merge_state(conn: sqlite3.Connection, project: dict[str, Any]) -> dict[str, Any]:
    project["duplicate_candidates"] = project_merge.compute_duplicate_candidates(conn, project)
    project["merge_recommendation"] = project_merge.compute_merge_recommendation(project)
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
    with concurrency.write_transaction(conn):
        concurrency.check_projects(conn, [project, other])
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
        blocked.append({
            "type": identifier_type,
            "value": normalized_value,
            "role": UNAMBIGUOUS_COMPONENT_ROLES.get(identifier_type, ""),
            "note": note.strip(),
            "added_at": project_record.iso_now(),
        })
    project["blocked_identifiers"] = blocked
    if not clear:
        project["deferred_identifiers"] = [item for item in project.get("deferred_identifiers", [])
                                           if (item.get("type"), item.get("value")) != key]
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
        "project_repository_urls": project.get("project_repository_urls", []),
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
        project["blocked_identifiers"] = [item for item in project.get("blocked_identifiers", [])
                                          if (item.get("type"), item.get("value")) != (identifier_type, normalized_value)]
        core.clear_confirmed_identifier_decision(project, identifier_type, normalized_value)
        core.defer_identifier_on_project(
            project,
            identifier_type,
            normalized_value,
            note=note.strip(),
            role=UNAMBIGUOUS_COMPONENT_ROLES.get(identifier_type, ""),
            days=days,
        )
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
        "project_repository_urls": project.get("project_repository_urls", []),
        "candidate_project_repository_urls": project.get("candidate_project_repository_urls", []),
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
        project_review.clear_active_promotion_deferrals(project)
        promotion_attention.record(project, "restore")
        entry = None
    else:
        entry = project_review.defer_promotion_on_project(project, reason=reason.strip(), days=days)
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
        "active_promotion_deferral": project_review.active_promotion_deferral(project),
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
    entry = project_review.set_promotion_priority_on_project(
        project,
        lowered=lowered,
        reason=reason,
    )
    promotion_attention.record(project, "lower" if lowered else "restore")
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


def acknowledge_review(
    conn: sqlite3.Connection, *, project_key: str, fingerprint: str, clear: bool = False,
) -> dict[str, Any]:
    project = core.resolve_single_project(conn, project_key=project_key)
    entries = [dict(item) for item in project.get("review_acknowledgements", [])]
    now = project_record.iso_now()
    matching = [item for item in entries if item.get("kind") == "acknowledge_review"
                and item.get("target_value") == fingerprint]
    if clear:
        if not matching:
            raise ValueError("review decision does not belong to this project")
        for item in matching:
            if not item.get("cleared_at"):
                item["cleared_at"] = now
    else:
        signal = next((item for item in project_review.compute_review_signals(project)
                       if item.get("fingerprint") == fingerprint), None)
        if signal is None:
            raise ValueError("review change is stale or does not belong to this project")
        entries.append({"kind": "acknowledge_review", "target_type": "review_change",
                        "target_value": fingerprint, "created_at": now,
                        "note": "", "expires_at": "", "cleared_at": "",
                        "metadata": {"signal_kind": signal["kind"], "details": signal["details"]}})
    project["review_acknowledgements"] = entries
    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")),
                                preserve_timestamps=True)
    return {"project_key": project["project_key"], "fingerprint": fingerprint, "cleared": clear}
