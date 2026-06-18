from __future__ import annotations

import sqlite3
from typing import Any, Callable, Sequence

from .. import registry as core


def import_seed_list(conn: sqlite3.Connection, *, path: str, execute: bool = False) -> dict[str, Any]:
    return core.import_text_seed_list(conn, path, execute=execute)


def introduced(
    conn: sqlite3.Connection,
    *,
    query: str,
    execute: bool = False,
    limit: int = 10,
    max_pages: int = 100,
    lookback_days: int = 7,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    return core.sync_introduced_entries(
        conn,
        query=query,
        execute=execute,
        limit=limit,
        max_pages=max_pages,
        lookback_days=lookback_days,
        incremental=incremental,
        all_history=all_history,
        progress=progress,
        cancel_requested=cancel_requested,
    )


def github(
    conn: sqlite3.Connection,
    *,
    queries: Sequence[str] | None = None,
    execute: bool = False,
    create_unmatched: bool = True,
    pages: int | None = None,
    per_page: int | None = None,
    lookback_days: int = 10,
    screen_unmatched: bool = False,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    config = core.load_config()
    search_cfg = config.get("github_repo_search", {}) if isinstance(config.get("github_repo_search", {}), dict) else {}
    resolved_queries = core.clean_string_list(queries or search_cfg.get("queries") or [], limit=12)
    if not resolved_queries:
        raise ValueError("github repo search queries are not configured")
    resolved_per_page = int(per_page or search_cfg.get("per_page") or 50)
    resolved_pages = int(pages or search_cfg.get("pages") or core.default_github_search_pages(resolved_per_page))
    return core.sync_github_search(
        conn,
        queries=resolved_queries,
        execute=execute,
        create_unmatched=create_unmatched,
        pages=resolved_pages,
        per_page=resolved_per_page,
        lookback_days=lookback_days,
        screen_unmatched=screen_unmatched,
        incremental=incremental,
        all_history=all_history,
        progress=progress,
        cancel_requested=cancel_requested,
    )


def collections(
    conn: sqlite3.Connection,
    *,
    source_url: str = "",
    execute: bool = False,
    create_unmatched: bool = True,
    lookback_days: int = 10,
    save_path: str | None = None,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    config = core.load_config()
    resolved_url = source_url or str(config.get("collection_inventory", {}).get("url") or "")
    if not resolved_url:
        raise ValueError("collection inventory source URL is not configured")
    return core.sync_collection_inventory(
        conn,
        source_url=resolved_url,
        execute=execute,
        create_unmatched=create_unmatched,
        lookback_days=lookback_days,
        save_path=save_path,
        incremental=incremental,
        all_history=all_history,
        progress=progress,
        cancel_requested=cancel_requested,
    )


def semble(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    create_unmatched: bool = True,
    pages: int = 100,
    per_page: int = 20,
    lookback_days: int = 14,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    return core.sync_semble_collections(
        conn,
        execute=execute,
        create_unmatched=create_unmatched,
        pages=pages,
        per_page=per_page,
        lookback_days=lookback_days,
        incremental=incremental,
        all_history=all_history,
        progress=progress,
        cancel_requested=cancel_requested,
    )


def tangled(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    create_unmatched: bool = False,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    return core.sync_tangled_repos(
        conn,
        execute=execute,
        create_unmatched=create_unmatched,
        incremental=incremental,
        all_history=all_history,
        progress=progress,
        cancel_requested=cancel_requested,
    )
