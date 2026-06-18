from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

from .. import registry as core


def _like(value: str) -> str:
    return f"%{str(value or '').strip().lower()}%"


def _choice_condition(column: str, value: str, parameters: list[Any]) -> str:
    parameters.append(value)
    return f"LOWER({column}) = LOWER(?)"


def search_project_rows(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    tracking_states: set[str] | None = None,
    service_statuses: set[str] | None = None,
    contribution_levels: set[str] | None = None,
    technical_interest_levels: set[str] | None = None,
    identifier_kind: str = "",
    identifier_value: str = "",
    identifier_classification: str = "",
    relation_predicate: str = "",
    relation_subject_type: str = "",
    relation_subject_value: str = "",
    relation_object_type: str = "",
    relation_object_value: str = "",
    relation_source: str = "",
    decision_kind: str = "",
    decision_target_type: str = "",
    decision_target_value: str = "",
    decision_status: str = "",
) -> list[dict[str, Any]]:
    """Return lightweight project rows without hydrating normalized children."""
    conditions: list[str] = []
    parameters: list[Any] = []

    def values_condition(column: str, values: set[str] | None) -> None:
        if not values:
            return
        cleaned = sorted(str(item) for item in values if str(item))
        if not cleaned:
            return
        conditions.append(f"{column} IN ({', '.join('?' for _ in cleaned)})")
        parameters.extend(cleaned)

    values_condition("p.tracking_state", tracking_states)
    values_condition("p.service_status", service_statuses)

    # These values live in profile/state JSON. Filtering the scalar project rows is
    # still substantially cheaper than hydrating every normalized child collection.
    if contribution_levels:
        values = sorted(contribution_levels)
        conditions.append(
            f"json_extract(p.state_json, '$.ops.atproto_contribution_level') "
            f"IN ({', '.join('?' for _ in values)})"
        )
        parameters.extend(values)
    if technical_interest_levels:
        values = sorted(technical_interest_levels)
        conditions.append(
            f"json_extract(p.state_json, '$.ops.technical_interest_level') "
            f"IN ({', '.join('?' for _ in values)})"
        )
        parameters.extend(values)

    text = str(query or "").strip()
    if text:
        pattern = _like(text)
        conditions.append(
            "(" 
            "LOWER(p.project_key) LIKE ? OR LOWER(p.name) LIKE ? OR LOWER(p.website_url) LIKE ? "
            "OR EXISTS (SELECT 1 FROM project_identifiers i WHERE i.project_key = p.project_key AND LOWER(i.value) LIKE ?) "
            "OR EXISTS (SELECT 1 FROM project_relations r WHERE r.project_key = p.project_key AND "
            "(LOWER(r.subject_value) LIKE ? OR LOWER(r.object_value) LIKE ? OR LOWER(r.locator) LIKE ?)) "
            "OR EXISTS (SELECT 1 FROM project_decisions d WHERE d.project_key = p.project_key AND "
            "(LOWER(d.target_value) LIKE ? OR LOWER(d.note) LIKE ?))"
            ")"
        )
        parameters.extend([pattern] * 9)

    identifier_parts: list[str] = []
    identifier_parameters: list[Any] = []
    if identifier_kind:
        identifier_parts.append(_choice_condition("i.kind", identifier_kind, identifier_parameters))
    if identifier_value:
        identifier_parts.append("LOWER(i.value) LIKE ?")
        identifier_parameters.append(_like(identifier_value))
    if identifier_classification:
        identifier_parts.append(_choice_condition("i.classification", identifier_classification, identifier_parameters))
    if identifier_parts:
        conditions.append(
            "EXISTS (SELECT 1 FROM project_identifiers i WHERE i.project_key = p.project_key AND "
            + " AND ".join(identifier_parts) + ")"
        )
        parameters.extend(identifier_parameters)

    relation_parts: list[str] = []
    relation_parameters: list[Any] = []
    for column, value, contains in (
        ("r.predicate", relation_predicate, False),
        ("r.subject_type", relation_subject_type, False),
        ("r.subject_value", relation_subject_value, True),
        ("r.object_type", relation_object_type, False),
        ("r.object_value", relation_object_value, True),
        ("r.source", relation_source, False),
    ):
        if not value:
            continue
        if contains:
            relation_parts.append(f"LOWER({column}) LIKE ?")
            relation_parameters.append(_like(value))
        else:
            relation_parts.append(_choice_condition(column, value, relation_parameters))
    if relation_parts:
        conditions.append(
            "EXISTS (SELECT 1 FROM project_relations r WHERE r.project_key = p.project_key AND "
            + " AND ".join(relation_parts) + ")"
        )
        parameters.extend(relation_parameters)

    decision_parts: list[str] = []
    decision_parameters: list[Any] = []
    for column, value, contains in (
        ("d.kind", decision_kind, False),
        ("d.target_type", decision_target_type, False),
        ("d.target_value", decision_target_value, True),
    ):
        if not value:
            continue
        if contains:
            decision_parts.append(f"LOWER({column}) LIKE ?")
            decision_parameters.append(_like(value))
        else:
            decision_parts.append(_choice_condition(column, value, decision_parameters))
    if decision_status:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        if decision_status == "active":
            decision_parts.append("d.cleared_at = '' AND (d.expires_at = '' OR d.expires_at > ?)")
            decision_parameters.append(now)
        elif decision_status == "expired":
            decision_parts.append("d.cleared_at = '' AND d.expires_at != '' AND d.expires_at <= ?")
            decision_parameters.append(now)
        elif decision_status == "cleared":
            decision_parts.append("d.cleared_at != ''")
    if decision_parts:
        conditions.append(
            "EXISTS (SELECT 1 FROM project_decisions d WHERE d.project_key = p.project_key AND "
            + " AND ".join(decision_parts) + ")"
        )
        parameters.extend(decision_parameters)

    sql = (
        "SELECT p.*, "
        "EXISTS (SELECT 1 FROM project_identifiers ci WHERE ci.project_key = p.project_key "
        "AND ci.classification = 'candidate') AS query_has_candidates, "
        "EXISTS (SELECT 1 FROM project_decisions pd WHERE pd.project_key = p.project_key "
        "AND pd.kind = 'defer_promotion' AND pd.cleared_at = '' "
        "AND (pd.expires_at = '' OR pd.expires_at > CURRENT_TIMESTAMP)) AS query_promotion_hold "
        "FROM projects p"
    )
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY p.last_seen_at DESC, p.project_key ASC"
    output: list[dict[str, Any]] = []
    for row in conn.execute(sql, tuple(parameters)).fetchall():
        project = core.project_from_row_payload(row)
        project["_query_has_candidates"] = bool(row["query_has_candidates"])
        project["_query_promotion_hold"] = bool(row["query_promotion_hold"])
        output.append(project)
    return output


