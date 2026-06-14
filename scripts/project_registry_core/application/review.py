from __future__ import annotations

import sqlite3
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
