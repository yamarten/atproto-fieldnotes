#!/usr/bin/env python3

from __future__ import annotations

from project_registry_core import (
    project_record,
    EvidenceBundle,
    investigation as investigation_core,
    tangled as tangled_core,
    tangled_identity,
    domain_policy as domain_policy_core,
    http_transport,
    identifiers as identifier_core,
    identifier_storage,
    introduced_review,
    relations as relation_core,
    repo_urls as repo_url_core,
    repository_material as repository_material_core,
    repository_sources as repository_source_core,
)

import copy
import contextlib
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import urllib.error
import urllib.parse
from pathlib import Path
from html.parser import HTMLParser

from typing import Any, Callable, Sequence

from project_registry_core.registry import (
    extract_urls,
    pdsls_collection_reference_nsid,
    is_bsky_post_url,
    detect_seed_type,
    resolve_project_key_for_seed_value,
    fetch_bsky_profile,
    fetch_bsky_author_feed,
    fetch_bsky_post_by_web_url,
    bsky_record_urls,
    GITHUB_API,
    SEMBLE_API,
    SEMBLE_TRACKED_COLLECTIONS,
    activity_is_recent,
    add_candidate_project_repository_url,
    add_candidate_url,
    add_collection_owned_nsid,
    add_manual_seed,
    all_projects,
    apply_tangled_evidence,
    apply_candidate_repo_trust_evidence,
    build_collection_project_matchers,
    clean_string_list,
    collect_applog_entries,
    compute_duplicate_candidates,
    compute_merge_recommendation,
    confirm_project_repository_url_with_trust,
    enrich_from_github_repo,
    ensure_project_for_seed,
    finalize_project_shape,
    find_project_key_by_collection_namespace,
    github_headers,
    import_collection_inventory_payload,
    load_config,
    parse_iso_datetime,
    read_sync_state,
    resolve_semble_project_key_for_seed_value,
    search_bsky_posts_payload,
    sync_watermark_datetime,
    upsert_collection_observation,
    write_sync_state,
)
















from project_registry_core.project_record import (
    add_candidate_text,
    DID_RE,
    add_unique,
    default_project_record,
    ensure_project_shape,
    fetch_project,
    find_project_key_by_identifier,
    generate_project_key,
    indexed_identifier_matches,
    iso_now,
    list_projects,
    namespace_from_collection_nsid,
    normalize_service_candidate_url,
    parse_iso,
    save_project,
    service_url_candidate_values,
    project_repository_url_values,
    unique_list,
)


def sync_bluesky_lexicons(
    conn: sqlite3.Connection,
    *,
    repo_url: str,
    target_service_url: str,
    execute: bool = False,
) -> dict[str, Any]:
    normalized_repo = repo_url_core.canonical_repo_url(repo_url)
    normalized_service = identifier_core.normalize_url(target_service_url)
    if not normalized_repo or not normalized_service:
        raise ValueError("Bluesky lexicon repository and target service URL are required")
    target_rows = conn.execute(
        """
        SELECT DISTINCT project_key
        FROM project_identifiers
        WHERE kind = 'service_url' AND value = ?
          AND role = 'service' AND classification = 'confirmed'
        ORDER BY project_key
        """,
        (normalized_service,),
    ).fetchall()
    target_keys = [str(row["project_key"]) for row in target_rows]
    repository = repository_source_core.fetch_github_repo(normalized_repo)
    ref = str(repository.get("revision") or repository.get("default_branch") or "")
    paths = sorted({
        str(item.get("path") or "")
        for item in repository.get("tree", [])
        if isinstance(item, dict)
        and str(item.get("type") or "") == "blob"
        and str(item.get("path") or "").startswith(("lexicons/app/bsky/", "lexicons/chat/bsky/"))
        and str(item.get("path") or "").endswith(".json")
    })
    collections: list[str] = []
    rejected: list[dict[str, str]] = []
    for path in paths:
        try:
            payload = json.loads(repository_source_core.github_repo_file_text(normalized_repo, path, ref=ref))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            rejected.append({"path": path, "reason": f"invalid_json:{type(exc).__name__}"})
            continue
        nsid = identifier_core.normalize_nsid(str(payload.get("id") or "")) if isinstance(payload, dict) else ""
        defs = payload.get("defs") if isinstance(payload, dict) and isinstance(payload.get("defs"), dict) else {}
        main = defs.get("main") if isinstance(defs.get("main"), dict) else {}
        if not nsid.startswith(("app.bsky.", "chat.bsky.")):
            continue
        if not identifier_core.is_valid_nsid(nsid) or str(main.get("type") or "") != "record":
            rejected.append({"path": path, "reason": "not_record_schema", "nsid": nsid})
            continue
        collections.append(nsid)
    collections = unique_list(collections)
    target_key = target_keys[0] if len(target_keys) == 1 else ""
    changed = 0
    if execute and target_key:
        project = fetch_project(conn, target_key)
        if project is None:
            raise ValueError(f"Bluesky target project disappeared: {target_key}")
        for nsid in collections:
            changed += int(add_collection_owned_nsid(
                project,
                nsid,
                source="bluesky_official_lexicons",
            ))
        project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "bluesky_official_lexicons")
        save_project(conn, finalize_project_shape(project), enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True)
        conn.commit()
    return {
        "mode": "execute" if execute else "dry_run",
        "source": normalized_repo,
        "target_service_url": normalized_service,
        "target_project_keys": target_keys,
        "target_project_key": target_key,
        "status": "ready" if target_key else "unmatched" if not target_keys else "ambiguous",
        "scanned_file_count": len(paths),
        "collection_count": len(collections),
        "changed_count": changed,
        "collections": collections,
        "rejected": rejected[:50],
    }


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
    return known


