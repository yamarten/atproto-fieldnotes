"""Reasoned operator decisions about AT Protocol usage.

This is independent of service availability, schema ownership and technical
interest. LLM output and observations never establish a role automatically.
"""
from __future__ import annotations

from typing import Any

ROLES = {"main_data", "supporting", "bluesky_client", "auth_only", "unknown"}
LIMITED_ROLES = {"supporting", "bluesky_client", "auth_only"}


def projection(project: dict[str, Any]) -> dict[str, Any]:
    override = active_override(project.get("atproto_usage_decisions"))
    if override:
        return {**override, "source": "operator", "sources": [], "stale": False}
    return {"role": "unknown", "reason": "利用範囲の手動判断は未記録です。OAuth宣言と関連記述を確認してください。",
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
