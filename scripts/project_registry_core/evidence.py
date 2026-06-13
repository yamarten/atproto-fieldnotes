from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


EVIDENCE_SCHEMA_VERSION = 1
EvidenceClassification = Literal["confirmed", "candidate", "derived", "heuristic"]
EVIDENCE_CLASSIFICATIONS = {"confirmed", "candidate", "derived", "heuristic"}
JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]


def _validate_json_value(value: Any, *, path: str) -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} is not JSON-compatible: {type(value).__name__}")


@dataclass(frozen=True)
class EvidenceRecord:
    kind: str
    source: str
    classification: EvidenceClassification
    observed_at: str
    value: JSONValue
    context: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("evidence kind is required")
        if not self.source.strip():
            raise ValueError("evidence source is required")
        if self.classification not in EVIDENCE_CLASSIFICATIONS:
            raise ValueError(f"invalid evidence classification: {self.classification}")
        if not self.observed_at.strip():
            raise ValueError("evidence observed_at is required")
        _validate_json_value(self.value, path="value")
        _validate_json_value(self.context, path="context")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "kind": self.kind,
            "source": self.source,
            "classification": self.classification,
            "observed_at": self.observed_at,
            "value": self.value,
            "context": dict(self.context),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceRecord":
        return cls(
            kind=str(payload.get("kind") or ""),
            source=str(payload.get("source") or ""),
            classification=str(payload.get("classification") or ""),  # type: ignore[arg-type]
            observed_at=str(payload.get("observed_at") or ""),
            value=payload.get("value"),
            context=dict(payload.get("context") or {}),
        )


@dataclass(frozen=True)
class CollectorFailure:
    source: str
    operation: str
    target: str
    category: str
    message: str
    retryable: bool

    def __post_init__(self) -> None:
        for field_name in ("source", "operation", "category", "message"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"collector failure {field_name} is required")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "source": self.source,
            "operation": self.operation,
            "target": self.target,
            "category": self.category,
            "message": self.message,
            "retryable": self.retryable,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CollectorFailure":
        return cls(
            source=str(payload.get("source") or ""),
            operation=str(payload.get("operation") or ""),
            target=str(payload.get("target") or ""),
            category=str(payload.get("category") or ""),
            message=str(payload.get("message") or ""),
            retryable=bool(payload.get("retryable")),
        )


@dataclass
class EvidenceBundle:
    project_key: str
    collected_at: str
    records: list[EvidenceRecord] = field(default_factory=list)
    failures: list[CollectorFailure] = field(default_factory=list)
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError(f"unsupported evidence schema version: {self.schema_version}")
        if not self.project_key.strip():
            raise ValueError("evidence project_key is required")
        if not self.collected_at.strip():
            raise ValueError("evidence collected_at is required")

    def add_record(self, record: EvidenceRecord) -> None:
        self.records.append(record)

    def add_failure(self, failure: CollectorFailure) -> None:
        self.failures.append(failure)

    def extend(self, other: "EvidenceBundle") -> None:
        if other.project_key != self.project_key:
            raise ValueError("cannot combine evidence for different projects")
        self.records.extend(other.records)
        self.failures.extend(other.failures)

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": self.schema_version,
            "project_key": self.project_key,
            "collected_at": self.collected_at,
            "records": [record.to_dict() for record in self.records],
            "failures": [failure.to_dict() for failure in self.failures],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceBundle":
        records = payload.get("records") or []
        failures = payload.get("failures") or []
        if not isinstance(records, list) or not isinstance(failures, list):
            raise ValueError("evidence records and failures must be arrays")
        return cls(
            schema_version=int(payload.get("schema_version") or 0),
            project_key=str(payload.get("project_key") or ""),
            collected_at=str(payload.get("collected_at") or ""),
            records=[EvidenceRecord.from_dict(dict(item)) for item in records],
            failures=[CollectorFailure.from_dict(dict(item)) for item in failures],
        )

    @classmethod
    def from_json(cls, content: str) -> "EvidenceBundle":
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("evidence payload must be an object")
        return cls.from_dict(payload)

    @classmethod
    def load(cls, path: Path) -> "EvidenceBundle":
        return cls.from_json(path.read_text(encoding="utf-8"))
