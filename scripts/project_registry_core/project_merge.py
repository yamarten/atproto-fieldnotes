"""Duplicate comparison, merge planning, and unsaved record composition.

Comparison may read the supplied database; external facts require an explicit
lookup supplied by the application. This module never finalizes or saves.
"""

from __future__ import annotations

import copy
import json
from .identity import resource_identity
import sqlite3
import urllib.parse
from typing import Any, Callable

from project_registry_core import atproto_usage
from project_registry_core import collection_trust
from project_registry_core.identity import domain_policy as domain_policy_core
from project_registry_core import editorial
from project_registry_core.persistence import concurrency, identifier_storage
from project_registry_core.identity import identifiers as identifier_core
from project_registry_core import oauth_metadata
from project_registry_core import operator_feedback
from project_registry_core.repositories import repo_urls as repo_url_core
from project_registry_core.repositories import repository_analysis as repository_analysis_core
from project_registry_core.persistence import runtime_view
from project_registry_core import project_record


def merge_promotion_priority_adjustments(
    target_entries: list[Any],
    source_entries: list[Any],
) -> list[dict[str, str]]:
    target = project_record.normalize_promotion_priority_adjustments(target_entries)
    source = project_record.normalize_promotion_priority_adjustments(source_entries)
    combined = [*target, *source]
    target_active = [index for index, item in enumerate(target) if not item.get("cleared_at")]
    source_active = [
        len(target) + index
        for index, item in enumerate(source)
        if not item.get("cleared_at")
    ]
    chosen = target_active[-1] if target_active else source_active[-1] if source_active else None
    if chosen is not None:
        cleared_at = project_record.iso_now()
        for index, item in enumerate(combined):
            if index != chosen and not item.get("cleared_at"):
                item["cleared_at"] = cleared_at
    return combined


def trusted_collection_nsids_for_merge(project: dict[str, Any]) -> set[str]:
    candidates = {
        identifier_core.normalize_nsid(str(value or ""))
        for value in (
            list(project.get("input_nsids", []))
            + list(project.get("resolved_nsids", []))
            + list(project.get("lexicons", []))
            + project_record.manual_seed_values(project, "nsid")
            + list(project_record.active_confirmed_identifier_values(project, "nsid"))
        )
        if identifier_core.normalize_nsid(str(value or ""))
    }
    trusted: set[str] = set()
    for nsid in candidates:
        if (collection_trust.publisher_only_adoption(project, nsid)
                or collection_trust.unverified_publication_origin(project, nsid)):
            continue
        if project_record.project_trusted_collection_nsid_from_state(project, nsid):
            trusted.add(nsid)
    return trusted


def project_merge_identity_hosts(project: dict[str, Any]) -> set[str]:
    hosts = set(project_record.service_hosts(project)) | set(project_record.manual_service_hosts(project))
    for domain in project_record.active_confirmed_identifier_values(project, "domain"):
        lowered = project_record.normalize_blocked_identifier_value("domain", domain)
        if lowered and not project_record.is_shared_official_identity_host(lowered):
            hosts.add(lowered)
    for nsid in trusted_collection_nsids_for_merge(project):
        authority_domain = identifier_core.derive_authority_domain(nsid)
        if authority_domain and not project_record.is_shared_official_identity_host(authority_domain):
            hosts.add(authority_domain)
    return {host for host in hosts if host}


def project_merge_authority_families(
    project: dict[str, Any], *, identity_hosts: set[str] | None = None,
) -> set[str]:
    hosts = project_merge_identity_hosts(project) if identity_hosts is None else identity_hosts
    return {root for host in hosts if (root := domain_policy_core.root_domain(host))}


def project_merge_domain_identity_tokens(
    project: dict[str, Any], *, identity_hosts: set[str] | None = None,
) -> set[str]:
    tokens: set[str] = set()
    hosts = project_merge_identity_hosts(project) if identity_hosts is None else identity_hosts
    for host in hosts:
        for value in (host, domain_policy_core.root_domain(host)):
            token = identifier_core.normalize_alias_token(value)
            if token:
                tokens.add(token)
    return tokens


def merge_score_from_reasons(reasons: dict[str, list[str]]) -> int:
    weights = {
        "project_repository_urls": 8,
        "github_repo_urls": 8,
        "candidate_project_repository_urls": 5,
        "service_urls": 7,
        "website_urls": 2,
        "recorded_lexicon_namespaces": 6,
        "record_creators_and_namespace_lineage": 7,
        "authority_service_families": 6,
        "domain_identity_tokens": 5,
        "collection_first_record_creator_dids": 6,
        "merge_aliases": 3,
    }
    score = 0
    for key, values in reasons.items():
        # Many shared candidate claims still represent one unconfirmed signal.
        score += 5 if key == "candidate_confirmed_identifiers" else weights.get(key, 1) * max(1, len(values))
    return score


def merge_confidence_from_reasons(reasons: dict[str, list[str]], score: int) -> tuple[str, bool]:
    if "candidate_confirmed_identifiers" in reasons:
        established = {key: values for key, values in reasons.items() if key != "candidate_confirmed_identifiers"}
        confidence, ready = merge_confidence_from_reasons(established, merge_score_from_reasons(established))
        return ("medium" if confidence == "low" else confidence), ready
    if "service_urls" in reasons:
        return "high", True
    if any(key in reasons for key in {"project_repository_urls", "github_repo_urls"}) and (
        "merge_aliases" in reasons
        or "domain_identity_tokens" in reasons
        or "authority_service_families" in reasons
        or "recorded_lexicon_namespaces" in reasons
    ):
        return "high", True
    if set(reasons).issubset({"project_repository_urls", "github_repo_urls"}):
        return "medium", False
    if "candidate_project_repository_urls" in reasons and (
        "domain_identity_tokens" in reasons
        or "merge_aliases" in reasons
        or "authority_service_families" in reasons
    ):
        return "high", True
    if "collection_first_record_creator_dids" in reasons and "merge_aliases" in reasons:
        return "medium", True
    if "authority_service_families" in reasons and "recorded_lexicon_namespaces" in reasons:
        return "high", True
    if "record_creators_and_namespace_lineage" in reasons:
        return "high", True
    if "domain_identity_tokens" in reasons and "merge_aliases" in reasons:
        return "medium", True
    if score >= 8:
        return "medium", True
    if score >= 4:
        return "medium", False
    return "low", False


def merge_is_blocked(project_a: dict[str, Any], project_b: dict[str, Any]) -> bool:
    key_a = str(project_a.get("project_key") or "")
    key_b = str(project_b.get("project_key") or "")
    if not key_a or not key_b or key_a == key_b:
        return False
    blocked_a = {str(item).strip() for item in project_a.get("separate_project_keys", []) if str(item).strip()}
    blocked_b = {str(item).strip() for item in project_b.get("separate_project_keys", []) if str(item).strip()}
    return key_b in blocked_a or key_a in blocked_b


