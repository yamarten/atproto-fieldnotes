"""Explicit, conservative migration of legacy page/repository graph observations."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from . import identifiers, repository_trust, repo_urls, runtime_view, storage, trust_graph


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _records(conn: sqlite3.Connection, project_key: str) -> list[dict[str, Any]]:
    return [{
        "relation": {"subject": {"type": row["subject_type"], "value": row["subject_value"]},
                     "predicate": row["predicate"], "object": {"type": row["object_type"], "value": row["object_value"]},
                     "locator": row["locator"], "method": row["method"]},
        "source": row["source"], "classification": row["classification"],
        "context": json.loads(row["context_json"]), "observed_at": row["observed_at"],
    } for row in conn.execute("SELECT * FROM project_relations WHERE project_key=? ORDER BY ordinal", (project_key,))]


def _pair_matches(item: dict[str, Any], edge: dict[str, Any]) -> bool:
    return (identifiers.normalize_url(str(item.get("service_url") or "")) == edge["subject"]["value"]
            and repo_urls.canonical_repo_url(str(item.get("repo_url") or "")) == edge["object"]["value"])


def _observation_source(edge: dict[str, Any]) -> str:
    """The old README writer used both owner/repo and full repository URLs."""
    source = edge["source"]
    if edge["method"] != "readme_link" or not source.startswith("github_readme:"):
        return source
    value = source.removeprefix("github_readme:")
    parts = value.split("/")
    if len(parts) == 2 and all(repo_urls.STRICT_REPO_SEGMENT_RE.fullmatch(part) for part in parts):
        return "github_readme:https://github.com/" + value
    return source


def _llm_evidence(project: dict[str, Any], edge: dict[str, Any]) -> dict[str, Any] | None:
    source = project.get("llm_source") or {}
    if not edge.get("observed_at") or source.get("generated_at") != edge["observed_at"]:
        return None
    pairs = (source.get("evidence") or {}).get("repository_link_candidates", [])
    pair = next((item for item in pairs if isinstance(item, dict) and _pair_matches(item, edge)), None)
    if pair is None:
        return None
    assessment = next((item for item in (project.get("llm_output") or {}).get("repository_link_assessments", [])
                       if isinstance(item, dict) and _pair_matches(item, edge)
                       and item.get("relationship") == "same_project"), None)
    if assessment is None:
        return None
    return {"relationship": "same_project", "reason": assessment.get("reason", ""),
            "input_pair": pair,
            "llm_generated_at": source["generated_at"]}


def build_plan(conn: sqlite3.Connection) -> dict[str, Any]:
    entries = []
    for row in conn.execute("SELECT project_key, state_json FROM projects ORDER BY project_key"):
        first_entry = len(entries)
        state = json.loads(row["state_json"])
        evidence = state.get("evidence") or {}
        records = _records(conn, row["project_key"])
        projected = repository_trust.project_edges({"relation_evidence": records})
        for edge in trust_graph.normalize_edges((evidence.get("trust_graph") or {}).get("edges", [])):
            if not repository_trust.is_repository_edge(edge):
                continue
            # Locator normalization differed in the old writer (notably README).
            matches = [current for current in projected
                       if all(current[key] == edge[key] for key in ("subject", "object", "predicate", "method"))
                       and _observation_source(current) == _observation_source(edge)]
            exact_source = [current for current in matches if current["source"] == edge["source"]]
            if exact_source:
                matches = exact_source
            matched = next((current for current in matches if current["locator"] == edge["locator"]), None)
            if matched is None and len(matches) == 1:
                matched = matches[0]
            entry = {"project_key": row["project_key"], "edge": edge, "category": "needs_review"}
            if matched is not None:
                entry.update(category="current_relation", current_edge=matched)
                if matched["source"] != edge["source"]:
                    entry["basis"] = "readme_source_format_equivalence"
            elif edge["method"] == "llm_same_project":
                material = _llm_evidence(evidence, edge)
                run_id = ""
                if material is None:
                    for operator, order in ((">=", "ASC"), ("<", "DESC")):
                        run = conn.execute(
                            f"SELECT run_id, started_at, completed_at, output_project_json FROM investigation_runs "
                            f"WHERE project_key=? AND completed_at{operator}? ORDER BY completed_at {order} LIMIT 1",
                            (row["project_key"], edge["observed_at"]),
                        ).fetchone()
                        if run is None or not (run["started_at"] <= edge["observed_at"] <= run["completed_at"]):
                            continue
                        snapshot = storage.decode_history_json(run["output_project_json"], context="repository trust migration")
                        material = _llm_evidence(snapshot, edge) if isinstance(snapshot, dict) else None
                        if material:
                            run_id = run["run_id"]
                            break
                if material:
                    projected_project: dict[str, Any] = {}
                    repository_trust.record_observation(projected_project, {
                        **edge, "context": {**edge["context"], **material, "migration_run_id": run_id},
                    })
                    entry.update(category="reconstructable_judgment", relation=projected_project["relation_evidence"][0])
            entries.append(entry)
        # Recovered forward judgments may also justify a saved homepage pair.
        # Include that dependency in the reviewed plan, not in a later implicit pass.
        project_entries = entries[first_entry:]
        restored = [entry["relation"] for entry in project_entries if "relation" in entry]
        if restored:
            after_restore = repository_trust.project_edges({"relation_evidence": [*records, *restored]})
            for entry in project_entries:
                if entry["category"] != "needs_review" or entry["edge"]["method"] != "repository_homepage_pair":
                    continue
                matched = next((edge for edge in after_restore
                                if trust_graph.edge_key(edge) == trust_graph.edge_key(entry["edge"])), None)
                if matched:
                    entry.update(category="reconstructable_judgment", current_edge=matched,
                                 basis="homepage_pair_after_judgment_restoration")
    content = {"version": 1, "entries": entries, "counts": dict(Counter(item["category"] for item in entries))}
    content["plan_digest"] = _digest(content)
    return content


def apply_plan(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, int]:
    """Call within a write transaction. Reject a changed DB or an edited plan."""
    current = build_plan(conn)
    if current != plan:
        raise ValueError("Migration plan no longer matches the database; regenerate and review the dry-run.")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in plan["entries"]:
        if entry["category"] != "needs_review":
            grouped.setdefault(entry["project_key"], []).append(entry)
    removed = 0
    for key, entries in grouped.items():
        state = json.loads(conn.execute("SELECT state_json FROM projects WHERE project_key=?", (key,)).fetchone()[0])
        graph = state["evidence"]["trust_graph"]
        keys = {trust_graph.edge_key(item["edge"]) for item in entries}
        retained = [edge for edge in graph.get("edges", []) if trust_graph.edge_key(edge) not in keys]
        removed += len(graph.get("edges", [])) - len(retained)
        graph["edges"] = retained
        for entry in entries:
            if "relation" not in entry:
                continue
            relation_row = runtime_view.relation_rows({"relation_evidence": [entry["relation"]]})[0]
            ordinal = conn.execute("SELECT COALESCE(MAX(ordinal), -1)+1 FROM project_relations WHERE project_key=?", (key,)).fetchone()[0]
            item = relation_row
            conn.execute("INSERT INTO project_relations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                key, ordinal, item["subject_type"], item["subject_value"], item["predicate"], item["object_type"],
                item["object_value"], item["classification"], item["source"], item["locator"], item["method"],
                json.dumps(item["context"], ensure_ascii=False), item["observed_at"],
            ))
        conn.execute("UPDATE projects SET state_json=? WHERE project_key=?", (json.dumps(state, ensure_ascii=False), key))
    return {"removed_legacy_edges": removed, "pending_review": plan["counts"].get("needs_review", 0)}


def run(path: Path, *, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(path.resolve().as_uri() + ("?mode=rw" if plan is not None else "?mode=ro"), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if plan is None:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            return build_plan(conn)
        conn.execute("BEGIN IMMEDIATE")
        result = apply_plan(conn, plan)
        conn.commit()
        return result
    finally:
        conn.close()
