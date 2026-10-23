from __future__ import annotations

from project_registry_core import project_review

import argparse
import datetime as dt
import json
import sqlite3
import sys
from collections.abc import Callable
from typing import Any

from .. import registry as core
from ..persistence import runtime_view
from ..application import review as services
from . import queue_interaction
from project_registry_core import project_record


def _print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2 if payload.get("operation_status") == "retry_wait" else 0


def _prompt_review_candidate(conn: sqlite3.Connection, item: dict[str, Any], apply_label: str) -> str:
    print(f"[y]={apply_label}, [n]=skip, [j]=full json, [q]=quit", file=sys.stderr)
    while True:
        try:
            answer = input().strip().lower()
        except EOFError:
            return "quit"
        if answer in {"", "y", "yes"}:
            return "apply"
        if answer in {"n", "no"}:
            return "skip"
        if answer in {"q", "quit"}:
            return "quit"
        if answer == "j":
            project = project_record.fetch_project(conn, str(item.get("project_key") or ""))
            if project is not None:
                print(json.dumps(
                    runtime_view.sanitize_runtime_snapshot(project),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ))


def _cmd_review_stalled_interactive(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    inactivity_days = max(1, int(args.inactivity_days or 45))
    max_enrich_age_days = max(1, int(args.max_enrich_age_days or 30))
    snapshot = services.stalled_review_snapshot(
        conn, now=now, inactivity_days=inactivity_days, max_enrich_age_days=max_enrich_age_days,
    )
    candidates = snapshot.candidates
    project_snapshots = snapshot.projects
    suggested_state_counts = snapshot.counts("suggested_tracking_state")

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("--interactive requires a TTY")
    conflicts: list[dict[str, str]] = []
    updated: list[str] = []
    updated_with_state: list[dict[str, str]] = []
    note = str(
        args.note
        or (
            "auto-updated after review-stalled "
            f"inactivity_days={inactivity_days} max_enrich_age_days={max_enrich_age_days}"
        )
    )
    for index, item in enumerate(candidates, start=1):
        print(json.dumps({
            "index": index,
            "review_stalled_count": len(candidates),
            **item,
        }, ensure_ascii=False, indent=2))
        answer = _prompt_review_candidate(conn, item, "apply")
        if answer == "quit":
            break
        if answer == "skip":
            continue
        project = project_snapshots.get(str(item.get("project_key") or ""))
        if not project:
            continue
        try:
            services.apply_review_candidate(conn, project, item, note=note)
        except project_record.StaleProjectWriteError as exc:
            conflicts.append({"project_key": str(project["project_key"]), "error": str(exc)})
            print(f"[retry_wait] {exc}", file=sys.stderr)
            continue
        updated.append(project["project_key"])
        updated_with_state.append({
            "project_key": project["project_key"],
            "tracking_state": str(project.get("tracking_state") or ""),
        })
    conn.commit()
    return _print({
        "mode": "execute",
        "operation_status": "retry_wait" if conflicts else "completed",
        "conflicted_projects": conflicts,
        "inactivity_days": inactivity_days,
        "max_enrich_age_days": max_enrich_age_days,
        "updated_count": len(updated),
        "updated_project_keys": updated,
        "updated_projects": updated_with_state,
        "suggested_state_counts": suggested_state_counts,
    })


def _cmd_review_classification_interactive(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    *,
    review_name: str,
    target_state: str,
    entry_builder: Callable[[dict[str, Any]], dict[str, Any] | None],
    note_reason_choices: dict[str, str],
    default_note_reason: Callable[[dict[str, Any]], str],
    build_note: Callable[[dict[str, Any], str, str], str],
    default_tracking_states: set[str] | None = None,
) -> int:
    tracking_states = (
        {str(item).strip() for item in (args.tracking_states or []) if str(item).strip()}
        or set(default_tracking_states or {"active", "stable", "stalled"})
    )
    snapshot = services.classification_review_snapshot(
        conn, tracking_states=tracking_states, entry_builder=entry_builder,
    )
    candidates = snapshot.candidates
    project_snapshots = snapshot.projects
    kind_counts = snapshot.counts("kind")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("--interactive requires a TTY")
    effective_limit = max(1, int(args.limit)) if args.limit is not None else None
    if effective_limit is not None:
        candidates = candidates[:effective_limit]

    conflicts: list[dict[str, str]] = []
    updated: list[str] = []
    noted_project_keys: list[dict[str, str]] = []
    static_note = str(args.note or "").strip()
    default_reason_key = str(getattr(args, "note_reason", "") or "").strip()
    for index, item in enumerate(candidates, start=1):
        print(json.dumps({
            "index": index,
            f"{review_name.replace('-', '_')}_count": len(candidates),
            **item,
        }, ensure_ascii=False, indent=2))
        answer = _prompt_review_candidate(conn, item, target_state)
        if answer == "quit":
            break
        if answer == "skip":
            continue
        project = project_snapshots.get(str(item.get("project_key") or ""))
        if not project:
            continue
        reason_key = default_reason_key or default_note_reason(item)
        extra_note = static_note
        if not default_reason_key:
            print(json.dumps({
                "note_reason_choices": note_reason_choices,
                "default": reason_key,
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            print("note reason? [Enter]=default", file=sys.stderr)
            try:
                chosen_reason = input().strip()
            except EOFError:
                chosen_reason = ""
            if chosen_reason:
                if chosen_reason in note_reason_choices:
                    reason_key = chosen_reason
                else:
                    print(json.dumps({
                        "warning": "unknown_note_reason",
                        "value": chosen_reason,
                    }, ensure_ascii=False), file=sys.stderr)
            if not static_note:
                print("extra note? [Enter]=none", file=sys.stderr)
                try:
                    extra_note = input().strip()
                except EOFError:
                    extra_note = ""
        note = build_note(item, reason_key, extra_note)
        try:
            services.apply_review_candidate(conn, project, item, note=note, default_state=target_state)
        except project_record.StaleProjectWriteError as exc:
            conflicts.append({"project_key": str(project["project_key"]), "error": str(exc)})
            print(f"[retry_wait] {exc}", file=sys.stderr)
            continue
        updated.append(project["project_key"])
        noted_project_keys.append({
            "project_key": project["project_key"],
            "tracking_note": str(project.get("tracking_note") or ""),
        })
    conn.commit()
    return _print({
        "mode": "execute",
        "operation_status": "retry_wait" if conflicts else "completed",
        "conflicted_projects": conflicts,
        "tracking_states": sorted(tracking_states),
        "updated_count": len(updated),
        "updated_project_keys": updated,
        "updated_tracking_notes": noted_project_keys,
        "kind_counts": kind_counts,
    })


def cmd_review_queue(args: argparse.Namespace) -> int:
    conn = core.open_db()
    tracking_states = {str(item).strip() for item in (args.tracking_states or []) if str(item).strip()} or {"active", "stable", "stalled"}
    kinds = {str(item).strip() for item in (args.kinds or []) if str(item).strip()} or None
    interactive = bool(getattr(args, "interactive", False))
    load_more: Callable[[], list[dict[str, Any]]] | None = None
    if interactive:
        batch_size = 100
        total_limit = max(1, int(args.limit)) if args.limit is not None else None
        project_list = project_record.list_review_candidate_projects(conn, tracking_states=tracking_states)
        next_index = 0
        emitted = 0

        def load_review_batch() -> list[dict[str, Any]]:
            nonlocal next_index, emitted
            if total_limit is not None and emitted >= total_limit:
                return []
            current_batch_size = batch_size if total_limit is None else min(batch_size, total_limit - emitted)
            items, next_index_result = services.build_review_queue_batch(
                conn,
                tracking_states=tracking_states,
                kinds=kinds,
                project_list=project_list,
                start_index=next_index,
                batch_size=current_batch_size,
            )
            next_index = next_index_result
            emitted += len(items)
            return items

        queue = load_review_batch()
        load_more = load_review_batch
    else:
        queue = services.review_queue(
            conn,
            tracking_states=tracking_states,
            kinds=kinds,
            limit=max(1, int(args.limit)) if args.limit is not None else 50,
        )
    try:
        return queue_interaction.output_project_queue(
            conn,
            queue_name="review",
            queue=queue,
            tracking_states=tracking_states,
            kinds=kinds,
            interactive=interactive,
            load_more=load_more,
            include_personal_tools=False,
        )
    finally:
        conn.close()


def cmd_promotion_queue(args: argparse.Namespace) -> int:
    conn = core.open_db()
    tracking_states = {str(item).strip() for item in (args.tracking_states or []) if str(item).strip()} or {"active", "stable"}
    kinds = {str(item).strip() for item in (args.kinds or []) if str(item).strip()} or None
    interactive = bool(getattr(args, "interactive", False))
    include_personal_tools = bool(getattr(args, "include_personal_tools", False))
    view = getattr(args, "view", "queue")
    queue = services.promotion_queue(
        conn,
        tracking_states=tracking_states,
        kinds=kinds,
        limit=max(1, int(args.limit)) if args.limit is not None else (None if interactive else 50),
        include_personal_tools=include_personal_tools,
        view=view,
    )
    try:
        return queue_interaction.output_project_queue(
            conn,
            queue_name="promotion",
            queue=queue,
            tracking_states=tracking_states,
            kinds=kinds,
            interactive=interactive,
            load_more=None,
            include_personal_tools=include_personal_tools,
            promotion_view=view,
        )
    finally:
        conn.close()


def cmd_review_stalled(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        if bool(getattr(args, "interactive", False)):
            return _cmd_review_stalled_interactive(conn, args)
        return _print(services.stalled(
            conn,
            execute=bool(args.execute),
            inactivity_days=int(args.inactivity_days or 45),
            max_enrich_age_days=int(args.max_enrich_age_days or 30),
            note=str(args.note or ""),
        ))
    finally:
        conn.close()


def cmd_review_ignore(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        if bool(getattr(args, "interactive", False)):
            return _cmd_review_classification_interactive(
                conn, args,
                review_name="review-ignore",
                target_state="ignore",
                entry_builder=project_review.ignore_review_entry,
                note_reason_choices=project_review.IGNORE_REVIEW_NOTE_REASONS,
                default_note_reason=project_review.default_ignore_review_note_reason,
                build_note=project_review.build_ignore_review_note,
            )
        tracking_states = {
            str(item).strip() for item in (args.tracking_states or []) if str(item).strip()
        } or None
        return _print(services.ignore(
            conn,
            execute=bool(args.execute),
            tracking_states=tracking_states,
            limit=max(1, int(args.limit)) if args.limit is not None else 100,
            note=str(args.note or ""),
            note_reason=str(args.note_reason or ""),
        ))
    finally:
        conn.close()


def cmd_review_out_of_scope(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        if bool(getattr(args, "interactive", False)):
            return _cmd_review_classification_interactive(
                conn, args,
                review_name="review-out-of-scope",
                target_state="out_of_scope",
                entry_builder=project_review.out_of_scope_review_entry,
                note_reason_choices=project_review.OUT_OF_SCOPE_REVIEW_NOTE_REASONS,
                default_note_reason=project_review.default_out_of_scope_review_note_reason,
                build_note=project_review.build_out_of_scope_review_note,
                default_tracking_states={"active", "stable", "stalled", "out_of_scope"},
            )
        tracking_states = {
            str(item).strip() for item in (args.tracking_states or []) if str(item).strip()
        } or None
        return _print(services.out_of_scope(
            conn,
            execute=bool(args.execute),
            tracking_states=tracking_states,
            limit=max(1, int(args.limit)) if args.limit is not None else 100,
            note=str(args.note or ""),
            note_reason=str(args.note_reason or ""),
        ))
    finally:
        conn.close()
