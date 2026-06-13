from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


EVIDENCE_SCHEMA_VERSION = 2
SUPPORTED_EVIDENCE_SCHEMA_VERSIONS = {1, 2}
EvidenceClassification = Literal["confirmed", "candidate", "derived", "heuristic"]
CollectorStatus = Literal["success", "partial", "skipped", "rate_limited", "failed"]
EVIDENCE_CLASSIFICATIONS = {"confirmed", "candidate", "derived", "heuristic"}
COLLECTOR_STATUSES = {"success", "partial", "skipped", "rate_limited", "failed"}
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


@dataclass(frozen=True)
class EvidenceEntity:
    type: str
    value: str

    def __post_init__(self) -> None:
        if not self.type.strip() or not self.value.strip():
            raise ValueError("evidence entity type and value are required")

    def to_dict(self) -> dict[str, JSONValue]:
        return {"type": self.type, "value": self.value}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceEntity":
        return cls(type=str(payload.get("type") or ""), value=str(payload.get("value") or ""))


@dataclass(frozen=True)
class RelationEvidence:
    subject: EvidenceEntity
    predicate: str
    object: EvidenceEntity
    locator: str = ""
    method: str = ""

    def __post_init__(self) -> None:
        if not self.predicate.strip():
            raise ValueError("relation predicate is required")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "subject": self.subject.to_dict(),
            "predicate": self.predicate,
            "object": self.object.to_dict(),
            "locator": self.locator,
            "method": self.method,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RelationEvidence":
        return cls(
            subject=EvidenceEntity.from_dict(dict(payload.get("subject") or {})),
            predicate=str(payload.get("predicate") or ""),
            object=EvidenceEntity.from_dict(dict(payload.get("object") or {})),
            locator=str(payload.get("locator") or ""),
            method=str(payload.get("method") or ""),
        )


@dataclass(frozen=True)
class CollectorResult:
    source: str
    operation: str
    status: CollectorStatus
    observed_at: str
    request_count: int = 0
    result_count: int = 0
    cursor: str = ""
    stop_reason: str = ""
    retry_at: str = ""
    cache_hits: int = 0

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.operation.strip() or not self.observed_at.strip():
            raise ValueError("collector result source, operation, and observed_at are required")
        if self.status not in COLLECTOR_STATUSES:
            raise ValueError(f"invalid collector status: {self.status}")
        if min(self.request_count, self.result_count, self.cache_hits) < 0:
            raise ValueError("collector result counts cannot be negative")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "source": self.source,
            "operation": self.operation,
            "status": self.status,
            "observed_at": self.observed_at,
            "request_count": self.request_count,
            "result_count": self.result_count,
            "cursor": self.cursor,
            "stop_reason": self.stop_reason,
            "retry_at": self.retry_at,
            "cache_hits": self.cache_hits,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CollectorResult":
        return cls(
            source=str(payload.get("source") or ""),
            operation=str(payload.get("operation") or ""),
            status=str(payload.get("status") or "failed"),  # type: ignore[arg-type]
            observed_at=str(payload.get("observed_at") or ""),
            request_count=int(payload.get("request_count") or 0),
            result_count=int(payload.get("result_count") or 0),
            cursor=str(payload.get("cursor") or ""),
            stop_reason=str(payload.get("stop_reason") or ""),
            retry_at=str(payload.get("retry_at") or ""),
            cache_hits=int(payload.get("cache_hits") or 0),
        )


@dataclass
class EvidenceBundle:
    project_key: str
    collected_at: str
    records: list[EvidenceRecord] = field(default_factory=list)
    failures: list[CollectorFailure] = field(default_factory=list)
    collector_results: list[CollectorResult] = field(default_factory=list)
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_EVIDENCE_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported evidence schema version: {self.schema_version}")
        if not self.project_key.strip():
            raise ValueError("evidence project_key is required")
        if not self.collected_at.strip():
            raise ValueError("evidence collected_at is required")

    def add_record(self, record: EvidenceRecord) -> None:
        self.records.append(record)

    def add_failure(self, failure: CollectorFailure) -> None:
        self.failures.append(failure)

    def add_collector_result(self, result: CollectorResult) -> None:
        self.collector_results.append(result)

    def extend(self, other: "EvidenceBundle") -> None:
        if other.project_key != self.project_key:
            raise ValueError("cannot combine evidence for different projects")
        self.records.extend(other.records)
        self.failures.extend(other.failures)
        self.collector_results.extend(other.collector_results)

    def to_dict(self) -> dict[str, JSONValue]:
        payload: dict[str, JSONValue] = {
            "schema_version": self.schema_version,
            "project_key": self.project_key,
            "collected_at": self.collected_at,
            "records": [record.to_dict() for record in self.records],
            "failures": [failure.to_dict() for failure in self.failures],
        }
        if self.schema_version >= 2:
            payload["collector_results"] = [result.to_dict() for result in self.collector_results]
        return payload

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceBundle":
        records = payload.get("records") or []
        failures = payload.get("failures") or []
        collector_results = payload.get("collector_results") or []
        if not isinstance(records, list) or not isinstance(failures, list) or not isinstance(collector_results, list):
            raise ValueError("evidence records and failures must be arrays")
        return cls(
            schema_version=int(payload.get("schema_version") or 0),
            project_key=str(payload.get("project_key") or ""),
            collected_at=str(payload.get("collected_at") or ""),
            records=[EvidenceRecord.from_dict(dict(item)) for item in records],
            failures=[CollectorFailure.from_dict(dict(item)) for item in failures],
            collector_results=[CollectorResult.from_dict(dict(item)) for item in collector_results],
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


def populate_collector_results(bundle: EvidenceBundle) -> EvidenceBundle:
    """Add conservative source summaries when a collector did not emit explicit results."""
    if bundle.schema_version < 2 or bundle.collector_results:
        return bundle
    roots: list[str] = []
    for source in [record.source for record in bundle.records] + [failure.source for failure in bundle.failures]:
        root = str(source or "").split(":", 1)[0]
        if root and root not in roots:
            roots.append(root)
    for root in roots:
        records = [record for record in bundle.records if record.source.split(":", 1)[0] == root]
        substantive_records = [
            record for record in records
            if record.kind not in {"investigation_query", "investigation_log"}
        ]
        failures = [failure for failure in bundle.failures if failure.source.split(":", 1)[0] == root]
        if failures and substantive_records:
            status: CollectorStatus = "partial"
        elif failures and all(failure.category == "rate_limit" for failure in failures):
            status = "rate_limited"
        elif failures and all(failure.category == "request_budget" for failure in failures):
            status = "skipped"
        elif failures:
            status = "failed"
        else:
            status = "success"
        bundle.add_collector_result(CollectorResult(
            source=root,
            operation="collect",
            status=status,
            observed_at=(records[0].observed_at if records else bundle.collected_at),
            request_count=1,
            result_count=len(substantive_records),
            stop_reason=(failures[0].category if failures else ""),
        ))
    return bundle
