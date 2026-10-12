"""Read-only resolution of a visited page and retrieval of operator plans."""

from __future__ import annotations

import sqlite3
import urllib.parse
from typing import Any

from .. import operator_feedback, project_record
from ..identity import identifiers
from ..repositories import repo_urls
from ..persistence.concurrency import consistent_read
from . import presentation


FORGE_HOSTS = {"github.com", "tangled.org", "codeberg.org", "gitlab.com", "git.sr.ht"}
SHARED_HOSTS = FORGE_HOSTS | {"bsky.app", "pds.ls", "pdsls.dev"}


def page_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(value).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("an HTTP(S) page URL without credentials is required")
        parsed.port
    except ValueError as error:
        raise ValueError("an HTTP(S) page URL without credentials is required") from error
    return identifiers.normalize_url(urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))


def _domain_namespace(value: str) -> str:
    """A host's reversed labels, without widening to a parent domain."""
    try:
        url = page_url(value if "://" in value else "https://" + value)
        host = urllib.parse.urlsplit(url).hostname.rstrip(".").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return ""
    namespace = ".".join(reversed(host.split(".")))
    return namespace.lower() if identifiers.is_valid_nsid(namespace + ".record") else ""


def _add_nsid_matches(conn: sqlite3.Connection, value: str, matches: dict[str, dict[str, Any]], *, existing_only: bool = False) -> None:
    namespace = _domain_namespace(value)
    if not namespace:
        return
    rows = conn.execute("""
        SELECT i.project_key, i.kind, i.value, i.classification,
               p.name, p.tracking_state, p.introduced_url
        FROM project_identifiers i JOIN projects p ON p.project_key = i.project_key
        WHERE i.kind = 'nsid' AND i.role = 'owned_nsid'
          AND i.classification IN ('confirmed', 'candidate') AND LOWER(i.value) LIKE ?
        ORDER BY i.project_key, i.value
    """, (namespace + ".%",)).fetchall()
    for row in rows:
        if not identifiers.is_valid_nsid(row["value"]) or (existing_only and row["project_key"] not in matches):
            continue
        item = matches.setdefault(row["project_key"], {
            "project_key": row["project_key"], "name": row["name"],
            "tracking_state": row["tracking_state"], "introduced_url": row["introduced_url"], "matches": [],
        })
        item.setdefault("matches", []).append({
            "kind": "nsid", "value": row["value"], "classification": row["classification"], "match": "nsid_domain",
        })


@consistent_read
def search(conn: sqlite3.Connection, query: str, *, limit: int = 50) -> dict[str, Any]:
    """Keep ordinary text search and supplement it with domain-derived NSIDs."""
    result = presentation.search_projects(conn, query=query, limit=limit, nsid_namespace=_domain_namespace(query.strip()))
    matches = {item["project_key"]: item for item in result["items"]}
    _add_nsid_matches(conn, query.strip(), matches, existing_only=True)
    return result


@consistent_read
def resolve_page(conn: sqlite3.Connection, url: str) -> dict[str, Any]:
    normalized = page_url(url)
    parsed = urllib.parse.urlsplit(normalized)
    paths = [parsed.path]
    path = parsed.path
    while path:
        path = path.rsplit("/", 1)[0]
        paths.append(path)
    values = {urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", "")) for path in paths}
    # Repository URL normalization knows the provider's repository boundary.
    repository = repo_urls.canonical_repo_url(normalized) if parsed.hostname in FORGE_HOSTS else ""
    if repository:
        values.add(repository)
    placeholders = ",".join("?" for _ in values)
    rows = conn.execute(f"""
        SELECT i.project_key, i.kind, i.value, i.role, i.classification,
               p.name, p.tracking_state, p.introduced_url
        FROM project_identifiers i JOIN projects p ON p.project_key = i.project_key
        WHERE i.value IN ({placeholders}) AND i.classification IN ('confirmed', 'candidate')
          AND ((i.kind = 'service_url' AND i.role = 'service')
            OR (i.kind = 'site_url' AND i.role = 'project_site')
            OR (i.kind = 'repo_url' AND i.role = 'project_repository'))
        ORDER BY i.project_key, i.value
    """, sorted(values)).fetchall()
    matches: dict[str, dict[str, Any]] = {}
    for row in rows:
        stored = urllib.parse.urlsplit(row["value"])
        if parsed.hostname in SHARED_HOSTS and row["value"] != normalized:
            if row["kind"] != "repo_url" or not repository or row["value"] != repository:
                continue
        if row["kind"] == "repo_url" and row["value"] != normalized and row["value"] != repository:
            continue
        # Root service URLs on ordinary hosts may match descendants. A path
        # prefix is always a complete segment, never a sibling such as /app-2.
        if row["value"] != repository and (stored.netloc != parsed.netloc or stored.scheme != parsed.scheme):
            continue
        item = matches.setdefault(row["project_key"], {
            "project_key": row["project_key"], "name": row["name"],
            "tracking_state": row["tracking_state"], "introduced_url": row["introduced_url"], "matches": [],
        })
        item["matches"].append({
            "kind": row["kind"], "value": row["value"], "classification": row["classification"],
            "match": "exact" if row["value"] == normalized else "repository" if row["kind"] == "repo_url" else "descendant",
        })
    url_match_count = len(matches)
    _add_nsid_matches(conn, normalized, matches)
    items = list(matches.values())
    automatic = len(items) == 1 and any(
        match["kind"] != "nsid" and match["classification"] == "confirmed" for match in items[0]["matches"]
    )
    return {
        "url": normalized, "items": items,
        "project_key": items[0]["project_key"] if automatic else "",
        "status": "matched" if automatic else "choose" if items else "unmatched",
        "seed": repository or normalized, "url_match_count": url_match_count,
    }


@consistent_read
def introduction_plans(conn: sqlite3.Connection, *, query: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    items = []
    for project in project_record.list_projects_with_decision(conn, "plan_introduction"):
        plan = operator_feedback.active_plan(project)
        if not plan or (query and query.casefold() not in f"{project['name']} {plan.get('reason', '')}".casefold()):
            continue
        items.append({
            "project_key": project["project_key"], "name": project["name"],
            "tracking_state": project["tracking_state"], "service_status": project["service_status"],
            "introduced_url": project.get("introduced_url", ""), "tagline": project.get("tagline", ""),
            "plan": plan,
        })
    items.sort(key=lambda item: (item["plan"].get("added_at", ""), item["project_key"]), reverse=True)
    limit, offset = max(1, min(100, limit)), max(0, offset)
    return {"items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset}
