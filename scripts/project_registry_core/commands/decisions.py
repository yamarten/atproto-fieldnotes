from __future__ import annotations

import argparse
import json

from .. import registry as core
from ..application import decisions as services


def _print(payload: dict[str, object]) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def cmd_set_atproto_usage(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        return _print(services.set_atproto_usage(
            conn, project_key=args.project_key, role=args.role,
            focus=args.focus,
            reason=args.reason or "", clear=args.clear,
        ))
    finally:
        conn.close()


def cmd_set_separate(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.set_separate(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.type,
            other_project_key=args.other_project_key,
            other_seed=args.other_seed,
            other_seed_type=args.other_type,
            clear=bool(args.clear),
        )
    finally:
        conn.close()
    return _print(payload)


def cmd_block_identifier(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.set_identifier_block(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.project_type,
            identifier_type=args.identifier_type,
            value=args.value,
            note=args.note or "",
            clear=bool(args.clear),
        )
    finally:
        conn.close()
    return _print(payload)


def cmd_defer_identifier(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.set_identifier_defer(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.project_type,
            identifier_type=args.identifier_type,
            value=args.value,
            note=args.note or "",
            days=int(args.days or core.DEFAULT_DEFER_IDENTIFIER_DAYS),
            clear=bool(args.clear),
        )
    finally:
        conn.close()
    return _print(payload)


def cmd_defer_promotion(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.set_promotion_defer(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.project_type,
            reason=args.reason or "",
            days=int(args.days) if args.days is not None else None,
            clear=bool(args.clear),
        )
    finally:
        conn.close()
    return _print(payload)
