from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

from . import service
from .evidence import EvidenceBundle
from .relations import normalize_relation_records

@dataclass(frozen=True)
class IntegrationDependencies:
    add_investigation_query: Callable[[dict[str, Any], str], None]
    add_investigation_log: Callable[..., None]
    add_candidate_source_code_url: Callable[..., None]
    add_candidate_url: Callable[[dict[str, Any], str, str], None]
    normalize_github_repo_url: Callable[[str], str | None]
    normalize_tangled_repo_url: Callable[[str], str | None]
    canonical_repo_url: Callable[[str], str]
    is_supported_repo_url: Callable[[str], bool]
    record_developer_handle: Callable[[dict[str, Any], str, str], None]
    note_failure: Callable[[dict[str, Any], str], None]


@dataclass(frozen=True)
class GitHubRepoIntegrationDependencies:
    normalize_url: Callable[[str], str]
    normalize_github_repo_url: Callable[[str], str | None]
    normalize_service_candidate_url: Callable[[str], str]
    confirm_source_code_url: Callable[..., None]
    add_investigation_log: Callable[..., None]
    add_name_candidate: Callable[[dict[str, Any], str, str], None]
    add_candidate_text: Callable[[dict[str, Any], str, str, str], None]
    add_candidate_url: Callable[[dict[str, Any], str, str], None]
    project_owned_lexicon_nsid: Callable[[dict[str, Any], str], bool]
    derive_authority_domain: Callable[[str], str]
    add_authority_service_candidates: Callable[..., None]
    upsert_lexicon_schema_sample: Callable[[dict[str, Any], str, dict[str, Any]], None]
    repo_url_allowed_for_project: Callable[[dict[str, Any], str], bool]
    add_candidate_source_code_url: Callable[..., None]
    repo_context_service_candidate_allowed: Callable[..., bool]
    record_trust_edge: Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ServiceIntegrationDependencies:
    apply_probe: Callable[[dict[str, Any], str, dict[str, Any]], None]
    service_url_is_guarded: Callable[[dict[str, Any], str], bool]
    note_failure: Callable[[dict[str, Any], str], None]


def apply_saved_service_probe(project: dict[str, Any], value: dict[str, Any]) -> None:
    """Apply only facts stored in an evidence record; never perform follow-up I/O."""
    status = str(value.get("service_status") or "")
    confidence = str(value.get("service_status_confidence") or "")
    if status:
        current = str(project.get("service_status") or "unknown")
        rank = {
            "unknown": 0, "unverified": 1, "none": 2, "dead": 3, "live": 4,
            "private": 5, "unreleased": 6, "beta": 7, "invite_only": 8,
        }
        project["service_status"] = status if rank.get(status, 0) >= rank.get(current, 0) else current
    if confidence:
        current = str(project.get("service_status_confidence") or "none")
        rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
        project["service_status_confidence"] = confidence if rank.get(confidence, 0) >= rank.get(current, 0) else current
    project["service_status_evidence"] = list(dict.fromkeys(
        list(project.get("service_status_evidence", []))
        + [str(item) for item in value.get("service_status_evidence", []) if str(item)]
    ))


@dataclass(frozen=True)
class TangledIntegrationDependencies:
    confirm_source_code_url: Callable[..., None]
    add_candidate_source_code_url: Callable[..., None]
    add_developer_account: Callable[[dict[str, Any], str, str, str], None]
    add_name_candidate: Callable[[dict[str, Any], str, str], None]
    add_candidate_text: Callable[[dict[str, Any], str, str, str], None]
    add_candidate_url: Callable[[dict[str, Any], str, str], None]
    normalize_service_candidate_url: Callable[[str], str]
    record_trust_edge: Callable[..., dict[str, Any]]
    note_failure: Callable[[dict[str, Any], str], None]


@dataclass(frozen=True)
class CollectorIntegrationDependencies:
    apply_lexicon_result: Callable[[dict[str, Any], str, dict[str, Any]], None]
    note_failure: Callable[[dict[str, Any], str], None]


