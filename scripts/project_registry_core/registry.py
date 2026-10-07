#!/usr/bin/env python3

from __future__ import annotations

from project_registry_core import project_review

from project_registry_core import project_merge

from . import lexicon_types

from . import concurrency

import copy
import datetime as dt
import json
import os
import re
import shlex
import socket
import sqlite3
import urllib.error
import urllib.parse
import uuid
from dataclasses import asdict
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from project_registry_core import CollectorResult, EvidenceBundle
from project_registry_core import collector_relation_policy
from project_registry_core import collection_trust

from project_registry_core import homepage_adoption
from project_registry_core import investigation as investigation_core
from project_registry_core import integration as integration_core
from project_registry_core import evaluation as evaluation_core
from project_registry_core import replay as replay_core
from project_registry_core import storage as investigation_storage
from project_registry_core.evidence import EvidenceRecord, add_collector_result_summary
from project_registry_core import relations as relation_core
from project_registry_core import service as service_core
from project_registry_core import tangled as tangled_core
from project_registry_core import trust_graph as trust_graph_core
from project_registry_core import url_alias as url_alias_core
from project_registry_core import reliability as reliability_core
from project_registry_core import database as registry_database
from project_registry_core import runtime_view
from project_registry_core import oauth_metadata
from project_registry_core import llm_evidence as llm_evidence_core
from project_registry_core import domain_policy as domain_policy_core
from project_registry_core import http_transport
from project_registry_core import github_api
from project_registry_core import llm_transport
from project_registry_core import identifiers as identifier_core
from project_registry_core import identifier_storage
from project_registry_core import repo_urls as repo_url_core
from project_registry_core import repository_analysis as repository_analysis_core
from project_registry_core import repository_material as repository_material_core
from project_registry_core import repository_sources as repository_source_core
from project_registry_core import project_record
from project_registry_core import naming as naming_core
from project_registry_core import editorial

from project_registry_core import usage_material


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "survey.db"
CONFIG_PATH = ROOT / "config" / "sources.json"
ENRICH_RUNS_DIR = ROOT / "data" / "enrich-runs"
GITHUB_API = "https://api.github.com"
DNS_RESOLVER_URL = "https://dns.google/resolve"
REQUEST_TIMEOUT_DEFAULT = 60.0
REQUEST_TIMEOUT_SHORT = 15.0
REQUEST_TIMEOUT_SERVICE_CONFIRMED = 45.0
REQUEST_TIMEOUT_SERVICE_CANDIDATE = 15.0
REQUEST_TIMEOUT_LEXICON_DNS = 12.0
REQUEST_TIMEOUT_LEXICON_HTTP = 45.0
REQUEST_TIMEOUT_DISCOVERY_SEARCH = 12.0
ENRICH_IO_MAX_WORKERS = 4
DEEP_INVESTIGATION_PATHS = ("/about", "/docs", "/faq", "/app", "/login", "/signup")
TRIAGE_PROMPT_PATH = ROOT / "prompts" / "project-triage.md"
EDITORIAL_SOURCE_POLICY_VERSION = 4
BSKY_PUBLIC_API = "https://public.api.bsky.app"
BSKY_ENTRYWAY = "https://bsky.social"
BSKY_APPVIEW_PROXY = "did:web:api.bsky.app#bsky_appview"

UFOS_API = "https://ufos-api.microcosm.blue"
SEMBLE_API = "https://api.semble.so"
_BSKY_SESSION_CACHE: dict[str, Any] = {}
_GITHUB_REPO_SEARCH_CACHE = reliability_core.TTLCache()
_SERVICE_PROBE_CACHE = reliability_core.TTLCache()

SEMBLE_TRACKED_COLLECTIONS = (
    {
        "handle": "byarielm.fyi",
        "rkey": "3mfdje7uuhu2r",
        "label": "atproto-visualizers",
    },
    {
        "handle": "byarielm.fyi",
        "rkey": "3mfdk4wmo5m2r",
        "label": "atmosphere-tools",
    },
    {
        "handle": "psingletary.com",
        "rkey": "3mabuiycpg626",
        "label": "AT://links",
    },
)
NSID_PATTERN = r"[a-z0-9]+(?:[.-][a-z0-9]+)*(?:\.[a-z0-9]+(?:[.-][a-z0-9]+)*)+"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


URL_FIELD_INVENTORY: tuple[dict[str, str], ...] = (
    {"field": "website_url", "layer": "canonical", "role": "primary public website"},
    {"field": "introduced_url", "layer": "canonical", "role": "editorial introduction reference"},
    {"field": "project_repository_url", "layer": "canonical", "role": "primary project repository"},
    {"field": "service_urls", "layer": "confirmed_set", "role": "confirmed service surfaces"},
    {"field": "project_repository_urls", "layer": "confirmed_set", "role": "confirmed project repositories"},
    {"field": "service_url_candidates", "layer": "candidate", "role": "unconfirmed service surfaces"},
    {"field": "candidate_project_repository_urls", "layer": "candidate", "role": "unconfirmed project repositories"},
    {"field": "resource_url_records", "layer": "observation", "role": "structured supporting page observations"},
    {"field": "evidence_urls", "layer": "observation", "role": "general supporting URLs"},
    {"field": "failed_service_urls", "layer": "observation", "role": "failed service probes"},
    {"field": "supported_service_urls", "layer": "observation", "role": "supported heuristic service probes"},
)


