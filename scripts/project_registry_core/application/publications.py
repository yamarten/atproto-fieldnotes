"""Bounded publication sync; network work never owns a registry write lock."""
from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from typing import Any, Callable

from .. import lexicon_publication_catalog as catalog, publication_registry as storage, identifiers, publication_verification
from ..lexicon_publication_comparison import fetch_snapshot


def _preview_catalog(conn: sqlite3.Connection) -> sqlite3.Connection:
    preview = sqlite3.connect(":memory:")
    preview.row_factory = sqlite3.Row
    storage.install_schema(preview)
    preview.execute("CREATE TABLE registry_meta(key TEXT PRIMARY KEY,value TEXT)")
    for table in ("catalog_metadata", "pending_publication_events", "publication_catalog", "publication_verifications", "collection_inventory_members"):
        preview.execute(f"DELETE FROM {table}")
        columns = [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")]
        preview.executemany(f"INSERT INTO {table} VALUES({','.join('?' for _ in columns)})", conn.execute(f"SELECT * FROM {table}"))
    preview.executemany("INSERT INTO registry_meta VALUES(?,?)", conn.execute("SELECT key,value FROM registry_meta WHERE key LIKE 'collection_inventory_%'"))
    preview.commit()
    return preview


def verification_targets(conn: sqlite3.Connection, *, limit: int | None, now: str) -> list[str]:
    if limit is not None and limit <= 0:
        return []
    groups: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    last_authority_check: dict[str, str] = {}
    # Renew successes before the next weekly run can narrowly miss their TTL.
    refresh_before = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    for row in conn.execute("""SELECT p.schema_id,MAX(p.content_changed_revision) revision,
        MAX(v.checked_at) checked_at,MAX(v.changed_revision) verification_revision,MAX(CASE WHEN v.verified_content_hash=p.record_hash
        AND v.next_verification_at>CASE WHEN v.dns_same_did=1 AND v.current_record_refetched=1 THEN ? ELSE ? END THEN 1 ELSE 0 END) fresh,
        MAX(CASE WHEN p.spaces_json<>'[]' THEN 1 ELSE 0 END) space
        FROM publication_catalog p LEFT JOIN publication_verifications v USING(did,rkey)
        WHERE p.is_deleted=0 AND p.problems_json='[]' GROUP BY p.schema_id""", (refresh_before, now)):
        sid = str(row["schema_id"])
        authority = identifiers.derive_authority_domain(sid)
        last_authority_check[authority] = max(last_authority_check.get(authority, ""), str(row["checked_at"] or ""))
        if not row["fresh"] or int(row["revision"]) > int(row["verification_revision"] or 0):
            groups[authority].append((bool(row["checked_at"]), -int(row["space"]), str(row["checked_at"] or ""), -int(row["revision"]), sid))
    # Round-robin across authorities before taking another schema from one group.
    ranked = sorted(((last_authority_check[authority], min(items)), authority, sorted(items)) for authority, items in groups.items())
    result = []
    while ranked and (limit is None or len(result) < limit):
        remaining = []
        for rank, authority, items in ranked:
            result.append(items[0][-1])
            if limit is not None and len(result) >= limit:
                break
            if len(items) > 1:
                remaining.append((rank, authority, items[1:]))
        ranked = remaining
    return result


def verify(conn: sqlite3.Connection, *, limit: int | None = None, max_seconds: float = 3600,
           fetcher: Callable[..., dict[str, Any]] | None = None,
           cancel_requested: Callable[[], bool] | None = None,
           progress: Callable[..., None] | None = None) -> dict[str, Any]:
    from .. import registry
    selected = verification_targets(conn, limit=limit, now=catalog.iso_now())
    started = time.monotonic()
    outcomes = []
    groups: dict[str, list[str]] = defaultdict(list)
    for sid in selected:
        groups[identifiers.derive_authority_domain(sid)].append(sid)
    # An authority owns its cache and at most one in-flight request.
    # SQLite stays on the caller thread; the pool performs bounded network IO.
    caches = {authority: ({}, {}) for authority in groups}
    ready = list(groups)
    processed = 0
    stop_reason = ""

    def fetch(sid, authority, remaining):
        authority_cache, document_cache = caches[authority]
        checked_at = catalog.iso_now()
        try:
            remaining = max(0.1, min(20.0, remaining))
            options = {} if fetcher else {"authority_cache": authority_cache, "document_cache": document_cache}
            payload = (fetcher or registry.fetch_lexicon_record)(
                sid, dns_timeout=min(10.0, remaining / 3), http_timeout=remaining / 3, **options)
            return {"payload": payload, "checked_at": checked_at}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "authority_did": authority_cache.get(authority, ""), "checked_at": checked_at}

    with ThreadPoolExecutor(max_workers=4) as executor:
        active = {}
        while ready or active:
            if cancel_requested and cancel_requested():
                stop_reason = "cancelled"
            elif time.monotonic() - started >= max_seconds:
                stop_reason = "time_budget"
            while ready and len(active) < 4 and not stop_reason:
                remaining_seconds = max_seconds - (time.monotonic() - started)
                if remaining_seconds <= 0:
                    stop_reason = "time_budget"
                    break
                authority = ready.pop(0)
                sid = groups[authority].pop(0)
                expected = publication_verification.witnesses(conn, sid)
                future = executor.submit(fetch, sid, authority, remaining_seconds)
                active[future] = (sid, authority, expected)
            if not active:
                break
            completed, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in completed:
                sid, authority, expected = active.pop(future)
                outcomes.extend(publication_verification.record_result(conn, sid, expected=expected, **future.result()))
                processed += 1
                if groups[authority]:
                    ready.append(authority)
                if progress:
                    progress(processed, len(selected), {"kind": "publication_verification", "schema_id": sid})
    remaining = len(verification_targets(conn, limit=None, now=catalog.iso_now()))
    outcomes.sort(key=lambda item: (item["schema_id"], item["did"]))
    return {"targets": outcomes, "verified": sum(bool(i["verified"]) for i in outcomes),
            "failed": sum(not i["verified"] for i in outcomes), "remaining": remaining,
            "selected": len(selected), "attempted": processed, "elapsed_seconds": round(time.monotonic() - started, 3),
            "status": "partial" if remaining else "completed",
            "stop_reason": stop_reason or ("item_limit" if limit is not None and remaining else "catalog_changed" if remaining else "")}


