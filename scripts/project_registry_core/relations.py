from __future__ import annotations

import re
import urllib.parse
from typing import Any

from . import collector_relation_policy
from .repositories import repo_urls
from .identity import resource_identity
from .evidence import EVIDENCE_CLASSIFICATIONS, EvidenceEntity, EvidenceRecord, RelationEvidence


RELATION_PREDICATES = {
    "defines_schema",
    "uses_collection",
    "mentions_collection",
    "official_account",
    "operates_service",
    "repository_homepage",
    "project_repository",
    "owns_repository",
    "requests_permission",
    "redirects_to",
}
CLASSIFICATION_RANK = {"heuristic": 0, "candidate": 1, "derived": 2, "confirmed": 3}


def _normalize_relation(relation: RelationEvidence, context: dict[str, Any]) -> tuple[RelationEvidence, dict[str, Any]]:
    payload = relation.to_dict()
    if (relation.predicate == "project_repository" and relation.subject.type == "did"
            and relation.object.type == "repo" and relation.method == "pds_list_records"):
        payload["predicate"] = "owns_repository"
        context = {**context, "legacy_predicate": relation.predicate}
    originals = list(context.get("original_endpoints", []))
    for side in ("subject", "object"):
        entity = payload[side]
        value = resource_identity.normalize_value(entity["type"], entity["value"])
        if value and value != entity["value"]:
            original = {"side": side, **entity}
            if original not in originals:
                originals.append(original)
            payload[side] = {**entity, "value": value}
    if originals:
        context = {**context, "original_endpoints": originals}
    return RelationEvidence.from_dict(payload), context


def _merge_context(first: dict[str, Any], second: dict[str, Any], *, latest_only: bool = False) -> dict[str, Any]:
    merged = dict(second) if latest_only else {**first, **second}
    originals = list(first.get("original_endpoints", []))
    for original in second.get("original_endpoints", []):
        if original not in originals:
            originals.append(original)
    if originals:
        merged["original_endpoints"] = originals
    return merged


def relation_dedupe_key(value: dict[str, Any]) -> tuple[str, ...]:
    relation = RelationEvidence.from_dict(dict(value.get("relation") or value.get("value") or value))
    relation, _ = _normalize_relation(relation, {})
    return (
        relation.subject.type,
        relation.subject.value,
        relation.predicate,
        relation.object.type,
        relation.object.value,
        str(value.get("source") or ""),
        relation.locator,
        relation.method,
    )


def relation_record(
    *,
    source: str,
    classification: str,
    observed_at: str,
    subject_type: str,
    subject_value: str,
    predicate: str,
    object_type: str,
    object_value: str,
    locator: str = "",
    method: str = "",
    context: dict[str, Any] | None = None,
) -> EvidenceRecord:
    if predicate not in RELATION_PREDICATES:
        raise ValueError(f"unsupported relation predicate: {predicate}")
    relation = RelationEvidence(
        subject=EvidenceEntity(subject_type, subject_value),
        predicate=predicate,
        object=EvidenceEntity(object_type, object_value),
        locator=locator,
        method=method,
    )
    relation, normalized_context = _normalize_relation(relation, context or {})
    return EvidenceRecord(
        kind="relation",
        source=source,
        classification=classification,  # type: ignore[arg-type]
        observed_at=observed_at,
        value=relation.to_dict(),
        context=normalized_context,
    )


