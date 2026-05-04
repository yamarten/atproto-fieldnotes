#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data" / "manual_candidates.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append one manual candidate to manual_candidates.jsonl.")
    parser.add_argument("--name", required=True, help="Candidate or project name.")
    parser.add_argument("--url", help="Optional canonical URL.")
    parser.add_argument("--summary", help="Short summary.")
    parser.add_argument("--notes", help="Free-form notes.")
    parser.add_argument("--kind", default="project", help="project | infra_tool | name_only | library | personal_tool | auth_only")
    parser.add_argument("--status", default="candidate", help="candidate | tracked | published | invite_only | beta | inactive | rejected")
    parser.add_argument("--key", help="Optional stable key. Defaults to url or name.")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH, help="Destination JSONL path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = {
        "key": args.key,
        "kind": args.kind,
        "name": args.name,
        "url": args.url,
        "summary": args.summary,
        "notes": args.notes,
        "status": args.status,
    }
    clean_payload = {key: value for key, value in payload.items() if value}
    args.path.parent.mkdir(parents=True, exist_ok=True)
    with args.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(clean_payload, ensure_ascii=False) + "\n")
    print(f"appended path={args.path} key={clean_payload.get('key') or clean_payload.get('url') or clean_payload['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
