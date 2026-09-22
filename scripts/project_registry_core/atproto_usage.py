"""Evidence-backed role of AT Protocol in a project's main function.

This is independent of service availability, schema ownership and technical
interest. Missing assessments never inherit a role from NSIDs or profile text.
"""
from __future__ import annotations

from typing import Any

from . import editorial, repository_analysis

ROLES = {"main_data", "supporting", "bluesky_client", "auth_only", "unknown"}
LIMITED_ROLES = {"supporting", "bluesky_client", "auth_only"}


def normalize(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    entry = value.get("automatic")
    if not isinstance(entry, dict) or not isinstance(entry.get("role"), str) or entry["role"] not in ROLES:
        return {}
    reason = str(entry.get("reason") or "").strip()[:1000]
    if not reason:
        return {}
    clean = {"role": entry["role"], "reason": reason,
             "assessed_at": str(entry.get("assessed_at") or ""), "sources": [
                 {k: str(source.get(k) or "") for k in ("id", "type", "locator")}
                 for source in (entry.get("sources") if isinstance(entry.get("sources"), list) else [])
                 if isinstance(source, dict) and isinstance(source.get("id"), str) and source["id"]
             ][:4]}
    if clean["role"] != "unknown" and not clean["sources"]:
        return {}
    return {"automatic": clean}


def material(project: dict[str, Any]) -> list[dict[str, Any]]:
    return editorial.sources({
        **project, "repository_analysis_results": repository_analysis.select_for_triage(project),
    })


def apply_assessment(project: dict[str, Any], value: Any, sources: list[dict[str, Any]], *, assessed_at: str) -> None:
    state = normalize(project.get("atproto_usage"))
    # Each completed triage replaces the automatic assessment, including unknown.
    # Unsupported new output must not silently retain an earlier positive role.
    state.pop("automatic", None)
    value = value if isinstance(value, dict) else {}
    role = value.get("role")
    reason = str(value.get("reason") or "").strip()[:1000]
    ids = value.get("sources")
    catalog = {s["id"]: s for s in sources if isinstance(s, dict) and s.get("id")}
    valid_ids = (isinstance(ids, list) and len(ids) <= 4
                 and all(isinstance(i, str) and i in catalog for i in ids))
    # Schema shape alone cannot establish where the app's main function runs,
    # nor prove that a profile is its *only* use of the protocol.
    contextual = valid_ids and any(catalog[i].get("type") != "lexicon" for i in ids)
    if isinstance(role, str) and role in ROLES and reason and (role == "unknown" or contextual):
        state["automatic"] = {"role": role, "reason": reason, "assessed_at": assessed_at,
                              "sources": [catalog[i] for i in dict.fromkeys(ids)] if valid_ids else []}
    project["atproto_usage"] = normalize(state)


def projection(project: dict[str, Any]) -> dict[str, Any]:
    state = normalize(project.get("atproto_usage"))
    override = active_override(project.get("atproto_usage_decisions"))
    if override:
        return {**override, "source": "operator", "sources": [], "stale": False}
    automatic = state.get("automatic")
    if automatic:
        current_ids = {s["id"] for s in material(project)}
        stale = any(s["id"] not in current_ids for s in automatic["sources"])
        if not stale:
            return {**automatic, "source": "llm", "stale": False}
        return {"role": "unknown", "reason": "評価に使った根拠が変わりました。再調査が必要です。",
                "source": "llm", "sources": automatic["sources"], "stale": True}
    return {"role": "unknown", "reason": "主機能でのatproto利用は未評価です。",
            "source": "none", "sources": [], "stale": False}


def set_override(project: dict[str, Any], *, role: str, reason: str, assessed_at: str, clear: bool = False) -> None:
    if not clear and (not isinstance(role, str) or role not in ROLES or not isinstance(reason, str) or not reason.strip()):
        raise ValueError("valid atproto usage role and reason are required")
    entries = normalize_decisions(project.get("atproto_usage_decisions"))
    for entry in entries:
        if not entry["cleared_at"]:
            entry["cleared_at"] = assessed_at
    if not clear:
        entries.append({"role": role, "reason": reason.strip()[:1000],
                        "assessed_at": assessed_at, "cleared_at": ""})
    project["atproto_usage_decisions"] = entries


def normalize_decisions(value: Any) -> list[dict[str, str]]:
    return [
        {"role": entry["role"], "reason": entry["reason"].strip()[:1000],
         "assessed_at": str(entry.get("assessed_at") or ""),
         "cleared_at": str(entry.get("cleared_at") or "")}
        for entry in (value if isinstance(value, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("role"), str)
        and entry["role"] in ROLES and isinstance(entry.get("reason"), str) and entry["reason"].strip()
    ]


def active_override(value: Any) -> dict[str, str] | None:
    return next((entry for entry in reversed(normalize_decisions(value)) if not entry["cleared_at"]), None)


def merge_decisions(target: Any, source: Any, *, cleared_at: str) -> list[dict[str, str]]:
    left, right = normalize_decisions(target), normalize_decisions(source)
    a, b = active_override(left), active_override(right)
    if a and b and a["role"] != b["role"]:
        raise ValueError("conflicting operator atproto usage roles; resolve before merging")
    if a:
        for entry in right:
            if not entry["cleared_at"]:
                entry["cleared_at"] = cleared_at
    return left + right
