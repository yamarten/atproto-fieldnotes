from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from . import identifiers as identifier_core
from .lexicon_publication_comparison import inspect_schema, normalize_event


CATALOG_SCHEMA_VERSION = 3
MAX_RECORD_MATERIAL_BYTES = 65536


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bounded_record_material(record: dict[str, Any]) -> tuple[str, bool]:
    rendered = canonical_json(record)
    if len(rendered.encode("utf-8")) <= MAX_RECORD_MATERIAL_BYTES:
        return rendered, False
    summary = {
        "$type": record.get("$type"),
        "id": record.get("id"),
        "lexicon": record.get("lexicon"),
        "description": str(record.get("description") or "")[:1000],
        "def_names": sorted(str(key) for key in (record.get("defs") or {}) if isinstance(record.get("defs"), dict))[:256],
        "truncated": True,
    }
    return canonical_json(summary), True


def install_catalog_schema(conn: sqlite3.Connection) -> None:
    """Install publication storage inside the caller's explicit transaction."""
    statements = """
        CREATE TABLE IF NOT EXISTS catalog_metadata(
          singleton INTEGER PRIMARY KEY CHECK(singleton=1),
          schema_version INTEGER NOT NULL,
          watermark_seq INTEGER NOT NULL DEFAULT 0,
          catalog_revision INTEGER NOT NULL DEFAULT 0,
          report_revision INTEGER NOT NULL DEFAULT 0,
          pending_sealed_tip_seq INTEGER,
          pending_after_seq INTEGER,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_publication_events(
          seq INTEGER NOT NULL,
          did TEXT NOT NULL,
          rkey TEXT NOT NULL,
          event_json TEXT NOT NULL,
          PRIMARY KEY(seq, did, rkey)
        );
        CREATE TABLE IF NOT EXISTS publication_catalog(
          did TEXT NOT NULL,
          rkey TEXT NOT NULL,
          is_deleted INTEGER NOT NULL,
          last_operation TEXT NOT NULL,
          seq INTEGER NOT NULL,
          rev TEXT NOT NULL,
          event_time TEXT NOT NULL,
          schema_id TEXT NOT NULL,
          authority_group TEXT NOT NULL,
          record_type TEXT NOT NULL,
          lexicon_version_json TEXT NOT NULL,
          definition_types_json TEXT NOT NULL,
          spaces_json TEXT NOT NULL,
          problems_json TEXT NOT NULL,
          record_hash TEXT NOT NULL,
          record_material_json TEXT NOT NULL,
          record_material_truncated INTEGER NOT NULL,
          first_seen_revision INTEGER NOT NULL,
          content_changed_revision INTEGER NOT NULL,
          PRIMARY KEY(did, rkey)
        );
        CREATE INDEX IF NOT EXISTS publication_catalog_schema_id
          ON publication_catalog(schema_id, is_deleted, did, rkey);
        CREATE TABLE IF NOT EXISTS publication_verifications(
          did TEXT NOT NULL,
          rkey TEXT NOT NULL,
          dns_same_did INTEGER,
          current_record_refetched INTEGER,
          reason TEXT NOT NULL,
          retryable INTEGER NOT NULL,
          checked_at TEXT NOT NULL,
          next_verification_at TEXT NOT NULL,
          authority_did TEXT NOT NULL,
          record_uri TEXT NOT NULL,
          refetched_record_hash TEXT NOT NULL,
          verified_content_hash TEXT NOT NULL,
          changed_revision INTEGER NOT NULL,
          PRIMARY KEY(did, rkey),
          FOREIGN KEY(did, rkey) REFERENCES publication_catalog(did, rkey) ON DELETE CASCADE
        );
    """
    for statement in statements.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("INSERT OR IGNORE INTO catalog_metadata(singleton,schema_version,updated_at) VALUES(1,?,?)", (CATALOG_SCHEMA_VERSION, ""))


