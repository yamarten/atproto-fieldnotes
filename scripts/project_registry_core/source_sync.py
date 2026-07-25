#!/usr/bin/env python3

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Sequence

from project_registry_core import EvidenceBundle
from project_registry_core import investigation as investigation_core
from project_registry_core import tangled as tangled_core
from project_registry_core import domain_policy as domain_policy_core
from project_registry_core import http_transport
from project_registry_core import identifiers as identifier_core
from project_registry_core import repo_urls as repo_url_core
from project_registry_core import repository_material as repository_material_core
from project_registry_core import repository_sources as repository_source_core

from project_registry_core.registry import (
    GITHUB_API,
    REQUEST_TIMEOUT_LEXICON_HTTP,
    SEMBLE_API,
    SEMBLE_TRACKED_COLLECTIONS,
    UFOS_API,
    activity_is_recent,
    add_candidate_text,
    add_candidate_url,
    add_manual_seed,
    all_projects,
    apply_tangled_evidence,
    build_collection_project_matchers,
    classify_text_seed_value,
    clean_string_list,
    collect_applog_entries,
    compute_duplicate_candidates,
    compute_merge_recommendation,
    confirmed_developer_dids,
    enrich_from_github_repo,
    ensure_project_for_seed,
    fetch_bsky_profile,
    finalize_project_shape,
    find_project_key_by_collection_namespace,
    github_headers,
    import_collection_inventory_payload,
    load_config,
    parse_iso_datetime,
    read_sync_state,
    registrable_namespace_from_namespace,
    resolve_semble_project_key_for_seed_value,
    search_bsky_posts_payload,
    sync_match_is_auto_attach_safe,
    sync_watermark_datetime,
    upsert_collection_observation,
    write_sync_state,
)
from project_registry_core.project_record import (
    add_unique,
    default_project_record,
    ensure_project_shape,
    fetch_project,
    find_project_key_by_identifier,
    generate_project_key,
    iso_now,
    list_projects,
    namespace_from_collection_nsid,
    normalize_service_candidate_url,
    parse_iso,
    save_project,
    service_url_candidate_values,
    source_code_url_values,
    unique_list,
)
from project_registry_core.registry import import_text_seed_list as _import_text_seed_list


def import_seed_list(conn: sqlite3.Connection, path: str, *, execute: bool = False) -> dict[str, Any]:
    return _import_text_seed_list(conn, path, execute=execute)


def fetch_collection_inventory(source_url: str) -> list[dict[str, Any]]:
    payload = http_transport.request_json(source_url)
    if not isinstance(payload, list):
        raise ValueError("collection inventory response must be a JSON array")
    output: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            output.append(dict(item))
    return output


def known_collection_nsids(projects: list[dict[str, Any]]) -> set[str]:
    known: set[str] = set()
    for project in projects:
        for value in list(project.get("input_nsids", [])) + list(project.get("resolved_nsids", [])) + list(project.get("lexicons", [])):
            normalized = identifier_core.normalize_nsid(str(value or ""))
            if normalized:
                known.add(normalized)
        for seed in project.get("manual_seeds", []):
            if str(seed.get("type") or "") != "nsid":
                continue
            normalized = identifier_core.normalize_nsid(str(seed.get("value") or ""))
            if normalized:
                known.add(normalized)
        for observation in project.get("collection_observations", []):
            if not isinstance(observation, dict):
                continue
            for member in observation.get("members", []):
                normalized = identifier_core.normalize_nsid(str(member or ""))
                if normalized:
                    known.add(normalized)
    return known


def filter_sync_collection_inventory_payload(
    payload: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    *,
    lookback_days: int,
    watermark: dt.datetime | None = None,
    all_history: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cutoff = None if all_history else (watermark or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(0, int(lookback_days)))))
    known_nsids = known_collection_nsids(projects)
    filtered: list[dict[str, Any]] = []
    skipped_known = 0
    skipped_old = 0
    skipped_invalid_min = 0
    skipped_invalid_collection = 0

    for item in payload:
        collection_nsid = identifier_core.normalize_nsid(str(item.get("collection") or ""))
        if not collection_nsid:
            skipped_invalid_collection += 1
            continue
        if collection_nsid in known_nsids:
            skipped_known += 1
            continue
        activity_at = parse_iso(str(item.get("max") or item.get("min") or ""))
        if activity_at is None:
            skipped_invalid_min += 1
            continue
        if cutoff is not None and activity_at < cutoff:
            skipped_old += 1
            continue
        filtered.append(item)

    return filtered, {
        "lookback_days": max(0, int(lookback_days)),
        "cutoff_min": cutoff.replace(microsecond=0).isoformat() if cutoff is not None else "",
        "cutoff_source": "all_history" if cutoff is None else ("watermark" if watermark is not None else "lookback"),
        "fetched_entries": len(payload),
        "eligible_entries": len(filtered),
        "skipped_known": skipped_known,
        "skipped_old": skipped_old,
        "skipped_invalid_min": skipped_invalid_min,
        "skipped_invalid_collection": skipped_invalid_collection,
    }


def refresh_known_collection_activity(
    conn: sqlite3.Connection,
    payload: list[dict[str, Any]],
    *,
    execute: bool,
    source: str,
    inactivity_days: int,
) -> dict[str, Any]:
    existing_projects = list_projects(conn)
    namespace_lookup, _, _ = build_collection_project_matchers(existing_projects)
    projects_by_key = {
        str(project.get("project_key") or ""): ensure_project_shape(project)
        for project in existing_projects
        if str(project.get("project_key") or "")
    }
    aggregated: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        collection_nsid = identifier_core.normalize_nsid(str(item.get("collection") or ""))
        namespace = namespace_from_collection_nsid(collection_nsid)
        if not namespace:
            continue
        registrable_namespace = registrable_namespace_from_namespace(namespace) or namespace
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

    refreshed_project_keys: list[str] = []
    reactivated_project_keys: list[str] = []
    changed_namespaces = 0
    now = dt.datetime.now(dt.timezone.utc)
    for namespace, item in aggregated.items():
        project_key = namespace_lookup.get(namespace) or find_project_key_by_collection_namespace(existing_projects, namespace)
        if not project_key:
            continue
        project = projects_by_key.get(project_key)
        if not project:
            continue
        previous = None
        for observation in project.get("collection_observations", []):
            if isinstance(observation, dict) and identifier_core.normalize_nsid(str(observation.get("collection") or "")) == namespace:
                previous = dict(observation)
                break
        current_signature = (
            int(item.get("count") or 0),
            int(item.get("recent_count") or 0),
            str(item.get("min") or ""),
            str(item.get("max") or ""),
            tuple(sorted(item.get("members", []))),
        )
        previous_signature = None
        if previous is not None:
            previous_signature = (
                int(previous.get("count") or 0),
                int(previous.get("recent_count") or 0),
                str(previous.get("min") or ""),
                str(previous.get("max") or ""),
                tuple(sorted(previous.get("members", []))) if isinstance(previous.get("members", []), list) else (),
            )
        if current_signature == previous_signature:
            continue
        changed_namespaces += 1
        if execute:
            upsert_collection_observation(project, item, source=source, merge_existing=False)
            if str(project.get("tracking_state") or "active") == "stalled":
                previous_max = parse_iso_datetime(str(previous.get("max") or "")) if previous else None
                current_max = parse_iso_datetime(str(item.get("max") or ""))
                recent_signal = int(item.get("recent_count") or 0) > 0
                newer_signal = bool(current_max and (previous_max is None or current_max > previous_max))
                still_recent = activity_is_recent(current_max, now, inactivity_days)
                if recent_signal or newer_signal or still_recent:
                    project["tracking_state"] = "active"
                    reactivated_project_keys.append(project["project_key"])
            save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
            projects_by_key[project_key] = project
        refreshed_project_keys.append(project_key)

    if execute and (refreshed_project_keys or reactivated_project_keys):
        conn.commit()

    return {
        "activity_refreshed_namespace_count": changed_namespaces,
        "activity_refreshed_project_count": len(set(refreshed_project_keys)),
        "reactivated_project_count": len(set(reactivated_project_keys)),
        "reactivated_project_keys": sorted(set(reactivated_project_keys))[:100],
    }


