from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from typing import Any

from .. import domain_policy
from .. import registry as core
from .. import storage, storage_cleanup
from project_registry_core import project_record


DEFAULT_INVESTIGATION_RETENTION_DAYS = 90
DEFAULT_INVESTIGATION_KEEP_LATEST = 3


def compress_investigation_history(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    example_limit: int = 20,
    vacuum: bool = False,
) -> dict[str, Any]:
    if example_limit < 0:
        raise ValueError("example_limit must be zero or greater")
    if vacuum and not execute:
        raise ValueError("--vacuum requires --execute")
    storage.ensure_investigation_tables(conn)
    if execute and conn.in_transaction:
        raise ValueError("history compression requires a connection without an active transaction")

    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count_before = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_before = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    run_count = 0
    compressed_run_count = 0
    candidate_run_count = 0
    candidate_value_count = 0
    logical_json_bytes = 0
    stored_bytes_before = 0
    projected_bytes_after = 0
    decode_seconds = 0.0
    encode_seconds = 0.0
    candidates: list[str] = []
    examples: list[dict[str, Any]] = []
    decode_failures: list[dict[str, str]] = []

    cursor = conn.execute(
        "SELECT run_id, input_project_json, output_project_json, evaluation_json, bundle_json "
        "FROM investigation_runs ORDER BY rowid"
    )
    while rows := cursor.fetchmany(50):
        for row in rows:
            run_count += 1
            run_id = str(row["run_id"])
            run_stored_before = 0
            run_projected_after = 0
            run_logical = 0
            run_candidate_values = 0
            run_failed = False
            for column in storage.HISTORY_JSON_COLUMNS:
                value = row[column]
                if isinstance(value, memoryview):
                    value = value.tobytes()
                stored_size = len(value.encode("utf-8")) if isinstance(value, str) else len(value)
                run_stored_before += stored_size
                try:
                    decode_started = time.perf_counter()
                    text = storage.decode_history_json_text(
                        value, context=f"investigation run {run_id} {column}"
                    )
                    json.loads(text)
                    decode_seconds += time.perf_counter() - decode_started
                except (storage.HistoryPayloadDecodeError, json.JSONDecodeError) as exc:
                    decode_failures.append({
                        "run_id": run_id,
                        "column": column,
                        "error": str(exc),
                    })
                    run_failed = True
                    break
                run_logical += len(text.encode("utf-8"))
                encode_started = time.perf_counter()
                encoded = storage.encode_history_json_text(text)
                encode_seconds += time.perf_counter() - encode_started
                run_projected_after += len(encoded)
                if not storage.is_compressed_history_json(value):
                    run_candidate_values += 1
            if run_failed:
                continue
            logical_json_bytes += run_logical
            stored_bytes_before += run_stored_before
            projected_bytes_after += run_projected_after
            if run_candidate_values:
                candidate_run_count += 1
                candidate_value_count += run_candidate_values
                candidates.append(run_id)
                if len(examples) < example_limit:
                    examples.append({
                        "run_id": run_id,
                        "candidate_value_count": run_candidate_values,
                        "stored_bytes_before": run_stored_before,
                        "projected_bytes_after": run_projected_after,
                        "bytes_saved": run_stored_before - run_projected_after,
                    })
            else:
                compressed_run_count += 1

    integrity_before = "not_run"
    integrity_after = "not_run"
    foreign_key_violation_count_before: int | None = None
    foreign_key_violation_count_after: int | None = None
    if execute:
        if decode_failures:
            raise ValueError(
                f"history compression refused because {len(decode_failures)} payloads failed to decode"
            )
        integrity_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        integrity_before = "ok" if integrity_rows == ["ok"] else "; ".join(integrity_rows)
        if integrity_before != "ok":
            raise ValueError(f"database integrity check failed before history compression: {integrity_before}")
        foreign_key_violation_count_before = sum(1 for _row in conn.execute("PRAGMA foreign_key_check"))
        if foreign_key_violation_count_before:
            raise ValueError(
                f"database has {foreign_key_violation_count_before} foreign key violations before history compression"
            )
        conn.execute("BEGIN IMMEDIATE")
        try:
            for run_id in candidates:
                row = conn.execute(
                    "SELECT input_project_json, output_project_json, evaluation_json, bundle_json "
                    "FROM investigation_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                assert row is not None
                values = {
                    column: storage.encode_history_json_text(storage.decode_history_json_text(
                        row[column], context=f"investigation run {run_id} {column}"
                    ))
                    for column in storage.HISTORY_JSON_COLUMNS
                }
                conn.execute(
                    "UPDATE investigation_runs SET input_project_json = ?, output_project_json = ?, "
                    "evaluation_json = ?, bundle_json = ? WHERE run_id = ?",
                    (
                        values["input_project_json"],
                        values["output_project_json"],
                        values["evaluation_json"],
                        values["bundle_json"],
                        run_id,
                    ),
                )
            foreign_key_violation_count_after = sum(
                1 for _row in conn.execute("PRAGMA foreign_key_check")
            )
            if foreign_key_violation_count_after:
                raise ValueError(
                    f"database has {foreign_key_violation_count_after} foreign key violations after history compression"
                )
            integrity_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
            integrity_after = "ok" if integrity_rows == ["ok"] else "; ".join(integrity_rows)
            if integrity_after != "ok":
                raise ValueError(
                    f"database integrity check failed after history compression: {integrity_after}"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if vacuum:
        conn.execute("VACUUM")

    page_count_after = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_after = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    return {
        "mode": "execute" if execute else "dry_run",
        "run_count": run_count,
        "already_compressed_run_count": compressed_run_count,
        "candidate_run_count": candidate_run_count,
        "candidate_value_count": candidate_value_count,
        "logical_json_bytes": logical_json_bytes,
        "stored_bytes_before": stored_bytes_before,
        "projected_bytes_after": projected_bytes_after,
        "estimated_bytes_saved": stored_bytes_before - projected_bytes_after,
        "decode_failure_count": len(decode_failures),
        "decode_failures": decode_failures[:example_limit],
        "decode_seconds": round(decode_seconds, 6),
        "encode_seconds": round(encode_seconds, 6),
        "updated_run_count": candidate_run_count if execute else 0,
        "vacuumed": vacuum,
        "integrity_check_before": integrity_before,
        "integrity_check_after": integrity_after,
        "foreign_key_violation_count_before": foreign_key_violation_count_before,
        "foreign_key_violation_count_after": foreign_key_violation_count_after,
        "storage": {
            "page_size": page_size,
            "page_count_before": page_count_before,
            "page_count_after": page_count_after,
            "freelist_count_before": freelist_before,
            "freelist_count_after": freelist_after,
        },
        "candidate_examples": examples,
    }


def configure_multi_tenant_suffix(conn: sqlite3.Connection, suffix: str) -> dict[str, Any]:
    normalized = domain_policy.normalize_multi_tenant_suffix(suffix)
    added = domain_policy.add_configured_multi_tenant_suffix(normalized)
    same_site_refresh = core.refresh_current_same_site_evidence(conn)
    return {
        "suffix": normalized,
        "added": added,
        "custom_multi_tenant_suffixes": sorted(domain_policy.configured_multi_tenant_suffixes()),
        "same_site_refresh": same_site_refresh,
    }


def repair_database(conn: sqlite3.Connection, *, execute: bool = False) -> dict[str, Any]:
    return core.audit_and_repair_identifiers(conn, execute=execute)


def audit_urls(conn: sqlite3.Connection, *, example_limit: int = 20) -> dict[str, Any]:
    return core.build_url_field_audit(conn, example_limit=max(0, example_limit))


def audit_trust_graph(conn: sqlite3.Connection, *, example_limit: int = 20) -> dict[str, Any]:
    return core.build_trust_graph_audit(conn, example_limit=max(0, example_limit))


def repair_trust_graph(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    downgrade_unsupported: bool = False,
    example_limit: int = 20,
) -> dict[str, Any]:
    return core.repair_trust_graph(
        conn,
        execute=execute,
        downgrade_unsupported=downgrade_unsupported,
        example_limit=max(0, example_limit),
    )


def cleanup_storage_payloads(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    example_limit: int = 20,
    vacuum: bool = False,
) -> dict[str, Any]:
    return storage_cleanup.compact_registry_storage(
        conn,
        normalize_resource_url_records=project_record.normalize_resource_url_records,
        execute=execute,
        example_limit=example_limit,
        vacuum=vacuum,
    )


def prune_investigation_history(
    conn: sqlite3.Connection,
    *,
    retention_days: int = DEFAULT_INVESTIGATION_RETENTION_DAYS,
    keep_latest_per_project: int = DEFAULT_INVESTIGATION_KEEP_LATEST,
    execute: bool = False,
    vacuum: bool = False,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    if retention_days < 0:
        raise ValueError("retention_days must be zero or greater")
    if keep_latest_per_project < 1:
        raise ValueError("keep_latest_per_project must be at least 1")
    if vacuum and not execute:
        raise ValueError("--vacuum requires --execute")

    storage.ensure_investigation_tables(conn)
    effective_now = now or dt.datetime.now(dt.timezone.utc)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=dt.timezone.utc)
    cutoff = effective_now - dt.timedelta(days=retention_days)
    rows = conn.execute(
        """
        SELECT
            i.run_id,
            i.project_key,
            i.completed_at,
            i.rowid AS storage_order,
            (
                LENGTH(CAST(i.input_project_json AS BLOB)) +
                LENGTH(CAST(i.output_project_json AS BLOB)) +
                LENGTH(CAST(i.evaluation_json AS BLOB)) +
                LENGTH(CAST(i.bundle_json AS BLOB)) +
                COALESCE(SUM(
                    LENGTH(CAST(c.result_json AS BLOB)) +
                    LENGTH(CAST(c.records_json AS BLOB)) +
                    LENGTH(CAST(c.failures_json AS BLOB))
                ), 0)
            ) AS estimated_payload_bytes,
            COUNT(c.ordinal) AS collector_run_count
        FROM investigation_runs i
        LEFT JOIN collector_runs c ON c.run_id = i.run_id
        GROUP BY i.run_id
        ORDER BY i.project_key ASC, i.completed_at DESC, i.rowid DESC
        """
    ).fetchall()

    project_rank: dict[str, int] = {}
    candidates: list[dict[str, Any]] = []
    retained_latest = 0
    retained_recent = 0
    retained_invalid_timestamp = 0
    for row in rows:
        project_key = str(row["project_key"])
        rank = project_rank.get(project_key, 0)
        project_rank[project_key] = rank + 1
        completed_at = str(row["completed_at"])
        completed = project_record.parse_iso(completed_at)
        if rank < keep_latest_per_project:
            retained_latest += 1
            continue
        if completed is None:
            retained_invalid_timestamp += 1
            continue
        if completed >= cutoff:
            retained_recent += 1
            continue
        candidates.append({
            "run_id": str(row["run_id"]),
            "project_key": project_key,
            "completed_at": completed_at,
            "estimated_payload_bytes": int(row["estimated_payload_bytes"] or 0),
            "collector_run_count": int(row["collector_run_count"] or 0),
        })

    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count_before = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_before = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    collector_candidates = sum(item["collector_run_count"] for item in candidates)
    estimated_payload_bytes = sum(item["estimated_payload_bytes"] for item in candidates)

    if execute and candidates:
        conn.executemany(
            "DELETE FROM investigation_runs WHERE run_id = ?",
            [(item["run_id"],) for item in candidates],
        )
        conn.commit()
    if vacuum:
        conn.execute("VACUUM")

    remaining_runs = int(conn.execute("SELECT COUNT(*) FROM investigation_runs").fetchone()[0])
    remaining_collector_runs = int(conn.execute("SELECT COUNT(*) FROM collector_runs").fetchone()[0])
    page_count_after = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_after = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    return {
        "mode": "execute" if execute else "dry_run",
        "policy": {
            "retention_days": retention_days,
            "keep_latest_per_project": keep_latest_per_project,
            "cutoff": cutoff.isoformat(),
        },
        "run_count_before": len(rows),
        "candidate_run_count": len(candidates),
        "candidate_collector_run_count": collector_candidates,
        "estimated_payload_bytes": estimated_payload_bytes,
        "retained_latest_count": retained_latest,
        "retained_recent_count": retained_recent,
        "retained_invalid_timestamp_count": retained_invalid_timestamp,
        "deleted_run_count": len(candidates) if execute else 0,
        "deleted_collector_run_count": collector_candidates if execute else 0,
        "remaining_run_count": remaining_runs,
        "remaining_collector_run_count": remaining_collector_runs,
        "vacuumed": vacuum,
        "storage": {
            "page_size": page_size,
            "page_count_before": page_count_before,
            "page_count_after": page_count_after,
            "freelist_count_before": freelist_before,
            "freelist_count_after": freelist_after,
        },
        "candidate_examples": candidates[:20],
    }
