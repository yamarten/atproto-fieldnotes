from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from .. import registry as core
from ..application import review as services


def cmd_review_queue(args: argparse.Namespace) -> int:
    conn = core.open_db()
    tracking_states = {str(item).strip() for item in (args.tracking_states or []) if str(item).strip()} or {"active", "watchlist", "stable", "stalled"}
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
    tracking_states = {str(item).strip() for item in (args.tracking_states or []) if str(item).strip()} or {"active", "watchlist"}
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
