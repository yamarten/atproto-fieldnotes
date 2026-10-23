"""Persistent review of posts seen by introduced sync.

The review ledger lives beside the introduced watermark so existing schema-v4
databases can use it without a project-record migration.  One item represents
one URL in one post (or a post with no URL).
"""

from __future__ import annotations

from . import tracking

import copy
import json
import re
import sqlite3
import urllib.parse
import uuid
from typing import Any

from .identity import identifiers
from . import naming, project_record
from . import project_merge
from .repositories import repo_urls
from .persistence import concurrency, database
from project_registry_core.registry import applog_seed_from_url
from project_registry_core.transport.atproto import bsky_post_web_url, extract_post_link_entries


TEXT_URL = re.compile(r"https?://[^\s<>\[\]{}\"']+", re.IGNORECASE)
MANUAL_STATUSES = {"ignored", "linked"}


def canonical_item_keys(conn: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(item)
    def canonical(key: str) -> str:
        return database.project_key_alias_target(conn, key) or key
    for field in ("project_key", "auto_set_project_key", "cleared_auto_project_key", "created_project_key", "resolved_project_key"):
        if item.get(field):
            item[field] = canonical(str(item[field]))
    item["matched_project_keys"] = list(dict.fromkeys(canonical(str(key)) for key in item.get("matched_project_keys", [])))
    for match in item.get("candidate_matches", []):
        match["project_key"] = canonical(str(match["project_key"]))
    return item


def link_identity(url: str) -> tuple[str, tuple[str, str] | None]:
    """Classify the resource, retaining a PR's repository as a search hint."""
    if not url:
        return "target", None
    seed = applog_seed_from_url(url) if url else None
    if seed:
        return "target", seed
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if repo_urls.is_supported_repo_url(url):
        repo = repo_urls.canonical_repo_url(url)
        if len([part for part in parsed.path.split("/") if part]) == 2:
            return "target", ("repo_url", repo)
        return "reference", ("repo_url", repo)
    if host in {"github.com", "tangled.org", "tangled.sh"}:
        return "reference", None
    if (re.match(r"^/t/[^/]+/\d+(?:/|$)", parsed.path)
            or host in {"bsky.app", "youtube.com", "www.youtube.com", "youtu.be",
                        "raw.githubusercontent.com", "docs.bsky.app", "docs.tangled.org", "blog.tangled.org"}
            or project_record.is_explicitly_non_project_service_url(url)
            or project_record.is_asset_like_url(url)):
        return "reference", None
    if parsed.scheme in {"http", "https"} and host:
        return "target", ("service_url", identifiers.normalize_url(url))
    return "target", None


class CandidateSearch:
    """Request-local prefilter snapshot and lazily hydrated comparison records."""
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.inputs = None
        self.projects: dict[str, dict[str, Any]] = {}
        self.results: dict[str, list[dict[str, Any]]] = {}

    def compare(self, probe: dict[str, Any]) -> list[dict[str, Any]]:
        cache_key = json.dumps([[(seed["type"], seed["value"]) for seed in probe.get("manual_seeds", [])],
                                probe.get("name")], sort_keys=True)
        if cache_key not in self.results:
            if self.inputs is None:
                self.inputs = project_record.list_duplicate_candidate_inputs(self.conn)
            keys = project_merge.duplicate_candidate_keys(self.inputs, project_merge.duplicate_profile(probe))
            if keys is None:
                keys = {str(item["project_key"]) for item in self.inputs}
            missing = keys - self.projects.keys()
            if missing:
                self.projects.update({p["project_key"]: p for p in project_record.list_duplicate_projects(
                    self.conn, project_keys=missing)})
            self.results[cache_key] = project_merge.compute_duplicate_candidates(
                self.conn, probe, projects=[self.projects[key] for key in keys if key in self.projects])
        return copy.deepcopy(self.results[cache_key])


def candidate_matches(conn: sqlite3.Connection, item: dict[str, Any], search: CandidateSearch | None = None) -> list[dict[str, Any]]:
    """A post supplies a small comparison probe, never a persisted project."""
    seed = (str(item.get("seed_type") or ""), str(item.get("seed_value") or ""))
    label = str(item.get("label") or "").strip()
    if not all(seed) and not label:
        return []
    from .registry import add_manual_seed
    probe = project_record.default_project_record("introduced:probe", *(seed if all(seed) else ("service_url", "")))
    if all(seed):
        add_manual_seed(probe, *seed, source="introduced_comparison")
    # A linked name can help; the complete post describes unrelated projects.
    if label and not TEXT_URL.search(label):
        probe["name"] = label.split(":", 1)[0].split("：", 1)[0].strip("* ")
    for hint in item.get("reference_seeds", []):
        if isinstance(hint, list) and len(hint) == 2:
            add_manual_seed(probe, *hint, source="introduced_reference")
    matches = (search or CandidateSearch(conn)).compare(probe)
    exact = _matches(conn, seed if all(seed) else None)
    by_key = {match["project_key"]: match for match in matches}
    for key in exact:
        candidate = by_key.setdefault(key, {"project_key": key, "score": 0, "reasons": {}})
        candidate["reasons"]["exact_identifier"] = [seed[1]]
    return sorted(by_key.values(), key=lambda match: (match["project_key"] not in exact,
                                                      -match["score"], match["project_key"]))


def refresh_item(conn: sqlite3.Connection, item: dict[str, Any], search: CandidateSearch | None = None,
                 *, explore: bool = True) -> dict[str, Any]:
    item = canonical_item_keys(conn, item)
    role, seed = link_identity(str(item.get("url") or ""))
    item["link_role"] = role
    item["seed_type"], item["seed_value"] = seed or ("", "")
    if item.get("status") in MANUAL_STATUSES:
        return item
    if explore:
        item["candidate_matches"] = candidate_matches(conn, item, search)
    else:
        by_key = {match["project_key"]: match for match in item.get("candidate_matches", [])}
        for priority, key in project_record.indexed_identifier_matches(conn, *seed) if seed else []:
            reason = "exact_identifier" if priority == 0 else "candidate_identifier"
            by_key.setdefault(key, {"project_key": key, "score": 0, "reasons": {reason: [seed[1]]}})
        item["candidate_matches"] = list(by_key.values())
    item["matched_project_keys"] = [match["project_key"] for match in item["candidate_matches"]]
    if role == "reference" and not item.get("review_as_target"):
        item.update(status="reference", reason="reference_link")
        return item
    item["reason"] = reason_for_item(conn, item, item["matched_project_keys"])
    item["status"] = "open" if item["reason"] else "auto_applied"
    return item


def target_post_line(text: str, url: str, facets: list[dict[str, Any]] | None = None) -> str:
    """Return a single, unambiguous URL line; never guess from a label."""
    if not url:
        return ""
    line_indexes: set[int] = set()
    for match in TEXT_URL.finditer(text):
        found = identifiers.normalize_url(match.group().rstrip(".,;:!?)]}、。！？」"))
        if found == url:
            line_indexes.add(text[:match.start()].count("\n"))
    raw = text.encode("utf-8")
    for facet in facets or []:
        if not isinstance(facet, dict) or not any(
            isinstance(feature, dict) and feature.get("$type") == "app.bsky.richtext.facet#link"
            and identifiers.normalize_url(str(feature.get("uri") or "")) == url
            for feature in facet.get("features", [])
        ):
            continue
        index = facet.get("index") or {}
        if not isinstance(index, dict):
            continue
        start, end = index.get("byteStart"), index.get("byteEnd")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw):
            continue
        try:
            raw[:start].decode("utf-8")
            label = raw[start:end].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if label.strip() and "\n" not in label and "\r" not in label:
            line_indexes.add(raw[:start].count(b"\n"))
    if len(line_indexes) != 1:
        return ""
    return text.split("\n")[next(iter(line_indexes))].strip()


