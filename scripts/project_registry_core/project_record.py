"""Canonical project state normalization, persistence, and exact lookup."""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
import sqlite3
import time
import urllib.parse
import uuid
from dataclasses import asdict
from typing import Any, Callable

from project_registry_core import database as registry_database
from project_registry_core import domain_policy as domain_policy_core
from project_registry_core import evaluation as evaluation_core
from project_registry_core import identifiers as identifier_core
from project_registry_core import identifier_storage
from project_registry_core import relations as relation_core
from project_registry_core import repo_urls as repo_url_core
from project_registry_core import repository_analysis as repository_analysis_core
from project_registry_core import runtime_view
from project_registry_core import storage as investigation_storage
from project_registry_core import concurrency
from project_registry_core import atproto_usage
from project_registry_core import oauth_metadata
from project_registry_core.concurrency import StaleProjectWriteError
from project_registry_core import trust_graph as trust_graph_core

BUILTIN_LEXICON_PREFIXES = ("app.bsky.", "chat.bsky.", "com.atproto.")

TRACKING_STATES = {"active", "stable", "stalled", "out_of_scope", "archived", "ignore"}

PDSLS_HOSTS = {"pdsls.dev", "pds.ls"}

PDSLS_VIEWER_HOSTS = PDSLS_HOSTS | {"pdsls.pages.dev"}

LOOSE_NSID_PATTERN = r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*(?:\.[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)+"


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def builtin_lexicon_prefixes() -> tuple[str, ...]:
    return BUILTIN_LEXICON_PREFIXES


def load_json(value: str) -> Any:
    return json.loads(value) if value else None


def dump_json(value: Any, *, sort_keys: bool = True) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=sort_keys)

SERVICE_STATUS_VALUES = {"unknown", "unverified", "none", "dead", "live", "private", "unreleased", "beta", "invite_only"}

SERVICE_STATUS_CONFIDENCE_VALUES = {"none", "low", "medium", "high"}

TECHNICAL_INTEREST_SCORES = {"none": 0, "notable": 40, "high": 70, "exceptional": 90}

TECHNICAL_INTEREST_REASONS = {
    "novel_design_pattern",
    "protocol_gap_exposed",
    "interoperability_experiment",
    "unusual_architecture",
    "implementation_reference",
}


def compact_grouped_payload(group_fields: dict[str, tuple[str, ...]], source: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for group_name, field_names in group_fields.items():
        group_payload: dict[str, Any] = {}
        for field_name in field_names:
            value = source.get(field_name)
            if value in ("", None, [], {}, ()):
                continue
            group_payload[field_name] = value
        if group_payload:
            payload[group_name] = group_payload
    return payload


def flatten_grouped_payload(grouped_payload: Any) -> dict[str, Any]:
    if not isinstance(grouped_payload, dict):
        return {}
    flattened: dict[str, Any] = {}
    for value in grouped_payload.values():
        if not isinstance(value, dict):
            continue
        for field_name, field_value in value.items():
            flattened[str(field_name)] = field_value
    return flattened

PROJECT_SCALAR_FIELDS = (
    "project_key",
    "name",
    "website_url",
    "service_status",
    "tracking_state",
    "first_seen_at",
    "last_seen_at",
    "last_enriched_at",
)

PROJECT_STORAGE_SCALAR_FIELDS = PROJECT_SCALAR_FIELDS + ("introduced_url",)

PROJECT_CATEGORY_VALUES = frozenset({
    "project", "lexicon", "infra_tool", "name_only", "library", "personal_tool", "reject",
})

OPS_JSON_GROUP_FIELDS: dict[str, tuple[str, ...]] = {
    "ops": (
        "status",
        "category",
        "app_category",
        "project_state",
        "ownership_signal",
        "tracking_note",
        "dismissed_review_kinds",
        "manual_review_recommended",
        "manual_review_kind",
        "manual_review_priority",
        "manual_review_reason",
        "promotion_ready",
        "promotion_blockers",
        "last_promoted_at",
        "last_llm_review_at",
        "duplicate_candidates",
        "merge_recommendation",
        "separate_project_keys",
        "blocked_identifiers",
        "deferred_identifiers",
        "promotion_deferrals",
        "promotion_attention",
        "collection_observations",
        "promotion_events",
        "last_run_errors",
        "service_status_confidence",
        "service_status_evidence",
        "atproto_auth_only",
        "atproto_contribution_level",
        "atproto_contribution_score",
        "atproto_contribution_evidence",
        "lexicon_scope",
        "lexicon_scope_evidence",
        "technical_interest_level",
        "technical_interest_score",
        "technical_interest_reasons",
        "technical_interest_summary",
    ),
    "evidence": (
        "discovery_sources",
        "system_notes",
        "evidence_urls",
        "investigation_queries",
        "investigation_log",
        "input_nsids",
        "resolved_nsids",
        "publication_schemas",
        "publication_review",
        "lexicon_resolution_entries",
        "lexicons",
        "lexicon_count",
        "lexicon_schema_samples",
        "recent_record_samples",
        "record_creators",
        "authority_domains",
        "external_lexicons",
        "features",
        "inspired_by",
        "name_candidates",
        "summary_candidates",
        "tagline_candidates",
        "service_urls",
        "project_site_urls",
        "project_site_url_candidates",
        "resource_url_records",
        "service_url_candidates",
        "failed_service_urls",
        "supported_service_urls",
        "project_repository_urls",
        "candidate_project_repository_urls",
        "developer_accounts",
        "candidate_developer_accounts",
        "bluesky_handles",
        "tangled_handles",
        "source_repo_records",
        "repository_analysis_results",
        "oauth_client_metadata",
        "llm_source",
        "llm_output",
        "classification_reason",
        "exclude_reason",
        "watch_reason",
        "editorial",
        "confidence",
        "relation_evidence",
        "trust_graph",
        "service_verification_results",
        "url_aliases",
    ),
}


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


def unique_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output

APP_LINK_RESOURCE_PATH_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("webManifest", ("/manifest.json", "/manifest.webmanifest", "/site.webmanifest")),
    ("changelog", ("/changelog", "/changes", "/releases")),
    ("privacyPolicy", ("/privacy", "/privacy-policy", "/policies/privacy")),
    ("termsOfService", ("/terms", "/terms-of-service", "/tos")),
    ("status", ("/status", "/uptime", "/health")),
    ("support", ("/support", "/help", "/contact", "/faq")),
    ("docs", ("/docs", "/doc", "/guide", "/guides", "/reference")),
)

APP_LINK_SERVICE_HOST_PREFIXES = {"app", "web", "client"}

APP_LINK_SERVICE_PATH_PREFIXES = (
    "/app",
    "/login",
    "/signin",
    "/sign-in",
    "/signup",
    "/sign-up",
    "/dashboard",
)

RESOURCE_URL_ROLES = {
    "unknown", "docs", "blog", "support", "status", "privacyPolicy", "termsOfService",
    "webManifest", "changelog", "appStore", "playStore", "fdroid", "distribution",
}

RESOURCE_URL_CONFIDENCE_VALUES = {"candidate", "confirmed"}

RESOURCE_URL_TOTAL_BUDGET = 32

RESOURCE_URL_ROLE_BUDGETS = {
    "docs": 8,
    "blog": 4,
    "support": 4,
    "status": 2,
    "privacyPolicy": 2,
    "termsOfService": 2,
    "webManifest": 2,
    "changelog": 3,
    "appStore": 3,
    "playStore": 3,
    "fdroid": 3,
    "distribution": 4,
    "unknown": 3,
}

RESOURCE_URL_HOST_ROLE_BUDGETS = {
    "docs": 4,
    "blog": 2,
    "support": 3,
    "status": 1,
    "privacyPolicy": 1,
    "termsOfService": 1,
    "webManifest": 1,
    "changelog": 2,
    "appStore": 2,
    "playStore": 2,
    "fdroid": 2,
    "distribution": 3,
    "unknown": 2,
}

RESOURCE_URL_ROLE_RANK = {
    "appStore": 0, "playStore": 1, "fdroid": 2, "distribution": 3,
    "docs": 10, "support": 11,
    "changelog": 20, "status": 21, "privacyPolicy": 22, "termsOfService": 23, "webManifest": 24,
    "blog": 30, "unknown": 99,
}

RESOURCE_URL_SOURCE_RANK_PREFIXES = {
    "service_page": 0,
    "service_follow_up": 1,
    "resource_page": 2,
    "resource_follow_up": 3,
    "service_url_cleanup": 4,
}

RESOURCE_URL_LOW_VALUE_PATH_SEGMENTS = {
    "archive",
    "archives",
    "author",
    "category",
    "categories",
    "feed",
    "feeds",
    "list",
    "lists",
    "page",
    "pagination",
    "post",
    "posts",
    "profile",
    "profiles",
    "search",
    "tag",
    "tags",
    "user",
    "users",
}


def normalize_app_link_role(value: str | None) -> str:
    return str(value or "").strip()


