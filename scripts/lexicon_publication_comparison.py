#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_registry_core.transport import http_transport
from project_registry_core.publications.lexicon_publication_comparison import (
    collection_inventory_nsids,
    compare,
    fetch_snapshot,
    load_database_index,
    open_read_only_database,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Bounded read-only Jetstream Lexicon publication comparison")
    value.add_argument("--db-path", required=True, type=Path)
    value.add_argument("--service", default="https://jetstream.us-west.bsky.network")
    value.add_argument("--after-seq", type=int, default=0)
    value.add_argument("--max-events", type=int, default=10000)
    value.add_argument("--max-seconds", type=int, default=300)
    value.add_argument("--events-jsonl", type=Path, nargs="+")
    value.add_argument("--write-events-jsonl", type=Path)
    value.add_argument("--collection-inventory-url")
    value.add_argument("--collection-inventory-json", type=Path)
    value.add_argument("--write-collection-inventory-json", type=Path)
    value.add_argument("--sample-limit", type=int, default=20)
    value.add_argument("--output", type=Path)
    return value


def _events_from_jsonl(paths: list[Path]) -> tuple[list[dict], dict]:
    events: list[dict] = []
    segments: list[dict] = []
    for path in paths:
        segment: dict = {"input": str(path)}
        for line in path.read_text().splitlines():
            item = json.loads(line)
            if item.get("type") == "summary":
                segment.update(item)
            else:
                events.append(item)
        segments.append(segment)
    segments.sort(key=lambda item: int(item.get("after_seq") or 0))
    coverage_complete = bool(segments and int(segments[0].get("after_seq") or 0) == 0)
    covered_through = 0
    for segment in segments:
        after = int(segment.get("after_seq") or 0)
        if after > covered_through:
            coverage_complete = False
        covered_through = max(covered_through, int(segment.get("last_event_seq") or after))
    if segments and bool(segments[-1].get("truncated")):
        coverage_complete = False
    summary: dict = {
        "offline": True,
        "segments": segments,
        "after_seq": int(segments[0].get("after_seq") or 0) if segments else 0,
        "sealed_tip_seq": int(segments[-1].get("sealed_tip_seq") or 0) if segments else 0,
        "last_event_seq": covered_through,
        "event_count": len(events),
        "truncated": not coverage_complete,
        "timed_out": any(bool(segment.get("timed_out")) for segment in segments),
    }
    return events, summary


def _observed_collections(args: argparse.Namespace) -> set[str]:
    payload = []
    if args.collection_inventory_json:
        payload = json.loads(args.collection_inventory_json.read_text())
    elif args.collection_inventory_url:
        payload = http_transport.request_json(args.collection_inventory_url)
    collections = collection_inventory_nsids(payload)
    if args.write_collection_inventory_json:
        args.write_collection_inventory_json.parent.mkdir(parents=True, exist_ok=True)
        args.write_collection_inventory_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    return collections


def main() -> int:
    args = parser().parse_args()
    if args.max_events < 1 or args.max_seconds < 1 or args.after_seq < 0 or args.sample_limit < 0:
        raise SystemExit("bounds must be positive and after-seq must be non-negative")
    if args.events_jsonl:
        events, snapshot = _events_from_jsonl(args.events_jsonl)
    else:
        snapshot = fetch_snapshot(service=args.service, after_seq=args.after_seq, max_events=args.max_events, max_seconds=args.max_seconds)
        events = list(snapshot.pop("events"))
    if args.write_events_jsonl:
        args.write_events_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.write_events_jsonl.open("w") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            handle.write(json.dumps({"type": "summary", **snapshot}, ensure_ascii=False, sort_keys=True) + "\n")
    conn = open_read_only_database(args.db_path)
    try:
        database_index = load_database_index(conn)
        result = compare(events, database_index, _observed_collections(args), sample_limit=args.sample_limit)
    finally:
        conn.close()
    result["input_contract"] = {
        "collection": "com.atproto.lexicon.schema",
        "after_seq_exclusive": args.after_seq,
        "sealed_tip_seq": snapshot.get("sealed_tip_seq"),
        "last_event_seq": snapshot.get("last_event_seq"),
        "max_events_per_segment": args.max_events,
        "max_seconds_per_segment": args.max_seconds,
        "truncated": bool(snapshot.get("truncated")),
        "timed_out": bool(snapshot.get("timed_out")),
        "event_count": len(events),
        "offline_input": bool(args.events_jsonl),
        "collection_inventory": str(args.collection_inventory_json or args.collection_inventory_url or "none"),
        "segments": snapshot.get("segments", []),
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
