from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

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
SYNC_STATE_FILENAME = "job-schedules.json"
SYNC_PRESET_ID = "sync_sources_then_enrich"
MAX_SYNC_AGE = timedelta(hours=48)


def sidecar_path_for_registry(registry_path: Path) -> Path:
    return Path(registry_path).expanduser().resolve().parent / SIDECAR_FILENAME


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def evaluation_period(at: str) -> str:
    local = _parse_time(at).astimezone(ZoneInfo("Asia/Tokyo"))
    iso = local.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def sync_comparison(registry_path: Path, *, observation_started_at: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "unavailable",
        "reason": "sync_state_missing",
        "evaluation_period": evaluation_period(observation_started_at),
        "sync_completed_at": "",
        "sync_period": "",
        "sync_age_seconds": None,
    }
    path = registry_path.parent / SYNC_STATE_FILENAME
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return result
    except (OSError, ValueError, TypeError):
        result["reason"] = "sync_state_invalid"
        return result
    schedule = (payload.get("schedules") or {}).get(SYNC_PRESET_ID) if isinstance(payload, dict) else None
    if not isinstance(schedule, dict):
        return result
    result["sync_completed_at"] = str(schedule.get("last_completed_at") or "")
    result["sync_period"] = str(schedule.get("last_period") or "")
    if str(schedule.get("last_status") or "") != "completed":
        result["reason"] = "sync_not_completed"
        return result
    try:
        sync_at = _parse_time(result["sync_completed_at"])
        observation_at = _parse_time(observation_started_at)
    except (TypeError, ValueError):
        result["reason"] = "sync_completion_time_invalid"
        return result
    age = observation_at - sync_at
    result["sync_age_seconds"] = int(age.total_seconds())
    if age.total_seconds() < 0:
        result["reason"] = "observation_precedes_sync"
    elif age > MAX_SYNC_AGE:
        result["reason"] = "sync_too_old"
    else:
        result["status"] = "available"
        result["reason"] = "post_sync"
    return result


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


def _new_verified_signals(
    report: dict[str, Any],
    verified_keys_before: set[tuple[str, str]],
    *,
    baseline_revision: int,
) -> dict[str, list[str]]:
    unregistered: list[str] = []
    spaces: list[str] = []
    for group in report.get("unregistered_authority_groups", []):
        publications = list(group.get("publications") or [])
        if any(
            (str(item.get("did") or ""), str(item.get("rkey") or "")) not in verified_keys_before
            and int(item.get("first_seen_revision") or 0) > baseline_revision
            for item in publications
        ):
            unregistered.append(str(group.get("authority_group") or ""))
    for item in report.get("verified_publisher_candidates", []):
        key = (str(item.get("did") or ""), str(item.get("rkey") or ""))
        if (
            key not in verified_keys_before
            and int(item.get("first_seen_revision") or 0) > baseline_revision
            and "space" in set(item.get("definition_types") or [])
        ):
            spaces.append(str(item.get("schema_id") or ""))
    return {
        "new_verified_unregistered_authorities": unregistered,
        "new_verified_spaces": spaces,
    }


