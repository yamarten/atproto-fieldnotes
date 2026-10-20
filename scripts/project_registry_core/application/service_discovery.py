"""Bounded, resumable investigation of unadopted service URLs."""
from __future__ import annotations

import collections
import copy
import time
import uuid
from typing import Any, Callable

from .. import project_record, registry, service, service_discovery as policy, operator_feedback
from ..persistence import concurrency, storage


def attribution_conflict(conn: Any, key: str, url: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM project_identifiers WHERE kind IN ('service_url','site_url') "
        "AND value=? AND classification='confirmed' AND project_key!=?", (url, key)).fetchone()
        or conn.execute("SELECT 1 FROM project_noncurrent_identifiers WHERE project_key=? "
        "AND kind IN ('service_url','site_url') AND value=?", (key, url)).fetchone()
        or registry.competing_repository_homepages(conn, key, [url]))


def select(conn: Any, *, now: str = "", limit: int = 50) -> dict[str, Any]:
    if not 1 <= limit <= 1000:
        raise ValueError("discovery limit must be 1..1000")
    current_time = project_record.parse_iso(now or project_record.iso_now())
    if current_time is None:
        raise ValueError("invalid investigation selection time")
    items = []
    excluded = collections.Counter()
    with concurrency.read_snapshot(conn):
        confirmed = collections.defaultdict(set)
        for row in conn.execute("SELECT project_key,value FROM project_identifiers "
                                "WHERE kind IN ('service_url','site_url') AND classification='confirmed'"):
            confirmed[row["value"]].add(row["project_key"])
        noncurrent = {(row["project_key"], row["value"]) for row in conn.execute(
            "SELECT project_key,value FROM project_noncurrent_identifiers WHERE kind IN ('service_url','site_url')")}
        keys = [row[0] for row in conn.execute("SELECT project_key FROM projects "
                    "WHERE tracking_state IN ('active','stable','stalled','out_of_scope') ORDER BY first_seen_at,project_key")]
        for key in keys:
            project = project_record.fetch_project(conn, key)
            previous = {value["url"]: value for value in project.get(policy.STATE_FIELD, []) if value.get("url")}
            hypotheses = policy.hypotheses(project)
            for url, item in hypotheses.items():
                if (key, url) in noncurrent or confirmed[url] - {key}:
                    excluded["ownership_conflict_or_noncurrent"] += 1
                    continue
                prior = previous.get(url, {})
                items.append({**item, "project_key": key,
                              "due": policy.due(item, prior, current_time),
                              "retry": prior.get("fingerprint") == item["fingerprint"],
                              "waiting_since": prior.get("attempted_at") or project.get("first_seen_at", ""),
                              "has_service": bool(project.get("service_urls")),
                              "planned": bool(operator_feedback.active_plan(project)),
                              "last_attempt_at": prior.get("attempted_at", "")})
    owners = collections.defaultdict(set)
    references = collections.defaultdict(set)
    for item in items:
        references[item["url"]].add(item["project_key"])
        if any(source["kind"] in {"repository", "authority"} for source in item["sources"]):
            owners[item["url"]].add(item["project_key"])
    eligible = []
    for item in items:
        if len(owners[item["url"]]) > 1:
            excluded["competing_attribution_claims"] += 1
        elif (len(references[item["url"]]) > 1 and item["group"] == "candidate" and not item["planned"]):
            excluded["shared_unattributed_candidate"] += 1
        else:
            eligible.append(item)
    due = [item for item in eligible if item["due"]]
    queues = collections.defaultdict(collections.deque)
    for item in sorted(due, key=lambda item: (not item["planned"], item["has_service"],
                                             item["waiting_since"], item["project_key"], item["url"])):
        queues[(item["group"], item["retry"])].append(item)
    selected, projects, urls = [], set(), set()
    for item in sorted((item for item in due if item["planned"]),
                       key=lambda item: (item["waiting_since"], item["project_key"], item["url"])):
        if len(selected) >= limit:
            break
        if item["project_key"] not in projects and item["url"] not in urls:
            selected.append(item)
            projects.add(item["project_key"])
            urls.add(item["url"])
    # Four first/material-change turns then one retry turn; rotate source groups.
    while any(queues.values()) and len(selected) < limit:
        for retry in (False, False, False, False, True):
            for group in ("candidate", "repository", "authority"):
                queue = queues[(group, retry)]
                while queue:
                    item = queue.popleft()
                    if item["project_key"] not in projects and item["url"] not in urls:
                        selected.append(item)
                        projects.add(item["project_key"])
                        urls.add(item["url"])
                        break
                if len(selected) == limit:
                    break
            if len(selected) == limit:
                break
    return {"eligible_count": len(eligible), "due_count": len(due), "selected": selected,
            "excluded_counts": dict(excluded)}


