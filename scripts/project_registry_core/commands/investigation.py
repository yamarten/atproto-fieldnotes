from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import registry as core
from ..application import investigation as services


def cmd_verify_service(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.verify_service(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.type,
            lane=args.lane,
            service_url=str(args.url or ""),
            execute=bool(args.execute),
        )
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_replay_investigation(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.replay(conn, run_id=args.run_id, project_key=args.project_key)
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_benchmark_investigation(args: argparse.Namespace) -> int:
    payload, exit_code = services.benchmark(path=Path(args.path))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


def cmd_export_investigation_fixture(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        content = services.export_fixture(conn, run_id=args.run_id, case_id=args.case_id or "")
    finally:
        conn.close()
    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
    else:
        print(content, end="")
    return 0
