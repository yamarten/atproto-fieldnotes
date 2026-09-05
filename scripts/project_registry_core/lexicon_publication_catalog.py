from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import identifiers as identifier_core
from . import http_transport
from .lexicon_publication_comparison import LEXICON_COLLECTION, inspect_schema, normalize_event
from .registry import (
    extract_pds_url,
    registrable_namespace_from_namespace,
    resolve_did_document,
    resolve_lexicon_authority_did,
)


CATALOG_SCHEMA_VERSION = 2
REPORT_CONTRACT_VERSION = 1
MAX_RECORD_MATERIAL_BYTES = 65536
OBSERVATION_HISTORY_LIMIT = 16
OBSERVATION_SIGNAL_LIMIT = 50


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


def assert_distinct_database_paths(sidecar_path: Path, registry_path: Path) -> None:
    if sidecar_path.resolve() == registry_path.resolve():
        raise ValueError("sidecar catalog and project registry must use different SQLite files")


def connect_catalog(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_catalog(path: Path) -> sqlite3.Connection:
    conn = connect_catalog(path)
    existing_tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    metadata_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_metadata'"
    ).fetchone()
    existing_version = 0
    if metadata_exists is not None:
        row = conn.execute("SELECT schema_version FROM catalog_metadata WHERE singleton=1").fetchone()
        existing_version = int(row[0]) if row is not None else 0
        if existing_version not in {1, CATALOG_SCHEMA_VERSION}:
            conn.close()
            version = "missing" if row is None else row[0]
            raise RuntimeError(f"unsupported publication catalog schema version: {version}")
    elif existing_tables:
        conn.close()
        raise RuntimeError("refusing to initialize a publication catalog in a non-empty SQLite database")
    conn.executescript("""
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
        CREATE TABLE IF NOT EXISTS publication_observation_runs(
          run_id TEXT PRIMARY KEY,
          started_at TEXT NOT NULL,
          completed_at TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('running','completed','partial','failed','cancelled')),
          starting_watermark_seq INTEGER NOT NULL,
          sealed_tip_seq INTEGER,
          ending_watermark_seq INTEGER NOT NULL,
          pending_after_seq INTEGER,
          catalog_revision INTEGER NOT NULL,
          new_count INTEGER NOT NULL DEFAULT 0,
          updated_count INTEGER NOT NULL DEFAULT 0,
          deleted_count INTEGER NOT NULL DEFAULT 0,
          inventory_status TEXT NOT NULL DEFAULT 'not_started',
          inventory_observed_at TEXT NOT NULL DEFAULT '',
          inventory_collection_count INTEGER NOT NULL DEFAULT 0,
          inventory_error TEXT NOT NULL DEFAULT '',
          verification_target_count INTEGER NOT NULL DEFAULT 0,
          verification_success_count INTEGER NOT NULL DEFAULT 0,
          verification_failure_count INTEGER NOT NULL DEFAULT 0,
          verification_json TEXT NOT NULL DEFAULT '[]',
          signals_json TEXT NOT NULL DEFAULT '{}',
          review_due INTEGER NOT NULL DEFAULT 0,
          review_reasons_json TEXT NOT NULL DEFAULT '[]',
          error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS publication_observation_runs_completed
          ON publication_observation_runs(completed_at DESC, run_id DESC);
        CREATE TABLE IF NOT EXISTS publication_observation_state(
          singleton INTEGER PRIMARY KEY CHECK(singleton=1),
          successful_run_count INTEGER NOT NULL DEFAULT 0,
          review_due INTEGER NOT NULL DEFAULT 0,
          review_reasons_json TEXT NOT NULL DEFAULT '[]',
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS publication_observation_requests(
          request_key TEXT PRIMARY KEY,
          observed_at TEXT NOT NULL
        );
    """)
    if metadata_exists is None:
        conn.execute(
            "INSERT INTO catalog_metadata(singleton, schema_version, updated_at) VALUES(1, ?, ?)",
            (CATALOG_SCHEMA_VERSION, iso_now()),
        )
    elif existing_version == 1:
        conn.execute(
            "UPDATE catalog_metadata SET schema_version=?, updated_at=? WHERE singleton=1",
            (CATALOG_SCHEMA_VERSION, iso_now()),
        )
    conn.execute(
        "INSERT OR IGNORE INTO publication_observation_state(singleton,updated_at) VALUES(1,?)",
        (iso_now(),),
    )
    conn.commit()
    return conn


