from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from . import llm_model_evaluation as llm_evaluation
from . import llm_transport
from . import collector_relation_shadow


POLICY_VERSION = "relation-assessment-v5"
CORPUS_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
VERDICTS = {"SUPPORTED", "NOT_SUPPORTED", "INSUFFICIENT"}
SUPPORT_EVIDENCE_MODES = {"both_endpoints", "direct_any"}
ALLOWED_PREDICATES = {"project_repository"}
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


def _legacy_verdict(relationship: str) -> str:
    return {
        "same_project": "SUPPORTED",
        "related": "NOT_SUPPORTED",
        "uncertain": "INSUFFICIENT",
    }.get(relationship, "")


def _database_case(row: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    evidence = state.get("evidence") if isinstance(state.get("evidence"), dict) else {}
    llm_source = evidence.get("llm_source") if isinstance(evidence.get("llm_source"), dict) else {}
    llm_input = llm_source.get("evidence") if isinstance(llm_source.get("evidence"), dict) else {}
    analysis = next((
        item for item in llm_input.get("repository_analysis_results", [])
        if isinstance(item, dict)
        and str(item.get("repo_url") or "") == str(row.get("repo_url") or "")
        and not bool(item.get("stale"))
    ), {})
    source_evidence: list[dict[str, str]] = []
    label = " ".join(
        str(row.get(key) or "").strip()
        for key in ("link_text", "aria_label", "link_title")
        if str(row.get(key) or "").strip()
    )
    if label:
        source_evidence.append({
            "id": "S1", "kind": "html_link", "locator": str(row.get("service_url") or ""),
            "target": str(row.get("repo_url") or ""), "label": label, "excerpt": label,
        })
    page_context = " — ".join(
        str(row.get(key) or "").strip()
        for key in ("page_title", "page_description")
        if str(row.get(key) or "").strip()
    )
    if page_context:
        source_evidence.append({
            "id": f"S{len(source_evidence) + 1}", "kind": "service_page_metadata",
            "locator": str(row.get("service_url") or ""), "label": "page title and description",
            "excerpt": page_context,
        })
    target_evidence: list[dict[str, str]] = []
    for raw in analysis.get("evidence_excerpts", []) if isinstance(analysis, dict) else []:
        if not isinstance(raw, dict) or not str(raw.get("excerpt") or "").strip():
            continue
        path = str(raw.get("path") or "")
        line = int(raw.get("line") or 0)
        locator = f"{path}:{line}" if path and line > 0 else path
        target_evidence.append({
            "id": f"R{len(target_evidence) + 1}", "kind": "repository_file",
            "locator": locator, "label": path, "excerpt": str(raw.get("excerpt") or ""),
        })
        if len(target_evidence) >= 8:
            break
    relationship = str(row.get("current_relationship") or "")
    identity = f"{row.get('service_url')}\0{row.get('repo_url')}"
    return {
        "case_id": f"{row.get('project_key')}:{hashlib.sha256(identity.encode()).hexdigest()[:12]}",
        "source_kind": "canonical_db_current_snapshot",
        "project_key": str(row.get("project_key") or ""),
        "project_url": str(row.get("project_url") or ""),
        "expected_verdict": "",
        "legacy_relationship": relationship,
        "legacy_verdict": _legacy_verdict(relationship),
        "input": {
            "predicate": "project_repository", "direction": "service_to_repo",
            "subject": {
                "identifier": str(row.get("service_url") or ""), "kind": "service_url",
                "role": "service_surface", "standing": str(row.get("subject_standing") or ""),
            },
            "object": {
                "identifier": str(row.get("repo_url") or ""), "kind": "repo_url",
                "role": "project_repository", "standing": "",
            },
            "source_evidence": source_evidence,
            "target_evidence": target_evidence,
        },
    }


def export_database_corpus(
    db_path: Path, *, case_catalog_path: Path,
) -> dict[str, Any]:
    resolved = db_path.expanduser().resolve()
    report = collector_relation_shadow.compare_database_path(
        resolved, case_catalog_path=case_catalog_path, example_limit=100000,
    )
    rows = list(
        report.get("relation_assessor_inventory", {})
        .get("examples", {})
        .get("directional_proposal_has_bounded_endpoint_material", [])
    )
    uri = f"{resolved.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        states = {
            str(row["project_key"]): json.loads(str(row["state_json"] or "{}"))
            for row in conn.execute(
                "SELECT project_key, state_json FROM projects WHERE project_key IN (%s)"
                % ",".join("?" for _ in {str(item.get("project_key") or "") for item in rows}),
                sorted({str(item.get("project_key") or "") for item in rows}),
            )
        } if rows else {}
        conn.rollback()
    finally:
        conn.close()
    cases = [
        _database_case(row, states.get(str(row.get("project_key") or ""), {}))
        for row in rows
    ]
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "created_at": llm_evaluation.iso_now(),
        "description": "Current ASSESS-routed service_to_repo proposals with saved bounded repository excerpts; expected verdicts require review.",
        "source": {
            "kind": "canonical_db_current_snapshot", "db_path": str(resolved),
            "read_only": True, "collector_report_hash": str(report.get("content_hash") or ""),
        },
        "selection": {
            "selected_count": len(cases),
            "missing_target_excerpt_count": sum(not case["input"]["target_evidence"] for case in cases),
            "expected_verdicts_reviewed": False,
        },
        "cases": cases,
    }


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
            for key in ("id", "kind", "locator", "target", "label", "excerpt")
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
    subject_endpoint = _clean_endpoint(value.get("subject"))
    object_endpoint = _clean_endpoint(value.get("object"))
    if subject_endpoint["standing"] in {"historical", "former", "non_production"}:
        raise ValueError("temporal or non-production subject is outside relation assessment scope")
    if object_endpoint["standing"] in {"historical", "former", "non_production"}:
        raise ValueError("temporal or non-production object is outside relation assessment scope")
    for item in source_evidence:
        if item["kind"] == "html_link" and not item["target"]:
            item["target"] = object_endpoint["identifier"]
    return {
        "predicate": predicate,
        "direction": direction,
        "subject": subject_endpoint,
        "object": object_endpoint,
        "source_evidence": source_evidence,
        "target_evidence": target_evidence,
    }


