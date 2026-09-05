#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from project_registry_core.lexicon_publication_catalog import (
    advance_report_revision,
    assert_distinct_database_paths,
    build_evaluation_report,
    catalog_state,
    ingest_segment,
    initialize_catalog,
    load_registry_index,
    record_non_record_usage_request,
    verify_due_publications,
)
from project_registry_core.lexicon_publication_comparison import (
    collection_inventory_nsids,
    fetch_snapshot,
    open_read_only_database,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Maintain a sidecar Jetstream Lexicon publication catalog")
    subparsers = value.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="Initialize the sidecar schema")
    init.add_argument("--sidecar-db", required=True, type=Path)

    update = subparsers.add_parser("update", help="Stage/apply a bounded sealed snapshot segment and emit evaluation JSON")
    update.add_argument("--sidecar-db", required=True, type=Path)
    update.add_argument("--registry-db", required=True, type=Path)
    update.add_argument("--collection-inventory-json", type=Path)
    update.add_argument("--events-jsonl", type=Path, nargs="+")
    update.add_argument("--service", default="https://jetstream.us-west.bsky.network")
    update.add_argument("--max-events", type=int, default=10000)
    update.add_argument("--max-seconds", type=int, default=300)
    update.add_argument("--verify-limit", type=int, default=20)
    update.add_argument("--verification-ttl-hours", type=int, default=168)
    update.add_argument("--verification-retry-hours", type=int, default=24)
    update.add_argument("--dns-timeout", type=float, default=10.0)
    update.add_argument("--http-timeout", type=float, default=20.0)
    update.add_argument("--output", type=Path)

    verify = subparsers.add_parser("verify", help="Verify due publications for explicit schema or publisher targets")
    verify.add_argument("--sidecar-db", required=True, type=Path)
    verify.add_argument("--registry-db", required=True, type=Path)
    verify.add_argument("--collection-inventory-json", type=Path)
    verify.add_argument("--schema-id", action="append", default=[])
    verify.add_argument("--did", action="append", default=[])
    verify.add_argument("--verify-limit", type=int, default=20)
    verify.add_argument("--verification-ttl-hours", type=int, default=168)
    verify.add_argument("--verification-retry-hours", type=int, default=24)
    verify.add_argument("--dns-timeout", type=float, default=10.0)
    verify.add_argument("--http-timeout", type=float, default=20.0)
    verify.add_argument("--output", type=Path)

    report = subparsers.add_parser("report", help="Emit the stable Codex evaluation JSON without network access")
    report.add_argument("--sidecar-db", required=True, type=Path)
    report.add_argument("--registry-db", required=True, type=Path)
    report.add_argument("--collection-inventory-json", type=Path)
    report.add_argument("--since-revision", type=int)
    report.add_argument("--advance", action="store_true")
    report.add_argument("--output", type=Path)
    signal = subparsers.add_parser("signal-non-record-usage", help="Record a concrete non-record Lexicon usage request for review")
    signal.add_argument("--sidecar-db", required=True, type=Path)
    signal.add_argument("--request-key", required=True)
    return value


def read_segment(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    summary: dict[str, Any] | None = None
    for line in path.read_text().splitlines():
        item = json.loads(line)
        if item.get("type") == "summary":
            if summary is not None:
                raise ValueError(f"multiple summaries in segment: {path}")
            summary = dict(item)
        else:
            events.append(item)
    if summary is None:
        raise ValueError(f"missing segment summary: {path}")
    return events, summary


def write_report(report: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        temporary.write_text(rendered)
        temporary.replace(output)
    else:
        print(rendered, end="")


def registry_index(sidecar_path: Path, registry_path: Path) -> dict[str, Any]:
    assert_distinct_database_paths(sidecar_path, registry_path)
    registry = open_read_only_database(registry_path)
    try:
        return load_registry_index(registry)
    finally:
        registry.close()


def add_collection_inventory(index: dict[str, Any], path: Path | None) -> dict[str, Any]:
    if path is None:
        return index
    merged = dict(index)
    merged["observed_collections"] = set(index.get("observed_collections", set())) | collection_inventory_nsids(
        json.loads(path.read_text())
    )
    return merged


def main() -> int:
    args = parser().parse_args()
    if args.command not in {"init", "signal-non-record-usage"}:
        assert_distinct_database_paths(args.sidecar_db, args.registry_db)
    conn = initialize_catalog(args.sidecar_db)
    try:
        if args.command == "init":
            print(json.dumps({"schema_version": catalog_state(conn)["schema_version"], "sidecar_db": str(args.sidecar_db)}, sort_keys=True))
            return 0
        if args.command == "signal-non-record-usage":
            history = record_non_record_usage_request(conn, args.request_key)
            print(json.dumps({
                "review_due": history["review_due"],
                "review_reasons": history["review_reasons"],
                "non_record_usage_requests": history["non_record_usage_requests"],
            }, ensure_ascii=False, sort_keys=True))
            return 0
        index = add_collection_inventory(
            registry_index(args.sidecar_db, args.registry_db), args.collection_inventory_json,
        )
        if args.command == "report":
            report = build_evaluation_report(conn, index, since_revision=args.since_revision)
            write_report(report, args.output)
            if args.advance:
                advance_report_revision(conn, int(report["catalog"]["catalog_revision"]))
            return 0
        if args.command == "verify":
            if not args.schema_id and not args.did:
                raise ValueError("verify requires at least one --schema-id or --did target")
            if args.verify_limit < 1:
                raise ValueError("verify-limit must be positive")
            before = catalog_state(conn)
            verification = verify_due_publications(
                conn, limit=args.verify_limit, schema_ids=args.schema_id, dids=args.did,
                ttl_hours=args.verification_ttl_hours, retry_hours=args.verification_retry_hours,
                dns_timeout=args.dns_timeout, http_timeout=args.http_timeout,
            )
            report = build_evaluation_report(conn, index, since_revision=int(before["report_revision"]))
            report["run"] = {"verification": verification}
            write_report(report, args.output)
            advance_report_revision(conn, int(report["catalog"]["catalog_revision"]))
            return 0
        if args.max_events < 1 or args.max_seconds < 1 or args.verify_limit < 0:
            raise ValueError("snapshot bounds must be positive and verify-limit must be non-negative")
        before = catalog_state(conn)
        ingestion_results = []
        if args.events_jsonl:
            for path in args.events_jsonl:
                events, summary = read_segment(path)
                ingestion_results.append(ingest_segment(conn, events, summary))
        else:
            state = catalog_state(conn)
            after_seq = int(state["pending_after_seq"] if state["pending_after_seq"] is not None else state["watermark_seq"])
            snapshot = fetch_snapshot(
                service=args.service, after_seq=after_seq, max_events=args.max_events,
                max_seconds=args.max_seconds,
                sealed_tip_seq=int(state["pending_sealed_tip_seq"]) if state["pending_sealed_tip_seq"] is not None else None,
            )
            events = list(snapshot.pop("events"))
            ingestion_results.append(ingest_segment(conn, events, snapshot))
        verification = verify_due_publications(
            conn, limit=args.verify_limit, ttl_hours=args.verification_ttl_hours,
            retry_hours=args.verification_retry_hours, dns_timeout=args.dns_timeout,
            http_timeout=args.http_timeout,
        ) if args.verify_limit else {"eligible_checked": 0, "verified": 0, "failed": 0}
        report = build_evaluation_report(conn, index, since_revision=int(before["report_revision"]))
        report["run"] = {"ingestion": ingestion_results, "verification": verification}
        write_report(report, args.output)
        advance_report_revision(conn, int(report["catalog"]["catalog_revision"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