def apply_collector_snapshot_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    dependencies: CollectorIntegrationDependencies,
) -> None:
    for record in bundle.records:
        if record.kind == "lexicon_resolution_snapshot" and isinstance(record.value, dict):
            dependencies.apply_lexicon_result(
                project, str(record.value.get("nsid") or ""), dict(record.value.get("result") or {})
            )
        elif record.kind == "record_samples_snapshot" and isinstance(record.value, dict):
            project["recent_record_samples"] = [
                dict(item) for item in record.value.get("samples", []) if isinstance(item, dict)
            ]
        elif record.kind == "project_patch" and isinstance(record.value, dict):
            fields = dict(record.value.get("fields") or {})
            allowed = {str(item) for item in record.context.get("allowed_fields", [])}
            for key, value in fields.items():
                if key in allowed:
                    project[key] = value
    for failure in bundle.failures:
        dependencies.note_failure(project, failure.message)


def apply_tangled_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    dependencies: TangledIntegrationDependencies,
) -> None:
    for record in bundle.records:
        if record.kind == "relation" and isinstance(record.value, dict):
            project["relation_evidence"] = normalize_relation_records(
                list(project.get("relation_evidence", []))
                + [{"relation": dict(record.value), "source": record.source,
                    "classification": record.classification, "observed_at": record.observed_at,
                    "context": dict(record.context)}]
            )
            continue
        if record.kind == "tangled_repo_inspection" and isinstance(record.value, dict):
            _apply_tangled_repo_inspection(project, dict(record.value), record, dependencies)
            continue
        if record.kind != "tangled_repo_snapshot" or not isinstance(record.value, dict):
            continue
        value = dict(record.value)
        handle = str(value.get("owner_handle") or "")
        did = str(value.get("owner_did") or "")
        tangled_record = dict(value.get("record") or {})
        repo_name = str(tangled_record.get("name") or "").strip()
        description = str(tangled_record.get("description") or "").strip()
        website = str(tangled_record.get("website") or "").strip()
        if handle or did:
            dependencies.add_developer_account(project, handle, record.source, did)
        repo_url = str(value.get("repo_url") or "")
        score = int(value.get("score") or 0)
        if repo_name:
            dependencies.add_name_candidate(project, repo_name, record.source)
        if description:
            dependencies.add_candidate_text(project, "summary", description, record.source)
            dependencies.add_candidate_text(project, "tagline", description, record.source)
        if website:
            normalized_website = dependencies.normalize_service_candidate_url(website)
            if normalized_website:
                dependencies.add_candidate_url(project, "service_url", normalized_website)
                dependencies.add_candidate_url(project, "website_url", normalized_website)
        if did and repo_url:
            dependencies.record_trust_edge(
                project,
                "did",
                did,
                "did_links_repo",
                "repo_url",
                repo_url,
                source=record.source,
                locator=repo_url,
                method="tangled_pds_record",
                classification="confirmed" if score >= 5 else "candidate",
                eligible_for_auto_confirm=False,
                observed_at=record.observed_at,
                context={"owner_handle": handle, "score": score},
            )
        if score >= 5:
            dependencies.add_candidate_source_code_url(
                project, repo_url, record.source,
                reason=f"Tangled repo belongs to a confirmed developer account; relevance score={score}",
                evidence_kind="tangled_pds_record", evidence_url=repo_url,
                relation="tangled_account_repo",
            )
        elif score >= 2:
            dependencies.add_candidate_source_code_url(
                project, repo_url, record.source,
                reason=f"Tangled repo belongs to a confirmed developer account; relevance score={score}",
                evidence_kind="tangled_pds_record", evidence_url=repo_url,
            )
    for failure in bundle.failures:
        dependencies.note_failure(project, failure.message)


