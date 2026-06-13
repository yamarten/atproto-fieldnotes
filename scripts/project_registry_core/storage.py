from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .evidence import EvidenceBundle, populate_collector_results


EVALUATION_VERSION = "investigation-evaluation-v2"


def ensure_investigation_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS investigation_runs (
            run_id TEXT PRIMARY KEY,
            project_key TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            evidence_schema_version INTEGER NOT NULL,
            evaluation_version TEXT NOT NULL,
            input_project_json TEXT NOT NULL,
            output_project_json TEXT NOT NULL,
            evaluation_json TEXT NOT NULL,
            bundle_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_investigation_runs_project ON investigation_runs(project_key, completed_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collector_runs (
            run_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            source TEXT NOT NULL,
            operation TEXT NOT NULL,
            status TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            result_json TEXT NOT NULL,
            records_json TEXT NOT NULL,
            failures_json TEXT NOT NULL,
            PRIMARY KEY (run_id, ordinal),
            FOREIGN KEY (run_id) REFERENCES investigation_runs(run_id) ON DELETE CASCADE
        )
        """
    )


def source_root(value: str) -> str:
    return str(value or "").split(":", 1)[0]


def collector_source_roots(source: str) -> set[str]:
    root = source_root(source)
    if root == "github_repo":
        return {"github_repo", "github_repo_metadata", "github_readme", "github_source"}
    if root.startswith("service_"):
        return {root, "service_page"}
    return {root}


def evaluation_snapshot(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "owned_nsids": sorted(str(item) for item in project.get("resolved_nsids", []) if str(item)),
        "external_nsids": sorted(str(item) for item in project.get("external_lexicons", []) if str(item)),
        "atproto_contribution_level": str(project.get("atproto_contribution_level") or "unknown"),
        "atproto_contribution_score": int(project.get("atproto_contribution_score") or 0),
        "service_status": str(project.get("service_status") or "unknown"),
        "service_status_confidence": str(project.get("service_status_confidence") or "none"),
        "source_code_urls": sorted(str(item) for item in project.get("source_code_urls", []) if str(item)),
        "service_urls": sorted(str(item) for item in project.get("service_urls", []) if str(item)),
        "service_url_candidates": sorted(str(item) for item in project.get("service_url_candidates", []) if str(item)),
        "url_aliases": list(project.get("url_aliases", [])),
    }


def save_investigation_run(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    started_at: str,
    completed_at: str,
    input_project: dict[str, Any],
    output_project: dict[str, Any],
    bundle: EvidenceBundle,
    evaluation: dict[str, Any] | None = None,
    evaluation_version: str = EVALUATION_VERSION,
    run_id: str | None = None,
) -> str:
    ensure_investigation_tables(conn)
    populate_collector_results(bundle)
    resolved_run_id = run_id or f"inv:{uuid.uuid4().hex}"
    evaluation_payload = evaluation or evaluation_snapshot(output_project)
    conn.execute(
        """
        INSERT INTO investigation_runs(
            run_id, project_key, started_at, completed_at, evidence_schema_version,
            evaluation_version, input_project_json, output_project_json, evaluation_json, bundle_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resolved_run_id,
            project_key,
            started_at,
            completed_at,
            bundle.schema_version,
            evaluation_version,
            json.dumps(input_project, ensure_ascii=False, sort_keys=True),
            json.dumps(output_project, ensure_ascii=False, sort_keys=True),
            json.dumps(evaluation_payload, ensure_ascii=False, sort_keys=True),
            json.dumps(bundle.to_dict(), ensure_ascii=False, sort_keys=True),
        ),
    )
    for ordinal, result in enumerate(bundle.collector_results):
        roots = collector_source_roots(result.source)
        records = [record.to_dict() for record in bundle.records if source_root(record.source) in roots]
        failures = [failure.to_dict() for failure in bundle.failures if source_root(failure.source) in roots]
        conn.execute(
            """
            INSERT INTO collector_runs(
                run_id, ordinal, source, operation, status, observed_at,
                result_json, records_json, failures_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_run_id,
                ordinal,
                result.source,
                result.operation,
                result.status,
                result.observed_at,
                json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True),
                json.dumps(records, ensure_ascii=False, sort_keys=True),
                json.dumps(failures, ensure_ascii=False, sort_keys=True),
            ),
        )
    return resolved_run_id


def load_investigation_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    ensure_investigation_tables(conn)
    row = conn.execute("SELECT * FROM investigation_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return {
        "run_id": str(row["run_id"]),
        "project_key": str(row["project_key"]),
        "started_at": str(row["started_at"]),
        "completed_at": str(row["completed_at"]),
        "evidence_schema_version": int(row["evidence_schema_version"]),
        "evaluation_version": str(row["evaluation_version"]),
        "input_project": json.loads(str(row["input_project_json"])),
        "output_project": json.loads(str(row["output_project_json"])),
        "evaluation": json.loads(str(row["evaluation_json"])),
        "bundle": EvidenceBundle.from_json(str(row["bundle_json"])),
    }


def latest_investigation_run(conn: sqlite3.Connection, project_key: str) -> dict[str, Any] | None:
    ensure_investigation_tables(conn)
    row = conn.execute(
        "SELECT run_id FROM investigation_runs WHERE project_key = ? ORDER BY completed_at DESC, rowid DESC LIMIT 1",
        (project_key,),
    ).fetchone()
    return load_investigation_run(conn, str(row["run_id"])) if row else None


def list_investigation_runs(conn: sqlite3.Connection, *, project_key: str = "") -> list[dict[str, Any]]:
    ensure_investigation_tables(conn)
    if project_key:
        rows = conn.execute(
            "SELECT run_id FROM investigation_runs WHERE project_key = ? ORDER BY completed_at DESC, rowid DESC",
            (project_key,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT run_id FROM investigation_runs ORDER BY completed_at DESC, rowid DESC").fetchall()
    return [item for row in rows if (item := load_investigation_run(conn, str(row["run_id"]))) is not None]
