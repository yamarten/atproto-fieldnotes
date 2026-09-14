"""A maintained description, with a bounded proposal instead of a version history."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

FIELDS = ("tagline", "summary", "features")
CHANGE_KINDS = {"correction", "changed_facts", "expansion"}


def profile(project: dict[str, Any]) -> dict[str, Any]:
    return {"tagline": str(project.get("tagline") or ""),
            "summary": str(project.get("summary") or ""),
            "features": list(project.get("features") or [])}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def automatic(project: dict[str, Any]) -> bool:
    state = project.get("editorial") or {}
    mode = state.get("mode")
    if state.get("profile_hash") and state["profile_hash"] != fingerprint(profile(project)):
        return False
    if mode in {"automatic", "review"}:
        return mode == "automatic"
    # Existing wording without provenance must not silently become machine-owned.
    return not any(profile(project).values())


def source(source_type: str, locator: str, content: str, observed_at: str = "") -> dict[str, Any]:
    text = content[:1800]
    identity = fingerprint([source_type, locator, text])[:20]
    return {"id": identity, "type": source_type, "locator": locator,
            "content": text, "observed_at": observed_at}


def sources(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Use actual bounded material, never the saved description as its own evidence."""
    result = []
    for item in project.get("repository_analysis_results", []):
        if not isinstance(item, dict) or item.get("stale"):
            continue
        # select_for_triage supplies only grounded latest successful analyses.
        text = str(item.get("summary") or "")
        if not text or not item.get("summary_evidence"):
            continue
        revision = str(item.get("source_revision") or item.get("revision") or "")
        locator = str(item.get("repo_url") or "")
        if not locator or not revision:
            continue
        content = text + "\n" + json.dumps(item.get("capabilities", []), ensure_ascii=False)
        result.append(source("repository", locator + "@" + revision, content,
                             str(item.get("analyzed_at") or "")))
    for item in project.get("service_verification_results", []):
        if not isinstance(item, dict) or not item.get("supports_project"):
            continue
        if item.get("fetch_error"):
            continue
        if item.get("lane") == "browser":
            text = "\n".join(str(page.get("text_extract") or "") for page in item.get("pages", [])
                             if isinstance(page, dict))
            if text:
                result.append(source("browser_page", str(item.get("requested_url") or ""),
                                     text, str(item.get("observed_at") or "")))
            continue
        if not 0 < int(item.get("status_code") or 0) < 400:
            continue
        text = str(item.get("html_extract") or "").strip()
        if text:
            result.append(source("service_page", str(item.get("requested_url") or ""),
                                 text, str(item.get("observed_at") or "")))
    for sample in project.get("lexicon_schema_samples", [])[:4]:
        if not isinstance(sample, dict):
            continue
        nsid = str(sample.get("nsid") or sample.get("id") or "")
        if nsid in project.get("resolved_nsids", []):
            result.append(source("lexicon", nsid, json.dumps(sample, ensure_ascii=False)))
    return result[:8]


def apply_proposal(
    project: dict[str, Any], proposed: dict[str, Any], change: Any,
    material: list[dict[str, Any]], *, generated_at: str,
) -> None:
    before = profile(project)
    is_automatic = automatic(project)
    state = copy.deepcopy(project.get("editorial") or {})
    state["mode"] = "automatic" if is_automatic else "review"
    # Missing fields are not a request to remove known information.
    after = {field: copy.deepcopy(proposed.get(field, before[field])) for field in FIELDS}
    for field in ("tagline", "summary"):
        if not after[field]:
            after[field] = before[field]
    if before == after:
        project["editorial"] = state
        return
    change = change if isinstance(change, dict) else {}
    references = {str(item.get("id")): item for item in material if isinstance(item, dict)}
    raw_ids = change.get("sources")
    ids = list(dict.fromkeys(item for item in raw_ids if isinstance(item, str))) if isinstance(raw_ids, list) else []
    kind = str(change.get("kind") or "")
    reason = str(change.get("reason") or "").strip()[:1000]
    supported = kind in CHANGE_KINDS and bool(reason) and 0 < len(ids) <= 4 and all(i in references for i in ids)
    if not any(before.values()):
        kind = "initial"
    elif not supported:
        # A longer answer or a transient failure is not evidence of changed facts.
        project["editorial"] = state
        return
    proposal = {"profile": after, "base_hash": fingerprint(before), "kind": kind,
                "reason": reason, "sources": [references[i] for i in ids[:4] if i in references],
                "generated_at": generated_at}
    if is_automatic:
        project.update(after)
        if after["tagline"] != before["tagline"]:
            project["tagline_is_provisional"] = True
        state.pop("proposal", None)
        state["last_update"] = {key: value for key, value in proposal.items() if key not in {"profile", "base_hash"}}
        state["profile_hash"] = fingerprint(after)
    elif fingerprint([after, ids]) != state.get("dismissed_proposal"):
        state["proposal"] = proposal
    project["editorial"] = state


def dismiss_proposal(project: dict[str, Any]) -> None:
    state = copy.deepcopy(project.get("editorial") or {})
    proposal = state.pop("proposal", None)
    if proposal:
        state["dismissed_proposal"] = fingerprint([proposal["profile"], [s["id"] for s in proposal["sources"]]])
    project["editorial"] = state


def record_edit(project: dict[str, Any], *, allow_automatic: bool, updated_at: str) -> None:
    state = copy.deepcopy(project.get("editorial") or {})
    state["mode"] = "automatic" if allow_automatic else "review"
    proposal = state.pop("proposal", None)
    state.pop("dismissed_proposal", None)
    state["last_update"] = {"kind": "operator_edit", "generated_at": updated_at}
    if proposal and proposal.get("profile") == profile(project):
        state["last_update"].update({"kind": "operator_accepted_proposal", "change_kind": proposal.get("kind"),
                                     "reason": proposal.get("reason"), "sources": proposal.get("sources", [])})
    state["profile_hash"] = fingerprint(profile(project))
    project["editorial"] = state