def _apply_tangled_repo_inspection(
    project: dict[str, Any],
    snapshot: dict[str, Any],
    record: Any,
    dependencies: TangledIntegrationDependencies,
) -> None:
    repo_url = str(snapshot.get("repo_url") or "").rstrip("/")
    repo = dict(snapshot.get("repo") or {})
    full_name = str(repo.get("full_name") or "")
    repo_name = str(repo.get("name") or "")
    description = str(repo.get("description") or "").strip()
    readme_excerpt = str(snapshot.get("readme_excerpt") or "").strip()
    source_label = full_name or repo_url
    if repo_name:
        dependencies.add_name_candidate(project, repo_name, f"tangled_repo:{source_label}")
    if description:
        dependencies.add_candidate_text(project, "summary", description, f"tangled_repo:{source_label}")
        dependencies.add_candidate_text(project, "tagline", description, f"tangled_repo:{source_label}")
    if readme_excerpt:
        dependencies.add_candidate_text(project, "summary", readme_excerpt, f"tangled_readme:{source_label}")
    for url_value in snapshot.get("readme_urls", []):
        url = str(url_value or "")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc in {"github.com", "tangled.org", "tangled.sh"}:
            continue
        normalized_service = dependencies.normalize_service_candidate_url(url)
        if not normalized_service:
            continue
        if repo_url:
            dependencies.record_trust_edge(
                project,
                "repo_url",
                repo_url,
                "repo_links_service",
                "service_url",
                normalized_service,
                source=f"tangled_readme:{source_label}",
                locator=repo_url,
                method="readme_link",
                classification="candidate",
                eligible_for_auto_confirm=False,
                observed_at=record.observed_at,
                context={"from_readme": True, "provider": "tangled"},
            )
        dependencies.add_candidate_url(project, "service_url", normalized_service)
        dependencies.add_candidate_url(project, "website_url", normalized_service)


def apply_service_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    dependencies: ServiceIntegrationDependencies,
) -> None:
    for record in bundle.records:
        if record.kind == "relation" and isinstance(record.value, dict):
            relation = dict(record.value)
            if (
                relation.get("predicate") == "operates_service"
                and relation.get("object", {}).get("type") == "service"
                and (
                    str(record.source).startswith("service_follow_up:")
                    or relation.get("method") == "follow_up_probe"
                    or dependencies.service_url_is_guarded(project, str(relation.get("object", {}).get("value") or ""))
                )
            ):
                continue
            project["relation_evidence"] = normalize_relation_records(
                list(project.get("relation_evidence", []))
                + [{"relation": relation, "source": record.source,
                    "classification": record.classification, "observed_at": record.observed_at,
                    "context": dict(record.context)}]
            )
        elif record.kind == "service_verification" and isinstance(record.value, dict):
            value = dict(record.value)
            project["service_verification_results"] = service.merge_service_verification_results(
                project.get("service_verification_results", []), value
            )
            if value.get("lane") in {"static", "deep", "follow_up"}:
                dependencies.apply_probe(project, str(value.get("requested_url") or ""), value)
            elif value.get("lane") == "browser":
                pages = list(value.get("pages") or [])
                if value.get("login_surface_reached"):
                    apply_saved_service_probe(project, {
                        "service_status": "live",
                        "service_status_confidence": "high",
                        "service_status_evidence": ["browser_login_surface_reached"],
                    })
                elif pages and not any(page.get("network_errors") for page in pages if isinstance(page, dict)):
                    project["service_status_evidence"] = list(dict.fromkeys(
                        list(project.get("service_status_evidence", [])) + ["browser_rendered_surface"]
                    ))
    for failure in bundle.failures:
        dependencies.note_failure(project, failure.message)


