"""Explicit, narrowly scoped completion of introduction plans already fulfilled."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .. import operator_feedback, project_record
from ..persistence.concurrency import write_transaction


def clear_introduced_plans(conn: sqlite3.Connection, *, execute: bool = False) -> dict[str, Any]:
    if execute:
        with write_transaction(conn):
            return _clear_introduced_plans(conn, execute=True)
    return _clear_introduced_plans(conn, execute=False)


def _clear_introduced_plans(conn: sqlite3.Connection, *, execute: bool) -> dict[str, Any]:
    now = project_record.iso_now()
    changes = []
    rows = conn.execute("""
        SELECT d.*, p.name, p.introduced_url
        FROM project_decisions d JOIN projects p USING(project_key)
        WHERE d.kind='plan_introduction' AND d.cleared_at='' AND p.introduced_url<>''
        ORDER BY d.project_key, d.ordinal
    """).fetchall()
    for row in rows:
        entry = json.loads(row["metadata_json"])
        entry["cleared_at"] = row["cleared_at"]
        project = {"introduced_url": row["introduced_url"], "introduction_plans": [entry]}
        if not operator_feedback.clear_introduced_plans(project, cleared_at=now):
            continue
        changes.append({"project_key": row["project_key"], "name": row["name"],
                        "plan_id": entry.get("id", ""), "reason": row["note"],
                        "introduced_url": row["introduced_url"]})
        if execute:
            conn.execute("""
                UPDATE project_decisions SET cleared_at=?, metadata_json=?
                WHERE project_key=? AND ordinal=?
            """, (now, json.dumps(entry, ensure_ascii=False), row["project_key"], row["ordinal"]))
    return {"execute": execute, "cleared_plans": len(changes), "changes": changes}
