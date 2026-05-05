#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    from publicsuffix2 import get_sld as publicsuffix_get_sld  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    publicsuffix_get_sld = None


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "survey.db"
DEFAULT_HEADERS = {
    "Accept": "application/json, text/html;q=0.9",
    "User-Agent": "atproto-survey-bot/0.3",
}
GITHUB_API = "https://api.github.com"
DNS_RESOLVER_URL = "https://dns.google/resolve"
TRIAGE_PROMPT_PATH = ROOT / "prompts" / "project-triage.md"
DEFAULT_LLM_PROMPT_VERSION = "project-triage-v1"
BSKY_PUBLIC_API = "https://public.api.bsky.app"
BSKY_ENTRYWAY = "https://bsky.social"
_BSKY_SESSION_CACHE: dict[str, Any] = {}
TRACKING_STATES = {"active", "watchlist", "stable", "archived", "ignore"}
PDSLS_HOSTS = {"pdsls.dev", "pds.ls"}
NSID_PATTERN = r"[a-z0-9]+(?:[.-][a-z0-9]+)*(?:\.[a-z0-9]+(?:[.-][a-z0-9]+)*)+"


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(value: str) -> Any:
    return json.loads(value) if value else None


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


PROJECT_SCALAR_FIELDS = (
    "project_key",
    "status",
    "category",
    "name",
    "primary_nsid",
    "website_url",
    "service_status",
    "first_seen_at",
    "last_seen_at",
    "last_enriched_at",
)

def ensure_dirs() -> None:
    (ROOT / "data").mkdir(exist_ok=True)


def request_bytes(
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: float = 60,
) -> tuple[int, bytes, dict[str, str]]:
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)
    req = urllib.request.Request(url, headers=merged_headers, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        body = response.read()
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        return status, body, response_headers


def request_json(url: str, headers: dict[str, str] | None = None) -> Any:
    _, body, _ = request_bytes(url, headers=headers)
    return json.loads(body.decode("utf-8"))


def request_json_post(url: str, payload: Any, headers: dict[str, str] | None = None) -> Any:
    merged_headers = {"Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)
    _, body, _ = request_bytes(
        url,
        headers=merged_headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    return json.loads(body.decode("utf-8"))


def request_json_authenticated(url: str, token: str) -> Any:
    return request_json(url, headers={"Authorization": f"Bearer {token}"})


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "https").lower()
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunparse((scheme, host, path, "", "", ""))


def is_bsky_post_url(url: str) -> bool:
    normalized = normalize_url(str(url or "")) if url else ""
    if not normalized:
        return False
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc != "bsky.app":
        return False
    return bool(re.match(r"^/profile/[^/]+/post/[^/]+$", parsed.path))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


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


def add_unique(values: list[str], value: str) -> list[str]:
    return unique_list(values + [value])


def is_opaque_project_key(value: str) -> bool:
    return bool(re.fullmatch(r"proj:[0-9a-f]{12}", str(value or "")))


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
        seed_value = str(value.get("value") or "")
        key = (seed_type, seed_value)
        if not seed_type or not seed_value or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def derive_authority_domain(nsid: str) -> str:
    parts = nsid.split(".")
    if len(parts) < 3:
        return ""
    authority = parts[:-1]
    return ".".join(reversed(authority))


def derive_authority_domain_from_namespace(namespace: str) -> str:
    parts = [part for part in normalize_nsid(namespace).split(".") if part]
    if len(parts) < 2:
        return ""
    return ".".join(reversed(parts))


def normalize_nsid(nsid: str) -> str:
    value = nsid.strip()
    if value.startswith("at://"):
        value = value.removeprefix("at://")
    value = value.split("#", 1)[0]
    value = value.strip("/")
    return value


def extract_collection_nsid(value: str) -> str:
    raw = urllib.parse.unquote(str(value or "").strip())
    if not raw:
        return ""
    if raw.startswith("at://"):
        segments = [segment for segment in raw.removeprefix("at://").split("/") if segment]
        if len(segments) >= 2 and re.fullmatch(NSID_PATTERN, segments[1]):
            return normalize_nsid(segments[1])
        return ""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower() in PDSLS_HOSTS:
        path = urllib.parse.unquote(parsed.path.lstrip("/"))
        if path.startswith("at://"):
            return extract_collection_nsid(path)
    if re.fullmatch(NSID_PATTERN, raw):
        return normalize_nsid(raw)
    return ""


def canonical_seed_value(seed_type: str, seed_value: str) -> str:
    if seed_type == "nsid":
        extracted = extract_collection_nsid(seed_value)
        return extracted or normalize_nsid(seed_value)
    if seed_type.endswith("_url"):
        return normalize_url(seed_value)
    return seed_value


def lexicon_namespace_for_project(nsid: str) -> str:
    parts = normalize_nsid(nsid).split(".")
    if len(parts) >= 2:
        return ".".join(parts[:2])
    return normalize_nsid(nsid)


def common_nsid_prefix(nsids: list[str]) -> str:
    normalized = [normalize_nsid(nsid) for nsid in nsids if normalize_nsid(nsid)]
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
    normalized = normalize_nsid(nsid)
    parts = [part for part in normalized.split(".") if part]
    if len(parts) < 3:
        return ""
    return ".".join(parts[:-1])


def registrable_namespace_from_namespace(namespace: str) -> str:
    normalized = normalize_nsid(namespace)
    if not normalized:
        return ""
    authority_domain = derive_authority_domain_from_namespace(normalized)
    registrable = root_domain(authority_domain)
    if not registrable or "." not in registrable:
        return ""
    return normalize_nsid(".".join(reversed(registrable.split("."))))


def detect_seed_type(seed: str, explicit_type: str | None = None) -> str:
    if explicit_type:
        return explicit_type
    if extract_collection_nsid(seed):
        return "nsid"
    parsed = urllib.parse.urlparse(seed)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        host = parsed.netloc.lower()
        segments = [segment for segment in parsed.path.split("/") if segment]
        if host == "github.com" and len(segments) >= 2:
            return "repo_url"
        if host == "tangled.org" and len(segments) >= 2:
            return "repo_url"
        return "service_url"
    if re.fullmatch(NSID_PATTERN, seed):
        return "nsid"
    raise ValueError(f"could not detect seed type: {seed}")


def default_project_record(project_key: str, seed_type: str, seed_value: str) -> dict[str, Any]:
    return {
        "project_key": project_key,
        "status": "draft",
        "category": "",
        "app_category": "",
        "name": "",
        "tagline": "",
        "tagline_is_provisional": False,
        "summary": "",
        "summary_candidates": [],
        "tagline_candidates": [],
        "features": [],
        "inspired_by": [],
        "tags": [],
        "platforms": [],
        "project_state": "",
        "brand_did": "",
        "ownership_signal": "unknown",
        "introduced": False,
        "introduced_url": "",
        "tracking_state": "active",
        "tracking_note": "",
        "discovery_sources": ["manual_seed"],
        "evidence_urls": [],
        "primary_nsid": "",
        "input_nsids": [],
        "resolved_nsids": [],
        "lexicon_resolution_entries": [],
        "recorded_lexicon_namespaces": [],
        "lexicons": [],
        "lexicon_count": 0,
        "record_creators": [],
        "authority_domains": [],
        "website_url": "",
        "website_url_candidates": [],
        "service_urls": [],
        "service_url_candidates": [],
        "failed_service_urls": [],
        "service_status": "unknown",
        "source_code_urls": [],
        "candidate_source_code_urls": [],
        "source_repo_records": [],
        "github_repo_urls": [],
        "tangled_repo_urls": [],
        "bluesky_handles": [],
        "tangled_handles": [],
        "developer_accounts": [],
        "investigation_queries": [],
        "investigation_log": [],
        "classification_reason": "",
        "exclude_reason": "",
        "watch_reason": "",
        "confidence": 0.0,
        "llm_source": {},
        "llm_output": {},
        "last_llm_review_at": "",
        "promotion_ready": False,
        "promotion_blockers": [],
        "last_promoted_at": "",
        "promotion_events": [],
        "duplicate_candidates": [],
        "merge_recommendation": {},
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


def ensure_project_shape(project: dict[str, Any]) -> dict[str, Any]:
    defaults = default_project_record(
        str(project.get("project_key") or "unknown"),
        str(project.get("seed_type") or "unknown"),
        str(project.get("seed_value") or ""),
    )
    defaults.update(project)
    defaults["primary_nsid"] = normalize_nsid(str(defaults.get("primary_nsid") or ""))
    defaults["input_nsids"] = unique_list([normalize_nsid(str(item)) for item in defaults.get("input_nsids", []) if item])
    if defaults["primary_nsid"] and defaults["primary_nsid"] not in defaults["input_nsids"]:
        defaults["input_nsids"] = [defaults["primary_nsid"], *defaults["input_nsids"]]
    defaults["resolved_nsids"] = unique_list([normalize_nsid(str(item)) for item in defaults.get("resolved_nsids", []) if item])
    defaults["service_status"] = str(defaults.get("service_status") or "unknown")
    defaults["status"] = str(defaults.get("status") or "draft")
    defaults["tagline_is_provisional"] = bool(defaults.get("tagline_is_provisional"))
    defaults["app_category"] = str(defaults.get("app_category") or "")
    defaults["project_state"] = str(defaults.get("project_state") or "")
    defaults["brand_did"] = str(defaults.get("brand_did") or "")
    defaults["ownership_signal"] = str(defaults.get("ownership_signal") or "unknown")
    defaults["tags"] = unique_list([str(item).strip() for item in defaults.get("tags", []) if str(item).strip()])
    defaults["platforms"] = unique_list([str(item).strip() for item in defaults.get("platforms", []) if str(item).strip()])
    defaults["introduced"] = bool(defaults.get("introduced"))
    defaults["introduced_url"] = normalize_url(str(defaults.get("introduced_url") or "")) if defaults.get("introduced_url") else ""
    tracking_state = str(defaults.get("tracking_state") or "active").strip().lower()
    defaults["tracking_state"] = tracking_state if tracking_state in TRACKING_STATES else "active"
    defaults["tracking_note"] = str(defaults.get("tracking_note") or "").strip()
    defaults["promotion_ready"] = bool(defaults.get("promotion_ready"))
    defaults["promotion_blockers"] = unique_list([str(item) for item in defaults.get("promotion_blockers", []) if item])
    defaults["last_promoted_at"] = str(defaults.get("last_promoted_at") or "")
    defaults["promotion_events"] = [dict(item) for item in defaults.get("promotion_events", []) if isinstance(item, dict)]
    defaults["llm_source"] = dict(defaults.get("llm_source") or {})
    defaults["llm_output"] = dict(defaults.get("llm_output") or {})
    defaults["duplicate_candidates"] = list(defaults.get("duplicate_candidates") or [])
    defaults["merge_recommendation"] = dict(defaults.get("merge_recommendation") or {})
    defaults["collection_observations"] = [dict(item) for item in defaults.get("collection_observations", []) if isinstance(item, dict)]
    defaults["investigation_log"] = list(defaults.get("investigation_log") or [])
    defaults["investigation_queries"] = unique_list([str(item) for item in defaults.get("investigation_queries", []) if item])
    defaults["last_run_errors"] = unique_list([str(item) for item in defaults.get("last_run_errors", []) if item])
    defaults["source_code_urls"] = normalize_repo_collection(defaults.get("source_code_urls", []))
    defaults["github_repo_urls"] = normalize_repo_collection(defaults.get("github_repo_urls", []), provider="github")
    defaults["tangled_repo_urls"] = normalize_repo_collection(defaults.get("tangled_repo_urls", []), provider="tangled")
    defaults["candidate_source_code_urls"] = normalize_repo_collection(defaults.get("candidate_source_code_urls", []))
    defaults["source_repo_records"] = normalize_source_repo_records(defaults.get("source_repo_records", []))
    defaults["developer_accounts"] = list(defaults.get("developer_accounts") or [])
    if defaults["status"] == "candidate":
        defaults["status"] = "draft"
    defaults["recorded_lexicon_namespaces"] = compute_recorded_lexicon_namespaces(defaults)
    defaults["lexicon_count"] = len(defaults.get("lexicons", []))
    return defaults


def normalize_lexicon_resolution_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    output: list[dict[str, Any]] = []
    for entry in entries:
        input_nsid = normalize_nsid(str(entry.get("input_nsid") or ""))
        resolved_nsid = normalize_nsid(str(entry.get("resolved_nsid") or ""))
        repo_did = str(entry.get("repo_did") or "")
        key = (input_nsid, resolved_nsid, repo_did)
        if not resolved_nsid or key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "input_nsid": input_nsid,
                "resolved_nsid": resolved_nsid,
                "repo_did": repo_did,
                "authority_domain": str(entry.get("authority_domain") or derive_authority_domain(resolved_nsid)),
                "pds_url": str(entry.get("pds_url") or ""),
                "record_uri": str(entry.get("record_uri") or ""),
                "resolved_at": str(entry.get("resolved_at") or ""),
            }
        )
    return output


def compute_recorded_lexicon_namespaces(project: dict[str, Any]) -> list[str]:
    entries = normalize_lexicon_resolution_entries(project.get("lexicon_resolution_entries", []))
    if not entries:
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


def open_db() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            project_key TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            primary_nsid TEXT NOT NULL,
            website_url TEXT NOT NULL,
            service_status TEXT NOT NULL,
            data_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_enriched_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def project_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = load_json(str(row["data_json"] or "")) or {}
    if not isinstance(payload, dict):
        payload = {}
    for field in PROJECT_SCALAR_FIELDS:
        if field in row.keys():
            payload[field] = row[field]
    return ensure_project_shape(payload)


def project_json_payload(project: dict[str, Any]) -> dict[str, Any]:
    payload = dict(project)
    for field in PROJECT_SCALAR_FIELDS:
        payload.pop(field, None)
    return payload


def fetch_project(conn: sqlite3.Connection, project_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM projects WHERE project_key = ?",
        (project_key,),
    ).fetchone()
    if row is None:
        return None
    return project_from_row(row)


def resolve_project_key_input(conn: sqlite3.Connection, supplied: str) -> str:
    value = str(supplied or "").strip()
    if not value:
        raise ValueError("project key is required")
    if fetch_project(conn, value) is not None:
        return value

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
        ORDER BY project_key ASC
        LIMIT 21
        """,
        (f"proj:{prefix}%",),
    ).fetchall()
    matches = [str(row["project_key"]) for row in rows]
    if not matches:
        raise ValueError(f"project not found: {supplied}")
    if len(matches) > 1:
        preview = ", ".join(matches[:10])
        suffix = " ..." if len(matches) > 10 else ""
        raise ValueError(f"ambiguous project key prefix: {supplied} -> {preview}{suffix}")
    return matches[0]


def list_projects(conn: sqlite3.Connection, tracking_states: set[str] | None = None) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM projects
        ORDER BY last_seen_at DESC, project_key ASC
        """
    ).fetchall()
    projects = [project_from_row(row) for row in rows]
    if tracking_states is None:
        return projects
    return [project for project in projects if str(project.get("tracking_state") or "active") in tracking_states]


def project_matches_identifier(project: dict[str, Any], identifier_type: str, identifier_value: str) -> tuple[int, bool]:
    normalized = canonical_seed_value(identifier_type, identifier_value)
    if identifier_type == "service_url":
        if project.get("website_url") and normalize_url(str(project.get("website_url") or "")) == normalized:
            return 0, True
        if normalized in [normalize_url(str(item)) for item in project.get("service_urls", []) if item]:
            return 0, True
        if normalized in [normalize_url(str(item)) for item in project.get("service_url_candidates", []) if item]:
            return 1, True
        if normalized in [normalize_url(str(item)) for item in project.get("website_url_candidates", []) if item]:
            return 1, True
        return 99, False
    if identifier_type == "nsid":
        if normalized in [normalize_nsid(str(item)) for item in project.get("input_nsids", []) if item]:
            return 0, True
        if normalize_nsid(str(project.get("primary_nsid") or "")) == normalized:
            return 0, True
        if normalized in [normalize_nsid(str(item)) for item in project.get("resolved_nsids", []) if item]:
            return 1, True
        return 99, False
    if identifier_type == "resolved_nsid":
        return (0, True) if normalized in [normalize_nsid(str(item)) for item in project.get("resolved_nsids", []) if item] else (99, False)
    if identifier_type == "repo_url":
        repo_urls = (
            list(project.get("source_code_urls", []))
            + list(project.get("github_repo_urls", []))
            + list(project.get("tangled_repo_urls", []))
        )
        return (0, True) if normalized in [normalize_url(str(item)) for item in repo_urls if item] else (99, False)
    if identifier_type == "github_repo_url":
        return (0, True) if normalized in [normalize_url(str(item)) for item in project.get("github_repo_urls", []) if item] else (99, False)
    if identifier_type == "tangled_repo_url":
        return (0, True) if normalized in [normalize_url(str(item)) for item in project.get("tangled_repo_urls", []) if item] else (99, False)
    if identifier_type == "service_url_candidate":
        candidate_urls = list(project.get("service_url_candidates", [])) + list(project.get("website_url_candidates", []))
        return (0, True) if normalized in [normalize_url(str(item)) for item in candidate_urls if item] else (99, False)
    return 99, False


def find_project_key_by_identifier(conn: sqlite3.Connection, identifier_type: str, identifier_value: str) -> str | None:
    matches: list[tuple[int, str]] = []
    for project in all_projects(conn):
        priority, matched = project_matches_identifier(project, identifier_type, identifier_value)
        if matched:
            matches.append((priority, str(project["project_key"])))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1]))
    return matches[0][1]


def load_known_icann_tlds() -> set[str]:
    global _KNOWN_ICANN_TLDS
    if _KNOWN_ICANN_TLDS is not None:
        return _KNOWN_ICANN_TLDS

    known: set[str] = set()
    for path in PUBLIC_SUFFIX_DATA_CANDIDATES:
        if not path.exists():
            continue
        try:
            in_private = False
            for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("// ===BEGIN PRIVATE DOMAINS==="):
                    in_private = line.startswith("// ===BEGIN PRIVATE DOMAINS===")
                    continue
                if line.startswith("//"):
                    continue
                if in_private:
                    continue
                rule = line.lstrip("!.")
                label = rule.rsplit(".", 1)[-1].lower()
                if re.fullmatch(r"[a-z]{2,24}", label):
                    known.add(label)
            if known:
                break
        except Exception:
            continue

    _KNOWN_ICANN_TLDS = known
    return known


def authority_domain_allowed_for_import(domain: str) -> bool:
    lowered = str(domain or "").strip().lower().rstrip(".")
    if not lowered or "." not in lowered:
        return False
    tld = lowered.rsplit(".", 1)[-1]
    if tld in SPECIAL_USE_TLDS:
        return False
    if not re.fullmatch(r"[a-z]{2,24}", tld):
        return False
    known_tlds = load_known_icann_tlds()
    if known_tlds and tld not in known_tlds:
        return False
    return True


def find_project_key_by_collection_namespace(projects: list[dict[str, Any]], namespace: str) -> str | None:
    normalized = normalize_nsid(namespace)
    for project in projects:
        candidates = unique_list(
            ([project.get("primary_nsid")] if project.get("primary_nsid") else [])
            + list(project.get("input_nsids", []))
            + list(project.get("resolved_nsids", []))
            + list(project.get("recorded_lexicon_namespaces", []))
        )
        if normalized in [normalize_nsid(str(item)) for item in candidates if item]:
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


