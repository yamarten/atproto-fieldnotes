#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "survey.db"
DEFAULT_HEADERS = {
    "Accept": "application/json, text/html;q=0.9",
    "User-Agent": "atproto-survey-bot/0.3",
}
GITHUB_API = "https://api.github.com"
DNS_RESOLVER_URL = "https://dns.google/resolve"


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(value: str) -> Any:
    return json.loads(value) if value else None


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def ensure_dirs() -> None:
    (ROOT / "data").mkdir(exist_ok=True)


def request_bytes(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)
    req = urllib.request.Request(url, headers=merged_headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        status = getattr(response, "status", 200)
        body = response.read()
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        return status, body, response_headers


def request_json(url: str, headers: dict[str, str] | None = None) -> Any:
    _, body, _ = request_bytes(url, headers=headers)
    return json.loads(body.decode("utf-8"))


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


def normalize_nsid(nsid: str) -> str:
    value = nsid.strip()
    if value.startswith("at://"):
        value = value.removeprefix("at://")
    value = value.split("#", 1)[0]
    value = value.strip("/")
    return value


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


def detect_seed_type(seed: str, explicit_type: str | None = None) -> str:
    if explicit_type:
        return explicit_type
    parsed = urllib.parse.urlparse(seed)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        host = parsed.netloc.lower()
        segments = [segment for segment in parsed.path.split("/") if segment]
        if host == "github.com" and len(segments) >= 2:
            return "repo_url"
        if host == "tangled.org" and len(segments) >= 2:
            return "repo_url"
        return "service_url"
    if re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*(?:\.[a-z0-9]+(?:[.-][a-z0-9]+)*)+", seed):
        return "nsid"
    raise ValueError(f"could not detect seed type: {seed}")


def default_project_record(project_key: str, seed_type: str, seed_value: str) -> dict[str, Any]:
    return {
        "project_key": project_key,
        "status": "candidate",
        "category": "project",
        "name": "",
        "tagline": "",
        "summary": "",
        "summary_candidates": [],
        "tagline_candidates": [],
        "features": [],
        "inspired_by": [],
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
        "github_repo_urls": [],
        "tangled_repo_urls": [],
        "bluesky_handles": [],
        "tangled_handles": [],
        "classification_reason": "",
        "exclude_reason": "",
        "watch_reason": "",
        "confidence": 0.0,
        "notes": "",
        "manual_seeds": [],
        "seed_type": seed_type,
        "seed_value": seed_value,
    }


def ensure_project_shape(project: dict[str, Any]) -> dict[str, Any]:
    defaults = default_project_record(
        str(project.get("project_key") or "unknown"),
        str(project.get("seed_type") or "unknown"),
        str(project.get("seed_value") or ""),
    )
    defaults.update(project)
    if not defaults.get("input_nsids") and defaults.get("primary_nsid"):
        defaults["input_nsids"] = [defaults["primary_nsid"]]
    if not defaults.get("lexicon_resolution_entries") and defaults.get("resolved_nsids"):
        defaults["lexicon_resolution_entries"] = [
            {
                "input_nsid": item,
                "resolved_nsid": item,
                "repo_did": "",
                "authority_domain": derive_authority_domain(item),
                "resolved_at": "",
            }
            for item in defaults.get("resolved_nsids", [])
        ]
    if not defaults.get("lexicon_resolution_entries") and defaults.get("lexicons"):
        defaults["lexicon_resolution_entries"] = [
            {
                "input_nsid": item,
                "resolved_nsid": item,
                "repo_did": "",
                "authority_domain": derive_authority_domain(item),
                "resolved_at": "",
            }
            for item in defaults.get("lexicons", [])
        ]
    if not defaults.get("last_enriched_at") and defaults.get("lexicons") and not defaults.get("resolved_nsids"):
        defaults["input_nsids"] = unique_list(defaults.get("input_nsids", []) + defaults.get("lexicons", []))
        defaults["lexicons"] = []
    if not defaults.get("last_enriched_at") and defaults.get("service_status") in {"", "unknown"}:
        if defaults.get("website_url"):
            defaults["website_url_candidates"] = add_unique(defaults.get("website_url_candidates", []), defaults["website_url"])
            defaults["service_url_candidates"] = add_unique(defaults.get("service_url_candidates", []), defaults["website_url"])
            defaults["website_url"] = ""
        for url in defaults.get("service_urls", []):
            defaults["service_url_candidates"] = add_unique(defaults.get("service_url_candidates", []), url)
        defaults["service_urls"] = []
    if defaults.get("summary") and not defaults.get("summary_candidates") and not defaults.get("last_llm_review_at"):
        defaults["summary_candidates"] = [{
            "value": defaults["summary"],
            "source": "legacy_unclassified",
            "status": "candidate",
            "added_at": iso_now(),
        }]
        defaults["summary"] = ""
    if defaults.get("tagline") and not defaults.get("tagline_candidates") and not defaults.get("last_llm_review_at"):
        defaults["tagline_candidates"] = [{
            "value": defaults["tagline"],
            "source": "legacy_unclassified",
            "status": "candidate",
            "added_at": iso_now(),
        }]
        defaults["tagline"] = ""
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_identifiers (
            identifier_type TEXT NOT NULL,
            identifier_value TEXT NOT NULL,
            project_key TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (identifier_type, identifier_value),
            FOREIGN KEY (project_key) REFERENCES projects(project_key)
        )
        """
    )
    conn.commit()
    return conn


def fetch_project(conn: sqlite3.Connection, project_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT data_json FROM projects WHERE project_key = ?",
        (project_key,),
    ).fetchone()
    if row is None:
        return None
    return ensure_project_shape(load_json(row["data_json"]))


def list_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT data_json
        FROM projects
        ORDER BY last_seen_at DESC, project_key ASC
        """
    ).fetchall()
    return [ensure_project_shape(load_json(row["data_json"])) for row in rows]


