from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

from .. import merge_policy_shadow, registry as core
from . import decisions, review_targets
from project_registry_core import project_record


TARGET_TYPE = "merge_policy_pair"
DEFAULT_DEFER_DAYS = 30


class RecommendationChangedError(RuntimeError):
    pass


def _project_summary(project: dict[str, Any]) -> dict[str, Any]:
    legacy = project.get("legacy") or {}
    return {
        "project_key": str(project.get("project_key") or ""),
        "name": str(project.get("name") or ""),
        "website_url": str(project.get("website_url") or ""),
        "tracking_state": str(legacy.get("tracking_state") or ""),
        "category": str(legacy.get("category") or ""),
    }


def _candidate_payload(entry: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    lookup = {
        str(entry["left"]["project_key"]): entry["left"],
        str(entry["right"]["project_key"]): entry["right"],
    }
    source_key = str(candidate["fragment_project_key"])
    target_key = str(candidate["target_project_key"])
    source = lookup[source_key]
    target = lookup[target_key]
    evidence = {
        "policy_version": merge_policy_shadow.POLICY_VERSION,
        "source_project_key": source_key,
        "target_project_key": target_key,
        "reason": str(entry["shadow"].get("reason") or ""),
        "recommendation_kind": str(entry["shadow"].get("recommendation_kind") or ""),
        "candidate": candidate,
    }
    review_target = {
        "target_type": TARGET_TYPE,
        "target_value": review_targets.fingerprint(TARGET_TYPE, evidence),
        "metadata": evidence,
    }
    return {
        "source_project_key": source_key,
        "target_project_key": target_key,
        "source_name": str(source.get("name") or source_key),
        "target_name": str(target.get("name") or target_key),
        "source": _project_summary(source),
        "target": _project_summary(target),
        "reason": evidence["reason"],
        "recommendation_kind": evidence["recommendation_kind"],
        "discovery": list(entry.get("discovery") or []),
        "fragment_collections": list(candidate.get("fragment_collections") or []),
        "target_collection_count": int(candidate.get("target_collection_count") or 0),
        "target_independent_components": list(candidate.get("target_independent_components") or []),
        "exact_collection_overlap": list(candidate.get("exact_collection_overlap") or []),
        "shared_authority_namespaces": list(candidate.get("shared_authority_namespaces") or []),
        "shared_direct_namespaces": list(candidate.get("shared_direct_namespaces") or []),
        "shared_aliases": list(candidate.get("shared_aliases") or []),
        "composition_relations": list(candidate.get("composition_relations") or []),
        "rule": str(candidate.get("rule") or ""),
        "review_target": review_target,
    }


def _recommendations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for entry in merge_policy_shadow.recommended_review_pairs(conn):
        for candidate in entry["shadow"]["signals"]["fragment_absorption_candidates"]:
            output.append(_candidate_payload(entry, candidate))
    return sorted(output, key=lambda item: (
        str(item["target_name"]).casefold(),
        str(item["target_project_key"]),
        str(item["source_name"]).casefold(),
        str(item["source_project_key"]),
    ))


def _active_deferred_targets(conn: sqlite3.Connection) -> set[str]:
    now = dt.datetime.now(dt.timezone.utc)
    output: set[str] = set()
    for row in conn.execute(
        """
        SELECT target_value, expires_at
        FROM project_decisions
        WHERE kind='defer_review_target' AND target_type=? AND cleared_at=''
        """,
        (TARGET_TYPE,),
    ):
        expires_at = str(row["expires_at"] or "")
        expires = project_record.parse_iso(expires_at) if expires_at else None
        if expires is None or expires > now:
            output.add(str(row["target_value"]))
    return output


def queue(conn: sqlite3.Connection, *, limit: int = 200) -> dict[str, Any]:
    recommendations = _recommendations(conn)
    deferred = _active_deferred_targets(conn)
    visible = [
        item for item in recommendations
        if str(item["review_target"]["target_value"]) not in deferred
    ]
    bounded = visible[:max(1, int(limit))]
    return {
        "queue": "merge_policy_review",
        "policy_version": merge_policy_shadow.POLICY_VERSION,
        "items": bounded,
        "count": len(visible),
        "target_count": len({str(item["target_project_key"]) for item in visible}),
        "deferred_count": len(recommendations) - len(visible),
        "total_recommendation_count": len(recommendations),
    }


def pair_recommendation(
    conn: sqlite3.Connection,
    *,
    source_project_key: str,
    target_project_key: str,
) -> dict[str, Any]:
    source_key = project_record.resolve_project_key_input(conn, source_project_key)
    target_key = project_record.resolve_project_key_input(conn, target_project_key)
    for item in _recommendations(conn):
        if item["source_project_key"] == source_key and item["target_project_key"] == target_key:
            return item
    raise RecommendationChangedError(
        "merge policy recommendation changed; reload the review queue before applying it"
    )


def defer_pair(
    conn: sqlite3.Connection,
    *,
    source_project_key: str,
    target_project_key: str,
    note: str = "",
    days: int = DEFAULT_DEFER_DAYS,
) -> dict[str, Any]:
    recommendation = pair_recommendation(
        conn,
        source_project_key=source_project_key,
        target_project_key=target_project_key,
    )
    project = core.resolve_single_project(conn, project_key=source_project_key)
    target = dict(recommendation["review_target"])
    result = decisions.save_verified_review_target(
        conn,
        project=project,
        target=target,
        action="defer",
        note=note,
        days=days,
    )
    return {**result, "source_project_key": source_project_key, "target_project_key": target_project_key}