def _weekly_categories(
    report: dict[str, Any],
    *,
    baseline_revision: int,
    observed_collections: set[str],
) -> dict[str, int]:
    new_items = [
        item for item in report.get("changes", {}).get("new_schemas", [])
        if int(item.get("first_seen_revision") or 0) > baseline_revision
    ]
    new_keys = {(str(item.get("did") or ""), str(item.get("rkey") or "")) for item in new_items}
    verified = {
        (str(item.get("did") or ""), str(item.get("rkey") or "")): item
        for item in report.get("verified_publisher_candidates", [])
        if (str(item.get("did") or ""), str(item.get("rkey") or "")) in new_keys
    }
    catalog_only = {
        (str(item.get("did") or ""), str(item.get("rkey") or "")): item
        for item in report.get("mirror_or_catalog_only", [])
        if (str(item.get("did") or ""), str(item.get("rkey") or "")) in new_keys
    }
    declarations = report.get("space_declared_collections", [])
    counts = {
        "record_with_fresh_activity": 0,
        "record_without_observed_activity": 0,
        "non_record": 0,
        "space_with_declared_activity": 0,
        "space_without_declared_activity": 0,
        "verification_pending": 0,
        "mirror_or_verification_failure": 0,
        "verified_exact_unique": 0,
        "verified_exact_ambiguous": 0,
        "verified_authority_unique": 0,
        "verified_authority_ambiguous": 0,
        "verified_unregistered": 0,
    }
    observed = {
        (str(item.get("schema_id") or ""), str(item.get("publisher_did") or ""))
        for item in declarations if str(item.get("declared_collection") or "") in observed_collections
    }
    for item in new_items:
        key = (str(item.get("did") or ""), str(item.get("rkey") or ""))
        types = set(item.get("definition_types") or [])
        if "record" in types:
            counts[
                "record_with_fresh_activity"
                if str(item.get("schema_id") or "") in observed_collections
                else "record_without_observed_activity"
            ] += 1
        if types & {"query", "procedure", "subscription", "permission-set"}:
            counts["non_record"] += 1
        if "space" in types:
            pair = (str(item.get("schema_id") or ""), str(item.get("did") or ""))
            counts["space_with_declared_activity" if pair in observed else "space_without_declared_activity"] += 1
        match = verified.get(key)
        if match is None:
            verification = (catalog_only.get(key) or {}).get("verification") or {}
            checked_at = str(verification.get("checked_at") or "")
            previously_verified = bool(
                verification.get("dns_same_did") is True
                and verification.get("current_record_refetched") is True
            )
            counts[
                "verification_pending"
                if not checked_at or previously_verified
                else "mirror_or_verification_failure"
            ] += 1
            continue
        exact = list(match.get("exact_project_keys") or [])
        authority = list(match.get("authority_project_keys") or [])
        if exact:
            counts["verified_exact_unique" if len(exact) == 1 else "verified_exact_ambiguous"] += 1
        elif authority:
            counts["verified_authority_unique" if len(authority) == 1 else "verified_authority_ambiguous"] += 1
        else:
            counts["verified_unregistered"] += 1
    return counts


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
    comparison = sync_comparison(registry_path, observation_started_at=started["started_at"])
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
        observation_state = sidecar.execute(
            "SELECT evaluation_baseline_revision FROM publication_observation_state WHERE singleton=1"
        ).fetchone()
        baseline_revision = int(observation_state[0]) if observation_state else 0
        new_signals = _new_verified_signals(
            report, set(reviewed_before.get("keys", set())), baseline_revision=baseline_revision,
        )
        categories = _weekly_categories(
            report, baseline_revision=baseline_revision, observed_collections=inventory_nsids,
        )
        signals = {
            **new_signals,
            "reviewed_publisher_deletes_or_identity_changes": _publisher_change_signals(sidecar, reviewed_before),
            "non_record_usage_requests": pending_non_record_usage_requests(sidecar),
        }
        complete = ingestion.get("status") == "applied" and inventory["status"] == "completed"
        status = "completed" if complete else "partial"
        history = finish_observation_run(
            sidecar, run_id, status=status, ingestion=ingestion, inventory=inventory,
            verification=verification, signals=signals, comparison=comparison, categories=categories,
        )
        if complete:
            advance_report_revision(sidecar, int(report["catalog"]["catalog_revision"]))
        return {
            "operation_status": status,
            "sidecar": SIDECAR_FILENAME,
            "started_at": started["started_at"],
            "ingestion": ingestion,
            "inventory": inventory,
            "comparison": comparison,
            "categories": categories,
            "verification": verification,
            "review_due": history["review_due"],
            "review_reasons": history["review_reasons"],
            "history": history,
        }
    except Exception as exc:
        history = finish_observation_run(
            sidecar, run_id, status="failed", ingestion=ingestion, inventory=inventory,
            verification=verification, comparison=comparison,
            error=f"{type(exc).__name__}: {str(exc)[:400]}",
        )
        return {
            "operation_status": "failed", "sidecar": SIDECAR_FILENAME,
            "error": {"type": type(exc).__name__, "message": str(exc)}, "history": history,
        }
    finally:
        registry.close()
        sidecar.close()