def item_id(post_uri: str, url: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"introduced:{post_uri}\n{url}").hex


def post_links(post: dict[str, Any]) -> list[dict[str, str]]:
    links = extract_post_link_entries(post)
    seen = {entry["url"] for entry in links}
    record = post.get("record") if isinstance(post.get("record"), dict) else {}
    for match in TEXT_URL.finditer(str(record.get("text") or "")):
        url = identifiers.normalize_url(match.group().rstrip(".,;:!?)]}、。！？」"))
        if url and url not in seen:
            links.append({"url": url, "label": "", "source": "text"})
            seen.add(url)
    for entry in links:
        entry.setdefault("source", "facet")
    return links


def posts_for_automatic_matching(posts: list[dict[str, Any]], review_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    suppressed = {str(item.get("id")) for item in review_items if item.get("status") in MANUAL_STATUSES}
    suppressed_legacy = {
        (str(item.get("post_web_url") or ""), str(item.get("seed_type") or ""), str(item.get("seed_value") or ""))
        for item in review_items if item.get("status") in MANUAL_STATUSES and item.get("url_source") == "legacy"
    }
    if not suppressed and not suppressed_legacy:
        return posts
    output: list[dict[str, Any]] = []
    for post in posts:
        uri = str(post.get("uri") or "")
        web_url = bsky_post_web_url(post)
        filtered = copy.deepcopy(post)
        record = filtered.get("record") if isinstance(filtered.get("record"), dict) else {}
        facets = []
        for facet in record.get("facets", []):
            if not isinstance(facet, dict):
                continue
            facet["features"] = [feature for feature in facet.get("features", []) if
                                 not (isinstance(feature, dict) and
                                      (item_id(uri, identifiers.normalize_url(str(feature.get("uri") or ""))) in suppressed
                                       or (web_url, *(applog_seed_from_url(str(feature.get("uri") or "")) or ("", "")))
                                       in suppressed_legacy))]
            if facet["features"]:
                facets.append(facet)
        record["facets"] = facets
        output.append(filtered)
    return output


def _matches(conn: sqlite3.Connection, seed: tuple[str, str] | None) -> list[str]:
    if seed is None:
        return []
    return [key for priority, key in project_record.indexed_identifier_matches(conn, *seed) if priority == 0]


def _reason(seed: tuple[str, str] | None, matches: list[str], *, no_url: bool = False) -> str:
    if no_url:
        return "no_url"
    if seed is None:
        return "no_seed"
    if not matches:
        return "unmatched"
    if len(matches) > 1:
        return "multiple_projects"
    return ""


def reason_for_item(conn: sqlite3.Connection, item: dict[str, Any], matches: list[str], *, allow_apply: bool = False) -> str:
    item.pop("resolution_only", None)
    seed = (str(item.get("seed_type") or ""), str(item.get("seed_value") or ""))
    exact = _matches(conn, seed if all(seed) else None)
    # An existing introduction is independent of identifier adoption. Candidate
    # exact matches can identify an already assigned project, never assign one.
    existing_matches = [key for _, key in project_record.indexed_identifier_matches(conn, *seed)] if all(seed) else []
    web_url = str(item.get("post_web_url") or "")
    # A pre-existing/manual assignment can settle a broader candidate search.
    # The old first-of-many automatic assignment is explicitly not proof.
    assigned = []
    for key in existing_matches if web_url else []:
        if not conn.execute("SELECT 1 FROM projects WHERE project_key=? AND introduced_url=?", (key, web_url)).fetchone():
            continue
        support = conn.execute("""SELECT json_extract(j.value,'$.status') AS status,
                json_extract(j.value,'$.project_key') AS project_key,
                json_extract(j.value,'$.auto_set_project_key') AS auto_key,
                json_extract(j.value,'$.reason') AS reason
            FROM sync_state, json_each(watermark_json,'$.review_items') j
            WHERE kind='introduced' AND json_extract(j.value,'$.post_web_url')=?""", (web_url,)).fetchall()
        manual = any(row["status"] == "linked" and row["project_key"] == key for row in support)
        ambiguous = (key == item.get("auto_set_project_key") and item.get("status") != "auto_applied") or any(
            row["status"] == "open" and row["auto_key"] == key and row["reason"] == "multiple_projects"
            for row in support)
        if manual or not ambiguous:
            assigned.append(key)
    if len(assigned) == 1:
        item["resolved_project_key"] = assigned[0]
        # Suppressing a duplicate question must not become independent support
        # that prevents undoing the actual manual assignment.
        item["resolution_only"] = len(existing_matches) > 1
        return ""
    item.pop("resolved_project_key", None)
    reason = _reason(seed if all(seed) else None, exact, no_url=not item.get("url"))
    if reason:
        if reason == "unmatched" and matches:
            return "multiple_projects" if len(matches) > 1 else "candidate_match"
        return reason
    if item.get("url_source") == "text" and not allow_apply:
        return "text_url_not_extracted"
    if not web_url:
        return "post_url_unavailable"
    project = project_record.fetch_project(conn, exact[0])
    if project and project.get("introduced_url") and project["introduced_url"] != web_url:
        return "different_introduced_url"
    if project and not project.get("introduced_url") and not allow_apply:
        return "not_applied"
    item["resolved_project_key"] = exact[0]
    return ""


@concurrency.consistent_read
def observe_posts(conn: sqlite3.Connection, posts: list[dict[str, Any]], *, explore: bool = False) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for post in posts:
        uri = str(post.get("uri") or "").strip()
        if not uri:
            continue
        record = post.get("record") if isinstance(post.get("record"), dict) else {}
        text = str(record.get("text") or "")
        web_url = bsky_post_web_url(post)
        if not web_url:
            parts = uri.removeprefix("at://").split("/")
            if uri.startswith("at://") and len(parts) >= 3 and parts[1] == "app.bsky.feed.post":
                web_url = f"https://bsky.app/profile/{urllib.parse.quote(parts[0], safe=':')}/post/{urllib.parse.quote(parts[2], safe='')}"
        links = post_links(post)
        for entry in links or [{"url": "", "label": "", "source": "none"}]:
            url = str(entry["url"])
            role, seed = link_identity(url)
            item = {
                "id": item_id(uri, url), "post_uri": uri, "post_web_url": web_url,
                "post_text": text, "indexed_at": str(post.get("indexedAt") or record.get("createdAt") or ""),
                "post_line": target_post_line(text, url, record.get("facets", [])),
                "url": url, "url_source": entry["source"], "label": str(entry.get("label") or ""),
                "seed_type": seed[0] if seed else "", "seed_value": seed[1] if seed else "",
                "link_role": role,
            }
            output.append(item)
    # Only a demonstrably identical line may share reference hints.
    for item in output:
        item["reference_seeds"] = [
            [other["seed_type"], other["seed_value"]] for other in output
            if other["post_uri"] == item["post_uri"] and other["link_role"] == "reference"
            and other["seed_type"] and item["post_line"] and other["post_line"] == item["post_line"]
            and other["id"] != item["id"]
        ]
    search = CandidateSearch(conn)
    output = [refresh_item(conn, item, search, explore=explore) for item in output]
    return output


def legacy_pending_items(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        seed_type = str(entry.get("seed_type") or "")
        seed_value = str(entry.get("seed_value") or "")
        if not seed_type or not seed_value:
            continue
        for web_url in entry.get("post_web_urls", []) or [""]:
            web_url = str(web_url or "")
            uri = f"legacy:{web_url or seed_type + ':' + seed_value}"
            output.append({
                "id": item_id(uri, seed_value), "post_uri": uri, "post_web_url": web_url,
                "post_text": "", "indexed_at": "", "url": seed_value, "url_source": "legacy",
                "label": str((entry.get("labels") or [""])[0]), "seed_type": seed_type,
                "seed_value": seed_value, "matched_project_keys": [], "reason": "unmatched",
                "status": "open",
            })
    return output


def merge_items(existing: list[dict[str, Any]], observed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item.get("id")): dict(item) for item in existing if isinstance(item, dict) and item.get("id")}
    for item in observed:
        previous = by_id.get(item["id"])
        if previous is None:
            old_id = next((key for key, old in by_id.items() if old.get("url_source") == "legacy"
                           and old.get("post_web_url") == item.get("post_web_url")
                           and old.get("seed_type") == item.get("seed_type")
                           and old.get("seed_value") == item.get("seed_value")
                           and old.get("seed_type")), None)
            if old_id is not None:
                previous = by_id.pop(old_id)
        if previous and (previous.get("status") in MANUAL_STATUSES or previous.get("review_as_target")):
            # A repeated search result never revives an operator decision.
            by_id[item["id"]] = {**item, **{
                key: previous[key] for key in ("status", "reason", "project_key", "reviewed_at", "link_changed",
                                               "previous_introduced_url", "previous_reason", "auto_set_project_key",
                                               "cleared_auto_project_key", "review_as_target", "created_project_key")
                if key in previous
            }}
        else:
            by_id[item["id"]] = ({**item, "auto_set_project_key": previous["auto_set_project_key"]}
                                 if previous and previous.get("auto_set_project_key") and
                                 not item.get("auto_set_project_key") and
                                 previous.get("post_web_url") == item.get("post_web_url") else item)
    return sorted(by_id.values(), key=lambda item: (str(item.get("indexed_at") or ""), str(item["id"])), reverse=True)


def save_state(conn: sqlite3.Connection, state: dict[str, Any], watermark: dict[str, Any], *, scan_complete: bool) -> None:
    conn.execute(
        """INSERT INTO sync_state(scope_key,kind,scope_json,watermark_json,last_success_at)
           VALUES (?,?,?,?,?) ON CONFLICT(scope_key) DO UPDATE SET
           scope_json=excluded.scope_json, watermark_json=excluded.watermark_json,
           last_success_at=excluded.last_success_at""",
        (state["scope_key"], "introduced", json.dumps(state["scope"], ensure_ascii=False, sort_keys=True),
         json.dumps(watermark, ensure_ascii=False, sort_keys=True),
         project_record.iso_now() if scan_complete else state.get("last_success_at", "")),
    )


def review_payload(conn: sqlite3.Connection, *, include_resolved: bool = False, limit: int = 100, offset: int = 0,
                   scope_key: str = "", target_id: str = "") -> dict[str, Any]:
    with concurrency.read_snapshot(conn):
        return _review_payload(conn, include_resolved=include_resolved, limit=limit, offset=offset,
                               scope_key=scope_key, target_id=target_id)


def _review_payload(conn: sqlite3.Connection, *, include_resolved: bool, limit: int, offset: int,
                    scope_key: str, target_id: str) -> dict[str, Any]:
    rows = conn.execute("SELECT scope_key, scope_json, watermark_json, last_success_at FROM sync_state WHERE kind='introduced'").fetchall()
    items: list[dict[str, Any]] = []
    scopes: list[dict[str, Any]] = []
    for row in rows:
        if target_id and row["scope_key"] != scope_key:
            continue
        watermark = json.loads(row["watermark_json"] or "{}")
        scope = json.loads(row["scope_json"] or "{}")
        scopes.append({"scope_key": row["scope_key"], "query": scope.get("query", ""),
                       "watermark": watermark.get("timestamp", ""), "last_complete_scan_at": row["last_success_at"],
                       "last_scan_complete": watermark.get("last_scan_complete"),
                       "last_scan_at": watermark.get("last_scan_at", ""),
                       "last_scan_pages": watermark.get("last_scan_pages", 0),
                       "last_scan_fetched_posts": watermark.get("last_scan_fetched_posts", 0)})
        for item in watermark.get("review_items", []):
            if isinstance(item, dict):
                if target_id and item.get("id") != target_id:
                    continue
                items.append({**item, "scope_key": row["scope_key"], "query": scope.get("query", "")})
        for item in legacy_pending_items(watermark.get("pending_entries", [])):
            if target_id and item["id"] != target_id:
                continue
            if not any(other["id"] == item["id"] for other in items):
                items.append({**item, "scope_key": row["scope_key"], "query": scope.get("query", "")})
    search = CandidateSearch(conn)
    items = [refresh_item(conn, item, search, explore=bool(target_id)) for item in items]
    items.sort(key=lambda item: (str(item.get("indexed_at") or ""), str(item.get("id") or "")), reverse=True)
    open_items = [item for item in items if item.get("status") == "open"]
    shown = items if include_resolved else open_items
    page = shown[offset:offset + limit]
    candidates: dict[str, dict[str, Any] | None] = {}
    def project_preview(key: str) -> dict[str, Any] | None:
        if key not in candidates:
            row = conn.execute("SELECT project_key,name,introduced_url FROM projects WHERE project_key=?", (key,)).fetchone()
            candidates[key] = dict(row) if row else None
        return candidates[key]

    for item in page:
        item["post_line"] = item.get("post_line", target_post_line(
            str(item.get("post_text") or ""), str(item.get("url") or "")))
        item["matched_projects"] = []
        for key in item.get("matched_project_keys", []):
            preview = project_preview(key)
            if preview is not None:
                match = next((match for match in item.get("candidate_matches", []) if match["project_key"] == key), {})
                item["matched_projects"].append({**preview, "match_reasons": match.get("reasons", {}),
                                                 "match_score": match.get("score", 0)})
        if item.get("status") == "linked" and item.get("project_key"):
            item["linked_project"] = project_preview(item["project_key"])
    return {"items": page, "count": len(shown), "open_count": len(open_items),
            "scopes": scopes, "offset": offset, "has_more": len(shown) > offset + limit,
            "coverage_note": "要確認が0件でも検索結果の完全取得を保証しません。直近の走査完了状態と取得上限を確認してください。"}


def other_review_items(conn: sqlite3.Connection, scope_key: str, items: list[dict[str, Any]],
                       target_id: str) -> list[dict[str, Any]]:
    others = [other for other in items if other.get("id") != target_id]
    for row in conn.execute(
        "SELECT scope_key,watermark_json FROM sync_state WHERE kind='introduced' AND scope_key != ?", (scope_key,)
    ):
        watermark = json.loads(row["watermark_json"] or "{}")
        others.extend({**other, "_scope_key": row["scope_key"]} for other in watermark.get("review_items", [])
                      if isinstance(other, dict))
    return [canonical_item_keys(conn, other) for other in others]


def transfer_rollback(conn: sqlite3.Connection, scope_key: str, linked: dict[str, Any], previous: str) -> None:
    other_scope = linked.get("_scope_key")
    if not other_scope or other_scope == scope_key:
        linked["link_changed"] = True
        linked["previous_introduced_url"] = previous
        return
    row = conn.execute("SELECT watermark_json FROM sync_state WHERE scope_key=? AND kind='introduced'",
                       (other_scope,)).fetchone()
    if row is None:
        return
    watermark = json.loads(row["watermark_json"] or "{}")
    for stored in watermark.get("review_items", []):
        if stored.get("id") == linked.get("id") and stored.get("status") == "linked":
            stored["link_changed"] = True
            stored["previous_introduced_url"] = previous
            conn.execute("UPDATE sync_state SET watermark_json=? WHERE scope_key=?",
                         (json.dumps(watermark, ensure_ascii=False, sort_keys=True), other_scope))
            return


def supports_introduced_url(item: dict[str, Any], project_key: str, post_web_url: str) -> bool:
    if item.get("post_web_url") != post_web_url:
        return False
    if item.get("status") == "linked" and item.get("project_key") == project_key:
        return True
    if item.get("status") == "auto_applied" and not item.get("resolution_only") and project_key == (item.get("resolved_project_key") or
            (item.get("matched_project_keys") or [""])[0]):
        return True
    return item.get("status") == "open" and item.get("auto_set_project_key") == project_key


def clear_auto_assignment(conn: sqlite3.Connection, scope_key: str, item: dict[str, Any],
                          items: list[dict[str, Any]], *, linked_project_key: str = "") -> str:
    key = str(item.get("auto_set_project_key") or "")
    if not key or key == linked_project_key:
        return ""
    # Another decision or automatic match for this post still supports the
    # introduced URL, so resolving this item must not clear that project.
    others = other_review_items(conn, scope_key, items, str(item.get("id") or ""))
    if any(supports_introduced_url(other, key, str(item.get("post_web_url") or "")) for other in others):
        # If a linked item is now the only remaining support for this sync's
        # automatic assignment, let its eventual undo restore the empty value.
        if not any(other.get("status") in {"open", "auto_applied"}
                   and supports_introduced_url(other, key, str(item.get("post_web_url") or "")) for other in others):
            linked = next((other for other in others if other.get("id") != item.get("id")
                           and other.get("status") == "linked" and other.get("project_key") == key
                           and other.get("post_web_url") == item.get("post_web_url")), None)
            if linked and not linked.get("link_changed"):
                transfer_rollback(conn, scope_key, linked, "")
        return ""
    project = project_record.fetch_project(conn, key)
    if project is None or project.get("introduced_url") != item.get("post_web_url"):
        return ""
    tracking.set_introduced_url(project, "")
    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True, commit=False)
    return key


