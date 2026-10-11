"""Explicit, scoped repair of missing registration identity and adoption reasons."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .. import project_record, registration, registry, relations
from ..persistence import concurrency, identifier_storage, runtime_view
from ..publications import publication_registry


def _changes(conn: sqlite3.Connection, project: dict[str, Any], current: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    key = project["project_key"]
    if project.get("tracking_state") not in {"active", "stable", "stalled"}:
        return [], "inactive_project"
    if conn.execute("SELECT 1 FROM project_key_aliases WHERE canonical_project_key=? OR alias_project_key=? LIMIT 1", (key, key)).fetchone():
        return [], "merged_project_requires_review"
    sources = set(project.get("discovery_sources", []))
    claims = runtime_view.project_identifiers(project)
    rows = project.get(runtime_view.RUNTIME_ADOPTION_ROWS, [])
    decisions = conn.execute("SELECT * FROM project_decisions WHERE project_key=?", (key,)).fetchall()
    if not registration.confirmed_components(project):
        if sources != {publication_registry.SOURCE} or decisions or rows or project.get("manual_seeds"):
            return [], "identity_requires_operator_review"
        candidates = {item.value for item in claims if item.kind == "nsid" and item.role == "owned_nsid"
                      and item.classification == "candidate"}
        observations = {item["schema_id"]: current[item["schema_id"]] for item in project.get("publication_schemas", [])
                        if item["schema_id"] in current and current[item["schema_id"]].get("status") in {"pending", "verified"}
                        and not item.get("attribution_conflict")
                        and not relations.identifier_is_guarded(project, "nsid", item["schema_id"])}
        if not candidates or len({value.rsplit(".", 1)[0] for value in candidates}) != 1:
            return [], "ambiguous_registration_scope"
        eligible = sorted(candidates & observations.keys())
        if not eligible:
            return [], "no_current_publication_target"
        target = project.get("seed_value") if project.get("seed_value") in eligible else eligible[0]
        return [{"kind": "nsid", "value": target, "action": "adopt_publication_origin",
                 "before": "candidate", "after": "confirmed", "registration_origin": True,
                 "evidence": publication_registry.registration_evidence(observations[target]),
                 "source": publication_registry.SOURCE}], ""
    if "collection_inventory_import" not in sources:
        return [], "no_supported_missing_adoption"
    namespaces = {item.get("collection") for item in project.get("collection_observations", [])
                  if item.get("source") == "collection_inventory_import"}
    changes = []
    for item in claims:
        if item.kind != "nsid" or item.role != "owned_nsid" or item.classification != "confirmed":
            continue
        if item.value.rsplit(".", 1)[0] not in namespaces:
            continue
        # Never reinstate a cleared/review-required adoption or a human decision.
        if any(row.get("kind") == "nsid" and row.get("value") == item.value for row in rows):
            continue
        if any(row["target_type"] == "nsid" and row["target_value"] == item.value for row in decisions):
            continue
        if project_record.identifier_is_blocked(project, "nsid", item.value) or project_record.identifier_is_deferred(project, "nsid", item.value):
            continue
        changes.append({"kind": "nsid", "value": item.value, "action": "record_collection_adoption",
                        "before": "confirmed_without_adoption", "after": "confirmed",
                        "registration_origin": sources == {"collection_inventory_import"},
                        "source": "collection_inventory_import"})
    return sorted(changes, key=lambda item: item["value"]), "" if changes else "no_supported_missing_adoption"


def repair(conn: sqlite3.Connection, *, project_keys: list[str] | None = None,
           execute: bool = False, expected_plan: str = "") -> dict[str, Any]:
    if execute and not project_keys:
        raise ValueError("execute requires explicit project keys")
    if execute and not expected_plan:
        raise ValueError("execute requires the reviewed plan id")
    with (concurrency.write_transaction(conn) if execute else concurrency.read_snapshot(conn)):
        current = publication_registry.schemas(conn, now=project_record.iso_now())
        if project_keys:
            keys = sorted(set(project_keys))
        else:
            roles = sorted(identifier_storage.COMPONENT_ROLES)
            placeholders = ",".join("?" for _ in roles)
            keys = [str(row[0]) for row in conn.execute(f"""SELECT p.project_key FROM projects p
                WHERE NOT EXISTS (SELECT 1 FROM project_identifiers i WHERE i.project_key=p.project_key
                  AND i.classification='confirmed' AND i.role IN ({placeholders}))
                OR EXISTS (SELECT 1 FROM project_identifiers i WHERE i.project_key=p.project_key
                  AND i.kind='nsid' AND i.role='owned_nsid' AND i.classification='confirmed'
                  AND NOT EXISTS (SELECT 1 FROM project_identifier_adoptions a WHERE a.project_key=i.project_key
                    AND a.kind=i.kind AND a.value=i.value AND a.role=i.role))
                ORDER BY p.project_key""", roles)]
        changes, skipped = [], []
        projects = {}
        for key in keys:
            project = project_record.fetch_project(conn, key)
            if project is None:
                raise ValueError(f"project not found: {key}")
            items, reason = _changes(conn, project, current)
            if items:
                projects[key] = project
                changes.append({"project_key": key, "name": project.get("name", ""),
                                "revision": project.get(runtime_view.RUNTIME_PROJECT_REVISION), "changes": items})
            else:
                skipped.append({"project_key": key, "reason": reason})
        plan_id = hashlib.sha256(json.dumps({"changes": changes, "skipped": skipped}, sort_keys=True).encode()).hexdigest()
        if execute and plan_id != expected_plan:
            raise project_record.StaleProjectWriteError("registration repair plan changed; preview again")
        if execute:
            for entry in changes:
                project = projects[entry["project_key"]]
                for item in entry["changes"]:
                    if item["action"] == "adopt_publication_origin":
                        publication_registry.adopt_registration_origin(project, current[item["value"]], repair=True)
                    else:
                        registry.add_collection_owned_nsid(project, item["value"], source=item["source"],
                            registration_origin=item["registration_origin"],
                            source_ref=json.dumps({"repair": "missing_collection_adoption", "plan_id": plan_id}),
                            policy_version="registration-repair-v1")
                project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")),
                                            preserve_timestamps=True, commit=False)
        return {"mode": "execute" if execute else "dry_run", "plan_id": plan_id,
                "project_count": len(changes), "changes": changes, "skipped": skipped,
                "adoption_time": "repair_time_not_original_registration_time"}