def normalize_relation_records(values: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    indexes: dict[tuple[str, ...], int] = {}
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            relation = RelationEvidence.from_dict(dict(item.get("relation") or item.get("value") or item))
        except ValueError:
            continue
        if relation.predicate not in RELATION_PREDICATES:
            continue
        relation, context = _normalize_relation(relation, dict(item.get("context") or {}))
        classification = str(item.get("classification") or "candidate")
        if classification not in EVIDENCE_CLASSIFICATIONS:
            classification = "candidate"
        normalized = {
            "relation": relation.to_dict(),
            "source": str(item.get("source") or ""),
            "classification": classification,
            "observed_at": str(item.get("observed_at") or ""),
            "context": context,
        }
        key = relation_dedupe_key(normalized)
        if key not in indexes:
            indexes[key] = len(output)
            output.append(normalized)
            continue
        existing = output[indexes[key]]
        if relation.predicate in {"project_repository", "repository_homepage"} and {
            relation.subject.type, relation.object.type
        } in ({"repo", "website"}, {"repo", "service"}):
            if normalized["observed_at"] >= existing["observed_at"]:
                normalized["context"] = _merge_context(existing["context"], normalized["context"], latest_only=True)
                output[indexes[key]] = normalized
            else:
                existing["context"] = _merge_context(normalized["context"], existing["context"], latest_only=True)
            continue
        if CLASSIFICATION_RANK[classification] > CLASSIFICATION_RANK[existing["classification"]]:
            existing["classification"] = classification
        if normalized["observed_at"] > existing["observed_at"]:
            existing["observed_at"] = normalized["observed_at"]
        existing["context"] = _merge_context(existing["context"], normalized["context"])
    return output


def classify_lexicon_path(path: str, schema: dict[str, Any]) -> tuple[str, str]:
    lowered = str(path or "").lower()
    excluded_segments = ("/vendor/", "/generated/", "/fixtures/", "/fixture/", "/test/", "/tests/")
    padded = f"/{lowered.strip('/')}"
    if any(segment in padded for segment in excluded_segments):
        return "mentions_collection", "candidate"
    if any(segment in padded for segment in ("/example/", "/examples/", "/docs/", "/documentation/")):
        return "mentions_collection", "candidate"
    if int(schema.get("lexicon") or 0) == 1 and isinstance(schema.get("defs"), dict):
        return "defines_schema", "candidate"
    return "mentions_collection", "candidate"


def oauth_metadata_relation_records(metadata_url: str, metadata: dict[str, Any], observed_at: str) -> list[EvidenceRecord]:
    from .oauth_metadata import analyze_scope
    return oauth_analysis_relation_records(metadata_url, analyze_scope(metadata.get("scope")), observed_at)


def oauth_analysis_relation_records(metadata_url: str, analysis: dict[str, Any], observed_at: str) -> list[EvidenceRecord]:
    by_target: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for permission in analysis.get("permissions", []):
        kind = permission["kind"]
        # Preserve the existing relation surface. RPCs and wildcards are not
        # collection NSIDs; an include NSID denotes an unresolved permission set.
        targets = permission.get("collection", []) if kind == "repo" else ([permission["nsid"]] if kind == "include" else [])
        for target in targets:
            if target == "*":
                continue
            by_target.setdefault((kind, target), []).append(permission)
    return [relation_record(
        source=f"oauth_client_metadata:{metadata_url}",
        classification="candidate" if kind == "include" else "derived",
        observed_at=observed_at,
        subject_type="oauth_client",
        subject_value=metadata_url,
        predicate="requests_permission",
        object_type="nsid",
        object_value=target,
        locator=metadata_url,
        method=f"oauth_scope:{kind}",
        context={"declaration": True, "scope_permissions": permissions},
    ) for (kind, target), permissions in by_target.items()]


def oauth_metadata_candidates(base_url: str, links: Any, html: str = "") -> list[str]:
    candidates: list[str] = []
    for item in links if isinstance(links, list) else []:
        if not isinstance(item, dict):
            continue
        href = urllib.parse.urljoin(base_url, str(item.get("href") or ""))
        label = f"{href} {item.get('text', '')}".lower()
        if href and "oauth" in label and ("client" in label or "metadata" in label):
            candidates.append(href)
    for match in re.findall(r"https?://[^\s\"'<>]+", str(html or "")):
        lowered = match.lower()
        if "oauth" in lowered and ("client" in lowered or "metadata" in lowered):
            candidates.append(match.rstrip(".,);"))
    return list(dict.fromkeys(candidates))[:3]


def identifier_is_guarded(project: dict[str, Any], identifier_type: str, value: str) -> bool:
    for field in ("blocked_identifiers", "deferred_identifiers"):
        for item in project.get(field, []):
            if not isinstance(item, dict) or str(item.get("type") or "") != identifier_type:
                continue
            if resource_identity.matches_guard(identifier_type, value, str(item.get("value") or ""), descendants=True):
                return True
    return False


def corroborate_source_repositories(project: dict[str, Any]) -> list[str]:
    relations = normalize_relation_records(project.get("relation_evidence", []))
    app_to_repo: dict[str, set[str]] = {}
    repo_to_app: dict[str, set[str]] = {}
    for item in relations:
        if item["classification"] != "confirmed":
            continue
        relation = item["relation"]
        subject, obj = relation["subject"], relation["object"]
        if relation["predicate"] == "project_repository":
            if subject["type"] in {"service", "website"} and obj["type"] == "repo":
                repo_url = repo_urls.canonical_repo_url(obj["value"])
                service_url = collector_relation_policy.normalize_repository_homepage(subject["value"])
                if repo_url and service_url:
                    app_to_repo.setdefault(repo_url, set()).add(service_url)
            elif subject["type"] == "repo" and obj["type"] in {"service", "website"}:
                repo_url = repo_urls.canonical_repo_url(subject["value"])
                service_url = collector_relation_policy.normalize_repository_homepage(obj["value"])
                if repo_url and service_url:
                    repo_to_app.setdefault(repo_url, set()).add(service_url)
        elif (
            relation["predicate"] == "repository_homepage"
            and subject["type"] == "repo"
            and obj["type"] == "website"
        ):
            repo_url = repo_urls.canonical_repo_url(subject["value"])
            homepage = collector_relation_policy.normalize_repository_homepage(obj["value"])
            if (
                repo_url
                and homepage
                and collector_relation_policy.repository_homepage_service_candidate(homepage)
            ):
                repo_to_app.setdefault(repo_url, set()).add(homepage)
    return sorted(
        repo
        for repo in set(app_to_repo) & set(repo_to_app)
        if app_to_repo[repo] & repo_to_app[repo]
        and not identifier_is_guarded(project, "repo_url", repo)
    )
