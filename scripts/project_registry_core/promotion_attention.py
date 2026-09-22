"""Bounded operator attention state, independent of candidate quality scoring."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from . import project_record
from . import atproto_usage


def snapshot(project: dict[str, Any]) -> dict[str, Any]:
    # Exclude refresh/activity timestamps, counts, page hashes and summary
    # rewrites. Structured changes prompt rechecking, not automatic acceptance.
    scalars = (
        "category", "service_status", "service_status_confidence",
        "atproto_contribution_level", "technical_interest_level", "lexicon_scope",
    )
    lists = (
        "service_urls", "resolved_nsids", "project_repository_urls",
        "technical_interest_reasons",
        "features",
    )
    result = {key: str(project.get(key) or "") for key in scalars}
    if project.get("atproto_usage_decisions"):
        result["atproto_usage"] = atproto_usage.projection(project)["role"]
    result.update({key: sorted(set(str(x) for x in project.get(key, []) if x)) for key in lists})
    # Presence of material fills a gap; wording-only changes must not rotate it.
    result["profile_available"] = {
        key: bool(project.get(key)) for key in ("summary", "tagline", "features")
    }
    result["surface_evidence"] = sorted({
        str(x) for x in project.get("service_status_evidence", [])
        if str(x).startswith(("browser_", "login_surface", "app_shell", "verified_authority", "coming_soon", "docs_only", "beta_text", "invite_text"))
    })
    # A confirmed schema's content is meaningful, unlike its observation time.
    result["schemas"] = sorted({
        hashlib.sha256(json.dumps({key: value for key, value in item.items()
                                  if key not in {"observed_at", "fetched_at"}},
                                 sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        for item in project.get("lexicon_schema_samples", [])
        if isinstance(item, dict) and str(item.get("nsid") or item.get("id") or "") in project.get("resolved_nsids", [])
    })
    return result


def fingerprint(project: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot(project), sort_keys=True, ensure_ascii=False).encode()
    return "promotion-attention-v1:" + hashlib.sha256(encoded).hexdigest()


def record(project: dict[str, Any], outcome: str, *, now: dt.datetime | None = None) -> None:
    now = now or dt.datetime.now(dt.timezone.utc)
    state = dict(project.get("promotion_attention") or {})
    counts = dict(state.get("outcome_counts") or {})
    counts[outcome] = int(counts.get(outcome, 0)) + 1
    state.update(outcome_counts=counts, last_outcome=outcome,
                 last_action_at=now.isoformat(), snapshot=snapshot(project),
                 fingerprint=fingerprint(project))
    if outcome == "reviewed":
        state["reviewed_fingerprint"] = fingerprint(project)
        state["reviewed_until"] = (now + dt.timedelta(days=30)).isoformat()
    elif outcome == "restore":
        state.pop("reviewed_fingerprint", None)
        state.pop("reviewed_until", None)
    project["promotion_attention"] = state


def projection(project: dict[str, Any], *, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    state = project.get("promotion_attention") or {}
    current = fingerprint(project)
    previous = state.get("fingerprint")
    raw_until = state.get("reviewed_until")
    until = project_record.parse_iso(raw_until)
    reviewed = bool(state.get("reviewed_fingerprint") == current and until and until > now)
    changed = bool(previous and previous != current)
    last_defer = (project.get("promotion_deferrals") or [{}])[-1]
    defer_expiry = project_record.parse_iso(last_defer.get("expires_at"))
    due = bool(raw_until or (state.get("last_outcome") == "defer" and defer_expiry and defer_expiry <= now))
    return {
        "group": "reviewed" if reviewed else "changed" if changed else "due" if due else "recorded" if previous else "unreviewed",
        "reviewed_until": raw_until or "",
        "last_action_at": state.get("last_action_at", ""),
        "last_outcome": state.get("last_outcome", ""),
    }
