from __future__ import annotations

import copy
import hashlib
import re
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterator

from . import llm_model_evaluation as llm_evaluation
from ..transport import llm_transport
from ..repositories import repository_analysis
from ..persistence import storage


CORPUS_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
REQUIRED_OUTPUT_FIELDS = {"summary", "summary_evidence", "observations", "capabilities", "collections"}
EVIDENCE_ID_IN_TEXT_RE = re.compile(r"(?<![A-Za-z0-9_])E\d{2}(?![A-Za-z0-9_])")


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
        for key in ("summary", "summary_evidence", "observations", "capabilities", "collections")
        if key in result
    }


def _corpus_source_rows(db_path: Path) -> Iterator[sqlite3.Row]:
    uri = f"file:{urllib.parse.quote(str(db_path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield from conn.execute(
            "SELECT run_id, project_key, started_at, completed_at, output_project_json, bundle_json "
            "FROM investigation_runs ORDER BY completed_at DESC, run_id DESC"
        )
    finally:
        conn.close()


def export_corpus(db_path: Path, *, limit: int = 12) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    resolved = db_path.expanduser().resolve()
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in _corpus_source_rows(resolved):
        project = storage.decode_history_json(
            row["output_project_json"],
            context=f"investigation run {row['run_id']} output_project_json",
        )
        bundle = storage.decode_history_json(
            row["bundle_json"],
            context=f"investigation run {row['run_id']} bundle_json",
        )
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
            "filtered_claims": {},
            "normalized_result": None,
        }
    missing = sorted(REQUIRED_OUTPUT_FIELDS - set(payload))
    if missing:
        errors.append(f"missing_fields:{','.join(missing)}")
    if "summary" in payload and not isinstance(payload.get("summary"), str):
        errors.append("invalid_type:summary:string")
    summary_text = str(payload.get("summary") or "")
    if summary_text and EVIDENCE_ID_IN_TEXT_RE.search(summary_text):
        errors.append("evidence_id_in_text:summary")
    if summary_text and not re.search(r"[ぁ-んァ-ン一-龯]", summary_text):
        errors.append("missing_japanese_text:summary")
    if "summary_evidence" in payload and not isinstance(payload.get("summary_evidence"), list):
        errors.append("invalid_type:summary_evidence:list")
    for field in ("observations", "capabilities", "collections"):
        if field in payload and not isinstance(payload.get(field), list):
            errors.append(f"invalid_type:{field}:list")

    evidence_ids = repository_analysis.supported_evidence_ids(material)
    observation_candidates = repository_analysis.observation_candidate_catalog(material)
    collection_candidates = repository_analysis.collection_candidate_catalog(material)
    summary_evidence = payload.get("summary_evidence") if isinstance(payload.get("summary_evidence"), list) else []
    if not summary_evidence:
        errors.append("missing_evidence:summary")
    for evidence_id in summary_evidence:
        if not isinstance(evidence_id, str) or evidence_id not in evidence_ids:
            unsupported_locators.append({"field": "summary_evidence", "index": 0, "evidence_id": evidence_id})
    for index, item in enumerate(payload.get("capabilities", []) if isinstance(payload.get("capabilities"), list) else []):
        if not isinstance(item, dict):
            errors.append(f"invalid_type:capabilities[{index}]:object")
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"invalid_type:capabilities[{index}].text:string")
        elif EVIDENCE_ID_IN_TEXT_RE.search(text):
            errors.append(f"evidence_id_in_text:capabilities[{index}]")
        elif not re.search(r"[ぁ-んァ-ン一-龯]", text):
            errors.append(f"missing_japanese_text:capabilities[{index}]")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"missing_evidence:capabilities[{index}]")
            continue
        for evidence_id in evidence:
            if not isinstance(evidence_id, str) or evidence_id not in evidence_ids:
                unsupported_locators.append({"field": "capabilities", "index": index, "evidence_id": evidence_id})
    for index, item in enumerate(payload.get("observations", []) if isinstance(payload.get("observations"), list) else []):
        if not isinstance(item, dict):
            errors.append(f"invalid_type:observations[{index}]:object")
            continue
        candidate_id = str(item.get("candidate_id") or "")
        if candidate_id not in observation_candidates:
            errors.append(f"unsupported_observation_candidate:observations[{index}]:{candidate_id}")
    for item in payload.get("collections", []) if isinstance(payload.get("collections"), list) else []:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "")
        candidate = collection_candidates.get(candidate_id)
        if not candidate:
            raw_nsid = str(item.get("nsid") or "")
            unsupported_collections.append(raw_nsid or f"candidate:{candidate_id}")
    if unsupported_locators:
        errors.append(f"unsupported_locators:{len(unsupported_locators)}")
    if unsupported_collections:
        errors.append(f"unsupported_collections:{len(set(unsupported_collections))}")

    normalized: dict[str, Any] | None = None
    filtered_claims: dict[str, int] = {}
    try:
        normalized = repository_analysis.normalize_result(
            material,
            payload,
            {"model": "evaluation", "transport": "evaluation", "prompt_version": repository_analysis.POLICY_VERSION},
            "1970-01-01T00:00:00+00:00",
        )
        raw_observations = [
            item for item in payload.get("observations", [])
            if isinstance(payload.get("observations"), list) and isinstance(item, dict)
        ]
        accepted_observations = {
            str(item.get("candidate_id") or "")
            for item in raw_observations
            if str(item.get("candidate_id") or "") in observation_candidates
        }
        raw_collections = [
            item for item in payload.get("collections", [])
            if isinstance(payload.get("collections"), list) and isinstance(item, dict)
        ]
        accepted_collections = {
            str(item.get("candidate_id") or "")
            for item in raw_collections
            if str(item.get("candidate_id") or "") in collection_candidates
        }
        raw_capabilities = [
            item for item in payload.get("capabilities", [])
            if isinstance(payload.get("capabilities"), list) and isinstance(item, dict)
        ]
        accepted_capabilities = [
            item for item in raw_capabilities
            if str(item.get("text") or "").strip()
            and isinstance(item.get("evidence"), list)
            and any(evidence_id in evidence_ids for evidence_id in item.get("evidence", []))
        ]
        filtered_claims = {
            "observations": max(0, len(raw_observations) - len(accepted_observations)),
            "capabilities": max(0, len(raw_capabilities) - len(accepted_capabilities)),
            "collections": max(0, len(raw_collections) - len(accepted_collections)),
        }
        for field_name, filtered_count in filtered_claims.items():
            if filtered_count:
                errors.append(f"filtered_{field_name}:{filtered_count}")
    except Exception as exc:
        errors.append(f"normalization_failed:{type(exc).__name__}:{exc}")
    return {
        "valid": not errors,
        "errors": errors,
        "unsupported_locators": unsupported_locators,
        "unsupported_collections": sorted(set(unsupported_collections)),
        "filtered_claims": filtered_claims,
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
                diagnostics = llm_transport.safe_response_diagnostics(exc)
                if diagnostics:
                    result["error_diagnostics"] = diagnostics
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
            "filtered_claim_result_count": sum(
                any(result.get("validation", {}).get("filtered_claims", {}).values())
                for result in successes
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


def _prompt_hashes(report: dict[str, Any]) -> dict[tuple[str, int], str]:
    return {
        (str(item.get("case_id") or ""), int(item.get("repeat_index") or 0)): str(
            item.get("prompt_hash") or ""
        )
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
    if _prompt_hashes(baseline) != _prompt_hashes(candidate):
        issues.append("prompt_hash_differs")
    baseline_summary = baseline.get("summary") if isinstance(baseline.get("summary"), dict) else {}
    candidate_summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
    metrics = (
        "success_count", "valid_count", "unsupported_locator_result_count",
        "unsupported_collection_result_count", "filtered_claim_result_count", "total_duration_ms",
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
    for metric in (
        "unsupported_locator_result_count", "unsupported_collection_result_count",
        "filtered_claim_result_count",
    ):
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


def overlay_on_triage_corpus(corpus: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Overlay repeat-zero normalized repository results onto matching triage cases."""
    if int(report.get("schema_version") or 0) != REPORT_SCHEMA_VERSION:
        raise ValueError("unsupported repository analysis report schema")
    if report.get("status") != "complete":
        raise ValueError("repository analysis report is not complete")
    by_project: dict[str, list[dict[str, Any]]] = {}
    for result in report.get("results", []):
        if not isinstance(result, dict) or result.get("status") != "success":
            continue
        if int(result.get("repeat_index") or 0) != 0:
            continue
        case_id = str(result.get("case_id") or "")
        project_key = str(result.get("project_key") or case_id.rsplit(":", 1)[0])
        validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
        normalized = validation.get("normalized_result") if isinstance(validation.get("normalized_result"), dict) else {}
        if not project_key or not str(normalized.get("summary") or "").strip():
            continue
        values = by_project.setdefault(project_key, [])
        repo_url = str(normalized.get("repo_url") or "")
        if repo_url and all(str(value.get("repo_url") or "") != repo_url for value in values):
            values.append(copy.deepcopy(normalized))

    overlaid = copy.deepcopy(corpus)
    applied_case_count = 0
    applied_result_count = 0
    for case in overlaid.get("cases", []):
        if not isinstance(case, dict):
            continue
        evidence = case.get("evidence") if isinstance(case.get("evidence"), dict) else None
        values = by_project.get(str(case.get("project_key") or ""), [])
        if evidence is None or not values:
            continue
        evidence["repository_analysis_results"] = copy.deepcopy(values)
        applied_case_count += 1
        applied_result_count += len(values)
    overlaid["repository_analysis_overlay"] = {
        "report_hash": llm_evaluation.sha256_json(report),
        "source_model": str(dict(report.get("model_spec") or {}).get("model") or ""),
        "applied_case_count": applied_case_count,
        "applied_result_count": applied_result_count,
        "repeat_index": 0,
    }
    return overlaid
