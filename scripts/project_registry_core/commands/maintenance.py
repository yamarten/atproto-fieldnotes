from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import registry as core
from .. import database
from .. import domain_policy
from ..application import maintenance as services


def _with_db(call):
    conn = core.open_db()
    try:
        return call(conn)
    finally:
        conn.close()


def cmd_init_db(args: argparse.Namespace) -> int:
    path = Path(args.db_path).expanduser() if args.db_path else core.DB_PATH
    conn = core.initialize_db(path)
    try:
        payload = {
            "db_path": str(path.resolve()),
            "schema_version": database.database_schema_version(conn),
        }
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_repair_db(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.repair_database(conn, execute=bool(args.execute)))
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0


def cmd_repair_identifier_roles(args: argparse.Namespace) -> int:
    collection_inventory_nsids = None
    if args.collection_inventory:
        inventory_path = Path(args.collection_inventory).expanduser()
        raw_inventory = json.loads(inventory_path.read_text())
        if not isinstance(raw_inventory, list):
            raise ValueError("collection inventory must be a JSON array")
        collection_inventory_nsids = [
            str(item.get("collection") or "")
            for item in raw_inventory
            if isinstance(item, dict) and str(item.get("collection") or "")
        ]
    payload = _with_db(lambda conn: services.repair_identifier_roles(
        conn,
        execute=bool(args.execute),
        example_limit=int(args.example_limit),
        collection_inventory_nsids=collection_inventory_nsids,
    ))
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


def cmd_cleanup_storage_payloads(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.cleanup_storage_payloads(
        conn,
        execute=bool(args.execute),
        example_limit=int(args.example_limit),
        vacuum=bool(args.vacuum),
    ))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_compress_investigation_history(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.compress_investigation_history(
        conn,
        execute=bool(args.execute),
        example_limit=int(args.example_limit),
        vacuum=bool(args.vacuum),
    ))
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


def cmd_audit_source_config(args: argparse.Namespace) -> int:
    interactive = bool(args.interactive)
    if interactive and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise ValueError("default interactive mode requires a TTY; use --no-interactive for JSON dry-run output")
    requested_suffix = domain_policy.normalize_multi_tenant_suffix(args.domain) if args.domain else ""
    added_suffix = False
    should_configure = bool(requested_suffix and args.execute)
    if requested_suffix and requested_suffix not in domain_policy.configured_multi_tenant_suffixes():
        if interactive and not should_configure:
            should_configure = core.prompt_for_confirmation(f"Add {requested_suffix} to config/domain-overrides.json? [y/N]")
    suffixes = [requested_suffix] if requested_suffix else sorted(domain_policy.configured_multi_tenant_suffixes())
    conn = core.open_db()
    try:
        suffix_update = services.configure_multi_tenant_suffix(conn, requested_suffix) if should_configure else None
        added_suffix = bool(suffix_update and suffix_update["added"])
        same_site_refresh = suffix_update["same_site_refresh"] if suffix_update else None
        suggestions = core.build_source_config_split_suggestions(conn, suffixes=suffixes)
        payload = {
            "mode": "interactive" if interactive else "dry_run",
            "requested_suffix": requested_suffix,
            "suffix_added": added_suffix,
            "audited_suffixes": suffixes,
            "same_site_refresh": same_site_refresh,
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