def _bounded_signal_values(values: Iterable[Any]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})[:OBSERVATION_SIGNAL_LIMIT]


def start_observation_run(conn: sqlite3.Connection, run_id: str, *, started_at: str | None = None) -> dict[str, Any]:
    value = str(run_id or "").strip()
    if not value:
        raise ValueError("observation run_id is required")
    state = catalog_state(conn)
    started = started_at or iso_now()
    with conn:
        conn.execute(
            """
            UPDATE publication_observation_runs SET status='failed',completed_at=?,
              error='worker interrupted before observation completed'
            WHERE status='running'
            """,
            (started,),
        )
        conn.execute(
            """
            INSERT INTO publication_observation_runs(
              run_id,started_at,completed_at,status,starting_watermark_seq,ending_watermark_seq,
              pending_after_seq,catalog_revision
            ) VALUES(?,?,?,'running',?,?,?,?)
            """,
            (
                value, started, "", int(state["watermark_seq"]), int(state["watermark_seq"]),
                state["pending_after_seq"], int(state["catalog_revision"]),
            ),
        )
    return {"run_id": value, "started_at": started, "starting_watermark_seq": int(state["watermark_seq"])}


def _successful_observation_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute(
        "SELECT successful_run_count FROM publication_observation_state WHERE singleton=1"
    ).fetchone()[0])


def record_non_record_usage_request(
    conn: sqlite3.Connection,
    request_key: str,
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    value = str(request_key or "").strip()
    if not value or len(value) > 160:
        raise ValueError("non-record usage request key must contain 1 to 160 characters")
    now = observed_at or iso_now()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO publication_observation_requests(request_key,observed_at) VALUES(?,?)",
            (value, now),
        )
        conn.execute(
            """
            DELETE FROM publication_observation_requests WHERE request_key IN (
              SELECT request_key FROM publication_observation_requests
              ORDER BY observed_at DESC,request_key DESC LIMIT -1 OFFSET ?
            )
            """,
            (OBSERVATION_SIGNAL_LIMIT,),
        )
        row = conn.execute(
            "SELECT review_reasons_json FROM publication_observation_state WHERE singleton=1"
        ).fetchone()
        reasons = set(json.loads(str(row[0] or "[]"))) if row else set()
        reasons.add("non_record_lexicon_usage_request")
        conn.execute(
            "UPDATE publication_observation_state SET review_due=1,review_reasons_json=?,updated_at=? WHERE singleton=1",
            (canonical_json(sorted(reasons)), now),
        )
    return observation_run_history(conn)


def pending_non_record_usage_requests(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT request_key FROM publication_observation_requests ORDER BY observed_at,request_key LIMIT ?",
            (OBSERVATION_SIGNAL_LIMIT,),
        )
    ]


