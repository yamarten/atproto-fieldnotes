from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from .evidence import CollectorResult, EvidenceBundle
from . import database
from .runtime_view import sanitize_runtime_snapshot


EVALUATION_VERSION = "investigation-evaluation-v2"
RUN_METRICS_KEY = "_run_metrics"
RUN_METRICS_VERSION = 1
RUN_PHASE_NAMES = {
    "initial_collection_ms",
    "planning_ms",
    "investigation_collection_ms",
    "triage_ms",
    "evaluation_ms",
    "run_history_save_ms",
    "project_save_ms",
}
LLM_REQUEST_FIELDS = {
    "purpose",
    "status",
    "error_category",
    "duration_ms",
    "prompt_chars",
    "prompt_bytes",
    "response_bytes",
    "timeout_seconds",
    "transport",
    "model",
    "api_style",
    "reasoning_effort",
    "prompt_version",
}


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
    if root == "tangled_repo":
        return {"tangled_repo", "tangled_readme"}
    if root.startswith("service_"):
        return {root, "service_page"}
    return {root}


def aggregate_collector_results(results: list[CollectorResult]) -> list[CollectorResult]:
    status_rank = {"skipped": 0, "success": 1, "partial": 2, "rate_limited": 3, "failed": 4}
    grouped: dict[tuple[str, str], CollectorResult] = {}
    for result in results:
        key = (result.source, result.operation)
        current = grouped.get(key)
        if current is None:
            grouped[key] = result
            continue
        stop_reasons = list(dict.fromkeys(
            item for item in (current.stop_reason, result.stop_reason) if item
        ))
        grouped[key] = CollectorResult(
            source=result.source,
            operation=result.operation,
            status=(
                result.status
                if status_rank[result.status] >= status_rank[current.status]
                else current.status
            ),
            observed_at=max(current.observed_at, result.observed_at),
            request_count=current.request_count + result.request_count,
            result_count=current.result_count + result.result_count,
            cursor=result.cursor or current.cursor,
            stop_reason="; ".join(stop_reasons)[:500],
            retry_at=result.retry_at or current.retry_at,
            cache_hits=current.cache_hits + result.cache_hits,
        )
    return list(grouped.values())


def collector_run_projection_rows(bundle: EvidenceBundle) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ordinal, result in enumerate(aggregate_collector_results(bundle.collector_results)):
        roots = collector_source_roots(result.source)
        result_payload = {
            **result.to_dict(),
            "evidence_storage": "bundle_json",
            "record_count": sum(1 for record in bundle.records if source_root(record.source) in roots),
            "failure_count": sum(1 for failure in bundle.failures if source_root(failure.source) in roots),
        }
        rows.append({
            "ordinal": ordinal,
            "source": result.source,
            "operation": result.operation,
            "status": result.status,
            "observed_at": result.observed_at,
            "result_json": json.dumps(result_payload, ensure_ascii=False, sort_keys=True),
            "records_json": "[]",
            "failures_json": "[]",
        })
    return rows


