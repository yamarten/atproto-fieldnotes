from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Any

from . import runtime_view


SCHEMA_VERSION = 2
CLASSIFICATIONS = {"confirmed", "candidate", "derived", "heuristic"}


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


def create_schema(conn: sqlite3.Connection) -> None:
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
        CREATE TABLE IF NOT EXISTS sync_state (
            scope_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            scope_json TEXT NOT NULL DEFAULT '{}',
            watermark_json TEXT NOT NULL DEFAULT '{}',
            last_success_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO registry_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )


def open_database(path: Path, *, create: bool = True) -> sqlite3.Connection:
    path = Path(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    version = database_schema_version(conn)
    has_projects = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
    ).fetchone() is not None
    if version is None and has_projects:
        conn.close()
        raise SchemaMismatchError(f"legacy registry database requires migrate-db: {path}")
    if version not in {None, SCHEMA_VERSION}:
        conn.close()
        raise SchemaMismatchError(f"unsupported registry schema version {version}: {path}")
    create_schema(conn)
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


def copy_table_rows(source: sqlite3.Connection, target: sqlite3.Connection, table: str) -> None:
    exists = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not exists:
        return
    rows = source.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return
    columns = [str(item[1]) for item in source.execute(f"PRAGMA table_info({table})")]
    placeholders = ",".join("?" for _ in columns)
    target.executemany(
        f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def legacy_flatten(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    flattened: dict[str, Any] = {}
    for value in payload.values():
        if isinstance(value, dict):
            flattened.update(value)
    return flattened


def legacy_project_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = legacy_flatten(_load_json(str(row["record_json"] or "{}"), {}))
    payload.update(legacy_flatten(_load_json(str(row["ops_json"] or "{}"), {})))
    for field in ("project_key", "name", "website_url", "service_status", "tracking_state", "introduced_url",
                  "first_seen_at", "last_seen_at", "last_enriched_at"):
        if field in row.keys():
            payload[field] = row[field]
    payload["service_url_candidates"] = list(dict.fromkeys(
        list(payload.get("service_url_candidates") or []) + list(payload.get("website_url_candidates") or [])
    ))
    payload["source_code_urls"] = list(dict.fromkeys(
        list(payload.get("source_code_urls") or []) + list(payload.get("github_repo_urls") or []) + list(payload.get("tangled_repo_urls") or [])
    ))
    if payload.get("resource_urls") and not payload.get("resource_url_records"):
        payload["resource_url_records"] = [
            {"url": value, "role": "unknown", "source": "legacy_resource_urls", "observed_at": "", "confidence": "legacy"}
            for value in payload["resource_urls"]
        ]
    return payload


def migrate_legacy_project_snapshot(payload: Any) -> dict[str, Any]:
    project = dict(payload) if isinstance(payload, dict) else {}
    project["service_url_candidates"] = list(dict.fromkeys(
        list(project.get("service_url_candidates") or []) + list(project.get("website_url_candidates") or [])
    ))
    project["source_code_urls"] = list(dict.fromkeys(
        list(project.get("source_code_urls") or [])
        + list(project.get("github_repo_urls") or [])
        + list(project.get("tangled_repo_urls") or [])
    ))
    if project.get("resource_urls") and not project.get("resource_url_records"):
        project["resource_url_records"] = [
            {"url": value, "role": "unknown", "source": "legacy_resource_urls", "observed_at": "", "confidence": "legacy"}
            for value in project["resource_urls"]
        ]
    for field in ("website_url_candidates", "github_repo_urls", "tangled_repo_urls", "resource_urls", "introduced"):
        project.pop(field, None)
    return project


def migrate_legacy_bundle(content: str) -> str:
    from .evidence import EvidenceBundle, populate_collector_results

    payload = _load_json(content, {})
    if not isinstance(payload, dict):
        raise ValueError("investigation bundle must be an object")
    if int(payload.get("schema_version") or 0) == 1:
        payload["schema_version"] = 2
        payload.setdefault("collector_results", [])
    bundle = EvidenceBundle.from_dict(payload)
    populate_collector_results(bundle)
    return _json(bundle.to_dict())


def migrate_investigation_rows(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    exists = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_runs'"
    ).fetchone()
    if not exists:
        return
    for row in source.execute("SELECT * FROM investigation_runs ORDER BY rowid"):
        bundle_json = migrate_legacy_bundle(str(row["bundle_json"]))
        target.execute(
            """
            INSERT INTO investigation_runs(
                run_id, project_key, started_at, completed_at, evidence_schema_version,
                evaluation_version, input_project_json, output_project_json, evaluation_json, bundle_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["run_id"], row["project_key"], row["started_at"], row["completed_at"], 2,
                row["evaluation_version"],
                _json(migrate_legacy_project_snapshot(_load_json(str(row["input_project_json"]), {}))),
                _json(migrate_legacy_project_snapshot(_load_json(str(row["output_project_json"]), {}))),
                row["evaluation_json"], bundle_json,
            ),
        )


def validate_migrated_projects(
    source_projects: list[dict[str, Any]],
    target: sqlite3.Connection,
) -> None:
    from . import registry

    expected_keys = [str(project.get("project_key") or "") for project in source_projects]
    actual_keys = [str(row[0]) for row in target.execute("SELECT project_key FROM projects ORDER BY project_key")]
    if sorted(expected_keys) != actual_keys:
        raise RuntimeError("project key verification failed")
    for source_project in source_projects:
        project_key = str(source_project.get("project_key") or "")
        migrated = registry.fetch_project(target, project_key)
        if migrated is None:
            raise RuntimeError(f"migrated project missing: {project_key}")
        for field in ("name", "website_url", "service_status", "tracking_state", "introduced_url"):
            if str(migrated.get(field) or "") != str(source_project.get(field) or ""):
                raise RuntimeError(f"canonical field mismatch: {project_key} {field}")
        for field in ("service_url_candidates", "source_code_urls"):
            expected_values = {str(value) for value in source_project.get(field, []) if str(value)}
            actual_values = {str(value) for value in migrated.get(field, []) if str(value)}
            if not expected_values.issubset(actual_values):
                raise RuntimeError(f"canonical identifier mismatch: {project_key} {field}")


def validate_database(
    conn: sqlite3.Connection,
    *,
    expected_projects: int | None = None,
    require_clean_normalized_state: bool = False,
) -> dict[str, Any]:
    integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    project_count = int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    if integrity != "ok" or foreign_keys:
        raise RuntimeError(f"database validation failed: integrity={integrity} foreign_keys={len(foreign_keys)}")
    if expected_projects is not None and project_count != expected_projects:
        raise RuntimeError(f"project count mismatch: expected={expected_projects} actual={project_count}")
    nested_forbidden_names = runtime_view.REMOVED_COMPATIBILITY_FIELDS | {runtime_view.RUNTIME_VIEW_MARKER}

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
            if key in runtime_view.NORMALIZED_FIELDS or key == runtime_view.RUNTIME_VIEW_MARKER
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
    if invalid_decisions or (require_clean_normalized_state and leakage):
        raise RuntimeError(
            f"database normalized-state validation failed: leakage={len(leakage)} invalid_decisions={invalid_decisions}"
        )
    return {
        "schema_version": database_schema_version(conn),
        "project_count": project_count,
        "identifier_count": int(conn.execute("SELECT COUNT(*) FROM project_identifiers").fetchone()[0]),
        "relation_count": int(conn.execute("SELECT COUNT(*) FROM project_relations").fetchone()[0]),
        "decision_count": int(conn.execute("SELECT COUNT(*) FROM project_decisions").fetchone()[0]),
        "integrity": integrity,
        "normalized_field_leak_count": len(leakage),
        "projects_without_manual_seed": projects_without_manual_seed,
        "invalid_decision_count": invalid_decisions,
    }


def backup_path(path: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.v1-backup-{timestamp}")
    ordinal = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.v1-backup-{timestamp}-{ordinal}")
        ordinal += 1
    return candidate


def migrate_database(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    source_mode = stat.S_IMODE(path.stat().st_mode)
    probe = sqlite3.connect(path)
    version = database_schema_version(probe)
    probe.close()
    if version == SCHEMA_VERSION:
        conn = open_database(path, create=False)
        try:
            return {"status": "already_current", "path": str(path), **validate_database(conn)}
        finally:
            conn.close()
    if version is not None:
        raise SchemaMismatchError(f"unsupported source schema version {version}: {path}")

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.v2-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    source = sqlite3.connect(path)
    source.row_factory = sqlite3.Row
    target = open_database(temp_path)
    try:
        from . import registry
        from . import storage as investigation_storage

        investigation_storage.ensure_investigation_tables(target)

        rows = source.execute("SELECT * FROM projects ORDER BY project_key").fetchall()
        source_projects = [legacy_project_payload(row) for row in rows]
        for project_payload in source_projects:
            project = registry.ensure_project_shape(project_payload)
            registry.save_project(target, project, enriched=False, preserve_timestamps=True)
        copy_table_rows(source, target, "sync_state")
        migrate_investigation_rows(source, target)
        copy_table_rows(source, target, "collector_runs")
        target.commit()
        report = validate_database(
            target,
            expected_projects=len(rows),
            require_clean_normalized_state=True,
        )
        validate_migrated_projects(source_projects, target)
        report["canonical_snapshot_verified"] = True
    except Exception as exc:
        target.close()
        source.close()
        raise RuntimeError(
            f"migration failed; source unchanged; temporary database retained at {temp_path}"
        ) from exc
    target.close()
    source.close()

    if dry_run:
        temp_path.unlink(missing_ok=True)
        return {"status": "dry_run", "path": str(path), "backup_path": "", **report}

    backup = backup_path(path)
    try:
        os.chmod(temp_path, source_mode)
        os.replace(path, backup)
        os.replace(temp_path, path)
    except Exception:
        if not path.exists() and backup.exists():
            os.replace(backup, path)
        raise
    return {"status": "migrated", "path": str(path), "backup_path": str(backup), **report}
