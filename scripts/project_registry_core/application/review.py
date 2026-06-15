from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Callable
from typing import Any

from .. import registry as core


def review_queue(
    conn: sqlite3.Connection,
    *,
    tracking_states: set[str] | None = None,
    kinds: set[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return core.build_review_queue(conn, tracking_states=tracking_states, kinds=kinds, limit=limit)


def promotion_queue(
    conn: sqlite3.Connection,
    *,
    tracking_states: set[str] | None = None,
    kinds: set[str] | None = None,
    limit: int | None = 50,
    include_personal_tools: bool = False,
) -> list[dict[str, Any]]:
    return core.build_showcase_queue(
        conn,
        tracking_states=tracking_states,
        kinds=kinds,
        limit=limit,
        include_personal_tools=include_personal_tools,
    )


def stalled(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    inactivity_days: int = 45,
    max_enrich_age_days: int = 30,
    limit: int | None = None,
    note: str = "",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    inactivity_days = max(1, int(inactivity_days))
    max_enrich_age_days = max(1, int(max_enrich_age_days))
    current_time = now or dt.datetime.now(dt.timezone.utc)
    projects = [
        core.ensure_project_shape(project)
        for project in core.list_projects(conn, tracking_states={"active", "watchlist", "stalled"})
    ]
    candidates = [
        entry
        for project in projects
        if (
            entry := core.stalled_review_entry(
                project,
                now=current_time,
                inactivity_days=inactivity_days,
                max_enrich_age_days=max_enrich_age_days,
            )
        )
    ]
    candidates.sort(
        key=lambda item: (
            str(item.get("suggested_tracking_state") or "") != "active",
            str(item.get("project_key") or ""),
        )
    )
    total_candidate_count = len(candidates)
    suggested_state_counts = _counts(candidates, "suggested_tracking_state")
    if limit is not None:
        candidates = candidates[:max(1, int(limit))]
    if not execute:
        return {
            "mode": "dry_run",
            "inactivity_days": inactivity_days,
            "max_enrich_age_days": max_enrich_age_days,
            "candidate_count": len(candidates),
            "total_candidate_count": total_candidate_count,
            "suggested_state_counts": suggested_state_counts,
            "candidates": candidates,
        }

    updated: list[str] = []
    updated_projects: list[dict[str, str]] = []
    resolved_note = str(
        note
        or (
            "auto-updated after review-stalled "
            f"inactivity_days={inactivity_days} max_enrich_age_days={max_enrich_age_days}"
        )
    )
    for item in candidates:
        project = core.fetch_project(conn, str(item.get("project_key") or ""))
        if not project:
            continue
        project["tracking_state"] = str(
            item.get("suggested_tracking_state") or project.get("tracking_state") or "active"
        )
        if resolved_note and not str(project.get("tracking_note") or "").strip():
            project["tracking_note"] = resolved_note
        core.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
        updated.append(str(project["project_key"]))
        updated_projects.append({
            "project_key": str(project["project_key"]),
            "tracking_state": str(project.get("tracking_state") or ""),
        })
    conn.commit()
    return {
        "mode": "execute",
        "inactivity_days": inactivity_days,
        "max_enrich_age_days": max_enrich_age_days,
        "updated_count": len(updated),
        "total_candidate_count": total_candidate_count,
        "updated_project_keys": updated,
        "updated_projects": updated_projects,
        "suggested_state_counts": suggested_state_counts,
    }


def ignore(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    tracking_states: set[str] | None = None,
    limit: int | None = 100,
    note: str = "",
    note_reason: str = "",
) -> dict[str, Any]:
    return _classification(
        conn,
        review_name="review-ignore",
        target_state="ignore",
        entry_builder=core.ignore_review_entry,
        build_note=core.build_ignore_review_note,
        execute=execute,
        tracking_states=tracking_states,
        limit=limit,
        note=note,
        note_reason=note_reason,
    )


def out_of_scope(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    tracking_states: set[str] | None = None,
    limit: int | None = 100,
    note: str = "",
    note_reason: str = "",
) -> dict[str, Any]:
    return _classification(
        conn,
        review_name="review-out-of-scope",
        target_state="out_of_scope",
        entry_builder=core.out_of_scope_review_entry,
        build_note=core.build_out_of_scope_review_note,
        execute=execute,
        tracking_states=tracking_states,
        limit=limit,
        note=note,
        note_reason=note_reason,
        default_tracking_states={"active", "watchlist", "stable", "stalled", "out_of_scope"},
    )


def _classification(
    conn: sqlite3.Connection,
    *,
    review_name: str,
    target_state: str,
    entry_builder: Callable[[dict[str, Any]], dict[str, Any] | None],
    build_note: Callable[[dict[str, Any], str, str], str],
    execute: bool,
    tracking_states: set[str] | None,
    limit: int | None,
    note: str,
    note_reason: str,
    default_tracking_states: set[str] | None = None,
) -> dict[str, Any]:
    resolved_states = set(
        tracking_states
        or default_tracking_states
        or {"active", "watchlist", "stable", "stalled"}
    )
    projects = [
        core.ensure_project_shape(project)
        for project in core.list_projects(conn, tracking_states=resolved_states)
    ]
    candidates = [entry for project in projects if (entry := entry_builder(project))]
    candidates.sort(key=lambda item: (str(item.get("kind") or ""), str(item.get("project_key") or "")))
    total_candidate_count = len(candidates)
    kind_counts = _counts(candidates, "kind")
    if limit is not None:
        candidates = candidates[:max(1, int(limit))]
    if not execute:
        return {
            "mode": "dry_run",
            "tracking_states": sorted(resolved_states),
            "candidate_count": len(candidates),
            "total_candidate_count": total_candidate_count,
            "kind_counts": kind_counts,
            "candidates": candidates,
        }

    updated: list[str] = []
    updated_notes: list[dict[str, str]] = []
    static_note = str(note or "").strip()
    reason_override = str(note_reason or "").strip()
    for item in candidates:
        project = core.fetch_project(conn, str(item.get("project_key") or ""))
        if not project:
            continue
        next_state = str(item.get("suggested_tracking_state") or target_state)
        project["tracking_state"] = next_state
        resolved_note = static_note
        if reason_override:
            resolved_note = build_note(item, reason_override, static_note)
        elif not resolved_note:
            resolved_note = f"auto-updated after {review_name}"
        if next_state == "active" and str(item.get("kind") or "") == "owned_protocol_surface_detected":
            project["tracking_note"] = "reactivated after owned protocol surface detection"
        elif resolved_note and not str(project.get("tracking_note") or "").strip():
            project["tracking_note"] = resolved_note
        core.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
        updated.append(str(project["project_key"]))
        updated_notes.append({
            "project_key": str(project["project_key"]),
            "tracking_note": str(project.get("tracking_note") or ""),
        })
    conn.commit()
    return {
        "mode": "execute",
        "tracking_states": sorted(resolved_states),
        "updated_count": len(updated),
        "total_candidate_count": total_candidate_count,
        "updated_project_keys": updated,
        "updated_tracking_notes": updated_notes,
        "kind_counts": kind_counts,
    }


def _counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts
