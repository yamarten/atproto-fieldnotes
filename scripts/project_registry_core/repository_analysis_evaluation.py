from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from . import llm_model_evaluation as llm_evaluation
from . import repository_analysis


CORPUS_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
REQUIRED_OUTPUT_FIELDS = {"summary", "observations", "capabilities", "collections"}


def corpus_content_hash(corpus: dict[str, Any]) -> str:
    return llm_evaluation.sha256_json({
        "schema_version": corpus.get("schema_version"),
        "cases": corpus.get("cases", []),
    })


def _analysis_value(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("value") if isinstance(record.get("value"), dict) else {}
    result = value.get("result") if isinstance(value.get("result"), dict) else {}
    return {
        key: copy.deepcopy(result.get(key))
        for key in ("summary", "observations", "capabilities", "collections")
        if key in result
    }


def export_corpus(db_path: Path, *, limit: int = 12) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    resolved = db_path.expanduser().resolve()
    uri = f"file:{urllib.parse.quote(str(resolved))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT run_id, project_key, started_at, completed_at, output_project_json, bundle_json "
            "FROM investigation_runs WHERE bundle_json LIKE '%repository_analysis_material%' "
            "ORDER BY completed_at DESC, run_id DESC"
        ).fetchall()
    finally:
        conn.close()

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        try:
            project = json.loads(str(row["output_project_json"]))
            bundle = json.loads(str(row["bundle_json"]))
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(project, dict) or not isinstance(bundle, dict):
            continue
        records = [item for item in bundle.get("records", []) if isinstance(item, dict)]
        reference_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
        for record in records:
            if record.get("kind") != "repository_analysis":
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            identity = (str(value.get("repo_url") or ""), str(value.get("material_hash") or ""))
            reference_by_identity[identity] = _analysis_value(record)
        for record in records:
            if record.get("kind") != "repository_analysis_material":
                continue
            material = record.get("value") if isinstance(record.get("value"), dict) else {}
            repo_url = str(material.get("repo_url") or "")
            material_hash = str(material.get("material_hash") or "")
            identity = (str(row["project_key"]), repo_url, material_hash)
            if not repo_url or not material_hash or identity in seen:
                continue
            seen.add(identity)
            project_context = {
                "project_key": str(row["project_key"]),
                "name": str(project.get("name") or ""),
                "resolved_nsids": [
                    str(value) for value in project.get("resolved_nsids", []) if str(value)
                ][:24],
            }
            prompt = repository_analysis.build_prompt(project_context, material)
            candidates.append({
                "case_id": f"{row['project_key']}:{material_hash.removeprefix('sha256:')[:12]}",
                "project_key": str(row["project_key"]),
                "project_name": str(project.get("name") or ""),
                "repo_url": repo_url,
                "provider": str(material.get("provider") or "unknown"),
                "source_run_id": str(row["run_id"]),
                "source_completed_at": str(row["completed_at"]),
                "material_hash": material_hash,
                "source_revision": str(material.get("source_revision") or ""),
                "prompt_chars": len(prompt),
                "prompt_bytes": len(prompt.encode("utf-8")),
                "prompt_bucket": llm_evaluation.prompt_bucket(len(prompt)),
                "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "project_context": project_context,
                "material": copy.deepcopy(material),
                "reference_result": reference_by_identity.get((repo_url, material_hash), {}),
                "reference_is_gold": False,
            })

    selected: list[dict[str, Any]] = []
    selected_projects: set[str] = set()
    for case in candidates:
        if case["project_key"] in selected_projects:
            continue
        selected.append(case)
        selected_projects.add(case["project_key"])
        if len(selected) >= limit:
            break
    for case in candidates:
        if len(selected) >= limit:
            break
        if case not in selected:
            selected.append(case)
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "created_at": llm_evaluation.iso_now(),
        "source": {"kind": "investigation_runs", "db_path": str(resolved), "read_only": True},
        "selection": {
            "candidate_count": len(candidates),
            "limit": limit,
            "selected_count": len(selected),
            "one_latest_material_per_project_first": True,
        },
        "cases": selected,
    }


def _locator_key(value: Any) -> tuple[str, int, str]:
    if not isinstance(value, dict):
        return ("", 0, "")
    return (
        str(value.get("path") or ""),
        max(0, int(value.get("line") or 0)),
        str(value.get("json_pointer") or ""),
    )


