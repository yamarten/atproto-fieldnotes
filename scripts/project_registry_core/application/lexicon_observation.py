from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .. import http_transport
from ..lexicon_publication_catalog import (
    advance_report_revision,
    build_evaluation_report,
    catalog_state,
    finish_observation_run,
    ingest_segment,
    initialize_catalog,
    iso_now,
    load_registry_index,
    pending_non_record_usage_requests,
    reviewed_publisher_snapshot,
    select_observation_verification_targets,
    start_observation_run,
    verify_due_publications,
)
from ..lexicon_publication_comparison import collection_inventory_nsids, fetch_snapshot, open_read_only_database


SIDECAR_FILENAME = "lexicon-publication-catalog.db"
JETSTREAM_SERVICE = "https://jetstream.us-west.bsky.network"
COLLECTION_INVENTORY_URL = "https://collectiondata.usounds.work/collection_count_view"
MAX_EVENTS = 20_000
MAX_SECONDS = 300
VERIFY_LIMIT = 12
INVENTORY_TIMEOUT_SECONDS = 60.0


def sidecar_path_for_registry(registry_path: Path) -> Path:
    return Path(registry_path).expanduser().resolve().parent / SIDECAR_FILENAME


def _publisher_change_signals(conn: Any, before: dict[str, Any]) -> list[str]:
    signals: set[str] = set()
    for did, rkey in before.get("keys", set()):
        row = conn.execute(
            "SELECT schema_id,is_deleted FROM publication_catalog WHERE did=? AND rkey=?",
            (did, rkey),
        ).fetchone()
        if row is not None and bool(row["is_deleted"]):
            signals.add(f"deleted:{row['schema_id']}:{did}")
    for schema_id, previous_dids in before.get("schema_dids", {}).items():
        current_dids = {
            str(row[0])
            for row in conn.execute(
                "SELECT did FROM publication_catalog WHERE schema_id=? AND is_deleted=0",
                (schema_id,),
            )
        }
        added = current_dids - set(previous_dids)
        if added and not current_dids.intersection(previous_dids):
            signals.add(f"identity_changed:{schema_id}:{','.join(sorted(added))}")
    return sorted(signals)


def _new_verified_signals(report: dict[str, Any], verified_keys_before: set[tuple[str, str]]) -> dict[str, list[str]]:
    unregistered: list[str] = []
    spaces: list[str] = []
    for group in report.get("unregistered_authority_groups", []):
        publications = list(group.get("publications") or [])
        if any((str(item.get("did") or ""), str(item.get("rkey") or "")) not in verified_keys_before for item in publications):
            unregistered.append(str(group.get("authority_group") or ""))
    for item in report.get("verified_publisher_candidates", []):
        key = (str(item.get("did") or ""), str(item.get("rkey") or ""))
        if key not in verified_keys_before and "space" in set(item.get("definition_types") or []):
            spaces.append(str(item.get("schema_id") or ""))
    return {
        "new_verified_unregistered_authorities": unregistered,
        "new_verified_spaces": spaces,
    }


