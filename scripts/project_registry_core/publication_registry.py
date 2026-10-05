"""Registry storage and project projection of public Lexicon observations."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from . import identifiers, lexicon_types

SOURCE = "lexicon_publication"


def install_schema(conn: sqlite3.Connection) -> None:
    from .lexicon_publication_catalog import install_catalog_schema
    install_catalog_schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS collection_inventory_members(
        nsid TEXT PRIMARY KEY, observed_at TEXT NOT NULL, material_json TEXT NOT NULL)""")


def save_inventory(conn: sqlite3.Connection, payload: list[dict[str, Any]], *, observed_at: str) -> None:
    """Replace only a complete inventory. The caller owns the transaction."""
    from .lexicon_publication_comparison import collection_inventory_nsids
    members = collection_inventory_nsids(payload)
    if not members:
        raise ValueError("empty collection inventory cannot establish absence")
    conn.execute("DELETE FROM collection_inventory_members")
    conn.executemany("INSERT OR REPLACE INTO collection_inventory_members VALUES(?,?,?)", [
        (n, observed_at, json.dumps(item, sort_keys=True)) for item in payload
        if (n := identifiers.normalize_nsid(str(item.get("collection") or ""))) in members
    ])
    conn.execute("INSERT OR REPLACE INTO registry_meta VALUES('collection_inventory_observed_at',?)", (observed_at,))
    conn.execute("INSERT OR REPLACE INTO registry_meta VALUES('collection_inventory_status','completed')")


def schemas(conn: sqlite3.Connection, *, now: str, schema_ids: set[str] | None = None) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in conn.execute("""SELECT p.*,v.dns_same_did,v.current_record_refetched,
        v.checked_at,v.next_verification_at,v.verified_content_hash,v.authority_did,v.reason
        FROM publication_catalog p LEFT JOIN publication_verifications v USING(did,rkey)
        ORDER BY p.schema_id,p.did,p.rkey"""):
        # rkey identifies the published NSID even if an update corrupts its id.
        if identifiers.is_valid_nsid(str(row["rkey"])):
            groups[str(row["rkey"])].append(dict(row))
    members = {str(r[0]) for r in conn.execute("SELECT nsid FROM collection_inventory_members")}
    inventory = conn.execute("SELECT value FROM registry_meta WHERE key='collection_inventory_observed_at'").fetchone()
    inventory_status = conn.execute("SELECT value FROM registry_meta WHERE key='collection_inventory_status'").fetchone()
    comparable = bool(inventory and inventory_status and inventory_status[0] == "completed")
    # DNS answers apply to the authority, including sibling schema IDs.
    authority_checks: dict[str, tuple[str, set[str]]] = {}
    for sid, publications in groups.items():
        authority = identifiers.derive_authority_domain(sid)
        for p in publications:
            at = str(p["checked_at"] or "")
            if not at or not p["authority_did"]:
                continue
            if authority not in authority_checks or at > authority_checks[authority][0]:
                authority_checks[authority] = (at, {p["authority_did"]})
            elif at == authority_checks[authority][0]:
                authority_checks[authority][1].add(p["authority_did"])
    output = {}
    for sid, publications in groups.items():
        if schema_ids is not None and sid not in schema_ids:
            continue
        current = [p for p in publications if not p["is_deleted"] and p["problems_json"] == "[]"]
        if not current:
            status = "invalid" if any(not p["is_deleted"] for p in publications) else "deleted"
            output[sid] = {"schema_id": sid, "schema_type": "unknown", "status": status, "publishers": [], "declarations": []}
            continue
        # A newer DNS answer supersedes witnesses for the whole authority.
        authorities = authority_checks.get(identifiers.derive_authority_domain(sid), ("", set()))[1]
        verified = [p for p in current if p["dns_same_did"] == 1 and p["current_record_refetched"] == 1
                    and p["verified_content_hash"] == p["record_hash"]
                    and str(p["next_verification_at"] or "") > now
                    and (not authorities or authorities == {p["did"]})]
        selected = verified[0] if len(verified) == 1 else current[0]
        materials = [json.loads(p["record_material_json"]) for p in current]
        kinds = {lexicon_types.schema_type(m) for m in materials}
        verified_material = json.loads(selected["record_material_json"])
        kind = lexicon_types.schema_type(verified_material) if len(verified) == 1 else next(iter(kinds)) if len(kinds) == 1 else "unknown"
        declarations = []
        declaration_rows = verified if len(verified) == 1 else current
        for p in declaration_rows:
            for space in json.loads(p["spaces_json"]):
                if space.get("def") != "main":
                    continue
                for n in space.get("collections", []):
                    declarations.append({"collection": n, "publisher_did": p["did"], "schema_hash": p["record_hash"],
                                         "public_activity": "unknown" if not comparable else "observed" if n in members else "not_observed"})
        output[sid] = {
            "schema_id": sid, "schema_type": kind,
            "status": "verified" if len(verified) == 1 else "pending",
            "publisher_did": selected["did"] if len(verified) == 1 else "",
            "authority_dids": sorted(authorities),
            "record_hash": selected["record_hash"] if len(verified) == 1 else "",
            "checked_at": selected["checked_at"] if len(verified) == 1 else "",
            "next_verification_at": selected["next_verification_at"] if len(verified) == 1 else "",
            "publishers": [{"did": p["did"], "hash": p["record_hash"], "reason": p["reason"] or "not_checked"} for p in current],
            "content_conflict": len({p["record_hash"] for p in current}) > 1,
            "description": str(verified_material.get("description") or (verified_material.get("defs", {}).get("main") or {}).get("description") or "") if len(verified) == 1 else "",
            "declarations": declarations,
            "public_activity": "not_applicable" if kind != "record" else "unknown" if not comparable else "observed" if sid in members else "not_observed",
            "inventory_status": str(inventory_status[0]) if inventory_status else "unavailable",
            "inventory_observed_at": str(inventory[0]) if inventory else "",
        }
    return output


