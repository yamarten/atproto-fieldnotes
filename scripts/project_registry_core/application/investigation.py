from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .. import registry as core
from .. import replay as replay_core
from .. import storage as investigation_storage


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


def replay(
    conn: sqlite3.Connection,
    *,
    run_id: str | None = None,
    project_key: str | None = None,
) -> dict[str, Any]:
    run = (
        investigation_storage.load_investigation_run(conn, run_id)
        if run_id
        else investigation_storage.latest_investigation_run(conn, core.resolve_project_key_input(conn, str(project_key or "")))
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