def build_prompt(value: Any, *, support_evidence_mode: str = "both_endpoints") -> str:
    if support_evidence_mode not in SUPPORT_EVIDENCE_MODES:
        raise ValueError(f"unsupported support evidence mode: {support_evidence_mode}")
    relation_input = normalize_input(value)
    evidence_json = json.dumps(relation_input, ensure_ascii=False, indent=2, sort_keys=True)
    support_rule = (
        "SUPPORTED must cite at least one S reference and at least one R reference."
        if support_evidence_mode == "both_endpoints"
        else "SUPPORTED requires at least one supplied reference that directly asserts the exact project_repository statement; that decisive reference may come from either endpoint."
    )
    return f"""You assess one proposed directional relation. Do not decide whether two items broadly belong to the same project.

Proposed statement:
The project page URL in subject directly identifies the repository URL in object as an official repository for that project.

Verdicts:
- SUPPORTED: the supplied evidence supports exactly that statement.
- NOT_SUPPORTED: the supplied evidence shows a different relation, such as documentation, a dependency, a sponsor, a developer site, or another project.
- INSUFFICIENT: the statement remains plausible, but the supplied evidence does not establish it.

Rules:
- Use only the supplied evidence. Do not infer from owner names, repository names, shared hosts, or general familiarity.
- An html_link evidence item records an observed link from locator to target. The target is the exact object URL, not merely a provider root.
- {support_rule}
- NOT_SUPPORTED must cite at least one supplied reference.
- Missing or weak evidence is INSUFFICIENT, not NOT_SUPPORTED. Use NOT_SUPPORTED only when evidence positively indicates a different relation or contradicts the proposed statement.
- "Built with", dependency, package, SDK, documentation, and sponsorship links do not identify the linked repository as the service source.
- A provider icon, generic GitHub/Tangled link, owner or name similarity, and a repository Website field establish association at most. They never directly assert project_repository and cannot support it without a decisive statement elsewhere.
- The exact href in an html_link proves which repository was linked, but not why it was linked. Do not call the link a direct source assertion unless its label or surrounding text expresses source, code, contribution, or self-hosting semantics.
- Repository implementation files prove that the repository contains software, but do not by themselves prove that the software is the subject service deployment.
- A repository excerpt that explicitly says its code is deployed at the subject service URL or that it contains that service's source may support the statement by itself.
- A documentation site linking its documentation repository, or a repository generating documentation at the subject URL, is a documentation relation and is NOT_SUPPORTED for this service source predicate.
- Evidence that the subject is a developer or organization site rather than the deployed service is NOT_SUPPORTED for this service source predicate.
- A shared implementation may be an official repository for more than one project; do not infer project merge.
- Archived or moved evidence alone does not disprove a historical source relation and does not establish the current one; without a direct time-scoped statement, use INSUFFICIENT.
- Evaluate only project_repository in the stated direction. Do not propose other predicates, reverse relations, identifiers, candidates, or confirmation decisions.

Decision order:
1. If a supplied reference directly asserts the exact project_repository statement and no evidence contradicts it, return SUPPORTED.
2. Otherwise, if supplied evidence positively identifies a different relation or contradicts the statement, return NOT_SUPPORTED.
3. Otherwise return INSUFFICIENT. Do not use NOT_SUPPORTED merely because direct support is missing.

Return exactly one JSON object:
{{"verdict":"SUPPORTED|NOT_SUPPORTED|INSUFFICIENT","reason":"short explanation","evidence_refs":["S1","R1"]}}

Input:
{evidence_json}
"""


def validate_output(
    payload: Any, relation_input: Any, *, support_evidence_mode: str = "both_endpoints",
) -> dict[str, Any]:
    errors: list[str] = []
    if support_evidence_mode not in SUPPORT_EVIDENCE_MODES:
        raise ValueError(f"unsupported support evidence mode: {support_evidence_mode}")
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
    if verdict == "SUPPORTED" and not refs:
        errors.append("supported_requires_evidence")
    if verdict == "SUPPORTED" and support_evidence_mode == "both_endpoints" and not (
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
            "support_evidence_mode": support_evidence_mode,
        }
    return {"valid": not errors, "errors": errors, "normalized_result": normalized_result}


def run_corpus(
    corpus: dict[str, Any], *, run_llm: Callable[[str], tuple[dict[str, Any], dict[str, Any]]],
    model_spec: dict[str, Any], repeat: int = 1, case_ids: set[str] | None = None,
    support_evidence_mode: str = "both_endpoints",
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
        prompt = build_prompt(relation_input, support_evidence_mode=support_evidence_mode)
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
                validation = validate_output(
                    payload, relation_input, support_evidence_mode=support_evidence_mode,
                )
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
        "support_evidence_mode": support_evidence_mode,
        "summary": {
            "request_count": len(results),
            "success_count": len(successful),
            "failure_count": len(results) - len(successful),
            "valid_count": len(valid),
            "expected_comparable_count": sum(bool(result.get("expected_verdict")) for result in valid),
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
