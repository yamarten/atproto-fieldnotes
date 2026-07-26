from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import sqlite3
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from . import llm_evidence as llm_evidence_core
from . import llm_transport
from . import storage


CORPUS_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 2
LABEL_SCHEMA_VERSION = 2
CATEGORY_VALUES = {
    "project", "lexicon", "infra_tool", "name_only", "library",
    "personal_tool", "reject",
}
CATEGORY_ORDER = (
    "project", "lexicon", "infra_tool", "name_only", "library",
    "personal_tool", "reject",
)
PROMPT_BUCKET_ORDER = ("short", "medium", "upper_medium", "long", "extreme")
LEXICON_SCOPE_VALUES = {"shared", "service_specific", "unknown"}
SERVICE_STATUS_VALUES = {"none", "unknown", "live", "invite_only", "beta", "private", "dead"}
TECHNICAL_INTEREST_VALUES = {"none", "notable", "high", "exceptional"}
STATUS_VALUES = {"candidate", "tracked", "published", "invite_only", "beta", "inactive", "rejected"}
TECHNICAL_INTEREST_REASON_VALUES = {
    "novel_design_pattern", "protocol_gap_exposed", "interoperability_experiment",
    "unusual_architecture", "implementation_reference",
}
MANUAL_REVIEW_KIND_VALUES = {
    "other", "editorial_refresh", "deep_source_material", "service_status_manual_check",
    "identity_resolution", "category_recheck",
}
MANUAL_REVIEW_PRIORITY_VALUES = {"none", "low", "medium", "high"}
REPOSITORY_RELATIONSHIP_VALUES = {"same_project", "related", "uncertain"}
REQUIRED_OUTPUT_FIELDS = {
    "category", "atproto_auth_only", "lexicon_scope", "status", "name", "tagline", "tagline_is_provisional",
    "summary", "features", "inspired_by", "source_code_urls", "service_urls",
    "service_status", "lexicons", "why_keep", "why_reject", "watch_reason",
    "technical_interest_level", "technical_interest_reasons", "technical_interest_summary",
    "manual_review_recommended", "manual_review_kind", "manual_review_priority",
    "manual_review_reason", "repository_link_assessments", "confidence",
}
STRING_OUTPUT_FIELDS = {
    "category", "lexicon_scope", "status", "name", "tagline", "summary",
    "service_status", "why_keep", "why_reject", "watch_reason",
    "technical_interest_level", "technical_interest_summary", "manual_review_kind",
    "manual_review_priority", "manual_review_reason",
}
STYLE_TEXT_FIELDS = {
    "tagline", "summary", "features", "why_keep", "why_reject", "watch_reason",
    "technical_interest_summary", "manual_review_reason",
}
EXECUTABLE_ROLE_RE = re.compile(
    r"(?:(?<![A-Za-z0-9])(?:app|application|cli|command[- ]line|script)(?![A-Za-z0-9])|"
    r"アプリ|CLI|コマンドライン|スクリプト)",
    re.IGNORECASE,
)
REUSABLE_LIBRARY_RE = re.compile(
    r"(?:(?<![A-Za-z0-9])(?:library|sdk|framework|package|module|dependency|import(?:ed|able)?)(?![A-Za-z0-9])|"
    r"ライブラリ|SDK|フレームワーク|パッケージ|モジュール|依存関係|インポート)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s\]\[{}()<>\"']+")
DID_RE = re.compile(r"(?<![A-Za-z0-9-])did:[a-z0-9]+:[A-Za-z0-9._:%-]+(?![A-Za-z0-9-])")
NSID_RE = re.compile(
    r"(?<![A-Za-z0-9-])(?:[a-zA-Z][a-zA-Z0-9-]*\.){2,}[a-zA-Z][a-zA-Z0-9-]*(?![A-Za-z0-9-])"
)
MIXED_SCRIPT_SPACING_RE = re.compile(
    r"(?:[A-Za-z0-9][ \t]+[\u3040-\u30ff\u3400-\u9fff]|"
    r"[\u3040-\u30ff\u3400-\u9fff][ \t]+[A-Za-z0-9])"
)


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def corpus_content_hash(corpus: dict[str, Any]) -> str:
    return sha256_json({
        "schema_version": corpus.get("schema_version"),
        "cases": corpus.get("cases", []),
    })


