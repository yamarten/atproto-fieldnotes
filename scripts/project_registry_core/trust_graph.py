from __future__ import annotations

from collections import deque
import copy
import hashlib
import json

from . import repository_trust
from typing import Any, Iterable, Mapping


TRUST_GRAPH_VERSION = 1
TRUST_POLICY_VERSION = "trust-graph-v2"
TRUST_GRAPH_FIELD = "trust_graph"

NODE_KINDS = {"service_url", "repo_url", "handle", "did", "domain", "collection", "nsid"}
URL_NODE_KINDS = {"service_url", "repo_url"}
CLASSIFICATION_RANK = {"heuristic": 0, "candidate": 1, "derived": 2, "confirmed": 3}

AUTO_CONFIRM_PREDICATES = {
    "service_links_repo",
    "repo_links_service",
    "service_canonical",
    "did_publishes_schema",
    "collection_authority_domain",
    "domain_publishes_lexicon_record",
    "service_exact_domain",
    "domain_exact_service",
    "domain_exact_handle",
    "handle_exact_domain",
    "handle_resolves_did",
    "did_document_claims_handle",
}


def node(kind: str, value: str) -> dict[str, str]:
    normalized_kind = str(kind or "").strip()
    normalized_value = str(value or "").strip()
    if normalized_kind not in NODE_KINDS or not normalized_value:
        return {}
    return {"kind": normalized_kind, "value": normalized_value}


def node_key(value: Mapping[str, Any]) -> tuple[str, str]:
    return (str(value.get("kind") or ""), str(value.get("value") or ""))


def edge_key(value: Mapping[str, Any]) -> tuple[str, ...]:
    subject = value.get("subject") if isinstance(value.get("subject"), Mapping) else {}
    obj = value.get("object") if isinstance(value.get("object"), Mapping) else {}
    return (
        str(subject.get("kind") or ""),
        str(subject.get("value") or ""),
        str(value.get("predicate") or ""),
        str(obj.get("kind") or ""),
        str(obj.get("value") or ""),
        str(value.get("source") or ""),
        str(value.get("locator") or ""),
        str(value.get("method") or ""),
    )


def normalize_edge(value: Mapping[str, Any]) -> dict[str, Any]:
    subject = node(
        str((value.get("subject") if isinstance(value.get("subject"), Mapping) else {}).get("kind") or ""),
        str((value.get("subject") if isinstance(value.get("subject"), Mapping) else {}).get("value") or ""),
    )
    obj = node(
        str((value.get("object") if isinstance(value.get("object"), Mapping) else {}).get("kind") or ""),
        str((value.get("object") if isinstance(value.get("object"), Mapping) else {}).get("value") or ""),
    )
    predicate = str(value.get("predicate") or "").strip()
    if not subject or not obj or not predicate:
        return {}
    classification = str(value.get("classification") or "candidate").strip()
    if classification not in CLASSIFICATION_RANK:
        classification = "candidate"
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "source": str(value.get("source") or ""),
        "locator": str(value.get("locator") or ""),
        "method": str(value.get("method") or ""),
        "classification": classification,
        "eligible_for_auto_confirm": bool(value.get("eligible_for_auto_confirm")),
        "observed_at": str(value.get("observed_at") or ""),
        "context": copy.deepcopy(dict(value.get("context") or {})),
    }


def normalize_edges(values: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    indexes: dict[tuple[str, ...], int] = {}
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, Mapping):
            continue
        normalized = normalize_edge(item)
        if not normalized:
            continue
        key = edge_key(normalized)
        if key not in indexes:
            indexes[key] = len(output)
            output.append(normalized)
            continue
        existing = output[indexes[key]]
        if CLASSIFICATION_RANK[normalized["classification"]] > CLASSIFICATION_RANK[existing["classification"]]:
            existing["classification"] = normalized["classification"]
        existing["eligible_for_auto_confirm"] = bool(
            existing.get("eligible_for_auto_confirm") or normalized.get("eligible_for_auto_confirm")
        )
        if normalized["observed_at"] > existing["observed_at"]:
            existing["observed_at"] = normalized["observed_at"]
        existing["context"] = {**dict(existing.get("context") or {}), **dict(normalized.get("context") or {})}
    return output