def find_project_key_by_identifier(conn: sqlite3.Connection, identifier_type: str, identifier_value: str) -> str | None:
    row = conn.execute(
        """
        SELECT project_key
        FROM project_identifiers
        WHERE identifier_type = ? AND identifier_value = ?
        """,
        (identifier_type, identifier_value),
    ).fetchone()
    return str(row["project_key"]) if row else None


def upsert_identifier(conn: sqlite3.Connection, project_key: str, identifier_type: str, identifier_value: str) -> None:
    now = iso_now()
    row = conn.execute(
        """
        SELECT project_key
        FROM project_identifiers
        WHERE identifier_type = ? AND identifier_value = ?
        """,
        (identifier_type, identifier_value),
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO project_identifiers (
                identifier_type, identifier_value, project_key, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (identifier_type, identifier_value, project_key, now, now),
        )
    else:
        conn.execute(
            """
            UPDATE project_identifiers
            SET project_key = ?, last_seen_at = ?
            WHERE identifier_type = ? AND identifier_value = ?
            """,
            (project_key, now, identifier_type, identifier_value),
        )


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
    project["source_code_urls"] = unique_list(project.get("source_code_urls", []))
    project["github_repo_urls"] = unique_list(project.get("github_repo_urls", []))
    project["tangled_repo_urls"] = unique_list(project.get("tangled_repo_urls", []))
    project["bluesky_handles"] = unique_list(project.get("bluesky_handles", []))
    project["tangled_handles"] = unique_list(project.get("tangled_handles", []))
    project["evidence_urls"] = unique_list(project.get("evidence_urls", []))
    project["discovery_sources"] = unique_list(project.get("discovery_sources", []))
    project["features"] = unique_list(project.get("features", []))
    project["inspired_by"] = unique_list(project.get("inspired_by", []))
    project["manual_seeds"] = unique_manual_seeds(project.get("manual_seeds", []))
    project["summary_candidates"] = project.get("summary_candidates", [])
    project["tagline_candidates"] = project.get("tagline_candidates", [])
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
                project.get("status", "candidate"),
                project.get("category", "project"),
                project.get("name", ""),
                project.get("primary_nsid", ""),
                project.get("website_url", ""),
                project.get("service_status", "unknown"),
                dump_json(project),
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
                project.get("status", "candidate"),
                project.get("category", "project"),
                project.get("name", ""),
                project.get("primary_nsid", ""),
                project.get("website_url", ""),
                project.get("service_status", "unknown"),
                dump_json(project),
                now,
                last_enriched_at or existing_last_enriched,
                project["project_key"],
            ),
        )
    conn.commit()