def sync_collection_inventory(
    conn: sqlite3.Connection,
    *,
    source_url: str,
    execute: bool,
    create_unmatched: bool,
    lookback_days: int,
    save_path: Path | None = None,
    incremental: bool = True,
    all_history: bool = False,
    max_items: int | None = None,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    scope = {"source_url": identifier_core.normalize_url(source_url)}
    state = read_sync_state(conn, "collections", scope)
    stored_watermark = sync_watermark_datetime(state) if incremental and not all_history else None
    payload = fetch_collection_inventory(source_url)
    processing_limit = max(1, int(max_items)) if max_items is not None else None
    processing_payload = payload[:processing_limit] if processing_limit is not None else payload
    truncated = len(processing_payload) < len(payload)
    if progress:
        progress(1, 2, {"kind": "source_page", "source": "collections", "entry_count": len(payload)})
    if cancel_requested and cancel_requested():
        return {
            "source": f"collection_inventory_url:{source_url}",
            "source_url": source_url,
            "mode": "execute" if execute else "dry_run",
            "cancelled": True,
            "entries": 0,
            "state_updated": False,
        }
    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    projects = all_projects(conn)
    filtered_payload, filter_stats = filter_sync_collection_inventory_payload(
        processing_payload,
        projects,
        lookback_days=lookback_days,
        watermark=None if all_history else stored_watermark,
        all_history=all_history,
    )
    filter_stats["fetched_entries"] = len(payload)
    filter_stats["processed_entries"] = len(processing_payload)
    filter_stats["truncated"] = truncated
    payload_timestamps = [
        parsed
        for item in processing_payload
        if (parsed := parse_iso_datetime(str(item.get("max") or item.get("min") or ""))) is not None
    ]
    newest_timestamp = max(payload_timestamps) if payload_timestamps else None
    activity_refresh = refresh_known_collection_activity(
        conn,
        processing_payload,
        execute=execute,
        source=f"collection_inventory_url:{source_url}",
        inactivity_days=lookback_days,
    )
    if not filtered_payload:
        state_updated = bool(execute and incremental and newest_timestamp is not None and not truncated)
        if state_updated:
            write_sync_state(conn, "collections", scope, {"timestamp": newest_timestamp.isoformat()})
            conn.commit()
        return {
            "source": f"collection_inventory_url:{source_url}",
            "source_url": source_url,
            "mode": "execute" if execute else "dry_run",
            "create_unmatched": create_unmatched,
            "entries": 0,
            "namespaces": 0,
            "created": 0,
            "updated": 0,
            "unmatched": 0,
            "rejected": 0,
            "imported_project_keys": [],
            "merge_candidates": [],
            "unmatched_entries": [],
            "rejected_entries": [],
            **filter_stats,
            **activity_refresh,
            "incremental": incremental and not all_history,
            "state_updated": state_updated,
            "watermark_before": stored_watermark.isoformat() if stored_watermark else "",
            "watermark_after": newest_timestamp.isoformat() if state_updated and newest_timestamp else "",
            **({"saved_payload_path": str(save_path)} if save_path is not None else {}),
        }
    summary = import_collection_inventory_payload(
        conn,
        filtered_payload,
        execute=execute,
        create_unmatched=create_unmatched,
        source=f"collection_inventory_url:{source_url}",
        merge_existing_observations=True,
    )
    if progress:
        progress(2, 2, {"kind": "source_items", "source": "collections", "entry_count": len(filtered_payload)})
    summary.update(filter_stats)
    summary.update(activity_refresh)
    state_updated = bool(
        execute
        and incremental
        and newest_timestamp is not None
        and not truncated
        and (create_unmatched or int(summary.get("unmatched") or 0) == 0)
    )
    if state_updated:
        write_sync_state(conn, "collections", scope, {"timestamp": newest_timestamp.isoformat()})
        conn.commit()
    summary["incremental"] = incremental and not all_history
    summary["state_updated"] = state_updated
    summary["watermark_before"] = stored_watermark.isoformat() if stored_watermark else ""
    summary["watermark_after"] = newest_timestamp.isoformat() if state_updated and newest_timestamp else ""
    summary["source_url"] = source_url
    if save_path is not None:
        summary["saved_payload_path"] = str(save_path)
    return summary


def sync_introduced_entries(
    conn: sqlite3.Connection,
    *,
    query: str,
    execute: bool,
    limit: int = 10,
    max_pages: int = 100,
    lookback_days: int = 7,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    scope = {"query": str(query).strip()}
    state = read_sync_state(conn, "introduced", scope)
    saved_watermark = sync_watermark_datetime(state)
    stored_watermark = saved_watermark if incremental and not all_history else None
    stored_pending = state.get("watermark", {}).get("pending_entries", []) if isinstance(state.get("watermark", {}), dict) else []
    if not isinstance(stored_pending, list):
        stored_pending = []
    fallback_cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(0, int(lookback_days)))
    cutoff = None if all_history else (stored_watermark or fallback_cutoff)
    posts: list[dict[str, Any]] = []
    cursor = ""
    fetched_posts = 0
    pages = 0
    boundary_reached = False
    exhausted = False
    cancelled = False
    newest_timestamp: dt.datetime | None = None
    while pages < max(1, max_pages):
        if cancel_requested and cancel_requested():
            cancelled = True
            break
        payload = search_bsky_posts_payload(
            query,
            limit=min(100, max(1, limit)),
            cursor=cursor,
            since=cutoff.isoformat() if cutoff is not None else "",
        )
        page_posts = [item for item in payload.get("posts", []) if isinstance(item, dict)]
        if not page_posts:
            exhausted = True
            break
        fetched_posts += len(page_posts)
        pages += 1
        if progress:
            progress(pages, max(1, max_pages), {"kind": "source_page", "source": "introduced", "item_count": len(page_posts)})
        for post in page_posts:
            record = post.get("record", {}) if isinstance(post.get("record"), dict) else {}
            timestamp = parse_iso_datetime(str(post.get("indexedAt") or record.get("createdAt") or ""))
            if timestamp and (newest_timestamp is None or timestamp > newest_timestamp):
                newest_timestamp = timestamp
            if cutoff is not None and timestamp is not None and timestamp < cutoff:
                boundary_reached = True
                continue
            posts.append(post)
        if boundary_reached:
            break
        next_cursor = str(payload.get("cursor") or "").strip()
        if not next_cursor or next_cursor == cursor:
            exhausted = True
            break
        cursor = next_cursor

    scan_complete = boundary_reached or exhausted

    entries_by_seed: dict[tuple[str, str], dict[str, Any]] = {}
    if incremental:
        for item in stored_pending:
            if not isinstance(item, dict):
                continue
            seed_type = str(item.get("seed_type") or "").strip()
            seed_value = str(item.get("seed_value") or "").strip()
            if not seed_type or not seed_value:
                continue
            entries_by_seed[(seed_type, seed_value)] = {
                "seed_type": seed_type,
                "seed_value": seed_value,
                "labels": clean_string_list(item.get("labels", []), limit=20),
                "post_web_urls": clean_string_list(item.get("post_web_urls", []), limit=20),
            }
    for key, item in collect_applog_entries(posts).items():
        bucket = entries_by_seed.setdefault(
            key,
            {"seed_type": item["seed_type"], "seed_value": item["seed_value"], "labels": [], "post_web_urls": []},
        )
        bucket["labels"] = unique_list([*bucket.get("labels", []), *item.get("labels", [])])
        bucket["post_web_urls"] = unique_list([*bucket.get("post_web_urls", []), *item.get("post_web_urls", [])])
    updated = 0
    already_introduced = 0
    unmatched = 0
    imported_project_keys: list[str] = []
    matched_project_keys: list[str] = []
    unmatched_entries: list[dict[str, Any]] = []
    pending_entries: list[dict[str, Any]] = []

    for item in entries_by_seed.values():
        seed_type = str(item["seed_type"])
        seed_value = str(item["seed_value"])
        project_key = find_project_key_by_identifier(conn, seed_type, seed_value)
        if project_key is None:
            unmatched += 1
            pending_entries.append(
                {
                    "seed_type": seed_type,
                    "seed_value": seed_value,
                    "labels": clean_string_list(item.get("labels", []), limit=20),
                    "post_web_urls": clean_string_list(item.get("post_web_urls", []), limit=20),
                }
            )
            unmatched_entries.append(
                {
                    "seed_type": seed_type,
                    "seed_value": seed_value,
                    "label": item["labels"][0] if item["labels"] else "",
                    "introduced_url": item["post_web_urls"][0] if item["post_web_urls"] else "",
                }
            )
            continue
        project = fetch_project(conn, project_key)
        if project is None:
            unmatched += 1
            continue
        was_introduced = bool(project.get("introduced_url"))
        if was_introduced:
            already_introduced += 1
        project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "bsky_introduced_sync")
        if not project.get("introduced_url") and item["post_web_urls"]:
            project["introduced_url"] = item["post_web_urls"][0]
        project = finalize_project_shape(project)
        project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
        project["merge_recommendation"] = compute_merge_recommendation(project)
        matched_project_keys.append(project["project_key"])
        is_introduced_now = bool(project.get("introduced_url"))
        if not was_introduced and is_introduced_now:
            imported_project_keys.append(project["project_key"])
        if execute:
            save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
        updated += 1
    if execute:
        if incremental and scan_complete and newest_timestamp is not None:
            write_sync_state(
                conn,
                "introduced",
                scope,
                {
                    "timestamp": newest_timestamp.isoformat(),
                    "pending_entries": pending_entries,
                },
            )
        elif incremental and scan_complete and saved_watermark is not None:
            write_sync_state(
                conn,
                "introduced",
                scope,
                {
                    "timestamp": saved_watermark.isoformat(),
                    "pending_entries": pending_entries,
                },
            )
        conn.commit()

    state_updated = bool(execute and incremental and scan_complete and (newest_timestamp or saved_watermark))

    return {
        "query": query,
        "posts": len(posts),
        "fetched_posts": fetched_posts,
        "pages": pages,
        "scan_complete": scan_complete,
        "boundary_reached": boundary_reached,
        "incremental": incremental and not all_history,
        "state_updated": state_updated,
        "watermark_before": saved_watermark.isoformat() if saved_watermark else "",
        "watermark_after": (
            newest_timestamp.isoformat()
            if state_updated and newest_timestamp
            else (saved_watermark.isoformat() if state_updated and saved_watermark else "")
        ),
        "fallback_lookback_days": lookback_days,
        "pending_before": len(stored_pending) if incremental else 0,
        "pending_after": len(pending_entries),
        "entries": len(entries_by_seed),
        "updated": updated,
        "already_introduced": already_introduced,
        "unmatched": unmatched,
        "mode": "execute" if execute else "dry_run",
        "imported_project_keys": imported_project_keys,
        "matched_project_keys": matched_project_keys,
        "unmatched_entries": unmatched_entries[:100],
        "cancelled": cancelled,
    }