def apply_labels(corpus: dict[str, Any], label_set: dict[str, Any]) -> dict[str, Any]:
    if int(label_set.get("schema_version") or 0) != LABEL_SCHEMA_VERSION:
        raise ValueError("unsupported LLM evaluation label schema")
    labeled = copy.deepcopy(corpus)
    cases = {
        str(case.get("case_id") or ""): case
        for case in labeled.get("cases", [])
        if isinstance(case, dict)
    }
    for label in label_set.get("labels", []):
        if not isinstance(label, dict):
            raise ValueError("LLM evaluation labels must be objects")
        case_id = str(label.get("case_id") or "")
        if case_id not in cases:
            raise ValueError(f"unknown labeled case ID: {case_id}")
        case = cases[case_id]
        expected_hash = str(label.get("evidence_hash") or "")
        if expected_hash and expected_hash != str(case.get("evidence_hash") or ""):
            raise ValueError(f"evidence hash mismatch for labeled case: {case_id}")
        expected_output = label.get("expected_output")
        if not isinstance(expected_output, dict) or not expected_output:
            raise ValueError(f"expected_output missing for labeled case: {case_id}")
        used_for_prompt_adjustment = label.get("used_for_prompt_adjustment")
        if not isinstance(used_for_prompt_adjustment, bool):
            raise ValueError(
                f"used_for_prompt_adjustment must be boolean for labeled case: {case_id}"
            )
        case["expected_output"] = copy.deepcopy(expected_output)
        case["label_status"] = str(label.get("status") or "")
        case["label_basis"] = str(label.get("basis") or "")
        case["used_for_prompt_adjustment"] = used_for_prompt_adjustment
    labeled["labels"] = {
        "schema_version": LABEL_SCHEMA_VERSION,
        "content_hash": sha256_json(label_set),
        "label_count": len(label_set.get("labels", [])),
    }
    return labeled


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
        "created_at": iso_now(),
        "status": "running",
        "corpus_hash": corpus_content_hash(corpus),
        "corpus_case_count": len(cases),
        "labels": copy.deepcopy(corpus.get("labels", {})),
        "model_spec": model_spec,
        "input_condition": {
            "current_profile": str(model_spec.get("current_profile_mode") or "without"),
        },
        "reasoning_observation": reasoning_observation(model_spec),
        "runtime_observation": {
            "peak_memory_bytes": None,
            "swap_delta_bytes": None,
            "prefill_ms": None,
            "decode_ms": None,
            "measurement_status": "not_connected",
        },
        "results": list(results),
    }


def reasoning_observation(model_spec: dict[str, Any]) -> dict[str, Any]:
    requested = str(
        model_spec.get("reasoning_effort_requested")
        or model_spec.get("reasoning_effort")
        or ""
    ).strip()
    status = str(model_spec.get("reasoning_observation") or "not_checked").strip()
    if not requested:
        status = "not_requested"
    return {
        "requested": requested,
        "runtime_status": status,
        "note": str(model_spec.get("reasoning_observation_note") or ""),
    }


def prompt_bucket(prompt_chars: int) -> str:
    if prompt_chars < 10_000:
        return "short"
    if prompt_chars < 20_000:
        return "medium"
    if prompt_chars < 50_000:
        return "upper_medium"
    if prompt_chars < 100_000:
        return "long"
    return "extreme"


def evidence_field_sizes(evidence: dict[str, Any]) -> dict[str, int]:
    return {
        str(key): len(canonical_json(value).encode("utf-8"))
        for key, value in sorted(evidence.items())
    }


def category_summary_conflict(category: str, summary: str) -> bool:
    narrative = URL_RE.sub(" ", summary)
    narrative = DID_RE.sub(" ", narrative)
    narrative = NSID_RE.sub(" ", narrative)
    return (
        category == "library"
        and bool(EXECUTABLE_ROLE_RE.search(narrative))
        and not bool(REUSABLE_LIBRARY_RE.search(narrative))
    )