def import_projects(conn: sqlite3.Connection, observed: dict[str, dict[str, Any]], *, execute: bool,
                    create_unmatched: bool = True) -> dict[str, Any]:
    from .concurrency import write_transaction, read_snapshot
    # Matching and inserts share a transaction, including the no-existing-match case.
    with (write_transaction(conn) if execute else read_snapshot(conn)):
        return _import_projects(conn, observed, execute=execute, create_unmatched=create_unmatched)


def _import_projects(conn: sqlite3.Connection, observed: dict[str, dict[str, Any]], *, execute: bool,
                     create_unmatched: bool) -> dict[str, Any]:
    from . import project_record as records, runtime_view, identifier_storage, domain_policy, relations
    from .concurrency import write_transaction
    from .relations import identifier_is_guarded

    projects = records.list_projects(conn)
    exact: dict[str, set[str]] = defaultdict(set)
    namespaces: dict[str, set[str]] = defaultdict(set)
    by_key = {str(p["project_key"]): p for p in projects}
    for p in projects:
        for ident in runtime_view.project_identifiers(p):
            if ident.kind == "nsid" and ident.role == "owned_nsid":
                exact[ident.value].add(str(p["project_key"]))
                namespaces[ident.value.rsplit(".", 1)[0]].add(str(p["project_key"]))
    suppressed = identifier_storage.known_noncurrent_collection_nsids(conn) | identifier_storage.active_exclusions(conn, "nsid")
    assigned: dict[str, list[dict[str, Any]]] = defaultdict(list)
    created_keys: set[str] = set()
    conflicts = []
    rejected = []
    for sid, item in sorted(observed.items()):
        if item["status"] in {"deleted", "invalid"}:
            for key in exact.get(sid, set()):
                assigned[key].append(item)
            continue
        if sid in suppressed:
            rejected.append({"schema_id": sid, "reason": "suppressed_identifier"})
            continue
        authority = identifiers.derive_authority_domain(sid)
        if not domain_policy.authority_domain_allowed_for_import(authority):
            rejected.append({"schema_id": sid, "reason": "rejected_authority"})
            continue
        namespace = sid.rsplit(".", 1)[0]
        matches = exact.get(sid) or namespaces.get(namespace) or set()
        if len(matches) > 1:
            conflicts.append({"schema_id": sid, "project_keys": sorted(matches)})
            for key in matches:
                if by_key[key].get("tracking_state") == "ignore" or identifier_is_guarded(by_key[key], "nsid", sid):
                    rejected.append({"schema_id": sid, "project_key": key, "reason": "guarded_identifier"})
                    continue
                assigned[key].append({**item, "attribution_conflict": True})
            continue
        if matches:
            key = next(iter(matches))
            p = by_key[key]
            if p.get("tracking_state") == "ignore" or identifier_is_guarded(p, "nsid", sid):
                rejected.append({"schema_id": sid, "project_key": key, "reason": "guarded_identifier"})
                continue
        elif create_unmatched:
            key = records.allocate_new_project_key(conn)
            p = records.default_project_record(key, "nsid", sid)
            p["resolved_nsids"] = []
            p["input_nsids"] = []
            p["name"] = namespace
            by_key[key] = p
            created_keys.add(key)
        else:
            continue
        p["input_nsids"] = records.unique_list(list(p.get("input_nsids", [])) + ([] if sid in p.get("resolved_nsids", []) else [sid]))
        exact[sid].add(key)
        namespaces[namespace].add(key)
        assigned[key].append(item)
    updated = 0
    changes = []
    for key, items in assigned.items():
        p = by_key[key]
        previous = {i["schema_id"]: i for i in p.get("publication_schemas", [])}
        before = records.dump_json(p.get("publication_schemas", []))
        reasons = []
        for item in items:
            old = previous.get(item["schema_id"])
            if old != item:
                relation_source = f"{SOURCE}:{item['schema_id']}"
                p["relation_evidence"] = [r for r in p.get("relation_evidence", []) if r.get("source") != relation_source]
                for declaration in item.get("declarations", []):
                    p["relation_evidence"].append(relations.relation_record(
                        source=relation_source, classification="confirmed" if item["status"] == "verified" else "candidate",
                        observed_at=records.iso_now(), subject_type="nsid", subject_value=item["schema_id"],
                        predicate="mentions_collection", object_type="nsid", object_value=declaration["collection"],
                        locator=f"at://{declaration['publisher_did']}/com.atproto.lexicon.schema/{item['schema_id']}",
                        method="space_collections", context={"schema_hash": declaration["schema_hash"], "meaning": "declaration_only"},
                    ).to_dict())
            if item.get("attribution_conflict"):
                reasons.append(f"帰属候補が競合: {item['schema_id']}")
            if old and old != item and (item["status"] in {"deleted", "invalid"} or old.get("schema_type") != item.get("schema_type") or
                    old.get("status") == "verified" and (item["status"] != "verified" or old.get("publisher_did") != item.get("publisher_did") or old.get("record_hash") != item.get("record_hash"))):
                reasons.append(f"発行根拠の変化: {item['schema_id']}")
                from . import trust_graph
                trust_graph.remove_edges(p, source=f"lexicon_resolution:{item['schema_id']}")
            previous[item["schema_id"]] = item
        p["publication_schemas"] = [previous[n] for n in sorted(previous)]
        if reasons:
            p["publication_review"] = records.unique_list(list(p.get("publication_review", [])) + reasons)
            p["dismissed_review_kinds"] = [kind for kind in p.get("dismissed_review_kinds", []) if kind != "lexicon_publication_changed"]
        p["discovery_sources"] = records.unique_list(list(p.get("discovery_sources", [])) + [SOURCE])
        if key in created_keys or records.dump_json(p["publication_schemas"]) != before or reasons:
            changes.append(p)
            updated += int(key not in created_keys)
    if execute:
        with write_transaction(conn):
            for p in changes:
                records.save_project(conn, p, enriched=False, commit=False)
    return {"created": len(created_keys), "updated": updated, "conflicts": conflicts,
            "rejected_entries": rejected, "imported_project_keys": [p["project_key"] for p in changes],
            "unmatched": sum(1 for n in observed if not exact.get(n)),
            "mode": "execute" if execute else "dry_run"}
