"""Unadopted service hypotheses, browser assessments, and durable retry state.

Observation is deliberately separate from service ownership and availability.
This module does not fetch, open a database, or confirm an identifier.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import re
import urllib.parse
from typing import Any

from . import collector_relation_policy, project_record, service, evaluation, oauth_metadata
from .identity import identifiers
from .persistence import identifier_storage, runtime_view
from .repositories import repository_analysis

POLICY_VERSION = "service-url-investigation-v2"
STATE_FIELD = "service_url_investigations"
LANE = "browser_discovery"
TRACKING_STATES = {"active", "stable", "stalled", "out_of_scope"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def target_url(value: str) -> str:
    """Syntactic public-service filter; DNS is checked at navigation time."""
    try:
        url = project_record.normalize_service_candidate_url(value) or ""
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if not url or parsed.username or parsed.password or host.endswith(".internal"):
            return ""
        if collector_relation_policy.classify_url_role(url) != "unknown":
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        return url if not service.SUPPORTING_PAGE_PATHS.intersection(
            {parsed.path.strip("/").split("/", 1)[0].lower()}) else ""
    except ValueError:
        return ""


def guarded(project: dict[str, Any], url: str) -> bool:
    return (project_record.identifier_is_blocked(project, "service_url", url)
            or project_record.identifier_is_deferred(project, "service_url", url))


def hypotheses(project: dict[str, Any], *, include_guarded: bool = False) -> dict[str, dict[str, Any]]:
    """Only existing service candidates, adopted repo placements, and probed authorities."""
    result: dict[str, dict[str, Any]] = {}
    confirmed = set(project.get("service_urls", []))

    def add(raw: str, source: dict[str, Any]) -> None:
        url = target_url(raw)
        if not url or url in confirmed or (not include_guarded and guarded(project, url)):
            return
        item = result.setdefault(url, {"url": url, "sources": []})
        if source not in item["sources"]:
            item["sources"].append(source)

    for url in project_record.service_url_candidate_values(project):
        add(url, {"kind": "candidate"})
    for metadata in oauth_metadata.usable_observations(project, fresh=True):
        add(oauth_metadata.web_surface(metadata), {"kind": "oauth_metadata", "metadata_url": metadata["metadata_url"],
                                                  "declaration": digest(metadata["analysis"])})
    repositories = set(project.get("project_repository_urls", []))
    if runtime_view.RUNTIME_ADOPTION_ROWS in project:
        repositories &= {value for kind, value, role in identifier_storage.active_adoption_claims(project)
                         if kind == "repo_url" and role == "project_repository"}
    else:
        repositories &= (set(project_record.active_confirmed_identifier_values(project, "repo_url"))
                         | set(project_record.active_manual_repo_urls(project)))
    api_urls = set()
    for claim in repository_analysis.service_publications(project, confirmed_only=True):
        if claim["repo_url"] not in repositories:
            continue
        if claim.get("service_kind") in {"api", "pds"}:
            api_urls.add(identifiers.normalize_url(claim["url"]))
            continue
        add(claim["url"], {"kind": "repository", "repo_url": claim["repo_url"],
                           "placement": "publication", "evidence": digest(claim.get("evidence"))})
    for item in project.get("relation_evidence", []):
        relation = item.get("relation", {})
        subject, obj = relation.get("subject", {}), relation.get("object", {})
        if (item.get("classification") == "confirmed" and relation.get("predicate") == "repository_homepage"
                and subject.get("type") == "repo" and subject.get("value") in repositories
                and obj.get("type") == "website"):
            add(obj.get("value", ""), {"kind": "repository", "repo_url": subject["value"],
                                        "placement": "homepage"})
    authorities: dict[str, list[str]] = {}
    for nsid in project.get("resolved_nsids", []):
        if nsid.startswith(project_record.BUILTIN_LEXICON_PREFIXES):
            continue
        domain = identifiers.derive_authority_domain(nsid)
        if domain:
            authorities.setdefault(domain, []).append(nsid)
    observations = {}
    for value in sorted(project.get("service_verification_results", []),
                        key=lambda value: service.observation_time(value) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)):
        if value.get("lane") in {"static", "authority", "repository_homepage"}:
            observations[value.get("requested_url", "")] = value
    for value in observations.values():
        raw = value.get("requested_url", "")
        host = urllib.parse.urlsplit(raw).hostname
        if (value.get("lane") in {"static", "authority"} and host in authorities
                and identifiers.normalize_url(raw) == f"https://{host}"
                and 0 < int(value.get("status_code") or 0) < 400
                and not value.get("fetch_error") and not evaluation.service_probe_looks_parked(value)):
            # Keep only the latest semantic page observation for this source.
            url = target_url(raw)
            if url in result:
                result[url]["sources"] = [s for s in result[url]["sources"] if s["kind"] != "authority"]
            add(raw, {"kind": "authority", "nsids": sorted(authorities[host]),
                      "content_hash": value.get("content_sha256", ""),
                      "final_url": value.get("final_url", raw)})
    previous = {item.get("url"): item for item in project.get(STATE_FIELD, [])}
    # The qualified authority origin survives eviction of the detailed probe.
    # It is still tied to the currently owned namespace, never to our candidate.
    for url, prior in previous.items():
        host = urllib.parse.urlsplit(url).hostname
        if host in authorities and url == f"https://{host}" and not any(
            source["kind"] == "authority" for source in result.get(url, {}).get("sources", [])
        ):
            for source in prior.get("sources", []):
                if source["kind"] == "authority":
                    add(url, {**source, "nsids": sorted(authorities[host])})
    for url in api_urls:
        result.pop(url, None)
    for url, item in result.items():
        proofs = [s for s in item["sources"] if s["kind"] != "candidate"]
        # A candidate created by this investigation is not a new independent source.
        if not proofs and previous.get(url, {}).get("candidate_added"):
            item["sources"] = []
        meaningful = proofs or item["sources"]
        page = observations.get(url)
        item["page_semantics"] = ([page.get("content_sha256", ""), page.get("final_url", url),
                                   page.get("status_code", 0), page.get("fetch_error", "")]
                                  if page else previous.get(url, {}).get("page_semantics", []))
        item["fingerprint"] = digest([POLICY_VERSION, url, sorted(meaningful, key=digest), item["page_semantics"]])
        item["group"] = next((kind for kind in ("repository", "authority", "oauth_metadata", "candidate")
                               if any(s["kind"] == kind for s in meaningful)), "")
    return {url: item for url, item in result.items() if item["sources"]}


def due(item: dict[str, Any], previous: dict[str, Any], now: dt.datetime) -> bool:
    if previous.get("fingerprint") != item["fingerprint"]:
        return True
    retry = project_record.parse_iso(previous.get("retry_at"))
    return retry is not None and retry <= now


def assessment(value: dict[str, Any]) -> dict[str, Any]:
    return service.browser_surface_assessment(value)


def apply(project: dict[str, Any], value: dict[str, Any], observed_at: str) -> None:
    """Apply a replayable attempt after checking its still-current input proof."""
    item = value.get("hypothesis", {})
    url = item.get("url", "")
    current = hypotheses(project).get(url)
    if not current or current["fingerprint"] != item.get("fingerprint"):
        return
    records = {entry["url"]: dict(entry) for entry in project.get(STATE_FIELD, []) if entry.get("url")}
    previous = records.get(url, {})
    previous_time = project_record.parse_iso(previous.get("attempted_at"))
    incoming_time = project_record.parse_iso(observed_at)
    if previous_time and (not incoming_time or previous_time > incoming_time):
        return
    facts = assessment(value)
    errors = value.get("fetch_error", "")
    failed = bool(errors or not facts["successful_page"])
    failure_count = int(previous.get("failure_count", 0)) + 1 if failed else 0
    retry_at = ""
    now = project_record.parse_iso(observed_at)
    if failed and now and facts["status_code"] not in {404, 410}:
        retry_at = (now + dt.timedelta(days=(7, 30, 90)[min(failure_count - 1, 2)])).isoformat()
    entry = {"url": url, "policy_version": POLICY_VERSION, "sources": item["sources"],
             "run_id": item.get("run_id", ""),
             "page_semantics": item.get("page_semantics", []),
             "fingerprint": item["fingerprint"], "attempted_at": observed_at,
             "status": "failed" if failed else "observed", "failure_count": failure_count,
             "retry_at": retry_at, "assessment": facts,
             "candidate_added": bool(previous.get("candidate_added")),
             "last_success": previous.get("last_success", {})}
    if not failed:
        entry["last_success"] = {"observed_at": observed_at, "fingerprint": item["fingerprint"],
                                 "run_id": item.get("run_id", ""),
                                 "assessment": facts}
    records[url] = entry
    project[STATE_FIELD] = list(records.values())
    reconcile(project)


def supports_candidate(project: dict[str, Any], url: str) -> bool:
    if not project.get(STATE_FIELD):
        return False
    current = hypotheses(project).get(url)
    entry = next((v for v in project.get(STATE_FIELD, []) if v.get("url") == url), {})
    success = entry.get("last_success", {})
    facts = success.get("assessment", {})
    return bool(current and success.get("fingerprint") == current["fingerprint"]
                and facts.get("successful_page") and facts.get("positive_surface")
                and facts.get("atproto_evidence")
                and identifiers.normalize_url(facts.get("url", "")) == url)


def retains_candidate(project: dict[str, Any], url: str) -> bool:
    """Inspecting a pre-existing hypothesis is not a decision to reject it."""
    if not project.get(STATE_FIELD) or guarded(project, url):
        return False
    entry = next((v for v in project[STATE_FIELD] if v.get("url") == url), {})
    return bool(entry and not entry.get("candidate_added")
                and any(source["kind"] == "candidate" for source in entry.get("sources", []))
                and url in hypotheses(project))


def reconcile(project: dict[str, Any]) -> None:
    if not project.get(STATE_FIELD):
        return
    current = hypotheses(project, include_guarded=True)
    retained = []
    candidates = project_record.service_url_candidate_values(project)
    for entry in project.get(STATE_FIELD, []):
        url = entry.get("url", "")
        if url not in current:
            if entry.get("candidate_added") and url in candidates:
                candidates.remove(url)
            continue
        retained.append(entry)
        if supports_candidate(project, url) and url not in candidates:
            candidates.append(url)
            entry["candidate_added"] = True
    project[STATE_FIELD] = retained
    project_record.set_service_url_candidate_values(project, candidates)


def triage_observations(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Unadopted page facts never enter editorial.description_sources."""
    if not project.get(STATE_FIELD):
        return []
    current = hypotheses(project)
    return [{"url": entry["url"], "attribution": "unconfirmed",
             "observation": entry["assessment"]}
            for entry in project.get(STATE_FIELD, [])
            if entry.get("url") in current and entry.get("fingerprint") == current[entry["url"]]["fingerprint"]
            and entry.get("status") == "observed"][:8]


def cached_assessment(project: dict[str, Any], url: str) -> dict[str, Any]:
    current = hypotheses(project).get(url)
    entry = next((v for v in project.get(STATE_FIELD, []) if v.get("url") == url), {})
    success = entry.get("last_success", {})
    observed = project_record.parse_iso(success.get("observed_at"))
    now = project_record.parse_iso(project_record.iso_now())
    if (current and success.get("fingerprint") == current["fingerprint"] and observed
            and now - observed <= dt.timedelta(days=30)):
        return {**success.get("assessment", {}), "observed_at": success["observed_at"],
                "run_id": success.get("run_id", "")}
    return {}
