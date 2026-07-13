from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import runtime_view


SCHEMA_VERSION = 2
CLASSIFICATIONS = {"confirmed", "candidate", "derived", "heuristic"}
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)


class SchemaMismatchError(RuntimeError):
    pass


ProjectIdentifier = runtime_view.ProjectIdentifier
ProjectDecision = runtime_view.ProjectDecision
IDENTIFIER_FIELDS = runtime_view.IDENTIFIER_FIELDS
DECISION_FIELDS = runtime_view.DECISION_FIELDS
NORMALIZED_FIELDS = runtime_view.NORMALIZED_FIELDS


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    parsed = json.loads(value)
    return parsed


def database_schema_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'registry_meta'"
    ).fetchone()
    if row is None:
        return None
    value = conn.execute("SELECT value FROM registry_meta WHERE key = 'schema_version'").fetchone()
    return int(value[0]) if value else None


def configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")


def enable_wal_mode(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS registry_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            project_key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            website_url TEXT NOT NULL,
            service_status TEXT NOT NULL,
            tracking_state TEXT NOT NULL DEFAULT 'active',
            introduced_url TEXT NOT NULL DEFAULT '',
            profile_json TEXT NOT NULL DEFAULT '{}',
            state_json TEXT NOT NULL DEFAULT '{}',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_enriched_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_projects_tracking_state ON projects(tracking_state);
        CREATE TABLE IF NOT EXISTS project_key_aliases (
            alias_project_key TEXT PRIMARY KEY,
            canonical_project_key TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            CHECK(alias_project_key <> canonical_project_key),
            FOREIGN KEY(canonical_project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_project_key_aliases_canonical
            ON project_key_aliases(canonical_project_key);
        CREATE TABLE IF NOT EXISTS project_identifiers (
            project_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            role TEXT NOT NULL,
            classification TEXT NOT NULL,
            is_primary INTEGER NOT NULL DEFAULT 0,
            provider TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            observed_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(project_key, ordinal),
            UNIQUE(project_key, kind, value, role),
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_project_identifiers_lookup
            ON project_identifiers(kind, value, classification);
        CREATE TABLE IF NOT EXISTS project_relations (
            project_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            subject_type TEXT NOT NULL,
            subject_value TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_value TEXT NOT NULL,
            classification TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            locator TEXT NOT NULL DEFAULT '',
            method TEXT NOT NULL DEFAULT '',
            context_json TEXT NOT NULL DEFAULT '{}',
            observed_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(project_key, ordinal),
            UNIQUE(project_key, subject_type, subject_value, predicate, object_type, object_value, source, locator, method),
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_project_relations_object
            ON project_relations(predicate, object_type, object_value, classification);
        CREATE INDEX IF NOT EXISTS idx_project_relations_subject
            ON project_relations(subject_type, subject_value, predicate, classification);
        CREATE INDEX IF NOT EXISTS idx_project_relations_source
            ON project_relations(source, predicate, classification);
        CREATE TABLE IF NOT EXISTS project_decisions (
            project_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT '',
            target_value TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL DEFAULT '',
            cleared_at TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(project_key, ordinal),
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_project_decisions_kind
            ON project_decisions(project_key, kind, expires_at);
        CREATE INDEX IF NOT EXISTS idx_project_decisions_target
            ON project_decisions(kind, target_type, target_value, cleared_at, expires_at);
        CREATE TABLE IF NOT EXISTS sync_state (
            scope_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            scope_json TEXT NOT NULL DEFAULT '{}',
            watermark_json TEXT NOT NULL DEFAULT '{}',
            last_success_at TEXT NOT NULL
        );
        """
    )


def create_schema(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO registry_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def project_key_alias_target(conn: sqlite3.Connection, project_key: str) -> str | None:
    row = conn.execute(
        "SELECT canonical_project_key FROM project_key_aliases WHERE alias_project_key = ?",
        (project_key,),
    ).fetchone()
    return str(row[0]) if row else None


def project_key_family(conn: sqlite3.Connection, canonical_project_key: str) -> list[str]:
    canonical_project_key = project_key_alias_target(conn, canonical_project_key) or canonical_project_key
    rows = conn.execute(
        "SELECT alias_project_key FROM project_key_aliases WHERE canonical_project_key = ? ORDER BY alias_project_key",
        (canonical_project_key,),
    ).fetchall()
    return [canonical_project_key, *[str(row[0]) for row in rows]]


def register_project_key_alias(
    conn: sqlite3.Connection,
    *,
    alias_project_key: str,
    canonical_project_key: str,
    reason: str,
    created_at: str,
) -> None:
    if alias_project_key == canonical_project_key:
        raise ValueError("project alias must differ from its canonical project")
    target = conn.execute(
        "SELECT 1 FROM projects WHERE project_key = ?",
        (canonical_project_key,),
    ).fetchone()
    if target is None:
        raise ValueError(f"canonical project not found: {canonical_project_key}")
    conn.execute(
        "UPDATE project_key_aliases SET canonical_project_key = ? WHERE canonical_project_key = ?",
        (canonical_project_key, alias_project_key),
    )
    conn.execute(
        """
        INSERT INTO project_key_aliases(alias_project_key, canonical_project_key, reason, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(alias_project_key) DO UPDATE SET
            canonical_project_key = excluded.canonical_project_key,
            reason = excluded.reason,
            created_at = excluded.created_at
        """,
        (alias_project_key, canonical_project_key, reason, created_at),
    )


def open_database(path: Path, *, create: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if not path.exists() and not create:
        raise FileNotFoundError(
            f"registry database not found: {path}; run project-registry init-db first"
        )
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    configure_connection(conn)
    version = database_schema_version(conn)
    has_projects = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
    ).fetchone() is not None
    if version is None and has_projects:
        conn.close()
        raise SchemaMismatchError(f"unsupported legacy registry database schema: {path}")
    if version is None and not create:
        conn.close()
        raise SchemaMismatchError(
            f"registry database is not initialized: {path}; run project-registry init-db first"
        )
    if version not in {None, SCHEMA_VERSION}:
        conn.close()
        raise SchemaMismatchError(f"unsupported registry schema version {version}: {path}")
    enable_wal_mode(conn)
    if version is None:
        create_schema(conn)
    else:
        ensure_schema(conn)
    return conn


def project_identifiers(project: dict[str, Any]) -> list[ProjectIdentifier]:
    return runtime_view.project_identifiers(project)


def project_decisions(project: dict[str, Any]) -> list[ProjectDecision]:
    return runtime_view.project_decisions(project)


def strip_normalized_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return runtime_view.strip_runtime_fields(payload)


def replace_project_children(conn: sqlite3.Connection, project: dict[str, Any]) -> None:
    project_key = str(project["project_key"])
    conn.execute("DELETE FROM project_identifiers WHERE project_key = ?", (project_key,))
    for ordinal, item in enumerate(project_identifiers(project)):
        conn.execute(
            "INSERT INTO project_identifiers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_key, ordinal, item.kind, item.value, item.role, item.classification, int(item.is_primary),
             item.provider, _json(item.metadata or {}), item.observed_at),
        )

    conn.execute("DELETE FROM project_relations WHERE project_key = ?", (project_key,))
    for ordinal, item in enumerate(runtime_view.relation_rows(project)):
        conn.execute(
            "INSERT INTO project_relations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_key, ordinal, item["subject_type"], item["subject_value"], item["predicate"],
                item["object_type"], item["object_value"], item["classification"], item["source"],
                item["locator"], item["method"], _json(item["context"]), item["observed_at"],
            ),
        )

    conn.execute("DELETE FROM project_decisions WHERE project_key = ?", (project_key,))
    for ordinal, item in enumerate(project_decisions(project)):
        conn.execute(
            "INSERT INTO project_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_key, ordinal, item.kind, item.target_type, item.target_value, item.note,
             item.created_at, item.expires_at, item.cleared_at, _json(item.metadata or {})),
        )


def hydrate_project_children(conn: sqlite3.Connection, project: dict[str, Any]) -> dict[str, Any]:
    project_key = str(project.get("project_key") or "")
    identifiers = conn.execute(
        "SELECT * FROM project_identifiers WHERE project_key = ? ORDER BY ordinal", (project_key,)
    ).fetchall()
    relations = conn.execute(
        "SELECT * FROM project_relations WHERE project_key = ? ORDER BY ordinal", (project_key,)
    ).fetchall()
    decisions = conn.execute(
        "SELECT * FROM project_decisions WHERE project_key = ? ORDER BY ordinal", (project_key,)
    ).fetchall()
    return runtime_view.hydrate(
        project,
        identifier_rows=identifiers,
        relation_rows=relations,
        decision_rows=decisions,
        load_json=_load_json,
    )


def replace_identifier_rows(
    conn: sqlite3.Connection,
    project_key: str,
    identifiers: list[ProjectIdentifier],
) -> None:
    conn.execute("DELETE FROM project_identifiers WHERE project_key = ?", (project_key,))
    for ordinal, item in enumerate(identifiers):
        conn.execute(
            "INSERT INTO project_identifiers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_key, ordinal, item.kind, item.value, item.role, item.classification, int(item.is_primary),
             item.provider, _json(item.metadata or {}), item.observed_at),
        )


def normalized_rows_snapshot(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    snapshot: dict[str, list[dict[str, Any]]] = {"identifiers": [], "relations": [], "decisions": []}
    for row in conn.execute(
        """
        SELECT project_key, ordinal, kind, value, role, classification, is_primary,
               provider, metadata_json, observed_at
        FROM project_identifiers
        ORDER BY project_key, ordinal
        """
    ):
        snapshot["identifiers"].append({
            "project_key": str(row["project_key"]),
            "ordinal": int(row["ordinal"]),
            "kind": str(row["kind"]),
            "value": str(row["value"]),
            "role": str(row["role"]),
            "classification": str(row["classification"]),
            "is_primary": int(row["is_primary"]),
            "provider": str(row["provider"]),
            "metadata": _load_json(str(row["metadata_json"]), {}),
            "observed_at": str(row["observed_at"]),
        })
    for row in conn.execute(
        """
        SELECT project_key, ordinal, subject_type, subject_value, predicate,
               object_type, object_value, classification, source, locator,
               method, context_json, observed_at
        FROM project_relations
        ORDER BY project_key, ordinal
        """
    ):
        snapshot["relations"].append({
            "project_key": str(row["project_key"]),
            "ordinal": int(row["ordinal"]),
            "subject_type": str(row["subject_type"]),
            "subject_value": str(row["subject_value"]),
            "predicate": str(row["predicate"]),
            "object_type": str(row["object_type"]),
            "object_value": str(row["object_value"]),
            "classification": str(row["classification"]),
            "source": str(row["source"]),
            "locator": str(row["locator"]),
            "method": str(row["method"]),
            "context": _load_json(str(row["context_json"]), {}),
            "observed_at": str(row["observed_at"]),
        })
    for row in conn.execute(
        """
        SELECT project_key, ordinal, kind, target_type, target_value, note,
               created_at, expires_at, cleared_at, metadata_json
        FROM project_decisions
        ORDER BY project_key, ordinal
        """
    ):
        snapshot["decisions"].append({
            "project_key": str(row["project_key"]),
            "ordinal": int(row["ordinal"]),
            "kind": str(row["kind"]),
            "target_type": str(row["target_type"]),
            "target_value": str(row["target_value"]),
            "note": str(row["note"]),
            "created_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"]),
            "cleared_at": str(row["cleared_at"]),
            "metadata": _load_json(str(row["metadata_json"]), {}),
        })
    return snapshot


def verify_normalized_roundtrip(conn: sqlite3.Connection) -> dict[str, Any]:
    from . import registry

    before = normalized_rows_snapshot(conn)
    project_keys = [str(row["project_key"]) for row in conn.execute("SELECT project_key FROM projects ORDER BY project_key")]
    for project_key in project_keys:
        project = registry.fetch_project(conn, project_key)
        if project is None:
            raise RuntimeError(f"roundtrip project missing: {project_key}")
        registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True)
    after = normalized_rows_snapshot(conn)
    examples: list[dict[str, Any]] = []
    diff_count = 0
    for table in ("identifiers", "relations", "decisions"):
        if before[table] != after[table]:
            diff_count += 1
            examples.append({
                "table": table,
                "before_count": len(before[table]),
                "after_count": len(after[table]),
                "before_first": before[table][:3],
                "after_first": after[table][:3],
            })
    return {"normalized_roundtrip_diff_count": diff_count, "normalized_roundtrip_examples": examples[:3]}


def validate_database(
    conn: sqlite3.Connection,
    *,
    expected_projects: int | None = None,
) -> dict[str, Any]:
    integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    project_count = int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    if integrity != "ok" or foreign_keys:
        raise RuntimeError(f"database validation failed: integrity={integrity} foreign_keys={len(foreign_keys)}")
    if expected_projects is not None and project_count != expected_projects:
        raise RuntimeError(f"project count mismatch: expected={expected_projects} actual={project_count}")
    nested_forbidden_names = runtime_view.RUNTIME_INTERNAL_FIELDS

    def nested_forbidden_keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return ({str(key) for key in value if key in nested_forbidden_names}
                    | set().union(*(nested_forbidden_keys(item) for item in value.values()), set()))
        if isinstance(value, list):
            return set().union(*(nested_forbidden_keys(item) for item in value), set())
        return set()

    leakage: dict[str, list[str]] = {}
    for row in conn.execute("SELECT project_key, profile_json, state_json FROM projects"):
        profile = _load_json(str(row["profile_json"]), {})
        state = _load_json(str(row["state_json"]), {})
        top_level = {
            str(key)
            for payload in (profile, state)
            if isinstance(payload, dict)
            for key in payload
            if key in runtime_view.NORMALIZED_FIELDS or key in runtime_view.RUNTIME_INTERNAL_FIELDS
        }
        fields = top_level | nested_forbidden_keys(profile) | nested_forbidden_keys(state)
        if fields:
            leakage[str(row["project_key"])] = sorted(fields)
    projects_without_manual_seed = int(conn.execute(
        """
        SELECT COUNT(*) FROM projects p
        WHERE NOT EXISTS (
            SELECT 1 FROM project_identifiers i
            WHERE i.project_key = p.project_key AND i.role = 'manual_seed'
        )
        """
    ).fetchone()[0])
    invalid_decisions = int(conn.execute(
        """
        SELECT COUNT(*) FROM project_decisions
        WHERE kind IN ('block_identifier', 'defer_identifier', 'separate_project')
          AND (target_type = '' OR target_value = '')
        """
    ).fetchone()[0])
    if invalid_decisions or leakage:
        raise RuntimeError(
            f"database normalized-state validation failed: leakage={len(leakage)} invalid_decisions={invalid_decisions}"
        )
    return {
        "schema_version": database_schema_version(conn),
        "project_count": project_count,
        "identifier_count": int(conn.execute("SELECT COUNT(*) FROM project_identifiers").fetchone()[0]),
        "relation_count": int(conn.execute("SELECT COUNT(*) FROM project_relations").fetchone()[0]),
        "decision_count": int(conn.execute("SELECT COUNT(*) FROM project_decisions").fetchone()[0]),
        "project_alias_count": int(conn.execute("SELECT COUNT(*) FROM project_key_aliases").fetchone()[0]),
        "integrity": integrity,
        "runtime_internal_field_leak_count": len(leakage),
        "projects_without_manual_seed": projects_without_manual_seed,
        "invalid_decision_count": invalid_decisions,
    }