def replace_collector_run_projection(
    conn: sqlite3.Connection,
    run_id: str,
    bundle: EvidenceBundle,
) -> None:
    conn.execute("DELETE FROM collector_runs WHERE run_id = ?", (run_id,))
    conn.executemany(
        """
        INSERT INTO collector_runs(
            run_id, ordinal, source, operation, status, observed_at,
            result_json, records_json, failures_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                row["ordinal"],
                row["source"],
                row["operation"],
                row["status"],
                row["observed_at"],
                row["result_json"],
                row["records_json"],
                row["failures_json"],
            )
            for row in collector_run_projection_rows(bundle)
        ],
    )


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


def normalize_run_metrics(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    phases = {
        key: max(0, int(raw_value or 0))
        for key, raw_value in dict(value.get("phases") or {}).items()
        if key in RUN_PHASE_NAMES
    }
    requests: list[dict[str, Any]] = []
    for item in list(value.get("llm_requests") or [])[:4]:
        if not isinstance(item, dict):
            continue
        requests.append({key: item[key] for key in LLM_REQUEST_FIELDS if key in item})
    return {
        "version": RUN_METRICS_VERSION,
        "total_ms": max(0, int(value.get("total_ms") or 0)),
        "phases": phases,
        "llm_requests": requests,
    }


def update_run_metrics_phase(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    phase: str,
    duration_ms: int,
) -> None:
    if phase not in RUN_PHASE_NAMES:
        raise ValueError(f"unknown investigation run phase: {phase}")
    row = conn.execute(
        "SELECT evaluation_json FROM investigation_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"investigation run not found: {run_id}")
    evaluation_payload = json.loads(str(row["evaluation_json"]))
    metrics_payload = normalize_run_metrics(evaluation_payload.get(RUN_METRICS_KEY))
    if not metrics_payload:
        return
    previous_ms = int(metrics_payload["phases"].get(phase, 0) or 0)
    resolved_ms = max(0, int(duration_ms or 0))
    metrics_payload["phases"][phase] = resolved_ms
    metrics_payload["total_ms"] = max(0, int(metrics_payload["total_ms"]) - previous_ms + resolved_ms)
    evaluation_payload[RUN_METRICS_KEY] = metrics_payload
    conn.execute(
        "UPDATE investigation_runs SET evaluation_json = ? WHERE run_id = ?",
        (json.dumps(evaluation_payload, ensure_ascii=False, sort_keys=True), run_id),
    )


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
    run_metrics: dict[str, Any] | None = None,
    evaluation_version: str = EVALUATION_VERSION,
    run_id: str | None = None,
) -> str:
    save_started = time.perf_counter()
    ensure_investigation_tables(conn)
    if bundle.records or bundle.failures:
        if not bundle.collector_results:
            raise ValueError("investigation bundles with collector output require explicit collector_results")
    resolved_run_id = run_id or f"inv:{uuid.uuid4().hex}"
    evaluation_payload = dict(evaluation or evaluation_snapshot(output_project))
    metrics_payload = normalize_run_metrics(run_metrics)
    if metrics_payload:
        evaluation_payload[RUN_METRICS_KEY] = metrics_payload
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
            json.dumps(sanitize_runtime_snapshot(input_project), ensure_ascii=False, sort_keys=True),
            json.dumps(sanitize_runtime_snapshot(output_project), ensure_ascii=False, sort_keys=True),
            json.dumps(evaluation_payload, ensure_ascii=False, sort_keys=True),
            json.dumps(bundle.to_dict(), ensure_ascii=False, sort_keys=True),
        ),
    )
    replace_collector_run_projection(conn, resolved_run_id, bundle)
    if metrics_payload:
        save_ms = max(0, round((time.perf_counter() - save_started) * 1000))
        metrics_payload["phases"]["run_history_save_ms"] = save_ms
        metrics_payload["total_ms"] = int(metrics_payload["total_ms"]) + save_ms
        evaluation_payload[RUN_METRICS_KEY] = metrics_payload
        conn.execute(
            "UPDATE investigation_runs SET evaluation_json = ? WHERE run_id = ?",
            (json.dumps(evaluation_payload, ensure_ascii=False, sort_keys=True), resolved_run_id),
        )
    return resolved_run_id


def load_investigation_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    ensure_investigation_tables(conn)
    row = conn.execute("SELECT * FROM investigation_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    evaluation = json.loads(str(row["evaluation_json"]))
    metrics = normalize_run_metrics(evaluation.pop(RUN_METRICS_KEY, None))
    return {
        "run_id": str(row["run_id"]),
        "project_key": str(row["project_key"]),
        "started_at": str(row["started_at"]),
        "completed_at": str(row["completed_at"]),
        "evidence_schema_version": int(row["evidence_schema_version"]),
        "evaluation_version": str(row["evaluation_version"]),
        "input_project": json.loads(str(row["input_project_json"])),
        "output_project": json.loads(str(row["output_project_json"])),
        "evaluation": evaluation,
        "metrics": metrics,
        "bundle": EvidenceBundle.from_json(str(row["bundle_json"])),
    }


def latest_investigation_run(conn: sqlite3.Connection, project_key: str) -> dict[str, Any] | None:
    ensure_investigation_tables(conn)
    keys = database.project_key_family(conn, project_key)
    placeholders = ",".join("?" for _ in keys)
    row = conn.execute(
        f"SELECT run_id FROM investigation_runs WHERE project_key IN ({placeholders}) "
        "ORDER BY completed_at DESC, rowid DESC LIMIT 1",
        keys,
    ).fetchone()
    return load_investigation_run(conn, str(row["run_id"])) if row else None


def list_investigation_runs(conn: sqlite3.Connection, *, project_key: str = "") -> list[dict[str, Any]]:
    ensure_investigation_tables(conn)
    if project_key:
        keys = database.project_key_family(conn, project_key)
        placeholders = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"SELECT run_id FROM investigation_runs WHERE project_key IN ({placeholders}) "
            "ORDER BY completed_at DESC, rowid DESC",
            keys,
        ).fetchall()
    else:
        rows = conn.execute("SELECT run_id FROM investigation_runs ORDER BY completed_at DESC, rowid DESC").fetchall()
    return [item for row in rows if (item := load_investigation_run(conn, str(row["run_id"]))) is not None]


def list_investigation_run_summaries(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ensure_investigation_tables(conn)
    keys = database.project_key_family(conn, project_key)
    placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"""
        SELECT r.run_id, r.project_key, r.started_at, r.completed_at,
               r.evidence_schema_version, r.evaluation_version,
               json_extract(r.evaluation_json, '$._run_metrics.total_ms') AS total_ms,
               COUNT(c.ordinal) AS collector_count,
               SUM(CASE WHEN c.status IN ('failed', 'partial', 'rate_limited') THEN 1 ELSE 0 END) AS problem_count
        FROM investigation_runs r
        LEFT JOIN collector_runs c ON c.run_id = r.run_id
        WHERE r.project_key IN ({placeholders})
        GROUP BY r.run_id
        ORDER BY r.completed_at DESC, r.rowid DESC
        LIMIT ?
        """,
        (*keys, max(1, min(500, int(limit)))),
    ).fetchall()
    return [{
        "run_id": str(row["run_id"]),
        "project_key": str(row["project_key"]),
        "started_at": str(row["started_at"]),
        "completed_at": str(row["completed_at"]),
        "evidence_schema_version": int(row["evidence_schema_version"]),
        "evaluation_version": str(row["evaluation_version"]),
        "total_ms": int(row["total_ms"] or 0),
        "collector_count": int(row["collector_count"] or 0),
        "problem_count": int(row["problem_count"] or 0),
    } for row in rows]


def list_collector_run_rows(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    ensure_investigation_tables(conn)
    rows = conn.execute(
        "SELECT * FROM collector_runs WHERE run_id = ? ORDER BY ordinal", (run_id,)
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        result = json.loads(str(row["result_json"]))
        output.append({
            "source": str(row["source"]),
            "operation": str(row["operation"]),
            "status": str(row["status"]),
            "observed_at": str(row["observed_at"]),
            "result": result,
            "record_count": int(result["record_count"]),
            "failure_count": int(result["failure_count"]),
        })
    return output
