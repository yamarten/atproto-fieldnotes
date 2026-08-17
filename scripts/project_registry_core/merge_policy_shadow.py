from __future__ import annotations

import collections
import contextlib
import hashlib
import itertools
import json
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

from . import identifiers as identifier_core
from . import domain_policy
from . import project_record
from . import registry
from . import runtime_view
from . import storage


REPORT_SCHEMA_VERSION = 1
POLICY_VERSION = "merge-policy-shadow-v4"
VERDICTS = (
    "RECOMMEND_MERGE_REVIEW",
    "REVIEW",
    "KEEP_SEPARATE",
    "NOT_EVALUATED",
)

COMPONENT_ROLES = {
    "service",
    "source_repository",
    "owned_nsid",
    "project_account",
    "project_handle",
    "brand_did",
}
EXCLUSIVE_ROLE_GROUPS = {
    "service": {"service"},
    "owned_nsid": {"owned_nsid"},
    "project_account": {"project_account", "project_handle", "brand_did"},
}
BRIDGING_PREDICATES = {
    "source_repository",
    "service_links_repo",
    "repo_links_service",
    "defines_schema",
    "did_publishes_schema",
    "official_account",
    "operates_service",
    "official_alternate_surface",
    "collection_canonical_source_repo",
}
FRAGMENT_RELATION_PREDICATES = {
    "defines_schema",
    "requests_permission",
    "uses_collection",
}
TEMPORAL_OR_DERIVATION_FLAGS = {
    "fork",
    "mirror",
    "tenant",
    "instance",
    "successor",
    "predecessor",
    "ownership_transfer",
}

KIND_MAP = {
    "nsid": "collection",
    "collection": "collection",
    "service": "service_url",
    "service_url": "service_url",
    "repo": "repo_url",
    "repo_url": "repo_url",
    "did": "did",
    "handle": "handle",
    "domain": "domain",
    "account": "account",
}

RETROSPECTIVE_COHORT_ORDER = (
    "collection_fragment",
    "confirmed_exclusive_component",
    "recorded_origin_matches_component",
    "shared_adoption_origin",
    "direct_component_relation",
    "confirmed_source_repository",
    "candidate_component_overlap",
    "same_website",
    "legacy_repository_overlap",
    "legacy_recorded_namespace_overlap",
    "collection_namespace_context",
    "collection_authority_context",
    "creator_namespace_context",
    "shared_alias",
    "shared_domain_context",
    "no_reconstructed_signal",
)


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _normalize_value(kind: str, value: str) -> str:
    text = str(value or "").strip()
    if kind in {"service_url", "repo_url"}:
        return identifier_core.normalize_url(text)
    if kind == "collection":
        return identifier_core.normalize_nsid(text)
    if kind in {"handle", "domain"}:
        return text.lower().rstrip(".")
    return text


def _node(kind: str, value: str) -> tuple[str, str]:
    normalized_kind = KIND_MAP.get(str(kind or ""), str(kind or ""))
    return normalized_kind, _normalize_value(normalized_kind, value)


def _claim_node(claim: dict[str, Any]) -> tuple[str, str]:
    return _node(str(claim.get("kind") or ""), str(claim.get("value") or ""))


def _claim_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    getter = row.__getitem__
    return {
        "kind": str(getter("kind")),
        "value": str(getter("value")),
        "role": str(getter("role")),
        "classification": str(getter("classification")),
        "is_primary": bool(getter("is_primary")),
        "provider": str(getter("provider")),
        "metadata": _json(getter("metadata_json"), {}),
        "observed_at": str(getter("observed_at")),
        "standing": "current",
    }


def _relation_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    getter = row.__getitem__
    return {
        "subject": _node(str(getter("subject_type")), str(getter("subject_value"))),
        "predicate": str(getter("predicate")),
        "object": _node(str(getter("object_type")), str(getter("object_value"))),
        "classification": str(getter("classification")),
        "source": str(getter("source")),
        "method": str(getter("method")),
        "locator": str(getter("locator")),
    }


def _snapshot_relation_payload(item: dict[str, Any]) -> dict[str, Any] | None:
    relation = item.get("relation")
    if not isinstance(relation, dict):
        return None
    subject = relation.get("subject")
    obj = relation.get("object")
    if not isinstance(subject, dict) or not isinstance(obj, dict):
        return None
    return {
        "subject": _node(str(subject.get("type") or ""), str(subject.get("value") or "")),
        "predicate": str(relation.get("predicate") or ""),
        "object": _node(str(obj.get("type") or ""), str(obj.get("value") or "")),
        "classification": str(item.get("classification") or "candidate"),
        "source": str(item.get("source") or ""),
        "method": str(relation.get("method") or ""),
        "locator": str(relation.get("locator") or ""),
    }


