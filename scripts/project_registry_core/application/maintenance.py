from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from .. import database
from .. import registry as core
from .. import storage


DEFAULT_INVESTIGATION_RETENTION_DAYS = 90
DEFAULT_INVESTIGATION_KEEP_LATEST = 3
MIGRATION_PROVENANCE_KEYS = {
    "migration_source",
    "legacy_field",
    "legacy_classification",
    "trust_policy",
    "downgrade_reason",
    "trust_basis",
    "legacy_record",
}


def migrate_database(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    return database.migrate_database(path, dry_run=dry_run)


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


def cleanup_app_links(conn: sqlite3.Connection, *, execute: bool = False) -> dict[str, Any]:
    return core.cleanup_project_app_links(conn, execute=execute)


def cleanup_sync_notes(conn: sqlite3.Connection, *, execute: bool = False, example_limit: int = 20) -> dict[str, Any]:
    return core.cleanup_project_sync_notes(conn, execute=execute, example_limit=max(0, example_limit))


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
                LENGTH(i.input_project_json) + LENGTH(i.output_project_json) +
                LENGTH(i.evaluation_json) + LENGTH(i.bundle_json) +
                COALESCE(SUM(
                    LENGTH(c.result_json) + LENGTH(c.records_json) + LENGTH(c.failures_json)
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
        completed = core.parse_iso(completed_at)
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


def prune_migration_provenance(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT project_key, ordinal, kind, value, role, classification, metadata_json
        FROM project_identifiers
        ORDER BY project_key, ordinal
        """
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        metadata = json.loads(str(row["metadata_json"] or "{}"))
        if not isinstance(metadata, dict):
            continue
        if metadata.get("migration_source") != "schema_v1" and metadata.get("trust_policy") != database.V1_TRUST_POLICY:
            continue
        legacy_record = metadata.get("legacy_record")
        retained = {
            str(key): value
            for key, value in metadata.items()
            if key not in MIGRATION_PROVENANCE_KEYS
        }
        if isinstance(legacy_record, dict):
            for key, value in legacy_record.items():
                if key not in MIGRATION_PROVENANCE_KEYS:
                    retained.setdefault(str(key), value)
        if str(row["kind"]) == "repo_url" and str(row["role"]) == "source_repository_record":
            retained["url"] = str(row["value"])
            retained["confidence"] = str(row["classification"])
        old_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        new_json = json.dumps(retained, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        candidates.append({
            "project_key": str(row["project_key"]),
            "ordinal": int(row["ordinal"]),
            "kind": str(row["kind"]),
            "value": str(row["value"]),
            "role": str(row["role"]),
            "classification": str(row["classification"]),
            "old_metadata_bytes": len(old_json.encode("utf-8")),
            "new_metadata_bytes": len(new_json.encode("utf-8")),
            "new_metadata_json": new_json,
        })

    if execute and candidates:
        conn.executemany(
            """
            UPDATE project_identifiers
            SET metadata_json = ?
            WHERE project_key = ? AND ordinal = ?
            """,
            [
                (item["new_metadata_json"], item["project_key"], item["ordinal"])
                for item in candidates
            ],
        )
        conn.commit()

    old_bytes = sum(item["old_metadata_bytes"] for item in candidates)
    new_bytes = sum(item["new_metadata_bytes"] for item in candidates)
    return {
        "mode": "execute" if execute else "dry_run",
        "trust_policy": database.V1_TRUST_POLICY,
        "candidate_identifier_count": len(candidates),
        "updated_identifier_count": len(candidates) if execute else 0,
        "estimated_metadata_bytes_before": old_bytes,
        "estimated_metadata_bytes_after": new_bytes,
        "estimated_metadata_bytes_removed": max(0, old_bytes - new_bytes),
        "preserved_identifier_columns": [
            "project_key",
            "kind",
            "value",
            "role",
            "classification",
            "is_primary",
            "provider",
            "observed_at",
        ],
        "candidate_examples": [
            {key: value for key, value in item.items() if key != "new_metadata_json"}
            for item in candidates[:20]
        ],
    }
