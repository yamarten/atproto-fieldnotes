from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

from .. import database
from .. import registry as core
from .. import runtime_view, storage, storage_cleanup


DEFAULT_INVESTIGATION_RETENTION_DAYS = 90
DEFAULT_INVESTIGATION_KEEP_LATEST = 3
def repair_database(conn: sqlite3.Connection, *, execute: bool = False) -> dict[str, Any]:
    return core.audit_and_repair_identifiers(conn, execute=execute)


def backfill_derived_identifiers(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    example_limit: int = 20,
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM projects ORDER BY project_key"
    ).fetchall()
    changes: list[tuple[str, list[Any], list[Any]]] = []
    added_by_kind: dict[str, int] = {}
    examples: list[dict[str, Any]] = []
    before_count = 0
    after_count = 0
    for row in rows:
        project = core.project_from_row(conn, row)
        existing = database.project_identifiers(project)
        proposed = runtime_view.identifiers_with_missing_derived_rows(project)
        before_count += len(existing)
        after_count += len(proposed)
        if proposed == existing:
            continue
        project_key = str(project["project_key"])
        changes.append((project_key, existing, proposed))
        existing_keys = {(item.kind, item.value, item.role) for item in existing}
        added = [
            item for item in proposed
            if (item.kind, item.value, item.role) not in existing_keys
        ]
        for item in added:
            added_by_kind[item.kind] = added_by_kind.get(item.kind, 0) + 1
        if len(examples) < max(0, example_limit):
            examples.append({
                "project_key": project_key,
                "added": [
                    {"kind": item.kind, "value": item.value, "role": item.role}
                    for item in added
                ],
            })

    if execute and changes:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for project_key, _existing, proposed in changes:
                database.replace_identifier_rows(conn, project_key, proposed)
            foreign_key_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_issues:
                raise RuntimeError(f"foreign key check failed: {len(foreign_key_issues)} issue(s)")
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise RuntimeError(f"integrity check failed: {integrity}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "mode": "execute" if execute else "dry_run",
        "project_count": len(rows),
        "changed_project_count": len(changes),
        "identifier_row_count_before": before_count,
        "identifier_row_count_after": after_count,
        "added_identifier_count": after_count - before_count,
        "added_by_kind": dict(sorted(added_by_kind.items())),
        "changed_project_examples": examples,
    }


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


def repair_same_site_evidence(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    example_limit: int = 20,
) -> dict[str, Any]:
    return core.repair_same_site_evidence(
        conn,
        execute=execute,
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
        normalize_resource_url_records=core.normalize_resource_url_records,
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
