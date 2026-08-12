from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable


POLICY_VERSION = "identifier-storage-shadow-v1"
NONCURRENT_STANDINGS = {"former", "non_production"}
ADOPTION_KINDS = {"operator", "bootstrap", "trust_policy"}
COMPONENT_ROLES = {
    "service",
    "source_repository",
    "owned_nsid",
    "project_account",
    "project_handle",
    "brand_did",
}
OBSERVATION_ONLY_ROLES = {"source_repository_record", "authority_domain"}
TARGET_KIND = {
    "service_url": "service_url",
    "repo_url": "repo_url",
    "nsid": "nsid",
    "collection": "nsid",
    "did": "did",
    "handle": "handle",
    "account": "account",
    "domain": "domain",
}
OPERATOR_SOURCES = {"web_ui", "operator_add_seed"}
BOOTSTRAP_SOURCES = {
    "legacy_manual_seed_migration",
    "canonical_redirect",
    "semble_collection_sync",
    "collection_sync",
    "source_sync",
}


def _json(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _decision_state(*, expires_at: str, cleared_at: str) -> str:
    if cleared_at:
        return "cleared"
    if expires_at:
        try:
            expires = dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=dt.timezone.utc)
            if expires <= dt.datetime.now(dt.timezone.utc):
                return "expired"
        except ValueError:
            return "active_invalid_expiry"
    return "active"