def validate_output(payload: Any, material: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    unsupported_locators: list[dict[str, Any]] = []
    unsupported_collections: list[str] = []
    if not isinstance(payload, dict):
        return {
            "valid": False,
            "errors": ["invalid_type:top_level:object"],
            "unsupported_locators": [],
            "unsupported_collections": [],
            "normalized_result": None,
        }
    missing = sorted(REQUIRED_OUTPUT_FIELDS - set(payload))
    if missing:
        errors.append(f"missing_fields:{','.join(missing)}")
    if "summary" in payload and not isinstance(payload.get("summary"), str):
        errors.append("invalid_type:summary:string")
    for field in ("observations", "capabilities", "collections"):
        if field in payload and not isinstance(payload.get(field), list):
            errors.append(f"invalid_type:{field}:list")

    locator_keys = set(repository_analysis.supported_locator_keys(material))
    for field in ("observations", "capabilities", "collections"):
        values = payload.get(field) if isinstance(payload.get(field), list) else []
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                errors.append(f"invalid_type:{field}[{index}]:object")
                continue
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"missing_evidence:{field}[{index}]")
                continue
            for locator in evidence:
                if _locator_key(locator) not in locator_keys:
                    unsupported_locators.append({"field": field, "index": index, "locator": locator})
    for item in payload.get("collections", []) if isinstance(payload.get("collections"), list) else []:
        if not isinstance(item, dict):
            continue
        nsid = str(item.get("nsid") or "")
        if nsid and not repository_analysis.material_supports_collection_nsid(material, nsid):
            unsupported_collections.append(nsid)
    if unsupported_locators:
        errors.append(f"unsupported_locators:{len(unsupported_locators)}")
    if unsupported_collections:
        errors.append(f"unsupported_collections:{len(set(unsupported_collections))}")

    normalized: dict[str, Any] | None = None
    try:
        normalized = repository_analysis.normalize_result(
            material,
            payload,
            {"model": "evaluation", "transport": "evaluation", "prompt_version": repository_analysis.POLICY_VERSION},
            "1970-01-01T00:00:00+00:00",
        )
    except Exception as exc:
        errors.append(f"normalization_failed:{type(exc).__name__}:{exc}")
    return {
        "valid": not errors,
        "errors": errors,
        "unsupported_locators": unsupported_locators,
        "unsupported_collections": sorted(set(unsupported_collections)),
        "normalized_result": normalized,
    }


def checkpoint_report(
    corpus: dict[str, Any],
    model_spec: dict[str, Any],
    results: list[dict[str, Any]],
    case_ids: set[str] | None = None,
) -> dict[str, Any]:
    cases = [case for case in corpus.get("cases", []) if isinstance(case, dict)]
    if case_ids:
        cases = [case for case in cases if str(case.get("case_id") or "") in case_ids]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": llm_evaluation.iso_now(),
        "status": "running",
        "corpus_hash": corpus_content_hash(corpus),
        "corpus_case_count": len(cases),
        "model_spec": copy.deepcopy(model_spec),
        "reasoning_observation": llm_evaluation.reasoning_observation(model_spec),
        "results": list(results),
    }


