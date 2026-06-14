from __future__ import annotations

import contextlib
import json
import signal
import sys
from pathlib import Path
from typing import Any

from . import database
from .application import enrichment
from .jobs import CancellationToken, OperationEvent, OperationRequest


def _write(payload: dict[str, Any]) -> None:
    sys.__stdout__.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    sys.__stdout__.flush()


def run_payload(payload: dict[str, Any]) -> int:
    db_path_value = str(payload.get("db_path") or "").strip()
    if not db_path_value:
        raise ValueError("db_path is required")
    db_path = Path(db_path_value).expanduser()
    request_value = payload.get("request")
    if not isinstance(request_value, dict):
        raise ValueError("request must be an object")
    request = OperationRequest.from_dict(request_value)
    cancellation = CancellationToken()

    def request_cancel(signum: int, _frame: Any) -> None:
        cancellation.request_cancel(signal.Signals(signum).name.lower())

    signal.signal(signal.SIGINT, request_cancel)
    signal.signal(signal.SIGTERM, request_cancel)

    def emit(event: OperationEvent) -> None:
        _write({"type": "event", "payload": event.to_dict()})

    if request.operation != "enrich":
        raise ValueError(f"unsupported worker operation: {request.operation}")
    conn = database.open_database(db_path)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = enrichment.run(conn, request, emit=emit, cancellation=cancellation)
    finally:
        conn.close()
    _write({"type": "result", "payload": result.to_dict()})
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("worker input must be an object")
        return run_payload(payload)
    except Exception as exc:
        _write({
            "type": "worker_error",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