def project_key_for_seed(seed_type: str, seed_value: str) -> str:
    if seed_type == "nsid":
        return f"nsid:{seed_value}"
    if seed_type == "repo_url":
        normalized = normalize_url(seed_value)
        parsed = urllib.parse.urlparse(normalized)
        if parsed.netloc == "github.com":
            segments = [segment for segment in parsed.path.split("/") if segment]
            if len(segments) >= 2:
                return f"github:{segments[0].lower()}/{segments[1].lower()}"
        return f"repo:{slugify(normalized)}"
    return f"service:{slugify(normalize_url(seed_value))}"


def ensure_project_for_seed(
    conn: sqlite3.Connection,
    seed_type: str,
    seed_value: str,
    project_key: str | None = None,
) -> dict[str, Any]:
    normalized_value = normalize_url(seed_value) if seed_type.endswith("_url") else seed_value
    identifier_value = normalized_value
    target_project_key = project_key or find_project_key_by_identifier(conn, seed_type, identifier_value)
    if target_project_key is None:
        target_project_key = project_key or project_key_for_seed(seed_type, identifier_value)
    project = fetch_project(conn, target_project_key)
    if project is None:
        project = default_project_record(target_project_key, seed_type, identifier_value)
    project["manual_seeds"] = project.get("manual_seeds", [])
    project["manual_seeds"].append({"type": seed_type, "value": identifier_value, "added_at": iso_now()})
    project["manual_seeds"] = [dict(item) for item in project["manual_seeds"]]
    apply_seed_to_project(project, seed_type, identifier_value)
    save_project(conn, project, enriched=False)
    upsert_identifier(conn, project["project_key"], seed_type, identifier_value)
    register_project_identifiers(conn, project)
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
            project["website_url_candidates"] = add_unique(project.get("website_url_candidates", []), f"https://{authority_domain}")
            project["service_url_candidates"] = add_unique(project.get("service_url_candidates", []), f"https://{authority_domain}")
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
        project["service_url_candidates"] = add_unique(project.get("service_url_candidates", []), normalized)
        project["website_url_candidates"] = add_unique(project.get("website_url_candidates", []), normalized)
        project["evidence_urls"] = add_unique(project.get("evidence_urls", []), normalized)
        if not project.get("name"):
            host = urllib.parse.urlparse(normalized).netloc
            project["name"] = host


def register_project_identifiers(conn: sqlite3.Connection, project: dict[str, Any]) -> None:
    if project.get("primary_nsid"):
        upsert_identifier(conn, project["project_key"], "nsid", project["primary_nsid"])
    for nsid in project.get("input_nsids", []):
        upsert_identifier(conn, project["project_key"], "nsid", normalize_nsid(nsid))
    for url in project.get("github_repo_urls", []):
        upsert_identifier(conn, project["project_key"], "repo_url", normalize_url(url))
        upsert_identifier(conn, project["project_key"], "github_repo_url", normalize_url(url))
    for url in project.get("tangled_repo_urls", []):
        upsert_identifier(conn, project["project_key"], "repo_url", normalize_url(url))
        upsert_identifier(conn, project["project_key"], "tangled_repo_url", normalize_url(url))
    for url in project.get("source_code_urls", []):
        upsert_identifier(conn, project["project_key"], "repo_url", normalize_url(url))
    for url in project.get("service_urls", []):
        upsert_identifier(conn, project["project_key"], "service_url", normalize_url(url))
    for url in project.get("service_url_candidates", []):
        upsert_identifier(conn, project["project_key"], "service_url", normalize_url(url))
    if project.get("website_url"):
        upsert_identifier(conn, project["project_key"], "service_url", normalize_url(project["website_url"]))
    for url in project.get("website_url_candidates", []):
        upsert_identifier(conn, project["project_key"], "service_url", normalize_url(url))


def github_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def extract_github_slug(repo_url: str) -> str | None:
    normalized = normalize_url(repo_url)
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