def rank_project_rows(conn: sqlite3.Connection, projects: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    lowered = str(text or "").strip().lower()
    ranked: list[dict[str, Any]] = []
    for project in projects:
        project_key = str(project.get("project_key") or "")
        name = str(project.get("name") or "")
        website = str(project.get("website_url") or "")
        score = 0
        reasons: list[str] = []
        if project_key.lower() == lowered:
            score, reasons = 100, ["project_key_exact"]
        elif project_key.lower().startswith(lowered):
            score, reasons = 70, ["project_key_prefix"]
        if name.lower() == lowered:
            score += 80
            reasons.append("name_exact")
        elif lowered in name.lower():
            score += 45
            reasons.append("name_substring")
        if website and lowered in website.lower():
            score += 35
            reasons.append("website_match")
        identifier = conn.execute(
            "SELECT value FROM project_identifiers WHERE project_key = ? AND LOWER(value) LIKE ? "
            "ORDER BY CASE WHEN LOWER(value) = ? THEN 0 ELSE 1 END, ordinal LIMIT 1",
            (project_key, _like(lowered), lowered),
        ).fetchone()
        if identifier is not None:
            exact = str(identifier["value"]).lower() == lowered
            score += 90 if exact else 35
            reasons.append("identifier_exact" if exact else "identifier_substring")
        if score <= 0:
            # Relation/decision text may have selected the project even when its
            # project and identifier fields do not match.
            score = 25
            reasons.append("evidence_match")
        ranked.append({"project_key": project_key, "score": score, "reasons": reasons})
    return sorted(ranked, key=lambda item: (-int(item["score"]), str(item["project_key"])))
