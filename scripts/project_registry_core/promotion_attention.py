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
    project["promotion_attention"] = state


def active_deferral(project: dict[str, Any], *, now: dt.datetime | None = None) -> dict[str, Any] | None:
    now = now or dt.datetime.now(dt.timezone.utc)
    deferrals = project_record.normalize_promotion_deferrals(list(project.get("promotion_deferrals", [])))
    if not deferrals:
        return None
    current = fingerprint(project)
    active = [item for item in deferrals
              if not item.get("cleared_at") and (project_record.parse_iso(item["expires_at"]) or now) > now
              and (not item.get("fingerprint") or item["fingerprint"] == current)]
    # Coexisting legacy review/defer decisions keep both deadlines. The hold
    # ends only once neither decision applies, so show the latest active expiry.
    return max(reversed(active), key=lambda item: project_record.parse_iso(item["expires_at"])) if active else None


def projection(project: dict[str, Any], *, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    state = project.get("promotion_attention") or {}
    current = fingerprint(project)
    previous = state.get("fingerprint")
    changed = bool(previous and previous != current)
    holds = [item for item in project.get("promotion_deferrals", []) if not item.get("cleared_at")]
    held = active_deferral(project, now=now) is not None
    changed = changed or any(item.get("fingerprint") and item["fingerprint"] != current for item in holds)
    due = any((project_record.parse_iso(item.get("expires_at")) or now) <= now for item in holds)
    return {
        "group": "held" if held else "changed" if changed else "due" if due else "recorded" if previous else "unreviewed",
        "last_action_at": state.get("last_action_at", ""),
        "last_outcome": state.get("last_outcome", ""),
    }