def run(
    registry_path: Path,
    *,
    run_id: str,
    cancel_requested: Callable[[], bool] | None = None,
    snapshot_fetcher: Callable[..., dict[str, Any]] = fetch_snapshot,
    inventory_fetcher: Callable[..., Any] = http_transport.request_json,
    verifier: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one fixed, bounded observation segment against a read-only registry."""
    registry_path = Path(registry_path).expanduser().resolve()
    sidecar_path = sidecar_path_for_registry(registry_path)
    registry = open_read_only_database(registry_path)
    try:
        sidecar = initialize_catalog(sidecar_path)
    except Exception:
        registry.close()
        raise
    ingestion: dict[str, Any] = {}
    inventory: dict[str, Any] = {"status": "not_started"}
    verification: dict[str, Any] = {"eligible_checked": 0, "verified": 0, "failed": 0, "targets": []}
    started = start_observation_run(sidecar, run_id)
    reviewed_before = reviewed_publisher_snapshot(sidecar)
    try:
        if cancel_requested and cancel_requested():
            history = finish_observation_run(sidecar, run_id, status="cancelled")
            return {"operation_status": "cancelled", "sidecar": SIDECAR_FILENAME, "history": history}
        state = catalog_state(sidecar)
        after_seq = int(state["pending_after_seq"] if state["pending_after_seq"] is not None else state["watermark_seq"])
        snapshot = snapshot_fetcher(
            service=JETSTREAM_SERVICE,
            after_seq=after_seq,
            max_events=MAX_EVENTS,
            max_seconds=MAX_SECONDS,
            sealed_tip_seq=int(state["pending_sealed_tip_seq"]) if state["pending_sealed_tip_seq"] is not None else None,
        )
        events = list(snapshot.pop("events"))
        ingestion = ingest_segment(sidecar, events, snapshot)
        if cancel_requested and cancel_requested():
            history = finish_observation_run(sidecar, run_id, status="cancelled", ingestion=ingestion)
            return {"operation_status": "cancelled", "sidecar": SIDECAR_FILENAME, "ingestion": ingestion, "history": history}

        inventory_observed_at = iso_now()
        try:
            inventory_payload = inventory_fetcher(COLLECTION_INVENTORY_URL, timeout=INVENTORY_TIMEOUT_SECONDS)
            inventory_nsids = collection_inventory_nsids(inventory_payload)
            inventory = {"status": "completed", "observed_at": inventory_observed_at, "collection_count": len(inventory_nsids)}
        except Exception as exc:
            inventory_nsids = set()
            inventory = {
                "status": "failed", "observed_at": inventory_observed_at,
                "collection_count": 0, "error": f"{type(exc).__name__}: {str(exc)[:400]}",
            }

        index = load_registry_index(registry)
        index["observed_collections"] = set(index.get("observed_collections", set())) | inventory_nsids
        if ingestion.get("status") == "applied" and not (cancel_requested and cancel_requested()):
            selected = select_observation_verification_targets(sidecar, index, limit=VERIFY_LIMIT)
            keys = [(item["did"], item["rkey"]) for item in selected]
            kwargs: dict[str, Any] = {"limit": VERIFY_LIMIT, "publication_keys": keys}
            if verifier is not None:
                kwargs["resolver"] = verifier
            verification = verify_due_publications(sidecar, **kwargs) if keys else verification
            selection_reasons = {(item["did"], item["rkey"]): item["selection_reason"] for item in selected}
            for item in verification.get("targets", []):
                item["selection_reason"] = selection_reasons.get((item["did"], item["rkey"]), "")
        report = build_evaluation_report(sidecar, index, since_revision=int(state["report_revision"]))
        new_signals = _new_verified_signals(report, set(reviewed_before.get("keys", set())))
        signals = {
            **new_signals,
            "reviewed_publisher_deletes_or_identity_changes": _publisher_change_signals(sidecar, reviewed_before),
            "non_record_usage_requests": pending_non_record_usage_requests(sidecar),
        }
        complete = ingestion.get("status") == "applied" and inventory["status"] == "completed"
        status = "completed" if complete else "partial"
        history = finish_observation_run(
            sidecar, run_id, status=status, ingestion=ingestion, inventory=inventory,
            verification=verification, signals=signals,
        )
        if complete:
            advance_report_revision(sidecar, int(report["catalog"]["catalog_revision"]))
        return {
            "operation_status": status,
            "sidecar": SIDECAR_FILENAME,
            "started_at": started["started_at"],
            "ingestion": ingestion,
            "inventory": inventory,
            "verification": verification,
            "review_due": history["review_due"],
            "review_reasons": history["review_reasons"],
            "history": history,
        }
    except Exception as exc:
        history = finish_observation_run(
            sidecar, run_id, status="failed", ingestion=ingestion, inventory=inventory,
            verification=verification, error=f"{type(exc).__name__}: {str(exc)[:400]}",
        )
        return {
            "operation_status": "failed", "sidecar": SIDECAR_FILENAME,
            "error": {"type": type(exc).__name__, "message": str(exc)}, "history": history,
        }
    finally:
        registry.close()
        sidecar.close()
