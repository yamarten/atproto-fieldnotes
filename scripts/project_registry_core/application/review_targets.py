from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


def fingerprint(target_type: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"target_type": target_type, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"review:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


def relation_target(row: sqlite3.Row) -> dict[str, Any]:
    payload = {
        "subject_type": str(row["subject_type"]),
        "subject_value": str(row["subject_value"]),
        "predicate": str(row["predicate"]),
        "object_type": str(row["object_type"]),
        "object_value": str(row["object_value"]),
        "source": str(row["source"]),
        "locator": str(row["locator"]),
        "method": str(row["method"]),
    }
    return {"target_type": "relation", "target_value": fingerprint("relation", payload), "metadata": payload}


def failure_target(run_id: str, failure: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "source": str(failure.get("source") or ""),
        "operation": str(failure.get("operation") or ""),
        "target": str(failure.get("target") or ""),
        "category": str(failure.get("category") or ""),
        "message": str(failure.get("message") or ""),
    }
    return {
        "target_type": "collector_failure",
        "target_value": fingerprint("collector_failure", payload),
        "metadata": payload,
    }


def with_review_state(target: dict[str, Any], active_decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Project review attention onto evidence without changing its truth or adoption."""
    decisions = [
        item for item in active_decisions
        if item["kind"] in {"dismiss_review_target", "defer_review_target"}
        and item["target_type"] == target["target_type"]
        and item["target_value"] == target["target_value"]
    ]
    status = "open"
    if decisions:
        status = "dismissed" if any(item["kind"] == "dismiss_review_target" for item in decisions) else "deferred"
    return {**target, "status": status, "decisions": decisions}
