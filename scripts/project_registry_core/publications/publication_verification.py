"""Share fetched Lexicon verification between discovery and investigation."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .. import collection_trust
from . import lexicon_publication_catalog as catalog
from ..persistence.concurrency import write_transaction


def witnesses(conn: sqlite3.Connection, nsid: str) -> dict[str, str]:
    return {str(row[0]): str(row[1]) for row in conn.execute(
        "SELECT did,record_hash FROM publication_catalog WHERE schema_id=? AND rkey=? AND is_deleted=0 AND problems_json='[]'",
        (nsid, nsid))}


def record_result(conn: sqlite3.Connection, nsid: str, *, expected: dict[str, str],
                  checked_at: str, payload: dict[str, Any] | None = None,
                  error: str = "", authority_did: str = "") -> list[dict[str, Any]]:
    """Persist captured IO only; stale catalog or newer verification wins."""
    if not expected:
        return []
    checked_time = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    checked_at = checked_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    validation = collection_trust.publication_validation(nsid, payload) if payload else {}
    did = str((payload or {}).get("did") or authority_did)
    target_did = did if did in expected else sorted(expected)[0]
    result = {
        "dns_same_did": did == target_did if did else None,
        "current_record_refetched": bool(validation and did == target_did),
        "reason": error or ("verified" if validation and did == target_did else
                            "authority_not_observed" if did != target_did else "invalid_publication"),
        "authority_did": did,
        "record_uri": str(((payload or {}).get("record") or {}).get("uri") or ""),
        "record_hash": str(validation.get("schema_hash") or ""),
        "retryable": bool(error or not validation or did != target_did),
    }
    with write_transaction(conn):
        if witnesses(conn, nsid) != expected:
            return []
        previous = conn.execute(
            "SELECT MAX(checked_at) FROM publication_verifications WHERE rkey=?", (nsid,)).fetchone()[0]
        if previous and datetime.fromisoformat(str(previous).replace("Z", "+00:00")) >= checked_time:
            return []
        outcome = catalog.verify_due_publications(
            conn, limit=1, force=True, publication_keys=[(target_did, nsid)], now=checked_at,
            resolver=lambda *_a, **_kw: result)
        return outcome["targets"]


def share_investigation(conn: sqlite3.Connection, pending: dict[str, Any]) -> None:
    """Called inside the accepted project/history save transaction."""
    observed = {item["schema_id"]: {p["did"]: p["hash"] for p in item.get("publishers", [])}
                for item in pending["input_project"].get("publication_schemas", [])}
    bundle = pending["bundle"]
    for record in bundle.records:
        if record.kind != "lexicon_resolution_snapshot" or not isinstance(record.value, dict):
            continue
        nsid = str(record.value.get("nsid") or "")
        payload = record.value.get("result")
        if not isinstance(payload, dict):
            continue
        expected = observed.get(nsid)
        if expected is None:
            # Projects from other sources can corroborate an exact current publication.
            validation = collection_trust.publication_validation(nsid, payload)
            expected = witnesses(conn, nsid)
            if not validation or expected.get(str(payload.get("did"))) != validation.get("schema_hash"):
                continue
        record_result(conn, nsid, expected=expected, payload=payload,
                      checked_at=str(payload.get("resolved_at") or record.observed_at))
    for failure in bundle.failures:
        if failure.source == "lexicon_resolution" and failure.target in observed:
            record_result(conn, failure.target, expected=observed[failure.target],
                          checked_at=pending["started_at"], error=failure.message)