def github_search_query_with_defaults(query: str) -> str:
    cleaned = str(query or "").strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    qualifiers: list[str] = []
    if "fork:" not in lowered:
        qualifiers.append("fork:false")
    if "archived:" not in lowered:
        qualifiers.append("archived:false")
    return " ".join([cleaned, *qualifiers]).strip()


def default_github_search_pages(per_page: int) -> int:
    normalized_per_page = max(1, min(int(per_page or 50), 100))
    return max(1, (1000 + normalized_per_page - 1) // normalized_per_page)


def fetch_github_search_query_sync_results(
    raw_query: str,
    *,
    pages: int,
    per_page: int,
    cutoff: dt.datetime | None,
) -> dict[str, Any]:
    headers = github_headers()
    deduped: dict[str, dict[str, Any]] = {}
    clamped_per_page = max(1, min(per_page, 100))
    query = github_search_query_with_defaults(raw_query)
    if cutoff is not None and "updated:" not in query.lower():
        query = f"{query} updated:>={cutoff.date().isoformat()}".strip()
    reached_cutoff = False
    exhausted = False
    fetched_pages = 0
    newest_timestamp: dt.datetime | None = None
    if query:
        for page in range(1, max(1, pages) + 1):
            params = urllib.parse.urlencode(
                {
                    "q": query,
                    "type": "Repositories",
                    "sort": "updated",
                    "order": "desc",
                    "per_page": str(clamped_per_page),
                    "page": str(page),
                }
            )
            payload = http_transport.request_json(f"{GITHUB_API}/search/repositories?{params}", headers=headers)
            items = payload.get("items", [])
            fetched_pages += 1
            for raw in items:
                html_url = str(raw.get("html_url") or "")
                normalized_repo = repo_url_core.normalize_github_repo_url(html_url)
                if not normalized_repo:
                    continue
                updated_at = str(raw.get("updated_at") or "")
                updated_at_dt = parse_iso(updated_at)
                if updated_at_dt and (newest_timestamp is None or updated_at_dt > newest_timestamp):
                    newest_timestamp = updated_at_dt
                if cutoff is not None and updated_at_dt and updated_at_dt < cutoff:
                    reached_cutoff = True
                    break
                entry = {
                    "url": normalized_repo,
                    "full_name": str(raw.get("full_name") or ""),
                    "description": str(raw.get("description") or ""),
                    "homepage": str(raw.get("homepage") or ""),
                    "updated_at": updated_at,
                    "stargazers_count": int(raw.get("stargazers_count") or 0),
                    "query": raw_query,
                }
                existing = deduped.get(normalized_repo)
                if existing is None or str(entry.get("updated_at") or "") > str(existing.get("updated_at") or ""):
                    deduped[normalized_repo] = entry
            if reached_cutoff:
                break
            if len(items) < clamped_per_page:
                exhausted = True
                break
    results = list(deduped.values())
    results.sort(key=lambda item: (str(item.get("updated_at") or ""), int(item.get("stargazers_count") or 0)), reverse=True)
    return {
        "query": raw_query,
        "results": results,
        "pages": fetched_pages,
        "boundary_reached": reached_cutoff,
        "scan_complete": reached_cutoff or exhausted,
        "newest_timestamp": newest_timestamp.isoformat() if newest_timestamp else "",
    }


def fetch_github_search_sync_results(
    queries: list[str],
    *,
    pages: int,
    per_page: int,
    lookback_days: int | None = None,
) -> list[dict[str, Any]]:
    cutoff = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(0, int(lookback_days)))
        if lookback_days is not None else None
    )
    deduped: dict[str, dict[str, Any]] = {}
    for query in queries:
        scan = fetch_github_search_query_sync_results(query, pages=pages, per_page=per_page, cutoff=cutoff)
        for entry in scan["results"]:
            repo_url = str(entry.get("url") or "")
            existing = deduped.get(repo_url)
            if existing is None or str(entry.get("updated_at") or "") > str(existing.get("updated_at") or ""):
                deduped[repo_url] = entry
    results = list(deduped.values())
    results.sort(key=lambda item: (str(item.get("updated_at") or ""), int(item.get("stargazers_count") or 0)), reverse=True)
    return results