def _candidate_from_row(
    row: sqlite3.Row,
    build_prompt: Callable[[dict[str, Any]], str],
) -> dict[str, Any] | None:
    project = storage.decode_history_json(
        row["output_project_json"],
        context=f"investigation run {row['run_id']} output_project_json",
    )
    if not isinstance(project, dict):
        return None
    llm_source = project.get("llm_source")
    saved_evidence = llm_source.get("evidence") if isinstance(llm_source, dict) else None
    if not isinstance(saved_evidence, dict) or not saved_evidence:
        return None
    evidence = llm_evidence_core.without_prior_llm_derived_state(saved_evidence)
    evidence.pop("current_profile", None)
    current_profile = {
        "tagline": str(project.get("tagline") or ""),
        "tagline_is_provisional": bool(project.get("tagline_is_provisional")),
        "summary": str(project.get("summary") or ""),
        "features": [str(item) for item in project.get("features", []) if str(item).strip()][:6],
    }
    prompt = build_prompt(evidence)
    category = str(project.get("category") or "")
    summary = str(project.get("summary") or "")
    evidence_hash = sha256_json(evidence)
    tags = [f"category:{category or 'unknown'}"]
    if category == "library":
        tags.append("library_focus")
    if category_summary_conflict(category, summary):
        tags.append("category_summary_conflict")
    return {
        "case_id": f"{row['project_key']}:{evidence_hash[:12]}",
        "project_key": str(row["project_key"]),
        "project_name": str(project.get("name") or ""),
        "source_run_id": str(row["run_id"]),
        "source_completed_at": str(row["completed_at"]),
        "prompt_chars": len(prompt),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "prompt_bucket": prompt_bucket(len(prompt)),
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "evidence_hash": evidence_hash,
        "evidence_field_sizes": evidence_field_sizes(evidence),
        "selection_tags": tags,
        "known_manual_signal": None,
        "current_profile": current_profile,
        "current_profile_hash": sha256_json(current_profile),
        "reference_output": {
            key: project.get(key)
            for key in (
                "category", "atproto_auth_only", "lexicon_scope", "service_status", "technical_interest_level",
                "name", "tagline", "summary", "features",
            )
        },
        "reference_is_gold": False,
        "evidence": evidence,
    }


def _corpus_source_rows(db_path: Path) -> Iterator[sqlite3.Row]:
    uri = f"file:{urllib.parse.quote(str(db_path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield from conn.execute(
            "SELECT run_id, project_key, completed_at, output_project_json "
            "FROM investigation_runs ORDER BY completed_at DESC, run_id DESC"
        )
    finally:
        conn.close()


