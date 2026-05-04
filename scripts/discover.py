#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "survey.db"
CONFIG_PATH = ROOT / "config" / "sources.json"
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "atproto-survey-bot/0.2",
}


@dataclass
class CollectionEntry:
    collection: str
    count: int
    recent_count: int
    min_at: str | None
    max_at: str | None
    payload: dict[str, Any]


@dataclass
class DiffEntry:
    collection: str
    count: int
    recent_count: int
    min_at: str | None
    max_at: str | None
    is_new: bool
    count_delta: int
    recent_delta: int
    max_changed: bool
    priority: int
    bucket: str


@dataclass
class GitHubRepoEntry:
    repo_id: int
    query: str
    full_name: str
    html_url: str
    description: str
    owner: str
    created_at: str | None
    updated_at: str | None
    stargazers_count: int
    payload: dict[str, Any]


@dataclass
class GitHubDiffEntry:
    repo_id: int
    full_name: str
    html_url: str
    description: str
    owner: str
    created_at: str | None
    updated_at: str | None
    stargazers_count: int
    is_new: bool
    priority: int
@dataclass
class TangledRepoEntry:
    record_uri: str
    repo_did: str
    rkey: str
    handle: str
    name: str
    description: str
    owner: str
    knot: str
    added_at: str | None
    repo_url: str
    payload: dict[str, Any]


@dataclass
class TangledDiffEntry:
    record_uri: str
    repo_did: str
    handle: str
    name: str
    description: str
    owner: str
    knot: str
    added_at: str | None
    repo_url: str
    is_new: bool
    priority: int


@dataclass
class ManualEntry:
    manual_key: str
    kind: str
    name: str
    url: str
    summary: str
    notes: str
    status: str
    payload: dict[str, Any]


@dataclass
class ManualDiffEntry:
    manual_key: str
    kind: str
    name: str
    url: str
    summary: str
    notes: str
    status: str
    is_new: bool


