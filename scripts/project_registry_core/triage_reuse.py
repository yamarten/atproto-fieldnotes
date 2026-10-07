"""Reuse a successful triage inference, while collection and application still run."""
from __future__ import annotations

import copy
import datetime as dt
import functools
import hashlib
import os
from pathlib import Path
from typing import Any

from . import editorial, llm_evidence
from .transport import llm_transport
from .persistence import runtime_view
from .project_record import PROJECT_CATEGORY_VALUES

CACHE_KEY = "triage_reuse"
MAX_AGE = dt.timedelta(days=30)
# Only these direct metadata fields are observation bookkeeping. Never strip
# timestamps recursively: a record's createdAt, schema default, or quoted date
# is material, as are source order, content, status, revision and stale flags.
OBSERVATION_FIELDS = {
    "description_sources": {"observed_at"},
    "summary_candidates": {"added_at"},
    "tagline_candidates": {"added_at"},
    "lexicon_resolution_entries": {"resolved_at"},
}


def digest(value: Any) -> str:
    return hashlib.sha256(llm_evidence.evidence_json(value).encode("utf-8")).hexdigest()


def semantic_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(evidence)
    for field, ignored in OBSERVATION_FIELDS.items():
        if isinstance(result.get(field), list):
            result[field] = [
                {key: value for key, value in item.items() if key not in ignored}
                if isinstance(item, dict) else item
                for item in result[field]
            ]
    return result


@functools.lru_cache(maxsize=1)
def policy_digest() -> str:
    # Prompt construction/rule changes are represented by the rendered prompt
    # and current evidence. Application/evaluation rules run again on every hit.
    # Hash the remaining inference contract, without invalidating every cached
    # inference for unrelated CLI/UI or collector implementation changes.
    root = Path(__file__).parent
    modules = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (root / "triage_reuse.py", Path(llm_transport.__file__))
    }
    return digest([modules, sorted(PROJECT_CATEGORY_VALUES)])


def request_fingerprint(prompt: str, evidence: dict[str, Any], **options: Any) -> str:
    serialized = llm_evidence.evidence_json(evidence)
    # An unfamiliar prompt format stays exact; do not guess where evidence is.
    if prompt.count(serialized) == 1:
        prompt = prompt.replace(serialized, llm_evidence.evidence_json(semantic_evidence(evidence)), 1)
    settings = {
        "endpoint": (options.get("llm_endpoint") or os.getenv("ATPROTO_SURVEY_LLM_ENDPOINT", "")).strip(),
        "model": (options.get("llm_model") or os.getenv("ATPROTO_SURVEY_LLM_MODEL", "")).strip(),
        "api_key": (options.get("llm_api_key") or os.getenv("ATPROTO_SURVEY_LLM_API_KEY", "")).strip(),
        "api_style": llm_transport.normalize_llm_api_style(
            options.get("llm_api_style") or os.getenv("ATPROTO_SURVEY_LLM_API_STYLE", "auto")),
        "reasoning_effort": llm_transport.normalize_llm_reasoning_effort(
            options.get("llm_reasoning_effort") or os.getenv("ATPROTO_SURVEY_LLM_REASONING_EFFORT", "")),
        "max_output_tokens": int(options.get("llm_max_output_tokens") or 0),
        "prompt_version": os.getenv("ATPROTO_SURVEY_LLM_PROMPT_VERSION", llm_transport.DEFAULT_LLM_PROMPT_VERSION).strip(),
        "timeout": llm_transport.llm_timeout_seconds(),
    }
    # Only the combined hash is retained, never credentials or prompt text.
    return digest([policy_digest(), prompt, settings, options.get("collection_state", {})])


def application_guard(project: dict[str, Any]) -> str:
    state = project.get("editorial") or {}
    update = state.get("last_update") or {}
    return digest({
        "decisions": {field: project.get(field, []) for field in sorted(runtime_view.DECISION_FIELDS)},
        "automatic_editorial": editorial.automatic(project),
        "dismissed_proposal": state.get("dismissed_proposal"),
        "operator_update": update if str(update.get("kind", "")).startswith("operator_") else {},
        "category": project.get("category"),
        "llm_output": project.get("llm_output"),
    })


def eligibility_reason(**options: Any) -> str:
    if os.getenv("ATPROTO_SURVEY_TRIAGE_REUSE", "1").strip() == "0":
        return "disabled"
    # Commands may depend on arbitrary files/environment beyond their prompt.
    if os.getenv("ATPROTO_SURVEY_LLM_COMMAND", "").strip():
        return "command_transport"
    if not (options.get("llm_endpoint") or os.getenv("ATPROTO_SURVEY_LLM_ENDPOINT", "")).strip():
        return "unconfigured_endpoint"
    if not (options.get("llm_model") or os.getenv("ATPROTO_SURVEY_LLM_MODEL", "")).strip():
        return "unconfigured_model"
    return ""


def lookup(project: dict[str, Any], fingerprint: str, now: str) -> tuple[tuple[dict, dict] | None, str]:
    source = project.get("llm_source") or {}
    entry = source.get(CACHE_KEY)
    if not isinstance(entry, dict):
        return None, "no_entry"
    if entry.get("fingerprint") != fingerprint:
        return None, "input_or_policy_changed"
    if entry.get("application_guard") != application_guard(project):
        return None, "application_state_changed"
    try:
        generated = dt.datetime.fromisoformat(str(source.get("generated_at") or "").replace("Z", "+00:00"))
        age = dt.datetime.fromisoformat(now.replace("Z", "+00:00")) - generated
        if not dt.timedelta(0) <= age < MAX_AGE:
            return None, "expired"
    except (ValueError, TypeError):
        return None, "invalid_generation_time"
    payload = project.get("llm_output")
    if not isinstance(payload, dict) or not payload:
        return None, "invalid_output"
    return (copy.deepcopy(payload), copy.deepcopy(source)), "hit"


def remember(project: dict[str, Any], fingerprint: str) -> None:
    payload = project.get("llm_output")
    if isinstance(payload, dict) and isinstance(payload.get("category"), str) and payload["category"] in PROJECT_CATEGORY_VALUES:
        project["llm_source"][CACHE_KEY] = {
            "fingerprint": fingerprint,
            "application_guard": application_guard(project),
        }


def invalidate(project: dict[str, Any]) -> None:
    if isinstance(project.get("llm_source"), dict):
        project["llm_source"].pop(CACHE_KEY, None)
