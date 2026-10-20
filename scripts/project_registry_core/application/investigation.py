from __future__ import annotations

import json
import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any

from .. import registry as core
from .. import replay as replay_core
from ..persistence import storage as investigation_storage
from project_registry_core import project_record
from .. import service
from . import service_discovery


def observe_services(conn: sqlite3.Connection, *, execute: bool = False, discovery_limit: int = 50,
                     discovery_budget_seconds: int = 900, progress: Any = None,
                     cancel_requested: Any = None) -> dict[str, Any]:
    """Compose independent budgets without making URL failures prerequisites."""
    if cancel_requested and cancel_requested():
        return {"operation_status": "cancelled"}
    if execute:
        service.ensure_browser_available()
    def phase_progress(phase: str):
        return lambda done, total, detail: progress(done, total, {**detail, "phase": phase}) if progress else None
    monitored = monitor_services(conn, execute=execute, progress=phase_progress("confirmed"),
                                 cancel_requested=cancel_requested)
    if cancel_requested and cancel_requested():
        return {"confirmed": monitored, "operation_status": "cancelled"}
    discovered = service_discovery.investigate(conn, execute=execute, limit=discovery_limit,
        budget_seconds=discovery_budget_seconds, progress=phase_progress("unadopted"), cancel_requested=cancel_requested)
    statuses = {monitored["operation_status"], discovered["operation_status"]}
    # URL failures have their own durable cooldown/retry state. Do not leave a
    # preset checkpoint that disables subsequent scheduled observations.
    return {"confirmed": monitored, "unadopted": discovered,
            "outcome": "partial" if statuses.intersection({"partial", "retry_wait"}) else "completed",
            "operation_status": "cancelled" if "cancelled" in statuses else "completed"}


def verify_service(
    conn: sqlite3.Connection,
    *,
    lane: str,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
    service_url: str = "",
    execute: bool = False,
) -> dict[str, Any]:
    project = core.resolve_single_project(conn, project_key=project_key, seed=seed, seed_type=seed_type)
    return core.verify_service_project(conn, project, lane=lane, service_url=service_url, execute=execute)


def monitor_services(
    conn: sqlite3.Connection, *, execute: bool = False, min_age_days: int = 30,
    limit: int = 20, now: str = "", progress: Any = None, cancel_requested: Any = None,
) -> dict[str, Any]:
    """Select due confirmed service URLs without fetching during a dry run."""
    if not 1 <= min_age_days <= 365 or not 1 <= limit <= 100:
        raise ValueError("min_age_days must be 1..365 and limit must be 1..100")
    current_time = project_record.parse_iso(now or project_record.iso_now())
    if current_time is None:
        raise ValueError("invalid observation selection time")
    cutoff = current_time - dt.timedelta(days=min_age_days)
    candidates = []
    rows = conn.execute("""
        SELECT DISTINCT p.project_key, p.tracking_state, p.state_json, i.value
        FROM projects p JOIN project_identifiers i ON i.project_key = p.project_key
        WHERE p.tracking_state IN ('active','stable','stalled','out_of_scope')
          AND i.kind = 'service_url' AND i.classification = 'confirmed'
    """).fetchall()
    for row in rows:
        state = json.loads(row["state_json"])
        observations = state.get("evidence", {}).get("service_verification_results", [])
        times = [service.observation_time(item) for item in observations
                 if item.get("lane") == "browser" and item.get("requested_url", "").rstrip("/") == row["value"].rstrip("/")]
        latest = max((value for value in times if value), default=None)
        if latest is None or latest <= cutoff:
            candidates.append({"project_key": row["project_key"], "service_url": row["value"],
                               "tracking_state": row["tracking_state"],
                               "last_attempt_at": latest.isoformat() if latest else ""})
    candidates.sort(key=lambda item: (item["last_attempt_at"], item["project_key"], item["service_url"]))
    selected = candidates[:limit]
    results = []
    for index, item in enumerate(selected if execute else []):
        if cancel_requested and cancel_requested():
            break
        project = project_record.fetch_project(conn, item["project_key"])
        if not project or project.get("tracking_state") not in {"active", "stable", "stalled", "out_of_scope"} or item["service_url"] not in project.get("service_urls", []):
            results.append({**item, "status": "skipped"})
            continue
        try:
            result = core.verify_service_project(conn, project, lane="browser", service_url=item["service_url"], execute=True)
            saved = project_record.fetch_project(conn, item["project_key"])
            observation = next((service.observation_summary(value) for value in saved.get("service_verification_results", [])
                                if value.get("lane") == "browser" and value.get("requested_url") == item["service_url"]), {})
            results.append({**item, "status": "failed" if result["bundle"].get("failures") else "observed",
                            "run_id": result["run_id"], "observation": observation})
        except project_record.StaleProjectWriteError:
            conn.rollback()
            results.append({**item, "status": "conflicted"})
        if progress:
            progress(index + 1, len(selected), {**item, "unit": "service"})
    statuses = {item["status"] for item in results}
    return {"mode": "execute" if execute else "dry_run", "eligible_count": len(rows),
            "due_count": len(candidates), "remaining_due_count": max(0, len(candidates) - len(results)) if execute else len(candidates),
            "min_age_days": min_age_days, "limit": limit, "selected": selected, "results": results,
            "operation_status": "cancelled" if cancel_requested and cancel_requested() else
                                "retry_wait" if "conflicted" in statuses else "partial" if "failed" in statuses else "completed"}


def replay(
    conn: sqlite3.Connection,
    *,
    run_id: str | None = None,
    project_key: str | None = None,
) -> dict[str, Any]:
    run = (
        investigation_storage.load_investigation_run(conn, run_id)
        if run_id
        else investigation_storage.latest_investigation_run(conn, project_record.resolve_project_key_input(conn, str(project_key or "")))
    )
    if run is None:
        raise ValueError("investigation run not found")
    payload = replay_core.replay_and_diff(run["input_project"], run["bundle"], run["evaluation"], core.replay_dependencies())
    payload["run_id"] = run["run_id"]
    return payload


def benchmark(*, path: Path) -> tuple[dict[str, Any], int]:
    summary = replay_core.benchmark_cases(core.load_benchmark_cases(path), core.replay_dependencies())
    return summary, 1 if summary["regression_count"] else 0


def export_fixture(conn: sqlite3.Connection, *, run_id: str, case_id: str = "") -> str:
    run = investigation_storage.load_investigation_run(conn, run_id)
    if run is None:
        raise ValueError(f"investigation run not found: {run_id}")
    fixture = core.sanitize_fixture_value({
        "case_id": case_id or run["project_key"],
        "fixture_metadata": {
            "sanitization_version": 2,
            "requires_manual_review_before_commit": True,
        },
        "input_project": run["input_project"],
        "bundle": run["bundle"].to_dict(),
        "expected_evaluation": run["evaluation"],
    })
    return json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
