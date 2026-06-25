from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import registry as core
from ..application import maintenance as services


def cmd_migrate_db(args: argparse.Namespace) -> int:
    payload = services.migrate_database(Path(args.database), dry_run=bool(args.dry_run))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _with_db(call):
    conn = core.open_db()
    try:
        return call(conn)
    finally:
        conn.close()


def cmd_repair_db(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.repair_database(conn, execute=bool(args.execute)))
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0


def cmd_audit_url_fields(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.audit_urls(conn, example_limit=int(args.example_limit)))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_audit_trust_graph(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.audit_trust_graph(conn, example_limit=int(args.example_limit)))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_repair_trust_graph(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.repair_trust_graph(
        conn,
        execute=bool(args.execute),
        downgrade_unsupported=bool(args.downgrade_unsupported),
        example_limit=int(args.example_limit),
    ))
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0


def cmd_cleanup_app_links(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.cleanup_app_links(conn, execute=bool(args.execute)))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_prune_investigation_history(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.prune_investigation_history(
        conn,
        retention_days=int(args.retention_days),
        keep_latest_per_project=int(args.keep_latest_per_project),
        execute=bool(args.execute),
        vacuum=bool(args.vacuum),
    ))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_prune_migration_provenance(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.prune_migration_provenance(
        conn,
        execute=bool(args.execute),
    ))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_audit_source_config(args: argparse.Namespace) -> int:
    interactive = bool(args.interactive)
    if interactive and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise ValueError("default interactive mode requires a TTY; use --no-interactive for JSON dry-run output")
    requested_suffix = core.normalize_multi_tenant_suffix(args.domain) if args.domain else ""
    added_suffix = False
    if requested_suffix and requested_suffix not in core.configured_multi_tenant_suffixes():
        should_add = bool(args.execute)
        if interactive and not should_add:
            should_add = core.prompt_for_confirmation(f"Add {requested_suffix} to config/domain-overrides.json? [y/N]")
        if should_add:
            added_suffix = core.add_configured_multi_tenant_suffix(requested_suffix)
    suffixes = [requested_suffix] if requested_suffix else sorted(core.configured_multi_tenant_suffixes())
    conn = core.open_db()
    try:
        suggestions = core.build_source_config_split_suggestions(conn, suffixes=suffixes)
        payload = {
            "mode": "interactive" if interactive else "dry_run",
            "requested_suffix": requested_suffix,
            "suffix_added": added_suffix,
            "audited_suffixes": suffixes,
            "split_suggestion_count": len(suggestions),
            "split_suggestions": suggestions,
        }
        if not interactive:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        review_result = core.review_source_config_split_suggestions(conn, suffixes, initial_suggestions=suggestions)
    finally:
        conn.close()
    print(json.dumps({**{key: value for key, value in payload.items() if key != "split_suggestions"}, **review_result}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
