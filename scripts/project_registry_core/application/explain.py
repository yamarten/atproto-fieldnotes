from __future__ import annotations

import sqlite3
from typing import Any

from .. import registry as core


TOPICS = ("service_status", "ownership", "contribution", "merge_candidate")


def _sources(project: dict[str, Any], predicates: set[str] | None = None) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for item in project.get("relation_evidence", []):
        if not isinstance(item, dict):
            continue
        relation = item.get("relation") if isinstance(item.get("relation"), dict) else {}
        predicate = str(relation.get("predicate") or "")
        if predicates and predicate not in predicates:
            continue
        output.append({
            "kind": "relation",
            "predicate": predicate,
            "source": str(item.get("source") or ""),
            "locator": str(relation.get("locator") or ""),
        })
    return output[:50]


def explain_project(conn: sqlite3.Connection, *, project_key: str, topic: str = "all") -> dict[str, Any]:
    resolved = core.resolve_project_key_input(conn, project_key)
    project = core.fetch_project(conn, resolved)
    if project is None:
        raise ValueError(f"project not found: {project_key}")
    project = core.ensure_project_shape(project)
    duplicate_candidates = core.compute_duplicate_candidates(conn, project)
    recommendation = core.compute_merge_recommendation(dict(project, duplicate_candidates=duplicate_candidates))

    service_evidence = [str(item) for item in project.get("service_status_evidence", []) if str(item)]
    service = {
        "topic": "service_status",
        "conclusion": str(project.get("service_status") or "unknown"),
        "rule": "saved service verification state",
        "supporting_signals": service_evidence,
        "counter_signals": [item for item in service_evidence if any(token in item for token in ("failed", "unreachable", "conflict"))],
        "sources": _sources(project, {"serves_collection", "links_to_service"}),
    }
    resolved_nsids = [str(item) for item in project.get("resolved_nsids", []) if str(item)]
    ownership = {
        "topic": "ownership",
        "conclusion": str(project.get("ownership_signal") or ("supported" if resolved_nsids else "unknown")),
        "rule": "confirmed identifiers plus saved ownership relations",
        "supporting_signals": [f"owned_nsid:{item}" for item in resolved_nsids],
        "counter_signals": [] if resolved_nsids else ["no_confirmed_owned_nsid"],
        "sources": _sources(project, {"defines_schema", "owns_namespace", "serves_collection"}),
    }
    contribution_evidence = [str(item) for item in project.get("atproto_contribution_evidence", []) if str(item)]
    contribution = {
        "topic": "contribution",
        "conclusion": str(project.get("atproto_contribution_level") or "unknown"),
        "rule": "rule-based atproto contribution evaluation",
        "supporting_signals": contribution_evidence,
        "counter_signals": list(project.get("atproto_contribution_blockers", []) or []),
        "score": int(project.get("atproto_contribution_score") or 0),
        "sources": _sources(project),
    }
    merge = {
        "topic": "merge_candidate",
        "conclusion": "recommended" if recommendation else "none",
        "rule": "current duplicate candidate and merge recommendation evaluation",
        "supporting_signals": list(recommendation.get("reasons", []) or recommendation.get("evidence", []) or []),
        "counter_signals": list(recommendation.get("blockers", []) or []),
        "score": int(recommendation.get("score") or 0),
        "target_project_key": str(recommendation.get("target_project_key") or ""),
        "auto_merge_ready": bool(recommendation.get("auto_merge_ready")),
        "sources": [],
    }
    explanations = {
        "service_status": service,
        "ownership": ownership,
        "contribution": contribution,
        "merge_candidate": merge,
    }
    if topic != "all" and topic not in explanations:
        raise ValueError(f"unknown explain topic: {topic}")
    return {
        "project_key": resolved,
        "topics": explanations if topic == "all" else {topic: explanations[topic]},
        "network_used": False,
        "llm_used": False,
    }