def filter_sync_collection_inventory_payload(
    payload: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    *,
    lookback_days: int,
    watermark: dt.datetime | None = None,
    all_history: bool = False,
    suppressed_nsids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cutoff = None if all_history else (watermark or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(0, int(lookback_days)))))
    known_nsids = known_collection_nsids(projects)
    suppressed = set(suppressed_nsids or set())
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
        if collection_nsid in suppressed or (not all_history and collection_nsid in known_nsids):
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
    aggregated: dict[tuple[str, str], dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        collection_nsid = identifier_core.normalize_nsid(str(item.get("collection") or ""))
        namespace = namespace_from_collection_nsid(collection_nsid)
        if not namespace:
            continue
        project_key = namespace_lookup.get(collection_nsid) or find_project_key_by_collection_namespace(
            existing_projects,
            collection_nsid,
        )
        if not project_key:
            continue
        bucket = aggregated.setdefault(
            (project_key, namespace),
            {
                "collection": namespace,
                "count": 0,
                "recent_count": 0,
                "min": str(item.get("min") or ""),
                "max": str(item.get("max") or ""),
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

    refreshed_project_keys: list[str] = []
    reactivated_project_keys: list[str] = []
    changed_namespaces = 0
    now = dt.datetime.now(dt.timezone.utc)
    for (project_key, namespace), item in aggregated.items():
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
        )
        previous_signature = None
        if previous is not None:
            previous_signature = (
                int(previous.get("count") or 0),
                int(previous.get("recent_count") or 0),
                str(previous.get("min") or ""),
                str(previous.get("max") or ""),
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
    try:
        payload = fetch_collection_inventory(source_url)
        if not payload:
            raise ValueError("empty collection inventory cannot establish absence")
    except Exception:
        if execute:
            from .concurrency import write_transaction
            with write_transaction(conn):
                conn.execute("INSERT OR REPLACE INTO registry_meta VALUES('collection_inventory_status','failed')")
        raise
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
    if execute and not truncated:
        from .publication_registry import save_inventory
        from .concurrency import write_transaction
        from .project_record import iso_now
        with write_transaction(conn):
            save_inventory(conn, payload, observed_at=iso_now())
    projects = all_projects(conn)
    filtered_payload, filter_stats = filter_sync_collection_inventory_payload(
        processing_payload,
        projects,
        lookback_days=lookback_days,
        watermark=None if all_history else stored_watermark,
        all_history=all_history,
        suppressed_nsids=(
            identifier_storage.known_noncurrent_collection_nsids(conn)
            | identifier_storage.active_exclusions(conn, "nsid")
        ),
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
    activity_refresh = (
        {
            "activity_refreshed_namespace_count": 0,
            "activity_refreshed_project_count": 0,
            "reactivated_project_count": 0,
            "reactivated_project_keys": [],
        }
        if all_history
        else refresh_known_collection_activity(
            conn,
            processing_payload,
            execute=execute,
            source=f"collection_inventory_url:{source_url}",
            inactivity_days=lookback_days,
        )
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
        merge_existing_observations=not all_history,
        rebuild_collection_observations=all_history,
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
    progress: Callable[[int, int | None, dict[str, Any]], None] | None = None,
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
            progress(
                pages,
                None,
                {"kind": "source_page", "source": "introduced", "unit": "page", "item_count": len(page_posts)},
            )
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

    # Search runs outside the writer lock; all subsequent writes are atomic.
    if execute:
        conn.execute("BEGIN IMMEDIATE")
    try:
        if execute:
            state = read_sync_state(conn, "introduced", scope)
            stored_pending = state.get("watermark", {}).get("pending_entries", [])
            if not isinstance(stored_pending, list):
                stored_pending = []

        entries_by_seed: dict[tuple[str, str], dict[str, Any]] = {}
        if incremental:
            retry_items = list(stored_pending)
            for review_item in state.get("watermark", {}).get("review_items", []):
                if not isinstance(review_item, dict) or review_item.get("status") != "open" or review_item.get("url_source") == "text":
                    continue
                if not review_item.get("seed_type") or not review_item.get("seed_value"):
                    continue
                retry_items.append({"seed_type": review_item["seed_type"], "seed_value": review_item["seed_value"],
                                    "labels": [review_item.get("label", "")],
                                    "post_web_urls": [review_item.get("post_web_url", "")]})
            for item in retry_items:
                if not isinstance(item, dict):
                    continue
                seed_type = str(item.get("seed_type") or "").strip()
                seed_value = str(item.get("seed_value") or "").strip()
                if not seed_type or not seed_value:
                    continue
                bucket = entries_by_seed.setdefault((seed_type, seed_value), {
                    "seed_type": seed_type, "seed_value": seed_value, "labels": [], "post_web_urls": [],
                })
                bucket["labels"] = unique_list([*bucket["labels"], *clean_string_list(item.get("labels", []), limit=20)])
                bucket["post_web_urls"] = unique_list([*bucket["post_web_urls"], *clean_string_list(item.get("post_web_urls", []), limit=20)])
        pending_before_count = len(entries_by_seed)
        automatic_posts = introduced_review.posts_for_automatic_matching(
            posts, state.get("watermark", {}).get("review_items", [])
        )
        for key, item in collect_applog_entries(automatic_posts).items():
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
        auto_assignments: dict[tuple[str, str], tuple[str, str]] = {}
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
                auto_assignments[(seed_type, seed_value)] = (project["project_key"], str(item["post_web_urls"][0]))
            if execute:
                save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
            updated += 1
        observed_review = introduced_review.observe_posts(conn, posts)
        for review_item in observed_review:
            assigned = auto_assignments.get((str(review_item.get("seed_type") or ""), str(review_item.get("seed_value") or "")))
            if assigned and review_item.get("post_web_url") == assigned[1]:
                review_item["auto_set_project_key"] = assigned[0]
        review_items = introduced_review.merge_items(
            list(state.get("watermark", {}).get("review_items", [])), observed_review
        )
        effective_timestamp = max((value for value in (newest_timestamp, saved_watermark) if value is not None), default=None)
        if execute:
            latest = read_sync_state(conn, "introduced", scope)
            watermark = dict(latest["watermark"])
            existing_review = list(watermark.get("review_items", []))
            legacy_review = introduced_review.legacy_pending_items(watermark.get("pending_entries", []))
            observed_urls = {(item.get("post_web_url"), item.get("seed_type"), item.get("seed_value")) for item in observed_review}
            legacy_review = [item for item in legacy_review if
                             (item.get("post_web_url"), item.get("seed_type"), item.get("seed_value")) not in observed_urls]
            review_items = introduced_review.merge_items(existing_review, [*legacy_review, *observed_review])
            for item in review_items:
                if item.get("status") != "open" or item.get("url_source") == "text" or not item.get("seed_type"):
                    continue
                matches = introduced_review._matches(conn, (str(item["seed_type"]), str(item["seed_value"])))
                item["matched_project_keys"] = matches
                item["reason"] = introduced_review.reason_for_item(conn, item, matches)
                if not item["reason"]:
                    item["status"] = "auto_applied"
            watermark["review_items"] = review_items
            watermark.pop("pending_entries", None)
            watermark["last_scan_complete"] = scan_complete
            watermark["last_scan_at"] = iso_now()
            watermark["last_scan_pages"] = pages
            watermark["last_scan_fetched_posts"] = fetched_posts
            if incremental and scan_complete:
                effective_timestamp = max(
                    (value for value in (effective_timestamp, sync_watermark_datetime(latest)) if value is not None),
                    default=None,
                )
                if effective_timestamp is not None:
                    watermark["timestamp"] = effective_timestamp.isoformat()
            introduced_review.save_state(conn, latest, watermark, scan_complete=scan_complete)
            conn.commit()

        state_updated = bool(execute and incremental and scan_complete and effective_timestamp)

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
            "watermark_after": effective_timestamp.isoformat() if state_updated and effective_timestamp else "",
            "fallback_lookback_days": lookback_days,
            "pending_before": pending_before_count,
            "pending_after": len(pending_entries),
            "review_open_count": sum(item.get("status") == "open" for item in review_items),
            "review_recorded_count": len(review_items),
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

    except BaseException:
        if execute:
            conn.rollback()
        raise

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
) -> tuple[str | None, str, list[str]]:
    repo_url = identifier_core.normalize_url(str(entry.get("url") or ""))
    if repo_url:
        project_key = find_project_key_by_identifier(conn, "repo_url", repo_url)
        if project_key:
            return project_key, "repo_url", [project_key]

    homepage = normalize_service_candidate_url(str(entry.get("homepage") or ""))
    if homepage:
        matches = indexed_identifier_matches(conn, "service_url", homepage)
        if matches:
            best_priority = matches[0][0]
            project_keys = sorted({project_key for priority, project_key in matches if priority == best_priority})
            standing = {
                0: "confirmed",
                1: "candidate",
                2: "former",
                3: "non_production",
            }.get(best_priority, "unknown")
            if len(project_keys) == 1:
                return project_keys[0], f"homepage_exact_{standing}", project_keys
            return None, f"homepage_exact_{standing}_ambiguous", project_keys
        host = urllib.parse.urlparse(homepage).netloc.lower()
        host_family = domain_policy_core.root_domain(host)
        if host_family:
            project_key = service_host_lookup.get(host_family)
            if project_key:
                return project_key, "homepage_family", [project_key]
    return None, "", []


def _github_homepage_relation_record(
    repo_url: str,
    homepage: str,
    *,
    observed_at: str,
) -> Any:
    return relation_core.relation_record(
        source=f"github_repo_metadata:{repo_url}",
        classification="confirmed",
        observed_at=observed_at or iso_now(),
        subject_type="repo",
        subject_value=repo_url,
        predicate="repository_homepage",
        object_type="website",
        object_value=homepage,
        locator=repo_url,
        method="github_homepage",
        context={"collector": "github_search"},
    )


def _store_github_homepage_observation(
    project: dict[str, Any],
    repo_url: str,
    homepage: str,
    *,
    observed_at: str,
) -> None:
    if not repo_url or not homepage:
        return
    record = _github_homepage_relation_record(
        repo_url,
        homepage,
        observed_at=observed_at,
    )
    project["relation_evidence"] = relation_core.normalize_relation_records(
        list(project.get("relation_evidence", [])) + [record.to_dict()]
    )


def _apply_github_homepage_attachment_policy(
    project: dict[str, Any],
    repo_url: str,
    homepage: str,
    *,
    observed_at: str,
    repo_updated_at: str,
) -> dict[str, Any] | None:
    candidate = copy.deepcopy(project)
    add_candidate_project_repository_url(
        candidate,
        repo_url,
        "github_repo_search_sync",
        reason="repository homepage exactly matches a confirmed service identifier",
        evidence_kind="github_repository_homepage",
        evidence_url=homepage,
        updated_at=repo_updated_at,
    )
    bundle = EvidenceBundle(
        project_key=str(candidate.get("project_key") or ""),
        collected_at=observed_at or iso_now(),
    )
    bundle.add_record(_github_homepage_relation_record(
        repo_url,
        homepage,
        observed_at=observed_at,
    ))
    if not apply_candidate_repo_trust_evidence(candidate, repo_url, bundle):
        return None
    if not confirm_project_repository_url_with_trust(
        candidate,
        repo_url,
        "github_repo_search_sync",
        relation="mutual_first_party_link",
        reason="repository homepage and confirmed service source relation agree",
        evidence_kind="github_repository_homepage",
        evidence_url=homepage,
        updated_at=repo_updated_at,
    ):
        return None
    return candidate


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
        for url in project_repository_url_values(project)
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
        observed_at = iso_now()
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
        project_key, match_kind, matched_project_keys = resolve_github_search_project_key(
            conn,
            entry,
            service_host_lookup=service_host_lookup,
        )
        suggested_project_key = None
        suggested_project_keys: list[str] = []
        suggested_match_kind = ""
        matched_project: dict[str, Any] | None = None
        if project_key and match_kind == "homepage_exact_confirmed":
            existing_project = fetch_project(conn, project_key)
            if existing_project is not None:
                matched_project = _apply_github_homepage_attachment_policy(
                    existing_project,
                    repo_url,
                    normalize_service_candidate_url(str(entry.get("homepage") or "")),
                    observed_at=observed_at,
                    repo_updated_at=str(entry.get("updated_at") or ""),
                )
            if matched_project is not None:
                match_kind = "homepage_exact_trust_policy"
        auto_attach_safe = match_kind in {"repo_url", "homepage_exact_trust_policy"}
        if match_kind and not auto_attach_safe:
            suggested_project_keys = list(matched_project_keys)
            suggested_project_key = suggested_project_keys[0] if len(suggested_project_keys) == 1 else None
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
                    "suggested_project_keys": suggested_project_keys,
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
                            "suggested_project_keys": suggested_project_keys,
                            "suggested_match_kind": suggested_match_kind,
                        }
                        if suggested_project_keys else {}
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
        project = matched_project or fetch_project(conn, project_key) or default_project_record(
            project_key, "repo_url", repo_url
        )
        if is_new:
            add_manual_seed(project, "repo_url", repo_url, source="github_repo_search_sync")
        project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "github_repo_search_sync")
        description = str(entry.get("description") or "").strip()
        if description:
            add_candidate_text(project, "summary", description, f"github_search_sync:{entry.get('query') or ''}")
            add_candidate_text(project, "tagline", description, f"github_search_sync:{entry.get('query') or ''}")
        homepage = normalize_service_candidate_url(str(entry.get("homepage") or ""))
        if homepage:
            _store_github_homepage_observation(
                project,
                repo_url,
                homepage,
                observed_at=observed_at,
            )
            add_candidate_url(project, "service_url", homepage)
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


def fetch_tangled_jetstream_snapshot(
    *,
    service: str,
    collection: str,
    after_seq: int,
    max_events: int,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    if not os.environ.get("ATPROTO_SURVEY_JETSTREAM_API_KEY", "").strip():
        raise RuntimeError("ATPROTO_SURVEY_JETSTREAM_API_KEY is not configured")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js 22.15 or newer is required for Tangled Jetstream sync")
    bridge = Path(__file__).resolve().with_name("jetstream_snapshot.mjs")
    process = subprocess.Popen(
        [node, str(bridge)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
        env=os.environ.copy(),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps({
        "service": service,
        "collection": collection,
        "after_seq": max(0, int(after_seq)),
        "max_events": max(1, int(max_events)),
    }) + "\n")
    process.stdin.close()
    events: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    cancelled = False
    try:
        for line in process.stdout:
            if cancel_requested and cancel_requested() and not cancelled:
                cancelled = True
                process.terminate()
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                process.terminate()
                raise RuntimeError("Jetstream snapshot bridge returned invalid JSON") from exc
            if not isinstance(item, dict):
                continue
            if item.get("type") == "event":
                normalized = tangled_core.normalize_jetstream_event(item)
                if normalized is not None:
                    events.append(normalized)
            elif item.get("type") == "summary":
                summary = dict(item)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    returncode = process.wait()
    stderr = process.stderr.read().strip() if process.stderr is not None else ""
    if returncode != 0 and not cancelled:
        detail = stderr.splitlines()[-1][:500] if stderr else f"exit status {returncode}"
        raise RuntimeError(f"Jetstream snapshot bridge failed: {detail}")
    if not summary:
        summary = {
            "after_seq": max(0, int(after_seq)),
            "sealed_tip_seq": max(0, int(after_seq)),
            "last_event_seq": max((int(item["seq"]) for item in events), default=max(0, int(after_seq))),
            "event_count": len(events),
            "truncated": True,
            "cancelled": cancelled,
        }
    summary["events"] = events
    summary["cancelled"] = bool(summary.get("cancelled") or cancelled)
    return summary


def project_for_tangled_entry(
    conn: sqlite3.Connection,
    repo_url: str,
) -> dict[str, Any] | None:
    def unique_project(project_keys: set[str]) -> dict[str, Any] | None:
        if len(project_keys) != 1:
            return None
        return fetch_project(conn, next(iter(project_keys)))

    normalized_repo = repo_url_core.normalize_tangled_repo_url(repo_url)
    if normalized_repo:
        rows = conn.execute(
            """
            SELECT DISTINCT project_key, classification
            FROM project_identifiers
            WHERE kind = 'repo_url' AND value = ?
              AND role = 'project_repository'
              AND classification IN ('confirmed', 'candidate')
            ORDER BY project_key
            LIMIT 2
            """,
            (normalized_repo,),
        ).fetchall()
        confirmed_keys = {
            str(row["project_key"])
            for row in rows
            if str(row["classification"]) == "confirmed"
        }
        confirmed_keys.update(
            project_key
            for priority, project_key in indexed_identifier_matches(conn, "repo_url", normalized_repo)
            if priority == 0
        )
        if exact := unique_project(confirmed_keys):
            return exact
        if confirmed_keys:
            return None
        candidate_keys = {
            str(row["project_key"])
            for row in rows
            if str(row["classification"]) == "candidate"
        }
        if exact := unique_project(candidate_keys):
            return exact
    return None


def tangled_fork_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Interpret a declared parent, without resolving it or inferring clone history.

    Tangled's ForkRepo writes the parent's repo DID, or its sh.tangled.repo
    AT-URI for older repositories. An external clone URI is not that contract.
    Missing/unrecognized data must never become a confirmed non-fork.
    """
    source = record.get("source")
    status = "missing" if source is None or source == "" else "invalid"
    if isinstance(source, str) and source and source == source.strip():
        if DID_RE.fullmatch(source) and not re.search(r"%(?![0-9a-fA-F]{2})", source):
            status = "repo_did"
        elif source.startswith("at://"):
            try:
                parsed = urllib.parse.urlsplit(source)
            except ValueError:
                parsed = None
            if (parsed and DID_RE.fullmatch(parsed.netloc)
                    and not re.search(r"%(?![0-9a-fA-F]{2})", parsed.netloc)
                    and re.fullmatch(r"/sh\.tangled\.repo/[A-Za-z0-9._~:-]{1,512}", parsed.path)
                    and parsed.path.rsplit("/", 1)[-1] not in {".", ".."}
                    and not parsed.query and not parsed.fragment
                    and source == urllib.parse.urlunsplit(parsed)):
                status = "repo_at_uri"
        elif not source.startswith("did:"):
            # Includes external import/mirror URIs: their relationship is unknown.
            status = "unrecognized"
    declared_fork = status in {"repo_did", "repo_at_uri"}
    return {
        "fork": True if declared_fork else None,
        "fork_source": source if declared_fork else "",
        "fork_source_status": status,
    }


def sync_tangled_repos(
    conn: sqlite3.Connection,
    *,
    execute: bool = False,
    create_unmatched: bool = True,
    incremental: bool = True,
    all_history: bool = False,
    max_items: int | None = None,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    config = dict(load_config().get("tangled_repo_discovery") or {})
    service = str(config.get("service_url") or "https://jetstream.us-west.bsky.network").strip().rstrip("/")
    collection = str(config.get("collection") or tangled_core.TANGLED_COLLECTION)
    configured_limit = max(1, int(config.get("max_events") or 10000))
    requested_limit = max(1, int(max_items)) if max_items is not None else configured_limit
    processing_limit = min(configured_limit, requested_limit)
    scope = {"source": "jetstream_v2", "service": service, "collection": collection}
    state = read_sync_state(conn, "tangled-repos", scope)
    watermark_before = int(state.get("watermark", {}).get("seq") or 0) if incremental and not all_history else 0
    snapshot = fetch_tangled_jetstream_snapshot(
        service=service,
        collection=collection,
        after_seq=watermark_before,
        max_events=processing_limit,
        cancel_requested=cancel_requested,
    )
    observed_entries = list(snapshot.get("events") or [])

    def record_uri(entry: dict[str, Any]) -> str:
        return f"at://{entry.get('owner_did')}/{collection}/{entry.get('rkey')}"

    # Retain unresolved records independently of the sequence checkpoint. Retry a
    # bounded prefix, then rotate unresolved items behind the unattempted ones.
    pending = {
        record_uri(entry): entry
        for entry in state.get("watermark", {}).get("pending_owner_records", [])
    } if incremental else {}
    replay = list(pending.values())[:processing_limit] if not all_history else []
    latest_by_uri = {record_uri(entry): entry for entry in replay}
    for entry in observed_entries:
        uri = record_uri(entry)
        pending.pop(uri, None)
        if str(entry.get("operation") or "") == "delete":
            latest_by_uri.pop(uri, None)
        else:
            latest_by_uri[uri] = entry
    selected = sorted(latest_by_uri.values(), key=lambda item: int(item.get("seq") or 0))
    owner_handles: dict[str, str] = {}
    owner_unavailable: dict[str, str] = {}
    owner_count = len({item["owner_did"] for item in selected if item.get("atproto_relevant")})
    cancelled = bool(snapshot.get("cancelled"))
    for entry in selected:
        if cancelled or (cancel_requested and cancel_requested()):
            cancelled = True
            break
        if not entry.get("atproto_relevant"):
            continue
        owner_did = str(entry.get("owner_did") or "")
        if owner_did in owner_handles or owner_did in owner_unavailable:
            continue
        if progress:
            progress(len(owner_handles) + len(owner_unavailable) + 1, owner_count,
                     {"kind": "owner_identity", "source": "tangled", "value": owner_did})
        try:
            owner_handles[owner_did] = tangled_identity.resolve_owner_handle(owner_did)
        except tangled_identity.OwnerUnavailable as exc:
            owner_unavailable[owner_did] = str(exc)
    sealed_tip_seq = int(snapshot.get("sealed_tip_seq") or watermark_before)
    last_event_seq = int(snapshot.get("last_event_seq") or watermark_before)
    bounded_truncated = bool(snapshot.get("truncated"))
    matched = created = unmatched = skipped_irrelevant = skipped_owner = skipped_forks = 0
    results: list[dict[str, Any]] = []
    with conn if execute else contextlib.nullcontext():
        for entry_index, entry in enumerate(selected, 1):
            if cancelled or (cancel_requested and cancel_requested()):
                cancelled = True
                break
            pending.pop(record_uri(entry), None)
            record = dict(entry.get("record") or {})
            if progress:
                progress(entry_index, len(selected), {"kind": "source_item", "source": "tangled", "value": str(entry.get("rkey") or "")})
            relevant = bool(entry.get("atproto_relevant"))
            if not relevant:
                skipped_irrelevant += 1
                continue
            owner_did = str(entry.get("owner_did") or "")
            if owner_did in owner_unavailable:
                skipped_owner += 1
                reason = owner_unavailable[owner_did]
                pending[record_uri(entry)] = {**entry, "owner_resolution_reason": reason}
                result = {
                    "owner_did": owner_did, "record_uri": record_uri(entry),
                    "seq": entry["seq"], "outcome": "deferred_owner", "reason": reason,
                }
                results.append(result)
                if progress:
                    progress(entry_index, len(selected), {"kind": "source_item_result", "source": "tangled", **result})
                continue
            handle = owner_handles[owner_did]
            repo_url = tangled_core.tangled_repo_url(handle, record, str(entry.get("rkey") or ""))
            project = project_for_tangled_entry(conn, repo_url)
            fork_metadata = tangled_fork_metadata(record)
            outcome = "unmatched"
            reason = ""
            if project is not None:
                matched += 1
                outcome = "matched"
            elif fork_metadata["fork"]:
                # Match first: explicit registration and existing projects remain
                # eligible for updates. Only automatic new discovery excludes forks.
                skipped_forks += 1
                outcome = "skipped_fork"
                reason = "tangled_fork_excluded_from_discovery"
            elif create_unmatched:
                project = ensure_project_for_seed(conn, "repo_url", repo_url, source="tangled_jetstream", commit=False) if execute else default_project_record("dry-run", "repo_url", repo_url)
                created += 1
                outcome = "created"
            else:
                unmatched += 1
            if project is not None:
                project = ensure_project_shape(project)
                score = tangled_core.repo_relevance(project, record, handle)
                bundle = EvidenceBundle(project_key=str(project.get("project_key") or "dry-run"), collected_at=iso_now())
                bundle.add_record(investigation_core.evidence_record(
                    kind="tangled_repo_snapshot", source="tangled_jetstream", classification="candidate",
                    observed_at=iso_now(),
                    value={"owner_handle": handle, "owner_did": owner_did, "repo_url": repo_url,
                           "repo_did": str(record.get("repoDid") or ""), "record": record, "score": score},
                ))
                if execute:
                    apply_tangled_evidence(project, bundle)
                    project["discovery_sources"] = add_unique(project.get("discovery_sources", []), "tangled_jetstream")
                    save_project(conn, finalize_project_shape(project), enriched=False, commit=False)
            result = {
                "project_key": str(project.get("project_key") or "") if project is not None else "",
                "repo_url": repo_url,
                "owner_did": owner_did,
                "outcome": outcome,
                "seq": entry["seq"],
                "observed_at": str(entry.get("time") or ""),
                **fork_metadata,
                **({"reason": reason} if reason else {}),
            }
            results.append(result)
            if outcome == "skipped_fork" and progress:
                progress(entry_index, len(selected), {"kind": "source_item_result", "source": "tangled", **result})
        state_updated = bool(
            execute
            and not cancelled
            and incremental
            and (create_unmatched or unmatched == 0)
        )
        if state_updated:
            next_seq = last_event_seq if bounded_truncated else sealed_tip_seq
            write_sync_state(conn, "tangled-repos", scope, {
                "seq": next_seq, "pending_owner_records": list(pending.values()),
            })
        if execute:
            conn.commit()
    return {
        "mode": "execute" if execute else "dry_run",
        "source": "jetstream_v2_snapshot",
        "service": service,
        "collection": collection,
        "observed": int(snapshot.get("event_count") or len(observed_entries)),
        "fetched": len(observed_entries),
        "processing_limit": processing_limit,
        "truncated": bounded_truncated,
        "selected": len(selected),
        "matched": matched,
        "created": created,
        "unmatched": unmatched,
        "skipped_irrelevant": skipped_irrelevant,
        "skipped_owner": skipped_owner,
        "skipped_forks": skipped_forks,
        "unresolved_owners": len(owner_unavailable),
        "owner_errors": [{"owner_did": did, "reason": reason} for did, reason in owner_unavailable.items()],
        "retried_owner_records": len(replay),
        "pending_owner_records": len(pending),
        "cancelled": cancelled,
        "watermark_before": watermark_before,
        "watermark_after": (last_event_seq if bounded_truncated else sealed_tip_seq) if state_updated else watermark_before,
        "sealed_tip_seq": sealed_tip_seq,
        "complete_history": bool(not watermark_before and not bounded_truncated and not cancelled),
        "snapshot_error": dict(snapshot.get("error") or {}) if isinstance(snapshot.get("error"), dict) else {},
        "state_updated": state_updated,
        "results": results[:100],
    }


class HrefHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(str(href))


def parse_text_seed_line_values(line: str) -> list[str]:
    text = str(line or "").strip()
    if not text:
        return []
    if text.startswith("at://") or project_record.extract_collection_nsid(text):
        return [text]
    if "http://" in text or "https://" in text:
        return extract_urls(text)
    return []


def line_looks_like_seed_section_header(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    if text.startswith("(") and text.endswith(")"):
        return False
    if ":" in text or "：" in text:
        return False
    return not parse_text_seed_line_values(text)


def seed_note_groups_multiple_seed_lines(line: str) -> bool:
    text = str(line or "").strip()
    return ":" in text or "：" in text


def text_seed_label_tokens(label: str) -> set[str]:
    text = str(label or "").strip()
    if not text:
        return set()
    head = re.split(r"[:：]", text, maxsplit=1)[0].strip()
    tokens = {identifier_core.normalize_alias_token(head)}
    tokens.update(identifier_core.normalize_alias_token(part) for part in re.split(r"[\s./_-]+", head) if part.strip())
    return {token for token in tokens if token}


def seed_values_match_group(raw_values: list[str], label: str, existing_values: list[str]) -> bool:
    label_tokens = text_seed_label_tokens(label)
    candidate_tokens: set[str] = set()
    for value in raw_values:
        candidate_tokens.update(project_record.seed_value_identity_tokens(value))
    if not candidate_tokens:
        return False
    if label_tokens and candidate_tokens & label_tokens:
        return True
    existing_tokens: set[str] = set()
    for value in existing_values:
        existing_tokens.update(project_record.seed_value_identity_tokens(value))
    return bool(existing_tokens and candidate_tokens & existing_tokens)


def classify_text_seed_value(raw_value: str) -> dict[str, Any]:
    raw = str(raw_value or "").strip()
    if not raw:
        return {"status": "skip", "reason": "empty"}
    normalized_url = identifier_core.normalize_url(raw)
    if normalized_url:
        parsed = urllib.parse.urlparse(normalized_url)
        pdsls_reference_nsid = pdsls_collection_reference_nsid(normalized_url)
        if pdsls_reference_nsid:
            return {
                "status": "skip",
                "reason": "pdsls_collection_reference_url",
                "normalized": normalized_url,
                "derived_nsid": pdsls_reference_nsid,
            }
        if is_bsky_post_url(normalized_url):
            return {"status": "skip", "reason": "bsky_post_url", "normalized": normalized_url}
        if project_record.is_bluesky_profile_url(normalized_url):
            return {"status": "skip", "reason": "bsky_profile_url", "normalized": normalized_url}
        if parsed.netloc.lower() == "lexicon.garden":
            return {"status": "skip", "reason": "lexicon_garden_non_seed_url", "normalized": normalized_url}
        if parsed.netloc.lower() in project_record.PDSLS_VIEWER_HOSTS:
            return {"status": "skip", "reason": "pdsls_non_collection_url", "normalized": normalized_url}
    nsid = project_record.extract_collection_nsid(raw)
    if nsid:
        return {"status": "seed", "seed_type": "nsid", "seed_value": nsid}
    try:
        seed_type = detect_seed_type(raw)
    except ValueError:
        return {"status": "skip", "reason": "unsupported_line"}
    seed_value = project_record.canonical_seed_value(seed_type, raw)
    if seed_type == "service_url" and project_record.is_explicitly_non_project_service_url(seed_value):
        return {"status": "skip", "reason": "non_project_service_url", "normalized": seed_value}
    return {"status": "seed", "seed_type": seed_type, "seed_value": seed_value}


def classify_derived_seed_values(raw_values: list[str]) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_value in raw_values:
        classified = classify_text_seed_value(raw_value)
        if classified.get("status") != "seed":
            continue
        seed_type = str(classified.get("seed_type") or "")
        seed_value = str(classified.get("seed_value") or "")
        key = (seed_type, seed_value)
        if not seed_type or not seed_value or key in seen:
            continue
        seen.add(key)
        seeds.append({"raw_value": raw_value, "seed_type": seed_type, "seed_value": seed_value})
    return seeds


def resolve_bsky_profile_url_seed_candidates(url: str) -> list[dict[str, Any]]:
    subject = project_record.extract_bluesky_profile_subject(url)
    actor = subject.get("handle") or subject.get("did") or ""
    if not actor:
        return []
    try:
        profile = fetch_bsky_profile(actor)
    except Exception:
        return []
    derived_urls = extract_urls(str(profile.get("description") or ""))
    try:
        for item in fetch_bsky_author_feed(str(profile.get("did") or str(profile.get("handle") or actor)), limit=5):
            derived_urls.extend([str(value or "") for value in item.get("urls", [])])
    except Exception:
        pass
    return classify_derived_seed_values(project_record.unique_list([value for value in derived_urls if value]))


def resolve_bsky_post_url_seed_candidates(url: str) -> list[dict[str, Any]]:
    try:
        post = fetch_bsky_post_by_web_url(url)
    except Exception:
        return []
    if not post:
        return []
    derived_urls = bsky_record_urls(post.get("record", {}) if isinstance(post.get("record", {}), dict) else {})
    embed = post.get("embed", {}) if isinstance(post.get("embed"), dict) else {}
    external = embed.get("external", {}) if isinstance(embed.get("external"), dict) else {}
    external_uri = str(external.get("uri") or "").strip()
    if external_uri:
        derived_urls.append(external_uri)
    return classify_derived_seed_values(project_record.unique_list([value for value in derived_urls if value]))


def resolve_lexicon_garden_identity_seed_candidates(url: str) -> list[dict[str, Any]]:
    normalized = identifier_core.normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.netloc.lower() != "lexicon.garden" or len(segments) < 2 or segments[0] != "identity":
        return []
    try:
        _, body, _ = http_transport.request_bytes(normalized, headers={"Accept": "text/html"})
    except Exception:
        return []
    parser = HrefHTMLParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    candidates: list[str] = []
    for href in parser.hrefs:
        if "/lexicon/" not in href:
            continue
        absolute = urllib.parse.urljoin(normalized, href)
        nsid = project_record.extract_collection_nsid(absolute)
        if nsid:
            candidates.append(nsid)
    return classify_derived_seed_values(project_record.unique_list(candidates))


def resolve_text_seed_reference_candidates(raw_value: str, reason: str) -> list[dict[str, Any]]:
    if reason == "bsky_profile_url":
        return resolve_bsky_profile_url_seed_candidates(raw_value)
    if reason == "bsky_post_url":
        return resolve_bsky_post_url_seed_candidates(raw_value)
    if reason == "lexicon_garden_non_seed_url":
        return resolve_lexicon_garden_identity_seed_candidates(raw_value)
    if reason == "pdsls_collection_reference_url":
        nsid = pdsls_collection_reference_nsid(raw_value)
        if nsid:
            return classify_derived_seed_values([nsid])
    return []


def parse_text_seed_bundles(path: str) -> list[dict[str, Any]]:
    lines = Path(path).read_text().splitlines()
    bundles: list[dict[str, Any]] = []
    current_section = ""
    pending_label = ""
    active_group: dict[str, Any] | None = None
    for index, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            pending_label = ""
            active_group = None
            continue
        values = parse_text_seed_line_values(line)
        if values:
            if pending_label:
                should_group = active_group is None or seed_values_match_group(values, pending_label, list(active_group.get("raw_values", [])))
                if not should_group:
                    pending_label = ""
                    active_group = None
                    for value in values:
                        bundles.append(
                            {
                                "section": current_section,
                                "label": "",
                                "line_numbers": [index],
                                "raw_values": [value],
                            }
                        )
                    continue
                if active_group is None:
                    active_group = {
                        "section": current_section,
                        "label": pending_label,
                        "line_numbers": [],
                        "raw_values": [],
                    }
                    bundles.append(active_group)
                active_group["line_numbers"].append(index)
                active_group["raw_values"].extend(values)
                if not seed_note_groups_multiple_seed_lines(pending_label):
                    pending_label = ""
                    active_group = None
            else:
                for value in values:
                    bundles.append(
                        {
                            "section": current_section,
                            "label": "",
                            "line_numbers": [index],
                            "raw_values": [value],
                        }
                    )
            continue
        if line_looks_like_seed_section_header(line):
            current_section = line
            pending_label = ""
            active_group = None
            continue
        pending_label = line
        active_group = None
    return bundles


def import_seed_list(conn: sqlite3.Connection, path: str, *, execute: bool = False) -> dict[str, Any]:
    bundles = parse_text_seed_bundles(path)
    summary_bundles: list[dict[str, Any]] = []
    imported_projects = 0
    imported_seeds = 0
    skipped_seeds = 0
    conflict_bundles = 0

    for bundle in bundles:
        raw_values = list(bundle.get("raw_values", []))
        section = str(bundle.get("section") or "").strip()
        label = str(bundle.get("label") or "").strip()
        line_numbers = list(bundle.get("line_numbers", []))
        actionable: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen_bundle_seeds: set[tuple[str, str]] = set()
        for raw_value in raw_values:
            classified = classify_text_seed_value(str(raw_value or ""))
            if classified.get("status") != "seed":
                derived = resolve_text_seed_reference_candidates(str(raw_value or ""), str(classified.get("reason") or ""))
                if derived:
                    for item in derived:
                        seed_type = str(item.get("seed_type") or "")
                        seed_value = str(item.get("seed_value") or "")
                        key = (seed_type, seed_value)
                        if not seed_type or not seed_value or key in seen_bundle_seeds:
                            continue
                        seen_bundle_seeds.add(key)
                        project_key, match_kind = resolve_project_key_for_seed_value(conn, seed_type, seed_value)
                        actionable.append(
                            {
                                "raw_value": raw_value,
                                "derived_from_reference": True,
                                "reference_reason": str(classified.get("reason") or ""),
                                "seed_type": seed_type,
                                "seed_value": seed_value,
                                "resolved_project_key": project_key,
                                "match_kind": match_kind,
                            }
                        )
                    continue
                skipped.append({"raw_value": raw_value, **classified})
                skipped_seeds += 1
                continue
            seed_type = str(classified.get("seed_type") or "")
            seed_value = str(classified.get("seed_value") or "")
            key = (seed_type, seed_value)
            if not seed_type or not seed_value or key in seen_bundle_seeds:
                continue
            seen_bundle_seeds.add(key)
            project_key, match_kind = resolve_project_key_for_seed_value(conn, seed_type, seed_value)
            actionable.append(
                {
                    "raw_value": raw_value,
                    "seed_type": seed_type,
                    "seed_value": seed_value,
                    "resolved_project_key": project_key,
                    "match_kind": match_kind,
                }
            )

        service_roots_by_host: dict[str, str] = {}
        for item in actionable:
            if str(item.get("seed_type") or "") != "service_url":
                continue
            seed_value = str(item.get("seed_value") or "")
            parsed = urllib.parse.urlparse(seed_value)
            if parsed.path in {"", "/"}:
                service_roots_by_host[parsed.netloc.lower()] = seed_value
        if service_roots_by_host:
            filtered_actionable: list[dict[str, Any]] = []
            for item in actionable:
                if str(item.get("seed_type") or "") != "service_url":
                    filtered_actionable.append(item)
                    continue
                seed_value = str(item.get("seed_value") or "")
                parsed = urllib.parse.urlparse(seed_value)
                root_value = service_roots_by_host.get(parsed.netloc.lower())
                if root_value and root_value != seed_value:
                    skipped.append(
                        {
                            "raw_value": item.get("raw_value", ""),
                            "status": "skip",
                            "reason": "service_subpage_with_same_host",
                            "normalized": seed_value,
                        }
                    )
                    skipped_seeds += 1
                    continue
                filtered_actionable.append(item)
            actionable = filtered_actionable

        if any(str(item.get("reference_reason") or "") == "pdsls_collection_reference_url" for item in actionable):
            filtered_actionable = []
            for item in actionable:
                seed_type = str(item.get("seed_type") or "")
                seed_value = str(item.get("seed_value") or "")
                parsed = urllib.parse.urlparse(seed_value) if seed_value.startswith("http") else None
                if seed_type == "service_url" and parsed and parsed.netloc.lower() in project_record.PDSLS_VIEWER_HOSTS:
                    skipped.append(
                        {
                            "raw_value": item.get("raw_value", ""),
                            "status": "skip",
                            "reason": "pdsls_viewer_bundle_context",
                            "normalized": seed_value,
                        }
                    )
                    skipped_seeds += 1
                    continue
                if seed_type == "service_url" and parsed and parsed.netloc.lower() in project_record.PDSLS_HOSTS:
                    skipped.append(
                        {
                            "raw_value": item.get("raw_value", ""),
                            "status": "skip",
                            "reason": "pdsls_service_bundle_context",
                            "normalized": seed_value,
                        }
                    )
                    skipped_seeds += 1
                    continue
                if seed_type == "repo_url" and repo_url_core.canonical_repo_url(seed_value) == "https://github.com/notjuliet/pdsls":
                    skipped.append(
                        {
                            "raw_value": item.get("raw_value", ""),
                            "status": "skip",
                            "reason": "pdsls_repo_bundle_context",
                            "normalized": seed_value,
                        }
                    )
                    skipped_seeds += 1
                    continue
                filtered_actionable.append(item)
            actionable = filtered_actionable

        resolved_keys = sorted({str(item.get("resolved_project_key") or "") for item in actionable if item.get("resolved_project_key")})
        conflict = len(resolved_keys) > 1
        if conflict:
            conflict_bundles += 1
        target_project_key = resolved_keys[0] if len(resolved_keys) == 1 else None
        created_project_key = ""
        if execute and actionable and not conflict:
            first = actionable[0]
            project = ensure_project_for_seed(
                conn,
                str(first["seed_type"]),
                str(first["seed_value"]),
                project_key=target_project_key,
            )
            for item in actionable[1:]:
                project = ensure_project_for_seed(
                    conn,
                    str(item["seed_type"]),
                    str(item["seed_value"]),
                    project_key=project["project_key"],
                )
            project = project_record.ensure_project_shape(project)
            project["discovery_sources"] = project_record.add_unique(project.get("discovery_sources", []), "text_seed_list_import")
            source_note = f"imported from text seed list: {Path(path).name}"
            project_record.append_system_note(project, source_note)
            if section:
                project_record.append_system_note(project, f"seed-list section: {section}")
            if label:
                project_record.append_system_note(project, f"seed-list note: {label}")
            project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
            created_project_key = project["project_key"]
            imported_projects += 1
            imported_seeds += len(actionable)

        summary_bundles.append(
            {
                "section": section,
                "label": label,
                "line_numbers": line_numbers,
                "actionable_seeds": actionable,
                "skipped_items": skipped,
                "resolved_project_keys": resolved_keys,
                "conflict_existing_projects": conflict,
                "imported_project_key": created_project_key or target_project_key or "",
            }
        )

    if execute:
        conn.commit()
    return {
        "mode": "execute" if execute else "dry_run",
        "path": str(path),
        "bundle_count": len(summary_bundles),
        "imported_projects": imported_projects,
        "imported_seeds": imported_seeds,
        "skipped_seeds": skipped_seeds,
        "conflict_bundles": conflict_bundles,
        "bundles": summary_bundles,
    }