def extract_urls(html: str) -> list[str]:
    pattern = r'https?://[^\s"\'<>]+'
    return unique_list(re.findall(pattern, html))


def clean_title(title: str) -> str:
    return re.sub(r"\s*[|\-]\s*.*$", "", title).strip()


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
    return {
        "status_code": status_code,
        "title": title,
        "description": description,
        "urls": urls,
        "service_status": infer_service_status(status_code, f"{title}\n{description}\n{html[:5000]}"),
    }


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


def enrich_from_lexicon(project: dict[str, Any]) -> None:
    nsid = project.get("primary_nsid")
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
        project["website_url_candidates"] = add_unique(project.get("website_url_candidates", []), candidate_url)
        project["service_url_candidates"] = add_unique(project.get("service_url_candidates", []), candidate_url)
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
    project["source_code_urls"] = add_unique(project.get("source_code_urls", []), normalize_url(repo_url))
    project["github_repo_urls"] = add_unique(project.get("github_repo_urls", []), normalize_url(repo_url))
    project["evidence_urls"] = add_unique(project.get("evidence_urls", []), normalize_url(repo_url))
    if full_name and not project.get("name"):
        project["name"] = full_name.split("/")[-1]
    add_candidate_text(project, "summary", description, f"github:{full_name or normalize_url(repo_url)}")
    add_candidate_text(project, "tagline", description, f"github:{full_name or normalize_url(repo_url)}")
    if homepage:
        normalized_homepage = normalize_url(homepage)
        project["service_url_candidates"] = add_unique(project.get("service_url_candidates", []), normalized_homepage)
        project["website_url_candidates"] = add_unique(project.get("website_url_candidates", []), normalized_homepage)
    urls = extract_urls(readme_text)
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc == "github.com":
            project["github_repo_urls"] = add_unique(project.get("github_repo_urls", []), normalize_url(url))
            project["source_code_urls"] = add_unique(project.get("source_code_urls", []), normalize_url(url))
        elif parsed.scheme in {"http", "https"}:
            project["service_url_candidates"] = add_unique(project.get("service_url_candidates", []), normalize_url(url))


def enrich_from_service(project: dict[str, Any], service_url: str) -> None:
    result = probe_service_url(service_url)
    title = clean_title(result["title"])
    description = result["description"]
    if title and not project.get("name"):
        project["name"] = title
    add_candidate_text(project, "summary", description, f"service_meta:{normalize_url(service_url)}")
    add_candidate_text(project, "tagline", description, f"service_meta:{normalize_url(service_url)}")
    project["service_status"] = result["service_status"]
    project["evidence_urls"] = add_unique(project.get("evidence_urls", []), normalize_url(service_url))
    normalized_service_url = normalize_url(service_url)
    if result["status_code"] < 400:
        project["service_urls"] = add_unique(project.get("service_urls", []), normalized_service_url)
        if not project.get("website_url"):
            project["website_url"] = normalized_service_url
    else:
        project["failed_service_urls"] = add_unique(project.get("failed_service_urls", []), normalized_service_url)
    for url in result["urls"]:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc == "github.com":
            project["github_repo_urls"] = add_unique(project.get("github_repo_urls", []), normalize_url(url))
            project["source_code_urls"] = add_unique(project.get("source_code_urls", []), normalize_url(url))
        elif parsed.netloc == "tangled.org":
            project["tangled_repo_urls"] = add_unique(project.get("tangled_repo_urls", []), normalize_url(url))
            project["source_code_urls"] = add_unique(project.get("source_code_urls", []), normalize_url(url))
        elif parsed.netloc.endswith("bsky.app") and "/profile/" in parsed.path:
            handle = parsed.path.split("/profile/", 1)[-1].split("/", 1)[0]
            if handle:
                project["bluesky_handles"] = add_unique(project.get("bluesky_handles", []), handle)