def fetch_semble_collection_source_sync_results(
    source: dict[str, Any],
    *,
    pages: int,
    per_page: int,
    cutoff: dt.datetime | None,
) -> dict[str, Any]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    clamped_per_page = max(1, min(per_page, 100))
    handle = str(source.get("handle") or "").strip()
    rkey = str(source.get("rkey") or "").strip()
    label = str(source.get("label") or rkey).strip()
    api_url = str(source.get("api_url") or SEMBLE_API).rstrip("/")
    reached_cutoff = False
    exhausted = False
    fetched_pages = 0
    fetched_cards = 0
    skipped_cards = 0
    ordered_desc = False
    newest_timestamp: dt.datetime | None = None
    evidence_only: dict[tuple[str, str], dict[str, Any]] = {}
    if handle and rkey:
        for page in range(1, max(1, pages) + 1):
            params = urllib.parse.urlencode(
                {
                    "handle": handle,
                    "recordKey": rkey,
                    "page": str(page),
                    "limit": str(clamped_per_page),
                }
            )
            payload = http_transport.request_json(
                f"{api_url}/xrpc/network.cosmik.collection.getByAtUri?{params}"
            )
            cards = payload.get("urlCards", [])
            if not isinstance(cards, list):
                cards = []
            fetched_pages += 1
            fetched_cards += len(cards)
            sorting = payload.get("sorting", {}) if isinstance(payload.get("sorting"), dict) else {}
            page_ordered_desc = (
                str(sorting.get("sortBy") or "").lower() == "updatedat"
                and str(sorting.get("sortOrder") or "").lower() == "desc"
            )
            ordered_desc = page_ordered_desc if fetched_pages == 1 else ordered_desc and page_ordered_desc
            collection_label = str(payload.get("name") or label).strip()
            for raw in cards:
                if not isinstance(raw, dict):
                    continue
                updated_at = parse_iso_datetime(str(raw.get("updatedAt") or raw.get("createdAt") or ""))
                if updated_at and (newest_timestamp is None or updated_at > newest_timestamp):
                    newest_timestamp = updated_at
                if cutoff is not None and updated_at is not None and updated_at < cutoff:
                    if ordered_desc:
                        reached_cutoff = True
                        break
                    continue
                url = str(raw.get("url") or "").strip()
                classified = classify_semble_card_seed_value(url)
                if classified.get("status") == "evidence_only":
                    evidence_url = str(classified.get("evidence_url") or "")
                    if not evidence_url:
                        skipped_cards += 1
                        continue
                    entry = {
                        "raw_url": url,
                        "evidence_url": evidence_url,
                        "root_service_url": str(classified.get("root_service_url") or ""),
                        "reason": str(classified.get("reason") or "evidence_only"),
                        "card_uri": str(raw.get("uri") or ""),
                        "card_updated_at": str(raw.get("updatedAt") or ""),
                        "card_created_at": str(raw.get("createdAt") or ""),
                        "title": str(((raw.get("cardContent") or {}) if isinstance(raw.get("cardContent"), dict) else {}).get("title") or ""),
                        "description": str(((raw.get("cardContent") or {}) if isinstance(raw.get("cardContent"), dict) else {}).get("description") or ""),
                        "site_name": str(((raw.get("cardContent") or {}) if isinstance(raw.get("cardContent"), dict) else {}).get("siteName") or ""),
                        "collection_handle": handle,
                        "collection_rkey": rkey,
                        "collection_label": collection_label,
                        "collection_uri": str(payload.get("uri") or f"at://{handle}/collections/{rkey}"),
                    }
                    key = (evidence_url, str(entry.get("card_uri") or ""))
                    existing = evidence_only.get(key)
                    if existing is None or str(entry.get("card_updated_at") or "") > str(existing.get("card_updated_at") or ""):
                        evidence_only[key] = entry
                    continue
                if classified.get("status") != "seed":
                    skipped_cards += 1
                    continue
                seed_type = str(classified.get("seed_type") or "")
                seed_value = str(classified.get("seed_value") or "")
                if not seed_type or not seed_value:
                    continue
                key = (seed_type, seed_value)
                entry = {
                    "seed_type": seed_type,
                    "seed_value": seed_value,
                    "raw_url": url,
                    "card_uri": str(raw.get("uri") or ""),
                    "card_updated_at": str(raw.get("updatedAt") or ""),
                    "card_created_at": str(raw.get("createdAt") or ""),
                    "title": str(((raw.get("cardContent") or {}) if isinstance(raw.get("cardContent"), dict) else {}).get("title") or ""),
                    "description": str(((raw.get("cardContent") or {}) if isinstance(raw.get("cardContent"), dict) else {}).get("description") or ""),
                    "site_name": str(((raw.get("cardContent") or {}) if isinstance(raw.get("cardContent"), dict) else {}).get("siteName") or ""),
                    "collection_handle": handle,
                    "collection_rkey": rkey,
                    "collection_label": collection_label,
                    "collection_uri": str(payload.get("uri") or f"at://{handle}/collections/{rkey}"),
                }
                existing = deduped.get(key)
                if existing is None or str(entry.get("card_updated_at") or "") > str(existing.get("card_updated_at") or ""):
                    deduped[key] = entry
            pagination = payload.get("pagination", {}) if isinstance(payload.get("pagination"), dict) else {}
            has_more = bool(pagination.get("hasMore"))
            if reached_cutoff:
                break
            if not has_more or len(cards) < clamped_per_page:
                exhausted = True
                break
    results = list(deduped.values())
    results.sort(key=lambda item: (str(item.get("card_updated_at") or ""), str(item.get("card_created_at") or "")), reverse=True)
    evidence_only_results = list(evidence_only.values())
    evidence_only_results.sort(key=lambda item: (str(item.get("card_updated_at") or ""), str(item.get("card_created_at") or "")), reverse=True)
    return {
        "source_key": f"{handle}/{rkey}",
        "results": results,
        "evidence_only": evidence_only_results,
        "pages": fetched_pages,
        "fetched_cards": fetched_cards,
        "skipped_cards": skipped_cards,
        "ordered_desc": ordered_desc,
        "boundary_reached": reached_cutoff,
        "scan_complete": reached_cutoff or exhausted,
        "newest_timestamp": newest_timestamp.isoformat() if newest_timestamp else "",
    }


def root_service_url_for_evidence(url: str) -> str:
    normalized = identifier_core.normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def classify_semble_card_seed_value(raw_value: str) -> dict[str, Any]:
    classified = classify_text_seed_value(raw_value)
    if classified.get("status") != "seed":
        return classified
    seed_type = str(classified.get("seed_type") or "")
    seed_value = str(classified.get("seed_value") or "")
    if seed_type != "service_url":
        return classified
    normalized = identifier_core.normalize_url(seed_value)
    parsed = urllib.parse.urlparse(normalized)
    path = parsed.path.strip("/")
    if not path:
        return classified
    return {
        "status": "evidence_only",
        "reason": "semble_service_subpage",
        "seed_type": seed_type,
        "seed_value": seed_value,
        "evidence_url": normalized,
        "root_service_url": root_service_url_for_evidence(normalized),
    }


def fetch_semble_collection_sync_results(*, pages: int, per_page: int) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for source in SEMBLE_TRACKED_COLLECTIONS:
        scan = fetch_semble_collection_source_sync_results(source, pages=pages, per_page=per_page, cutoff=None)
        for entry in scan["results"]:
            key = (str(entry.get("seed_type") or ""), str(entry.get("seed_value") or ""))
            existing = deduped.get(key)
            if existing is None or str(entry.get("card_updated_at") or "") > str(existing.get("card_updated_at") or ""):
                deduped[key] = entry
    results = list(deduped.values())
    results.sort(key=lambda item: (str(item.get("card_updated_at") or ""), str(item.get("card_created_at") or "")), reverse=True)
    return results


ATPROTO_REPO_SCREEN_KEYWORDS = (
    "atproto",
    "at protocol",
    "bluesky",
    "lexicon",
    "xrpc",
    "did:plc",
    "at://",
    "com.atproto",
    "app.bsky",
    "createrecord",
)