def ensure_dirs() -> None:
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "reports").mkdir(exist_ok=True)


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def request_json(url: str, headers: dict[str, str] | None = None) -> Any:
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)
    req = urllib.request.Request(url, headers=merged_headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT NOT NULL,
            started_at TEXT NOT NULL,
            item_count INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_snapshots (
            run_id INTEGER NOT NULL,
            collection TEXT NOT NULL,
            count INTEGER NOT NULL,
            recent_count INTEGER NOT NULL,
            min_at TEXT,
            max_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (run_id, collection),
            FOREIGN KEY (run_id) REFERENCES collection_runs(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_state (
            collection TEXT PRIMARY KEY,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            latest_count INTEGER NOT NULL,
            latest_recent_count INTEGER NOT NULL,
            min_at TEXT,
            max_at TEXT,
            last_run_id INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS github_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            item_count INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS github_snapshots (
            run_id INTEGER NOT NULL,
            repo_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            full_name TEXT NOT NULL,
            html_url TEXT NOT NULL,
            description TEXT NOT NULL,
            owner TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT,
            stargazers_count INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (run_id, repo_id),
            FOREIGN KEY (run_id) REFERENCES github_runs(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS github_state (
            repo_id INTEGER PRIMARY KEY,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            query TEXT NOT NULL,
            full_name TEXT NOT NULL,
            html_url TEXT NOT NULL,
            description TEXT NOT NULL,
            owner TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT,
            stargazers_count INTEGER NOT NULL,
            last_run_id INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tangled_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            relay_url TEXT NOT NULL,
            collection TEXT NOT NULL,
            started_at TEXT NOT NULL,
            repo_count INTEGER NOT NULL,
            record_count INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tangled_snapshots (
            run_id INTEGER NOT NULL,
            record_uri TEXT NOT NULL,
            repo_did TEXT NOT NULL,
            rkey TEXT NOT NULL,
            handle TEXT NOT NULL,
            name TEXT NOT NULL,
            owner TEXT NOT NULL,
            knot TEXT NOT NULL,
            added_at TEXT,
            repo_url TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (run_id, record_uri),
            FOREIGN KEY (run_id) REFERENCES tangled_runs(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tangled_state (
            record_uri TEXT PRIMARY KEY,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            repo_did TEXT NOT NULL,
            rkey TEXT NOT NULL,
            handle TEXT NOT NULL,
            name TEXT NOT NULL,
            owner TEXT NOT NULL,
            knot TEXT NOT NULL,
            added_at TEXT,
            repo_url TEXT NOT NULL,
            last_run_id INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            item_count INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_snapshots (
            run_id INTEGER NOT NULL,
            manual_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            summary TEXT NOT NULL,
            notes TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (run_id, manual_key),
            FOREIGN KEY (run_id) REFERENCES manual_runs(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_state (
            manual_key TEXT PRIMARY KEY,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            summary TEXT NOT NULL,
            notes TEXT NOT NULL,
            status TEXT NOT NULL,
            last_run_id INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def normalize_entry(raw: dict[str, Any]) -> CollectionEntry:
    return CollectionEntry(
        collection=str(raw["collection"]),
        count=int(raw.get("count", 0) or 0),
        recent_count=int(raw.get("recent_count", 0) or 0),
        min_at=raw.get("min"),
        max_at=raw.get("max"),
        payload=raw,
    )


def fetch_inventory(source_url: str) -> list[CollectionEntry]:
    payload = request_json(source_url)
    if not isinstance(payload, list):
        raise ValueError("collection inventory response must be a JSON array")
    return [normalize_entry(item) for item in payload]


def github_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def normalize_github_repo(raw: dict[str, Any], query: str) -> GitHubRepoEntry:
    return GitHubRepoEntry(
        repo_id=int(raw["id"]),
        query=query,
        full_name=str(raw["full_name"]),
        html_url=str(raw["html_url"]),
        description=raw.get("description") or "",
        owner=raw.get("owner", {}).get("login", ""),
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
        stargazers_count=int(raw.get("stargazers_count", 0) or 0),
        payload=raw,
    )


def fetch_github_repos(config: dict[str, Any]) -> list[GitHubRepoEntry]:
    search_cfg = config["github_repo_search"]
    headers = github_headers()
    deduped: dict[int, GitHubRepoEntry] = {}
    for query in search_cfg.get("queries", []):
        for page in range(1, int(search_cfg.get("pages", 1)) + 1):
            params = urllib.parse.urlencode(
                {
                    "q": query,
                    "type": "Repositories",
                    "sort": "updated",
                    "order": "desc",
                    "per_page": str(search_cfg.get("per_page", 50)),
                    "page": str(page),
                }
            )
            payload = request_json(f"https://api.github.com/search/repositories?{params}", headers=headers)
            items = payload.get("items", [])
            for raw in items:
                entry = normalize_github_repo(raw, query)
                existing = deduped.get(entry.repo_id)
                if existing is None or (entry.updated_at or "") > (existing.updated_at or ""):
                    deduped[entry.repo_id] = entry
            if len(items) < int(search_cfg.get("per_page", 50)):
                break
    return list(deduped.values())


def parse_at_uri(uri: str) -> tuple[str, str, str]:
    prefix = "at://"
    if not uri.startswith(prefix):
        return "", "", ""
    rest = uri.removeprefix(prefix)
    parts = rest.split("/", 2)
    repo = parts[0] if len(parts) >= 1 else ""
    collection = parts[1] if len(parts) >= 2 else ""
    rkey = parts[2] if len(parts) >= 3 else ""
    return repo, collection, rkey


def fetch_tangled_repos(config: dict[str, Any]) -> tuple[list[TangledRepoEntry], int]:
    tangled_cfg = config["tangled_repo_discovery"]
    params = {
        "collection": tangled_cfg["collection"],
        "limit": str(tangled_cfg.get("limit", 100)),
    }
    url = f"{tangled_cfg['ufos_api_url']}?{urllib.parse.urlencode(params)}"
    payload = request_json(url)
    if isinstance(payload, list):
        raw_records = payload
    elif isinstance(payload, dict):
        raw_records = payload.get("records", []) or payload.get("items", [])
    else:
        raise ValueError("unexpected UFOs response shape")

    deduped: dict[str, TangledRepoEntry] = {}
    for raw in raw_records:
        entry = normalize_tangled_record(raw)
        if entry.record_uri:
            deduped[entry.record_uri] = entry
    return list(deduped.values()), len(deduped)


def normalize_tangled_record(raw: dict[str, Any]) -> TangledRepoEntry:
    uri = str(raw.get("uri") or raw.get("at_uri") or raw.get("record_uri") or "")
    value = raw.get("value", {}) or raw.get("record", {}) or {}
    parsed_repo_did, _, parsed_rkey = parse_at_uri(uri)
    repo_did = str(raw.get("repo") or raw.get("did") or raw.get("repo_did") or parsed_repo_did or "")
    collection = str(raw.get("collection") or "")
    rkey = str(raw.get("rkey") or parsed_rkey or "")
    if not uri and repo_did and collection and rkey:
        uri = f"at://{repo_did}/{collection}/{rkey}"
    handle = str(raw.get("handle") or raw.get("repo_handle") or raw.get("author_handle") or "")
    name = str(value.get("name") or raw.get("name") or "")
    description = str(value.get("description") or raw.get("description") or "")
    owner = str(value.get("owner") or raw.get("owner") or "")
    knot = str(value.get("knot") or raw.get("knot") or "")
    added_at = (
        value.get("addedAt")
        or value.get("createdAt")
        or raw.get("addedAt")
        or raw.get("indexed_at")
        or raw.get("indexedAt")
        or raw.get("createdAt")
        or raw.get("time")
    )
    repo_url = str(raw.get("url") or raw.get("repo_url") or "")
    if not repo_url and handle and name:
        repo_url = f"https://tangled.org/{handle}/{name}"
    return TangledRepoEntry(
        record_uri=uri,
        repo_did=repo_did,
        rkey=rkey,
        handle=handle,
        name=name,
        description=description,
        owner=owner,
        knot=knot,
        added_at=str(added_at) if added_at else None,
        repo_url=repo_url,
        payload=raw,
    )


def manual_path_default() -> Path:
    return ROOT / "data" / "manual_candidates.jsonl"


def derive_manual_key(payload: dict[str, Any]) -> str:
    for key in ("key", "manual_key", "id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    url = str(payload.get("url") or "").strip()
    if url:
        return url
    return str(payload.get("name") or "").strip()


def normalize_manual_entry(raw: dict[str, Any]) -> ManualEntry:
    manual_key = derive_manual_key(raw)
    if not manual_key:
        raise ValueError("manual candidate requires one of key/url/name")
    return ManualEntry(
        manual_key=manual_key,
        kind=str(raw.get("kind") or "project"),
        name=str(raw.get("name") or manual_key),
        url=str(raw.get("url") or ""),
        summary=str(raw.get("summary") or ""),
        notes=str(raw.get("notes") or ""),
        status=str(raw.get("status") or "candidate"),
        payload=raw,
    )


def fetch_manual_entries(path: Path) -> list[ManualEntry]:
    if not path.exists():
        return []
    entries: list[ManualEntry] = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        raw = json.loads(stripped)
        if not isinstance(raw, dict):
            raise ValueError(f"manual candidate line {lineno} must be a JSON object")
        entries.append(normalize_manual_entry(raw))
    return entries


def classify_bucket(collection: str, config: dict[str, Any]) -> str:
    inventory = config["collection_inventory"]
    builtin_prefixes = tuple(inventory.get("builtin_prefixes", []))
    infra_keywords = tuple(inventory.get("infra_keywords", []))
    lowered = collection.lower()

    if collection.startswith(builtin_prefixes):
        return "builtin"
    if any(keyword in lowered for keyword in infra_keywords):
        return "infra"
    return "project"


def calc_priority(entry: CollectionEntry, bucket: str) -> int:
    score = 0
    if bucket == "project":
        score += 12
    elif bucket == "infra":
        score += 8

    if entry.recent_count > 0:
        score += 6
    if entry.recent_count >= 100:
        score += 4
    if entry.recent_count >= 1000:
        score += 4

    min_at = parse_iso(entry.min_at)
    if min_at:
        age_days = (dt.datetime.now(dt.timezone.utc) - min_at).days
        if age_days <= 7:
            score += 10
        elif age_days <= 30:
            score += 6
        elif age_days <= 90:
            score += 3

    if entry.count <= 10:
        score += 4
    elif entry.count <= 100:
        score += 2

    return score


def create_run(conn: sqlite3.Connection, source_url: str, item_count: int) -> int:
    cur = conn.execute(
        """
        INSERT INTO collection_runs (source_url, started_at, item_count)
        VALUES (?, ?, ?)
        """,
        (source_url, iso_now(), item_count),
    )
    conn.commit()
    return int(cur.lastrowid)


def create_github_run(conn: sqlite3.Connection, item_count: int) -> int:
    cur = conn.execute(
        "INSERT INTO github_runs (started_at, item_count) VALUES (?, ?)",
        (iso_now(), item_count),
    )
    conn.commit()
    return int(cur.lastrowid)


def create_tangled_run(conn: sqlite3.Connection, source_url: str, collection: str, repo_count: int, record_count: int) -> int:
    cur = conn.execute(
        """
        INSERT INTO tangled_runs (relay_url, collection, started_at, repo_count, record_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        (source_url, collection, iso_now(), repo_count, record_count),
    )
    conn.commit()
    return int(cur.lastrowid)


def create_manual_run(conn: sqlite3.Connection, item_count: int) -> int:
    cur = conn.execute(
        "INSERT INTO manual_runs (started_at, item_count) VALUES (?, ?)",
        (iso_now(), item_count),
    )
    conn.commit()
    return int(cur.lastrowid)


def save_snapshot(conn: sqlite3.Connection, run_id: int, entries: list[CollectionEntry]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO collection_snapshots (
            run_id, collection, count, recent_count, min_at, max_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                entry.collection,
                entry.count,
                entry.recent_count,
                entry.min_at,
                entry.max_at,
                json.dumps(entry.payload, ensure_ascii=False, sort_keys=True),
            )
            for entry in entries
        ],
    )
    conn.commit()


def save_github_snapshot(conn: sqlite3.Connection, run_id: int, entries: list[GitHubRepoEntry]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO github_snapshots (
            run_id, repo_id, query, full_name, html_url, description, owner,
            created_at, updated_at, stargazers_count, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                entry.repo_id,
                entry.query,
                entry.full_name,
                entry.html_url,
                entry.description,
                entry.owner,
                entry.created_at,
                entry.updated_at,
                entry.stargazers_count,
                json.dumps(entry.payload, ensure_ascii=False, sort_keys=True),
            )
            for entry in entries
        ],
    )
    conn.commit()


def save_tangled_snapshot(conn: sqlite3.Connection, run_id: int, entries: list[TangledRepoEntry]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO tangled_snapshots (
            run_id, record_uri, repo_did, rkey, handle, name, owner, knot,
            added_at, repo_url, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                entry.record_uri,
                entry.repo_did,
                entry.rkey,
                entry.handle,
                entry.name,
                entry.owner,
                entry.knot,
                entry.added_at,
                entry.repo_url,
                json.dumps(entry.payload, ensure_ascii=False, sort_keys=True),
            )
            for entry in entries
        ],
    )
    conn.commit()


def save_manual_snapshot(conn: sqlite3.Connection, run_id: int, entries: list[ManualEntry]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO manual_snapshots (
            run_id, manual_key, kind, name, url, summary, notes, status, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                entry.manual_key,
                entry.kind,
                entry.name,
                entry.url,
                entry.summary,
                entry.notes,
                entry.status,
                json.dumps(entry.payload, ensure_ascii=False, sort_keys=True),
            )
            for entry in entries
        ],
    )
    conn.commit()


def diff_entries(conn: sqlite3.Connection, entries: list[CollectionEntry], config: dict[str, Any]) -> list[DiffEntry]:
    diffs: list[DiffEntry] = []
    for entry in entries:
        previous = conn.execute(
            """
            SELECT latest_count, latest_recent_count, min_at, max_at
            FROM collection_state
            WHERE collection = ?
            """,
            (entry.collection,),
        ).fetchone()
        is_new = previous is None
        count_delta = entry.count - int(previous["latest_count"]) if previous else entry.count
        recent_delta = entry.recent_count - int(previous["latest_recent_count"]) if previous else entry.recent_count
        max_changed = bool(previous and (previous["max_at"] != entry.max_at))
        if not is_new and count_delta == 0 and recent_delta == 0 and not max_changed:
            continue
        bucket = classify_bucket(entry.collection, config)
        diffs.append(
            DiffEntry(
                collection=entry.collection,
                count=entry.count,
                recent_count=entry.recent_count,
                min_at=entry.min_at,
                max_at=entry.max_at,
                is_new=is_new,
                count_delta=count_delta,
                recent_delta=recent_delta,
                max_changed=max_changed,
                priority=calc_priority(entry, bucket) + (12 if is_new else 0),
                bucket=bucket,
            )
        )
    diffs.sort(key=lambda item: (item.is_new, item.priority, item.recent_count, item.count), reverse=True)
    return diffs


def upsert_state(conn: sqlite3.Connection, run_id: int, entries: list[CollectionEntry]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    now = iso_now()
    for entry in entries:
        existing = conn.execute(
            "SELECT collection FROM collection_state WHERE collection = ?",
            (entry.collection,),
        ).fetchone()
        payload_json = json.dumps(entry.payload, ensure_ascii=False, sort_keys=True)
        if existing is None:
            conn.execute(
                """
                INSERT INTO collection_state (
                    collection, first_seen_at, last_seen_at, latest_count,
                    latest_recent_count, min_at, max_at, last_run_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.collection,
                    now,
                    now,
                    entry.count,
                    entry.recent_count,
                    entry.min_at,
                    entry.max_at,
                    run_id,
                    payload_json,
                ),
            )
            inserted += 1
        else:
            conn.execute(
                """
                UPDATE collection_state
                SET last_seen_at = ?, latest_count = ?, latest_recent_count = ?,
                    min_at = ?, max_at = ?, last_run_id = ?, payload_json = ?
                WHERE collection = ?
                """,
                (
                    now,
                    entry.count,
                    entry.recent_count,
                    entry.min_at,
                    entry.max_at,
                    run_id,
                    payload_json,
                    entry.collection,
                ),
            )
            updated += 1
    conn.commit()
    return inserted, updated


def github_priority(entry: GitHubRepoEntry) -> int:
    score = 0
    created_at = parse_iso(entry.created_at)
    if created_at:
        age_days = (dt.datetime.now(dt.timezone.utc) - created_at).days
        if age_days <= 7:
            score += 10
        elif age_days <= 30:
            score += 6
    if entry.stargazers_count >= 20:
        score += 6
    elif entry.stargazers_count >= 5:
        score += 3
    text = f"{entry.full_name} {entry.description}".lower()
    for keyword in ("atproto", "bluesky", "lexicon", "pds", "jetstream", "oauth"):
        if keyword in text:
            score += 2
    return score


def diff_github_entries(conn: sqlite3.Connection, entries: list[GitHubRepoEntry]) -> list[GitHubDiffEntry]:
    diffs: list[GitHubDiffEntry] = []
    for entry in entries:
        previous = conn.execute(
            "SELECT updated_at FROM github_state WHERE repo_id = ?",
            (entry.repo_id,),
        ).fetchone()
        is_new = previous is None
        updated_changed = bool(previous and previous["updated_at"] != entry.updated_at)
        if not is_new and not updated_changed:
            continue
        diffs.append(
            GitHubDiffEntry(
                repo_id=entry.repo_id,
                full_name=entry.full_name,
                html_url=entry.html_url,
                description=entry.description,
                owner=entry.owner,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
                stargazers_count=entry.stargazers_count,
                is_new=is_new,
                priority=github_priority(entry) + (8 if is_new else 0),
            )
        )
    diffs.sort(key=lambda item: (item.is_new, item.priority, item.stargazers_count), reverse=True)
    return diffs


def upsert_github_state(conn: sqlite3.Connection, run_id: int, entries: list[GitHubRepoEntry]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    now = iso_now()
    for entry in entries:
        existing = conn.execute(
            "SELECT repo_id FROM github_state WHERE repo_id = ?",
            (entry.repo_id,),
        ).fetchone()
        payload_json = json.dumps(entry.payload, ensure_ascii=False, sort_keys=True)
        if existing is None:
            conn.execute(
                """
                INSERT INTO github_state (
                    repo_id, first_seen_at, last_seen_at, query, full_name, html_url,
                    description, owner, created_at, updated_at, stargazers_count,
                    last_run_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.repo_id,
                    now,
                    now,
                    entry.query,
                    entry.full_name,
                    entry.html_url,
                    entry.description,
                    entry.owner,
                    entry.created_at,
                    entry.updated_at,
                    entry.stargazers_count,
                    run_id,
                    payload_json,
                ),
            )
            inserted += 1
        else:
            conn.execute(
                """
                UPDATE github_state
                SET last_seen_at = ?, query = ?, full_name = ?, html_url = ?,
                    description = ?, owner = ?, created_at = ?, updated_at = ?,
                    stargazers_count = ?, last_run_id = ?, payload_json = ?
                WHERE repo_id = ?
                """,
                (
                    now,
                    entry.query,
                    entry.full_name,
                    entry.html_url,
                    entry.description,
                    entry.owner,
                    entry.created_at,
                    entry.updated_at,
                    entry.stargazers_count,
                    run_id,
                    payload_json,
                    entry.repo_id,
                ),
            )
            updated += 1
    conn.commit()
    return inserted, updated


def tangled_priority(entry: TangledRepoEntry) -> int:
    score = 10
    added_at = parse_iso(entry.added_at)
    if added_at:
        age_days = (dt.datetime.now(dt.timezone.utc) - added_at).days
        if age_days <= 7:
            score += 10
        elif age_days <= 30:
            score += 6
    text = f"{entry.name} {entry.knot}".lower()
    for keyword in ("atproto", "pds", "feed", "oauth", "bot"):
        if keyword in text:
            score += 2
    return score


def diff_tangled_entries(conn: sqlite3.Connection, entries: list[TangledRepoEntry]) -> list[TangledDiffEntry]:
    diffs: list[TangledDiffEntry] = []
    for entry in entries:
        previous = conn.execute(
            "SELECT payload_json FROM tangled_state WHERE record_uri = ?",
            (entry.record_uri,),
        ).fetchone()
        is_new = previous is None
        payload_json = json.dumps(entry.payload, ensure_ascii=False, sort_keys=True)
        changed = bool(previous and previous["payload_json"] != payload_json)
        if not is_new and not changed:
            continue
        diffs.append(
            TangledDiffEntry(
                record_uri=entry.record_uri,
                repo_did=entry.repo_did,
                handle=entry.handle,
                name=entry.name,
                description=entry.description,
                owner=entry.owner,
                knot=entry.knot,
                added_at=entry.added_at,
                repo_url=entry.repo_url,
                is_new=is_new,
                priority=tangled_priority(entry) + (8 if is_new else 0),
            )
        )
    diffs.sort(key=lambda item: (item.is_new, item.priority, item.added_at or ""), reverse=True)
    return diffs


def upsert_tangled_state(conn: sqlite3.Connection, run_id: int, entries: list[TangledRepoEntry]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    now = iso_now()
    for entry in entries:
        existing = conn.execute(
            "SELECT record_uri FROM tangled_state WHERE record_uri = ?",
            (entry.record_uri,),
        ).fetchone()
        payload_json = json.dumps(entry.payload, ensure_ascii=False, sort_keys=True)
        if existing is None:
            conn.execute(
                """
                INSERT INTO tangled_state (
                    record_uri, first_seen_at, last_seen_at, repo_did, rkey, handle,
                    name, owner, knot, added_at, repo_url, last_run_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.record_uri,
                    now,
                    now,
                    entry.repo_did,
                    entry.rkey,
                    entry.handle,
                    entry.name,
                    entry.owner,
                    entry.knot,
                    entry.added_at,
                    entry.repo_url,
                    run_id,
                    payload_json,
                ),
            )
            inserted += 1
        else:
            conn.execute(
                """
                UPDATE tangled_state
                SET last_seen_at = ?, repo_did = ?, rkey = ?, handle = ?,
                    name = ?, owner = ?, knot = ?, added_at = ?, repo_url = ?,
                    last_run_id = ?, payload_json = ?
                WHERE record_uri = ?
                """,
                (
                    now,
                    entry.repo_did,
                    entry.rkey,
                    entry.handle,
                    entry.name,
                    entry.owner,
                    entry.knot,
                    entry.added_at,
                    entry.repo_url,
                    run_id,
                    payload_json,
                    entry.record_uri,
                ),
            )
            updated += 1
    conn.commit()
    return inserted, updated


def diff_manual_entries(conn: sqlite3.Connection, entries: list[ManualEntry]) -> list[ManualDiffEntry]:
    diffs: list[ManualDiffEntry] = []
    for entry in entries:
        previous = conn.execute(
            "SELECT payload_json FROM manual_state WHERE manual_key = ?",
            (entry.manual_key,),
        ).fetchone()
        is_new = previous is None
        payload_json = json.dumps(entry.payload, ensure_ascii=False, sort_keys=True)
        changed = bool(previous and previous["payload_json"] != payload_json)
        if not is_new and not changed:
            continue
        diffs.append(
            ManualDiffEntry(
                manual_key=entry.manual_key,
                kind=entry.kind,
                name=entry.name,
                url=entry.url,
                summary=entry.summary,
                notes=entry.notes,
                status=entry.status,
                is_new=is_new,
            )
        )
    diffs.sort(key=lambda item: (item.is_new, item.name.lower()), reverse=True)
    return diffs


def upsert_manual_state(conn: sqlite3.Connection, run_id: int, entries: list[ManualEntry]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    now = iso_now()
    for entry in entries:
        existing = conn.execute(
            "SELECT manual_key FROM manual_state WHERE manual_key = ?",
            (entry.manual_key,),
        ).fetchone()
        payload_json = json.dumps(entry.payload, ensure_ascii=False, sort_keys=True)
        if existing is None:
            conn.execute(
                """
                INSERT INTO manual_state (
                    manual_key, first_seen_at, last_seen_at, kind, name, url,
                    summary, notes, status, last_run_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.manual_key,
                    now,
                    now,
                    entry.kind,
                    entry.name,
                    entry.url,
                    entry.summary,
                    entry.notes,
                    entry.status,
                    run_id,
                    payload_json,
                ),
            )
            inserted += 1
        else:
            conn.execute(
                """
                UPDATE manual_state
                SET last_seen_at = ?, kind = ?, name = ?, url = ?, summary = ?,
                    notes = ?, status = ?, last_run_id = ?, payload_json = ?
                WHERE manual_key = ?
                """,
                (
                    now,
                    entry.kind,
                    entry.name,
                    entry.url,
                    entry.summary,
                    entry.notes,
                    entry.status,
                    run_id,
                    payload_json,
                    entry.manual_key,
                ),
            )
            updated += 1
    conn.commit()
    return inserted, updated


def filter_diffs(
    diffs: list[DiffEntry],
    *,
    include_builtin: bool,
    only_new: bool,
    limit: int,
) -> list[DiffEntry]:
    rows = diffs
    if not include_builtin:
        rows = [row for row in rows if row.bucket != "builtin"]
    if only_new:
        rows = [row for row in rows if row.is_new]
    return rows[:limit]


T = TypeVar("T")


def filter_simple_diffs(diffs: list[T], only_new: bool, limit: int) -> list[T]:
    rows = diffs
    if only_new:
        rows = [row for row in rows if getattr(row, "is_new", False)]
    return rows[:limit]


def write_report(
    path: Path,
    *,
    collection_diffs: list[DiffEntry],
    github_diffs: list[GitHubDiffEntry],
    tangled_diffs: list[TangledDiffEntry],
    manual_diffs: list[ManualDiffEntry],
    source_url: str,
    total_collection_count: int,
    total_github_count: int,
    total_tangled_count: int,
    total_manual_count: int,
) -> None:
    lines = [
        "# atproto discovery report",
        "",
        f"- generated_at: {iso_now()}",
        f"- collection_source_url: {source_url}",
        f"- changed_collections: {total_collection_count}",
        f"- changed_github_repos: {total_github_count}",
        f"- changed_tangled_repos: {total_tangled_count}",
        f"- changed_manual_candidates: {total_manual_count}",
        "",
    ]

    lines.append("## Collections")
    lines.append("")
    if not collection_diffs:
        lines.append("No new or changed collections.")
        lines.append("")
    else:
        for diff in collection_diffs:
            status = "new" if diff.is_new else "updated"
            lines.append(f"### {diff.collection}")
            lines.append("")
            lines.append(f"- status: {status}")
            lines.append(f"- bucket: {diff.bucket}")
            lines.append(f"- priority: {diff.priority}")
            lines.append(f"- count: {diff.count} ({diff.count_delta:+d})")
            lines.append(f"- recent_count: {diff.recent_count} ({diff.recent_delta:+d})")
            lines.append(f"- min: {diff.min_at or 'unknown'}")
            lines.append(f"- max: {diff.max_at or 'unknown'}")
            lines.append("")

    lines.append("## GitHub")
    lines.append("")
    if not github_diffs:
        lines.append("No new or changed GitHub repos.")
        lines.append("")
    else:
        for diff in github_diffs:
            status = "new" if diff.is_new else "updated"
            lines.append(f"### [{diff.full_name}]({diff.html_url})")
            lines.append("")
            lines.append(f"- status: {status}")
            lines.append(f"- priority: {diff.priority}")
            lines.append(f"- stars: {diff.stargazers_count}")
            lines.append(f"- owner: {diff.owner}")
            lines.append(f"- created_at: {diff.created_at or 'unknown'}")
            lines.append(f"- updated_at: {diff.updated_at or 'unknown'}")
            lines.append(f"- summary: {diff.description or '(none)'}")
            lines.append("")

    lines.append("## Tangled")
    lines.append("")
    if not tangled_diffs:
        lines.append("No new or changed Tangled repos.")
        lines.append("")
    else:
        for diff in tangled_diffs:
            status = "new" if diff.is_new else "updated"
            title = diff.name or diff.record_uri
            if diff.repo_url:
                lines.append(f"### [{title}]({diff.repo_url})")
            else:
                lines.append(f"### {title}")
            lines.append("")
            lines.append(f"- status: {status}")
            lines.append(f"- priority: {diff.priority}")
            lines.append(f"- repo_did: {diff.repo_did}")
            lines.append(f"- handle: {diff.handle or 'unknown'}")
            lines.append(f"- owner: {diff.owner or 'unknown'}")
            lines.append(f"- knot: {diff.knot or 'unknown'}")
            lines.append(f"- added_at: {diff.added_at or 'unknown'}")
            lines.append(f"- summary: {diff.description or '(none)'}")
            lines.append(f"- record_uri: {diff.record_uri}")
            lines.append("")

    lines.append("## Manual")
    lines.append("")
    if not manual_diffs:
        lines.append("No new or changed manual candidates.")
        lines.append("")
    else:
        for diff in manual_diffs:
            status = "new" if diff.is_new else "updated"
            if diff.url:
                lines.append(f"### [{diff.name}]({diff.url})")
            else:
                lines.append(f"### {diff.name}")
            lines.append("")
            lines.append(f"- status: {status}")
            lines.append(f"- kind: {diff.kind}")
            lines.append(f"- candidate_status: {diff.status}")
            lines.append(f"- summary: {diff.summary or '(none)'}")
            lines.append(f"- notes: {diff.notes or '(none)'}")
            lines.append(f"- manual_key: {diff.manual_key}")
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover atproto collections, GitHub repos, and Tangled repos.")
    parser.add_argument("--source-url", help="Override the collection inventory URL from config.")
    parser.add_argument("--report", type=Path, default=ROOT / "reports" / "latest.md", help="Markdown report path.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of rows per section in the report.")
    parser.add_argument("--include-builtin", action="store_true", help="Include app.bsky/chat.bsky/com.atproto collections in the report.")
    parser.add_argument("--only-new", action="store_true", help="Only report collections not seen in previous snapshots.")
    parser.add_argument("--skip-collections", action="store_true", help="Skip collection_count_view discovery.")
    parser.add_argument("--skip-github", action="store_true", help="Skip GitHub repository discovery.")
    parser.add_argument("--skip-tangled", action="store_true", help="Skip Tangled repository discovery.")
    parser.add_argument("--skip-manual", action="store_true", help="Skip loading manual candidates.")
    parser.add_argument("--manual-file", type=Path, default=manual_path_default(), help="JSONL file with manual candidates.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_dirs()
    config = load_config()
    conn = open_db()
    collection_source_url = args.source_url or config["collection_inventory"]["url"]

    collection_diffs: list[DiffEntry] = []
    github_diffs: list[GitHubDiffEntry] = []
    tangled_diffs: list[TangledDiffEntry] = []
    manual_diffs: list[ManualDiffEntry] = []
    collection_stats = {"run_id": "-", "fetched": 0, "changed": 0, "inserted": 0, "updated": 0}
    github_stats = {"run_id": "-", "fetched": 0, "changed": 0, "inserted": 0, "updated": 0}
    tangled_stats = {"run_id": "-", "repos": 0, "fetched": 0, "changed": 0, "inserted": 0, "updated": 0}
    manual_stats = {"run_id": "-", "fetched": 0, "changed": 0, "inserted": 0, "updated": 0}

    if not args.skip_collections:
        try:
            entries = fetch_inventory(collection_source_url)
            run_id = create_run(conn, collection_source_url, len(entries))
            save_snapshot(conn, run_id, entries)
            diffs = diff_entries(conn, entries, config)
            inserted, updated = upsert_state(conn, run_id, entries)
            collection_diffs = filter_diffs(
                diffs,
                include_builtin=args.include_builtin,
                only_new=args.only_new,
                limit=args.limit,
            )
            collection_stats = {
                "run_id": run_id,
                "fetched": len(entries),
                "changed": len(diffs),
                "inserted": inserted,
                "updated": updated,
            }
        except urllib.error.URLError as exc:
            print(f"[warn] collection fetch failed: source_url={collection_source_url!r} error={exc}", file=sys.stderr)
        except Exception as exc:
            print(f"[warn] collection error: source_url={collection_source_url!r} error={exc}", file=sys.stderr)

    if config.get("github_repo_search", {}).get("enabled", False) and not args.skip_github:
        try:
            github_entries = fetch_github_repos(config)
            github_run_id = create_github_run(conn, len(github_entries))
            save_github_snapshot(conn, github_run_id, github_entries)
            all_github_diffs = diff_github_entries(conn, github_entries)
            inserted, updated = upsert_github_state(conn, github_run_id, github_entries)
            github_diffs = filter_simple_diffs(all_github_diffs, args.only_new, args.limit)
            github_stats = {
                "run_id": github_run_id,
                "fetched": len(github_entries),
                "changed": len(all_github_diffs),
                "inserted": inserted,
                "updated": updated,
            }
        except urllib.error.URLError as exc:
            print(f"[warn] github fetch failed: error={exc}", file=sys.stderr)
        except Exception as exc:
            print(f"[warn] github error: error={exc}", file=sys.stderr)

    if config.get("tangled_repo_discovery", {}).get("enabled", False) and not args.skip_tangled:
        tangled_source_url = config["tangled_repo_discovery"]["ufos_api_url"]
        collection = config["tangled_repo_discovery"]["collection"]
        try:
            tangled_entries, repo_count = fetch_tangled_repos(config)
            tangled_run_id = create_tangled_run(conn, tangled_source_url, collection, repo_count, len(tangled_entries))
            save_tangled_snapshot(conn, tangled_run_id, tangled_entries)
            all_tangled_diffs = diff_tangled_entries(conn, tangled_entries)
            inserted, updated = upsert_tangled_state(conn, tangled_run_id, tangled_entries)
            tangled_diffs = filter_simple_diffs(all_tangled_diffs, args.only_new, args.limit)
            tangled_stats = {
                "run_id": tangled_run_id,
                "repos": repo_count,
                "fetched": len(tangled_entries),
                "changed": len(all_tangled_diffs),
                "inserted": inserted,
                "updated": updated,
            }
        except urllib.error.URLError as exc:
            print(f"[warn] tangled fetch failed: source_url={tangled_source_url!r} error={exc}", file=sys.stderr)
        except Exception as exc:
            print(f"[warn] tangled error: source_url={tangled_source_url!r} error={exc}", file=sys.stderr)

    if not args.skip_manual:
        try:
            manual_entries = fetch_manual_entries(args.manual_file)
            manual_run_id = create_manual_run(conn, len(manual_entries))
            save_manual_snapshot(conn, manual_run_id, manual_entries)
            all_manual_diffs = diff_manual_entries(conn, manual_entries)
            inserted, updated = upsert_manual_state(conn, manual_run_id, manual_entries)
            manual_diffs = filter_simple_diffs(all_manual_diffs, args.only_new, args.limit)
            manual_stats = {
                "run_id": manual_run_id,
                "fetched": len(manual_entries),
                "changed": len(all_manual_diffs),
                "inserted": inserted,
                "updated": updated,
            }
        except Exception as exc:
            print(f"[warn] manual import error: file={args.manual_file!s} error={exc}", file=sys.stderr)

    write_report(
        args.report,
        collection_diffs=collection_diffs,
        github_diffs=github_diffs,
        tangled_diffs=tangled_diffs,
        manual_diffs=manual_diffs,
        source_url=collection_source_url,
        total_collection_count=collection_stats["changed"],
        total_github_count=github_stats["changed"],
        total_tangled_count=tangled_stats["changed"],
        total_manual_count=manual_stats["changed"],
    )

    print(
        " ".join(
            [
                f"collection_run_id={collection_stats['run_id']}",
                f"collection_fetched={collection_stats['fetched']}",
                f"collection_changed={collection_stats['changed']}",
                f"github_run_id={github_stats['run_id']}",
                f"github_fetched={github_stats['fetched']}",
                f"github_changed={github_stats['changed']}",
                f"tangled_run_id={tangled_stats['run_id']}",
                f"tangled_repos={tangled_stats['repos']}",
                f"tangled_fetched={tangled_stats['fetched']}",
                f"tangled_changed={tangled_stats['changed']}",
                f"manual_run_id={manual_stats['run_id']}",
                f"manual_fetched={manual_stats['fetched']}",
                f"manual_changed={manual_stats['changed']}",
                f"report={args.report}",
                f"db={DB_PATH}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
