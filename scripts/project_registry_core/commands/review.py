from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Callable
from typing import Any

from .. import registry as core
from .. import runtime_view
from ..application import review as services


def _print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_review_stalled_interactive(args: argparse.Namespace) -> int:
    conn = core.open_db()
    now = dt.datetime.now(dt.timezone.utc)
    inactivity_days = max(1, int(args.inactivity_days or 45))
    max_enrich_age_days = max(1, int(args.max_enrich_age_days or 30))
    project_list = [
        core.ensure_project_shape(project)
        for project in core.list_projects(conn, tracking_states={"active", "stalled"})
    ]
    candidates = [
        entry
        for project in project_list
        if (entry := core.stalled_review_entry(
            project,
            now=now,
            inactivity_days=inactivity_days,
            max_enrich_age_days=max_enrich_age_days,
        ))
    ]
    candidates.sort(
        key=lambda item: (
            str(item.get("suggested_tracking_state") or "") != "active",
            item["project_key"],
        )
    )
    suggested_state_counts: dict[str, int] = {}
    for item in candidates:
        state = str(item.get("suggested_tracking_state") or "")
        suggested_state_counts[state] = int(suggested_state_counts.get(state, 0)) + 1

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("--interactive requires a TTY")
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
        print("[y]=apply, [n]=skip, [j]=full json, [q]=quit", file=sys.stderr)
        while True:
            try:
                answer = input().strip().lower()
            except EOFError:
                answer = "q"
            if answer in {"", "y", "yes"}:
                break
            if answer in {"n", "no"}:
                item = None
                break
            if answer == "j":
                project = core.fetch_project(conn, str(item.get("project_key") or ""))
                if project is not None:
                    print(json.dumps(
                        runtime_view.sanitize_runtime_snapshot(project),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ))
                continue
            if answer in {"q", "quit"}:
                candidates = []
                item = None
                break
        if item is None:
            if answer in {"q", "quit"}:
                break
            continue
        project = core.fetch_project(conn, str(item.get("project_key") or ""))
        if not project:
            continue
        project["tracking_state"] = str(
            item.get("suggested_tracking_state") or project.get("tracking_state") or "active"
        )
        if note and not str(project.get("tracking_note") or "").strip():
            project["tracking_note"] = note
        core.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
        updated.append(project["project_key"])
        updated_with_state.append({
            "project_key": project["project_key"],
            "tracking_state": str(project.get("tracking_state") or ""),
        })
    conn.commit()
    return _print({
        "mode": "execute",
        "inactivity_days": inactivity_days,
        "max_enrich_age_days": max_enrich_age_days,
        "updated_count": len(updated),
        "updated_project_keys": updated,
        "updated_projects": updated_with_state,
        "suggested_state_counts": suggested_state_counts,
    })