def merge_pair_multi_tenant_suffix_suggestions(project_a: dict[str, Any], project_b: dict[str, Any]) -> list[dict[str, Any]]:
    configured = domain_policy_core.configured_multi_tenant_suffixes() | domain_policy_core.KNOWN_MULTI_TENANT_SUFFIXES
    source_hosts = sorted(project_merge_identity_hosts(project_a))
    target_hosts = sorted(project_merge_identity_hosts(project_b))
    suggestions: dict[str, dict[str, Any]] = {}
    for source_host in source_hosts:
        source_root = domain_policy_core.root_domain(source_host, include_configured_suffixes=False)
        if not source_root or source_root in configured or source_root == source_host:
            continue
        source_tenant = domain_policy_core.tenant_identity_for_suffix(source_host, source_root)
        if not source_tenant:
            continue
        for target_host in target_hosts:
            if target_host == source_host:
                continue
            target_root = domain_policy_core.root_domain(target_host, include_configured_suffixes=False)
            if target_root != source_root:
                continue
            target_tenant = domain_policy_core.tenant_identity_for_suffix(target_host, source_root)
            if not target_tenant or target_tenant == source_tenant:
                continue
            entry = suggestions.setdefault(
                source_root,
                {
                    "suffix": source_root,
                    "source_tenants": [],
                    "target_tenants": [],
                    "source_hosts": [],
                    "target_hosts": [],
                    "reason": "shared_registrable_domain_with_distinct_tenants",
                    "configured": False,
                },
            )
            entry["source_tenants"] = project_record.add_unique(entry["source_tenants"], source_tenant)
            entry["target_tenants"] = project_record.add_unique(entry["target_tenants"], target_tenant)
            entry["source_hosts"] = project_record.add_unique(entry["source_hosts"], source_host)
            entry["target_hosts"] = project_record.add_unique(entry["target_hosts"], target_host)
    return sorted(suggestions.values(), key=lambda item: str(item.get("suffix") or ""))


def duplicate_candidate_for_pair(
    project: dict[str, Any],
    other: dict[str, Any],
    *,
    project_profile: dict[str, set[str]] | None = None,
    other_profile: dict[str, set[str]] | None = None,
) -> dict[str, Any] | None:
    if other.get("project_key") == project.get("project_key"):
        return None
    if merge_is_blocked(project, other):
        return None
    target_profile = project_profile or duplicate_profile(project)
    candidate_profile = other_profile or duplicate_profile(other)
    reasons: dict[str, list[str]] = {}
    identifier_matches = candidate_confirmed_identifier_matches(target_profile, candidate_profile)
    if identifier_matches:
        reasons["candidate_confirmed_identifiers"] = identifier_match_values(identifier_matches)
    source_overlap = sorted(target_profile["project_repository_urls"] & candidate_profile["project_repository_urls"])
    if source_overlap:
        reasons["project_repository_urls"] = source_overlap
    github_overlap = sorted(target_profile["github_repo_urls"] & candidate_profile["github_repo_urls"])
    if github_overlap:
        reasons["github_repo_urls"] = github_overlap
    candidate_repo_overlap = sorted(target_profile["candidate_project_repository_urls"] & candidate_profile["candidate_project_repository_urls"])
    if candidate_repo_overlap:
        reasons["candidate_project_repository_urls"] = candidate_repo_overlap
    service_overlap = sorted(target_profile["service_urls"] & candidate_profile["service_urls"])
    if service_overlap:
        reasons["service_urls"] = service_overlap
    website_overlap = sorted(target_profile["website_urls"] & candidate_profile["website_urls"])
    if website_overlap:
        reasons["website_urls"] = website_overlap
    namespace_overlap = sorted(target_profile["recorded_lexicon_namespaces"] & candidate_profile["recorded_lexicon_namespaces"])
    if namespace_overlap:
        reasons["recorded_lexicon_namespaces"] = namespace_overlap
    record_creator_overlap = sorted(set(project.get("record_creators", [])) & set(other.get("record_creators", [])))
    namespace_lineage = namespace_lineage_overlap(project, other)
    if record_creator_overlap and namespace_lineage:
        reasons["record_creators_and_namespace_lineage"] = namespace_lineage
    family_overlap = sorted(target_profile["authority_service_families"] & candidate_profile["authority_service_families"])
    if family_overlap:
        reasons["authority_service_families"] = family_overlap
    domain_token_overlap = sorted(target_profile["domain_identity_tokens"] & candidate_profile["domain_identity_tokens"])
    if domain_token_overlap:
        reasons["domain_identity_tokens"] = domain_token_overlap
    alias_overlap = sorted(target_profile["merge_aliases"] & candidate_profile["merge_aliases"])
    if alias_overlap:
        reasons["merge_aliases"] = alias_overlap
    if not reasons:
        return None
    score = merge_score_from_reasons(reasons)
    confidence, auto_merge_ready = merge_confidence_from_reasons(reasons, score)
    return {
        "project_key": other["project_key"],
        "name": other.get("name") or "",
        "status": other.get("status") or "",
        "reasons": reasons,
        "score": score,
        "merge_confidence": confidence,
        "auto_merge_ready": auto_merge_ready,
        "identifier_matches": identifier_matches,
    }


