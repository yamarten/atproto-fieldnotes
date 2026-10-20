"""Bounded, non-semantic observations for later enrich comparisons."""
from __future__ import annotations

import hashlib
import functools
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable

from .repositories import repo_urls
from .private_data import redact

VERSION = 1
STORAGE_KEY = "_enrich_observations"
SECTION_LIMITS = {"planning": 96 * 1024, "search_outcomes": 48 * 1024,
                  "operations": 16 * 1024, "search_execution": 16 * 1024, "triage_changes": 16 * 1024}


@functools.lru_cache(maxsize=1)
def planning_policy() -> dict[str, str]:
    root = Path(__file__).parent
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (
        root / "investigation.py",)}


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def bounded_section(value: Any, limit: int) -> dict[str, Any]:
    changes: list[str] = []
    sanitized = redact(value, "$", changes, preserve_url_details=True)
    encoded = json_bytes(sanitized)
    # An oversized section is explicitly unavailable, never a silently cut input.
    result = {"complete": not changes and len(encoded) <= limit,
              "redacted": bool(changes), "omitted": len(encoded) > limit,
              "original_bytes": len(encoded), "limit_bytes": limit}
    if len(encoded) <= limit:
        result["data"] = sanitized
    return result


def pack(sections: dict[str, Any]) -> dict[str, Any]:
    return {"version": VERSION, **{key: bounded_section(sections[key], limit)
            for key, limit in SECTION_LIMITS.items() if key in sections}}


class OperationTimer:
    """Elapsed operation time including internal retries/cache; overlaps allowed."""
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def call(self, name: str, operation: Callable, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        failed = False
        try:
            return operation(*args, **kwargs)
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = round((time.perf_counter() - started) * 1000, 3)
            with self.lock:
                row = self.rows.setdefault(name, {"calls": 0, "failed_calls": 0, "duration_ms": 0})
                row["calls"] += 1
                row["failed_calls"] += int(failed)
                row["duration_ms"] = round(row["duration_ms"] + elapsed, 3)


def candidate_inventory(project: dict[str, Any]) -> dict[str, set[str]]:
    """Known persisted candidates/references; absence is not global novelty."""
    repos = set()
    handles = set()
    references = set(project.get("evidence_urls", []))
    references.add(str(project.get("website_url") or ""))
    for key in ("service_urls", "service_url_candidates", "project_site_urls", "project_site_url_candidates"):
        references.update(project.get(key, []))
    for key in ("project_repository_urls", "candidate_project_repository_urls"):
        for item in project.get(key, []):
            value = item.get("url", "") if isinstance(item, dict) else item
            if value:
                repos.add(repo_urls.canonical_repo_url(str(value)))
    for key in ("developer_accounts", "candidate_developer_accounts"):
        handles.update(str(item.get("handle") or "") for item in project.get(key, []) if isinstance(item, dict))
    handles.update(project.get("bluesky_handles", []))
    for key in ("source_repo_records", "resource_url_records"):
        references.update(str(item.get("url") or "") for item in project.get(key, []) if isinstance(item, dict))
    references.update(repos)
    return {"repo": repos - {""}, "handle": handles - {""},
            "reference": {repo_urls.canonical_repo_url(str(value)) for value in references if value}}


def search_outcomes(bundle: Any, start: dict, before_search: dict, final: dict) -> list[dict]:
    final_known = candidate_inventory(final)
    confirmed_repos = candidate_inventory({"project_repository_urls": final.get("project_repository_urls", [])})["repo"]
    confirmed_handles = candidate_inventory({"developer_accounts": final.get("developer_accounts", [])})["handle"]
    rows = []
    for record in bundle.records:
        if record.kind == "github_repo_search_candidate":
            kind, value = "repo", repo_urls.canonical_repo_url(str((record.value or {}).get("url") or ""))
        elif record.kind == "developer_handle_candidate":
            kind, value = "handle", str(record.value)
        elif record.kind == "source_code_reference":
            kind, value = "reference", str(record.value)
        else:
            continue
        compared_value = repo_urls.canonical_repo_url(value) if kind == "reference" else value
        confirmed = compared_value in (confirmed_handles if kind == "handle" else confirmed_repos)
        rows.append({"kind": kind, "value": value, "source": record.source,
                     "known_at_start": compared_value in start[kind],
                     "known_before_search": compared_value in before_search[kind],
                     "present_at_end": compared_value in final_known[kind], "confirmed_at_end": confirmed})
    return rows


def triage_changes(project: dict, evidence: dict) -> dict:
    from .triage_reuse import semantic_evidence
    previous = (project.get("llm_source") or {}).get("evidence")
    if not isinstance(previous, dict):
        return {"previous_evidence_available": False}
    before, after = semantic_evidence(previous), semantic_evidence(evidence)
    return {"previous_evidence_available": True,
            "changed_evidence_fields": [key for key in sorted(before.keys() | after.keys())
                                        if before.get(key) != after.get(key)]}


def triage_components(prompt: str, evidence: dict, options: dict, collection_state: dict) -> dict:
    from .transport import llm_transport
    from . import triage_reuse, llm_evidence
    serialized = llm_evidence.evidence_json(evidence)
    template = prompt.replace(serialized, "<evidence>", 1) if prompt.count(serialized) == 1 else prompt
    values = {"template": template, "policy": triage_reuse.policy_digest(),
              **{"setting." + key: value for key, value in llm_transport.request_settings(options).items()},
              **{"collection." + key: value for key, value in collection_state.items()}}
    return {key: hashlib.sha256(json_bytes(value)).hexdigest() for key, value in values.items()}