def _cmd_review_classification_interactive(
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
    conn = core.open_db()
    tracking_states = (
        {str(item).strip() for item in (args.tracking_states or []) if str(item).strip()}
        or set(default_tracking_states or {"active", "stable", "stalled"})
    )
    project_list = [
        core.ensure_project_shape(project)
        for project in core.list_projects(conn, tracking_states=tracking_states)
    ]
    candidates = [entry for project in project_list if (entry := entry_builder(project))]
    candidates.sort(key=lambda item: (
        str(item.get("kind") or ""),
        str(item.get("project_key") or ""),
    ))
    kind_counts: dict[str, int] = {}
    for item in candidates:
        kind = str(item.get("kind") or "")
        kind_counts[kind] = int(kind_counts.get(kind, 0)) + 1
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("--interactive requires a TTY")
    effective_limit = max(1, int(args.limit)) if args.limit is not None else None
    if effective_limit is not None:
        candidates = candidates[:effective_limit]

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
        print(f"[y]={target_state}, [n]=skip, [j]=full json, [q]=quit", file=sys.stderr)
        while True:
            try:
                answer = input().strip().lower()
            except EOFError:
                answer = "q"
            if answer in {"", "y", "yes"}:
                break
            if answer in {"n", "no"}:
                item = None
                break
            if answer == "j":
                project = core.fetch_project(conn, str(item.get("project_key") or ""))
                if project is not None:
                    print(json.dumps(
                        runtime_view.sanitize_runtime_snapshot(project),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ))
                continue
            if answer in {"q", "quit"}:
                candidates = []
                item = None
                break
        if item is None:
            if answer in {"q", "quit"}:
                break
            continue
        project = core.fetch_project(conn, str(item.get("project_key") or ""))
        if not project:
            continue
        next_state = str(item.get("suggested_tracking_state") or target_state)
        project["tracking_state"] = next_state
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
        if next_state == "active" and str(item.get("kind") or "") == "owned_protocol_surface_detected":
            project["tracking_note"] = "reactivated after owned protocol surface detection"
        elif note and not str(project.get("tracking_note") or "").strip():
            project["tracking_note"] = note
        core.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
        updated.append(project["project_key"])
        noted_project_keys.append({
            "project_key": project["project_key"],
            "tracking_note": str(project.get("tracking_note") or ""),
        })
    conn.commit()
    return _print({
        "mode": "execute",
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
        project_list = core.list_review_projects(conn, tracking_states=tracking_states)
        next_index = 0
        emitted = 0

        def load_review_batch() -> list[dict[str, Any]]:
            nonlocal next_index, emitted
            if total_limit is not None and emitted >= total_limit:
                return []
            current_batch_size = batch_size if total_limit is None else min(batch_size, total_limit - emitted)
            items, next_index_result = core.build_review_queue_batch(
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
        return core.output_project_queue(
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
    tracking_states = {str(item).strip() for item in (args.tracking_states or []) if str(item).strip()} or {"active"}
    kinds = {str(item).strip() for item in (args.kinds or []) if str(item).strip()} or None
    interactive = bool(getattr(args, "interactive", False))
    include_personal_tools = bool(getattr(args, "include_personal_tools", False))
    queue = services.promotion_queue(
        conn,
        tracking_states=tracking_states,
        kinds=kinds,
        limit=max(1, int(args.limit)) if args.limit is not None else (None if interactive else 50),
        include_personal_tools=include_personal_tools,
    )
    try:
        return core.output_project_queue(
            conn,
            queue_name="promotion",
            queue=queue,
            tracking_states=tracking_states,
            kinds=kinds,
            interactive=interactive,
            load_more=None,
            include_personal_tools=include_personal_tools,
        )
    finally:
        conn.close()


def cmd_review_stalled(args: argparse.Namespace) -> int:
    if bool(getattr(args, "interactive", False)):
        return _cmd_review_stalled_interactive(args)
    conn = core.open_db()
    try:
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
    if bool(getattr(args, "interactive", False)):
        return _cmd_review_classification_interactive(
            args,
            review_name="review-ignore",
            target_state="ignore",
            entry_builder=core.ignore_review_entry,
            note_reason_choices=core.IGNORE_REVIEW_NOTE_REASONS,
            default_note_reason=core.default_ignore_review_note_reason,
            build_note=core.build_ignore_review_note,
        )
    conn = core.open_db()
    try:
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
    if bool(getattr(args, "interactive", False)):
        return _cmd_review_classification_interactive(
            args,
            review_name="review-out-of-scope",
            target_state="out_of_scope",
            entry_builder=core.out_of_scope_review_entry,
            note_reason_choices=core.OUT_OF_SCOPE_REVIEW_NOTE_REASONS,
            default_note_reason=core.default_out_of_scope_review_note_reason,
            build_note=core.build_out_of_scope_review_note,
            default_tracking_states={"active", "stable", "stalled", "out_of_scope"},
        )
    conn = core.open_db()
    try:
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
