from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

from . import domain_policy, storage, repository_trust


REPORT_SCHEMA_VERSION = 1
POLICY_VERSION = "trust-policy-shadow-v5"
VERDICTS = ("SUPPORTED", "INSUFFICIENT", "CONFLICTING", "NOT_EVALUATED")
CURRENT_IDENTIFIER_STANDING = "current"
IDENTIFIER_STANDINGS = {CURRENT_IDENTIFIER_STANDING, "former", "non_production"}
REDIRECT_REVIEW_CATEGORIES = {"same_site_surface_redirect", "cross_site_redirect"}

EVALUATED_ROLES = {
    ("service_url", "service"),
    ("repo_url", "project_repository"),
    ("collection", "owned_nsid"),
    ("handle", "project_handle"),
    ("did", "brand_did"),
}

PROFILE_SOURCES = {
    "operator": {"web_ui", "operator_add_seed"},
    "operator_plus_legacy": {"web_ui", "operator_add_seed", "legacy_manual_seed_migration"},
    "all_current_sources": {
        "web_ui",
        "operator_add_seed",
        "legacy_manual_seed_migration",
        "canonical_redirect",
        "collection_inventory_import",
        "migration_inferred_collection_inventory",
        "manual_seed",
        "semble_collection_sync",
    },
}

# This is deliberately independent of eligible_for_auto_confirm.  Predicates and
# endpoint kinds are the static policy input.
EDGE_POLICY = {
    ("service_url", "service_links_repo", "repo_url"): "strong",
    ("repo_url", "repo_links_service", "service_url"): "strong",
    ("repo_url", "defines_schema", "collection"): "strong",
    ("service_url", "requests_permission", "collection"): "conditional",
    ("service_url", "service_exact_domain", "domain"): "intermediary",
    ("domain", "domain_exact_service", "service_url"): "intermediary",
    ("handle", "handle_resolves_did", "did"): "identity",
    ("did", "did_document_claims_handle", "handle"): "identity",
    ("handle", "handle_exact_domain", "domain"): "intermediary",
    ("domain", "domain_exact_handle", "handle"): "intermediary",
    ("collection", "collection_authority_domain", "domain"): "intermediary",
    ("domain", "domain_publishes_lexicon_record", "collection"): "strong_legacy_composite",
    ("domain", "lexicon_authority_resolves_to_did", "did"): "intermediary",
    ("collection", "collection_resolves_to_did", "did"): "intermediary",
    ("did", "did_publishes_schema", "collection"): "strong",
    ("did", "did_web_identity_domain", "domain"): "identity",
    ("domain", "did_web_identity_domain", "did"): "identity",
    ("service_url", "official_alternate_surface", "service_url"): "strong",
    ("repo_url", "same_repository_identity", "repo_url"): "identity",
    ("collection", "collection_canonical_source_repo", "repo_url"): "strong",
}

ADMISSION_POLICY = {
    ("service_url", "service_links_repo", "repo_url"): "project_repository",
    ("repo_url", "repo_links_service", "service_url"): "service",
    ("repo_url", "defines_schema", "collection"): "owned_nsid",
    ("service_url", "official_alternate_surface", "service_url"): "service",
    ("collection", "collection_canonical_source_repo", "repo_url"): "project_repository",
}

ENTITY_KIND = {
    "service": "service_url",
    "repo": "repo_url",
    "nsid": "collection",
    "collection": "collection",
    "did": "did",
    "handle": "handle",
    "domain": "domain",
    "service_url": "service_url",
    "repo_url": "repo_url",
}

IDENTIFIER_KIND = {
    "service_url": "service_url",
    "repo_url": "repo_url",
    "nsid": "collection",
    "collection": "collection",
    "did": "did",
    "handle": "handle",
    "domain": "domain",
}