def resolve_item(conn: sqlite3.Connection, *, scope_key: str, target_id: str, action: str,
                 project_key: str = "", replace: bool = False, seed_type: str = "", seed_value: str = "",
                 name: str = "") -> dict[str, Any]:
    if action not in {"ignore", "link", "create", "reopen", "recheck", "review_target"}:
        raise ValueError("invalid introduced review action")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT scope_json,watermark_json,last_success_at FROM sync_state WHERE scope_key=? AND kind='introduced'", (scope_key,)).fetchone()
        if row is None:
            raise KeyError("introduced review scope not found")
        state = {"scope_key": scope_key, "scope": json.loads(row["scope_json"]), "last_success_at": row["last_success_at"]}
        watermark = json.loads(row["watermark_json"])
        items = [canonical_item_keys(conn, item) for item in merge_items(
            watermark.get("review_items", []), legacy_pending_items(watermark.get("pending_entries", [])))]
        item = next((entry for entry in items if entry["id"] == target_id), None)
        if item is None:
            raise KeyError("introduced review item not found")
        if action in {"link", "create"}:
            if action == "link" and project_key:
                project_key = database.project_key_alias_target(conn, project_key) or project_key
            if action == "create":
                if item.get("status") != "open":
                    raise ValueError("only an open item can create a project")
                normalized = project_record.canonical_seed_value(seed_type, seed_value.strip())
                if seed_type not in {"repo_url", "service_url", "nsid"} or not normalized:
                    raise ValueError("a valid project URL or NSID is required")
                if seed_type == "nsid":
                    if not identifiers.is_valid_nsid(normalized):
                        raise ValueError("a valid NSID is required")
                else:
                    role, inferred = link_identity(identifiers.normalize_url(seed_value.strip()))
                    # Do not silently turn a PR/topic into a project seed.
                    if role != "target" or not inferred or inferred[0] != seed_type:
                        raise ValueError("use a project URL, repository root, or NSID instead of a reference link")
                if _matches(conn, (seed_type, normalized)):
                    raise ValueError("this identifier already belongs to a project; select it for linking")
                from .application import projects
                project_key = project_record.allocate_new_project_key(conn)
                project_record.save_project(conn, project_record.default_project_record(project_key, seed_type, normalized),
                                            commit=False)
                projects.add_seed(conn, seed=normalized, seed_type=seed_type, project_key=project_key, commit=False)
                project = project_record.fetch_project(conn, project_key)
                if name.strip():
                    project["name"] = name.strip()
                    naming.add_name_candidate(project, name.strip(), "operator_edit")
                    project_record.save_project(conn, project, commit=False)
                item["created_project_key"] = project_key
            if item.get("status") != "open" or not project_key:
                raise ValueError("open item and project_key are required")
            project = project_record.fetch_project(conn, project_key)
            if project is None:
                raise KeyError("project not found")
            web_url = str(item.get("post_web_url") or "")
            if not web_url:
                raise ValueError("post web URL is required")
            previous = str(project.get("introduced_url") or "")
            if previous and previous != web_url and not replace:
                raise ValueError("project has a different introduced URL; confirm replacement")
            cleared_auto = clear_auto_assignment(conn, scope_key, item, items, linked_project_key=project_key)
            changed = previous != web_url
            rollback_previous = previous
            if not changed:
                others = other_review_items(conn, scope_key, items, target_id)
                inherited = next((other for other in others if other.get("status") == "linked"
                                  and other.get("project_key") == project_key and other.get("post_web_url") == web_url
                                  and other.get("link_changed")), None)
                if inherited:
                    changed = True
                    rollback_previous = str(inherited.get("previous_introduced_url") or "")
                elif item.get("auto_set_project_key") != project_key and any(
                    other.get("auto_set_project_key") == project_key and other.get("post_web_url") == web_url
                    and other.get("status") in {"open", "auto_applied"} for other in others
                ):
                    changed = True
                    rollback_previous = ""
            if previous != web_url:
                tracking.set_introduced_url(project, web_url)
                project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True, commit=False)
            item.update(status="linked", reason="", project_key=project_key,
                        link_changed=changed, previous_introduced_url=rollback_previous,
                        previous_reason=str(item.get("reason") or ""),
                        cleared_auto_project_key=cleared_auto, reviewed_at=project_record.iso_now())
        elif action == "ignore":
            if item.get("status") != "open":
                raise ValueError("only an open item can be ignored")
            cleared_auto = clear_auto_assignment(conn, scope_key, item, items)
            item.update(status="ignored", cleared_auto_project_key=cleared_auto, reviewed_at=project_record.iso_now())
        elif action == "reopen":
            if item.get("status") not in MANUAL_STATUSES:
                raise ValueError("only a manual decision can be reopened")
            if item.get("status") == "linked" and item.get("link_changed"):
                project = project_record.fetch_project(conn, str(item.get("project_key") or ""))
                others = other_review_items(conn, scope_key, items, target_id)
                supporting = [other for other in others if supports_introduced_url(
                    other, str(item.get("project_key") or ""), str(item.get("post_web_url") or ""))]
                if project and project.get("introduced_url") == item.get("post_web_url"):
                    if not supporting:
                        tracking.set_introduced_url(project, str(item.get("previous_introduced_url") or ""))
                        project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True, commit=False)
                    elif not any(other.get("status") in {"open", "auto_applied"} or other.get("link_changed")
                                 for other in supporting):
                        linked = next((other for other in supporting if other.get("status") == "linked"), None)
                        if linked:
                            transfer_rollback(conn, scope_key, linked,
                                              str(item.get("previous_introduced_url") or ""))
            auto_key = str(item.get("cleared_auto_project_key") or "")
            if auto_key:
                project = project_record.fetch_project(conn, auto_key)
                if project and not project.get("introduced_url"):
                    tracking.set_introduced_url(project, str(item.get("post_web_url") or ""))
                    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True, commit=False)
            item.update(status="open", reason=str(item.get("previous_reason") or item.get("reason") or "reopened"),
                        reviewed_at="", project_key="", link_changed=False, cleared_auto_project_key="")
        elif action == "review_target":
            if item.get("status") not in {"open", "reference"} or link_identity(str(item.get("url") or ""))[0] != "reference":
                raise ValueError("only a reference link can be reviewed as a target")
            item.update(status="open", reason="reference_target", review_as_target=True)
        else:
            if item.get("status") != "open":
                raise ValueError("reopen the manual decision before rechecking")
            item.update(refresh_item(conn, item))
            if item.get("link_role") == "reference" and not item.get("review_as_target"):
                item.update(status="reference", reason="reference_link")
            else:
                item["reason"] = reason_for_item(conn, item, item["matched_project_keys"], allow_apply=True)
            if not item["reason"]:
                project = project_record.fetch_project(conn, item["resolved_project_key"])
                if project and not project.get("introduced_url") and item.get("post_web_url"):
                    tracking.set_introduced_url(project, item["post_web_url"])
                    project["discovery_sources"] = project_record.add_unique(project.get("discovery_sources", []), "bsky_introduced_sync")
                    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True, commit=False)
                    item["auto_set_project_key"] = project["project_key"]
                item["status"] = "auto_applied"
        watermark["review_items"] = items
        watermark.pop("pending_entries", None)
        save_state(conn, state, watermark, scan_complete=False)
        conn.commit()
        return {"item": item}
    except Exception:
        conn.rollback()
        raise