def apply_investigation_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    dependencies: IntegrationDependencies,
) -> None:
    if str(project.get("project_key") or "") != bundle.project_key:
        raise ValueError("evidence project_key does not match target project")

    for record in bundle.records:
        if record.kind == "relation" and isinstance(record.value, dict):
            relation = dict(record.value)
            if relation.get("predicate") == "operates_service" and str(record.source).startswith("service_"):
                continue
            project["relation_evidence"] = normalize_relation_records(
                list(project.get("relation_evidence", []))
                + [{
                    "relation": relation,
                    "source": record.source,
                    "classification": record.classification,
                    "observed_at": record.observed_at,
                    "context": dict(record.context),
                }]
            )
            continue
        if record.kind == "investigation_query":
            dependencies.add_investigation_query(project, str(record.value or ""))
            continue
        if record.kind == "investigation_log" and isinstance(record.value, dict):
            payload = dict(record.value)
            kind = str(payload.pop("kind", ""))
            dependencies.add_investigation_log(project, kind, **payload)
            continue
        if record.kind == "github_repo_search_candidate" and isinstance(record.value, dict):
            repo_url = str(record.value.get("url") or "")
            score = int(record.context.get("score") or 0)
            query = str(record.context.get("query") or "")
            dependencies.add_candidate_source_code_url(
                project,
                repo_url,
                record.source,
                reason=f"matched GitHub repository search query: {query} (score={score})",
                evidence_kind="github_search",
                evidence_url=repo_url,
            )
            homepage = str(record.value.get("homepage") or "")
            if homepage:
                dependencies.add_candidate_url(project, "service_url", homepage)
                dependencies.add_candidate_url(project, "website_url", homepage)
            continue
        if record.kind == "developer_handle_candidate":
            dependencies.record_developer_handle(project, str(record.value or ""), record.source)
            continue
        if record.kind == "source_code_reference":
            url = str(record.value or "")
            normalized = dependencies.canonical_repo_url(url) if dependencies.is_supported_repo_url(url) else ""
            if normalized:
                dependencies.add_candidate_source_code_url(
                    project,
                    normalized,
                    record.source,
                    reason="mentioned in Bluesky investigation result",
                    evidence_kind="bluesky_reference",
                    evidence_url=url,
                )

    for failure in bundle.failures:
        dependencies.note_failure(project, failure.message)