def _json(value: str, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _node(kind: str, value: str) -> tuple[str, str]:
    return (str(kind or ""), str(value or "").strip())


def _claim_key(claim: dict[str, Any]) -> tuple[str, str, str]:
    return (str(claim["node"][0]), str(claim["node"][1]), str(claim.get("role") or ""))


def _claim_standing(claim: dict[str, Any]) -> str:
    return str(claim.get("standing") or CURRENT_IDENTIFIER_STANDING)


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (*edge["subject"], str(edge["predicate"]), *edge["object"])


def _redirect_url_identity(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    scheme = str(parsed.scheme or "").lower()
    host = str(parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        return str(url or "").strip()
    try:
        port = parsed.port
    except ValueError:
        return str(url or "").strip()
    if port in {None, 80 if scheme == "http" else 443}:
        netloc = host
    else:
        netloc = f"{host}:{port}"
    path = (parsed.path or "/").rstrip("/") or "/"
    return urllib.parse.urlunparse((scheme, netloc, path, "", parsed.query, ""))


def _is_non_service_resource_destination(url: str) -> bool:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    host = str(parsed.hostname or "").lower()
    parts = [part for part in (parsed.path or "").split("/") if part]
    if host == "github.com" and len(parts) >= 2:
        return True
    if host == "pub.dev" and len(parts) >= 2 and parts[0] == "packages":
        return True
    return False


def classify_redirect_observation(subject_url: str, object_url: str) -> str:
    subject = urllib.parse.urlparse(str(subject_url or "").strip())
    obj = urllib.parse.urlparse(str(object_url or "").strip())
    if subject.scheme not in {"http", "https"} or obj.scheme not in {"http", "https"}:
        return "invalid_url"
    subject_host = str(subject.hostname or "").lower()
    object_host = str(obj.hostname or "").lower()
    if not subject_host or not object_host:
        return "invalid_url"
    subject_path = (subject.path or "/").rstrip("/") or "/"
    object_path = (obj.path or "/").rstrip("/") or "/"
    if subject_host == object_host and subject_path == object_path:
        return "url_normalization"
    if (
        subject_host.removeprefix("www.") == object_host.removeprefix("www.")
        and subject_host != object_host
        and subject_path == "/"
        and object_path == "/"
    ):
        return "root_www_alias_candidate"
    if subject_host == object_host:
        return "same_host_routing"
    if _is_non_service_resource_destination(object_url):
        return "resource_destination"
    if domain_policy.is_same_site_family(subject_url, object_url):
        return "same_site_surface_redirect"
    return "cross_site_redirect"


def _relation_entity_kind(raw_kind: str, value: str, confirmed_services: set[str]) -> str | None:
    if raw_kind == "website":
        return "service_url" if str(value).strip() in confirmed_services else None
    return ENTITY_KIND.get(raw_kind)


def _normalize_relation_edge(
    row: sqlite3.Row | dict[str, Any],
    *,
    confirmed_services: set[str] | None = None,
) -> dict[str, Any] | None:
    services = confirmed_services or set()
    subject_kind = _relation_entity_kind(str(row["subject_type"]), str(row["subject_value"]), services)
    object_kind = _relation_entity_kind(str(row["object_type"]), str(row["object_value"]), services)
    predicate = str(row["predicate"])
    if predicate == "project_repository":
        if subject_kind == "service_url" and object_kind == "repo_url":
            predicate = "service_links_repo"
        elif subject_kind == "repo_url" and object_kind == "service_url":
            predicate = "repo_links_service"
    if not subject_kind or not object_kind:
        return None
    return {
        "subject": _node(subject_kind, str(row["subject_value"])),
        "predicate": predicate,
        "object": _node(object_kind, str(row["object_value"])),
        "classification": str(row["classification"]),
        "source": str(row["source"]),
        "method": str(row["method"]),
        "locator": str(row["locator"]),
        "origin": "project_relations",
    }


def _normalize_legacy_edge(
    raw: dict[str, Any],
    *,
    confirmed_services: set[str] | None = None,
) -> dict[str, Any] | None:
    subject = raw.get("subject") if isinstance(raw.get("subject"), dict) else {}
    obj = raw.get("object") if isinstance(raw.get("object"), dict) else {}
    services = confirmed_services or set()
    subject_kind = _relation_entity_kind(
        str(subject.get("kind") or subject.get("type") or ""),
        str(subject.get("value") or ""),
        services,
    )
    object_kind = _relation_entity_kind(
        str(obj.get("kind") or obj.get("type") or ""),
        str(obj.get("value") or ""),
        services,
    )
    if not subject_kind or not object_kind:
        return None
    return {
        "subject": _node(subject_kind, str(subject.get("value") or "")),
        "predicate": str(raw.get("predicate") or ""),
        "object": _node(object_kind, str(obj.get("value") or "")),
        "classification": str(raw.get("classification") or "candidate"),
        "source": str(raw.get("source") or ""),
        "method": str(raw.get("method") or ""),
        "locator": str(raw.get("locator") or ""),
        "origin": "legacy_trust_graph",
    }


def dedupe_edges(edges: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    rank = {"heuristic": 0, "candidate": 1, "derived": 2, "confirmed": 3}
    for edge in edges:
        key = _edge_key(edge)
        if not all(key):
            continue
        current = output.get(key)
        if current is None:
            item = dict(edge)
            item["origins"] = [str(edge.get("origin") or "unknown")]
            output[key] = item
            continue
        origin = str(edge.get("origin") or "unknown")
        if origin not in current["origins"]:
            current["origins"].append(origin)
        if rank.get(str(edge.get("classification")), 0) > rank.get(str(current.get("classification")), 0):
            for field in ("classification", "source", "method", "locator"):
                current[field] = edge.get(field, "")
    return sorted(output.values(), key=_edge_key)


def _allowed_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        edge for edge in edges
        if edge.get("classification") in {"confirmed", "derived"}
        and (edge["subject"][0], edge["predicate"], edge["object"][0]) in EDGE_POLICY
    ]


def _shortest_path(
    adjacency: dict[tuple[str, str], list[dict[str, Any]]],
    start: tuple[str, str],
    goal: tuple[str, str],
) -> tuple[list[dict[str, Any]], int]:
    if start == goal:
        return [], 0
    queue = collections.deque([(start, [])])
    seen = {start}
    explored = 0
    while queue:
        current, path = queue.popleft()
        for edge in adjacency.get(current, []):
            explored += 1
            target = edge["object"]
            if target in seen:
                continue
            next_path = [*path, edge]
            if target == goal:
                return next_path, explored
            seen.add(target)
            queue.append((target, next_path))
    return [], explored


def _path_payload(path: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "subject": list(edge["subject"]),
        "predicate": edge["predicate"],
        "object": list(edge["object"]),
        "source": edge.get("source", ""),
        "origins": edge.get("origins", [edge.get("origin", "unknown")]),
    } for edge in path]


def _guarded(claim: dict[str, Any], guards: set[tuple[str, str]]) -> bool:
    return claim["node"] in guards


def _evaluate_from_origins(
    claims: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    origins: list[dict[str, Any]],
    guards: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    adjacency: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for edge in _allowed_edges(edges):
        adjacency[edge["subject"]].append(edge)
    origin_nodes = {origin["node"] for origin in origins}
    output: list[dict[str, Any]] = []
    explored_total = 0
    lengths: collections.Counter[int] = collections.Counter()
    for claim in claims:
        result = {"claim": claim, "strategy": "full_origin"}
        if _claim_standing(claim) != CURRENT_IDENTIFIER_STANDING:
            result.update(verdict="NOT_EVALUATED", reason="identifier_standing_outside_current_policy")
        elif (claim["node"][0], claim.get("role", "")) not in EVALUATED_ROLES:
            result.update(verdict="NOT_EVALUATED", reason="role_outside_project_attribution_policy")
        elif claim["node"] in origin_nodes:
            result.update(verdict="NOT_EVALUATED", reason="adopted_registration_origin")
        elif _guarded(claim, guards):
            result.update(verdict="CONFLICTING", reason="active_guard")
        else:
            supported = None
            for origin in origins:
                forward, explored = _shortest_path(adjacency, origin["node"], claim["node"])
                explored_total += explored
                if not forward:
                    continue
                backward, explored = _shortest_path(adjacency, claim["node"], origin["node"])
                explored_total += explored
                if backward:
                    supported = (origin, forward, backward)
                    break
            if supported:
                origin, forward, backward = supported
                lengths[len(forward)] += 1
                lengths[len(backward)] += 1
                result.update(
                    verdict="SUPPORTED",
                    reason="semantic_bidirectional_path",
                    origin=origin,
                    forward_path=_path_payload(forward),
                    backward_path=_path_payload(backward),
                )
            else:
                result.update(verdict="INSUFFICIENT", reason="no_semantic_bidirectional_path")
        output.append(result)
    return output, {"explored_edge_count": explored_total, **{f"path_length_{k}": v for k, v in sorted(lengths.items())}}


def _evaluate_with_trace_reuse(
    claims: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    origins: list[dict[str, Any]],
    guards: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    adjacency: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for edge in _allowed_edges(edges):
        adjacency[edge["subject"]].append(edge)
    adopted: dict[tuple[str, str], dict[str, Any]] = {
        origin["node"]: {"origin": origin, "forward_path": [], "backward_path": []}
        for origin in origins
    }
    pending = [
        claim for claim in claims
        if _claim_standing(claim) == CURRENT_IDENTIFIER_STANDING
        and (claim["node"][0], claim.get("role", "")) in EVALUATED_ROLES
    ]
    explored_total = 0
    changed = True
    while changed:
        changed = False
        for claim in pending:
            if claim["node"] in adopted or _guarded(claim, guards):
                continue
            for anchor_node, trace in list(adopted.items()):
                forward, explored = _shortest_path(adjacency, anchor_node, claim["node"])
                explored_total += explored
                if not forward:
                    continue
                backward, explored = _shortest_path(adjacency, claim["node"], anchor_node)
                explored_total += explored
                if not backward:
                    continue
                adopted[claim["node"]] = {
                    "origin": trace["origin"],
                    "forward_path": [*trace["forward_path"], *forward],
                    "backward_path": [*backward, *trace["backward_path"]],
                }
                changed = True
                break
    output: list[dict[str, Any]] = []
    lengths: collections.Counter[int] = collections.Counter()
    origin_nodes = {origin["node"] for origin in origins}
    for claim in claims:
        result = {"claim": claim, "strategy": "trace_reuse"}
        if _claim_standing(claim) != CURRENT_IDENTIFIER_STANDING:
            result.update(verdict="NOT_EVALUATED", reason="identifier_standing_outside_current_policy")
        elif (claim["node"][0], claim.get("role", "")) not in EVALUATED_ROLES:
            result.update(verdict="NOT_EVALUATED", reason="role_outside_project_attribution_policy")
        elif claim["node"] in origin_nodes:
            result.update(verdict="NOT_EVALUATED", reason="adopted_registration_origin")
        elif _guarded(claim, guards):
            result.update(verdict="CONFLICTING", reason="active_guard")
        elif claim["node"] in adopted:
            trace = adopted[claim["node"]]
            lengths[len(trace["forward_path"])] += 1
            lengths[len(trace["backward_path"])] += 1
            result.update(
                verdict="SUPPORTED",
                reason="semantic_bidirectional_path",
                origin=trace["origin"],
                forward_path=_path_payload(trace["forward_path"]),
                backward_path=_path_payload(trace["backward_path"]),
            )
        else:
            result.update(verdict="INSUFFICIENT", reason="no_semantic_bidirectional_path")
        output.append(result)
    return output, {"explored_edge_count": explored_total, **{f"path_length_{k}": v for k, v in sorted(lengths.items())}}


def _origins_for_profile(
    claims: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    adoptions: list[dict[str, Any]],
    profile: str,
    *,
    adoption_provenance_available: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    def dedupe_origins(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in items:
            key = (*item["node"], item["role"])
            sources = {str(item.get("source") or "")}
            adoption_kinds = {str(item.get("adoption_kind") or "")} - {""}
            if key in grouped:
                sources.update(grouped[key].get("sources", []))
                adoption_kinds.update(grouped[key].get("adoption_kinds", []))
            grouped[key] = {
                "node": item["node"],
                "role": item["role"],
                "source": sorted(sources)[0],
                "sources": sorted(sources),
                "adoption_kinds": sorted(adoption_kinds),
            }
        return list(grouped.values())

    allowed_sources = PROFILE_SOURCES[profile]
    by_node: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    by_claim: dict[tuple[str, str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for claim in claims:
        by_node[claim["node"]].append(claim)
        by_claim[_claim_key(claim)].append(claim)
    origins: list[dict[str, Any]] = []
    ambiguities: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    if adoption_provenance_available:
        for adoption in adoptions:
            if adoption.get("cleared_at"):
                continue
            source = str(adoption.get("source") or "")
            if source not in allowed_sources or not bool(adoption.get("registration_origin")):
                continue
            kind = IDENTIFIER_KIND.get(str(adoption.get("kind") or ""))
            if not kind:
                exclusions.append({"adoption": adoption, "reason": "unsupported_identifier_kind"})
                continue
            claim_key = (*_node(kind, str(adoption.get("value") or "")), str(adoption.get("role") or ""))
            matches = by_claim.get(claim_key, [])
            if len(matches) != 1:
                ambiguities.append({"adoption": adoption, "matching_claim_count": len(matches)})
                continue
            claim = matches[0]
            if _claim_standing(claim) != CURRENT_IDENTIFIER_STANDING:
                exclusions.append({
                    "adoption": adoption,
                    "claim": claim,
                    "reason": "identifier_standing_outside_current_policy",
                })
                continue
            if claim.get("classification") != "confirmed":
                exclusions.append({
                    "adoption": adoption,
                    "claim": claim,
                    "reason": "registration_origin_claim_not_confirmed",
                })
                continue
            if bool(adoption.get("review_required")):
                exclusions.append({
                    "adoption": adoption,
                    "claim": claim,
                    "reason": "registration_origin_requires_review",
                })
                continue
            origins.append({
                "node": claim["node"],
                "role": claim["role"],
                "source": source,
                "adoption_kind": str(adoption.get("adoption_kind") or ""),
            })
        return dedupe_origins(origins), ambiguities, exclusions

    # Pre-v3 comparison fallback. Schema-v3 databases use the role-qualified
    # adoption rows above and never infer origins from decisions or inventory.
    for decision in decisions:
        if decision["kind"] != "confirm_identifier" or decision.get("cleared_at"):
            continue
        source = str(decision.get("source") or "")
        if source not in allowed_sources:
            continue
        kind = IDENTIFIER_KIND.get(str(decision.get("target_type") or ""))
        if not kind:
            continue
        node = _node(kind, str(decision.get("target_value") or ""))
        matches = by_node.get(node, [])
        if len(matches) != 1:
            ambiguities.append({"decision": decision, "matching_claim_count": len(matches)})
            continue
        if _claim_standing(matches[0]) != CURRENT_IDENTIFIER_STANDING:
            exclusions.append({
                "decision": decision,
                "claim": matches[0],
                "reason": "identifier_standing_outside_current_policy",
            })
            continue
        origins.append({"node": node, "role": matches[0]["role"], "source": source})
    if profile == "all_current_sources":
        for claim in claims:
            if (
                _claim_standing(claim) == CURRENT_IDENTIFIER_STANDING
                and claim["node"][0] == "collection"
                and claim.get("role") == "owned_nsid"
                and claim.get("classification") == "confirmed"
            ):
                origins.append({
                    "node": claim["node"],
                    "role": claim["role"],
                    "source": "implicit_collection_inventory",
                })
    return dedupe_origins(origins), ambiguities, exclusions


def _admission_projection(
    claims: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    adopted_nodes: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    existing = {_claim_key(claim): claim for claim in claims}
    output: list[dict[str, Any]] = []
    suppression: collections.Counter[str] = collections.Counter()
    for edge in edges:
        key = (edge["subject"][0], edge["predicate"], edge["object"][0])
        target_role = ADMISSION_POLICY.get(key)
        if edge["subject"] not in adopted_nodes:
            continue
        if not target_role:
            if edge["object"][0] in {"service_url", "repo_url", "collection", "handle", "did"}:
                suppression[f"predicate_not_admissible:{edge['predicate']}"] += 1
            continue
        if edge.get("classification") != "confirmed":
            suppression["relation_not_confirmed"] += 1
            continue
        candidate_key = (*edge["object"], target_role)
        if candidate_key in existing:
            standing = _claim_standing(existing[candidate_key])
            if standing == CURRENT_IDENTIFIER_STANDING:
                suppression["claim_already_exists"] += 1
            else:
                suppression[f"known_identifier_standing:{standing}"] += 1
            continue
        output.append({
            "node": list(edge["object"]),
            "role": target_role,
            "via": {"subject": list(edge["subject"]), "predicate": edge["predicate"], "source": edge.get("source", "")},
        })
    unique = {(item["node"][0], item["node"][1], item["role"]): item for item in output}
    return list(unique.values()), _counter_dict(suppression)


def evaluate_project(
    project: dict[str, Any],
    *,
    profile: str,
) -> dict[str, Any]:
    claims = list(project.get("claims", []))
    edges = dedupe_edges(project.get("edges", []))
    decisions = list(project.get("decisions", []))
    adoptions = list(project.get("adoptions", []))
    guards = {
        _node(IDENTIFIER_KIND.get(str(item.get("target_type") or ""), ""), str(item.get("target_value") or ""))
        for item in decisions
        if item.get("kind") in {"block_identifier", "defer_identifier"} and not item.get("cleared_at")
    }
    guards.discard(("", ""))
    origins, ambiguities, origin_exclusions = _origins_for_profile(
        claims,
        decisions,
        adoptions,
        profile,
        adoption_provenance_available=bool(project.get("adoption_provenance_available", False)),
    )
    origin_guard_conflicts = [origin for origin in origins if origin["node"] in guards]
    started = time.perf_counter()
    full, full_metrics = _evaluate_from_origins(claims, edges, origins, guards)
    full_metrics["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    started = time.perf_counter()
    reuse, reuse_metrics = _evaluate_with_trace_reuse(claims, edges, origins, guards)
    reuse_metrics["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    full_verdicts = {_claim_key(item["claim"]): item["verdict"] for item in full}
    reuse_verdicts = {_claim_key(item["claim"]): item["verdict"] for item in reuse}
    divergence = [list(key) for key in sorted(set(full_verdicts) | set(reuse_verdicts)) if full_verdicts.get(key) != reuse_verdicts.get(key)]
    supported_nodes = {item["claim"]["node"] for item in full if item["verdict"] == "SUPPORTED"}
    adopted_nodes = {origin["node"] for origin in origins} | supported_nodes
    admission_projection, admission_suppression = _admission_projection(claims, edges, adopted_nodes)
    return {
        "project_key": project.get("project_key", ""),
        "profile": profile,
        "origins": origins,
        "origin_ambiguities": ambiguities,
        "origin_exclusions": origin_exclusions,
        "origin_guard_conflicts": origin_guard_conflicts,
        "origin_guard_conflict_count": len(origin_guard_conflicts),
        "edge_count": len(edges),
        "allowed_edge_count": len(_allowed_edges(edges)),
        "full_origin": full,
        "trace_reuse": reuse,
        "strategy_divergence": divergence,
        "metrics": {"full_origin": full_metrics, "trace_reuse": reuse_metrics},
        "admission_projection": admission_projection,
        "admission_suppression_counts": admission_suppression,
    }


def _load_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    adoption_provenance_available = _table_exists(conn, "project_identifier_adoptions")
    projects: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT project_key, state_json FROM projects ORDER BY project_key"):
        state = _json(str(row["state_json"]), {})
        graph = (((state.get("evidence") or {}).get("trust_graph") or {}) if isinstance(state, dict) else {})
        projects[str(row["project_key"])] = {
            "project_key": str(row["project_key"]),
            "legacy_edges": list(graph.get("edges") or []),
            "claims": [],
            "edges": [],
            "decisions": [],
            "adoptions": [],
            "adoption_provenance_available": adoption_provenance_available,
            "v1_evaluations": list(graph.get("evaluations") or []),
        }
    for row in conn.execute("SELECT * FROM project_identifiers ORDER BY project_key, ordinal"):
        kind = IDENTIFIER_KIND.get(str(row["kind"]))
        if not kind:
            node = _node(str(row["kind"]), str(row["value"]))
        else:
            node = _node(kind, str(row["value"]))
        projects[str(row["project_key"])]["claims"].append({
            "node": node,
            "identifier_kind": str(row["kind"]),
            "role": str(row["role"]),
            "classification": str(row["classification"]),
            "provider": str(row["provider"]),
            # The production schema has no standing field yet.  Existing rows
            # remain current inputs during this read-only comparison.
            "standing": CURRENT_IDENTIFIER_STANDING,
        })
    for row in conn.execute("SELECT * FROM project_relations ORDER BY project_key, ordinal"):
        project = projects[str(row["project_key"])]
        confirmed_services = {
            str(claim["node"][1])
            for claim in project["claims"]
            if claim["node"][0] == "service_url"
            and claim["role"] == "service"
            and claim["classification"] == "confirmed"
        }
        project.setdefault("relation_evidence", []).append({
            "relation": {"subject": {"type": row["subject_type"], "value": row["subject_value"]},
                         "predicate": row["predicate"], "object": {"type": row["object_type"], "value": row["object_value"]},
                         "locator": row["locator"], "method": row["method"]},
            "source": row["source"], "classification": row["classification"],
            "observed_at": row["observed_at"], "context": _json(row["context_json"], {}),
        })
        if row["predicate"] in {"project_repository", "repository_homepage"} and {row["subject_type"], row["object_type"]} in ({"repo", "website"}, {"repo", "service"}):
            continue
        edge = _normalize_relation_edge(row, confirmed_services=confirmed_services)
        if edge:
            project["edges"].append(edge)
        else:
            project.setdefault("unmapped_relation_count", 0)
            project["unmapped_relation_count"] += 1
    for project in projects.values():
        confirmed_services = {
            str(claim["node"][1])
            for claim in project["claims"]
            if claim["node"][0] == "service_url"
            and claim["role"] == "service"
            and claim["classification"] == "confirmed"
        }
        for raw in [*(edge for edge in project.pop("legacy_edges", []) if not repository_trust.is_repository_edge(edge)),
                    *repository_trust.project_edges(project)]:
            edge = _normalize_legacy_edge(raw, confirmed_services=confirmed_services) if isinstance(raw, dict) else None
            if edge:
                project["edges"].append(edge)
    for row in conn.execute("SELECT * FROM project_decisions ORDER BY project_key, ordinal"):
        metadata = _json(str(row["metadata_json"]), {})
        projects[str(row["project_key"])]["decisions"].append({
            "kind": str(row["kind"]),
            "target_type": str(row["target_type"]),
            "target_value": str(row["target_value"]),
            "created_at": str(row["created_at"]),
            "cleared_at": str(row["cleared_at"]),
            "source": str(metadata.get("source") or ""),
        })
    if adoption_provenance_available:
        for row in conn.execute(
            "SELECT * FROM project_identifier_adoptions ORDER BY project_key, created_at, adoption_id"
        ):
            projects[str(row["project_key"])]["adoptions"].append({key: row[key] for key in row.keys()})
    # URL comparison uses today's production inputs; saved verdicts remain historical.
    from . import project_record, registry
    for project in projects.values():
        url_claims = [claim for claim in project["claims"] if claim["node"][0] in {"service_url", "repo_url"}]
        if not url_claims:
            continue
        runtime = project_record.fetch_project(conn, project["project_key"])
        project["current_url_evaluations"] = [
            registry.trust_auto_confirm_identifier(runtime, claim["node"][0], claim["node"][1])
            for claim in url_claims
        ]
    return list(projects.values())


def _v1_verdict(project: dict[str, Any], claim: dict[str, Any]) -> str:
    evaluations = project.get("current_url_evaluations", []) if claim["node"][0] in {"service_url", "repo_url"} else project.get("v1_evaluations", [])
    for item in evaluations:
        candidate = item.get("candidate") if isinstance(item, dict) else None
        if not isinstance(candidate, dict):
            continue
        if _node(str(candidate.get("kind") or ""), str(candidate.get("value") or "")) == claim["node"]:
            return "SUPPORTED" if bool(item.get("auto_confirm")) else "INSUFFICIENT"
    return "NOT_EVALUATED"


def _counter_dict(counter: collections.Counter[Any]) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda item: str(item[0]))}


def _summarize_results(
    projects: list[dict[str, Any]],
    evaluations: dict[str, dict[str, dict[str, Any]]],
    *,
    example_limit: int,
) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    for profile in PROFILE_SOURCES:
        verdicts: collections.Counter[str] = collections.Counter()
        roles: collections.Counter[str] = collections.Counter()
        classifications: collections.Counter[str] = collections.Counter()
        standings: collections.Counter[str] = collections.Counter()
        role_classifications: collections.Counter[str] = collections.Counter()
        origin_sources: collections.Counter[str] = collections.Counter()
        v1_deltas: collections.Counter[str] = collections.Counter()
        examples: list[dict[str, Any]] = []
        admission_examples: list[dict[str, Any]] = []
        ambiguity_examples: list[dict[str, Any]] = []
        review_examples: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        admission_count = ambiguity_count = exclusion_count = divergence_count = 0
        origin_count = 0
        origin_guard_conflicts = 0
        admission_suppression: collections.Counter[str] = collections.Counter()
        workload: collections.Counter[str] = collections.Counter()
        full_edges = reuse_edges = 0
        full_ms = reuse_ms = 0.0
        path_lengths: collections.Counter[str] = collections.Counter()
        for project in projects:
            result = evaluations[project["project_key"]][profile]
            ambiguity_count += len(result["origin_ambiguities"])
            exclusion_count += len(result["origin_exclusions"])
            origin_count += len(result["origins"])
            for origin in result["origins"]:
                origin_sources.update(
                    str(source or "(none)") for source in (origin.get("sources") or [origin.get("source")])
                )
            origin_guard_conflicts += int(result["origin_guard_conflict_count"])
            divergence_count += len(result["strategy_divergence"])
            admission_count += len(result["admission_projection"])
            for item in result["admission_projection"]:
                if len(admission_examples) < example_limit:
                    admission_examples.append({"project_key": project["project_key"], **item})
            for item in result["origin_ambiguities"]:
                if len(ambiguity_examples) < example_limit:
                    ambiguity_examples.append({"project_key": project["project_key"], **item})
            admission_suppression.update(result["admission_suppression_counts"])
            full_edges += int(result["metrics"]["full_origin"].get("explored_edge_count", 0))
            reuse_edges += int(result["metrics"]["trace_reuse"].get("explored_edge_count", 0))
            full_ms += float(result["metrics"]["full_origin"].get("elapsed_ms", 0))
            reuse_ms += float(result["metrics"]["trace_reuse"].get("elapsed_ms", 0))
            for key, value in result["metrics"]["full_origin"].items():
                if key.startswith("path_length_"):
                    path_lengths[key.removeprefix("path_length_")] += int(value)
            for item in result["full_origin"]:
                claim = item["claim"]
                verdicts[item["verdict"]] += 1
                roles[f"{claim['role']}:{item['verdict']}"] += 1
                classifications[f"{claim['classification']}:{item['verdict']}"] += 1
                standings[f"{_claim_standing(claim)}:{item['verdict']}"] += 1
                role_classifications[f"{claim['role']}:{claim['classification']}:{item['verdict']}"] += 1
                if item["verdict"] == "SUPPORTED" and claim["classification"] == "candidate":
                    workload["candidate_supported_for_auto_adoption"] += 1
                    example_kind = "candidate_supported_for_auto_adoption"
                elif item["verdict"] == "INSUFFICIENT" and claim["classification"] == "confirmed":
                    workload["confirmed_insufficient_review"] += 1
                    example_kind = "confirmed_insufficient_review"
                elif item["verdict"] == "INSUFFICIENT" and claim["classification"] == "candidate":
                    workload["candidate_remains_without_review"] += 1
                    example_kind = ""
                elif item["verdict"] == "CONFLICTING":
                    workload["conflicting_review"] += 1
                    example_kind = "conflicting_review"
                elif item["reason"] == "adopted_registration_origin":
                    workload["adopted_registration_origin"] += 1
                    example_kind = ""
                elif item["verdict"] == "NOT_EVALUATED":
                    workload["outside_component_trust_audit"] += 1
                    example_kind = ""
                else:
                    example_kind = ""
                if example_kind and len(review_examples[example_kind]) < example_limit:
                    review_examples[example_kind].append({
                        "project_key": project["project_key"],
                        "claim": {**claim, "node": list(claim["node"])},
                        "reason": item["reason"],
                    })
                old = _v1_verdict(project, claim)
                v1_deltas[f"{old}->{item['verdict']}"] += 1
                if old != item["verdict"] and len(examples) < example_limit:
                    examples.append({
                        "project_key": project["project_key"],
                        "claim": {**claim, "node": list(claim["node"])},
                        "v1": old,
                        "shadow": item["verdict"],
                        "reason": item["reason"],
                    })
        profiles[profile] = {
            "verdict_counts": _counter_dict(verdicts),
            "role_verdict_counts": _counter_dict(roles),
            "classification_verdict_counts": _counter_dict(classifications),
            "standing_verdict_counts": _counter_dict(standings),
            "role_classification_verdict_counts": _counter_dict(role_classifications),
            "v1_to_shadow": _counter_dict(v1_deltas),
            "origin_ambiguity_count": ambiguity_count,
            "origin_exclusion_count": exclusion_count,
            "origin_count": origin_count,
            "origin_source_counts": _counter_dict(origin_sources),
            "origin_guard_conflict_count": origin_guard_conflicts,
            "strategy_divergence_count": divergence_count,
            "admission_projection_count": admission_count,
            "admission_suppression_counts": _counter_dict(admission_suppression),
            "manual_work_counts": _counter_dict(workload),
            "path_length_counts": _counter_dict(path_lengths),
            "metrics": {
                "full_origin_explored_edges": full_edges,
                "trace_reuse_explored_edges": reuse_edges,
                "full_origin_elapsed_ms": round(full_ms, 3),
                "trace_reuse_elapsed_ms": round(reuse_ms, 3),
            },
            "examples": examples,
            "admission_examples": admission_examples,
            "origin_ambiguity_examples": ambiguity_examples,
            "manual_work_examples": dict(review_examples),
        }
    return profiles


def _decision_history(conn: sqlite3.Connection, evaluations: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    source_counts: collections.Counter[str] = collections.Counter()
    cleared = 0
    cleared_readopted = 0
    for row in conn.execute("SELECT project_key, target_type, target_value, cleared_at, metadata_json FROM project_decisions WHERE kind='confirm_identifier'"):
        metadata = _json(str(row["metadata_json"]), {})
        source_counts[str(metadata.get("source") or "(none)")] += 1
        if not str(row["cleared_at"]):
            continue
        cleared += 1
        node_kind = IDENTIFIER_KIND.get(str(row["target_type"]))
        if not node_kind:
            continue
        target = _node(node_kind, str(row["target_value"]))
        result = evaluations.get(str(row["project_key"]), {}).get("all_current_sources", {})
        if any(item["claim"]["node"] == target and item["verdict"] == "SUPPORTED" for item in result.get("full_origin", [])):
            cleared_readopted += 1
    return {
        "confirm_source_counts": _counter_dict(source_counts),
        "cleared_confirm_count": cleared,
        "cleared_confirm_supported_by_shadow_count": cleared_readopted,
    }


def _snapshot_identifiers(payload: dict[str, Any]) -> set[tuple[str, str]]:
    values: set[tuple[str, str]] = set()
    fields = {
        "service_urls": "service_url", "service_url_candidates": "service_url",
        "project_repository_urls": "repo_url", "candidate_project_repository_urls": "repo_url",
        "resolved_nsids": "collection", "input_nsids": "collection",
    }
    for field, kind in fields.items():
        for value in payload.get(field, []) if isinstance(payload.get(field), list) else []:
            if str(value):
                values.add((kind, str(value)))
    scalar_fields = {"project_repository_url": "repo_url", "primary_nsid": "collection"}
    for field, kind in scalar_fields.items():
        value = str(payload.get(field) or "")
        if value:
            values.add((kind, value))
    return values


def _merge_history(conn: sqlite3.Connection, projects: list[dict[str, Any]]) -> dict[str, Any]:
    current = {
        project["project_key"]: {claim["node"] for claim in project["claims"]}
        for project in projects
    }
    alias_count = snapshot_count = retained = missing = 0
    examples: list[dict[str, Any]] = []
    for alias in conn.execute("SELECT * FROM project_key_aliases ORDER BY created_at"):
        alias_count += 1
        run = conn.execute(
            "SELECT output_project_json FROM investigation_runs WHERE project_key=? AND completed_at<=? ORDER BY completed_at DESC LIMIT 1",
            (str(alias["alias_project_key"]), str(alias["created_at"])),
        ).fetchone()
        if run is None:
            continue
        snapshot_count += 1
        try:
            snapshot = storage.decode_history_json(run["output_project_json"], context="merge history output_project_json")
        except storage.HistoryPayloadDecodeError:
            continue
        identifiers = _snapshot_identifiers(snapshot if isinstance(snapshot, dict) else {})
        canonical = current.get(str(alias["canonical_project_key"]), set())
        present = identifiers & canonical
        absent = identifiers - canonical
        retained += len(present)
        missing += len(absent)
        if absent and len(examples) < 20:
            examples.append({
                "alias_project_key": str(alias["alias_project_key"]),
                "canonical_project_key": str(alias["canonical_project_key"]),
                "missing_identifiers": [list(item) for item in sorted(absent)[:10]],
            })
    return {
        "alias_count": alias_count,
        "pre_merge_snapshot_count": snapshot_count,
        "snapshot_identifier_retained_count": retained,
        "snapshot_identifier_missing_count": missing,
        "examples": examples,
        "interpretation": "coverage comparison only; an executed merge is not treated as trust-policy ground truth",
    }


def load_case_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported trust policy case catalog schema")
    for case in payload.get("cases", []):
        for claim in case.get("claims", []):
            standing = str(claim.get("standing") or CURRENT_IDENTIFIER_STANDING)
            if standing not in IDENTIFIER_STANDINGS:
                raise ValueError(f"unsupported identifier standing in {case.get('case_id', '(unknown)')}: {standing}")
    return payload


def evaluate_case_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in catalog.get("cases", []):
        project = {
            "project_key": case["case_id"],
            "claims": [{
                **claim,
                "node": tuple(claim["node"]),
                "standing": str(claim.get("standing") or CURRENT_IDENTIFIER_STANDING),
            } for claim in case.get("claims", [])],
            "edges": [{
                **edge,
                "subject": tuple(edge["subject"]),
                "object": tuple(edge["object"]),
                "classification": edge.get("classification", "confirmed"),
                "origin": edge.get("origin", "fixture"),
            } for edge in case.get("edges", [])],
            "decisions": list(case.get("decisions", [])),
        }
        evaluation = evaluate_project(project, profile=str(case.get("profile") or "operator"))
        actual = {
            f"{item['claim']['node'][0]}|{item['claim']['node'][1]}|{item['claim']['role']}": item["verdict"]
            for item in evaluation["full_origin"]
        }
        expected = dict(case.get("expected_verdicts") or {})
        results.append({
            "case_id": case["case_id"],
            "description": case.get("description", ""),
            "passed": actual == expected and not evaluation["strategy_divergence"],
            "expected": expected,
            "actual": actual,
            "strategy_divergence": evaluation["strategy_divergence"],
        })
    return {
        "case_count": len(results),
        "passed_count": sum(1 for item in results if item["passed"]),
        "failed_count": sum(1 for item in results if not item["passed"]),
        "cases": results,
    }


def _redirect_observation_summary(
    conn: sqlite3.Connection,
    projects: list[dict[str, Any]],
    *,
    example_limit: int,
) -> dict[str, Any]:
    by_key = {project["project_key"]: project for project in projects}
    categories: collections.Counter[str] = collections.Counter()
    methods: collections.Counter[str] = collections.Counter()
    method_categories: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    review_pairs: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    rows = list(conn.execute(
        """
        SELECT project_key, subject_value, object_value, method, source, locator, observed_at
        FROM project_relations
        WHERE predicate='redirects_to' AND classification IN ('confirmed', 'derived')
        ORDER BY project_key, ordinal
        """
    ))
    target_identities: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for row in rows:
        category = classify_redirect_observation(str(row["subject_value"]), str(row["object_value"]))
        if category in REDIRECT_REVIEW_CATEGORIES:
            target_identities[
                (str(row["project_key"]), _redirect_url_identity(str(row["subject_value"])))
            ].add(_redirect_url_identity(str(row["object_value"])))
    for row in rows:
        project_key = str(row["project_key"])
        subject = str(row["subject_value"])
        obj = str(row["object_value"])
        method = str(row["method"] or "(none)")
        observation_key = (project_key, subject, obj, method)
        if observation_key in seen:
            continue
        seen.add(observation_key)
        category = classify_redirect_observation(subject, obj)
        categories[category] += 1
        methods[method] += 1
        method_categories[f"{method}:{category}"] += 1
        payload = {
            "project_key": project_key,
            "subject": subject,
            "object": obj,
            "method": method,
            "source": str(row["source"]),
            "locator": str(row["locator"]),
            "observed_at": str(row["observed_at"]),
        }
        if len(examples[category]) < example_limit:
            examples[category].append(payload)
        if category not in REDIRECT_REVIEW_CATEGORIES:
            continue
        project = by_key.get(project_key, {})
        claims = list(project.get("claims", []))
        subject_current = any(
            claim["node"] == ("service_url", subject)
            and claim.get("classification") == "confirmed"
            and _claim_standing(claim) == CURRENT_IDENTIFIER_STANDING
            for claim in claims
        )
        target_claimed = any(
            claim["node"] == ("service_url", obj)
            and _claim_standing(claim) == CURRENT_IDENTIFIER_STANDING
            for claim in claims
        )
        subject_had_confirm = any(
            decision.get("kind") == "confirm_identifier"
            and IDENTIFIER_KIND.get(str(decision.get("target_type") or "")) == "service_url"
            and str(decision.get("target_value") or "") == subject
            for decision in project.get("decisions", [])
        )
        subject_target_count = len(target_identities[(project_key, _redirect_url_identity(subject))])
        reason = ""
        if subject_target_count > 1 and (subject_current or subject_had_confirm):
            reason = "multiple_observed_targets_from_current_or_former_confirmed_service"
        elif target_claimed and (subject_current or subject_had_confirm):
            reason = "claimed_target_from_current_or_former_confirmed_service"
        elif subject_current:
            reason = "unregistered_target_from_current_service"
        if not reason:
            continue
        review_key = (
            project_key,
            _redirect_url_identity(subject),
            _redirect_url_identity(obj),
            category,
            reason,
        )
        if review_key not in review_pairs:
            review_pairs[review_key] = {
                **payload,
                "category": category,
                "reason": reason,
                "methods": [method],
                "subject_target_count": subject_target_count,
            }
        elif method not in review_pairs[review_key]["methods"]:
            review_pairs[review_key]["methods"].append(method)
    review_reasons = collections.Counter(item["reason"] for item in review_pairs.values())
    return {
        "deduplicated_observation_count": len(seen),
        "category_counts": _counter_dict(categories),
        "method_counts": _counter_dict(methods),
        "method_category_counts": _counter_dict(method_categories),
        "review_candidate_count": len(review_pairs),
        "review_reason_counts": _counter_dict(review_reasons),
        "examples": dict(examples),
        "review_examples": list(review_pairs.values())[:example_limit],
        "interpretation": "URL topology and existing project state only; review candidates are not confirmed aliases or moves",
    }


def compare_database(conn: sqlite3.Connection, *, case_catalog: dict[str, Any], example_limit: int = 20) -> dict[str, Any]:
    started_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    projects = _load_projects(conn)
    all_adoptions = [adoption for project in projects for adoption in project.get("adoptions", [])]
    active_adoptions = [adoption for adoption in all_adoptions if not adoption.get("cleared_at")]
    active_registration_origins = [
        adoption for adoption in active_adoptions if bool(adoption.get("registration_origin"))
    ]
    evaluations: dict[str, dict[str, dict[str, Any]]] = {}
    for project in projects:
        evaluations[project["project_key"]] = {
            profile: evaluate_project(project, profile=profile)
            for profile in PROFILE_SOURCES
        }
    edge_origins: collections.Counter[str] = collections.Counter()
    edge_policy: collections.Counter[str] = collections.Counter()
    allowed = total_edges = unmapped = 0
    for project in projects:
        unmapped += int(project.get("unmapped_relation_count", 0))
        edges = dedupe_edges(project["edges"])
        total_edges += len(edges)
        allowed += len(_allowed_edges(edges))
        for edge in edges:
            edge_origins["+".join(sorted(edge.get("origins", [])))] += 1
            status = "allowed" if edge in _allowed_edges([edge]) else "excluded"
            edge_policy[f"{status}:{edge['predicate']}"] += 1
    case_results = evaluate_case_catalog(case_catalog)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "created_at": started_at,
        "source": {"read_only": True},
        "input": {
            "project_count": len(projects),
            "claim_count": sum(len(project["claims"]) for project in projects),
            "deduplicated_edge_count": total_edges,
            "allowed_edge_count": allowed,
            "unmapped_relation_count": unmapped,
            "edge_origin_counts": _counter_dict(edge_origins),
            "edge_policy_counts": _counter_dict(edge_policy),
            "adoption_provenance_available": bool(
                projects and projects[0].get("adoption_provenance_available", False)
            ),
            "adoption_count": len(all_adoptions),
            "active_adoption_count": len(active_adoptions),
            "active_registration_origin_count": len(active_registration_origins),
            "active_adoption_source_counts": _counter_dict(collections.Counter(
                str(adoption.get("source") or "(none)") for adoption in active_adoptions
            )),
            "active_registration_origin_source_counts": _counter_dict(collections.Counter(
                str(adoption.get("source") or "(none)") for adoption in active_registration_origins
            )),
        },
        "case_catalog": case_results,
        "profiles": _summarize_results(projects, evaluations, example_limit=example_limit),
        "decision_history": _decision_history(conn, evaluations),
        "merge_history": _merge_history(conn, projects),
        "redirect_observations": _redirect_observation_summary(conn, projects, example_limit=example_limit),
        "review_gate": {
            "case_catalog_passed": case_results["failed_count"] == 0,
            "strategy_divergence_count": sum(
                profile["strategy_divergence_count"]
                for profile in _summarize_results(projects, evaluations, example_limit=0).values()
            ),
            "production_behavior_changed": False,
        },
    }
    report["content_hash"] = _report_content_hash(report)
    return report


def _report_content_hash(report: dict[str, Any]) -> str:
    payload = {key: value for key, value in report.items() if key != "content_hash"}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def compare_database_path(
    db_path: Path,
    *,
    case_catalog_path: Path,
    example_limit: int = 20,
) -> dict[str, Any]:
    target = db_path.expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"database does not exist: {target}")
    uri = f"{target.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        report = compare_database(conn, case_catalog=load_case_catalog(case_catalog_path), example_limit=max(0, example_limit))
        conn.rollback()
    finally:
        conn.close()
    report["source"]["db_path"] = str(target)
    report["content_hash"] = _report_content_hash(report)
    return report