def normalize_app_link_official(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    lowered = str(value or "").strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None


def append_or_merge_app_link(links: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    uri = identifier_core.normalize_url(str(entry.get("uri") or ""))
    role = normalize_app_link_role(entry.get("role"))
    if not uri or not role:
        return
    normalized_entry: dict[str, Any] = {"uri": uri, "role": role}
    label = str(entry.get("label") or "").strip()
    if label:
        normalized_entry["label"] = label
    official = normalize_app_link_official(entry.get("official"))
    if official is not None:
        normalized_entry["official"] = official
    key = (uri, role)
    for existing in links:
        if (str(existing.get("uri") or ""), str(existing.get("role") or "")) != key:
            continue
        if "label" not in existing and "label" in normalized_entry:
            existing["label"] = normalized_entry["label"]
        if "official" not in existing and "official" in normalized_entry:
            existing["official"] = normalized_entry["official"]
        return
    links.append(normalized_entry)


def normalize_app_links(entries: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        uri = identifier_core.normalize_url(str(item.get("uri") or item.get("url") or ""))
        role = normalize_app_link_role(item.get("role"))
        if not uri or not role:
            continue
        normalized: dict[str, Any] = {"uri": uri, "role": role}
        label = str(item.get("label") or "").strip()
        if label:
            normalized["label"] = label
        official = normalize_app_link_official(item.get("official"))
        if official is not None:
            normalized["official"] = official
        append_or_merge_app_link(output, normalized)
    return output


def app_link_role_from_resource_url(url: str) -> str:
    normalized = identifier_core.normalize_url(url)
    if not normalized:
        return ""
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path.lower().rstrip("/")
    if host == "apps.apple.com" and "/app/" in f"{path}/":
        return "appStore"
    if host == "play.google.com" and path == "/store/apps/details":
        return "playStore"
    if host in {"f-droid.org", "www.f-droid.org"} and path.startswith("/packages/"):
        return "fdroid"
    if host.endswith(".itch.io") and path not in {"", "/"}:
        return "distribution"
    if host.startswith("docs."):
        return "docs"
    if host.startswith("support.") or host.startswith("help."):
        return "support"
    if host.startswith("status."):
        return "status"
    for role, prefixes in APP_LINK_RESOURCE_PATH_HINTS:
        if any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes):
            return role
    return ""


def app_link_role_from_service_url(url: str, website_url: str = "") -> str:
    normalized = identifier_core.normalize_url(url)
    if not normalized:
        return ""
    if normalized == identifier_core.normalize_url(website_url):
        return ""
    if app_link_role_from_resource_url(normalized):
        return ""
    if repo_url_core.canonical_repo_url(normalized):
        return ""
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path.lower().rstrip("/")
    if host.split(".", 1)[0] in APP_LINK_SERVICE_HOST_PREFIXES:
        return "webApp"
    if any(path == prefix or path.startswith(f"{prefix}/") for prefix in APP_LINK_SERVICE_PATH_PREFIXES):
        return "webApp"
    if website_url and domain_policy_core.is_same_site_family(website_url, normalized):
        website_host = urllib.parse.urlparse(identifier_core.normalize_url(website_url)).netloc.lower()
        host_prefix = host.split(".", 1)[0]
        if host != website_host and host_prefix not in {"www", "docs", "blog", "status", "support", "help"} and path in {"", "/"}:
            return "webApp"
    return ""


def build_project_app_links(project: dict[str, Any]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []

    website_url = identifier_core.normalize_url(str(project.get("website_url") or ""))
    if website_url:
        append_or_merge_app_link(links, {"uri": website_url, "role": "website", "official": True})

    project_repository_url = repo_url_core.canonical_repo_url(str(project.get("project_repository_url") or ""))
    if not project_repository_url:
        project_repository_urls = repo_url_core.normalize_repo_collection(project.get("project_repository_urls", []))
        if project_repository_urls:
            project_repository_url = project_repository_urls[0]
    if project_repository_url:
        append_or_merge_app_link(links, {"uri": project_repository_url, "role": "repository", "official": True})

    for url in unique_list([identifier_core.normalize_url(str(item)) for item in project.get("service_urls", []) if identifier_core.normalize_url(str(item))]):
        role = app_link_role_from_service_url(url, website_url=website_url)
        if role:
            append_or_merge_app_link(links, {"uri": url, "role": role, "official": True})

    for record in normalize_resource_url_records(project.get("resource_url_records", [])):
        role = str(record.get("role") or "")
        if record.get("confidence") == "confirmed" and role not in {"", "unknown", "blog"}:
            append_or_merge_app_link(links, {"uri": record["url"], "role": role, "official": True})

    return links


def normalize_developer_accounts(entries: list[Any]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in entries:
        handle = ""
        did = ""
        source = ""
        if isinstance(item, dict):
            handle = str(item.get("handle") or "").strip()
            did = str(item.get("did") or "").strip()
            source = str(item.get("source") or "").strip()
        elif isinstance(item, str):
            handle = str(item).strip()
        if DID_RE.fullmatch(handle):
            if not did or did.lower() == handle.lower():
                did = handle
            handle = ""
        if handle:
            handle = handle.rstrip(".")
        if handle and not ATPROTO_HANDLE_RE.fullmatch(handle):
            handle = ""
        if handle:
            handle = handle.lower()
        if not handle and not did:
            continue
        key = (handle.lower(), did.lower(), source)
        if key in seen:
            continue
        seen.add(key)
        account: dict[str, str] = {}
        if handle:
            account["handle"] = handle
        if did:
            account["did"] = did
        if source:
            account["source"] = source
        output.append(account)
    return output


def normalize_project_service_candidate_url(project: dict[str, Any], url: str) -> str | None:
    exact = identifier_core.normalize_url(url)
    if any(claim["url"] == exact for claim in repository_analysis_core.service_publications(project)):
        return normalize_confirmed_service_url(url)
    return normalize_service_candidate_url(url)


def service_url_candidate_values(project: dict[str, Any]) -> list[str]:
    candidates = list(project.get("service_url_candidates", []))
    return unique_list(
        [
            normalized
            for item in candidates
            if (normalized := normalize_project_service_candidate_url(project, str(item or "")))
        ]
    )


def set_service_url_candidate_values(project: dict[str, Any], values: list[str]) -> None:
    candidates = unique_list(
        [
            normalized
            for item in values
            if (normalized := normalize_project_service_candidate_url(project, str(item or "")))
        ]
    )
    project["service_url_candidates"] = candidates


def add_unique(values: list[str], value: str) -> list[str]:
    return unique_list(values + [value])


def generate_project_key(existing_keys: set[str] | None = None) -> str:
    reserved = existing_keys if existing_keys is not None else set()
    while True:
        candidate = f"proj:{uuid.uuid4().hex[:12]}"
        if candidate not in reserved:
            reserved.add(candidate)
            return candidate


def unique_manual_seeds(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    output: list[dict[str, Any]] = []
    for value in values:
        seed_type = str(value.get("type") or "")
        raw_seed_value = str(value.get("value") or "")
        seed_value = raw_seed_value
        if seed_type == "service_url":
            extracted_nsid = extract_collection_nsid(raw_seed_value)
            if extracted_nsid:
                seed_type = "nsid"
                seed_value = extracted_nsid
            else:
                seed_value = canonical_seed_value(seed_type, raw_seed_value)
        elif seed_type in {"repo_url", "nsid", "did", "handle", "domain"}:
            seed_value = canonical_seed_value(seed_type, raw_seed_value)
        key = (seed_type, seed_value)
        if not seed_type or not seed_value or key in seen:
            continue
        seen.add(key)
        normalized_entry = dict(value)
        normalized_entry["type"] = seed_type
        normalized_entry["value"] = seed_value
        output.append(normalized_entry)
    return output


def set_project_seed_identity(project: dict[str, Any], seed_type: str, seed_value: str) -> None:
    project["seed_type"] = seed_type
    project["seed_value"] = canonical_seed_value(seed_type, seed_value)


def refresh_project_seed_identity(project: dict[str, Any]) -> None:
    manual_seeds = [item for item in project.get("manual_seeds", []) if isinstance(item, dict)]
    for preferred_type in ("nsid", "repo_url", "service_url"):
        for item in manual_seeds:
            if str(item.get("type") or "") == preferred_type and str(item.get("value") or "").strip():
                set_project_seed_identity(project, preferred_type, str(item.get("value") or ""))
                return
    nsid = next(iter(unique_list(list(project.get("resolved_nsids", [])) + list(project.get("input_nsids", [])))), "")
    if nsid:
        set_project_seed_identity(project, "nsid", str(nsid))
        return
    repo_url = next(iter(project_repository_url_values(project)), "")
    if repo_url:
        set_project_seed_identity(project, "repo_url", str(repo_url))
        return
    service_url = next(iter(unique_list(project.get("service_urls", []))), "")
    if service_url:
        set_project_seed_identity(project, "service_url", str(service_url))
        return
    project["seed_type"] = "unknown"
    project["seed_value"] = ""


def normalize_extracted_nsid(nsid: str) -> str:
    return identifier_core.normalize_nsid(nsid)


def extract_loose_collection_nsid(value: str) -> str:
    cleaned = identifier_core.normalize_nsid(value)
    if re.fullmatch(LOOSE_NSID_PATTERN, cleaned):
        return normalize_extracted_nsid(cleaned)
    return ""


def extract_collection_nsid(value: str) -> str:
    raw = urllib.parse.unquote(str(value or "").strip())
    if not raw:
        return ""
    if raw.startswith("at://"):
        segments = [segment for segment in raw.removeprefix("at://").split("/") if segment]
        if len(segments) >= 2:
            extracted = extract_loose_collection_nsid(segments[1])
            if extracted:
                return extracted
        return ""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower() in PDSLS_VIEWER_HOSTS:
        path = urllib.parse.unquote(parsed.path.lstrip("/"))
        if path.startswith("at://"):
            return extract_collection_nsid(path)
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) >= 3 and segments[0] == "at" and segments[1].startswith("did:"):
            extracted = extract_loose_collection_nsid(segments[2])
            if extracted:
                return extracted
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower() == "lexicon.garden":
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) >= 3 and segments[0] == "lexicon":
            extracted = extract_loose_collection_nsid(segments[2])
            if extracted:
                return extracted
    extracted = extract_loose_collection_nsid(raw)
    if extracted:
        return extracted
    return ""


def is_shared_official_identity_host(host: str) -> bool:
    lowered = str(host or "").strip().lower().rstrip(".")
    return bool(lowered and domain_policy_core.root_domain(lowered) in SHARED_OFFICIAL_IDENTITY_ROOTS)


def canonical_seed_value(seed_type: str, seed_value: str) -> str:
    if seed_type == "nsid":
        extracted = extract_collection_nsid(seed_value)
        return extracted or identifier_core.normalize_nsid(seed_value)
    if seed_type == "repo_url":
        return repo_url_core.canonical_repo_url(seed_value)
    if seed_type in {"handle", "domain"}:
        return str(seed_value or "").strip().lower().rstrip(".")
    if seed_type.endswith("_url"):
        return identifier_core.normalize_url(seed_value)
    return seed_value


def lexicon_namespace_for_project(nsid: str) -> str:
    namespace = namespace_from_collection_nsid(nsid)
    if namespace:
        return namespace
    return identifier_core.normalize_nsid(nsid)


def common_nsid_prefix(nsids: list[str]) -> str:
    normalized = [identifier_core.normalize_nsid(nsid) for nsid in nsids if identifier_core.normalize_nsid(nsid)]
    if not normalized:
        return ""
    split_items = [item.split(".") for item in normalized]
    prefix: list[str] = []
    for index in range(min(len(item) for item in split_items)):
        token = split_items[0][index]
        if all(item[index] == token for item in split_items[1:]):
            prefix.append(token)
        else:
            break
    return ".".join(prefix)


def namespace_from_collection_nsid(nsid: str) -> str:
    normalized = identifier_core.normalize_nsid(nsid)
    parts = [part for part in normalized.split(".") if part]
    if len(parts) < 3:
        return ""
    return ".".join(parts[:-1])


def default_project_record(project_key: str, seed_type: str, seed_value: str) -> dict[str, Any]:
    initial_owned_nsids = (
        [canonical_seed_value("nsid", seed_value)]
        if seed_type == "nsid" and identifier_core.is_valid_nsid(canonical_seed_value("nsid", seed_value))
        else []
    )
    return {
        "project_key": project_key,
        runtime_view.RUNTIME_PROJECT_REVISION: 0,
        "status": "draft",
        "category": "",
        "app_category": "",
        "name": "",
        "name_candidates": [],
        "tagline": "",
        "tagline_is_provisional": False,
        "summary": "",
        "summary_candidates": [],
        "tagline_candidates": [],
        "features": [],
        "inspired_by": [],
        "tags": [],
        "platforms": [],
        "links": [],
        "project_state": "",
        "brand_did": "",
        "ownership_signal": "unknown",
        "introduced_url": "",
        "tracking_state": "active",
        "tracking_note": "",
        "system_notes": [],
        "discovery_sources": [],
        "evidence_urls": [],
        "input_nsids": [],
        "resolved_nsids": initial_owned_nsids,
        "lexicon_resolution_entries": [],
        "recorded_lexicon_namespaces": [],
        "lexicons": [],
        "lexicon_count": 0,
        "lexicon_schema_samples": [],
        "recent_record_samples": [],
        "record_creators": [],
        "authority_domains": [],
        "external_lexicons": [],
        "website_url": "",
        "service_urls": [],
        "project_site_urls": [],
        "project_site_url_candidates": [],
        "resource_url_records": [],
        "service_url_candidates": [],
        "failed_service_urls": [],
        "supported_service_urls": [],
        "service_status": "unknown",
        "service_status_confidence": "none",
        "service_status_evidence": [],
        "atproto_auth_only": False,
        "atproto_usage_decisions": [],
        "atproto_contribution_level": "unknown",
        "atproto_contribution_score": 0,
        "atproto_contribution_evidence": [],
        "lexicon_scope": "unknown",
        "lexicon_scope_evidence": [],
        "technical_interest_level": "none",
        "technical_interest_score": 0,
        "technical_interest_reasons": [],
        "technical_interest_summary": "",
        "project_repository_url": "",
        "project_repository_urls": [],
        "candidate_project_repository_urls": [],
        "source_repo_records": [],
        "repository_analysis_results": [],
        "oauth_client_metadata": [],
        "bluesky_handles": [],
        "tangled_handles": [],
        "developer_accounts": [],
        "candidate_developer_accounts": [],
        "investigation_queries": [],
        "investigation_log": [],
        "classification_reason": "",
        "exclude_reason": "",
        "watch_reason": "",
        "dismissed_review_kinds": [],
        "manual_review_recommended": False,
        "manual_review_kind": "other",
        "manual_review_priority": "none",
        "manual_review_reason": "",
        "confidence": 0.0,
        "llm_source": {},
        "editorial": {},
        "llm_output": {},
        "last_llm_review_at": "",
        "promotion_ready": False,
        "promotion_blockers": [],
        "last_promoted_at": "",
        "promotion_events": [],
        "duplicate_candidates": [],
        "merge_recommendation": {},
        "separate_project_keys": [],
        "blocked_identifiers": [],
        "deferred_identifiers": [],
        "confirmed_identifiers": [],
        "promotion_deferrals": [],
        "promotion_priority_adjustments": [],
        "collection_observations": [],
        "notes": "",
        "last_run_errors": [],
        "manual_seeds": [],
        "seed_type": seed_type,
        "seed_value": seed_value,
        "first_seen_at": "",
        "last_seen_at": "",
        "last_enriched_at": "",
    }


def normalize_tracking_state(value: str | None) -> str:
    tracking_state = str(value or "active").strip().lower()
    return tracking_state if tracking_state in TRACKING_STATES else "active"


def normalize_service_status(value: str | None) -> str:
    service_status = str(value or "unknown").strip().lower()
    return service_status if service_status in SERVICE_STATUS_VALUES else "unknown"


def normalize_service_status_confidence(value: str | None) -> str:
    confidence = str(value or "none").strip().lower()
    return confidence if confidence in SERVICE_STATUS_CONFIDENCE_VALUES else "none"


def compute_atproto_contribution(project: dict[str, Any]) -> dict[str, Any]:
    return evaluation_core.compute_atproto_contribution(project)


def apply_atproto_contribution_state(project: dict[str, Any]) -> dict[str, Any]:
    project.update(compute_atproto_contribution(project))
    return project


def apply_technical_interest_state(project: dict[str, Any]) -> dict[str, Any]:
    level = str(project.get("technical_interest_level") or "none").strip().lower()
    if level not in TECHNICAL_INTEREST_SCORES:
        level = "none"
    project["technical_interest_level"] = level
    project["technical_interest_score"] = TECHNICAL_INTEREST_SCORES[level]
    project["technical_interest_reasons"] = unique_list(
        [
            str(item).strip().lower()
            for item in project.get("technical_interest_reasons", [])
            if str(item).strip().lower() in TECHNICAL_INTEREST_REASONS
        ]
    )
    project["technical_interest_summary"] = normalize_ja_en_mixed_spacing(
        str(project.get("technical_interest_summary") or "").strip()
    )
    return project


def normalize_blocked_identifier_value(identifier_type: str, value: str) -> str:
    if identifier_type == "service_url":
        return identifier_core.normalize_url(value)
    if identifier_type == "repo_url":
        return repo_url_core.canonical_repo_url(value)
    if identifier_type == "nsid":
        return identifier_core.normalize_nsid(value)
    if identifier_type in {"handle", "domain"}:
        return str(value or "").strip().lower().rstrip(".")
    return str(value or "").strip()


def normalize_blocked_identifiers(entries: list[Any]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        identifier_type = str(item.get("type") or "").strip()
        if identifier_type not in IDENTIFIER_DECISION_TYPES:
            continue
        normalized_value = normalize_blocked_identifier_value(identifier_type, str(item.get("value") or ""))
        if not normalized_value:
            continue
        key = (identifier_type, normalized_value)
        if key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "type": identifier_type,
                "value": normalized_value,
                "role": str(item.get("role") or "").strip(),
                "note": str(item.get("note") or "").strip(),
                "added_at": str(item.get("added_at") or ""),
            }
        )
    return output

IDENTIFIER_DECISION_TYPES = {"service_url", "repo_url", "nsid", "account", "did", "handle", "domain"}


def normalize_deferred_identifiers(entries: list[Any]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    now = dt.datetime.now(dt.timezone.utc)
    for item in entries:
        if not isinstance(item, dict):
            continue
        identifier_type = str(item.get("type") or "").strip()
        if identifier_type not in IDENTIFIER_DECISION_TYPES:
            continue
        normalized_value = normalize_blocked_identifier_value(identifier_type, str(item.get("value") or ""))
        expires_at = str(item.get("expires_at") or "").strip()
        expires = parse_iso(expires_at)
        if not normalized_value or expires is None or expires <= now:
            continue
        key = (identifier_type, normalized_value)
        if key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "type": identifier_type,
                "value": normalized_value,
                "role": str(item.get("role") or "").strip(),
                "note": str(item.get("note") or "").strip(),
                "added_at": str(item.get("added_at") or ""),
                "expires_at": expires.isoformat(),
            }
        )
    return output


def normalize_confirmed_identifiers(entries: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        identifier_type = str(item.get("type") or "").strip()
        if identifier_type not in IDENTIFIER_DECISION_TYPES:
            continue
        normalized_value = normalize_blocked_identifier_value(identifier_type, str(item.get("value") or ""))
        if not normalized_value:
            continue
        cleared_at = str(item.get("cleared_at") or "").strip()
        normalized = {
            **copy.deepcopy(item),
            "type": identifier_type,
            "value": normalized_value,
            "role": str(item.get("role") or "").strip(),
            "note": str(item.get("note") or "").strip(),
            "source": str(item.get("source") or "").strip(),
            "added_at": str(item.get("added_at") or ""),
            "cleared_at": cleared_at,
            "registration_origin": "1" if bool(item.get("registration_origin")) else "",
            "source_ref": str(item.get("source_ref") or "").strip(),
            "policy_version": str(item.get("policy_version") or "").strip(),
            "input_fingerprint": str(item.get("input_fingerprint") or "").strip(),
        }
        key = dump_json(normalized)
        if key not in seen:
            seen.add(key)
            output.append(normalized)
    return output


def normalize_promotion_deferrals(entries: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "").strip()
        expires = parse_iso(str(item.get("expires_at") or "").strip())
        if expires is None:
            continue
        normalized = {
            "reason": reason,
            "added_at": str(item.get("added_at") or ""),
            "expires_at": expires.isoformat(),
        }
        if item.get("fingerprint"):
            normalized["fingerprint"] = str(item["fingerprint"])
        if isinstance(item.get("legacy_review"), dict):
            normalized["legacy_review"] = copy.deepcopy(item["legacy_review"])
        cleared_at = str(item.get("cleared_at") or "").strip()
        if cleared_at:
            normalized["cleared_at"] = cleared_at
        output.append(normalized)
    return output


def normalize_promotion_priority_adjustments(entries: list[Any]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        normalized = {
            "reason": str(item.get("reason") or "").strip(),
            "added_at": str(item.get("added_at") or ""),
        }
        cleared_at = str(item.get("cleared_at") or "").strip()
        if cleared_at:
            normalized["cleared_at"] = cleared_at
        output.append(normalized)
    return output


def ensure_project_shape(project: dict[str, Any]) -> dict[str, Any]:
    defaults = default_project_record(
        str(project.get("project_key") or "unknown"),
        str(project.get("seed_type") or "unknown"),
        str(project.get("seed_value") or ""),
    )
    defaults.update(project)
    defaults["input_nsids"] = unique_list([identifier_core.normalize_nsid(str(item)) for item in defaults.get("input_nsids", []) if item])
    defaults["resolved_nsids"] = unique_list([identifier_core.normalize_nsid(str(item)) for item in defaults.get("resolved_nsids", []) if item])
    defaults["service_status"] = normalize_service_status(defaults.get("service_status"))
    defaults["service_status_confidence"] = normalize_service_status_confidence(defaults.get("service_status_confidence"))
    defaults["service_status_evidence"] = unique_list([str(item).strip() for item in defaults.get("service_status_evidence", []) if str(item).strip()])
    defaults["atproto_auth_only"] = bool(defaults.get("atproto_auth_only"))
    defaults["atproto_usage_decisions"] = atproto_usage.normalize_decisions(defaults.get("atproto_usage_decisions"))
    defaults["status"] = str(defaults.get("status") or "draft")
    defaults["dismissed_review_kinds"] = unique_list([str(item).strip() for item in defaults.get("dismissed_review_kinds", []) if str(item).strip()])
    defaults["name_candidates"] = [dict(item) for item in defaults.get("name_candidates", []) if isinstance(item, dict)]
    defaults["tagline_is_provisional"] = bool(defaults.get("tagline_is_provisional"))
    defaults["app_category"] = str(defaults.get("app_category") or "")
    defaults["project_state"] = str(defaults.get("project_state") or "")
    defaults["brand_did"] = str(defaults.get("brand_did") or "")
    defaults["ownership_signal"] = str(defaults.get("ownership_signal") or "unknown")
    defaults["tags"] = unique_list([str(item).strip() for item in defaults.get("tags", []) if str(item).strip()])
    defaults["platforms"] = unique_list([str(item).strip() for item in defaults.get("platforms", []) if str(item).strip()])
    defaults["links"] = normalize_app_links(list(defaults.get("links", []) or []))
    defaults["introduced_url"] = identifier_core.normalize_url(str(defaults.get("introduced_url") or "")) if defaults.get("introduced_url") else ""
    defaults["tracking_state"] = normalize_tracking_state(str(defaults.get("tracking_state") or "active"))
    defaults["tracking_note"] = str(defaults.get("tracking_note") or "").strip()
    defaults["system_notes"] = unique_list([str(item).strip() for item in defaults.get("system_notes", []) if str(item).strip()])
    defaults["manual_review_recommended"] = bool(defaults.get("manual_review_recommended"))
    defaults["manual_review_kind"] = str(defaults.get("manual_review_kind") or "other").strip().lower() or "other"
    if defaults["manual_review_kind"] not in {
        "other",
        "editorial_refresh",
        "deep_source_material",
        "service_status_manual_check",
        "identity_resolution",
        "category_recheck",
    }:
        defaults["manual_review_kind"] = "other"
    defaults["manual_review_priority"] = str(defaults.get("manual_review_priority") or "none").strip().lower() or "none"
    if defaults["manual_review_priority"] not in {"none", "low", "medium", "high"}:
        defaults["manual_review_priority"] = "none"
    defaults["manual_review_reason"] = normalize_ja_en_mixed_spacing(str(defaults.get("manual_review_reason") or "").strip())
    defaults["promotion_ready"] = bool(defaults.get("promotion_ready"))
    defaults["promotion_blockers"] = unique_list([str(item) for item in defaults.get("promotion_blockers", []) if item])
    defaults["last_promoted_at"] = str(defaults.get("last_promoted_at") or "")
    defaults["promotion_events"] = [dict(item) for item in defaults.get("promotion_events", []) if isinstance(item, dict)]
    defaults["llm_source"] = dict(defaults.get("llm_source") or {})
    if isinstance(defaults["llm_source"].get("evidence"), dict):
        evidence = dict(defaults["llm_source"]["evidence"])
        evidence["developer_accounts"] = normalize_developer_accounts(evidence.get("developer_accounts", []))
        evidence["candidate_developer_accounts"] = normalize_developer_accounts(evidence.get("candidate_developer_accounts", []))
        defaults["llm_source"] = dict(defaults["llm_source"], evidence=evidence)
    defaults["llm_output"] = dict(defaults.get("llm_output") or {})
    defaults["editorial"] = dict(defaults.get("editorial") or {})
    defaults["duplicate_candidates"] = list(defaults.get("duplicate_candidates") or [])
    defaults["merge_recommendation"] = dict(defaults.get("merge_recommendation") or {})
    defaults["separate_project_keys"] = unique_list(
        [
            str(item).strip()
            for item in defaults.get("separate_project_keys", [])
            if str(item).strip() and str(item).strip() != str(defaults.get("project_key") or "")
        ]
    )
    defaults["blocked_identifiers"] = normalize_blocked_identifiers(list(defaults.get("blocked_identifiers") or []))
    defaults["deferred_identifiers"] = normalize_deferred_identifiers(list(defaults.get("deferred_identifiers") or []))
    defaults["confirmed_identifiers"] = normalize_confirmed_identifiers(list(defaults.get("confirmed_identifiers") or []))
    defaults["promotion_deferrals"] = normalize_promotion_deferrals(list(defaults.get("promotion_deferrals") or []))
    defaults["promotion_priority_adjustments"] = normalize_promotion_priority_adjustments(
        list(defaults.get("promotion_priority_adjustments") or [])
    )
    defaults["collection_observations"] = [
        {key: value for key, value in item.items() if key != "members"}
        for item in defaults.get("collection_observations", [])
        if isinstance(item, dict)
    ]
    defaults["lexicon_schema_samples"] = [dict(item) for item in defaults.get("lexicon_schema_samples", []) if isinstance(item, dict)]
    defaults["recent_record_samples"] = [dict(item) for item in defaults.get("recent_record_samples", []) if isinstance(item, dict)]
    defaults["investigation_log"] = list(defaults.get("investigation_log") or [])
    defaults["investigation_queries"] = unique_list([str(item) for item in defaults.get("investigation_queries", []) if item])
    defaults["last_run_errors"] = unique_list([str(item) for item in defaults.get("last_run_errors", []) if item])
    defaults["relation_evidence"] = relation_core.normalize_relation_records(defaults.get("relation_evidence", []))
    oauth_metadata.reconcile_relations(defaults)
    defaults["service_verification_results"] = [
        dict(item) for item in defaults.get("service_verification_results", []) if isinstance(item, dict)
    ]
    defaults["url_aliases"] = [dict(item) for item in defaults.get("url_aliases", []) if isinstance(item, dict)]
    defaults["manual_seeds"] = unique_manual_seeds(defaults.get("manual_seeds", []))
    defaults["project_repository_url"] = repo_url_core.canonical_repo_url(str(defaults.get("project_repository_url") or ""))
    defaults["project_repository_urls"] = repo_url_core.normalize_repo_collection(defaults.get("project_repository_urls", []))
    if defaults["project_repository_url"] and defaults["project_repository_url"] not in defaults["project_repository_urls"]:
        defaults["project_repository_urls"] = [defaults["project_repository_url"], *defaults["project_repository_urls"]]
    defaults["candidate_project_repository_urls"] = repo_url_core.normalize_repo_collection(defaults.get("candidate_project_repository_urls", []))
    defaults["source_repo_records"] = normalize_source_repo_records(defaults.get("source_repo_records", []))
    defaults["repository_analysis_results"] = repository_analysis_core.normalize_results(
        defaults.get("repository_analysis_results", [])
    )
    sync_source_repo_provider_views(defaults)
    set_service_url_candidate_values(defaults, service_url_candidate_values(defaults))
    defaults["developer_accounts"] = normalize_developer_accounts(list(defaults.get("developer_accounts") or []))
    defaults["candidate_developer_accounts"] = normalize_developer_accounts(list(defaults.get("candidate_developer_accounts") or []))
    if defaults["status"] == "candidate":
        defaults["status"] = "draft"
    defaults["lexicon_namespaces"] = unique_list([identifier_core.normalize_nsid(str(item)) for item in defaults.get("lexicon_namespaces", []) if str(item).strip()])
    if runtime_view.is_complete(defaults):
        defaults["recorded_lexicon_namespaces"] = unique_list(
            [identifier_core.normalize_nsid(str(item)) for item in defaults.get("recorded_lexicon_namespaces", []) if str(item).strip()]
        )
    else:
        defaults["recorded_lexicon_namespaces"] = compute_recorded_lexicon_namespaces(defaults)
    defaults["lexicon_count"] = len(defaults.get("lexicons", []))
    defaults["external_lexicons"] = unique_list(
        [identifier_core.normalize_nsid(str(item)) for item in defaults.get("external_lexicons", []) if item]
        + compute_external_lexicons(defaults)
    )
    sync_resource_url_records(defaults)
    defaults["supported_service_urls"] = unique_list([identifier_core.normalize_url(str(item)) for item in defaults.get("supported_service_urls", []) if identifier_core.normalize_url(str(item))])
    refresh_project_seed_identity(defaults)
    return apply_technical_interest_state(apply_atproto_contribution_state(defaults))


def normalize_lexicon_resolution_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexes: dict[tuple[str, str, str], int] = {}
    output: list[dict[str, Any]] = []
    # Repeated fetches replace the prior witness, including invalidation of a
    # formerly validated schema. Do not keep the first response indefinitely.
    for entry in entries:
        input_nsid = identifier_core.normalize_nsid(str(entry.get("input_nsid") or ""))
        resolved_nsid = identifier_core.normalize_nsid(str(entry.get("resolved_nsid") or ""))
        repo_did = str(entry.get("repo_did") or "")
        key = (input_nsid, resolved_nsid, repo_did)
        if not resolved_nsid:
            continue
        normalized = {
            "input_nsid": input_nsid,
            "resolved_nsid": resolved_nsid,
            "repo_did": repo_did,
            "authority_domain": str(entry.get("authority_domain") or identifier_core.derive_authority_domain(resolved_nsid)),
            "pds_url": str(entry.get("pds_url") or ""),
            "record_uri": str(entry.get("record_uri") or ""),
            "resolved_at": str(entry.get("resolved_at") or ""),
            **({"publication_validation": {
                key: str(entry["publication_validation"].get(key) or "")
                for key in ("version", "schema_id", "schema_type", "schema_hash", "type_changed")
            }} if isinstance(entry.get("publication_validation"), dict) else {}),
        }
        if key not in indexes:
            indexes[key] = len(output)
            output.append(normalized)
        elif normalized["resolved_at"] >= output[indexes[key]]["resolved_at"]:
            output[indexes[key]] = normalized
    return output


def compute_recorded_lexicon_namespaces(project: dict[str, Any]) -> list[str]:
    explicit_namespaces = unique_list(
        [
            identifier_core.normalize_nsid(str(item))
            for item in list(project.get("recorded_lexicon_namespaces", [])) + list(project.get("lexicon_namespaces", []))
            if str(item).strip()
        ]
    )
    entries = normalize_lexicon_resolution_entries(project.get("lexicon_resolution_entries", []))
    if not entries:
        if explicit_namespaces:
            return explicit_namespaces
        return unique_list([lexicon_namespace_for_project(item) for item in project.get("input_nsids", [])])

    grouped: dict[str, list[str]] = {}
    for entry in entries:
        group_key = str(entry.get("repo_did") or entry.get("authority_domain") or entry.get("resolved_nsid"))
        grouped.setdefault(group_key, []).append(str(entry.get("resolved_nsid") or ""))

    namespaces: list[str] = []
    for nsids in grouped.values():
        unique_nsids = unique_list(nsids)
        if len(unique_nsids) == 1:
            namespaces.append(lexicon_namespace_for_project(unique_nsids[0]))
            continue
        prefix = common_nsid_prefix(unique_nsids)
        if prefix and len(prefix.split(".")) >= 2:
            namespaces.append(prefix)
            continue
        namespaces.extend(lexicon_namespace_for_project(nsid) for nsid in unique_nsids)
    return unique_list(namespaces)


def builtin_lexicon_prefix(nsid: str) -> str:
    normalized = identifier_core.normalize_nsid(nsid)
    if not normalized:
        return ""
    for prefix in builtin_lexicon_prefixes():
        normalized_prefix = identifier_core.normalize_nsid(prefix.rstrip("."))
        if normalized_prefix and (
            normalized == normalized_prefix or normalized.startswith(f"{normalized_prefix}.")
        ):
            return normalized_prefix
    return ""


def compute_external_lexicons(project: dict[str, Any]) -> list[str]:
    observed_nsids: list[str] = []
    for item in list(project.get("input_nsids", [])) + list(project.get("resolved_nsids", [])):
        normalized = identifier_core.normalize_nsid(str(item or ""))
        if normalized:
            observed_nsids.append(normalized)
    prefixes: list[str] = []
    for entry in project.get("lexicon_schema_samples", []):
        if not isinstance(entry, dict):
            continue
        nsid = identifier_core.normalize_nsid(str(entry.get("nsid") or ""))
        if nsid:
            observed_nsids.append(nsid)
    for entry in project.get("recent_record_samples", []):
        if not isinstance(entry, dict):
            continue
        collection = identifier_core.normalize_nsid(str(entry.get("collection") or ""))
        if collection:
            observed_nsids.append(collection)
    for entry in project.get("lexicon_resolution_entries", []):
        if not isinstance(entry, dict):
            continue
        input_nsid = identifier_core.normalize_nsid(str(entry.get("input_nsid") or ""))
        resolved_nsid = identifier_core.normalize_nsid(str(entry.get("resolved_nsid") or ""))
        if input_nsid:
            observed_nsids.append(input_nsid)
        if resolved_nsid:
            observed_nsids.append(resolved_nsid)
    for nsid in unique_list(observed_nsids):
        if project_trusted_collection_nsid_from_state(project, nsid):
            continue
        prefix = builtin_lexicon_prefix(nsid)
        if prefix:
            prefixes.append(prefix)
            continue
        segments = nsid.split(".")
        if len(segments) >= 2:
            prefixes.append(".".join(segments[:2]))
    return unique_list(prefixes)


def ownership_anchor_service_hosts(project: dict[str, Any]) -> set[str]:
    return {host for host in manual_service_hosts(project) if host}


def project_trusted_collection_nsid_from_state(project: dict[str, Any], nsid: str) -> bool:
    normalized = identifier_core.normalize_nsid(nsid)
    if not normalized or relation_core.identifier_is_guarded(project, "nsid", normalized):
        return False
    return (
        normalized in {identifier_core.normalize_nsid(str(item)) for item in project.get("resolved_nsids", [])}
        or normalized in active_confirmed_identifier_values(project, "nsid")
        or trust_graph_core.confirmed_by_trust_policy(project, "collection", normalized)
    )


def project_from_row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    profile_payload = flatten_grouped_payload(load_json(str(row["profile_json"] or "")) or {})
    state_payload = flatten_grouped_payload(load_json(str(row["state_json"] or "")) or {})
    payload.update(profile_payload)
    payload.update(state_payload)
    for field in PROJECT_STORAGE_SCALAR_FIELDS:
        if field in row.keys():
            payload[field] = row[field]
    return payload


def project_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    # A row supplied by a caller may predate its child rows. Reload atomically.
    project = fetch_project(conn, str(row["project_key"]))
    if project is None:
        raise StaleProjectWriteError(f"project was deleted: {row['project_key']}", project_key=str(row["project_key"]))
    return project


def project_record_payload(project: dict[str, Any]) -> dict[str, Any]:
    record_payload: dict[str, Any] = {}

    core: dict[str, Any] = {}
    if project.get("tagline"):
        core["tagline"] = project.get("tagline")
    if project.get("tagline_is_provisional"):
        core["tagline_is_provisional"] = True
    if project.get("summary"):
        core["summary"] = project.get("summary")
    if project.get("notes"):
        core["notes"] = project.get("notes")
    if core:
        record_payload["core"] = core

    app: dict[str, Any] = {}
    if project.get("tags"):
        app["tags"] = project.get("tags")
    if project.get("platforms"):
        app["platforms"] = project.get("platforms")
    app_links = build_project_app_links(project)
    if app_links:
        app["links"] = app_links
    record_payload["app"] = app

    return record_payload


def project_ops_payload(project: dict[str, Any]) -> dict[str, Any]:
    ops_payload = compact_grouped_payload(OPS_JSON_GROUP_FIELDS, project)
    return {
        group: runtime_view.strip_runtime_fields(dict(values))
        for group, values in ops_payload.items()
        if runtime_view.strip_runtime_fields(dict(values))
    }


@concurrency.consistent_read
def fetch_project(conn: sqlite3.Connection, project_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM projects WHERE project_key = ?",
        (project_key,),
    ).fetchone()
    if row is None:
        return None
    return _project_from_snapshot_row(conn, row)


def _project_from_snapshot_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Hydrate a row selected inside the caller's read snapshot."""
    payload = project_from_row_payload(row)
    payload = registry_database.hydrate_project_children(conn, payload)
    payload[runtime_view.RUNTIME_PROJECT_REVISION] = concurrency.revision(conn, str(row["project_key"]))
    return runtime_view.refresh_baselines(ensure_project_shape(payload))


def resolve_project_key_input(conn: sqlite3.Connection, supplied: str) -> str:
    value = str(supplied or "").strip()
    if not value:
        raise ValueError("project key is required")
    if fetch_project(conn, value) is not None:
        return value
    alias_target = registry_database.project_key_alias_target(conn, value)
    if alias_target and fetch_project(conn, alias_target) is not None:
        return alias_target

    prefix = value.lower()
    if prefix.startswith("proj:"):
        prefix = prefix.removeprefix("proj:")
    if not re.fullmatch(r"[0-9a-f]{1,12}", prefix):
        raise ValueError(f"project not found: {supplied}")

    rows = conn.execute(
        """
        SELECT project_key
        FROM projects
        WHERE project_key LIKE ?
        UNION
        SELECT canonical_project_key AS project_key
        FROM project_key_aliases
        WHERE alias_project_key LIKE ?
        ORDER BY project_key ASC
        LIMIT 21
        """,
        (f"proj:{prefix}%", f"proj:{prefix}%"),
    ).fetchall()
    matches = [str(row["project_key"]) for row in rows]
    if not matches:
        raise ValueError(f"project not found: {supplied}")
    if len(matches) > 1:
        preview = ", ".join(matches[:10])
        suffix = " ..." if len(matches) > 10 else ""
        raise ValueError(f"ambiguous project key prefix: {supplied} -> {preview}{suffix}")
    return matches[0]


@concurrency.consistent_read
def list_projects(
    conn: sqlite3.Connection, tracking_states: set[str] | None = None,
    *, check_cancelled: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    if check_cancelled:
        check_cancelled()
    if tracking_states is None:
        rows = conn.execute(
            """
            SELECT *
            FROM projects
            ORDER BY last_seen_at DESC, project_key ASC
            """
        ).fetchall()
    else:
        requested = sorted(normalize_tracking_state(item) for item in tracking_states if item)
        if not requested:
            return []
        placeholders = ", ".join("?" for _ in requested)
        rows = conn.execute(
            f"""
            SELECT *
            FROM projects
            WHERE tracking_state IN ({placeholders})
            ORDER BY last_seen_at DESC, project_key ASC
            """,
            tuple(requested),
        ).fetchall()
    projects = []
    for row in rows:
        if check_cancelled:
            check_cancelled()
        projects.append(_project_from_snapshot_row(conn, row))
    return projects


@concurrency.consistent_read
def list_duplicate_projects(
    conn: sqlite3.Connection, *, check_cancelled: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Read-only inputs for duplicate matching, never a saveable runtime view.

    Keep the scalar identity, creator evidence, trust policy, current identifier
    rows and operator decisions. Descriptions, observations and relation evidence
    do not participate in duplicate_profile or duplicate_candidate_for_pair.
    All tables are read in one snapshot, including caller-owned transactions.
    """
    if check_cancelled:
        check_cancelled()
    children: dict[str, dict[str, list[sqlite3.Row]]] = {}
    for table in ("project_identifiers", "project_decisions", "project_identifier_adoptions"):
        grouped: dict[str, list[sqlite3.Row]] = {}
        order = "created_at, adoption_id" if table == "project_identifier_adoptions" else "ordinal"
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY project_key, {order}"):
            if check_cancelled:
                check_cancelled()
            grouped.setdefault(str(row["project_key"]), []).append(row)
        children[table] = grouped
    projects = []
    rows = conn.execute("""
        SELECT project_key, name, website_url,
               json_extract(state_json, '$.ops.status') AS status,
               json_extract(state_json, '$.evidence.record_creators') AS record_creators,
               json_extract(state_json, '$.evidence.trust_graph') AS trust_graph
        FROM projects ORDER BY last_seen_at DESC, project_key ASC
    """)
    for row in rows:
        if check_cancelled:
            check_cancelled()
        key = str(row["project_key"])
        projects.append(_duplicate_project_from_rows(
            row,
            identifier_rows=children["project_identifiers"].pop(key, []),
            decision_rows=children["project_decisions"].pop(key, []),
            adoption_rows=children["project_identifier_adoptions"].pop(key, []),
        ))
    return projects


def _duplicate_project_from_rows(
    row: sqlite3.Row, *, identifier_rows: list[sqlite3.Row],
    decision_rows: list[sqlite3.Row], adoption_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    payload = dict(row)
    payload["record_creators"] = load_json(row["record_creators"] or "[]")
    payload["trust_graph"] = load_json(row["trust_graph"] or "{}")
    payload = runtime_view.hydrate(
        payload, identifier_rows=identifier_rows, decision_rows=decision_rows,
        adoption_rows=adoption_rows, relation_rows=(), load_json=registry_database._load_json,
    )
    # Reuse normalization and identifier baselines so the original stored
    # kind/value/role/classification survives legacy field projection exactly as
    # in a full read. The partial input must not pass the update completeness gate.
    payload = runtime_view.refresh_baselines(ensure_project_shape(payload))
    payload.pop(runtime_view.RUNTIME_VIEW_MARKER, None)
    return payload


@concurrency.consistent_read
def list_projects_with_decision(
    conn: sqlite3.Connection,
    decision_kind: str,
    *,
    tracking_states: set[str] | None = None,
) -> list[dict[str, Any]]:
    # Select a superset, including cleared history. Hydration and domain policy
    # remain responsible for interpreting the decision's metadata/current effect.
    conditions = ["project_key IN (SELECT project_key FROM project_decisions WHERE kind = ?)"]
    params: list[str] = [decision_kind]
    if decision_kind == "defer_promotion":
        # Hydration also projects the older review deadline into a deferral.
        # Keep a superset; expiry, fingerprint and cleared metadata are policy.
        conditions[0] = (
            f"({conditions[0]} OR "
            "json_extract(state_json, '$.ops.promotion_attention.reviewed_until') IS NOT NULL)"
        )
    if tracking_states is not None:
        requested = sorted(normalize_tracking_state(item) for item in tracking_states if item)
        if not requested:
            return []
        conditions.append(f"tracking_state IN ({', '.join('?' for _ in requested)})")
        params.extend(requested)
    rows = conn.execute(
        f"SELECT * FROM projects WHERE {' AND '.join(conditions)} "
        "ORDER BY last_seen_at DESC, project_key ASC",
        params,
    ).fetchall()
    # Keep key selection and complete project reads in the same snapshot.
    return [_project_from_snapshot_row(conn, row) for row in rows]


def project_matches_identifier(project: dict[str, Any], identifier_type: str, identifier_value: str) -> tuple[int, bool]:
    normalized = canonical_seed_value(identifier_type, identifier_value)
    if (
        str(project.get("seed_type") or "") == identifier_type
        and canonical_seed_value(identifier_type, str(project.get("seed_value") or "")) == normalized
    ):
        return 0, True
    for item in project.get("manual_seeds", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != identifier_type:
            continue
        if canonical_seed_value(identifier_type, str(item.get("value") or "")) == normalized:
            return 0, True
    if identifier_type == "service_url":
        if normalized in [identifier_core.normalize_url(str(item)) for item in project.get("service_urls", []) if item]:
            return 0, True
        if normalized in [identifier_core.normalize_url(str(item)) for item in service_url_candidate_values(project) if item]:
            return 1, True
        return 99, False
    if identifier_type == "nsid":
        if normalized in [identifier_core.normalize_nsid(str(item)) for item in project.get("input_nsids", []) if item]:
            return 0, True
        if normalized in [identifier_core.normalize_nsid(str(item)) for item in project.get("resolved_nsids", []) if item]:
            return 1, True
        return 99, False
    if identifier_type == "resolved_nsid":
        return (0, True) if normalized in [identifier_core.normalize_nsid(str(item)) for item in project.get("resolved_nsids", []) if item] else (99, False)
    if identifier_type == "repo_url":
        return (0, True) if normalized in [identifier_core.normalize_url(str(item)) for item in project_repository_url_values(project) if item] else (99, False)
    if identifier_type == "did":
        if str(project.get("brand_did") or "").strip() == normalized:
            return 0, True
        for account in normalize_developer_accounts(list(project.get("developer_accounts", [])) + list(project.get("candidate_developer_accounts", []))):
            if str(account.get("did") or "").strip() == normalized:
                return 0, True
        return 99, False
    if identifier_type == "handle":
        lowered = normalized.lower()
        for account in normalize_developer_accounts(list(project.get("developer_accounts", [])) + list(project.get("candidate_developer_accounts", []))):
            if str(account.get("handle") or "").strip().lower().rstrip(".") == lowered:
                return 0, True
        for handle in list(project.get("bluesky_handles", [])) + list(project.get("tangled_handles", [])):
            if str(handle or "").strip().lower().rstrip(".") == lowered:
                return 1, True
        return 99, False
    if identifier_type == "domain":
        lowered = normalized.lower()
        if lowered in {str(domain or "").strip().lower().rstrip(".") for domain in project.get("authority_domains", [])}:
            return 0, True
        return 99, False
    if identifier_type == "github_repo_url":
        return (0, True) if normalized in [identifier_core.normalize_url(str(item)) for item in provider_repo_url_values(project, "github") if item] else (99, False)
    if identifier_type == "tangled_repo_url":
        return (0, True) if normalized in [identifier_core.normalize_url(str(item)) for item in provider_repo_url_values(project, "tangled") if item] else (99, False)
    if identifier_type == "service_url_candidate":
        return (0, True) if normalized in [identifier_core.normalize_url(str(item)) for item in service_url_candidate_values(project) if item] else (99, False)
    return 99, False


def indexed_identifier_matches(
    conn: sqlite3.Connection,
    identifier_type: str,
    identifier_value: str,
) -> list[tuple[int, str]]:
    """Use the normalized index to bound exact identifier matches."""
    normalized = canonical_seed_value(identifier_type, identifier_value)
    kind = {
        "github_repo_url": "repo_url",
        "tangled_repo_url": "repo_url",
        "service_url_candidate": "service_url",
        "resolved_nsid": "nsid",
    }.get(identifier_type, identifier_type)
    if kind not in {"service_url", "repo_url", "nsid", "did", "handle", "domain"} or not normalized:
        return []

    rows = conn.execute(
        """
        SELECT project_key, role, classification, provider
        FROM project_identifiers INDEXED BY idx_project_identifiers_lookup
        WHERE kind = ? AND value = ?
        """,
        (kind, normalized),
    ).fetchall()
    matches: dict[str, int] = {}
    for row in rows:
        role = str(row["role"])
        classification = str(row["classification"])
        provider = str(row["provider"])
        priority: int | None = None
        if identifier_type == "service_url":
            if role == "service" and classification == "confirmed":
                priority = 0
            elif role == "service" and classification == "candidate":
                priority = 1
        elif identifier_type == "nsid":
            if role == "owned_nsid" and classification == "confirmed":
                priority = 0
            elif role == "owned_nsid":
                priority = 1
        elif identifier_type == "resolved_nsid" and role == "owned_nsid":
            priority = 0
        elif identifier_type == "repo_url":
            if role == "project_repository" and classification == "confirmed":
                priority = 0
        elif identifier_type in {"github_repo_url", "tangled_repo_url"}:
            expected_provider = identifier_type.removesuffix("_repo_url")
            if (
                role == "project_repository"
                and classification == "confirmed"
                and provider == expected_provider
            ):
                priority = 0
        elif identifier_type == "did" and role in {"brand_did", "developer_did"}:
            priority = 0
        elif identifier_type == "handle" and role == "developer_handle":
            priority = 0
        elif identifier_type == "handle" and role == "project_handle":
            priority = 1
        elif identifier_type == "domain" and role == "authority_domain":
            priority = 0
        elif identifier_type == "service_url_candidate" and role == "service" and classification == "candidate":
            priority = 0
        if priority is not None:
            project_key = str(row["project_key"])
            matches[project_key] = min(priority, matches.get(project_key, priority))
    ordered = sorted(((priority, project_key) for project_key, priority in matches.items()))
    if identifier_type in {"service_url", "repo_url", "nsid"}:
        for priority, project_key in identifier_storage.noncurrent_identifier_matches(conn, kind, normalized):
            matches[project_key] = min(priority, matches.get(project_key, priority))
        ordered = sorted((priority, project_key) for project_key, priority in matches.items())
    if identifier_type in {"service_url_candidate", "did"}:
        ordered = [
            (priority, project_key)
            for priority, project_key in ordered
            if (
                (project := fetch_project(conn, project_key)) is not None
                and project_matches_identifier(project, identifier_type, normalized)[1]
            )
        ]
    return ordered


def find_project_key_by_identifier(conn: sqlite3.Connection, identifier_type: str, identifier_value: str) -> str | None:
    matches = indexed_identifier_matches(conn, identifier_type, identifier_value)
    return matches[0][1] if matches else None


def save_project(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    enriched: bool = False,
    *,
    preserve_timestamps: bool = False,
    commit: bool = True,
) -> None:
    with concurrency.write_transaction(conn, commit=commit):
        exists = conn.execute("SELECT 1 FROM projects WHERE project_key=?", (project["project_key"],)).fetchone() is not None
        runtime_view.require_complete_for_update(project, exists=exists)
        concurrency.check_projects(conn, [project])
        pending = project.get(runtime_view.RUNTIME_PENDING_RUN)
        if pending:
            investigation_storage.save_investigation_run(conn, **pending)
            from .publication_verification import share_investigation
            share_investigation(conn, pending)
        _save_project(conn, project, enriched, preserve_timestamps=preserve_timestamps, commit=False)
        project[runtime_view.RUNTIME_PROJECT_REVISION] = concurrency.revision(conn, str(project["project_key"]))
        runtime_view.mark_complete(project)
    project.pop(runtime_view.RUNTIME_PENDING_RUN, None)


def _save_project(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    enriched: bool = False,
    *,
    preserve_timestamps: bool = False,
    commit: bool = True,
) -> None:
    project_save_started = time.perf_counter()
    investigation_run_id = str(project.get(runtime_view.RUNTIME_INVESTIGATION_RUN_ID) or "")
    now = iso_now()
    preserve_normalized_rows = runtime_view.normalized_children_unchanged(project)
    preserved_runtime_rows = {
        key: copy.deepcopy(project.get(key))
        for key in (
            runtime_view.RUNTIME_IDENTIFIER_ROWS,
            runtime_view.RUNTIME_RELATION_ROWS,
            runtime_view.RUNTIME_DECISION_ROWS,
        )
    } if preserve_normalized_rows else {}
    project = ensure_project_shape(project)
    alias_target = registry_database.project_key_alias_target(conn, str(project.get("project_key") or ""))
    if alias_target:
        raise StaleProjectWriteError(
            f"project was merged while this write was in progress: "
            f"{project['project_key']} -> {alias_target}; retry against current project state"
        )
    project["input_nsids"] = unique_list([identifier_core.normalize_nsid(item) for item in project.get("input_nsids", []) if item])
    project["resolved_nsids"] = unique_list([identifier_core.normalize_nsid(item) for item in project.get("resolved_nsids", []) if item])
    project["lexicon_resolution_entries"] = normalize_lexicon_resolution_entries(project.get("lexicon_resolution_entries", []))
    if runtime_view.is_complete(project):
        project["recorded_lexicon_namespaces"] = unique_list(
            [identifier_core.normalize_nsid(str(item)) for item in project.get("recorded_lexicon_namespaces", []) if str(item).strip()]
        )
    else:
        project["recorded_lexicon_namespaces"] = compute_recorded_lexicon_namespaces(project)
    project["lexicons"] = unique_list(project.get("lexicons", []))
    project["authority_domains"] = unique_list(project.get("authority_domains", []))
    project["external_lexicons"] = unique_list(project.get("external_lexicons", []) + compute_external_lexicons(project))
    set_service_url_candidate_values(project, service_url_candidate_values(project))
    project["service_urls"] = unique_list(project.get("service_urls", []))
    project["project_site_urls"] = unique_list([
        identifier_core.normalize_url(str(item))
        for item in project.get("project_site_urls", [])
        if identifier_core.normalize_url(str(item))
    ])
    confirmed_project_sites = set(project["project_site_urls"])
    project["project_site_url_candidates"] = unique_list([
        identifier_core.normalize_url(str(item))
        for item in project.get("project_site_url_candidates", [])
        if identifier_core.normalize_url(str(item)) not in confirmed_project_sites
    ])
    sync_resource_url_records(project)
    project["failed_service_urls"] = unique_list(project.get("failed_service_urls", []))
    project["supported_service_urls"] = unique_list([identifier_core.normalize_url(str(item)) for item in project.get("supported_service_urls", []) if identifier_core.normalize_url(str(item))])
    project["service_status"] = normalize_service_status(project.get("service_status"))
    project["service_status_confidence"] = normalize_service_status_confidence(project.get("service_status_confidence"))
    project["service_status_evidence"] = unique_list([str(item).strip() for item in project.get("service_status_evidence", []) if str(item).strip()])
    project["project_repository_urls"] = repo_url_core.normalize_repo_collection(project.get("project_repository_urls", []))
    project["candidate_project_repository_urls"] = repo_url_core.normalize_repo_collection(project.get("candidate_project_repository_urls", []))
    project["source_repo_records"] = normalize_source_repo_records(project.get("source_repo_records", []))
    oauth_metadata.reconcile_relations(project)
    project["repository_analysis_results"] = repository_analysis_core.normalize_results(
        project.get("repository_analysis_results", [])
    )
    sync_source_repo_provider_views(project)
    project["bluesky_handles"] = unique_list(project.get("bluesky_handles", []))
    project["tangled_handles"] = unique_list(project.get("tangled_handles", []))
    project["investigation_queries"] = unique_list(project.get("investigation_queries", []))
    project["investigation_log"] = list(project.get("investigation_log", []))
    project["last_run_errors"] = unique_list(project.get("last_run_errors", []))
    project["developer_accounts"] = normalize_developer_accounts(list(project.get("developer_accounts", [])))
    project["candidate_developer_accounts"] = normalize_developer_accounts(list(project.get("candidate_developer_accounts", [])))
    project["name_candidates"] = [dict(item) for item in project.get("name_candidates", []) if isinstance(item, dict)]
    project["tags"] = unique_list(project.get("tags", []))
    project["platforms"] = unique_list(project.get("platforms", []))
    project["introduced_url"] = identifier_core.normalize_url(str(project.get("introduced_url") or "")) if project.get("introduced_url") else ""
    project["last_promoted_at"] = str(project.get("last_promoted_at") or "")
    project["promotion_events"] = [dict(item) for item in project.get("promotion_events", []) if isinstance(item, dict)]
    project["evidence_urls"] = unique_list(project.get("evidence_urls", []))
    project["discovery_sources"] = unique_list(project.get("discovery_sources", []))
    project["features"] = unique_list(project.get("features", []))
    project["inspired_by"] = unique_list(project.get("inspired_by", []))
    project["manual_seeds"] = unique_manual_seeds(project.get("manual_seeds", []))
    project["confirmed_identifiers"] = normalize_confirmed_identifiers(list(project.get("confirmed_identifiers", [])))
    refresh_project_seed_identity(project)
    project["summary_candidates"] = project.get("summary_candidates", [])
    project["tagline_candidates"] = project.get("tagline_candidates", [])
    project["lexicon_schema_samples"] = [dict(item) for item in project.get("lexicon_schema_samples", []) if isinstance(item, dict)][:12]
    project["recent_record_samples"] = [dict(item) for item in project.get("recent_record_samples", []) if isinstance(item, dict)][:18]
    normalize_website_and_service_urls(project)
    project["lexicon_count"] = len(project.get("lexicons", []))
    reclassify_service_specific_lexicon(project)
    apply_atproto_contribution_state(project)

    existing = conn.execute(
        """
        SELECT first_seen_at
        FROM projects
        WHERE project_key = ?
        """,
        (project["project_key"],),
    ).fetchone()
    runtime_view.require_complete_for_update(project, exists=existing is not None)
    first_seen_at = (
        str(project.get("first_seen_at") or now)
        if preserve_timestamps
        else (str(existing["first_seen_at"]) if existing else now)
    )
    last_seen_at = str(project.get("last_seen_at") or now) if preserve_timestamps else now
    last_enriched_at = (
        str(project.get("last_enriched_at") or "") or None
        if preserve_timestamps
        else (now if enriched else None)
    )
    project["first_seen_at"] = first_seen_at
    project["last_seen_at"] = last_seen_at
    if last_enriched_at:
        project["last_enriched_at"] = last_enriched_at
    if existing is None:
        conn.execute(
            """
            INSERT INTO projects (
                project_key, name, website_url, service_status, tracking_state,
                introduced_url, profile_json, state_json, first_seen_at, last_seen_at, last_enriched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project["project_key"],
                project.get("name", ""),
                project.get("website_url", ""),
                project.get("service_status", "unknown"),
                project.get("tracking_state", "active"),
                project.get("introduced_url", ""),
                dump_json(project_record_payload(project), sort_keys=False),
                dump_json(project_ops_payload(project), sort_keys=False),
                first_seen_at,
                last_seen_at,
                last_enriched_at,
            ),
        )
    else:
        last_enriched_value = conn.execute(
            "SELECT last_enriched_at FROM projects WHERE project_key = ?",
            (project["project_key"],),
        ).fetchone()
        existing_last_enriched = str(last_enriched_value["last_enriched_at"]) if last_enriched_value and last_enriched_value["last_enriched_at"] else None
        conn.execute(
            """
            UPDATE projects
            SET name = ?, website_url = ?, service_status = ?, tracking_state = ?, introduced_url = ?, profile_json = ?, state_json = ?,
                last_seen_at = ?, last_enriched_at = ?
            WHERE project_key = ?
            """,
            (
                project.get("name", ""),
                project.get("website_url", ""),
                project.get("service_status", "unknown"),
                project.get("tracking_state", "active"),
                project.get("introduced_url", ""),
                dump_json(project_record_payload(project), sort_keys=False),
                dump_json(project_ops_payload(project), sort_keys=False),
                last_seen_at,
                last_enriched_at or existing_last_enriched,
                project["project_key"],
            ),
        )
    if preserve_normalized_rows:
        project.update(preserved_runtime_rows)
        runtime_view.refresh_baselines(project)
    registry_database.replace_project_children(conn, project)
    if investigation_run_id:
        investigation_storage.update_run_metrics_phase(
            conn,
            investigation_run_id,
            phase="project_save_ms",
            duration_ms=max(0, round((time.perf_counter() - project_save_started) * 1000)),
        )
    if commit:
        conn.commit()


def allocate_new_project_key(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT project_key FROM projects").fetchall()
    existing_keys = {str(row["project_key"]) for row in rows}
    return generate_project_key(existing_keys)

NOISY_SERVICE_HOSTS = {
    "img.shields.io",
    "shields.io",
    "www.docker.com",
    "docker.com",
    "opensource.org",
    "www.apache.org",
    "apache.org",
    "www.npmjs.com",
    "npmjs.com",
    "cdn.jsdelivr.net",
    "jsdelivr.net",
    "raw.githubusercontent.com",
    "crowdin.com",
    "datatracker.ietf.org",
    "discourse.atprotocol.community",
    "github.com",
    "www.github.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "discord.gg",
    "forms.gle",
    "readme-contribs.as93.net",
    "relay1.us-west.bsky.network",
    "plc.directory",
    "cdn.bsky.app",
}

NOISY_SERVICE_HOST_SUFFIXES = {
    "huggingface.co",
    "img.shields.io",
}

NOISY_SERVICE_PATH_TOKENS = {
    "/docs",
    "/doc",
    "/blog",
    "/guides",
    "/guide",
    "/specs",
    "/spec",
    "/license",
    "/licenses",
    "/terms",
    "/privacy",
    "/readme",
    "/package",
    "/packages",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif"}

SHARED_OFFICIAL_IDENTITY_ROOTS = {
    "bsky.app",
    "atproto.com",
    "bsky.network",
}


def has_noisy_service_host(host: str) -> bool:
    lowered = host.lower()
    if lowered in NOISY_SERVICE_HOSTS:
        return True
    return any(lowered == suffix or lowered.endswith(f".{suffix}") for suffix in NOISY_SERVICE_HOST_SUFFIXES)


def is_asset_like_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    lowered_path = parsed.path.lower()
    return any(lowered_path.endswith(ext) for ext in IMAGE_EXTENSIONS)

ATPROTO_HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", re.IGNORECASE)

DID_RE = re.compile(r"^did:[a-z0-9]+:[A-Za-z0-9._:%-]+$", re.IGNORECASE)


def extract_bluesky_profile_subject(url: str) -> dict[str, str]:
    normalized = identifier_core.normalize_url(identifier_core.sanitize_extracted_url(url))
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc != "bsky.app":
        return {}
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2 or segments[0] != "profile":
        return {}
    subject = urllib.parse.unquote(segments[1]).strip()
    if DID_RE.fullmatch(subject):
        return {"did": subject}
    if ATPROTO_HANDLE_RE.fullmatch(subject):
        return {"handle": subject.lower()}
    return {}


def is_bluesky_profile_url(url: str) -> bool:
    return bool(extract_bluesky_profile_subject(url))


def is_explicitly_non_project_service_url(url: str) -> bool:
    normalized = identifier_core.normalize_url(identifier_core.sanitize_extracted_url(url))
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if not host:
        return True
    if is_bluesky_profile_url(normalized):
        return True
    if has_noisy_service_host(host):
        return True
    if host == "discord.com" and path.startswith("/developers/"):
        return True
    if host == "atproto.com" and any(path.startswith(prefix) for prefix in ("/guides/", "/specs/", "/blog/")):
        return True
    if host.startswith("docs.") and len(path.split("/")) > 2:
        return True
    return False


def normalize_service_candidate_url(url: str) -> str | None:
    cleaned = identifier_core.sanitize_extracted_url(url)
    if not cleaned:
        return None
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    lowered_host = parsed.netloc.lower()
    if has_noisy_service_host(lowered_host):
        return None
    if is_asset_like_url(cleaned):
        return None
    lowered_path = parsed.path.lower().rstrip("/")
    if lowered_path and any(
        lowered_path == token or lowered_path.startswith(f"{token}/")
        for token in NOISY_SERVICE_PATH_TOKENS
    ):
        return None
    normalized = urllib.parse.urlunparse((parsed.scheme.lower(), lowered_host, "", "", "", ""))
    if is_explicitly_non_project_service_url(normalized):
        return None
    return normalized


def normalize_confirmed_service_url(url: str) -> str | None:
    cleaned = identifier_core.sanitize_extracted_url(url)
    if not cleaned:
        return None
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    lowered_host = parsed.netloc.lower()
    if has_noisy_service_host(lowered_host):
        return None
    if is_asset_like_url(cleaned):
        return None
    normalized = identifier_core.normalize_url(cleaned)
    if is_explicitly_non_project_service_url(normalized):
        return None
    return normalized


def normalize_ja_en_mixed_spacing(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    # Remove spaces only at Japanese<->ASCII boundaries.
    text = re.sub(r"(?<=[\u3040-\u30ff\u3400-\u9fff])\s+(?=[A-Za-z0-9])", "", text)
    text = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[\u3040-\u30ff\u3400-\u9fff])", "", text)
    return text


def manual_service_hosts(project: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    for seed in project.get("manual_seeds", []):
        if str(seed.get("type") or "") != "service_url":
            continue
        value = str(seed.get("value") or "").strip()
        if not value:
            continue
        host = urllib.parse.urlparse(identifier_core.normalize_url(value)).netloc.lower()
        if host:
            hosts.add(host)
    for value in active_confirmed_identifier_values(project, "service_url"):
        host = urllib.parse.urlparse(identifier_core.normalize_url(value)).netloc.lower()
        if host:
            hosts.add(host)
    return hosts


def project_repository_url_values(project: dict[str, Any]) -> list[str]:
    return repo_url_core.normalize_repo_collection(project.get("project_repository_urls", []))


def provider_repo_url_values(project: dict[str, Any], provider: str) -> list[str]:
    return repo_url_core.normalize_repo_collection(
        [
            url
            for url in project_repository_url_values(project)
            if repo_url_core.infer_repo_provider(url) == provider
        ],
        provider=provider,
    )


def sync_source_repo_provider_views(project: dict[str, Any]) -> None:
    project["project_repository_urls"] = project_repository_url_values(project)


def normalize_source_repo_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for record in records:
        url = repo_url_core.canonical_repo_url(str(record.get("url") or ""))
        if not url:
            continue
        provider = str(record.get("provider") or repo_url_core.infer_repo_provider(url))
        confidence = str(record.get("confidence") or "candidate")
        relation = str(record.get("relation") or "candidate_repo")
        source = str(record.get("source") or "")
        evidence_kind = str(record.get("evidence_kind") or "")
        updated_at = str(record.get("updated_at") or "")
        key = (url, provider, confidence, source, evidence_kind, updated_at)
        if key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "url": url,
                "provider": provider,
                "confidence": confidence,
                "relation": relation,
                "source": source,
                "reason": str(record.get("reason") or ""),
                "evidence_kind": evidence_kind,
                "evidence_url": identifier_core.normalize_url(str(record.get("evidence_url") or "")) if record.get("evidence_url") else "",
                "recorded_at": str(record.get("recorded_at") or ""),
                "updated_at": updated_at,
            }
        )
    return output


def normalize_resource_url_records(entries: list[Any]) -> list[dict[str, str]]:
    merged: dict[tuple[str, str], dict[str, str]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        url = identifier_core.normalize_url(str(item.get("url") or ""))
        if not url:
            continue
        role = str(item.get("role") or app_link_role_from_resource_url(url) or "unknown").strip()
        if not role or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", role):
            role = "unknown"
        confidence = str(item.get("confidence") or "candidate").strip().lower()
        if confidence not in RESOURCE_URL_CONFIDENCE_VALUES:
            confidence = "candidate"
        source = str(item.get("source") or "").strip()
        observed_at = str(item.get("observed_at") or "").strip()
        record = {
            "url": url,
            "role": role,
            "source": source,
            "observed_at": observed_at,
            "confidence": confidence,
        }
        key = (url, role)
        existing = merged.get(key)
        if existing is None or resource_url_record_sort_key(record) < resource_url_record_sort_key(existing):
            merged[key] = record
        elif existing["confidence"] != "confirmed" and confidence == "confirmed":
            existing["confidence"] = "confirmed"
    return select_resource_url_records(list(merged.values()))


def resource_url_source_rank(source: str) -> int:
    prefix = str(source or "").split(":", 1)[0]
    return RESOURCE_URL_SOURCE_RANK_PREFIXES.get(prefix, 99)


def resource_url_path_segments(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(identifier_core.normalize_url(url))
    return [segment for segment in parsed.path.lower().strip("/").split("/") if segment]


def resource_url_path_rank(url: str, role: str) -> tuple[int, int, int, str]:
    normalized = identifier_core.normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    path = parsed.path.lower().rstrip("/")
    segments = resource_url_path_segments(normalized)
    if not path:
        return (0, 0, 0, normalized)
    if app_link_role_from_resource_url(normalized) == role:
        for _hint_role, prefixes in APP_LINK_RESOURCE_PATH_HINTS:
            if _hint_role != role:
                continue
            if any(path == prefix for prefix in prefixes):
                return (0, len(segments), len(path), normalized)
            if any(path.startswith(f"{prefix}/") for prefix in prefixes):
                return (1, len(segments), len(path), normalized)
    if segments and segments[0] == str(role or "").lower() and len(segments) == 1:
        return (1, len(segments), len(path), normalized)
    if any(segment in RESOURCE_URL_LOW_VALUE_PATH_SEGMENTS for segment in segments):
        return (8, len(segments), len(path), normalized)
    if segments and segments[0] == str(role or "").lower():
        return (3, len(segments), len(path), normalized)
    if any(re.fullmatch(r"\d{4}|\d+|[0-9a-f]{12,}", segment) for segment in segments):
        return (6, len(segments), len(path), normalized)
    return (3, len(segments), len(path), normalized)


def resource_url_record_sort_key(record: dict[str, str]) -> tuple[int, int, int, tuple[int, int, int, str], str]:
    role = str(record.get("role") or "unknown")
    confidence_rank = 0 if str(record.get("confidence") or "") == "confirmed" else 1
    return (
        confidence_rank,
        RESOURCE_URL_ROLE_RANK.get(role, 99),
        resource_url_source_rank(str(record.get("source") or "")),
        resource_url_path_rank(str(record.get("url") or ""), role),
        str(record.get("url") or ""),
    )


def resource_url_host_key(url: str) -> str:
    host = urllib.parse.urlparse(identifier_core.normalize_url(url)).netloc.lower()
    return domain_policy_core.root_domain(host) or host


def select_resource_url_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    role_counts: dict[str, int] = {}
    host_role_counts: dict[tuple[str, str], int] = {}
    selected: list[dict[str, str]] = []
    for record in sorted(records, key=resource_url_record_sort_key):
        if len(selected) >= RESOURCE_URL_TOTAL_BUDGET:
            break
        role = str(record.get("role") or "unknown")
        role_budget = RESOURCE_URL_ROLE_BUDGETS.get(role, 3)
        if role_counts.get(role, 0) >= role_budget:
            continue
        host_role = (resource_url_host_key(str(record.get("url") or "")), role)
        host_role_budget = RESOURCE_URL_HOST_ROLE_BUDGETS.get(role, 2)
        if host_role_counts.get(host_role, 0) >= host_role_budget:
            continue
        selected.append(record)
        role_counts[role] = role_counts.get(role, 0) + 1
        host_role_counts[host_role] = host_role_counts.get(host_role, 0) + 1
    return selected


def resource_url_record_values(project: dict[str, Any]) -> list[dict[str, str]]:
    website_url = identifier_core.normalize_url(str(project.get("website_url") or ""))
    service_urls = {identifier_core.normalize_url(str(item)) for item in project.get("service_urls", []) if identifier_core.normalize_url(str(item))}
    filtered_entries: list[Any] = []
    for item in list(project.get("resource_url_records", [])):
        if not isinstance(item, dict):
            continue
        url = identifier_core.normalize_url(str(item.get("url") or ""))
        if not url or url == website_url or url in service_urls:
            continue
        filtered_entries.append(item)
    return normalize_resource_url_records(filtered_entries)


def resource_url_values(
    project: dict[str, Any],
    *,
    confidences: set[str] | None = None,
    roles: set[str] | None = None,
) -> list[str]:
    normalized_confidences = {str(item or "").strip().lower() for item in confidences or set()}
    normalized_roles = {str(item or "").strip() for item in roles or set()}
    urls: list[str] = []
    for record in resource_url_record_values(project):
        if normalized_confidences and str(record.get("confidence") or "") not in normalized_confidences:
            continue
        if normalized_roles and str(record.get("role") or "") not in normalized_roles:
            continue
        urls.append(record["url"])
    return unique_list(urls)


def sync_resource_url_records(project: dict[str, Any]) -> None:
    project["resource_url_records"] = resource_url_record_values(project)


def normalize_website_and_service_urls(project: dict[str, Any]) -> None:
    for field_name in ("service_urls",):
        for item in project.get(field_name, []):
            if is_bluesky_profile_url(str(item or "")):
                record_developer_profile_url(project, str(item), f"{field_name}:profile_url")
    for item in service_url_candidate_values(project):
        if is_bluesky_profile_url(str(item or "")):
            record_developer_profile_url(project, str(item), "service_url_candidates:profile_url")
    if project.get("website_url") and is_bluesky_profile_url(str(project["website_url"])):
        record_developer_profile_url(project, str(project["website_url"]), "website_url:profile_url")
    project["service_urls"] = unique_list([item for item in [normalize_confirmed_service_url(item) for item in project.get("service_urls", []) if item] if item])
    project["project_site_urls"] = unique_list([
        identifier_core.normalize_url(str(item))
        for item in project.get("project_site_urls", [])
        if identifier_core.normalize_url(str(item))
    ])
    confirmed_project_sites = set(project["project_site_urls"])
    project["project_site_url_candidates"] = unique_list([
        identifier_core.normalize_url(str(item))
        for item in project.get("project_site_url_candidates", [])
        if identifier_core.normalize_url(str(item)) not in confirmed_project_sites
    ])
    set_service_url_candidate_values(project, service_url_candidate_values(project))
    project["website_url"] = (
        identifier_core.normalize_url(str(project.get("website_url") or ""))
        if project.get("website_url")
        else ""
    )
    confirmed_urls = {identifier_core.normalize_url(item) for item in project.get("service_urls", []) if item}
    set_service_url_candidate_values(
        project,
        [item for item in service_url_candidate_values(project) if identifier_core.normalize_url(item) not in confirmed_urls],
    )
    sync_resource_url_records(project)


def add_developer_account(project: dict[str, Any], handle: str, source: str, did: str = "") -> None:
    normalized_handle = str(handle or "").strip()
    normalized_did = str(did or "").strip()
    if not normalized_handle and not normalized_did:
        return
    account: dict[str, str] = {}
    if normalized_handle:
        account["handle"] = normalized_handle
    if normalized_did:
        account["did"] = normalized_did
    if source:
        account["source"] = source
    project["developer_accounts"] = normalize_developer_accounts(list(project.get("developer_accounts", [])) + [account])
    if normalized_handle and normalized_did:
        record_trust_edge(
            project,
            "handle",
            normalized_handle,
            "handle_resolves_did",
            "did",
            normalized_did,
            source=source,
            method="resolved_profile",
            classification="confirmed",
            eligible_for_auto_confirm=False,
            observed_at=iso_now(),
        )


def add_candidate_developer_account(project: dict[str, Any], handle: str, source: str, did: str = "") -> None:
    normalized_handle = str(handle or "").strip()
    normalized_did = str(did or "").strip()
    if not normalized_handle and not normalized_did:
        return
    account: dict[str, str] = {}
    if normalized_handle:
        account["handle"] = normalized_handle
    if normalized_did:
        account["did"] = normalized_did
    if source:
        account["source"] = source
    project["candidate_developer_accounts"] = normalize_developer_accounts(
        list(project.get("candidate_developer_accounts", [])) + [account]
    )


def handle_matches_confirmed_service_identity(project: dict[str, Any], handle: str) -> bool:
    normalized_handle = str(handle or "").strip().lower()
    if not normalized_handle or "." not in normalized_handle:
        return False
    handle_root = domain_policy_core.root_domain(normalized_handle)
    confirmed_hosts = {
        str(host or "").strip().lower()
        for host in ownership_anchor_service_hosts(project)
        if str(host or "").strip()
    }
    for host in confirmed_hosts:
        host_root = domain_policy_core.root_domain(host)
        if normalized_handle == host or normalized_handle.endswith(f".{host}"):
            return True
        if handle_root and host_root and handle_root == host_root:
            return True
    return False


def record_developer_handle(project: dict[str, Any], handle: str, source: str, did: str = "") -> None:
    if handle_matches_confirmed_service_identity(project, handle):
        project["bluesky_handles"] = add_unique(project.get("bluesky_handles", []), handle)
        add_developer_account(project, handle, source, did=did)
        return
    add_candidate_developer_account(project, handle, source, did=did)


def record_developer_profile_url(project: dict[str, Any], url: str, source: str) -> None:
    subject = extract_bluesky_profile_subject(url)
    handle = subject.get("handle", "")
    did = subject.get("did", "")
    if handle:
        record_developer_handle(project, handle, source)
    elif did:
        add_candidate_developer_account(project, "", source, did=did)


def normalize_trust_node_value(kind: str, value: str) -> str:
    if kind == "service_url":
        return normalize_confirmed_service_url(value) or ""
    if kind == "repo_url":
        return repo_url_core.canonical_repo_url(value)
    if kind in {"collection", "nsid"}:
        return identifier_core.normalize_nsid(value)
    if kind == "domain":
        return str(value or "").strip().lower().rstrip(".")
    if kind == "handle":
        return str(value or "").strip().lower().rstrip(".")
    if kind == "did":
        return str(value or "").strip()
    return ""


def trust_node(kind: str, value: str) -> dict[str, str]:
    return trust_graph_core.node(kind, normalize_trust_node_value(kind, value))


def record_trust_edge(
    project: dict[str, Any],
    subject_kind: str,
    subject_value: str,
    predicate: str,
    object_kind: str,
    object_value: str,
    *,
    source: str = "",
    locator: str = "",
    method: str = "",
    classification: str = "candidate",
    eligible_for_auto_confirm: bool = False,
    observed_at: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    subject = trust_node(subject_kind, subject_value)
    obj = trust_node(object_kind, object_value)
    if not subject or not obj:
        return {}
    return trust_graph_core.upsert_edge(
        project,
        subject=subject,
        predicate=predicate,
        object=obj,
        source=source,
        locator=identifier_core.normalize_url(locator) if locator else "",
        method=method,
        classification=classification,
        eligible_for_auto_confirm=eligible_for_auto_confirm,
        observed_at=observed_at,
        context=context or {},
        generated_at=iso_now(),
    )


def manual_nsid_seed_supports(project: dict[str, Any], nsid: str) -> bool:
    normalized = identifier_core.normalize_nsid(nsid)
    if not normalized:
        return False
    namespace = namespace_from_collection_nsid(normalized)
    for seed in manual_seed_values(project, "nsid"):
        seed_nsid = identifier_core.normalize_nsid(seed)
        if not seed_nsid:
            continue
        seed_namespace = namespace_from_collection_nsid(seed_nsid)
        if normalized == seed_nsid or (namespace and namespace == seed_namespace):
            return True
    return False


def service_hosts(project: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    urls = set(project.get("service_urls", []))
    for url in urls:
        host = urllib.parse.urlparse(identifier_core.normalize_url(url)).netloc.lower()
        if host:
            hosts.add(host)
    return hosts


def project_identity_hosts(project: dict[str, Any], *, include_candidates: bool = True) -> set[str]:
    hosts = set(service_hosts(project))
    for domain in project.get("authority_domains", []):
        lowered = str(domain or "").strip().lower()
        if lowered and not is_shared_official_identity_host(lowered):
            hosts.add(lowered)
            hosts.add(domain_policy_core.root_domain(lowered))
    for value in manual_seed_values(project, "service_url"):
        host = urllib.parse.urlparse(value).netloc.lower()
        if host:
            hosts.add(host)
    if include_candidates:
        for value in project.get("service_url_candidates", []):
            host = urllib.parse.urlparse(identifier_core.normalize_url(str(value or ""))).netloc.lower()
            if host and not is_shared_official_identity_host(host):
                hosts.add(host)
    return {host for host in hosts if host}


def active_confirmed_identifier_values(project: dict[str, Any], identifier_type: str) -> set[str]:
    return {
        str(item.get("value") or "").strip()
        for item in normalize_confirmed_identifiers(list(project.get("confirmed_identifiers", [])))
        if str(item.get("type") or "") == identifier_type
        and str(item.get("value") or "").strip()
        and not str(item.get("cleared_at") or "").strip()
    }


def project_domain_identity_tokens(project: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for host in project_identity_hosts(project, include_candidates=False):
        for value in (host, domain_policy_core.root_domain(host)):
            token = identifier_core.normalize_alias_token(value)
            if token:
                tokens.add(token)
    return tokens


def project_merge_aliases(project: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    if project.get("name"):
        token = identifier_core.normalize_alias_token(str(project.get("name") or ""))
        if token:
            aliases.add(token)
    if project.get("website_url"):
        host = urllib.parse.urlparse(identifier_core.normalize_url(project["website_url"])).netloc.lower()
        first = host.split(".", 1)[0] if host else ""
        token = identifier_core.normalize_alias_token(first)
        if token:
            aliases.add(token)
    for url in project_repository_url_values(project):
        parsed = urllib.parse.urlparse(identifier_core.normalize_url(url))
        segs = [s for s in parsed.path.split("/") if s]
        if segs:
            token = identifier_core.normalize_alias_token(segs[-1])
            if token:
                aliases.add(token)
    return aliases


def project_repo_owner_tokens(project: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for url in project_repository_url_values(project):
        parsed = urllib.parse.urlparse(identifier_core.normalize_url(url))
        segs = [s for s in parsed.path.split("/") if s]
        if segs:
            token = identifier_core.normalize_alias_token(segs[0])
            if token:
                tokens.add(token)
    return tokens


def lexicon_brand_tokens(project: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for value in list(project.get("input_nsids", [])) + list(project.get("resolved_nsids", [])) + list(project.get("recorded_lexicon_namespaces", [])):
        normalized = identifier_core.normalize_nsid(str(value or ""))
        if not normalized:
            continue
        combined = identifier_core.normalize_alias_token(normalized)
        if combined:
            tokens.add(combined)
        for segment in normalized.split("."):
            token = identifier_core.normalize_alias_token(segment)
            if token:
                tokens.add(token)
    for value in project.get("authority_domains", []):
        host = str(value or "").strip().lower()
        if not host:
            continue
        combined = identifier_core.normalize_alias_token(host)
        if combined:
            tokens.add(combined)
        registrable = domain_policy_core.root_domain(host)
        combined_registrable = identifier_core.normalize_alias_token(registrable)
        if combined_registrable:
            tokens.add(combined_registrable)
        for segment in host.split("."):
            token = identifier_core.normalize_alias_token(segment)
            if token:
                tokens.add(token)
    return tokens


def has_shared_lexicon_positioning(project: dict[str, Any]) -> bool:
    text_blob = " ".join(
        [
            str(project.get("name") or ""),
            str(project.get("summary") or ""),
            str(project.get("tagline") or ""),
            str(project.get("classification_reason") or ""),
            str(project.get("watch_reason") or ""),
            " ".join(str(item or "") for item in project.get("features", [])),
            " ".join(str(item or "") for item in project.get("external_lexicons", [])),
        ]
    ).lower()
    strong_markers = [
        "shared",
        "common",
        "vendor-neutral",
        "reference implementation",
        "community lexicon",
        "共有",
        "共通",
        "コミュニティレキシコン",
    ]
    return any(marker in text_blob for marker in strong_markers)


def compute_lexicon_scope(project: dict[str, Any]) -> dict[str, Any]:
    has_lexicon_surface = bool(
        project.get("input_nsids")
        or project.get("resolved_nsids")
        or project.get("recorded_lexicon_namespaces")
    )
    if not has_lexicon_surface:
        return {"lexicon_scope": "unknown", "lexicon_scope_evidence": []}

    llm_output = project.get("llm_output") if isinstance(project.get("llm_output"), dict) else {}
    llm_scope = str(llm_output.get("lexicon_scope") or "").strip().lower()
    if llm_scope == "shared":
        return {
            "lexicon_scope": "shared",
            "lexicon_scope_evidence": ["llm_output:shared"],
        }
    if has_shared_lexicon_positioning(project):
        return {
            "lexicon_scope": "shared",
            "lexicon_scope_evidence": ["shared_lexicon_positioning"],
        }

    namespace_tokens = lexicon_brand_tokens(project)
    identity_tokens = project_domain_identity_tokens(project) | project_merge_aliases(project) | project_repo_owner_tokens(project)
    has_service_surface = bool(
        project.get("service_urls")
        or service_url_candidate_values(project)
        or project.get("collection_observations")
    )
    if namespace_tokens & identity_tokens and has_service_surface:
        return {
            "lexicon_scope": "service_specific",
            "lexicon_scope_evidence": [
                "namespace_matches_project_identity",
                "service_or_collection_surface_detected",
            ],
        }
    if llm_scope == "service_specific":
        return {
            "lexicon_scope": "service_specific",
            "lexicon_scope_evidence": ["llm_output:service_specific"],
        }
    return {
        "lexicon_scope": "unknown",
        "lexicon_scope_evidence": ["shared_lexicon_positioning_not_found"],
    }


def apply_lexicon_scope_state(project: dict[str, Any]) -> None:
    project.update(compute_lexicon_scope(project))


def reclassify_service_specific_lexicon(project: dict[str, Any]) -> None:
    apply_lexicon_scope_state(project)
    if str(project.get("category") or "") != "lexicon":
        return
    if project.get("lexicon_scope") != "service_specific":
        return
    project["category"] = "project"


def manual_seed_values(project: dict[str, Any], seed_type: str) -> list[str]:
    values: list[str] = []
    for item in project.get("manual_seeds", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != seed_type:
            continue
        raw = str(item.get("value") or "").strip()
        if not raw:
            continue
        if seed_type.endswith("_url"):
            values.append(identifier_core.normalize_url(raw))
        elif seed_type == "nsid":
            values.append(identifier_core.normalize_nsid(raw))
        else:
            values.append(raw)
    return unique_list(values)