def stored_graph(project: Mapping[str, Any]) -> dict[str, Any]:
    value = project.get(TRUST_GRAPH_FIELD)
    if not isinstance(value, Mapping):
        return {"version": TRUST_GRAPH_VERSION, "generated_at": "", "edges": [], "evaluations": []}
    return {
        "version": int(value.get("version") or TRUST_GRAPH_VERSION),
        "generated_at": str(value.get("generated_at") or ""),
        "edges": normalize_edges(value.get("edges", [])),
        "evaluations": [
            copy.deepcopy(dict(item))
            for item in value.get("evaluations", [])
            if isinstance(item, Mapping)
        ],
    }


def graph(project: Mapping[str, Any]) -> dict[str, Any]:
    current = stored_graph(project)
    current["edges"] = normalize_edges([
        *(edge for edge in current["edges"] if not repository_trust.is_repository_edge(edge)),
        *repository_trust.project_edges(project),
    ])
    if project.get("publication_schemas"):
        from .collection_trust import verified_publications
        valid = verified_publications(project)
        current["edges"] = [edge for edge in current["edges"]
                            if not str(edge.get("source") or "").startswith("lexicon_resolution:")
                            or str(edge["source"]).removeprefix("lexicon_resolution:") in valid]
    return current


def evaluation_inputs_digest(project: Mapping[str, Any]) -> str:
    inputs = {key: project.get(key, []) for key in (
        "manual_seeds", "blocked_identifiers", "deferred_identifiers",
    )}
    inputs[TRUST_GRAPH_FIELD] = graph(project)["edges"]
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


def set_graph(project: dict[str, Any], value: Mapping[str, Any]) -> None:
    edges = normalize_edges(value.get("edges", []))
    evaluations = [
        copy.deepcopy(dict(item))
        for item in value.get("evaluations", [])
        if isinstance(item, Mapping)
    ]
    if not edges and not evaluations:
        project.pop(TRUST_GRAPH_FIELD, None)
        return
    project[TRUST_GRAPH_FIELD] = {
        "version": int(value.get("version") or TRUST_GRAPH_VERSION),
        "generated_at": str(value.get("generated_at") or ""),
        "edges": edges,
        "evaluations": evaluations,
    }


def upsert_edge(
    project: dict[str, Any],
    *,
    subject: Mapping[str, Any],
    predicate: str,
    object: Mapping[str, Any],
    source: str = "",
    locator: str = "",
    method: str = "",
    classification: str = "candidate",
    eligible_for_auto_confirm: bool = False,
    observed_at: str = "",
    context: Mapping[str, Any] | None = None,
    generated_at: str = "",
) -> dict[str, Any]:
    candidate = normalize_edge({
        "subject": subject,
        "predicate": predicate,
        "object": object,
        "source": source,
        "locator": locator,
        "method": method,
        "classification": classification,
        "eligible_for_auto_confirm": eligible_for_auto_confirm,
        "observed_at": observed_at,
        "context": dict(context or {}),
    })
    if not candidate:
        return {}
    if repository_trust.is_repository_edge(candidate):
        repository_trust.record_observation(project, candidate)
        return candidate
    current = stored_graph(project)
    if generated_at and not current.get("generated_at"):
        current["generated_at"] = generated_at
    current["edges"] = normalize_edges([*current.get("edges", []), candidate])
    set_graph(project, current)
    return candidate


