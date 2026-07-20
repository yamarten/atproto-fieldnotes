from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable


CORPUS_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
CATEGORY_VALUES = {
    "project", "lexicon", "infra_tool", "name_only", "library",
    "personal_tool", "auth_only", "reject",
}
CATEGORY_ORDER = (
    "project", "lexicon", "infra_tool", "name_only", "library",
    "personal_tool", "auth_only", "reject",
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
    "category", "lexicon_scope", "status", "name", "tagline", "tagline_is_provisional",
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
    r"(?:(?<![A-Za-z0-9])(?:app|application|cli|command[- ]line|script|plugin|template|example|prototype)(?![A-Za-z0-9])|"
    r"アプリ|CLI|コマンドライン|スクリプト|プラグイン|テンプレート|サンプル|プロトタイプ)",
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
        "model_spec": model_spec,
        "runtime_observation": {
            "peak_memory_bytes": None,
            "swap_delta_bytes": None,
            "prefill_ms": None,
            "decode_ms": None,
            "measurement_status": "not_connected",
        },
        "results": list(results),
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
    try:
        project = json.loads(str(row["output_project_json"]))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(project, dict):
        return None
    llm_source = project.get("llm_source")
    evidence = llm_source.get("evidence") if isinstance(llm_source, dict) else None
    if not isinstance(evidence, dict) or not evidence:
        return None
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
        "reference_output": {
            key: project.get(key)
            for key in (
                "category", "lexicon_scope", "service_status", "technical_interest_level",
                "name", "tagline", "summary", "features",
            )
        },
        "reference_is_gold": False,
        "evidence": evidence,
    }


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
    uri = f"file:{urllib.parse.quote(str(resolved))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT run_id, project_key, completed_at, output_project_json "
            "FROM investigation_runs ORDER BY completed_at DESC, run_id DESC"
        ).fetchall()
    finally:
        conn.close()

    candidates: list[dict[str, Any]] = []
    seen_projects: set[str] = set()
    for row in rows:
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
    for field in ("tagline_is_provisional", "manual_review_recommended"):
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
    summary = str(payload.get("summary") or "")
    conflict = category_summary_conflict(category, summary)
    if conflict:
        errors.append("category_summary_conflict:library_executable_role")
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
        "category", "lexicon_scope", "service_status", "technical_interest_level",
        "name", "tagline", "summary", "features",
    ):
        before = reference.get(field)
        after = payload.get(field)
        if before != after:
            changes.append({"field": field, "reference": before, "candidate": after})
    return changes


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
    case_ids: set[str] | None = None,
    root: Path | None = None,
    progress_callback: Callable[[list[dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:
    if int(corpus.get("schema_version") or 0) != CORPUS_SCHEMA_VERSION:
        raise ValueError("unsupported LLM evaluation corpus schema")
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    cases = [case for case in corpus.get("cases", []) if isinstance(case, dict)]
    if case_ids:
        available_ids = {str(case.get("case_id") or "") for case in cases}
        missing_ids = sorted(case_ids - available_ids)
        if missing_ids:
            raise ValueError(f"unknown corpus case IDs: {', '.join(missing_ids)}")
        cases = [case for case in cases if str(case.get("case_id")) in case_ids]
    results: list[dict[str, Any]] = []
    for case in cases:
        evidence = case.get("evidence")
        if not isinstance(evidence, dict):
            continue
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
                })
            except Exception as exc:
                result.update({"error_type": type(exc).__name__, "error": str(exc)})
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
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": iso_now(),
        "status": "complete",
        "corpus_hash": corpus_content_hash(corpus),
        "corpus_case_count": len(cases),
        "git": git_state(root) if root else {"commit": "", "dirty": None},
        "model_spec": model_spec,
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
            "total_duration_ms": sum(int(result["duration_ms"]) for result in results),
            "prompt_buckets": bucket_summary,
        },
        "results": results,
    }