def build_collection_project_matchers(projects: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    namespace_pairs: list[tuple[str, str]] = []
    registrable_namespace_pairs: list[tuple[str, str]] = []
    service_host_pairs: list[tuple[str, str]] = []
    for project in projects:
        project_key = str(project["project_key"])
        namespace_candidates = unique_list(
            ([project.get("primary_nsid")] if project.get("primary_nsid") else [])
            + list(project.get("input_nsids", []))
            + list(project.get("resolved_nsids", []))
            + list(project.get("recorded_lexicon_namespaces", []))
        )
        for namespace in namespace_candidates:
            normalized = normalize_nsid(str(namespace or ""))
            if not normalized:
                continue
            namespace_pairs.append((normalized, project_key))
            registrable = registrable_namespace_from_namespace(normalized)
            if registrable:
                registrable_namespace_pairs.append((registrable, project_key))
        service_urls = unique_list(
            ([project.get("website_url")] if project.get("website_url") else [])
            + list(project.get("service_urls", []))
        )
        for service_url in service_urls:
            normalized_service = normalize_url(str(service_url or ""))
            host = urllib.parse.urlparse(normalized_service).netloc.lower()
            registrable_host = root_domain(host)
            if registrable_host:
                service_host_pairs.append((registrable_host, project_key))
    return (
        build_unique_lookup(namespace_pairs),
        build_unique_lookup(registrable_namespace_pairs),
        build_unique_lookup(service_host_pairs),
    )


def find_project_key_for_nsid_seed(projects: list[dict[str, Any]], nsid: str) -> tuple[str | None, str]:
    normalized = normalize_nsid(nsid)
    if not normalized:
        return None, ""
    namespace_lookup, registrable_namespace_lookup, service_host_lookup = build_collection_project_matchers(projects)
    project_key = namespace_lookup.get(normalized)
    if project_key:
        return project_key, "exact_namespace"
    project_key = find_project_key_by_collection_namespace(projects, normalized)
    if project_key:
        return project_key, "exact_namespace_scan"
    registrable_namespace = registrable_namespace_from_namespace(normalized)
    if registrable_namespace:
        project_key = registrable_namespace_lookup.get(registrable_namespace)
        if project_key:
            return project_key, "registrable_namespace"
    authority_domain = derive_authority_domain_from_namespace(normalized)
    service_host_key = root_domain(authority_domain)
    if service_host_key:
        project_key = service_host_lookup.get(service_host_key)
        if project_key:
            return project_key, "service_host_family"
    return None, ""

def save_project(conn: sqlite3.Connection, project: dict[str, Any], enriched: bool = False) -> None:
    now = iso_now()
    project = ensure_project_shape(project)
    project["primary_nsid"] = normalize_nsid(project.get("primary_nsid", "")) if project.get("primary_nsid") else ""
    project["input_nsids"] = unique_list([normalize_nsid(item) for item in project.get("input_nsids", []) if item])
    project["resolved_nsids"] = unique_list([normalize_nsid(item) for item in project.get("resolved_nsids", []) if item])
    project["lexicon_resolution_entries"] = normalize_lexicon_resolution_entries(project.get("lexicon_resolution_entries", []))
    project["recorded_lexicon_namespaces"] = compute_recorded_lexicon_namespaces(project)
    project["lexicons"] = unique_list(project.get("lexicons", []))
    project["authority_domains"] = unique_list(project.get("authority_domains", []))
    project["website_url_candidates"] = unique_list(project.get("website_url_candidates", []))
    project["service_urls"] = unique_list(project.get("service_urls", []))
    project["service_url_candidates"] = unique_list(project.get("service_url_candidates", []))
    project["failed_service_urls"] = unique_list(project.get("failed_service_urls", []))
    project["source_code_urls"] = normalize_repo_collection(project.get("source_code_urls", []))
    project["candidate_source_code_urls"] = normalize_repo_collection(project.get("candidate_source_code_urls", []))
    project["source_repo_records"] = normalize_source_repo_records(project.get("source_repo_records", []))
    project["github_repo_urls"] = normalize_repo_collection(project.get("github_repo_urls", []), provider="github")
    project["tangled_repo_urls"] = normalize_repo_collection(project.get("tangled_repo_urls", []), provider="tangled")
    project["bluesky_handles"] = unique_list(project.get("bluesky_handles", []))
    project["tangled_handles"] = unique_list(project.get("tangled_handles", []))
    project["investigation_queries"] = unique_list(project.get("investigation_queries", []))
    project["investigation_log"] = list(project.get("investigation_log", []))
    project["last_run_errors"] = unique_list(project.get("last_run_errors", []))
    project["developer_accounts"] = list(project.get("developer_accounts", []))
    project["tags"] = unique_list(project.get("tags", []))
    project["platforms"] = unique_list(project.get("platforms", []))
    project["introduced"] = bool(project.get("introduced"))
    project["introduced_url"] = normalize_url(str(project.get("introduced_url") or "")) if project.get("introduced_url") else ""
    project["last_promoted_at"] = str(project.get("last_promoted_at") or "")
    project["promotion_events"] = [dict(item) for item in project.get("promotion_events", []) if isinstance(item, dict)]
    project["evidence_urls"] = unique_list(project.get("evidence_urls", []))
    project["discovery_sources"] = unique_list(project.get("discovery_sources", []))
    project["features"] = unique_list(project.get("features", []))
    project["inspired_by"] = unique_list(project.get("inspired_by", []))
    project["manual_seeds"] = unique_manual_seeds(project.get("manual_seeds", []))
    project["summary_candidates"] = project.get("summary_candidates", [])
    project["tagline_candidates"] = project.get("tagline_candidates", [])
    sync_primary_service_urls(project)
    project["lexicon_count"] = len(project.get("lexicons", []))

    existing = conn.execute(
        """
        SELECT first_seen_at
        FROM projects
        WHERE project_key = ?
        """,
        (project["project_key"],),
    ).fetchone()
    first_seen_at = str(existing["first_seen_at"]) if existing else now
    last_enriched_at = now if enriched else None
    project["first_seen_at"] = first_seen_at
    project["last_seen_at"] = now
    if last_enriched_at:
        project["last_enriched_at"] = last_enriched_at
    if existing is None:
        conn.execute(
            """
            INSERT INTO projects (
                project_key, status, category, name, primary_nsid, website_url,
                service_status, data_json, first_seen_at, last_seen_at, last_enriched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project["project_key"],
                project.get("status", "draft"),
                project.get("category", ""),
                project.get("name", ""),
                project.get("primary_nsid", ""),
                project.get("website_url", ""),
                project.get("service_status", "unknown"),
                dump_json(project_json_payload(project)),
                first_seen_at,
                now,
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
            SET status = ?, category = ?, name = ?, primary_nsid = ?, website_url = ?,
                service_status = ?, data_json = ?, last_seen_at = ?, last_enriched_at = ?
            WHERE project_key = ?
            """,
            (
                project.get("status", "draft"),
                project.get("category", ""),
                project.get("name", ""),
                project.get("primary_nsid", ""),
                project.get("website_url", ""),
                project.get("service_status", "unknown"),
                dump_json(project_json_payload(project)),
                now,
                last_enriched_at or existing_last_enriched,
                project["project_key"],
            ),
        )
    conn.commit()


def allocate_new_project_key(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT project_key FROM projects").fetchall()
    existing_keys = {str(row["project_key"]) for row in rows}
    return generate_project_key(existing_keys)


def ensure_project_for_seed(
    conn: sqlite3.Connection,
    seed_type: str,
    seed_value: str,
    project_key: str | None = None,
) -> dict[str, Any]:
    normalized_value = canonical_seed_value(seed_type, seed_value)
    identifier_value = normalized_value
    resolved_project_key = resolve_project_key_input(conn, project_key) if project_key else None
    target_project_key = resolved_project_key or find_project_key_by_identifier(conn, seed_type, identifier_value)
    match_kind = ""
    if target_project_key is None and seed_type == "nsid":
        target_project_key, match_kind = find_project_key_for_nsid_seed(all_projects(conn), identifier_value)
    if target_project_key is None:
        target_project_key = allocate_new_project_key(conn)
    project = fetch_project(conn, target_project_key)
    if project is None:
        project = default_project_record(target_project_key, seed_type, identifier_value)
    project["manual_seeds"] = project.get("manual_seeds", [])
    project["manual_seeds"].append({"type": seed_type, "value": identifier_value, "added_at": iso_now()})
    project["manual_seeds"] = [dict(item) for item in project["manual_seeds"]]
    apply_seed_to_project(project, seed_type, identifier_value)
    if match_kind:
        note = f"seed matched by {match_kind}: {identifier_value}"
        existing_note = str(project.get("notes") or "").strip()
        if note not in existing_note.splitlines():
            project["notes"] = "\n".join([part for part in [existing_note, note] if part]).strip()
    save_project(conn, project, enriched=False)
    conn.commit()
    return project


def apply_seed_to_project(project: dict[str, Any], seed_type: str, seed_value: str) -> None:
    project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "manual_seed")
    if seed_type == "nsid":
        normalized_nsid = normalize_nsid(seed_value)
        project["primary_nsid"] = project.get("primary_nsid") or normalized_nsid
        project["input_nsids"] = add_unique(project.get("input_nsids", []), normalized_nsid)
        authority_domain = derive_authority_domain(normalized_nsid)
        if authority_domain:
            project["authority_domains"] = add_unique(project.get("authority_domains", []), authority_domain)
            add_candidate_url(project, "website_url", f"https://{authority_domain}")
            add_candidate_url(project, "service_url", f"https://{authority_domain}")
        if not project.get("name"):
            project["name"] = normalized_nsid.split(".")[-1]
    elif seed_type == "repo_url":
        normalized = normalize_url(seed_value)
        project["source_code_urls"] = add_unique(project.get("source_code_urls", []), normalized)
        project["evidence_urls"] = add_unique(project.get("evidence_urls", []), normalized)
        parsed = urllib.parse.urlparse(normalized)
        if parsed.netloc == "github.com":
            project["github_repo_urls"] = add_unique(project.get("github_repo_urls", []), normalized)
        elif parsed.netloc == "tangled.org":
            project["tangled_repo_urls"] = add_unique(project.get("tangled_repo_urls", []), normalized)
        if not project.get("name"):
            segments = [segment for segment in parsed.path.split("/") if segment]
            if segments:
                project["name"] = segments[-1]
    else:
        normalized = normalize_url(seed_value)
        add_candidate_url(project, "service_url", normalized)
        add_candidate_url(project, "website_url", normalized)
        project["evidence_urls"] = add_unique(project.get("evidence_urls", []), normalized)
        if not project.get("name"):
            host = urllib.parse.urlparse(normalized).netloc
            project["name"] = host

def project_lookup_identifiers(project: dict[str, Any]) -> set[tuple[str, str]]:
    identifiers: set[tuple[str, str]] = set()
    if project.get("primary_nsid"):
        identifiers.add(("nsid", normalize_nsid(project["primary_nsid"])))
    for nsid in project.get("input_nsids", []):
        identifiers.add(("nsid", normalize_nsid(nsid)))
    for nsid in project.get("resolved_nsids", []):
        identifiers.add(("resolved_nsid", normalize_nsid(nsid)))
    for url in project.get("github_repo_urls", []):
        normalized = normalize_url(url)
        identifiers.add(("repo_url", normalized))
        identifiers.add(("github_repo_url", normalized))
    for url in project.get("tangled_repo_urls", []):
        normalized = normalize_url(url)
        identifiers.add(("repo_url", normalized))
        identifiers.add(("tangled_repo_url", normalized))
    for url in project.get("source_code_urls", []):
        identifiers.add(("repo_url", normalize_url(url)))
    for url in project.get("service_urls", []):
        identifiers.add(("service_url", normalize_url(url)))
    for url in project.get("service_url_candidates", []):
        identifiers.add(("service_url_candidate", normalize_url(url)))
    if project.get("website_url"):
        identifiers.add(("service_url", normalize_url(project["website_url"])))
    for url in project.get("website_url_candidates", []):
        identifiers.add(("service_url_candidate", normalize_url(url)))
    return {item for item in identifiers if item[1]}


def github_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def preprocess_project_json_rows(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT project_key, data_json
        FROM projects
        ORDER BY project_key ASC
        """
    ).fetchall()
    updated = 0

    for row in rows:
        project_key = str(row["project_key"])
        raw_text = str(row["data_json"] or "")
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid project JSON in {project_key}: {exc}") from exc

        changed = False
        if isinstance(data, dict):
            for field in PROJECT_SCALAR_FIELDS:
                if field in data:
                    data.pop(field, None)
                    changed = True

        if changed:
            conn.execute(
                "UPDATE projects SET data_json = ? WHERE project_key = ?",
                (dump_json(data), project_key),
            )
            updated += 1

    if updated:
        conn.commit()

    return {
        "updated_rows": updated,
    }


def audit_and_repair_identifiers(conn: sqlite3.Connection) -> dict[str, Any]:
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
    for row in rows:
        raw = project_from_row(row)
        original = ensure_project_shape(raw)
        normalized = finalize_project_shape(ensure_project_shape(dict(original)))
        if dump_json(project_json_payload(raw)) != dump_json(project_json_payload(original)):
            normalized_projects += 1
        if dump_json(project_json_payload(raw)) != dump_json(project_json_payload(normalized)):
            pruned_projects += 1
            save_project(conn, normalized, enriched=False)
        project_map[normalized["project_key"]] = normalized

    expected_by_project: dict[str, set[tuple[str, str]]] = {
        project_key: project_lookup_identifiers(project)
        for project_key, project in project_map.items()
    }

    refreshed_derived_projects = 0
    for project_key in sorted(project_map):
        project = dict(project_map[project_key])
        duplicate_candidates = compute_duplicate_candidates(conn, project)
        merge_recommendation = compute_merge_recommendation(
            dict(project, duplicate_candidates=duplicate_candidates)
        )
        if (
            dump_json(project.get("duplicate_candidates", [])) != dump_json(duplicate_candidates)
            or dump_json(project.get("merge_recommendation", {})) != dump_json(merge_recommendation)
        ):
            project["duplicate_candidates"] = duplicate_candidates
            project["merge_recommendation"] = merge_recommendation
            save_project(conn, project, enriched=False)
            project_map[project_key] = project
            refreshed_derived_projects += 1

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
        "project_count": len(rows),
        "normalized_project_count": normalized_projects,
        "pruned_project_count": pruned_projects,
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


def bsky_auth_settings() -> dict[str, str]:
    return {
        "identifier": os.getenv("BSKY_HANDLE", "").strip(),
        "password": os.getenv("BSKY_APP_PASSWORD", "").strip(),
        "host": os.getenv("BSKY_AUTH_HOST", BSKY_ENTRYWAY).rstrip("/"),
    }


def bsky_create_session() -> dict[str, Any]:
    settings = bsky_auth_settings()
    if not settings["identifier"] or not settings["password"]:
        raise RuntimeError("BSKY_HANDLE and BSKY_APP_PASSWORD are required for authenticated Bluesky search")
    cache_key = f"{settings['host']}|{settings['identifier']}"
    cached = _BSKY_SESSION_CACHE.get(cache_key)
    if cached:
        return cached
    payload = request_json_post(
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


def bsky_authenticated_app_url(path: str) -> str:
    settings = bsky_auth_settings()
    base = settings["host"] or BSKY_ENTRYWAY
    return f"{base.rstrip('/')}/xrpc/{path.lstrip('/')}"


def extract_github_slug(repo_url: str) -> str | None:
    normalized = normalize_url(sanitize_extracted_url(repo_url))
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc != "github.com":
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return None
    return f"{segments[0]}/{segments[1]}"


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


STRICT_GITHUB_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
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
NON_PROJECT_SERVICE_PATH_PREFIXES = {
    "/profile/",
    "/developers/",
    "/guides/",
    "/specs/",
    "/blog/",
}
KNOWN_MULTI_TENANT_SUFFIXES = {
    "vercel.app",
    "microcosm.blue",
    "leaflet.pub",
    "pages.dev",
    "netlify.app",
    "web.app",
    "firebaseapp.com",
    "onrender.com",
    "fly.dev",
    "workers.dev",
    "github.io",
}
SPECIAL_USE_TLDS = {
    "test",
    "example",
    "invalid",
    "localhost",
    "local",
    "internal",
    "alt",
    "onion",
    "home",
    "corp",
    "lan",
}
PUBLIC_SUFFIX_DATA_CANDIDATES = [
    Path("/opt/homebrew/lib/ruby/gems/3.2.0/gems/domain_name-0.6.20231109/data/public_suffix_list.dat"),
    Path("/opt/homebrew/Library/Homebrew/vendor/bundle/ruby/4.0.0/gems/public_suffix-7.0.5/data/list.txt"),
]
_KNOWN_ICANN_TLDS: set[str] | None = None


def sanitize_extracted_url(raw_url: str) -> str:
    value = str(raw_url or "").strip().strip("<>[]{}\"'")
    while value.endswith(("**", "__")):
        value = value[:-2].rstrip()
    while value.count("(") < value.count(")") and value.endswith(")"):
        value = value[:-1].rstrip()
    while value.endswith((".", ",", ";", ":", "!", "?", "]", "}", ">")):
        value = value[:-1].rstrip()
    return value


def has_noisy_service_host(host: str) -> bool:
    lowered = host.lower()
    if lowered in NOISY_SERVICE_HOSTS:
        return True
    return any(lowered == suffix or lowered.endswith(f".{suffix}") for suffix in NOISY_SERVICE_HOST_SUFFIXES)


def is_asset_like_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    lowered_path = parsed.path.lower()
    return any(lowered_path.endswith(ext) for ext in IMAGE_EXTENSIONS)


def extract_bluesky_profile_handle(url: str) -> str:
    normalized = normalize_url(sanitize_extracted_url(url))
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc != "bsky.app":
        return ""
    if "/profile/" not in parsed.path:
        return ""
    return parsed.path.split("/profile/", 1)[-1].split("/", 1)[0].strip()


def is_explicitly_non_project_service_url(url: str) -> bool:
    normalized = normalize_url(sanitize_extracted_url(url))
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if not host:
        return True
    if extract_bluesky_profile_handle(normalized):
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
    cleaned = sanitize_extracted_url(url)
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
    cleaned = sanitize_extracted_url(url)
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
    normalized = normalize_url(cleaned)
    if is_explicitly_non_project_service_url(normalized):
        return None
    return normalized


def service_candidate_matches_project(project: dict[str, Any], url: str) -> bool:
    parsed = urllib.parse.urlparse(normalize_url(url))
    host = parsed.netloc.lower()
    if not host:
        return False
    identities = project_identity_strings(project)
    if host in identities:
        return True
    if root_domain(host) in {root_domain(value) for value in identities if "." in value}:
        return True
    return False


def extract_urls(html: str) -> list[str]:
    pattern = r'https?://[^\s"\'<>]+'
    output: list[str] = []
    for raw in re.findall(pattern, html):
        cleaned = sanitize_extracted_url(raw)
        parsed = urllib.parse.urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        output.append(normalize_url(cleaned))
    return unique_list(output)


def clean_title(title: str) -> str:
    return re.sub(r"\s*[|\-]\s*.*$", "", title).strip()


def normalize_ja_en_mixed_spacing(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    # Remove spaces only at Japanese<->ASCII boundaries.
    text = re.sub(r"(?<=[\u3040-\u30ff\u3400-\u9fff])\s+(?=[A-Za-z0-9])", "", text)
    text = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[\u3040-\u30ff\u3400-\u9fff])", "", text)
    return text


def normalize_tagline(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.split(r"[。．.!！?？\n\r]", text, maxsplit=1)[0].strip()
    text = re.sub(r"\s+", "", text)
    text = normalize_ja_en_mixed_spacing(text)
    text = re.sub(r"(です|ます|でした|ます。)$", "", text)
    if "の" in text and len(text) > 12:
        suffix = text.rsplit("の", 1)[-1].strip()
        if 2 <= len(suffix) <= 30:
            text = suffix
    text = text.strip("・-:：,，、")
    return text[:30].rstrip("・-:：,，、")


def set_project_tagline(project: dict[str, Any], value: str, *, provisional: bool) -> None:
    normalized = normalize_tagline(value)
    if not normalized:
        return
    existing = str(project.get("tagline") or "").strip()
    if existing and existing == normalized and project.get("tagline_is_provisional") is False and provisional:
        return
    project["tagline"] = normalized
    project["tagline_is_provisional"] = bool(provisional)


def normalize_github_repo_url(url: str) -> str | None:
    cleaned = sanitize_extracted_url(url)
    slug = extract_github_slug(cleaned)
    if slug is None:
        return None
    owner, repo = slug.split("/", 1)
    if not STRICT_GITHUB_SEGMENT_RE.fullmatch(owner) or not STRICT_GITHUB_SEGMENT_RE.fullmatch(repo):
        return None
    return f"https://github.com/{owner}/{repo}"


def normalize_tangled_repo_url(url: str) -> str | None:
    normalized = normalize_url(sanitize_extracted_url(url))
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc not in {"tangled.org", "tangled.sh"}:
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return None
    if parsed.netloc == "tangled.sh" and segments[0].startswith("@"):
        segments = [segments[0].lstrip("@"), *segments[1:]]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    if any(segment in {"issues", "pulls", "pull", "brand", "search", "about", "blob", "tree"} for segment in (owner, repo)):
        return None
    if not STRICT_GITHUB_SEGMENT_RE.fullmatch(owner) or not STRICT_GITHUB_SEGMENT_RE.fullmatch(repo):
        return None
    return f"https://tangled.org/{owner}/{repo}"


def manual_service_hosts(project: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    for seed in project.get("manual_seeds", []):
        if str(seed.get("type") or "") != "service_url":
            continue
        value = str(seed.get("value") or "").strip()
        if not value:
            continue
        host = urllib.parse.urlparse(normalize_url(value)).netloc.lower()
        if host:
            hosts.add(host)
    return hosts


def manual_repo_urls(project: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for seed in project.get("manual_seeds", []):
        if str(seed.get("type") or "") != "repo_url":
            continue
        value = canonical_repo_url(str(seed.get("value") or "").strip())
        if value:
            urls.add(value)
    return urls


def project_alias_tokens(project: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    if project.get("name"):
        token = normalize_alias_token(str(project.get("name") or ""))
        if token:
            tokens.add(token)
    for domain in project.get("authority_domains", []):
        first = str(domain or "").split(".", 1)[0].strip().lower()
        token = normalize_alias_token(first)
        if token:
            tokens.add(token)
    if project.get("primary_nsid"):
        token = normalize_alias_token(str(project["primary_nsid"]).split(".")[-1])
        if token:
            tokens.add(token)
    if project.get("website_url"):
        host = urllib.parse.urlparse(normalize_url(project["website_url"])).netloc.lower()
        first = host.split(".", 1)[0] if host else ""
        token = normalize_alias_token(first)
        if token:
            tokens.add(token)
    for host in manual_service_hosts(project):
        first = host.split(".", 1)[0]
        token = normalize_alias_token(first)
        if token:
            tokens.add(token)
    for url in manual_repo_urls(project):
        parsed = urllib.parse.urlparse(normalize_url(url))
        segments = [segment for segment in parsed.path.split("/") if segment]
        if segments:
            token = normalize_alias_token(segments[-1])
            if token:
                tokens.add(token)
    return tokens


def service_url_allowed_for_project(project: dict[str, Any], url: str, *, confirmed: bool) -> bool:
    normalized = normalize_confirmed_service_url(url) if confirmed else normalize_service_candidate_url(url)
    if not normalized:
        return False
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.netloc.lower()
    if not host:
        return False
    if is_explicitly_non_project_service_url(normalized):
        return False

    baseline_hosts = set(manual_service_hosts(project))
    for domain in project.get("authority_domains", []):
        lowered = str(domain or "").strip().lower()
        if lowered:
            baseline_hosts.add(lowered)

    baseline_roots = {root_domain(item) for item in baseline_hosts if item}
    if host in baseline_hosts or root_domain(host) in baseline_roots:
        return True

    alias_tokens = project_alias_tokens(project)
    if any(token and token in host.replace("-", "").replace("_", "") for token in alias_tokens):
        return True

    if not confirmed and service_candidate_matches_project(project, normalized):
        return True
    return False


def repo_url_allowed_for_project(project: dict[str, Any], url: str) -> bool:
    normalized = canonical_repo_url(url)
    if not normalized:
        return False
    if not (normalize_github_repo_url(normalized) or normalize_tangled_repo_url(normalized)):
        return False
    if normalized in manual_repo_urls(project):
        return True
    parsed = urllib.parse.urlparse(normalized)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return False
    repo_token = normalize_alias_token(segments[-1])
    alias_tokens = project_alias_tokens(project)
    if repo_token and repo_token in alias_tokens:
        return True
    return False


def normalize_repo_collection(urls: list[str], *, provider: str | None = None) -> list[str]:
    normalized_urls: list[str] = []
    for url in urls:
        parsed = urllib.parse.urlparse(normalize_url(url))
        if provider == "github" or parsed.netloc == "github.com":
            normalized = normalize_github_repo_url(url)
            if normalized:
                normalized_urls.append(normalized)
            continue
        if provider == "tangled" or parsed.netloc == "tangled.org":
            normalized = normalize_tangled_repo_url(url)
            if normalized:
                normalized_urls.append(normalized)
            continue
        normalized_urls.append(normalize_url(url))
    return unique_list(normalized_urls)


def normalize_source_repo_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for record in records:
        url = canonical_repo_url(str(record.get("url") or ""))
        if not url:
            continue
        provider = str(record.get("provider") or infer_repo_provider(url))
        confidence = str(record.get("confidence") or "candidate")
        relation = str(record.get("relation") or "candidate_repo")
        source = str(record.get("source") or "")
        evidence_kind = str(record.get("evidence_kind") or "")
        key = (url, provider, confidence, source, evidence_kind)
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
                "evidence_url": normalize_url(str(record.get("evidence_url") or "")) if record.get("evidence_url") else "",
                "recorded_at": str(record.get("recorded_at") or ""),
            }
        )
    return output


def infer_repo_provider(url: str) -> str:
    parsed = urllib.parse.urlparse(normalize_url(url))
    if parsed.netloc == "github.com":
        return "github"
    if parsed.netloc == "tangled.org":
        return "tangled"
    return parsed.netloc or "unknown"


def canonical_repo_url(url: str) -> str:
    provider = infer_repo_provider(url)
    if provider == "github":
        normalized = normalize_github_repo_url(url)
        return normalized or normalize_url(url)
    if provider == "tangled":
        normalized = normalize_tangled_repo_url(url)
        return normalized or normalize_url(url)
    return normalize_url(url)


def repo_owner_identity(url: str) -> str:
    normalized = normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.netloc == "github.com" and len(segments) >= 1:
        return segments[0].lower()
    if parsed.netloc == "tangled.org" and len(segments) >= 1:
        return segments[0].lower()
    return ""


def owner_matches_project(project: dict[str, Any], url: str) -> bool:
    owner = repo_owner_identity(url)
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


def project_identity_strings(project: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for value in project.get("authority_domains", []):
        if value:
            values.add(str(value).lower())
    for value in project.get("input_nsids", []):
        if value:
            values.add(str(value).lower())
    if project.get("primary_nsid"):
        values.add(str(project.get("primary_nsid") or "").lower())
    for value in project.get("service_urls", []):
        host = urllib.parse.urlparse(normalize_url(value)).netloc
        if host:
            values.add(host.lower())
    if project.get("website_url"):
        host = urllib.parse.urlparse(normalize_url(project["website_url"])).netloc
        if host:
            values.add(host.lower())
    for seed in project.get("manual_seeds", []):
        raw = str(seed.get("value") or "").strip().lower()
        if raw:
            values.add(raw)
            if raw.startswith("http"):
                host = urllib.parse.urlparse(normalize_url(raw)).netloc
                if host:
                    values.add(host.lower())
    if project.get("name"):
        name = str(project["name"]).strip().lower()
        if name and ("." in name or "-" in name or "_" in name or len(name) >= 8):
            values.add(name)
    return {value for value in values if value}


def handle_matches_project(project: dict[str, Any], handle: str) -> bool:
    lowered = str(handle or "").strip().lower()
    if not lowered:
        return False
    identities = project_identity_strings(project)
    if lowered in identities:
        return True
    host = lowered
    if "." in host:
        if host in identities:
            return True
    return False


def github_repo_relevance(project: dict[str, Any], repo: dict[str, Any]) -> int:
    haystacks = [
        str(repo.get("url") or "").lower(),
        str(repo.get("full_name") or "").lower(),
        str(repo.get("description") or "").lower(),
        str(repo.get("homepage") or "").lower(),
    ]
    identities = project_identity_strings(project)
    score = 0
    for needle in identities:
        if not needle:
            continue
        if any(needle in haystack for haystack in haystacks):
            score += 3
    owner = repo_owner_identity(str(repo.get("url") or ""))
    if owner and owner_matches_project(project, str(repo.get("url") or "")):
        score += 3
    homepage = str(repo.get("homepage") or "").strip()
    if homepage:
        for service_url in project.get("service_urls", []):
            if is_same_site_family(homepage, service_url):
                score += 4
                break
        if project.get("website_url") and is_same_site_family(homepage, project["website_url"]):
            score += 4
    return score


def bsky_actor_relevance(project: dict[str, Any], actor: dict[str, Any]) -> int:
    handle = str(actor.get("handle") or "").strip().lower()
    display_name = str(actor.get("display_name") or "").strip().lower()
    description = str(actor.get("description") or "").strip().lower()
    identities = project_identity_strings(project)
    score = 0
    if handle_matches_project(project, handle):
        score += 5
    for needle in identities:
        if needle and (needle in display_name or needle in description):
            score += 3
    if project.get("website_url") and handle:
        website_host = urllib.parse.urlparse(normalize_url(project["website_url"])).netloc.lower()
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        href = attr_map.get("href", "").strip()
        if not href:
            return
        self._href = urllib.parse.urljoin(self.base_url, href)
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = unescape(re.sub(r"\s+", " ", "".join(self._text_parts)).strip())
        self.links.append({"url": self._href, "text": text})
        self._href = None
        self._text_parts = []


def extract_links(html: str, base_url: str) -> list[dict[str, str]]:
    parser = AnchorExtractor(base_url)
    parser.feed(html)
    seen: set[tuple[str, str]] = set()
    output: list[dict[str, str]] = []
    for item in parser.links:
        url = normalize_url(item["url"])
        text = item["text"]
        key = (url, text)
        if key in seen:
            continue
        seen.add(key)
        output.append({"url": url, "text": text})
    return output


def root_domain(host: str) -> str:
    lowered = str(host or "").strip().lower().rstrip(".")
    if not lowered:
        return ""
    if publicsuffix_get_sld is not None:
        try:
            resolved = str(publicsuffix_get_sld(lowered, strict=False) or "").strip().lower().rstrip(".")
            if resolved:
                return resolved
        except Exception:
            pass
    for suffix in KNOWN_MULTI_TENANT_SUFFIXES:
        if lowered == suffix:
            return suffix
        if lowered.endswith(f".{suffix}"):
            labels = [label for label in lowered.split(".") if label]
            suffix_labels = [label for label in suffix.split(".") if label]
            if len(labels) > len(suffix_labels):
                return ".".join(labels[-(len(suffix_labels) + 1):])
            return lowered
    labels = [label for label in lowered.split(".") if label]
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return lowered


def is_same_site_family(url_a: str, url_b: str) -> bool:
    host_a = urllib.parse.urlparse(normalize_url(url_a)).netloc
    host_b = urllib.parse.urlparse(normalize_url(url_b)).netloc
    if not host_a or not host_b:
        return False
    if root_domain(host_a) == root_domain(host_b):
        return True
    first_a = host_a.split(".", 1)[0]
    first_b = host_b.split(".", 1)[0]
    return bool(first_a and first_b and first_a == first_b)


def classify_official_link(base_url: str, url: str, text: str) -> str:
    normalized = normalize_url(url)
    lowered_text = text.lower()
    lowered_url = normalized.lower()
    if normalize_github_repo_url(normalized) or normalize_tangled_repo_url(normalized):
        if any(token in lowered_text for token in ["source", "view source", "repo", "repository", "monorepo"]):
            return "source_repo"
    if any(token in lowered_text for token in ["source", "view source", "monorepo"]):
        return "source_like"
    if "docs" in lowered_text or "/docs" in lowered_url or normalized.startswith("https://docs."):
        return "docs"
    if "blog" in lowered_text or ".org/blog" in lowered_url or normalized.startswith("https://blog."):
        return "blog"
    if "bluesky" in lowered_text or "bsky.app/profile/" in lowered_url:
        return "bluesky_profile"
    if is_same_site_family(base_url, normalized):
        return "same_site"
    return "other"


def add_candidate_text(project: dict[str, Any], field_name: str, value: str, source: str) -> None:
    value = value.strip()
    if not value:
        return
    key = f"{field_name}_candidates"
    candidates = project.get(key, [])
    if any(item.get("value") == value and item.get("source") == source for item in candidates):
        return
    candidates.append({
        "value": value,
        "source": source,
        "status": "candidate",
        "added_at": iso_now(),
    })
    project[key] = candidates


def add_candidate_url(project: dict[str, Any], field_name: str, url: str) -> None:
    key = f"{field_name}_candidates"
    if field_name in {"service_url", "website_url"}:
        normalized = normalize_service_candidate_url(url)
        if not normalized:
            return
        project[key] = add_unique(project.get(key, []), normalized)
        return
    project[key] = add_unique(project.get(key, []), normalize_url(url))


def add_candidate_source_code_url(
    project: dict[str, Any],
    url: str,
    source: str,
    *,
    reason: str = "",
    evidence_kind: str = "",
    evidence_url: str = "",
    relation: str = "candidate_repo",
) -> None:
    normalized = canonical_repo_url(url)
    if normalized in project.get("source_code_urls", []):
        return
    project["candidate_source_code_urls"] = add_unique(project.get("candidate_source_code_urls", []), normalized)
    record = {
        "url": normalized,
        "provider": infer_repo_provider(normalized),
        "confidence": "candidate",
        "relation": relation,
        "source": source,
        "reason": reason,
        "evidence_kind": evidence_kind,
        "evidence_url": normalize_url(evidence_url) if evidence_url else "",
        "recorded_at": iso_now(),
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
        "kind": "candidate_source_code_url",
        "url": normalized,
        "source": source,
        "reason": reason,
        "evidence_kind": evidence_kind,
        "evidence_url": record["evidence_url"],
        "added_at": iso_now(),
    }
    if entry not in project["investigation_log"]:
        project["investigation_log"].append(entry)


def confirm_source_code_url(
    project: dict[str, Any],
    url: str,
    source: str,
    relation: str = "source_repo",
    *,
    reason: str = "",
    evidence_kind: str = "",
    evidence_url: str = "",
) -> None:
    normalized = canonical_repo_url(url)
    project["source_code_urls"] = add_unique(project.get("source_code_urls", []), normalized)
    project["candidate_source_code_urls"] = [item for item in project.get("candidate_source_code_urls", []) if item != normalized]
    provider = infer_repo_provider(normalized)
    if provider == "github":
        project["github_repo_urls"] = add_unique(project.get("github_repo_urls", []), normalized)
    if provider == "tangled":
        project["tangled_repo_urls"] = add_unique(project.get("tangled_repo_urls", []), normalized)
    record = {
        "url": normalized,
        "provider": provider,
        "confidence": "confirmed",
        "relation": relation,
        "source": source,
        "reason": reason,
        "evidence_kind": evidence_kind,
        "evidence_url": normalize_url(evidence_url) if evidence_url else "",
        "recorded_at": iso_now(),
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


def confirm_service_url(project: dict[str, Any], url: str) -> None:
    normalized = normalize_confirmed_service_url(url)
    if not normalized:
        return
    project["service_urls"] = add_unique(project.get("service_urls", []), normalized)
    project["service_url_candidates"] = [item for item in project.get("service_url_candidates", []) if normalize_url(item) != normalized]
    project["failed_service_urls"] = [item for item in project.get("failed_service_urls", []) if normalize_url(item) != normalized]
    if not project.get("website_url"):
        project["website_url"] = normalized
    project["website_url_candidates"] = [item for item in project.get("website_url_candidates", []) if normalize_url(item) != normalized]


def sync_primary_service_urls(project: dict[str, Any]) -> None:
    project["service_urls"] = unique_list([item for item in [normalize_confirmed_service_url(item) for item in project.get("service_urls", []) if item] if item])
    project["service_url_candidates"] = unique_list([item for item in [normalize_service_candidate_url(item) for item in project.get("service_url_candidates", []) if item] if item])
    project["website_url_candidates"] = unique_list([item for item in [normalize_service_candidate_url(item) for item in project.get("website_url_candidates", []) if item] if item])
    website_url = normalize_confirmed_service_url(str(project.get("website_url") or "")) if project.get("website_url") else ""
    if website_url:
        project["website_url"] = website_url
        project["service_urls"] = add_unique(project.get("service_urls", []), website_url)
        project["website_url_candidates"] = [item for item in project.get("website_url_candidates", []) if normalize_url(item) != website_url]
        project["service_url_candidates"] = [item for item in project.get("service_url_candidates", []) if normalize_url(item) != website_url]
    elif project.get("service_urls"):
        project["website_url"] = project["service_urls"][0]
    else:
        project["website_url"] = ""


def prune_project_urls(project: dict[str, Any]) -> None:
    for field_name in ("service_urls", "service_url_candidates", "website_url_candidates"):
        cleaned_urls: list[str] = []
        for url in project.get(field_name, []):
            handle = extract_bluesky_profile_handle(url)
            if handle:
                project["bluesky_handles"] = add_unique(project.get("bluesky_handles", []), handle)
                continue
            is_confirmed = field_name == "service_urls"
            if service_url_allowed_for_project(project, url, confirmed=is_confirmed):
                normalized = normalize_confirmed_service_url(url) if is_confirmed else normalize_service_candidate_url(url)
                if normalized:
                    cleaned_urls.append(normalized)
        project[field_name] = unique_list(cleaned_urls)

    website_url = str(project.get("website_url") or "").strip()
    if website_url and extract_bluesky_profile_handle(website_url):
        project["website_url"] = ""
    elif website_url and not service_url_allowed_for_project(project, website_url, confirmed=True):
        project["website_url"] = ""

    cleaned_records: list[dict[str, Any]] = []
    for record in normalize_source_repo_records(project.get("source_repo_records", [])):
        url = str(record.get("url") or "")
        relation = str(record.get("relation") or "")
        confidence = str(record.get("confidence") or "candidate")
        evidence_url = str(record.get("evidence_url") or "")
        same_site_evidence = bool(evidence_url and project.get("website_url") and is_same_site_family(evidence_url, project["website_url"]))
        if relation in {"official_link", "same_site_repo"} and same_site_evidence:
            record = dict(record)
            record["confidence"] = "confirmed"
        elif not repo_url_allowed_for_project(project, url):
            continue
        elif relation == "readme_link" and confidence == "confirmed" and url not in manual_repo_urls(project):
            record = dict(record)
            record["confidence"] = "candidate"
        cleaned_records.append(record)
    project["source_repo_records"] = normalize_source_repo_records(cleaned_records)

    kept_confirmed = {record["url"] for record in project["source_repo_records"] if record.get("confidence") == "confirmed"}
    kept_candidates = {record["url"] for record in project["source_repo_records"] if record.get("confidence") != "confirmed"}

    project["source_code_urls"] = [
        url
        for url in normalize_repo_collection(project.get("source_code_urls", []))
        if repo_url_allowed_for_project(project, url) and (url in manual_repo_urls(project) or url in kept_confirmed)
    ]
    project["candidate_source_code_urls"] = [
        url
        for url in normalize_repo_collection(project.get("candidate_source_code_urls", []))
        if repo_url_allowed_for_project(project, url) and url not in project["source_code_urls"]
    ]
    project["candidate_source_code_urls"] = unique_list(project["candidate_source_code_urls"] + sorted(kept_candidates - set(project["source_code_urls"])))
    project["source_code_urls"] = unique_list(project["source_code_urls"] + sorted(kept_confirmed))

    project["github_repo_urls"] = [
        url for url in normalize_repo_collection(project.get("github_repo_urls", []), provider="github")
        if repo_url_allowed_for_project(project, url) and (url in project["source_code_urls"] or url in project["candidate_source_code_urls"])
    ]
    project["tangled_repo_urls"] = [
        url for url in normalize_repo_collection(project.get("tangled_repo_urls", []), provider="tangled")
        if repo_url_allowed_for_project(project, url) and (url in project["source_code_urls"] or url in project["candidate_source_code_urls"])
    ]


def add_developer_account(project: dict[str, Any], provider: str, handle: str, source: str) -> None:
    normalized_handle = str(handle or "").strip()
    if not normalized_handle:
        return
    account = {"provider": provider, "handle": normalized_handle, "source": source}
    project.setdefault("developer_accounts", [])
    if account not in project["developer_accounts"]:
        project["developer_accounts"].append(account)


def add_investigation_query(project: dict[str, Any], query: str) -> None:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return
    project["investigation_queries"] = add_unique(project.get("investigation_queries", []), normalized_query)


def add_investigation_log(project: dict[str, Any], kind: str, **payload: Any) -> None:
    project.setdefault("investigation_log", [])
    entry = {"kind": kind, **payload}
    project["investigation_log"].append(entry)


def note_failure(project: dict[str, Any], message: str) -> None:
    errors = project.setdefault("_run_errors", [])
    if message not in errors:
        errors.append(message)


def reset_investigation_state(project: dict[str, Any]) -> None:
    project["investigation_queries"] = []
    project["investigation_log"] = []


def parse_concatenated_json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    index = 0
    objects: list[dict[str, Any]] = []
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        obj, end = decoder.raw_decode(text, index)
        if isinstance(obj, dict):
            objects.append(obj)
        index = end
    return objects


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
            url = normalize_url(str(feature.get("uri") or ""))
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


def applog_seed_from_url(url: str) -> tuple[str, str] | None:
    collection_nsid = extract_collection_nsid(url)
    if collection_nsid:
        return ("nsid", collection_nsid)
    parsed = urllib.parse.urlparse(normalize_url(url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.netloc == "github.com" and len(segments) == 2:
        normalized = normalize_github_repo_url(url)
        return ("repo_url", normalized) if normalized else None
    if parsed.netloc == "tangled.org" and len(segments) == 2:
        normalized = normalize_tangled_repo_url(url)
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
        return ("service_url", normalize_url(url))
    return None


ROOT_SERVICE_BLOCKED_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "tangled.org",
    "bsky.app",
    "public.api.bsky.app",
    "api.bsky.app",
    "api.bsky.chat",
    "discover.bsky.app",
    "docs.bsky.app",
    "docs.tangled.org",
    "blog.tangled.org",
    "discourse.atprotocol.community",
    "web.plc.directory",
    "atproto.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
}


def import_seed_from_url(url: str, *, accept_service_root_paths: bool) -> tuple[str, str] | None:
    seed = applog_seed_from_url(url)
    if seed is not None:
        return seed
    if not accept_service_root_paths:
        return None
    normalized = normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.netloc in ROOT_SERVICE_BLOCKED_HOSTS:
        return None
    return ("service_url", f"{parsed.scheme}://{parsed.netloc}")


def clean_imported_name(label: str) -> str:
    text = str(label or "").strip()
    text = text.strip("* ")
    text = text.rstrip("!！?？")
    text = re.sub(r"\s+", " ", text)
    return text


def should_replace_project_name(project: dict[str, Any], candidate_name: str) -> bool:
    candidate = clean_imported_name(candidate_name)
    if not candidate:
        return False
    current = str(project.get("name") or "").strip()
    if not current:
        return True
    current_normalized = normalize_alias_token(current)
    candidate_normalized = normalize_alias_token(candidate)
    if not current_normalized:
        return True
    if current_normalized == candidate_normalized:
        return False
    website = str(project.get("website_url") or "")
    if website:
        host = urllib.parse.urlparse(normalize_url(website)).netloc.lower()
        first = host.split(".", 1)[0] if host else ""
        if normalize_alias_token(first) == current_normalized:
            return True
    return False


def add_manual_seed(project: dict[str, Any], seed_type: str, seed_value: str, *, source: str) -> None:
    normalized_value = canonical_seed_value(seed_type, seed_value)
    project["manual_seeds"] = unique_manual_seeds(
        list(project.get("manual_seeds", [])) + [{"type": seed_type, "value": normalized_value, "added_at": iso_now()}]
    )
    apply_seed_to_project(project, seed_type, normalized_value)
    if source:
        project["discovery_sources"] = add_unique(project.get("discovery_sources", []), source)


def all_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM projects ORDER BY project_key")
    rows = cur.fetchall()
    return [project_from_row(row) for row in rows]


def project_keys_for_alias(conn: sqlite3.Connection, alias: str) -> list[str]:
    target = normalize_alias_token(alias)
    if not target:
        return []
    matches: list[str] = []
    for project in all_projects(conn):
        if target in project_merge_aliases(project):
            matches.append(project["project_key"])
    return matches


def resolve_import_project_key(
    conn: sqlite3.Connection,
    *,
    exact_identifiers: list[tuple[str, str]],
    names: list[str] | None = None,
) -> str | None:
    for identifier_type, identifier_value in exact_identifiers:
        project_key = find_project_key_by_identifier(conn, identifier_type, identifier_value)
        if project_key:
            return project_key
        if identifier_type == "nsid":
            project_key = find_project_key_by_identifier(conn, "resolved_nsid", identifier_value)
            if project_key:
                return project_key
    alias_matches: set[str] = set()
    for name in names or []:
        alias_matches.update(project_keys_for_alias(conn, name))
    if len(alias_matches) == 1:
        return next(iter(alias_matches))
    return None


def upsert_collection_observation(project: dict[str, Any], item: dict[str, Any], *, source: str) -> None:
    nsid = normalize_nsid(str(item.get("collection") or ""))
    if not nsid:
        return
    observation = {
        "collection": nsid,
        "count": int(item.get("count") or 0),
        "recent_count": int(item.get("recent_count") or 0),
        "min": str(item.get("min") or ""),
        "max": str(item.get("max") or ""),
        "members": [normalize_nsid(str(member)) for member in item.get("members", []) if normalize_nsid(str(member))],
        "source": source,
        "observed_at": iso_now(),
    }
    existing = [dict(entry) for entry in project.get("collection_observations", []) if isinstance(entry, dict)]
    replaced = False
    for index, entry in enumerate(existing):
        if normalize_nsid(str(entry.get("collection") or "")) == nsid:
            existing[index] = observation
            replaced = True
            break
    if not replaced:
        existing.append(observation)
    project["collection_observations"] = existing


def import_collection_inventory(conn: sqlite3.Connection, path: str, *, execute: bool, create_unmatched: bool) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, list):
        raise ValueError("collection inventory must be a JSON array")

    existing_projects = list_projects(conn)
    existing_keys = {str(project["project_key"]) for project in existing_projects}
    namespace_lookup, registrable_namespace_lookup, service_host_lookup = build_collection_project_matchers(existing_projects)

    aggregated: dict[str, dict[str, Any]] = {}
    rejected_entries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        collection_nsid = normalize_nsid(str(item.get("collection") or ""))
        namespace = namespace_from_collection_nsid(collection_nsid)
        if not namespace:
            rejected_entries.append({"collection": collection_nsid, "reason": "namespace_too_short"})
            continue
        registrable_namespace = registrable_namespace_from_namespace(namespace) or namespace
        authority_domain = derive_authority_domain_from_namespace(registrable_namespace)
        if not authority_domain_allowed_for_import(authority_domain):
            rejected_entries.append({"collection": collection_nsid, "namespace": registrable_namespace, "reason": f"rejected_authority_domain:{authority_domain}"})
            continue
        bucket = aggregated.setdefault(
            registrable_namespace,
            {
                "collection": registrable_namespace,
                "count": 0,
                "recent_count": 0,
                "min": str(item.get("min") or ""),
                "max": str(item.get("max") or ""),
                "members": [],
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
        bucket["members"] = add_unique(bucket.get("members", []), collection_nsid)

    created = 0
    updated = 0
    unmatched = 0
    imported_project_keys: list[str] = []
    unmatched_entries: list[dict[str, Any]] = []
    merge_candidates: list[dict[str, Any]] = []

    for namespace, item in aggregated.items():
        authority_domain = derive_authority_domain_from_namespace(namespace)
        registrable_namespace = registrable_namespace_from_namespace(namespace) or namespace
        service_host_key = root_domain(authority_domain)
        match_kind = ""
        project_key = namespace_lookup.get(namespace)
        if project_key:
            match_kind = "exact_namespace"
        if not project_key:
            project_key = find_project_key_by_collection_namespace(existing_projects, namespace)
        if project_key:
            match_kind = "exact_namespace_scan"
        if not project_key and registrable_namespace:
            project_key = registrable_namespace_lookup.get(registrable_namespace)
            if project_key:
                match_kind = "registrable_namespace"
        if not project_key and service_host_key:
            project_key = service_host_lookup.get(service_host_key)
            if project_key:
                match_kind = "service_host_family"
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
                }
            )
            continue
        if project_key is None:
            project_key = generate_project_key(existing_keys)
            existing_keys.add(project_key)
        representative_nsid = item.get("members", [namespace])[0] if item.get("members") else namespace
        project = fetch_project(conn, project_key) or default_project_record(project_key, "nsid", representative_nsid)
        add_manual_seed(project, "nsid", representative_nsid, source="collection_inventory_import")
        project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "collection_inventory_import")
        upsert_collection_observation(project, item, source="collection_inventory_import")
        if match_kind:
            note = f"collection import matched by {match_kind}: {namespace}"
            existing_note = str(project.get("notes") or "").strip()
            if note not in existing_note.splitlines():
                project["notes"] = "\n".join([part for part in [existing_note, note] if part]).strip()
        project = finalize_project_shape(project)
        project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
        project["merge_recommendation"] = compute_merge_recommendation(project)
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
            save_project(conn, project, enriched=False)
            namespace_lookup[namespace] = project["project_key"]
            if registrable_namespace:
                registrable_namespace_lookup[registrable_namespace] = project["project_key"]
            if service_host_key:
                service_host_lookup[service_host_key] = project["project_key"]
        if is_new:
            created += 1
        else:
            updated += 1

    if execute:
        conn.commit()

    merge_candidates.sort(
        key=lambda item: (
            -int(item.get("merge_recommendation", {}).get("score", 0) or 0),
            item.get("project_key", ""),
        )
    )
    return {
        "source": "collection_inventory",
        "entries": len(payload),
        "namespaces": len(aggregated),
        "created": created,
        "updated": updated,
        "unmatched": unmatched,
        "rejected": len(rejected_entries),
        "mode": "execute" if execute else "dry_run",
        "create_unmatched": create_unmatched,
        "imported_project_keys": imported_project_keys,
        "merge_candidates": merge_candidates[:50],
        "unmatched_entries": unmatched_entries[:100],
        "rejected_entries": rejected_entries[:100],
    }


def import_applog_entries(conn: sqlite3.Connection, path: str, *, execute: bool, create_unmatched: bool) -> dict[str, Any]:
    text = Path(path).read_text()
    objects = parse_concatenated_json_objects(text)
    posts: list[dict[str, Any]] = []
    for obj in objects:
        posts.extend(obj.get("posts", []))

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

    created = 0
    updated = 0
    unmatched = 0
    merge_candidates: list[dict[str, Any]] = []
    imported_project_keys: list[str] = []
    unmatched_entries: list[dict[str, Any]] = []

    for item in entries_by_seed.values():
        seed_type = str(item["seed_type"])
        seed_value = str(item["seed_value"])
        project_key = find_project_key_by_identifier(conn, seed_type, seed_value)
        is_new = project_key is None
        if is_new and not create_unmatched:
            unmatched += 1
            unmatched_entries.append(
                {
                    "seed_type": seed_type,
                    "seed_value": seed_value,
                    "label": item["labels"][0] if item["labels"] else "",
                    "introduced_url": item["post_web_urls"][0] if item["post_web_urls"] else "",
                }
            )
            continue
        if project_key is None:
            project_key = allocate_new_project_key(conn)
        project = fetch_project(conn, project_key) or default_project_record(project_key, seed_type, seed_value)
        if is_new:
            apply_seed_to_project(project, seed_type, seed_value)
            project["manual_seeds"] = unique_manual_seeds(list(project.get("manual_seeds", [])) + [{"type": seed_type, "value": seed_value, "added_at": iso_now()}])
        project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "applog_import")
        project["introduced"] = True
        if not project.get("introduced_url") and item["post_web_urls"]:
            project["introduced_url"] = item["post_web_urls"][0]
        project["evidence_urls"] = unique_list(list(project.get("evidence_urls", [])) + [seed_value])
        if item["labels"] and should_replace_project_name(project, item["labels"][0]):
            project["name"] = item["labels"][0]
        project = finalize_project_shape(project)
        project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
        project["merge_recommendation"] = compute_merge_recommendation(project)
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
            save_project(conn, project, enriched=False)
        if is_new:
            created += 1
        else:
            updated += 1

    if execute:
        conn.commit()

    merge_candidates.sort(
        key=lambda item: (
            -int(item.get("merge_recommendation", {}).get("score", 0) or 0),
            item.get("project_key", ""),
        )
    )
    return {
        "objects": len(objects),
        "posts": len(posts),
        "entries": len(entries_by_seed),
        "created": created,
        "updated": updated,
        "unmatched": unmatched,
        "mode": "execute" if execute else "dry_run",
        "create_unmatched": create_unmatched,
        "imported_project_keys": imported_project_keys,
        "merge_candidates": merge_candidates[:50],
        "unmatched_entries": unmatched_entries[:100],
    }


def infer_service_status(status_code: int, text: str) -> str:
    lowered = text.lower()
    if status_code >= 500:
        return "dead"
    if status_code >= 400:
        return "dead"
    if "invite only" in lowered or "waitlist" in lowered:
        return "invite_only"
    if "private beta" in lowered or "closed beta" in lowered:
        return "beta"
    if "beta" in lowered:
        return "beta"
    if "coming soon" in lowered or "under construction" in lowered:
        return "private"
    return "live"


def merge_service_status(current: str, new_value: str) -> str:
    rank = {
        "unknown": 0,
        "none": 1,
        "dead": 2,
        "live": 3,
        "private": 4,
        "beta": 5,
        "invite_only": 6,
    }
    current_value = current or "unknown"
    candidate_value = new_value or "unknown"
    return candidate_value if rank.get(candidate_value, 0) >= rank.get(current_value, 0) else current_value


def probe_service_url(url: str) -> dict[str, Any]:
    status_code, body, headers = request_bytes(url)
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
    return {
        "status_code": status_code,
        "title": title,
        "description": description,
        "urls": urls,
        "links": links,
        "html": html,
        "service_status": infer_service_status(status_code, f"{title}\n{description}\n{html[:5000]}"),
    }


def collect_first_party_links(project: dict[str, Any], base_url: str, links: list[dict[str, str]], source: str) -> list[str]:
    follow_up_urls: list[str] = []
    for link in links:
        url = normalize_url(link.get("url", ""))
        text = str(link.get("text", "") or "")
        if not url:
            continue
        classification = classify_official_link(base_url, url, text)
        if classification == "source_repo":
            if owner_matches_project(project, url) or is_same_site_family(base_url, url):
                confirm_source_code_url(
                    project,
                    url,
                    source,
                    relation="official_link",
                    reason=f"first-party page linked repo with anchor text: {text[:120]}",
                    evidence_kind="first_party_link",
                    evidence_url=base_url,
                )
            else:
                add_candidate_source_code_url(
                    project,
                    url,
                    source,
                    reason=f"first-party page linked external repo-like URL with anchor text: {text[:120]}",
                    evidence_kind="first_party_link",
                    evidence_url=base_url,
                    relation="linked_repo_candidate",
                )
        elif classification == "source_like":
            if is_same_site_family(base_url, url):
                follow_up_urls.append(url)
            else:
                add_candidate_source_code_url(
                    project,
                    url,
                    source,
                    reason=f"first-party page linked source-like external URL with anchor text: {text[:120]}",
                    evidence_kind="first_party_link",
                    evidence_url=base_url,
                    relation="linked_repo_candidate",
                )
        elif classification in {"docs", "blog"}:
            if is_same_site_family(base_url, url):
                follow_up_urls.append(url)
        elif classification == "bluesky_profile":
            parsed = urllib.parse.urlparse(url)
            if "/profile/" in parsed.path:
                handle = parsed.path.split("/profile/", 1)[-1].split("/", 1)[0]
                if handle:
                    project["bluesky_handles"] = add_unique(project.get("bluesky_handles", []), handle)
                    add_developer_account(project, "bluesky", handle, source)
        elif classification == "same_site":
            if (normalize_github_repo_url(url) or normalize_tangled_repo_url(url)) and owner_matches_project(project, url):
                confirm_source_code_url(
                    project,
                    url,
                    source,
                    relation="same_site_repo",
                    reason=f"same-site family link resolved to repo URL with anchor text: {text[:120]}",
                    evidence_kind="same_site_link",
                    evidence_url=base_url,
                )
            elif any(token in text.lower() for token in ["source", "repo", "repository", "core"]):
                add_candidate_source_code_url(
                    project,
                    url,
                    source,
                    reason=f"same-site page linked repo-like path with anchor text: {text[:120]}",
                    evidence_kind="same_site_link",
                    evidence_url=base_url,
                    relation="same_site_candidate",
                )
    return unique_list(follow_up_urls)


def fetch_github_repo(repo_url: str) -> dict[str, Any]:
    slug = extract_github_slug(repo_url)
    if slug is None:
        raise ValueError(f"not a GitHub repo URL: {repo_url}")
    headers = github_headers()
    payload = request_json(f"{GITHUB_API}/repos/{slug}", headers=headers)
    readme_text = ""
    try:
        readme_payload = request_json(f"{GITHUB_API}/repos/{slug}/readme", headers=headers)
        content = readme_payload.get("content", "")
        if content:
            readme_text = base64.b64decode(content).decode("utf-8", errors="replace")
    except Exception:
        readme_text = ""
    return {"repo": payload, "readme_text": readme_text}


def search_github_repositories(query: str, limit: int = 5) -> list[dict[str, Any]]:
    headers = github_headers()
    params = urllib.parse.urlencode({"q": query, "per_page": max(1, min(limit, 10)), "sort": "updated", "order": "desc"})
    payload = request_json(f"{GITHUB_API}/search/repositories?{params}", headers=headers)
    results: list[dict[str, Any]] = []
    for item in payload.get("items", [])[:limit]:
        html_url = str(item.get("html_url") or "")
        normalized_repo = normalize_github_repo_url(html_url)
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
    return results


def search_bsky_actors(query: str, limit: int = 5) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"q": query, "limit": max(1, min(limit, 10))})
    payload = request_json(f"{BSKY_PUBLIC_API}/xrpc/app.bsky.actor.searchActors?{params}")
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


def search_bsky_posts(query: str, limit: int = 5) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"q": query, "limit": max(1, min(limit, 10))})
    payload: dict[str, Any]
    auth_error: str | None = None
    try:
        payload = request_json_authenticated(
            f"{bsky_authenticated_app_url('app.bsky.feed.searchPosts')}?{params}",
            bsky_access_token(),
        )
    except Exception as exc:
        auth_error = str(exc)
        payload = request_json(f"{BSKY_PUBLIC_API}/xrpc/app.bsky.feed.searchPosts?{params}")
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
    if auth_error and results:
        for item in results:
            item["auth_warning"] = auth_error
    return results


def fetch_bsky_author_feed(actor: str, limit: int = 10) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"actor": actor, "limit": max(1, min(limit, 20))})
    payload = request_json(f"{BSKY_PUBLIC_API}/xrpc/app.bsky.feed.getAuthorFeed?{params}")
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
                "urls": extract_urls(text),
                "author_handle": str(author.get("handle") or actor),
            }
        )
    return results


def resolve_dns_txt(name: str) -> list[str]:
    params = urllib.parse.urlencode({"name": name, "type": "TXT"})
    payload = request_json(f"{DNS_RESOLVER_URL}?{params}")
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


def resolve_did_document(did: str) -> dict[str, Any]:
    return request_json(resolve_did_document_url(did))


def extract_pds_url(did_doc: dict[str, Any]) -> str:
    for service in did_doc.get("service", []):
        service_type = str(service.get("type") or "")
        service_id = str(service.get("id") or "")
        if service_type == "AtprotoPersonalDataServer" or service_id.endswith("#atproto_pds"):
            return str(service.get("serviceEndpoint") or "").rstrip("/")
    return ""


def resolve_lexicon_authority_did(authority_domain: str) -> str:
    txt_name = f"_lexicon.{authority_domain}"
    for value in resolve_dns_txt(txt_name):
        if value.startswith("did="):
            return value.removeprefix("did=").strip()
    raise ValueError(f"no lexicon DID found for authority domain: {authority_domain}")


def fetch_lexicon_record(nsid: str) -> dict[str, Any]:
    normalized = normalize_nsid(nsid)
    authority_domain = derive_authority_domain(normalized)
    if not authority_domain:
        raise ValueError(f"invalid NSID: {nsid}")
    did = resolve_lexicon_authority_did(authority_domain)
    did_doc = resolve_did_document(did)
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
    payload = request_json(f"{pds_url}/xrpc/com.atproto.repo.getRecord?{params}")
    return {
        "did": did,
        "did_doc": did_doc,
        "pds_url": pds_url,
        "record": payload,
    }


def enrich_from_lexicon(project: dict[str, Any], nsid: str) -> None:
    nsid = normalize_nsid(nsid)
    if not nsid:
        return
    result = fetch_lexicon_record(nsid)
    record = result["record"]
    schema = record.get("value", {})
    resolved_nsid = str(record.get("uri", "")).rsplit("/", 1)[-1] or str(schema.get("id") or normalize_nsid(nsid))
    project["resolved_nsids"] = add_unique(project.get("resolved_nsids", []), resolved_nsid)
    project["lexicons"] = add_unique(project.get("lexicons", []), resolved_nsid)
    project["record_creators"] = add_unique(project.get("record_creators", []), result["did"])
    project["lexicon_resolution_entries"] = project.get("lexicon_resolution_entries", [])
    project["lexicon_resolution_entries"].append(
        {
            "input_nsid": normalize_nsid(nsid),
            "resolved_nsid": normalize_nsid(resolved_nsid),
            "repo_did": result["did"],
            "authority_domain": derive_authority_domain(resolved_nsid),
            "pds_url": result["pds_url"],
            "record_uri": str(record.get("uri") or ""),
            "resolved_at": iso_now(),
        }
    )
    authority_domain = derive_authority_domain(resolved_nsid)
    if authority_domain:
        project["authority_domains"] = add_unique(project.get("authority_domains", []), authority_domain)
        candidate_url = f"https://{authority_domain}"
        add_candidate_url(project, "website_url", candidate_url)
        add_candidate_url(project, "service_url", candidate_url)
    description = str(schema.get("description") or "")
    add_candidate_text(project, "summary", description, f"lexicon:{resolved_nsid}")
    add_candidate_text(project, "tagline", description, f"lexicon:{resolved_nsid}")
    if not project.get("name"):
        project["name"] = resolved_nsid.split(".")[-1]


def enrich_from_github_repo(project: dict[str, Any], repo_url: str) -> None:
    data = fetch_github_repo(repo_url)
    repo = data["repo"]
    readme_text = data["readme_text"]
    description = str(repo.get("description") or "")
    homepage = str(repo.get("homepage") or "")
    full_name = str(repo.get("full_name") or "")
    confirm_source_code_url(
        project,
        repo_url,
        "github_repo_seed",
        relation="repo_seed",
        reason="registered manually as repository seed",
        evidence_kind="manual_seed",
        evidence_url=repo_url,
    )
    project["evidence_urls"] = add_unique(project.get("evidence_urls", []), normalize_url(repo_url))
    if full_name and not project.get("name"):
        project["name"] = full_name.split("/")[-1]
    add_candidate_text(project, "summary", description, f"github:{full_name or normalize_url(repo_url)}")
    add_candidate_text(project, "tagline", description, f"github:{full_name or normalize_url(repo_url)}")
    if homepage:
        normalized_homepage = normalize_service_candidate_url(homepage)
        if normalized_homepage:
            add_candidate_url(project, "service_url", normalized_homepage)
            add_candidate_url(project, "website_url", normalized_homepage)
    urls = extract_urls(readme_text)
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc == "github.com":
            normalized_repo = normalize_github_repo_url(url)
            if normalized_repo:
                if normalized_repo == normalize_github_repo_url(repo_url):
                    confirm_source_code_url(
                        project,
                        normalized_repo,
                        f"github_readme:{full_name or normalize_url(repo_url)}",
                        relation="readme_link",
                        reason="linked from repository README",
                        evidence_kind="readme_link",
                        evidence_url=repo_url,
                    )
                elif repo_url_allowed_for_project(project, normalized_repo):
                    add_candidate_source_code_url(
                        project,
                        normalized_repo,
                        f"github_readme:{full_name or normalize_url(repo_url)}",
                        reason="linked from repository README",
                        evidence_kind="readme_link",
                        evidence_url=repo_url,
                        relation="readme_link",
                    )
        elif parsed.scheme in {"http", "https"}:
            normalized_service = normalize_service_candidate_url(url)
            if normalized_service and service_candidate_matches_project(project, normalized_service):
                add_candidate_url(project, "service_url", normalized_service)
                add_candidate_url(project, "website_url", normalized_service)


def enrich_from_service(project: dict[str, Any], service_url: str) -> None:
    result = probe_service_url(service_url)
    title = clean_title(result["title"])
    description = result["description"]
    if title and not project.get("name"):
        project["name"] = title
    add_candidate_text(project, "summary", description, f"service_meta:{normalize_url(service_url)}")
    add_candidate_text(project, "tagline", description, f"service_meta:{normalize_url(service_url)}")
    project["service_status"] = merge_service_status(str(project.get("service_status") or "unknown"), str(result["service_status"] or "unknown"))
    project["evidence_urls"] = add_unique(project.get("evidence_urls", []), normalize_url(service_url))
    normalized_service_url = normalize_url(service_url)
    if result["status_code"] < 400:
        confirm_service_url(project, normalized_service_url)
    else:
        project["failed_service_urls"] = add_unique(project.get("failed_service_urls", []), normalized_service_url)
    for url in result["urls"]:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.endswith("bsky.app") and "/profile/" in parsed.path:
            handle = parsed.path.split("/profile/", 1)[-1].split("/", 1)[0]
            if handle:
                project["bluesky_handles"] = add_unique(project.get("bluesky_handles", []), handle)
                add_developer_account(project, "bluesky", handle, f"service_raw_url:{normalized_service_url}")
    follow_ups = collect_first_party_links(project, normalized_service_url, result.get("links", []), f"service_page:{normalized_service_url}")
    for follow_url in follow_ups[:3]:
        try:
            nested = probe_service_url(follow_url)
            collect_first_party_links(project, follow_url, nested.get("links", []), f"service_follow_up:{follow_url}")
            add_investigation_log(project, "service_follow_up", url=follow_url, result_count=len(nested.get("links", [])), at=iso_now())
        except Exception as exc:
            note_failure(project, f"service follow-up failed for {follow_url}: {exc}")


def canonical_repo_dids(project: dict[str, Any]) -> list[str]:
    return unique_list([str(item.get("repo_did") or "") for item in project.get("lexicon_resolution_entries", []) if item.get("repo_did")])


def service_hosts(project: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    urls = set(project.get("service_urls", []))
    if project.get("website_url"):
        urls.add(project["website_url"])
    for url in urls:
        host = urllib.parse.urlparse(normalize_url(url)).netloc.lower()
        if host:
            hosts.add(host)
    return hosts


def authority_families(project: dict[str, Any]) -> set[str]:
    families: set[str] = set()
    for domain in project.get("authority_domains", []):
        lowered = str(domain or "").strip().lower()
        if lowered:
            families.add(root_domain(lowered))
    for host in service_hosts(project):
        families.add(root_domain(host))
    return families


def normalize_alias_token(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    if len(lowered) < 4:
        return ""
    if lowered in {"post", "tool", "app", "site", "feed", "project", "service", "atproto", "bluesky", "tangled", "whtwnd"}:
        return ""
    return lowered


def project_merge_aliases(project: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    if project.get("name"):
        token = normalize_alias_token(str(project.get("name") or ""))
        if token:
            aliases.add(token)
    if project.get("website_url"):
        host = urllib.parse.urlparse(normalize_url(project["website_url"])).netloc.lower()
        first = host.split(".", 1)[0] if host else ""
        token = normalize_alias_token(first)
        if token:
            aliases.add(token)
    for url in list(project.get("source_code_urls", [])) + list(project.get("github_repo_urls", [])) + list(project.get("tangled_repo_urls", [])):
        parsed = urllib.parse.urlparse(normalize_url(url))
        segs = [s for s in parsed.path.split("/") if s]
        if segs:
            token = normalize_alias_token(segs[-1])
            if token:
                aliases.add(token)
    return aliases


def merge_score_from_reasons(reasons: dict[str, list[str]]) -> int:
    weights = {
        "source_code_urls": 8,
        "github_repo_urls": 8,
        "service_urls": 7,
        "repo_dids": 9,
        "recorded_lexicon_namespaces": 6,
        "authority_service_families": 6,
        "merge_aliases": 3,
    }
    score = 0
    for key, values in reasons.items():
        score += weights.get(key, 1) * max(1, len(values))
    return score


def merge_confidence_from_reasons(reasons: dict[str, list[str]], score: int) -> tuple[str, bool]:
    if any(key in reasons for key in {"source_code_urls", "github_repo_urls", "service_urls", "repo_dids"}):
        return "high", True
    if "authority_service_families" in reasons and (
        "recorded_lexicon_namespaces" in reasons or "repo_dids" in reasons
    ):
        return "high", True
    if score >= 8:
        return "medium", True
    if score >= 4:
        return "medium", False
    return "low", False


def build_llm_evidence_bundle(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_key": project["project_key"],
        "name_hint": project.get("name") or "",
        "manual_seeds": project.get("manual_seeds", []),
        "primary_nsid": project.get("primary_nsid") or "",
        "input_nsids": project.get("input_nsids", []),
        "resolved_nsids": project.get("resolved_nsids", []),
        "recorded_lexicon_namespaces": project.get("recorded_lexicon_namespaces", []),
        "lexicon_resolution_entries": project.get("lexicon_resolution_entries", []),
        "record_creators": project.get("record_creators", []),
        "authority_domains": project.get("authority_domains", []),
        "website_url": project.get("website_url") or "",
        "website_url_candidates": project.get("website_url_candidates", []),
        "service_urls": project.get("service_urls", []),
        "service_url_candidates": project.get("service_url_candidates", []),
        "failed_service_urls": project.get("failed_service_urls", []),
        "service_status_hint": project.get("service_status") or "unknown",
        "source_code_urls": project.get("source_code_urls", []),
        "candidate_source_code_urls": project.get("candidate_source_code_urls", []),
        "github_repo_urls": project.get("github_repo_urls", []),
        "tangled_repo_urls": project.get("tangled_repo_urls", []),
        "bluesky_handles": project.get("bluesky_handles", []),
        "tangled_handles": project.get("tangled_handles", []),
        "developer_accounts": project.get("developer_accounts", []),
        "summary_candidates": project.get("summary_candidates", []),
        "tagline_candidates": project.get("tagline_candidates", []),
        "evidence_urls": [
            url for url in project.get("evidence_urls", [])
            if not is_bsky_post_url(url)
        ],
    }


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
    evidence = build_llm_evidence_bundle(project)
    evidence_json = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True)
    template = load_triage_prompt_template()
    prompt = template.replace("{{evidence_json}}", evidence_json)
    if "For human-readable text fields, write in Japanese." not in prompt:
        prompt = (
            "For human-readable text fields, write in Japanese. "
            "Keep schema enums, URLs, handles, DIDs, and NSIDs unchanged.\n\n"
            f"{prompt}"
        )
    return prompt, evidence


def build_investigation_prompt(project: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    evidence = build_llm_evidence_bundle(project)
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
        f"Evidence:\n{json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True)}\n"
    )
    return prompt, evidence


def strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()
    return stripped


def llm_endpoint_url(endpoint: str) -> str:
    normalized = endpoint.rstrip("/")
    if normalized.endswith("/v1/chat/completions") or normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def llm_endpoint_candidates(endpoint: str) -> list[str]:
    normalized = endpoint.rstrip("/")
    candidates = [llm_endpoint_url(normalized)]
    if normalized.endswith("/v1"):
        candidates.append(f"{normalized}/completions")
    elif normalized.endswith("/v1/chat/completions"):
        base = normalized.removesuffix("/chat/completions")
        candidates.append(f"{base}/completions")
    elif normalized.endswith("/chat/completions"):
        base = normalized.removesuffix("/chat/completions")
        if not base.endswith("/v1"):
            candidates.append(f"{base}/v1/completions")
        candidates.append(f"{base}/completions")
    else:
        candidates.append(f"{normalized}/v1/completions")
    return unique_list(candidates)


def parse_openai_compatible_response(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("choices"), list) and payload["choices"]:
        first_choice = payload["choices"][0]
        message = first_choice.get("message")
        if isinstance(message, dict) and message.get("content"):
            return str(message["content"])
        if first_choice.get("text"):
            return str(first_choice["text"])
    if payload.get("output_text"):
        return str(payload["output_text"])
    raise ValueError("endpoint response did not contain message content")


def request_json_post_raw(
    url: str,
    payload: Any,
    headers: dict[str, str] | None = None,
    *,
    timeout: float = 60,
) -> tuple[Any, str]:
    merged_headers = {"Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)
    _, body, _ = request_bytes(
        url,
        headers=merged_headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        timeout=timeout,
    )
    text = body.decode("utf-8", errors="replace")
    return json.loads(text), text


def parse_completion_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("choices"), list) and payload["choices"]:
        first_choice = payload["choices"][0]
        if first_choice.get("text"):
            return str(first_choice["text"])
        message = first_choice.get("message")
        if isinstance(message, dict) and message.get("content"):
            return str(message["content"])
    raise ValueError("completion response did not contain text")


def run_local_llm_via_endpoint(prompt: str, endpoint: str, model: str, api_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    llm_timeout = float(os.getenv("ATPROTO_SURVEY_LLM_TIMEOUT", "180").strip() or "180")
    endpoint_urls = llm_endpoint_candidates(endpoint)
    chat_payloads = [
        {
            "model": model or "local-model",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        {
            "model": model or "local-model",
            "messages": [{"role": "system", "content": "Return JSON only."}, {"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
    ]
    completion_payload = {
        "model": model or "local-model",
        "prompt": prompt + "\n\nReturn JSON only.",
        "temperature": 0.2,
    }
    errors: list[str] = []

    for url in endpoint_urls:
        is_chat = url.endswith("/chat/completions")
        payloads = chat_payloads if is_chat else [completion_payload]
        for payload in payloads:
            try:
                response_payload, _ = request_json_post_raw(url, payload, headers=headers, timeout=llm_timeout)
                raw_content = parse_openai_compatible_response(response_payload) if is_chat else parse_completion_text(response_payload)
                content = strip_json_fence(raw_content)
                parsed = json.loads(content)
                meta = {
                    "transport": "openai_compatible_endpoint",
                    "endpoint": endpoint,
                    "resolved_endpoint": url,
                    "model": model or "local-model",
                    "prompt_version": os.getenv("ATPROTO_SURVEY_LLM_PROMPT_VERSION", DEFAULT_LLM_PROMPT_VERSION).strip(),
                    "generated_at": iso_now(),
                }
                return parsed, meta
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                errors.append(f"{url} -> HTTP {exc.code}: {body[:300].strip() or exc.reason}")
            except Exception as exc:
                errors.append(f"{url} -> {exc}")

    joined_errors = " | ".join(errors[:6]) if errors else "unknown endpoint failure"
    raise RuntimeError(joined_errors)


def run_local_llm(prompt: str, llm_endpoint: str | None = None, llm_model: str | None = None, llm_api_key: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    command = os.getenv("ATPROTO_SURVEY_LLM_COMMAND", "").strip()
    if command:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            shell=True,
            cwd=str(ROOT),
            timeout=300,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            raise RuntimeError(f"LLM command failed with exit code {completed.returncode}: {stderr or 'no stderr'}")
        stdout = strip_json_fence(completed.stdout)
        payload = json.loads(stdout)
        meta = {
            "transport": "command",
            "command": command,
            "model": llm_model or os.getenv("ATPROTO_SURVEY_LLM_MODEL", "").strip(),
            "prompt_version": os.getenv("ATPROTO_SURVEY_LLM_PROMPT_VERSION", DEFAULT_LLM_PROMPT_VERSION).strip(),
            "generated_at": iso_now(),
        }
        return payload, meta

    endpoint = (llm_endpoint or os.getenv("ATPROTO_SURVEY_LLM_ENDPOINT", "")).strip()
    model = (llm_model or os.getenv("ATPROTO_SURVEY_LLM_MODEL", "")).strip()
    api_key = (llm_api_key or os.getenv("ATPROTO_SURVEY_LLM_API_KEY", "")).strip()
    if endpoint:
        return run_local_llm_via_endpoint(prompt, endpoint, model, api_key)

    raise RuntimeError("ATPROTO_SURVEY_LLM_COMMAND or ATPROTO_SURVEY_LLM_ENDPOINT is required")


def clean_string_list(values: Any, *, limit: int | None = None) -> list[str]:
    output: list[str] = []
    if isinstance(values, list):
        for item in values:
            text = str(item).strip()
            if text:
                output.append(text)
    if limit is not None:
        output = output[:limit]
    return unique_list(output)


def clean_human_string_list(values: Any, *, limit: int | None = None) -> list[str]:
    output: list[str] = []
    if isinstance(values, list):
        for item in values:
            text = normalize_ja_en_mixed_spacing(str(item).strip())
            if text:
                output.append(text)
    if limit is not None:
        output = output[:limit]
    return unique_list(output)


def clean_search_query(value: str) -> str:
    query = str(value or "").strip()
    if not query:
        return ""
    query = re.sub(r"\bsite:[^\s]+", "", query)
    query = re.sub(r"\b(?:github|gitlab|bitbucket|bluesky)\b", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+", " ", query).strip()
    return query


def seed_investigation_queries(project: dict[str, Any]) -> dict[str, list[str]]:
    repo_queries: list[str] = []
    actor_queries: list[str] = []
    post_queries: list[str] = []
    focus_terms: list[str] = []

    if project.get("name"):
        repo_queries.append(str(project["name"]))
        actor_queries.append(str(project["name"]))
        post_queries.append(str(project["name"]))
        focus_terms.append(str(project["name"]))
    if project.get("primary_nsid"):
        repo_queries.append(str(project["primary_nsid"]))
        actor_queries.append(str(project["primary_nsid"]))
        post_queries.append(str(project["primary_nsid"]))
        focus_terms.append(str(project["primary_nsid"]))
    for domain in project.get("authority_domains", [])[:3]:
        repo_queries.append(domain)
        actor_queries.append(domain)
        post_queries.append(domain)
        focus_terms.append(domain)
    for handle in project.get("bluesky_handles", [])[:3]:
        actor_queries.append(handle)
        post_queries.append(handle)
        focus_terms.append(handle)

    return {
        "repo_search_queries": unique_list(repo_queries)[:5],
        "actor_search_queries": unique_list(actor_queries)[:5],
        "post_search_queries": unique_list(post_queries)[:5],
        "focus_terms": unique_list(focus_terms)[:8],
    }


def plan_investigation_queries(
    project: dict[str, Any],
    *,
    llm_endpoint: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
) -> dict[str, Any]:
    seed_plan = seed_investigation_queries(project)
    try:
        prompt, evidence = build_investigation_prompt(project)
        payload, meta = run_local_llm(prompt, llm_endpoint=llm_endpoint, llm_model=llm_model, llm_api_key=llm_api_key)
        plan = {
            "repo_search_queries": unique_list([clean_search_query(item) for item in clean_string_list(payload.get("repo_search_queries"), limit=6) if clean_search_query(item)]) or seed_plan["repo_search_queries"],
            "actor_search_queries": unique_list([clean_search_query(item) for item in clean_string_list(payload.get("actor_search_queries"), limit=6) if clean_search_query(item)]) or seed_plan["actor_search_queries"],
            "post_search_queries": unique_list([clean_search_query(item) for item in clean_string_list(payload.get("post_search_queries"), limit=6) if clean_search_query(item)]) or seed_plan["post_search_queries"],
            "focus_terms": clean_string_list(payload.get("focus_terms"), limit=8) or seed_plan["focus_terms"],
            "reason": str(payload.get("reason") or "").strip(),
            "llm_source": {**meta, "evidence": evidence},
        }
        return plan
    except Exception as exc:
        note_failure(project, f"investigation planning failed: {exc}")
        return {**seed_plan, "reason": "fallback deterministic plan", "llm_source": {}}


def merge_investigation_results(project: dict[str, Any], *, source: str, urls: list[str], handles: list[str]) -> None:
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc == "github.com":
            normalized = normalize_github_repo_url(url)
            if normalized:
                add_candidate_source_code_url(
                    project,
                    normalized,
                    source,
                    reason="mentioned in Bluesky investigation result",
                    evidence_kind="bluesky_reference",
                    evidence_url=url,
                )
        elif parsed.netloc == "tangled.org":
            normalized = normalize_tangled_repo_url(url)
            if normalized:
                add_candidate_source_code_url(
                    project,
                    normalized,
                    source,
                    reason="mentioned in Bluesky investigation result",
                    evidence_kind="bluesky_reference",
                    evidence_url=url,
                )
    for handle in handles:
        if handle_matches_project(project, handle):
            add_developer_account(project, "bluesky", handle, source)


def run_investigation_collectors(project: dict[str, Any], plan: dict[str, Any]) -> None:
    for query in clean_string_list(plan.get("repo_search_queries"), limit=6):
        add_investigation_query(project, query)
        try:
            repos = search_github_repositories(query, limit=5)
            accepted = 0
            for repo in repos:
                score = github_repo_relevance(project, repo)
                if score < 3:
                    continue
                accepted += 1
                add_candidate_source_code_url(
                    project,
                    repo["url"],
                    f"github_search:{query}",
                    reason=f"matched GitHub repository search query: {query} (score={score})",
                    evidence_kind="github_search",
                    evidence_url=repo["url"],
                )
                if repo.get("homepage"):
                    add_candidate_url(project, "service_url", repo["homepage"])
                    add_candidate_url(project, "website_url", repo["homepage"])
            add_investigation_log(project, "github_repo_search", query=query, result_count=len(repos), accepted_count=accepted, at=iso_now())
        except Exception as exc:
            note_failure(project, f"github repo search failed for {query}: {exc}")

    actor_handles_to_probe: list[str] = []
    for query in clean_string_list(plan.get("actor_search_queries"), limit=6):
        add_investigation_query(project, query)
        try:
            actors = search_bsky_actors(query, limit=5)
            accepted_actors = [item for item in actors if bsky_actor_relevance(project, item) >= 4]
            add_investigation_log(project, "bsky_actor_search", query=query, result_count=len(actors), accepted_count=len(accepted_actors), at=iso_now())
            handles = [item["handle"] for item in accepted_actors if item.get("handle")]
            merge_investigation_results(project, source=f"bsky_actor_search:{query}", urls=[], handles=handles)
            actor_handles_to_probe.extend(handles[:2])
        except Exception as exc:
            note_failure(project, f"Bluesky actor search failed for {query}: {exc}")

    for query in clean_string_list(plan.get("post_search_queries"), limit=6):
        add_investigation_query(project, query)
        try:
            posts = search_bsky_posts(query, limit=5)
            add_investigation_log(project, "bsky_post_search", query=query, result_count=len(posts), at=iso_now())
            urls: list[str] = []
            handles: list[str] = []
            for post in posts:
                urls.extend(post.get("urls", []))
                if post.get("author_handle"):
                    if handle_matches_project(project, post["author_handle"]):
                        handles.append(post["author_handle"])
            merge_investigation_results(project, source=f"bsky_post_search:{query}", urls=urls, handles=handles)
            actor_handles_to_probe.extend(handles[:2])
        except Exception as exc:
            note_failure(project, f"Bluesky post search failed for {query}: {exc}")

    for handle in unique_list(actor_handles_to_probe)[:4]:
        try:
            feed = fetch_bsky_author_feed(handle, limit=8)
            add_investigation_log(project, "bsky_author_feed", actor=handle, result_count=len(feed), at=iso_now())
            urls: list[str] = []
            for item in feed:
                urls.extend(item.get("urls", []))
            merge_investigation_results(project, source=f"bsky_author_feed:{handle}", urls=urls, handles=[handle])
        except Exception as exc:
            note_failure(project, f"Bluesky author feed failed for {handle}: {exc}")


def apply_llm_output(project: dict[str, Any], payload: dict[str, Any], meta: dict[str, Any], evidence: dict[str, Any]) -> None:
    project["llm_output"] = payload
    project["llm_source"] = {
        **meta,
        "evidence": evidence,
    }
    project["last_llm_review_at"] = str(meta.get("generated_at") or iso_now())
    name = str(payload.get("name") or "").strip()
    if name:
        project["name"] = name
    if payload.get("tagline"):
        explicit_provisional = payload.get("tagline_is_provisional")
        set_project_tagline(
            project,
            str(payload.get("tagline") or ""),
            provisional=True if explicit_provisional is None else bool(explicit_provisional),
        )
    project["summary"] = normalize_ja_en_mixed_spacing(str(payload.get("summary") or "").strip())
    project["features"] = clean_human_string_list(payload.get("features"), limit=6)
    project["inspired_by"] = clean_human_string_list(payload.get("inspired_by"), limit=6)
    project["category"] = str(payload.get("category") or "").strip()
    project["classification_reason"] = normalize_ja_en_mixed_spacing(str(payload.get("why_keep") or payload.get("classification_reason") or "").strip())
    project["exclude_reason"] = normalize_ja_en_mixed_spacing(str(payload.get("why_reject") or payload.get("exclude_reason") or "").strip())
    project["watch_reason"] = normalize_ja_en_mixed_spacing(str(payload.get("watch_reason") or "").strip())
    try:
        project["confidence"] = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        project["confidence"] = 0.0
    llm_service_status = str(payload.get("service_status") or "").strip()
    if llm_service_status:
        project["service_status"] = merge_service_status(str(project.get("service_status") or "unknown"), llm_service_status)
    for url in clean_string_list(payload.get("source_code_urls")):
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc == "github.com":
            normalized = normalize_github_repo_url(url)
            if normalized:
                if normalized in project.get("source_code_urls", []) or normalized in project.get("github_repo_urls", []):
                    project["github_repo_urls"] = add_unique(project.get("github_repo_urls", []), normalized)
                    project["source_code_urls"] = add_unique(project.get("source_code_urls", []), normalized)
                else:
                    add_candidate_source_code_url(
                        project,
                        normalized,
                        "llm_output",
                        reason="suggested by LLM from collected evidence",
                        evidence_kind="llm_suggestion",
                        evidence_url=url,
                    )
        elif parsed.netloc == "tangled.org":
            normalized = normalize_tangled_repo_url(url)
            if normalized:
                if normalized in project.get("source_code_urls", []) or normalized in project.get("tangled_repo_urls", []):
                    project["tangled_repo_urls"] = add_unique(project.get("tangled_repo_urls", []), normalized)
                    project["source_code_urls"] = add_unique(project.get("source_code_urls", []), normalized)
                else:
                    add_candidate_source_code_url(
                        project,
                        normalized,
                        "llm_output",
                        reason="suggested by LLM from collected evidence",
                        evidence_kind="llm_suggestion",
                        evidence_url=url,
                    )
        elif parsed.scheme in {"http", "https"}:
            add_candidate_source_code_url(
                project,
                url,
                "llm_output",
                reason="suggested by LLM from collected evidence",
                evidence_kind="llm_suggestion",
                evidence_url=url,
            )
    for url in clean_string_list(payload.get("service_urls")):
        normalized = normalize_url(url)
        if normalized == normalize_url(project.get("website_url") or "") or normalized in project.get("service_urls", []):
            confirm_service_url(project, normalized)
        else:
            add_candidate_url(project, "service_url", normalized)
            add_candidate_url(project, "website_url", normalized)
    for nsid in clean_string_list(payload.get("lexicons")):
        normalized_nsid = normalize_nsid(nsid)
        if normalized_nsid in project.get("resolved_nsids", []):
            project["lexicons"] = add_unique(project.get("lexicons", []), normalized_nsid)


def determine_promoted_status(project: dict[str, Any]) -> str:
    category = str(project.get("category") or "")
    if category == "reject":
        return "rejected"
    service_status = str(project.get("service_status") or "unknown")
    return {
        "live": "published",
        "invite_only": "invite_only",
        "beta": "beta",
        "dead": "inactive",
        "private": "tracked",
        "none": "tracked",
        "unknown": "tracked",
    }.get(service_status, "tracked")


def evaluate_promotion(project: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not project.get("manual_seeds"):
        blockers.append("no seeds registered")
    has_confirmed_identifier = bool(
        project.get("website_url")
        or project.get("source_code_urls")
        or project.get("github_repo_urls")
        or project.get("tangled_repo_urls")
        or project.get("resolved_nsids")
    )
    if not has_confirmed_identifier:
        blockers.append("no confirmed website/repository/lexicon")
    if str(project.get("service_status") or "unknown") in {"", "unknown"}:
        blockers.append("service_status is still unknown")
    if not str(project.get("summary") or "").strip():
        blockers.append("summary missing")
    if not str(project.get("tagline") or "").strip():
        blockers.append("tagline missing")
    if len(project.get("features", [])) < 3:
        blockers.append("fewer than 3 features")
    if not str(project.get("category") or "").strip():
        blockers.append("category missing")
    if not project.get("llm_source"):
        blockers.append("LLM review missing")
    return blockers


def apply_promotion_state(project: dict[str, Any]) -> dict[str, Any]:
    blockers = evaluate_promotion(project)
    project["promotion_blockers"] = blockers
    project["promotion_ready"] = not blockers
    if blockers:
        project["status"] = "draft"
        if not project.get("watch_reason"):
            project["watch_reason"] = "; ".join(blockers)
    else:
        project["status"] = determine_promoted_status(project)
    return project


def record_promotion_transition(project: dict[str, Any], previous_status: str) -> dict[str, Any]:
    current_status = str(project.get("status") or "draft")
    project["promoted_now"] = previous_status == "draft" and current_status != "draft"
    if project["promoted_now"]:
        event = {
            "at": iso_now(),
            "from_status": previous_status,
            "to_status": current_status,
            "service_status": str(project.get("service_status") or ""),
            "website_url": str(project.get("website_url") or ""),
            "name": str(project.get("name") or ""),
        }
        project["promotion_events"] = [event] + [dict(item) for item in project.get("promotion_events", []) if isinstance(item, dict)]
        project["last_promoted_at"] = event["at"]
    else:
        project.setdefault("promotion_events", [])
        project.setdefault("last_promoted_at", str(project.get("last_promoted_at") or ""))
    return project


def compute_duplicate_candidates(conn: sqlite3.Connection, project: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    target_github = set(project.get("github_repo_urls", []))
    target_source = set(project.get("source_code_urls", []))
    target_services = set(project.get("service_urls", []))
    if project.get("website_url"):
        target_services.add(project["website_url"])
    target_repo_dids = set(canonical_repo_dids(project))
    target_namespaces = set(project.get("recorded_lexicon_namespaces", []))
    target_families = authority_families(project)
    target_aliases = project_merge_aliases(project)
    for other in list_projects(conn):
        if other["project_key"] == project["project_key"]:
            continue
        reasons: dict[str, list[str]] = {}
        source_overlap = sorted(target_source & set(other.get("source_code_urls", [])))
        if source_overlap:
            reasons["source_code_urls"] = source_overlap
        github_overlap = sorted(target_github & set(other.get("github_repo_urls", [])))
        if github_overlap:
            reasons["github_repo_urls"] = github_overlap
        other_services = set(other.get("service_urls", []))
        if other.get("website_url"):
            other_services.add(other["website_url"])
        service_overlap = sorted(target_services & other_services)
        if service_overlap:
            reasons["service_urls"] = service_overlap
        repo_did_overlap = sorted(target_repo_dids & set(canonical_repo_dids(other)))
        if repo_did_overlap:
            reasons["repo_dids"] = repo_did_overlap
        namespace_overlap = sorted(target_namespaces & set(other.get("recorded_lexicon_namespaces", [])))
        if namespace_overlap:
            reasons["recorded_lexicon_namespaces"] = namespace_overlap
        family_overlap = sorted(target_families & authority_families(other))
        if family_overlap:
            reasons["authority_service_families"] = family_overlap
        alias_overlap = sorted(target_aliases & project_merge_aliases(other))
        if alias_overlap:
            reasons["merge_aliases"] = alias_overlap
        if reasons:
            score = merge_score_from_reasons(reasons)
            confidence, auto_merge_ready = merge_confidence_from_reasons(reasons, score)
            candidates.append(
                {
                    "project_key": other["project_key"],
                    "name": other.get("name") or "",
                    "status": other.get("status") or "",
                    "reasons": reasons,
                    "score": score,
                    "merge_confidence": confidence,
                    "auto_merge_ready": auto_merge_ready,
                }
            )
    candidates.sort(key=lambda item: (-int(item.get("score", 0)), item["project_key"]))
    return candidates


def compute_merge_recommendation(project: dict[str, Any]) -> dict[str, Any]:
    candidates = list(project.get("duplicate_candidates") or [])
    if not candidates:
        return {}
    top = candidates[0]
    recommendation = {
        "target_project_key": top.get("project_key", ""),
        "score": int(top.get("score", 0) or 0),
        "merge_confidence": str(top.get("merge_confidence") or ""),
        "auto_merge_ready": bool(top.get("auto_merge_ready")),
        "reasons": dict(top.get("reasons") or {}),
    }
    if not recommendation["auto_merge_ready"] and len(candidates) > 1:
        second = candidates[1]
        if int(second.get("score", 0) or 0) == recommendation["score"]:
            recommendation["ambiguous_with"] = [str(second.get("project_key") or "")]
    return recommendation


STRICT_REPO_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def is_strict_github_repo_url(url: str) -> bool:
    normalized = normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc != "github.com":
        return False
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return False
    owner, repo = segments[0], segments[1]
    return bool(STRICT_REPO_SEGMENT_RE.fullmatch(owner) and STRICT_REPO_SEGMENT_RE.fullmatch(repo))


def is_strict_tangled_repo_url(url: str) -> bool:
    normalized = normalize_tangled_repo_url(url)
    if not normalized:
        return False
    parsed = urllib.parse.urlparse(normalized)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 2:
        return False
    owner, repo = segments
    return bool(STRICT_REPO_SEGMENT_RE.fullmatch(owner) and STRICT_REPO_SEGMENT_RE.fullmatch(repo))


def batch_safe_merge_reasons(reasons: dict[str, list[str]]) -> dict[str, list[str]]:
    safe: dict[str, list[str]] = {}
    if reasons.get("repo_dids"):
        safe["repo_dids"] = list(reasons["repo_dids"])
    if reasons.get("github_repo_urls"):
        valid = [value for value in reasons["github_repo_urls"] if is_strict_github_repo_url(value)]
        if valid:
            safe["github_repo_urls"] = valid
    if reasons.get("source_code_urls"):
        valid = [
            value for value in reasons["source_code_urls"]
            if is_strict_github_repo_url(value) or is_strict_tangled_repo_url(value)
        ]
        if valid:
            safe["source_code_urls"] = valid
    return safe


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
            values.append(normalize_url(raw))
        elif seed_type == "nsid":
            values.append(normalize_nsid(raw))
        else:
            values.append(raw)
    return unique_list(values)


def batch_service_seed_hosts(project: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    for value in manual_seed_values(project, "service_url"):
        parsed = urllib.parse.urlparse(value)
        if parsed.netloc:
            hosts.add(parsed.netloc.lower())
    return hosts


def batch_repo_seed_urls(project: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for value in manual_seed_values(project, "repo_url"):
        normalized = normalize_repo_collection([value])
        if normalized:
            urls.add(normalized[0])
    return urls


def batch_strict_overlap(project_a: dict[str, Any], project_b: dict[str, Any]) -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}
    repo_dids = sorted(set(canonical_repo_dids(project_a)) & set(canonical_repo_dids(project_b)))
    if repo_dids:
        reasons["repo_dids"] = repo_dids
    repo_seed_overlap = sorted(batch_repo_seed_urls(project_a) & batch_repo_seed_urls(project_b))
    if repo_seed_overlap:
        reasons["manual_repo_seed_urls"] = repo_seed_overlap
    service_seed_overlap = sorted(batch_service_seed_hosts(project_a) & batch_service_seed_hosts(project_b))
    if service_seed_overlap:
        reasons["manual_service_seed_hosts"] = service_seed_overlap
    return reasons


def batch_moderate_overlap(project_a: dict[str, Any], project_b: dict[str, Any]) -> dict[str, list[str]]:
    reasons = batch_strict_overlap(project_a, project_b)
    if reasons:
        return reasons

    service_overlap = sorted(set(project_a.get("service_urls", [])) & set(project_b.get("service_urls", [])))
    if service_overlap:
        reasons["confirmed_service_urls"] = service_overlap

    host_a = batch_service_seed_hosts(project_a)
    host_b = batch_service_seed_hosts(project_b)
    alias_overlap = sorted(project_alias_tokens(project_a) & project_alias_tokens(project_b))
    root_overlap = sorted({root_domain(host) for host in host_a} & {root_domain(host) for host in host_b})
    if root_overlap and alias_overlap:
        reasons["manual_service_root_domain_and_alias"] = root_overlap
        reasons["alias_overlap"] = alias_overlap
        return reasons

    website_host_a = urllib.parse.urlparse(normalize_url(str(project_a.get("website_url") or ""))).netloc.lower() if project_a.get("website_url") else ""
    website_host_b = urllib.parse.urlparse(normalize_url(str(project_b.get("website_url") or ""))).netloc.lower() if project_b.get("website_url") else ""
    if website_host_a and website_host_b and root_domain(website_host_a) == root_domain(website_host_b) and alias_overlap:
        reasons["website_root_domain_and_alias"] = [root_domain(website_host_a)]
        reasons["alias_overlap"] = alias_overlap

    return reasons


def project_batch_merge_preference(project: dict[str, Any]) -> tuple[int, int, int, str]:
    seed_types = {str(item.get("type") or "") for item in project.get("manual_seeds", []) if isinstance(item, dict)}
    seed_priority = 0
    if "service_url" in seed_types:
        seed_priority = 3
    elif "nsid" in seed_types:
        seed_priority = 2
    elif "repo_url" in seed_types:
        seed_priority = 1
    confirmed_count = sum(
        1 for value in [
            bool(project.get("website_url")),
            bool(project.get("source_code_urls")),
            bool(project.get("github_repo_urls")),
            bool(project.get("tangled_repo_urls")),
            bool(project.get("resolved_nsids")),
        ]
        if value
    )
    introduced = 1 if project.get("introduced") else 0
    return (seed_priority, confirmed_count, introduced, str(project.get("project_key") or ""))


def choose_batch_merge_direction(project_a: dict[str, Any], project_b: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if project_batch_merge_preference(project_a) >= project_batch_merge_preference(project_b):
        return project_b, project_a
    return project_a, project_b


def build_batch_merge_plan(conn: sqlite3.Connection, mode: str = "strict") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    projects = {project["project_key"]: ensure_project_shape(project) for project in list_projects(conn)}
    skipped: list[dict[str, Any]] = []
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    analyzed: dict[str, dict[str, Any]] = {}

    for project_key, project in projects.items():
        candidates = compute_duplicate_candidates(conn, project)
        analyzed[project_key] = {
            "project": project,
            "candidates": candidates,
            "recommendation": compute_merge_recommendation(dict(project, duplicate_candidates=candidates)),
        }

    for project_key, payload in analyzed.items():
        project = payload["project"]
        recommendation = payload["recommendation"]
        target_key = str(recommendation.get("target_project_key") or "")
        if not target_key:
            continue
        target_payload = analyzed.get(target_key)
        if not target_payload:
            continue
        target = target_payload["project"]
        reverse_recommendation = target_payload["recommendation"]
        if str(reverse_recommendation.get("target_project_key") or "") != project_key:
            skipped.append(
                {
                    "project_key": project_key,
                    "target_project_key": target_key,
                    "reason": "not_mutual_recommendation",
                    "recommendation": recommendation,
                }
            )
            continue
        safe_reasons = batch_strict_overlap(project, target) if mode == "strict" else batch_moderate_overlap(project, target)
        if not safe_reasons:
            skipped.append(
                {
                    "project_key": project_key,
                    "target_project_key": target_key,
                    "reason": "no_safe_identity_overlap",
                    "recommendation": recommendation,
                }
            )
            continue

        source, target_project = choose_batch_merge_direction(project, target)
        pair_key = tuple(sorted([source["project_key"], target_project["project_key"]]))
        plan = {
            "source_project_key": source["project_key"],
            "target_project_key": target_project["project_key"],
            "source_name": source.get("name", ""),
            "target_name": target_project.get("name", ""),
            "reasons": safe_reasons,
            "recommendation": recommendation,
        }
        existing = selected.get(pair_key)
        if existing is None or int(recommendation.get("score", 0) or 0) > int(existing["recommendation"].get("score", 0) or 0):
            selected[pair_key] = plan

    plans = sorted(
        selected.values(),
        key=lambda item: (
            -int(item["recommendation"].get("score", 0) or 0),
            item["source_project_key"],
            item["target_project_key"],
        ),
    )
    return plans, skipped


def choose_preferred_scalar(current: Any, incoming: Any) -> Any:
    current_value = current if current not in {None, ""} else ""
    incoming_value = incoming if incoming not in {None, ""} else ""
    return current_value or incoming_value


def merge_timestamp(current: str, incoming: str, *, prefer: str) -> str:
    current_value = str(current or "")
    incoming_value = str(incoming or "")
    if not current_value:
        return incoming_value
    if not incoming_value:
        return current_value
    if prefer == "min":
        return min(current_value, incoming_value)
    return max(current_value, incoming_value)


def merge_project_records(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    merged = ensure_project_shape(dict(target))
    source = ensure_project_shape(source)

    merged["manual_seeds"] = unique_manual_seeds(list(merged.get("manual_seeds", [])) + list(source.get("manual_seeds", [])))
    merged["discovery_sources"] = unique_list(list(merged.get("discovery_sources", [])) + list(source.get("discovery_sources", [])))
    merged["evidence_urls"] = unique_list(list(merged.get("evidence_urls", [])) + list(source.get("evidence_urls", [])))

    merged["primary_nsid"] = choose_preferred_scalar(merged.get("primary_nsid"), source.get("primary_nsid"))
    merged["input_nsids"] = unique_list(list(merged.get("input_nsids", [])) + list(source.get("input_nsids", [])))
    merged["resolved_nsids"] = unique_list(list(merged.get("resolved_nsids", [])) + list(source.get("resolved_nsids", [])))
    merged["lexicon_resolution_entries"] = normalize_lexicon_resolution_entries(
        list(merged.get("lexicon_resolution_entries", [])) + list(source.get("lexicon_resolution_entries", []))
    )
    merged["authority_domains"] = unique_list(list(merged.get("authority_domains", [])) + list(source.get("authority_domains", [])))
    merged["record_creators"] = unique_list(list(merged.get("record_creators", [])) + list(source.get("record_creators", [])))

    merged["name"] = choose_preferred_scalar(merged.get("name"), source.get("name"))
    target_tagline = str(merged.get("tagline") or "")
    source_tagline = str(source.get("tagline") or "")
    if (not target_tagline and source_tagline) or (merged.get("tagline_is_provisional") and not source.get("tagline_is_provisional") and source_tagline):
        merged["tagline"] = source_tagline
        merged["tagline_is_provisional"] = bool(source.get("tagline_is_provisional"))
    merged["summary"] = choose_preferred_scalar(merged.get("summary"), source.get("summary"))
    merged["summary_candidates"] = list(merged.get("summary_candidates", [])) + list(source.get("summary_candidates", []))
    merged["tagline_candidates"] = list(merged.get("tagline_candidates", [])) + list(source.get("tagline_candidates", []))
    merged["features"] = unique_list(list(merged.get("features", [])) + list(source.get("features", [])))
    merged["inspired_by"] = unique_list(list(merged.get("inspired_by", [])) + list(source.get("inspired_by", [])))

    merged["website_url"] = choose_preferred_scalar(merged.get("website_url"), source.get("website_url"))
    merged["website_url_candidates"] = unique_list(list(merged.get("website_url_candidates", [])) + list(source.get("website_url_candidates", [])))
    merged["service_urls"] = unique_list(list(merged.get("service_urls", [])) + list(source.get("service_urls", [])))
    merged["service_url_candidates"] = unique_list(list(merged.get("service_url_candidates", [])) + list(source.get("service_url_candidates", [])))
    merged["failed_service_urls"] = unique_list(list(merged.get("failed_service_urls", [])) + list(source.get("failed_service_urls", [])))
    merged["service_status"] = merge_service_status(str(merged.get("service_status") or "unknown"), str(source.get("service_status") or "unknown"))

    merged["source_code_urls"] = normalize_repo_collection(list(merged.get("source_code_urls", [])) + list(source.get("source_code_urls", [])))
    merged["candidate_source_code_urls"] = normalize_repo_collection(
        list(merged.get("candidate_source_code_urls", [])) + list(source.get("candidate_source_code_urls", []))
    )
    merged["source_repo_records"] = normalize_source_repo_records(
        list(merged.get("source_repo_records", [])) + list(source.get("source_repo_records", []))
    )
    merged["github_repo_urls"] = normalize_repo_collection(list(merged.get("github_repo_urls", [])) + list(source.get("github_repo_urls", [])), provider="github")
    merged["tangled_repo_urls"] = normalize_repo_collection(list(merged.get("tangled_repo_urls", [])) + list(source.get("tangled_repo_urls", [])), provider="tangled")

    merged["bluesky_handles"] = unique_list(list(merged.get("bluesky_handles", [])) + list(source.get("bluesky_handles", [])))
    merged["tangled_handles"] = unique_list(list(merged.get("tangled_handles", [])) + list(source.get("tangled_handles", [])))
    merged["developer_accounts"] = list(merged.get("developer_accounts", [])) + [item for item in source.get("developer_accounts", []) if item not in merged.get("developer_accounts", [])]

    merged["introduced"] = bool(merged.get("introduced") or source.get("introduced"))
    merged["introduced_url"] = choose_preferred_scalar(merged.get("introduced_url"), source.get("introduced_url"))

    merged["app_category"] = choose_preferred_scalar(merged.get("app_category"), source.get("app_category"))
    merged["category"] = choose_preferred_scalar(merged.get("category"), source.get("category"))
    merged["project_state"] = choose_preferred_scalar(merged.get("project_state"), source.get("project_state"))
    merged["brand_did"] = choose_preferred_scalar(merged.get("brand_did"), source.get("brand_did"))
    merged["ownership_signal"] = choose_preferred_scalar(
        "" if str(merged.get("ownership_signal") or "unknown") == "unknown" else merged.get("ownership_signal"),
        "" if str(source.get("ownership_signal") or "unknown") == "unknown" else source.get("ownership_signal"),
    ) or "unknown"
    merged["tags"] = unique_list(list(merged.get("tags", [])) + list(source.get("tags", [])))
    merged["platforms"] = unique_list(list(merged.get("platforms", [])) + list(source.get("platforms", [])))

    merged["classification_reason"] = choose_preferred_scalar(merged.get("classification_reason"), source.get("classification_reason"))
    merged["exclude_reason"] = choose_preferred_scalar(merged.get("exclude_reason"), source.get("exclude_reason"))
    merged["watch_reason"] = choose_preferred_scalar(merged.get("watch_reason"), source.get("watch_reason"))
    merged["confidence"] = max(float(merged.get("confidence") or 0.0), float(source.get("confidence") or 0.0))
    merged["llm_source"] = dict(merged.get("llm_source") or source.get("llm_source") or {})
    merged["llm_output"] = dict(merged.get("llm_output") or source.get("llm_output") or {})

    merged["first_seen_at"] = merge_timestamp(str(merged.get("first_seen_at") or ""), str(source.get("first_seen_at") or ""), prefer="min")
    merged["last_seen_at"] = merge_timestamp(str(merged.get("last_seen_at") or ""), str(source.get("last_seen_at") or ""), prefer="max")
    merged["last_enriched_at"] = merge_timestamp(str(merged.get("last_enriched_at") or ""), str(source.get("last_enriched_at") or ""), prefer="max")
    merged["last_llm_review_at"] = merge_timestamp(str(merged.get("last_llm_review_at") or ""), str(source.get("last_llm_review_at") or ""), prefer="max")

    source_key = str(source.get("project_key") or "")
    merged_note = f"merged from {source_key} at {iso_now()}"
    merged["notes"] = "\n".join([item for item in [str(merged.get("notes") or "").strip(), str(source.get("notes") or "").strip(), merged_note] if item]).strip()
    merged["last_run_errors"] = []
    merged["duplicate_candidates"] = []
    merged["merge_recommendation"] = {}
    return finalize_project_shape(merged)


def delete_project(conn: sqlite3.Connection, project_key: str) -> None:
    conn.execute("DELETE FROM projects WHERE project_key = ?", (project_key,))


def finalize_project_shape(project: dict[str, Any]) -> dict[str, Any]:
    introduced_url = normalize_url(str(project.get("introduced_url") or "")) if project.get("introduced_url") else ""
    project["introduced_url"] = introduced_url
    project["evidence_urls"] = [
        url for url in unique_list(project.get("evidence_urls", []))
        if url and normalize_url(url) != introduced_url and not is_bsky_post_url(url)
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
        evidence["project_key"] = project["project_key"]
        evidence["evidence_urls"] = [
            url for url in evidence.get("evidence_urls", [])
            if url and normalize_url(str(url)) != introduced_url and not is_bsky_post_url(str(url))
        ]
        project["llm_source"] = dict(project["llm_source"], evidence=evidence)

    sync_primary_service_urls(project)
    prune_project_urls(project)
    sync_primary_service_urls(project)
    project["recorded_lexicon_namespaces"] = compute_recorded_lexicon_namespaces(project)
    project["lexicons"] = unique_list(project.get("resolved_nsids", []))
    project["lexicon_count"] = len(project.get("lexicons", []))
    project["source_repo_records"] = normalize_source_repo_records(project.get("source_repo_records", []))
    confirmed_urls = [record["url"] for record in project["source_repo_records"] if record.get("confidence") == "confirmed"]
    candidate_urls = [record["url"] for record in project["source_repo_records"] if record.get("confidence") != "confirmed"]
    project["source_code_urls"] = normalize_repo_collection(unique_list(project.get("source_code_urls", []) + confirmed_urls))
    project["candidate_source_code_urls"] = [
        url for url in normalize_repo_collection(unique_list(project.get("candidate_source_code_urls", []) + candidate_urls))
        if url not in project.get("source_code_urls", [])
    ]
    project["github_repo_urls"] = normalize_repo_collection(
        unique_list(project.get("github_repo_urls", []) + [record["url"] for record in project["source_repo_records"] if record.get("provider") == "github" and record.get("confidence") == "confirmed"]),
        provider="github",
    )
    project["tangled_repo_urls"] = normalize_repo_collection(
        unique_list(project.get("tangled_repo_urls", []) + [record["url"] for record in project["source_repo_records"] if record.get("provider") == "tangled" and record.get("confidence") == "confirmed"]),
        provider="tangled",
    )
    if not project.get("primary_nsid") and project.get("input_nsids"):
        project["primary_nsid"] = project["input_nsids"][0]
    if not project.get("name"):
        if project.get("primary_nsid"):
            project["name"] = project["primary_nsid"].split(".")[-1]
        elif project.get("website_url"):
            project["name"] = urllib.parse.urlparse(project["website_url"]).netloc
        elif project.get("website_url_candidates"):
            project["name"] = urllib.parse.urlparse(project["website_url_candidates"][0]).netloc
        elif project.get("github_repo_urls"):
            slug = extract_github_slug(project["github_repo_urls"][0])
            if slug:
                project["name"] = slug.split("/")[-1]
    return project


def enrich_project(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    *,
    llm_endpoint: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
) -> dict[str, Any]:
    project = ensure_project_shape(project)
    previous_status = str(project.get("status") or "draft")
    project["_run_errors"] = []
    reset_investigation_state(project)

    for nsid in unique_list(([project.get("primary_nsid")] if project.get("primary_nsid") else []) + project.get("input_nsids", [])):
        try:
            enrich_from_lexicon(project, nsid)
        except Exception as exc:
            note_failure(project, f"lexicon enrichment failed for {nsid}: {exc}")

    initial_repo_urls = unique_list(project.get("github_repo_urls", []) + project.get("source_code_urls", []))
    for repo_url in initial_repo_urls:
        if extract_github_slug(repo_url) is None:
            continue
        try:
            enrich_from_github_repo(project, repo_url)
        except Exception as exc:
            note_failure(project, f"github enrichment failed for {repo_url}: {exc}")

    initial_service_urls = unique_list(
        ([project.get("website_url")] if project.get("website_url") else [])
        + project.get("service_urls", [])
        + project.get("website_url_candidates", [])
        + project.get("service_url_candidates", [])
    )
    for service_url in initial_service_urls:
        try:
            enrich_from_service(project, service_url)
        except Exception as exc:
            note_failure(project, f"service probe failed for {service_url}: {exc}")

    secondary_repo_urls = unique_list(project.get("github_repo_urls", []))
    for repo_url in secondary_repo_urls:
        try:
            enrich_from_github_repo(project, repo_url)
        except Exception as exc:
            note_failure(project, f"github secondary enrichment failed for {repo_url}: {exc}")

    project = finalize_project_shape(project)

    investigation_plan = plan_investigation_queries(
        project,
        llm_endpoint=llm_endpoint,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
    )
    add_investigation_log(
        project,
        "investigation_plan",
        reason=str(investigation_plan.get("reason") or ""),
        repo_search_queries=clean_string_list(investigation_plan.get("repo_search_queries"), limit=6),
        post_search_queries=clean_string_list(investigation_plan.get("post_search_queries"), limit=6),
        at=iso_now(),
    )
    run_investigation_collectors(project, investigation_plan)

    try:
        prompt, evidence = build_triage_prompt(project)
        payload, meta = run_local_llm(prompt, llm_endpoint=llm_endpoint, llm_model=llm_model, llm_api_key=llm_api_key)
        apply_llm_output(project, payload, meta, evidence)
    except Exception as exc:
        note_failure(project, f"LLM review failed: {exc}")

    project = finalize_project_shape(project)
    project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
    project["merge_recommendation"] = compute_merge_recommendation(project)
    project["last_run_errors"] = unique_list(project.pop("_run_errors", []))
    project = apply_promotion_state(project)
    return record_promotion_transition(project, previous_status)


def cmd_add_seed(args: argparse.Namespace) -> int:
    conn = open_db()
    seed_type = detect_seed_type(args.seed, args.type)
    project = ensure_project_for_seed(conn, seed_type, args.seed, project_key=args.project_key)
    print(json.dumps({
        "project_key": project["project_key"],
        "seed_type": seed_type,
        "seed_value": canonical_seed_value(seed_type, args.seed),
        "name": project.get("name", ""),
    }, ensure_ascii=False))
    return 0


def select_projects(conn: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.project_key:
        project = fetch_project(conn, resolve_project_key_input(conn, args.project_key))
        if project is None:
            raise ValueError(f"project not found: {args.project_key}")
        return [project]
    if args.seed:
        seed_type = detect_seed_type(args.seed, args.type)
        identifier_value = canonical_seed_value(seed_type, args.seed)
        project_key = find_project_key_by_identifier(conn, seed_type, identifier_value)
        if project_key is None:
            project = ensure_project_for_seed(conn, seed_type, args.seed)
            return [project]
        project = fetch_project(conn, project_key)
        return [project] if project else []
    if args.all:
        tracking_states = requested_tracking_states(args)
        return list_projects(conn, tracking_states=tracking_states)
    raise ValueError("choose one of --project-key, --seed, or --all")


def requested_tracking_states(args: argparse.Namespace) -> set[str] | None:
    explicit = getattr(args, "tracking_states", None)
    if explicit:
        cleaned = {str(item).strip().lower() for item in explicit if str(item).strip()}
        return {item for item in cleaned if item in TRACKING_STATES} or {"active", "watchlist"}
    if getattr(args, "all", False) and getattr(args, "command", "") == "enrich":
        states = {"active", "watchlist"}
        if getattr(args, "include_stable", False):
            states.add("stable")
        if getattr(args, "include_archived", False):
            states.add("archived")
        if getattr(args, "include_ignore", False):
            states.add("ignore")
        return states
    return None


def cmd_enrich(args: argparse.Namespace) -> int:
    conn = open_db()
    projects = select_projects(conn, args)
    promoted_now: list[dict[str, Any]] = []
    for project in projects:
        project = enrich_project(
            conn,
            project,
            llm_endpoint=args.llm_endpoint,
            llm_model=args.llm_model,
            llm_api_key=args.llm_api_key,
        )
        save_project(conn, project, enriched=True)
        conn.commit()
        if project.get("promoted_now"):
            promoted_now.append(
                {
                    "project_key": project["project_key"],
                    "name": project.get("name", ""),
                    "status": project.get("status", ""),
                    "service_status": project.get("service_status", ""),
                    "website_url": project.get("website_url", ""),
                    "last_promoted_at": project.get("last_promoted_at", ""),
                }
            )
        print(json.dumps({
            "project_key": project["project_key"],
            "name": project.get("name", ""),
            "status": project.get("status", ""),
            "tracking_state": project.get("tracking_state", "active"),
            "promoted_now": project.get("promoted_now", False),
            "promotion_ready": project.get("promotion_ready", False),
            "promotion_blockers": project.get("promotion_blockers", []),
            "service_status": project.get("service_status", ""),
            "category": project.get("category", ""),
            "app_category": project.get("app_category", ""),
            "tagline_is_provisional": project.get("tagline_is_provisional", False),
            "website_url": project.get("website_url", ""),
            "introduced": project.get("introduced", False),
            "introduced_url": project.get("introduced_url", ""),
            "last_promoted_at": project.get("last_promoted_at", ""),
            "last_run_errors": project.get("last_run_errors", []),
            "merge_recommendation": project.get("merge_recommendation", {}),
            "github_repo_urls": project.get("github_repo_urls", []),
            "resolved_nsids": project.get("resolved_nsids", []),
        }, ensure_ascii=False))
    if len(projects) > 1:
        print(json.dumps({
            "summary": "enrich_all",
            "project_count": len(projects),
            "promoted_now_count": len(promoted_now),
            "promoted_now": promoted_now,
        }, ensure_ascii=False))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    conn = open_db()
    projects = select_projects(conn, args)
    projects = [
        apply_promotion_state(
            dict(
                project,
                duplicate_candidates=compute_duplicate_candidates(conn, project),
                merge_recommendation=compute_merge_recommendation(
                    dict(project, duplicate_candidates=compute_duplicate_candidates(conn, project))
                ),
            )
        )
        for project in projects
    ]
    if args.format == "json":
        for project in projects:
            print(json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    for project in projects:
        print(f"[{project['project_key']}] {project.get('name') or '(unnamed)'}")
        print(f"  status={project.get('status')} tracking_state={project.get('tracking_state') or 'active'} category={project.get('category') or '-'} app_category={project.get('app_category') or '-'} promotion_ready={project.get('promotion_ready')}")
        print(f"  tracking_note={project.get('tracking_note') or '-'}")
        print(f"  input_nsids={', '.join(project.get('input_nsids', [])) or '-'}")
        print(f"  resolved_nsids={', '.join(project.get('resolved_nsids', [])) or '-'}")
        print(f"  namespaces={', '.join(project.get('recorded_lexicon_namespaces', [])) or '-'}")
        print(f"  website={project.get('website_url') or '-'}")
        print(f"  website_candidates={', '.join(project.get('website_url_candidates', [])) or '-'}")
        print(f"  service_status={project.get('service_status') or '-'}")
        print(f"  introduced={project.get('introduced', False)} introduced_url={project.get('introduced_url') or '-'}")
        print(f"  last_promoted_at={project.get('last_promoted_at') or '-'} promotion_events={len(project.get('promotion_events', []))}")
        print(f"  github={', '.join(project.get('github_repo_urls', [])) or '-'}")
        print(f"  source_code={', '.join(project.get('source_code_urls', [])) or '-'}")
        print(f"  candidate_source_code={', '.join(project.get('candidate_source_code_urls', [])) or '-'}")
        print(f"  features={'; '.join(project.get('features', [])) or '-'}")
        print(f"  tagline={project.get('tagline') or '-'} provisional={project.get('tagline_is_provisional', False)}")
        print(f"  summary={project.get('summary') or '-'}")
        print(f"  blockers={'; '.join(project.get('promotion_blockers', [])) or '-'}")
        print(f"  last_run_errors={'; '.join(project.get('last_run_errors', [])) or '-'}")
        duplicate_candidates = project.get("duplicate_candidates", [])
        if duplicate_candidates:
            print(f"  duplicate_candidates={', '.join(item['project_key'] for item in duplicate_candidates)}")
        merge_recommendation = project.get("merge_recommendation", {})
        if merge_recommendation:
            print(
                "  merge_recommendation="
                f"{merge_recommendation.get('target_project_key') or '-'} "
                f"score={merge_recommendation.get('score', 0)} "
                f"confidence={merge_recommendation.get('merge_confidence') or '-'} "
                f"auto_ready={merge_recommendation.get('auto_merge_ready', False)}"
            )
    return 0


def cmd_set_tracking_state(args: argparse.Namespace) -> int:
    conn = open_db()
    project = resolve_single_project(conn, project_key=args.project_key, seed=args.seed, seed_type=args.type)
    project["tracking_state"] = str(args.tracking_state)
    if args.note is not None:
        project["tracking_note"] = str(args.note).strip()
    save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
    conn.commit()
    print(json.dumps({
        "project_key": project["project_key"],
        "tracking_state": project.get("tracking_state", "active"),
        "tracking_note": project.get("tracking_note", ""),
    }, ensure_ascii=False))
    return 0


def cmd_import_applog(args: argparse.Namespace) -> int:
    conn = open_db()
    summary = import_applog_entries(conn, args.path, execute=args.execute, create_unmatched=args.create_unmatched)
    print(json.dumps(summary, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0


def cmd_import_collections(args: argparse.Namespace) -> int:
    conn = open_db()
    summary = import_collection_inventory(conn, args.path, execute=args.execute, create_unmatched=args.create_unmatched)
    print(json.dumps(summary, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0


def cmd_repair_db(args: argparse.Namespace) -> int:
    conn = open_db()
    preprocessed = preprocess_project_json_rows(conn)
    summary = audit_and_repair_identifiers(conn)
    summary["preprocess"] = preprocessed
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def resolve_single_project(
    conn: sqlite3.Connection,
    *,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
) -> dict[str, Any]:
    if project_key:
        project = fetch_project(conn, resolve_project_key_input(conn, project_key))
        if project is None:
            raise ValueError(f"project not found: {project_key}")
        return project
    if seed:
        detected_type = detect_seed_type(seed, seed_type)
        identifier_value = canonical_seed_value(detected_type, seed)
        found_project_key = find_project_key_by_identifier(conn, detected_type, identifier_value)
        if found_project_key is None:
            raise ValueError(f"project not found for seed: {seed}")
        project = fetch_project(conn, found_project_key)
        if project is None:
            raise ValueError(f"project not found: {found_project_key}")
        return project
    raise ValueError("source/target selector is required")


def cmd_merge(args: argparse.Namespace) -> int:
    conn = open_db()
    source = resolve_single_project(conn, project_key=args.project_key, seed=args.seed, seed_type=args.type)
    source = ensure_project_shape(source)
    source["duplicate_candidates"] = compute_duplicate_candidates(conn, source)
    source["merge_recommendation"] = compute_merge_recommendation(source)

    if args.use_recommendation:
        recommendation = source.get("merge_recommendation") or {}
        target_key = str(recommendation.get("target_project_key") or "")
        if not target_key:
            raise ValueError("no merge recommendation available for source project")
        if args.execute and not recommendation.get("auto_merge_ready") and not args.force:
            raise ValueError("merge recommendation is not auto-merge-ready; rerun without --execute or pass --force")
        target = resolve_single_project(conn, project_key=target_key)
    else:
        target = resolve_single_project(conn, project_key=args.target_project_key, seed=args.target_seed, seed_type=args.target_type)

    target = ensure_project_shape(target)
    if source["project_key"] == target["project_key"]:
        raise ValueError("source and target resolve to the same project")

    merged = merge_project_records(target, source)
    merged["duplicate_candidates"] = compute_duplicate_candidates(conn, merged)
    merged["merge_recommendation"] = compute_merge_recommendation(merged)
    merged = apply_promotion_state(merged)

    summary = {
        "mode": "execute" if args.execute else "dry_run",
        "source_project_key": source["project_key"],
        "target_project_key": target["project_key"],
        "used_recommendation": bool(args.use_recommendation),
        "source_name": source.get("name", ""),
        "target_name": target.get("name", ""),
        "merged_name": merged.get("name", ""),
        "merged_website_url": merged.get("website_url", ""),
        "merged_service_urls": merged.get("service_urls", []),
        "merged_tagline": merged.get("tagline", ""),
        "merged_tagline_is_provisional": merged.get("tagline_is_provisional", False),
        "merged_service_status": merged.get("service_status", ""),
        "merged_source_code_urls": merged.get("source_code_urls", []),
        "introduced": merged.get("introduced", False),
        "introduced_url": merged.get("introduced_url", ""),
        "merge_recommendation": source.get("merge_recommendation", {}),
    }

    if not args.execute:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    save_project(conn, merged, enriched=bool(merged.get("last_enriched_at")))
    delete_project(conn, source["project_key"])
    conn.commit()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def cmd_merge_batch(args: argparse.Namespace) -> int:
    conn = open_db()
    plans, skipped = build_batch_merge_plan(conn, mode=str(args.mode or "strict"))
    if args.max_merges is not None:
        plans = plans[: max(0, int(args.max_merges))]

    executed: list[dict[str, Any]] = []
    for plan in plans:
        source = fetch_project(conn, plan["source_project_key"])
        target = fetch_project(conn, plan["target_project_key"])
        if source is None or target is None:
            skipped.append(
                {
                    "project_key": plan["source_project_key"],
                    "target_project_key": plan["target_project_key"],
                    "reason": "project_missing_during_execution",
                }
            )
            continue
        source = ensure_project_shape(source)
        target = ensure_project_shape(target)
        merged = merge_project_records(target, source)
        merged["duplicate_candidates"] = compute_duplicate_candidates(conn, merged)
        merged["merge_recommendation"] = compute_merge_recommendation(merged)
        merged = apply_promotion_state(merged)

        summary = {
            "source_project_key": source["project_key"],
            "target_project_key": target["project_key"],
            "source_name": source.get("name", ""),
            "target_name": target.get("name", ""),
            "merged_name": merged.get("name", ""),
            "merged_website_url": merged.get("website_url", ""),
            "merged_source_code_urls": merged.get("source_code_urls", []),
            "merged_service_status": merged.get("service_status", ""),
            "reasons": plan["reasons"],
        }
        executed.append(summary)

        if args.execute:
            save_project(conn, merged, enriched=bool(merged.get("last_enriched_at")))
            delete_project(conn, source["project_key"])
            conn.commit()

    print(
        json.dumps(
            {
                "mode": "execute" if args.execute else "dry_run",
                "merge_mode": str(args.mode or "strict"),
                "planned_merges": len(plans),
                "executed_merges": len(executed),
                "executed": executed,
                "skipped_count": len(skipped),
                "skipped": skipped[:100],
            },
            ensure_ascii=False,
            indent=2 if not args.execute else None,
            sort_keys=not args.execute,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register manual ATProto project seeds and enrich project records.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add-seed", help="Register one manual seed.")
    add_parser.add_argument("seed", help="NSID, repository URL, or service URL.")
    add_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Override automatic seed type detection.")
    add_parser.add_argument("--project-key", help="Attach the seed to an existing project key.")
    add_parser.set_defaults(func=cmd_add_seed)

    enrich_parser = subparsers.add_parser("enrich", help="Enrich one or more projects.")
    enrich_group = enrich_parser.add_mutually_exclusive_group(required=True)
    enrich_group.add_argument("--project-key", help="Target one project key.")
    enrich_group.add_argument("--seed", help="Target by known seed value.")
    enrich_group.add_argument("--all", action="store_true", help="Enrich all registered projects.")
    enrich_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    enrich_parser.add_argument("--include-stable", action="store_true", help="When used with --all, include projects marked stable.")
    enrich_parser.add_argument("--include-archived", action="store_true", help="When used with --all, include projects marked archived.")
    enrich_parser.add_argument("--include-ignore", action="store_true", help="When used with --all, include projects marked ignore.")
    enrich_parser.add_argument("--llm-endpoint", help="OpenAI-compatible LLM endpoint or base URL.")
    enrich_parser.add_argument("--llm-model", help="Model name for the LLM endpoint.")
    enrich_parser.add_argument("--llm-api-key", help="Optional API key for the LLM endpoint.")
    enrich_parser.set_defaults(func=cmd_enrich)

    show_parser = subparsers.add_parser("show", help="Show registered projects.")
    show_group = show_parser.add_mutually_exclusive_group(required=True)
    show_group.add_argument("--project-key", help="Target one project key.")
    show_group.add_argument("--seed", help="Target by known seed value.")
    show_group.add_argument("--all", action="store_true", help="Show all projects.")
    show_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    show_parser.add_argument("--tracking-states", nargs="+", choices=sorted(TRACKING_STATES), help="When used with --all, restrict output to these tracking states.")
    show_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    show_parser.set_defaults(func=cmd_show)

    applog_parser = subparsers.add_parser("import-applog", help="Import applog-style introduced links.")
    applog_parser.add_argument("path", help="Path to applog JSON dump.")
    applog_parser.add_argument("--execute", action="store_true", help="Apply imports. Without this flag, show a dry-run summary only.")
    applog_parser.add_argument("--create-unmatched", action="store_true", help="Create new projects for unmatched imported seeds.")
    applog_parser.set_defaults(func=cmd_import_applog)

    collections_parser = subparsers.add_parser("import-collections", help="Import a known collection inventory JSON dump.")
    collections_parser.add_argument("path", help="Path to the collection inventory JSON export.")
    collections_parser.add_argument("--execute", action="store_true", help="Apply imports. Without this flag, show a dry-run summary only.")
    collections_parser.add_argument("--create-unmatched", action="store_true", help="Create new projects for unmatched imported collections.")
    collections_parser.set_defaults(func=cmd_import_collections)

    repair_parser = subparsers.add_parser("repair-db", help="Normalize stored project JSON, refresh derived fields, and report collisions.")
    repair_parser.set_defaults(func=cmd_repair_db)

    tracking_parser = subparsers.add_parser("set-tracking-state", help="Set tracking state for one project.")
    tracking_target = tracking_parser.add_mutually_exclusive_group(required=True)
    tracking_target.add_argument("--project-key", help="Target one project key.")
    tracking_target.add_argument("--seed", help="Target by known seed value.")
    tracking_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    tracking_parser.add_argument("--tracking-state", required=True, choices=sorted(TRACKING_STATES), help="Tracking state to assign.")
    tracking_parser.add_argument("--note", help="Optional tracking note.")
    tracking_parser.set_defaults(func=cmd_set_tracking_state)

    merge_parser = subparsers.add_parser("merge", help="Merge one project into another.")
    merge_source = merge_parser.add_mutually_exclusive_group(required=True)
    merge_source.add_argument("--project-key", help="Source project key.")
    merge_source.add_argument("--seed", help="Source project by known seed value.")
    merge_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    merge_target = merge_parser.add_mutually_exclusive_group(required=True)
    merge_target.add_argument("--target-project-key", help="Explicit target project key.")
    merge_target.add_argument("--target-seed", help="Explicit target project by known seed value.")
    merge_target.add_argument("--use-recommendation", action="store_true", help="Use the current merge recommendation for the source project.")
    merge_parser.add_argument("--target-type", choices=["nsid", "repo_url", "service_url"], help="Required when --target-seed is ambiguous.")
    merge_parser.add_argument("--execute", action="store_true", help="Apply the merge. Without this flag, only show a dry-run summary.")
    merge_parser.add_argument("--force", action="store_true", help="Allow executing a non-auto-ready recommendation merge.")
    merge_parser.set_defaults(func=cmd_merge)

    merge_batch_parser = subparsers.add_parser("merge-batch", help="Batch-merge only high-safety duplicate pairs.")
    merge_batch_parser.add_argument("--execute", action="store_true", help="Apply the planned merges. Without this flag, only show a dry-run summary.")
    merge_batch_parser.add_argument("--max-merges", type=int, help="Optional cap on how many planned merges to execute.")
    merge_batch_parser.add_argument("--mode", choices=["strict", "moderate"], default="strict", help="Safety level for automatic batch merge planning.")
    merge_batch_parser.set_defaults(func=cmd_merge_batch)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except urllib.error.URLError as exc:
        print(f"[error] network request failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
