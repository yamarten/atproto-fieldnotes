from __future__ import annotations

import argparse
import contextlib
import json
import signal
import threading
from collections.abc import Iterator

from .. import registry as core
from ..application import enrichment as services
from ..jobs import CancellationToken, OperationEvent, OperationRequest


def request_from_args(args: argparse.Namespace) -> OperationRequest:
    operation_id = str(args.resume_run or args.run_id or "")
    return OperationRequest.create(
        "enrich",
        operation_id=operation_id,
        execute=True,
        parameters={
            "project_key": str(args.project_key or ""),
            "seed": str(args.seed or ""),
            "seed_type": str(args.type or ""),
            "all_projects": bool(args.all),
            "all_tracking_states": bool(args.all_tracking_states),
            "tracking_states": tuple(args.tracking_states or ()),
            "include_stable": bool(args.include_stable),
            "include_stalled": bool(args.include_stalled),
            "include_out_of_scope": bool(args.include_out_of_scope),
            "include_archived": bool(args.include_archived),
            "include_ignore": bool(args.include_ignore),
            "new_only": bool(args.new_only),
            "retry_failures": bool(args.retry_failures),
            "continue_on_error": not bool(args.fail_fast),
            "checkpoint_path": str(args.checkpoint_path or ""),
            "no_checkpoint": bool(args.no_checkpoint),
            "resume_run": str(args.resume_run or ""),
            "deep_investigation": bool(args.deep_investigation),
            "llm_endpoint": str(args.llm_endpoint or ""),
            "llm_model": str(args.llm_model or ""),
            "llm_api_key": str(args.llm_api_key or ""),
            "llm_api_style": str(args.llm_api_style or ""),
            "llm_reasoning_effort": str(args.llm_reasoning_effort or ""),
            "llm_max_output_tokens": int(args.llm_max_output_tokens or 0),
            "llm_triage_prompt_suffix_file": str(args.llm_triage_prompt_suffix_file or ""),
        },
    )


@contextlib.contextmanager
def cancel_on_sigint(token: CancellationToken) -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGINT)

    def request_cancel(_signum, _frame) -> None:  # type: ignore[no-untyped-def]
        token.request_cancel("sigint")

    signal.signal(signal.SIGINT, request_cancel)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def cmd_enrich(args: argparse.Namespace) -> int:
    request = request_from_args(args)
    cancellation = CancellationToken()

    def emit(event: OperationEvent) -> None:
        print(json.dumps(event.data, ensure_ascii=False))

    conn = core.open_db()
    try:
        with cancel_on_sigint(cancellation):
            result = services.run(conn, request, emit=emit, cancellation=cancellation)
    finally:
        conn.close()
    if result.status == "cancelled":
        return 130
    return 1 if result.status == "failed" else 0
