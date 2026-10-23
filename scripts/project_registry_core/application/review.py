from __future__ import annotations

import shlex

from project_registry_core import project_review

import datetime as dt
import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from project_registry_core import project_record
from project_registry_core.persistence import runtime_view


@dataclass
class ReviewSnapshot:
    """Keep candidate-selection project revisions until the review is applied."""

    projects: dict[str, dict[str, Any]]
    candidates: list[dict[str, Any]]

    def counts(self, key: str) -> dict[str, int]:
        return dict(Counter(str(item.get(key) or "") for item in self.candidates))


def _review_snapshot(
    conn: sqlite3.Connection,
    *,
    tracking_states: set[str],
    entry_builder: Callable[[dict[str, Any]], dict[str, Any] | None],
    sort_key: Callable[[dict[str, Any]], tuple[Any, ...]],
) -> ReviewSnapshot:
    projects = [
        project_record.ensure_project_shape(project)
        for project in project_record.list_projects(conn, tracking_states=tracking_states)
    ]
    candidates = [entry for project in projects if (entry := entry_builder(project))]
    candidates.sort(key=sort_key)
    return ReviewSnapshot({str(project["project_key"]): project for project in projects}, candidates)


def stalled_review_snapshot(
    conn: sqlite3.Connection,
    *,
    now: dt.datetime,
    inactivity_days: int,
    max_enrich_age_days: int,
) -> ReviewSnapshot:
    return _review_snapshot(
        conn,
        tracking_states={"active", "stalled"},
        entry_builder=lambda project: project_review.stalled_review_entry(
            project, now=now, inactivity_days=inactivity_days, max_enrich_age_days=max_enrich_age_days,
        ),
        sort_key=lambda item: (
            str(item.get("suggested_tracking_state") or "") != "active",
            str(item.get("project_key") or ""),
        ),
    )


def classification_review_snapshot(
    conn: sqlite3.Connection,
    *,
    tracking_states: set[str],
    entry_builder: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> ReviewSnapshot:
    return _review_snapshot(
        conn, tracking_states=tracking_states, entry_builder=entry_builder,
        sort_key=lambda item: (str(item.get("kind") or ""), str(item.get("project_key") or "")),
    )


def apply_review_candidate(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    item: dict[str, Any],
    *,
    note: str,
    default_state: str = "",
) -> None:
    """Save the selected snapshot; do not reload and lose its revision check."""
    project["tracking_state"] = str(
        item.get("suggested_tracking_state") or default_state or project.get("tracking_state") or "active"
    )
    if project["tracking_state"] == "active" and str(item.get("kind") or "") == "owned_protocol_surface_detected":
        project["tracking_note"] = "reactivated after owned protocol surface detection"
    elif note and not str(project.get("tracking_note") or "").strip():
        project["tracking_note"] = note
    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))