def _select_cases(
    candidates: list[dict[str, Any]],
    limit: int,
    library_min: int,
    category_min: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add(case: dict[str, Any]) -> None:
        if len(selected) >= limit or case["case_id"] in selected_ids:
            return
        selected.append(case)
        selected_ids.add(case["case_id"])

    library_cases = [case for case in candidates if "library_focus" in case["selection_tags"]]
    library_cases.sort(
        key=lambda case: (
            "category_summary_conflict" not in case["selection_tags"],
            case["prompt_chars"],
            case["case_id"],
        )
    )
    for case in library_cases[: min(library_min, limit)]:
        add(case)

    for category in CATEGORY_ORDER:
        category_cases = [
            case for case in candidates
            if case.get("reference_output", {}).get("category") == category
        ]
        category_cases.sort(key=lambda case: (case["prompt_chars"], case["case_id"]))
        category_representatives: list[dict[str, Any]] = []
        left, right = 0, len(category_cases) - 1
        while left <= right:
            category_representatives.append(category_cases[left])
            if right != left:
                category_representatives.append(category_cases[right])
            left += 1
            right -= 1
        already_selected = sum(
            case.get("reference_output", {}).get("category") == category
            for case in selected
        )
        for case in category_representatives[: max(0, category_min - already_selected)]:
            add(case)

    strata = {
        (bucket, category): [
            case for case in candidates
            if case["prompt_bucket"] == bucket
            and case.get("reference_output", {}).get("category") == category
        ]
        for category in CATEGORY_ORDER
        for bucket in PROMPT_BUCKET_ORDER
    }
    for cases in strata.values():
        cases.sort(key=lambda case: (-case["prompt_chars"], case["case_id"]))
    while len(selected) < limit and any(strata.values()):
        for key in strata:
            while strata[key] and strata[key][0]["case_id"] in selected_ids:
                strata[key].pop(0)
            if strata[key]:
                add(strata[key].pop(0))
            if len(selected) >= limit:
                break

    for case in candidates:
        add(case)
    return selected


def export_corpus(
    db_path: Path,
    *,
    build_prompt: Callable[[dict[str, Any]], str],
    limit: int = 40,
    library_min: int = 8,
    category_min: int = 2,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if library_min < 0:
        raise ValueError("library_min cannot be negative")
    if category_min < 0:
        raise ValueError("category_min cannot be negative")
    resolved = db_path.expanduser().resolve()
    candidates: list[dict[str, Any]] = []
    seen_projects: set[str] = set()
    for row in _corpus_source_rows(resolved):
        project_key = str(row["project_key"])
        if project_key in seen_projects:
            continue
        candidate = _candidate_from_row(row, build_prompt)
        if candidate is None:
            continue
        seen_projects.add(project_key)
        candidates.append(candidate)
    cases = _select_cases(candidates, limit, library_min, category_min)
    bucket_counts: dict[str, int] = {}
    for case in cases:
        bucket = str(case["prompt_bucket"])
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "created_at": iso_now(),
        "source": {"kind": "investigation_runs", "db_path": str(resolved), "read_only": True},
        "selection": {
            "latest_run_per_project": True,
            "candidate_count": len(candidates),
            "limit": limit,
            "library_min": library_min,
            "category_min": category_min,
            "selected_count": len(cases),
            "prompt_bucket_counts": bucket_counts,
        },
        "cases": cases,
    }


def _all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def _identifiers(value: Any) -> set[str]:
    output: set[str] = set()
    for text in _all_strings(value):
        output.update(match.rstrip(".,;:") for match in URL_RE.findall(text))
        output.update(DID_RE.findall(text))
        output.update(NSID_RE.findall(text))
    return output


def _style_warnings(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for field in sorted(STYLE_TEXT_FIELDS):
        value = payload.get(field)
        values = value if isinstance(value, list) else [value]
        if any(isinstance(item, str) and MIXED_SCRIPT_SPACING_RE.search(item) for item in values):
            warnings.append(f"mixed_script_spacing:{field}")
    assessments = payload.get("repository_link_assessments")
    if isinstance(assessments, list) and any(
        isinstance(item, dict)
        and isinstance(item.get("reason"), str)
        and MIXED_SCRIPT_SPACING_RE.search(item["reason"])
        for item in assessments
    ):
        warnings.append("mixed_script_spacing:repository_link_assessments.reason")
    return warnings


def validate_output(payload: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "valid": False,
            "errors": ["invalid_type:top_level:object"],
            "missing_fields": sorted(REQUIRED_OUTPUT_FIELDS),
            "unsupported_identifiers": sorted(_identifiers(payload) - _identifiers(evidence)),
            "category_summary_consistent": True,
            "style_warnings": [],
        }
    errors: list[str] = []
    missing = sorted(REQUIRED_OUTPUT_FIELDS - set(payload))
    if missing:
        errors.append(f"missing_fields:{','.join(missing)}")
    enum_fields = {
        "category": CATEGORY_VALUES,
        "lexicon_scope": LEXICON_SCOPE_VALUES,
        "status": STATUS_VALUES,
        "service_status": SERVICE_STATUS_VALUES,
        "technical_interest_level": TECHNICAL_INTEREST_VALUES,
        "manual_review_kind": MANUAL_REVIEW_KIND_VALUES,
        "manual_review_priority": MANUAL_REVIEW_PRIORITY_VALUES,
    }
    for field, allowed in enum_fields.items():
        if field in payload and str(payload.get(field)) not in allowed:
            errors.append(f"invalid_enum:{field}:{payload.get(field)}")
    list_fields = (
        "features", "inspired_by", "source_code_urls", "service_urls", "lexicons",
        "technical_interest_reasons", "repository_link_assessments",
    )
    for field in list_fields:
        if field in payload and not isinstance(payload.get(field), list):
            errors.append(f"invalid_type:{field}:list")
    for field in STRING_OUTPUT_FIELDS:
        if field in payload and not isinstance(payload.get(field), str):
            errors.append(f"invalid_type:{field}:string")
    for field in list_fields[:-1]:
        value = payload.get(field)
        if isinstance(value, list) and any(not isinstance(item, str) for item in value):
            errors.append(f"invalid_item_type:{field}:string")
    for field in ("atproto_auth_only", "tagline_is_provisional", "manual_review_recommended"):
        if field in payload and not isinstance(payload.get(field), bool):
            errors.append(f"invalid_type:{field}:bool")
    if "confidence" in payload and (
        isinstance(payload.get("confidence"), bool)
        or not isinstance(payload.get("confidence"), (int, float))
    ):
        errors.append("invalid_type:confidence:number")
    elif "confidence" in payload and not 0 <= float(payload["confidence"]) <= 1:
        errors.append("confidence_out_of_range")
    if isinstance(payload.get("technical_interest_reasons"), list):
        for reason in payload["technical_interest_reasons"]:
            if str(reason) not in TECHNICAL_INTEREST_REASON_VALUES:
                errors.append(f"invalid_enum:technical_interest_reasons:{reason}")
    if isinstance(payload.get("repository_link_assessments"), list):
        for index, assessment in enumerate(payload["repository_link_assessments"]):
            if not isinstance(assessment, dict):
                errors.append(f"invalid_type:repository_link_assessments[{index}]:object")
                continue
            missing_assessment = {"service_url", "repo_url", "relationship", "reason"} - set(assessment)
            if missing_assessment:
                errors.append(
                    f"missing_fields:repository_link_assessments[{index}]:{','.join(sorted(missing_assessment))}"
                )
            relationship = str(assessment.get("relationship") or "")
            if relationship not in REPOSITORY_RELATIONSHIP_VALUES:
                errors.append(f"invalid_enum:repository_link_assessments[{index}].relationship:{relationship}")
    if len(str(payload.get("tagline") or "")) > 30:
        errors.append("tagline_too_long")
    category = str(payload.get("category") or "")
    if payload.get("atproto_auth_only") is True and category not in {"project", "personal_tool"}:
        errors.append("atproto_auth_only_requires_independent_app_or_service")
    summary = str(payload.get("summary") or "")
    conflict = category_summary_conflict(category, summary)
    if conflict:
        errors.append("category_summary_conflict:library_nonembedded_role")
    unsupported = sorted(_identifiers(payload) - _identifiers(evidence))
    if unsupported:
        errors.append("unsupported_identifiers")
    return {
        "valid": not errors,
        "errors": errors,
        "missing_fields": missing,
        "unsupported_identifiers": unsupported,
        "category_summary_consistent": not conflict,
        "style_warnings": _style_warnings(payload),
    }


def reference_diff(payload: dict[str, Any], reference: Any) -> list[dict[str, Any]]:
    if not isinstance(reference, dict):
        return []
    changes: list[dict[str, Any]] = []
    for field in (
        "category", "atproto_auth_only", "lexicon_scope", "service_status", "technical_interest_level",
        "name", "tagline", "summary", "features",
    ):
        before = reference.get(field)
        after = payload.get(field)
        if before != after:
            changes.append({"field": field, "reference": before, "candidate": after})
    return changes


def repeat_comparison(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if result.get("status") != "success" or not isinstance(result.get("payload"), dict):
            continue
        by_case.setdefault(str(result.get("case_id") or ""), []).append(result)
    comparisons: list[dict[str, Any]] = []
    compared_fields = (
        "category", "atproto_auth_only", "lexicon_scope", "status", "name", "tagline", "summary", "features",
        "source_code_urls", "service_urls", "service_status", "lexicons",
        "technical_interest_level", "manual_review_recommended", "manual_review_kind",
        "manual_review_priority", "repository_link_assessments",
    )
    for case_id, case_results in sorted(by_case.items()):
        if len(case_results) < 2:
            continue
        first_payload = case_results[0]["payload"]
        different_fields = sorted(
            field
            for field in compared_fields
            if any(result["payload"].get(field) != first_payload.get(field) for result in case_results[1:])
        )
        identifier_sets = [_identifiers(result["payload"]) for result in case_results]
        validation_states = [
            (
                bool(result.get("validation", {}).get("valid")),
                tuple(result.get("validation", {}).get("errors", [])),
            )
            for result in case_results
        ]
        comparisons.append({
            "case_id": case_id,
            "successful_run_count": len(case_results),
            "different_fields": different_fields,
            "identifiers_differ": any(value != identifier_sets[0] for value in identifier_sets[1:]),
            "validation_differs": any(value != validation_states[0] for value in validation_states[1:]),
        })
    return {
        "compared_case_count": len(comparisons),
        "different_output_case_count": sum(bool(item["different_fields"]) for item in comparisons),
        "category_difference_case_count": sum("category" in item["different_fields"] for item in comparisons),
        "identifier_difference_case_count": sum(bool(item["identifiers_differ"]) for item in comparisons),
        "validation_difference_case_count": sum(bool(item["validation_differs"]) for item in comparisons),
        "cases": comparisons,
    }


def _report_result_keys(report: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        (str(result.get("case_id") or ""), int(result.get("repeat_index") or 0))
        for result in report.get("results", [])
        if isinstance(result, dict)
    }


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    for name, report in (("baseline", baseline), ("candidate", candidate)):
        if int(report.get("schema_version") or 0) != REPORT_SCHEMA_VERSION:
            raise ValueError(f"unsupported {name} LLM evaluation report schema")
        if report.get("status") != "complete":
            raise ValueError(f"{name} LLM evaluation report is not complete")
    comparison_issues: list[str] = []
    if baseline.get("corpus_hash") != candidate.get("corpus_hash"):
        comparison_issues.append("corpus_hash_differs")
    if baseline.get("labels") != candidate.get("labels"):
        comparison_issues.append("reviewed_labels_differ")
    if baseline.get("input_condition") != candidate.get("input_condition"):
        comparison_issues.append("input_condition_differs")
    if _report_result_keys(baseline) != _report_result_keys(candidate):
        comparison_issues.append("case_or_repeat_set_differs")

    baseline_summary = baseline.get("summary") if isinstance(baseline.get("summary"), dict) else {}
    candidate_summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
    metrics = (
        "success_count",
        "schema_valid_count",
        "unsupported_identifier_result_count",
        "reviewed_not_used_for_prompt_adjustment_match_count",
        "reviewed_not_used_for_prompt_adjustment_mismatch_count",
        "total_duration_ms",
    )
    metric_comparison = {
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
    automatic_regressions: list[str] = []
    for metric in ("success_count", "schema_valid_count", "reviewed_not_used_for_prompt_adjustment_match_count"):
        values = metric_comparison[metric]
        if isinstance(values["difference"], (int, float)) and values["difference"] < 0:
            automatic_regressions.append(f"{metric}_decreased")
    for metric in ("unsupported_identifier_result_count", "reviewed_not_used_for_prompt_adjustment_mismatch_count"):
        values = metric_comparison[metric]
        if isinstance(values["difference"], (int, float)) and values["difference"] > 0:
            automatic_regressions.append(f"{metric}_increased")

    remaining_checks: list[str] = []
    reasoning_status = candidate.get("reasoning_observation", {}).get("runtime_status")
    if reasoning_status == "not_checked":
        remaining_checks.append("reasoning_request_not_checked_in_runtime")
    elif reasoning_status == "ignored":
        remaining_checks.append("reasoning_request_ignored_by_runtime")
    if not candidate_summary.get("reviewed_not_used_for_prompt_adjustment_case_count"):
        remaining_checks.append("no_reviewed_cases_outside_prompt_adjustment")
    if not candidate.get("repeat_comparison", {}).get("compared_case_count"):
        remaining_checks.append("same_input_not_run_multiple_times")
    if candidate.get("runtime_observation", {}).get("measurement_status") != "measured":
        remaining_checks.append("memory_and_runtime_breakdown_not_measured")
    remaining_checks.extend([
        "summary_tagline_features_not_reviewed_by_operator",
        "copied_database_enrich_comparison_not_reviewed",
        "limited_project_trial_not_completed",
    ])
    return {
        "schema_version": 1,
        "created_at": iso_now(),
        "baseline": baseline.get("model_spec", {}),
        "candidate": candidate.get("model_spec", {}),
        "comparison_issues": comparison_issues,
        "metric_comparison": metric_comparison,
        "automatic_regressions": automatic_regressions,
        "remaining_checks": remaining_checks,
        "automatic_checks_pass": not comparison_issues and not automatic_regressions,
    }


def git_state(root: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, text=True, capture_output=True, check=True,
        ).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "", "dirty": None}


def run_corpus(
    corpus: dict[str, Any],
    *,
    build_prompt: Callable[[dict[str, Any]], str],
    run_llm: Callable[[str], tuple[dict[str, Any], dict[str, Any]]],
    model_spec: dict[str, Any],
    repeat: int = 1,
    current_profile_mode: str = "without",
    case_ids: set[str] | None = None,
    root: Path | None = None,
    progress_callback: Callable[[list[dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:
    if int(corpus.get("schema_version") or 0) != CORPUS_SCHEMA_VERSION:
        raise ValueError("unsupported LLM evaluation corpus schema")
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    if current_profile_mode not in {"without", "with"}:
        raise ValueError("current_profile_mode must be 'without' or 'with'")
    cases = [case for case in corpus.get("cases", []) if isinstance(case, dict)]
    if case_ids:
        available_ids = {str(case.get("case_id") or "") for case in cases}
        missing_ids = sorted(case_ids - available_ids)
        if missing_ids:
            raise ValueError(f"unknown corpus case IDs: {', '.join(missing_ids)}")
        cases = [case for case in cases if str(case.get("case_id")) in case_ids]
    results: list[dict[str, Any]] = []
    for case in cases:
        base_evidence = case.get("evidence")
        if not isinstance(base_evidence, dict):
            continue
        evidence = copy.deepcopy(base_evidence)
        evidence.pop("current_profile", None)
        if current_profile_mode == "with":
            profile = case.get("current_profile")
            if not isinstance(profile, dict):
                raise ValueError(f"current_profile missing for corpus case: {case.get('case_id', '')}")
            evidence["current_profile"] = copy.deepcopy(profile)
        prompt = build_prompt(evidence)
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
                validation = validate_output(payload, evidence)
                result.update({
                    "status": "success",
                    "payload": payload,
                    "meta": meta,
                    "validation": validation,
                    "reference_diff": reference_diff(payload, case.get("reference_output"))
                    if isinstance(payload, dict) else [],
                    "reference_is_gold": False,
                    "expected_output": case.get("expected_output")
                    if isinstance(case.get("expected_output"), dict) else None,
                    "expected_diff": reference_diff(payload, case.get("expected_output"))
                    if isinstance(payload, dict) and isinstance(case.get("expected_output"), dict) else [],
                    "label_status": str(case.get("label_status") or ""),
                    "used_for_prompt_adjustment": case.get("used_for_prompt_adjustment")
                    if isinstance(case.get("used_for_prompt_adjustment"), bool) else None,
                })
            except Exception as exc:
                result.update({"error_type": type(exc).__name__, "error": str(exc)})
                diagnostics = llm_transport.safe_response_diagnostics(exc)
                if diagnostics:
                    result["error_diagnostics"] = diagnostics
            finally:
                result["duration_ms"] = max(0, round((time.perf_counter() - started) * 1000))
            results.append(result)
            if progress_callback is not None:
                progress_callback(results)
    successes = [result for result in results if result["status"] == "success"]
    valid = [result for result in successes if result.get("validation", {}).get("valid")]
    library_conflicts = [
        result for result in successes
        if not result.get("validation", {}).get("category_summary_consistent", True)
    ]
    style_warnings = [
        result for result in successes
        if result.get("validation", {}).get("style_warnings")
    ]
    category_labeled = [
        result for result in successes
        if isinstance(result.get("expected_output"), dict)
        and "category" in result["expected_output"]
    ]
    category_matches = [
        result for result in category_labeled
        if result.get("payload", {}).get("category") == result["expected_output"].get("category")
    ]
    reviewed_case_ids = {
        str(result.get("case_id") or "") for result in category_labeled
    }
    reviewed_adjustment_case_ids = {
        str(result.get("case_id") or "")
        for result in category_labeled
        if result.get("used_for_prompt_adjustment") is True
    }
    reviewed_unadjusted = [
        result for result in category_labeled
        if result.get("used_for_prompt_adjustment") is False
    ]
    reviewed_unadjusted_matches = [
        result for result in reviewed_unadjusted
        if result.get("payload", {}).get("category") == result["expected_output"].get("category")
    ]
    reviewed_unadjusted_case_ids = {
        str(result.get("case_id") or "") for result in reviewed_unadjusted
    }
    bucket_summary: dict[str, dict[str, int]] = {}
    case_by_id = {str(case.get("case_id") or ""): case for case in cases}
    for result in results:
        case = case_by_id.get(str(result.get("case_id") or ""), {})
        bucket = str(case.get("prompt_bucket") or prompt_bucket(int(result["prompt_chars"])))
        summary = bucket_summary.setdefault(bucket, {"requests": 0, "successes": 0, "valid": 0, "library_conflicts": 0})
        summary["requests"] += 1
        if result["status"] == "success":
            summary["successes"] += 1
            summary["valid"] += int(bool(result.get("validation", {}).get("valid")))
            summary["library_conflicts"] += int(
                not result.get("validation", {}).get("category_summary_consistent", True)
            )
    repetition = repeat_comparison(results)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": iso_now(),
        "status": "complete",
        "corpus_hash": corpus_content_hash(corpus),
        "corpus_case_count": len(cases),
        "labels": copy.deepcopy(corpus.get("labels", {})),
        "git": git_state(root) if root else {"commit": "", "dirty": None},
        "model_spec": model_spec,
        "input_condition": {"current_profile": current_profile_mode},
        "reasoning_observation": reasoning_observation(model_spec),
        "runtime_observation": {
            "peak_memory_bytes": None,
            "swap_delta_bytes": None,
            "prefill_ms": None,
            "decode_ms": None,
            "measurement_status": "not_connected",
        },
        "summary": {
            "request_count": len(results),
            "success_count": len(successes),
            "failure_count": len(results) - len(successes),
            "schema_valid_count": len(valid),
            "library_category_summary_conflict_count": len(library_conflicts),
            "unsupported_identifier_result_count": sum(
                bool(result.get("validation", {}).get("unsupported_identifiers")) for result in successes
            ),
            "style_warning_result_count": len(style_warnings),
            "expected_category_labeled_count": len(category_labeled),
            "expected_category_match_count": len(category_matches),
            "expected_category_mismatch_count": len(category_labeled) - len(category_matches),
            "expected_category_accuracy": (
                len(category_matches) / len(category_labeled) if category_labeled else None
            ),
            "reviewed_case_count": len(reviewed_case_ids),
            "reviewed_used_for_prompt_adjustment_case_count": len(reviewed_adjustment_case_ids),
            "reviewed_not_used_for_prompt_adjustment_case_count": len(reviewed_unadjusted_case_ids),
            "reviewed_not_used_for_prompt_adjustment_result_count": len(reviewed_unadjusted),
            "reviewed_not_used_for_prompt_adjustment_match_count": len(reviewed_unadjusted_matches),
            "reviewed_not_used_for_prompt_adjustment_mismatch_count": (
                len(reviewed_unadjusted) - len(reviewed_unadjusted_matches)
            ),
            "reviewed_not_used_for_prompt_adjustment_accuracy": (
                len(reviewed_unadjusted_matches) / len(reviewed_unadjusted)
                if reviewed_unadjusted else None
            ),
            "total_duration_ms": sum(int(result["duration_ms"]) for result in results),
            "prompt_buckets": bucket_summary,
        },
        "repeat_comparison": repetition,
        "results": results,
    }
