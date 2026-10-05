"""Collection publication evidence and authority-scoped trust policy.

Publishers are evidence intermediaries, never project identity anchors. The
policy supplies a restricted graph to the shared evaluator; its edges describe
publication facts, not inferred ownership between two collections.
"""
from __future__ import annotations

from . import lexicon_types

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from . import identifier_storage, identifiers, relations, runtime_view, trust_graph


POLICY_VERSION = "accepted-lexicon-publisher-v1"
ADOPTION_SOURCE = "trust_policy:accepted_lexicon_publisher"
VALIDATION_VERSION = "lexicon-publication-v1"
PUBLICATION_PREDICATES = {"collection_authority_domain", "domain_publishes_lexicon_record"}


def publication_validation(nsid: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a DNS -> DID document -> schema response from the collector.

    Legacy snapshots without explicit DNS authority context are not upgraded.
    This validates a retrieved publication, not a signed repository proof.
    """
    did = str(result.get("did") or "")
    record = result.get("record") or {}
    document = result.get("did_doc") or {}
    if not isinstance(record, Mapping) or not isinstance(document, Mapping):
        return {}
    schema = record.get("value") or {}
    if not isinstance(schema, Mapping):
        return {}
    definitions = schema.get("defs") or {}
    if (
        not identifiers.is_valid_nsid(nsid)
        or not did.startswith("did:")
        or document.get("id") != did
        or result.get("authority_domain") != identifiers.derive_authority_domain(nsid)
        or not result.get("pds_url")
        or record.get("uri") != f"at://{did}/com.atproto.lexicon.schema/{nsid}"
        or schema.get("$type") != "com.atproto.lexicon.schema"
        or type(schema.get("lexicon")) is not int or schema["lexicon"] != 1
        or schema.get("id") != nsid
        or lexicon_types.schema_type(schema) == "unknown"
        or any(definition.get("type") == "space" and (not isinstance(definition.get("collections"), list)
               or any(not identifiers.is_valid_nsid(str(n)) for n in definition["collections"]))
               for definition in definitions.values())
    ):
        return {}
    return {
        "version": VALIDATION_VERSION,
        "schema_id": nsid,
        "schema_type": lexicon_types.schema_type(schema),
        "schema_hash": hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
    }


def adoption_evidence(project: Mapping[str, Any], nsid: str) -> list[dict[str, Any]]:
    """Use current DB adoption claims, or their explicit investigation snapshot."""
    rows = project.get(runtime_view.RUNTIME_ADOPTION_ROWS, project.get("identifier_adoptions"))
    if isinstance(rows, list):
        return [dict(row) for row in rows if (
            row.get("kind") == "nsid" and row.get("value") == nsid and row.get("role") == "owned_nsid"
            and not row.get("cleared_at") and not row.get("review_required")
            and row.get("project_key") == project.get("project_key")
            and not any(item.get("type") == "nsid" and item.get("value") == nsid
                        and item.get("source") == row.get("source")
                        and item.get("added_at") == row.get("created_at") and item.get("cleared_at")
                        and (not item.get("origin_project_key")
                             or item.get("origin_project_key") == row.get("origin_project_key"))
                        for item in project.get("confirmed_identifiers", []))
        )]
    # Unsaved projects have decisions but no adoption rows yet, as in the
    # existing anchor policy. Never infer an adoption from classification alone.
    return [{
        "source": item.get("source", ""),
        "adoption_kind": identifier_storage.adoption_kind_for_source(str(item.get("source") or "")),
        "created_at": item.get("added_at", ""),
        "source_ref": item.get("source_ref", ""),
    } for item in project.get("confirmed_identifiers", []) if (
        item.get("type") == "nsid" and item.get("value") == nsid
        and item.get("role") in {None, "", "owned_nsid"} and not item.get("cleared_at")
    )]


def independent_adoptions(project: Mapping[str, Any], nsid: str) -> list[dict[str, Any]]:
    return [row for row in adoption_evidence(project, nsid)
            if row.get("adoption_kind") != "trust_policy"
            and identifier_storage.adoption_kind_for_source(str(row.get("source") or "")) != "trust_policy"]


def publisher_only_adoption(project: Mapping[str, Any], nsid: str) -> bool:
    rows = adoption_evidence(project, nsid)
    # A newly emitted decision has not been reconciled into DB adoption rows
    # yet. Its restriction must already apply to the in-memory merge view.
    rows += [item for item in project.get("confirmed_identifiers", [])
             if item.get("type") == "nsid" and item.get("value") == nsid and not item.get("cleared_at")]
    return bool(rows) and all(row.get("source") == ADOPTION_SOURCE for row in rows)


def publication_matches_catalog(project: Mapping[str, Any], nsid: str, entry: Mapping[str, Any]) -> bool:
    for item in project.get("publication_schemas", []):
        if item.get("schema_id") != nsid:
            continue
        if item.get("status") in {"deleted", "invalid"}:
            return False
        if item.get("authority_dids") and set(item["authority_dids"]) != {entry.get("repo_did")}:
            return False
        witnesses = {(p.get("did"), p.get("hash")) for p in item.get("publishers", [])}
        if (entry.get("repo_did"), entry.get("publication_validation", {}).get("schema_hash")) not in witnesses:
            return False
        if item.get("publisher_did") and item["publisher_did"] != entry.get("repo_did"):
            return False
        try:
            if datetime.fromisoformat(str(entry.get("resolved_at") or "").replace("Z", "+00:00")) + timedelta(days=7) <= datetime.now(timezone.utc):
                return False
        except (ValueError, TypeError):
            return False
    return True


def verified_publications(project: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    # A newer response (including an unvalidated response or publisher change)
    # supersedes an old witness. Select latest before checking validity.
    latest: dict[str, dict[str, Any]] = {}
    authorities: dict[str, tuple[str, set[str]]] = {}
    for item in project.get("lexicon_resolution_entries", []):
        nsid = str(item.get("input_nsid") or item.get("resolved_nsid") or "")
        if nsid not in latest or str(item.get("resolved_at") or "") >= str(latest[nsid].get("resolved_at") or ""):
            latest[nsid] = dict(item)
        authority = str(item.get("authority_domain") or "")
        if authority and authority == identifiers.derive_authority_domain(nsid):
            observed_at = str(item.get("resolved_at") or "")
            publisher = str(item.get("repo_did") or "")
            if authority not in authorities or observed_at > authorities[authority][0]:
                authorities[authority] = (observed_at, {publisher})
            elif observed_at == authorities[authority][0]:
                authorities[authority][1].add(publisher)
    return {nsid: entry for nsid, entry in latest.items() if (
        identifiers.is_valid_nsid(nsid)
        and entry.get("input_nsid") == nsid
        and entry.get("resolved_nsid") == nsid
        and entry.get("authority_domain") == identifiers.derive_authority_domain(nsid)
        and authorities[entry["authority_domain"]][1] == {entry.get("repo_did")}
        and entry.get("record_uri") == f"at://{entry.get('repo_did')}/com.atproto.lexicon.schema/{nsid}"
        and entry.get("resolved_at")
        and isinstance(entry.get("publication_validation"), Mapping)
        and entry["publication_validation"].get("version") == VALIDATION_VERSION
        and entry["publication_validation"].get("schema_id") == nsid
        and entry["publication_validation"].get("schema_type") in lexicon_types.PRIMARY_TYPES | {"definitions"}
        and publication_matches_catalog(project, nsid, entry)
        and entry["publication_validation"].get("schema_hash")
        and not entry["publication_validation"].get("type_changed")
    )}


def evaluate_publisher_support(project: Mapping[str, Any], nsid: str) -> dict[str, Any]:
    publications = verified_publications(project)
    node_kind = lambda value: "collection" if publications.get(value, {}).get("publication_validation", {}).get("schema_type", "record") == "record" else "nsid"
    candidate = trust_graph.node(node_kind(nsid), nsid)
    unsupported = {"auto_confirm": False, "reason": "no_scoped_publication_path", "candidate": candidate, "policy_version": POLICY_VERSION}
    claims = {item.value: item for item in runtime_view.project_identifiers(project)
              if item.kind == "nsid" and item.role == "owned_nsid"}
    if nsid not in claims or relations.identifier_is_guarded(project, "nsid", nsid):
        return unsupported
    publications = verified_publications(project)
    target = publications.get(nsid)
    if not target:
        return unsupported
    roots = {value: independent_adoptions(project, value) for value, claim in claims.items()
             if value != nsid and claim.classification == "confirmed"
             and not relations.identifier_is_guarded(project, "nsid", value)}
    roots = {value: rows for value, rows in roots.items() if rows and value in publications
             and publications[value]["authority_domain"] == target["authority_domain"]
             and publications[value]["repo_did"] == target["repo_did"]}
    if not roots:
        return unsupported
    scope = {"project_key": project.get("project_key"), "role": "owned_nsid",
             "authority_domain": target["authority_domain"], "publisher_did": target["repo_did"]}
    edges = []
    for value in [nsid, *sorted(roots)]:
        entry = publications[value]
        collection = trust_graph.node(node_kind(value), value)
        domain = trust_graph.node("domain", entry["authority_domain"])
        for subject, predicate, obj in (
            (collection, "collection_authority_domain", domain),
            (domain, "domain_publishes_lexicon_record", collection),
        ):
            edges.append({"subject": subject, "predicate": predicate, "object": obj,
                          "source": f"lexicon_resolution:{value}", "locator": entry["record_uri"],
                          "method": "lexicon_dns_and_repo_record", "observed_at": entry["resolved_at"],
                          "classification": "confirmed", "eligible_for_auto_confirm": True,
                          "context": {**scope, "publication_validation": entry["publication_validation"]}})
    evaluation = trust_graph.evaluate_auto_confirm(
        project, candidate=candidate,
        anchors=[trust_graph.node(node_kind(value), value) for value in sorted(roots)],
        allowed_node_kinds={"collection", "nsid", "domain"}, allowed_predicates=PUBLICATION_PREDICATES,
        scoped_edges=edges,
    )
    evaluation.update(policy_version=POLICY_VERSION, scope=scope)
    if evaluation.get("auto_confirm"):
        evaluation["reason"] = "accepted_lexicon_publisher"
        evaluation["anchor_adoptions"] = roots[evaluation["anchor"]["value"]]
    return evaluation