def remove_edges(
    project: dict[str, Any],
    *,
    predicate: str = "",
    source: str = "",
    locator: str = "",
    methods: set[str] | None = None,
) -> int:
    relation_removed = 0
    retained_relations = []
    for record in project.get("relation_evidence", []):
        edges = repository_trust.project_edges({"relation_evidence": [record]})
        matches = any((not predicate or edge["predicate"] == predicate)
                      and (not source or edge["source"] == source)
                      and (not locator or edge["locator"] == locator)
                      and (methods is None or edge["method"] in methods) for edge in edges)
        if matches:
            relation_removed += 1
        else:
            retained_relations.append(record)
    if relation_removed:
        project["relation_evidence"] = retained_relations
    current = stored_graph(project)
    retained: list[dict[str, Any]] = []
    removed = 0
    for edge in current.get("edges", []):
        matches = (
            (not predicate or str(edge.get("predicate") or "") == predicate)
            and (not source or str(edge.get("source") or "") == source)
            and (not locator or str(edge.get("locator") or "") == locator)
            and (methods is None or str(edge.get("method") or "") in methods)
        )
        if matches:
            removed += 1
        else:
            retained.append(edge)
    if removed:
        current["edges"] = retained
        set_graph(project, current)
    return removed + relation_removed


def auto_confirm_edges(
    edges: Iterable[Mapping[str, Any]],
    *,
    allowed_node_kinds: set[str] | None = None,
    allowed_predicates: set[str] | None = None,
) -> list[dict[str, Any]]:
    node_kinds = allowed_node_kinds or URL_NODE_KINDS
    predicates = allowed_predicates or AUTO_CONFIRM_PREDICATES
    output: list[dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        normalized = normalize_edge(edge)
        if (
            normalized
            and normalized["classification"] == "confirmed"
            and normalized.get("eligible_for_auto_confirm")
            and normalized["predicate"] in predicates
            and normalized["subject"]["kind"] in node_kinds
            and normalized["object"]["kind"] in node_kinds
        ):
            output.append(normalized)
    return output


def _path_between(edges: list[dict[str, Any]], source: Mapping[str, Any], target: Mapping[str, Any]) -> list[dict[str, Any]]:
    start = node_key(source)
    goal = node_key(target)
    if not start[0] or not goal[0]:
        return []
    if start == goal:
        return []
    outgoing: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for edge in edges:
        outgoing.setdefault(node_key(edge["subject"]), []).append(edge)
    queue: deque[tuple[tuple[str, str], list[dict[str, Any]]]] = deque([(start, [])])
    visited = {start}
    while queue:
        current, path = queue.popleft()
        for edge in outgoing.get(current, []):
            next_key = node_key(edge["object"])
            next_path = [*path, edge]
            if next_key == goal:
                return next_path
            if next_key not in visited:
                visited.add(next_key)
                queue.append((next_key, next_path))
    return []


def directional_paths(
    project: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    anchors: Iterable[Mapping[str, Any]],
    allowed_node_kinds: set[str] | None = None,
    allowed_predicates: set[str] | None = None,
) -> list[dict[str, Any]]:
    candidate_node = node(str(candidate.get("kind") or ""), str(candidate.get("value") or ""))
    if not candidate_node:
        return []
    node_kinds = allowed_node_kinds or URL_NODE_KINDS
    normalized_edges = auto_confirm_edges(
        graph(project).get("edges", []),
        allowed_node_kinds=node_kinds,
        allowed_predicates=allowed_predicates,
    )
    output: list[dict[str, Any]] = []
    for anchor in anchors:
        anchor_node = node(str(anchor.get("kind") or ""), str(anchor.get("value") or ""))
        if not anchor_node or anchor_node["kind"] not in node_kinds:
            continue
        forward_path = _path_between(normalized_edges, anchor_node, candidate_node)
        backward_path = _path_between(normalized_edges, candidate_node, anchor_node)
        if forward_path or backward_path:
            output.append({
                "anchor": anchor_node,
                "candidate": candidate_node,
                "anchor_to_candidate": forward_path,
                "candidate_to_anchor": backward_path,
            })
    return output


def evaluate_auto_confirm(
    project: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    anchors: Iterable[Mapping[str, Any]],
    allowed_node_kinds: set[str] | None = None,
    allowed_predicates: set[str] | None = None,
    scoped_edges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candidate_node = node(str(candidate.get("kind") or ""), str(candidate.get("value") or ""))
    node_kinds = allowed_node_kinds or URL_NODE_KINDS
    if not candidate_node:
        return {"auto_confirm": False, "reason": "invalid_candidate", "candidate": dict(candidate)}
    if candidate_node["kind"] not in node_kinds:
        return {"auto_confirm": False, "reason": "kind_not_in_v1_scope", "candidate": candidate_node}
    # Repository references establish project attribution, not an operated service.
    # Keep those edges for repository adoption and directional explanations, but
    # never use a repository (including as an intermediate hop) to adopt a service.
    if candidate_node["kind"] == "service_url":
        node_kinds = node_kinds - {"repo_url"}
    normalized_edges = auto_confirm_edges(
        graph(project).get("edges", []) if scoped_edges is None else scoped_edges,
        allowed_node_kinds=node_kinds,
        allowed_predicates=allowed_predicates,
    )
    normalized_anchors = [
        item
        for anchor in anchors
        if (item := node(str(anchor.get("kind") or ""), str(anchor.get("value") or "")))
    ]
    if not normalized_anchors:
        return {"auto_confirm": False, "reason": "no_trusted_anchor", "candidate": candidate_node}
    for anchor in normalized_anchors:
        if anchor["kind"] not in node_kinds:
            continue
        if node_key(anchor) == node_key(candidate_node):
            return {
                "auto_confirm": True,
                "reason": "candidate_is_trusted_anchor",
                "candidate": candidate_node,
                "anchor": anchor,
                "forward_path": [],
                "backward_path": [],
                "policy_version": TRUST_POLICY_VERSION,
            }
        forward_path = _path_between(normalized_edges, anchor, candidate_node)
        backward_path = _path_between(normalized_edges, candidate_node, anchor)
        if forward_path and backward_path:
            return {
                "auto_confirm": True,
                "reason": "mutual_trust_path",
                "candidate": candidate_node,
                "anchor": anchor,
                "forward_path": forward_path,
                "backward_path": backward_path,
                "policy_version": TRUST_POLICY_VERSION,
            }
    return {
        "auto_confirm": False,
        "reason": "no_mutual_trust_path",
        "candidate": candidate_node,
        "anchor_count": len(normalized_anchors),
        "policy_version": TRUST_POLICY_VERSION,
    }


def upsert_evaluation(project: dict[str, Any], evaluation: Mapping[str, Any], *, generated_at: str = "") -> None:
    candidate = evaluation.get("candidate") if isinstance(evaluation.get("candidate"), Mapping) else {}
    candidate_key = node_key(candidate)
    if not candidate_key[0]:
        return
    current = stored_graph(project)
    if generated_at and not current.get("generated_at"):
        current["generated_at"] = generated_at
    evaluations = [
        item for item in current.get("evaluations", [])
        if node_key(item.get("candidate", {}) if isinstance(item.get("candidate"), Mapping) else {}) != candidate_key
    ]
    saved = copy.deepcopy(dict(evaluation))
    if candidate_key[0] in URL_NODE_KINDS:
        saved["inputs_digest"] = evaluation_inputs_digest(project)
    evaluations.append(saved)
    current["evaluations"] = evaluations
    set_graph(project, current)


def confirmed_by_trust_policy(project: Mapping[str, Any], kind: str, value: str) -> bool:
    key = (kind, value)
    for item in graph(project).get("evaluations", []):
        candidate = item.get("candidate") if isinstance(item.get("candidate"), Mapping) else {}
        if node_key(candidate) == key and bool(item.get("auto_confirm")) and item.get("policy_version") == TRUST_POLICY_VERSION:
            if kind in URL_NODE_KINDS and item.get("inputs_digest") != evaluation_inputs_digest(project):
                return False
            if kind in URL_NODE_KINDS:
                from . import runtime_view
                anchor = item.get("anchor") or {}
                if not any(identifier.kind == anchor.get("kind") and identifier.value == anchor.get("value")
                           and identifier.classification == "confirmed"
                           for identifier in runtime_view.project_identifiers(project)):
                    return False
            return True
    return False