def apply_github_repo_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    dependencies: GitHubRepoIntegrationDependencies,
) -> None:
    if str(project.get("project_key") or "") != bundle.project_key:
        raise ValueError("evidence project_key does not match target project")

    snapshot_lexicon_ids: dict[str, list[str]] = {}
    for record in bundle.records:
        if record.kind != "github_repo_snapshot" or not isinstance(record.value, dict):
            continue
        repo_url = dependencies.normalize_github_repo_url(str(record.value.get("repo_url") or ""))
        if not repo_url:
            continue
        snapshot_lexicon_ids[repo_url] = [
            str(item.get("nsid") or "")
            for item in record.value.get("lexicon_hints", [])
            if isinstance(item, dict) and str(item.get("nsid") or "")
        ]

    for record in bundle.records:
        if record.kind == "relation" and isinstance(record.value, dict):
            classification = record.classification
            relation = dict(record.value)
            relation, classification = _normalize_github_repo_schema_relation(
                project,
                relation,
                classification,
                dependencies,
            )
            project["relation_evidence"] = normalize_relation_records(
                list(project.get("relation_evidence", []))
                + [{"relation": relation, "source": record.source,
                    "classification": classification, "observed_at": record.observed_at,
                    "context": dict(record.context)}]
            )
            subject = relation.get("subject") if isinstance(relation.get("subject"), dict) else {}
            obj = relation.get("object") if isinstance(relation.get("object"), dict) else {}
            if (
                relation.get("predicate") == "source_repository"
                and subject.get("type") == "repo"
                and obj.get("type") in {"website", "service"}
            ):
                method = str(relation.get("method") or "")
                dependencies.record_trust_edge(
                    project,
                    "repo_url",
                    str(subject.get("value") or ""),
                    "repo_links_service",
                    "service_url",
                    str(obj.get("value") or ""),
                    source=record.source,
                    locator=str(relation.get("locator") or ""),
                    method=method,
                    classification="confirmed" if method == "github_homepage" else classification,
                    eligible_for_auto_confirm=method == "github_homepage",
                    observed_at=record.observed_at,
                    context={"from_relation": True},
                )
            if str(record.source).startswith("github_readme:") and subject.get("type") == "repo":
                repo_url = dependencies.normalize_github_repo_url(str(subject.get("value") or ""))
                object_value = str(obj.get("value") or "")
                if obj.get("type") == "repo":
                    normalized_repo = dependencies.normalize_github_repo_url(object_value)
                    if (
                        normalized_repo
                        and normalized_repo != repo_url
                        and dependencies.repo_url_allowed_for_project(project, normalized_repo)
                    ):
                        dependencies.add_candidate_source_code_url(
                            project,
                            normalized_repo,
                            record.source,
                            reason="linked from repository README",
                            evidence_kind="readme_link",
                            evidence_url=repo_url or "",
                            relation="readme_link",
                            updated_at="",
                        )
                elif obj.get("type") in {"website", "service"} and repo_url:
                    normalized_service = dependencies.normalize_service_candidate_url(object_value)
                    if normalized_service and dependencies.repo_context_service_candidate_allowed(
                        project,
                        normalized_service,
                        repo_url=repo_url,
                        lexicon_ids=snapshot_lexicon_ids.get(repo_url, []),
                    ):
                        dependencies.add_candidate_url(project, "service_url", normalized_service)
                        dependencies.add_candidate_url(project, "website_url", normalized_service)
    for record in bundle.records:
        if record.kind == "relation":
            continue
        if record.kind != "github_repo_snapshot" or not isinstance(record.value, dict):
            continue
        snapshot = record.value
        repo_url = str(snapshot.get("repo_url") or "")
        repo = dict(snapshot.get("repo") or {})
        description = str(repo.get("description") or "")
        homepage = str(repo.get("homepage") or "")
        full_name = str(repo.get("full_name") or "")
        repo_updated_at = str(repo.get("updated_at") or "")
        fork = bool(repo.get("fork"))
        fork_parent = repo.get("parent", {}) if isinstance(repo.get("parent", {}), dict) else {}
        fork_parent_url = dependencies.normalize_github_repo_url(str(fork_parent.get("html_url") or ""))
        fork_parent_full_name = str(fork_parent.get("full_name") or "")

        dependencies.confirm_source_code_url(
            project,
            repo_url,
            "github_repo_seed",
            relation="fork_repo" if fork else "repo_seed",
            reason=(
                f"registered manually as repository seed; GitHub reports fork of {fork_parent_full_name or fork_parent_url}"
                if fork and (fork_parent_full_name or fork_parent_url)
                else "registered manually as repository seed"
            ),
            evidence_kind="manual_seed",
            evidence_url=repo_url,
            updated_at=repo_updated_at,
        )
        if fork:
            dependencies.add_investigation_log(
                project,
                "github_repo_fork",
                repo_url=dependencies.normalize_url(repo_url),
                parent_url=fork_parent_url,
                parent_full_name=fork_parent_full_name,
            )
        project["evidence_urls"] = _add_unique(
            list(project.get("evidence_urls", [])),
            dependencies.normalize_url(repo_url),
        )
        if full_name:
            dependencies.add_name_candidate(project, full_name.split("/")[-1], f"github_repo_name:{full_name}")
        source_label = full_name or dependencies.normalize_url(repo_url)
        dependencies.add_candidate_text(project, "summary", description, f"github:{source_label}")
        dependencies.add_candidate_text(project, "tagline", description, f"github:{source_label}")
        if homepage:
            normalized_homepage = dependencies.normalize_service_candidate_url(homepage)
            if normalized_homepage:
                dependencies.record_trust_edge(
                    project,
                    "repo_url",
                    repo_url,
                    "repo_links_service",
                    "service_url",
                    normalized_homepage,
                    source=record.source,
                    locator=repo_url,
                    method="github_homepage",
                    classification="confirmed",
                    eligible_for_auto_confirm=True,
                    observed_at=record.observed_at,
                    context={"from_snapshot": True},
                )
                dependencies.add_candidate_url(project, "service_url", normalized_homepage)
                dependencies.add_candidate_url(project, "website_url", normalized_homepage)

        lexicon_hints = [
            dict(item) for item in snapshot.get("lexicon_hints", [])
            if isinstance(item, dict)
        ]
        repo_lexicon_ids = [str(item.get("nsid") or "") for item in lexicon_hints if item.get("nsid")]
        for item in lexicon_hints:
            nsid = str(item.get("nsid") or "")
            if not nsid:
                continue
            if not dependencies.project_owned_lexicon_nsid(project, nsid):
                dependencies.add_investigation_log(
                    project,
                    "github_repo_lexicon_reference",
                    repo_url=dependencies.normalize_url(repo_url),
                    nsid=nsid,
                    path=str(item.get("path") or ""),
                )
                continue
            project["input_nsids"] = _add_unique(list(project.get("input_nsids", [])), nsid)
            authority_domain = dependencies.derive_authority_domain(nsid)
            if authority_domain:
                project["authority_domains"] = _add_unique(
                    list(project.get("authority_domains", [])),
                    authority_domain,
                )
                dependencies.add_authority_service_candidates(project, authority_domain)
            schema = item.get("schema")
            if isinstance(schema, dict):
                dependencies.upsert_lexicon_schema_sample(project, nsid, schema)
            dependencies.add_investigation_log(
                project,
                "github_repo_lexicon_hint",
                repo_url=dependencies.normalize_url(repo_url),
                nsid=nsid,
                path=str(item.get("path") or ""),
            )

        for url_value in snapshot.get("readme_urls", []):
            url = str(url_value or "")
            parsed = urllib.parse.urlparse(url)
            if parsed.netloc == "github.com":
                normalized_repo = dependencies.normalize_github_repo_url(url)
                if not normalized_repo:
                    continue
                if normalized_repo == dependencies.normalize_github_repo_url(repo_url):
                    dependencies.confirm_source_code_url(
                        project,
                        normalized_repo,
                        f"github_readme:{source_label}",
                        relation="readme_link",
                        reason="linked from repository README",
                        evidence_kind="readme_link",
                        evidence_url=repo_url,
                        updated_at=repo_updated_at,
                    )
                elif dependencies.repo_url_allowed_for_project(project, normalized_repo):
                    dependencies.add_candidate_source_code_url(
                        project,
                        normalized_repo,
                        f"github_readme:{source_label}",
                        reason="linked from repository README",
                        evidence_kind="readme_link",
                        evidence_url=repo_url,
                        relation="readme_link",
                        updated_at="",
                    )
            elif parsed.scheme in {"http", "https"}:
                normalized_service = dependencies.normalize_service_candidate_url(url)
                if normalized_service and dependencies.repo_context_service_candidate_allowed(
                    project,
                    normalized_service,
                    repo_url=repo_url,
                    lexicon_ids=repo_lexicon_ids,
                ):
                    dependencies.record_trust_edge(
                        project,
                        "repo_url",
                        repo_url,
                        "repo_links_service",
                        "service_url",
                        normalized_service,
                        source=f"github_readme:{source_label}",
                        locator=repo_url,
                        method="readme_link",
                        classification="candidate",
                        eligible_for_auto_confirm=False,
                        observed_at=record.observed_at,
                        context={"from_readme": True},
                    )
                    dependencies.add_candidate_url(project, "service_url", normalized_service)
                    dependencies.add_candidate_url(project, "website_url", normalized_service)


