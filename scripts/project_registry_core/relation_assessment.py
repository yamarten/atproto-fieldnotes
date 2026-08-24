from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from . import llm_model_evaluation as llm_evaluation
from . import llm_transport


POLICY_VERSION = "relation-assessment-v1"
CORPUS_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
VERDICTS = {"SUPPORTED", "NOT_SUPPORTED", "INSUFFICIENT"}
ALLOWED_PREDICATES = {"source_repository"}
ALLOWED_DIRECTIONS = {"service_to_repo"}
FORBIDDEN_INPUT_FIELDS = {
    "project", "project_key", "project_name", "profile", "current_profile",
    "existing_assessment", "existing_verdict", "legacy_verdict",
}


def corpus_content_hash(corpus: dict[str, Any]) -> str:
    return llm_evaluation.sha256_json({
        "schema_version": corpus.get("schema_version"),
        "cases": corpus.get("cases", []),
    })


def _contains_forbidden_field(value: Any) -> str:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in FORBIDDEN_INPUT_FIELDS:
                return str(key)
            nested = _contains_forbidden_field(child)
            if nested:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _contains_forbidden_field(child)
            if nested:
                return nested
    return ""


def _clean_endpoint(value: Any) -> dict[str, str]:
    value = value if isinstance(value, dict) else {}
    return {
        key: str(value.get(key) or "")[:1000]
        for key in ("identifier", "kind", "role", "standing")
    }


def _clean_evidence(values: Any, prefix: str) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in values if isinstance(values, list) else []:
        if not isinstance(raw, dict):
            continue
        evidence_id = str(raw.get("id") or "")
        if not evidence_id.startswith(prefix) or evidence_id in seen:
            continue
        seen.add(evidence_id)
        item = {
            key: str(raw.get(key) or "")[:2000]
            for key in ("id", "kind", "locator", "label", "excerpt")
        }
        output.append(item)
    return output[:16]


