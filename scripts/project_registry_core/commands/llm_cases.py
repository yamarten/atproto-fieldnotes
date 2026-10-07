from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..model_evaluation import llm_case_store


def cmd_register(args: argparse.Namespace) -> int:
    result = llm_case_store.register(Path(args.store), Path(args.file))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_append_event(args: argparse.Namespace) -> int:
    result = llm_case_store.append_event(Path(args.store), Path(args.file))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    result = llm_case_store.list_cases(Path(args.store))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    result = llm_case_store.read(Path(args.store), args.case_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if args.case_id:
        result = llm_case_store.read(Path(args.store), args.case_id)
        summary = {"verified": 1, "case_ids": [result["case"]["case_id"]]}
    else:
        rows = llm_case_store.list_cases(Path(args.store))
        summary = {"verified": len(rows), "case_ids": [row["case_id"] for row in rows]}
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
