from __future__ import annotations

import argparse
import json

from .. import registry as core
from ..application import sync as services


def _run_with_db(call):
    conn = core.open_db()
    try:
        return call(conn)
    finally:
        conn.close()


def _print(payload, *, execute: bool) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not execute else None, sort_keys=not execute))
    return 0


def cmd_import_seed_list(args: argparse.Namespace) -> int:
    payload = _run_with_db(lambda conn: services.import_seed_list(conn, path=args.path, execute=bool(args.execute)))
    return _print(payload, execute=bool(args.execute))


def cmd_sync_introduced(args: argparse.Namespace) -> int:
    payload = _run_with_db(lambda conn: services.introduced(
        conn,
        query=str(args.query or "from:yamarten.bsky.social #ATプロトーク"),
        execute=bool(args.execute),
        limit=int(args.limit or 10),
        max_pages=int(args.max_pages or 100),
        lookback_days=int(args.lookback_days or 7),
        incremental=bool(args.incremental),
        all_history=bool(args.all_history),
    ))
    return _print(payload, execute=bool(args.execute))


def cmd_sync_github_search(args: argparse.Namespace) -> int:
    payload = _run_with_db(lambda conn: services.github(
        conn,
        queries=args.query,
        execute=bool(args.execute),
        create_unmatched=bool(args.create_unmatched),
        pages=args.pages,
        per_page=args.per_page,
        lookback_days=int(args.lookback_days or 10),
        screen_unmatched=bool(args.screen_unmatched),
        incremental=bool(args.incremental),
        all_history=bool(args.all_history),
    ))
    return _print(payload, execute=bool(args.execute))


def cmd_sync_collections(args: argparse.Namespace) -> int:
    payload = _run_with_db(lambda conn: services.collections(
        conn,
        source_url=str(args.source_url or ""),
        execute=bool(args.execute),
        create_unmatched=bool(args.create_unmatched),
        lookback_days=int(args.lookback_days or 10),
        save_path=args.save_path,
        incremental=bool(args.incremental),
        all_history=bool(args.all_history),
        max_items=max(1, int(args.max_items)) if args.max_items is not None else None,
    ))
    return _print(payload, execute=bool(args.execute))


def cmd_sync_semble_collections(args: argparse.Namespace) -> int:
    payload = _run_with_db(lambda conn: services.semble(
        conn,
        execute=bool(args.execute),
        create_unmatched=bool(args.create_unmatched),
        pages=int(args.pages or 100),
        per_page=int(args.per_page or 20),
        lookback_days=int(args.lookback_days or 14),
        incremental=bool(args.incremental),
        all_history=bool(args.all_history),
    ))
    return _print(payload, execute=bool(args.execute))


def cmd_sync_tangled_repos(args: argparse.Namespace) -> int:
    payload = _run_with_db(lambda conn: services.tangled(
        conn,
        execute=bool(args.execute),
        create_unmatched=bool(args.create_unmatched),
        incremental=bool(args.incremental),
        all_history=bool(args.all_history),
        max_items=max(1, int(args.max_items)) if args.max_items is not None else None,
    ))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
