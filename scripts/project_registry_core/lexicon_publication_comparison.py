from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from . import identifiers as identifier_core


LEXICON_COLLECTION = "com.atproto.lexicon.schema"
DEFINITION_TYPES = {"record", "query", "procedure", "subscription", "object", "token", "string", "bytes", "boolean", "integer", "unknown", "cid-link", "blob", "array", "ref", "union", "space", "permission-set"}


def normalize_event(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    item = dict(value) if isinstance(value, dict) else {}
    if item.get("type") not in {None, "event"}:
        return None, "not_event"
    if str(item.get("collection") or "") != LEXICON_COLLECTION:
        return None, "wrong_collection"
    operation = str(item.get("operation") or "")
    if operation not in {"create", "update", "delete"}:
        return None, "invalid_operation"
    try:
        seq = int(item.get("seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    did = str(item.get("did") or "").strip()
    rkey = str(item.get("rkey") or "").strip()
    if seq <= 0 or not did or not rkey:
        return None, "missing_key_fields"
    record = None if operation == "delete" else item.get("record")
    if operation != "delete" and not isinstance(record, dict):
        return None, "malformed_record"
    return {
        "type": "event",
        "seq": seq,
        "time": str(item.get("time") or ""),
        "did": did,
        "operation": operation,
        "rev": str(item.get("rev") or ""),
        "collection": LEXICON_COLLECTION,
        "rkey": rkey,
        "record": dict(record) if isinstance(record, dict) else None,
    }, None


def fold_events(values: Iterable[Any]) -> dict[str, Any]:
    current: dict[tuple[str, str], dict[str, Any]] = {}
    latest_seq: dict[tuple[str, str], int] = {}
    operations: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    accepted = 0
    stale = 0
    for value in values:
        event, error = normalize_event(value)
        if event is None:
            failures[str(error or "invalid_event")] += 1
            continue
        accepted += 1
        operations[event["operation"]] += 1
        key = (event["did"], event["rkey"])
        previous_seq = latest_seq.get(key, 0)
        if event["seq"] <= previous_seq:
            stale += 1
            continue
        latest_seq[key] = event["seq"]
        if event["operation"] == "delete":
            current.pop(key, None)
        else:
            current[key] = event
    return {
        "current": [current[key] for key in sorted(current)],
        "accepted_event_count": accepted,
        "operation_counts": dict(sorted(operations.items())),
        "invalid_event_counts": dict(sorted(failures.items())),
        "stale_or_duplicate_event_count": stale,
        "tracked_key_count": len(latest_seq),
        "current_key_count": len(current),
        "deleted_key_count": len(latest_seq) - len(current),
    }


def _definition_types(record: dict[str, Any]) -> list[str]:
    defs = record.get("defs")
    if not isinstance(defs, dict):
        return []
    return sorted({str(value.get("type") or "") for value in defs.values() if isinstance(value, dict) and str(value.get("type") or "")})


def inspect_schema(event: dict[str, Any]) -> dict[str, Any]:
    from .registry import registrable_namespace_from_namespace
    record = dict(event.get("record") or {})
    record_type = str(record.get("$type") or "")
    schema_id = identifier_core.normalize_nsid(str(record.get("id") or ""))
    rkey = identifier_core.normalize_nsid(str(event.get("rkey") or ""))
    lexicon = record.get("lexicon")
    definition_types = _definition_types(record)
    defs = record.get("defs") if isinstance(record.get("defs"), dict) else {}
    spaces: list[dict[str, Any]] = []
    for name, value in defs.items():
        if not isinstance(value, dict) or value.get("type") != "space":
            continue
        raw_collections = value.get("collections")
        normalized_collections = (
            [identifier_core.normalize_nsid(str(item)) for item in raw_collections]
            if isinstance(raw_collections, list)
            else []
        )
        collections = sorted({item for item in normalized_collections if identifier_core.is_valid_nsid(item)})
        invalid_collections = sorted({item for item in normalized_collections if not identifier_core.is_valid_nsid(item)})
        spaces.append({
            "def": str(name),
            "collections": collections,
            "invalid_collections": invalid_collections,
            "collections_shape": "list" if isinstance(raw_collections, list) else "missing_or_non_list",
        })
    problems: list[str] = []
    if not identifier_core.is_valid_nsid(schema_id):
        problems.append("missing_or_invalid_id")
    if record_type != LEXICON_COLLECTION:
        problems.append("record_type_mismatch")
    if rkey != schema_id:
        problems.append("rkey_id_mismatch")
    if type(lexicon) is not int or lexicon != 1:
        problems.append("unsupported_lexicon_version")
    if not isinstance(record.get("defs"), dict) or not record["defs"] or any(not isinstance(value, dict) or not value.get("type") for value in defs.values()):
        problems.append("missing_or_invalid_defs")
    unknown_types = sorted(set(definition_types) - DEFINITION_TYPES)
    if unknown_types:
        problems.append("unknown_definition_type")
    if any(space["invalid_collections"] for space in spaces):
        problems.append("invalid_space_collection")
    if any(space["collections_shape"] != "list" for space in spaces):
        problems.append("invalid_space_collections_shape")
    authority_group = registrable_namespace_from_namespace(schema_id) if identifier_core.is_valid_nsid(schema_id) else ""
    return {
        "did": event["did"],
        "rkey": event["rkey"],
        "seq": event["seq"],
        "time": event.get("time", ""),
        "rev": event.get("rev", ""),
        "id": schema_id,
        "record_type": record_type,
        "lexicon": lexicon,
        "definition_types": definition_types,
        "main_type": str(defs.get("main", {}).get("type") or "") if isinstance(defs.get("main"), dict) else "",
        "spaces": spaces,
        "authority_group": authority_group,
        "problems": problems,
    }


def open_read_only_database(path: Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def load_database_index(conn: sqlite3.Connection) -> dict[str, Any]:
    from .registry import registrable_namespace_from_namespace
    known_nsids: dict[str, set[str]] = defaultdict(set)
    authority_projects: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute("SELECT project_key, value FROM project_identifiers WHERE kind='nsid'"):
        nsid = identifier_core.normalize_nsid(str(row["value"] or ""))
        if not identifier_core.is_valid_nsid(nsid):
            continue
        key = str(row["project_key"])
        known_nsids[nsid].add(key)
        group = registrable_namespace_from_namespace(nsid)
        if group:
            authority_projects[group].add(key)
    known_schemas: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute("SELECT project_key, state_json FROM projects"):
        try:
            state = json.loads(str(row["state_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        evidence = state.get("evidence") if isinstance(state, dict) else {}
        samples = evidence.get("lexicon_schema_samples", []) if isinstance(evidence, dict) else []
        for item in samples if isinstance(samples, list) else []:
            if not isinstance(item, dict):
                continue
            nsid = identifier_core.normalize_nsid(str(item.get("id") or item.get("nsid") or ""))
            if identifier_core.is_valid_nsid(nsid):
                known_schemas[nsid].add(str(row["project_key"]))
    return {
        "known_nsids": {key: sorted(value) for key, value in known_nsids.items()},
        "known_schemas": {key: sorted(value) for key, value in known_schemas.items()},
        "authority_projects": {key: sorted(value) for key, value in authority_projects.items()},
        "project_count": int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]),
        "query_only": int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1,
    }


def collection_inventory_nsids(payload: Any) -> set[str]:
    if not isinstance(payload, list):
        raise ValueError("collection inventory must be a JSON array")
    collections = {
        identifier_core.normalize_nsid(str(item.get("collection") or ""))
        for item in payload
        if isinstance(item, dict)
    }
    return {item for item in collections if identifier_core.is_valid_nsid(item)}


def compare(events: Iterable[Any], database_index: dict[str, Any], observed_collections: set[str], *, sample_limit: int = 20) -> dict[str, Any]:
    from .registry import registrable_namespace_from_namespace
    folded = fold_events(events)
    schemas = [inspect_schema(event) for event in folded["current"]]
    known_schemas = database_index.get("known_schemas", {})
    known_nsids = database_index.get("known_nsids", {})
    authority_projects = database_index.get("authority_projects", {})
    type_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    declared_collections: set[str] = set()
    space_collections: set[str] = set()
    schema_publication_dids: dict[str, set[str]] = defaultdict(set)
    for schema in schemas:
        type_counts.update(schema["definition_types"])
        schema_id = schema["id"]
        group_projects = list(authority_projects.get(schema["authority_group"], []))
        if not identifier_core.is_valid_nsid(schema_id):
            category = "malformed_schema_observation"
        elif schema_id in known_schemas:
            category = "known_schema_overlap"
        elif len(group_projects) == 1:
            category = "existing_project_unique_candidate"
        else:
            category = "unattributed_schema_catalog_observation"
        schema["comparison_category"] = category
        schema["known_schema_project_keys"] = list(known_schemas.get(schema_id, []))
        schema["known_nsid_project_keys"] = list(known_nsids.get(schema_id, []))
        schema["authority_project_keys"] = group_projects
        category_counts[category] += 1
        if identifier_core.is_valid_nsid(schema_id):
            schema_publication_dids[schema_id].add(schema["did"])
        if "record" in schema["definition_types"] and identifier_core.is_valid_nsid(schema_id):
            declared_collections.add(schema_id)
        for space in schema["spaces"]:
            space_collections.update(space["collections"])
    unknown_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for schema in schemas:
        if schema["comparison_category"] == "unattributed_schema_catalog_observation":
            unknown_by_group[schema["authority_group"] or "(invalid)"].append(schema)
    independent_candidates = {
        group for group, items in unknown_by_group.items()
        if (
            group != "(invalid)"
            and not authority_projects.get(group)
            and len(items) >= 2
            and len({item["did"] for item in items}) == 1
        )
    }
    for schema in schemas:
        if (
            schema["comparison_category"] == "unattributed_schema_catalog_observation"
            and schema["authority_group"] in independent_candidates
        ):
            schema["comparison_category"] = "independent_project_candidate"
    if independent_candidates:
        category_counts = Counter(schema["comparison_category"] for schema in schemas)
    problem_counts = Counter(problem for schema in schemas for problem in schema["problems"])
    non_record = [schema for schema in schemas if set(schema["definition_types"]) & {"query", "procedure", "subscription"}]
    spaces = [schema for schema in schemas if schema["spaces"]]
    unique_schema_ids = set(schema_publication_dids)
    unknown_groups = sorted({
        schema["authority_group"]
        for schema in schemas
        if schema["authority_group"] and schema["id"] not in known_schemas
    })
    unsynced_declared = sorted(declared_collections - observed_collections)
    unsynced_space = sorted(space_collections - observed_collections)
    sample_buckets: dict[str, list[dict[str, Any]]] = {}
    for category in sorted(category_counts):
        sample_buckets[category] = [schema for schema in schemas if schema["comparison_category"] == category][:sample_limit]
    return {
        "event_folding": {key: value for key, value in folded.items() if key != "current"},
        "database": {"project_count": database_index.get("project_count", 0), "query_only": bool(database_index.get("query_only"))},
        "counts": {
            "current_schema_publications": len(schemas),
            "valid_schema_publications": sum(
                1 for schema in schemas if identifier_core.is_valid_nsid(schema["id"])
            ),
            "unique_current_schema_ids": len(unique_schema_ids),
            "schema_ids_with_multiple_publication_dids": sum(
                1 for dids in schema_publication_dids.values() if len(dids) > 1
            ),
            "known_schema_overlap": sum(
                1
                for schema in schemas
                if identifier_core.is_valid_nsid(schema["id"]) and schema["id"] in known_schemas
            ),
            "unique_known_schema_ids": len(unique_schema_ids & set(known_schemas)),
            "unknown_authority_groups": len(unknown_groups),
            "declared_record_collections": len(declared_collections),
            "declared_record_collections_not_sync_observed": len(unsynced_declared),
            "non_record_query_procedure_subscription": len(non_record),
            "space_schemas": len(spaces),
            "space_declared_collections": len(space_collections),
            "space_declared_collections_not_sync_observed": len(unsynced_space),
        },
        "definition_type_counts": dict(sorted(type_counts.items())),
        "comparison_category_counts": dict(sorted(category_counts.items())),
        "schema_problem_counts": dict(sorted(problem_counts.items())),
        "unknown_authority_groups": unknown_groups,
        "declared_record_collections_not_sync_observed": unsynced_declared,
        "space_declared_collections_not_sync_observed": unsynced_space,
        "non_record_samples": non_record[:sample_limit],
        "space_samples": spaces[:sample_limit],
        "category_samples": sample_buckets,
    }


def fetch_snapshot(
    *, service: str, after_seq: int, max_events: int, max_seconds: int,
    sealed_tip_seq: int | None = None,
) -> dict[str, Any]:
    if not os.environ.get("ATPROTO_SURVEY_JETSTREAM_API_KEY", "").strip():
        raise RuntimeError("ATPROTO_SURVEY_JETSTREAM_API_KEY is not configured")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js 22.15 or newer is required")
    bridge = Path(__file__).resolve().with_name("jetstream_snapshot.mjs")
    completed = subprocess.run(
        [node, str(bridge)],
        input=json.dumps({
            "service": service, "collection": LEXICON_COLLECTION, "after_seq": after_seq,
            "max_events": max_events, "max_seconds": max_seconds,
            "sealed_tip_seq": sealed_tip_seq,
        }) + "\n",
        text=True,
        capture_output=True,
        check=False,
        cwd=str(Path(__file__).resolve().parents[2]),
        env=os.environ.copy(),
        timeout=max_seconds + 15,
    )
    if completed.returncode != 0:
        lines = [line.strip() for line in completed.stderr.splitlines() if line.strip()]
        detail = " | ".join(lines[-6:])[:1000] if lines else f"exit status {completed.returncode}"
        raise RuntimeError(f"Jetstream snapshot bridge failed: {detail}")
    events: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for line in completed.stdout.splitlines():
        item = json.loads(line)
        if item.get("type") == "event":
            events.append(item)
        elif item.get("type") == "summary":
            summary = dict(item)
    if not summary:
        raise RuntimeError("Jetstream snapshot bridge did not return a summary")
    summary["events"] = events
    return summary
