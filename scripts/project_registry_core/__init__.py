"""Core research pipeline contracts for project_registry."""

from .evidence import CollectorFailure, CollectorResult, EvidenceBundle, EvidenceEntity, EvidenceRecord, RelationEvidence
from .jobs import CancellationToken, OperationEvent, OperationRequest, OperationResult

__all__ = [
    "CollectorFailure",
    "CollectorResult",
    "EvidenceBundle",
    "EvidenceEntity",
    "EvidenceRecord",
    "RelationEvidence",
    "CancellationToken",
    "OperationEvent",
    "OperationRequest",
    "OperationResult",
]