def finish_observation_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    ingestion: dict[str, Any] | None = None,
    inventory: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
    signals: dict[str, Iterable[Any]] | None = None,
    error: str = "",
    completed_at: str | None = None,
) -> dict[str, Any]:
    if status not in {"completed", "partial", "failed", "cancelled"}:
        raise ValueError(f"invalid observation status: {status}")
    state = catalog_state(conn)
    ingestion = dict(ingestion or {})
    inventory = dict(inventory or {})
    verification = dict(verification or {})
    verification_targets = [
        {
            "did": str(item.get("did") or ""),
            "rkey": str(item.get("rkey") or ""),
            "schema_id": str(item.get("schema_id") or ""),
            "selection_reason": str(item.get("selection_reason") or ""),
            "verified": bool(item.get("verified")),
            "reason": str(item.get("reason") or "")[:240],
        }
        for item in list(verification.get("targets") or [])[:12]
        if isinstance(item, dict)
    ]
    bounded_signals = {
        "new_verified_unregistered_authorities": _bounded_signal_values((signals or {}).get("new_verified_unregistered_authorities", [])),
        "new_verified_spaces": _bounded_signal_values((signals or {}).get("new_verified_spaces", [])),
        "reviewed_publisher_deletes_or_identity_changes": _bounded_signal_values((signals or {}).get("reviewed_publisher_deletes_or_identity_changes", [])),
        "non_record_usage_requests": _bounded_signal_values((signals or {}).get("non_record_usage_requests", [])),
    }
    reasons: list[str] = []
    previous_successes = _successful_observation_count(conn)
    successful_run_count = previous_successes + int(status == "completed")
    if status == "completed" and successful_run_count >= 4:
        reasons.append("four_successful_weekly_observations")
    if bounded_signals["new_verified_unregistered_authorities"]:
        reasons.append("new_verified_unregistered_authority")
    if bounded_signals["new_verified_spaces"]:
        reasons.append("new_verified_space")
    if bounded_signals["reviewed_publisher_deletes_or_identity_changes"]:
        reasons.append("reviewed_publisher_delete_or_identity_change")
    if bounded_signals["non_record_usage_requests"]:
        reasons.append("non_record_lexicon_usage_request")
    finished = completed_at or iso_now()
    with conn:
        conn.execute(
            """
            UPDATE publication_observation_runs SET
              completed_at=?,status=?,sealed_tip_seq=?,ending_watermark_seq=?,pending_after_seq=?,catalog_revision=?,
              new_count=?,updated_count=?,deleted_count=?,inventory_status=?,inventory_observed_at=?,
              inventory_collection_count=?,inventory_error=?,verification_target_count=?,
              verification_success_count=?,verification_failure_count=?,verification_json=?,signals_json=?,review_due=?,
              review_reasons_json=?,error=?
            WHERE run_id=? AND status='running'
            """,
            (
                finished, status, ingestion.get("sealed_tip_seq"), int(state["watermark_seq"]), state["pending_after_seq"],
                int(state["catalog_revision"]), int(ingestion.get("new") or 0), int(ingestion.get("updated") or 0),
                int(ingestion.get("deleted") or 0), str(inventory.get("status") or "not_started"),
                str(inventory.get("observed_at") or ""), int(inventory.get("collection_count") or 0),
                str(inventory.get("error") or "")[:500], int(verification.get("eligible_checked") or 0),
                int(verification.get("verified") or 0), int(verification.get("failed") or 0),
                canonical_json(verification_targets), canonical_json(bounded_signals), int(bool(reasons)),
                canonical_json(reasons), str(error or "")[:500], run_id,
            ),
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise ValueError(f"observation run is missing or already finished: {run_id}")
        conn.execute(
            """
            DELETE FROM publication_observation_runs WHERE run_id IN (
              SELECT run_id FROM publication_observation_runs
              ORDER BY completed_at DESC, run_id DESC LIMIT -1 OFFSET ?
            )
            """,
            (OBSERVATION_HISTORY_LIMIT,),
        )
        state_row = conn.execute(
            "SELECT review_due,review_reasons_json FROM publication_observation_state WHERE singleton=1"
        ).fetchone()
        persisted_reasons = set(json.loads(str(state_row["review_reasons_json"] or "[]"))) if state_row else set()
        persisted_reasons.update(reasons)
        conn.execute(
            """
            UPDATE publication_observation_state SET successful_run_count=?,review_due=?,
              review_reasons_json=?,updated_at=? WHERE singleton=1
            """,
            (successful_run_count, int(bool(persisted_reasons)), canonical_json(sorted(persisted_reasons)), finished),
        )
    return observation_run_history(conn)


def observation_run_history(conn: sqlite3.Connection, *, limit: int = OBSERVATION_HISTORY_LIMIT) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM publication_observation_runs ORDER BY completed_at DESC, run_id DESC LIMIT ?",
        (max(1, min(OBSERVATION_HISTORY_LIMIT, int(limit))),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["review_due"] = bool(item["review_due"])
        item["verification_targets"] = json.loads(str(item.pop("verification_json") or "[]"))
        item["signals"] = json.loads(str(item.pop("signals_json") or "{}"))
        item["review_reasons"] = json.loads(str(item.pop("review_reasons_json") or "[]"))
        items.append(item)
    state = conn.execute("SELECT * FROM publication_observation_state WHERE singleton=1").fetchone()
    state_reasons = json.loads(str(state["review_reasons_json"] or "[]")) if state else []
    return {
        "history_limit": OBSERVATION_HISTORY_LIMIT,
        "successful_run_count": _successful_observation_count(conn),
        "review_due": bool(state["review_due"]) if state else False,
        "review_reasons": state_reasons,
        "non_record_usage_requests": pending_non_record_usage_requests(conn),
        "items": items,
    }


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
    with conn:
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


def load_registry_index(conn: sqlite3.Connection) -> dict[str, Any]:
    known_nsids: dict[str, set[str]] = defaultdict(set)
    authority_projects: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute("SELECT project_key, value FROM project_identifiers WHERE kind='nsid'"):
        nsid = identifier_core.normalize_nsid(str(row["value"] or ""))
        if identifier_core.is_valid_nsid(nsid):
            known_nsids[nsid].add(str(row["project_key"]))
            group = registrable_namespace_from_namespace(nsid)
            if group:
                authority_projects[group].add(str(row["project_key"]))
    known_schemas: dict[str, set[str]] = defaultdict(set)
    observed_collections: set[str] = set()
    for row in conn.execute("SELECT project_key, state_json FROM projects"):
        try:
            state = json.loads(str(row["state_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        evidence = state.get("evidence", {}) if isinstance(state, dict) else {}
        ops = state.get("ops", {}) if isinstance(state, dict) else {}
        for item in evidence.get("lexicon_schema_samples", []) if isinstance(evidence, dict) else []:
            if isinstance(item, dict):
                nsid = identifier_core.normalize_nsid(str(item.get("id") or item.get("nsid") or ""))
                if identifier_core.is_valid_nsid(nsid):
                    known_schemas[nsid].add(str(row["project_key"]))
        for item in ops.get("collection_observations", []) if isinstance(ops, dict) else []:
            if isinstance(item, dict):
                nsid = identifier_core.normalize_nsid(str(item.get("collection") or ""))
                if identifier_core.is_valid_nsid(nsid):
                    observed_collections.add(nsid)
    return {
        "known_nsids": {key: sorted(value) for key, value in known_nsids.items()},
        "known_schemas": {key: sorted(value) for key, value in known_schemas.items()},
        "authority_projects": {key: sorted(value) for key, value in authority_projects.items()},
        "observed_collections": observed_collections,
        "project_count": int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]),
        "query_only": int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1,
    }


def verify_publisher(nsid: str, expected_did: str, *, dns_timeout: float, http_timeout: float) -> dict[str, Any]:
    authority = identifier_core.derive_authority_domain(nsid)
    try:
        authority_did = resolve_lexicon_authority_did(authority, timeout=dns_timeout)
    except Exception as exc:
        return {
            "dns_same_did": None, "current_record_refetched": False,
            "reason": f"dns_lookup_failed:{type(exc).__name__}:{str(exc)[:180]}", "retryable": True,
            "authority_did": "", "record_uri": "", "record_hash": "",
        }
    if authority_did != expected_did:
        return {
            "dns_same_did": False, "current_record_refetched": False,
            "reason": "dns_did_mismatch", "retryable": True, "authority_did": authority_did,
            "record_uri": "", "record_hash": "",
        }
    try:
        did_doc = resolve_did_document(authority_did, timeout=http_timeout)
        pds_url = extract_pds_url(did_doc)
        if not pds_url:
            return {
                "dns_same_did": True, "current_record_refetched": False,
                "reason": "missing_pds_endpoint", "retryable": True, "authority_did": authority_did,
                "record_uri": "", "record_hash": "",
            }
        params = urllib.parse.urlencode({"repo": authority_did, "collection": LEXICON_COLLECTION, "rkey": nsid})
        payload = http_transport.request_json(f"{pds_url}/xrpc/com.atproto.repo.getRecord?{params}", timeout=http_timeout)
    except Exception as exc:
        return {
            "dns_same_did": True, "current_record_refetched": False,
            "reason": f"current_record_fetch_failed:{type(exc).__name__}:{str(exc)[:180]}", "retryable": True,
            "authority_did": authority_did,
            "record_uri": "", "record_hash": "",
        }
    value = payload.get("value") if isinstance(payload, dict) else None
    uri = str(payload.get("uri") or "") if isinstance(payload, dict) else ""
    expected_uri = f"at://{authority_did}/{LEXICON_COLLECTION}/{nsid}"
    valid = isinstance(value, dict) and identifier_core.normalize_nsid(str(value.get("id") or "")) == nsid and uri == expected_uri
    return {
        "dns_same_did": True, "current_record_refetched": bool(valid),
        "reason": "verified" if valid else "current_record_mismatch", "retryable": not valid,
        "authority_did": authority_did, "record_uri": uri,
        "record_hash": json_hash(value) if isinstance(value, dict) else "",
    }


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify_due_publications(
    conn: sqlite3.Connection,
    *,
    limit: int,
    schema_ids: Iterable[str] | None = None,
    dids: Iterable[str] | None = None,
    publication_keys: Iterable[tuple[str, str]] | None = None,
    now: str | None = None,
    ttl_hours: int = 168,
    retry_hours: int = 24,
    dns_timeout: float = 10.0,
    http_timeout: float = 20.0,
    resolver: Callable[..., dict[str, Any]] = verify_publisher,
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
    parameters: list[Any] = [checked_at]
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
        AND (v.did IS NULL OR v.verified_content_hash<>p.record_hash OR v.next_verification_at<=?)
        {target_where}
      ORDER BY CASE WHEN v.did IS NULL THEN 0 WHEN v.verified_content_hash<>p.record_hash THEN 1 ELSE 2 END,
        p.schema_id, p.did, p.rkey
      LIMIT ?
    """, parameters).fetchall()
    if not rows:
        return {"eligible_checked": 0, "verified": 0, "failed": 0, "targets": []}
    revision = int(catalog_state(conn)["catalog_revision"]) + 1
    verified = 0
    targets: list[dict[str, Any]] = []
    with conn:
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


def select_observation_verification_targets(
    conn: sqlite3.Connection,
    registry_index: dict[str, Any],
    *,
    limit: int,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Choose a bounded, actionable and fair subset without rechecking a schema's mirrors."""
    checked_now = now or iso_now()
    rows = conn.execute(
        """
        SELECT p.*,v.checked_at,v.next_verification_at,v.verified_content_hash,
          v.dns_same_did,v.current_record_refetched
        FROM publication_catalog p LEFT JOIN publication_verifications v USING(did,rkey)
        WHERE p.is_deleted=0 AND p.schema_id<>'' AND p.problems_json='[]'
          AND (v.did IS NULL OR v.verified_content_hash<>p.record_hash OR v.next_verification_at<=?)
        ORDER BY COALESCE(v.next_verification_at,''),COALESCE(v.checked_at,''),
          p.content_changed_revision,p.schema_id,p.did,p.rkey
        LIMIT 500
        """,
        (checked_now,),
    ).fetchall()
    authority_projects = registry_index.get("authority_projects", {})
    ranked: list[tuple[tuple[Any, ...], sqlite3.Row, str]] = []
    for row in rows:
        definitions = set(json.loads(str(row["definition_types_json"])))
        unregistered = bool(row["authority_group"] and not authority_projects.get(str(row["authority_group"])))
        is_space = "space" in definitions
        is_non_record = bool(definitions & {"query", "procedure", "subscription", "permission-set"})
        if row["checked_at"] is None:
            change_kind = "new"
            freshness = 0
        elif str(row["verified_content_hash"] or "") != str(row["record_hash"]):
            change_kind = "content_updated"
            freshness = 0
        else:
            change_kind = "verification_due"
            freshness = 1
        actionable = 0 if (unregistered or is_space) else (1 if is_non_record else 2)
        ranked.append(((freshness, actionable, str(row["checked_at"] or ""), int(row["content_changed_revision"]), str(row["schema_id"]), str(row["did"])), row, change_kind))
    selected: list[dict[str, Any]] = []
    selected_schemas: set[str] = set()
    for _rank, row, change_kind in sorted(ranked, key=lambda item: item[0]):
        schema_id = str(row["schema_id"])
        if schema_id in selected_schemas:
            continue
        selected_schemas.add(schema_id)
        selected.append({
            "did": str(row["did"]), "rkey": str(row["rkey"]), "schema_id": schema_id,
            "selection_reason": change_kind,
        })
        if len(selected) >= max(0, int(limit)):
            break
    return selected


def reviewed_publisher_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT p.did,p.rkey,p.schema_id,p.is_deleted
        FROM publication_catalog p JOIN publication_verifications v USING(did,rkey)
        WHERE v.dns_same_did=1 AND v.current_record_refetched=1
        ORDER BY p.schema_id,p.did,p.rkey
        """
    ).fetchall()
    return {
        "keys": {(str(row["did"]), str(row["rkey"])) for row in rows if not bool(row["is_deleted"])},
        "schema_dids": {
            schema_id: sorted({str(row["did"]) for row in rows if str(row["schema_id"]) == schema_id and not bool(row["is_deleted"])})
            for schema_id in sorted({str(row["schema_id"]) for row in rows})
        },
    }


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = {
        "did": str(row["did"]), "rkey": str(row["rkey"]), "seq": int(row["seq"]),
        "rev": str(row["rev"]), "event_time": str(row["event_time"]),
        "schema_id": str(row["schema_id"]), "authority_group": str(row["authority_group"]),
        "record_type": str(row["record_type"]), "lexicon_version": json.loads(str(row["lexicon_version_json"])),
        "definition_types": json.loads(str(row["definition_types_json"])),
        "spaces": json.loads(str(row["spaces_json"])), "problems": json.loads(str(row["problems_json"])),
        "record_hash": str(row["record_hash"]), "record_material_truncated": bool(row["record_material_truncated"]),
    }
    if "dns_same_did" in row.keys():
        payload["verification"] = {
            "dns_same_did": None if row["dns_same_did"] is None else bool(row["dns_same_did"]),
            "current_record_refetched": bool(row["current_record_refetched"]) if row["current_record_refetched"] is not None else None,
            "reason": str(row["reason"] or ""), "retryable": bool(row["retryable"]) if row["retryable"] is not None else None,
            "checked_at": str(row["checked_at"] or ""), "next_verification_at": str(row["next_verification_at"] or ""),
            "authority_did": str(row["authority_did"] or ""), "record_uri": str(row["record_uri"] or ""),
        }
    return payload


def build_evaluation_report(
    conn: sqlite3.Connection,
    registry_index: dict[str, Any],
    *,
    since_revision: int | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    state = catalog_state(conn)
    since = int(state["report_revision"] if since_revision is None else since_revision)
    checked_now = now or iso_now()
    rows = conn.execute("""
      SELECT p.*, v.dns_same_did,v.current_record_refetched,v.reason,v.retryable,v.checked_at,
        v.next_verification_at,v.authority_did,v.record_uri,v.changed_revision AS verification_changed_revision
      FROM publication_catalog p LEFT JOIN publication_verifications v USING(did,rkey)
      ORDER BY p.schema_id,p.did,p.rkey
    """).fetchall()
    current = [row for row in rows if not bool(row["is_deleted"])]
    def is_verified(row: sqlite3.Row) -> bool:
        return bool(row["dns_same_did"] == 1 and row["current_record_refetched"] == 1 and str(row["next_verification_at"] or "") > checked_now)
    verified_rows = [row for row in current if is_verified(row)]
    verified_keys = {(str(row["did"]), str(row["rkey"])) for row in verified_rows}
    schema_publishers: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in current:
        schema_publishers[str(row["schema_id"])].append(row)

    def verified_payload(row: sqlite3.Row) -> dict[str, Any]:
        payload = _row_payload(row)
        payload["publisher_count_for_schema"] = len(schema_publishers[str(row["schema_id"])])
        return payload

    authority_projects = registry_index.get("authority_projects", {})
    known_schemas = registry_index.get("known_schemas", {})
    known_nsids = registry_index.get("known_nsids", {})
    existing = []
    unregistered: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in verified_rows:
        payload = verified_payload(row)
        schema_id = str(row["schema_id"])
        exact_projects = sorted(set(known_schemas.get(schema_id, [])) | set(known_nsids.get(schema_id, [])))
        authority_matches = list(authority_projects.get(str(row["authority_group"]), []))
        projects = exact_projects or authority_matches
        payload["exact_project_keys"] = exact_projects
        payload["authority_project_keys"] = authority_matches
        payload["related_project_keys"] = projects
        payload["project_match_basis"] = "exact_schema_or_nsid" if exact_projects else ("authority_group" if authority_matches else "none")
        if len(projects) == 1:
            existing.append(payload)
        elif not projects and row["authority_group"]:
            unregistered[str(row["authority_group"])].append(payload)
    authority_groups = []
    for group in sorted(unregistered):
        publications = unregistered[group]
        authority_groups.append({
            "authority_group": group,
            "independent_candidate": len(publications) >= 2 and len({item["did"] for item in publications}) == 1,
            "publisher_dids": sorted({item["did"] for item in publications}),
            "schema_ids": sorted({item["schema_id"] for item in publications}),
            "publications": publications,
        })
    observed = set(registry_index.get("observed_collections", set()))
    declarations = []
    for row in current:
        for space in json.loads(str(row["spaces_json"])):
            for collection in space.get("collections", []):
                declarations.append({
                    "schema_id": str(row["schema_id"]), "publisher_did": str(row["did"]),
                    "space_def": str(space.get("def") or ""), "declared_collection": str(collection),
                    "activity_observed": collection in observed,
                })
    declarations.sort(key=lambda item: (item["schema_id"], item["publisher_did"], item["space_def"], item["declared_collection"]))
    catalog_only = []
    for schema_id in sorted(schema_publishers):
        group = schema_publishers[schema_id]
        for row in group:
            key = (str(row["did"]), str(row["rkey"]))
            if key not in verified_keys:
                payload = _row_payload(row)
                payload["catalog_reason"] = "mirror_group" if len(group) > 1 else "publisher_not_verified"
                payload["publisher_count_for_schema"] = len(group)
                catalog_only.append(payload)
    deleted_or_failed = []
    for row in rows:
        expired = bool(row["next_verification_at"] and str(row["next_verification_at"]) <= checked_now)
        if bool(row["is_deleted"]) or (not bool(row["is_deleted"]) and (row["checked_at"] is not None and not is_verified(row))):
            payload = _row_payload(row)
            payload["state"] = "deleted" if bool(row["is_deleted"]) else ("verification_expired" if expired else "verification_failed")
            deleted_or_failed.append(payload)
    new_rows = [row for row in rows if int(row["content_changed_revision"]) > since and int(row["first_seen_revision"]) == int(row["content_changed_revision"]) and not row["is_deleted"]]
    updated_rows = [row for row in rows if int(row["content_changed_revision"]) > since and int(row["first_seen_revision"]) != int(row["content_changed_revision"]) and not row["is_deleted"]]
    deleted_rows = [row for row in rows if int(row["content_changed_revision"]) > since and row["is_deleted"]]
    verification_changed = [row for row in rows if row["verification_changed_revision"] is not None and int(row["verification_changed_revision"]) > since]
    sections = {
        "verified_publisher_candidates": [verified_payload(row) for row in verified_rows],
        "existing_project_unique_candidates": existing,
        "unregistered_authority_groups": authority_groups,
        "space_declared_collections": declarations,
        "space_declared_collections_without_activity": [item for item in declarations if not item["activity_observed"]],
        "mirror_or_catalog_only": catalog_only,
        "deleted_or_verification_failed_or_expired": deleted_or_failed,
    }
    return {
        "contract_version": REPORT_CONTRACT_VERSION,
        "catalog": {
            "schema_version": int(state["schema_version"]), "watermark_seq": int(state["watermark_seq"]),
            "catalog_revision": int(state["catalog_revision"]), "since_revision_exclusive": since,
            "pending_sealed_tip_seq": state["pending_sealed_tip_seq"], "pending_after_seq": state["pending_after_seq"],
        },
        "registry": {"project_count": registry_index.get("project_count", 0), "query_only": bool(registry_index.get("query_only"))},
        "changes": {
            "new_schemas": [_row_payload(row) for row in new_rows],
            "updated_schemas": [_row_payload(row) for row in updated_rows],
            "deleted": [_row_payload(row) for row in deleted_rows],
            "verification_changed": [_row_payload(row) for row in verification_changed],
        },
        "counts": {key: len(value) for key, value in sections.items()},
        **sections,
    }


def advance_report_revision(conn: sqlite3.Connection, revision: int) -> None:
    with conn:
        conn.execute("UPDATE catalog_metadata SET report_revision=?, updated_at=? WHERE singleton=1", (revision, iso_now()))