def duplicate_candidate_keys(
    inputs: list[dict[str, Any]], target_profile: dict[str, set[str]], *,
    check_cancelled: Callable[[], None] | None = None,
) -> set[str] | None:
    """Return a superset of matches without reading large project state.

    Ignore guards, adoption restrictions and classification for URL/domain
    witnesses here; the regular policy applies them after loading selected rows.
    A namespace/creator lineage input needs saved state, so retain the complete
    comparison set in that case. Creator overlap alone cannot produce a match.
    """
    if target_profile["recorded_lexicon_namespaces"]:
        return None
    keys: set[str] = set()
    roots: dict[str, str] = {}

    def root(host: str) -> str:
        if host not in roots:
            roots[host] = domain_policy_core.root_domain(host)
        return roots[host]

    for project in inputs:
        if check_cancelled:
            check_cancelled()
        repos: set[str] = set()
        services: set[str] = set()
        hosts: set[str] = set()
        component_match = False
        for kind, value, role, classification in project["identifier_claims"]:
            normalized = resource_identity.normalize_value(kind, value)
            opposite = "confirmed" if classification == "candidate" else "candidate" if classification == "confirmed" else ""
            if opposite and role in identifier_storage.COMPONENT_ROLES:
                claim = json.dumps([kind, normalized, role])
                if claim in target_profile[f"{opposite}_component_identifiers"]:
                    component_match = True
                    break
            if kind == "repo_url":
                if url := repo_url_core.canonical_repo_url(value):
                    repos.add(url)
            elif kind == "service_url":
                if url := identifier_core.normalize_url(value):
                    services.add(url)
                    hosts.add(urllib.parse.urlparse(url).netloc.lower())
            elif kind == "nsid":
                if host := identifier_core.derive_authority_domain(normalized):
                    hosts.add(host)
            elif kind == "domain" and normalized:
                hosts.add(normalized)
        key = str(project["project_key"])
        if component_match:
            keys.add(key)
            continue
        for kind, value in project["confirmation_claims"]:
            if kind == "service_url":
                hosts.add(urllib.parse.urlparse(identifier_core.normalize_url(value)).netloc.lower())
            elif kind == "nsid":
                if host := identifier_core.derive_authority_domain(identifier_core.normalize_nsid(value)):
                    hosts.add(host)
            elif kind == "domain":
                hosts.add(project_record.normalize_blocked_identifier_value(kind, value))
        if (repos & (target_profile["project_repository_urls"] | target_profile["github_repo_urls"] | target_profile["candidate_project_repository_urls"])
                or services & target_profile["service_urls"]
                or str(project.get("website_url") or "") in target_profile["website_urls"]
                or project_record.project_merge_aliases({**project, "project_repository_urls": list(repos)}) & target_profile["merge_aliases"]):
            keys.add(key)
            continue
        for host in hosts - {""}:
            family = root(host)
            if family in target_profile["authority_service_families"] or any(
                identifier_core.normalize_alias_token(value) in target_profile["domain_identity_tokens"]
                for value in (host, family) if value
            ):
                keys.add(key)
                break
    return keys