def investigate(
    conn: Any, *, execute: bool = False, limit: int = 50, budget_seconds: float = 900,
    now: str = "", progress: Any = None, cancel_requested: Any = None,
    navigate: Callable[..., Any] | None = None, clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if not 1 <= budget_seconds <= 14400:
        raise ValueError("discovery budget seconds must be 1..14400")
    started = clock()
    selection = select(conn, now=now, limit=limit)
    results = []
    stop_reason = "selection_exhausted"
    if (execute and selection["selected"] and navigate is None
            and not (cancel_requested and cancel_requested()) and clock() - started < budget_seconds):
        service.ensure_browser_available()
    for item in selection["selected"] if execute else []:
        if cancel_requested and cancel_requested():
            stop_reason = "cancelled"
            break
        if clock() - started >= budget_seconds:
            stop_reason = "time_budget"
            break
        # Recheck this claim, not the entire registry, at the mutation boundary.
        project = project_record.fetch_project(conn, item["project_key"])
        current = policy.hypotheses(project).get(item["url"]) if project else None
        if (not current or current["fingerprint"] != item["fingerprint"]
                or project.get("tracking_state") not in policy.TRACKING_STATES
                or attribution_conflict(conn, item["project_key"], item["url"])):
            results.append({"project_key": item["project_key"], "url": item["url"], "status": "skipped"})
            continue
        original = copy.deepcopy(project)
        observed_at = now or project_record.iso_now()
        deadline = min(started + budget_seconds, clock() + 45)
        def bounded_navigate(url: str) -> dict[str, Any]:
            if cancel_requested and cancel_requested():
                raise InterruptedError("browser investigation cancelled")
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError("browser investigation URL budget exhausted")
            return (navigate or service.playwright_navigate)(url, timeout_ms=max(1, int(min(30, remaining) * 1000)))
        attempt_run_id = f"inv:{uuid.uuid4().hex}"
        bundle = service.collect_browser_service_evidence(project, item["url"], service.BrowserDependencies(
            navigate=bounded_navigate, now=lambda: observed_at), lane=policy.LANE,
            hypothesis={**current, "run_id": attempt_run_id})
        if cancel_requested and cancel_requested():
            stop_reason = "cancelled"
            break
        registry.apply_service_evidence(project, bundle)
        # Investigation does not run unrelated ownership/status normalization.
        policy.reconcile(project)
        run_id = storage.stage_investigation_run(project, project_key=item["project_key"],
            run_id=attempt_run_id,
            started_at=observed_at, completed_at=now or project_record.iso_now(),
            input_project=original, output_project=project, bundle=bundle)
        try:
            with concurrency.write_transaction(conn):
                if attribution_conflict(conn, item["project_key"], item["url"]):
                    raise project_record.StaleProjectWriteError("service attribution changed during investigation", project_key=item["project_key"])
                project_record.save_project(conn, project, enriched=bool(original.get("last_enriched_at")), preserve_timestamps=True)
            attempt = next((value for value in project.get(policy.STATE_FIELD, []) if value["url"] == item["url"]), {})
            results.append({"project_key": item["project_key"], "url": item["url"], "run_id": run_id,
                            "status": attempt.get("status", "skipped"),
                            "candidate_supported": policy.supports_candidate(project, item["url"])})
        except project_record.StaleProjectWriteError as exc:
            conn.rollback()
            storage.save_conflicted_investigation(conn, project, exc)
            results.append({"project_key": item["project_key"], "url": item["url"],
                            "run_id": run_id, "status": "conflicted"})
        if progress:
            progress(len(results), len(selection["selected"]), {"unit": "unadopted_url", **results[-1]})
    if len(results) == limit and selection["due_count"] > limit:
        stop_reason = "url_budget"
    statuses = collections.Counter(item["status"] for item in results)
    return {**selection, "mode": "execute" if execute else "dry_run", "limit": limit,
            "budget_seconds": budget_seconds, "elapsed_seconds": round(clock() - started, 3),
            "processed_count": sum(statuses[s] for s in ("observed", "failed")),
            "observed_count": statuses["observed"], "failed_count": statuses["failed"], "conflicted_count": statuses["conflicted"],
            "remaining_due_count": max(0, selection["due_count"] - sum(statuses[s] for s in ("observed", "failed"))),
            "stop_reason": stop_reason, "results": results,
            "operation_status": "cancelled" if stop_reason == "cancelled" else
                                "retry_wait" if statuses["conflicted"] else "partial" if statuses["failed"] else "completed"}