def catalog_state(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM catalog_metadata WHERE singleton=1").fetchone()
    if row is None:
        raise RuntimeError("publication catalog is not initialized")
    return dict(row)


def _publication_values(event: dict[str, Any], revision: int, previous: sqlite3.Row | None) -> tuple[Any, ...]:
    operation = str(event["operation"])
    if operation == "delete":
        inspected = {
            "id": str(previous["schema_id"] if previous else ""),
            "authority_group": str(previous["authority_group"] if previous else ""),
            "record_type": str(previous["record_type"] if previous else ""),
            "lexicon": json.loads(str(previous["lexicon_version_json"])) if previous else None,
            "definition_types": json.loads(str(previous["definition_types_json"])) if previous else [],
            "spaces": json.loads(str(previous["spaces_json"])) if previous else [],
            "problems": json.loads(str(previous["problems_json"])) if previous else [],
        }
        record_hash = str(previous["record_hash"] if previous else "")
        material = str(previous["record_material_json"] if previous else "{}")
        truncated = int(previous["record_material_truncated"] if previous else 0)
    else:
        record = dict(event["record"])
        inspected = inspect_schema(event)
        record_hash = json_hash(record)
        material, was_truncated = bounded_record_material(record)
        truncated = int(was_truncated)
    first_revision = int(previous["first_seen_revision"]) if previous else revision
    return (
        event["did"], event["rkey"], int(operation == "delete"), operation,
        event["seq"], event.get("rev", ""), event.get("time", ""), inspected["id"],
        inspected["authority_group"], inspected["record_type"], canonical_json(inspected["lexicon"]),
        canonical_json(inspected["definition_types"]), canonical_json(inspected["spaces"]),
        canonical_json(inspected["problems"]), record_hash, material, truncated, first_revision, revision,
    )


def _apply_pending_events(conn: sqlite3.Connection, revision: int) -> dict[str, int]:
    counts = {"new": 0, "updated": 0, "deleted": 0, "stale_or_duplicate": 0}
    for staged in conn.execute("SELECT event_json FROM pending_publication_events ORDER BY seq, did, rkey"):
        event = json.loads(str(staged[0]))
        previous = conn.execute(
            "SELECT * FROM publication_catalog WHERE did=? AND rkey=?", (event["did"], event["rkey"])
        ).fetchone()
        if previous is not None and int(event["seq"]) <= int(previous["seq"]):
            counts["stale_or_duplicate"] += 1
            continue
        values = _publication_values(event, revision, previous)
        conn.execute("""
          INSERT INTO publication_catalog(
            did,rkey,is_deleted,last_operation,seq,rev,event_time,schema_id,authority_group,
            record_type,lexicon_version_json,definition_types_json,spaces_json,problems_json,
            record_hash,record_material_json,record_material_truncated,first_seen_revision,content_changed_revision
          ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(did,rkey) DO UPDATE SET
            is_deleted=excluded.is_deleted,last_operation=excluded.last_operation,seq=excluded.seq,
            rev=excluded.rev,event_time=excluded.event_time,schema_id=excluded.schema_id,
            authority_group=excluded.authority_group,record_type=excluded.record_type,
            lexicon_version_json=excluded.lexicon_version_json,
            definition_types_json=excluded.definition_types_json,spaces_json=excluded.spaces_json,
            problems_json=excluded.problems_json,record_hash=excluded.record_hash,
            record_material_json=excluded.record_material_json,
            record_material_truncated=excluded.record_material_truncated,
            content_changed_revision=excluded.content_changed_revision
        """, values)
        if event["operation"] == "delete":
            counts["deleted"] += 1
        elif previous is None:
            counts["new"] += 1
        else:
            counts["updated"] += 1
    return counts


def ingest_segment(
    conn: sqlite3.Connection,
    events: Iterable[Any],
    summary: dict[str, Any],
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    state = catalog_state(conn)
    after_seq = int(summary.get("after_seq") or 0)
    sealed_tip = int(summary.get("sealed_tip_seq") or 0)
    last_event_seq = int(summary.get("last_event_seq") or after_seq)
    truncated = bool(summary.get("truncated"))
    expected_after = int(state["pending_after_seq"] if state["pending_after_seq"] is not None else state["watermark_seq"])
    pending_tip = state["pending_sealed_tip_seq"]
    if after_seq != expected_after:
        raise ValueError(f"snapshot gap or overlap: expected after_seq {expected_after}, got {after_seq}")
    if pending_tip is not None and sealed_tip != int(pending_tip):
        raise ValueError(f"cannot mix sealed tips {pending_tip} and {sealed_tip}")
    if sealed_tip < after_seq or last_event_seq < after_seq or last_event_seq > sealed_tip:
        raise ValueError("invalid snapshot sequence bounds")
    normalized: list[dict[str, Any]] = []
    invalid: dict[str, int] = defaultdict(int)
    for value in events:
        event, error = normalize_event(value)
        if event is None:
            invalid[str(error or "invalid_event")] += 1
            continue
        if int(event["seq"]) <= after_seq:
            continue
        if int(event["seq"]) > sealed_tip:
            raise ValueError("event exceeds sealed tip")
        normalized.append(event)
    if invalid:
        raise ValueError(f"invalid publication events: {dict(sorted(invalid.items()))}")
    maximum = max((int(item["seq"]) for item in normalized), default=after_seq)
    if maximum != last_event_seq:
        raise ValueError(f"summary last_event_seq {last_event_seq} does not match event maximum {maximum}")
    now = observed_at or iso_now()
    from .concurrency import write_transaction
    with write_transaction(conn):
        if catalog_state(conn) != state:
            raise ValueError("publication catalog changed during fetch; retry")
        if pending_tip is None:
            conn.execute(
                "UPDATE catalog_metadata SET pending_sealed_tip_seq=?, pending_after_seq=?, updated_at=? WHERE singleton=1",
                (sealed_tip, after_seq, now),
            )
        for event in normalized:
            rendered = canonical_json(event)
            existing = conn.execute(
                "SELECT event_json FROM pending_publication_events WHERE seq=? AND did=? AND rkey=?",
                (event["seq"], event["did"], event["rkey"]),
            ).fetchone()
            if existing is not None and str(existing[0]) != rendered:
                raise ValueError("conflicting duplicate event in pending snapshot")
            conn.execute(
                "INSERT OR IGNORE INTO pending_publication_events(seq,did,rkey,event_json) VALUES(?,?,?,?)",
                (event["seq"], event["did"], event["rkey"], rendered),
            )
        conn.execute("UPDATE catalog_metadata SET pending_after_seq=?, updated_at=? WHERE singleton=1", (last_event_seq, now))
        if truncated:
            return {
                "status": "staged", "sealed_tip_seq": sealed_tip, "resume_after_seq": last_event_seq,
                "watermark_seq": int(state["watermark_seq"]), "staged_event_count": len(normalized),
            }
        revision = int(state["catalog_revision"]) + 1
        counts = _apply_pending_events(conn, revision)
        conn.execute("DELETE FROM pending_publication_events")
        conn.execute("""
          UPDATE catalog_metadata SET watermark_seq=?, catalog_revision=?,
            pending_sealed_tip_seq=NULL, pending_after_seq=NULL, updated_at=? WHERE singleton=1
        """, (sealed_tip, revision, now))
    return {"status": "applied", "sealed_tip_seq": sealed_tip, "watermark_seq": sealed_tip, "catalog_revision": revision, **counts}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify_due_publications(
    conn: sqlite3.Connection,
    *,
    limit: int,
    schema_ids: Iterable[str] | None = None,
    dids: Iterable[str] | None = None,
    publication_keys: Iterable[tuple[str, str]] | None = None,
    force: bool = False,
    now: str | None = None,
    ttl_hours: int = 168,
    retry_hours: int = 24,
    dns_timeout: float = 10.0,
    http_timeout: float = 20.0,
    resolver: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    checked_at = now or iso_now()
    normalized_schema_ids = sorted({identifier_core.normalize_nsid(value) for value in (schema_ids or [])})
    invalid_schema_ids = [value for value in normalized_schema_ids if not identifier_core.is_valid_nsid(value)]
    if invalid_schema_ids:
        raise ValueError(f"invalid target schema ID: {invalid_schema_ids[0]}")
    normalized_dids = sorted({str(value).strip() for value in (dids or [])})
    if any(not value for value in normalized_dids):
        raise ValueError("target DID must not be empty")
    target_predicates = []
    parameters: list[Any] = [int(force), checked_at]
    if normalized_schema_ids:
        target_predicates.append(f"p.schema_id IN ({','.join('?' for _ in normalized_schema_ids)})")
        parameters.extend(normalized_schema_ids)
    if normalized_dids:
        target_predicates.append(f"p.did IN ({','.join('?' for _ in normalized_dids)})")
        parameters.extend(normalized_dids)
    normalized_keys = sorted({(str(did).strip(), str(rkey).strip()) for did, rkey in (publication_keys or [])})
    if normalized_keys:
        target_predicates.append(f"(p.did,p.rkey) IN ({','.join('(?,?)' for _ in normalized_keys)})")
        for did, rkey in normalized_keys:
            parameters.extend((did, rkey))
    target_where = f" AND {' AND '.join(target_predicates)}" if target_predicates else ""
    parameters.append(max(0, limit))
    rows = conn.execute(f"""
      SELECT p.*, v.checked_at, v.next_verification_at, v.verified_content_hash
      FROM publication_catalog p
      LEFT JOIN publication_verifications v USING(did,rkey)
      WHERE p.is_deleted=0 AND p.schema_id<>'' AND p.problems_json='[]'
        AND (? OR v.did IS NULL OR v.verified_content_hash<>p.record_hash OR v.next_verification_at<=?)
        {target_where}
      ORDER BY CASE WHEN v.did IS NULL THEN 0 WHEN v.verified_content_hash<>p.record_hash THEN 1 ELSE 2 END,
        p.schema_id, p.did, p.rkey
      LIMIT ?
    """, parameters).fetchall()
    if not rows:
        return {"eligible_checked": 0, "verified": 0, "failed": 0, "targets": []}
    state = catalog_state(conn)
    revision = int(state["catalog_revision"]) + 1
    verified = 0
    targets: list[dict[str, Any]] = []
    from .concurrency import write_transaction
    with write_transaction(conn):
        if catalog_state(conn) != state:
            raise ValueError("publication catalog changed during verification; retry")
        for row in rows:
            try:
                result = resolver(str(row["schema_id"]), str(row["did"]), dns_timeout=dns_timeout, http_timeout=http_timeout)
            except Exception as exc:  # network/protocol failures are retained for retry
                result = {
                    "dns_same_did": None, "current_record_refetched": False,
                    "reason": f"{type(exc).__name__}: {str(exc)[:240]}", "retryable": True,
                    "authority_did": "", "record_uri": "", "record_hash": "",
                }
            if result.get("current_record_refetched") is True and str(result.get("record_hash") or "") != str(row["record_hash"]):
                result = dict(result)
                result["current_record_refetched"] = False
                result["reason"] = "current_record_hash_mismatch"
                result["retryable"] = True
            is_verified = bool(result.get("dns_same_did") is True and result.get("current_record_refetched") is True)
            verified += int(is_verified)
            targets.append({
                "did": str(row["did"]), "rkey": str(row["rkey"]), "schema_id": str(row["schema_id"]),
                "verified": is_verified, "reason": str(result.get("reason") or ""),
            })
            hours = ttl_hours if is_verified else retry_hours
            next_at = (_parse_time(checked_at) + timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            conn.execute("""
              INSERT INTO publication_verifications(
                did,rkey,dns_same_did,current_record_refetched,reason,retryable,checked_at,
                next_verification_at,authority_did,record_uri,refetched_record_hash,
                verified_content_hash,changed_revision
              ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(did,rkey) DO UPDATE SET
                dns_same_did=excluded.dns_same_did,current_record_refetched=excluded.current_record_refetched,
                reason=excluded.reason,retryable=excluded.retryable,checked_at=excluded.checked_at,
                next_verification_at=excluded.next_verification_at,authority_did=excluded.authority_did,
                record_uri=excluded.record_uri,refetched_record_hash=excluded.refetched_record_hash,
                verified_content_hash=excluded.verified_content_hash,changed_revision=excluded.changed_revision
            """, (
                row["did"], row["rkey"], result.get("dns_same_did"), int(bool(result.get("current_record_refetched"))),
                str(result.get("reason") or ""), int(bool(result.get("retryable"))), checked_at, next_at,
                str(result.get("authority_did") or ""), str(result.get("record_uri") or ""),
                str(result.get("record_hash") or ""), row["record_hash"], revision,
            ))
        conn.execute("UPDATE catalog_metadata SET catalog_revision=?, updated_at=? WHERE singleton=1", (revision, checked_at))
    return {
        "eligible_checked": len(rows), "verified": verified, "failed": len(rows) - verified,
        "catalog_revision": revision, "targets": targets,
    }
