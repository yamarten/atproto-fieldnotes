from __future__ import annotations

import copy
import json
from typing import Any


LLM_EVIDENCE_MAX_BYTES = 24 * 1024
LLM_EVIDENCE_STRING_MAX_BYTES = 4 * 1024
LLM_EVIDENCE_COUNT_LIMITS = {
    "manual_seeds": 24,
    "input_nsids": 64,
    "resolved_nsids": 64,
    "recorded_lexicon_namespaces": 32,
    "lexicon_resolution_entries": 32,
    "publication_schemas": 12,
    "lexicon_schema_samples": 6,
    "recent_record_samples": 12,
    "record_creators": 32,
    "authority_domains": 32,
    "service_urls": 24,
    "resource_url_records": 24,
    "service_url_candidates": 24,
    "failed_service_urls": 24,
    "supported_service_urls": 24,
    "lexicon_scope_evidence": 16,
    "project_repository_urls": 24,
    "candidate_project_repository_urls": 8,
    "repository_link_candidates": 12,
    "bluesky_handles": 16,
    "tangled_handles": 16,
    "developer_accounts": 24,
    "candidate_developer_accounts": 24,
    "summary_candidates": 16,
    "description_sources": 8,
    "tagline_candidates": 16,
    "evidence_urls": 32,
}
LLM_EVIDENCE_TRIM_ORDER = (
    "evidence_urls",
    "candidate_developer_accounts",
    "summary_candidates",
    "tagline_candidates",
    "resource_url_records",
    "description_sources",
    "recent_record_samples",
    "lexicon_schema_samples",
    "publication_schemas",
    "lexicon_resolution_entries",
    "service_url_candidates",
    "failed_service_urls",
    "supported_service_urls",
    "lexicon_scope_evidence",
    "candidate_project_repository_urls",
    "repository_link_candidates",
    "record_creators",
    "authority_domains",
    "recorded_lexicon_namespaces",
    "input_nsids",
    "resolved_nsids",
    "developer_accounts",
    "bluesky_handles",
    "tangled_handles",
    "project_repository_urls",
    "service_urls",
    "manual_seeds",
)
LLM_EVIDENCE_MIN_COUNTS = {
    "manual_seeds": 1,
    "description_sources": 1,
    "input_nsids": 1,
    "resolved_nsids": 1,
    "lexicon_schema_samples": 1,
    "recent_record_samples": 1,
    "resource_url_records": 1,
    "project_repository_urls": 1,
    "service_urls": 1,
    "developer_accounts": 1,
}


def evidence_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def evidence_bytes(value: dict[str, Any]) -> int:
    return len(evidence_json(value).encode("utf-8"))


def without_prior_llm_derived_state(value: dict[str, Any]) -> dict[str, Any]:
    """Remove derived state that explicitly came from an earlier LLM output."""
    evidence = copy.deepcopy(value)
    scope_evidence = evidence.get("lexicon_scope_evidence")
    if isinstance(scope_evidence, list):
        retained = [
            item for item in scope_evidence
            if not str(item or "").startswith("llm_output:")
        ]
        if retained != scope_evidence:
            evidence["lexicon_scope_evidence"] = retained
            if not retained:
                evidence["lexicon_scope"] = "unknown"
    return evidence


