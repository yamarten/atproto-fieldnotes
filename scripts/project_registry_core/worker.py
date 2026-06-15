from __future__ import annotations

import contextlib
import json
import signal
import sys
from pathlib import Path
from typing import Any

from . import database
from .application import enrichment, review, sync
from .jobs import CancellationToken, OperationEvent, OperationRequest, OperationResult, utc_now


def _write(payload: dict[str, Any]) -> None:
    sys.__stdout__.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    sys.__stdout__.flush()


def _string_set(value: Any) -> set[str] | None:
    if not isinstance(value, (list, tuple, set)):
        return None
    result = {str(item).strip() for item in value if str(item).strip()}
    return result or None


def _run_sync(conn: Any, request: OperationRequest) -> dict[str, Any]:
    parameters = request.parameters
    source = str(parameters.get("source") or "").strip()
    common = {
        "execute": request.execute,
        "incremental": bool(parameters.get("incremental", True)),
        "all_history": bool(parameters.get("all_history")),
    }
    if source == "introduced":
        return sync.introduced(
            conn,
            query=str(parameters.get("query") or "from:yamarten.bsky.social #ATプロトーク"),
            limit=max(1, int(parameters.get("limit") or 10)),
            max_pages=max(1, int(parameters.get("max_pages") or 100)),
            lookback_days=max(1, int(parameters.get("lookback_days") or 7)),
            **common,
        )
    if source == "github":
        return sync.github(
            conn,
            create_unmatched=bool(parameters.get("create_unmatched")),
            pages=int(parameters["pages"]) if parameters.get("pages") else None,
            per_page=int(parameters["per_page"]) if parameters.get("per_page") else None,
            lookback_days=max(1, int(parameters.get("lookback_days") or 10)),
            screen_unmatched=bool(parameters.get("screen_unmatched")),
            **common,
        )
    if source == "collections":
        return sync.collections(
            conn,
            create_unmatched=bool(parameters.get("create_unmatched")),
            lookback_days=max(1, int(parameters.get("lookback_days") or 10)),
            **common,
        )
    if source == "semble":
        return sync.semble(
            conn,
            create_unmatched=bool(parameters.get("create_unmatched")),
            pages=max(1, int(parameters.get("pages") or 100)),
            per_page=max(1, int(parameters.get("per_page") or 20)),
            lookback_days=max(1, int(parameters.get("lookback_days") or 14)),
            **common,
        )
    if source == "tangled":
        return sync.tangled(
            conn,
            create_unmatched=bool(parameters.get("create_unmatched")),
            **common,
        )
    raise ValueError(f"unsupported sync source: {source}")


def _run_review(conn: Any, request: OperationRequest) -> dict[str, Any]:
    parameters = request.parameters
    kind = str(parameters.get("kind") or "").strip()
    if kind == "stalled":
        return review.stalled(
            conn,
            execute=request.execute,
            inactivity_days=max(1, int(parameters.get("inactivity_days") or 45)),
            max_enrich_age_days=max(1, int(parameters.get("max_enrich_age_days") or 30)),
            limit=max(1, int(parameters.get("limit") or 100)),
            note=str(parameters.get("note") or ""),
        )
    common = {
        "execute": request.execute,
        "tracking_states": _string_set(parameters.get("tracking_states")),
        "limit": max(1, int(parameters.get("limit") or 100)),
        "note": str(parameters.get("note") or ""),
        "note_reason": str(parameters.get("note_reason") or ""),
    }
    if kind == "ignore":
        return review.ignore(conn, **common)
    if kind == "out_of_scope":
        return review.out_of_scope(conn, **common)
    raise ValueError(f"unsupported review kind: {kind}")


def _run_simple(
    conn: Any,
    request: OperationRequest,
    cancellation: CancellationToken,
    emit: Any,
) -> OperationResult:
    started_at = utc_now()
    emit(OperationEvent(
        operation_id=request.operation_id,
        operation=request.operation,
        kind="started",
        sequence=1,
        data={"parameters": request.to_dict()["parameters"]},
    ))
    if cancellation.cancel_requested:
        completed_at = utc_now()
        emit(OperationEvent(
            operation_id=request.operation_id,
            operation=request.operation,
            kind="cancelled",
            sequence=2,
            data={"reason": cancellation.reason},
        ))
        return OperationResult(
            operation_id=request.operation_id,
            operation=request.operation,
            status="cancelled",
            started_at=started_at,
            completed_at=completed_at,
            data={"reason": cancellation.reason},
        )
    try:
        payload = _run_sync(conn, request) if request.operation == "sync" else _run_review(conn, request)
    except Exception as exc:
        completed_at = utc_now()
        error = {"type": type(exc).__name__, "message": str(exc)}
        emit(OperationEvent(
            operation_id=request.operation_id,
            operation=request.operation,
            kind="failed",
            sequence=2,
            data={"error": error},
        ))
        return OperationResult(
            operation_id=request.operation_id,
            operation=request.operation,
            status="failed",
            started_at=started_at,
            completed_at=completed_at,
            error=error,
        )
    completed_at = utc_now()
    data = {
        "summary": payload,
        "cancel_requested": cancellation.cancel_requested,
    }
    emit(OperationEvent(
        operation_id=request.operation_id,
        operation=request.operation,
        kind="completed",
        sequence=2,
        data=data,
    ))
    return OperationResult(
        operation_id=request.operation_id,
        operation=request.operation,
        status="completed",
        started_at=started_at,
        completed_at=completed_at,
        data=data,
    )


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

    if request.operation not in {"enrich", "sync", "review"}:
        raise ValueError(f"unsupported worker operation: {request.operation}")
    conn = database.open_database(db_path)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if request.operation == "enrich":
                result = enrichment.run(conn, request, emit=emit, cancellation=cancellation)
            else:
                result = _run_simple(conn, request, cancellation, emit)
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