def _legacy_payload(
    payload: dict[str, Any],
    claims: Iterable[dict[str, Any]],
    separate_project_keys: Iterable[str],
    decisions: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    original_seed_type = str(payload.get("seed_type") or "")
    original_seed_value = str(payload.get("seed_value") or "")
    shaped = dict(payload)
    shaped.update({
        "input_nsids": [],
        "resolved_nsids": [],
        "lexicons": [],
        "external_lexicons": [],
        "recorded_lexicon_namespaces": [],
        "authority_domains": [],
        "service_urls": [],
        "service_url_candidates": [],
        "source_code_urls": [],
        "candidate_source_code_urls": [],
        "source_repo_records": [],
        "developer_accounts": [],
        "candidate_developer_accounts": [],
        "bluesky_handles": [],
        "tangled_handles": [],
        "manual_seeds": [],
    })
    shaped["confirmed_identifiers"] = []
    for claim in claims:
        kind = str(claim.get("kind") or "")
        value = str(claim.get("value") or "")
        classification = str(claim.get("classification") or "")
        role = str(claim.get("role") or "")
        metadata = dict(claim.get("metadata") or {})
        if not value:
            continue
        if kind == "service_url":
            field = "service_urls" if classification == "confirmed" else "service_url_candidates"
            shaped[field] = project_record.add_unique(shaped[field], value)
        elif kind == "repo_url":
            field = "source_code_urls" if classification == "confirmed" else "candidate_source_code_urls"
            shaped[field] = project_record.add_unique(shaped[field], value)
            if bool(claim.get("is_primary")) and classification == "confirmed":
                shaped["source_code_url"] = value
            if metadata and role == "source_repository_record":
                shaped["source_repo_records"].append({**metadata, "confidence": classification})
        elif kind == "nsid" and role == "owned_nsid":
            field = "resolved_nsids" if classification == "confirmed" else "input_nsids"
            shaped[field] = project_record.add_unique(shaped[field], value)
        elif kind == "domain" and role == "authority_domain":
            shaped["authority_domains"] = project_record.add_unique(shaped["authority_domains"], value)
        elif kind == "did" and role == "brand_did" and classification == "confirmed":
            shaped["brand_did"] = value
    for decision in decisions:
        if decision.get("kind") != "confirm_identifier":
            continue
        metadata = dict(decision.get("metadata") or {})
        shaped["confirmed_identifiers"].append(metadata or {
            "type": str(decision.get("target_type") or ""),
            "value": str(decision.get("target_value") or ""),
            "note": str(decision.get("note") or ""),
            "added_at": str(decision.get("created_at") or ""),
            "cleared_at": str(decision.get("cleared_at") or ""),
        })
    shaped["separate_project_keys"] = sorted({str(value) for value in separate_project_keys if str(value)})
    runtime_view.mark_complete(shaped)
    normalized = project_record.ensure_project_shape(shaped)
    if original_seed_type and original_seed_value:
        normalized["seed_type"] = original_seed_type
        normalized["seed_value"] = original_seed_value
    return normalized


def _model_from_snapshot(snapshot: dict[str, Any], *, project_key: str = "") -> dict[str, Any]:
    original_seed_type = str(snapshot.get("seed_type") or "")
    original_seed_value = str(snapshot.get("seed_value") or "")
    payload = project_record.ensure_project_shape(dict(snapshot))
    if original_seed_type and original_seed_value:
        payload["seed_type"] = original_seed_type
        payload["seed_value"] = original_seed_value
    claims = [
        {
            "kind": item.kind,
            "value": item.value,
            "role": item.role,
            "classification": item.classification,
            "standing": "current",
        }
        for item in runtime_view.project_identifiers(payload)
    ]
    relations: list[dict[str, Any]] = []
    for item in payload.get("relation_evidence", []):
        if not isinstance(item, dict):
            continue
        relation = _snapshot_relation_payload(item)
        if relation is not None:
            relations.append(relation)
    key = project_key or str(payload.get("project_key") or "")
    separate = {str(value) for value in payload.get("separate_project_keys", []) if str(value)}
    legacy = dict(payload)
    legacy["separate_project_keys"] = sorted(separate)
    return {
        "project_key": key,
        "name": str(payload.get("name") or ""),
        "website_url": str(payload.get("website_url") or ""),
        "claims": claims,
        "relations": relations,
        "decisions": [],
        "adoptions": [],
        "separate_project_keys": separate,
        "legacy": legacy,
    }


def load_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM projects ORDER BY project_key"):
        payload = project_record.project_from_row_payload(row)
        key = str(row["project_key"])
        projects[key] = {
            "project_key": key,
            "name": str(row["name"]),
            "website_url": str(row["website_url"]),
            "claims": [],
            "relations": [],
            "decisions": [],
            "adoptions": [],
            "separate_project_keys": set(),
            "raw_payload": payload,
        }
    for row in conn.execute("SELECT * FROM project_identifiers ORDER BY project_key, ordinal"):
        projects[str(row["project_key"])]["claims"].append(_claim_payload(row))
    for row in conn.execute("SELECT * FROM project_relations ORDER BY project_key, ordinal"):
        projects[str(row["project_key"])]["relations"].append(_relation_payload(row))
    for row in conn.execute("SELECT * FROM project_decisions ORDER BY project_key, ordinal"):
        key = str(row["project_key"])
        metadata = _json(row["metadata_json"], {})
        decision = {
            "kind": str(row["kind"]),
            "target_type": str(row["target_type"]),
            "target_value": str(row["target_value"]),
            "target_role": str(row["target_role"]) if "target_role" in row.keys() else "",
            "created_at": str(row["created_at"]),
            "cleared_at": str(row["cleared_at"]),
            "source": str(metadata.get("source") or ""),
            "note": str(row["note"]),
            "metadata": metadata,
        }
        projects[key]["decisions"].append(decision)
        if decision["kind"] == "separate_project" and not decision["cleared_at"]:
            projects[key]["separate_project_keys"].add(decision["target_value"])
    if _table_exists(conn, "project_identifier_adoptions"):
        for row in conn.execute(
            "SELECT * FROM project_identifier_adoptions WHERE cleared_at='' ORDER BY project_key, created_at, adoption_id"
        ):
            projects[str(row["project_key"])]["adoptions"].append({key: row[key] for key in row.keys()})
    for project in projects.values():
        project["legacy"] = _legacy_payload(
            project.pop("raw_payload"),
            project["claims"],
            project["separate_project_keys"],
            project["decisions"],
        )
    return list(projects.values())


def _confirmed_component_nodes(project: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        _claim_node(claim)
        for claim in project.get("claims", [])
        if claim.get("classification") == "confirmed" and claim.get("role") in COMPONENT_ROLES
    }


def _values_for_roles(project: dict[str, Any], roles: set[str], *, confirmed_only: bool = True) -> set[tuple[str, str]]:
    return {
        _claim_node(claim)
        for claim in project.get("claims", [])
        if claim.get("role") in roles
        and (not confirmed_only or claim.get("classification") == "confirmed")
    }


def _candidate_values_for_roles(project: dict[str, Any], roles: set[str]) -> set[tuple[str, str]]:
    return {
        _claim_node(claim)
        for claim in project.get("claims", [])
        if claim.get("role") in roles and claim.get("classification") == "candidate"
    }


def _confirmed_owned_nsids(project: dict[str, Any]) -> set[str]:
    return {
        value
        for kind, value in _values_for_roles(project, {"owned_nsid"})
        if kind == "collection"
    }


def _seed_nsid(project: dict[str, Any]) -> str:
    legacy = project.get("legacy") or {}
    if str(legacy.get("seed_type") or "") != "nsid":
        return ""
    return identifier_core.normalize_nsid(str(legacy.get("seed_value") or ""))


def _collection_context_nsids(project: dict[str, Any]) -> set[str]:
    values = set(_confirmed_owned_nsids(project))
    seed = _seed_nsid(project)
    if seed:
        values.add(seed)
    return values


def _collection_only_component_record(project: dict[str, Any]) -> bool:
    nodes = _confirmed_component_nodes(project)
    return bool(nodes) and all(kind == "collection" for kind, _ in nodes)


def _independent_component_nodes(project: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        node
        for node in _confirmed_component_nodes(project)
        if node[0] in {"service_url", "repo_url", "did", "handle"}
    }


def _collection_authority_namespaces(nsids: Iterable[str]) -> set[str]:
    output: set[str] = set()
    for nsid in nsids:
        namespace = registry.registrable_namespace_from_namespace(nsid)
        if namespace:
            output.add(namespace)
    return output


def _collection_direct_namespaces(nsids: Iterable[str]) -> set[str]:
    return {
        namespace
        for value in nsids
        if (namespace := project_record.namespace_from_collection_nsid(value))
    }


def _name_token(project: dict[str, Any]) -> str:
    return identifier_core.normalize_alias_token(str(project.get("name") or ""))


def _candidate_service_host_tokens(project: dict[str, Any]) -> set[str]:
    output: set[str] = set()
    for value in (project.get("legacy") or {}).get("service_url_candidates", []):
        normalized = identifier_core.normalize_url(str(value or ""))
        host = urllib.parse.urlparse(normalized).netloc.lower()
        token = identifier_core.normalize_alias_token(host)
        if token:
            output.add(token)
    return output


def _fragment_relation_matches(fragment: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    fragment_collections = _confirmed_owned_nsids(fragment)
    target_components = _confirmed_component_nodes(target)
    output: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for relation in [*fragment.get("relations", []), *target.get("relations", [])]:
        if relation.get("predicate") not in FRAGMENT_RELATION_PREDICATES:
            continue
        subject = tuple(relation["subject"])
        obj = tuple(relation["object"])
        if subject not in target_components:
            continue
        if obj[0] != "collection" or obj[1] not in fragment_collections:
            continue
        key = (subject, relation["predicate"], obj, relation.get("source", ""), relation.get("method", ""))
        if key in seen:
            continue
        seen.add(key)
        output.append({
            "subject": list(subject),
            "predicate": relation["predicate"],
            "object": list(obj),
            "classification": relation.get("classification", ""),
            "source": relation.get("source", ""),
            "method": relation.get("method", ""),
        })
    return output


def _fragment_absorption_candidate(
    fragment: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any] | None:
    if not _collection_only_component_record(fragment):
        return None
    if str((fragment.get("legacy") or {}).get("category") or "") == "lexicon":
        return None
    fragment_nsids = _confirmed_owned_nsids(fragment)
    target_nsids = _confirmed_owned_nsids(target)
    if not fragment_nsids:
        return None

    exact_overlap = sorted(fragment_nsids & target_nsids)
    exact_subset = bool(exact_overlap) and fragment_nsids < target_nsids
    target_context = _collection_context_nsids(target)
    shared_authorities = sorted(
        _collection_authority_namespaces(fragment_nsids)
        & _collection_authority_namespaces(target_context)
    )
    shared_direct_namespaces = sorted(
        _collection_direct_namespaces(fragment_nsids)
        & _collection_direct_namespaces(target_context)
    )
    fragment_name = _name_token(fragment)
    target_name = _name_token(target)
    fragment_name_matches_candidate_service = fragment_name in _candidate_service_host_tokens(fragment)
    target_name_in_fragment = bool(
        not fragment_name_matches_candidate_service
        and len(target_name) >= 5
        and target_name in fragment_name
    )
    fragment_aliases = project_record.project_merge_aliases(fragment["legacy"])
    if fragment_name_matches_candidate_service:
        fragment_aliases.discard(fragment_name)
    shared_aliases = sorted(fragment_aliases & project_record.project_merge_aliases(target["legacy"]))
    relation_matches = _fragment_relation_matches(fragment, target)
    independent_target = sorted(_independent_component_nodes(target))

    if exact_subset:
        rule = "exact_owned_collection_subset"
    elif independent_target and shared_authorities and (
        shared_direct_namespaces
        or shared_aliases
        or target_name_in_fragment
    ):
        rule = "collection_fragment_with_encompassing_project"
    else:
        return None

    return {
        "fragment_project_key": str(fragment.get("project_key") or ""),
        "target_project_key": str(target.get("project_key") or ""),
        "rule": rule,
        "fragment_collections": sorted(fragment_nsids),
        "target_collection_count": len(target_nsids),
        "exact_collection_overlap": exact_overlap,
        "shared_authority_namespaces": shared_authorities,
        "shared_direct_namespaces": shared_direct_namespaces,
        "shared_aliases": shared_aliases,
        "fragment_name_matches_candidate_service": fragment_name_matches_candidate_service,
        "target_name_in_fragment_name": target_name_in_fragment,
        "target_independent_components": [list(item) for item in independent_target],
        "composition_relations": relation_matches,
    }


def _fragment_absorption_candidates(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in (
            _fragment_absorption_candidate(left, right),
            _fragment_absorption_candidate(right, left),
        )
        if item is not None
    ]
    return sorted(
        candidates,
        key=lambda item: (item["fragment_project_key"], item["target_project_key"], item["rule"]),
    )


def _shared_adoption_origins(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    def keys(project: dict[str, Any]) -> set[tuple[str, str, str, str, str, str]]:
        output = set()
        for item in project.get("adoptions", []):
            source_ref = str(item.get("source_ref") or "")
            if not source_ref:
                continue
            output.add((
                str(item.get("kind") or ""),
                str(item.get("value") or ""),
                str(item.get("role") or ""),
                str(item.get("source") or ""),
                source_ref,
                str(item.get("input_fingerprint") or ""),
            ))
        return output

    return ["|".join(item) for item in sorted(keys(left) & keys(right))]


def _direct_component_relations(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    left_nodes = _confirmed_component_nodes(left)
    right_nodes = _confirmed_component_nodes(right)
    output: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for relation in [*left.get("relations", []), *right.get("relations", [])]:
        if relation.get("predicate") not in BRIDGING_PREDICATES:
            continue
        if relation.get("classification") != "confirmed":
            continue
        subject = tuple(relation["subject"])
        obj = tuple(relation["object"])
        crosses = (
            subject in left_nodes and obj in right_nodes
        ) or (
            subject in right_nodes and obj in left_nodes
        )
        if not crosses:
            continue
        key = (subject, relation["predicate"], obj, relation.get("source", ""), relation.get("method", ""))
        if key in seen:
            continue
        seen.add(key)
        output.append({
            "subject": list(subject),
            "predicate": relation["predicate"],
            "object": list(obj),
            "source": relation.get("source", ""),
            "method": relation.get("method", ""),
        })
    return output


def _pair_signals(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    flags: Iterable[str] = (),
) -> dict[str, Any]:
    shared_exclusive: dict[str, list[list[str]]] = {}
    for label, roles in EXCLUSIVE_ROLE_GROUPS.items():
        shared = sorted(_values_for_roles(left, roles) & _values_for_roles(right, roles))
        if shared:
            shared_exclusive[label] = [list(item) for item in shared]
    shared_repositories = sorted(
        _values_for_roles(left, {"source_repository"})
        & _values_for_roles(right, {"source_repository"})
    )
    left_ns = {
        project_record.namespace_from_collection_nsid(value)
        for kind, value in _values_for_roles(left, {"owned_nsid"})
        if kind == "collection" and project_record.namespace_from_collection_nsid(value)
    }
    right_ns = {
        project_record.namespace_from_collection_nsid(value)
        for kind, value in _values_for_roles(right, {"owned_nsid"})
        if kind == "collection" and project_record.namespace_from_collection_nsid(value)
    }
    alias_overlap = sorted(
        project_record.project_merge_aliases(left["legacy"])
        & project_record.project_merge_aliases(right["legacy"])
    )
    return {
        "shared_exclusive_components": shared_exclusive,
        "fragment_absorption_candidates": _fragment_absorption_candidates(left, right),
        "direct_component_relations": _direct_component_relations(left, right),
        "shared_source_repositories": [list(item) for item in shared_repositories],
        "shared_collection_namespaces": sorted(left_ns & right_ns),
        "shared_aliases": alias_overlap,
        "shared_adoption_origins": _shared_adoption_origins(left, right),
        "flags": sorted({str(flag) for flag in flags if str(flag)}),
        "distinct_confirmed_component_count": len(
            _confirmed_component_nodes(left) ^ _confirmed_component_nodes(right)
        ),
    }


def _recorded_origins(project: dict[str, Any]) -> set[tuple[str, str]]:
    legacy = project.get("legacy") or {}
    output: set[tuple[str, str]] = set()

    def add(kind: str, value: str) -> None:
        normalized = _node(kind, value)
        if normalized[0] and normalized[1]:
            output.add(normalized)

    add(str(legacy.get("seed_type") or ""), str(legacy.get("seed_value") or ""))
    for item in legacy.get("manual_seeds", []):
        if not isinstance(item, dict):
            continue
        add(str(item.get("type") or ""), str(item.get("value") or ""))
    return output


def _retrospective_features(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    signals = _pair_signals(left, right)
    left_components = _confirmed_component_nodes(left)
    right_components = _confirmed_component_nodes(right)
    left_candidate_components = _candidate_values_for_roles(left, COMPONENT_ROLES)
    right_candidate_components = _candidate_values_for_roles(right, COMPONENT_ROLES)
    shared_candidates = sorted(left_candidate_components & right_candidate_components)
    left_origins = _recorded_origins(left)
    right_origins = _recorded_origins(right)
    origin_matches = {
        "left_origin_in_right": sorted(left_origins & right_components),
        "right_origin_in_left": sorted(right_origins & left_components),
        "same_recorded_origin": sorted(left_origins & right_origins),
    }
    left_legacy = left.get("legacy") or {}
    right_legacy = right.get("legacy") or {}
    left_profile = _current_v2_profile(left_legacy)
    right_profile = _current_v2_profile(right_legacy)
    legacy_reason_keys = sorted(
        field
        for field in (
            "source_code_urls",
            "github_repo_urls",
            "candidate_source_code_urls",
            "service_urls",
            "recorded_lexicon_namespaces",
            "authority_service_families",
            "domain_identity_tokens",
            "merge_aliases",
        )
        if left_profile[field] & right_profile[field]
    )
    if (
        set(left_legacy.get("record_creators", [])) & set(right_legacy.get("record_creators", []))
        and registry.namespace_lineage_overlap(left_legacy, right_legacy)
    ):
        legacy_reason_keys.append("record_creators_and_namespace_lineage")
    domain_context = {
        "authority_service_families": sorted(
            left_profile["authority_service_families"] & right_profile["authority_service_families"]
        ),
        "domain_identity_tokens": sorted(
            left_profile["domain_identity_tokens"] & right_profile["domain_identity_tokens"]
        ),
    }
    left_website = identifier_core.normalize_url(str(left.get("website_url") or ""))
    right_website = identifier_core.normalize_url(str(right.get("website_url") or ""))
    same_website = left_website if left_website and left_website == right_website else ""
    shared_collection_authorities = sorted(
        _collection_authority_namespaces(_collection_context_nsids(left))
        & _collection_authority_namespaces(_collection_context_nsids(right))
    )
    containment = {
        "left_components_contained_by_right": bool(left_components and left_components <= right_components),
        "right_components_contained_by_left": bool(right_components and right_components <= left_components),
    }

    tags: list[str] = []
    if signals["fragment_absorption_candidates"]:
        tags.append("collection_fragment")
    if signals["shared_exclusive_components"]:
        tags.append("confirmed_exclusive_component")
    if any(origin_matches.values()):
        tags.append("recorded_origin_matches_component")
    if signals["shared_adoption_origins"]:
        tags.append("shared_adoption_origin")
    if signals["direct_component_relations"]:
        tags.append("direct_component_relation")
    if signals["shared_source_repositories"]:
        tags.append("confirmed_source_repository")
    if shared_candidates:
        tags.append("candidate_component_overlap")
    if same_website:
        tags.append("same_website")
    if any(key in legacy_reason_keys for key in ("source_code_urls", "github_repo_urls")):
        tags.append("legacy_repository_overlap")
    if "recorded_lexicon_namespaces" in legacy_reason_keys:
        tags.append("legacy_recorded_namespace_overlap")
    if signals["shared_collection_namespaces"]:
        tags.append("collection_namespace_context")
    if shared_collection_authorities:
        tags.append("collection_authority_context")
    if "record_creators_and_namespace_lineage" in legacy_reason_keys:
        tags.append("creator_namespace_context")
    if signals["shared_aliases"]:
        tags.append("shared_alias")
    if any(domain_context.values()):
        tags.append("shared_domain_context")
    if not tags:
        tags.append("no_reconstructed_signal")
    primary_cohort = next(label for label in RETROSPECTIVE_COHORT_ORDER if label in tags)
    return {
        "primary_cohort": primary_cohort,
        "tags": tags,
        "shared_exclusive_components": signals["shared_exclusive_components"],
        "shared_source_repositories": signals["shared_source_repositories"],
        "shared_adoption_origins": signals["shared_adoption_origins"],
        "direct_component_relations": signals["direct_component_relations"],
        "shared_candidate_components": [list(item) for item in shared_candidates],
        "shared_collection_namespaces": signals["shared_collection_namespaces"],
        "shared_collection_authorities": shared_collection_authorities,
        "shared_aliases": signals["shared_aliases"],
        "same_website": same_website,
        "origin_matches": {key: [list(item) for item in values] for key, values in origin_matches.items()},
        "component_counts": {"left": len(left_components), "right": len(right_components)},
        "containment": containment,
        "domain_context": domain_context,
        "legacy_overlap_reason_keys": legacy_reason_keys,
    }


def _component_count_bucket(count: int) -> str:
    if count <= 1:
        return str(count)
    if count <= 4:
        return "2-4"
    return "5+"


def _retrospective_detail_labels(features: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    labels.extend(
        f"exclusive_role:{role}"
        for role in sorted(features["shared_exclusive_components"])
    )
    labels.extend(
        f"candidate_kind:{kind}"
        for kind in sorted({str(item[0]) for item in features["shared_candidate_components"]})
    )
    for direction, values in features["origin_matches"].items():
        labels.extend(f"origin:{direction}:{item[0]}" for item in values)
    labels.extend(
        f"containment:{direction}"
        for direction, matched in features["containment"].items()
        if matched
    )
    labels.append(f"left_component_count:{_component_count_bucket(int(features['component_counts']['left']))}")
    labels.append(f"right_component_count:{_component_count_bucket(int(features['component_counts']['right']))}")
    return sorted(set(labels))


def evaluate_pair(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    flags: Iterable[str] = (),
) -> dict[str, Any]:
    signals = _pair_signals(left, right, flags=flags)
    left_key = str(left.get("project_key") or "")
    right_key = str(right.get("project_key") or "")
    separate = (
        right_key in left.get("separate_project_keys", set())
        or left_key in right.get("separate_project_keys", set())
    )
    typed_flags = sorted(set(signals["flags"]) & TEMPORAL_OR_DERIVATION_FLAGS)
    if separate:
        verdict = "KEEP_SEPARATE"
        reason = "active_separate_project_decision"
        recommendation_kind = ""
    elif typed_flags:
        verdict = "NOT_EVALUATED"
        reason = "temporal_or_derivation_relation_requires_separate_review"
        recommendation_kind = ""
    elif signals["fragment_absorption_candidates"]:
        verdict = "RECOMMEND_MERGE_REVIEW"
        reason = "collection_fragment_has_encompassing_project"
        recommendation_kind = "fragment_absorption"
    elif signals["shared_exclusive_components"] and signals["direct_component_relations"]:
        verdict = "RECOMMEND_MERGE_REVIEW"
        reason = "exclusive_component_collision_with_direct_composition_relation"
        recommendation_kind = "duplicate_record"
    else:
        verdict = "REVIEW"
        recommendation_kind = ""
        if signals["shared_exclusive_components"]:
            reason = "exclusive_component_collision_without_direct_composition_relation"
        elif signals["shared_source_repositories"]:
            reason = "shared_repository_is_not_project_identity"
        elif signals["shared_collection_namespaces"]:
            reason = "shared_namespace_requires_full_claim_review"
        elif signals["shared_adoption_origins"]:
            reason = "shared_operational_provenance_needs_external_identity_review"
        else:
            reason = "weak_similarity_only"
    return {
        "left_project_key": left_key,
        "right_project_key": right_key,
        "verdict": verdict,
        "reason": reason,
        "recommendation_kind": recommendation_kind,
        "signals": signals,
        "automatic_execution_allowed": False,
    }


def load_case_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported merge policy case catalog schema")
    return payload


def _case_project(case_id: str, side: str, payload: dict[str, Any]) -> dict[str, Any]:
    project_key = str(payload.get("project_key") or f"{case_id}:{side}")
    claims = [
        {
            "kind": str(item["kind"]),
            "value": str(item["value"]),
            "role": str(item["role"]),
            "classification": str(item.get("classification") or "confirmed"),
            "standing": "current",
        }
        for item in payload.get("claims", [])
    ]
    relations = []
    for item in payload.get("relations", []):
        relations.append({
            "subject": _node(str(item["subject"][0]), str(item["subject"][1])),
            "predicate": str(item["predicate"]),
            "object": _node(str(item["object"][0]), str(item["object"][1])),
            "classification": str(item.get("classification") or "confirmed"),
            "source": str(item.get("source") or "fixture"),
            "method": str(item.get("method") or "fixture"),
            "locator": "",
        })
    separate = {str(value) for value in payload.get("separate_project_keys", [])}
    raw = {
        "project_key": project_key,
        "name": str(payload.get("name") or ""),
        "website_url": str(payload.get("website_url") or ""),
        "category": str(payload.get("category") or ""),
        "seed_type": str(payload.get("seed_type") or ""),
        "seed_value": str(payload.get("seed_value") or ""),
    }
    return {
        **raw,
        "claims": claims,
        "relations": relations,
        "decisions": [],
        "adoptions": list(payload.get("adoptions", [])),
        "separate_project_keys": separate,
        "legacy": _legacy_payload(raw, claims, separate),
    }


def evaluate_case_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    for case in catalog.get("cases", []):
        case_id = str(case["case_id"])
        left = _case_project(case_id, "left", dict(case.get("left") or {}))
        right = _case_project(case_id, "right", dict(case.get("right") or {}))
        actual = evaluate_pair(left, right, flags=case.get("flags", []))
        expected = str(case["expected_verdict"])
        output.append({
            "case_id": case_id,
            "description": str(case.get("description") or ""),
            "expected": expected,
            "actual": actual["verdict"],
            "reason": actual["reason"],
            "passed": actual["verdict"] == expected,
        })
    return {
        "case_count": len(output),
        "passed_count": sum(1 for item in output if item["passed"]),
        "failed_count": sum(1 for item in output if not item["passed"]),
        "cases": output,
    }


def _current_v2_shape(project: dict[str, Any]) -> dict[str, Any]:
    shaped = dict(project)
    services = set(str(value) for value in project.get("service_urls", []) if str(value))
    website = str(project.get("website_url") or "")
    if website:
        services.add(website)
    shaped["service_urls"] = sorted(services)
    return shaped


def _current_v2_profile(project: dict[str, Any]) -> dict[str, set[str]]:
    shaped = _current_v2_shape(project)
    return {
        "source_code_urls": set(project_record.source_code_url_values(shaped)),
        "github_repo_urls": set(project_record.provider_repo_url_values(shaped, "github")),
        "candidate_source_code_urls": set(shaped.get("candidate_source_code_urls", [])),
        "service_urls": set(shaped.get("service_urls", [])),
        "recorded_lexicon_namespaces": set(shaped.get("recorded_lexicon_namespaces", [])),
        "record_creators": set(shaped.get("record_creators", [])),
        "authority_service_families": registry.project_merge_authority_families(shaped),
        "domain_identity_tokens": registry.project_merge_domain_identity_tokens(shaped),
        "merge_aliases": project_record.project_merge_aliases(shaped),
    }


def _current_v2_candidate(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    left_profile: dict[str, set[str]] | None = None,
    right_profile: dict[str, set[str]] | None = None,
) -> dict[str, Any] | None:
    if registry.merge_is_blocked(left, right):
        return None
    left_values = left_profile or _current_v2_profile(left)
    right_values = right_profile or _current_v2_profile(right)
    reason_fields = (
        "source_code_urls",
        "github_repo_urls",
        "candidate_source_code_urls",
        "service_urls",
        "recorded_lexicon_namespaces",
        "authority_service_families",
        "domain_identity_tokens",
        "merge_aliases",
    )
    reasons = {
        field: sorted(left_values[field] & right_values[field])
        for field in reason_fields
        if left_values[field] & right_values[field]
    }
    record_creator_overlap = sorted(
        set(left.get("record_creators", [])) & set(right.get("record_creators", []))
    )
    namespace_lineage = registry.namespace_lineage_overlap(left, right)
    if record_creator_overlap and namespace_lineage:
        reasons["record_creators_and_namespace_lineage"] = namespace_lineage
    if not reasons:
        return None
    score = registry.merge_score_from_reasons(reasons)
    confidence, auto_merge_ready = registry.merge_confidence_from_reasons(reasons, score)
    return {
        "project_key": str(right.get("project_key") or ""),
        "name": str(right.get("name") or ""),
        "status": str(right.get("status") or ""),
        "reasons": reasons,
        "score": score,
        "merge_confidence": confidence,
        "auto_merge_ready": auto_merge_ready,
    }


def _current_pair_candidates(projects: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    legacy_projects = [project["legacy"] for project in projects]
    profiles = {
        str(project["project_key"]): _current_v2_profile(project)
        for project in legacy_projects
    }
    index: dict[str, dict[str, set[str]]] = {
        field: collections.defaultdict(set)
        for field in next(iter(profiles.values()), {})
    }
    for project_key, profile in profiles.items():
        for field, values in profile.items():
            for value in values:
                index[field][str(value)].add(project_key)
    lookup = {str(item["project_key"]): item for item in legacy_projects}
    pair_keys: set[tuple[str, str]] = set()
    for values in index.values():
        for project_keys in values.values():
            for left, right in itertools.combinations(sorted(project_keys), 2):
                pair_keys.add((left, right))
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for left_key, right_key in sorted(pair_keys):
        left = lookup[left_key]
        right = lookup[right_key]
        blocked = registry.merge_is_blocked(left, right)
        candidate = None if blocked else _current_v2_candidate(
            left,
            right,
            left_profile=profiles[left_key],
            right_profile=profiles[right_key],
        )
        if not candidate and not blocked:
            continue
        left_v2 = _current_v2_shape(left)
        right_v2 = _current_v2_shape(right)
        strict = registry.batch_strict_overlap(left_v2, right_v2) if not blocked else {}
        moderate = registry.batch_moderate_overlap(left_v2, right_v2) if not blocked else {}
        output[(left_key, right_key)] = {
            "blocked": blocked,
            "candidate": candidate,
            "batch": "strict" if strict else "moderate" if moderate else "broad_only" if candidate else "blocked",
        }
    return output


def _shadow_candidate_pairs(projects: list[dict[str, Any]]) -> dict[tuple[str, str], set[str]]:
    indexes: dict[str, dict[str, set[str]]] = collections.defaultdict(lambda: collections.defaultdict(set))
    node_projects: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for project in projects:
        key = str(project["project_key"])
        for node in _confirmed_component_nodes(project):
            node_projects[node].add(key)
        for label, roles in EXCLUSIVE_ROLE_GROUPS.items():
            for kind, value in _values_for_roles(project, roles):
                indexes[f"exclusive:{label}"][f"{kind}|{value}"].add(key)
        for kind, value in _values_for_roles(project, {"source_repository"}, confirmed_only=False):
            indexes["repository"][f"{kind}|{value}"].add(key)
        for kind, value in _values_for_roles(project, {"owned_nsid"}, confirmed_only=False):
            namespace = project_record.namespace_from_collection_nsid(value) if kind == "collection" else ""
            if namespace:
                indexes["collection_namespace"][namespace].add(key)
            authority_namespace = registry.registrable_namespace_from_namespace(value) if kind == "collection" else ""
            if authority_namespace:
                indexes["collection_authority_namespace"][authority_namespace].add(key)
        seed_nsid = _seed_nsid(project)
        if seed_nsid:
            seed_namespace = project_record.namespace_from_collection_nsid(seed_nsid)
            if seed_namespace:
                indexes["collection_origin_namespace"][seed_namespace].add(key)
            seed_authority = registry.registrable_namespace_from_namespace(seed_nsid)
            if seed_authority:
                indexes["collection_authority_namespace"][seed_authority].add(key)
        for alias in project_record.project_merge_aliases(project["legacy"]):
            indexes["alias"][alias].add(key)
    output: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for label, values in indexes.items():
        for project_keys in values.values():
            for pair in itertools.combinations(sorted(project_keys), 2):
                output[pair].add(label)
    for project in projects:
        for relation in project.get("relations", []):
            if relation.get("classification") != "confirmed":
                continue
            if relation.get("predicate") not in BRIDGING_PREDICATES:
                continue
            subject_projects = node_projects.get(tuple(relation["subject"]), set())
            object_projects = node_projects.get(tuple(relation["object"]), set())
            for left_key in subject_projects:
                for right_key in object_projects:
                    if left_key == right_key:
                        continue
                    output[tuple(sorted((left_key, right_key)))].add("direct_component_relation")
    return output


def _current_status(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return "not_candidate"
    if payload.get("blocked"):
        return "blocked"
    candidate = payload.get("candidate") or {}
    return "auto_ready" if candidate.get("auto_merge_ready") else "manual_candidate"


def _summarize_pairs(
    projects: list[dict[str, Any]],
    current: dict[tuple[str, str], dict[str, Any]],
    shadow_discovery: dict[tuple[str, str], set[str]],
    *,
    example_limit: int,
) -> dict[str, Any]:
    lookup = {str(project["project_key"]): project for project in projects}
    pairs = sorted(set(current) | set(shadow_discovery))
    verdicts: collections.Counter[str] = collections.Counter()
    reasons: collections.Counter[str] = collections.Counter()
    transitions: collections.Counter[str] = collections.Counter()
    discovery: collections.Counter[str] = collections.Counter()
    signal_counts: collections.Counter[str] = collections.Counter()
    recommendation_kinds: collections.Counter[str] = collections.Counter()
    retrospective_cohorts: collections.Counter[str] = collections.Counter()
    retrospective_tags: collections.Counter[str] = collections.Counter()
    retrospective_tag_combinations: collections.Counter[str] = collections.Counter()
    retrospective_details: collections.Counter[str] = collections.Counter()
    retrospective_by_status: collections.Counter[str] = collections.Counter()
    separate_cohorts: collections.Counter[str] = collections.Counter()
    separate_tags: collections.Counter[str] = collections.Counter()
    separate_details: collections.Counter[str] = collections.Counter()
    separate_examples: list[dict[str, Any]] = []
    retrospective_examples: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    containment_counts: collections.Counter[str] = collections.Counter()
    cohort_detail_counts: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    examples_by_reason: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    examples_by_signal: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    auto_current = strict = moderate = 0
    for pair in pairs:
        left = lookup[pair[0]]
        right = lookup[pair[1]]
        result = evaluate_pair(left, right)
        retrospective = _retrospective_features(left, right)
        current_payload = current.get(pair)
        current_status = _current_status(current_payload)
        verdicts[result["verdict"]] += 1
        reasons[result["reason"]] += 1
        if result["recommendation_kind"]:
            recommendation_kinds[result["recommendation_kind"]] += 1
        cohort = str(retrospective["primary_cohort"])
        retrospective_cohorts[cohort] += 1
        retrospective_tags.update(retrospective["tags"])
        retrospective_tag_combinations[" + ".join(retrospective["tags"])] += 1
        detail_labels = _retrospective_detail_labels(retrospective)
        retrospective_details.update(detail_labels)
        cohort_detail_counts.update(f"{cohort}->{label}" for label in detail_labels)
        retrospective_by_status[f"{current_status}->{cohort}"] += 1
        if any(retrospective["containment"].values()):
            containment_counts[cohort] += 1
        if result["verdict"] == "KEEP_SEPARATE":
            separate_cohorts[cohort] += 1
            separate_tags.update(retrospective["tags"])
            separate_details.update(detail_labels)
            if len(separate_examples) < example_limit:
                separate_examples.append({
                    "left": {"project_key": pair[0], "name": left.get("name", "")},
                    "right": {"project_key": pair[1], "name": right.get("name", "")},
                    "features": retrospective,
                })
        if len(retrospective_examples[cohort]) < example_limit:
            retrospective_examples[cohort].append({
                "left": {"project_key": pair[0], "name": left.get("name", "")},
                "right": {"project_key": pair[1], "name": right.get("name", "")},
                "current_status": current_status,
                "shadow_verdict": result["verdict"],
                "features": retrospective,
            })
        transitions[f"{current_status}->{result['verdict']}"] += 1
        discovery.update(shadow_discovery.get(pair, set()))
        signals = result["signals"]
        for label in signals["shared_exclusive_components"]:
            signal_key = f"shared_exclusive:{label}"
            signal_counts[signal_key] += 1
            if len(examples_by_signal[signal_key]) < example_limit:
                examples_by_signal[signal_key].append({
                    "left": {"project_key": pair[0], "name": left.get("name", "")},
                    "right": {"project_key": pair[1], "name": right.get("name", "")},
                    "current_status": current_status,
                    "shadow_verdict": result["verdict"],
                    "values": signals["shared_exclusive_components"][label],
                })
        for label in (
            "fragment_absorption_candidates",
            "direct_component_relations",
            "shared_source_repositories",
            "shared_collection_namespaces",
            "shared_aliases",
            "shared_adoption_origins",
        ):
            if signals[label]:
                signal_counts[label] += 1
                if len(examples_by_signal[label]) < example_limit:
                    examples_by_signal[label].append({
                        "left": {"project_key": pair[0], "name": left.get("name", "")},
                        "right": {"project_key": pair[1], "name": right.get("name", "")},
                        "current_status": current_status,
                        "shadow_verdict": result["verdict"],
                        "values": signals[label],
                    })
        if current_payload:
            candidate = current_payload.get("candidate") or {}
            auto_current += int(bool(candidate.get("auto_merge_ready")))
            strict += int(current_payload.get("batch") == "strict")
            moderate += int(current_payload.get("batch") == "moderate")
        if len(examples[result["verdict"]]) < example_limit:
            examples[result["verdict"]].append({
                "left": {"project_key": pair[0], "name": left.get("name", "")},
                "right": {"project_key": pair[1], "name": right.get("name", "")},
                "current": current_payload,
                "shadow": result,
                "discovery": sorted(shadow_discovery.get(pair, set())),
            })
        if len(examples_by_reason[result["reason"]]) < example_limit:
            examples_by_reason[result["reason"]].append({
                "left": {"project_key": pair[0], "name": left.get("name", "")},
                "right": {"project_key": pair[1], "name": right.get("name", "")},
                "current_status": current_status,
                "shadow_verdict": result["verdict"],
                "discovery": sorted(shadow_discovery.get(pair, set())),
            })
    return {
        "pair_count": len(pairs),
        "current_candidate_count": sum(1 for item in current.values() if item.get("candidate")),
        "current_auto_ready_count": auto_current,
        "current_strict_pair_count": strict,
        "current_moderate_pair_count": moderate,
        "shadow_verdict_counts": _counter_dict(verdicts),
        "shadow_reason_counts": _counter_dict(reasons),
        "shadow_recommendation_kind_counts": _counter_dict(recommendation_kinds),
        "current_to_shadow_counts": _counter_dict(transitions),
        "shadow_discovery_counts": _counter_dict(discovery),
        "shadow_signal_counts": _counter_dict(signal_counts),
        "retrospective_shape_analysis": {
            "primary_cohort_counts": _counter_dict(retrospective_cohorts),
            "tag_counts": _counter_dict(retrospective_tags),
            "tag_combination_counts": _counter_dict(retrospective_tag_combinations),
            "detail_counts": _counter_dict(retrospective_details),
            "current_status_to_cohort_counts": _counter_dict(retrospective_by_status),
            "known_separate_primary_cohort_counts": _counter_dict(separate_cohorts),
            "known_separate_tag_counts": _counter_dict(separate_tags),
            "known_separate_detail_counts": _counter_dict(separate_details),
            "known_separate_examples": separate_examples,
            "component_containment_counts_by_cohort": _counter_dict(containment_counts),
            "cohort_detail_counts": _counter_dict(cohort_detail_counts),
            "examples": dict(retrospective_examples),
        },
        "review_workload": {
            "recommendations_requiring_human_confirmation": int(verdicts["RECOMMEND_MERGE_REVIEW"]),
            "pairs_still_requiring_open_review": int(verdicts["REVIEW"]),
            "pairs_removed_by_hard_guard": int(verdicts["KEEP_SEPARATE"]),
            "pairs_routed_to_separate_relation_review": int(verdicts["NOT_EVALUATED"]),
            "new_pairs_not_found_by_current_v2": sum(1 for pair in pairs if pair not in current),
            "current_auto_ready_pairs_not_approved_for_automatic_execution": auto_current,
        },
        "examples": dict(examples),
        "examples_by_reason": dict(examples_by_reason),
        "examples_by_signal": dict(examples_by_signal),
    }


def _historical_merges(
    conn: sqlite3.Connection,
    *,
    example_limit: int,
) -> dict[str, Any]:
    buckets: collections.Counter[str] = collections.Counter()
    transitions: collections.Counter[str] = collections.Counter()
    current_buckets: collections.Counter[str] = collections.Counter()
    recommendation_kinds: collections.Counter[str] = collections.Counter()
    retrospective_cohorts: collections.Counter[str] = collections.Counter()
    retrospective_tags: collections.Counter[str] = collections.Counter()
    retrospective_tag_combinations: collections.Counter[str] = collections.Counter()
    retrospective_details: collections.Counter[str] = collections.Counter()
    cohort_by_current_bucket: collections.Counter[str] = collections.Counter()
    containment_counts: collections.Counter[str] = collections.Counter()
    retrospective_examples: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    cohort_detail_counts: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    rows = list(conn.execute("SELECT * FROM project_key_aliases ORDER BY created_at, alias_project_key"))
    for alias in rows:
        source_run = conn.execute(
            "SELECT output_project_json FROM investigation_runs WHERE project_key=? AND completed_at<=? ORDER BY completed_at DESC LIMIT 1",
            (str(alias["alias_project_key"]), str(alias["created_at"])),
        ).fetchone()
        target_run = conn.execute(
            "SELECT output_project_json FROM investigation_runs WHERE project_key=? AND completed_at<=? ORDER BY completed_at DESC LIMIT 1",
            (str(alias["canonical_project_key"]), str(alias["created_at"])),
        ).fetchone()
        if source_run is None:
            buckets["missing_source_snapshot"] += 1
            continue
        if target_run is None:
            buckets["missing_target_snapshot"] += 1
            continue
        try:
            source_snapshot = storage.decode_history_json(source_run["output_project_json"], context="merge source snapshot")
            target_snapshot = storage.decode_history_json(target_run["output_project_json"], context="merge target snapshot")
        except storage.HistoryPayloadDecodeError:
            buckets["decode_error"] += 1
            continue
        source = _model_from_snapshot(source_snapshot, project_key=str(alias["alias_project_key"]))
        target = _model_from_snapshot(target_snapshot, project_key=str(alias["canonical_project_key"]))
        result = evaluate_pair(source, target)
        retrospective = _retrospective_features(source, target)
        if result["recommendation_kind"]:
            recommendation_kinds[result["recommendation_kind"]] += 1
        source_v2 = _current_v2_shape(source["legacy"])
        target_v2 = _current_v2_shape(target["legacy"])
        candidate = _current_v2_candidate(source_v2, target_v2)
        strict = registry.batch_strict_overlap(source_v2, target_v2)
        moderate = registry.batch_moderate_overlap(source_v2, target_v2)
        current_bucket = (
            "strict" if strict else "moderate" if moderate else "broad_only" if candidate else "no_signal"
        )
        current_buckets[current_bucket] += 1
        cohort = str(retrospective["primary_cohort"])
        retrospective_cohorts[cohort] += 1
        retrospective_tags.update(retrospective["tags"])
        retrospective_tag_combinations[" + ".join(retrospective["tags"])] += 1
        detail_labels = _retrospective_detail_labels(retrospective)
        retrospective_details.update(detail_labels)
        cohort_detail_counts.update(f"{cohort}->{label}" for label in detail_labels)
        cohort_by_current_bucket[f"{current_bucket}->{cohort}"] += 1
        if any(retrospective["containment"].values()):
            containment_counts[cohort] += 1
        if len(retrospective_examples[cohort]) < example_limit:
            retrospective_examples[cohort].append({
                "source": {"project_key": source["project_key"], "name": source["name"]},
                "target": {"project_key": target["project_key"], "name": target["name"]},
                "merged_at": str(alias["created_at"]),
                "current_v2_bucket": current_bucket,
                "features": retrospective,
            })
        transitions[f"{current_bucket}->{result['verdict']}"] += 1
        buckets[result["verdict"]] += 1
        if len(examples[result["verdict"]]) < example_limit:
            examples[result["verdict"]].append({
                "source": {"project_key": source["project_key"], "name": source["name"]},
                "target": {"project_key": target["project_key"], "name": target["name"]},
                "merged_at": str(alias["created_at"]),
                "current_v2_bucket": current_bucket,
                "shadow": result,
            })
    return {
        "alias_count": len(rows),
        "current_v2_bucket_counts": _counter_dict(current_buckets),
        "verdict_counts": _counter_dict(buckets),
        "recommendation_kind_counts": _counter_dict(recommendation_kinds),
        "current_v2_to_shadow_counts": _counter_dict(transitions),
        "retrospective_shape_analysis": {
            "primary_cohort_counts": _counter_dict(retrospective_cohorts),
            "tag_counts": _counter_dict(retrospective_tags),
            "tag_combination_counts": _counter_dict(retrospective_tag_combinations),
            "detail_counts": _counter_dict(retrospective_details),
            "current_v2_bucket_to_cohort_counts": _counter_dict(cohort_by_current_bucket),
            "component_containment_counts_by_cohort": _counter_dict(containment_counts),
            "cohort_detail_counts": _counter_dict(cohort_detail_counts),
            "examples": dict(retrospective_examples),
        },
        "examples": dict(examples),
        "interpretation": "retrospective coverage only; an executed merge is not treated as ground truth",
    }


def _counter_dict(counter: collections.Counter[Any]) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda item: str(item[0]))}


def _input_inventory(projects: list[dict[str, Any]]) -> dict[str, Any]:
    roles: collections.Counter[str] = collections.Counter()
    classifications: collections.Counter[str] = collections.Counter()
    predicates: collections.Counter[str] = collections.Counter()
    confirmed_policy_relations: collections.Counter[str] = collections.Counter()
    for project in projects:
        for claim in project.get("claims", []):
            roles[str(claim.get("role") or "")] += 1
            classifications[str(claim.get("classification") or "")] += 1
        for relation in project.get("relations", []):
            predicate = str(relation.get("predicate") or "")
            predicates[predicate] += 1
            if predicate in BRIDGING_PREDICATES and relation.get("classification") == "confirmed":
                confirmed_policy_relations[predicate] += 1
    return {
        "identifier_role_counts": _counter_dict(roles),
        "identifier_classification_counts": _counter_dict(classifications),
        "relation_predicate_counts": _counter_dict(predicates),
        "confirmed_merge_policy_relation_counts": _counter_dict(confirmed_policy_relations),
    }


def _stable_hash(report: dict[str, Any]) -> str:
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(item)
                for key, item in value.items()
                if key not in {"content_hash", "db_path", "elapsed_ms"}
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return hashlib.sha256(
        json.dumps(clean(report), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextlib.contextmanager
def _using_domain_overrides(path: Path) -> Iterable[dict[str, Any]]:
    original = domain_policy.DOMAIN_OVERRIDES_PATH
    target = path.expanduser().resolve()
    domain_policy.DOMAIN_OVERRIDES_PATH = target
    domain_policy.reset_domain_config_cache()
    try:
        configured = domain_policy.configured_multi_tenant_suffixes()
        yield {
            "path": str(target),
            "exists": target.is_file(),
            "sha256": _file_hash(target),
            "configured_multi_tenant_suffix_count": len(configured),
        }
    finally:
        domain_policy.DOMAIN_OVERRIDES_PATH = original
        domain_policy.reset_domain_config_cache()


def compare_database(
    conn: sqlite3.Connection,
    *,
    case_catalog: dict[str, Any],
    example_limit: int = 20,
) -> dict[str, Any]:
    started = time.perf_counter()
    projects = load_projects(conn)
    current = _current_pair_candidates(projects)
    shadow_discovery = _shadow_candidate_pairs(projects)
    case_results = evaluate_case_catalog(case_catalog)
    pair_summary = _summarize_pairs(
        projects,
        current,
        shadow_discovery,
        example_limit=max(0, example_limit),
    )
    adoption_provenance_available = _table_exists(conn, "project_identifier_adoptions")
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "source": {"read_only": True},
        "input": {
            "project_count": len(projects),
            "current_identifier_count": sum(len(project["claims"]) for project in projects),
            "relation_count": sum(len(project["relations"]) for project in projects),
            "adoption_count": sum(len(project["adoptions"]) for project in projects),
            **_input_inventory(projects),
        },
        "case_catalog": case_results,
        "current_and_shadow": pair_summary,
        "historical_merges": _historical_merges(conn, example_limit=max(0, example_limit)),
        "missing_inputs": {
            "typed_cross_project_temporal_relations_available": False,
            "typed_cross_project_derivation_relations_available": False,
            "sync_attach_outcome_available": bool(sum(len(project["adoptions"]) for project in projects)),
            "schema_v3_adoption_provenance_available": adoption_provenance_available,
            "interpretation": (
                "typed project-level time/derivation facts are not available in this input; absence is not counterevidence"
                if adoption_provenance_available
                else "typed project-level time/derivation facts and schema-v3 adoption provenance are not available in this input; absence is not counterevidence"
            ),
        },
        "policy": {
            "verdicts": list(VERDICTS),
            "exclusive_role_groups": {key: sorted(value) for key, value in EXCLUSIVE_ROLE_GROUPS.items()},
            "bridging_predicates": sorted(BRIDGING_PREDICATES),
            "fragment_absorption": {
                "fragment_shape": "current confirmed project components are owned collections only",
                "exact_subset_rule": "fragment owned collections are a proper subset of target owned collections",
                "encompassing_project_rule": "target has an independent component, both records share a registrable collection authority, and direct namespace, alias, or project-name context agrees",
                "shared_lexicon_excluded": True,
                "candidate_service_host_name_is_merge_evidence": False,
                "composition_predicates_are_supplementary_only": sorted(FRAGMENT_RELATION_PREDICATES),
                "automatic_execution_allowed": False,
            },
            "temporal_or_derivation_flags": sorted(TEMPORAL_OR_DERIVATION_FLAGS),
            "retrospective_shape_analysis": {
                "cohort_order": list(RETROSPECTIVE_COHORT_ORDER),
                "interpretation": "cohorts explain the strongest reconstructable pre-merge shape; they are review priorities, not correctness labels or automatic merge rules",
            },
            "automatic_execution_allowed": False,
        },
        "metrics": {"elapsed_ms": round((time.perf_counter() - started) * 1000, 3)},
        "review_gate": {
            "case_catalog_passed": case_results["failed_count"] == 0,
            "automatic_execution_enabled": False,
            "production_behavior_changed": False,
        },
    }
    report["content_hash"] = _stable_hash(report)
    return report


def compare_database_path(
    db_path: Path,
    *,
    case_catalog_path: Path,
    domain_overrides_path: Path | None = None,
    example_limit: int = 20,
) -> dict[str, Any]:
    target = db_path.expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"database does not exist: {target}")
    uri = f"{target.as_uri()}?mode=ro"
    adjacent_overrides = target.parent.parent / "config" / "domain-overrides.json"
    override_path = domain_overrides_path or (
        adjacent_overrides if adjacent_overrides.is_file() else domain_policy.DOMAIN_OVERRIDES_PATH
    )
    with _using_domain_overrides(override_path) as domain_config:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            report = compare_database(
                conn,
                case_catalog=load_case_catalog(case_catalog_path),
                example_limit=max(0, example_limit),
            )
            conn.rollback()
        finally:
            conn.close()
    report["source"]["db_path"] = str(target)
    report["source"]["domain_overrides"] = domain_config
    report["content_hash"] = _stable_hash(report)
    return report
