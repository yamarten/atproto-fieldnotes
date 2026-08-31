from __future__ import annotations

import sqlite3
from typing import Any, Callable, Sequence

from .. import source_sync as core


def _source_config(name: str) -> dict[str, Any]:
    config = core.load_config()
    value = config.get(name, {}) if isinstance(config, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


def _require_enabled(name: str, config: dict[str, Any]) -> None:
    if config.get("enabled") is False:
        raise ValueError(f"source is disabled in config/sources.json: {name}")


def _positive_int(value: int | None, config: dict[str, Any], key: str, default: int) -> int:
    return max(1, int(value if value is not None else config.get(key) or default))


def import_seed_list(conn: sqlite3.Connection, *, path: str, execute: bool = False) -> dict[str, Any]:
    return core.import_seed_list(conn, path, execute=execute)


def bluesky_lexicons(conn: sqlite3.Connection, *, execute: bool = False) -> dict[str, Any]:
    config = _source_config("bluesky_official_lexicons")
    _require_enabled("bluesky_official_lexicons", config)
    return core.sync_bluesky_lexicons(
        conn,
        repo_url=str(config.get("repo_url") or ""),
        target_service_url=str(config.get("target_service_url") or ""),
        execute=execute,
    )


def introduced(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    execute: bool = False,
    limit: int | None = None,
    max_pages: int | None = None,
    lookback_days: int | None = None,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int | None, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    config = _source_config("introduced_search")
    _require_enabled("introduced_search", config)
    resolved_query = str(query or config.get("query") or "").strip()
    if not resolved_query:
        raise ValueError("introduced search query is not configured")
    return core.sync_introduced_entries(
        conn,
        query=resolved_query,
        execute=execute,
        limit=_positive_int(limit, config, "limit", 10),
        max_pages=_positive_int(max_pages, config, "max_pages", 100),
        lookback_days=_positive_int(lookback_days, config, "lookback_days", 7),
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
    lookback_days: int | None = None,
    screen_unmatched: bool = False,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    search_cfg = _source_config("github_repo_search")
    _require_enabled("github_repo_search", search_cfg)
    resolved_queries = core.clean_string_list(queries or search_cfg.get("queries") or [], limit=12)
    if not resolved_queries:
        raise ValueError("github repo search queries are not configured")
    resolved_per_page = _positive_int(per_page, search_cfg, "per_page", 50)
    resolved_pages = _positive_int(pages, search_cfg, "pages", core.default_github_search_pages(resolved_per_page))
    return core.sync_github_search(
        conn,
        queries=resolved_queries,
        execute=execute,
        create_unmatched=create_unmatched,
        pages=resolved_pages,
        per_page=resolved_per_page,
        lookback_days=_positive_int(lookback_days, search_cfg, "lookback_days", 10),
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
    lookback_days: int | None = None,
    save_path: str | None = None,
    incremental: bool = True,
    all_history: bool = False,
    max_items: int | None = None,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    config = _source_config("collection_inventory")
    _require_enabled("collection_inventory", config)
    resolved_url = source_url or str(config.get("url") or "")
    if not resolved_url:
        raise ValueError("collection inventory source URL is not configured")
    return core.sync_collection_inventory(
        conn,
        source_url=resolved_url,
        execute=execute,
        create_unmatched=create_unmatched,
        lookback_days=_positive_int(lookback_days, config, "lookback_days", 10),
        save_path=save_path,
        incremental=incremental,
        all_history=all_history,
        max_items=max_items,
        progress=progress,
        cancel_requested=cancel_requested,
    )


def semble(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    create_unmatched: bool = True,
    pages: int | None = None,
    per_page: int | None = None,
    lookback_days: int | None = None,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    config = _source_config("semble_collections")
    _require_enabled("semble_collections", config)
    api_url = str(config.get("api_url") or "").strip()
    raw_sources = config.get("tracked_collections")
    tracked_collections = [
        {**dict(item), "api_url": api_url}
        for item in raw_sources if isinstance(item, dict)
    ] if isinstance(raw_sources, list) else []
    if not api_url or not tracked_collections:
        raise ValueError("Semble API URL and tracked collections are not configured")
    return core.sync_semble_collections(
        conn,
        execute=execute,
        create_unmatched=create_unmatched,
        pages=_positive_int(pages, config, "pages", 100),
        per_page=_positive_int(per_page, config, "per_page", 20),
        lookback_days=_positive_int(lookback_days, config, "lookback_days", 14),
        tracked_collections=tracked_collections,
        incremental=incremental,
        all_history=all_history,
        progress=progress,
        cancel_requested=cancel_requested,
    )


def tangled(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    create_unmatched: bool = True,
    incremental: bool = True,
    all_history: bool = False,
    max_items: int | None = None,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    config = _source_config("tangled_repo_discovery")
    _require_enabled("tangled_repo_discovery", config)
    return core.sync_tangled_repos(
        conn,
        execute=execute,
        create_unmatched=create_unmatched,
        incremental=incremental,
        all_history=all_history,
        max_items=max_items,
        progress=progress,
        cancel_requested=cancel_requested,
    )