def screen_github_repo_sync_candidate(entry: dict[str, Any]) -> dict[str, Any]:
    repo_url = identifier_core.normalize_url(str(entry.get("url") or ""))
    if not repo_url:
        return {"allow": False, "reasons": ["missing_repo_url"]}
    try:
        data = repository_source_core.fetch_github_repo(repo_url)
    except Exception as exc:
        return {"allow": False, "reasons": ["fetch_failed"], "error": str(exc)}

    repo = data.get("repo", {}) if isinstance(data.get("repo", {}), dict) else {}
    readme_text = str(data.get("readme_text") or "")
    repo_tree = [dict(item) for item in data.get("tree", []) if isinstance(item, dict)]
    default_branch = str(data.get("default_branch") or "")
    description = str(repo.get("description") or "")
    homepage = normalize_service_candidate_url(str(repo.get("homepage") or ""))
    lowered_text = "\n".join([description, readme_text]).lower()
    keyword_hits = [keyword for keyword in ATPROTO_REPO_SCREEN_KEYWORDS if keyword in lowered_text]
    repo_lexicons = repository_material_core.repo_lexicon_hints(
        repo_url, ref=default_branch, tree=repo_tree
    )
    lexicon_paths = repository_material_core.github_repo_candidate_lexicon_paths(repo_tree)

    temp_project = default_project_record("proj:screen", "repo_url", repo_url)
    enrich_from_github_repo(temp_project, repo_url)

    reasons: list[str] = []
    if repo_lexicons:
        reasons.append("repo_lexicon_hint")
    if lexicon_paths:
        reasons.append("lexicon_like_paths")
    if temp_project.get("input_nsids"):
        reasons.append("derived_nsid")
    if temp_project.get("lexicon_schema_samples"):
        reasons.append("lexicon_schema_sample")
    if homepage:
        reasons.append("repo_homepage")
    if service_url_candidate_values(temp_project):
        reasons.append("derived_service_candidate")
    if keyword_hits:
        reasons.append("atproto_keywords")

    fork = bool(repo.get("fork"))
    fork_parent = repo.get("parent", {}) if isinstance(repo.get("parent", {}), dict) else {}
    fork_parent_url = repo_url_core.normalize_github_repo_url(str(fork_parent.get("html_url") or ""))
    fork_parent_full_name = str(fork_parent.get("full_name") or "")
    fork_independent_signals = unique_list(
        (["repo_homepage"] if homepage else [])
        + (["owned_lexicon_surface"] if (repo_lexicons or temp_project.get("input_nsids") or temp_project.get("lexicon_schema_samples")) else [])
        + (["derived_service_candidate"] if service_url_candidate_values(temp_project) else [])
    )
    if fork and not fork_independent_signals:
        return {
            "allow": False,
            "reasons": unique_list(["github_fork_without_independent_surface"] + reasons),
            "keyword_hits": keyword_hits[:12],
            "lexicon_ids": [str(item.get("nsid") or "") for item in repo_lexicons if item.get("nsid")][:12],
            "homepage": homepage,
            "service_url_candidates": unique_list(list(temp_project.get("service_url_candidates", [])))[:10],
            "fork": True,
            "fork_parent_url": fork_parent_url,
            "fork_parent_full_name": fork_parent_full_name,
        }
    if fork and fork_independent_signals:
        reasons.append("github_fork_with_independent_surface")

    allow = bool(reasons)
    return {
        "allow": allow,
        "reasons": unique_list(reasons),
        "keyword_hits": keyword_hits[:12],
        "lexicon_ids": [str(item.get("nsid") or "") for item in repo_lexicons if item.get("nsid")][:12],
        "homepage": homepage,
        "service_url_candidates": unique_list(list(temp_project.get("service_url_candidates", [])))[:10],
        "fork": fork,
        "fork_parent_url": fork_parent_url,
        "fork_parent_full_name": fork_parent_full_name,
    }


def resolve_github_search_project_key(
    conn: sqlite3.Connection,
    entry: dict[str, Any],
    *,
    service_host_lookup: dict[str, str],
) -> tuple[str | None, str]:
    repo_url = identifier_core.normalize_url(str(entry.get("url") or ""))
    if repo_url:
        project_key = find_project_key_by_identifier(conn, "repo_url", repo_url)
        if project_key:
            return project_key, "repo_url"

    homepage = normalize_service_candidate_url(str(entry.get("homepage") or ""))
    if homepage:
        project_key = find_project_key_by_identifier(conn, "service_url", homepage)
        if project_key:
            return project_key, "homepage_exact"
        host = urllib.parse.urlparse(homepage).netloc.lower()
        host_family = domain_policy_core.root_domain(host)
        if host_family:
            project_key = service_host_lookup.get(host_family)
            if project_key:
                return project_key, "homepage_family"
    return None, ""