def normalize_input(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("relation assessment input must be an object")
    forbidden = _contains_forbidden_field(value)
    if forbidden:
        raise ValueError(f"relation assessment input contains forbidden field: {forbidden}")
    predicate = str(value.get("predicate") or "")
    direction = str(value.get("direction") or "")
    if predicate not in ALLOWED_PREDICATES:
        raise ValueError(f"unsupported relation predicate: {predicate}")
    if direction not in ALLOWED_DIRECTIONS:
        raise ValueError(f"unsupported relation direction: {direction}")
    source_evidence = _clean_evidence(value.get("source_evidence"), "S")
    target_evidence = _clean_evidence(value.get("target_evidence"), "R")
    return {
        "predicate": predicate,
        "direction": direction,
        "subject": _clean_endpoint(value.get("subject")),
        "object": _clean_endpoint(value.get("object")),
        "source_evidence": source_evidence,
        "target_evidence": target_evidence,
    }


def build_prompt(value: Any) -> str:
    relation_input = normalize_input(value)
    evidence_json = json.dumps(relation_input, ensure_ascii=False, indent=2, sort_keys=True)
    return f"""You assess one proposed directional relation. Do not decide whether two items broadly belong to the same project.

Proposed statement:
The service URL in subject directly identifies the repository URL in object as a source repository for that service.

Verdicts:
- SUPPORTED: the supplied evidence supports exactly that statement.
- NOT_SUPPORTED: the supplied evidence shows a different relation, such as documentation, a dependency, a sponsor, a developer site, or another project.
- INSUFFICIENT: the statement remains plausible, but the supplied evidence does not establish it.

Rules:
- Use only the supplied evidence. Do not infer from owner names, repository names, shared hosts, or general familiarity.
- SUPPORTED must cite at least one S reference and at least one R reference.
- NOT_SUPPORTED must cite at least one supplied reference.
- A service-side provider icon or generic GitHub link and a repository Website field are not sufficient by themselves.
- A shared implementation may be a source repository for more than one service; do not infer project merge.
- Evaluate only source_repository in the stated direction. Do not propose other predicates, reverse relations, identifiers, candidates, or confirmation decisions.

Return exactly one JSON object:
{{"verdict":"SUPPORTED|NOT_SUPPORTED|INSUFFICIENT","reason":"short explanation","evidence_refs":["S1","R1"]}}

Input:
{evidence_json}
"""


def validate_output(payload: Any, relation_input: Any) -> dict[str, Any]:
    errors: list[str] = []
    normalized_input = normalize_input(relation_input)
    if not isinstance(payload, dict):
        return {"valid": False, "errors": ["invalid_type:top_level:object"], "normalized_result": None}
    extra = sorted(set(payload) - {"verdict", "reason", "evidence_refs"})
    missing = sorted({"verdict", "reason", "evidence_refs"} - set(payload))
    if extra:
        errors.append(f"extra_fields:{','.join(extra)}")
    if missing:
        errors.append(f"missing_fields:{','.join(missing)}")
    verdict = str(payload.get("verdict") or "")
    reason = payload.get("reason")
    refs = payload.get("evidence_refs")
    if verdict not in VERDICTS:
        errors.append("invalid_verdict")
    if not isinstance(reason, str) or not reason.strip():
        errors.append("invalid_reason")
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        errors.append("invalid_evidence_refs")
        refs = []
    allowed_refs = {
        item["id"]
        for field in ("source_evidence", "target_evidence")
        for item in normalized_input[field]
    }
    unsupported = sorted({str(ref) for ref in refs if str(ref) not in allowed_refs})
    if unsupported:
        errors.append(f"unsupported_evidence_refs:{','.join(unsupported)}")
    if verdict == "SUPPORTED" and not (
        any(str(ref).startswith("S") for ref in refs)
        and any(str(ref).startswith("R") for ref in refs)
    ):
        errors.append("supported_requires_both_endpoints")
    if verdict == "NOT_SUPPORTED" and not refs:
        errors.append("not_supported_requires_evidence")
    normalized_result = None
    if not errors:
        normalized_result = {
            "verdict": verdict,
            "reason": str(reason).strip()[:1000],
            "evidence_refs": list(dict.fromkeys(str(ref) for ref in refs)),
            "predicate": normalized_input["predicate"],
            "direction": normalized_input["direction"],
            "policy_version": POLICY_VERSION,
        }
    return {"valid": not errors, "errors": errors, "normalized_result": normalized_result}


def run_corpus(
    corpus: dict[str, Any], *, run_llm: Callable[[str], tuple[dict[str, Any], dict[str, Any]]],
    model_spec: dict[str, Any], repeat: int = 1, case_ids: set[str] | None = None,
    root: Path | None = None, progress_callback: Callable[[list[dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:
    if int(corpus.get("schema_version") or 0) != CORPUS_SCHEMA_VERSION:
        raise ValueError("unsupported relation assessment corpus schema")
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
        relation_input = case.get("input")
        prompt = build_prompt(relation_input)
        for repeat_index in range(repeat):
            started = time.perf_counter()
            result: dict[str, Any] = {
                "case_id": str(case.get("case_id") or ""),
                "repeat_index": repeat_index,
                "status": "failed",
                "expected_verdict": str(case.get("expected_verdict") or ""),
                "legacy_verdict": str(case.get("legacy_verdict") or ""),
                "prompt_chars": len(prompt),
                "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            }
            try:
                payload, meta = run_llm(prompt)
                validation = validate_output(payload, relation_input)
                actual = str(dict(validation.get("normalized_result") or {}).get("verdict") or "")
                result.update({
                    "status": "success", "payload": payload, "meta": meta,
                    "validation": validation,
                    "verdict_matches_expected": bool(validation["valid"] and actual == result["expected_verdict"]),
                    "verdict_matches_legacy": bool(
                        validation["valid"] and result["legacy_verdict"]
                        and actual == result["legacy_verdict"]
                    ),
                })
            except Exception as exc:
                result.update({"error_type": type(exc).__name__, "error": str(exc)})
                diagnostics = llm_transport.safe_response_diagnostics(exc)
                if diagnostics:
                    result["error_diagnostics"] = diagnostics
            result["duration_ms"] = max(0, round((time.perf_counter() - started) * 1000))
            results.append(result)
            if progress_callback:
                progress_callback(results)
    successful = [result for result in results if result.get("status") == "success"]
    valid = [result for result in successful if result.get("validation", {}).get("valid")]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": llm_evaluation.iso_now(),
        "status": "complete",
        "corpus_hash": corpus_content_hash(corpus),
        "corpus_case_count": len(cases),
        "git": llm_evaluation.git_state(root) if root else {"commit": "", "dirty": None},
        "model_spec": copy.deepcopy(model_spec),
        "summary": {
            "request_count": len(results),
            "success_count": len(successful),
            "failure_count": len(results) - len(successful),
            "valid_count": len(valid),
            "expected_match_count": sum(bool(result.get("verdict_matches_expected")) for result in valid),
            "legacy_comparable_count": sum(bool(result.get("legacy_verdict")) for result in valid),
            "legacy_match_count": sum(bool(result.get("verdict_matches_legacy")) for result in valid),
            "total_duration_ms": sum(int(result.get("duration_ms") or 0) for result in results),
        },
        "results": results,
    }


def checkpoint_report(corpus: dict[str, Any], model_spec: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": llm_evaluation.iso_now(),
        "status": "running",
        "corpus_hash": corpus_content_hash(corpus),
        "corpus_case_count": len(corpus.get("cases", [])),
        "model_spec": copy.deepcopy(model_spec),
        "results": list(results),
    }