def format_duration_hms(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def is_bsky_post_url(url: str) -> bool:
    normalized = identifier_core.normalize_url(str(url or "")) if url else ""
    if not normalized:
        return False
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc != "bsky.app":
        return False
    return bool(re.match(r"^/profile/[^/]+/post/[^/]+$", parsed.path))


def exact_service_origin_for_authority_domain(authority_domain: str) -> str:
    lowered = str(authority_domain or "").strip().lower().rstrip(".")
    return f"https://{lowered}" if lowered else ""


def authority_probe_url_chains(project: dict[str, Any]) -> list[list[str]]:
    existing_urls = {
        identifier_core.normalize_url(str(url or ""))
        for url in (
            list(project.get("service_urls", []))
            + project_record.service_url_candidate_values(project)
        )
        if identifier_core.normalize_url(str(url or ""))
    }
    failed_urls = {
        identifier_core.normalize_url(str(url or ""))
        for url in project.get("failed_service_urls", [])
        if identifier_core.normalize_url(str(url or ""))
    }
    chains: list[list[str]] = []
    seen_urls: set[str] = set(existing_urls) | set(failed_urls)
    observed_nsids = project_record.unique_list(list(project.get("resolved_nsids", [])) + list(project.get("input_nsids", [])))
    for raw_nsid in observed_nsids:
        nsid = identifier_core.normalize_nsid(str(raw_nsid or ""))
        if not nsid:
            continue
        authority_domain = identifier_core.derive_authority_domain(nsid)
        chain = [
            identifier_core.normalize_url(url)
            for url in domain_policy_core.candidate_service_origins_for_authority_domain(authority_domain)
            if identifier_core.normalize_url(url)
        ]
        chain = [url for url in chain if url not in seen_urls]
        if not chain:
            continue
        chains.append(chain)
        seen_urls.update(chain)
    return chains


def authority_probe_all_urls(chains: list[list[str]]) -> list[str]:
    return project_record.unique_list([url for chain in chains for url in chain])


def authority_probe_parent_fallback_reason(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return ""
    status_code = int(result.get("status_code") or 0)
    if 400 <= status_code < 500:
        return "http_4xx"
    if str(result.get("fetch_error") or "") == "dns_failed":
        return "dns_failed"
    return ""


def authority_probe_result_is_success(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    status_code = int(result.get("status_code") or 0)
    return 0 < status_code < 400


def select_authority_probe_urls(
    chains: list[list[str]],
    results_by_url: dict[str, dict[str, Any]],
) -> set[str]:
    selected: set[str] = set()
    for chain in chains:
        previous_result: dict[str, Any] | None = None
        for index, url in enumerate(chain):
            result = results_by_url.get(url)
            if authority_probe_result_is_success(result):
                if index > 0 and previous_result is not None:
                    result["authority_fallback_from"] = chain[index - 1]
                    result["authority_fallback_status_code"] = int(previous_result.get("status_code") or 0)
                    fallback_fetch_error = str(previous_result.get("fetch_error") or "")
                    if fallback_fetch_error:
                        result["authority_fallback_fetch_error"] = fallback_fetch_error
                selected.add(url)
                break
            fallback_reason = authority_probe_parent_fallback_reason(result)
            if not fallback_reason:
                break
            previous_result = result
    return selected


def authority_service_candidates_allowed(
    project: dict[str, Any],
    authority_domain: str,
    *,
    verified: bool = False,
) -> bool:
    lowered = str(authority_domain or "").strip().lower().rstrip(".")
    if not lowered:
        return False
    if verified:
        return True
    for host in project_record.ownership_anchor_service_hosts(project):
        if lowered == host or lowered.endswith(f".{host}") or host.endswith(f".{lowered}"):
            return True
    return False


def add_authority_service_candidates(
    project: dict[str, Any],
    authority_domain: str,
    *,
    verified: bool = False,
) -> None:
    # Lexicon authority proves where the NSID record is published, not that the
    # same host is a project service surface. Keep the function as an adapter
    # hook for existing integration paths, but do not synthesize URL candidates.
    _ = (project, authority_domain, verified)


def unverified_authority_service_guesses(project: dict[str, Any]) -> set[str]:
    resolved_nsids = {
        identifier_core.normalize_nsid(str(item or ""))
        for item in project.get("resolved_nsids", [])
        if identifier_core.normalize_nsid(str(item or ""))
    }
    guesses: set[str] = set()
    supported_urls = {
        identifier_core.normalize_url(str(item or ""))
        for item in project.get("supported_service_urls", [])
        if identifier_core.normalize_url(str(item or ""))
    }
    for raw_nsid in project.get("input_nsids", []):
        nsid = identifier_core.normalize_nsid(str(raw_nsid or ""))
        if not nsid or nsid in resolved_nsids:
            continue
        authority_domain = identifier_core.derive_authority_domain(nsid)
        if not authority_domain:
            continue
        if authority_service_candidates_allowed(project, authority_domain, verified=False):
            continue
        guesses.update(
            candidate_url
            for candidate_url in domain_policy_core.candidate_service_origins_for_authority_domain(authority_domain)
            if identifier_core.normalize_url(candidate_url) not in supported_urls
            and not authority_service_candidate_has_record_url(project, candidate_url)
            and not authority_service_candidate_has_saved_probe_support(project, candidate_url)
        )
    return guesses


def urls_from_nested_value(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 5:
        return []
    if isinstance(value, str):
        return extract_urls(value)
    if isinstance(value, dict):
        urls: list[str] = []
        for item in value.values():
            urls.extend(urls_from_nested_value(item, depth=depth + 1))
        return project_record.unique_list(urls)
    if isinstance(value, list):
        urls = []
        for item in value:
            urls.extend(urls_from_nested_value(item, depth=depth + 1))
        return project_record.unique_list(urls)
    return []


def recent_record_sample_urls(project: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for sample in project.get("recent_record_samples", []):
        if not isinstance(sample, dict):
            continue
        urls.extend(urls_from_nested_value(sample.get("record", {})))
    return project_record.unique_list([identifier_core.normalize_url(url) for url in urls if identifier_core.normalize_url(url)])


def authority_service_candidate_has_record_url(project: dict[str, Any], service_url: str) -> bool:
    normalized = identifier_core.normalize_url(service_url)
    host = urllib.parse.urlparse(normalized).netloc.lower()
    if not host:
        return False
    for url in recent_record_sample_urls(project):
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.lower() == host:
            return True
    return False


def authority_service_candidate_has_saved_probe_support(project: dict[str, Any], service_url: str) -> bool:
    normalized = identifier_core.normalize_url(service_url)
    if not normalized:
        return False
    for item in project.get("service_verification_results", []):
        if not isinstance(item, dict):
            continue
        requested_url = identifier_core.normalize_url(str(item.get("requested_url") or ""))
        if requested_url != normalized:
            continue
        result = {**item, "html": str(item.get("html_extract") or item.get("html") or ""), "lane": "authority"}
        if authority_service_probe_candidate_allowed(project, normalized, result):
            return True
    return False


def prunable_nsid_authority_service_guesses(project: dict[str, Any]) -> set[str]:
    guesses = naming_core.nsid_authority_service_origins(project)
    has_nsid_evidence = bool(
        any(identifier_core.normalize_nsid(str(item or "")) for item in project.get("input_nsids", []))
        or any(identifier_core.normalize_nsid(str(item or "")) for item in project.get("resolved_nsids", []))
    )
    if has_nsid_evidence:
        for authority_domain in project.get("authority_domains", []):
            guesses.update(domain_policy_core.candidate_service_origins_for_authority_domain(str(authority_domain or "")))
    for entry in project.get("lexicon_resolution_entries", []):
        if not isinstance(entry, dict):
            continue
        guesses.update(domain_policy_core.candidate_service_origins_for_authority_domain(str(entry.get("authority_domain") or "")))
    if not guesses:
        return set()
    manual_service_seed_urls = [
        project_record.canonical_seed_value(str(item.get("type") or ""), str(item.get("value") or ""))
        for item in project.get("manual_seeds", [])
        if isinstance(item, dict) and str(item.get("type") or "") == "service_url"
    ]
    retained = {
        *(identifier_core.normalize_url(str(item or "")) for item in project.get("service_urls", [])),
        *(identifier_core.normalize_url(str(item or "")) for item in project.get("supported_service_urls", [])),
        *(identifier_core.normalize_url(str(item or "")) for item in manual_service_seed_urls),
        *(url for url in guesses if authority_service_candidate_has_record_url(project, url)),
        *(url for url in guesses if authority_service_candidate_has_saved_probe_support(project, url)),
    }
    return {url for url in guesses if url and url not in retained}


def prune_nsid_authority_service_url_guesses(project: dict[str, Any]) -> None:
    prunable_authority_guesses = prunable_nsid_authority_service_guesses(project)
    if not prunable_authority_guesses:
        return
    project["failed_service_urls"] = [
        url
        for url in project.get("failed_service_urls", [])
        if identifier_core.normalize_url(str(url or "")) not in prunable_authority_guesses
    ]
    project_record.set_service_url_candidate_values(
        project,
        [
            url
            for url in project_record.service_url_candidate_values(project)
            if identifier_core.normalize_url(str(url or "")) not in prunable_authority_guesses
        ],
    )


def prune_stale_service_trust_evaluations(project: dict[str, Any]) -> None:
    graph = trust_graph_core.stored_graph(project)
    evaluations = graph.get("evaluations", [])
    if not evaluations:
        return
    active_service_urls = {
        *(identifier_core.normalize_url(str(item or "")) for item in project.get("service_urls", [])),
        *(identifier_core.normalize_url(str(item or "")) for item in project_record.service_url_candidate_values(project)),
        *(identifier_core.normalize_url(str(item or "")) for item in project.get("supported_service_urls", [])),
        *(identifier_core.normalize_url(str(item or "")) for item in project.get("failed_service_urls", [])),
    }
    active_service_urls.discard("")
    cleaned: list[dict[str, Any]] = []
    changed = False
    for item in evaluations:
        candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
        key = trust_graph_core.node_key(candidate)
        if key[0] == "service_url" and identifier_core.normalize_url(key[1]) not in active_service_urls:
            changed = True
            continue
        cleaned.append(dict(item))
    if changed:
        graph["evaluations"] = cleaned
        trust_graph_core.set_graph(project, graph)


def derived_authority_probe_urls(project: dict[str, Any]) -> list[str]:
    existing_urls = {
        identifier_core.normalize_url(str(url or ""))
        for url in (
            list(project.get("service_urls", []))
            + project_record.service_url_candidate_values(project)
        )
        if identifier_core.normalize_url(str(url or ""))
    }
    failed_urls = {
        identifier_core.normalize_url(str(url or ""))
        for url in project.get("failed_service_urls", [])
        if identifier_core.normalize_url(str(url or ""))
    }
    probe_urls: list[str] = []
    observed_nsids = project_record.unique_list(list(project.get("resolved_nsids", [])) + list(project.get("input_nsids", [])))
    for raw_nsid in observed_nsids:
        nsid = identifier_core.normalize_nsid(str(raw_nsid or ""))
        if not nsid:
            continue
        candidate_url = identifier_core.normalize_url(exact_service_origin_for_authority_domain(identifier_core.derive_authority_domain(nsid)))
        if not candidate_url or candidate_url in existing_urls or candidate_url in failed_urls:
            continue
        if not authority_service_candidate_has_record_url(project, candidate_url):
            continue
        probe_urls.append(candidate_url)
        existing_urls.add(candidate_url)
    return project_record.unique_list(probe_urls)


def pdsls_collection_reference_nsid(value: str) -> str:
    normalized = identifier_core.normalize_url(value)
    if not normalized:
        return ""
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc.lower() not in project_record.PDSLS_VIEWER_HOSTS:
        return ""
    return project_record.extract_collection_nsid(normalized)


def registrable_namespace_from_namespace(namespace: str) -> str:
    normalized = identifier_core.normalize_nsid(namespace)
    if not normalized:
        return ""
    authority_domain = identifier_core.derive_authority_domain_from_namespace(normalized)
    registrable = domain_policy_core.root_domain(authority_domain)
    if not registrable or "." not in registrable:
        return ""
    return identifier_core.normalize_nsid(".".join(reversed(registrable.split("."))))


def detect_seed_type(seed: str, explicit_type: str | None = None) -> str:
    if explicit_type:
        return explicit_type
    if project_record.extract_collection_nsid(seed):
        return "nsid"
    parsed = urllib.parse.urlparse(seed)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        if repo_url_core.is_supported_repo_url(seed):
            return "repo_url"
        return "service_url"
    if seed.startswith("did:"):
        return "did"
    if re.fullmatch(NSID_PATTERN, seed):
        return "nsid"
    raise ValueError(f"could not detect seed type: {seed}")


DEFAULT_DEFER_IDENTIFIER_DAYS = 30


def add_confirmed_identifier_decision(
    project: dict[str, Any],
    identifier_type: str,
    value: str,
    *,
    source: str,
    note: str = "",
    role: str = "",
    registration_origin: bool = False,
    source_ref: str = "",
    policy_version: str = "",
    input_fingerprint: str = "",
) -> str:
    if identifier_type not in project_record.IDENTIFIER_DECISION_TYPES:
        raise ValueError("identifier_type must be service_url, repo_url, nsid, account, did, handle, or domain")
    normalized = project_record.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized:
        raise ValueError("identifier value could not be normalized")
    confirmations = [
        item
        for item in project_record.normalize_confirmed_identifiers(list(project.get("confirmed_identifiers", [])))
        if not (
            str(item.get("type") or "") == identifier_type
            and str(item.get("value") or "") == normalized
            and not str(item.get("cleared_at") or "")
        )
    ]
    confirmations.append(
        {
            "type": identifier_type,
            "value": normalized,
            "role": role.strip(),
            "note": note.strip(),
            "source": source.strip(),
            "registration_origin": bool(registration_origin),
            "source_ref": source_ref.strip(),
            "policy_version": policy_version.strip(),
            "input_fingerprint": input_fingerprint.strip(),
            "added_at": project_record.iso_now(),
            "cleared_at": "",
        }
    )
    project["confirmed_identifiers"] = confirmations
    if identifier_type == "nsid" and identifier_storage.adoption_kind_for_source(source) == "operator":
        for entry in project.get("lexicon_resolution_entries", []):
            if entry.get("input_nsid") == normalized:
                entry.get("publication_validation", {}).pop("type_changed", None)
    return normalized


def clear_confirmed_identifier_decision(project: dict[str, Any], identifier_type: str, value: str) -> None:
    normalized = project_record.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized:
        return
    cleared_at = project_record.iso_now()
    confirmations: list[dict[str, str]] = []
    for item in project_record.normalize_confirmed_identifiers(list(project.get("confirmed_identifiers", []))):
        if (
            str(item.get("type") or "") == identifier_type
            and str(item.get("value") or "") == normalized
            and not str(item.get("cleared_at") or "")
        ):
            item = dict(item)
            item["cleared_at"] = cleared_at
        confirmations.append(item)
    project["confirmed_identifiers"] = confirmations


def nsid_is_builtin_reference(nsid: str) -> bool:
    normalized = identifier_core.normalize_nsid(nsid)
    if not normalized:
        return False
    return any(normalized.startswith(prefix) for prefix in project_record.builtin_lexicon_prefixes())


def confirmed_developer_handles(project: dict[str, Any]) -> set[str]:
    return {
        str(item.get("handle") or "").strip().lower()
        for item in project.get("developer_accounts", [])
        if isinstance(item, dict) and str(item.get("handle") or "").strip()
    }


def confirmed_developer_dids(project: dict[str, Any]) -> set[str]:
    return {
        str(item.get("did") or "").strip()
        for item in project.get("developer_accounts", [])
        if isinstance(item, dict) and str(item.get("did") or "").strip()
    }


def developer_controls_authority_domain(project: dict[str, Any], authority_domain: str) -> bool:
    lowered = str(authority_domain or "").strip().lower().rstrip(".")
    if not lowered:
        return False
    authority_root = domain_policy_core.root_domain(lowered)
    for handle in confirmed_developer_handles(project):
        handle_root = domain_policy_core.root_domain(handle)
        if handle == lowered or handle.endswith(f".{lowered}"):
            return True
        if authority_root and handle_root and handle_root == authority_root:
            return True
    return False


def developer_holds_schema_record(project: dict[str, Any], nsid: str) -> bool:
    normalized = identifier_core.normalize_nsid(nsid)
    if not normalized:
        return False
    confirmed_dids = confirmed_developer_dids(project)
    if not confirmed_dids:
        return False
    for entry in project_record.normalize_lexicon_resolution_entries(project.get("lexicon_resolution_entries", [])):
        input_nsid = identifier_core.normalize_nsid(str(entry.get("input_nsid") or ""))
        resolved_nsid = identifier_core.normalize_nsid(str(entry.get("resolved_nsid") or ""))
        repo_did = str(entry.get("repo_did") or "").strip()
        if repo_did and repo_did in confirmed_dids and normalized in {input_nsid, resolved_nsid}:
            return True
    return False


def project_owned_lexicon_nsid(project: dict[str, Any], nsid: str) -> bool:
    normalized = identifier_core.normalize_nsid(nsid)
    if relation_core.identifier_is_guarded(project, "nsid", normalized):
        return False
    if normalized in {
        identifier_core.normalize_nsid(str(value or ""))
        for value in project.get("resolved_nsids", [])
    }:
        return True
    if project_record.manual_nsid_seed_supports(project, normalized):
        return True
    evaluation = trust_collection_ownership_evaluation(project, normalized)
    return bool(evaluation.get("auto_confirm"))


def prune_project_lexicon_identity(project: dict[str, Any]) -> None:
    allowed_input_nsids = [
        nsid
        for nsid in project.get("input_nsids", [])
        if not relation_core.identifier_is_guarded(project, "nsid", identifier_core.normalize_nsid(str(nsid or "")))
    ]
    allowed_resolved_nsids = [nsid for nsid in project.get("resolved_nsids", []) if project_owned_lexicon_nsid(project, str(nsid or ""))]
    allowed_nsids = {identifier_core.normalize_nsid(nsid) for nsid in allowed_input_nsids + allowed_resolved_nsids if identifier_core.normalize_nsid(nsid)}

    project["input_nsids"] = project_record.unique_list(allowed_input_nsids)
    project["resolved_nsids"] = project_record.unique_list(allowed_resolved_nsids)
    project["lexicons"] = [nsid for nsid in project.get("lexicons", []) if identifier_core.normalize_nsid(str(nsid or "")) in allowed_nsids]

    project["lexicon_resolution_entries"] = [
        dict(item)
        for item in project_record.normalize_lexicon_resolution_entries(project.get("lexicon_resolution_entries", []))
        if identifier_core.normalize_nsid(str(item.get("input_nsid") or "")) in allowed_nsids
        or identifier_core.normalize_nsid(str(item.get("resolved_nsid") or "")) in allowed_nsids
    ]
    project["lexicon_schema_samples"] = [
        dict(item)
        for item in project.get("lexicon_schema_samples", [])
        if isinstance(item, dict) and identifier_core.normalize_nsid(str(item.get("nsid") or "")) in allowed_nsids
    ]

    allowed_authority_domains = {
        identifier_core.derive_authority_domain(nsid)
        for nsid in allowed_nsids
        if identifier_core.derive_authority_domain(nsid)
    }
    confirmed_service_hosts = set(project_record.service_hosts(project)) | set(project_record.manual_service_hosts(project))
    project["authority_domains"] = project_record.unique_list(
        [
            str(domain or "").strip().lower()
            for domain in project.get("authority_domains", [])
            if str(domain or "").strip()
            and (
                str(domain or "").strip().lower() in allowed_authority_domains
                or str(domain or "").strip().lower() in confirmed_service_hosts
                or any(
                    str(domain or "").strip().lower().endswith(f".{host}") or host.endswith(f".{str(domain or '').strip().lower()}")
                    for host in confirmed_service_hosts
                )
            )
        ]
    )
    allowed_repo_dids = {
        str(item.get("repo_did") or "").strip()
        for item in project.get("lexicon_resolution_entries", [])
        if isinstance(item, dict) and str(item.get("repo_did") or "").strip()
    }
    project["record_creators"] = [
        did for did in project_record.unique_list(project.get("record_creators", []))
        if str(did or "").strip() in allowed_repo_dids
    ]


def initialize_db(path: Path | None = None) -> sqlite3.Connection:
    target_path = Path(path) if path is not None else DB_PATH
    conn = registry_database.open_database(target_path, create=True)
    investigation_storage.ensure_investigation_tables(conn)
    conn.commit()
    return conn


def open_db() -> sqlite3.Connection:
    return registry_database.open_database(DB_PATH, create=False)


def sync_scope_key(kind: str, scope: dict[str, Any]) -> str:
    encoded = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"atproto-survey:{kind}:{encoded}").hex[:16]
    return f"{kind}:{digest}"


def read_sync_state(conn: sqlite3.Connection, kind: str, scope: dict[str, Any]) -> dict[str, Any]:
    scope_key = sync_scope_key(kind, scope)
    row = conn.execute(
        "SELECT scope_key, kind, scope_json, watermark_json, last_success_at FROM sync_state WHERE scope_key = ?",
        (scope_key,),
    ).fetchone()
    if row is None:
        return {
            "scope_key": scope_key,
            "kind": kind,
            "scope": dict(scope),
            "watermark": {},
            "last_success_at": "",
        }
    return {
        "scope_key": str(row["scope_key"]),
        "kind": str(row["kind"]),
        "scope": project_record.load_json(str(row["scope_json"] or "")) or dict(scope),
        "watermark": project_record.load_json(str(row["watermark_json"] or "")) or {},
        "last_success_at": str(row["last_success_at"] or ""),
    }


def write_sync_state(
    conn: sqlite3.Connection,
    kind: str,
    scope: dict[str, Any],
    watermark: dict[str, Any],
) -> dict[str, Any]:
    scope_key = sync_scope_key(kind, scope)
    last_success_at = project_record.iso_now()
    conn.execute(
        """
        INSERT INTO sync_state(scope_key, kind, scope_json, watermark_json, last_success_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(scope_key) DO UPDATE SET
            kind = excluded.kind,
            scope_json = excluded.scope_json,
            watermark_json = excluded.watermark_json,
            last_success_at = excluded.last_success_at
        """,
        (
            scope_key,
            kind,
            json.dumps(scope, ensure_ascii=False, sort_keys=True),
            json.dumps(watermark, ensure_ascii=False, sort_keys=True),
            last_success_at,
        ),
    )
    return {
        "scope_key": scope_key,
        "kind": kind,
        "scope": dict(scope),
        "watermark": dict(watermark),
        "last_success_at": last_success_at,
    }


def sync_watermark_datetime(state: dict[str, Any], key: str = "timestamp") -> dt.datetime | None:
    watermark = state.get("watermark", {}) if isinstance(state.get("watermark", {}), dict) else {}
    return project_record.parse_iso_datetime(str(watermark.get(key) or ""))


def normalized_url_field_values(project: dict[str, Any], field_name: str) -> set[str]:
    value = project.get(field_name)
    if field_name == "resource_url_records":
        return {record["url"] for record in project_record.normalize_resource_url_records(value if isinstance(value, list) else [])}
    values = value if isinstance(value, list) else ([value] if value else [])
    if field_name in {"project_repository_url", "project_repository_urls", "candidate_project_repository_urls"}:
        return {normalized for item in values if (normalized := repo_url_core.canonical_repo_url(str(item)))}
    return {normalized for item in values if (normalized := identifier_core.normalize_url(str(item)))}


def build_url_field_audit(conn: sqlite3.Connection, *, example_limit: int = 20) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM projects ORDER BY project_key ASC").fetchall()
    projects = [project_record.project_from_row(conn, row) for row in rows]
    field_summaries: list[dict[str, Any]] = []
    for spec in URL_FIELD_INVENTORY:
        field_name = spec["field"]
        values: list[str] = []
        nonempty_projects = 0
        for project in projects:
            field_values = normalized_url_field_values(project, field_name)
            if field_values:
                nonempty_projects += 1
                values.extend(sorted(field_values))
        field_summaries.append(
            {
                **spec,
                "nonempty_projects": nonempty_projects,
                "value_count": len(values),
                "unique_value_count": len(set(values)),
            }
        )

    alias_pairs = (
        ("project_repository_url", "project_repository_urls", "canonical_in_confirmed_set"),
    )
    alias_summaries: list[dict[str, Any]] = []
    for left, right, relation in alias_pairs:
        left_nonempty = 0
        exact_match = 0
        left_subset = 0
        overlap_values: set[str] = set()
        for project in projects:
            left_values = normalized_url_field_values(project, left)
            right_values = normalized_url_field_values(project, right)
            if not left_values:
                continue
            left_nonempty += 1
            if left_values == right_values:
                exact_match += 1
            if left_values <= right_values:
                left_subset += 1
            overlap_values.update(left_values & right_values)
        alias_summaries.append(
            {
                "left": left,
                "right": right,
                "relation": relation,
                "left_nonempty_projects": left_nonempty,
                "exact_match_projects": exact_match,
                "left_subset_projects": left_subset,
                "overlap_unique_value_count": len(overlap_values),
            }
        )

    anomaly_specs = (
        ("project_repository_url_missing_from_project_repository_urls", lambda p: normalized_url_field_values(p, "project_repository_url") - normalized_url_field_values(p, "project_repository_urls")),
        ("confirmed_candidate_service_overlap", lambda p: normalized_url_field_values(p, "service_urls") & normalized_url_field_values(p, "service_url_candidates")),
        ("observed_url_alias_still_candidate", lambda p: {
            identifier_core.normalize_url(str(item.get("alias") or ""))
            for item in url_alias_core.observed_url_aliases(p)
        } & normalized_url_field_values(p, "service_url_candidates")),
        ("confirmed_candidate_repo_overlap", lambda p: normalized_url_field_values(p, "project_repository_urls") & normalized_url_field_values(p, "candidate_project_repository_urls")),
        ("introduced_url_in_evidence_urls", lambda p: normalized_url_field_values(p, "introduced_url") & normalized_url_field_values(p, "evidence_urls")),
    )
    anomalies: list[dict[str, Any]] = []
    for kind, collect_values in anomaly_specs:
        examples: list[dict[str, Any]] = []
        project_count = 0
        value_count = 0
        for project in projects:
            values = sorted(collect_values(project))
            if not values:
                continue
            project_count += 1
            value_count += len(values)
            if len(examples) < max(0, int(example_limit)):
                examples.append({"project_key": str(project.get("project_key") or ""), "values": values})
        anomalies.append({"kind": kind, "project_count": project_count, "value_count": value_count, "examples": examples})

    return {
        "project_count": len(projects),
        "field_inventory": field_summaries,
        "alias_relationships": alias_summaries,
        "anomalies": anomalies,
    }


def project_storage_projection(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "scalars": {
            field: project.get(field)
            for field in ("name", "website_url", "service_status", "tracking_state", "introduced_url")
        },
        "profile": project_record.project_record_payload(project),
        "state": project_record.project_ops_payload(project),
    }


def project_normalized_child_projection(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "identifiers": [asdict(item) for item in runtime_view.project_identifiers(project)],
        "relations": runtime_view.relation_rows(project),
        "decisions": [asdict(item) for item in runtime_view.project_decisions(project)],
    }


def find_project_key_by_collection_namespace(projects: list[dict[str, Any]], namespace: str) -> str | None:
    normalized = identifier_core.normalize_nsid(namespace)
    for project in projects:
        candidates = project_record.unique_list(
            list(project.get("input_nsids", []))
            + list(project.get("resolved_nsids", []))
            + list(project.get("recorded_lexicon_namespaces", []))
        )
        if normalized in [identifier_core.normalize_nsid(str(item)) for item in candidates if item]:
            return str(project["project_key"])
    return None


def build_unique_lookup(pairs: list[tuple[str, str]]) -> dict[str, str]:
    grouped: dict[str, set[str]] = {}
    for key, value in pairs:
        normalized_key = str(key or "").strip().lower()
        normalized_value = str(value or "").strip()
        if not normalized_key or not normalized_value:
            continue
        grouped.setdefault(normalized_key, set()).add(normalized_value)
    return {key: next(iter(values)) for key, values in grouped.items() if len(values) == 1}


def sync_match_is_auto_attach_safe(match_kind: str) -> bool:
    normalized = str(match_kind or "")
    if normalized.startswith("member_"):
        return True
    return normalized in {
        "exact_namespace",
        "exact_namespace_scan",
        "seed_parent_namespace",
        "repo_url",
    }


def build_collection_project_matchers(projects: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    namespace_pairs: list[tuple[str, str]] = []
    registrable_namespace_pairs: list[tuple[str, str]] = []
    service_host_pairs: list[tuple[str, str]] = []
    for project in projects:
        project_key = str(project["project_key"])
        namespace_candidates = project_record.unique_list(
            list(project.get("input_nsids", []))
            + list(project.get("resolved_nsids", []))
            + list(project.get("recorded_lexicon_namespaces", []))
        )
        for namespace in namespace_candidates:
            normalized = identifier_core.normalize_nsid(str(namespace or ""))
            if not normalized:
                continue
            namespace_pairs.append((normalized, project_key))
            registrable = registrable_namespace_from_namespace(normalized)
            if registrable:
                registrable_namespace_pairs.append((registrable, project_key))
        service_urls = project_record.unique_list(list(project.get("service_urls", [])))
        for service_url in service_urls:
            normalized_service = identifier_core.normalize_url(str(service_url or ""))
            host = urllib.parse.urlparse(normalized_service).netloc.lower()
            registrable_host = domain_policy_core.root_domain(host)
            if registrable_host:
                service_host_pairs.append((registrable_host, project_key))
    return (
        build_unique_lookup(namespace_pairs),
        build_unique_lookup(registrable_namespace_pairs),
        build_unique_lookup(service_host_pairs),
    )


def build_nsid_seed_parent_namespace_lookup(projects: list[dict[str, Any]]) -> dict[str, str]:
    pairs: list[tuple[str, str]] = []
    for project in projects:
        seed_values = []
        if str(project.get("seed_type") or "") == "nsid":
            seed_values.append(str(project.get("seed_value") or ""))
        seed_values.extend(project.get("resolved_nsids", []))
        for seed_value in project_record.unique_list(seed_values):
            normalized_seed = identifier_core.normalize_nsid(str(seed_value or ""))
            parent_namespace = project_record.namespace_from_collection_nsid(normalized_seed)
            if not parent_namespace:
                continue
            pairs.append((parent_namespace, str(project.get("project_key") or "")))
    return build_unique_lookup(pairs)


def find_project_key_for_nsid_seed(projects: list[dict[str, Any]], nsid: str) -> tuple[str | None, str]:
    namespace_lookup, registrable_namespace_lookup, service_host_lookup = build_collection_project_matchers(projects)
    parent_seed_namespace_lookup = build_nsid_seed_parent_namespace_lookup(projects)
    return _find_project_key_for_nsid_seed_with_lookups(
        projects,
        nsid,
        namespace_lookup=namespace_lookup,
        registrable_namespace_lookup=registrable_namespace_lookup,
        service_host_lookup=service_host_lookup,
        parent_seed_namespace_lookup=parent_seed_namespace_lookup,
    )


def _find_project_key_for_nsid_seed_with_lookups(
    projects: list[dict[str, Any]],
    nsid: str,
    *,
    namespace_lookup: dict[str, str],
    registrable_namespace_lookup: dict[str, str],
    service_host_lookup: dict[str, str],
    parent_seed_namespace_lookup: dict[str, str],
) -> tuple[str | None, str]:
    normalized = identifier_core.normalize_nsid(nsid)
    if not normalized:
        return None, ""
    project_key = namespace_lookup.get(normalized)
    if project_key:
        return project_key, "exact_namespace"
    project_key = find_project_key_by_collection_namespace(projects, normalized)
    if project_key:
        return project_key, "exact_namespace_scan"
    parent_namespace = project_record.namespace_from_collection_nsid(normalized)
    if parent_namespace:
        project_key = parent_seed_namespace_lookup.get(parent_namespace)
        if project_key:
            return project_key, "seed_parent_namespace"
    registrable_namespace = registrable_namespace_from_namespace(normalized)
    if registrable_namespace:
        project_key = registrable_namespace_lookup.get(registrable_namespace)
        if project_key:
            return project_key, "registrable_namespace"
    authority_domain = identifier_core.derive_authority_domain_from_namespace(normalized)
    service_host_key = domain_policy_core.root_domain(authority_domain)
    if service_host_key:
        project_key = service_host_lookup.get(service_host_key)
        if project_key:
            return project_key, "service_host_family"
    return None, ""


def ensure_project_for_seed(
    conn: sqlite3.Connection,
    seed_type: str,
    seed_value: str,
    project_key: str | None = None,
    *,
    source: str = "manual_seed",
    commit: bool = True,
) -> dict[str, Any]:
    normalized_value = project_record.canonical_seed_value(seed_type, seed_value)
    identifier_value = normalized_value
    resolved_project_key = project_record.resolve_project_key_input(conn, project_key) if project_key else None
    target_project_key = resolved_project_key or project_record.find_project_key_by_identifier(conn, seed_type, identifier_value)
    match_kind = ""
    if target_project_key is None and seed_type == "nsid":
        target_project_key, match_kind = find_project_key_for_nsid_seed(all_projects(conn), identifier_value)
    if target_project_key is None:
        target_project_key = project_record.allocate_new_project_key(conn)
    project = project_record.fetch_project(conn, target_project_key)
    if project is None:
        project = project_record.default_project_record(target_project_key, seed_type, identifier_value)
    add_manual_seed(project, seed_type, identifier_value, source=source)
    if match_kind:
        project_record.append_system_note(project, f"seed matched by {match_kind}: {identifier_value}")
    project_record.save_project(conn, project, enriched=False, commit=commit)
    if commit:
        conn.commit()
    saved = project_record.fetch_project(conn, target_project_key)
    if saved is None:  # pragma: no cover - save_project succeeded immediately above
        raise RuntimeError(f"saved project could not be reloaded: {target_project_key}")
    return saved


def apply_seed_to_project(project: dict[str, Any], seed_type: str, seed_value: str) -> None:
    if project_record.identifier_is_blocked(project, seed_type, seed_value) or project_record.identifier_is_deferred(project, seed_type, seed_value):
        return
    if seed_type == "nsid":
        normalized_nsid = identifier_core.normalize_nsid(seed_value)
        project["resolved_nsids"] = project_record.add_unique(project.get("resolved_nsids", []), normalized_nsid)
        project["input_nsids"] = [
            value for value in project.get("input_nsids", [])
            if identifier_core.normalize_nsid(str(value or "")) != normalized_nsid
        ]
        authority_domain = identifier_core.derive_authority_domain(normalized_nsid)
        if authority_domain:
            project["authority_domains"] = project_record.add_unique(project.get("authority_domains", []), authority_domain)
            add_authority_service_candidates(project, authority_domain)
    elif seed_type == "repo_url":
        normalized = repo_url_core.canonical_repo_url(seed_value)
        if not normalized:
            normalized = identifier_core.normalize_url(seed_value)
            add_candidate_url(project, "service_url", normalized)
            project["evidence_urls"] = project_record.add_unique(project.get("evidence_urls", []), normalized)
            naming_core.add_name_candidate(project, naming_core.service_seed_name_hint(normalized), f"seed:service_url:{normalized}")
            return
        project["project_repository_urls"] = project_record.add_unique(project.get("project_repository_urls", []), normalized)
        project["evidence_urls"] = project_record.add_unique(project.get("evidence_urls", []), normalized)
        parsed = urllib.parse.urlparse(normalized)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if segments:
            naming_core.add_name_candidate(project, segments[-1], f"seed:repo_url:{normalized}")
    elif seed_type == "service_url":
        normalized = project_record.normalize_confirmed_service_url(seed_value)
        if normalized:
            project["service_urls"] = project_record.add_unique(project.get("service_urls", []), normalized)
            project_record.set_service_url_candidate_values(
                project,
                [
                    value
                    for value in project_record.service_url_candidate_values(project)
                    if identifier_core.normalize_url(str(value or "")) != normalized
                ],
            )
            project["evidence_urls"] = project_record.add_unique(project.get("evidence_urls", []), normalized)
            naming_core.add_name_candidate(project, naming_core.service_seed_name_hint(normalized), f"seed:service_url:{normalized}")
    elif seed_type == "did":
        normalized = str(seed_value or "").strip()
        if normalized:
            project["brand_did"] = project.get("brand_did") or normalized
            project_record.add_developer_account(project, "", "manual_seed", did=normalized)
    elif seed_type == "handle":
        normalized = str(seed_value or "").strip().lower()
        if normalized:
            project_record.add_developer_account(project, normalized, "manual_seed")
            if "." in normalized and not project_record.is_shared_official_identity_host(normalized):
                project["authority_domains"] = project_record.add_unique(project.get("authority_domains", []), normalized)
    elif seed_type == "domain":
        normalized = str(seed_value or "").strip().lower().rstrip(".")
        if normalized:
            project["authority_domains"] = project_record.add_unique(project.get("authority_domains", []), normalized)
    else:
        normalized = identifier_core.normalize_url(seed_value)
        add_candidate_url(project, "service_url", normalized)
        project["evidence_urls"] = project_record.add_unique(project.get("evidence_urls", []), normalized)
        naming_core.add_name_candidate(project, naming_core.service_seed_name_hint(normalized), f"seed:service_url:{normalized}")

def project_collision_identifiers(project: dict[str, Any]) -> set[tuple[str, str]]:
    identifiers: set[tuple[str, str]] = set()
    for nsid in project_record.current_confirmed_nsids(project):
        identifiers.add(("nsid", identifier_core.normalize_nsid(nsid)))
    for url in project_record.project_repository_url_values(project):
        identifiers.add(("repo_url", identifier_core.normalize_url(url)))
    for url in project.get("service_urls", []):
        identifiers.add(("service_url", identifier_core.normalize_url(url)))
    return {item for item in identifiers if item[1]}


def github_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def downgrade_identifier_to_candidate(
    project: dict[str, Any],
    identifier_type: str,
    value: str,
    *,
    keep_candidate: bool = True,
) -> None:
    normalized = project_record.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized:
        return
    if identifier_type == "service_url":
        remove_identifier_from_project(project, identifier_type, normalized)
        add_candidate_url(project, "service_url", normalized)
        project["evidence_urls"] = project_record.add_unique(project.get("evidence_urls", []), normalized)
    elif identifier_type == "repo_url":
        remove_identifier_from_project(project, identifier_type, normalized)
        project["candidate_project_repository_urls"] = project_record.add_unique(project.get("candidate_project_repository_urls", []), normalized)
        project["evidence_urls"] = project_record.add_unique(project.get("evidence_urls", []), normalized)
    elif identifier_type == "nsid":
        if not keep_candidate:
            remove_identifier_from_project(project, identifier_type, normalized)
            return
        project["resolved_nsids"] = [
            nsid
            for nsid in project.get("resolved_nsids", [])
            if identifier_core.normalize_nsid(str(nsid or "")) != normalized
        ]
        project["input_nsids"] = project_record.add_unique(project.get("input_nsids", []), normalized)
    elif identifier_type == "did":
        if not keep_candidate:
            remove_identifier_from_project(project, identifier_type, normalized)
            return
        if str(project.get("brand_did") or "").strip() == normalized:
            project["brand_did"] = ""
            project["candidate_developer_accounts"] = project_record.normalize_developer_accounts(
                list(project.get("candidate_developer_accounts", []))
                + [{"did": normalized, "source": "trust_graph_downgrade"}]
            )
        remaining_accounts: list[dict[str, str]] = []
        candidate_accounts = list(project.get("candidate_developer_accounts", []))
        for account in project_record.normalize_developer_accounts(list(project.get("developer_accounts", []))):
            if str(account.get("did") or "").strip() != normalized:
                remaining_accounts.append(account)
                continue
            candidate = {"did": normalized}
            if account.get("source"):
                candidate["source"] = str(account["source"])
            candidate_accounts.append(candidate)
            if account.get("handle"):
                retained = dict(account)
                retained.pop("did", None)
                remaining_accounts.append(retained)
        project["developer_accounts"] = project_record.normalize_developer_accounts(remaining_accounts)
        project["candidate_developer_accounts"] = project_record.normalize_developer_accounts(candidate_accounts)
    elif identifier_type == "handle":
        if not keep_candidate:
            remove_identifier_from_project(project, identifier_type, normalized)
            return
        lowered = normalized.lower()
        remaining_accounts = []
        candidate_accounts = list(project.get("candidate_developer_accounts", []))
        for account in project_record.normalize_developer_accounts(list(project.get("developer_accounts", []))):
            if str(account.get("handle") or "").strip().lower().rstrip(".") != lowered:
                remaining_accounts.append(account)
                continue
            candidate = {"handle": normalized}
            if account.get("source"):
                candidate["source"] = str(account["source"])
            candidate_accounts.append(candidate)
            if account.get("did"):
                retained = dict(account)
                retained.pop("handle", None)
                remaining_accounts.append(retained)
        project["developer_accounts"] = project_record.normalize_developer_accounts(remaining_accounts)
        project["candidate_developer_accounts"] = project_record.normalize_developer_accounts(candidate_accounts)
    elif identifier_type == "domain":
        if not keep_candidate:
            remove_identifier_from_project(project, identifier_type, normalized)
            return
        lowered = normalized.lower()
        project["authority_domains"] = [
            domain
            for domain in project_record.unique_list(project.get("authority_domains", []))
            if str(domain or "").strip().lower().rstrip(".") != lowered
        ]


def defer_identifier_on_project(
    project: dict[str, Any],
    identifier_type: str,
    value: str,
    *,
    note: str = "",
    role: str = "",
    days: int = DEFAULT_DEFER_IDENTIFIER_DAYS,
) -> str:
    if identifier_type not in project_record.IDENTIFIER_DECISION_TYPES:
        raise ValueError("identifier_type must be service_url, repo_url, nsid, account, did, handle, or domain")
    normalized = project_record.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized:
        raise ValueError("identifier value could not be normalized")
    effective_days = max(1, min(365, int(days or DEFAULT_DEFER_IDENTIFIER_DAYS)))
    expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=effective_days)).isoformat()
    deferred = [
        item
        for item in project_record.normalize_deferred_identifiers(list(project.get("deferred_identifiers", [])))
        if (str(item.get("type") or ""), str(item.get("value") or "")) != (identifier_type, normalized)
    ]
    deferred.append(
        {
            "type": identifier_type,
            "value": normalized,
            "role": role.strip(),
            "note": note or "deferred for observation",
            "added_at": project_record.iso_now(),
            "expires_at": expires_at,
        }
    )
    project["deferred_identifiers"] = deferred
    downgrade_identifier_to_candidate(
        project,
        identifier_type,
        normalized,
        keep_candidate=identifier_type in {"service_url", "repo_url"},
    )
    return normalized


def apply_deferred_identifier_guards(project: dict[str, Any]) -> None:
    project["deferred_identifiers"] = project_record.normalize_deferred_identifiers(list(project.get("deferred_identifiers", [])))
    for item in project["deferred_identifiers"]:
        identifier_type = str(item.get("type") or "")
        downgrade_identifier_to_candidate(
            project,
            identifier_type,
            str(item.get("value") or ""),
            keep_candidate=identifier_type in {"service_url", "repo_url"},
        )


def service_url_is_guarded(project: dict[str, Any], url: str) -> bool:
    if identifier_has_active_confirm_decision(project, "service_url", url):
        return False
    return project_record.identifier_is_blocked(project, "service_url", url) or project_record.identifier_is_deferred(project, "service_url", url)


def purge_guarded_service_observations(project: dict[str, Any]) -> None:
    project["service_urls"] = [
        url
        for url in project.get("service_urls", [])
        if not service_url_is_guarded(project, str(url or ""))
    ]
    project["supported_service_urls"] = [
        url
        for url in project.get("supported_service_urls", [])
        if not service_url_is_guarded(project, str(url or ""))
    ]
    project["failed_service_urls"] = [
        url
        for url in project.get("failed_service_urls", [])
        if not service_url_is_guarded(project, str(url or ""))
    ]
    project_record.set_service_url_candidate_values(
        project,
        [
            url
            for url in project_record.service_url_candidate_values(project)
            if not project_record.identifier_is_blocked(project, "service_url", url)
        ],
    )


def purge_guarded_service_relations(project: dict[str, Any]) -> None:
    relations = []
    for item in relation_core.normalize_relation_records(project.get("relation_evidence", [])):
        relation = item["relation"]
        if (
            relation["predicate"] == "operates_service"
            and relation["object"]["type"] == "service"
            and (
                service_url_is_guarded(project, relation["object"]["value"])
                or service_url_is_supporting_page(relation["object"]["value"])
            )
        ):
            continue
        relations.append(item)
    project["relation_evidence"] = relations


def purge_blocked_identifiers(project: dict[str, Any]) -> None:
    blocked_services = project_record.blocked_identifier_values(project, "service_url")
    blocked_repos = project_record.blocked_identifier_values(project, "repo_url")
    blocked_nsids = project_record.blocked_identifier_values(project, "nsid")
    blocked_dids = project_record.blocked_identifier_values(project, "did")
    blocked_handles = project_record.blocked_identifier_values(project, "handle")
    blocked_domains = project_record.blocked_identifier_values(project, "domain")

    if blocked_services:
        project["service_urls"] = [url for url in project.get("service_urls", []) if identifier_core.normalize_url(str(url or "")) not in blocked_services]
        project_record.set_service_url_candidate_values(
            project,
            [url for url in project_record.service_url_candidate_values(project) if identifier_core.normalize_url(str(url or "")) not in blocked_services],
        )
        project["failed_service_urls"] = [url for url in project.get("failed_service_urls", []) if identifier_core.normalize_url(str(url or "")) not in blocked_services]
        project["supported_service_urls"] = [url for url in project.get("supported_service_urls", []) if identifier_core.normalize_url(str(url or "")) not in blocked_services]

    if blocked_repos:
        if repo_url_core.canonical_repo_url(str(project.get("project_repository_url") or "")) in blocked_repos:
            project["project_repository_url"] = ""
        project["project_repository_urls"] = [url for url in project.get("project_repository_urls", []) if repo_url_core.canonical_repo_url(str(url or "")) not in blocked_repos]
        project["candidate_project_repository_urls"] = [url for url in project.get("candidate_project_repository_urls", []) if repo_url_core.canonical_repo_url(str(url or "")) not in blocked_repos]
        project["source_repo_records"] = [
            dict(item)
            for item in project.get("source_repo_records", [])
            if isinstance(item, dict) and repo_url_core.canonical_repo_url(str(item.get("url") or "")) not in blocked_repos
        ]
        project["evidence_urls"] = [url for url in project.get("evidence_urls", []) if repo_url_core.canonical_repo_url(str(url or "")) not in blocked_repos]
        project_record.sync_source_repo_provider_views(project)

    if blocked_nsids:
        project["input_nsids"] = [nsid for nsid in project.get("input_nsids", []) if identifier_core.normalize_nsid(str(nsid or "")) not in blocked_nsids]
        project["resolved_nsids"] = [nsid for nsid in project.get("resolved_nsids", []) if identifier_core.normalize_nsid(str(nsid or "")) not in blocked_nsids]
        project["lexicons"] = [nsid for nsid in project.get("lexicons", []) if identifier_core.normalize_nsid(str(nsid or "")) not in blocked_nsids]
        project["lexicon_resolution_entries"] = [
            dict(item)
            for item in project.get("lexicon_resolution_entries", [])
            if isinstance(item, dict)
            and identifier_core.normalize_nsid(str(item.get("input_nsid") or "")) not in blocked_nsids
            and identifier_core.normalize_nsid(str(item.get("resolved_nsid") or "")) not in blocked_nsids
        ]
        project["recent_record_samples"] = [
            dict(item)
            for item in project.get("recent_record_samples", [])
            if isinstance(item, dict) and identifier_core.normalize_nsid(str(item.get("collection") or "")) not in blocked_nsids
        ]

    for did in blocked_dids:
        remove_identifier_from_project(project, "did", did)
    for handle in blocked_handles:
        remove_identifier_from_project(project, "handle", handle)
    for domain in blocked_domains:
        remove_identifier_from_project(project, "domain", domain)

    if blocked_services or blocked_repos or blocked_nsids or blocked_dids or blocked_handles or blocked_domains:
        project["manual_seeds"] = [
            dict(item)
            for item in project.get("manual_seeds", [])
            if isinstance(item, dict)
            and not project_record.identifier_is_blocked(project, str(item.get("type") or ""), str(item.get("value") or ""))
        ]


def remove_identifier_from_project(
    project: dict[str, Any],
    identifier_type: str,
    value: str,
    *,
    remove_normalized_rows: bool = False,
) -> None:
    normalized = project_record.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized:
        return
    if identifier_type == "service_url":
        project["service_urls"] = [url for url in project.get("service_urls", []) if identifier_core.normalize_url(str(url or "")) != normalized]
        project_record.set_service_url_candidate_values(
            project,
            [url for url in project_record.service_url_candidate_values(project) if identifier_core.normalize_url(str(url or "")) != normalized],
        )
        project["failed_service_urls"] = [url for url in project.get("failed_service_urls", []) if identifier_core.normalize_url(str(url or "")) != normalized]
        project["supported_service_urls"] = [url for url in project.get("supported_service_urls", []) if identifier_core.normalize_url(str(url or "")) != normalized]
    elif identifier_type == "repo_url":
        if repo_url_core.canonical_repo_url(str(project.get("project_repository_url") or "")) == normalized:
            project["project_repository_url"] = ""
        project["project_repository_urls"] = [url for url in project.get("project_repository_urls", []) if repo_url_core.canonical_repo_url(str(url or "")) != normalized]
        project["candidate_project_repository_urls"] = [url for url in project.get("candidate_project_repository_urls", []) if repo_url_core.canonical_repo_url(str(url or "")) != normalized]
        project["source_repo_records"] = [
            dict(item)
            for item in project.get("source_repo_records", [])
            if isinstance(item, dict) and repo_url_core.canonical_repo_url(str(item.get("url") or "")) != normalized
        ]
        project["evidence_urls"] = [url for url in project.get("evidence_urls", []) if repo_url_core.canonical_repo_url(str(url or "")) != normalized]
        project_record.sync_source_repo_provider_views(project)
    elif identifier_type == "nsid":
        project["input_nsids"] = [nsid for nsid in project.get("input_nsids", []) if identifier_core.normalize_nsid(str(nsid or "")) != normalized]
        project["resolved_nsids"] = [nsid for nsid in project.get("resolved_nsids", []) if identifier_core.normalize_nsid(str(nsid or "")) != normalized]
        project["lexicons"] = [nsid for nsid in project.get("lexicons", []) if identifier_core.normalize_nsid(str(nsid or "")) != normalized]
        project["lexicon_resolution_entries"] = [
            dict(item)
            for item in project.get("lexicon_resolution_entries", [])
            if isinstance(item, dict)
            and identifier_core.normalize_nsid(str(item.get("input_nsid") or "")) != normalized
            and identifier_core.normalize_nsid(str(item.get("resolved_nsid") or "")) != normalized
        ]
        project["recent_record_samples"] = [
            dict(item)
            for item in project.get("recent_record_samples", [])
            if isinstance(item, dict) and identifier_core.normalize_nsid(str(item.get("collection") or "")) != normalized
        ]
    elif identifier_type == "account":
        lowered = normalized.lower()
        for field in ("developer_accounts", "candidate_developer_accounts"):
            project[field] = [
                dict(item)
                for item in project_record.normalize_developer_accounts(list(project.get(field, [])))
                if lowered not in {str(item.get("handle") or "").lower(), str(item.get("did") or "").lower()}
            ]
    elif identifier_type == "did":
        if str(project.get("brand_did") or "").strip() == normalized:
            project["brand_did"] = ""
        for field in ("developer_accounts", "candidate_developer_accounts"):
            project[field] = [
                dict(item)
                for item in project_record.normalize_developer_accounts(list(project.get(field, [])))
                if str(item.get("did") or "").strip() != normalized
            ]
    elif identifier_type == "handle":
        lowered = normalized.lower()
        for field in ("developer_accounts", "candidate_developer_accounts"):
            project[field] = [
                dict(item)
                for item in project_record.normalize_developer_accounts(list(project.get(field, [])))
                if str(item.get("handle") or "").strip().lower().rstrip(".") != lowered
            ]
        project["bluesky_handles"] = [
            handle
            for handle in project_record.unique_list(project.get("bluesky_handles", []))
            if str(handle or "").strip().lower().rstrip(".") != lowered
        ]
        project["tangled_handles"] = [
            handle
            for handle in project_record.unique_list(project.get("tangled_handles", []))
            if str(handle or "").strip().lower().rstrip(".") != lowered
        ]
    elif identifier_type == "domain":
        lowered = normalized.lower()
        project["authority_domains"] = [
            domain
            for domain in project_record.unique_list(project.get("authority_domains", []))
            if str(domain or "").strip().lower().rstrip(".") != lowered
        ]
    runtime_rows = project.get(runtime_view.RUNTIME_IDENTIFIER_ROWS)
    if remove_normalized_rows and isinstance(runtime_rows, list):
        project[runtime_view.RUNTIME_IDENTIFIER_ROWS] = [
            dict(item)
            for item in runtime_rows
            if not (
                isinstance(item, dict)
                and str(item.get("kind") or "") == identifier_type
                and project_record.normalize_blocked_identifier_value(identifier_type, str(item.get("value") or "")) == normalized
            )
        ]
    project["manual_seeds"] = [
        dict(item)
        for item in project.get("manual_seeds", [])
        if isinstance(item, dict)
        and not (
            str(item.get("type") or "") == identifier_type
            and project_record.canonical_seed_value(str(item.get("type") or ""), str(item.get("value") or "")) == normalized
        )
    ]
    project_record.refresh_project_seed_identity(project)


def audit_and_repair_identifiers(conn: sqlite3.Connection, *, execute: bool = False) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT *
        FROM projects
        ORDER BY last_seen_at DESC, project_key ASC
        """
    ).fetchall()
    project_map: dict[str, dict[str, Any]] = {}
    pruned_projects = 0
    normalized_projects = 0
    manual_repo_seed_repairs = 0
    developer_account_repairs = 0
    current_service_projection_changes = 0
    current_service_projection_examples: list[dict[str, Any]] = []
    normalized_child_changes = 0
    normalized_child_change_examples: list[str] = []
    for row in rows:
        stored_payload = project_record.project_from_row_payload(row)
        project_key = str(row["project_key"])
        malformed_developer_accounts = sum(
            1
            for field in ("developer_accounts", "candidate_developer_accounts")
            for item in (stored_payload.get(field) if isinstance(stored_payload.get(field), list) else [])
            if isinstance(item, dict)
            and str(item.get("handle") or "").strip()
            and (
                project_record.DID_RE.fullmatch(str(item.get("handle") or "").strip())
                or not project_record.ATPROTO_HANDLE_RE.fullmatch(str(item.get("handle") or "").strip())
            )
        )
        malformed_developer_identifier_rows = 0
        identifier_rows = conn.execute(
            """
            SELECT kind, value, role, metadata_json
            FROM project_identifiers
            WHERE project_key = ?
              AND role IN ('developer_account', 'developer_handle')
            """,
            (project_key,),
        ).fetchall()
        for identifier_row in identifier_rows:
            kind = str(identifier_row["kind"])
            value = str(identifier_row["value"])
            role = str(identifier_row["role"])
            metadata = project_record.load_json(str(identifier_row["metadata_json"] or "{}")) or {}
            if kind == "handle" and role == "developer_handle" and not project_record.ATPROTO_HANDLE_RE.fullmatch(value):
                malformed_developer_identifier_rows += 1
            elif (
                kind == "account"
                and role == "developer_account"
                and isinstance(metadata, dict)
                and str((metadata or {"handle": value}).get("handle") or "").strip()
                and (
                    project_record.DID_RE.fullmatch(str((metadata or {"handle": value}).get("handle") or "").strip())
                    or not project_record.ATPROTO_HANDLE_RE.fullmatch(str((metadata or {"handle": value}).get("handle") or "").strip())
                )
            ):
                malformed_developer_identifier_rows += 1
        developer_account_repairs += malformed_developer_accounts + malformed_developer_identifier_rows
        raw = project_record.project_from_row(conn, row)
        original = project_record.ensure_project_shape(raw)
        original_repo_urls = set(repo_url_core.normalize_repo_collection(project_record.project_repository_url_values(original)))
        manual_repo_seed_repairs += len(project_record.active_manual_repo_urls(original) - original_repo_urls)
        normalized = project_record.ensure_project_shape(dict(original))
        normalized = finalize_project_shape(normalized)
        project_record.reclassify_service_specific_lexicon(normalized)
        normalized = project_record.apply_atproto_contribution_state(normalized)
        current_service_before = {
            "service_status": str(original.get("service_status") or "unknown"),
            "service_status_confidence": str(original.get("service_status_confidence") or "none"),
            "service_status_evidence": list(original.get("service_status_evidence", [])),
        }
        current_service_after = {
            "service_status": str(normalized.get("service_status") or "unknown"),
            "service_status_confidence": str(normalized.get("service_status_confidence") or "none"),
            "service_status_evidence": list(normalized.get("service_status_evidence", [])),
        }
        current_service_projection_changed = current_service_before != current_service_after
        if current_service_projection_changed:
            current_service_projection_changes += 1
            if len(current_service_projection_examples) < 20:
                current_service_projection_examples.append({
                    "project_key": project_key,
                    "before": current_service_before,
                    "after": current_service_after,
                })
        contribution_changed = any(
            project_record.dump_json(stored_payload.get(field, default)) != project_record.dump_json(normalized.get(field, default))
            for field, default in (
                ("atproto_contribution_level", "unknown"),
                ("atproto_contribution_score", 0),
                ("atproto_contribution_evidence", []),
            )
        )
        if project_record.dump_json(project_storage_projection(raw)) != project_record.dump_json(project_storage_projection(original)):
            normalized_projects += 1
        normalized_child_changed = (
            project_record.dump_json(project_normalized_child_projection(original))
            != project_record.dump_json(project_normalized_child_projection(normalized))
        )
        if normalized_child_changed:
            normalized_child_changes += 1
            if len(normalized_child_change_examples) < 20:
                normalized_child_change_examples.append(project_key)
        stored_payload_changed = (
            malformed_developer_accounts
            or malformed_developer_identifier_rows
            or project_record.dump_json(project_storage_projection(raw)) != project_record.dump_json(project_storage_projection(normalized))
            or normalized_child_changed
            or contribution_changed
        )
        if stored_payload_changed:
            pruned_projects += 1
        if execute and (stored_payload_changed or current_service_projection_changed):
            if malformed_developer_accounts or malformed_developer_identifier_rows:
                normalized.pop(runtime_view.RUNTIME_IDENTIFIER_ROWS, None)
                normalized.pop(runtime_view.RUNTIME_IDENTIFIER_BASELINE, None)
            project_record.save_project(conn, normalized, enriched=False)
        project_map[normalized["project_key"]] = normalized

    expected_by_project: dict[str, set[tuple[str, str]]] = {
        project_key: project_collision_identifiers(project)
        for project_key, project in project_map.items()
    }

    project_list = [project_map[project_key] for project_key in sorted(project_map)]
    duplicate_profiles, duplicate_index = project_merge.build_duplicate_candidate_index(project_list)
    refreshed_derived_projects = 0
    for project_key in sorted(project_map):
        project = dict(project_map[project_key])
        duplicate_candidates = project_merge.compute_duplicate_candidates(
            conn,
            project,
            projects=project_list,
            duplicate_profiles=duplicate_profiles,
            duplicate_index=duplicate_index,
        )
        merge_recommendation = project_merge.compute_merge_recommendation(
            dict(project, duplicate_candidates=duplicate_candidates)
        )
        if (
            project_record.dump_json(project.get("duplicate_candidates", [])) != project_record.dump_json(duplicate_candidates)
            or project_record.dump_json(project.get("merge_recommendation", {})) != project_record.dump_json(merge_recommendation)
        ):
            project["duplicate_candidates"] = duplicate_candidates
            project["merge_recommendation"] = merge_recommendation
            if execute:
                project_record.save_project(conn, project, enriched=False)
            project_map[project_key] = project
            refreshed_derived_projects += 1

    if execute:
        conn.commit()

    expected_owners: dict[tuple[str, str], list[str]] = {}
    for project_key, identifiers in expected_by_project.items():
        for identifier in identifiers:
            expected_owners.setdefault(identifier, []).append(project_key)

    collisions: list[dict[str, Any]] = []
    colliding_identifiers: set[tuple[str, str]] = set()
    for identifier, owners in expected_owners.items():
        unique_owners = sorted(set(owners))
        if len(unique_owners) > 1:
            colliding_identifiers.add(identifier)
            collisions.append(
                {
                    "identifier_type": identifier[0],
                    "identifier_value": identifier[1],
                    "project_keys": unique_owners,
                }
            )

    return {
        "mode": "execute" if execute else "dry_run",
        "project_count": len(rows),
        "normalized_project_count": normalized_projects,
        "pruned_project_count": pruned_projects,
        "manual_repo_seed_repair_count": manual_repo_seed_repairs,
        "developer_account_repair_count": developer_account_repairs,
        "normalized_child_change_count": normalized_child_changes,
        "normalized_child_change_examples": normalized_child_change_examples,
        "current_service_projection_change_count": current_service_projection_changes,
        "current_service_projection_examples": current_service_projection_examples,
        "refreshed_derived_project_count": refreshed_derived_projects,
        "orphan_count": 0,
        "stale_count": 0,
        "missing_count": 0,
        "collision_count": len(collisions),
        "repaired": {
            "deleted_orphans": 0,
            "deleted_stale": 0,
            "inserted_missing": 0,
        },
        "orphan_rows": [],
        "stale_rows": [],
        "missing_rows": [],
        "collisions": collisions[:100],
    }


def identifier_has_active_confirm_decision(project: dict[str, Any], identifier_type: str, value: str) -> bool:
    normalized = project_record.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized:
        return False
    for item in project_record.normalize_confirmed_identifiers(list(project.get("confirmed_identifiers", []))):
        if (
            str(item.get("type") or "") == identifier_type
            and str(item.get("value") or "") == normalized
            and not str(item.get("cleared_at") or "")
        ):
            return True
    return False


def identifier_is_manual_seed(project: dict[str, Any], identifier_type: str, value: str) -> bool:
    normalized = project_record.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized:
        return False
    for seed in project.get("manual_seeds", []):
        if not isinstance(seed, dict) or str(seed.get("type") or "") != identifier_type:
            continue
        if project_record.normalize_blocked_identifier_value(identifier_type, str(seed.get("value") or "")) == normalized:
            return True
    return False


def trust_explanation(project: dict[str, Any]) -> dict[str, Any]:
    project = project_record.ensure_project_shape(project)
    identifiers = runtime_view.project_identifiers(project)
    rows: list[dict[str, Any]] = []
    for item in identifiers:
        kind = "collection" if item.kind == "nsid" else item.kind
        if kind not in trust_graph_core.NODE_KINDS:
            continue
        node = project_record.trust_node(kind, item.value)
        if not node:
            continue
        evaluation = {}
        directional_paths: list[dict[str, Any]] = []
        if item.kind in {"service_url", "repo_url"}:
            evaluation = trust_graph_core.evaluate_auto_confirm(
                project,
                candidate=node,
                anchors=trusted_anchor_nodes(project),
            )
            directional_paths = trust_graph_core.directional_paths(
                project,
                candidate=node,
                anchors=trusted_anchor_nodes(project),
            )
        elif item.kind == "nsid":
            evaluation = trust_collection_ownership_evaluation(project, item.value)
            directional_paths = trust_graph_core.directional_paths(
                project,
                candidate=node,
                anchors=collection_trusted_anchor_nodes(project),
                allowed_node_kinds=COLLECTION_TRUST_NODE_KINDS,
                allowed_predicates=COLLECTION_TRUST_PREDICATES,
            )
            if evaluation.get("policy_version") == collection_trust.POLICY_VERSION and evaluation.get("auto_confirm"):
                directional_paths = [{
                    "anchor": evaluation["anchor"], "candidate": node, "scope": evaluation["scope"],
                    "anchor_to_candidate": evaluation["forward_path"],
                    "candidate_to_anchor": evaluation["backward_path"],
                }]
        elif item.kind in IDENTITY_TRUST_NODE_KINDS:
            evaluation = trust_auto_confirm_identifier(project, item.kind, item.value)
            directional_paths = trust_graph_core.directional_paths(
                project,
                candidate=node,
                anchors=identity_trusted_anchor_nodes(project),
                allowed_node_kinds=IDENTITY_TRUST_NODE_KINDS,
                allowed_predicates=IDENTITY_TRUST_PREDICATES,
            )
        rows.append({
            "identifier": {"kind": item.kind, "value": item.value, "role": item.role},
            "node": node,
            "classification": item.classification,
            "manual_seed": identifier_is_manual_seed(project, item.kind, item.value),
            "operator_confirmed": identifier_has_active_confirm_decision(project, item.kind, item.value),
            "source_confirmed": item.kind == "nsid" and item.role == "owned_nsid" and item.classification == "confirmed",
            "guarded": project_record.identifier_is_blocked(project, item.kind, item.value)
            or project_record.identifier_is_deferred(project, item.kind, item.value),
            "evaluation": evaluation,
            "directional_paths": directional_paths,
        })
    edges = trust_graph_core.graph(project).get("edges", [])
    auto_confirmed = [row for row in rows if bool(row.get("evaluation", {}).get("auto_confirm"))]
    unsupported_confirmed = [
        row for row in rows
        if row["classification"] == "confirmed"
        and row["identifier"]["kind"] in {"service_url", "repo_url", "nsid", "did", "handle", "domain"}
        and not row["manual_seed"]
        and not row["operator_confirmed"]
        and not row["source_confirmed"]
        and not bool(row.get("evaluation", {}).get("auto_confirm"))
    ]
    return {
        "topic": "trust",
        "conclusion": "needs_review" if unsupported_confirmed else ("supported" if auto_confirmed else "unknown"),
        "rule": "trust-graph-v1 mutual directed trust paths for URL, collection, DID, handle, and domain identifiers",
        "supporting_signals": [
            f"{row['identifier']['kind']}:{row['identifier']['value']}"
            for row in auto_confirmed[:20]
        ],
        "counter_signals": [
            f"unsupported_confirmed:{row['identifier']['kind']}:{row['identifier']['value']}"
            for row in unsupported_confirmed[:20]
        ],
        "identifier_evaluations": rows,
        "edge_count": len(edges),
        "edges": edges[:100],
        "anchors": trusted_anchor_nodes(project),
        "identity_anchors": identity_trusted_anchor_nodes(project),
        "collection_anchors": collection_trusted_anchor_nodes(project),
    }


def refresh_trust_graph_evaluations(project: dict[str, Any]) -> dict[str, Any]:
    before = project_record.dump_json(trust_graph_core.graph(project))
    graph = trust_graph_core.stored_graph(project)
    graph["evaluations"] = []
    trust_graph_core.set_graph(project, graph)
    evaluated = 0
    auto_confirmable = 0
    for item in runtime_view.project_identifiers(project):
        if item.kind in {"service_url", "repo_url", "nsid", "did", "handle", "domain"}:
            evaluation = trust_auto_confirm_identifier(project, item.kind, item.value)
        else:
            continue
        evaluated += 1
        if evaluation.get("auto_confirm"):
            auto_confirmable += 1
    after = project_record.dump_json(trust_graph_core.graph(project))
    return {
        "changed": before != after,
        "evaluated_identifier_count": evaluated,
        "auto_confirmable_identifier_count": auto_confirmable,
    }


def classify_trust_identifier_issue(item: dict[str, Any]) -> str:
    if item.get("guarded"):
        return "guarded_confirmed_identifier"
    if item.get("manual_seed") or item.get("operator_confirmed"):
        return "operator_confirmed_identifier"
    if item.get("source_confirmed"):
        return "source_confirmed_identifier"
    evaluation = item.get("evaluation") if isinstance(item.get("evaluation"), dict) else {}
    if evaluation.get("auto_confirm"):
        return "supported_by_trust_graph"
    directional = item.get("directional_paths") if isinstance(item.get("directional_paths"), list) else []
    if directional:
        return "needs_reprobe"
    return "should_downgrade"


def build_trust_graph_audit(conn: sqlite3.Connection, *, example_limit: int = 20) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM projects ORDER BY project_key ASC").fetchall()
    issues: dict[str, list[dict[str, Any]]] = {
        "needs_reprobe": [],
        "should_downgrade": [],
        "unsupported_confirmed_identifier": [],
        "guarded_confirmed_identifier": [],
        "unsupported_confirmed_anchor_dependency": [],
    }
    project_count = 0
    edge_count = 0
    seen_issue_keys: set[tuple[str, str, str, str]] = set()
    for row in rows:
        project = project_record.ensure_project_shape(project_record.project_from_row(conn, row))
        project_count += 1
        explanation = trust_explanation(project)
        edge_count += int(explanation.get("edge_count") or 0)
        for item in explanation.get("identifier_evaluations", []):
            identifier = item.get("identifier") if isinstance(item.get("identifier"), dict) else {}
            identifier_type = str(identifier.get("kind") or "")
            value = str(identifier.get("value") or "")
            classification = str(item.get("classification") or "")
            if identifier_type not in {"service_url", "repo_url", "nsid", "did", "handle", "domain"} or classification != "confirmed":
                continue
            issue = {
                "project_key": str(project.get("project_key") or ""),
                "identifier_type": identifier_type,
                "value": value,
                "role": str(identifier.get("role") or ""),
                "evaluation_reason": str((item.get("evaluation") or {}).get("reason") or ""),
            }
            classification_kind = classify_trust_identifier_issue(item)
            issue_key = (classification_kind, issue["project_key"], identifier_type, value)
            if issue_key in seen_issue_keys:
                continue
            seen_issue_keys.add(issue_key)
            issue["classification"] = classification_kind
            if classification_kind in issues:
                issues[classification_kind].append(issue)
            if item.get("manual_seed") or item.get("operator_confirmed") or item.get("source_confirmed"):
                continue
            if not bool((item.get("evaluation") or {}).get("auto_confirm")):
                issues["unsupported_confirmed_identifier"].append(issue)
                issues["unsupported_confirmed_anchor_dependency"].append(issue)
    return {
        "mode": "dry_run",
        "policy_version": trust_graph_core.TRUST_POLICY_VERSION,
        "project_count": project_count,
        "trust_edge_count": edge_count,
        "issue_counts": {key: len(value) for key, value in issues.items()},
        "examples": {
            key: value[: max(0, int(example_limit))]
            for key, value in issues.items()
        },
    }


def repair_trust_graph(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    downgrade_unsupported: bool = False,
    example_limit: int = 20,
) -> dict[str, Any]:
    if downgrade_unsupported and not execute:
        raise ValueError("--downgrade-unsupported requires --execute")
    rows = conn.execute("SELECT * FROM projects ORDER BY project_key ASC").fetchall()
    changed_projects: list[dict[str, Any]] = []
    downgraded_identifiers: list[dict[str, str]] = []
    evaluated_identifier_count = 0
    auto_confirmable_identifier_count = 0
    for row in rows:
        project = project_record.ensure_project_shape(project_record.project_from_row(conn, row))
        project_key = str(project.get("project_key") or "")
        refresh = refresh_trust_graph_evaluations(project)
        evaluated_identifier_count += int(refresh.get("evaluated_identifier_count") or 0)
        auto_confirmable_identifier_count += int(refresh.get("auto_confirmable_identifier_count") or 0)

        downgrade_values: list[tuple[str, str]] = []
        seen_downgrade_values: set[tuple[str, str]] = set()
        if downgrade_unsupported:
            while True:
                batch: list[tuple[str, str]] = []
                explanation = trust_explanation(project)
                for item in explanation.get("identifier_evaluations", []):
                    identifier = item.get("identifier") if isinstance(item.get("identifier"), dict) else {}
                    identifier_type = str(identifier.get("kind") or "")
                    value = str(identifier.get("value") or "")
                    if identifier_type not in {"service_url", "repo_url", "nsid", "did", "handle", "domain"}:
                        continue
                    if str(item.get("classification") or "") != "confirmed":
                        continue
                    if classify_trust_identifier_issue(item) != "should_downgrade":
                        continue
                    normalized_value = project_record.normalize_blocked_identifier_value(identifier_type, value)
                    key = (identifier_type, normalized_value)
                    if key in seen_downgrade_values:
                        continue
                    seen_downgrade_values.add(key)
                    batch.append(key)
                if not batch:
                    break
                for identifier_type, value in batch:
                    downgrade_identifier_to_candidate(project, identifier_type, value)
                    downgraded_identifiers.append({
                        "project_key": project_key,
                        "identifier_type": identifier_type,
                        "value": project_record.normalize_blocked_identifier_value(identifier_type, value),
                    })
                downgrade_values.extend(batch)
                refresh_trust_graph_evaluations(project)

        if refresh.get("changed") or downgrade_values:
            changed_projects.append({
                "project_key": project_key,
                "trust_graph_changed": bool(refresh.get("changed")),
                "downgraded_identifier_count": len(downgrade_values),
            })
            if execute:
                project_record.save_project(conn, project, enriched=False, preserve_timestamps=True)

    if execute:
        conn.commit()
    return {
        "mode": "execute" if execute else "dry_run",
        "policy_version": trust_graph_core.TRUST_POLICY_VERSION,
        "project_count": len(rows),
        "evaluated_identifier_count": evaluated_identifier_count,
        "auto_confirmable_identifier_count": auto_confirmable_identifier_count,
        "changed_project_count": len(changed_projects),
        "updated_project_count": len(changed_projects) if execute else 0,
        "downgrade_unsupported": bool(downgrade_unsupported),
        "downgraded_identifier_count": len(downgraded_identifiers) if execute else 0,
        "changed_project_examples": changed_projects[: max(0, int(example_limit))],
        "downgraded_identifier_examples": downgraded_identifiers[: max(0, int(example_limit))],
    }


def bsky_auth_settings() -> dict[str, str]:
    return {
        "identifier": os.getenv("BSKY_HANDLE", "").strip(),
        "password": os.getenv("BSKY_APP_PASSWORD", "").strip(),
        "host": os.getenv("BSKY_AUTH_HOST", BSKY_ENTRYWAY).rstrip("/"),
    }


def bsky_session_cache_key() -> str:
    settings = bsky_auth_settings()
    return f"{settings['host']}|{settings['identifier']}"


def bsky_pds_url_from_session(session: dict[str, Any]) -> str:
    did_doc = session.get("didDoc")
    if isinstance(did_doc, dict):
        for service in did_doc.get("service", []):
            if not isinstance(service, dict):
                continue
            if str(service.get("id") or "") != "#atproto_pds":
                continue
            endpoint = str(service.get("serviceEndpoint") or "").rstrip("/")
            if endpoint.startswith("https://"):
                return endpoint
    return ""


def bsky_create_session() -> dict[str, Any]:
    settings = bsky_auth_settings()
    if not settings["identifier"] or not settings["password"]:
        raise RuntimeError("BSKY_HANDLE and BSKY_APP_PASSWORD are required for authenticated Bluesky search")
    cache_key = bsky_session_cache_key()
    cached = _BSKY_SESSION_CACHE.get(cache_key)
    if cached:
        return cached
    payload = http_transport.request_json_post(
        f"{settings['host']}/xrpc/com.atproto.server.createSession",
        {"identifier": settings["identifier"], "password": settings["password"]},
    )
    _BSKY_SESSION_CACHE[cache_key] = payload
    return payload


def bsky_access_token() -> str:
    session = bsky_create_session()
    token = str(session.get("accessJwt") or "")
    if not token:
        raise RuntimeError("Bluesky session did not return accessJwt")
    return token


def bsky_refresh_session() -> dict[str, Any]:
    cache_key = bsky_session_cache_key()
    session = bsky_create_session()
    refresh_token = str(session.get("refreshJwt") or "")
    if not refresh_token:
        raise RuntimeError("Bluesky session did not return refreshJwt")
    pds_url = bsky_pds_url_from_session(session)
    if not pds_url:
        settings = bsky_auth_settings()
        pds_url = settings["host"]
    _, body, _ = http_transport.request_bytes(
        f"{pds_url}/xrpc/com.atproto.server.refreshSession",
        headers={"Authorization": f"Bearer {refresh_token}"},
        method="POST",
    )
    refreshed = json.loads(body.decode("utf-8"))
    if not isinstance(refreshed, dict):
        raise RuntimeError("Bluesky refreshSession response was not a JSON object")
    if not refreshed.get("didDoc") and session.get("didDoc"):
        refreshed["didDoc"] = session["didDoc"]
    _BSKY_SESSION_CACHE[cache_key] = refreshed
    return refreshed


def bsky_pds_url() -> str:
    session = bsky_create_session()
    pds_url = bsky_pds_url_from_session(session)
    if pds_url:
        return pds_url
    settings = bsky_auth_settings()
    if settings["host"]:
        return settings["host"]
    raise RuntimeError("Bluesky session did not include a resolvable PDS endpoint")


def bsky_authenticated_app_url(path: str) -> str:
    base = bsky_pds_url()
    return f"{base.rstrip('/')}/xrpc/{path.lstrip('/')}"


def extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return unescape(re.sub(r"\s+", " ", match.group(1)).strip()) if match else ""


def extract_meta_description(html: str) -> str:
    patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return unescape(re.sub(r"\s+", " ", match.group(1)).strip())
    return ""


def project_service_identity_hosts(project: dict[str, Any]) -> set[str]:
    hosts = set(project_record.manual_service_hosts(project))
    for domain in project.get("authority_domains", []):
        lowered = str(domain or "").strip().lower()
        if lowered and not project_record.is_shared_official_identity_host(lowered):
            hosts.add(lowered)
    for url in project.get("service_urls", []):
        host = urllib.parse.urlparse(identifier_core.normalize_url(url)).netloc.lower()
        if host and not project_record.is_shared_official_identity_host(host):
            hosts.add(host)
    return {host for host in hosts if host}


def service_candidate_matches_project(project: dict[str, Any], url: str) -> bool:
    parsed = urllib.parse.urlparse(identifier_core.normalize_url(url))
    host = parsed.netloc.lower()
    if not host:
        return False
    identity_hosts = project_service_identity_hosts(project)
    if host in identity_hosts:
        return True
    if domain_policy_core.root_domain(host) in {domain_policy_core.root_domain(value) for value in identity_hosts if "." in value}:
        return True
    normalized_host = host.replace("-", "").replace("_", "")
    return any(token and token in normalized_host for token in project_record.project_alias_tokens(project))


def service_url_auto_confirmable(project: dict[str, Any], url: str) -> bool:
    normalized = project_record.normalize_confirmed_service_url(url)
    if not normalized:
        return False
    host = urllib.parse.urlparse(normalized).netloc.lower()
    if not host:
        return False

    manual_hosts = project_record.manual_service_hosts(project)
    if host in manual_hosts:
        return True

    authority_domains = {
        str(domain or "").strip().lower()
        for domain in project.get("authority_domains", [])
        if str(domain or "").strip()
    }
    if host in authority_domains and not project_record.is_shared_official_identity_host(host):
        return True

    if service_candidate_matches_project(project, normalized):
        alias_tokens = project_record.project_alias_tokens(project)
        normalized_host = host.replace("-", "").replace("_", "")
        if any(token and token in normalized_host for token in alias_tokens):
            return True

    return False


def extract_urls(html: str) -> list[str]:
    pattern = r'https?://[^\s"\'<>]+'
    output: list[str] = []
    for raw in re.findall(pattern, html):
        cleaned = identifier_core.sanitize_extracted_url(raw)
        parsed = urllib.parse.urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        output.append(identifier_core.normalize_url(cleaned))
    for raw in re.findall(r'\]\((https?://[^)\s]+)\)', html):
        cleaned = identifier_core.sanitize_extracted_url(raw)
        parsed = urllib.parse.urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        output.append(identifier_core.normalize_url(cleaned))
    return project_record.unique_list(output)


def normalize_tagline(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.split(r"[。．.!！?？\n\r]", text, maxsplit=1)[0].strip()
    text = re.sub(r"\s+", "", text)
    text = project_record.normalize_ja_en_mixed_spacing(text)
    text = re.sub(r"(です|ます|でした|ます。)$", "", text)
    if "の" in text and len(text) > 12:
        suffix = text.rsplit("の", 1)[-1].strip()
        if 2 <= len(suffix) <= 30:
            text = suffix
    text = text.strip("・-:：,，、")
    return text[:30].rstrip("・-:：,，、")


def add_tagline_candidate(project: dict[str, Any], value: str, source: str, *, provisional: bool | None = None) -> None:
    normalized = normalize_tagline(value)
    if not normalized:
        return
    candidates = project.get("tagline_candidates", [])
    if any(
        isinstance(item, dict)
        and str(item.get("value") or "") == normalized
        and str(item.get("source") or "") == source
        for item in candidates
    ):
        return
    candidate = {
        "value": normalized,
        "source": source,
        "status": "candidate",
        "added_at": project_record.iso_now(),
    }
    if provisional is not None:
        candidate["provisional"] = bool(provisional)
    candidates.append(candidate)
    project["tagline_candidates"] = candidates


def service_url_allowed_for_project(project: dict[str, Any], url: str, *, confirmed: bool) -> bool:
    normalized = project_record.normalize_confirmed_service_url(url) if confirmed else project_record.normalize_project_service_candidate_url(project, url)
    if not normalized:
        return False
    if not confirmed and any(claim["url"] == normalized for claim in repository_analysis_core.service_publications(project)):
        return not service_url_is_guarded(project, normalized)
    if confirmed and url_has_current_trust(project, "service_url", normalized):
        return True
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.netloc.lower()
    if not host:
        return False
    if project_record.is_explicitly_non_project_service_url(normalized):
        return False
    if host in project_record.PDSLS_VIEWER_HOSTS and not integration_core.project_explicitly_targets_pdsls(project):
        return False
    if host in project_record.PDSLS_HOSTS and not integration_core.project_explicitly_targets_pdsls(project):
        return False

    manual_hosts = set(project_record.manual_service_hosts(project))
    manual_hosts.update(
        urllib.parse.urlparse(identifier_core.normalize_url(value)).netloc.lower()
        for value in project_record.active_confirmed_identifier_values(project, "service_url")
        if identifier_core.normalize_url(value)
    )
    baseline_hosts = set(manual_hosts)
    for domain in project.get("authority_domains", []):
        lowered = str(domain or "").strip().lower()
        if lowered and not project_record.is_shared_official_identity_host(lowered):
            baseline_hosts.add(lowered)

    baseline_roots = {domain_policy_core.root_domain(item) for item in baseline_hosts if item}
    if host in baseline_hosts or domain_policy_core.root_domain(host) in baseline_roots:
        return True if not confirmed else host in manual_hosts or host in baseline_hosts

    alias_tokens = project_record.project_alias_tokens(project)
    if any(token and token in host.replace("-", "").replace("_", "") for token in alias_tokens):
        return True

    if confirmed:
        return service_url_auto_confirmable(project, normalized)
    return service_candidate_matches_project(project, normalized)


def url_has_current_trust(project: dict[str, Any], kind: str, value: str) -> bool:
    if project_record.identifier_is_blocked(project, kind, value) or project_record.identifier_is_deferred(project, kind, value):
        return False
    return bool(trust_graph_core.evaluate_auto_confirm(
        project, candidate=project_record.trust_node(kind, value), anchors=trusted_anchor_nodes(project),
    ).get("auto_confirm"))


def repo_url_allowed_for_project(project: dict[str, Any], url: str) -> bool:
    normalized = repo_url_core.canonical_repo_url(url)
    if not normalized:
        return False
    if url_has_current_trust(project, "repo_url", normalized):
        return True
    if normalized == "https://github.com/notjuliet/pdsls" and not integration_core.project_explicitly_targets_pdsls(project):
        return False
    if normalized in project_record.manual_repo_urls(project):
        return True
    if not repo_url_core.is_supported_repo_url(normalized):
        return False
    parsed = urllib.parse.urlparse(normalized)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return False
    repo_token = identifier_core.normalize_alias_token(segments[-1])
    alias_tokens = project_record.project_alias_tokens(project)
    if repo_token and repo_token in alias_tokens:
        return True
    return False


def repo_link_matches_project_identity(project: dict[str, Any], url: str) -> bool:
    normalized = repo_url_core.canonical_repo_url(url)
    if not normalized or not repo_url_core.is_supported_repo_url(normalized):
        return False
    segments = [segment for segment in urllib.parse.urlparse(normalized).path.split("/") if segment]
    if len(segments) < 2:
        return False
    repo_token = identifier_core.normalize_alias_token(segments[-1])
    if len(repo_token) < 4:
        return False
    identity_tokens: set[str] = set()
    name_token = identifier_core.normalize_alias_token(str(project.get("name") or ""))
    if name_token:
        identity_tokens.add(name_token)
    for service_url in project_record.unique_list(
        list(project.get("service_urls", []))
        + ([str(project.get("website_url") or "")] if project.get("website_url") else [])
        + project_record.manual_seed_values(project, "service_url")
    ):
        host = urllib.parse.urlparse(identifier_core.normalize_url(service_url)).netloc.lower()
        if host:
            identity_tokens.add(identifier_core.normalize_alias_token(host.removeprefix("www.")))
    return repo_token in identity_tokens


def trusted_repo_owner_identities(project: dict[str, Any]) -> set[str]:
    owners: set[str] = set()
    trusted_urls = set(project_record.manual_repo_urls(project)) | set(project.get("project_repository_urls", []))
    for url in trusted_urls:
        owner = repo_url_core.repo_owner_identity(str(url or ""))
        if owner:
            owners.add(owner)
    return owners


def repo_candidate_has_confirmed_component_relation(project: dict[str, Any], url: str) -> bool:
    target_identity = repo_url_core.repo_identity_key(url)
    if not target_identity:
        return False
    for item in relation_core.normalize_relation_records(project.get("relation_evidence", [])):
        relation = dict(item.get("relation") or {})
        subject = dict(relation.get("subject") or {})
        target = dict(relation.get("object") or {})
        context = dict(item.get("context") or {})
        if (
            str(item.get("classification") or "") == "confirmed"
            and str(relation.get("predicate") or "") == "project_repository"
            and str(relation.get("method") or "") == "bounded_first_party_link"
            and str(subject.get("type") or "") in {"service", "website"}
            and str(target.get("type") or "") == "repo"
            and bool(context.get("subject_is_exact_component"))
            and repo_url_core.repo_identity_key(str(target.get("value") or "")) == target_identity
        ):
            return True
    return False


def repo_candidate_allowed_for_project(project: dict[str, Any], url: str) -> bool:
    normalized = repo_url_core.canonical_repo_url(url)
    if not normalized:
        return False
    if repo_candidate_has_confirmed_component_relation(project, normalized):
        return True
    owner = repo_url_core.repo_owner_identity(normalized)
    developer_handles = {
        str(item.get("handle") or "").strip().lower().removeprefix("@")
        for item in project.get("developer_accounts", [])
        if isinstance(item, dict) and str(item.get("handle") or "").strip()
    }
    developer_handle_match = bool(owner and owner.lower().removeprefix("@") in developer_handles)
    if (
        not repo_url_allowed_for_project(project, normalized)
        and not repo_link_matches_project_identity(project, normalized)
        and not developer_handle_match
    ):
        return False
    if normalized in project_record.manual_repo_urls(project) or normalized in project.get("project_repository_urls", []):
        return True
    trusted_owners = trusted_repo_owner_identities(project)
    if trusted_owners:
        return bool(owner and owner in trusted_owners)
    if developer_handle_match:
        return True
    return True


def owner_matches_project(project: dict[str, Any], url: str) -> bool:
    owner = repo_url_core.repo_owner_identity(url)
    if not owner:
        return False
    known_tokens: set[str] = set()
    for domain in project.get("authority_domains", []):
        known_tokens.add(domain.lower())
        known_tokens.add(domain.split(".", 1)[0].lower())
    for handle in project.get("bluesky_handles", []):
        known_tokens.add(handle.lower())
        known_tokens.add(handle.split(".", 1)[0].lower())
    for seed in project.get("manual_seeds", []):
        value = str(seed.get("value") or "").lower()
        if value:
            known_tokens.add(value)
    if project.get("name"):
        known_tokens.add(str(project["name"]).lower())
    return owner in known_tokens


def github_repo_relevance(project: dict[str, Any], repo: dict[str, Any]) -> int:
    haystacks = [
        str(repo.get("url") or "").lower(),
        str(repo.get("full_name") or "").lower(),
        str(repo.get("description") or "").lower(),
        str(repo.get("homepage") or "").lower(),
    ]
    identities = integration_core.project_identity_strings(project)
    score = 0
    for needle in identities:
        if not needle:
            continue
        if any(needle in haystack for haystack in haystacks):
            score += 3
    owner = repo_url_core.repo_owner_identity(str(repo.get("url") or ""))
    if owner and owner_matches_project(project, str(repo.get("url") or "")):
        score += 3
    homepage = str(repo.get("homepage") or "").strip()
    if homepage:
        for service_url in project.get("service_urls", []):
            if domain_policy_core.is_same_site_family(homepage, service_url):
                score += 4
                break
        if project.get("website_url") and domain_policy_core.is_same_site_family(homepage, project["website_url"]):
            score += 4
    return score


def bsky_actor_relevance(project: dict[str, Any], actor: dict[str, Any]) -> int:
    handle = str(actor.get("handle") or "").strip().lower()
    display_name = str(actor.get("display_name") or "").strip().lower()
    description = str(actor.get("description") or "").strip().lower()
    identities = integration_core.project_identity_strings(project)
    score = 0
    if integration_core.handle_matches_project(project, handle):
        score += 5
    for needle in identities:
        if needle and (needle in display_name or needle in description):
            score += 3
    if project.get("website_url") and handle:
        website_host = urllib.parse.urlparse(identifier_core.normalize_url(project["website_url"])).netloc.lower()
        if handle == website_host:
            score += 4
    return score


class AnchorExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []
        self._link_attributes: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        href = attr_map.get("href", "").strip()
        if not href:
            return
        self._href = urllib.parse.urljoin(self.base_url, href)
        self._text_parts = []
        self._link_attributes = {
            "aria_label": unescape(attr_map.get("aria-label", "").strip()),
            "title": unescape(attr_map.get("title", "").strip()),
        }

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = unescape(re.sub(r"\s+", " ", "".join(self._text_parts)).strip())
        self.links.append({"url": self._href, "text": text, **self._link_attributes})
        self._href = None
        self._text_parts = []
        self._link_attributes = {}


def extract_links(html: str, base_url: str) -> list[dict[str, str]]:
    parser = AnchorExtractor(base_url)
    parser.feed(html)
    seen: set[tuple[str, str, str, str]] = set()
    output: list[dict[str, str]] = []
    for item in parser.links:
        url = identifier_core.normalize_url(item["url"])
        text = item["text"]
        aria_label = item.get("aria_label", "")
        title = item.get("title", "")
        key = (url, text, aria_label, title)
        if key in seen:
            continue
        seen.add(key)
        output.append({"url": url, "text": text, "aria_label": aria_label, "title": title})
    return output


def link_label(link: dict[str, Any]) -> str:
    return " ".join(
        value
        for value in (
            str(link.get("text") or "").strip(),
            str(link.get("aria_label") or "").strip(),
            str(link.get("title") or "").strip(),
        )
        if value
    )


def classify_official_link(base_url: str, url: str, text: str) -> str:
    normalized = identifier_core.normalize_url(url)
    lowered_text = text.lower()
    lowered_url = normalized.lower()
    source_marker = bool(re.search(r"\b(?:source(?:\s+code)?|repo|repository|repositories|monorepo)\b", lowered_text))
    if project_record.is_bluesky_profile_url(normalized):
        return "bluesky_profile"
    if repo_url_core.is_supported_repo_url(normalized):
        if source_marker:
            return "source_repo"
    if re.search(r"\b(?:source(?:\s+code)?|monorepo)\b", lowered_text):
        return "source_like"
    if "docs" in lowered_text or "/docs" in lowered_url or normalized.startswith("https://docs."):
        return "docs"
    if "blog" in lowered_text or ".org/blog" in lowered_url or normalized.startswith("https://blog."):
        return "blog"
    if domain_policy_core.is_same_site_family(base_url, normalized):
        return "same_site"
    return "other"


def add_candidate_url(project: dict[str, Any], field_name: str, url: str) -> None:
    key = f"{field_name}_candidates"
    if field_name in {"service_url", "website_url"}:
        normalized = project_record.normalize_project_service_candidate_url(project, url)
        canonical_alias = url_alias_core.canonical_alias_url(project, normalized)
        confirmed = {
            identifier_core.normalize_url(str(item)) for item in project.get("service_urls", [])
        }
        if canonical_alias != normalized and canonical_alias in confirmed:
            return
        if not normalized or project_record.identifier_is_blocked(project, "service_url", normalized):
            return
        project[key] = project_record.add_unique(project.get(key, []), normalized)
        return
    normalized = identifier_core.normalize_url(url)
    if not normalized:
        return
    project[key] = project_record.add_unique(project.get(key, []), normalized)


def add_candidate_project_repository_url(
    project: dict[str, Any],
    url: str,
    source: str,
    *,
    reason: str = "",
    evidence_kind: str = "",
    evidence_url: str = "",
    relation: str = "candidate_repo",
    updated_at: str = "",
    admitted_by_relation: bool = False,
) -> None:
    normalized = repo_url_core.canonical_repo_url(url)
    if project_record.identifier_is_blocked(project, "repo_url", normalized):
        return
    if not admitted_by_relation and not repo_candidate_allowed_for_project(project, normalized):
        return
    if normalized in project.get("project_repository_urls", []):
        return
    project["candidate_project_repository_urls"] = project_record.add_unique(project.get("candidate_project_repository_urls", []), normalized)
    record = {
        "url": normalized,
        "provider": repo_url_core.infer_repo_provider(normalized),
        "confidence": "candidate",
        "relation": relation,
        "source": source,
        "reason": reason,
        "evidence_kind": evidence_kind,
        "evidence_url": identifier_core.normalize_url(evidence_url) if evidence_url else "",
        "recorded_at": project_record.iso_now(),
        "updated_at": str(updated_at or ""),
    }
    project.setdefault("source_repo_records", [])
    if not any(
        item.get("url") == normalized
        and item.get("confidence") == "candidate"
        and item.get("source") == source
        and item.get("evidence_kind", "") == record["evidence_kind"]
        for item in project["source_repo_records"]
    ):
        project["source_repo_records"].append(record)
    project.setdefault("investigation_log", [])
    entry = {
        "kind": "candidate_project_repository_url",
        "url": normalized,
        "source": source,
        "reason": reason,
        "evidence_kind": evidence_kind,
        "evidence_url": record["evidence_url"],
        "added_at": project_record.iso_now(),
    }
    if entry not in project["investigation_log"]:
        project["investigation_log"].append(entry)


def confirm_project_repository_url(
    project: dict[str, Any],
    url: str,
    source: str,
    relation: str = "source_repo",
    *,
    reason: str = "",
    evidence_kind: str = "",
    evidence_url: str = "",
    updated_at: str = "",
    operator_override: bool = False,
) -> None:
    normalized = repo_url_core.canonical_repo_url(url)
    if not normalized:
        return
    if not operator_override and (
        project_record.identifier_is_blocked(project, "repo_url", normalized)
        or project_record.identifier_is_deferred(project, "repo_url", normalized)
    ):
        return
    project["project_repository_urls"] = project_record.add_unique(project.get("project_repository_urls", []), normalized)
    project["candidate_project_repository_urls"] = [item for item in project.get("candidate_project_repository_urls", []) if item != normalized]
    provider = repo_url_core.infer_repo_provider(normalized)
    record = {
        "url": normalized,
        "provider": provider,
        "confidence": "confirmed",
        "relation": relation,
        "source": source,
        "reason": reason,
        "evidence_kind": evidence_kind,
        "evidence_url": identifier_core.normalize_url(evidence_url) if evidence_url else "",
        "recorded_at": project_record.iso_now(),
        "updated_at": str(updated_at or ""),
    }
    project.setdefault("source_repo_records", [])
    if not any(
        item.get("url") == normalized
        and item.get("confidence") == "confirmed"
        and item.get("source") == source
        and item.get("evidence_kind", "") == record["evidence_kind"]
        for item in project["source_repo_records"]
    ):
        project["source_repo_records"].append(record)
    project_record.sync_source_repo_provider_views(project)


def confirm_service_url(project: dict[str, Any], url: str, *, operator_override: bool = False) -> None:
    normalized = project_record.normalize_confirmed_service_url(url)
    if not normalized:
        return
    if not operator_override and (
        project_record.identifier_is_blocked(project, "service_url", normalized)
        or project_record.identifier_is_deferred(project, "service_url", normalized)
    ):
        return
    project["service_urls"] = project_record.add_unique(project.get("service_urls", []), normalized)
    project_record.set_service_url_candidate_values(
        project,
        [item for item in project_record.service_url_candidate_values(project) if identifier_core.normalize_url(item) != normalized],
    )
    project["failed_service_urls"] = [item for item in project.get("failed_service_urls", []) if identifier_core.normalize_url(item) != normalized]


def url_origin(url: str) -> str:
    parsed = urllib.parse.urlparse(identifier_core.normalize_url(url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def deep_investigation_page_urls(project: dict[str, Any], *, max_pages: int = 6) -> list[str]:
    limit = max(1, int(max_pages or 6))
    urls: list[str] = []
    urls.extend(project_record.resource_url_values(project))

    service_surfaces = project_record.unique_list(
        [str(item or "") for item in project.get("service_urls", [])]
        + [str(item or "") for item in project.get("supported_service_urls", [])]
        + [str(item or "") for item in project.get("service_url_candidates", [])]
    )
    for service_url in service_surfaces:
        if project_record.is_bluesky_profile_url(service_url):
            continue
        origin = url_origin(service_url)
        if not origin:
            continue
        for path in DEEP_INVESTIGATION_PATHS:
            urls.append(identifier_core.normalize_url(f"{origin}{path}"))
    if project.get("website_url"):
        urls.append(identifier_core.normalize_url(str(project.get("website_url") or "")))
    return project_record.unique_list([url for url in urls if url])[:limit]


def deep_investigation_page_kind(url: str) -> str:
    path = urllib.parse.urlparse(identifier_core.normalize_url(url)).path.strip("/").lower()
    first = path.split("/", 1)[0] if path else ""
    return {
        "about": "about",
        "docs": "docs",
        "documentation": "docs",
        "faq": "faq",
        "app": "app",
        "login": "login",
        "signin": "login",
        "sign-in": "login",
        "signup": "signup",
        "sign-up": "signup",
        "register": "signup",
    }.get(first, "resource")


def service_probe_represents_service_identity(url: str, result: dict[str, Any]) -> bool:
    lane = str(result.get("lane") or "static")
    if lane == "follow_up":
        return False
    parsed = urllib.parse.urlparse(identifier_core.normalize_url(url))
    first = parsed.path.strip("/").split("/", 1)[0].lower()
    return not first or first not in service_core.SUPPORTING_PAGE_PATHS


def service_url_is_supporting_page(url: str) -> bool:
    normalized = identifier_core.normalize_url(url)
    return bool(normalized and not service_probe_represents_service_identity(normalized, {"lane": "static"}))


def supporting_page_resource_record(url: str, *, confidence: str) -> dict[str, str]:
    normalized = identifier_core.normalize_url(url)
    return {
        "url": normalized,
        "role": deep_investigation_page_kind(normalized),
        "source": "service_url_cleanup",
        "observed_at": "",
        "confidence": confidence if confidence in project_record.RESOURCE_URL_CONFIDENCE_VALUES else "candidate",
    }


def purge_supporting_page_service_observations(project: dict[str, Any]) -> None:
    def should_purge(url: str) -> bool:
        return service_url_is_supporting_page(url) and not identifier_has_active_confirm_decision(
            project,
            "service_url",
            url,
        )

    moved_records: list[dict[str, str]] = []
    cleaned_service_urls: list[str] = []
    for url in project.get("service_urls", []):
        normalized = identifier_core.normalize_url(str(url or ""))
        if normalized and should_purge(normalized):
            moved_records.append(supporting_page_resource_record(normalized, confidence="confirmed"))
            continue
        cleaned_service_urls.append(str(url))
    project["service_urls"] = project_record.unique_list(cleaned_service_urls)

    cleaned_supported_urls: list[str] = []
    for url in project.get("supported_service_urls", []):
        normalized = identifier_core.normalize_url(str(url or ""))
        if normalized and should_purge(normalized):
            moved_records.append(supporting_page_resource_record(normalized, confidence="confirmed"))
            continue
        cleaned_supported_urls.append(str(url))
    project["supported_service_urls"] = project_record.unique_list(cleaned_supported_urls)

    project["failed_service_urls"] = [
        url
        for url in project.get("failed_service_urls", [])
        if not should_purge(str(url or ""))
    ]

    candidate_records: list[dict[str, str]] = []
    cleaned_candidates: list[str] = []
    for url in project_record.service_url_candidate_values(project):
        if should_purge(url):
            candidate_records.append(supporting_page_resource_record(url, confidence="candidate"))
            continue
        cleaned_candidates.append(url)
    project_record.set_service_url_candidate_values(project, cleaned_candidates)

    if moved_records or candidate_records:
        project["resource_url_records"] = project_record.normalize_resource_url_records(
            list(project.get("resource_url_records", [])) + moved_records + candidate_records
        )


def normalize_service_identifier_views(project: dict[str, Any]) -> None:
    project["service_urls"] = project_record.unique_list(
        [
            normalized
            for item in project.get("service_urls", [])
            if (normalized := project_record.normalize_confirmed_service_url(str(item or "")))
        ]
    )
    confirmed_urls = {identifier_core.normalize_url(str(item or "")) for item in project.get("service_urls", []) if identifier_core.normalize_url(str(item or ""))}
    project["supported_service_urls"] = project_record.unique_list(
        [
            normalized
            for item in project.get("supported_service_urls", [])
            if (normalized := project_record.normalize_confirmed_service_url(str(item or "")))
        ]
    )
    project["failed_service_urls"] = project_record.unique_list(
        [
            normalized
            for item in project.get("failed_service_urls", [])
            if (normalized := project_record.normalize_service_candidate_url(str(item or "")))
        ]
    )
    project_record.set_service_url_candidate_values(
        project,
        [
            url
            for url in project_record.service_url_candidate_values(project)
            if identifier_core.normalize_url(url) not in confirmed_urls
        ],
    )


def apply_resource_page_probe_status(project: dict[str, Any], page_url: str, result: dict[str, Any]) -> None:
    observed_service_status = project_record.normalize_service_status(result.get("service_status"))
    observed_confidence = project_record.normalize_service_status_confidence(result.get("service_status_confidence"))
    observed_evidence = [str(item).strip() for item in result.get("service_status_evidence", []) if str(item).strip()]
    page_kind = deep_investigation_page_kind(page_url)
    if page_kind != "resource":
        observed_evidence = project_record.add_unique(observed_evidence, f"deep_page:{page_kind}")
    if observed_service_status in {"unknown"} and not observed_evidence:
        return
    project["service_status"] = project_record.merge_service_status(
        str(project.get("service_status") or "unknown"),
        observed_service_status,
    )
    project["service_status_confidence"] = project_record.merge_service_status_confidence(
        str(project.get("service_status_confidence") or "none"),
        observed_confidence,
    )
    project["service_status_evidence"] = project_record.unique_list(list(project.get("service_status_evidence", [])) + observed_evidence)


def add_resource_url(
    project: dict[str, Any],
    url: str,
    *,
    role: str = "",
    source: str = "",
    confidence: str = "candidate",
) -> None:
    normalized = identifier_core.normalize_url(url)
    if not normalized:
        return
    if project_record.identifier_is_blocked(project, "service_url", normalized) or project_record.identifier_is_deferred(project, "service_url", normalized):
        return
    if normalized == identifier_core.normalize_url(str(project.get("website_url") or "")):
        return
    if normalized in {identifier_core.normalize_url(str(item or "")) for item in project.get("service_urls", [])}:
        return
    normalized_role = str(role or project_record.app_link_role_from_resource_url(normalized) or "unknown").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", normalized_role):
        normalized_role = "unknown"
    normalized_confidence = str(confidence or "candidate").strip().lower()
    if normalized_confidence not in project_record.RESOURCE_URL_CONFIDENCE_VALUES:
        normalized_confidence = "candidate"
    project["resource_url_records"] = project_record.normalize_resource_url_records(
        list(project.get("resource_url_records", []))
        + [
            {
                "url": normalized,
                "role": normalized_role,
                "source": str(source or "").strip(),
                "observed_at": project_record.iso_now(),
                "confidence": normalized_confidence,
            }
        ]
    )
    project_record.sync_resource_url_records(project)


def prune_project_urls(project: dict[str, Any], *, restore_manual_repositories: bool = True) -> None:
    blocked_guesses = unverified_authority_service_guesses(project)
    prunable_authority_guesses = prunable_nsid_authority_service_guesses(project)
    prune_nsid_authority_service_url_guesses(project)
    for field_name in ("service_urls",):
        cleaned_urls: list[str] = []
        for url in project.get(field_name, []):
            if project_record.is_bluesky_profile_url(url):
                project_record.record_developer_profile_url(project, url, f"{field_name}:profile_url")
                continue
            is_confirmed = field_name == "service_urls"
            normalized_candidate = identifier_core.normalize_url(str(url or ""))
            if not is_confirmed and normalized_candidate in blocked_guesses:
                continue
            if service_url_allowed_for_project(project, url, confirmed=is_confirmed):
                normalized = project_record.normalize_confirmed_service_url(url) if is_confirmed else project_record.normalize_project_service_candidate_url(project, url)
                if normalized:
                    cleaned_urls.append(normalized)
        project[field_name] = project_record.unique_list(cleaned_urls)
    cleaned_candidates: list[str] = []
    for url in project_record.service_url_candidate_values(project):
        if project_record.is_bluesky_profile_url(url):
            project_record.record_developer_profile_url(project, url, "service_url_candidates:profile_url")
            continue
        normalized_candidate = identifier_core.normalize_url(str(url or ""))
        if normalized_candidate in blocked_guesses:
            continue
        if normalized_candidate in prunable_authority_guesses:
            continue
        if service_url_allowed_for_project(project, url, confirmed=False):
            normalized = project_record.normalize_project_service_candidate_url(project, url)
            if normalized:
                cleaned_candidates.append(normalized)
    project_record.set_service_url_candidate_values(project, cleaned_candidates)

    website_url = identifier_core.normalize_url(str(project.get("website_url") or ""))
    if website_url and project_record.is_bluesky_profile_url(website_url):
        project["website_url"] = ""
    else:
        project["website_url"] = website_url

    active_manual_repos = project_record.active_manual_repo_urls(project)
    if not restore_manual_repositories:
        active_manual_repos.intersection_update(project.get("project_repository_urls", []))
    cleaned_records: list[dict[str, Any]] = []
    for record in project_record.normalize_source_repo_records(project.get("source_repo_records", [])):
        url = str(record.get("url") or "")
        relation = str(record.get("relation") or "")
        confidence = str(record.get("confidence") or "candidate")
        evidence_url = str(record.get("evidence_url") or "")
        same_site_evidence = bool(evidence_url and project.get("website_url") and domain_policy_core.is_same_site_family(evidence_url, project["website_url"]))
        if project_record.identifier_is_deferred(project, "repo_url", url):
            record = dict(record)
            record["confidence"] = "candidate"
        elif url in active_manual_repos:
            record = dict(record)
            record["confidence"] = "confirmed"
        elif relation in {"official_link", "same_site_repo"} and same_site_evidence:
            record = dict(record)
            record["confidence"] = "confirmed"
        elif confidence == "confirmed" and relation != "tangled_account_repo" and not repo_url_allowed_for_project(project, url):
            continue
        elif confidence != "confirmed" and not repo_candidate_allowed_for_project(project, url):
            continue
        elif relation == "readme_link" and confidence == "confirmed" and url not in project_record.manual_repo_urls(project):
            record = dict(record)
            record["confidence"] = "candidate"
        cleaned_records.append(record)
    project["source_repo_records"] = project_record.normalize_source_repo_records(cleaned_records)

    kept_confirmed = {
        url
        for url in repo_url_core.normalize_repo_collection(project.get("project_repository_urls", []))
        if not project_record.identifier_is_blocked(project, "repo_url", url)
        and not project_record.identifier_is_deferred(project, "repo_url", url)
    }

    project["project_repository_urls"] = [
        url
        for url in repo_url_core.normalize_repo_collection(project_record.unique_list(project.get("project_repository_urls", []) + sorted(active_manual_repos)))
        if url in kept_confirmed or url in active_manual_repos
    ]
    project["candidate_project_repository_urls"] = [
        url
        for url in repo_url_core.normalize_repo_collection(project.get("candidate_project_repository_urls", []))
        if repo_candidate_allowed_for_project(project, url) and url not in project["project_repository_urls"]
    ]

    project_record.sync_source_repo_provider_views(project)

    ignored_handles = integration_core.generic_platform_handles_to_ignore(project)
    if ignored_handles:
        project["bluesky_handles"] = [
            handle
            for handle in project_record.unique_list(project.get("bluesky_handles", []))
            if str(handle or "").strip().lower() not in ignored_handles
        ]
        project["developer_accounts"] = [
            dict(item)
            for item in project_record.normalize_developer_accounts(project.get("developer_accounts", []))
            if str(item.get("handle") or "").strip().lower() not in ignored_handles
        ]


def bsky_post_web_url(post: dict[str, Any]) -> str:
    author = post.get("author", {}) if isinstance(post.get("author"), dict) else {}
    handle = str(author.get("handle") or "").strip()
    uri = str(post.get("uri") or "").strip()
    rkey = uri.rsplit("/", 1)[-1] if uri else ""
    if handle and rkey:
        return f"https://bsky.app/profile/{handle}/post/{rkey}"
    return ""


def extract_post_link_entries(post: dict[str, Any]) -> list[dict[str, str]]:
    record = post.get("record", {}) if isinstance(post.get("record"), dict) else {}
    text = str(record.get("text") or "")
    raw = text.encode("utf-8")
    output: list[dict[str, str]] = []
    for facet in record.get("facets", []):
        if not isinstance(facet, dict):
            continue
        index_data = facet.get("index", {}) if isinstance(facet.get("index"), dict) else {}
        start = index_data.get("byteStart")
        end = index_data.get("byteEnd")
        label = ""
        if isinstance(start, int) and isinstance(end, int):
            try:
                label = raw[start:end].decode("utf-8", errors="replace").strip()
            except Exception:
                label = ""
        for feature in facet.get("features", []):
            if not isinstance(feature, dict):
                continue
            if feature.get("$type") != "app.bsky.richtext.facet#link":
                continue
            url = identifier_core.normalize_url(str(feature.get("uri") or ""))
            if not url:
                continue
            output.append(
                {
                    "label": label,
                    "url": url,
                    "post_uri": str(post.get("uri") or ""),
                    "post_web_url": bsky_post_web_url(post),
                }
            )
    return output


def collect_applog_entries(posts: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    entries_by_seed: dict[tuple[str, str], dict[str, Any]] = {}
    for post in posts:
        for entry in extract_post_link_entries(post):
            seed = applog_seed_from_url(entry["url"])
            if seed is None:
                continue
            seed_type, seed_value = seed
            key = (seed_type, seed_value)
            bucket = entries_by_seed.setdefault(
                key,
                {
                    "seed_type": seed_type,
                    "seed_value": seed_value,
                    "labels": [],
                    "post_web_urls": [],
                },
            )
            label = clean_imported_name(entry.get("label", ""))
            if label and label not in bucket["labels"]:
                bucket["labels"].append(label)
            post_web = str(entry.get("post_web_url") or "")
            if post_web and post_web not in bucket["post_web_urls"]:
                bucket["post_web_urls"].append(post_web)
    return entries_by_seed


def applog_seed_from_url(url: str) -> tuple[str, str] | None:
    collection_nsid = project_record.extract_collection_nsid(url)
    if collection_nsid:
        return ("nsid", collection_nsid)
    parsed = urllib.parse.urlparse(identifier_core.normalize_url(url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.netloc == "github.com" and len(segments) == 2:
        normalized = repo_url_core.normalize_github_repo_url(url)
        return ("repo_url", normalized) if normalized else None
    if parsed.netloc == "tangled.org" and len(segments) == 2:
        normalized = repo_url_core.normalize_tangled_repo_url(url)
        return ("repo_url", normalized) if normalized else None
    blocked_hosts = {
        "bsky.app",
        "raw.githubusercontent.com",
        "discourse.atprotocol.community",
        "youtube.com",
        "www.youtube.com",
        "youtu.be",
        "atproto.com",
        "docs.bsky.app",
        "docs.tangled.org",
        "blog.tangled.org",
    }
    if parsed.netloc in blocked_hosts:
        return None
    if not segments:
        return ("service_url", identifier_core.normalize_url(url))
    return None


def clean_imported_name(label: str) -> str:
    text = str(label or "").strip()
    text = text.strip("* ")
    text = text.rstrip("!！?？")
    text = re.sub(r"\s+", " ", text)
    return text


def add_manual_seed(project: dict[str, Any], seed_type: str, seed_value: str, *, source: str) -> None:
    normalized_value = project_record.canonical_seed_value(seed_type, seed_value)
    if project_record.identifier_is_deferred(project, seed_type, normalized_value):
        return
    seed_entry = {"type": seed_type, "value": normalized_value, "added_at": project_record.iso_now()}
    if source:
        seed_entry["source"] = source
    project["manual_seeds"] = project_record.unique_manual_seeds(
        list(project.get("manual_seeds", [])) + [seed_entry]
    )
    apply_seed_to_project(project, seed_type, normalized_value)
    if seed_type in project_record.IDENTIFIER_DECISION_TYPES:
        add_confirmed_identifier_decision(
            project,
            seed_type,
            normalized_value,
            source=source or "seed",
            note="identifier accepted as a project seed",
            role={
                "service_url": "service",
                "repo_url": "project_repository",
                "nsid": "owned_nsid",
            }.get(seed_type, ""),
            registration_origin=True,
        )
    if source:
        project["discovery_sources"] = project_record.add_unique(project.get("discovery_sources", []), source)


def add_collection_owned_nsid(
    project: dict[str, Any],
    nsid: str,
    *,
    source: str = "collection_observation",
    registration_origin: bool = False,
) -> bool:
    normalized = identifier_core.normalize_nsid(nsid)
    if not normalized or project_record.identifier_is_blocked(project, "nsid", normalized) or project_record.identifier_is_deferred(project, "nsid", normalized):
        return False
    before = set(project.get("resolved_nsids", []))
    project["resolved_nsids"] = project_record.add_unique(project.get("resolved_nsids", []), normalized)
    project["input_nsids"] = [
        value for value in project.get("input_nsids", [])
        if identifier_core.normalize_nsid(str(value or "")) != normalized
    ]
    authority_domain = identifier_core.derive_authority_domain(normalized)
    if authority_domain:
        project["authority_domains"] = project_record.add_unique(project.get("authority_domains", []), authority_domain)
        add_authority_service_candidates(project, authority_domain)
    changed = normalized not in before
    if changed:
        add_confirmed_identifier_decision(
            project,
            "nsid",
            normalized,
            source=source,
            note="collection accepted from an explicit collection source",
            role="owned_nsid",
            registration_origin=registration_origin,
        )
    return changed


def is_collection_inventory_source(source: str) -> bool:
    normalized = str(source or "").strip()
    return normalized in {"collection_inventory_import", "collection_observation_repair"} or normalized.startswith(
        "collection_inventory_url:"
    )


def confirm_service_seed(project: dict[str, Any], service_url: str, *, source: str) -> str:
    normalized = identifier_core.normalize_url(service_url)
    if not normalized or project_record.identifier_is_deferred(project, "service_url", normalized):
        return ""
    add_manual_seed(project, "service_url", normalized, source=source)
    if service_url_allowed_for_project(project, normalized, confirmed=True):
        project["service_urls"] = project_record.add_unique(project.get("service_urls", []), normalized)
    project_record.set_service_url_candidate_values(
        project,
        [item for item in project_record.service_url_candidate_values(project) if identifier_core.normalize_url(str(item or "")) != normalized],
    )
    return normalized


def confirm_repo_seed(project: dict[str, Any], repo_url: str, *, source: str) -> str:
    normalized = repo_url_core.canonical_repo_url(repo_url)
    if not normalized or project_record.identifier_is_deferred(project, "repo_url", normalized):
        return ""
    add_manual_seed(project, "repo_url", normalized, source=source)
    if repo_url_allowed_for_project(project, normalized):
        project["project_repository_urls"] = project_record.add_unique(project.get("project_repository_urls", []), normalized)
        project["evidence_urls"] = project_record.add_unique(project.get("evidence_urls", []), normalized)
        project_record.sync_source_repo_provider_views(project)
    project["candidate_project_repository_urls"] = [
        item for item in project.get("candidate_project_repository_urls", [])
        if repo_url_core.canonical_repo_url(str(item or "")) != normalized
    ]
    return normalized


def all_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM projects ORDER BY project_key")
    rows = cur.fetchall()
    return [project_record.project_from_row(conn, row) for row in rows]


def project_search_candidates(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 5,
    projects: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    text = str(query or "").strip()
    if not text:
        return []
    lowered = text.lower()
    alias = identifier_core.normalize_alias_token(text)
    candidates: list[tuple[int, dict[str, Any]]] = []
    search_projects = [project_record.ensure_project_shape(project) for project in (projects if projects is not None else all_projects(conn))]
    for project in search_projects:
        score = 0
        reasons: list[str] = []
        project_key = str(project.get("project_key") or "")
        name = str(project.get("name") or "")
        seed_value = str(project.get("seed_value") or "")
        identifiers = project_record.unique_list(
            ([seed_value] if seed_value else [])
            + [str(item or "") for item in project.get("input_nsids", [])]
            + [str(item or "") for item in project.get("resolved_nsids", [])]
            + ([str(project.get("website_url") or "")] if project.get("website_url") else [])
            + [str(item or "") for item in project.get("service_urls", [])]
            + [str(item or "") for item in project.get("project_repository_urls", [])]
        )
        if project_key == text:
            score += 100
            reasons.append("project_key_exact")
        elif project_key.startswith(text) or text.startswith(project_key):
            score += 70
            reasons.append("project_key_prefix")
        if seed_value and seed_value == text:
            score += 95
            reasons.append("seed_exact")
        if name and name.lower() == lowered:
            score += 80
            reasons.append("name_exact")
        elif name and lowered in name.lower():
            score += 45
            reasons.append("name_substring")
        if alias and alias in project_record.project_merge_aliases(project):
            score += 60
            reasons.append("alias_match")
        for identifier in identifiers:
            normalized_identifier = str(identifier or "").strip()
            if not normalized_identifier:
                continue
            if normalized_identifier == text:
                score += 90
                reasons.append("identifier_exact")
                break
            if lowered in normalized_identifier.lower():
                score += 35
                reasons.append("identifier_substring")
                break
        if score <= 0:
            continue
        candidates.append(
            (
                score,
                {
                    "project_key": project_key,
                    "name": name,
                    "seed_type": str(project.get("seed_type") or ""),
                    "seed_value": seed_value,
                    "tracking_state": str(project.get("tracking_state") or "active"),
                    "status": str(project.get("status") or ""),
                    "website_url": str(project.get("website_url") or ""),
                    "reasons": project_record.unique_list(reasons),
                    "score": score,
                },
            )
        )
    candidates.sort(key=lambda item: (-item[0], item[1]["project_key"]))
    return [item for _, item in candidates[: max(1, limit)]]


def upsert_collection_observation(project: dict[str, Any], item: dict[str, Any], *, source: str, merge_existing: bool = False) -> None:
    nsid = identifier_core.normalize_nsid(str(item.get("collection") or ""))
    if not nsid:
        return
    observation = {
        "collection": nsid,
        "count": int(item.get("count") or 0),
        "recent_count": int(item.get("recent_count") or 0),
        "min": str(item.get("min") or ""),
        "max": str(item.get("max") or ""),
        "source": source,
        "observed_at": project_record.iso_now(),
    }
    existing = [dict(entry) for entry in project.get("collection_observations", []) if isinstance(entry, dict)]
    replaced = False
    for index, entry in enumerate(existing):
        if identifier_core.normalize_nsid(str(entry.get("collection") or "")) == nsid:
            if merge_existing:
                merged_min = str(entry.get("min") or "")
                if observation["min"] and (not merged_min or observation["min"] < merged_min):
                    merged_min = observation["min"]
                merged_max = str(entry.get("max") or "")
                if observation["max"] and (not merged_max or observation["max"] > merged_max):
                    merged_max = observation["max"]
                existing[index] = {
                    "collection": nsid,
                    "count": int(entry.get("count") or 0) + int(observation.get("count") or 0),
                    "recent_count": int(entry.get("recent_count") or 0) + int(observation.get("recent_count") or 0),
                    "min": merged_min,
                    "max": merged_max,
                    "source": source,
                    "observed_at": project_record.iso_now(),
                }
            else:
                existing[index] = observation
            replaced = True
            break
    if not replaced:
        existing.append(observation)
    project["collection_observations"] = existing


def import_collection_inventory_payload(
    conn: sqlite3.Connection,
    payload: Any,
    *,
    execute: bool,
    create_unmatched: bool,
    source: str,
    merge_existing_observations: bool = False,
    rebuild_collection_observations: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, list):
        raise ValueError("collection inventory must be a JSON array")

    existing_projects = project_record.list_projects(conn)
    existing_keys = {str(project["project_key"]) for project in existing_projects}
    namespace_lookup, registrable_namespace_lookup, service_host_lookup = build_collection_project_matchers(existing_projects)
    parent_seed_namespace_lookup = build_nsid_seed_parent_namespace_lookup(existing_projects)

    aggregated: dict[tuple[str, str, str], dict[str, Any]] = {}
    parent_namespaces: set[str] = set()
    rejected_entries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        collection_nsid = identifier_core.normalize_nsid(str(item.get("collection") or ""))
        namespace = project_record.namespace_from_collection_nsid(collection_nsid)
        if not namespace:
            rejected_entries.append({"collection": collection_nsid, "reason": "namespace_too_short"})
            continue
        registrable_namespace = registrable_namespace_from_namespace(namespace) or namespace
        authority_domain = identifier_core.derive_authority_domain_from_namespace(registrable_namespace)
        if not domain_policy_core.authority_domain_allowed_for_import(authority_domain):
            rejected_entries.append({"collection": collection_nsid, "namespace": registrable_namespace, "reason": f"rejected_authority_domain:{authority_domain}"})
            continue
        parent_namespaces.add(namespace)
        matched_project_key, member_match_kind = _find_project_key_for_nsid_seed_with_lookups(
            existing_projects,
            collection_nsid,
            namespace_lookup=namespace_lookup,
            registrable_namespace_lookup=registrable_namespace_lookup,
            service_host_lookup=service_host_lookup,
            parent_seed_namespace_lookup=parent_seed_namespace_lookup,
        )
        safely_matched = bool(
            matched_project_key and sync_match_is_auto_attach_safe(member_match_kind)
        )
        group_key = (
            "existing" if safely_matched else "unmatched",
            str(matched_project_key or "") if safely_matched else "",
            namespace,
        )
        bucket = aggregated.setdefault(
            group_key,
            {
                "collection": namespace,
                "count": 0,
                "recent_count": 0,
                "min": str(item.get("min") or ""),
                "max": str(item.get("max") or ""),
                "members": [],
                "matched_project_key": str(matched_project_key or "") if safely_matched else "",
                "member_match_kinds": [],
            },
        )
        bucket["count"] += int(item.get("count") or 0)
        bucket["recent_count"] += int(item.get("recent_count") or 0)
        item_min = str(item.get("min") or "")
        item_max = str(item.get("max") or "")
        if item_min and (not bucket["min"] or item_min < bucket["min"]):
            bucket["min"] = item_min
        if item_max and (not bucket["max"] or item_max > bucket["max"]):
            bucket["max"] = item_max
        bucket["members"] = project_record.add_unique(bucket.get("members", []), collection_nsid)
        if safely_matched:
            bucket["member_match_kinds"] = project_record.add_unique(
                bucket.get("member_match_kinds", []), member_match_kind
            )

    created = 0
    updated = 0
    unmatched = 0
    imported_project_keys: list[str] = []
    unmatched_entries: list[dict[str, Any]] = []
    merge_candidates: list[dict[str, Any]] = []
    suggested_existing_matches = 0
    suggested_entries: list[dict[str, Any]] = []
    working_projects: dict[str, dict[str, Any]] = {}
    cleanup_project_keys: set[str] = set()
    collection_observation_cleanups = 0
    if rebuild_collection_observations:
        for project in existing_projects:
            project_key = str(project.get("project_key") or "")
            existing_observations = [
                dict(entry)
                for entry in project.get("collection_observations", [])
                if isinstance(entry, dict)
            ]
            retained_observations = [
                entry
                for entry in existing_observations
                if not is_collection_inventory_source(str(entry.get("source") or ""))
            ]
            removed_observations = len(existing_observations) - len(retained_observations)
            if removed_observations:
                project["collection_observations"] = retained_observations
                working_projects[project_key] = project
                cleanup_project_keys.add(project_key)
                collection_observation_cleanups += removed_observations

    duplicate_profiles, duplicate_index = project_merge.build_duplicate_candidate_index(existing_projects)
    saved_project_keys: set[str] = set()

    for (_group_kind, _group_project_key, namespace), item in aggregated.items():
        authority_domain = identifier_core.derive_authority_domain_from_namespace(namespace)
        registrable_namespace = registrable_namespace_from_namespace(namespace) or namespace
        service_host_key = domain_policy_core.root_domain(authority_domain)
        match_kind = ""
        project_key = str(item.get("matched_project_key") or "") or None
        if project_key:
            member_match_kinds = sorted(str(value) for value in item.get("member_match_kinds", []) if value)
            match_kind = f"member_{'+'.join(member_match_kinds)}" if member_match_kinds else "member_namespace"
        if not project_key:
            project_key = namespace_lookup.get(namespace)
        if project_key:
            match_kind = match_kind or "exact_namespace"
        if not project_key:
            project_key = find_project_key_by_collection_namespace(existing_projects, namespace)
        if project_key:
            match_kind = match_kind or "exact_namespace_scan"
        if not project_key and registrable_namespace:
            project_key = registrable_namespace_lookup.get(registrable_namespace)
            if project_key:
                match_kind = "registrable_namespace"
        if not project_key and service_host_key:
            project_key = service_host_lookup.get(service_host_key)
            if project_key:
                match_kind = "service_host_family"
        suggested_project_key = None
        suggested_match_kind = ""
        if project_key and match_kind and not sync_match_is_auto_attach_safe(match_kind):
            suggested_project_key = project_key
            suggested_match_kind = match_kind
            suggested_existing_matches += 1
            suggested_entries.append(
                {
                    "seed_type": "nsid",
                    "seed_value": namespace,
                    "representative_nsid": item.get("members", [namespace])[0] if item.get("members") else namespace,
                    "suggested_project_key": suggested_project_key,
                    "suggested_match_kind": suggested_match_kind,
                    "count": int(item.get("count") or 0),
                    "recent_count": int(item.get("recent_count") or 0),
                }
            )
            project_key = None
            match_kind = ""
        is_new = project_key is None
        if is_new and not create_unmatched:
            unmatched += 1
            unmatched_entries.append(
                {
                    "seed_type": "nsid",
                    "seed_value": namespace,
                    "representative_nsid": item.get("members", [namespace])[0] if item.get("members") else namespace,
                    "count": int(item.get("count") or 0),
                    "recent_count": int(item.get("recent_count") or 0),
                    "registrable_namespace": registrable_namespace,
                    "service_host_family": service_host_key,
                    **(
                        {
                            "suggested_project_key": suggested_project_key,
                            "suggested_match_kind": suggested_match_kind,
                        }
                        if suggested_project_key else {}
                    ),
                }
            )
            continue
        if project_key is None:
            project_key = project_record.generate_project_key(existing_keys)
            existing_keys.add(project_key)
        representative_nsid = item.get("members", [namespace])[0] if item.get("members") else namespace
        project = working_projects.get(project_key)
        if project is None:
            project = project_record.fetch_project(conn, project_key) or project_record.default_project_record(
                project_key,
                "nsid",
                representative_nsid,
            )
        project["discovery_sources"] = project_record.add_unique(project.get("discovery_sources", []), "collection_inventory_import")
        upsert_collection_observation(
            project,
            item,
            source="collection_inventory_import",
            merge_existing=merge_existing_observations,
        )
        for member in item.get("members", []):
            add_collection_owned_nsid(
                project,
                str(member),
                source="collection_inventory_import",
                registration_origin=is_new,
            )
        project = finalize_project_shape(project)
        project["duplicate_candidates"] = project_merge.compute_duplicate_candidates(
            conn,
            project,
            projects=existing_projects,
            duplicate_profiles=duplicate_profiles,
            duplicate_index=duplicate_index,
        )
        project["merge_recommendation"] = project_merge.compute_merge_recommendation(project)
        working_projects[project_key] = project
        imported_project_keys.append(project["project_key"])
        if project.get("merge_recommendation"):
            merge_candidates.append(
                {
                    "project_key": project["project_key"],
                    "name": project.get("name", ""),
                    "merge_recommendation": project.get("merge_recommendation", {}),
                }
            )
        if execute:
            project_record.save_project(conn, project, enriched=False)
            saved_project_keys.add(project_key)
        if is_new:
            created += 1
        else:
            updated += 1

    if execute:
        for project_key in sorted(cleanup_project_keys - saved_project_keys):
            project = finalize_project_shape(working_projects[project_key])
            project_record.save_project(
                conn,
                project,
                enriched=False,
                preserve_timestamps=True,
                commit=False,
            )
        conn.commit()

    merge_candidates.sort(
        key=lambda item: (
            -int(item.get("merge_recommendation", {}).get("score", 0) or 0),
            item.get("project_key", ""),
        )
    )
    return {
        "source": source,
        "entries": len(payload),
        "namespaces": len(parent_namespaces),
        "created": created,
        "updated": updated,
        "unmatched": unmatched,
        "suggested_existing_matches": suggested_existing_matches,
        "rejected": len(rejected_entries),
        "collection_observation_rebuild_project_count": len(cleanup_project_keys),
        "collection_observation_cleanup_count": collection_observation_cleanups,
        "mode": "execute" if execute else "dry_run",
        "create_unmatched": create_unmatched,
        "imported_project_keys": imported_project_keys,
        "merge_candidates": merge_candidates[:50],
        "suggested_entries": suggested_entries[:100],
        "unmatched_entries": unmatched_entries[:100],
        "rejected_entries": rejected_entries[:100],
    }


def infer_service_status(status_code: int, text: str) -> str:
    return evaluation_core.infer_service_status(status_code, text)


def classify_service_probe_error(exc: Exception) -> str:
    reason = exc
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
    lowered = str(reason or "").lower()
    if isinstance(reason, socket.gaierror):
        if reason.errno == getattr(socket, "EAI_AGAIN", None):
            return "dns_temporary_failure"
        permanent_dns_errors = {
            value
            for value in (getattr(socket, "EAI_NONAME", None), getattr(socket, "EAI_NODATA", None))
            if value is not None
        }
        if reason.errno in permanent_dns_errors:
            return "dns_failed"
    if "temporary failure in name resolution" in lowered:
        return "dns_temporary_failure"
    if (
        "name or service not known" in lowered
        or "nodename nor servname provided" in lowered
        or "no address associated with hostname" in lowered
    ):
        return "dns_failed"
    if "tls" in lowered or "ssl" in lowered or "certificate" in lowered:
        return "tls_failed"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "connection refused" in lowered:
        return "connection_refused"
    return "network_error"


def service_probe_redirect_metadata(requested_url: str, final_url: str) -> dict[str, Any]:
    requested = identifier_core.normalize_url(requested_url)
    final = identifier_core.normalize_url(final_url)
    if not requested or not final:
        return {
            "requested_url": requested,
            "final_url": final,
            "redirected": False,
            "redirect_same_host": False,
            "redirect_same_site_family": False,
            "redirect_http_to_https": False,
            "redirect_https_to_http": False,
            "redirect_cross_host": False,
        }
    requested_parsed = urllib.parse.urlparse(requested)
    final_parsed = urllib.parse.urlparse(final)
    redirected = requested != final
    same_host = requested_parsed.netloc.lower() == final_parsed.netloc.lower()
    same_site_family = domain_policy_core.is_same_site_family(requested, final)
    return {
        "requested_url": requested,
        "final_url": final,
        "redirected": redirected,
        "redirect_same_host": redirected and same_host,
        "redirect_same_site_family": redirected and same_site_family,
        "redirect_http_to_https": redirected and same_host and requested_parsed.scheme == "http" and final_parsed.scheme == "https",
        "redirect_https_to_http": redirected and same_host and requested_parsed.scheme == "https" and final_parsed.scheme == "http",
        "redirect_cross_host": redirected and not same_host,
    }


def service_probe_canonical_url(requested_url: str, result: dict[str, Any]) -> str:
    requested = identifier_core.normalize_url(requested_url)
    final = identifier_core.normalize_url(str(result.get("final_url") or ""))
    if (result.get("redirect_http_to_https") or result.get("redirect_same_site_family")) and final:
        return final
    return requested


def classify_service_probe_result(
    *,
    status_code: int,
    title: str,
    description: str,
    html: str,
    links: list[dict[str, str]],
    fetch_error: str = "",
) -> tuple[str, str, list[str]]:
    return evaluation_core.classify_service_probe_result(
        status_code=status_code,
        title=title,
        description=description,
        html=html,
        links=links,
        fetch_error=fetch_error,
    )


def probe_service_url(url: str, *, timeout: float = REQUEST_TIMEOUT_DEFAULT) -> dict[str, Any]:
    normalized_url = identifier_core.normalize_url(url)
    cached = _SERVICE_PROBE_CACHE.get(normalized_url)
    if isinstance(cached, dict):
        result = copy.deepcopy(cached)
        result["cache_hit"] = True
        return result
    status_code = 0
    body = b""
    headers: dict[str, str] = {}
    fetch_error = ""
    final_url = normalized_url
    try:
        status_code, body, headers, final_url = http_transport.request_bytes_with_final_url(url, timeout=timeout)
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code or 0)
        body = exc.read()
        headers = {key.lower(): value for key, value in exc.headers.items()} if exc.headers else {}
        final_url = identifier_core.normalize_url(str(exc.geturl() or url))
    except Exception as exc:
        fetch_error = classify_service_probe_error(exc)
    redirect_metadata = service_probe_redirect_metadata(normalized_url, final_url)
    charset = "utf-8"
    content_type = headers.get("content-type", "")
    charset_match = re.search(r"charset=([A-Za-z0-9_-]+)", content_type)
    if charset_match:
        charset = charset_match.group(1)
    html = body.decode(charset, errors="replace")
    title = extract_title(html)
    description = extract_meta_description(html)
    urls = extract_urls(html)
    links = extract_links(html, url)
    service_status, service_status_confidence, service_status_evidence = classify_service_probe_result(
        status_code=status_code,
        title=title,
        description=description,
        html=html,
        links=links,
        fetch_error=fetch_error,
    )
    if redirect_metadata.get("redirected"):
        service_status_evidence = project_record.add_unique(service_status_evidence, "redirect_detected")
        if redirect_metadata.get("redirect_http_to_https"):
            service_status_evidence = project_record.add_unique(service_status_evidence, "redirect_http_to_https")
        elif redirect_metadata.get("redirect_https_to_http"):
            service_status_evidence = project_record.add_unique(service_status_evidence, "redirect_https_to_http")
        elif redirect_metadata.get("redirect_same_host"):
            service_status_evidence = project_record.add_unique(service_status_evidence, "redirect_same_host")
        elif redirect_metadata.get("redirect_same_site_family"):
            service_status_evidence = project_record.add_unique(service_status_evidence, "redirect_same_site_family")
        elif redirect_metadata.get("redirect_cross_host"):
            service_status_evidence = project_record.add_unique(service_status_evidence, "redirect_cross_host")
    result = {
        **redirect_metadata,
        "status_code": status_code,
        "title": title,
        "description": description,
        "urls": urls,
        "links": links,
        "html": html,
        "service_status": service_status,
        "service_status_confidence": service_status_confidence,
        "service_status_evidence": service_status_evidence,
        "fetch_error": fetch_error,
        "cache_hit": False,
    }
    _SERVICE_PROBE_CACHE.set(normalized_url, copy.deepcopy(result), ttl=300)
    return result


AUTHORITY_SERVICE_PAGE_ATPROTO_MARKERS = (
    "atproto",
    "at protocol",
    "xrpc",
    "did:plc:",
    "com.atproto",
    "personal data server",
)


def service_probe_looks_parked(result: dict[str, Any]) -> bool:
    return evaluation_core.service_probe_looks_parked(result)


def authority_service_page_has_atproto_evidence(result: dict[str, Any]) -> bool:
    page_text = "\n".join(
        str(result.get(field) or "")
        for field in ("title", "description", "html")
    ).lower()
    return (
        any(marker in page_text for marker in AUTHORITY_SERVICE_PAGE_ATPROTO_MARKERS)
        or re.search(r"\bpds\b", page_text) is not None
    )


def verified_lexicon_authority_domains(project: dict[str, Any]) -> set[str]:
    return {
        str(item.get("authority_domain") or "").strip().lower().rstrip(".")
        for item in project.get("lexicon_resolution_entries", [])
        if isinstance(item, dict)
        and str(item.get("record_uri") or "").strip()
        and str(item.get("resolved_nsid") or "").strip()
        and str(item.get("authority_domain") or "").strip()
    }


def trusted_anchor_nodes(project: dict[str, Any]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(identifier_type: str, value: str) -> None:
        if project_record.identifier_is_blocked(project, identifier_type, value) or project_record.identifier_is_deferred(project, identifier_type, value):
            return
        kind = "collection" if identifier_type == "nsid" else identifier_type
        node = project_record.trust_node(kind, value)
        key = trust_graph_core.node_key(node)
        if not node or key in seen:
            return
        seen.add(key)
        output.append(node)

    adopted = identifier_storage.active_adoption_claims(project)
    require_adoption = runtime_view.RUNTIME_ADOPTION_ROWS in project
    for item in runtime_view.project_identifiers(project):
        claim = (item.kind, item.value, item.role)
        if item.classification != "confirmed" or item.role not in identifier_storage.COMPONENT_ROLES:
            continue
        if require_adoption and claim not in adopted:
            continue
        if not require_adoption and not identifier_has_active_confirm_decision(project, item.kind, item.value):
            continue
        add(item.kind, item.value)
    return output


COLLECTION_TRUST_NODE_KINDS = {"service_url", "did", "collection", "domain", "handle"}
COLLECTION_TRUST_PREDICATES = {
    "did_publishes_schema",
    "collection_authority_domain",
    "domain_publishes_lexicon_record",
    "service_exact_domain",
    "domain_exact_service",
    "domain_exact_handle",
    "handle_resolves_did",
    "did_document_claims_handle",
}
IDENTITY_TRUST_NODE_KINDS = {"did", "domain", "handle"}
IDENTITY_TRUST_PREDICATES = {
    "domain_exact_handle",
    "handle_exact_domain",
    "handle_resolves_did",
    "did_document_claims_handle",
}


def sync_trust_edges_from_project_identity(project: dict[str, Any]) -> None:
    for account in project.get("developer_accounts", []):
        if not isinstance(account, dict):
            continue
        handle = str(account.get("handle") or "").strip().lower()
        did = str(account.get("did") or "").strip()
        if handle and did:
            project_record.record_trust_edge(
                project,
                "handle",
                handle,
                "handle_resolves_did",
                "did",
                did,
                source=str(account.get("source") or "developer_account"),
                method="developer_account",
                classification="confirmed",
                eligible_for_auto_confirm=True,
                observed_at=str(account.get("observed_at") or ""),
            )
        if handle and "." in handle and not project_record.is_shared_official_identity_host(handle):
            project_record.record_trust_edge(
                project,
                "domain",
                handle,
                "domain_exact_handle",
                "handle",
                handle,
                source=str(account.get("source") or "developer_account"),
                method="exact_handle",
                classification="confirmed",
                eligible_for_auto_confirm=True,
                observed_at=str(account.get("observed_at") or ""),
            )
            project_record.record_trust_edge(
                project,
                "handle",
                handle,
                "handle_exact_domain",
                "domain",
                handle,
                source=str(account.get("source") or "developer_account"),
                method="exact_handle",
                classification="confirmed",
                eligible_for_auto_confirm=True,
                observed_at=str(account.get("observed_at") or ""),
            )
    for entry in project_record.normalize_lexicon_resolution_entries(project.get("lexicon_resolution_entries", [])):
        resolved_nsid = identifier_core.normalize_nsid(str(entry.get("resolved_nsid") or ""))
        repo_did = str(entry.get("repo_did") or "").strip()
        if repo_did and resolved_nsid:
            project_record.record_trust_edge(
                project,
                "did",
                repo_did,
                "did_publishes_schema",
                "collection",
                resolved_nsid,
                source=f"lexicon_resolution:{resolved_nsid}",
                locator=str(entry.get("record_uri") or ""),
                method="repo_record",
                classification="confirmed",
                eligible_for_auto_confirm=True,
                observed_at=str(entry.get("resolved_at") or ""),
            )
        authority_domain = str(entry.get("authority_domain") or identifier_core.derive_authority_domain(resolved_nsid)).strip().lower().rstrip(".")
        if resolved_nsid and authority_domain:
            project_record.record_trust_edge(
                project,
                "collection",
                resolved_nsid,
                "collection_authority_domain",
                "domain",
                authority_domain,
                source=f"lexicon_resolution:{resolved_nsid}",
                locator=str(entry.get("record_uri") or ""),
                method="lexicon_record_authority",
                classification="confirmed",
                eligible_for_auto_confirm=True,
                observed_at=str(entry.get("resolved_at") or ""),
            )
            project_record.record_trust_edge(
                project,
                "domain",
                authority_domain,
                "domain_publishes_lexicon_record",
                "collection",
                resolved_nsid,
                source=f"lexicon_resolution:{resolved_nsid}",
                locator=str(entry.get("record_uri") or ""),
                method="lexicon_dns_and_repo_record",
                classification="confirmed",
                eligible_for_auto_confirm=True,
                observed_at=str(entry.get("resolved_at") or ""),
            )


def exact_root_service_domain(service_url: str) -> str:
    normalized = identifier_core.normalize_url(service_url)
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or parsed.path not in {"", "/"}:
        return ""
    return str(parsed.hostname or "").strip().lower().rstrip(".")


def sync_trust_edges_from_collection_service_anchors(project: dict[str, Any]) -> None:
    authority_domains = {
        str(entry.get("authority_domain") or "").strip().lower().rstrip(".")
        for entry in project_record.normalize_lexicon_resolution_entries(project.get("lexicon_resolution_entries", []))
        if str(entry.get("authority_domain") or "").strip()
    }
    if not authority_domains:
        return
    for anchor in trusted_anchor_nodes(project):
        if str(anchor.get("kind") or "") != "service_url":
            continue
        service_url = identifier_core.normalize_url(str(anchor.get("value") or ""))
        domain = exact_root_service_domain(service_url)
        if not service_url or domain not in authority_domains:
            continue
        source = f"trusted_service_domain:{service_url}"
        project_record.record_trust_edge(
            project,
            "service_url",
            service_url,
            "service_exact_domain",
            "domain",
            domain,
            source=source,
            locator=service_url,
            method="exact_root_service_domain",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        project_record.record_trust_edge(
            project,
            "domain",
            domain,
            "domain_exact_service",
            "service_url",
            service_url,
            source=source,
            locator=service_url,
            method="exact_root_service_domain",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )


def collection_trusted_anchor_nodes(project: dict[str, Any]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for did in sorted(confirmed_developer_dids(project) | ({str(project.get("brand_did") or "").strip()} if str(project.get("brand_did") or "").strip() else set())):
        node = project_record.trust_node("did", did)
        key = trust_graph_core.node_key(node)
        if node and key not in seen:
            seen.add(key)
            output.append(node)
    for anchor in trusted_anchor_nodes(project):
        if str(anchor.get("kind") or "") != "service_url":
            continue
        key = trust_graph_core.node_key(anchor)
        if key not in seen:
            seen.add(key)
            output.append(anchor)
    return output


def identity_trusted_anchor_nodes(project: dict[str, Any]) -> list[dict[str, str]]:
    return [
        node
        for node in trusted_anchor_nodes(project)
        if str(node.get("kind") or "") in IDENTITY_TRUST_NODE_KINDS
    ]


def trust_collection_ownership_evaluation(project: dict[str, Any], nsid: str) -> dict[str, Any]:
    normalized = identifier_core.normalize_nsid(nsid)
    candidate = project_record.trust_node("collection", normalized)
    if not candidate:
        return {"auto_confirm": False, "reason": "invalid_candidate", "candidate": {"kind": "collection", "value": nsid}}
    if relation_core.identifier_is_guarded(project, "nsid", normalized):
        return {"auto_confirm": False, "reason": "guarded_identifier", "candidate": candidate}
    if project_record.manual_nsid_seed_supports(project, normalized):
        return {
            "auto_confirm": True,
            "reason": "manual_collection_seed",
            "candidate": candidate,
            "policy_version": trust_graph_core.TRUST_POLICY_VERSION,
        }
    sync_trust_edges_from_project_identity(project)
    sync_trust_edges_from_collection_service_anchors(project)
    evaluation = trust_graph_core.evaluate_auto_confirm(
        project,
        candidate=candidate,
        anchors=collection_trusted_anchor_nodes(project),
        allowed_node_kinds=COLLECTION_TRUST_NODE_KINDS,
        allowed_predicates=COLLECTION_TRUST_PREDICATES,
    )
    if not evaluation.get("auto_confirm"):
        publication_evaluation = collection_trust.evaluate_publisher_support(project, normalized)
        if publication_evaluation.get("auto_confirm"):
            evaluation = publication_evaluation
    trust_graph_core.upsert_evaluation(project, evaluation, generated_at=project_record.iso_now())
    return evaluation


def trust_auto_confirm_identifier(project: dict[str, Any], identifier_type: str, value: str) -> dict[str, Any]:
    kind = "collection" if identifier_type == "nsid" else identifier_type
    candidate = project_record.trust_node(kind, value)
    if not candidate:
        return {"auto_confirm": False, "reason": "invalid_candidate", "candidate": {"kind": kind, "value": value}}
    if project_record.identifier_is_blocked(project, identifier_type, value) or project_record.identifier_is_deferred(project, identifier_type, value):
        evaluation = {"auto_confirm": False, "reason": "guarded_identifier", "candidate": candidate}
        trust_graph_core.upsert_evaluation(project, evaluation, generated_at=project_record.iso_now())
        return evaluation
    if identifier_type == "nsid":
        return trust_collection_ownership_evaluation(project, value)
    if identifier_type in IDENTITY_TRUST_NODE_KINDS:
        sync_trust_edges_from_project_identity(project)
        evaluation = trust_graph_core.evaluate_auto_confirm(
            project,
            candidate=candidate,
            anchors=identity_trusted_anchor_nodes(project),
            allowed_node_kinds=IDENTITY_TRUST_NODE_KINDS,
            allowed_predicates=IDENTITY_TRUST_PREDICATES,
        )
        trust_graph_core.upsert_evaluation(project, evaluation, generated_at=project_record.iso_now())
        return evaluation
    evaluation = trust_graph_core.evaluate_auto_confirm(
        project,
        candidate=candidate,
        anchors=trusted_anchor_nodes(project),
    )
    trust_graph_core.upsert_evaluation(project, evaluation, generated_at=project_record.iso_now())
    return evaluation


def confirm_service_url_with_trust(project: dict[str, Any], url: str) -> bool:
    normalized = project_record.normalize_confirmed_service_url(url) or ""
    if not normalized:
        return False
    evaluation = trust_auto_confirm_identifier(project, "service_url", normalized)
    if not evaluation.get("auto_confirm"):
        return False
    confirm_service_url(project, normalized)
    add_confirmed_identifier_decision(
        project,
        "service_url",
        normalized,
        source="trust_policy",
        note="service accepted by the configured trust policy",
        role="service",
        source_ref=project_record.dump_json(evaluation),
        policy_version=trust_graph_core.TRUST_POLICY_VERSION,
    )
    return True


def confirm_project_repository_url_with_trust(
    project: dict[str, Any],
    url: str,
    source: str,
    relation: str = "source_repo",
    *,
    reason: str = "",
    evidence_kind: str = "",
    evidence_url: str = "",
    updated_at: str = "",
) -> bool:
    normalized = repo_url_core.canonical_repo_url(url)
    if not normalized:
        return False
    evaluation = trust_auto_confirm_identifier(project, "repo_url", normalized)
    if not evaluation.get("auto_confirm"):
        return False
    confirm_project_repository_url(
        project,
        normalized,
        source,
        relation,
        reason=reason,
        evidence_kind=evidence_kind,
        evidence_url=evidence_url,
        updated_at=updated_at,
    )
    add_confirmed_identifier_decision(
        project,
        "repo_url",
        normalized,
        source="trust_policy",
        note="repository accepted by the configured trust policy",
        role="project_repository",
        source_ref=project_record.dump_json(evaluation),
        policy_version=trust_graph_core.TRUST_POLICY_VERSION,
    )
    return True


def prune_confirmed_http_redirect_candidates(project: dict[str, Any]) -> list[str]:
    confirmed_services = {
        identifier_core.normalize_url(str(value or ""))
        for value in (
            list(project.get("service_urls", []))
        )
        if identifier_core.normalize_url(str(value or ""))
    }
    obsolete: set[str] = set()
    for item in project.get("service_verification_results", []):
        if not isinstance(item, dict):
            continue
        requested = identifier_core.normalize_url(str(item.get("requested_url") or ""))
        final = identifier_core.normalize_url(str(item.get("final_url") or ""))
        try:
            status_code = int(item.get("status_code") or 0)
        except (TypeError, ValueError):
            continue
        if (
            requested
            and final in confirmed_services
            and requested != final
            and 0 < status_code < 400
            and bool(item.get("redirected"))
            and bool(item.get("redirect_same_host"))
            and bool(item.get("redirect_http_to_https"))
        ):
            obsolete.add(requested)
    if not obsolete:
        return []
    project_record.set_service_url_candidate_values(
        project,
        [
            value
            for value in project_record.service_url_candidate_values(project)
            if identifier_core.normalize_url(str(value or "")) not in obsolete
        ],
    )
    project["failed_service_urls"] = [
        value
        for value in project.get("failed_service_urls", [])
        if identifier_core.normalize_url(str(value or "")) not in obsolete
    ]
    prune_stale_service_trust_evaluations(project)
    return sorted(obsolete)


def reconcile_trust_supported_url_candidates(project: dict[str, Any]) -> dict[str, list[str]]:
    removed_redirects = prune_confirmed_http_redirect_candidates(project)
    confirmed_services: list[str] = []
    for service_url in list(project_record.service_url_candidate_values(project)):
        if confirm_service_url_with_trust(project, service_url):
            confirmed_services.append(identifier_core.normalize_url(service_url))

    confirmed_repos: list[str] = []
    for repo_url in list(project.get("candidate_project_repository_urls", [])):
        normalized = repo_url_core.canonical_repo_url(str(repo_url or ""))
        if not normalized:
            continue
        if confirm_project_repository_url_with_trust(
            project,
            normalized,
            "trust_graph_finalization",
            relation="mutual_trust_path",
            reason="mutual trust path became complete after all enrichment evidence was applied",
            evidence_kind="trust_graph_evaluation",
            evidence_url=normalized,
        ):
            confirmed_repos.append(normalized)
    return {
        "confirmed_service_urls": project_record.unique_list(confirmed_services),
        "confirmed_repo_urls": project_record.unique_list(confirmed_repos),
        "removed_redirect_service_urls": removed_redirects,
    }


def confirmed_service_identity_urls(project: dict[str, Any]) -> list[str]:
    return project_record.unique_list([
        normalized
        for value in (
            list(project.get("service_urls", []))
            + project_record.manual_seed_values(project, "service_url")
        )
        if (normalized := identifier_core.normalize_url(str(value or "")))
    ])


def service_reference_matches_project(project: dict[str, Any], value: str) -> bool:
    normalized = identifier_core.normalize_url(value)
    if not normalized:
        return False
    return any(
        domain_policy_core.is_same_site_family(normalized, service_url)
        for service_url in confirmed_service_identity_urls(project)
    )


def candidate_repo_trust_probe_urls(project: dict[str, Any]) -> list[str]:
    candidates = {
        repo_url_core.canonical_repo_url(str(value or ""))
        for value in project.get("candidate_project_repository_urls", [])
        if repo_url_core.canonical_repo_url(str(value or ""))
    }
    output: list[str] = []
    for edge in trust_graph_core.graph(project).get("edges", []):
        if (
            edge.get("predicate") != "service_links_repo"
            or edge.get("classification") != "confirmed"
            or not edge.get("eligible_for_auto_confirm")
        ):
            continue
        subject = edge.get("subject") if isinstance(edge.get("subject"), dict) else {}
        obj = edge.get("object") if isinstance(edge.get("object"), dict) else {}
        repo_url = repo_url_core.canonical_repo_url(str(obj.get("value") or ""))
        if (
            subject.get("kind") == "service_url"
            and obj.get("kind") == "repo_url"
            and repo_url in candidates
            and service_reference_matches_project(project, str(subject.get("value") or ""))
            and repo_candidate_allowed_for_project(project, repo_url)
        ):
            output.append(repo_url)
    return project_record.unique_list(output)


def apply_candidate_repo_trust_evidence(
    project: dict[str, Any],
    repo_url: str,
    bundle: EvidenceBundle,
) -> int:
    normalized_repo = repo_url_core.canonical_repo_url(repo_url)
    if not normalized_repo or str(project.get("project_key") or "") != bundle.project_key:
        return 0
    accepted = 0
    for record in bundle.records:
        if record.kind != "relation" or not isinstance(record.value, dict):
            continue
        relation = dict(record.value)
        subject = relation.get("subject") if isinstance(relation.get("subject"), dict) else {}
        obj = relation.get("object") if isinstance(relation.get("object"), dict) else {}
        predicate = str(relation.get("predicate") or "")
        object_value = identifier_core.normalize_url(str(obj.get("value") or ""))
        homepage_matches_project_service = (
            predicate == "repository_homepage"
            and object_value in set(confirmed_service_identity_urls(project))
        )
        if (
            predicate not in {"repository_homepage", "project_repository"}
            or subject.get("type") != "repo"
            or repo_url_core.canonical_repo_url(str(subject.get("value") or "")) != normalized_repo
            or obj.get("type") not in {"website", "service"}
            or (
                not homepage_matches_project_service
                and not (
                    predicate == "project_repository"
                    and service_reference_matches_project(project, str(obj.get("value") or ""))
                )
            )
        ):
            continue
        normalized_service = object_value
        relation["subject"] = {"type": "repo", "value": normalized_repo}
        relation["object"] = {"type": "website", "value": normalized_service}
        project["relation_evidence"] = relation_core.normalize_relation_records(
            list(project.get("relation_evidence", []))
            + [{
                "relation": relation,
                "source": record.source,
                "classification": "confirmed",
                "observed_at": record.observed_at,
                "context": {**dict(record.context), "candidate_repo_trust_inspection": True},
            }]
        )
        if predicate == "repository_homepage":
            accepted += 1
            continue
        accepted += 1
    return accepted


def accepted_repo_schema_relation(project: dict[str, Any], relation: dict[str, Any]) -> bool:
    if relation.get("predicate") != "defines_schema":
        return True
    subject = relation.get("subject") or {}
    obj = relation.get("object") or {}
    if subject.get("type") != "repo" or obj.get("type") != "nsid":
        return True
    repo_url = repo_url_core.canonical_repo_url(str(subject.get("value") or ""))
    nsid = identifier_core.normalize_nsid(str(obj.get("value") or ""))
    if not repo_url or not nsid:
        return False
    accepted_repos = set(project_record.manual_repo_urls(project)) | {
        repo_url_core.canonical_repo_url(str(item or ""))
        for item in list(project.get("project_repository_urls", [])) + [str(project.get("project_repository_url") or "")]
        if repo_url_core.canonical_repo_url(str(item or ""))
    }
    return repo_url in accepted_repos and project_owned_lexicon_nsid(project, nsid)


def normalize_schema_definition_relations(project: dict[str, Any]) -> None:
    normalized: list[dict[str, Any]] = []
    for item in relation_core.normalize_relation_records(project.get("relation_evidence", [])):
        relation = dict(item["relation"])
        obj = relation.get("object") or {}
        if obj.get("type") == "nsid" and not identifier_core.is_valid_nsid(str(obj.get("value") or "")):
            continue
        if not accepted_repo_schema_relation(project, relation):
            relation["predicate"] = "mentions_collection"
            item = {
                **item,
                "relation": relation,
                "classification": "candidate",
                "context": {
                    **dict(item.get("context") or {}),
                    "downgraded_from": "defines_schema",
                    "downgrade_reason": "repo_and_nsid_not_both_accepted",
                },
            }
        elif (
            relation.get("predicate") == "defines_schema"
            and (relation.get("subject") or {}).get("type") == "repo"
            and (relation.get("object") or {}).get("type") == "nsid"
        ):
            item = {**item, "classification": "confirmed"}
        normalized.append(item)
    project["relation_evidence"] = relation_core.normalize_relation_records(normalized)


def service_probe_supports_project(project: dict[str, Any], service_url: str, result: dict[str, Any]) -> bool:
    host = urllib.parse.urlparse(identifier_core.normalize_url(service_url)).netloc.lower()
    if not host or service_probe_looks_parked(result) or result.get("fetch_error") or int(result.get("status_code") or 200) >= 400:
        return False
    normalized_service_url = identifier_core.normalize_url(service_url)
    trust_graph_core.remove_edges(
        project,
        predicate="service_links_repo",
        source=f"service_probe:{normalized_service_url}",
        locator=normalized_service_url,
        methods={"html_link"},
    )
    relevant_repos = {
        repo_url_core.canonical_repo_url(str(value or ""))
        for value in list(project_record.project_repository_url_values(project))
        + list(project.get("candidate_project_repository_urls", []))
        if repo_url_core.canonical_repo_url(str(value or ""))
    }
    for link in result.get("links", []):
        if not isinstance(link, dict):
            continue
        url = identifier_core.normalize_url(str(link.get("url") or ""))
        if not url:
            continue
        repo_url = repo_url_core.canonical_repo_url(url)
        if (
            repo_url_core.is_supported_repo_url(url)
            and repo_url
            and (repo_url in relevant_repos or repo_link_matches_project_identity(project, repo_url))
        ):
            label = link_label(link)
            explicit_source_link = classify_official_link(service_url, repo_url, label) == "source_repo"
            project_record.record_trust_edge(
                project,
                "service_url",
                service_url,
                "service_links_repo",
                "repo_url",
                repo_url,
                source=f"service_probe:{identifier_core.normalize_url(service_url)}",
                locator=identifier_core.normalize_url(service_url),
                method="html_link",
                classification="confirmed" if explicit_source_link else "candidate",
                eligible_for_auto_confirm=explicit_source_link,
                observed_at=project_record.iso_now(),
                context={
                    "link_text": str(link.get("text") or "")[:200],
                    "aria_label": str(link.get("aria_label") or "")[:200],
                    "link_title": str(link.get("title") or "")[:200],
                    "page_title": str(result.get("title") or "")[:300],
                    "page_description": str(result.get("description") or "")[:1000],
                },
            )
        if trust_auto_confirm_identifier(project, "service_url", service_url).get("auto_confirm"):
            return True
    if host in project_record.manual_service_hosts(project):
        return True
    return False


def verified_authority_live_surface(project: dict[str, Any], service_url: str, result: dict[str, Any]) -> bool:
    host = urllib.parse.urlparse(identifier_core.normalize_url(service_url)).netloc.lower()
    status_code = int(result.get("status_code") or 0)
    if not host or not (0 < status_code < 400):
        return False
    if host not in verified_lexicon_authority_domains(project):
        return False
    return bool(str(result.get("title") or "").strip() or str(result.get("description") or "").strip())


def authority_service_probe_is_parent_fallback(project: dict[str, Any], service_url: str, result: dict[str, Any]) -> bool:
    normalized = identifier_core.normalize_url(service_url)
    fallback_from = identifier_core.normalize_url(str(result.get("authority_fallback_from") or ""))
    fallback_status_code = int(result.get("authority_fallback_status_code") or 0)
    if not normalized or not fallback_from:
        return False
    fallback_result = {
        "status_code": fallback_status_code,
        "fetch_error": str(result.get("authority_fallback_fetch_error") or ""),
    }
    if not authority_probe_parent_fallback_reason(fallback_result):
        return False
    for authority_domain in project.get("authority_domains", []):
        authority = str(authority_domain or "").strip().lower().rstrip(".")
        registrable = domain_policy_core.root_domain(authority)
        if not authority or not registrable or registrable == authority:
            continue
        if (
            fallback_from == identifier_core.normalize_url(exact_service_origin_for_authority_domain(authority))
            and normalized == identifier_core.normalize_url(exact_service_origin_for_authority_domain(registrable))
        ):
            return True
    return False


def authority_service_probe_candidate_allowed(project: dict[str, Any], service_url: str, result: dict[str, Any]) -> bool:
    normalized = project_record.normalize_service_candidate_url(service_url)
    if not normalized or service_url_is_guarded(project, normalized):
        return False
    redirect_final = project_record.normalize_service_candidate_url(str(result.get("final_url") or ""))
    if result.get("redirect_same_site_family") and redirect_final and redirect_final != normalized:
        return False
    status_code = int(result.get("status_code") or 0)
    if not (0 < status_code < 400):
        return False
    if service_probe_looks_parked(result):
        return False
    if not service_probe_represents_service_identity(normalized, {**result, "lane": str(result.get("lane") or "authority")}):
        return False
    parsed = urllib.parse.urlparse(normalized)
    if normalized != identifier_core.normalize_url(exact_service_origin_for_authority_domain(parsed.netloc)):
        return False
    authority_domains = {str(item or "").strip().lower().rstrip(".") for item in project.get("authority_domains", []) if str(item or "").strip()}
    is_authority_host = parsed.netloc.lower() in authority_domains
    is_parent_fallback = authority_service_probe_is_parent_fallback(project, normalized, result)
    if not is_authority_host and not is_parent_fallback:
        return False
    has_record_url = authority_service_candidate_has_record_url(project, normalized)
    has_page_atproto_evidence = is_authority_host and authority_service_page_has_atproto_evidence(result)
    if not is_parent_fallback and not has_record_url and not has_page_atproto_evidence:
        return False
    observed_status = project_record.normalize_service_status(result.get("service_status"))
    if observed_status in {"dead", "unreleased", "unknown"}:
        return False
    return True


def apply_authority_service_probe_result(project: dict[str, Any], service_url: str, result: dict[str, Any]) -> None:
    normalized = project_record.normalize_service_candidate_url(service_url)
    if not normalized:
        return
    if authority_service_probe_candidate_allowed(project, normalized, result):
        add_candidate_url(project, "service_url", normalized)
        project["evidence_urls"] = project_record.add_unique(project.get("evidence_urls", []), normalized)


def sync_authority_service_candidates_from_probe_results(project: dict[str, Any]) -> None:
    for item in project.get("service_verification_results", []):
        if not isinstance(item, dict):
            continue
        requested_url = identifier_core.normalize_url(str(item.get("requested_url") or ""))
        if not requested_url:
            continue
        result = {**item, "html": str(item.get("html_extract") or item.get("html") or ""), "lane": "authority"}
        apply_authority_service_probe_result(project, requested_url, result)


def service_probe_supported_redirect_final_url(project: dict[str, Any], requested_url: str, result: dict[str, Any]) -> str:
    requested = identifier_core.normalize_url(requested_url)
    final = project_record.normalize_service_candidate_url(str(result.get("final_url") or ""))
    status_code = int(result.get("status_code") or 0)
    if not (0 < status_code < 400):
        return ""
    if not result.get("redirected") or not requested or not final or requested == final:
        return ""
    if result.get("redirect_same_host") and result.get("redirect_http_to_https"):
        return final
    if result.get("redirect_same_site_family") and service_probe_supports_project(project, requested, result):
        return final
    if not service_probe_supports_project(project, final, result):
        return ""
    return final


def apply_service_probe_redirect_final_url(project: dict[str, Any], requested_url: str, result: dict[str, Any]) -> None:
    final_url = service_probe_supported_redirect_final_url(project, requested_url, result)
    if not final_url:
        return
    normalized_requested = identifier_core.normalize_url(requested_url)
    project["evidence_urls"] = project_record.add_unique(project.get("evidence_urls", []), final_url)
    confirmed_services = {
        identifier_core.normalize_url(str(value or ""))
        for value in project.get("service_urls", [])
    }
    if result.get("redirect_same_site_family") and normalized_requested in confirmed_services:
        project_record.record_trust_edge(
            project,
            "service_url",
            normalized_requested,
            "service_canonical",
            "service_url",
            final_url,
            source=f"service_redirect:{normalized_requested}",
            locator=normalized_requested,
            method="same_site_redirect",
            classification="confirmed",
            eligible_for_auto_confirm=True,
            observed_at=project_record.iso_now(),
        )
        project_record.record_trust_edge(
            project,
            "service_url",
            final_url,
            "service_canonical",
            "service_url",
            normalized_requested,
            source=f"service_redirect:{normalized_requested}",
            locator=normalized_requested,
            method="same_site_redirect",
            classification="confirmed",
            eligible_for_auto_confirm=True,
            observed_at=project_record.iso_now(),
        )
        if not confirm_service_url_with_trust(project, final_url):
            add_candidate_url(project, "service_url", final_url)
            return
        if identifier_has_active_confirm_decision(project, "service_url", normalized_requested):
            clear_confirmed_identifier_decision(project, "service_url", normalized_requested)
            add_confirmed_identifier_decision(
                project,
                "service_url",
                final_url,
                source="canonical_redirect",
                note=f"canonical redirect from {normalized_requested}",
            )
        project["service_urls"] = [
            value
            for value in project.get("service_urls", [])
            if identifier_core.normalize_url(str(value or "")) != normalized_requested
        ]
        project_record.set_service_url_candidate_values(
            project,
            [
                value
                for value in project_record.service_url_candidate_values(project)
                if identifier_core.normalize_url(str(value or "")) != normalized_requested
            ],
        )
        return
    add_candidate_url(project, "service_url", final_url)


def service_observation_source_url(source: str, prefixes: set[str]) -> str:
    prefix, separator, raw_url = str(source or "").partition(":")
    if not separator or prefix not in prefixes:
        return ""
    return identifier_core.normalize_url(raw_url)


def remove_unsupported_service_surface_evidence(project: dict[str, Any], service_url: str) -> None:
    normalized_service_url = identifier_core.normalize_url(service_url)
    service_path = urllib.parse.urlparse(normalized_service_url).path.rstrip("/")
    remove_same_site_follow_ups = not service_path

    def is_same_surface_source(source: str, prefixes: set[str], *, include_same_site: bool = False) -> bool:
        source_url = service_observation_source_url(source, prefixes)
        if not source_url:
            return False
        if source_url == normalized_service_url:
            return True
        return include_same_site and domain_policy_core.is_same_site_family(normalized_service_url, source_url)

    project["resource_url_records"] = project_record.normalize_resource_url_records([
        record
        for record in project_record.normalize_resource_url_records(list(project.get("resource_url_records", [])))
        if not is_same_surface_source(
            str(record.get("source") or ""),
            {"service_page", "service_follow_up"},
            include_same_site=remove_same_site_follow_ups,
        )
    ])
    removed_account_handles: set[str] = set()
    for field_name in ("developer_accounts", "candidate_developer_accounts"):
        accounts = project_record.normalize_developer_accounts(list(project.get(field_name, [])))
        kept_accounts = [
            account
            for account in accounts
            if not is_same_surface_source(
                str(account.get("source") or ""),
                {"service_raw_url", "service_page", "service_follow_up"},
            )
        ]
        removed_account_handles.update(
            str(account.get("handle") or "").strip().lower()
            for account in accounts
            if account not in kept_accounts and str(account.get("handle") or "").strip()
        )
        project[field_name] = project_record.normalize_developer_accounts(kept_accounts)
    surviving_account_handles = {
        str(account.get("handle") or "").strip().lower()
        for field_name in ("developer_accounts", "candidate_developer_accounts")
        for account in project.get(field_name, [])
        if str(account.get("handle") or "").strip()
    }
    project["bluesky_handles"] = [
        handle
        for handle in project_record.unique_list(project.get("bluesky_handles", []))
        if str(handle or "").strip().lower() not in removed_account_handles
        or str(handle or "").strip().lower() in surviving_account_handles
    ]
    for field_name in ("name_candidates", "summary_candidates", "tagline_candidates"):
        project[field_name] = [
            item
            for item in project.get(field_name, [])
            if isinstance(item, dict)
            and not is_same_surface_source(
                str(item.get("source") or ""),
                {"service_title", "service_meta"},
            )
        ]
    project_record.sync_resource_url_records(project)


def remove_related_repository_service_surfaces(project: dict[str, Any]) -> None:
    llm_output = project.get("llm_output") if isinstance(project.get("llm_output"), dict) else {}
    for item in llm_output.get("repository_link_assessments", []):
        if not isinstance(item, dict) or str(item.get("relationship") or "") != "related":
            continue
        service_url = identifier_core.normalize_url(str(item.get("service_url") or ""))
        if service_url:
            remove_unsupported_service_surface_evidence(project, service_url)


def collect_first_party_links(
    project: dict[str, Any],
    base_url: str,
    links: list[dict[str, str]],
    source: str,
    *,
    collect_surfaces: bool = True,
) -> list[str]:
    follow_up_urls: list[str] = []
    trust_graph_core.remove_edges(
        project,
        predicate="service_links_repo",
        source=source,
        locator=identifier_core.normalize_url(base_url),
        methods={"first_party_link", "first_party_repo_identity_match", "same_site_link"},
    )
    for link in links:
        url = identifier_core.normalize_url(link.get("url", ""))
        text = link_label(link)
        if not url:
            continue
        if not collect_surfaces and not repo_url_core.is_supported_repo_url(url):
            continue
        # Repository attribution is collected from bounded page context in
        # service.collect_service_probe_evidence. Do not run the legacy label/name
        # heuristics in parallel with that relation policy.
        if repo_url_core.is_supported_repo_url(url):
            continue
        classification = classify_official_link(base_url, url, text)
        if classification == "source_like":
            if domain_policy_core.is_same_site_family(base_url, url):
                follow_up_urls.append(url)
        elif classification in {"docs", "blog"}:
            add_resource_url(
                project,
                url,
                role=classification,
                source=source,
                confidence="confirmed" if domain_policy_core.is_same_site_family(base_url, url) else "candidate",
            )
            if domain_policy_core.is_same_site_family(base_url, url):
                follow_up_urls.append(url)
        elif classification == "bluesky_profile":
            project_record.record_developer_profile_url(project, url, source)
        elif classification == "same_site":
            resource_role = project_record.app_link_role_from_resource_url(url)
            if resource_role:
                add_resource_url(
                    project,
                    url,
                    role=resource_role,
                    source=source,
                    confidence="confirmed",
                )
    return project_record.unique_list(follow_up_urls)


def search_github_repositories(
    query: str,
    limit: int = 5,
    *,
    timeout: float = REQUEST_TIMEOUT_DISCOVERY_SEARCH,
    request_metrics: github_api.GitHubRequestMetrics | None = None,
) -> list[dict[str, Any]]:
    cache_key = (str(query or "").strip(), int(limit))
    cached = _GITHUB_REPO_SEARCH_CACHE.get(repr(cache_key))
    if cached is not None:
        if request_metrics is not None:
            request_metrics.record_cache_hit()
        return [dict(item) for item in cached]
    headers = github_headers()
    params = urllib.parse.urlencode({"q": query, "per_page": max(1, min(limit, 10)), "sort": "updated", "order": "desc"})
    payload = github_api.request_json(
        f"{GITHUB_API}/search/repositories?{params}",
        headers=headers,
        timeout=timeout,
        metrics=request_metrics,
    )
    results: list[dict[str, Any]] = []
    for item in payload.get("items", [])[:limit]:
        html_url = str(item.get("html_url") or "")
        normalized_repo = repo_url_core.normalize_github_repo_url(html_url)
        if not normalized_repo:
            continue
        results.append(
            {
                "url": normalized_repo,
                "full_name": str(item.get("full_name") or ""),
                "description": str(item.get("description") or ""),
                "homepage": str(item.get("homepage") or ""),
                "updated_at": str(item.get("updated_at") or ""),
                "stargazers_count": int(item.get("stargazers_count") or 0),
            }
        )
    _GITHUB_REPO_SEARCH_CACHE.set(repr(cache_key), [dict(item) for item in results], ttl=300)
    return results


def search_bsky_actors(query: str, limit: int = 5, *, timeout: float = REQUEST_TIMEOUT_DISCOVERY_SEARCH) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"q": query, "limit": max(1, min(limit, 10))})
    payload = http_transport.request_json(f"{BSKY_PUBLIC_API}/xrpc/app.bsky.actor.searchActors?{params}", timeout=timeout)
    results: list[dict[str, Any]] = []
    for item in payload.get("actors", [])[:limit]:
        results.append(
            {
                "handle": str(item.get("handle") or ""),
                "did": str(item.get("did") or ""),
                "display_name": str(item.get("displayName") or ""),
                "description": str(item.get("description") or ""),
            }
        )
    return results


def repo_context_service_candidate_allowed(project: dict[str, Any], url: str, *, repo_url: str, lexicon_ids: list[str]) -> bool:
    normalized = project_record.normalize_service_candidate_url(url)
    if not normalized:
        return False
    if service_candidate_matches_project(project, normalized):
        return True
    host = urllib.parse.urlparse(normalized).netloc.lower()
    if not host:
        return False
    tokens = set(project_record.project_merge_aliases(project))
    slug = repo_url_core.extract_github_slug(repo_url)
    if slug:
        tokens.add(identifier_core.normalize_alias_token(slug.split("/", 1)[-1]))
    for nsid in lexicon_ids:
        authority_domain = identifier_core.derive_authority_domain(nsid)
        if authority_domain:
            tokens.add(identifier_core.normalize_alias_token(authority_domain.split(".", 1)[0]))
    normalized_host = host.replace("-", "").replace("_", "")
    return any(token and token in normalized_host for token in tokens)


def search_bsky_posts(query: str, limit: int = 5, *, timeout: float = REQUEST_TIMEOUT_DISCOVERY_SEARCH) -> list[dict[str, Any]]:
    payload = search_bsky_posts_payload(query, limit=limit, timeout=timeout)
    results: list[dict[str, Any]] = []
    for item in payload.get("posts", [])[:limit]:
        author = item.get("author", {}) if isinstance(item.get("author"), dict) else {}
        record = item.get("record", {}) if isinstance(item.get("record"), dict) else {}
        text = str(record.get("text") or "")
        results.append(
            {
                "uri": str(item.get("uri") or ""),
                "text": text,
                "urls": extract_urls(text),
                "author_handle": str(author.get("handle") or ""),
                "author_did": str(author.get("did") or ""),
            }
        )
    return results


def search_bsky_posts_payload(
    query: str,
    *,
    limit: int = 25,
    cursor: str = "",
    since: str = "",
    timeout: float = REQUEST_TIMEOUT_DISCOVERY_SEARCH,
) -> dict[str, Any]:
    params_dict: dict[str, Any] = {"q": query, "limit": max(1, min(limit, 100)), "sort": "latest"}
    if cursor:
        params_dict["cursor"] = cursor
    if since:
        params_dict["since"] = since
    params = urllib.parse.urlencode(params_dict)
    url = f"{bsky_authenticated_app_url('app.bsky.feed.searchPosts')}?{params}"
    headers = {"atproto-proxy": BSKY_APPVIEW_PROXY}
    try:
        payload = http_transport.request_json_authenticated(url, bsky_access_token(), headers=headers, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if exc.code == 401 or (exc.code == 400 and ("InvalidToken" in body or "ExpiredToken" in body)):
            refreshed = bsky_refresh_session()
            payload = http_transport.request_json_authenticated(url, str(refreshed.get("accessJwt") or ""), headers=headers, timeout=timeout)
        else:
            snippet = body[:300].strip()
            raise RuntimeError(f"Bluesky post search failed HTTP {exc.code}: {snippet or exc.reason}") from exc
    return payload if isinstance(payload, dict) else {}


def fetch_bsky_author_feed(actor: str, limit: int = 10, *, timeout: float = REQUEST_TIMEOUT_DISCOVERY_SEARCH) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"actor": actor, "limit": max(1, min(limit, 20))})
    payload = http_transport.request_json(f"{BSKY_PUBLIC_API}/xrpc/app.bsky.feed.getAuthorFeed?{params}", timeout=timeout)
    results: list[dict[str, Any]] = []
    for item in payload.get("feed", [])[:limit]:
        post = item.get("post", {}) if isinstance(item.get("post"), dict) else {}
        author = post.get("author", {}) if isinstance(post.get("author"), dict) else {}
        record = post.get("record", {}) if isinstance(post.get("record"), dict) else {}
        text = str(record.get("text") or "")
        results.append(
            {
                "uri": str(post.get("uri") or ""),
                "text": text,
                "urls": bsky_record_urls(record),
                "author_handle": str(author.get("handle") or actor),
            }
        )
    return results


def fetch_bsky_profile(actor: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"actor": actor})
    payload = http_transport.request_json(f"{BSKY_PUBLIC_API}/xrpc/app.bsky.actor.getProfile?{params}")
    return payload if isinstance(payload, dict) else {}


def fetch_bsky_post_by_web_url(url: str) -> dict[str, Any]:
    normalized = identifier_core.normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc != "bsky.app":
        return {}
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 4 or segments[0] != "profile" or segments[2] != "post":
        return {}
    actor = segments[1]
    rkey = segments[3]
    profile = fetch_bsky_profile(actor)
    did = str(profile.get("did") or "")
    if not did:
        return {}
    params = urllib.parse.urlencode({"uris": f"at://{did}/app.bsky.feed.post/{rkey}"})
    payload = http_transport.request_json(f"{BSKY_PUBLIC_API}/xrpc/app.bsky.feed.getPosts?{params}")
    posts = payload.get("posts", []) if isinstance(payload, dict) else []
    return posts[0] if posts else {}


def bsky_record_urls(record: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    facets = record.get("facets", []) if isinstance(record.get("facets", []), list) else []
    for facet in facets:
        if not isinstance(facet, dict):
            continue
        for feature in facet.get("features", []) if isinstance(facet.get("features", []), list) else []:
            if not isinstance(feature, dict):
                continue
            uri = str(feature.get("uri") or "").strip()
            if uri:
                urls.append(uri)
    urls.extend(extract_urls(str(record.get("text") or "")))
    return project_record.unique_list(urls)


def resolve_dns_txt(name: str, *, timeout: float = REQUEST_TIMEOUT_LEXICON_DNS) -> list[str]:
    params = urllib.parse.urlencode({"name": name, "type": "TXT"})
    payload = http_transport.request_json(f"{DNS_RESOLVER_URL}?{params}", timeout=timeout)
    values: list[str] = []
    for answer in payload.get("Answer", []):
        data = str(answer.get("data") or "").strip()
        if data.startswith('"') and data.endswith('"'):
            data = data[1:-1]
        values.append(data)
    return values


def resolve_did_document_url(did: str) -> str:
    if did.startswith("did:plc:"):
        return f"https://plc.directory/{urllib.parse.quote(did, safe=':')}"
    if did.startswith("did:web:"):
        body = did.removeprefix("did:web:")
        parts = body.split(":")
        if len(parts) == 1:
            return f"https://{parts[0]}/.well-known/did.json"
        host = parts[0]
        path = "/".join(parts[1:])
        return f"https://{host}/{path}/did.json"
    raise ValueError(f"unsupported DID method: {did}")


def resolve_did_document(did: str, *, timeout: float = REQUEST_TIMEOUT_LEXICON_HTTP) -> dict[str, Any]:
    return http_transport.request_json(resolve_did_document_url(did), timeout=timeout)


def extract_pds_url(did_doc: dict[str, Any]) -> str:
    for service in did_doc.get("service", []):
        service_type = str(service.get("type") or "")
        service_id = str(service.get("id") or "")
        if service_type == "AtprotoPersonalDataServer" or service_id.endswith("#atproto_pds"):
            return str(service.get("serviceEndpoint") or "").rstrip("/")
    return ""


def did_document_handles(did_doc: dict[str, Any]) -> list[str]:
    values = []
    for item in did_doc.get("alsoKnownAs", []) if isinstance(did_doc.get("alsoKnownAs"), list) else []:
        text = str(item or "").strip()
        if text.startswith("at://"):
            values.append(text.removeprefix("at://").strip().lower().rstrip("."))
    return project_record.unique_list([value for value in values if value])


def fetch_recent_record_samples(collections: list[str], *, timeout: float = REQUEST_TIMEOUT_LEXICON_HTTP) -> list[dict[str, Any]]:
    normalized = project_record.unique_list([identifier_core.normalize_nsid(value) for value in collections if identifier_core.normalize_nsid(value)])
    if not normalized:
        return []
    params = urllib.parse.urlencode([("collection", value) for value in normalized])
    payload = http_transport.request_json(f"{UFOS_API}/records?{params}", timeout=timeout)
    if not isinstance(payload, list):
        raise ValueError("UFOs record sample response must be a JSON array")
    samples: list[dict[str, Any]] = []
    per_collection_counts: dict[str, int] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        collection = identifier_core.normalize_nsid(str(item.get("collection") or ""))
        if not collection:
            continue
        count = per_collection_counts.get(collection, 0)
        if count >= 3:
            continue
        per_collection_counts[collection] = count + 1
        samples.append(
            {
                "collection": collection,
                "did": str(item.get("did") or ""),
                "rkey": str(item.get("rkey") or ""),
                "time_us": int(item.get("time_us") or 0),
                "record": integration_core.compact_json_value(item.get("record", {}), max_depth=3, max_items=8, max_string=240),
            }
        )
    return samples[:18]


def project_content_is_clear_enough(project: dict[str, Any]) -> bool:
    summary = str(project.get("summary") or "").strip()
    tagline = str(project.get("tagline") or "").strip()
    features = [str(item or "").strip() for item in project.get("features", []) if str(item or "").strip()]
    presence = bool(
        project.get("website_url")
        or project.get("service_urls")
        or project.get("project_repository_urls")
        or project_record.resource_url_values(project)
    )
    if summary and (tagline or features or presence):
        return True
    if tagline and len(features) >= 2 and presence:
        return True
    return False


def should_refresh_recent_record_samples(project: dict[str, Any]) -> bool:
    collections = lexicon_types.record_sample_nsids(project)[:6]
    if not collections:
        return False
    if not project.get("recent_record_samples"):
        return True
    return not project_content_is_clear_enough(project)


def should_refresh_lexicon_surface(project: dict[str, Any], nsids: list[str]) -> bool:
    normalized_nsids = {
        identifier_core.normalize_nsid(value)
        for value in nsids
        if identifier_core.normalize_nsid(value)
    }
    if not normalized_nsids:
        return False
    resolved = {identifier_core.normalize_nsid(str(item or "")) for item in project.get("resolved_nsids", []) if identifier_core.normalize_nsid(str(item or ""))}
    sampled = {
        identifier_core.normalize_nsid(str(item.get("nsid") or ""))
        for item in project.get("lexicon_schema_samples", [])
        if isinstance(item, dict) and identifier_core.normalize_nsid(str(item.get("nsid") or ""))
    }
    if not normalized_nsids or not normalized_nsids.issubset(resolved) or not normalized_nsids.issubset(sampled):
        return True
    return not project_content_is_clear_enough(project)


def resolve_lexicon_authority_did(authority_domain: str, *, timeout: float = REQUEST_TIMEOUT_LEXICON_DNS) -> str:
    txt_name = f"_lexicon.{authority_domain}"
    for value in resolve_dns_txt(txt_name, timeout=timeout):
        if value.startswith("did="):
            return value.removeprefix("did=").strip()
    raise ValueError(f"no lexicon DID found for authority domain: {authority_domain}")


def fetch_lexicon_record(
    nsid: str,
    *,
    dns_timeout: float = REQUEST_TIMEOUT_LEXICON_DNS,
    http_timeout: float = REQUEST_TIMEOUT_LEXICON_HTTP,
    authority_cache: dict[str, str] | None = None,
    document_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = identifier_core.normalize_nsid(nsid)
    authority_domain = identifier_core.derive_authority_domain(normalized)
    if not authority_domain:
        raise ValueError(f"invalid NSID: {nsid}")
    did = (authority_cache or {}).get(authority_domain) or resolve_lexicon_authority_did(authority_domain, timeout=dns_timeout)
    if authority_cache is not None:
        authority_cache[authority_domain] = did
    did_doc = (document_cache or {}).get(did) or resolve_did_document(did, timeout=http_timeout)
    if document_cache is not None:
        document_cache[did] = did_doc
    pds_url = extract_pds_url(did_doc)
    if not pds_url:
        raise ValueError(f"no PDS endpoint found for DID: {did}")
    params = urllib.parse.urlencode(
        {
            "repo": did,
            "collection": "com.atproto.lexicon.schema",
            "rkey": normalized,
        }
    )
    payload = http_transport.request_json(f"{pds_url}/xrpc/com.atproto.repo.getRecord?{params}", timeout=http_timeout)
    return {
        "did": did,
        "did_doc": did_doc,
        "authority_domain": authority_domain,
        "pds_url": pds_url,
        "record": payload,
    }


def apply_lexicon_record_result(project: dict[str, Any], nsid: str, result: dict[str, Any]) -> None:
    nsid = identifier_core.normalize_nsid(nsid)
    if not nsid:
        return
    admitted_candidates = {item.value for item in runtime_view.project_identifiers(project)
                           if item.kind == "nsid" and item.role == "owned_nsid" and item.classification == "candidate"}
    validation = collection_trust.publication_validation(nsid, result)
    previous_entries = [e for e in project.get("lexicon_resolution_entries", []) if e.get("input_nsid") == nsid]
    previous_type = next((e.get("publication_validation", {}).get("schema_type") for e in reversed(previous_entries)
                          if e.get("publication_validation", {}).get("schema_type")), None)
    if not validation:
        project.setdefault("lexicon_resolution_entries", []).append({
            "input_nsid": nsid, "resolved_nsid": str((result.get("record", {}).get("value") or {}).get("id") or nsid),
            "repo_did": str(result.get("did") or ""), "authority_domain": str(result.get("authority_domain") or ""),
            "record_uri": str(result.get("record", {}).get("uri") or ""),
            "resolved_at": str(result.get("resolved_at") or project_record.iso_now()),
            "publication_validation": {"schema_type": previous_type or "", "type_changed": bool(previous_entries and previous_entries[-1].get("publication_validation", {}).get("type_changed"))},
        })
        trust_graph_core.remove_edges(project, source=f"lexicon_resolution:{nsid}")
        return
    type_changed = bool(previous_type and previous_type != validation["schema_type"]) or any(
        e.get("publication_validation", {}).get("type_changed") for e in previous_entries[-1:])
    if type_changed:
        project["publication_review"] = project_record.unique_list(list(project.get("publication_review", [])) + [f"schema型の変更: {nsid}"])
        trust_graph_core.remove_edges(project, source=f"lexicon_resolution:{nsid}")
    record = result["record"]
    schema = record.get("value", {})
    schema_node_kind = "collection" if validation["schema_type"] == "record" else "nsid"
    resolved_nsid = identifier_core.normalize_nsid(
        str(record.get("uri", "")).rsplit("/", 1)[-1]
        or str(schema.get("id") or nsid)
    )
    if not resolved_nsid:
        return
    was_confirmed = resolved_nsid in {
        identifier_core.normalize_nsid(str(value or ""))
        for value in project.get("resolved_nsids", [])
    }
    project["lexicons"] = project_record.add_unique(project.get("lexicons", []), resolved_nsid)
    if schema_node_kind == "collection":
        project["record_creators"] = project_record.add_unique(project.get("record_creators", []), result["did"])
    project["lexicon_resolution_entries"] = project.get("lexicon_resolution_entries", [])
    project["lexicon_resolution_entries"].append(
        {
            "input_nsid": identifier_core.normalize_nsid(nsid),
            "resolved_nsid": identifier_core.normalize_nsid(resolved_nsid),
            "repo_did": result["did"],
            "authority_domain": str(result.get("authority_domain") or identifier_core.derive_authority_domain(resolved_nsid)),
            "pds_url": result["pds_url"],
            "record_uri": str(record.get("uri") or ""),
            "resolved_at": str(result.get("resolved_at") or project_record.iso_now()),
            "publication_validation": {**validation, **({"type_changed": True} if type_changed else {})},
        }
    )
    project_record.record_trust_edge(
        project,
        "did",
        result["did"],
        "did_publishes_schema",
        schema_node_kind,
        resolved_nsid,
        source=f"lexicon_resolution:{identifier_core.normalize_nsid(nsid)}",
        locator=str(record.get("uri") or ""),
        method="repo_record",
        classification="confirmed",
        eligible_for_auto_confirm=not type_changed,
        observed_at=project_record.iso_now(),
    )
    for handle in did_document_handles(dict(result.get("did_doc") or {})):
        project_record.record_trust_edge(
            project,
            "did",
            result["did"],
            "did_document_claims_handle",
            "handle",
            handle,
            source=f"did_document:{result['did']}",
            locator=resolve_did_document_url(str(result["did"])),
            method="alsoKnownAs",
            classification="confirmed",
            eligible_for_auto_confirm=not type_changed,
            observed_at=project_record.iso_now(),
        )
    authority_domain = identifier_core.derive_authority_domain(resolved_nsid)
    if authority_domain:
        project["authority_domains"] = project_record.add_unique(project.get("authority_domains", []), authority_domain)
        add_authority_service_candidates(project, authority_domain, verified=True)
        project_record.record_trust_edge(
            project,
            schema_node_kind,
            resolved_nsid,
            "collection_authority_domain",
            "domain",
            authority_domain,
            source=f"lexicon_resolution:{identifier_core.normalize_nsid(nsid)}",
            locator=str(record.get("uri") or ""),
            method="lexicon_record_authority",
            classification="confirmed",
            eligible_for_auto_confirm=not type_changed,
            observed_at=project_record.iso_now(),
        )
        project_record.record_trust_edge(
            project,
            "domain",
            authority_domain,
            "domain_publishes_lexicon_record",
            schema_node_kind,
            resolved_nsid,
            source=f"lexicon_resolution:{identifier_core.normalize_nsid(nsid)}",
            locator=str(record.get("uri") or ""),
            method="lexicon_dns_and_repo_record",
            classification="confirmed",
            eligible_for_auto_confirm=not type_changed,
            observed_at=project_record.iso_now(),
        )
    evaluation = trust_collection_ownership_evaluation(project, resolved_nsid) if not was_confirmed and not type_changed and schema_node_kind == "collection" else {}
    if was_confirmed or (evaluation.get("auto_confirm") and evaluation.get("policy_version") != collection_trust.POLICY_VERSION):
        project["resolved_nsids"] = project_record.add_unique(project.get("resolved_nsids", []), resolved_nsid)
        project["input_nsids"] = [
            value
            for value in project.get("input_nsids", [])
            if identifier_core.normalize_nsid(str(value or "")) not in {nsid, resolved_nsid}
        ]
    else:
        project["resolved_nsids"] = [
            value
            for value in project.get("resolved_nsids", [])
            if identifier_core.normalize_nsid(str(value or "")) != resolved_nsid
        ]
        project["input_nsids"] = project_record.add_unique(project.get("input_nsids", []), resolved_nsid)
    description = str(schema.get("description") or "")
    project_record.add_candidate_text(project, "summary", description, f"lexicon:{resolved_nsid}")
    project_record.add_candidate_text(project, "tagline", description, f"lexicon:{resolved_nsid}")
    integration_core.upsert_lexicon_schema_sample(project, resolved_nsid, schema if isinstance(schema, dict) else {})
    # All current candidates can now use the newly observed anchor. This also
    # makes the result independent of A/B collector application order.
    confirm_collection_candidates_from_publications(project, admitted_candidates)


def confirm_collection_candidates_from_publications(project: dict[str, Any], admitted_candidates: set[str]) -> None:
    candidates = [item.value for item in runtime_view.project_identifiers(project)
                  if item.kind == "nsid" and item.role == "owned_nsid" and item.classification == "candidate"]
    for candidate in candidates:
        if candidate not in admitted_candidates:
            continue
        evaluation = collection_trust.evaluate_publisher_support(project, candidate)
        if not evaluation.get("auto_confirm"):
            continue
        project["resolved_nsids"] = project_record.add_unique(project.get("resolved_nsids", []), candidate)
        project["input_nsids"] = [value for value in project.get("input_nsids", []) if value != candidate]
        trust_graph_core.upsert_evaluation(project, evaluation, generated_at=project_record.iso_now())
        add_confirmed_identifier_decision(
            project, "nsid", candidate, source=collection_trust.ADOPTION_SOURCE, role="owned_nsid",
            note="collection accepted through authority-scoped publication evidence",
            source_ref=project_record.dump_json(evaluation), policy_version=collection_trust.POLICY_VERSION,
        )


def collect_github_repo_evidence(
    project: dict[str, Any],
    repo_url: str,
    *,
    trust_only: bool = False,
) -> EvidenceBundle:
    request_metrics = github_api.GitHubRequestMetrics()
    file_text = lambda url, path, **kwargs: repository_source_core.github_repo_file_text(
        url, path, **kwargs, request_metrics=request_metrics
    )
    return investigation_core.collect_repository_evidence(
        project,
        repo_url,
        investigation_core.RepositoryCollectorDependencies(
            now=project_record.iso_now,
            fetch_repo=lambda url: repository_source_core.fetch_github_repo(
                url, request_metrics=request_metrics
            ),
            repo_lexicon_hints=lambda url, **kwargs: repository_material_core.repo_lexicon_hints(
                url, **kwargs, file_text=file_text
            ),
            extract_urls=extract_urls,
            repo_semantic_hints=lambda url, **kwargs: repository_material_core.repo_semantic_hints(
                url, **kwargs, file_text=file_text
            ),
            repo_manifest_hints=lambda url, **kwargs: repository_material_core.repo_manifest_hints(
                url, **kwargs, file_text=file_text
            ),
            repo_configuration_hints=lambda url, **kwargs: repository_material_core.repo_configuration_hints(
                url, **kwargs, file_text=file_text
            ),
            request_metrics=request_metrics,
        ),
        trust_only=trust_only,
    )


def collect_codeberg_repo_evidence(
    project: dict[str, Any],
    repo_url: str,
    *,
    trust_only: bool = False,
) -> EvidenceBundle:
    return investigation_core.collect_repository_evidence(
        project,
        repo_url,
        investigation_core.RepositoryCollectorDependencies(
            now=project_record.iso_now,
            fetch_repo=repository_source_core.fetch_codeberg_repo,
            repo_lexicon_hints=lambda url, **kwargs: repository_material_core.repo_lexicon_hints(
                url, **kwargs, file_text=repository_source_core.codeberg_repo_file_text,
            ),
            extract_urls=extract_urls,
            repo_semantic_hints=lambda url, **kwargs: repository_material_core.repo_semantic_hints(
                url, **kwargs, file_text=repository_source_core.codeberg_repo_file_text,
            ),
            repo_manifest_hints=lambda url, **kwargs: repository_material_core.repo_manifest_hints(
                url, **kwargs, file_text=repository_source_core.codeberg_repo_file_text,
            ),
            repo_configuration_hints=lambda url, **kwargs: repository_material_core.repo_configuration_hints(
                url, **kwargs, file_text=repository_source_core.codeberg_repo_file_text,
            ),
            provider="codeberg",
        ),
        trust_only=trust_only,
    )


def collect_tangled_repo_evidence(
    project: dict[str, Any],
    repo_url: str,
    *,
    trust_only: bool = False,
) -> EvidenceBundle:
    return investigation_core.collect_tangled_repo_evidence(
        project,
        repo_url,
        investigation_core.TangledRepoCollectorDependencies(
            now=project_record.iso_now,
            fetch_tangled_repo=repository_source_core.fetch_tangled_repo,
            extract_urls=extract_urls,
        ),
        trust_only=trust_only,
    )


def apply_github_repo_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    *,
    confirm_repo_snapshot: bool = True,
) -> None:
    integration_core.apply_github_repo_evidence(
        project,
        bundle,
        integration_core.GitHubRepoIntegrationDependencies(
            confirm_project_repository_url=confirm_project_repository_url,
            add_candidate_url=add_candidate_url,
            project_owned_lexicon_nsid=project_owned_lexicon_nsid,
            add_authority_service_candidates=add_authority_service_candidates,
            repo_url_allowed_for_project=repo_url_allowed_for_project,
            add_candidate_project_repository_url=add_candidate_project_repository_url,
            repo_context_service_candidate_allowed=repo_context_service_candidate_allowed,
        ),
        confirm_repo_snapshot=confirm_repo_snapshot,
    )


def enrich_from_github_repo(project: dict[str, Any], repo_url: str) -> None:
    bundle = collect_github_repo_evidence(project, repo_url)
    apply_github_repo_evidence(project, bundle)


def apply_service_probe_result(
    project: dict[str, Any],
    service_url: str,
    result: dict[str, Any],
    *,
    follow_up_timeout: float = REQUEST_TIMEOUT_SHORT,
    collect_follow_ups: bool = True,
) -> None:
    title = naming_core.clean_title(result["title"])
    description = result["description"]
    supported = (
        bool(result.get("supports_project"))
        if "supports_project" in result
        else service_probe_supports_project(project, service_url, result)
    )
    if not supported:
        remove_unsupported_service_surface_evidence(project, service_url)
    host = urllib.parse.urlparse(identifier_core.normalize_url(service_url)).netloc.lower()
    if supported:
        if title and host not in naming_core.GENERIC_PLATFORM_HOSTS:
            naming_core.add_name_candidate(project, title, f"service_title:{identifier_core.normalize_url(service_url)}")
        project_record.add_candidate_text(project, "summary", description, f"service_meta:{identifier_core.normalize_url(service_url)}")
        project_record.add_candidate_text(project, "tagline", description, f"service_meta:{identifier_core.normalize_url(service_url)}")
        observed_service_status = project_record.normalize_service_status(result.get("service_status"))
        observed_service_confidence = str(result.get("service_status_confidence") or "none")
        observed_service_evidence = list(result.get("service_status_evidence", []))
        if observed_service_status == "unverified" and verified_authority_live_surface(project, service_url, result):
            observed_service_status = "live"
            observed_service_evidence = project_record.add_unique(observed_service_evidence, "verified_authority_service_surface")
            observed_service_confidence = project_record.merge_service_status_confidence(
                observed_service_confidence,
                "medium",
            )
        if observed_service_status == "unverified" and "mixed_launch_signals_detected" in observed_service_evidence:
            project["service_status"] = "unverified"
        else:
            project["service_status"] = project_record.merge_service_status(
                str(project.get("service_status") or "unknown"),
                observed_service_status,
            )
        project["service_status_confidence"] = project_record.merge_service_status_confidence(
            str(project.get("service_status_confidence") or "none"),
            observed_service_confidence,
        )
        project["service_status_evidence"] = project_record.unique_list(
            list(project.get("service_status_evidence", [])) + observed_service_evidence
        )
    normalized_service_url = identifier_core.normalize_url(service_url)
    canonical_service_url = service_probe_canonical_url(normalized_service_url, result)
    represents_service_identity = service_probe_represents_service_identity(canonical_service_url, result)
    if result.get("redirected"):
        integration_core.add_investigation_log(
            project,
            "service_redirect",
            url=normalized_service_url,
            final_url=str(result.get("final_url") or ""),
            redirect_http_to_https=bool(result.get("redirect_http_to_https")),
            redirect_same_host=bool(result.get("redirect_same_host")),
            redirect_same_site_family=bool(result.get("redirect_same_site_family")),
            redirect_cross_host=bool(result.get("redirect_cross_host")),
            at=project_record.iso_now(),
        )
        if result.get("redirect_same_host") and result.get("redirect_http_to_https"):
            project_record.record_trust_edge(
                project,
                "service_url",
                normalized_service_url,
                "service_canonical",
                "service_url",
                canonical_service_url,
                source=f"service_redirect:{normalized_service_url}",
                locator=normalized_service_url,
                method="http_to_https_redirect",
                classification="confirmed",
                eligible_for_auto_confirm=True,
                observed_at=project_record.iso_now(),
            )
            project_record.record_trust_edge(
                project,
                "service_url",
                canonical_service_url,
                "service_canonical",
                "service_url",
                normalized_service_url,
                source=f"service_redirect:{normalized_service_url}",
                locator=normalized_service_url,
                method="http_to_https_redirect",
                classification="confirmed",
                eligible_for_auto_confirm=True,
                observed_at=project_record.iso_now(),
            )
    if supported:
        project["evidence_urls"] = project_record.add_unique(project.get("evidence_urls", []), canonical_service_url)
        if not represents_service_identity:
            add_resource_url(
                project,
                canonical_service_url,
                role=deep_investigation_page_kind(canonical_service_url),
                source=f"service_{str(result.get('lane') or 'static')}:{normalized_service_url}",
                confidence="confirmed",
            )
    if represents_service_identity:
        apply_service_probe_redirect_final_url(project, normalized_service_url, result)
    if 0 < result["status_code"] < 400:
        if supported and represents_service_identity:
            project["supported_service_urls"] = project_record.add_unique(project.get("supported_service_urls", []), canonical_service_url)
        else:
            project["supported_service_urls"] = [
                item for item in project.get("supported_service_urls", [])
                if identifier_core.normalize_url(str(item or "")) != canonical_service_url
            ]
        if supported and represents_service_identity:
            if not confirm_service_url_with_trust(project, canonical_service_url):
                add_candidate_url(project, "service_url", canonical_service_url)
        elif not supported:
            remove_identifier_from_project(project, "service_url", normalized_service_url)
    elif result["status_code"] >= 400 or result.get("fetch_error"):
        project["supported_service_urls"] = [
            item for item in project.get("supported_service_urls", [])
            if identifier_core.normalize_url(str(item or "")) != normalized_service_url
        ]
        project["failed_service_urls"] = project_record.add_unique(project.get("failed_service_urls", []), normalized_service_url)
    for url in result["urls"]:
        if supported and project_record.is_bluesky_profile_url(url):
            project_record.record_developer_profile_url(project, url, f"service_raw_url:{normalized_service_url}")
    follow_ups = collect_first_party_links(
        project,
        normalized_service_url,
        result.get("links", []),
        f"service_page:{normalized_service_url}",
        collect_surfaces=supported,
    )
    for follow_url in follow_ups[:3] if collect_follow_ups else []:
        try:
            nested = probe_service_url(follow_url, timeout=follow_up_timeout)
            collect_first_party_links(project, follow_url, nested.get("links", []), f"service_follow_up:{follow_url}")
            integration_core.add_investigation_log(project, "service_follow_up", url=follow_url, result_count=len(nested.get("links", [])), at=project_record.iso_now())
        except Exception as exc:
            integration_core.note_failure(project, f"service follow-up failed for {follow_url}: {exc}")


CURRENT_SERVICE_BASE_LANE_PRIORITY = {"authority": 1, "static": 2, "deep": 3}


def current_service_result_supports_project(
    project: dict[str, Any],
    requested_url: str,
    result: dict[str, Any],
) -> bool:
    if "supports_project" in result:
        return bool(result.get("supports_project"))
    host = urllib.parse.urlparse(identifier_core.normalize_url(requested_url)).netloc.lower()
    if not host or service_probe_looks_parked(result) or result.get("fetch_error") or int(result.get("status_code") or 200) >= 400:
        return False
    accepted_urls = project_record.unique_list(
        list(project.get("service_urls", []))
        + list(project.get("supported_service_urls", []))
    )
    accepted_hosts = {
        urllib.parse.urlparse(identifier_core.normalize_url(str(item or ""))).netloc.lower()
        for item in accepted_urls
        if identifier_core.normalize_url(str(item or ""))
    }
    final_host = urllib.parse.urlparse(identifier_core.normalize_url(str(result.get("final_url") or ""))).netloc.lower()
    confirmed_redirect_to_accepted_host = bool(
        final_host in accepted_hosts
        and (
            result.get("redirect_same_host")
            or result.get("redirect_same_site_family")
            or result.get("redirect_http_to_https")
        )
    )
    return (
        host in project_record.manual_service_hosts(project)
        or host in accepted_hosts
        or confirmed_redirect_to_accepted_host
    )


def project_current_service_observation(project: dict[str, Any]) -> dict[str, Any] | None:
    """Project current service scalars from the latest observation in each lane and URL."""
    current_results: list[dict[str, Any]] = []
    for item in project.get("service_verification_results", []):
        if isinstance(item, dict):
            current_results = service_core.merge_service_verification_results(current_results, item)

    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for item in current_results:
        lane = str(item.get("lane") or "static").strip().lower()
        requested_url = identifier_core.normalize_url(str(
            item.get("requested_url") or item.get("final_url") or ""
        )).rstrip("/")
        if not requested_url:
            continue
        if lane in CURRENT_SERVICE_BASE_LANE_PRIORITY:
            if not service_probe_represents_service_identity(requested_url, {**item, "lane": lane}):
                continue
        elif lane != "browser":
            continue
        grouped.setdefault(requested_url, {})[lane] = item

    if not grouped:
        return None

    aggregate_status = "unknown"
    aggregate_confidence = "none"
    aggregate_evidence: list[str] = []
    has_supported_observation = False
    for requested_url, lanes in grouped.items():
        current_status = "unknown"
        current_confidence = "none"
        current_evidence: list[str] = []

        base_lane = max(
            (lane for lane in lanes if lane in CURRENT_SERVICE_BASE_LANE_PRIORITY),
            key=lambda lane: (service_core.observation_time(lanes[lane]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                              CURRENT_SERVICE_BASE_LANE_PRIORITY[lane]),
            default="",
        )
        if base_lane:
            base = dict(lanes[base_lane])
            confirmed_target = requested_url in {
                identifier_core.normalize_url(str(url)).rstrip("/") for url in project.get("service_urls", [])
            }
            failed_target = bool(base.get("fetch_error") or int(base.get("status_code") or 0) >= 400
                                 or base.get("service_status") in {"dead", "private"})
            if current_service_result_supports_project(project, requested_url, base) or (confirmed_target and failed_target):
                has_supported_observation = True
                current_status = project_record.normalize_service_status(base.get("service_status"))
                current_confidence = project_record.normalize_service_status_confidence(
                    base.get("service_status_confidence")
                )
                current_evidence = project_record.unique_list(
                    [str(item) for item in base.get("service_status_evidence", []) if str(item)]
                )
                if failed_target:
                    current_status = "unverified"
                    current_confidence = "low"
                if current_status == "unverified" and verified_authority_live_surface(
                    project, requested_url, base
                ):
                    current_status = "live"
                    current_confidence = project_record.merge_service_status_confidence(current_confidence, "medium")
                    current_evidence = project_record.add_unique(
                        current_evidence, "verified_authority_service_surface"
                    )

        browser = lanes.get("browser")
        if browser is not None:
            has_supported_observation = True
            browser_observation = service_core.browser_service_observation(browser)
            base_time = service_core.observation_time(lanes.get(base_lane, {}))
            browser_time = service_core.observation_time(browser)
            newer_base_observation = bool(
                base_lane and base_time
                and (not browser_time or base_time > browser_time)
            )
            if newer_base_observation and browser_observation["positive_surface"] and current_status not in {"live", "beta", "invite_only"}:
                current_status, current_confidence = "unverified", "low"
            newer_browser_failure = bool(
                not browser_observation["positive_surface"] and browser_time
                and (not base_time or browser_time >= base_time)
            )
            if newer_browser_failure:
                current_status, current_confidence = "unverified", "low"
                current_evidence.append("browser_surface_not_confirmed")
            if browser_observation["positive_surface"] and not newer_base_observation:
                if current_status not in {"beta", "invite_only"}:
                    current_status = "live"
                current_confidence = "high"
                current_evidence = [
                    value
                    for value in current_evidence
                    if value not in service_core.SERVICE_BROWSER_POSITIVE_CONTRADICTIONS
                ]
            current_evidence = project_record.unique_list(
                current_evidence + ([] if newer_base_observation else list(browser_observation["service_status_evidence"]))
            )

        aggregate_status = project_record.merge_service_status(
            aggregate_status,
            current_status,
        )
        aggregate_confidence = project_record.merge_service_status_confidence(
            aggregate_confidence,
            current_confidence,
        )
        aggregate_evidence = project_record.unique_list(aggregate_evidence + current_evidence)

    if not has_supported_observation:
        return None
    return {
        "service_status": aggregate_status,
        "service_status_confidence": aggregate_confidence,
        "service_status_evidence": aggregate_evidence,
    }


def apply_current_service_observation_state(project: dict[str, Any]) -> bool:
    projection = project_current_service_observation(project)
    if projection is None:
        return False
    changed = any(project.get(field) != value for field, value in projection.items())
    project.update(projection)
    return changed


def exact_project_page_component_role(project: dict[str, Any], url: str) -> str:
    normalized = identifier_core.normalize_url(url)
    if not normalized:
        return ""
    if normalized in {
        identifier_core.normalize_url(str(item))
        for item in project.get("service_urls", [])
        if identifier_core.normalize_url(str(item))
    }:
        return "service"
    if normalized in {
        identifier_core.normalize_url(str(item))
        for item in project.get("project_site_urls", [])
        if identifier_core.normalize_url(str(item))
    }:
        return "project_site"
    return ""


def apply_project_repository_page_observations(
    project: dict[str, Any],
    page_url: str,
    html: str,
    *,
    source: str,
) -> None:
    observed_at = project_record.iso_now()
    project["relation_evidence"] = relation_core.normalize_relation_records([
        item
        for item in project.get("relation_evidence", [])
        if not (
            str(item.get("source") or "") == source
            and str((item.get("relation") or {}).get("predicate") or "") == "project_repository"
        )
    ])
    bundle = EvidenceBundle(
        project_key=str(project.get("project_key") or ""),
        collected_at=observed_at,
    )
    for record in service_core.project_repository_relation_records(
        page_url,
        html,
        observed_at=observed_at,
        subject_component_role=exact_project_page_component_role(project, page_url),
        source=source,
    ):
        bundle.add_record(record)
    apply_service_evidence(project, bundle)


def enrich_from_resource_pages(project: dict[str, Any], *, max_pages: int = 6) -> None:
    queued = deep_investigation_page_urls(project, max_pages=max_pages)
    visited: set[str] = set()
    for resource_url in queued:
        normalized_resource_url = identifier_core.normalize_url(resource_url)
        if not normalized_resource_url or normalized_resource_url in visited:
            continue
        visited.add(normalized_resource_url)
        result = probe_service_url(normalized_resource_url)
        apply_resource_page_probe_status(project, normalized_resource_url, result)
        apply_project_repository_page_observations(
            project,
            normalized_resource_url,
            str(result.get("html") or ""),
            source=f"resource_page:{normalized_resource_url}",
        )
        title = naming_core.clean_title(result["title"])
        description = str(result.get("description") or "").strip()
        if title:
            naming_core.add_name_candidate(project, title, f"resource_title:{normalized_resource_url}")
            project_record.add_candidate_text(project, "tagline", title, f"resource_meta:{normalized_resource_url}")
        if description:
            project_record.add_candidate_text(project, "summary", description, f"resource_meta:{normalized_resource_url}")
            project_record.add_candidate_text(project, "tagline", description, f"resource_meta:{normalized_resource_url}")
        integration_core.add_investigation_log(
            project,
            "resource_probe",
            url=normalized_resource_url,
            page_kind=deep_investigation_page_kind(normalized_resource_url),
            status_code=int(result.get("status_code") or 0),
            title=title,
            at=project_record.iso_now(),
        )
        follow_ups = collect_first_party_links(project, normalized_resource_url, result.get("links", []), f"resource_page:{normalized_resource_url}")
        for follow_url in follow_ups[:2]:
            normalized_follow_url = identifier_core.normalize_url(follow_url)
            if not normalized_follow_url or normalized_follow_url in visited:
                continue
            visited.add(normalized_follow_url)
            nested = probe_service_url(normalized_follow_url)
            apply_resource_page_probe_status(project, normalized_follow_url, nested)
            apply_project_repository_page_observations(
                project,
                normalized_follow_url,
                str(nested.get("html") or ""),
                source=f"resource_follow_up:{normalized_follow_url}",
            )
            nested_title = naming_core.clean_title(nested["title"])
            nested_description = str(nested.get("description") or "").strip()
            if nested_title:
                naming_core.add_name_candidate(project, nested_title, f"resource_title:{normalized_follow_url}")
                project_record.add_candidate_text(project, "tagline", nested_title, f"resource_follow_up:{normalized_follow_url}")
            if nested_description:
                project_record.add_candidate_text(project, "summary", nested_description, f"resource_follow_up:{normalized_follow_url}")
                project_record.add_candidate_text(project, "tagline", nested_description, f"resource_follow_up:{normalized_follow_url}")
            collect_first_party_links(project, normalized_follow_url, nested.get("links", []), f"resource_follow_up:{normalized_follow_url}")
            integration_core.add_investigation_log(
                project,
                "resource_follow_up",
                url=normalized_follow_url,
                page_kind=deep_investigation_page_kind(normalized_follow_url),
                status_code=int(nested.get("status_code") or 0),
                title=nested_title,
                at=project_record.iso_now(),
            )


def service_probe_timeout_for_project(project: dict[str, Any], service_url: str) -> float:
    normalized = identifier_core.normalize_url(service_url)
    if not normalized:
        return REQUEST_TIMEOUT_SERVICE_CANDIDATE
    confirmed = {
        identifier_core.normalize_url(str(item or ""))
        for item in list(project.get("service_urls", []))
        if identifier_core.normalize_url(str(item or ""))
    }
    manual = {
        identifier_core.normalize_url(str(item.get("value") or "").strip())
        for item in project.get("manual_seeds", [])
        if isinstance(item, dict)
        and str(item.get("type") or "") == "service_url"
        and identifier_core.normalize_url(str(item.get("value") or "").strip())
    }
    if normalized in confirmed or normalized in manual:
        return REQUEST_TIMEOUT_SERVICE_CONFIRMED
    return REQUEST_TIMEOUT_SERVICE_CANDIDATE


def build_llm_evidence_bundle(
    project: dict[str, Any],
    *,
    include_candidate_repos: bool = False,
    max_bytes: int = llm_evidence_core.LLM_EVIDENCE_MAX_BYTES,
) -> dict[str, Any]:
    candidate_repo_urls: list[str] = []
    if include_candidate_repos and not project.get("project_repository_urls"):
        candidate_repo_urls = [
            url
            for url in project.get("candidate_project_repository_urls", [])
            if repo_candidate_allowed_for_project(project, str(url or ""))
        ]
    relationship_candidates: list[dict[str, str]] = []
    relationship_indexes: set[tuple[str, str]] = set()
    relevant_repos = {
        repo_url_core.canonical_repo_url(str(value or ""))
        for value in list(project_record.project_repository_url_values(project))
        + list(project.get("candidate_project_repository_urls", []))
        if repo_url_core.canonical_repo_url(str(value or ""))
    }
    for edge in trust_graph_core.graph(project).get("edges", []):
        subject = edge.get("subject") if isinstance(edge.get("subject"), dict) else {}
        obj = edge.get("object") if isinstance(edge.get("object"), dict) else {}
        repo_url = repo_url_core.canonical_repo_url(str(obj.get("value") or ""))
        service_url = identifier_core.normalize_url(str(subject.get("value") or ""))
        if (
            edge.get("predicate") != "service_links_repo"
            or edge.get("classification") != "candidate"
            or edge.get("eligible_for_auto_confirm")
            or subject.get("kind") != "service_url"
            or obj.get("kind") != "repo_url"
            or not service_url
            or not repo_url
            or repo_url not in relevant_repos
            or (service_url, repo_url) in relationship_indexes
        ):
            continue
        context = edge.get("context") if isinstance(edge.get("context"), dict) else {}
        relationship_indexes.add((service_url, repo_url))
        relationship_candidates.append({
            "service_url": service_url,
            "repo_url": repo_url,
            "link_text": str(context.get("link_text") or ""),
            "aria_label": str(context.get("aria_label") or ""),
            "link_title": str(context.get("link_title") or ""),
            "page_title": str(context.get("page_title") or ""),
            "page_description": str(context.get("page_description") or ""),
        })
    evidence = {
        "project_key": project["project_key"],
        "name_hint": project.get("name") or "",
        "current_profile": {
            "tagline": llm_evidence_core.bounded_utf8_string(str(project.get("tagline") or ""), 1024),
            "tagline_is_provisional": bool(project.get("tagline_is_provisional")),
            "summary": llm_evidence_core.bounded_utf8_string(str(project.get("summary") or ""), 2048),
            "features": [
                llm_evidence_core.bounded_utf8_string(str(item or ""), 512)
                for item in list(project.get("features", []))[:6]
                if str(item or "").strip()
            ],
        },
        "manual_seeds": project.get("manual_seeds", []),
        "input_nsids": project.get("input_nsids", []),
        "resolved_nsids": project.get("resolved_nsids", []),
        "recorded_lexicon_namespaces": project.get("recorded_lexicon_namespaces", []),
        "lexicon_resolution_entries": project.get("lexicon_resolution_entries", []),
        "lexicon_schema_samples": project.get("lexicon_schema_samples", [])[:6],
        "publication_schemas": project.get("publication_schemas", [])[:12],
        "recent_record_samples": project.get("recent_record_samples", [])[:12],
        "record_creators": project.get("record_creators", []),
        "authority_domains": project.get("authority_domains", []),
        "website_url": project.get("website_url") or "",
        "service_urls": project.get("service_urls", []),
        "resource_url_records": project_record.resource_url_record_values(project),
        "service_url_candidates": project.get("service_url_candidates", []),
        "failed_service_urls": project.get("failed_service_urls", []),
        "supported_service_urls": project.get("supported_service_urls", []),
        "lexicon_scope": project.get("lexicon_scope") or "unknown",
        "lexicon_scope_evidence": project.get("lexicon_scope_evidence", []),
        "service_status_hint": project.get("service_status") or "unknown",
        "service_status_confidence": project.get("service_status_confidence") or "none",
        "service_status_evidence": project.get("service_status_evidence", []),
        "project_repository_urls": project.get("project_repository_urls", []),
        "repository_analysis_results": repository_analysis_core.select_for_triage(project),
        "candidate_project_repository_urls": candidate_repo_urls[:2],
        "repository_link_candidates": relationship_candidates,
        "bluesky_handles": project.get("bluesky_handles", []),
        "tangled_handles": project.get("tangled_handles", []),
        "developer_accounts": project.get("developer_accounts", []),
        "candidate_developer_accounts": project.get("candidate_developer_accounts", []),
        "summary_candidates": project.get("summary_candidates", []),
        "tagline_candidates": project.get("tagline_candidates", []),
        "evidence_urls": [
            url for url in project.get("evidence_urls", [])
            if not is_bsky_post_url(url)
        ],
    }
    evidence["description_sources"] = editorial.sources({
        **project, "repository_analysis_results": evidence["repository_analysis_results"],
    })
    return llm_evidence_core.bounded_llm_evidence(
        llm_evidence_core.without_prior_llm_derived_state(evidence), max_bytes=max_bytes
    )


def load_triage_prompt_template() -> str:
    if TRIAGE_PROMPT_PATH.exists():
        content = TRIAGE_PROMPT_PATH.read_text(encoding="utf-8")
        match = re.search(r"```text\n(.*?)```", content, re.DOTALL)
        if match:
            return match.group(1).strip()
        return content.strip()
    return (
        "Classify this candidate.\n\n"
        "Return JSON only.\n"
        "Evidence:\n{{evidence_json}}\n"
    )


def build_triage_prompt(project: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    # Reserve space for the current template within the 32 KiB base prompt.
    overhead = len(build_triage_prompt_from_evidence({}).encode("utf-8")) - 2
    budget = min(llm_evidence_core.LLM_EVIDENCE_MAX_BYTES, 32 * 1024 - overhead)
    evidence = build_llm_evidence_bundle(project, max_bytes=budget)
    return build_triage_prompt_from_evidence(evidence), evidence


def build_triage_prompt_from_evidence(evidence: dict[str, Any]) -> str:
    evidence_json = llm_evidence_core.evidence_json(evidence)
    template = load_triage_prompt_template()
    prompt = template.replace("{{evidence_json}}", evidence_json)
    if "For human-readable text fields, write in Japanese." not in prompt:
        prompt = (
            "For human-readable text fields, write in Japanese. "
            "Keep schema enums, URLs, handles, DIDs, and NSIDs unchanged.\n\n"
            f"{prompt}"
        )
    return prompt


def build_investigation_prompt(project: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    evidence = build_llm_evidence_bundle(project, include_candidate_repos=True)
    prompt = (
        "You are planning a focused investigation for an AT Protocol project.\n"
        "Return JSON only. Be conservative.\n"
        "Use the evidence to suggest the next external searches to run.\n"
        "Prefer search terms that help locate source code repositories, official accounts, and status signals.\n"
        "Do not invent concrete URLs unless they already appear in evidence.\n"
        "Search queries must be plain keywords. Do not use site:, advanced web operators, or full URLs as the main query.\n\n"
        "Output schema:\n"
        "{\n"
        '  "repo_search_queries": ["string"],\n'
        '  "actor_search_queries": ["string"],\n'
        '  "post_search_queries": ["string"],\n'
        '  "focus_terms": ["string"],\n'
        '  "reason": "string"\n'
        "}\n\n"
        f"Evidence:\n{llm_evidence_core.evidence_json(evidence)}\n"
    )
    return prompt, evidence


def clean_string_list(values: Any, *, limit: int | None = None) -> list[str]:
    output: list[str] = []
    if isinstance(values, list):
        for item in values:
            text = str(item).strip()
            if text:
                output.append(text)
    if limit is not None:
        output = output[:limit]
    return project_record.unique_list(output)


def clean_human_string_list(values: Any, *, limit: int | None = None) -> list[str]:
    output: list[str] = []
    if isinstance(values, list):
        for item in values:
            text = project_record.normalize_ja_en_mixed_spacing(str(item).strip())
            if text:
                output.append(text)
    if limit is not None:
        output = output[:limit]
    return project_record.unique_list(output)


def apply_repository_link_assessments(
    project: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
) -> None:
    allowed_pairs = {
        (
            identifier_core.normalize_url(str(item.get("service_url") or "")),
            repo_url_core.canonical_repo_url(str(item.get("repo_url") or "")),
        )
        : dict(item)
        for item in evidence.get("repository_link_candidates", [])
        if isinstance(item, dict)
    }
    for item in payload.get("repository_link_assessments", []):
        if not isinstance(item, dict) or str(item.get("relationship") or "") not in {"same_project", "related", "uncertain"}:
            continue
        service_url = identifier_core.normalize_url(str(item.get("service_url") or ""))
        repo_url = repo_url_core.canonical_repo_url(str(item.get("repo_url") or ""))
        if not service_url or not repo_url or (service_url, repo_url) not in allowed_pairs:
            continue
        reason = project_record.normalize_ja_en_mixed_spacing(str(item.get("reason") or "").strip())
        project_record.record_trust_edge(
            project,
            "service_url",
            service_url,
            "service_links_repo",
            "repo_url",
            repo_url,
            source=f"llm_repository_relationship:{service_url}",
            locator=service_url,
            method="llm_same_project",
            classification="confirmed" if item.get("relationship") == "same_project" else "candidate",
            eligible_for_auto_confirm=item.get("relationship") == "same_project",
            observed_at=project_record.iso_now(),
            context={"relationship": str(item.get("relationship")), "reason": reason[:500], "input_pair": allowed_pairs[(service_url, repo_url)]},
        )
        if item.get("relationship") != "same_project":
            continue
        confirm_service_url_with_trust(project, service_url)
        confirm_project_repository_url_with_trust(
            project,
            repo_url,
            "llm_repository_relationship",
            relation="mutual_first_party_link",
            reason=reason or "LLM classified the bounded service/repository evidence as the same project",
            evidence_kind="llm_repository_relationship",
            evidence_url=service_url,
        )


def plan_investigation_queries(
    project: dict[str, Any],
    *,
    observation: dict[str, Any] | None = None,
    llm_endpoint: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    llm_reasoning_effort: str | None = None,
    llm_api_style: str | None = None,
    llm_max_output_tokens: int | None = None,
    llm_runner: Callable[..., tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    return investigation_core.plan_investigation_queries(
        project,
        investigation_core.PlanningDependencies(
            build_prompt=build_investigation_prompt,
            run_llm=llm_runner or llm_transport.run_local_llm,
            note_failure=integration_core.note_failure,
        ),
        observation=observation,
        llm_endpoint=llm_endpoint,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        llm_reasoning_effort=llm_reasoning_effort,
        llm_api_style=llm_api_style,
        llm_max_output_tokens=llm_max_output_tokens,
    )


def collect_investigation_evidence(project: dict[str, Any], plan: dict[str, Any], *, observation: dict[str, Any] | None = None) -> EvidenceBundle:
    github_request_metrics = github_api.GitHubRequestMetrics()
    return investigation_core.collect_investigation_evidence(
        project,
        plan,
        investigation_core.SearchCollectorDependencies(
            now=project_record.iso_now,
            search_github_repositories=lambda *args, **kwargs: search_github_repositories(
                *args, **kwargs, request_metrics=github_request_metrics
            ),
            github_repo_relevance=github_repo_relevance,
            repo_candidate_allowed=repo_candidate_allowed_for_project,
            search_bsky_actors=search_bsky_actors,
            bsky_actor_relevance=bsky_actor_relevance,
            search_bsky_posts=search_bsky_posts,
            handle_matches_project=integration_core.handle_matches_project,
            fetch_bsky_author_feed=fetch_bsky_author_feed,
            timeout=REQUEST_TIMEOUT_DISCOVERY_SEARCH,
            github_request_metrics=github_request_metrics,
        ),
        observation=observation,
    )


def apply_investigation_evidence(project: dict[str, Any], bundle: EvidenceBundle) -> None:
    integration_core.apply_investigation_evidence(
        project,
        bundle,
        integration_core.CandidateIntegrationDependencies(
            add_candidate_project_repository_url=add_candidate_project_repository_url,
            add_candidate_url=add_candidate_url,
        ),
    )


def apply_service_evidence(project: dict[str, Any], bundle: EvidenceBundle) -> None:
    integration_core.apply_service_evidence(
        project,
        bundle,
        integration_core.ServiceIntegrationDependencies(
            apply_probe=lambda target, url, result: apply_service_probe_result(
                target,
                url,
                {**result, 'html': str(result.get('html_extract') or '')},
                follow_up_timeout=min(service_probe_timeout_for_project(target, url), REQUEST_TIMEOUT_SHORT),
                collect_follow_ups=False,
            ),
            add_candidate_project_repository_url=add_candidate_project_repository_url,
        ),
    )
    apply_current_service_observation_state(project)


def repository_homepage_probe_urls(project: dict[str, Any]) -> list[str]:
    """Inspect exact homepage references from currently adopted repositories."""
    repositories = {
        repo_url_core.canonical_repo_url(node["value"])
        for node in trusted_anchor_nodes(project) if node["kind"] == "repo_url"
    }
    urls: list[str] = []
    for item in relation_core.normalize_relation_records(project.get("relation_evidence", [])):
        relation = item["relation"]
        subject, obj = relation["subject"], relation["object"]
        if (
            item["classification"] != "confirmed"
            or relation["predicate"] != "repository_homepage"
            or subject["type"] != "repo" or obj["type"] != "website"
            or repo_url_core.canonical_repo_url(subject["value"]) not in repositories
        ):
            continue
        url = collector_relation_policy.normalize_repository_homepage(obj["value"])
        if (
            url and collector_relation_policy.classify_url_role(url) in {"unknown", "documentation"}
            and not service_url_is_guarded(project, url) and url not in urls
        ):
            urls.append(url)
    for claim in repository_analysis_core.service_publications(project):
        url = claim["url"]
        if claim["repo_url"] in repositories and not service_url_is_guarded(project, url) and url not in urls:
            urls.append(url)
    return urls


def apply_publication_candidates(project: dict[str, Any]) -> None:
    repositories = {node["value"] for node in trusted_anchor_nodes(project) if node["kind"] == "repo_url"}
    for claim in repository_analysis_core.service_publications(project):
        if claim["repo_url"] in repositories and not service_url_is_guarded(project, claim["url"]):
            add_candidate_url(project, "service_url", claim["url"])


def confirmed_homepage_operation_relations(project: dict[str, Any], url: str) -> list[dict[str, Any]]:
    """Use an operation claim, never a probe's inference from existing support."""
    return [item for item in relation_core.normalize_relation_records(project.get("relation_evidence", []))
            if item["classification"] == "confirmed"
            and item["relation"]["predicate"] == "operates_service"
            and item["relation"]["subject"] == {"type": "project", "value": project["project_key"]}
            and item["relation"]["object"]["type"] == "service"
            and identifier_core.normalize_url(item["relation"]["object"]["value"]) == url
            and not item["relation"]["method"].endswith("_probe")
            and not item["source"].startswith("service_")]


def adopted_homepage_repositories(project: dict[str, Any], url: str) -> list[str]:
    adopted = {node["value"] for node in trusted_anchor_nodes(project) if node["kind"] == "repo_url"}
    return sorted({
        repo_url_core.canonical_repo_url(item["relation"]["subject"]["value"])
        for item in relation_core.normalize_relation_records(project.get("relation_evidence", []))
        if item["classification"] == "confirmed" and item["relation"]["predicate"] == "repository_homepage"
        and item["relation"]["subject"]["type"] == "repo"
        and identifier_core.normalize_url(item["relation"]["object"]["value"]) == url
        and repo_url_core.canonical_repo_url(item["relation"]["subject"]["value"]) in adopted
    }.union(claim["repo_url"] for claim in repository_analysis_core.service_publications(project)
             if claim["url"] == url and claim["repo_url"] in adopted))


def competing_repository_homepages(conn: sqlite3.Connection, project_key: str, urls: list[str]) -> list[dict[str, str]]:
    """Include pending homepage claims before either project adopts the URL."""
    placeholders = ",".join("?" for _ in urls)
    rows = conn.execute(
        f"""SELECT DISTINCT r.project_key, r.subject_value, r.object_value
        FROM project_relations r
        WHERE r.project_key != ? AND r.predicate = 'repository_homepage'
          AND r.classification = 'confirmed' AND r.subject_type = 'repo' AND r.object_type = 'website'
          AND (r.object_value IN ({placeholders}) OR EXISTS (
              SELECT 1 FROM project_relations d WHERE d.project_key = r.project_key
              AND d.predicate = 'redirects_to' AND d.classification = 'confirmed' AND d.method = 'http_redirect'
              AND d.subject_value = r.object_value AND d.object_value IN ({placeholders})))
        ORDER BY r.project_key, r.subject_value, r.object_value""",
        (project_key, *urls, *urls),
    ).fetchall()
    claims = []
    allowed = {}
    for row in rows:
        other_key = str(row[0])
        if other_key not in allowed:
            other = project_record.fetch_project(conn, other_key)
            allowed[other_key] = (
                set(repository_homepage_probe_urls(other)),
                {node["value"] for node in trusted_anchor_nodes(other) if node["kind"] == "repo_url"},
            )
        homepage = identifier_core.normalize_url(str(row[2]))
        repo = repo_url_core.canonical_repo_url(str(row[1]))
        if homepage in allowed[other_key][0] and repo in allowed[other_key][1]:
            claims.append({"project_key": other_key, "repository": repo, "homepage": homepage})
    for row in conn.execute(
        "SELECT project_key FROM projects WHERE project_key != ? AND state_json LIKE ?",
        (project_key, '%declares_service_url%'),
    ):
        other = project_record.fetch_project(conn, str(row[0]))
        if not other:
            continue
        other_repositories = {node["value"] for node in trusted_anchor_nodes(other) if node["kind"] == "repo_url"}
        for claim in repository_analysis_core.service_publications(other):
            if claim["repo_url"] in other_repositories and claim["url"] in urls and not service_url_is_guarded(other, claim["url"]):
                candidate = {"project_key": str(row[0]), "repository": claim["repo_url"], "homepage": claim["url"]}
                if candidate not in claims:
                    claims.append(candidate)
    return claims


def collect_homepage_role_evidence(
    conn: sqlite3.Connection, project: dict[str, Any], requested_url: str,
    result: dict[str, Any], *, allow_browser: bool = True,
) -> EvidenceBundle:
    observed_at = project_record.iso_now()
    bundle = EvidenceBundle(project_key=str(project["project_key"]), collected_at=observed_at)
    url = identifier_core.normalize_url(str(result.get("final_url") or requested_url))
    repositories = adopted_homepage_repositories(project, requested_url)
    returns = service_core.project_repository_relation_records(
        url, str(result.get("html") or ""), observed_at=observed_at,
    )
    # API root responses can advertise source code as labelled plain text.
    body = str(result.get("html") or "")
    if "<" not in body:
        for match in re.finditer(r"(?im)^\s*(?:code|source code):\s*(https?://\S+)\s*$", body):
            repo = repo_url_core.canonical_repo_url(match.group(1))
            if repo in repositories:
                returns.append(relation_core.relation_record(
                    source=f"service_page:{url}", classification="candidate", observed_at=observed_at,
                    subject_type="website", subject_value=url, predicate="project_repository",
                    object_type="repo", object_value=repo, locator=url, method="plain_text_code_reference",
                    context={"policy_disposition": "CONFIRM_PROJECT_REPOSITORY", "excerpt": match.group(0).strip()},
                ))
    supporting = [record for record in returns
                  if record.context.get("policy_disposition") == "CONFIRM_PROJECT_REPOSITORY"
                  and repo_url_core.canonical_repo_url(record.value["object"]["value"]) in repositories]
    collisions = sorted({str(row[0]) for row in conn.execute(
        "SELECT project_key FROM project_identifiers WHERE kind IN ('service_url', 'site_url') "
        "AND value IN (?, ?) AND classification = 'confirmed' AND project_key != ?",
        (requested_url, url, project["project_key"]),
    )})
    pending_collisions = competing_repository_homepages(conn, str(project["project_key"]), sorted({requested_url, url}))
    collisions = sorted(set(collisions).union(item["project_key"] for item in pending_collisions))
    noncurrent = conn.execute(
        "SELECT 1 FROM project_noncurrent_identifiers WHERE project_key = ? "
        "AND kind IN ('service_url', 'site_url') AND value IN (?, ?) LIMIT 1",
        (project["project_key"], requested_url, url),
    ).fetchone()
    facts = {
        "requested_url": requested_url, "url": url, "repositories": repositories,
        "adopted_repository": bool(repositories),
        "successful_page": 200 <= int(result.get("status_code") or 0) < 300 and not result.get("fetch_error"),
        "page_role_allowed": collector_relation_policy.classify_url_role(url) in {"unknown", "documentation"},
        "guarded": bool(noncurrent) or any(service_url_is_guarded(project, value) for value in {requested_url, url}),
        "collision_projects": collisions,
        "collision_homepages": pending_collisions,
        "existing_role": exact_project_page_component_role(project, url),
        "observed_return_reference": bool(supporting),
        "operation_relations": confirmed_homepage_operation_relations(project, url),
        "publication_claims": [claim for claim in repository_analysis_core.service_publications(project, confirmed_only=True)
                               if claim["url"] == requested_url and claim["repo_url"] in repositories],
        "protocol_surface": False,
        "return_references": [record.to_dict() for record in supporting],
        "visible_surface": False,
        "browser_status": "not_requested",
    }
    can_verify = bool(
        (facts["operation_relations"] or facts["publication_claims"])
        and facts["adopted_repository"] and facts["observed_return_reference"]
        and facts["successful_page"] and facts["page_role_allowed"]
        and not facts["guarded"] and not facts["collision_projects"]
        and facts["existing_role"] != "service"
    )
    service_kinds = {claim.get("service_kind", "unknown") for claim in facts["publication_claims"]}
    if allow_browser and can_verify and service_kinds.intersection({"api", "pds"}):
        protocol = service_core.collect_protocol_service_evidence(url, probe=probe_service_url)
        facts["protocol_evidence"] = protocol
        facts["protocol_surface"] = protocol["verified"]
        bundle.add_record(EvidenceRecord(
            kind="service_verification", source=f"service_repository_homepage_protocol:{requested_url}",
            classification="confirmed" if protocol["verified"] else "candidate", observed_at=observed_at,
            value={"requested_url": requested_url, "lane": "repository_homepage_protocol", "protocol_evidence": protocol},
        ))
        bundle.add_collector_result(CollectorResult(
            source="service_protocol", operation="verify", status="success" if protocol["verified"] else "partial",
            observed_at=observed_at, request_count=protocol["request_count"], result_count=int(protocol["verified"]),
        ))
    if allow_browser and can_verify and not service_kinds.intersection({"api", "pds"}):
        browser = service_core.collect_browser_service_evidence(
            project, requested_url,
            service_core.BrowserDependencies(navigate=service_core.playwright_navigate, now=project_record.iso_now),
        )
        facts["browser_status"] = "failed" if browser.failures else "observed"
        for record in browser.records:
            if record.kind == "service_verification" and isinstance(record.value, dict):
                value = {**record.value, "lane": "repository_homepage_browser"}
                # Only the first navigation can establish the observed homepage's role.
                first = next(iter(value.get("pages", [])), {})
                features = service_core.browser_page_visible_surface_features(first)
                facts["visible_surface"] = bool(
                    identifier_core.normalize_url(str(first.get("url") or "")) == url
                    and features["visibility_observed"] and features["positive_surface"]
                    and not first.get("network_errors") and not first.get("console_errors")
                )
                facts["browser_evidence"] = {
                    "source": f"service_repository_homepage_browser:{requested_url}",
                    "observed_at": record.observed_at,
                    "requested_url": requested_url,
                    "url": first.get("url", ""), "features": features,
                }
                bundle.add_record(EvidenceRecord(
                    kind=record.kind, source=f"service_repository_homepage_browser:{requested_url}",
                    classification=record.classification, observed_at=record.observed_at, value=value,
                ))
        bundle.failures.extend(browser.failures)
        bundle.collector_results.extend(browser.collector_results)
        if browser.failures and not browser.records:
            bundle.add_record(EvidenceRecord(
                kind="service_verification", source=f"service_repository_homepage_browser:{requested_url}",
                classification="candidate", observed_at=observed_at,
                value={"requested_url": requested_url, "lane": "repository_homepage_browser", "pages": [],
                       "fetch_error": "browser_error"},
            ))
    facts["role_assessments"] = homepage_adoption.role_assessments(facts)
    bundle.add_record(EvidenceRecord(
        kind="homepage_role_assessment", source="repository_homepage_policy", classification="derived",
        observed_at=observed_at, value={"policy_version": homepage_adoption.POLICY_VERSION,
                                      "facts": facts, "action": homepage_adoption.evaluate(facts)},
    ))
    return bundle


def apply_homepage_role_evidence(project: dict[str, Any], bundle: EvidenceBundle) -> None:
    for record in bundle.records:
        if record.kind != "homepage_role_assessment" or not isinstance(record.value, dict):
            continue
        if record.value.get("policy_version") != homepage_adoption.POLICY_VERSION:
            continue
        facts = dict(record.value["facts"])
        url = identifier_core.normalize_url(str(facts["url"]))
        # Saved protocol observations must still refer to this service's supported endpoint.
        endpoint = service_core.protocol_service_endpoint(url)
        protocol = facts.get("protocol_evidence", {})
        facts["protocol_surface"] = bool(
            facts.get("protocol_surface") and endpoint and protocol.get("verified")
            and protocol.get("endpoint") == endpoint
        )
        current_repositories = set(adopted_homepage_repositories(project, facts["requested_url"]))
        facts["repositories"] = sorted(current_repositories.intersection(facts.get("repositories", [])))
        facts["adopted_repository"] = bool(facts["repositories"])
        facts["observed_return_reference"] = any(
            repo_url_core.canonical_repo_url(item["value"]["object"]["value"]) in facts["repositories"]
            for item in facts.get("return_references", [])
        )
        facts["guarded"] = facts.get("guarded") or any(service_url_is_guarded(project, value) for value in {url, facts["requested_url"]})
        facts["existing_role"] = exact_project_page_component_role(project, url)
        facts["operation_relations"] = confirmed_homepage_operation_relations(project, url)
        recorded_publications = {(claim["repo_url"], claim["url"], claim.get("source_revision", ""))
                                 for claim in facts.get("publication_claims", [])}
        facts["publication_claims"] = [claim for claim in repository_analysis_core.service_publications(project, confirmed_only=True)
                                      if claim["url"] == facts["requested_url"] and claim["repo_url"] in facts["repositories"]
                                      and (claim["repo_url"], claim["url"], claim.get("source_revision", "")) in recorded_publications]
        facts["role_assessments"] = homepage_adoption.role_assessments(facts)
        action = homepage_adoption.evaluate(facts)
        if action == "CONFIRM_SERVICE":
            confirm_service_url(project, url)
            if url in project.get("service_urls", []):
                add_confirmed_identifier_decision(
                    project, "service_url", url, source="trust_policy:repository_homepage",
                    role="service", note="publication or operation attribution with return reference and verified service surface",
                    source_ref=project_record.dump_json(facts), policy_version=homepage_adoption.POLICY_VERSION,
                )
            else:
                action = "REVIEW_UNSUPPORTED_URL"
        elif action == "CANDIDATE_PROJECT_SITE":
            project["project_site_url_candidates"] = project_record.add_unique(
                project.get("project_site_url_candidates", []), url,
            )
        integration_core.add_investigation_log(project, "homepage_role_assessment", url=url, action=action,
                              policy_version=homepage_adoption.POLICY_VERSION)


def collect_service_evidence_with_followups(
    project: dict[str, Any],
    service_url: str,
    result: dict[str, Any],
    *,
    lane: str = "static",
) -> EvidenceBundle:
    collection_names = usage_material.known_collections(project)
    if lane == "repository_homepage":
        # Observation precedes attribution and role selection. In particular, a
        # reciprocal repository link does not establish that this page is a service.
        return service_core.collect_service_probe_evidence(
            str(project.get("project_key") or ""), service_url, result,
            observed_at=project_record.iso_now(), lane=lane,
            subject_component_role=exact_project_page_component_role(project, service_url),
            collection_names=collection_names,
        )
    supports_project = service_probe_supports_project(project, service_url, result)
    normalized_service_url = identifier_core.normalize_url(service_url)
    component_role = exact_project_page_component_role(project, normalized_service_url)
    bundle = service_core.collect_service_probe_evidence(
        str(project.get("project_key") or ""), service_url, result, observed_at=project_record.iso_now(), lane=lane,
        supports_project=supports_project,
        subject_component_role=component_role,
        collection_names=collection_names,
    )
    working = copy.deepcopy(project)
    follow_urls = collect_first_party_links(
        working,
        identifier_core.normalize_url(service_url),
        result.get("links", []),
        f"service_page:{identifier_core.normalize_url(service_url)}",
        collect_surfaces=supports_project,
    )
    for follow_url in follow_urls[:3]:
        try:
            follow_result = probe_service_url(
                follow_url,
                timeout=min(service_probe_timeout_for_project(project, follow_url), REQUEST_TIMEOUT_SHORT),
            )
            follow_bundle = service_core.collect_service_probe_evidence(
                str(project.get("project_key") or ""), follow_url, follow_result,
                observed_at=project_record.iso_now(), lane="follow_up",
                supports_project=service_probe_supports_project(project, follow_url, follow_result),
                subject_component_role="",
                collection_names=collection_names,
            )
        except Exception as exc:
            follow_bundle = failed_collector_bundle(
                project, source="service_follow_up", operation="probe", target=follow_url, exc=exc,
            )
        bundle.extend(follow_bundle)
    return bundle


def apply_saved_service_evidence(project: dict[str, Any], bundle: EvidenceBundle) -> None:
    integration_core.apply_service_evidence(
        project,
        bundle,
        integration_core.ServiceIntegrationDependencies(
            apply_probe=lambda target, url, result: apply_service_probe_result(
                target,
                url,
                {**result, 'html': str(result.get('html_extract') or '')},
                follow_up_timeout=0,
                collect_follow_ups=False,
            ),
            add_candidate_project_repository_url=add_candidate_project_repository_url,
        ),
    )
    apply_current_service_observation_state(project)


def list_atproto_records(pds_url: str, did: str, cursor: str = "") -> dict[str, Any]:
    params: dict[str, Any] = {"repo": did, "collection": tangled_core.TANGLED_COLLECTION, "limit": 100}
    if cursor:
        params["cursor"] = cursor
    payload = http_transport.request_json(
        f"{pds_url}/xrpc/com.atproto.repo.listRecords?{urllib.parse.urlencode(params)}",
        timeout=REQUEST_TIMEOUT_LEXICON_HTTP,
    )
    return payload if isinstance(payload, dict) else {}


def collect_tangled_account_evidence(project: dict[str, Any], handle: str) -> EvidenceBundle:
    return tangled_core.collect_account_repos(
        project,
        handle,
        tangled_core.TangledDependencies(
            now=project_record.iso_now,
            resolve_handle=fetch_bsky_profile,
            resolve_did_document=resolve_did_document,
            extract_pds_url=extract_pds_url,
            list_records=list_atproto_records,
        ),
    )


def apply_tangled_evidence(project: dict[str, Any], bundle: EvidenceBundle) -> None:
    integration_core.apply_tangled_evidence(
        project,
        bundle,
        integration_core.CandidateIntegrationDependencies(
            add_candidate_project_repository_url=add_candidate_project_repository_url,
            add_candidate_url=add_candidate_url,
        ),
    )


def apply_collector_snapshot_evidence(project: dict[str, Any], bundle: EvidenceBundle) -> None:
    integration_core.apply_collector_snapshot_evidence(
        project,
        bundle,
        apply_lexicon_result=apply_lexicon_record_result,
    )


def failed_collector_bundle(
    project: dict[str, Any], *, source: str, operation: str, target: str, exc: BaseException,
) -> EvidenceBundle:
    bundle = EvidenceBundle(project_key=str(project.get("project_key") or ""), collected_at=project_record.iso_now())
    bundle.add_failure(investigation_core.collector_failure(
        source=source, operation=operation, target=target,
        message=f"{source} {operation} failed for {target}: {exc}", error=exc,
    ))
    add_collector_result_summary(
        bundle,
        source=source.split(":", 1)[0],
        operation=operation,
        observed_at=bundle.collected_at,
        request_count=max(1, int(getattr(exc, "request_count", 1) or 1)),
        cache_hits=max(0, int(getattr(exc, "cache_hits", 0) or 0)),
        rate_limit=dict(getattr(exc, "rate_limit", {}) or {}),
    )
    return bundle


def collect_oauth_metadata_evidence(project: dict[str, Any], metadata_url: str) -> EvidenceBundle:
    observed_at = project_record.iso_now()
    bundle = EvidenceBundle(project_key=str(project.get("project_key") or ""), collected_at=observed_at)
    try:
        metadata = http_transport.request_json(metadata_url, timeout=REQUEST_TIMEOUT_SHORT)
        if not isinstance(metadata, dict):
            raise ValueError("OAuth client metadata must be a JSON object")
        observation = oauth_metadata.observe(metadata_url, metadata, observed_at)
        bundle.add_record(EvidenceRecord(
            kind="oauth_client_metadata", source=f"oauth_client_metadata:{metadata_url}",
            classification="derived", observed_at=observed_at, value=observation,
        ))
        if observation["analysis"]["status"] == "unavailable":
            bundle.add_failure(investigation_core.collector_failure(
                source="oauth_client_metadata", operation="parse", target=metadata_url,
                message=observation["analysis"]["error"],
                error=RuntimeError(observation["analysis"]["error"]),
            ))
        bundle.add_collector_result(CollectorResult(
            source="oauth_client_metadata", operation="fetch", status="success", observed_at=observed_at,
            request_count=1, result_count=len(bundle.records),
        ))
        parse_status = observation["analysis"]["status"]
        bundle.add_collector_result(CollectorResult(
            source="oauth_client_metadata", operation="parse", observed_at=observed_at,
            status="success" if parse_status == "parsed" else ("failed" if parse_status == "unavailable" else "partial"),
            result_count=len(observation["analysis"]["permissions"]), stop_reason=parse_status,
        ))
    except Exception as exc:
        bundle.add_failure(investigation_core.collector_failure(
            source="oauth_client_metadata", operation="fetch", target=metadata_url,
            message=f"OAuth client metadata fetch failed for {metadata_url}: {exc}", error=exc,
        ))
        add_collector_result_summary(
            bundle,
            source="oauth_client_metadata",
            operation="fetch",
            observed_at=observed_at,
            request_count=1,
        )
    return bundle


def apply_llm_output(project: dict[str, Any], payload: dict[str, Any], meta: dict[str, Any], evidence: dict[str, Any]) -> None:
    payload = dict(payload)
    project["llm_output"] = payload
    project["llm_source"] = {
        **meta,
        "evidence": evidence,
        "editorial_source_policy_version": EDITORIAL_SOURCE_POLICY_VERSION,
    }
    project["last_llm_review_at"] = str(meta.get("generated_at") or project_record.iso_now())
    apply_repository_link_assessments(project, payload, evidence)
    naming_core.sync_current_llm_name_candidate(project)
    if payload.get("tagline"):
        explicit_provisional = payload.get("tagline_is_provisional")
        resolved_provisional = True if explicit_provisional is None else bool(explicit_provisional)
        add_tagline_candidate(project, str(payload.get("tagline") or ""), "llm_output", provisional=resolved_provisional)
    llm_summary = project_record.normalize_ja_en_mixed_spacing(str(payload.get("summary") or "").strip())
    if llm_summary:
        project_record.add_candidate_text(project, "summary", llm_summary, "llm_output")
    proposed = {}
    if payload.get("tagline"):
        proposed["tagline"] = normalize_tagline(str(payload["tagline"]))
    if llm_summary:
        proposed["summary"] = llm_summary
    if isinstance(payload.get("features"), list):
        proposed["features"] = clean_human_string_list(payload["features"], limit=6)
    editorial.apply_proposal(project, proposed, payload.get("description_change"),
                            evidence.get("description_sources", []),
                            generated_at=project["last_llm_review_at"])
    project["inspired_by"] = clean_human_string_list(payload.get("inspired_by"), limit=6)
    llm_category = str(payload.get("category") or "").strip()
    if llm_category in project_record.PROJECT_CATEGORY_VALUES:
        project["category"] = llm_category
    if isinstance(payload.get("atproto_auth_only"), bool):
        project["atproto_auth_only"] = bool(
            payload["atproto_auth_only"]
            and str(project.get("category") or "") in {"project", "personal_tool"}
        )
    project["classification_reason"] = project_record.normalize_ja_en_mixed_spacing(str(payload.get("why_keep") or payload.get("classification_reason") or "").strip())
    project["exclude_reason"] = project_record.normalize_ja_en_mixed_spacing(str(payload.get("why_reject") or payload.get("exclude_reason") or "").strip())
    project["watch_reason"] = project_record.normalize_ja_en_mixed_spacing(str(payload.get("watch_reason") or "").strip())
    if any(
        key in payload
        for key in ("technical_interest_level", "technical_interest_reasons", "technical_interest_summary")
    ):
        project["technical_interest_level"] = str(
            payload.get("technical_interest_level", project.get("technical_interest_level") or "none") or "none"
        ).strip().lower()
        project["technical_interest_reasons"] = clean_string_list(
            payload.get("technical_interest_reasons", project.get("technical_interest_reasons", [])),
            limit=5,
        )
        project["technical_interest_summary"] = project_record.normalize_ja_en_mixed_spacing(
            str(
                payload.get("technical_interest_summary", project.get("technical_interest_summary") or "") or ""
            ).strip()
        )
        project_record.apply_technical_interest_state(project)
    project["manual_review_recommended"] = bool(payload.get("manual_review_recommended"))
    manual_review_kind = str(payload.get("manual_review_kind") or "other").strip().lower()
    project["manual_review_kind"] = (
        manual_review_kind
        if manual_review_kind in {
            "other",
            "editorial_refresh",
            "deep_source_material",
            "service_status_manual_check",
            "identity_resolution",
            "category_recheck",
        }
        else "other"
    )
    manual_review_priority = str(payload.get("manual_review_priority") or "none").strip().lower()
    project["manual_review_priority"] = manual_review_priority if manual_review_priority in {"none", "low", "medium", "high"} else "none"
    project["manual_review_reason"] = project_review.sanitize_manual_review_reason(project, str(payload.get("manual_review_reason") or "").strip())
    try:
        project["confidence"] = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        project["confidence"] = 0.0
    llm_service_status = str(payload.get("service_status") or "").strip()
    if llm_service_status and llm_service_status not in {"dead", "private"}:
        project["service_status"] = project_record.merge_service_status(str(project.get("service_status") or "unknown"), llm_service_status)
    for url in clean_string_list(payload.get("project_repository_urls")):
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc == "github.com":
            normalized = repo_url_core.normalize_github_repo_url(url)
            if normalized:
                if normalized in project_record.project_repository_url_values(project):
                    project["project_repository_urls"] = project_record.add_unique(project.get("project_repository_urls", []), normalized)
                    project_record.sync_source_repo_provider_views(project)
                else:
                    add_candidate_project_repository_url(
                        project,
                        normalized,
                        "llm_output",
                        reason="suggested by LLM from collected evidence",
                        evidence_kind="llm_suggestion",
                        evidence_url=url,
                    )
        elif parsed.netloc == "tangled.org":
            normalized = repo_url_core.normalize_tangled_repo_url(url)
            if normalized:
                if normalized in project_record.project_repository_url_values(project):
                    project["project_repository_urls"] = project_record.add_unique(project.get("project_repository_urls", []), normalized)
                    project_record.sync_source_repo_provider_views(project)
                else:
                    add_candidate_project_repository_url(
                        project,
                        normalized,
                        "llm_output",
                        reason="suggested by LLM from collected evidence",
                        evidence_kind="llm_suggestion",
                        evidence_url=url,
                    )
        elif parsed.scheme in {"http", "https"}:
            add_candidate_project_repository_url(
                project,
                url,
                "llm_output",
                reason="suggested by LLM from collected evidence",
                evidence_kind="llm_suggestion",
                evidence_url=url,
            )
    for url in clean_string_list(payload.get("service_urls")):
        normalized = identifier_core.normalize_url(url)
        if normalized in project.get("service_urls", []):
            confirm_service_url(project, normalized)
        else:
            add_candidate_url(project, "service_url", normalized)
    for nsid in clean_string_list(payload.get("lexicons")):
        normalized_nsid = identifier_core.normalize_nsid(nsid)
        if normalized_nsid in project.get("resolved_nsids", []):
            project["lexicons"] = project_record.add_unique(project.get("lexicons", []), normalized_nsid)


def update_project_tracking_state_value(
    conn: sqlite3.Connection,
    project_key: str,
    tracking_state: str,
    *,
    note: str = "",
) -> dict[str, Any] | None:
    project = project_record.fetch_project(conn, project_key)
    if project is None:
        return None
    project = project_record.ensure_project_shape(project)
    project["tracking_state"] = tracking_state
    if note:
        project["tracking_note"] = note
    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
    conn.commit()
    return project


def finalize_project_shape(project: dict[str, Any], *, restore_manual_repositories: bool = True) -> dict[str, Any]:
    purge_blocked_identifiers(project)
    apply_deferred_identifier_guards(project)
    remove_related_repository_service_surfaces(project)
    purge_supporting_page_service_observations(project)
    purge_guarded_service_observations(project)
    purge_guarded_service_relations(project)
    sync_authority_service_candidates_from_probe_results(project)
    prune_nsid_authority_service_url_guesses(project)
    pre_prune_external_lexicons = project_record.compute_external_lexicons(project)
    prune_project_lexicon_identity(project)
    introduced_url = identifier_core.normalize_url(str(project.get("introduced_url") or "")) if project.get("introduced_url") else ""
    project["introduced_url"] = introduced_url
    project["evidence_urls"] = [
        url for url in project_record.unique_list(project.get("evidence_urls", []))
        if url and identifier_core.normalize_url(url) != introduced_url and not is_bsky_post_url(url)
    ]
    project["investigation_queries"] = []
    cleaned_logs: list[dict[str, Any]] = []
    for item in project.get("investigation_log", []):
        if not isinstance(item, dict):
            continue
        cleaned = dict(item)
        cleaned.pop("actor_search_queries", None)
        cleaned_logs.append(cleaned)
    project["investigation_log"] = cleaned_logs
    if isinstance(project.get("llm_source"), dict) and isinstance(project["llm_source"].get("evidence"), dict):
        evidence = dict(project["llm_source"]["evidence"])
        prune_nsid_authority_service_url_guesses(evidence)
        evidence["project_key"] = project["project_key"]
        evidence["evidence_urls"] = [
            url for url in evidence.get("evidence_urls", [])
            if url and identifier_core.normalize_url(str(url)) != introduced_url and not is_bsky_post_url(str(url))
        ]
        integration_core.normalize_evidence_accounts(project, evidence)
        project["llm_source"] = dict(project["llm_source"], evidence=evidence)

    project_record.normalize_website_and_service_urls(project)
    prune_project_urls(project, restore_manual_repositories=restore_manual_repositories)
    prune_stale_service_trust_evaluations(project)
    purge_supporting_page_service_observations(project)
    integration_core.normalize_project_accounts(project)
    project_record.normalize_website_and_service_urls(project)
    purge_blocked_identifiers(project)
    apply_deferred_identifier_guards(project)
    purge_supporting_page_service_observations(project)
    purge_guarded_service_observations(project)
    purge_guarded_service_relations(project)
    if runtime_view.is_complete(project):
        project["recorded_lexicon_namespaces"] = project_record.unique_list(
            [identifier_core.normalize_nsid(str(item)) for item in project.get("recorded_lexicon_namespaces", []) if str(item).strip()]
        )
    else:
        project["recorded_lexicon_namespaces"] = project_record.compute_recorded_lexicon_namespaces(project)
    project["lexicons"] = project_record.unique_list(project.get("resolved_nsids", []))
    project["lexicon_count"] = len(project.get("lexicons", []))
    project["external_lexicons"] = project_record.unique_list(
        list(project.get("external_lexicons", []))
        + pre_prune_external_lexicons
        + project_record.compute_external_lexicons(project)
    )
    project_record.reclassify_service_specific_lexicon(project)
    project_record.normalize_repository_views(project, restore_manual_repositories=restore_manual_repositories)
    normalize_schema_definition_relations(project)
    apply_deferred_identifier_guards(project)
    url_alias_core.apply_url_aliases(project)
    naming_core.apply_project_name(project)
    normalize_service_identifier_views(project)
    apply_current_service_observation_state(project)
    return project


def collect_service_verification(project: dict[str, Any], service_url: str, lane: str) -> EvidenceBundle:
    if lane == "browser":
        return service_core.collect_browser_service_evidence(
            project,
            service_url,
            service_core.BrowserDependencies(navigate=service_core.playwright_navigate, now=project_record.iso_now),
        )
    bundle = EvidenceBundle(project_key=str(project.get("project_key") or ""), collected_at=project_record.iso_now())
    urls = [service_url]
    if lane == "deep":
        urls.extend(urllib.parse.urljoin(service_url.rstrip("/") + "/", path.lstrip("/")) for path in DEEP_INVESTIGATION_PATHS)
    for url in project_record.unique_list(urls):
        try:
            result = probe_service_url(url, timeout=service_probe_timeout_for_project(project, url))
            evidence = collect_service_evidence_with_followups(project, url, result, lane=lane)
        except Exception as exc:
            evidence = EvidenceBundle(project_key=str(project.get("project_key") or ""), collected_at=project_record.iso_now())
            evidence.add_failure(investigation_core.collector_failure(
                source=f"service_{lane}", operation="probe", target=url,
                message=f"service {lane} verification failed for {url}: {exc}", error=exc,
            ))
            add_collector_result_summary(
                evidence,
                source=f"service_{lane}",
                operation="probe",
                observed_at=evidence.collected_at,
                request_count=1,
            )
        bundle.extend(evidence)
    return bundle


def verify_service_project(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    *,
    lane: str,
    service_url: str = "",
    execute: bool = False,
) -> dict[str, Any]:
    project = project_record.ensure_project_shape(project)
    target_url = identifier_core.normalize_url(service_url) or next(iter(project_record.unique_list(
        list(project.get("service_urls", []))
        + project_record.service_url_candidate_values(project)
    )), "")
    if not target_url:
        raise ValueError("project has no service URL to verify")
    input_project = copy.deepcopy(project)
    started_at = project_record.iso_now()
    bundle = collect_service_verification(project, target_url, lane)
    if execute:
        apply_service_evidence(project, bundle)
        project = finalize_project_shape(project)
        run_id = investigation_storage.stage_investigation_run(
            project, project_key=str(project["project_key"]), started_at=started_at, completed_at=project_record.iso_now(),
            input_project=input_project, output_project=project, bundle=bundle,
        )
        try:
            project_record.save_project(conn, project, enriched=bool(input_project.get("last_enriched_at")), preserve_timestamps=True)
        except project_record.StaleProjectWriteError as exc:
            investigation_storage.save_conflicted_investigation(conn, project, exc)
            raise
        conn.commit()
    else:
        run_id = ""
    return {
        "mode": "execute" if execute else "dry_run",
        "project_key": str(project.get("project_key") or ""),
        "lane": lane,
        "service_url": target_url,
        "run_id": run_id,
        "bundle": bundle.to_dict(),
        "evaluation": investigation_storage.evaluation_snapshot(project) if execute else {},
    }


def _replay_repository_homepage_relation(value: Any) -> dict[str, Any] | None:
    relation = dict(value) if isinstance(value, dict) else {}
    subject = relation.get("subject") if isinstance(relation.get("subject"), dict) else {}
    obj = relation.get("object") if isinstance(relation.get("object"), dict) else {}
    if not (
        relation.get("predicate") == "project_repository"
        and str(relation.get("method") or "") == "github_homepage"
        and subject.get("type") == "repo"
        and obj.get("type") in {"service", "website"}
    ):
        return relation
    repo_url = repo_url_core.canonical_repo_url(str(subject.get("value") or ""))
    homepage = collector_relation_policy.normalize_repository_homepage(
        str(obj.get("value") or "")
    )
    if not repo_url or not homepage:
        return None
    return {
        **relation,
        "subject": {"type": "repo", "value": repo_url},
        "predicate": "repository_homepage",
        "object": {"type": "website", "value": homepage},
    }


def normalize_legacy_repository_homepage_replay(
    project: dict[str, Any], bundle: EvidenceBundle,
) -> tuple[EvidenceBundle, bool]:
    compatibility_applied = False
    relations: list[dict[str, Any]] = []
    for item in relation_core.normalize_relation_records(project.get("relation_evidence", [])):
        relation = _replay_repository_homepage_relation(item["relation"])
        relation_changed = relation != item["relation"]
        if relation_changed:
            compatibility_applied = True
        if relation is None:
            continue
        relations.append({
            **item,
            "relation": relation,
            "classification": (
                "confirmed"
                if relation_changed
                else item["classification"]
            ),
        })
    project["relation_evidence"] = relation_core.normalize_relation_records(relations)

    records: list[EvidenceRecord] = []
    for record in bundle.records:
        if record.kind != "relation":
            records.append(record)
            continue
        relation = _replay_repository_homepage_relation(record.value)
        if relation == record.value:
            records.append(record)
            continue
        compatibility_applied = True
        if relation is None:
            continue
        records.append(EvidenceRecord(
            kind=record.kind,
            source=record.source,
            classification="confirmed",
            observed_at=record.observed_at,
            value=relation,
            context=dict(record.context),
        ))
    return EvidenceBundle(
        project_key=bundle.project_key,
        collected_at=bundle.collected_at,
        records=records,
        failures=list(bundle.failures),
        collector_results=list(bundle.collector_results),
        schema_version=bundle.schema_version,
    ), compatibility_applied


def apply_replay_evidence(project: dict[str, Any], bundle: EvidenceBundle) -> None:
    shaped = project_record.ensure_project_shape(project)
    project.clear()
    project.update(shaped)
    bundle, legacy_homepage_compatibility_applied = normalize_legacy_repository_homepage_replay(
        project, bundle
    )
    apply_investigation_evidence(project, bundle)
    apply_github_repo_evidence(project, bundle)
    apply_saved_service_evidence(project, bundle)
    apply_tangled_evidence(project, bundle)
    apply_collector_snapshot_evidence(project, bundle)
    for record in bundle.records:
        if record.kind == "repository_analysis" and isinstance(record.value, dict):
            result = record.value.get("result", {})
            if result.get("policy_version") == repository_analysis_core.POLICY_VERSION:
                project["repository_analysis_results"] = repository_analysis_core.normalize_results(
                    list(project.get("repository_analysis_results", [])) + [result]
                )
    apply_publication_candidates(project)
    apply_homepage_role_evidence(project, bundle)
    if legacy_homepage_compatibility_applied:
        trust_graph_core.remove_edges(
            project,
            predicate="repo_links_service",
            methods={"github_homepage"},
        )
        refresh_trust_graph_evaluations(project)
    for repo_url in relation_core.corroborate_source_repositories(project):
        confirm_project_repository_url_with_trust(
            project,
            repo_url,
            "relation_corroboration",
            relation="mutual_first_party_link",
            reason="repository and first-party application independently link to each other",
            evidence_kind="relation_corroboration",
            evidence_url=repo_url,
        )


def replay_dependencies() -> replay_core.ReplayDependencies:
    return replay_core.ReplayDependencies(
        apply_evidence=apply_replay_evidence,
        finalize=lambda project: project_record.apply_atproto_contribution_state(finalize_project_shape(project)),
    )


def load_benchmark_cases(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        payloads = [project_record.load_json(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]
        return [
            dict(item)
            for payload in payloads
            for item in (payload if isinstance(payload, list) else [payload])
            if isinstance(item, dict)
            and isinstance(item.get("input_project"), dict)
            and isinstance(item.get("bundle"), dict)
            and isinstance(item.get("expected_evaluation"), dict)
        ]
    payload = project_record.load_json(path.read_text(encoding="utf-8"))
    return [dict(item) for item in (payload if isinstance(payload, list) else [payload]) if isinstance(item, dict)]


def sanitize_fixture_value(value: Any) -> Any:
    dropped_keys = {
        "html", "readme_text", "body", "text_extract", "prompt", "command", "endpoint",
        "headers", "request_headers", "response_headers", "cookie", "cookies", "raw_response",
    }

    def secret_key(key: str) -> bool:
        lowered = key.lower()
        return (
            lowered in {"authorization", "api_key", "password", "secret"}
            or lowered.endswith(("_token", "_api_key", "_password", "_secret"))
        )

    def sanitize_string(text: str) -> str:
        if re.match(r"^(?:/Users/|/home/|[A-Za-z]:[\\/])", text):
            return "[local-path]"
        if re.search(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", text):
            return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [redacted]", text)
        try:
            parsed = urllib.parse.urlparse(text)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                host = parsed.hostname or ""
                if parsed.port:
                    host = f"{host}:{parsed.port}"
                return urllib.parse.urlunparse((parsed.scheme, host, parsed.path, "", "", ""))
        except ValueError:
            pass
        return text

    if isinstance(value, str):
        return sanitize_string(value)
    if isinstance(value, list):
        return [sanitize_fixture_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): ("[redacted]" if secret_key(str(key)) else sanitize_fixture_value(item))
            for key, item in value.items()
            if str(key).lower() not in dropped_keys
        }
    return value


def refresh_current_same_site_evidence(
    conn: sqlite3.Connection,
    *,
    example_limit: int = 20,
) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM projects ORDER BY project_key").fetchall()
    changes: list[dict[str, Any]] = []
    repaired_result_count = 0
    aggregate_evidence_change_count = 0
    projects_to_save: list[dict[str, Any]] = []
    for row in rows:
        project = project_record.project_from_row(conn, row)
        project_repairs = 0
        for field in ("service_verification_results", "investigation_log"):
            for item in project.get(field, []):
                if not isinstance(item, dict) or "redirect_same_site_family" not in item:
                    continue
                normalized = service_core.normalize_redirect_same_site_policy(item)
                corrected = bool(normalized.get("redirect_same_site_family"))
                normalized_evidence = list(normalized.get("service_status_evidence", []))
                if (
                    bool(item.get("redirect_same_site_family")) == corrected
                    and list(item.get("service_status_evidence", [])) == normalized_evidence
                ):
                    continue
                item["redirect_same_site_family"] = corrected
                item["service_status_evidence"] = normalized_evidence
                project_repairs += 1

        aggregate_before = list(project.get("service_status_evidence", []))
        has_current_same_site = any(
            isinstance(item, dict) and item.get("redirect_same_site_family") is True
            for field in ("service_verification_results", "investigation_log")
            for item in project.get(field, [])
        )
        project["service_status_evidence"] = list(aggregate_before)
        if project_repairs and not has_current_same_site:
            project["service_status_evidence"] = [
                value for value in aggregate_before if value != "redirect_same_site_family"
            ]
        aggregate_changed = project["service_status_evidence"] != aggregate_before
        if not project_repairs and not aggregate_changed:
            continue
        repaired_result_count += project_repairs
        aggregate_evidence_change_count += int(aggregate_changed)
        projects_to_save.append(project)
        if len(changes) < max(0, example_limit):
            changes.append({
                "project_key": str(project["project_key"]),
                "repaired_result_count": project_repairs,
                "aggregate_evidence_changed": aggregate_changed,
            })

    if projects_to_save:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for project in projects_to_save:
                project_record.save_project(conn, project, enriched=False, preserve_timestamps=True, commit=False)
            foreign_key_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_issues:
                raise RuntimeError(f"foreign key check failed: {len(foreign_key_issues)} issue(s)")
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise RuntimeError(f"integrity check failed: {integrity}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "project_count": len(rows),
        "changed_project_count": len(projects_to_save),
        "repaired_result_count": repaired_result_count,
        "aggregate_evidence_change_count": aggregate_evidence_change_count,
        "changed_project_examples": changes,
    }


def project_split_identifiers_by_tenant(project: dict[str, Any], suffix: str) -> dict[str, dict[str, list[str]]]:
    groups: dict[str, dict[str, list[str]]] = {}

    def add(tenant: str, identifier_type: str, value: str) -> None:
        if not tenant or not value:
            return
        group = groups.setdefault(
            tenant,
            {"service_urls": [], "nsids": [], "authority_domains": []},
        )
        field = {
            "service_url": "service_urls",
            "nsid": "nsids",
            "authority_domain": "authority_domains",
        }[identifier_type]
        group[field] = project_record.add_unique(group[field], value)

    service_urls = project_record.unique_list(
        list(project.get("service_urls", []))
        + project_record.manual_seed_values(project, "service_url")
    )
    for service_url in service_urls:
        normalized = identifier_core.normalize_url(str(service_url or ""))
        host = urllib.parse.urlparse(normalized).netloc.lower()
        add(domain_policy_core.tenant_identity_for_suffix(host, suffix), "service_url", normalized)

    nsids = project_record.unique_list(
        list(project.get("input_nsids", []))
        + list(project.get("resolved_nsids", []))
        + project_record.manual_seed_values(project, "nsid")
    )
    for nsid in nsids:
        normalized = identifier_core.normalize_nsid(str(nsid or ""))
        authority = identifier_core.derive_authority_domain_from_namespace(normalized)
        add(domain_policy_core.tenant_identity_for_suffix(authority, suffix), "nsid", normalized)

    for authority in project.get("authority_domains", []):
        normalized = str(authority or "").strip().lower().rstrip(".")
        add(domain_policy_core.tenant_identity_for_suffix(normalized, suffix), "authority_domain", normalized)
    return groups


def virtual_tenant_project(project: dict[str, Any], tenant: str, group: dict[str, list[str]]) -> dict[str, Any]:
    virtual = project_record.default_project_record(f"virtual:{project['project_key']}:{tenant}", "service_url", "")
    virtual["name"] = tenant
    virtual["service_urls"] = list(group.get("service_urls", []))
    virtual["input_nsids"] = list(group.get("nsids", []))
    virtual["resolved_nsids"] = list(group.get("nsids", []))
    virtual["authority_domains"] = list(group.get("authority_domains", []))
    virtual["recorded_lexicon_namespaces"] = project_record.unique_list(
        [
            project_record.namespace_from_collection_nsid(nsid)
            for nsid in group.get("nsids", [])
            if project_record.namespace_from_collection_nsid(nsid)
        ]
    )
    return finalize_project_shape(virtual)


def project_split_identifiers_by_authority_domain(project: dict[str, Any], authority_domain: str) -> dict[str, list[str]]:
    normalized_domain = str(authority_domain or "").strip().lower().rstrip(".")
    group: dict[str, list[str]] = {"service_urls": [], "nsids": [], "authority_domains": []}
    if not normalized_domain:
        return group

    service_urls = project_record.unique_list(
        list(project.get("service_urls", []))
        + project_record.manual_seed_values(project, "service_url")
    )
    for service_url in service_urls:
        normalized_url = identifier_core.normalize_url(str(service_url or ""))
        if urllib.parse.urlparse(normalized_url).netloc.lower() == normalized_domain:
            group["service_urls"] = project_record.add_unique(group["service_urls"], normalized_url)

    nsids = project_record.unique_list(
        list(project.get("input_nsids", []))
        + list(project.get("resolved_nsids", []))
        + project_record.manual_seed_values(project, "nsid")
    )
    for nsid in nsids:
        normalized_nsid = identifier_core.normalize_nsid(str(nsid or ""))
        if identifier_core.derive_authority_domain(normalized_nsid) == normalized_domain:
            group["nsids"] = project_record.add_unique(group["nsids"], normalized_nsid)

    authority_domains = project_record.unique_list(list(project.get("authority_domains", [])) + project_record.manual_seed_values(project, "domain"))
    for domain in authority_domains:
        normalized = str(domain or "").strip().lower().rstrip(".")
        if normalized == normalized_domain:
            group["authority_domains"] = project_record.add_unique(group["authority_domains"], normalized)
    return group


def split_commands_for_tenant(project_key: str, group: dict[str, list[str]]) -> list[str]:
    commands: list[str] = []
    for identifier_type, values in (
        ("service_url", group.get("service_urls", [])),
        ("nsid", group.get("nsids", [])),
        ("domain", group.get("authority_domains", [])),
    ):
        for value in values:
            commands.append(
                "project-registry split-project "
                f"--project-key {shlex.quote(project_key)} "
                f"--identifier-type {identifier_type} "
                f"--value {shlex.quote(str(value))}"
            )
    return commands


def split_project_identifier_group(
    conn: sqlite3.Connection,
    source_project_key: str,
    *,
    split_label: str,
    group: dict[str, list[str]],
    discovery_source: str,
    seed_source: str,
    note: str = "",
    execute: bool = True,
) -> dict[str, Any]:
    source = project_record.fetch_project(conn, source_project_key)
    if source is None:
        raise ValueError(f"source project not found: {source_project_key}")
    source = project_record.ensure_project_shape(source)
    identifiers = [
        (identifier_type, str(value))
        for identifier_type, values in (
            ("service_url", group.get("service_urls", [])),
            ("nsid", group.get("nsids", [])),
            ("domain", group.get("authority_domains", [])),
        )
        for value in values
        if str(value).strip()
    ]
    if not identifiers:
        raise ValueError(f"split group has no movable identifiers: {split_label}")

    target_key = project_record.allocate_new_project_key(conn)
    first_type, first_value = identifiers[0]
    target = project_record.default_project_record(target_key, first_type, first_value)
    if first_type == "nsid":
        target["resolved_nsids"] = []
    target["tracking_state"] = source.get("tracking_state", "active")
    target["status"] = source.get("status", "draft")
    target["discovery_sources"] = project_record.unique_list(list(source.get("discovery_sources", [])) + [discovery_source])
    target["tracking_note"] = f"split {split_label} from {source['project_key']}"

    blocked = project_record.normalize_blocked_identifiers(list(source.get("blocked_identifiers", [])))
    for identifier_type, identifier_value in identifiers:
        if identifier_type == "service_url":
            confirm_service_seed(target, identifier_value, source=seed_source)
        elif identifier_type == "nsid":
            source_confirmed = identifier_value in source.get("resolved_nsids", [])
            field = "resolved_nsids" if source_confirmed else "input_nsids"
            target[field] = project_record.add_unique(target.get(field, []), identifier_value)
        else:
            add_manual_seed(target, identifier_type, identifier_value, source=seed_source)
        remove_identifier_from_project(source, identifier_type, identifier_value)
        block_key = (identifier_type, identifier_value)
        if not any((str(item.get("type") or ""), str(item.get("value") or "")) == block_key for item in blocked):
            blocked.append(
                {
                    "type": identifier_type,
                    "value": identifier_value,
                    "note": note.strip() or f"split {split_label} to {target_key}",
                    "added_at": project_record.iso_now(),
                }
            )
    source["blocked_identifiers"] = blocked
    source["separate_project_keys"] = project_record.unique_list(list(source.get("separate_project_keys", [])) + [target_key])
    target["separate_project_keys"] = project_record.unique_list(list(target.get("separate_project_keys", [])) + [source["project_key"]])
    note_line = f"split {split_label} -> {target_key}"
    if note:
        note_line = f"{note_line} ({note.strip()})"
    project_record.append_system_note(source, note_line)

    source = finalize_project_shape(source)
    target = finalize_project_shape(target)
    source["duplicate_candidates"] = project_merge.compute_duplicate_candidates(conn, source)
    source["merge_recommendation"] = project_merge.compute_merge_recommendation(source)
    target["duplicate_candidates"] = project_merge.compute_duplicate_candidates(conn, target)
    target["merge_recommendation"] = project_merge.compute_merge_recommendation(target)
    if execute:
        with concurrency.write_transaction(conn):
            concurrency.check_projects(conn, [source, target])
            project_record.save_project(conn, source, enriched=bool(source.get("last_enriched_at")), preserve_timestamps=True, commit=False)
            project_record.save_project(conn, target, enriched=False, preserve_timestamps=True, commit=False)
    return {
        "mode": "execute" if execute else "dry_run",
        "source_project_key": source["project_key"],
        "target_project_key": target["project_key"],
        "split_label": split_label,
        "moved_identifiers": [
            {"type": identifier_type, "value": identifier_value}
            for identifier_type, identifier_value in identifiers
        ],
        "source_name": source.get("name", ""),
        "target_name": target.get("name", ""),
        "source_seed_type": source.get("seed_type", ""),
        "source_seed_value": source.get("seed_value", ""),
        "target_seed_type": target.get("seed_type", ""),
        "target_seed_value": target.get("seed_value", ""),
        "source_blocked_identifiers": source.get("blocked_identifiers", []),
        "source_input_nsids": source.get("input_nsids", []),
        "source_service_urls": source.get("service_urls", []),
        "source_authority_domains": source.get("authority_domains", []),
        "source_project_repository_urls": source.get("project_repository_urls", []),
        "target_input_nsids": target.get("input_nsids", []),
        "target_service_urls": target.get("service_urls", []),
        "target_authority_domains": target.get("authority_domains", []),
        "target_project_repository_urls": target.get("project_repository_urls", []),
        "target_merge_recommendation": target.get("merge_recommendation", {}),
    }


def split_project_authority_domain(
    conn: sqlite3.Connection,
    source_project_key: str,
    authority_domain: str,
    *,
    note: str = "",
    execute: bool = False,
) -> dict[str, Any]:
    source = project_record.fetch_project(conn, source_project_key)
    if source is None:
        raise ValueError(f"source project not found: {source_project_key}")
    source = project_record.ensure_project_shape(source)
    normalized_domain = str(authority_domain or "").strip().lower().rstrip(".")
    if not normalized_domain:
        raise ValueError("authority domain is required")
    group = project_split_identifiers_by_authority_domain(source, normalized_domain)
    return split_project_identifier_group(
        conn,
        source["project_key"],
        split_label=f"authority domain {normalized_domain}",
        group=group,
        discovery_source="split_from_authority_domain",
        seed_source="authority_domain_split",
        note=note,
        execute=execute,
    )


def split_project_tenant_group(
    conn: sqlite3.Connection,
    source_project_key: str,
    tenant: str,
    group: dict[str, list[str]],
) -> dict[str, Any]:
    result = split_project_identifier_group(
        conn,
        source_project_key,
        split_label=f"tenant {tenant}",
        group=group,
        discovery_source="split_from_source_config_audit",
        seed_source="source_config_audit_split",
        execute=True,
    )
    return {
        "source_project_key": result["source_project_key"],
        "target_project_key": result["target_project_key"],
        "tenant": tenant,
        "moved_identifiers": result["moved_identifiers"],
        "target_merge_recommendation": result.get("target_merge_recommendation", {}),
    }


def build_source_config_split_suggestions(conn: sqlite3.Connection, suffixes: list[str] | None = None) -> list[dict[str, Any]]:
    normalized_suffixes = sorted(
        {domain_policy_core.normalize_multi_tenant_suffix(item) for item in (suffixes or sorted(domain_policy_core.configured_multi_tenant_suffixes()))}
    )
    projects = [project_record.ensure_project_shape(project) for project in project_record.list_projects(conn)]
    suggestions: list[dict[str, Any]] = []
    for project in projects:
        for suffix in normalized_suffixes:
            groups = project_split_identifiers_by_tenant(project, suffix)
            if len(groups) < 2:
                continue
            tenant_groups: list[dict[str, Any]] = []
            for tenant_group_index, (tenant, group) in enumerate(sorted(groups.items()), start=1):
                virtual = virtual_tenant_project(project, tenant, group)
                comparison_projects = [
                    item for item in projects if item.get("project_key") != project.get("project_key")
                ] + [virtual]
                candidates = project_merge.compute_duplicate_candidates(conn, virtual, projects=comparison_projects)
                tenant_groups.append(
                    {
                        "tenant_group_index": tenant_group_index,
                        "tenant": tenant,
                        "identifiers": group,
                        "merge_candidates_after_split": candidates[:10],
                        "suggested_split_commands": split_commands_for_tenant(str(project["project_key"]), group),
                    }
                )
            suggestions.append(
                {
                    "source_project_key": project["project_key"],
                    "source_name": project.get("name", ""),
                    "trigger": f"custom_multi_tenant_suffix:{suffix}",
                    "tenant_groups": tenant_groups,
                    "shared_or_ambiguous_identifiers": {
                        "repo_urls": project_record.unique_list(
                            list(project.get("project_repository_urls", []))
                            + list(project.get("candidate_project_repository_urls", []))
                        ),
                    },
                    "requires_manual_confirmation": True,
                }
            )
    return suggestions


def source_config_suggestion_key(suggestion: dict[str, Any]) -> tuple[str, str]:
    return (
        str(suggestion.get("source_project_key") or ""),
        str(suggestion.get("trigger") or ""),
    )


def review_source_config_split_suggestions(
    conn: sqlite3.Connection,
    suffixes: list[str],
    *,
    initial_suggestions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    suggestions = (
        initial_suggestions
        if initial_suggestions is not None
        else build_source_config_split_suggestions(conn, suffixes=suffixes)
    )
    skipped: set[tuple[str, str]] = set()
    preferred_key: tuple[str, str] | None = None
    split_results: list[dict[str, Any]] = []
    quit_requested = False

    while True:
        available = [
            suggestion
            for suggestion in suggestions
            if source_config_suggestion_key(suggestion) not in skipped
        ]
        if preferred_key is not None:
            available.sort(key=lambda suggestion: source_config_suggestion_key(suggestion) != preferred_key)
        if not available:
            break

        suggestion = available[0]
        suggestion_key = source_config_suggestion_key(suggestion)
        print(json.dumps(
            {"remaining": len(available), **suggestion},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        answer = input("split which tenant group? [number]/[Enter]next/[q]uit: ").strip().lower()
        if answer in {"q", "quit"}:
            quit_requested = True
            break
        if not answer:
            skipped.add(suggestion_key)
            preferred_key = None
            continue
        try:
            selected_index = int(answer)
        except ValueError:
            print(json.dumps({"warning": "invalid_tenant_group_selection", "answer": answer}, ensure_ascii=False))
            continue
        tenant_groups = suggestion.get("tenant_groups", [])
        if selected_index < 1 or selected_index > len(tenant_groups):
            print(json.dumps({"warning": "tenant_group_selection_out_of_range", "answer": answer}, ensure_ascii=False))
            continue
        selected = tenant_groups[selected_index - 1]
        result = split_project_tenant_group(
            conn,
            str(suggestion.get("source_project_key") or ""),
            str(selected.get("tenant") or ""),
            dict(selected.get("identifiers") or {}),
        )
        split_results.append(result)
        print(json.dumps({"split_applied": True, **result}, ensure_ascii=False, indent=2, sort_keys=True))

        preferred_key = suggestion_key
        suggestions = build_source_config_split_suggestions(conn, suffixes=suffixes)

    remaining = [
        suggestion
        for suggestion in suggestions
        if source_config_suggestion_key(suggestion) not in skipped
    ]
    return {
        "split_count": len(split_results),
        "split_results": split_results,
        "skipped_count": len(skipped),
        "remaining_suggestion_count": len(remaining),
        "quit_requested": quit_requested,
    }


def resolve_single_project(
    conn: sqlite3.Connection,
    *,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
) -> dict[str, Any]:
    if project_key:
        project = project_record.fetch_project(conn, project_record.resolve_project_key_input(conn, project_key))
        if project is None:
            raise ValueError(f"project not found: {project_key}")
        return project
    if seed:
        detected_type = detect_seed_type(seed, seed_type)
        identifier_value = project_record.canonical_seed_value(detected_type, seed)
        found_project_key = project_record.find_project_key_by_identifier(conn, detected_type, identifier_value)
        if found_project_key is None:
            raise ValueError(f"project not found for seed: {seed}")
        project = project_record.fetch_project(conn, found_project_key)
        if project is None:
            raise ValueError(f"project not found: {found_project_key}")
        return project
    raise ValueError("source/target selector is required")


def resolve_project_key_for_seed_value(
    conn: sqlite3.Connection,
    seed_type: str,
    seed_value: str,
) -> tuple[str | None, str]:
    normalized_value = project_record.canonical_seed_value(seed_type, seed_value)
    target_project_key = project_record.find_project_key_by_identifier(conn, seed_type, normalized_value)
    match_kind = ""
    if target_project_key is None and seed_type == "nsid":
        target_project_key, match_kind = find_project_key_for_nsid_seed(all_projects(conn), normalized_value)
    return target_project_key, match_kind


def resolve_semble_project_key_for_seed_value(
    conn: sqlite3.Connection,
    seed_type: str,
    seed_value: str,
    *,
    projects: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str, str | None, str]:
    normalized_value = project_record.canonical_seed_value(seed_type, seed_value)
    projects = projects if projects is not None else all_projects(conn)
    indexed_matches = project_record.indexed_identifier_matches(conn, seed_type, normalized_value)
    exact_project_keys = {project_key for priority, project_key in indexed_matches if priority == 0}
    weak_project_keys = {project_key for priority, project_key in indexed_matches if priority > 0}
    if len(exact_project_keys) == 1:
        return next(iter(exact_project_keys)), "exact_identifier", None, ""
    if len(exact_project_keys) > 1:
        return None, "", None, ""
    if seed_type == "nsid":
        project_key, match_kind = find_project_key_for_nsid_seed(projects, normalized_value)
        if project_key and sync_match_is_auto_attach_safe(match_kind):
            return project_key, match_kind, None, ""
        if project_key:
            return None, "", project_key, match_kind
    if len(weak_project_keys) == 1:
        return None, "", next(iter(weak_project_keys)), "candidate_identifier"
    return None, "", None, ""
