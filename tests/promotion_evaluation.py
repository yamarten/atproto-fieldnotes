from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project_registry_core import registry
from project_registry_core.replay import semantic_diff
from project_registry_core import project_record


def _nonzero_score(score_breakdown: dict[str, Any]) -> dict[str, Any]:
    return {
        "total": int(score_breakdown.get("total", 0) or 0),
        "components": {
            str(key): int(value)
            for key, value in dict(score_breakdown.get("components") or {}).items()
            if int(value or 0) != 0
        },
    }


def _fixture_project(case: dict[str, Any]) -> dict[str, Any]:
    subject = dict(case.get("subject") or {})
    subject_ref = str(subject.get("ref") or "").strip()
    if not subject_ref:
        raise ValueError("promotion fixture subject ref is required")
    subject["project_key"] = f"fixture:{subject_ref}"
    subject["website_url"] = str(subject.pop("service_url", "") or "")
    project = registry.apply_promotion_state(project_record.ensure_project_shape(subject))
    expected_promotion = dict(case.get("expected_promotion") or {})
    if expected_promotion.get("disposition") == "deprioritize":
        registry.set_promotion_priority_on_project(
            project,
            lowered=True,
            reason=str(expected_promotion.get("reason") or ""),
        )
    return project


def evaluate(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0) or 0) != 1:
        raise ValueError("unsupported manual gold fixture schema version")
    cases = list(payload.get("promotion_cases") or [])
    if not cases:
        raise ValueError("manual gold fixture has no promotion cases")

    case_by_ref: dict[str, dict[str, Any]] = {}
    case_ids: set[str] = set()
    projects: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("promotion fixture case_id is required")
        if case_id in case_ids:
            raise ValueError(f"duplicate promotion fixture case_id: {case_id}")
        case_ids.add(case_id)
        expected_promotion = dict(case.get("expected_promotion") or {})
        if expected_promotion.get("disposition") not in {"exclude", "queue", "deprioritize"}:
            raise ValueError(f"invalid promotion disposition for {case_id}")
        if not dict(case.get("expected_policy") or {}):
            raise ValueError(f"expected_policy is required for {case_id}")
        project = _fixture_project(case)
        subject_ref = str(project.get("ref") or "")
        if subject_ref in case_by_ref:
            raise ValueError(f"duplicate promotion fixture subject ref: {subject_ref}")
        case_by_ref[subject_ref] = case
        projects.append(project)

    queue = registry.build_showcase_queue_from_projects(projects, limit=None)
    queued_by_ref = {
        str(item["project_key"]).removeprefix("fixture:"): item
        for item in queue
    }
    actual_order = [
        str(item["project_key"]).removeprefix("fixture:")
        for item in queue
    ]
    expected_order = [
        str(item)
        for item in dict(payload.get("promotion_queue_expectation") or {}).get("ordered_subject_refs") or []
    ]
    expected_queued_refs = {
        str(dict(case.get("subject") or {}).get("ref") or "")
        for case in cases
        if dict(case.get("expected_promotion") or {}).get("disposition") != "exclude"
    }
    if len(expected_order) != len(set(expected_order)) or set(expected_order) != expected_queued_refs:
        raise ValueError("promotion_queue_expectation must list every expected queued subject exactly once")
    order_diff = semantic_diff(
        {"ordered_subject_refs": expected_order},
        {"ordered_subject_refs": actual_order},
    )

    results: list[dict[str, Any]] = []
    case_regression_count = 0
    for project in projects:
        subject_ref = str(project.get("ref") or "")
        case = case_by_ref[subject_ref]
        signals = registry.compute_showcase_signals(project)
        policy = registry.promotion_policy_projection(project, signals)
        queue_item = queued_by_ref.get(subject_ref)
        actual = {
            "disposition": (
                "deprioritize"
                if queue_item is not None and policy["priority_group"] == "lowered"
                else "queue"
                if queue_item is not None
                else "exclude"
            ),
            "candidate_visibility": policy["candidate_visibility"],
            "service_evidence_group": policy["service_evidence_group"],
            "priority_group": policy["priority_group"],
            "priority_reason": policy["priority_reason"],
            "signal_kinds": sorted(str(item.get("kind") or "") for item in signals),
            "top_priority": max([int(item.get("priority", 0) or 0) for item in signals] or [0]),
            "score_breakdown": _nonzero_score(policy["score_breakdown"]),
            "queue_rank": actual_order.index(subject_ref) + 1 if subject_ref in actual_order else None,
        }
        expected = {
            "disposition": str(dict(case.get("expected_promotion") or {}).get("disposition") or ""),
            **dict(case.get("expected_policy") or {}),
        }
        diff = semantic_diff(expected, {key: actual.get(key) for key in expected})
        if diff["changed"]:
            case_regression_count += 1
        results.append({
            "case_id": str(case.get("case_id") or subject_ref),
            "subject_ref": subject_ref,
            "expected": expected,
            "actual": actual,
            "diff": diff,
        })

    return {
        "fixture_schema_version": 1,
        "case_count": len(results),
        "passed_case_count": len(results) - case_regression_count,
        "case_regression_count": case_regression_count,
        "order_regression": bool(order_diff["changed"]),
        "regression_count": case_regression_count + int(order_diff["changed"]),
        "expected_order": expected_order,
        "actual_order": actual_order,
        "order_diff": order_diff,
        "results": results,
    }
