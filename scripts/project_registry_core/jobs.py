from __future__ import annotations

import datetime as dt
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


OperationStatus = Literal["completed", "cancelled", "failed"]
EventKind = Literal["started", "progress", "item_completed", "cancelled", "failed", "completed"]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def generate_operation_id(operation: str) -> str:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{operation}-{timestamp}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class OperationRequest:
    operation_id: str
    operation: str
    parameters: dict[str, Any] = field(default_factory=dict)
    execute: bool = False
    requested_at: str = field(default_factory=utc_now)
    schema_version: int = 1

    @classmethod
    def create(
        cls,
        operation: str,
        *,
        operation_id: str = "",
        parameters: dict[str, Any] | None = None,
        execute: bool = False,
    ) -> OperationRequest:
        normalized_operation = str(operation or "").strip()
        if not normalized_operation:
            raise ValueError("operation is required")
        return cls(
            operation_id=str(operation_id or generate_operation_id(normalized_operation)).strip(),
            operation=normalized_operation,
            parameters=dict(parameters or {}),
            execute=bool(execute),
        )

    def to_dict(self, *, include_sensitive: bool = False) -> dict[str, Any]:
        parameters = dict(self.parameters)
        if not include_sensitive:
            for key in list(parameters):
                if key.endswith("api_key") or key in {"token", "password"}:
                    parameters[key] = "<configured>" if parameters[key] else ""
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "operation": self.operation,
            "requested_at": self.requested_at,
            "execute": self.execute,
            "parameters": parameters,
        }


@dataclass(frozen=True)
class OperationEvent:
    operation_id: str
    operation: str
    kind: EventKind
    sequence: int
    data: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=utc_now)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "operation": self.operation,
            "kind": self.kind,
            "sequence": self.sequence,
            "at": self.at,
            "data": self.data,
        }


@dataclass(frozen=True)
class OperationResult:
    operation_id: str
    operation: str
    status: OperationStatus
    started_at: str
    completed_at: str
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "operation": self.operation,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "data": self.data,
            "error": self.error,
        }


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = ""
        self._requested_at = ""

    def request_cancel(self, reason: str = "operator_requested") -> bool:
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = str(reason or "operator_requested")
            self._requested_at = utc_now()
            self._event.set()
            return True

    @property
    def cancel_requested(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def requested_at(self) -> str:
        return self._requested_at


EventSink = Callable[[OperationEvent], None]

