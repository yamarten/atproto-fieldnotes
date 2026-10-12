"""Persistent review of posts seen by introduced sync.

The review ledger lives beside the introduced watermark so existing schema-v4
databases can use it without a project-record migration.  One item represents
one URL in one post (or a post with no URL).
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import urllib.parse
import uuid
from typing import Any

from .identity import identifiers
from . import project_record
from project_registry_core.registry import applog_seed_from_url
from project_registry_core.transport.atproto import bsky_post_web_url, extract_post_link_entries


TEXT_URL = re.compile(r"https?://[^\s<>\[\]{}\"']+", re.IGNORECASE)
MANUAL_STATUSES = {"ignored", "linked"}


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
    return [key for _, key in project_record.indexed_identifier_matches(conn, *seed)]


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
    if item.get("url_source") == "text":
        return "text_url_not_extracted"
    seed = (str(item.get("seed_type") or ""), str(item.get("seed_value") or ""))
    reason = _reason(seed if all(seed) else None, matches, no_url=not item.get("url"))
    if reason or not matches:
        return reason
    web_url = str(item.get("post_web_url") or "")
    if not web_url:
        return "post_url_unavailable"
    project = project_record.fetch_project(conn, matches[0])
    if project and project.get("introduced_url") and project["introduced_url"] != web_url:
        return "different_introduced_url"
    if project and not project.get("introduced_url") and not allow_apply:
        return "not_applied"
    return ""


def observe_posts(conn: sqlite3.Connection, posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            seed = applog_seed_from_url(url) if url else None
            matches = _matches(conn, seed)
            item = {
                "id": item_id(uri, url), "post_uri": uri, "post_web_url": web_url,
                "post_text": text, "indexed_at": str(post.get("indexedAt") or record.get("createdAt") or ""),
                "post_line": target_post_line(text, url, record.get("facets", [])),
                "url": url, "url_source": entry["source"], "label": str(entry.get("label") or ""),
                "seed_type": seed[0] if seed else "", "seed_value": seed[1] if seed else "",
                "matched_project_keys": matches,
            }
            reason = reason_for_item(conn, item, matches)
            output.append({**item, "reason": reason, "status": "open" if reason else "auto_applied"})
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
        if previous and previous.get("status") in MANUAL_STATUSES:
            # A repeated search result never revives an operator decision.
            by_id[item["id"]] = {**item, **{
                key: previous[key] for key in ("status", "reason", "project_key", "reviewed_at", "link_changed",
                                               "previous_introduced_url", "previous_reason", "auto_set_project_key",
                                               "cleared_auto_project_key")
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


def review_payload(conn: sqlite3.Connection, *, include_resolved: bool = False, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    rows = conn.execute("SELECT scope_key, scope_json, watermark_json, last_success_at FROM sync_state WHERE kind='introduced'").fetchall()
    items: list[dict[str, Any]] = []
    scopes: list[dict[str, Any]] = []
    for row in rows:
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
                items.append({**item, "scope_key": row["scope_key"], "query": scope.get("query", "")})
        for item in legacy_pending_items(watermark.get("pending_entries", [])):
            if not any(other["id"] == item["id"] for other in items):
                items.append({**item, "scope_key": row["scope_key"], "query": scope.get("query", "")})
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
                item["matched_projects"].append(preview)
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
    return others


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
    if item.get("status") == "auto_applied" and project_key in item.get("matched_project_keys", []):
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
    project["introduced_url"] = ""
    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True, commit=False)
    return key


def resolve_item(conn: sqlite3.Connection, *, scope_key: str, target_id: str, action: str,
                 project_key: str = "", replace: bool = False) -> dict[str, Any]:
    if action not in {"ignore", "link", "reopen", "recheck"}:
        raise ValueError("invalid introduced review action")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT scope_json,watermark_json,last_success_at FROM sync_state WHERE scope_key=? AND kind='introduced'", (scope_key,)).fetchone()
        if row is None:
            raise KeyError("introduced review scope not found")
        state = {"scope_key": scope_key, "scope": json.loads(row["scope_json"]), "last_success_at": row["last_success_at"]}
        watermark = json.loads(row["watermark_json"])
        items = merge_items(watermark.get("review_items", []), legacy_pending_items(watermark.get("pending_entries", [])))
        item = next((entry for entry in items if entry["id"] == target_id), None)
        if item is None:
            raise KeyError("introduced review item not found")
        if action == "link":
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
                project["introduced_url"] = web_url
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
                        project["introduced_url"] = str(item.get("previous_introduced_url") or "")
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
                    project["introduced_url"] = str(item.get("post_web_url") or "")
                    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True, commit=False)
            item.update(status="open", reason=str(item.get("previous_reason") or item.get("reason") or "reopened"),
                        reviewed_at="", project_key="", link_changed=False, cleared_auto_project_key="")
        else:
            if item.get("status") != "open":
                raise ValueError("reopen the manual decision before rechecking")
            seed = (str(item.get("seed_type") or ""), str(item.get("seed_value") or ""))
            matches = _matches(conn, seed if all(seed) else None)
            item["matched_project_keys"] = matches
            item["reason"] = reason_for_item(conn, item, matches, allow_apply=True)
            if not item["reason"]:
                project = project_record.fetch_project(conn, matches[0])
                if project and not project.get("introduced_url") and item.get("post_web_url"):
                    project["introduced_url"] = item["post_web_url"]
                    project["discovery_sources"] = project_record.add_unique(project.get("discovery_sources", []), "bsky_introduced_sync")
                    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True, commit=False)
                item["status"] = "auto_applied"
        watermark["review_items"] = items
        watermark.pop("pending_entries", None)
        save_state(conn, state, watermark, scan_complete=False)
        conn.commit()
        return {"item": item}
    except Exception:
        conn.rollback()
        raise
