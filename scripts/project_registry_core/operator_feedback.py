"""Operator introduction plans and service usage assertions, independent of ranking."""

from __future__ import annotations

from typing import Any

from .relations import normalize_relation_records
from .identity import identifiers


USAGE_SOURCE = "operator_service_usage"


def normalize_history(values: Any) -> list[dict[str, str]]:
    return [
        {str(key): str(value) for key, value in item.items()}
        for item in values if isinstance(item, dict) and item.get("id")
    ] if isinstance(values, list) else []


def active_plan(project: dict[str, Any]) -> dict[str, str] | None:
    return next((item for item in reversed(project.get("introduction_plans", []))
                 if not item.get("cleared_at")), None)


def clear_introduced_plans(project: dict[str, Any], *, cleared_at: str) -> int:
    """Complete plans against the saved introduction, retaining every entry.

    An explicit reintroduction plan remembers the URL already present when it
    was made. Re-saving that introduction must not complete the new plan.
    Older entries without that baseline are completed when already introduced.
    """
    current = identifiers.normalize_url(str(project.get("introduced_url") or ""))
    if not current:
        return 0
    cleared = 0
    for entry in project.get("introduction_plans", []):
        previous = identifiers.normalize_url(str(entry.get("introduced_url_at_creation") or ""))
        if not entry.get("cleared_at") and current != previous:
            entry.update(cleared_at=cleared_at, clear_reason="introduced", cleared_by_introduced_url=current)
            cleared += 1
    return cleared


def reconcile_usage(project: dict[str, Any]) -> None:
    records = [item for item in project.get("relation_evidence", []) if item.get("source") != USAGE_SOURCE]
    for item in project.get("service_usage_decisions", []):
        if item.get("cleared_at"):
            continue
        records.append({
            "relation": {
                "subject": {"type": "service", "value": item["service_url"]},
                "predicate": "uses_collection",
                "object": {"type": "nsid", "value": item["nsid"]},
                "locator": item.get("source_url", ""), "method": "operator",
            },
            "source": USAGE_SOURCE, "classification": "confirmed",
            "observed_at": item["added_at"],
            "context": {"operator_decision_id": item["id"], "reason": item.get("reason", "")},
        })
    project["relation_evidence"] = normalize_relation_records(records)


def merge_history(target: list[dict[str, str]], source: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    output = []
    for item in [*target, *source]:
        if item["id"] not in seen:
            output.append(dict(item))
            seen.add(item["id"])
    return output