def run(conn: sqlite3.Connection, *, execute: bool = False, create_unmatched: bool = True,
        service: str, max_events: int = 20000, max_seconds: int = 300,
        verify_limit: int | None = None, verification_seconds: int = 3600,
        snapshot_fetcher: Callable[..., dict[str, Any]] = fetch_snapshot,
        verifier: Callable[..., dict[str, Any]] | None = None,
        progress: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None) -> dict[str, Any]:
    working = conn if execute else _preview_catalog(conn)
    try:
        state = catalog.catalog_state(working)
        if cancel_requested and cancel_requested():
            return {"cancelled": True}
        snapshot = snapshot_fetcher(service=service,
            after_seq=int(state["pending_after_seq"] if state["pending_after_seq"] is not None else state["watermark_seq"]),
            sealed_tip_seq=state["pending_sealed_tip_seq"], max_events=max_events, max_seconds=max_seconds)
        events = snapshot.pop("events")
        ingestion = catalog.ingest_segment(working, events, snapshot)
        if progress:
            progress(1, 2, {"kind": "source_page", "source": "lexicons", "entry_count": len(events)})
        if ingestion["status"] != "applied":
            return {"operation_status": "partial", "ingestion": ingestion, "created": 0, "updated": 0}
        if cancel_requested and cancel_requested():
            return {"cancelled": True, "ingestion": ingestion}
        verification = verify(working, limit=verify_limit, max_seconds=verification_seconds, fetcher=verifier, cancel_requested=cancel_requested, progress=progress)
        if cancel_requested and cancel_requested():
            return {"cancelled": True, "ingestion": ingestion, "verification": verification}
        from ..concurrency import write_transaction, read_snapshot
        with (write_transaction(conn) if execute else read_snapshot(conn)):
            projection = storage.schemas(working, now=catalog.iso_now())
            imported = storage.import_projects(conn, projection, execute=execute, create_unmatched=create_unmatched)
            if execute:
                conn.execute("INSERT OR REPLACE INTO registry_meta VALUES('publication_sync_result',?)", (json.dumps({"ingestion": ingestion, "verification": verification, "created": imported["created"], "updated": imported["updated"]}),))
        if progress:
            progress(2, 2, {"kind": "source_items", "source": "lexicons", "entry_count": len(projection)})
        return {**imported, "source": "lexicon_publication", "operation_status": "completed", "ingestion": ingestion, "verification": verification}
    finally:
        if working is not conn:
            working.close()