def create_prototype_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE projects (
            project_key TEXT PRIMARY KEY
        );
        CREATE TABLE current_identifiers (
            project_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            role TEXT NOT NULL,
            classification TEXT NOT NULL,
            PRIMARY KEY(project_key, kind, value, role),
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE TABLE noncurrent_identifiers (
            project_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            role TEXT NOT NULL,
            standing TEXT NOT NULL CHECK(standing IN ('former', 'non_production')),
            reason TEXT NOT NULL DEFAULT '',
            recorded_at TEXT NOT NULL DEFAULT '',
            origin_project_key TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(project_key, kind, value, role),
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE INDEX idx_noncurrent_identifier_lookup
            ON noncurrent_identifiers(kind, value, standing);
        CREATE TABLE identifier_adoptions (
            adoption_id TEXT PRIMARY KEY,
            project_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            role TEXT NOT NULL,
            adoption_kind TEXT NOT NULL CHECK(adoption_kind IN ('operator', 'bootstrap', 'trust_policy')),
            source TEXT NOT NULL,
            registration_origin INTEGER NOT NULL DEFAULT 0,
            source_ref TEXT NOT NULL DEFAULT '',
            policy_version TEXT NOT NULL DEFAULT '',
            input_fingerprint TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            cleared_at TEXT NOT NULL DEFAULT '',
            review_required INTEGER NOT NULL DEFAULT 0,
            review_reason TEXT NOT NULL DEFAULT '',
            origin_project_key TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE INDEX idx_identifier_adoptions_claim
            ON identifier_adoptions(project_key, kind, value, role, cleared_at);
        CREATE TABLE identifier_transitions (
            transition_id TEXT PRIMARY KEY,
            project_key TEXT NOT NULL,
            from_kind TEXT NOT NULL,
            from_value TEXT NOT NULL,
            from_role TEXT NOT NULL,
            to_kind TEXT NOT NULL,
            to_value TEXT NOT NULL,
            to_role TEXT NOT NULL,
            relation_kind TEXT NOT NULL,
            evidence_ref TEXT NOT NULL DEFAULT '',
            effective_at TEXT NOT NULL DEFAULT '',
            origin_project_key TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE TABLE identifier_exclusions (
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            cleared_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(kind, value)
        );
        """
    )


def _prototype_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_prototype_schema(conn)
    return conn


def add_project(conn: sqlite3.Connection, project_key: str) -> None:
    conn.execute("INSERT INTO projects(project_key) VALUES (?)", (project_key,))


def add_current_identifier(
    conn: sqlite3.Connection,
    project_key: str,
    kind: str,
    value: str,
    role: str,
    *,
    classification: str = "confirmed",
) -> None:
    overlap = conn.execute(
        "SELECT 1 FROM noncurrent_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        (project_key, kind, value, role),
    ).fetchone()
    if overlap is not None:
        raise ValueError("identifier is already noncurrent for this project and role")
    conn.execute(
        "INSERT INTO current_identifiers(project_key, kind, value, role, classification) VALUES (?, ?, ?, ?, ?)",
        (project_key, kind, value, role, classification),
    )


def add_noncurrent_identifier(
    conn: sqlite3.Connection,
    project_key: str,
    kind: str,
    value: str,
    role: str,
    standing: str,
    *,
    reason: str = "",
    recorded_at: str = "",
    origin_project_key: str = "",
) -> None:
    if standing not in NONCURRENT_STANDINGS:
        raise ValueError(f"unsupported noncurrent standing: {standing}")
    overlap = conn.execute(
        "SELECT 1 FROM current_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        (project_key, kind, value, role),
    ).fetchone()
    if overlap is not None:
        raise ValueError("identifier is already current for this project and role")
    conn.execute(
        """
        INSERT INTO noncurrent_identifiers(
            project_key, kind, value, role, standing, reason, recorded_at, origin_project_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (project_key, kind, value, role, standing, reason, recorded_at, origin_project_key or project_key),
    )


def add_adoption(
    conn: sqlite3.Connection,
    adoption_id: str,
    project_key: str,
    kind: str,
    value: str,
    role: str,
    adoption_kind: str,
    source: str,
    *,
    registration_origin: bool = False,
    created_at: str = "",
    cleared_at: str = "",
    review_required: bool = False,
    review_reason: str = "",
    origin_project_key: str = "",
) -> None:
    if adoption_kind not in ADOPTION_KINDS:
        raise ValueError(f"unsupported adoption kind: {adoption_kind}")
    conn.execute(
        """
        INSERT INTO identifier_adoptions(
            adoption_id, project_key, kind, value, role, adoption_kind, source,
            registration_origin, created_at, cleared_at, review_required,
            review_reason, origin_project_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            adoption_id,
            project_key,
            kind,
            value,
            role,
            adoption_kind,
            source,
            int(registration_origin),
            created_at,
            cleared_at,
            int(review_required),
            review_reason,
            origin_project_key or project_key,
        ),
    )


def move_identifier_to_noncurrent(
    conn: sqlite3.Connection,
    project_key: str,
    kind: str,
    value: str,
    role: str,
    standing: str,
    *,
    reason: str,
    recorded_at: str,
) -> None:
    if standing not in NONCURRENT_STANDINGS:
        raise ValueError(f"unsupported noncurrent standing: {standing}")
    row = conn.execute(
        "SELECT 1 FROM current_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        (project_key, kind, value, role),
    ).fetchone()
    if row is None:
        raise ValueError("current identifier not found")
    conn.execute(
        "DELETE FROM current_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        (project_key, kind, value, role),
    )
    add_noncurrent_identifier(
        conn,
        project_key,
        kind,
        value,
        role,
        standing,
        reason=reason,
        recorded_at=recorded_at,
        origin_project_key=project_key,
    )


def reactivate_identifier(
    conn: sqlite3.Connection,
    project_key: str,
    kind: str,
    value: str,
    role: str,
    *,
    classification: str = "confirmed",
) -> None:
    row = conn.execute(
        "SELECT 1 FROM noncurrent_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        (project_key, kind, value, role),
    ).fetchone()
    if row is None:
        raise ValueError("noncurrent identifier not found")
    conn.execute(
        "DELETE FROM noncurrent_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        (project_key, kind, value, role),
    )
    add_current_identifier(conn, project_key, kind, value, role, classification=classification)


def exact_identifier_lookup(conn: sqlite3.Connection, kind: str, value: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT project_key, kind, value, role, 'current' AS standing, 0 AS priority
        FROM current_identifiers
        WHERE kind=? AND value=?
        UNION ALL
        SELECT project_key, kind, value, role, standing,
               CASE standing WHEN 'former' THEN 1 ELSE 2 END AS priority
        FROM noncurrent_identifiers
        WHERE kind=? AND value=?
        ORDER BY priority, project_key, role
        """,
        (kind, value, kind, value),
    ).fetchall()
    return [dict(row) for row in rows]


def trusted_anchor_claims(conn: sqlite3.Connection, project_key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT kind, value, role, classification
        FROM current_identifiers c
        WHERE project_key=? AND classification='confirmed'
          AND EXISTS (
              SELECT 1 FROM identifier_adoptions a
              WHERE a.project_key=c.project_key
                AND a.kind=c.kind
                AND a.value=c.value
                AND a.role=c.role
                AND a.cleared_at=''
                AND a.review_required=0
          )
        ORDER BY kind, value, role
        """,
        (project_key,),
    ).fetchall()
    return [dict(row) for row in rows if str(row["role"]) in COMPONENT_ROLES]


def known_collection_nsids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """
        SELECT value FROM current_identifiers WHERE kind='nsid'
        UNION
        SELECT value FROM noncurrent_identifiers WHERE kind='nsid'
        UNION
        SELECT value FROM identifier_exclusions WHERE kind='nsid' AND cleared_at=''
        """
    ).fetchall()
    return {str(row[0]) for row in rows}


def merge_project(conn: sqlite3.Connection, source_key: str, target_key: str) -> dict[str, int]:
    if source_key == target_key:
        raise ValueError("merge source and target must differ")
    for project_key in (source_key, target_key):
        if conn.execute("SELECT 1 FROM projects WHERE project_key=?", (project_key,)).fetchone() is None:
            raise ValueError(f"project not found: {project_key}")
    counts: collections.Counter[str] = collections.Counter()
    for row in conn.execute("SELECT * FROM current_identifiers WHERE project_key=?", (source_key,)).fetchall():
        key = (target_key, row["kind"], row["value"], row["role"])
        if conn.execute(
            "SELECT 1 FROM current_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
            key,
        ).fetchone():
            counts["current_deduplicated"] += 1
        else:
            conn.execute(
                "INSERT INTO current_identifiers(project_key, kind, value, role, classification) VALUES (?, ?, ?, ?, ?)",
                (*key, row["classification"]),
            )
            counts["current_moved"] += 1
    for row in conn.execute("SELECT * FROM noncurrent_identifiers WHERE project_key=?", (source_key,)).fetchall():
        key = (target_key, row["kind"], row["value"], row["role"])
        if conn.execute(
            "SELECT 1 FROM current_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
            key,
        ).fetchone():
            counts["noncurrent_shadowed_by_current"] += 1
            continue
        if conn.execute(
            "SELECT 1 FROM noncurrent_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
            key,
        ).fetchone():
            counts["noncurrent_deduplicated"] += 1
            continue
        conn.execute(
            """
            INSERT INTO noncurrent_identifiers(
                project_key, kind, value, role, standing, reason, recorded_at, origin_project_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *key,
                row["standing"],
                row["reason"],
                row["recorded_at"],
                row["origin_project_key"] or source_key,
            ),
        )
        counts["noncurrent_moved"] += 1
    counts["adoptions_moved"] += conn.execute(
        "UPDATE identifier_adoptions SET project_key=? WHERE project_key=?",
        (target_key, source_key),
    ).rowcount
    counts["transitions_moved"] += conn.execute(
        "UPDATE identifier_transitions SET project_key=? WHERE project_key=?",
        (target_key, source_key),
    ).rowcount
    conn.execute("DELETE FROM projects WHERE project_key=?", (source_key,))
    return {key: int(value) for key, value in sorted(counts.items())}


def validate_prototype(conn: sqlite3.Connection) -> dict[str, Any]:
    overlaps = conn.execute(
        """
        SELECT c.project_key, c.kind, c.value, c.role
        FROM current_identifiers c
        JOIN noncurrent_identifiers n
          ON n.project_key=c.project_key AND n.kind=c.kind AND n.value=c.value AND n.role=c.role
        """
    ).fetchall()
    integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    return {"integrity": integrity, "current_noncurrent_overlap_count": len(overlaps)}


def _run_case(case_id: str, description: str, body: Callable[[sqlite3.Connection], dict[str, Any]]) -> dict[str, Any]:
    conn = _prototype_connection()
    try:
        actual = body(conn)
        validation = validate_prototype(conn)
        passed = bool(actual.pop("passed")) and validation == {
            "integrity": "ok",
            "current_noncurrent_overlap_count": 0,
        }
        return {
            "case_id": case_id,
            "description": description,
            "passed": passed,
            "actual": actual,
            "validation": validation,
        }
    finally:
        conn.close()


def evaluate_prototype_cases() -> dict[str, Any]:
    def former_case(conn: sqlite3.Connection) -> dict[str, Any]:
        add_project(conn, "project")
        add_current_identifier(conn, "project", "service_url", "https://old.example", "service")
        add_adoption(conn, "adopt-old", "project", "service_url", "https://old.example", "service", "operator", "web_ui")
        move_identifier_to_noncurrent(
            conn,
            "project",
            "service_url",
            "https://old.example",
            "service",
            "former",
            reason="moved",
            recorded_at="2026-01-02",
        )
        lookup = exact_identifier_lookup(conn, "service_url", "https://old.example")
        adoption_count = int(conn.execute("SELECT COUNT(*) FROM identifier_adoptions").fetchone()[0])
        return {
            "passed": lookup[0]["standing"] == "former" and not trusted_anchor_claims(conn, "project") and adoption_count == 1,
            "lookup_standing": lookup[0]["standing"],
            "trusted_anchor_count": len(trusted_anchor_claims(conn, "project")),
            "adoption_count": adoption_count,
        }

    def nonproduction_case(conn: sqlite3.Connection) -> dict[str, Any]:
        nsid = "com.example.temp.post"
        add_project(conn, "project")
        add_current_identifier(conn, "project", "nsid", nsid, "owned_nsid")
        move_identifier_to_noncurrent(
            conn,
            "project",
            "nsid",
            nsid,
            "owned_nsid",
            "non_production",
            reason="test schema",
            recorded_at="2026-01-02",
        )
        return {
            "passed": nsid in known_collection_nsids(conn) and not trusted_anchor_claims(conn, "project"),
            "known_to_sync": nsid in known_collection_nsids(conn),
            "trusted_anchor_count": len(trusted_anchor_claims(conn, "project")),
        }

    def reactivation_case(conn: sqlite3.Connection) -> dict[str, Any]:
        add_project(conn, "project")
        add_current_identifier(conn, "project", "service_url", "https://return.example", "service")
        add_adoption(conn, "bootstrap", "project", "service_url", "https://return.example", "service", "bootstrap", "collection_sync")
        move_identifier_to_noncurrent(
            conn,
            "project",
            "service_url",
            "https://return.example",
            "service",
            "former",
            reason="ended",
            recorded_at="2026-01-02",
        )
        reactivate_identifier(conn, "project", "service_url", "https://return.example", "service")
        add_adoption(conn, "operator", "project", "service_url", "https://return.example", "service", "operator", "web_ui")
        standings = [row["standing"] for row in exact_identifier_lookup(conn, "service_url", "https://return.example")]
        adoption_count = int(conn.execute("SELECT COUNT(*) FROM identifier_adoptions").fetchone()[0])
        return {
            "passed": standings == ["current"] and adoption_count == 2,
            "lookup_standings": standings,
            "adoption_count": adoption_count,
        }

    def exclusion_case(conn: sqlite3.Connection) -> dict[str, Any]:
        nsid = "com.example.noise"
        conn.execute(
            "INSERT INTO identifier_exclusions(kind, value, reason, source) VALUES ('nsid', ?, 'noise', 'operator')",
            (nsid,),
        )
        return {
            "passed": nsid in known_collection_nsids(conn) and not exact_identifier_lookup(conn, "nsid", nsid),
            "known_to_sync": nsid in known_collection_nsids(conn),
            "project_lookup_count": len(exact_identifier_lookup(conn, "nsid", nsid)),
        }

    def transition_case(conn: sqlite3.Connection) -> dict[str, Any]:
        add_project(conn, "project")
        add_current_identifier(conn, "project", "service_url", "https://old.example", "service")
        add_adoption(conn, "old-adoption", "project", "service_url", "https://old.example", "service", "operator", "web_ui")
        move_identifier_to_noncurrent(
            conn,
            "project",
            "service_url",
            "https://old.example",
            "service",
            "former",
            reason="moved",
            recorded_at="2026-01-02",
        )
        add_current_identifier(conn, "project", "service_url", "https://new.example", "service")
        add_adoption(conn, "new-adoption", "project", "service_url", "https://new.example", "service", "operator", "web_ui")
        conn.execute(
            """
            INSERT INTO identifier_transitions(
                transition_id, project_key, from_kind, from_value, from_role,
                to_kind, to_value, to_role, relation_kind, evidence_ref,
                effective_at, origin_project_key
            ) VALUES (
                'move', 'project', 'service_url', 'https://old.example', 'service',
                'service_url', 'https://new.example', 'service', 'moved_to',
                'operator:move', '2026-01-02', 'project'
            )
            """
        )
        transition = conn.execute("SELECT * FROM identifier_transitions").fetchone()
        old_standing = exact_identifier_lookup(conn, "service_url", "https://old.example")[0]["standing"]
        new_standing = exact_identifier_lookup(conn, "service_url", "https://new.example")[0]["standing"]
        adoption_count = int(conn.execute("SELECT COUNT(*) FROM identifier_adoptions").fetchone()[0])
        return {
            "passed": (
                transition["from_value"] == "https://old.example"
                and transition["to_value"] == "https://new.example"
                and old_standing == "former"
                and new_standing == "current"
                and adoption_count == 2
            ),
            "transition_kind": transition["relation_kind"],
            "old_standing": old_standing,
            "new_standing": new_standing,
            "adoption_count": adoption_count,
        }

    def merge_case(conn: sqlite3.Connection) -> dict[str, Any]:
        add_project(conn, "source")
        add_project(conn, "target")
        add_current_identifier(conn, "source", "repo_url", "https://github.com/example/source", "source_repository")
        add_current_identifier(conn, "source", "service_url", "https://old.example", "service")
        move_identifier_to_noncurrent(
            conn,
            "source",
            "service_url",
            "https://old.example",
            "service",
            "former",
            reason="moved",
            recorded_at="2026-01-02",
        )
        add_adoption(conn, "source-adoption", "source", "repo_url", "https://github.com/example/source", "source_repository", "operator", "web_ui")
        add_current_identifier(conn, "target", "service_url", "https://new.example", "service")
        conn.execute(
            """
            INSERT INTO identifier_transitions(
                transition_id, project_key, from_kind, from_value, from_role,
                to_kind, to_value, to_role, relation_kind, origin_project_key
            ) VALUES (
                'source-transition', 'source', 'service_url', 'https://old.example', 'service',
                'service_url', 'https://new.example', 'service', 'moved_to', 'source'
            )
            """
        )
        merge_counts = merge_project(conn, "source", "target")
        repo_lookup = exact_identifier_lookup(conn, "repo_url", "https://github.com/example/source")
        old_lookup = exact_identifier_lookup(conn, "service_url", "https://old.example")
        adoption = conn.execute("SELECT project_key, origin_project_key FROM identifier_adoptions").fetchone()
        transition = conn.execute("SELECT project_key, origin_project_key FROM identifier_transitions").fetchone()
        return {
            "passed": (
                repo_lookup[0]["project_key"] == "target"
                and old_lookup[0]["project_key"] == "target"
                and adoption["project_key"] == "target"
                and adoption["origin_project_key"] == "source"
                and transition["project_key"] == "target"
                and transition["origin_project_key"] == "source"
            ),
            "merge_counts": merge_counts,
            "repo_project": repo_lookup[0]["project_key"],
            "former_project": old_lookup[0]["project_key"],
            "adoption_origin": adoption["origin_project_key"],
            "transition_origin": transition["origin_project_key"],
        }

    def overlap_case(conn: sqlite3.Connection) -> dict[str, Any]:
        add_project(conn, "project")
        add_current_identifier(conn, "project", "service_url", "https://same.example", "service")
        rejected = False
        try:
            add_noncurrent_identifier(
                conn,
                "project",
                "service_url",
                "https://same.example",
                "service",
                "former",
            )
        except ValueError:
            rejected = True
        return {"passed": rejected, "overlap_rejected": rejected}

    def candidate_anchor_case(conn: sqlite3.Connection) -> dict[str, Any]:
        add_project(conn, "project")
        add_current_identifier(
            conn,
            "project",
            "service_url",
            "https://candidate.example",
            "service",
            classification="candidate",
        )
        add_current_identifier(
            conn,
            "project",
            "service_url",
            "https://confirmed.example",
            "service",
            classification="confirmed",
        )
        lookup = exact_identifier_lookup(conn, "service_url", "https://candidate.example")
        anchors_before_adoption = trusted_anchor_claims(conn, "project")
        add_adoption(
            conn,
            "confirmed-adoption",
            "project",
            "service_url",
            "https://confirmed.example",
            "service",
            "operator",
            "web_ui",
        )
        anchors_after_adoption = trusted_anchor_claims(conn, "project")
        return {
            "passed": (
                lookup[0]["standing"] == "current"
                and not anchors_before_adoption
                and [anchor["value"] for anchor in anchors_after_adoption] == ["https://confirmed.example"]
            ),
            "lookup_standing": lookup[0]["standing"],
            "trusted_anchor_count_before_adoption": len(anchors_before_adoption),
            "trusted_anchor_values_after_adoption": [anchor["value"] for anchor in anchors_after_adoption],
        }

    cases = [
        _run_case("former_exact_search", "Former identifier resolves to the current project but is not a Trust input.", former_case),
        _run_case("nonproduction_sync_guard", "Non-production NSID suppresses collection rediscovery without becoming a Trust input.", nonproduction_case),
        _run_case("reactivation_preserves_adoptions", "Reactivation restores only the current claim and retains adoption history.", reactivation_case),
        _run_case("noise_exclusion", "Noise suppresses collection rediscovery without creating project attribution.", exclusion_case),
        _run_case("transition_separate_from_adoption", "A confirmed replacement links old and new identifiers without using adoption history as the transition.", transition_case),
        _run_case("merge_preserves_origin", "Merge moves current and noncurrent claims while retaining the source project of adoption.", merge_case),
        _run_case("current_noncurrent_exclusive", "A claim cannot be current and noncurrent for the same project and role.", overlap_case),
        _run_case("trusted_anchor_requires_adoption", "A current candidate remains searchable, while a confirmed claim becomes a trusted anchor only with an active adoption reason.", candidate_anchor_case),
    ]
    return {
        "case_count": len(cases),
        "passed_count": sum(1 for case in cases if case["passed"]),
        "failed_count": sum(1 for case in cases if not case["passed"]),
        "cases": cases,
    }


def _decision_role_analysis(conn: sqlite3.Connection, *, example_limit: int) -> dict[str, Any]:
    status_counts: collections.Counter[str] = collections.Counter()
    kind_status_counts: collections.Counter[str] = collections.Counter()
    state_status_counts: collections.Counter[str] = collections.Counter()
    kind_state_status_counts: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    mappings: list[dict[str, Any]] = []
    rows = conn.execute(
        """
        SELECT project_key, ordinal, kind, target_type, target_value, created_at,
               expires_at, cleared_at, metadata_json
        FROM project_decisions
        WHERE kind IN ('confirm_identifier', 'block_identifier', 'defer_identifier')
        ORDER BY project_key, ordinal
        """
    ).fetchall()
    for row in rows:
        target_kind = TARGET_KIND.get(str(row["target_type"]))
        raw_roles: list[str] = []
        if target_kind:
            raw_roles = [
                str(item["role"])
                for item in conn.execute(
                    """
                    SELECT DISTINCT role FROM project_identifiers
                    WHERE project_key=? AND kind=? AND value=?
                    ORDER BY role
                    """,
                    (str(row["project_key"]), target_kind, str(row["target_value"])),
                ).fetchall()
            ]
        roles = [role for role in raw_roles if role not in OBSERVATION_ONLY_ROLES]
        if not target_kind:
            status = "unsupported_target_type"
        elif len(roles) == 1:
            status = "resolved"
        elif len(roles) > 1:
            status = "ambiguous_role"
        elif raw_roles:
            status = "observation_only"
        else:
            status = "no_current_claim"
        metadata = _json(str(row["metadata_json"]), {})
        state = _decision_state(expires_at=str(row["expires_at"]), cleared_at=str(row["cleared_at"]))
        item = {
            "project_key": str(row["project_key"]),
            "ordinal": int(row["ordinal"]),
            "kind": str(row["kind"]),
            "target_type": str(row["target_type"]),
            "target_kind": target_kind or "",
            "target_value": str(row["target_value"]),
            "target_role": roles[0] if len(roles) == 1 else "",
            "matching_roles": roles,
            "status": status,
            "state": state,
            "created_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"]),
            "cleared_at": str(row["cleared_at"]),
            "source": str(metadata.get("source") or ""),
        }
        mappings.append(item)
        status_counts[status] += 1
        kind_status_counts[f"{item['kind']}:{status}"] += 1
        state_status_counts[f"{state}:{status}"] += 1
        kind_state_status_counts[f"{item['kind']}:{state}:{status}"] += 1
        if len(examples[status]) < example_limit:
            examples[status].append(item)
    return {
        "decision_count": len(mappings),
        "status_counts": dict(sorted(status_counts.items())),
        "kind_status_counts": dict(sorted(kind_status_counts.items())),
        "state_status_counts": dict(sorted(state_status_counts.items())),
        "kind_state_status_counts": dict(sorted(kind_state_status_counts.items())),
        "examples": dict(examples),
        "mappings": mappings,
    }


def _adoption_projection(
    conn: sqlite3.Connection,
    decision_analysis: dict[str, Any],
    *,
    example_limit: int,
    include_rows: bool = False,
) -> dict[str, Any]:
    project_names = {
        str(row["project_key"]): str(row["name"])
        for row in conn.execute("SELECT project_key, name FROM projects")
    }
    project_context = {}
    for row in conn.execute("SELECT project_key, website_url, state_json FROM projects"):
        state = _json(str(row["state_json"]), {})
        discovery_sources = state.get("evidence", {}).get("discovery_sources", [])
        project_context[str(row["project_key"])] = {
            "website_url": str(row["website_url"]),
            "discovery_sources": {str(source) for source in discovery_sources if source},
        }

    relation_evidence: dict[tuple[str, str, str], list[dict[str, str]]] = collections.defaultdict(list)
    for row in conn.execute(
        """
        SELECT project_key, subject_type, subject_value, predicate,
               object_type, object_value, source, locator, method
        FROM project_relations
        WHERE classification='confirmed'
        ORDER BY project_key, ordinal
        """
    ):
        evidence = {
            "predicate": str(row["predicate"]),
            "source": str(row["source"]),
            "locator": str(row["locator"]),
            "method": str(row["method"]),
        }
        relation_evidence[(str(row["project_key"]), str(row["subject_type"]), str(row["subject_value"]))].append(evidence)
        relation_evidence[(str(row["project_key"]), str(row["object_type"]), str(row["object_value"]))].append(evidence)

    def claim_payload(claim_key: tuple[str, str, str, str]) -> dict[str, Any]:
        return {
            "project_key": claim_key[0],
            "project_name": project_names.get(claim_key[0], ""),
            "kind": claim_key[1],
            "value": claim_key[2],
            "role": claim_key[3],
        }

    claim_adoptions: dict[tuple[str, str, str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    active_guards: dict[tuple[str, str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for item in decision_analysis["mappings"]:
        if item["state"] not in {"active", "active_invalid_expiry"}:
            continue
        if item["kind"] in {"block_identifier", "defer_identifier"} and item["target_kind"]:
            active_guards[(item["project_key"], item["target_kind"], item["target_value"])].append(item)
    source_counts: collections.Counter[str] = collections.Counter()
    kind_counts: collections.Counter[str] = collections.Counter()
    for item in decision_analysis["mappings"]:
        if (
            item["kind"] != "confirm_identifier"
            or item["status"] != "resolved"
            or item["state"] not in {"active", "active_invalid_expiry"}
        ):
            continue
        claim_key = (item["project_key"], item["target_kind"], item["target_value"], item["target_role"])
        confirmed_claim = conn.execute(
            """
            SELECT 1 FROM project_identifiers
            WHERE project_key=? AND kind=? AND value=? AND role=? AND classification='confirmed'
            LIMIT 1
            """,
            claim_key,
        ).fetchone()
        if confirmed_claim is None:
            continue
        later_guards = [
            guard
            for guard in active_guards.get(claim_key[:3], [])
            if str(guard["created_at"]) >= str(item["created_at"])
        ]
        if later_guards:
            continue
        source = item["source"] or "unknown"
        if source in OPERATOR_SOURCES:
            adoption_kind = "operator"
        elif source in BOOTSTRAP_SOURCES:
            adoption_kind = "bootstrap"
        else:
            adoption_kind = "bootstrap"
            source = f"migration_unmapped:{source}"
        adoption = {
            "project_key": claim_key[0],
            "kind": claim_key[1],
            "value": claim_key[2],
            "role": claim_key[3],
            "adoption_kind": adoption_kind,
            "source": source,
            "registration_origin": source in OPERATOR_SOURCES or source in BOOTSTRAP_SOURCES,
            "created_at": item["created_at"],
            "review_required": source.startswith("migration_unmapped:"),
        }
        claim_adoptions[claim_key].append(adoption)
        source_counts[source] += 1
        kind_counts[adoption_kind] += 1

    component_claims: list[tuple[str, str, str, str]] = []
    excluded_roles: collections.Counter[str] = collections.Counter()
    for row in conn.execute(
        """
        SELECT project_key, kind, value, role
        FROM project_identifiers
        WHERE classification='confirmed'
        ORDER BY project_key, ordinal
        """
    ):
        role = str(row["role"])
        if role not in COMPONENT_ROLES:
            excluded_roles[role] += 1
            continue
        component_claims.append((str(row["project_key"]), str(row["kind"]), str(row["value"]), role))

    explicit_covered_count = sum(1 for claim in component_claims if claim_adoptions.get(claim))
    explicit_covered_projects = {claim[0] for claim in component_claims if claim_adoptions.get(claim)}
    inferred: list[dict[str, Any]] = []
    inferred_count = 0
    inferred_projects: set[str] = set()
    relation_recovered_count = 0
    relation_recovered_projects: set[str] = set()
    legacy_context_recovered_count = 0
    legacy_context_recovered_projects: set[str] = set()
    recovery_basis_counts: collections.Counter[str] = collections.Counter()
    uncovered: list[dict[str, Any]] = []
    uncovered_projects: set[str] = set()
    guarded: list[dict[str, Any]] = []
    guarded_projects: set[str] = set()
    for claim_key in component_claims:
        if claim_adoptions.get(claim_key):
            continue
        claim = claim_payload(claim_key)
        if active_guards.get(claim_key[:3]):
            guarded_projects.add(claim_key[0])
            if len(guarded) < example_limit:
                guarded.append(claim)
            continue
        if claim_key[3] == "owned_nsid":
            adoption = {
                **claim,
                "adoption_kind": "bootstrap",
                "source": "migration_inferred_collection_inventory",
                "registration_origin": True,
                "created_at": "",
                "review_required": False,
            }
            claim_adoptions[claim_key].append(adoption)
            source_counts[adoption["source"]] += 1
            kind_counts["bootstrap"] += 1
            inferred_count += 1
            inferred_projects.add(claim_key[0])
            if len(inferred) < example_limit:
                inferred.append(adoption)
        elif claim_key[3] in {"service", "source_repository"}:
            graph_kind = {"service_url": "service", "repo_url": "repo"}.get(claim_key[1], claim_key[1])
            evidence = relation_evidence.get((claim_key[0], graph_kind, claim_key[2]), [])
            context = project_context.get(claim_key[0], {})
            legacy_manual_seed = "manual_seed" in context.get("discovery_sources", set())
            matches_website = claim_key[3] == "service" and claim_key[2] == context.get("website_url", "")
            if evidence:
                first = evidence[0]
                adoption = {
                    **claim,
                    "adoption_kind": "bootstrap",
                    "source": "migration_confirmed_relation",
                    "source_ref": ":".join(
                        part
                        for part in (
                            first["predicate"],
                            first["method"],
                            first["source"],
                            first["locator"],
                        )
                        if part
                    ),
                    "registration_origin": False,
                    "created_at": "",
                    "review_required": False,
                }
                claim_adoptions[claim_key].append(adoption)
                source_counts[adoption["source"]] += 1
                kind_counts["bootstrap"] += 1
                relation_recovered_count += 1
                relation_recovered_projects.add(claim_key[0])
                recovery_basis_counts[f"confirmed_relation:{first['predicate']}:{first['method'] or 'unspecified'}"] += 1
            elif legacy_manual_seed and matches_website:
                adoption = {
                    **claim,
                    "adoption_kind": "bootstrap",
                    "source": "migration_legacy_project_context",
                    "source_ref": "state_json.evidence.discovery_sources:manual_seed+projects.website_url",
                    "registration_origin": False,
                    "created_at": "",
                    "review_required": False,
                }
                claim_adoptions[claim_key].append(adoption)
                source_counts[adoption["source"]] += 1
                kind_counts["bootstrap"] += 1
                legacy_context_recovered_count += 1
                legacy_context_recovered_projects.add(claim_key[0])
                recovery_basis_counts["legacy_manual_seed:website_url"] += 1
            else:
                uncovered_projects.add(claim_key[0])
                if len(uncovered) < example_limit:
                    uncovered.append(claim)
        else:
            uncovered_projects.add(claim_key[0])
            if len(uncovered) < example_limit:
                uncovered.append(claim)

    covered_count = sum(1 for claim in component_claims if claim_adoptions.get(claim))
    guarded_count = sum(1 for claim in component_claims if not claim_adoptions.get(claim) and active_guards.get(claim[:3]))
    uncovered_count = len(component_claims) - covered_count - guarded_count
    uncovered_roles: collections.Counter[str] = collections.Counter(
        claim[3]
        for claim in component_claims
        if not claim_adoptions.get(claim) and not active_guards.get(claim[:3])
    )
    result = {
        "confirmed_component_claim_count": len(component_claims),
        "covered_claim_count": covered_count,
        "coverage_percent": round(100.0 * covered_count / len(component_claims), 2) if component_claims else 100.0,
        "explicit_covered_claim_count": explicit_covered_count,
        "explicit_covered_project_count": len(explicit_covered_projects),
        "inferred_collection_claim_count": inferred_count,
        "inferred_collection_project_count": len(inferred_projects),
        "relation_recovered_claim_count": relation_recovered_count,
        "relation_recovered_project_count": len(relation_recovered_projects),
        "legacy_context_recovered_claim_count": legacy_context_recovered_count,
        "legacy_context_recovered_project_count": len(legacy_context_recovered_projects),
        "recovery_basis_counts": dict(sorted(recovery_basis_counts.items())),
        "guarded_confirmed_claim_count": guarded_count,
        "guarded_confirmed_project_count": len(guarded_projects),
        "uncovered_claim_count": uncovered_count,
        "uncovered_project_count": len(uncovered_projects),
        "uncovered_role_counts": dict(sorted(uncovered_roles.items())),
        "adoption_kind_counts": dict(sorted(kind_counts.items())),
        "adoption_source_counts": dict(sorted(source_counts.items())),
        "excluded_confirmed_role_counts": dict(sorted(excluded_roles.items())),
        "inferred_collection_examples": inferred,
        "guarded_confirmed_examples": guarded,
        "uncovered_examples": uncovered,
    }
    if include_rows:
        result["adoption_rows"] = [
            adoption
            for claim_key in sorted(claim_adoptions)
            for adoption in claim_adoptions[claim_key]
        ]
    return result


def _content_hash(report: dict[str, Any]) -> str:
    stable = {key: value for key, value in report.items() if key not in {"generated_at", "content_hash"}}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compare_database(conn: sqlite3.Connection, *, example_limit: int = 20) -> dict[str, Any]:
    input_counts = {
        "project_count": int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]),
        "identifier_count": int(conn.execute("SELECT COUNT(*) FROM project_identifiers").fetchone()[0]),
        "confirmed_identifier_count": int(conn.execute("SELECT COUNT(*) FROM project_identifiers WHERE classification='confirmed'").fetchone()[0]),
        "decision_count": int(conn.execute("SELECT COUNT(*) FROM project_decisions").fetchone()[0]),
    }
    decision_analysis = _decision_role_analysis(conn, example_limit=example_limit)
    projection = _adoption_projection(conn, decision_analysis, example_limit=example_limit)
    cases = evaluate_prototype_cases()
    decision_analysis = {key: value for key, value in decision_analysis.items() if key != "mappings"}
    report = {
        "report_schema_version": 1,
        "policy_version": POLICY_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "input": input_counts,
        "decision_role_mapping": decision_analysis,
        "adoption_projection": projection,
        "prototype_cases": cases,
        "storage_contract": {
            "current_identifiers": "existing project_identifiers remains current-only",
            "noncurrent_identifiers": "former and non-production positive project associations",
            "identifier_adoptions": "operator, bootstrap, and trust-policy reasons independent from standing",
            "identifier_transitions": "confirmed resource replacement independent from adoption",
            "identifier_exclusions": "registry-wide noise suppression without project attribution",
        },
        "review_gate": {
            "prototype_cases_passed": cases["failed_count"] == 0,
            "ready_for_user_review": cases["failed_count"] == 0,
            "production_schema_changed": False,
        },
    }
    report["content_hash"] = _content_hash(report)
    return report


def compare_database_path(path: Path, *, example_limit: int = 20) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"registry database not found: {resolved}")
    uri = f"{resolved.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        report = compare_database(conn, example_limit=max(1, int(example_limit)))
        conn.rollback()
    finally:
        conn.close()
    report["source"] = {"db_path": str(resolved), "read_only": True, "single_read_transaction": True}
    report["content_hash"] = _content_hash(report)
    return report
