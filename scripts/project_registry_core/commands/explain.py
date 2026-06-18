from __future__ import annotations

import argparse
import json

from .. import registry as core
from ..application import explain as services

TOPICS = services.TOPICS


def cmd_explain(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.explain_project(conn, project_key=args.project_key, topic=args.topic)
    finally:
        conn.close()
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"[{payload['project_key']}]")
    for topic, item in payload["topics"].items():
        print(f"{topic}: {item['conclusion']}")
        print(f"  rule: {item['rule']}")
        for signal in item.get("supporting_signals", []):
            print(f"  supports: {signal}")
        for signal in item.get("counter_signals", []):
            print(f"  counters: {signal}")
    return 0