def compute_duplicate_candidates(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    *,
    projects: list[dict[str, Any]] | None = None,
    duplicate_profiles: dict[str, dict[str, set[str]]] | None = None,
    duplicate_index: dict[str, dict[str, set[str]]] | None = None,
    project_lookup: dict[str, dict[str, Any]] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Compare a target, optionally sharing an unchanged indexed snapshot.

    project_lookup must refer to the same records used to build the supplied
    profiles/index, with no edits since indexing. Separate unsaved targets still
    get a fresh profile; ordinary callers need not supply a lookup.
    """
    candidates: list[dict[str, Any]] = []
    if check_cancelled:
        check_cancelled()
    prefilter_profile = None
    if projects is None:
        # Prefilter and complete comparison reads must see the same snapshot.
        with concurrency.read_snapshot(conn):
            inputs = project_record.list_duplicate_candidate_inputs(conn, check_cancelled=check_cancelled)
            prefilter_profile = duplicate_profile(project)
            keys = duplicate_candidate_keys(inputs, prefilter_profile, check_cancelled=check_cancelled)
            if keys is not None:
                keys.discard(str(project.get("project_key") or ""))
            all_projects = project_record.list_duplicate_projects(conn, project_keys=keys, check_cancelled=check_cancelled)
    else:
        all_projects = projects
    if duplicate_profiles is None or duplicate_index is None:
        duplicate_profiles, duplicate_index = build_duplicate_candidate_index(all_projects, check_cancelled=check_cancelled)
    # A caller-supplied target may contain unsaved edits. Only reuse its indexed
    # profile when it is the very same record used to build the comparison set.
    key = str(project.get("project_key") or "")
    target_profile = (
        duplicate_profiles.get(key)
        if project_lookup is not None and project_lookup.get(key) is project
        else None
    ) or prefilter_profile or duplicate_profile(project)
    candidate_keys: set[str] = set()
    for field, values in target_profile.items():
        opposite_field = {
            "candidate_component_identifiers": "confirmed_component_identifiers",
            "confirmed_component_identifiers": "candidate_component_identifiers",
        }.get(field, field)
        field_index = duplicate_index.get(opposite_field, {})
        for value in values:
            candidate_keys.update(field_index.get(str(value), set()))
    candidate_keys.discard(str(project.get("project_key") or ""))

    if project_lookup is None:
        project_lookup = {
            str(item.get("project_key") or ""): item
            for item in all_projects
            if str(item.get("project_key") or "")
        }
    for other_key in sorted(candidate_keys):
        if check_cancelled:
            check_cancelled()
        other = project_lookup.get(other_key)
        if other is None:
            continue
        if other["project_key"] == project["project_key"]:
            continue
        other_profile = duplicate_profiles.get(other_key) or duplicate_profile(other)
        candidate = duplicate_candidate_for_pair(
            project,
            other,
            project_profile=target_profile,
            other_profile=other_profile,
        )
        if candidate:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (-int(item.get("score", 0)), item["project_key"]))
    return candidates


def compute_merge_recommendation(project: dict[str, Any]) -> dict[str, Any]:
    candidates = list(project.get("duplicate_candidates") or [])
    if not candidates:
        return {}
    top = candidates[0]
    recommendation = {
        "target_project_key": top.get("project_key", ""),
        "score": int(top.get("score", 0) or 0),
        "merge_confidence": str(top.get("merge_confidence") or ""),
        "auto_merge_ready": bool(top.get("auto_merge_ready")),
        "reasons": dict(top.get("reasons") or {}),
    }
    if not recommendation["auto_merge_ready"] and len(candidates) > 1:
        second = candidates[1]
        if int(second.get("score", 0) or 0) == recommendation["score"]:
            recommendation["ambiguous_with"] = [str(second.get("project_key") or "")]
    return recommendation


def component_identifier_profile(project: dict[str, Any]) -> dict[str, set[str]]:
    profile: dict[str, set[str]] = {
        "candidate_component_identifiers": set(),
        "confirmed_component_identifiers": set(),
    }
    for item in runtime_view.project_identifiers(project):
        if item.role not in identifier_storage.COMPONENT_ROLES or item.classification not in {"candidate", "confirmed"}:
            continue
        if project_record.identifier_is_blocked(project, item.kind, item.value) or project_record.identifier_is_deferred(project, item.kind, item.value):
            continue
        profile[f"{item.classification}_component_identifiers"].add(json.dumps([item.kind, resource_identity.normalize_value(item.kind, item.value), item.role]))
    return profile


def candidate_confirmed_identifier_matches(
    project_profile: dict[str, set[str]], other_profile: dict[str, set[str]],
) -> list[dict[str, str]]:
    matches = []
    for classification, other_classification in (("candidate", "confirmed"), ("confirmed", "candidate")):
        overlap = project_profile[f"{classification}_component_identifiers"] & other_profile[f"{other_classification}_component_identifiers"]
        for claim in sorted(overlap):
            kind, value, role = json.loads(claim)
            matches.append({"kind": kind, "value": value, "role": role,
                            "project_classification": classification, "other_classification": other_classification})
    return matches


def identifier_match_values(matches: list[dict[str, str]]) -> list[str]:
    return sorted({f"{item['kind']}: {item['value']} ({item['role']})" for item in matches})


def duplicate_profile(project: dict[str, Any]) -> dict[str, set[str]]:
    identity_hosts = project_merge_identity_hosts(project)
    return {
        **component_identifier_profile(project),
        "project_repository_urls": set(project_record.project_repository_url_values(project)),
        "github_repo_urls": set(project_record.provider_repo_url_values(project, "github")),
        "candidate_project_repository_urls": set(repo_url_core.normalize_repo_collection(project.get("candidate_project_repository_urls", []))),
        "service_urls": set(project.get("service_urls", [])),
        "website_urls": {str(project.get("website_url") or "")} if project.get("website_url") else set(),
        "recorded_lexicon_namespaces": set(project.get("recorded_lexicon_namespaces", [])),
        "record_creators": set(project.get("record_creators", [])),
        "authority_service_families": project_merge_authority_families(project, identity_hosts=identity_hosts),
        "domain_identity_tokens": project_merge_domain_identity_tokens(project, identity_hosts=identity_hosts),
        "merge_aliases": project_record.project_merge_aliases(project),
    }


def namespace_lineage_overlap(project_a: dict[str, Any], project_b: dict[str, Any]) -> list[str]:
    namespaces_a = {
        str(value or "").strip()
        for value in project_a.get("recorded_lexicon_namespaces", [])
        if str(value or "").strip()
    }
    namespaces_b = {
        str(value or "").strip()
        for value in project_b.get("recorded_lexicon_namespaces", [])
        if str(value or "").strip()
    }
    overlap: set[str] = set()
    for left in namespaces_a:
        for right in namespaces_b:
            if left == right:
                overlap.add(left)
            elif left.startswith(f"{right}."):
                overlap.add(right)
            elif right.startswith(f"{left}."):
                overlap.add(left)
    return sorted(overlap)


def build_duplicate_candidate_index(
    projects: list[dict[str, Any]], *, check_cancelled: Callable[[], None] | None = None,
) -> tuple[dict[str, dict[str, set[str]]], dict[str, dict[str, set[str]]]]:
    profiles: dict[str, dict[str, set[str]]] = {}
    index: dict[str, dict[str, set[str]]] = {
        "candidate_component_identifiers": {},
        "confirmed_component_identifiers": {},
        "project_repository_urls": {},
        "github_repo_urls": {},
        "candidate_project_repository_urls": {},
        "service_urls": {},
        "website_urls": {},
        "recorded_lexicon_namespaces": {},
        "record_creators": {},
        "authority_service_families": {},
        "domain_identity_tokens": {},
        "merge_aliases": {},
    }
    for project in projects:
        if check_cancelled:
            check_cancelled()
        project_key = str(project.get("project_key") or "")
        if not project_key:
            continue
        profile = duplicate_profile(project)
        profiles[project_key] = profile
        for field, values in profile.items():
            field_index = index[field]
            for value in values:
                field_index.setdefault(str(value), set()).add(project_key)
    return profiles, index


def is_strict_github_repo_url(url: str) -> bool:
    normalized = identifier_core.normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc != "github.com":
        return False
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return False
    owner, repo = segments[0], segments[1]
    return bool(repo_url_core.STRICT_REPO_SEGMENT_RE.fullmatch(owner) and repo_url_core.STRICT_REPO_SEGMENT_RE.fullmatch(repo))


def is_strict_tangled_repo_url(url: str) -> bool:
    normalized = repo_url_core.normalize_tangled_repo_url(url)
    if not normalized:
        return False
    parsed = urllib.parse.urlparse(normalized)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 2:
        return False
    owner, repo = segments
    return bool(repo_url_core.STRICT_REPO_SEGMENT_RE.fullmatch(owner) and repo_url_core.STRICT_REPO_SEGMENT_RE.fullmatch(repo))


def batch_service_seed_hosts(project: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    for value in project_record.manual_seed_values(project, "service_url"):
        parsed = urllib.parse.urlparse(value)
        if parsed.netloc:
            hosts.add(parsed.netloc.lower())
    return hosts


def batch_repo_seed_urls(project: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for value in project_record.manual_seed_values(project, "repo_url"):
        normalized = repo_url_core.normalize_repo_collection([value])
        if normalized:
            urls.add(normalized[0])
    return urls


def batch_candidate_repo_urls(project: dict[str, Any]) -> set[str]:
    return set(repo_url_core.normalize_repo_collection(project.get("candidate_project_repository_urls", [])))


def batch_strict_overlap(project_a: dict[str, Any], project_b: dict[str, Any]) -> dict[str, list[str]]:
    if merge_is_blocked(project_a, project_b):
        return {}
    reasons: dict[str, list[str]] = {}
    alias_overlap = sorted(project_record.project_alias_tokens(project_a) & project_record.project_alias_tokens(project_b))
    source_overlap = sorted(set(project_record.project_repository_url_values(project_a)) & set(project_record.project_repository_url_values(project_b)))
    if source_overlap:
        valid = [
            value for value in source_overlap
            if is_strict_github_repo_url(value) or is_strict_tangled_repo_url(value)
        ]
        if valid and alias_overlap:
            reasons["project_repository_urls"] = valid
            reasons["alias_overlap"] = alias_overlap
    github_overlap = sorted(set(project_record.provider_repo_url_values(project_a, "github")) & set(project_record.provider_repo_url_values(project_b, "github")))
    if github_overlap:
        valid = [value for value in github_overlap if is_strict_github_repo_url(value)]
        if valid and alias_overlap:
            reasons["github_repo_urls"] = valid
            reasons["alias_overlap"] = alias_overlap
    repo_seed_overlap = sorted(batch_repo_seed_urls(project_a) & batch_repo_seed_urls(project_b))
    if repo_seed_overlap:
        reasons["manual_repo_seed_urls"] = repo_seed_overlap
    service_seed_overlap = sorted(batch_service_seed_hosts(project_a) & batch_service_seed_hosts(project_b))
    if service_seed_overlap:
        reasons["manual_service_seed_hosts"] = service_seed_overlap
    return reasons


def batch_moderate_overlap(
    project_a: dict[str, Any],
    project_b: dict[str, Any],
    *,
    first_creator_lookup: Callable[[dict[str, Any]], set[str]] | None = None,
    project_profile: dict[str, set[str]] | None = None,
    other_profile: dict[str, set[str]] | None = None,
) -> dict[str, list[str]]:
    if merge_is_blocked(project_a, project_b):
        return {}
    reasons = batch_strict_overlap(project_a, project_b)
    if reasons:
        return reasons

    service_overlap = sorted(set(project_a.get("service_urls", [])) & set(project_b.get("service_urls", [])))
    if service_overlap:
        reasons["confirmed_service_urls"] = service_overlap

    alias_overlap = sorted(project_record.project_alias_tokens(project_a) & project_record.project_alias_tokens(project_b))
    candidate_repo_overlap = sorted(batch_candidate_repo_urls(project_a) & batch_candidate_repo_urls(project_b))
    if candidate_repo_overlap and alias_overlap:
        reasons["candidate_repo_urls"] = candidate_repo_overlap
        reasons["alias_overlap"] = alias_overlap
        return reasons

    record_creator_overlap = sorted(set(project_a.get("record_creators", [])) & set(project_b.get("record_creators", [])))
    namespace_lineage = namespace_lineage_overlap(project_a, project_b)
    if record_creator_overlap and namespace_lineage:
        reasons["record_creators_and_namespace_lineage"] = namespace_lineage
        return reasons

    domain_tokens_a = project_profile["domain_identity_tokens"] if project_profile is not None else project_merge_domain_identity_tokens(project_a)
    domain_tokens_b = other_profile["domain_identity_tokens"] if other_profile is not None else project_merge_domain_identity_tokens(project_b)
    domain_token_overlap = sorted(domain_tokens_a & domain_tokens_b)
    if domain_token_overlap and alias_overlap:
        reasons["domain_identity_tokens"] = domain_token_overlap
        reasons["alias_overlap"] = alias_overlap
        return reasons

    if first_creator_lookup is not None:
        first_creator_overlap = sorted(first_creator_lookup(project_a) & first_creator_lookup(project_b))
        if first_creator_overlap:
            reasons["collection_first_record_creator_dids"] = first_creator_overlap
            if alias_overlap:
                reasons["alias_overlap"] = alias_overlap
            return reasons

    families_a = project_profile["authority_service_families"] if project_profile is not None else project_merge_authority_families(project_a)
    families_b = other_profile["authority_service_families"] if other_profile is not None else project_merge_authority_families(project_b)
    family_overlap = sorted(families_a & families_b)
    if family_overlap and domain_token_overlap:
        reasons["authority_service_families"] = family_overlap
        reasons["domain_identity_tokens"] = domain_token_overlap
        return reasons

    host_a = project_merge_identity_hosts(project_a)
    host_b = project_merge_identity_hosts(project_b)
    root_overlap = sorted({domain_policy_core.root_domain(host) for host in host_a} & {domain_policy_core.root_domain(host) for host in host_b})
    if root_overlap and alias_overlap:
        reasons["manual_service_root_domain_and_alias"] = root_overlap
        reasons["alias_overlap"] = alias_overlap
        return reasons

    website_host_a = urllib.parse.urlparse(identifier_core.normalize_url(str(project_a.get("website_url") or ""))).netloc.lower() if project_a.get("website_url") else ""
    website_host_b = urllib.parse.urlparse(identifier_core.normalize_url(str(project_b.get("website_url") or ""))).netloc.lower() if project_b.get("website_url") else ""
    if website_host_a and website_host_b and domain_policy_core.root_domain(website_host_a) == domain_policy_core.root_domain(website_host_b) and alias_overlap:
        reasons["website_root_domain_and_alias"] = [domain_policy_core.root_domain(website_host_a)]
        reasons["alias_overlap"] = alias_overlap

    identifier_matches = candidate_confirmed_identifier_matches(component_identifier_profile(project_a), component_identifier_profile(project_b))
    if identifier_matches:
        reasons["candidate_confirmed_identifiers"] = identifier_match_values(identifier_matches)

    return reasons


def batch_broad_overlap(
    project_a: dict[str, Any],
    project_b: dict[str, Any],
    recommendation: dict[str, Any],
    *, project_profile: dict[str, set[str]] | None = None,
    other_profile: dict[str, set[str]] | None = None,
) -> dict[str, list[str]]:
    reasons = batch_moderate_overlap(project_a, project_b, project_profile=project_profile, other_profile=other_profile)
    if reasons:
        return reasons
    if merge_is_blocked(project_a, project_b):
        return {}
    recommendation_reasons = recommendation.get("reasons", {})
    if not isinstance(recommendation_reasons, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for key, values in recommendation_reasons.items():
        if isinstance(values, list):
            normalized_values = [str(value) for value in values if str(value).strip()]
            if normalized_values:
                normalized[str(key)] = normalized_values
    return normalized


def project_editorial_preference(project: dict[str, Any]) -> tuple[int, int, int, int, int]:
    confirmed_service = 1 if project.get("service_urls") else 0
    service_surface = 0
    if project.get("website_url"):
        service_surface += 2
    if project.get("service_urls"):
        service_surface += 3
    if project.get("project_repository_urls"):
        service_surface += 2
    if project_record.resource_url_values(project):
        service_surface += 1
    status_rank = {
        "dead": 0,
        "unknown": 1,
        "unverified": 2,
        "none": 3,
        "private": 4,
        "unreleased": 5,
        "beta": 6,
        "invite_only": 7,
        "live": 8,
    }.get(project_record.normalize_service_status(project.get("service_status")), 1)
    introduced = 1 if project.get("introduced_url") else 0
    confirmed_nsids = len(project_record.current_confirmed_nsids(project))
    return (confirmed_service, service_surface, status_rank, introduced, confirmed_nsids)


def project_batch_merge_preference(project: dict[str, Any]) -> tuple[int, tuple[int, int, int, int, int], int, int, str]:
    seed_priority = 0
    if project.get("service_urls"):
        seed_priority = 3
    elif project.get("website_url"):
        seed_priority = 2
    elif project.get("resolved_nsids"):
        seed_priority = 2
    elif project_record.project_repository_url_values(project):
        seed_priority = 1
    editorial_score = project_editorial_preference(project)
    confirmed_count = sum(
        1 for value in [
            bool(project.get("service_urls")),
            bool(project_record.project_repository_url_values(project)),
            bool(project.get("resolved_nsids")),
        ]
        if value
    )
    introduced = 1 if project.get("introduced_url") else 0
    return (seed_priority, editorial_score, confirmed_count, introduced, str(project.get("project_key") or ""))


def choose_batch_merge_direction(project_a: dict[str, Any], project_b: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if project_batch_merge_preference(project_a) >= project_batch_merge_preference(project_b):
        return project_b, project_a
    return project_a, project_b


def build_batch_merge_plan(
    conn: sqlite3.Connection,
    mode: str = "strict",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merge_tracking_states = project_record.TRACKING_STATES - {"ignore"}
    project_list = project_record.list_batch_merge_projects(conn, tracking_states=merge_tracking_states)
    duplicate_profiles, duplicate_index = build_duplicate_candidate_index(project_list)
    projects = {project["project_key"]: project for project in project_list}
    skipped: list[dict[str, Any]] = []
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    analyzed: dict[str, dict[str, Any]] = {}

    for project_key, project in projects.items():
        candidates = compute_duplicate_candidates(
            conn,
            project,
            projects=project_list,
            duplicate_profiles=duplicate_profiles,
            duplicate_index=duplicate_index,
            project_lookup=projects,
        )
        analyzed[project_key] = {
            "project": project,
            "candidates": candidates,
            "recommendation": compute_merge_recommendation(dict(project, duplicate_candidates=candidates)),
        }

    for project_key, payload in analyzed.items():
        project = payload["project"]
        recommendation = payload["recommendation"]
        target_key = str(recommendation.get("target_project_key") or "")
        if not target_key:
            continue
        target_payload = analyzed.get(target_key)
        if not target_payload:
            continue
        target = target_payload["project"]
        reverse_recommendation = target_payload["recommendation"]
        if str(reverse_recommendation.get("target_project_key") or "") != project_key:
            skipped.append(
                attach_batch_entry_seed_fields(
                    {
                    "project_key": project_key,
                    "target_project_key": target_key,
                    "reason": "not_mutual_recommendation",
                    "recommendation": recommendation,
                    },
                    project,
                    target,
                )
            )
            continue
        if mode == "strict":
            safe_reasons = batch_strict_overlap(project, target)
        elif mode == "moderate":
            safe_reasons = batch_moderate_overlap(
                project, target, project_profile=duplicate_profiles[project_key],
                other_profile=duplicate_profiles[target_key],
            )
        else:
            safe_reasons = batch_broad_overlap(
                project, target, recommendation, project_profile=duplicate_profiles[project_key],
                other_profile=duplicate_profiles[target_key],
            )
        if not safe_reasons:
            skipped.append(
                attach_batch_entry_seed_fields(
                    {
                    "project_key": project_key,
                    "target_project_key": target_key,
                    "reason": "no_safe_identity_overlap",
                    "recommendation": recommendation,
                    },
                    project,
                    target,
                )
            )
            continue

        source, target_project = choose_batch_merge_direction(project, target)
        pair_key = tuple(sorted([source["project_key"], target_project["project_key"]]))
        plan = {
            "source_project_key": source["project_key"],
            "target_project_key": target_project["project_key"],
            "source_name": source.get("name", ""),
            "target_name": target_project.get("name", ""),
            "reasons": safe_reasons,
            "recommendation": recommendation,
            "identifier_matches": candidate_confirmed_identifier_matches(duplicate_profiles[source["project_key"]], duplicate_profiles[target_project["project_key"]]),
        }
        existing = selected.get(pair_key)
        if existing is None or int(recommendation.get("score", 0) or 0) > int(existing["recommendation"].get("score", 0) or 0):
            selected[pair_key] = plan

    plans = sorted(
        selected.values(),
        key=lambda item: (
            -int(item["recommendation"].get("score", 0) or 0),
            item["source_project_key"],
            item["target_project_key"],
        ),
    )
    return plans, skipped


def choose_preferred_scalar(current: Any, incoming: Any) -> Any:
    current_value = current if current not in {None, ""} else ""
    incoming_value = incoming if incoming not in {None, ""} else ""
    return current_value or incoming_value


def choose_editorial_scalar(current: Any, incoming: Any, *, prefer_incoming: bool) -> Any:
    current_value = current if current not in {None, ""} else ""
    incoming_value = incoming if incoming not in {None, ""} else ""
    if prefer_incoming and incoming_value:
        return incoming_value
    return current_value or incoming_value


def merge_timestamp(current: str, incoming: str, *, prefer: str) -> str:
    current_value = str(current or "")
    incoming_value = str(incoming or "")
    if not current_value:
        return incoming_value
    if not incoming_value:
        return current_value
    if prefer == "min":
        return min(current_value, incoming_value)
    return max(current_value, incoming_value)


def attach_batch_entry_seed_fields(entry: dict[str, Any], source: dict[str, Any] | None, target: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(entry)
    payload["source_seed_type"] = str(source.get("seed_type") or "") if source else ""
    payload["source_seed_value"] = str(source.get("seed_value") or "") if source else ""
    payload["target_seed_type"] = str(target.get("seed_type") or "") if target else ""
    payload["target_seed_value"] = str(target.get("seed_value") or "") if target else ""
    return payload


def combine_records(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Combine fields and runtime rows; caller must finalize before saving."""
    merged = project_record.ensure_project_shape(dict(target))
    source = project_record.ensure_project_shape(source)
    for current, other in ((target, source), (source, target)):
        for item in runtime_view.project_identifiers(current):
            if item.classification == "confirmed" and (
                project_record.identifier_is_blocked(other, item.kind, item.value)
                or project_record.identifier_is_deferred(other, item.kind, item.value)
            ):
                raise ValueError(f"merge conflicts with an identifier block/defer: {item.kind} {item.value}; resolve the identifier decision first")
    confirmations = []
    for owner in (target, source):
        for entry in owner.get("confirmed_identifiers", []):
            entry = copy.deepcopy(entry)
            if not entry.get("origin_project_key"):
                origins = {
                    row.get("origin_project_key") or owner["project_key"]
                    for row in owner.get(runtime_view.RUNTIME_ADOPTION_ROWS, [])
                    if row.get("kind") == entry.get("type") and row.get("value") == entry.get("value")
                    and (not entry.get("role") or row.get("role") == entry.get("role"))
                    and row.get("source") == entry.get("source") and row.get("created_at") == entry.get("added_at")
                }
                if len(origins) <= 1:
                    entry["origin_project_key"] = next(iter(origins), owner["project_key"])
            confirmations.append(entry)
    merged["confirmed_identifiers"] = project_record.normalize_confirmed_identifiers(confirmations)
    target_editorial_preference = project_editorial_preference(merged)
    source_editorial_preference = project_editorial_preference(source)
    prefer_source_editorial = source_editorial_preference > target_editorial_preference
    merged["blocked_identifiers"] = project_record.normalize_blocked_identifiers(
        list(merged.get("blocked_identifiers", [])) + list(source.get("blocked_identifiers", []))
    )
    merged["deferred_identifiers"] = project_record.normalize_deferred_identifiers(
        list(merged.get("deferred_identifiers", [])) + list(source.get("deferred_identifiers", []))
    )
    merged["promotion_deferrals"] = project_record.normalize_promotion_deferrals(
        list(merged.get("promotion_deferrals", [])) + list(source.get("promotion_deferrals", []))
    )
    merged["promotion_priority_adjustments"] = merge_promotion_priority_adjustments(
        list(merged.get("promotion_priority_adjustments", [])),
        list(source.get("promotion_priority_adjustments", [])),
    )
    for field in ("introduction_plans", "service_usage_decisions"):
        entries = operator_feedback.merge_history(merged.get(field, []), source.get(field, []))
        active_targets = set()
        for entry in entries:
            if entry.get("cleared_at"):
                continue
            feedback_target = "plan" if field == "introduction_plans" else (entry["service_url"], entry["nsid"])
            if feedback_target in active_targets:
                entry["cleared_at"] = project_record.iso_now()
            active_targets.add(feedback_target)
        merged[field] = entries
    merged["dismissed_review_kinds"] = project_record.unique_list(
        list(merged.get("dismissed_review_kinds", [])) + list(source.get("dismissed_review_kinds", []))
    )
    merged["review_target_decisions"] = [
        dict(item)
        for item in list(merged.get("review_target_decisions", [])) + list(source.get("review_target_decisions", []))
        if isinstance(item, dict)
    ]

    merged["manual_seeds"] = project_record.unique_manual_seeds(list(merged.get("manual_seeds", [])) + list(source.get("manual_seeds", [])))
    merged["discovery_sources"] = project_record.unique_list(list(merged.get("discovery_sources", [])) + list(source.get("discovery_sources", [])))
    merged["evidence_urls"] = project_record.unique_list(list(merged.get("evidence_urls", [])) + list(source.get("evidence_urls", [])))

    merged["input_nsids"] = project_record.unique_list(list(merged.get("input_nsids", [])) + list(source.get("input_nsids", [])))
    merged["resolved_nsids"] = project_record.unique_list(list(merged.get("resolved_nsids", [])) + list(source.get("resolved_nsids", [])))
    merged["lexicon_resolution_entries"] = project_record.normalize_lexicon_resolution_entries(
        list(merged.get("lexicon_resolution_entries", [])) + list(source.get("lexicon_resolution_entries", []))
    )
    publication_items = sorted(list(merged.get("publication_schemas", [])) + list(source.get("publication_schemas", [])),
                               key=lambda item: str(item.get("checked_at") or ""))
    merged["publication_schemas"] = list({item["schema_id"]: item for item in publication_items}.values())
    merged["publication_review"] = project_record.unique_list(list(merged.get("publication_review", [])) + list(source.get("publication_review", [])))
    merged["authority_domains"] = project_record.unique_list(list(merged.get("authority_domains", [])) + list(source.get("authority_domains", [])))
    merged["record_creators"] = project_record.unique_list(list(merged.get("record_creators", [])) + list(source.get("record_creators", [])))

    merged["name"] = choose_editorial_scalar(merged.get("name"), source.get("name"), prefer_incoming=prefer_source_editorial)
    target_tagline = str(merged.get("tagline") or "")
    source_tagline = str(source.get("tagline") or "")
    if (
        (not target_tagline and source_tagline)
        or (
            prefer_source_editorial
            and source_tagline
            and (
                not target_tagline
                or (merged.get("tagline_is_provisional") and not source.get("tagline_is_provisional"))
            )
        )
        or (
            merged.get("tagline_is_provisional")
            and not source.get("tagline_is_provisional")
            and source_tagline
            and source_editorial_preference >= target_editorial_preference
        )
    ):
        merged["tagline"] = source_tagline
        merged["tagline_is_provisional"] = bool(source.get("tagline_is_provisional"))
    merged["summary"] = choose_editorial_scalar(merged.get("summary"), source.get("summary"), prefer_incoming=prefer_source_editorial)
    merged["summary_candidates"] = list(merged.get("summary_candidates", [])) + list(source.get("summary_candidates", []))
    merged["tagline_candidates"] = list(merged.get("tagline_candidates", [])) + list(source.get("tagline_candidates", []))
    merged["features"] = project_record.unique_list(list(merged.get("features", [])) + list(source.get("features", [])))
    if editorial.profile(merged) != editorial.profile(target) or not editorial.automatic(source):
        # A merge can combine two descriptions; it is not permission to auto-edit either author's wording.
        merged["editorial"] = {"mode": "review", "profile_hash": editorial.fingerprint(editorial.profile(merged))}
    merged["inspired_by"] = project_record.unique_list(list(merged.get("inspired_by", [])) + list(source.get("inspired_by", [])))

    merged["website_url"] = choose_editorial_scalar(merged.get("website_url"), source.get("website_url"), prefer_incoming=prefer_source_editorial)
    project_record.set_service_url_candidate_values(merged, project_record.service_url_candidate_values(merged) + project_record.service_url_candidate_values(source))
    merged["service_urls"] = project_record.unique_list(list(merged.get("service_urls", [])) + list(source.get("service_urls", [])))
    merged["project_site_urls"] = project_record.unique_list(
        list(merged.get("project_site_urls", [])) + list(source.get("project_site_urls", []))
    )
    merged["project_site_url_candidates"] = project_record.unique_list(
        list(merged.get("project_site_url_candidates", []))
        + list(source.get("project_site_url_candidates", []))
    )
    merged["resource_url_records"] = project_record.normalize_resource_url_records(
        list(project_record.resource_url_record_values(merged)) + list(project_record.resource_url_record_values(source))
    )
    merged["failed_service_urls"] = project_record.unique_list(list(merged.get("failed_service_urls", [])) + list(source.get("failed_service_urls", [])))
    merged["supported_service_urls"] = project_record.unique_list(list(merged.get("supported_service_urls", [])) + list(source.get("supported_service_urls", [])))
    merged["service_status"] = project_record.merge_service_status(str(merged.get("service_status") or "unknown"), str(source.get("service_status") or "unknown"))
    merged["service_status_confidence"] = project_record.merge_service_status_confidence(
        str(merged.get("service_status_confidence") or "none"),
        str(source.get("service_status_confidence") or "none"),
    )
    merged["service_status_evidence"] = project_record.unique_list(
        list(merged.get("service_status_evidence", [])) + list(source.get("service_status_evidence", []))
    )

    merged["project_repository_urls"] = repo_url_core.normalize_repo_collection(project_record.project_repository_url_values(merged) + project_record.project_repository_url_values(source))
    merged["candidate_project_repository_urls"] = repo_url_core.normalize_repo_collection(
        list(merged.get("candidate_project_repository_urls", [])) + list(source.get("candidate_project_repository_urls", []))
    )
    merged["source_repo_records"] = project_record.normalize_source_repo_records(
        list(merged.get("source_repo_records", [])) + list(source.get("source_repo_records", []))
    )
    merged["repository_analysis_results"] = repository_analysis_core.normalize_results(
        list(merged.get("repository_analysis_results", []))
        + list(source.get("repository_analysis_results", []))
    )
    merged["oauth_client_metadata"] = oauth_metadata.normalize_results(
        list(merged.get("oauth_client_metadata", [])) + list(source.get("oauth_client_metadata", []))
    )
    project_record.sync_source_repo_provider_views(merged)

    merged["bluesky_handles"] = project_record.unique_list(list(merged.get("bluesky_handles", [])) + list(source.get("bluesky_handles", [])))
    merged["tangled_handles"] = project_record.unique_list(list(merged.get("tangled_handles", [])) + list(source.get("tangled_handles", [])))
    merged["developer_accounts"] = project_record.normalize_developer_accounts(list(merged.get("developer_accounts", [])) + list(source.get("developer_accounts", [])))
    merged["candidate_developer_accounts"] = project_record.normalize_developer_accounts(
        list(merged.get("candidate_developer_accounts", [])) + list(source.get("candidate_developer_accounts", []))
    )

    merged["introduced_url"] = choose_preferred_scalar(merged.get("introduced_url"), source.get("introduced_url"))

    merged["app_category"] = choose_preferred_scalar(merged.get("app_category"), source.get("app_category"))
    merged["category"] = choose_preferred_scalar(merged.get("category"), source.get("category"))
    merged["atproto_auth_only"] = bool(merged.get("atproto_auth_only") or source.get("atproto_auth_only"))
    merged["atproto_usage_decisions"] = atproto_usage.merge_decisions(
        merged.get("atproto_usage_decisions"), source.get("atproto_usage_decisions"),
        cleared_at=project_record.iso_now(),
    )
    merged["project_state"] = choose_preferred_scalar(merged.get("project_state"), source.get("project_state"))
    merged["brand_did"] = choose_preferred_scalar(merged.get("brand_did"), source.get("brand_did"))
    merged["ownership_signal"] = choose_preferred_scalar(
        "" if str(merged.get("ownership_signal") or "unknown") == "unknown" else merged.get("ownership_signal"),
        "" if str(source.get("ownership_signal") or "unknown") == "unknown" else source.get("ownership_signal"),
    ) or "unknown"
    merged["tags"] = project_record.unique_list(list(merged.get("tags", [])) + list(source.get("tags", [])))
    merged["platforms"] = project_record.unique_list(list(merged.get("platforms", [])) + list(source.get("platforms", [])))

    merged["classification_reason"] = choose_preferred_scalar(merged.get("classification_reason"), source.get("classification_reason"))
    merged["exclude_reason"] = choose_preferred_scalar(merged.get("exclude_reason"), source.get("exclude_reason"))
    merged["watch_reason"] = choose_preferred_scalar(merged.get("watch_reason"), source.get("watch_reason"))
    if int(source.get("technical_interest_score", 0) or 0) > int(merged.get("technical_interest_score", 0) or 0):
        merged["technical_interest_level"] = source.get("technical_interest_level", "none")
        merged["technical_interest_summary"] = source.get("technical_interest_summary", "")
    merged["technical_interest_reasons"] = project_record.unique_list(
        list(merged.get("technical_interest_reasons", [])) + list(source.get("technical_interest_reasons", []))
    )
    project_record.apply_technical_interest_state(merged)
    merged["confidence"] = max(float(merged.get("confidence") or 0.0), float(source.get("confidence") or 0.0))
    merged["llm_source"] = dict(merged.get("llm_source") or source.get("llm_source") or {})
    merged["llm_output"] = dict(merged.get("llm_output") or source.get("llm_output") or {})

    merged["first_seen_at"] = merge_timestamp(str(merged.get("first_seen_at") or ""), str(source.get("first_seen_at") or ""), prefer="min")
    merged["last_seen_at"] = merge_timestamp(str(merged.get("last_seen_at") or ""), str(source.get("last_seen_at") or ""), prefer="max")
    merged["last_enriched_at"] = merge_timestamp(str(merged.get("last_enriched_at") or ""), str(source.get("last_enriched_at") or ""), prefer="max")
    merged["last_llm_review_at"] = merge_timestamp(str(merged.get("last_llm_review_at") or ""), str(source.get("last_llm_review_at") or ""), prefer="max")

    source_key = str(source.get("project_key") or "")
    merged["notes"] = "\n".join(project_record.unique_list([item for item in [str(merged.get("notes") or "").strip(), str(source.get("notes") or "").strip()] if item])).strip()
    merged["system_notes"] = project_record.unique_list(
        list(merged.get("system_notes", []))
        + list(source.get("system_notes", []))
        + [f"merged from {source_key} at {project_record.iso_now()}"]
    )
    merged["last_run_errors"] = []
    merged["duplicate_candidates"] = []
    merged["merge_recommendation"] = {}
    confirmed_nsids = set(merged.get("resolved_nsids", []))
    merged["input_nsids"] = [value for value in merged.get("input_nsids", []) if value not in confirmed_nsids]
    runtime_view.merge_identifier_rows(merged, target, source)
    return merged


def project_split_identifiers_by_authority_domain(project: dict[str, Any], authority_domain: str) -> dict[str, list[str]]:
    normalized_domain = str(authority_domain or "").strip().lower().rstrip(".")
    group: dict[str, list[str]] = {"service_urls": [], "nsids": [], "authority_domains": []}
    if not normalized_domain:
        return group

    service_urls = project_record.unique_list(
        list(project.get("service_urls", []))
        + project_record.manual_seed_values(project, "service_url")
    )
    for service_url in service_urls:
        normalized_url = identifier_core.normalize_url(str(service_url or ""))
        if urllib.parse.urlparse(normalized_url).netloc.lower() == normalized_domain:
            group["service_urls"] = project_record.add_unique(group["service_urls"], normalized_url)

    nsids = project_record.unique_list(
        list(project.get("input_nsids", []))
        + list(project.get("resolved_nsids", []))
        + project_record.manual_seed_values(project, "nsid")
    )
    for nsid in nsids:
        normalized_nsid = identifier_core.normalize_nsid(str(nsid or ""))
        if identifier_core.derive_authority_domain(normalized_nsid) == normalized_domain:
            group["nsids"] = project_record.add_unique(group["nsids"], normalized_nsid)

    authority_domains = project_record.unique_list(list(project.get("authority_domains", [])) + project_record.manual_seed_values(project, "domain"))
    for domain in authority_domains:
        normalized = str(domain or "").strip().lower().rstrip(".")
        if normalized == normalized_domain:
            group["authority_domains"] = project_record.add_unique(group["authority_domains"], normalized)
    return group