def _add_unique(values: list[str], value: str) -> list[str]:
    if value and value not in values:
        values.append(value)
    return values


def _accepted_github_repo_urls(
    project: dict[str, Any],
    normalize_github_repo_url: Callable[[str], str | None],
) -> set[str]:
    urls: set[str] = set()
    for value in (
        [str(project.get("source_code_url") or ""), str(project.get("seed_value") or "")]
        + [str(item or "") for item in project.get("source_code_urls", [])]
    ):
        normalized = normalize_github_repo_url(value)
        if normalized:
            urls.add(normalized)
    for seed in project.get("manual_seeds", []):
        if not isinstance(seed, dict) or str(seed.get("type") or "") != "repo_url":
            continue
        normalized = normalize_github_repo_url(str(seed.get("value") or ""))
        if normalized:
            urls.add(normalized)
    return urls


def _normalize_github_repo_schema_relation(
    project: dict[str, Any],
    relation: dict[str, Any],
    classification: str,
    dependencies: GitHubRepoIntegrationDependencies,
) -> tuple[dict[str, Any], str]:
    if relation.get("predicate") != "defines_schema":
        return relation, classification
    subject = relation.get("subject") or {}
    obj = relation.get("object") or {}
    if subject.get("type") != "repo" or obj.get("type") != "nsid":
        return relation, classification
    repo_url = dependencies.normalize_github_repo_url(str(subject.get("value") or ""))
    nsid = str(obj.get("value") or "").strip()
    repo_accepted = bool(repo_url and repo_url in _accepted_github_repo_urls(project, dependencies.normalize_github_repo_url))
    nsid_accepted = bool(nsid and dependencies.project_owned_lexicon_nsid(project, nsid))
    if repo_accepted and nsid_accepted:
        return relation, "confirmed"

    downgraded = dict(relation)
    downgraded["predicate"] = "mentions_collection"
    return downgraded, "candidate"
