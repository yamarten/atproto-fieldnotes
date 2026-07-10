from __future__ import annotations

import argparse
import json

from .. import registry as core
from ..application import merge as services


def cmd_inspect_merge_pair(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.inspect_pair(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.type,
            target_project_key=args.target_project_key,
            target_seed=args.target_seed,
            target_seed_type=args.target_type,
        )
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.merge_projects(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.type,
            target_project_key=args.target_project_key,
            target_seed=args.target_seed,
            target_seed_type=args.target_type,
            use_recommendation=bool(args.use_recommendation),
            execute=bool(args.execute),
            force=bool(args.force),
        )
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0


def cmd_split_project(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.split_identifier(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.project_type,
            identifier_type=args.identifier_type,
            value=args.value,
            note=args.note or "",
            execute=bool(args.execute),
        )
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0
