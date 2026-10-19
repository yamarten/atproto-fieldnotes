#!/usr/bin/env python3

from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any, Callable

from project_registry_core.persistence import runtime_view
from project_registry_core.enrichment_pipeline import enrich_project
from project_registry_core.registry import (
    add_confirmed_identifier_decision,
    confirm_repo_seed,
    confirm_service_seed,
    finalize_project_shape,
    update_project_tracking_state_value,
    verify_service_project,
)
from project_registry_core.project_review import (
    defer_promotion_on_project,
    latest_promotion_deferral_reason,
    project_repo_hint_urls,
    project_service_hint_urls,
)
from project_registry_core.application.review import build_review_queue_item, build_showcase_queue_item
from project_registry_core.project_merge import compute_duplicate_candidates, compute_merge_recommendation
from project_registry_core.project_record import (
    ensure_project_shape,
    fetch_project,
    iso_now,
    normalize_blocked_identifiers,
    save_project,
    unique_list,
)


def output_project_queue(
    conn: sqlite3.Connection,
    *,
    queue_name: str,
    queue: list[dict[str, Any]],
    tracking_states: set[str],
    kinds: set[str] | None,
    interactive: bool,
    load_more: Callable[[], list[dict[str, Any]]] | None = None,
    include_personal_tools: bool = False,
    promotion_view: str = "queue",
) -> int:
    kind_counts: dict[str, int] = {}
    for item in queue:
        for signal in item.get("signals", []):
            kind = str(signal.get("kind") or "")
            kind_counts[kind] = int(kind_counts.get(kind, 0)) + 1
    if interactive and sys.stdin.isatty() and sys.stdout.isatty():
        index = 0
        picked_items: dict[str, dict[str, Any]] = {}
        stop_interactive = False
        while True:
            if stop_interactive:
                break
            if index >= len(queue):
                if load_more is None:
                    break
                more_items = load_more()
                if not more_items:
                    break
                queue.extend(more_items)
            item = queue[index]
            current_item = dict(item)
            while True:
                print(
                    json.dumps(
                        {
                            "index": index + 1,
                            f"{queue_name}_count": len(queue),
                            "project_key": current_item.get("project_key", ""),
                            "name": current_item.get("name", ""),
                            "tracking_state": current_item.get("tracking_state", ""),
                            "category": current_item.get("category", ""),
                            "service_status": current_item.get("service_status", ""),
                            "atproto_contribution_level": current_item.get("atproto_contribution_level", "unknown"),
                            "atproto_contribution_score": int(current_item.get("atproto_contribution_score", 0) or 0),
                            "first_seen_at": current_item.get("first_seen_at", ""),
                            "last_seen_at": current_item.get("last_seen_at", ""),
                            "seed_type": current_item.get("seed_type", ""),
                            "seed_value": current_item.get("seed_value", ""),
                            "nsids": current_item.get("nsids", []),
                            "website_url": current_item.get("website_url", ""),
                            "repo_urls": current_item.get("repo_urls", []),
                            "tagline": current_item.get("tagline", ""),
                            "summary": current_item.get("summary", ""),
                            "atproto_usage": current_item.get("atproto_usage", {}),
                            "last_promotion_deferral_reason": current_item.get("last_promotion_deferral_reason", ""),
                            "signals": current_item.get("signals", []),
                            "suggested_commands": current_item.get("suggested_commands", []),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                signal_kinds = {str(signal.get("kind") or "") for signal in current_item.get("signals", []) if isinstance(signal, dict)}
                actions = ["[Enter]=next", "j=full json", "q=quit"]
                if queue_name == "review":
                    actions.append("e=enrich")
                    actions.append("k=tracking-state")
                    actions.append("x=dismiss signal")
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    project_hints = ensure_project_shape(project) if project is not None else None
                    service_candidates = project_service_hint_urls(project_hints) if project_hints is not None else []
                    repo_candidates = project_repo_hint_urls(project_hints) if project_hints is not None else []
                    service_verification_urls = unique_list(
                        (([project_hints.get("website_url")] if project_hints and project_hints.get("website_url") else [])
                         + (list(project_hints.get("service_urls", [])) if project_hints else [])
                         + service_candidates)
                    )
                    if signal_kinds & {
                        "llm_manual_review_recommended",
                        "high_information_density",
                        "stale_metadata_with_recent_activity",
                        "service_status_unverified_but_promising",
                        "external_lexicon_dependency_needs_review",
                        "category_recheck_needed",
                    }:
                        actions.append("E=deep enrich")
                    if service_candidates:
                        actions.append("c=confirm service")
                        actions.append("C=reject service")
                    if service_verification_urls:
                        actions.append("b=browser verify")
                    if repo_candidates:
                        actions.append("r=confirm repo")
                        actions.append("R=reject repo")
                elif queue_name == "promotion":
                    actions.append("p=pick")
                    actions.append("h=hold 7d")
                    actions.append("k=tracking-state")
                print(", ".join(actions), file=sys.stderr)
                try:
                    answer = input().strip()
                except EOFError:
                    stop_interactive = True
                    break
                if answer == "":
                    break
                lowered = answer.lower()
                if lowered == "q":
                    stop_interactive = True
                    break
                if lowered == "j":
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    if project is not None:
                        print(json.dumps(runtime_view.sanitize_runtime_snapshot(project), ensure_ascii=False, indent=2, sort_keys=True))
                    continue
                if queue_name == "review" and lowered == "k":
                    print("[a]ctive/[s]table/s[t]alled/[i]gnore/[Enter]=cancel", file=sys.stderr)
                    try:
                        state_answer = input().strip().lower()
                    except EOFError:
                        return 0
                    state_map = {
                        "a": "active",
                        "active": "active",
                        "s": "stable",
                        "stable": "stable",
                        "t": "stalled",
                        "stalled": "stalled",
                        "i": "ignore",
                        "ignore": "ignore",
                    }
                    next_state = state_map.get(state_answer)
                    if next_state:
                        updated = update_project_tracking_state_value(
                            conn,
                            str(current_item.get("project_key") or ""),
                            next_state,
                        )
                        print(
                            json.dumps(
                                {
                                    "updated_tracking_state": next_state,
                                    "project_key": current_item.get("project_key", ""),
                                },
                                ensure_ascii=False,
                            )
                        )
                        refreshed = build_review_queue_item(conn, str(current_item.get("project_key") or ""), kinds=kinds)
                        if refreshed is None:
                            break
                        current_item = refreshed
                    continue
                if queue_name == "promotion" and lowered == "k":
                    print("[a]ctive/[s]table/s[t]alled/[i]gnore/[Enter]=cancel", file=sys.stderr)
                    try:
                        state_answer = input().strip().lower()
                    except EOFError:
                        break
                    state_map = {
                        "a": "active",
                        "active": "active",
                        "s": "stable",
                        "stable": "stable",
                        "t": "stalled",
                        "stalled": "stalled",
                        "i": "ignore",
                        "ignore": "ignore",
                    }
                    next_state = state_map.get(state_answer)
                    if next_state:
                        updated = update_project_tracking_state_value(
                            conn,
                            str(current_item.get("project_key") or ""),
                            next_state,
                        )
                        print(
                            json.dumps(
                                {
                                    "updated_tracking_state": next_state,
                                    "project_key": current_item.get("project_key", ""),
                                },
                                ensure_ascii=False,
                            )
                        )
                        refreshed = build_showcase_queue_item(
                            conn,
                            str(current_item.get("project_key") or ""),
                            kinds=kinds,
                            include_personal_tools=include_personal_tools,
                            view=promotion_view,
                        )
                        if refreshed is None:
                            break
                        current_item = refreshed
                    continue
                if queue_name == "promotion" and lowered == "p":
                    picked_items[str(current_item.get("project_key") or "")] = dict(current_item)
                    print(
                        json.dumps(
                            {
                                "picked_for_introduction": True,
                                "project_key": current_item.get("project_key", ""),
                            },
                            ensure_ascii=False,
                        )
                    )
                    break
                if queue_name == "promotion" and lowered == "h":
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    if project is None:
                        print(json.dumps({"warning": "project_not_found"}, ensure_ascii=False))
                        continue
                    previous_reason = latest_promotion_deferral_reason(project)
                    prompt = "Hold reason"
                    if previous_reason:
                        prompt += f" [Enter=reuse: {previous_reason}]"
                    else:
                        prompt += " [required]"
                    print(prompt, file=sys.stderr)
                    try:
                        reason = input().strip()
                    except EOFError:
                        stop_interactive = True
                        break
                    if not reason and not previous_reason:
                        print(json.dumps({"warning": "promotion_deferral_reason_required"}, ensure_ascii=False))
                        continue
                    entry = defer_promotion_on_project(project, reason=reason)
                    save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
                    conn.commit()
                    print(
                        json.dumps(
                            {
                                "promotion_deferred": True,
                                "project_key": current_item.get("project_key", ""),
                                "reason": entry["reason"],
                                "expires_at": entry["expires_at"],
                            },
                            ensure_ascii=False,
                        )
                    )
                    break
                if queue_name == "review" and lowered == "e":
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    if project is not None:
                        enriched = enrich_project(conn, project, deep_investigation=False)
                        save_project(conn, enriched, enriched=True)
                        conn.commit()
                        print(
                            json.dumps(
                                {
                                    "enriched": True,
                                    "project_key": current_item.get("project_key", ""),
                                    "service_status": enriched.get("service_status", ""),
                                    "last_run_errors": enriched.get("last_run_errors", []),
                                },
                                ensure_ascii=False,
                            )
                        )
                        refreshed = build_review_queue_item(conn, str(current_item.get("project_key") or ""), kinds=kinds)
                        if refreshed is None:
                            break
                        current_item = refreshed
                    continue
                if queue_name == "review" and lowered == "b":
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    if project is not None:
                        payload = verify_service_project(conn, project, lane="browser", execute=True)
                        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
                        refreshed = build_review_queue_item(conn, str(current_item.get("project_key") or ""), kinds=kinds)
                        if refreshed is None:
                            break
                        current_item = refreshed
                    continue
                if queue_name == "review" and answer == "E" and signal_kinds & {
                    "llm_manual_review_recommended",
                    "high_information_density",
                    "stale_metadata_with_recent_activity",
                    "service_status_unverified_but_promising",
                    "external_lexicon_dependency_needs_review",
                    "category_recheck_needed",
                }:
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    if project is not None:
                        enriched = enrich_project(conn, project, deep_investigation=True)
                        save_project(conn, enriched, enriched=True)
                        conn.commit()
                        print(
                            json.dumps(
                                {
                                    "deep_enriched": True,
                                    "project_key": current_item.get("project_key", ""),
                                    "service_status": enriched.get("service_status", ""),
                                    "last_run_errors": enriched.get("last_run_errors", []),
                                },
                                ensure_ascii=False,
                            )
                        )
                        refreshed = build_review_queue_item(conn, str(current_item.get("project_key") or ""), kinds=kinds)
                        if refreshed is None:
                            break
                        current_item = refreshed
                    continue
                if queue_name == "review" and lowered == "x":
                    signal_list = [dict(signal) for signal in current_item.get("signals", []) if isinstance(signal, dict)]
                    if not signal_list:
                        continue
                    if len(signal_list) == 1:
                        dismissed_kind = str(signal_list[0].get("kind") or "")
                    else:
                        print(
                            json.dumps(
                                {
                                    "dismissible_signals": [
                                        {
                                            "index": idx,
                                            "kind": str(signal.get("kind") or ""),
                                            "priority": int(signal.get("priority", 0) or 0),
                                            "details": signal.get("details", {}),
                                        }
                                        for idx, signal in enumerate(signal_list, start=1)
                                    ]
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                        )
                        print("dismiss which signal? [number]/[Enter]=cancel", file=sys.stderr)
                        try:
                            dismiss_answer = input().strip()
                        except EOFError:
                            return 0
                        if not dismiss_answer:
                            continue
                        try:
                            dismiss_index = int(dismiss_answer)
                        except ValueError:
                            print(json.dumps({"warning": "invalid_signal_selection", "answer": dismiss_answer}, ensure_ascii=False))
                            continue
                        if dismiss_index < 1 or dismiss_index > len(signal_list):
                            print(json.dumps({"warning": "signal_selection_out_of_range", "answer": dismiss_answer}, ensure_ascii=False))
                            continue
                        dismissed_kind = str(signal_list[dismiss_index - 1].get("kind") or "")
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    if project is not None and dismissed_kind:
                        project = ensure_project_shape(project)
                        project["dismissed_review_kinds"] = unique_list(
                            list(project.get("dismissed_review_kinds", [])) + [dismissed_kind]
                        )
                        save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
                        conn.commit()
                        print(
                            json.dumps(
                                {
                                    "dismissed_review_kind": dismissed_kind,
                                    "project_key": current_item.get("project_key", ""),
                                },
                                ensure_ascii=False,
                            )
                        )
                        refreshed = build_review_queue_item(conn, str(current_item.get("project_key") or ""), kinds=kinds)
                        if refreshed is None:
                            break
                        current_item = refreshed
                    continue
                if queue_name == "review" and lowered == "c":
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    if project is not None:
                        project = ensure_project_shape(project)
                        service_candidates = project_service_hint_urls(project)
                        if service_candidates:
                            confirmed_service = confirm_service_seed(project, service_candidates[0], source="review_queue")
                            if confirmed_service:
                                add_confirmed_identifier_decision(
                                    project,
                                    "service_url",
                                    confirmed_service,
                                    source="review_queue",
                                    note="candidate confirmed by operator",
                                )
                            project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
                            project["merge_recommendation"] = compute_merge_recommendation(project)
                            save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
                            conn.commit()
                            print(
                                json.dumps(
                                    {
                                        "confirmed_service_seed": confirmed_service or service_candidates[0],
                                        "project_key": current_item.get("project_key", ""),
                                    },
                                    ensure_ascii=False,
                                )
                            )
                            refreshed = build_review_queue_item(conn, str(current_item.get("project_key") or ""), kinds=kinds)
                            if refreshed is None:
                                break
                            current_item = refreshed
                    continue
                if queue_name == "review" and answer == "C":
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    if project is not None:
                        project = ensure_project_shape(project)
                        service_candidates = project_service_hint_urls(project)
                        if service_candidates:
                            blocked = normalize_blocked_identifiers(list(project.get("blocked_identifiers", [])))
                            blocked.append(
                                {
                                    "type": "service_url",
                                    "value": service_candidates[0],
                                    "note": "rejected from review-queue interactive",
                                    "added_at": iso_now(),
                                }
                            )
                            project["blocked_identifiers"] = blocked
                            project = finalize_project_shape(project)
                            project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
                            project["merge_recommendation"] = compute_merge_recommendation(project)
                            save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
                            conn.commit()
                            print(json.dumps({"rejected_service_candidate": service_candidates[0], "project_key": current_item.get("project_key", "")}, ensure_ascii=False))
                            refreshed = build_review_queue_item(conn, str(current_item.get("project_key") or ""), kinds=kinds)
                            if refreshed is None:
                                break
                            current_item = refreshed
                    continue
                if queue_name == "review" and lowered == "r":
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    if project is not None:
                        project = ensure_project_shape(project)
                        repo_candidates = project_repo_hint_urls(project)
                        if repo_candidates:
                            confirmed_repo = confirm_repo_seed(project, repo_candidates[0], source="review_queue")
                            if confirmed_repo:
                                add_confirmed_identifier_decision(
                                    project,
                                    "repo_url",
                                    confirmed_repo,
                                    source="review_queue",
                                    note="candidate confirmed by operator",
                                )
                            project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
                            project["merge_recommendation"] = compute_merge_recommendation(project)
                            save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
                            conn.commit()
                            print(
                                json.dumps(
                                    {
                                        "confirmed_repo_seed": confirmed_repo or repo_candidates[0],
                                        "project_key": current_item.get("project_key", ""),
                                    },
                                    ensure_ascii=False,
                                )
                            )
                            refreshed = build_review_queue_item(conn, str(current_item.get("project_key") or ""), kinds=kinds)
                            if refreshed is None:
                                break
                            current_item = refreshed
                    continue
                if queue_name == "review" and answer == "R":
                    project = fetch_project(conn, str(current_item.get("project_key") or ""))
                    if project is not None:
                        project = ensure_project_shape(project)
                        repo_candidates = project_repo_hint_urls(project)
                        if repo_candidates:
                            blocked = normalize_blocked_identifiers(list(project.get("blocked_identifiers", [])))
                            blocked.append(
                                {
                                    "type": "repo_url",
                                    "value": repo_candidates[0],
                                    "note": "rejected from review-queue interactive",
                                    "added_at": iso_now(),
                                }
                            )
                            project["blocked_identifiers"] = blocked
                            project = finalize_project_shape(project)
                            project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
                            project["merge_recommendation"] = compute_merge_recommendation(project)
                            save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
                            conn.commit()
                            print(json.dumps({"rejected_repo_candidate": repo_candidates[0], "project_key": current_item.get("project_key", "")}, ensure_ascii=False))
                            refreshed = build_review_queue_item(conn, str(current_item.get("project_key") or ""), kinds=kinds)
                            if refreshed is None:
                                break
                            current_item = refreshed
                    continue
                print(json.dumps({"warning": "unknown_or_unavailable_action", "answer": answer}, ensure_ascii=False))
            index += 1
        if queue_name == "promotion":
            print(
                json.dumps(
                    {
                        "queue_name": queue_name,
                        "tracking_states": sorted(tracking_states),
                        "kinds": sorted(kinds) if kinds else [],
                        "picked_count": len(picked_items),
                        "picked_items": list(picked_items.values()),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        return 0
    print(json.dumps({
        "queue_name": queue_name,
        "tracking_states": sorted(tracking_states),
        "kinds": sorted(kinds) if kinds else [],
        f"{queue_name}_count": len(queue),
        "kind_counts": kind_counts,
        "items": queue,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
