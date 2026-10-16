from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


COMPONENT_ROLES = frozenset({
    "service",
    "project_site",
    "project_repository",
    "owned_nsid",
    "project_account",
    "project_handle",
    "brand_did",
})
TARGET_KIND = {
    "service": "service_url",
    "service_url": "service_url",
    "site": "site_url",
    "site_url": "site_url",
    "repo": "repo_url",
    "repo_url": "repo_url",
    "collection": "nsid",
    "nsid": "nsid",
    "account": "account",
    "did": "did",
    "handle": "handle",
    "domain": "domain",
}
OPERATOR_SOURCES = frozenset({"web_ui", "operator_add_seed"})


def _json(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _row_id(prefix: str, *values: str) -> str:
    payload = "\0".join((prefix, *values))
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def adoption_kind_for_source(source: str) -> str:
    normalized = str(source or "").strip()
    if normalized in OPERATOR_SOURCES or normalized.startswith("operator_review:"):
        return "operator"
    if normalized == "trust_policy" or normalized.startswith("trust_policy:"):
        return "trust_policy"
    return "bootstrap"


def insert_adoption(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    kind: str,
    value: str,
    role: str,
    source: str,
    created_at: str,
    adoption_kind: str | None = None,
    registration_origin: bool = False,
    source_ref: str = "",
    policy_version: str = "",
    input_fingerprint: str = "",
    review_required: bool = False,
    review_reason: str = "",
    origin_project_key: str = "",
) -> str:
    effective_kind = adoption_kind or adoption_kind_for_source(source)
    adoption_id = _row_id(
        "adoption",
        origin_project_key or project_key,
        kind,
        value,
        role,
        effective_kind,
        source,
        source_ref,
        created_at,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO project_identifier_adoptions(
            adoption_id, project_key, kind, value, role, adoption_kind, source,
            registration_origin, source_ref, policy_version, input_fingerprint,
            created_at, cleared_at, review_required, review_reason, origin_project_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
        """,
        (
            adoption_id,
            project_key,
            kind,
            value,
            role,
            effective_kind,
            source,
            int(registration_origin),
            source_ref,
            policy_version,
            input_fingerprint,
            created_at,
            int(review_required),
            review_reason,
            origin_project_key or project_key,
        ),
    )
    return adoption_id


def clear_claim_adoptions(
    conn: sqlite3.Connection,
    claim: tuple[str, str, str, str],
    *,
    cleared_at: str,
    reason: str,
) -> None:
    conn.execute(
        """
        UPDATE project_identifier_adoptions
        SET cleared_at=CASE WHEN cleared_at='' THEN ? ELSE cleared_at END,
            review_reason=CASE WHEN review_reason='' THEN ? ELSE review_reason END
        WHERE project_key=? AND kind=? AND value=? AND role=? AND cleared_at=''
        """,
        (cleared_at, reason, *claim),
    )


def _current_component_claims(conn: sqlite3.Connection, project_key: str) -> set[tuple[str, str, str, str]]:
    return {
        (project_key, str(row["kind"]), str(row["value"]), str(row["role"]))
        for row in conn.execute(
            """
            SELECT kind, value, role
            FROM project_identifiers
            WHERE project_key=? AND classification='confirmed'
            """,
            (project_key,),
        )
        if str(row["role"]) in COMPONENT_ROLES
    }


def reconcile_project_adoptions(conn: sqlite3.Connection, project_key: str, *, now: str) -> dict[str, int]:
    current = _current_component_claims(conn, project_key)
    cleared = 0
    for row in conn.execute(
        """
        SELECT project_key, kind, value, role
        FROM project_identifier_adoptions
        WHERE project_key=? AND cleared_at=''
        """,
        (project_key,),
    ):
        claim = (str(row["project_key"]), str(row["kind"]), str(row["value"]), str(row["role"]))
        if claim in current:
            continue
        clear_claim_adoptions(conn, claim, cleared_at=now, reason="claim is no longer current")
        cleared += 1

    reactivated = 0
    for claim in current:
        deleted = conn.execute(
            "DELETE FROM project_noncurrent_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
            claim,
        ).rowcount
        reactivated += max(0, int(deleted))

    inserted = 0
    decisions = conn.execute(
        """
        SELECT ordinal, target_type, target_value, target_role, created_at, metadata_json
        FROM project_decisions
        WHERE project_key=? AND kind='confirm_identifier' AND cleared_at=''
        ORDER BY ordinal
        """,
        (project_key,),
    ).fetchall()
    for row in decisions:
        kind = TARGET_KIND.get(str(row["target_type"]), "")
        value = str(row["target_value"])
        role = str(row["target_role"])
        if not role and kind:
            matching = sorted({claim[3] for claim in current if claim[1] == kind and claim[2] == value})
            if len(matching) == 1:
                role = matching[0]
                conn.execute(
                    "UPDATE project_decisions SET target_role=? WHERE project_key=? AND ordinal=?",
                    (role, project_key, int(row["ordinal"])),
                )
        claim = (project_key, kind, value, role)
        if not kind or role not in COMPONENT_ROLES or claim not in current:
            continue
        metadata = _json(str(row["metadata_json"]))
        source = str(metadata.get("source") or "") or "unspecified_confirm"
        origin = str(metadata.get("origin_project_key") or "")
        # Merge changes the owning project, not the adoption event identity.
        # Its original ID/origin must survive both this save and later saves.
        if conn.execute(
            """
            SELECT 1 FROM project_identifier_adoptions
            WHERE project_key=? AND kind=? AND value=? AND role=?
              AND adoption_kind=? AND source=? AND source_ref=? AND created_at=?
              AND (?='' OR origin_project_key=?)
            LIMIT 1
            """,
            (*claim, adoption_kind_for_source(source), source,
             str(metadata.get("source_ref") or ""), str(row["created_at"]), origin, origin),
        ).fetchone():
            continue
        insert_adoption(
            conn,
            project_key=project_key,
            kind=kind,
            value=value,
            role=role,
            source=source,
            created_at=str(row["created_at"]),
            registration_origin=bool(metadata.get("registration_origin")),
            source_ref=str(metadata.get("source_ref") or ""),
            policy_version=str(metadata.get("policy_version") or ""),
            input_fingerprint=str(metadata.get("input_fingerprint") or ""),
            origin_project_key=origin,
        )
        inserted += int(conn.execute(
            "SELECT changes()"
        ).fetchone()[0] > 0)
    return {"inserted": inserted, "cleared": cleared, "reactivated": reactivated}


def move_current_to_noncurrent(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    kind: str,
    value: str,
    role: str,
    standing: str,
    reason: str,
    recorded_at: str,
) -> None:
    if standing not in {"former", "non_production"}:
        raise ValueError("standing must be former or non_production")
    row = conn.execute(
        """
        SELECT metadata_json FROM project_identifiers
        WHERE project_key=? AND kind=? AND value=? AND role=?
        """,
        (project_key, kind, value, role),
    ).fetchone()
    if row is None:
        raise ValueError("current identifier claim not found")
    claim = (project_key, kind, value, role)
    conn.execute(
        """
        INSERT INTO project_noncurrent_identifiers(
            project_key, kind, value, role, standing, reason, recorded_at,
            origin_project_key, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_key, kind, value, role) DO UPDATE SET
            standing=excluded.standing,
            reason=excluded.reason,
            recorded_at=excluded.recorded_at,
            metadata_json=excluded.metadata_json
        """,
        (*claim, standing, reason, recorded_at, project_key, str(row["metadata_json"])),
    )
    conn.execute(
        "DELETE FROM project_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        claim,
    )
    clear_claim_adoptions(conn, claim, cleared_at=recorded_at, reason=f"moved to {standing}")


def record_confirmed_transition(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    from_kind: str,
    from_value: str,
    from_role: str,
    to_kind: str,
    to_value: str,
    to_role: str,
    relation_kind: str,
    evidence_ref: str,
    effective_at: str,
) -> str:
    """Record an operator-confirmed rename/replacement without inferring one from coexistence."""
    if not relation_kind.strip():
        raise ValueError("relation_kind is required")
    if not evidence_ref.strip():
        raise ValueError("evidence_ref is required for a confirmed transition")
    transition_id = _row_id(
        "transition",
        project_key,
        from_kind,
        from_value,
        from_role,
        to_kind,
        to_value,
        to_role,
        relation_kind,
        evidence_ref,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO project_identifier_transitions(
            transition_id, project_key, from_kind, from_value, from_role,
            to_kind, to_value, to_role, relation_kind, evidence_ref,
            effective_at, origin_project_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transition_id,
            project_key,
            from_kind,
            from_value,
            from_role,
            to_kind,
            to_value,
            to_role,
            relation_kind,
            evidence_ref,
            effective_at,
            project_key,
        ),
    )
    return transition_id


def noncurrent_identifier_matches(
    conn: sqlite3.Connection,
    kind: str,
    value: str,
) -> list[tuple[int, str]]:
    standing_priority = {"former": 2, "non_production": 3}
    matches: dict[str, int] = {}
    github = kind == "repo_url" and value.startswith("https://github.com/")
    value_expression = "value COLLATE NOCASE" if github else "value"
    for row in conn.execute(
        f"""
        SELECT project_key, standing
        FROM project_noncurrent_identifiers INDEXED BY idx_project_noncurrent_identifiers_lookup
        WHERE kind=? AND {value_expression}=?
        """,
        (kind, value),
    ):
        project_key = str(row["project_key"])
        priority = standing_priority[str(row["standing"])]
        matches[project_key] = min(priority, matches.get(project_key, priority))
    return sorted((priority, project_key) for project_key, priority in matches.items())


def known_noncurrent_collection_nsids(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT value FROM project_noncurrent_identifiers WHERE kind='nsid'"
        )
    }


def active_exclusions(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT value FROM registry_identifier_exclusions WHERE kind=? AND cleared_at=''",
            (kind,),
        )
    }


def set_exclusion(
    conn: sqlite3.Connection,
    *,
    kind: str,
    value: str,
    reason: str,
    source: str,
    created_at: str,
    clear: bool = False,
) -> None:
    if clear:
        conn.execute(
            "UPDATE registry_identifier_exclusions SET cleared_at=? WHERE kind=? AND value=? AND cleared_at=''",
            (created_at, kind, value),
        )
        return
    conn.execute(
        """
        INSERT INTO registry_identifier_exclusions(kind, value, reason, source, created_at, cleared_at)
        VALUES (?, ?, ?, ?, ?, '')
        ON CONFLICT(kind, value) DO UPDATE SET
            reason=excluded.reason,
            source=excluded.source,
            created_at=excluded.created_at,
            cleared_at=''
        """,
        (kind, value, reason, source, created_at),
    )


def merge_project_storage(conn: sqlite3.Connection, source_key: str, target_key: str) -> None:
    for table in ("project_noncurrent_identifiers", "project_identifier_transitions"):
        conn.execute(f"UPDATE OR IGNORE {table} SET project_key=? WHERE project_key=?", (target_key, source_key))
        conn.execute(f"DELETE FROM {table} WHERE project_key=?", (source_key,))
    conn.execute(
        """
        UPDATE project_identifier_adoptions
        SET project_key=?,
            origin_project_key=CASE WHEN origin_project_key='' THEN ? ELSE origin_project_key END
        WHERE project_key=?
        """,
        (target_key, source_key, source_key),
    )


def active_adoption_claims(project: dict[str, Any]) -> set[tuple[str, str, str]]:
    from . import runtime_view

    return {
        (str(row.get("kind") or ""), str(row.get("value") or ""), str(row.get("role") or ""))
        for row in project.get(runtime_view.RUNTIME_ADOPTION_ROWS, [])
        if not str(row.get("cleared_at") or "") and not bool(row.get("review_required"))
    }


def missing_active_adoption_count(conn: sqlite3.Connection) -> int:
    placeholders = ",".join("?" for _ in COMPONENT_ROLES)
    return int(conn.execute(
        f"""
        SELECT COUNT(*)
        FROM project_identifiers i
        WHERE i.classification='confirmed' AND i.role IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1 FROM project_identifier_adoptions a
              WHERE a.project_key=i.project_key AND a.kind=i.kind AND a.value=i.value AND a.role=i.role
                AND a.cleared_at='' AND a.review_required=0
          )
        """,
        tuple(sorted(COMPONENT_ROLES)),
    ).fetchone()[0])