def _candidate_id(item: dict[str, Any]) -> str:
    identity = {
        key: item.get(key)
        for key in ("project_key", "kind", "suggested_tracking_state", "reason", "project_revision")
        if key in item
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"candidate:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:20]}"


def _plan(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    decorated = [{**item, "candidate_id": _candidate_id(item)} for item in candidates]
    encoded = json.dumps([item["candidate_id"] for item in decorated], separators=(",", ":"))
    return decorated, f"plan:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _select_plan(
    candidates: list[dict[str, Any]],
    *,
    selected_candidate_ids: set[str] | None,
    expected_plan_fingerprint: str,
) -> tuple[list[dict[str, Any]], str]:
    decorated, fingerprint = _plan(candidates)
    if expected_plan_fingerprint and expected_plan_fingerprint != fingerprint:
        raise ValueError("review plan changed; run dry-run again")
    if selected_candidate_ids is not None:
        available = {str(item["candidate_id"]) for item in decorated}
        unknown = selected_candidate_ids - available
        if unknown:
            raise ValueError("review plan contains stale candidate ids")
        decorated = [item for item in decorated if item["candidate_id"] in selected_candidate_ids]
    return decorated, fingerprint


def stalled(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    inactivity_days: int = 45,
    max_enrich_age_days: int = 30,
    limit: int | None = None,
    note: str = "",
    now: dt.datetime | None = None,
    selected_candidate_ids: set[str] | None = None,
    expected_plan_fingerprint: str = "",
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    inactivity_days = max(1, int(inactivity_days))
    max_enrich_age_days = max(1, int(max_enrich_age_days))
    snapshot = stalled_review_snapshot(
        conn, now=now or dt.datetime.now(dt.timezone.utc),
        inactivity_days=inactivity_days, max_enrich_age_days=max_enrich_age_days,
    )
    candidates = snapshot.candidates
    total_candidate_count = len(candidates)
    suggested_state_counts = snapshot.counts("suggested_tracking_state")
    if limit is not None:
        candidates = candidates[:max(1, int(limit))]
    project_snapshots = snapshot.projects
    for item in candidates:
        item["project_revision"] = project_snapshots[str(item["project_key"])][runtime_view.RUNTIME_PROJECT_REVISION]
    candidates, plan_fingerprint = _select_plan(
        candidates,
        selected_candidate_ids=selected_candidate_ids,
        expected_plan_fingerprint=expected_plan_fingerprint,
    )
    if not execute:
        if progress:
            for index, item in enumerate(candidates, 1):
                progress(index, len(candidates), item)
        return {
            "mode": "dry_run",
            "inactivity_days": inactivity_days,
            "max_enrich_age_days": max_enrich_age_days,
            "candidate_count": len(candidates),
            "total_candidate_count": total_candidate_count,
            "suggested_state_counts": suggested_state_counts,
            "candidates": candidates,
            "plan_fingerprint": plan_fingerprint,
        }

    conflicts: list[dict[str, str]] = []
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
        if cancel_requested and cancel_requested():
            break
        project = project_snapshots.get(str(item.get("project_key") or ""))
        if not project:
            continue
        try:
            apply_review_candidate(conn, project, item, note=resolved_note)
        except project_record.StaleProjectWriteError as exc:
            conflicts.append({"project_key": str(project["project_key"]), "error": str(exc)})
            continue
        updated.append(str(project["project_key"]))
        updated_projects.append({
            "project_key": str(project["project_key"]),
            "tracking_state": str(project.get("tracking_state") or ""),
        })
        if progress:
            progress(len(updated), len(candidates), item)
    conn.commit()
    return {
        "mode": "execute",
        "operation_status": "retry_wait" if conflicts else "completed",
        "conflicted_projects": conflicts,
        "inactivity_days": inactivity_days,
        "max_enrich_age_days": max_enrich_age_days,
        "updated_count": len(updated),
        "total_candidate_count": total_candidate_count,
        "updated_project_keys": updated,
        "updated_projects": updated_projects,
        "suggested_state_counts": suggested_state_counts,
        "plan_fingerprint": plan_fingerprint,
        "cancelled": len(updated) < len(candidates) and bool(cancel_requested and cancel_requested()),
    }

def ignore(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    tracking_states: set[str] | None = None,
    limit: int | None = 100,
    note: str = "",
    note_reason: str = "",
    selected_candidate_ids: set[str] | None = None,
    expected_plan_fingerprint: str = "",
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    return _classification(
        conn,
        review_name="review-ignore",
        target_state="ignore",
        entry_builder=project_review.ignore_review_entry,
        build_note=project_review.build_ignore_review_note,
        execute=execute,
        tracking_states=tracking_states,
        limit=limit,
        note=note,
        note_reason=note_reason,
        selected_candidate_ids=selected_candidate_ids,
        expected_plan_fingerprint=expected_plan_fingerprint,
        progress=progress,
        cancel_requested=cancel_requested,
    )


def out_of_scope(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    tracking_states: set[str] | None = None,
    limit: int | None = 100,
    note: str = "",
    note_reason: str = "",
    selected_candidate_ids: set[str] | None = None,
    expected_plan_fingerprint: str = "",
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    return _classification(
        conn,
        review_name="review-out-of-scope",
        target_state="out_of_scope",
        entry_builder=project_review.out_of_scope_review_entry,
        build_note=project_review.build_out_of_scope_review_note,
        execute=execute,
        tracking_states=tracking_states,
        limit=limit,
        note=note,
        note_reason=note_reason,
        default_tracking_states={"active", "stable", "stalled", "out_of_scope"},
        selected_candidate_ids=selected_candidate_ids,
        expected_plan_fingerprint=expected_plan_fingerprint,
        progress=progress,
        cancel_requested=cancel_requested,
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
    selected_candidate_ids: set[str] | None = None,
    expected_plan_fingerprint: str = "",
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    resolved_states = set(
        tracking_states
        or default_tracking_states
        or {"active", "stable", "stalled"}
    )
    snapshot = classification_review_snapshot(conn, tracking_states=resolved_states, entry_builder=entry_builder)
    candidates = snapshot.candidates
    total_candidate_count = len(candidates)
    kind_counts = snapshot.counts("kind")
    if limit is not None:
        candidates = candidates[:max(1, int(limit))]
    project_snapshots = snapshot.projects
    for item in candidates:
        item["project_revision"] = project_snapshots[str(item["project_key"])][runtime_view.RUNTIME_PROJECT_REVISION]
    candidates, plan_fingerprint = _select_plan(
        candidates,
        selected_candidate_ids=selected_candidate_ids,
        expected_plan_fingerprint=expected_plan_fingerprint,
    )
    if not execute:
        if progress:
            for index, item in enumerate(candidates, 1):
                progress(index, len(candidates), item)
        return {
            "mode": "dry_run",
            "tracking_states": sorted(resolved_states),
            "candidate_count": len(candidates),
            "total_candidate_count": total_candidate_count,
            "kind_counts": kind_counts,
            "candidates": candidates,
            "plan_fingerprint": plan_fingerprint,
        }

    conflicts: list[dict[str, str]] = []
    updated: list[str] = []
    updated_notes: list[dict[str, str]] = []
    static_note = str(note or "").strip()
    reason_override = str(note_reason or "").strip()
    for item in candidates:
        if cancel_requested and cancel_requested():
            break
        project = project_snapshots.get(str(item.get("project_key") or ""))
        if not project:
            continue
        resolved_note = static_note
        if reason_override:
            resolved_note = build_note(item, reason_override, static_note)
        elif not resolved_note:
            resolved_note = f"auto-updated after {review_name}"
        try:
            apply_review_candidate(conn, project, item, note=resolved_note, default_state=target_state)
        except project_record.StaleProjectWriteError as exc:
            conflicts.append({"project_key": str(project["project_key"]), "error": str(exc)})
            continue
        updated.append(str(project["project_key"]))
        updated_notes.append({
            "project_key": str(project["project_key"]),
            "tracking_note": str(project.get("tracking_note") or ""),
        })
        if progress:
            progress(len(updated), len(candidates), item)
    conn.commit()
    return {
        "mode": "execute",
        "operation_status": "retry_wait" if conflicts else "completed",
        "conflicted_projects": conflicts,
        "tracking_states": sorted(resolved_states),
        "updated_count": len(updated),
        "total_candidate_count": total_candidate_count,
        "updated_project_keys": updated,
        "updated_tracking_notes": updated_notes,
        "kind_counts": kind_counts,
        "plan_fingerprint": plan_fingerprint,
        "cancelled": len(updated) < len(candidates) and bool(cancel_requested and cancel_requested()),
    }


def registry_command_prefix(conn: sqlite3.Connection) -> str:
    path = next(str(row[2]) for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    return f"project-registry --db-path {shlex.quote(path)}" if path else "project-registry"


def review_queue_suggested_commands(project: dict[str, Any], signals: list[dict[str, Any]], *, command_prefix: str = "project-registry") -> list[str]:
    commands: list[str] = []
    project_key = str(project.get("project_key") or "")
    if not project_key:
        return commands
    commands.append(f"{command_prefix} show -p {project_key} --format json")
    signal_kinds = {str(item.get("kind") or "") for item in signals}
    if "service_observation_changed" in signal_kinds:
        commands.append(f"{command_prefix} enrich -p {project_key} --deep-investigation")
    return commands


def showcase_queue_suggested_commands(project: dict[str, Any], signals: list[dict[str, Any]], *, command_prefix: str = "project-registry") -> list[str]:
    commands = review_queue_suggested_commands(project, signals, command_prefix=command_prefix)
    project_key = str(project.get("project_key") or "")
    if not project_key:
        return commands
    signal_kinds = {str(item.get("kind") or "") for item in signals}
    if signal_kinds & {
        "service_surface_confirmed",
        "service_candidate_needs_validation",
        "lexicon_publisher_confirmed",
        "lexicon_definition_confirmed",
    }:
        commands.append(f"{command_prefix} enrich -p {project_key} --deep-investigation")
    return project_record.unique_list(commands)


def build_showcase_queue_from_projects(
    projects: list[dict[str, Any]],
    *,
    limit: int | None = 50,
    kinds: set[str] | None = None,
    include_personal_tools: bool = False,
    command_prefix: str = "project-registry",
    view: str = "queue",
) -> list[dict[str, Any]]:
    project_list = [project_record.ensure_project_shape(project) for project in projects]
    return _showcase_queue_from_shaped(
        project_list, limit=limit, kinds=kinds, include_personal_tools=include_personal_tools,
        command_prefix=command_prefix, view=view,
    )


def _showcase_queue_from_shaped(
    projects: list[dict[str, Any]], *, limit: int | None, kinds: set[str] | None,
    include_personal_tools: bool, command_prefix: str, view: str,
) -> list[dict[str, Any]]:
    """Use normalized inputs from the record loader or the public dict builder."""
    queue: list[dict[str, Any]] = []
    for project in projects:
        shaped = dict(project)
        signals = project_review._showcase_signals_from_shaped(shaped, include_personal_tools=include_personal_tools, include_ineligible=True)
        if view != "technical":
            signals = [signal for signal in signals if signal["kind"] != "technical_insight_candidate"]
        if kinds is not None:
            signals = [item for item in signals if str(item.get("kind") or "") in kinds]
        policy = project_review.promotion_policy_projection(shaped, signals)
        eligible = policy["candidate_visibility"]["group"] == "eligible"
        lowered = policy["priority_group"] == "lowered"
        waiting = bool(policy["active_deferral"])
        included = {
            "queue": eligible,
            "inbox": eligible and not lowered,
            "technical": eligible and not lowered and any(
                signal["kind"] == "technical_insight_candidate" for signal in signals
            ),
            "lowered": lowered,
            "waiting": waiting,
            "all": eligible or lowered or waiting,
        }
        if view not in included:
            raise ValueError(f"unknown promotion view: {view}")
        if not included[view]:
            continue
        if not signals and (view in {"queue", "inbox", "technical"} or kinds is not None):
            continue
        if not signals and not (lowered or waiting):
            continue
        top_priority = max((int(item.get("priority", 0) or 0) for item in signals), default=0)
        sort_score = int(policy["score_breakdown"]["total"])
        queue.append(
            {
                "project_key": shaped["project_key"],
                "name": shaped.get("name", ""),
                "tracking_state": shaped.get("tracking_state", "active"),
                "category": shaped.get("category", ""),
                "service_status": shaped.get("service_status", "unknown"),
                "atproto_contribution_level": shaped.get("atproto_contribution_level", "unknown"),
                "atproto_contribution_score": int(shaped.get("atproto_contribution_score", 0) or 0),
                "technical_interest_level": shaped.get("technical_interest_level", "none"),
                "technical_interest_score": int(shaped.get("technical_interest_score", 0) or 0),
                "last_promotion_deferral_reason": project_review.latest_promotion_deferral_reason(shaped),
                "first_seen_at": shaped.get("first_seen_at", ""),
                "last_seen_at": shaped.get("last_seen_at", ""),
                "seed_type": shaped.get("seed_type", ""),
                "seed_value": shaped.get("seed_value", ""),
                "nsids": project_record.unique_list(
                    [str(item or "") for item in shaped.get("resolved_nsids", []) if str(item or "").strip()]
                    + [str(item or "") for item in shaped.get("input_nsids", []) if str(item or "").strip()]
                )[:5],
                "website_url": shaped.get("website_url", ""),
                "repo_urls": project_review.project_repo_hint_urls(shaped)[:3],
                "tagline": shaped.get("tagline", ""),
                "summary": shaped.get("summary", ""),
                "description_missing": project_review.missing_description_fields(shaped),
                "top_priority": top_priority,
                "sort_score": sort_score,
                **policy,
                "signals": sorted(signals, key=lambda item: (-int(item.get("priority", 0) or 0), str(item.get("kind") or ""))),
                "suggested_commands": showcase_queue_suggested_commands(shaped, signals, command_prefix=command_prefix),
            }
        )
    queue.sort(key=lambda item: (
        1 if item.get("priority_group") == "lowered" else 0,
        -int(item.get("technical_interest_score", 0) if view == "technical" else 0),
        0 if view == "technical" else -int(item.get("sort_score", 0) or 0),
        str(item.get("project_key") or ""),
    ))
    return queue if limit is None else queue[: max(1, int(limit or 50))]


def promotion_queue(
    conn: sqlite3.Connection,
    *,
    tracking_states: set[str] | None = None,
    limit: int | None = 50,
    kinds: set[str] | None = None,
    include_personal_tools: bool = False,
    view: str = "queue",
) -> list[dict[str, Any]]:
    projects = (
        project_record.list_projects_with_decision(
            conn, "lower_promotion_priority" if view == "lowered" else "defer_promotion",
            tracking_states=tracking_states,
        )
        if view in {"lowered", "waiting"}
        else project_record.list_projects(conn, tracking_states=tracking_states)
    )
    return _showcase_queue_from_shaped(
        projects,
        limit=limit,
        kinds=kinds,
        include_personal_tools=include_personal_tools,
        command_prefix=registry_command_prefix(conn),
        view=view,
    )


def build_showcase_queue_item(
    conn: sqlite3.Connection,
    project_key: str,
    *,
    kinds: set[str] | None = None,
    include_personal_tools: bool = False,
    view: str = "queue",
) -> dict[str, Any] | None:
    project = project_record.fetch_project(conn, project_key)
    if project is None:
        return None
    queue = build_showcase_queue_from_projects(
        [project],
        limit=None,
        kinds=kinds,
        include_personal_tools=include_personal_tools,
        command_prefix=registry_command_prefix(conn),
        view=view,
    )
    return queue[0] if queue else None


def review_queue(
    conn: sqlite3.Connection,
    *,
    tracking_states: set[str] | None = None,
    limit: int = 50,
    kinds: set[str] | None = None,
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    project_list = project_record.list_review_candidate_projects(conn, tracking_states=tracking_states)
    for project in project_list:
        entry = _review_queue_entry(
            dict(project), kinds=kinds, conn=conn,
        )
        if entry is not None:
            queue.append(entry)
    queue.sort(key=lambda item: (-int(item.get("sort_score", 0) or 0), str(item.get("project_key") or "")))
    return queue[: max(1, int(limit or 50))]


def build_review_queue_batch(
    conn: sqlite3.Connection,
    *,
    tracking_states: set[str] | None = None,
    kinds: set[str] | None = None,
    project_list: list[dict[str, Any]] | None = None,
    start_index: int = 0,
    batch_size: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    batch: list[dict[str, Any]] = []
    projects = (project_list if project_list is not None else
                project_record.list_review_candidate_projects(conn, tracking_states=tracking_states))
    index = max(0, int(start_index or 0))
    target_size = max(1, int(batch_size or 100))
    while index < len(projects) and len(batch) < target_size:
        project = projects[index]
        index += 1
        entry = _review_queue_entry(
            dict(project), kinds=kinds, conn=conn,
        )
        if entry is not None:
            batch.append(entry)
    batch.sort(key=lambda item: (-int(item.get("sort_score", 0) or 0), str(item.get("project_key") or "")))
    return batch, index


def build_review_queue_item(
    conn: sqlite3.Connection,
    project_key: str,
    *,
    kinds: set[str] | None = None,
) -> dict[str, Any] | None:
    project = project_record.fetch_project(conn, project_key)
    if project is None:
        return None
    return _review_queue_entry(project, kinds=kinds, conn=conn)


def _review_queue_entry(
    shaped: dict[str, Any], *, kinds: set[str] | None, conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    signals = project_review.compute_review_signals(shaped, assume_shaped=True)
    if kinds is not None:
        signals = [item for item in signals if str(item.get("kind") or "") in kinds]
    if not signals:
        return None
    top_priority = max(int(item.get("priority", 0) or 0) for item in signals)
    sort_score = project_review.review_queue_sort_score(shaped, signals)
    return {
        "project_key": shaped["project_key"],
        "name": shaped.get("name", ""),
        "tracking_state": shaped.get("tracking_state", "active"),
        "service_status": shaped.get("service_status", "unknown"),
        "atproto_contribution_level": shaped.get("atproto_contribution_level", "unknown"),
        "atproto_contribution_score": int(shaped.get("atproto_contribution_score", 0) or 0),
        "seed_type": shaped.get("seed_type", ""),
        "seed_value": shaped.get("seed_value", ""),
        "website_url": shaped.get("website_url", ""),
        "top_priority": top_priority,
        "sort_score": sort_score,
        "signals": sorted(signals, key=lambda item: (-int(item.get("priority", 0) or 0), str(item.get("kind") or ""))),
        "suggested_commands": review_queue_suggested_commands(shaped, signals, command_prefix=registry_command_prefix(conn)),
    }