def run_corpus(
    corpus: dict[str, Any],
    *,
    run_llm: Callable[[str], tuple[dict[str, Any], dict[str, Any]]],
    model_spec: dict[str, Any],
    repeat: int = 1,
    case_ids: set[str] | None = None,
    root: Path | None = None,
    progress_callback: Callable[[list[dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:
    if int(corpus.get("schema_version") or 0) != CORPUS_SCHEMA_VERSION:
        raise ValueError("unsupported repository analysis corpus schema")
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    cases = [case for case in corpus.get("cases", []) if isinstance(case, dict)]
    available = {str(case.get("case_id") or "") for case in cases}
    if case_ids:
        missing = sorted(case_ids - available)
        if missing:
            raise ValueError(f"unknown corpus case IDs: {', '.join(missing)}")
        cases = [case for case in cases if str(case.get("case_id") or "") in case_ids]
    results: list[dict[str, Any]] = []
    for case in cases:
        project_context = case.get("project_context") if isinstance(case.get("project_context"), dict) else {}
        material = case.get("material") if isinstance(case.get("material"), dict) else {}
        prompt = repository_analysis.build_prompt(project_context, material)
        for repeat_index in range(repeat):
            started = time.perf_counter()
            result: dict[str, Any] = {
                "case_id": str(case.get("case_id") or ""),
                "repeat_index": repeat_index,
                "status": "failed",
                "prompt_chars": len(prompt),
                "prompt_bytes": len(prompt.encode("utf-8")),
                "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            }
            try:
                payload, meta = run_llm(prompt)
                result.update({
                    "status": "success",
                    "payload": payload,
                    "meta": meta,
                    "validation": validate_output(payload, material),
                    "reference_result": copy.deepcopy(case.get("reference_result", {})),
                    "reference_is_gold": False,
                })
            except Exception as exc:
                result.update({"error_type": type(exc).__name__, "error": str(exc)})
            finally:
                result["duration_ms"] = max(0, round((time.perf_counter() - started) * 1000))
            results.append(result)
            if progress_callback:
                progress_callback(results)
    successes = [result for result in results if result.get("status") == "success"]
    valid = [result for result in successes if result.get("validation", {}).get("valid")]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": llm_evaluation.iso_now(),
        "status": "complete",
        "corpus_hash": corpus_content_hash(corpus),
        "corpus_case_count": len(cases),
        "git": llm_evaluation.git_state(root) if root else {"commit": "", "dirty": None},
        "model_spec": copy.deepcopy(model_spec),
        "reasoning_observation": llm_evaluation.reasoning_observation(model_spec),
        "summary": {
            "request_count": len(results),
            "success_count": len(successes),
            "failure_count": len(results) - len(successes),
            "valid_count": len(valid),
            "unsupported_locator_result_count": sum(
                bool(result.get("validation", {}).get("unsupported_locators")) for result in successes
            ),
            "unsupported_collection_result_count": sum(
                bool(result.get("validation", {}).get("unsupported_collections")) for result in successes
            ),
            "total_duration_ms": sum(int(result.get("duration_ms") or 0) for result in results),
        },
        "results": results,
    }


def _result_keys(report: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        (str(item.get("case_id") or ""), int(item.get("repeat_index") or 0))
        for item in report.get("results", []) if isinstance(item, dict)
    }


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    for name, report in (("baseline", baseline), ("candidate", candidate)):
        if int(report.get("schema_version") or 0) != REPORT_SCHEMA_VERSION:
            raise ValueError(f"unsupported {name} repository analysis report schema")
        if report.get("status") != "complete":
            raise ValueError(f"{name} repository analysis report is not complete")
    issues: list[str] = []
    if baseline.get("corpus_hash") != candidate.get("corpus_hash"):
        issues.append("corpus_hash_differs")
    if _result_keys(baseline) != _result_keys(candidate):
        issues.append("case_or_repeat_set_differs")
    baseline_summary = baseline.get("summary") if isinstance(baseline.get("summary"), dict) else {}
    candidate_summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
    metrics = (
        "success_count", "valid_count", "unsupported_locator_result_count",
        "unsupported_collection_result_count", "total_duration_ms",
    )
    comparison = {
        metric: {
            "baseline": baseline_summary.get(metric),
            "candidate": candidate_summary.get(metric),
            "difference": (
                candidate_summary[metric] - baseline_summary[metric]
                if isinstance(baseline_summary.get(metric), (int, float))
                and isinstance(candidate_summary.get(metric), (int, float))
                else None
            ),
        }
        for metric in metrics
    }
    regressions: list[str] = []
    for metric in ("success_count", "valid_count"):
        if isinstance(comparison[metric]["difference"], (int, float)) and comparison[metric]["difference"] < 0:
            regressions.append(f"{metric}_decreased")
    for metric in ("unsupported_locator_result_count", "unsupported_collection_result_count"):
        if isinstance(comparison[metric]["difference"], (int, float)) and comparison[metric]["difference"] > 0:
            regressions.append(f"{metric}_increased")
    return {
        "schema_version": 1,
        "created_at": llm_evaluation.iso_now(),
        "baseline": baseline.get("model_spec", {}),
        "candidate": candidate.get("model_spec", {}),
        "comparison_issues": issues,
        "metric_comparison": comparison,
        "automatic_regressions": regressions,
        "remaining_checks": [
            "summary_capabilities_and_observations_not_reviewed_by_operator",
            "downstream_triage_not_compared",
        ],
        "automatic_checks_pass": not issues and not regressions,
    }