def enrich_project(project: dict[str, Any]) -> dict[str, Any]:
    if project.get("primary_nsid"):
        try:
            enrich_from_lexicon(project)
        except Exception as exc:
            project["notes"] = (project.get("notes", "") + f"\nlexicon enrichment failed: {exc}").strip()

    initial_repo_urls = unique_list(project.get("github_repo_urls", []) + project.get("source_code_urls", []))
    for repo_url in initial_repo_urls:
        if extract_github_slug(repo_url) is None:
            continue
        try:
            enrich_from_github_repo(project, repo_url)
        except Exception as exc:
            project["notes"] = (project.get("notes", "") + f"\ngithub enrichment failed for {repo_url}: {exc}").strip()

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
            project["notes"] = (project.get("notes", "") + f"\nservice probe failed for {service_url}: {exc}").strip()

    secondary_repo_urls = unique_list(project.get("github_repo_urls", []))
    for repo_url in secondary_repo_urls:
        try:
            enrich_from_github_repo(project, repo_url)
        except Exception:
            continue

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

    project["last_llm_review_at"] = project.get("last_llm_review_at", "")
    return project


def cmd_add_seed(args: argparse.Namespace) -> int:
    conn = open_db()
    seed_type = detect_seed_type(args.seed, args.type)
    project = ensure_project_for_seed(conn, seed_type, args.seed, project_key=args.project_key)
    print(json.dumps({
        "project_key": project["project_key"],
        "seed_type": seed_type,
        "seed_value": normalize_url(args.seed) if seed_type.endswith("_url") else args.seed,
        "name": project.get("name", ""),
    }, ensure_ascii=False))
    return 0


def select_projects(conn: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.project_key:
        project = fetch_project(conn, args.project_key)
        if project is None:
            raise ValueError(f"project not found: {args.project_key}")
        return [project]
    if args.seed:
        seed_type = detect_seed_type(args.seed, args.type)
        identifier_value = normalize_url(args.seed) if seed_type.endswith("_url") else args.seed
        project_key = find_project_key_by_identifier(conn, seed_type, identifier_value)
        if project_key is None:
            project = ensure_project_for_seed(conn, seed_type, args.seed)
            return [project]
        project = fetch_project(conn, project_key)
        return [project] if project else []
    if args.all:
        return list_projects(conn)
    raise ValueError("choose one of --project-key, --seed, or --all")


def cmd_enrich(args: argparse.Namespace) -> int:
    conn = open_db()
    projects = select_projects(conn, args)
    for project in projects:
        project = enrich_project(project)
        save_project(conn, project, enriched=True)
        register_project_identifiers(conn, project)
        conn.commit()
        print(json.dumps({
            "project_key": project["project_key"],
            "name": project.get("name", ""),
            "primary_nsid": project.get("primary_nsid", ""),
            "website_url": project.get("website_url", ""),
            "github_repo_urls": project.get("github_repo_urls", []),
            "service_urls": project.get("service_urls", []),
            "service_status": project.get("service_status", ""),
        }, ensure_ascii=False))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    conn = open_db()
    projects = select_projects(conn, args)
    if args.format == "json":
        for project in projects:
            print(json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    for project in projects:
        print(f"[{project['project_key']}] {project.get('name') or '(unnamed)'}")
        print(f"  status={project.get('status')} category={project.get('category')}")
        print(f"  input_nsids={', '.join(project.get('input_nsids', [])) or '-'}")
        print(f"  resolved_nsids={', '.join(project.get('resolved_nsids', [])) or '-'}")
        print(f"  namespaces={', '.join(project.get('recorded_lexicon_namespaces', [])) or '-'}")
        print(f"  website={project.get('website_url') or '-'}")
        print(f"  website_candidates={', '.join(project.get('website_url_candidates', [])) or '-'}")
        print(f"  github={', '.join(project.get('github_repo_urls', [])) or '-'}")
        print(f"  summary={project.get('summary') or '-'}")
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
    enrich_parser.set_defaults(func=cmd_enrich)

    show_parser = subparsers.add_parser("show", help="Show registered projects.")
    show_group = show_parser.add_mutually_exclusive_group(required=True)
    show_group.add_argument("--project-key", help="Target one project key.")
    show_group.add_argument("--seed", help="Target by known seed value.")
    show_group.add_argument("--all", action="store_true", help="Show all projects.")
    show_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    show_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    show_parser.set_defaults(func=cmd_show)

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