def sync_github_search(
    conn: sqlite3.Connection,
    *,
    queries: list[str],
    execute: bool,
    create_unmatched: bool,
    pages: int,
    per_page: int,
    lookback_days: int | None,
    screen_unmatched: bool = False,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    scope = {"queries": sorted(str(query).strip() for query in queries if str(query).strip())}
    state = read_sync_state(conn, "github-search", scope)
    stored_watermarks = state.get("watermark", {}).get("queries", {}) if isinstance(state.get("watermark", {}), dict) else {}
    if not isinstance(stored_watermarks, dict):
        stored_watermarks = {}
    fallback_cutoff = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(0, int(lookback_days)))
        if lookback_days is not None else None
    )
    deduped_results: dict[str, dict[str, Any]] = {}
    scans: list[dict[str, Any]] = []
    next_watermarks = dict(stored_watermarks)
    cancelled = False
    for query_index, query in enumerate(queries, 1):
        if cancel_requested and cancel_requested():
            cancelled = True
            break
        stored_cutoff = parse_iso_datetime(str(stored_watermarks.get(query) or "")) if incremental and not all_history else None
        cutoff = None if all_history else (stored_cutoff or fallback_cutoff)
        scan = fetch_github_search_query_sync_results(query, pages=pages, per_page=per_page, cutoff=cutoff)
        if progress:
            progress(query_index, len(queries), {"kind": "source_query", "source": "github", "query": query, "item_count": len(scan["results"])})
        scans.append(scan)
        for entry in scan["results"]:
            repo_url = str(entry.get("url") or "")
            existing = deduped_results.get(repo_url)
            if existing is None or str(entry.get("updated_at") or "") > str(existing.get("updated_at") or ""):
                deduped_results[repo_url] = entry
        if scan.get("scan_complete") and scan.get("newest_timestamp"):
            next_watermarks[query] = str(scan["newest_timestamp"])
    results = list(deduped_results.values())
    results.sort(key=lambda item: (str(item.get("updated_at") or ""), int(item.get("stargazers_count") or 0)), reverse=True)
    scan_complete = not cancelled and all(bool(scan.get("scan_complete")) for scan in scans)
    existing_projects = list_projects(conn)
    _, _, service_host_lookup = build_collection_project_matchers(existing_projects)
    known_repo_urls = {
        identifier_core.normalize_url(url)
        for project in existing_projects
        for url in source_code_url_values(project)
        if identifier_core.normalize_url(url)
    }
    existing_keys = {str(project["project_key"]) for project in existing_projects}
    eligible: list[dict[str, Any]] = []
    skipped_known = 0
    skipped_old = 0
    for entry in results:
        repo_url = identifier_core.normalize_url(str(entry.get("url") or ""))
        if not repo_url:
            continue
        if repo_url in known_repo_urls:
            skipped_known += 1
            continue
        eligible.append(entry)

    created = 0
    unmatched = 0
    imported_project_keys: list[str] = []
    unmatched_entries: list[dict[str, Any]] = []
    matched_existing = 0
    match_reasons: dict[str, int] = {}
    suggested_existing_matches = 0
    suggested_entries: list[dict[str, Any]] = []
    screened_out = 0
    screened_out_entries: list[dict[str, Any]] = []
    screen_reason_counts: dict[str, int] = {}

    for entry_index, entry in enumerate(eligible, 1):
        if cancel_requested and cancel_requested():
            cancelled = True
            scan_complete = False
            break
        repo_url = identifier_core.normalize_url(str(entry.get("url") or ""))
        if not repo_url:
            continue
        if progress:
            progress(entry_index, len(eligible), {"kind": "source_item", "source": "github", "value": repo_url})
        project_key, match_kind = resolve_github_search_project_key(
            conn,
            entry,
            service_host_lookup=service_host_lookup,
        )
        suggested_project_key = None
        suggested_match_kind = ""
        if project_key and match_kind and not sync_match_is_auto_attach_safe(match_kind):
            suggested_project_key = project_key
            suggested_match_kind = match_kind
            suggested_existing_matches += 1
            suggested_entries.append(
                {
                    "seed_type": "repo_url",
                    "seed_value": repo_url,
                    "full_name": str(entry.get("full_name") or ""),
                    "description": str(entry.get("description") or ""),
                    "updated_at": str(entry.get("updated_at") or ""),
                    "suggested_project_key": suggested_project_key,
                    "suggested_match_kind": suggested_match_kind,
                }
            )
            project_key = None
            match_kind = ""
        is_new = project_key is None
        if is_new and not create_unmatched:
            unmatched += 1
            unmatched_entries.append(
                {
                    "seed_type": "repo_url",
                    "seed_value": repo_url,
                    "full_name": str(entry.get("full_name") or ""),
                    "description": str(entry.get("description") or ""),
                    "updated_at": str(entry.get("updated_at") or ""),
                    "stargazers_count": int(entry.get("stargazers_count") or 0),
                    "query": str(entry.get("query") or ""),
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
        if is_new and create_unmatched and screen_unmatched:
            try:
                screen = screen_github_repo_sync_candidate(entry)
            except Exception as exc:
                screen = {"allow": False, "reasons": ["screen_failed"], "error": str(exc)}
            for reason in screen.get("reasons", []) if isinstance(screen.get("reasons", []), list) else []:
                screen_reason_counts[str(reason)] = int(screen_reason_counts.get(str(reason), 0)) + 1
            if not screen.get("allow"):
                screened_out += 1
                screened_out_entries.append(
                    {
                        "seed_type": "repo_url",
                        "seed_value": repo_url,
                        "full_name": str(entry.get("full_name") or ""),
                        "description": str(entry.get("description") or ""),
                        "updated_at": str(entry.get("updated_at") or ""),
                        "stargazers_count": int(entry.get("stargazers_count") or 0),
                        "query": str(entry.get("query") or ""),
                        "screen": screen,
                    }
                )
                continue
        if project_key is None:
            project_key = generate_project_key(existing_keys)
            existing_keys.add(project_key)
        else:
            matched_existing += 1
            if match_kind:
                match_reasons[match_kind] = int(match_reasons.get(match_kind, 0)) + 1
        project = fetch_project(conn, project_key) or default_project_record(project_key, "repo_url", repo_url)
        add_manual_seed(project, "repo_url", repo_url, source="github_repo_search_sync")
        project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "github_repo_search_sync")
        description = str(entry.get("description") or "").strip()
        if description:
            add_candidate_text(project, "summary", description, f"github_search_sync:{entry.get('query') or ''}")
            add_candidate_text(project, "tagline", description, f"github_search_sync:{entry.get('query') or ''}")
        homepage = normalize_service_candidate_url(str(entry.get("homepage") or ""))
        if homepage:
            add_candidate_url(project, "service_url", homepage)
            add_candidate_url(project, "website_url", homepage)
        project = finalize_project_shape(project)
        project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
        project["merge_recommendation"] = compute_merge_recommendation(project)
        imported_project_keys.append(project["project_key"])
        if execute:
            save_project(conn, project, enriched=False)
        if is_new:
            created += 1
    state_updated = bool(
        execute
        and incremental
        and scan_complete
        and bool(next_watermarks)
        and (create_unmatched or unmatched == 0)
        and int(screen_reason_counts.get("screen_failed", 0)) == 0
    )
    if execute:
        if state_updated:
            write_sync_state(conn, "github-search", scope, {"queries": next_watermarks})
        conn.commit()

    return {
        "queries": queries,
        "fetched_repos": len(results),
        "eligible_repos": len(eligible),
        "skipped_known": skipped_known,
        "skipped_old": skipped_old,
        "screen_unmatched": bool(screen_unmatched),
        "screened_out": screened_out,
        "created": created,
        "matched_existing": matched_existing,
        "suggested_existing_matches": suggested_existing_matches,
        "match_reasons": match_reasons,
        "screen_reason_counts": screen_reason_counts,
        "unmatched": unmatched,
        "mode": "execute" if execute else "dry_run",
        "create_unmatched": create_unmatched,
        "lookback_days": lookback_days,
        "incremental": incremental and not all_history,
        "state_updated": state_updated,
        "scan_complete": scan_complete,
        "query_scans": [
            {
                "query": scan.get("query"),
                "pages": scan.get("pages"),
                "boundary_reached": scan.get("boundary_reached"),
                "scan_complete": scan.get("scan_complete"),
                "result_count": len(scan.get("results", [])),
                "watermark_before": str(stored_watermarks.get(str(scan.get("query") or "")) or ""),
                "watermark_after": str(scan.get("newest_timestamp") or "") if state_updated else "",
            }
            for scan in scans
        ],
        "imported_project_keys": imported_project_keys,
        "suggested_entries": suggested_entries[:100],
        "screened_out_entries": screened_out_entries[:100],
        "unmatched_entries": unmatched_entries[:100],
        "cancelled": cancelled,
    }


def sync_semble_collections(
    conn: sqlite3.Connection,
    *,
    execute: bool,
    create_unmatched: bool,
    pages: int,
    per_page: int,
    lookback_days: int | None,
    tracked_collections: Sequence[dict[str, Any]] | None = None,
    incremental: bool = True,
    all_history: bool = False,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    sources = tuple(tracked_collections or SEMBLE_TRACKED_COLLECTIONS)
    scope = {
        "collections": sorted(
            f"{str(source.get('handle') or '').strip()}/{str(source.get('rkey') or '').strip()}"
            for source in sources
        )
    }
    state = read_sync_state(conn, "semble-collections", scope)
    stored_watermarks = state.get("watermark", {}).get("collections", {}) if isinstance(state.get("watermark", {}), dict) else {}
    if not isinstance(stored_watermarks, dict):
        stored_watermarks = {}
    fallback_cutoff = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(0, int(lookback_days)))
        if lookback_days is not None else None
    )
    deduped_results: dict[tuple[str, str], dict[str, Any]] = {}
    deduped_evidence_only: dict[tuple[str, str], dict[str, Any]] = {}
    scans: list[dict[str, Any]] = []
    next_watermarks = dict(stored_watermarks)
    cancelled = False
    for source_index, source in enumerate(sources, 1):
        if cancel_requested and cancel_requested():
            cancelled = True
            break
        source_key = f"{str(source.get('handle') or '').strip()}/{str(source.get('rkey') or '').strip()}"
        stored_cutoff = parse_iso_datetime(str(stored_watermarks.get(source_key) or "")) if incremental and not all_history else None
        cutoff = None if all_history else (stored_cutoff or fallback_cutoff)
        scan = fetch_semble_collection_source_sync_results(source, pages=pages, per_page=per_page, cutoff=cutoff)
        if progress:
            evidence_count = len(scan.get("evidence_only", [])) if isinstance(scan.get("evidence_only", []), list) else 0
            progress(source_index, len(sources), {"kind": "source_collection", "source": "semble", "value": source_key, "item_count": len(scan["results"]) + evidence_count})
        scans.append(scan)
        for entry in scan["results"]:
            key = (str(entry.get("seed_type") or ""), str(entry.get("seed_value") or ""))
            existing = deduped_results.get(key)
            if existing is None or str(entry.get("card_updated_at") or "") > str(existing.get("card_updated_at") or ""):
                deduped_results[key] = entry
        for entry in scan.get("evidence_only", []) if isinstance(scan.get("evidence_only", []), list) else []:
            evidence_url = str(entry.get("evidence_url") or "")
            if not evidence_url:
                continue
            key = (evidence_url, str(entry.get("card_uri") or ""))
            existing = deduped_evidence_only.get(key)
            if existing is None or str(entry.get("card_updated_at") or "") > str(existing.get("card_updated_at") or ""):
                deduped_evidence_only[key] = dict(entry)
        if scan.get("scan_complete") and scan.get("newest_timestamp"):
            next_watermarks[source_key] = str(scan["newest_timestamp"])
    results = list(deduped_results.values())
    results.sort(key=lambda item: (str(item.get("card_updated_at") or ""), str(item.get("card_created_at") or "")), reverse=True)
    evidence_only_results = list(deduped_evidence_only.values())
    evidence_only_results.sort(key=lambda item: (str(item.get("card_updated_at") or ""), str(item.get("card_created_at") or "")), reverse=True)
    scan_complete = not cancelled and all(bool(scan.get("scan_complete")) for scan in scans)
    existing_projects = all_projects(conn)
    existing_keys = {str(project["project_key"]) for project in existing_projects}
    imported_project_keys: list[str] = []
    skipped_old = 0
    skipped_non_seed = 0
    matched_existing = 0
    created = 0
    unmatched = 0
    suggested_existing_matches = 0
    suggested_entries: list[dict[str, Any]] = []
    unmatched_entries: list[dict[str, Any]] = []
    evidence_only_attached = 0
    evidence_only_unmatched = 0
    evidence_only_entries: list[dict[str, Any]] = []

    eligible: list[dict[str, Any]] = []
    for entry in results:
        seed_type = str(entry.get("seed_type") or "")
        seed_value = str(entry.get("seed_value") or "")
        if not seed_type or not seed_value:
            skipped_non_seed += 1
            continue
        eligible.append(entry)

    for entry_index, entry in enumerate(eligible, 1):
        if cancel_requested and cancel_requested():
            cancelled = True
            scan_complete = False
            break
        seed_type = str(entry.get("seed_type") or "")
        seed_value = str(entry.get("seed_value") or "")
        if progress:
            progress(entry_index, len(eligible), {"kind": "source_item", "source": "semble", "value": seed_value})
        project_key, _match_kind, suggested_project_key, suggested_match_kind = resolve_semble_project_key_for_seed_value(
            conn, seed_type, seed_value, projects=existing_projects
        )
        if suggested_project_key:
            suggested_existing_matches += 1
            suggested_entries.append({
                "seed_type": seed_type,
                "seed_value": seed_value,
                "suggested_project_key": suggested_project_key,
                "suggested_match_kind": suggested_match_kind,
            })
        is_new = project_key is None
        if is_new and not create_unmatched:
            unmatched += 1
            unmatched_entries.append(
                {
                    "seed_type": seed_type,
                    "seed_value": seed_value,
                    "raw_url": str(entry.get("raw_url") or ""),
                    "title": str(entry.get("title") or ""),
                    "collection_label": str(entry.get("collection_label") or ""),
                    "collection_rkey": str(entry.get("collection_rkey") or ""),
                    "card_updated_at": str(entry.get("card_updated_at") or ""),
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
            project_key = generate_project_key(existing_keys)
            existing_keys.add(project_key)
        else:
            matched_existing += 1
        project = fetch_project(conn, project_key) or default_project_record(project_key, seed_type, seed_value)
        add_manual_seed(project, seed_type, seed_value, source="semble_collection_sync")
        project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "semble_collection_sync")
        title = str(entry.get("title") or "").strip()
        description = str(entry.get("description") or "").strip()
        if title:
            add_candidate_text(project, "tagline", title, f"semble_collection_sync:{entry.get('collection_rkey') or ''}")
        if description:
            add_candidate_text(project, "summary", description, f"semble_collection_sync:{entry.get('collection_rkey') or ''}")
        raw_url = identifier_core.normalize_url(str(entry.get("raw_url") or ""))
        if raw_url:
            project["evidence_urls"] = add_unique(project.get("evidence_urls", []), raw_url)
        card_uri = str(entry.get("card_uri") or "").strip()
        if card_uri:
            project["evidence_urls"] = add_unique(project.get("evidence_urls", []), card_uri)
        project = finalize_project_shape(project)
        project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
        project["merge_recommendation"] = compute_merge_recommendation(project)
        imported_project_keys.append(project["project_key"])
        if execute:
            save_project(conn, project, enriched=False)
            existing_projects = [
                existing_project
                for existing_project in existing_projects
                if str(existing_project.get("project_key") or "") != str(project.get("project_key") or "")
            ]
            existing_projects.append(project)
        if is_new:
            created += 1

    for entry_index, entry in enumerate(evidence_only_results, 1):
        if cancel_requested and cancel_requested():
            cancelled = True
            scan_complete = False
            break
        evidence_url = identifier_core.normalize_url(str(entry.get("evidence_url") or entry.get("raw_url") or ""))
        root_service_url = identifier_core.normalize_url(str(entry.get("root_service_url") or root_service_url_for_evidence(evidence_url)))
        if progress:
            progress(entry_index, len(evidence_only_results), {"kind": "source_item", "source": "semble", "value": evidence_url, "classification": "evidence_only"})
        project_key = None
        suggested_project_key = None
        suggested_match_kind = ""
        if root_service_url:
            project_key, _match_kind, suggested_project_key, suggested_match_kind = resolve_semble_project_key_for_seed_value(
                conn, "service_url", root_service_url, projects=existing_projects
            )
        if not project_key:
            evidence_only_unmatched += 1
            if suggested_project_key:
                suggested_existing_matches += 1
                suggested_entries.append({
                    "seed_type": "service_url",
                    "seed_value": root_service_url,
                    "evidence_url": evidence_url,
                    "suggested_project_key": suggested_project_key,
                    "suggested_match_kind": suggested_match_kind,
                })
            evidence_only_entries.append(
                {
                    "evidence_url": evidence_url,
                    "root_service_url": root_service_url,
                    "reason": str(entry.get("reason") or "evidence_only"),
                    "title": str(entry.get("title") or ""),
                    "collection_label": str(entry.get("collection_label") or ""),
                    "collection_rkey": str(entry.get("collection_rkey") or ""),
                    "card_updated_at": str(entry.get("card_updated_at") or ""),
                    "attached_project_key": "",
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
        project = fetch_project(conn, project_key)
        if project is None:
            evidence_only_unmatched += 1
            continue
        if evidence_url:
            project["evidence_urls"] = add_unique(project.get("evidence_urls", []), evidence_url)
        card_uri = str(entry.get("card_uri") or "").strip()
        if card_uri:
            project["evidence_urls"] = add_unique(project.get("evidence_urls", []), card_uri)
        title = str(entry.get("title") or "").strip()
        description = str(entry.get("description") or "").strip()
        if title:
            add_candidate_text(project, "tagline", title, f"semble_collection_sync:{entry.get('collection_rkey') or ''}")
        if description:
            add_candidate_text(project, "summary", description, f"semble_collection_sync:{entry.get('collection_rkey') or ''}")
        project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "semble_collection_sync")
        project = finalize_project_shape(project)
        project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
        project["merge_recommendation"] = compute_merge_recommendation(project)
        if execute:
            save_project(conn, project, enriched=False)
        evidence_only_attached += 1
        evidence_only_entries.append(
            {
                "evidence_url": evidence_url,
                "root_service_url": root_service_url,
                "reason": str(entry.get("reason") or "evidence_only"),
                "title": title,
                "collection_label": str(entry.get("collection_label") or ""),
                "collection_rkey": str(entry.get("collection_rkey") or ""),
                "card_updated_at": str(entry.get("card_updated_at") or ""),
                "attached_project_key": project_key,
            }
        )

    state_updated = bool(
        execute
        and incremental
        and scan_complete
        and bool(next_watermarks)
        and (create_unmatched or unmatched == 0)
    )
    if execute:
        if state_updated:
            write_sync_state(conn, "semble-collections", scope, {"collections": next_watermarks})
        conn.commit()

    return {
        "tracked_collections": [
            {
                "handle": str(source.get("handle") or ""),
                "rkey": str(source.get("rkey") or ""),
                "label": str(source.get("label") or ""),
            }
            for source in sources
        ],
        "fetched_cards": sum(int(scan.get("fetched_cards") or 0) for scan in scans),
        "eligible_cards": len(eligible),
        "evidence_only_cards": len(evidence_only_results),
        "evidence_only_attached": evidence_only_attached,
        "evidence_only_unmatched": evidence_only_unmatched,
        "skipped_old": skipped_old,
        "skipped_non_seed": skipped_non_seed + sum(int(scan.get("skipped_cards") or 0) for scan in scans),
        "created": created,
        "matched_existing": matched_existing,
        "suggested_existing_matches": suggested_existing_matches,
        "unmatched": unmatched,
        "mode": "execute" if execute else "dry_run",
        "create_unmatched": create_unmatched,
        "lookback_days": lookback_days,
        "incremental": incremental and not all_history,
        "scan_complete": scan_complete,
        "state_updated": state_updated,
        "collection_scans": [
            {
                "source_key": str(scan.get("source_key") or ""),
                "pages": int(scan.get("pages") or 0),
                "fetched_cards": int(scan.get("fetched_cards") or 0),
                "evidence_only_cards": len(scan.get("evidence_only", [])) if isinstance(scan.get("evidence_only", []), list) else 0,
                "skipped_cards": int(scan.get("skipped_cards") or 0),
                "ordered_desc": bool(scan.get("ordered_desc")),
                "boundary_reached": bool(scan.get("boundary_reached")),
                "scan_complete": bool(scan.get("scan_complete")),
                "result_count": len(scan.get("results", [])),
                "watermark_before": str(stored_watermarks.get(str(scan.get("source_key") or "")) or ""),
                "watermark_after": str(scan.get("newest_timestamp") or "") if state_updated else "",
            }
            for scan in scans
        ],
        "imported_project_keys": imported_project_keys,
        "suggested_entries": suggested_entries[:100],
        "evidence_only_entries": evidence_only_entries[:100],
        "unmatched_entries": unmatched_entries[:100],
        "cancelled": cancelled,
    }


def fetch_tangled_ufos_window() -> list[dict[str, Any]]:
    config = dict(load_config().get("tangled_repo_discovery") or {})
    api_url = str(config.get("ufos_api_url") or f"{UFOS_API}/records")
    collection = str(config.get("collection") or tangled_core.TANGLED_COLLECTION)
    separator = "&" if "?" in api_url else "?"
    payload = http_transport.request_json(f"{api_url}{separator}{urllib.parse.urlencode({'collection': collection})}", timeout=REQUEST_TIMEOUT_LEXICON_HTTP)
    return tangled_core.normalize_ufos_entries(payload)


def project_for_tangled_entry(conn: sqlite3.Connection, owner_did: str, website: str) -> dict[str, Any] | None:
    normalized_website = identifier_core.normalize_url(website)
    for project in all_projects(conn):
        if owner_did and owner_did in confirmed_developer_dids(project):
            return project
        urls = unique_list(([project.get("website_url")] if project.get("website_url") else []) + list(project.get("service_urls", [])))
        if normalized_website and normalized_website in {identifier_core.normalize_url(str(item)) for item in urls}:
            return project
    return None


def sync_tangled_repos(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    create_unmatched: bool = False,
    incremental: bool = True,
    all_history: bool = False,
    max_items: int | None = None,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    scope = {"source": "ufos", "collection": tangled_core.TANGLED_COLLECTION}
    state = read_sync_state(conn, "tangled-repos", scope)
    watermark_before = int(state.get("watermark", {}).get("time_us") or 0) if incremental and not all_history else 0
    observed_entries = fetch_tangled_ufos_window()
    config = dict(load_config().get("tangled_repo_discovery") or {})
    configured_limit = max(1, int(config.get("limit") or 100))
    requested_limit = max(1, int(max_items)) if max_items is not None else configured_limit
    processing_limit = min(configured_limit, requested_limit)
    entries = observed_entries[:processing_limit]
    bounded_truncated = (
        max_items is not None
        and processing_limit < configured_limit
        and len(entries) < len(observed_entries)
    )
    newest = max((int(item.get("time_us") or 0) for item in entries), default=0)
    oldest = min((int(item.get("time_us") or 0) for item in entries), default=0)
    boundary_reached = not watermark_before or any(int(item.get("time_us") or 0) <= watermark_before for item in entries)
    selected = [item for item in entries if not watermark_before or int(item.get("time_us") or 0) > watermark_before]
    matched = created = unmatched = skipped_irrelevant = 0
    results: list[dict[str, Any]] = []
    cancelled = False
    for entry_index, entry in enumerate(selected, 1):
        if cancel_requested and cancel_requested():
            cancelled = True
            break
        record = dict(entry.get("record") or {})
        if progress:
            progress(entry_index, len(selected), {"kind": "source_item", "source": "tangled", "value": str(entry.get("rkey") or "")})
        relevant = bool(entry.get("atproto_relevant"))
        if not relevant:
            skipped_irrelevant += 1
            continue
        owner_did = str(entry.get("owner_did") or "")
        try:
            profile = fetch_bsky_profile(owner_did) if owner_did else {}
        except Exception:
            profile = {}
        handle = str(profile.get("handle") or owner_did)
        repo_url = tangled_core.tangled_repo_url(handle, record, str(entry.get("rkey") or ""))
        project = project_for_tangled_entry(conn, owner_did, str(record.get("website") or ""))
        outcome = "unmatched"
        if project is not None:
            matched += 1
            outcome = "matched"
        elif create_unmatched:
            project = ensure_project_for_seed(conn, "repo_url", repo_url) if execute else default_project_record("dry-run", "repo_url", repo_url)
            created += 1
            outcome = "created"
        else:
            unmatched += 1
        if project is not None:
            project = ensure_project_shape(project)
            score = tangled_core.repo_relevance(project, record, handle)
            bundle = EvidenceBundle(project_key=str(project.get("project_key") or "dry-run"), collected_at=iso_now())
            bundle.add_record(investigation_core.evidence_record(
                kind="tangled_repo_snapshot", source="tangled_ufos", classification="candidate",
                observed_at=iso_now(),
                value={"owner_handle": handle, "owner_did": owner_did, "repo_url": repo_url,
                       "repo_did": str(record.get("repoDid") or ""), "record": record, "score": score},
            ))
            if execute:
                apply_tangled_evidence(project, bundle)
                project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "tangled_ufos")
                save_project(conn, finalize_project_shape(project), enriched=False)
        results.append({
            "project_key": str(project.get("project_key") or "") if project is not None else "",
            "repo_url": repo_url,
            "owner_did": owner_did,
            "outcome": outcome,
            "time_us": entry["time_us"],
        })
    state_updated = bool(
        execute
        and not cancelled
        and incremental
        and newest
        and boundary_reached
        and not bounded_truncated
        and (create_unmatched or unmatched == 0)
    )
    if state_updated:
        write_sync_state(conn, "tangled-repos", scope, {"time_us": newest, "rolling_window_oldest_time_us": oldest})
    if execute:
        conn.commit()
    return {
        "mode": "execute" if execute else "dry_run",
        "source": "ufos_rolling_window",
        "observed": len(observed_entries),
        "fetched": len(entries),
        "processing_limit": processing_limit,
        "truncated": bounded_truncated,
        "selected": len(selected),
        "matched": matched,
        "created": created,
        "unmatched": unmatched,
        "skipped_irrelevant": skipped_irrelevant,
        "cancelled": cancelled,
        "watermark_before": watermark_before,
        "watermark_after": newest if state_updated else watermark_before,
        "boundary_reached": boundary_reached,
        "gap_possible": bool(watermark_before and not boundary_reached),
        "complete_history": False,
        "state_updated": state_updated,
        "results": results[:100],
    }
