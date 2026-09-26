#!/usr/bin/env python3
"""Read-only report of retained enrich history; never collects or calls an LLM."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from project_registry_core import enrich_observations as obs
from project_registry_core import storage, llm_transport


def section(observations: dict, key: str) -> Any:
    value = observations.get(key, {})
    return value.get("data") if value.get("complete") else None


def planning_conditions(planning: dict, execution: dict) -> dict:
    request = planning.get("request", {})
    return {"request": {key: request.get(key) for key in (
        "transport", "model", "endpoint_hash", "requested_api_style", "api_style",
        "reasoning_effort", "max_output_tokens", "timeout_seconds", "prompt_version",
        "request_variant", "temperature")},
        "search": {key: value for key, value in execution.items() if key != "operations"},
        "policy": planning.get("policy")}


def summarize(rows: list[dict], *, details: bool = False) -> dict:
    coverage, applications, reuse, changes = Counter(), Counter(), Counter(), Counter()
    llm, operations, collectors, request_groups, search_operations = {}, {}, {}, {}, {}
    pairs, previous = [], {}
    outcomes = Counter()
    retained_bytes = 0
    for row in sorted(rows, key=lambda r: (r["completed_at"], r["run_id"])):
        evaluation = storage.decode_history_json(row["evaluation_json"])
        metrics = storage.normalize_run_metrics(evaluation.get(storage.RUN_METRICS_KEY))
        observations = evaluation.get(obs.STORAGE_KEY, {})
        retained_bytes += len(obs.json_bytes(observations)) if observations else 0
        coverage["runs_with_observations"] += bool(observations)
        coverage["runs_without_llm_metrics"] += not bool(metrics)
        coverage["llm_request_rows_omitted"] += metrics.get("llm_request_rows_omitted", 0)
        applications[row["application_status"]] += 1
        for key in obs.SECTION_LIMITS:
            saved = observations.get(key, {})
            coverage[key + ("_complete" if saved.get("complete") else "_incomplete" if saved else "_missing")] += 1
            coverage[key + "_redacted"] += bool(saved.get("redacted"))
            coverage[key + "_omitted"] += bool(saved.get("omitted"))
        triage = metrics.get("triage_reuse", {})
        reuse[str(triage.get("reason") or "unrecorded")] += 1
        change = section(observations, "triage_changes") or {}
        for key in change.get("changed_evidence_fields", []):
            changes["evidence." + key] += 1
        for key in change.get("changed_components", []):
            changes[key] += 1
        for purpose, summary in metrics.get("llm_request_summary", {}).items():
            group = llm.setdefault(purpose, Counter())
            group.update({key: value for key, value in summary.items() if type(value) is int})
        for request in metrics.get("llm_requests", []):
            conditions = {key: request.get(key) for key in (
                "purpose", "model", "transport", "endpoint_hash", "api_style", "requested_api_style",
                "reasoning_effort", "max_output_tokens", "timeout_seconds", "prompt_version", "prompt_suffix_hash")}
            key = hashlib.sha256(obs.json_bytes(conditions)).hexdigest()
            group = request_groups.setdefault(key, {"conditions": conditions, "retained_request_count": 0,
                "duration_ms": 0, "token_totals": Counter(), "token_reported_counts": Counter()})
            group["retained_request_count"] += 1
            group["duration_ms"] += request.get("duration_ms", 0)
            for name, count in llm_transport.token_usage(request.get("usage")).items():
                group["token_totals"][name] += count
                group["token_reported_counts"][name] += 1
        for name, timer in (section(observations, "operations") or {}).items():
            operations.setdefault(name, Counter()).update(timer)
        for outcome in section(observations, "search_outcomes") or []:
            novelty = "known_at_start" if outcome["known_at_start"] else "initial_collection" if outcome["known_before_search"] else "first_seen_in_search"
            outcomes[novelty] += 1
            if outcome["confirmed_at_end"]:
                outcomes[novelty + "_confirmed_at_end"] += 1
        bundle = storage.decode_history_json(row["bundle_json"])
        for result in bundle.get("collector_results", []):
            key = result["source"] + "/" + result["operation"]
            group = collectors.setdefault(key, {"records": 0, "request_count_recorded": 0,
                "cache_hits_recorded": 0, "count_basis": "legacy_mixed_not_all_measured", "statuses": Counter()})
            group["records"] += 1
            group["request_count_recorded"] += result.get("request_count", 0)
            group["cache_hits_recorded"] += result.get("cache_hits", 0)
            group["statuses"][result["status"]] += 1
        planning = section(observations, "planning")
        execution = section(observations, "search_execution")
        for operation in (execution or {}).get("operations", []):
            group = search_operations.setdefault(operation["source"], Counter())
            group["operations"] += 1
            group["failed_operations"] += operation.get("status") == "failed"
            for name in ("attempts", "retry_wait_ms", "duration_ms"):
                group[name] += operation.get(name, 0)
        prior = previous.get(row["project_key"])
        if prior:
            old_row, old_plan, old_execution = prior
            pair = {"project_key": row["project_key"], "previous_run": old_row["run_id"], "run": row["run_id"],
                    "comparable": bool(planning and old_plan and execution and old_execution
                        and planning.get("status") == old_plan.get("status") == "success"
                        and old_row["application_status"] == row["application_status"] == "applied")}
            if pair["comparable"]:
                pair.update(same_conditions=planning_conditions(planning, execution) == planning_conditions(old_plan, old_execution),
                            same_input=planning.get("prompt") == old_plan.get("prompt"),
                            same_plan=planning.get("plan") == old_plan.get("plan"),
                            same_search_queries=planning.get("search_queries") == old_plan.get("search_queries"))
            pairs.append(pair)
        previous[row["project_key"]] = row, planning, execution
    for purpose, summary in llm.items():
        for name in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens"):
            summary[name + "_missing_count"] = summary["count"] - summary.get(name + "_reported_count", 0)
    ids = [r["run_id"] for r in rows]
    result = {"version": 1, "run_count": len(rows), "project_count": len(previous),
              "selection": {"run_ids_sha256": hashlib.sha256(obs.json_bytes(ids)).hexdigest(),
                            "newest_run": ids[0] if ids else None, "oldest_run": ids[-1] if ids else None},
              "application_status": applications, "coverage": coverage,
              "observation_logical_bytes": retained_bytes,
              "llm_endpoint_reported": llm, "operation_elapsed_including_wait_ms": operations,
              "llm_retained_requests_by_settings": list(request_groups.values()),
              "search_collector_attempts_and_wait": search_operations,
              "collector_existing_counts": collectors, "triage_reuse_reasons": reuse,
              "triage_changed_fields": changes, "candidate_occurrences": outcomes,
              "consecutive_pairs": {"total": len(pairs),
                  "comparable": sum(p["comparable"] for p in pairs),
                  "same_conditions": sum(p.get("same_conditions", False) for p in pairs),
                  "same_conditions_and_input": sum(p.get("same_conditions", False) and p.get("same_input", False) for p in pairs),
                  "same_conditions_and_input_changed_search_queries": sum(p.get("same_conditions", False) and p.get("same_input", False) and not p.get("same_search_queries", True) for p in pairs),
                  "same_conditions_and_input_changed_plan": sum(p.get("same_conditions", False) and p.get("same_input", False) and not p.get("same_plan", True) for p in pairs)},
              "limitations": ["Missing token counts are not zero; totals cover reported requests only.",
                  "Operation times can overlap and include internal cache, retry, and parsing work.",
                  "Candidate occurrences include repeated URLs/handles, not unique discoveries or causal adoption gains.",
                  "First seen means absent from selected current fields before search, not absent from all historical data.",
                  "No counterfactual search was executed; old-plan recall and quality are unknown.",
                  "Same conditions means recorded settings only; credentials, backend defaults, and command environments are not fixed.",
                  "History does not certify weekly job completion; consult existing checkpoint/job records.",
                  "Older version-2 request summaries may already have lost requests beyond the 16-row cap."]}
    if details:
        result["pairs"] = pairs
        result["run_ids"] = ids
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--since", default="", help="Inclusive completed_at lower bound (ISO timestamp)")
    parser.add_argument("--until", default="", help="Exclusive completed_at upper bound (ISO timestamp)")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--run-id", help="Inspect saved observation sections and search evidence for one run")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    with sqlite3.connect(args.db_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        clauses, parameters = [], []
        for predicate, value in (("completed_at >= ?", args.since), ("completed_at < ?", args.until), ("run_id = ?", args.run_id)):
            if value:
                clauses.append(predicate)
                parameters.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        # Select IDs first so SQLite need not sort large compressed bundles.
        rows = [dict(row) for row in conn.execute(
            "SELECT run_id, project_key, completed_at, application_status, evaluation_json, bundle_json FROM investigation_runs"
            " WHERE run_id IN (SELECT run_id FROM investigation_runs" + where
            + " ORDER BY completed_at DESC, run_id DESC LIMIT ?) ORDER BY completed_at DESC, run_id DESC", (*parameters, args.limit))]
        if args.run_id:
            if not rows:
                parser.error("run not found")
            row = rows[0]
            evaluation = storage.decode_history_json(row.pop("evaluation_json"))
            bundle = storage.decode_history_json(row.pop("bundle_json"))
            result = {**row, "observations": evaluation.get(obs.STORAGE_KEY, {}),
                      "metrics": storage.normalize_run_metrics(evaluation.get(storage.RUN_METRICS_KEY)),
                      "search_records": [r for r in bundle.get("records", []) if r.get("source", "").split(":", 1)[0] in {"github_search", "bsky_actor_search", "bsky_post_search", "bsky_author_feed"}],
                      "failures": bundle.get("failures", [])}
        else:
            result = summarize(rows, details=args.details)
            result["selection"].update(since=args.since, until=args.until, limit=args.limit,
                                      limit_reached=len(rows) == args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