def bounded_utf8_string(value: str, max_bytes: int = LLM_EVIDENCE_STRING_MAX_BYTES) -> str:
    encoded = str(value or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return str(value or "")
    suffix = "…"
    suffix_bytes = len(suffix.encode("utf-8"))
    return encoded[: max(0, max_bytes - suffix_bytes)].decode("utf-8", errors="ignore") + suffix


def bound_strings(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        bounded = bounded_utf8_string(value)
        return bounded, int(bounded != value)
    if isinstance(value, list):
        output: list[Any] = []
        changed = 0
        for item in value:
            bounded, item_changed = bound_strings(item)
            output.append(bounded)
            changed += item_changed
        return output, changed
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        changed = 0
        for key, item in value.items():
            bounded, item_changed = bound_strings(item)
            output[str(key)] = bounded
            changed += item_changed
        return output, changed
    return copy.deepcopy(value), 0


def middle_index(length: int) -> int:
    return max(0, (length - 1) // 2)


def balanced_sample(values: list[Any], limit: int) -> list[Any]:
    if len(values) <= limit:
        return list(values)
    front_count = (limit + 1) // 2
    back_count = limit - front_count
    return list(values[:front_count]) + (list(values[-back_count:]) if back_count else [])


REPOSITORY_CLAIM_FIELDS = ("collections", "capabilities", "observations")


def _evidence_locator(value: Any) -> tuple[str, int, str]:
    if not isinstance(value, dict):
        return ("", 0, "")
    return (
        str(value.get("path") or ""),
        int(value.get("line") or 0),
        str(value.get("json_pointer") or ""),
    )


def _trim_repository_claim(result: dict[str, Any]) -> bool:
    """Remove one complete claim and only excerpts unused by retained claims."""
    field = next(
        (name for name in REPOSITORY_CLAIM_FIELDS if result.get(name)), None
    )
    if field is None:
        return False
    claims = result[field]
    claims.pop(middle_index(len(claims)))
    used = {
        _evidence_locator(ref)
        for ref in result.get("summary_evidence", [])
        if isinstance(ref, dict)
    }
    for name in REPOSITORY_CLAIM_FIELDS:
        for claim in result.get(name, []):
            if isinstance(claim, dict):
                used.update(_evidence_locator(ref) for ref in claim.get("evidence", []))
    result["evidence_excerpts"] = [
        excerpt for excerpt in result.get("evidence_excerpts", [])
        if _evidence_locator(excerpt) in used
    ]
    return True


def bounded_llm_evidence(
    evidence: dict[str, Any],
    *,
    max_bytes: int = LLM_EVIDENCE_MAX_BYTES,
) -> dict[str, Any]:
    if max_bytes < 1024:
        raise ValueError("LLM evidence max_bytes must be at least 1024")
    bounded, string_values_truncated = bound_strings(evidence)
    result = dict(bounded)
    observed_counts = {
        field: len(value)
        for field, value in result.items()
        if isinstance(value, list)
    }
    truncated_fields: set[str] = set()
    original_repositories = {
        str(item.get("repo_url") or ""): {
            name: len(item.get(name, []))
            for name in (*REPOSITORY_CLAIM_FIELDS, "evidence_excerpts")
        }
        for item in result.get("repository_analysis_results", [])
        if isinstance(item, dict)
    }
    omitted_repositories: list[str] = []
    for field, limit in LLM_EVIDENCE_COUNT_LIMITS.items():
        values = result.get(field)
        if not isinstance(values, list) or len(values) <= limit:
            continue
        result[field] = balanced_sample(values, limit)
        truncated_fields.add(field)

    def update_budget_metadata() -> None:
        if not truncated_fields and not string_values_truncated:
            result.pop("evidence_budget", None)
            return
        result["evidence_budget"] = {
            "max_bytes": max_bytes,
            "truncated_fields": {
                field: {
                    "observed_count": observed_counts[field],
                    "included_count": len(result.get(field, [])),
                }
                for field in sorted(truncated_fields)
            },
            "string_values_truncated": string_values_truncated,
        }
        if omitted_repositories:
            result["evidence_budget"]["omitted_repository_analysis_urls"] = omitted_repositories

    update_budget_metadata()
    while evidence_bytes(result) > max_bytes:
        trimmed = False
        for allow_below_minimum in (False, True):
            for field in LLM_EVIDENCE_TRIM_ORDER:
                values = result.get(field)
                if not isinstance(values, list) or not values:
                    continue
                minimum = 0 if allow_below_minimum else LLM_EVIDENCE_MIN_COUNTS.get(field, 0)
                if len(values) <= minimum:
                    continue
                values.pop(middle_index(len(values)))
                truncated_fields.add(field)
                update_budget_metadata()
                trimmed = True
                break
            if trimmed:
                break
        if trimmed:
            continue
        repositories = result.get("repository_analysis_results")
        if isinstance(repositories, list) and repositories:
            # Keep every repository overview as long as possible. A claim and its
            # locators stay together; unused excerpt text is removed with it.
            for repo in sorted(repositories, key=lambda item: -len(evidence_json(item))):
                if not _trim_repository_claim(repo):
                    continue
                original = original_repositories.get(str(repo.get("repo_url") or ""), {})
                repo.setdefault("coverage", {})["triage_input"] = {
                    name: {"observed_count": original[name], "included_count": len(repo.get(name, []))}
                    for name in (*REPOSITORY_CLAIM_FIELDS, "evidence_excerpts")
                    if original.get(name, 0) != len(repo.get(name, []))
                }
                truncated_fields.add("repository_analysis_results")
                update_budget_metadata()
                trimmed = True
                break
            if trimmed:
                continue
            removed = repositories.pop(middle_index(len(repositories)))
            omitted_repositories.append(str(removed.get("repo_url") or ""))
            truncated_fields.add("repository_analysis_results")
            update_budget_metadata()
            continue
        raise ValueError("LLM evidence cannot fit within configured byte budget")
    return result
