from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

from . import service, naming, project_record, developer_links, tangled
from . import oauth_metadata
from .identity import identifiers
from .identity import url_alias
from .repositories import repo_urls
from . import trust_graph
from .evidence import EvidenceBundle
from .relations import normalize_relation_records


@dataclass(frozen=True)
class CandidateIntegrationDependencies:
    add_candidate_project_repository_url: Callable[..., None]
    add_candidate_url: Callable[[dict[str, Any], str, str], None]


@dataclass(frozen=True)
class GitHubRepoIntegrationDependencies:
    confirm_project_repository_url: Callable[..., None]
    add_candidate_url: Callable[[dict[str, Any], str, str], None]
    project_owned_lexicon_nsid: Callable[[dict[str, Any], str], bool]
    add_authority_service_candidates: Callable[..., None]

    repo_url_allowed_for_project: Callable[[dict[str, Any], str], bool]
    add_candidate_project_repository_url: Callable[..., None]
    repo_context_service_candidate_allowed: Callable[..., bool]


@dataclass(frozen=True)
class ServiceIntegrationDependencies:
    apply_probe: Callable[[dict[str, Any], str, dict[str, Any]], None]
    add_candidate_project_repository_url: Callable[..., None]


def apply_developer_page_observations(project: dict[str, Any], url: str, value: dict[str, Any], *, supports_project: bool) -> None:
    if value.get("fetch_error") or not 0 < int(value.get("status_code") or 0) < 400:
        return
    snapshot = value.get("developer_account_observations")
    # Direct probes have full HTML. Legacy saved evidence only has a bounded
    # extract, whose absence of a credit cannot invalidate an earlier witness.
    if snapshot is None and "html" in value and "html_extract" not in value:
        snapshot = service.collect_developer_account_observations(url, str(value.get("html") or ""))
    if not isinstance(snapshot, dict) or snapshot.get("policy_version") != developer_links.POLICY_VERSION:
        return
    page = url_alias.canonical_alias_url(project, url)
    if snapshot.get("complete") is True:
        retained = []
        for account in project.get("candidate_developer_accounts", []):
            prefix, separator, source_url = str(account.get("source") or "").partition(":")
            if separator and prefix in {"developer_page", "service_raw_url", "service_page", "service_follow_up"}:
                if url_alias.canonical_alias_url(project, source_url) == page:
                    continue
            retained.append(account)
        project["candidate_developer_accounts"] = retained
    if not supports_project:
        return
    for entry in snapshot.get("accounts", []):
        if not isinstance(entry, dict) or not entry.get("observations"):
            continue
        kind, account_value = str(entry.get("kind") or ""), str(entry.get("value") or "")
        normalized = identifiers.normalize_atproto_account_identifier(account_value)
        if not normalized or normalized[0] != kind:
            continue
        if any(project_record.identifier_is_blocked(project, target_kind, account_value)
               or project_record.identifier_is_deferred(project, target_kind, account_value) for target_kind in (kind, "account")):
            continue
        project_record.add_candidate_developer_account(project,
            normalized[1] if kind == "handle" else "", f"developer_page:{identifiers.normalize_url(url)}",
            did=normalized[1] if kind == "did" else "")


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


def apply_collector_snapshot_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    *,
    apply_lexicon_result: Callable[[dict[str, Any], str, dict[str, Any]], None],
) -> None:
    for record in bundle.records:
        if record.kind == "lexicon_resolution_snapshot" and isinstance(record.value, dict):
            apply_lexicon_result(
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
                    project[key] = (
                        project_record.normalize_resource_url_records(list(value or []))
                        if key == "resource_url_records" and isinstance(value, list)
                        else value
                    )
    for failure in bundle.failures:
        note_failure(project, failure.message)


def apply_tangled_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    dependencies: CandidateIntegrationDependencies,
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
        repo_url = str(value.get("repo_url") or "")
        if did and repo_url:
            project_record.record_trust_edge(
                project,
                "did",
                did,
                "did_links_repo",
                "repo_url",
                repo_url,
                source=record.source,
                locator=repo_url,
                method="tangled_pds_record",
                classification="confirmed",
                eligible_for_auto_confirm=False,
                observed_at=record.observed_at,
                context={"owner_handle": handle, "policy_version": tangled.REPOSITORY_POLICY_VERSION},
            )
        reason = tangled.repository_match_reason(project, repo_url, tangled_record)
        if reason:
            dependencies.add_candidate_project_repository_url(
                project, repo_url, record.source,
                reason=f"Tangled repository matches project: {reason}",
                evidence_kind="tangled_pds_record", evidence_url=repo_url,
                repository_metadata=tangled_record,
            )
        if reason != "accepted_repository":
            continue
        if handle or did:
            project_record.add_developer_account(project, handle, record.source, did=did)
        metadata_source = f"{record.source}:{repo_urls.canonical_repo_url(repo_url)}"
        if repo_name:
            naming.add_name_candidate(project, repo_name, metadata_source)
        if description:
            project_record.add_candidate_text(project, "summary", description, metadata_source)
            project_record.add_candidate_text(project, "tagline", description, metadata_source)
        if website:
            normalized_website = project_record.normalize_service_candidate_url(website)
            if normalized_website:
                dependencies.add_candidate_url(project, "service_url", normalized_website)
    for failure in bundle.failures:
        note_failure(project, failure.message)


def _apply_tangled_repo_inspection(
    project: dict[str, Any],
    snapshot: dict[str, Any],
    record: Any,
    dependencies: CandidateIntegrationDependencies,
) -> None:
    repo_url = str(snapshot.get("repo_url") or "").rstrip("/")
    if tangled.repository_match_reason(project, repo_url, {}) != "accepted_repository":
        return
    repo = dict(snapshot.get("repo") or {})
    full_name = str(repo.get("full_name") or "")
    repo_name = str(repo.get("name") or "")
    description = str(repo.get("description") or "").strip()
    readme_excerpt = str(snapshot.get("readme_excerpt") or "").strip()
    source_label = full_name or repo_url
    if repo_name:
        naming.add_name_candidate(project, repo_name, f"tangled_repo:{source_label}")
    if description:
        project_record.add_candidate_text(project, "summary", description, f"tangled_repo:{source_label}")
        project_record.add_candidate_text(project, "tagline", description, f"tangled_repo:{source_label}")
    if readme_excerpt:
        project_record.add_candidate_text(project, "summary", readme_excerpt, f"tangled_readme:{source_label}")
    for url_value in snapshot.get("readme_urls", []):
        url = str(url_value or "")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc in {"github.com", "tangled.org", "tangled.sh"}:
            continue
        normalized_service = project_record.normalize_service_candidate_url(url)
        if not normalized_service:
            continue
        if repo_url:
            project_record.record_trust_edge(
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
                    or project_record.identifier_is_blocked(project, "service_url", str(relation.get("object", {}).get("value") or ""))
                    or project_record.identifier_is_deferred(project, "service_url", str(relation.get("object", {}).get("value") or ""))
                )
            ):
                continue
            if (
                relation.get("predicate") == "project_repository"
                and relation.get("subject", {}).get("type") in {"service", "website"}
                and relation.get("object", {}).get("type") == "repo"
                and record.classification == "confirmed"
                and relation.get("method") == "bounded_first_party_link"
                and bool(record.context.get("subject_is_exact_component"))
            ):
                dependencies.add_candidate_project_repository_url(
                    project,
                    str(relation.get("object", {}).get("value") or ""),
                    record.source,
                    reason="exact project component directly identifies an official project repository",
                    evidence_kind="bounded_first_party_link",
                    evidence_url=str(relation.get("locator") or ""),
                    relation="project_repository_relation",
                    updated_at=record.observed_at,
                    admitted_by_relation=True,
                )
            project["relation_evidence"] = normalize_relation_records(
                list(project.get("relation_evidence", []))
                + [{"relation": relation, "source": record.source,
                    "classification": record.classification, "observed_at": record.observed_at,
                    "context": dict(record.context)}]
            )
        elif record.kind == "service_verification" and isinstance(record.value, dict):
            value = service.normalize_redirect_same_site_policy(dict(record.value))
            value["observed_at"] = record.observed_at
            previous = next((item for item in project.get("service_verification_results", [])
                             if service.service_verification_result_key(item) == service.service_verification_result_key(value)), {})
            old_time, new_time = service.observation_time(previous), service.observation_time(value)
            if old_time and (not new_time or new_time < old_time):
                continue
            requested_url = identifiers.normalize_url(str(value.get("requested_url") or ""))
            if requested_url and value.get("lane") in {"static", "deep", "follow_up", "repository_homepage", "authority"} and 0 < int(value.get("status_code") or 0) < 400 and not value.get("fetch_error"):
                page_source = f"service_page:{requested_url}"
                replaced_predicates = {"official_account"}
                if value.get("lane") != "authority":
                    replaced_predicates.add("project_repository")
                project["relation_evidence"] = normalize_relation_records([
                    item
                    for item in project.get("relation_evidence", [])
                    if not (
                        isinstance(item, dict)
                        and str(item.get("source") or "").rstrip("/") == page_source
                        and str((item.get("relation") or {}).get("predicate") or "")
                        in replaced_predicates
                    )
                ])
            project["service_verification_results"] = service.merge_service_verification_results(
                project.get("service_verification_results", []), value
            )
            if value.get("lane") in {"static", "deep", "follow_up"}:
                dependencies.apply_probe(project, str(value.get("requested_url") or ""), value)
            elif value.get("lane") == "browser":
                apply_saved_service_probe(project, service.browser_service_observation(value))
    for failure in bundle.failures:
        note_failure(project, failure.message)


def apply_investigation_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    dependencies: CandidateIntegrationDependencies,
) -> None:
    if str(project.get("project_key") or "") != bundle.project_key:
        raise ValueError("evidence project_key does not match target project")

    for record in bundle.records:
        if record.kind == "oauth_client_metadata" and isinstance(record.value, dict):
            project["oauth_client_metadata"] = oauth_metadata.normalize_results(
                list(project.get("oauth_client_metadata", [])) + [record.value]
            )
            oauth_metadata.reconcile_relations(project)
            continue
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
            add_investigation_query(project, str(record.value or ""))
            continue
        if record.kind == "investigation_log" and isinstance(record.value, dict):
            payload = dict(record.value)
            kind = str(payload.pop("kind", ""))
            if kind in {"bsky_actor_search", "bsky_post_search", "bsky_author_feed"} and record.context.get("policy_version") == developer_links.POLICY_VERSION:
                target = str(payload.get("actor") if kind == "bsky_author_feed" else payload.get("query"))
                source = f"{kind}:{target}"
                project["candidate_developer_accounts"] = [item for item in project.get("candidate_developer_accounts", [])
                                                           if item.get("source") != source]
            add_investigation_log(project, kind, **payload)
            continue
        if record.kind == "github_repo_search_candidate" and isinstance(record.value, dict):
            repo_url = str(record.value.get("url") or "")
            score = int(record.context.get("score") or 0)
            query = str(record.context.get("query") or "")
            dependencies.add_candidate_project_repository_url(
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
            continue
        if record.kind == "developer_handle_candidate":
            if record.context.get("policy_version") == developer_links.POLICY_VERSION and record.context.get("reason") == "profile_developer_claim":
                project_record.add_candidate_developer_account(project, str(record.value or ""), record.source)
            else:
                project_record.record_account_reference(project, f"https://bsky.app/profile/{record.value}")
            continue
        if record.kind == "account_reference":
            project_record.record_account_reference(project, f"https://bsky.app/profile/{record.value}")
            continue
        if record.kind == "source_code_reference":
            url = str(record.value or "")
            normalized = repo_urls.canonical_repo_url(url) if repo_urls.is_supported_repo_url(url) else ""
            if normalized:
                dependencies.add_candidate_project_repository_url(
                    project,
                    normalized,
                    record.source,
                    reason="mentioned in Bluesky investigation result",
                    evidence_kind="bluesky_reference",
                    evidence_url=url,
                )

    # Legacy relation-only replay must not resurrect an older declaration when
    # a complete metadata snapshot is already available for the same source.
    oauth_metadata.reconcile_relations(project)
    for failure in bundle.failures:
        note_failure(project, failure.message)


def apply_github_repo_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    dependencies: GitHubRepoIntegrationDependencies,
    *,
    confirm_repo_snapshot: bool = True,
) -> None:
    if str(project.get("project_key") or "") != bundle.project_key:
        raise ValueError("evidence project_key does not match target project")

    inspected_repo_urls = {
        repo_urls.normalize_github_repo_url(str(record.value.get("repo_url") or ""))
        for record in bundle.records
        if record.kind == "github_repo_snapshot" and isinstance(record.value, dict)
    }
    inspected_repo_urls.discard(None)
    if inspected_repo_urls:
        retained_relations: list[dict[str, Any]] = []
        for item in normalize_relation_records(project.get("relation_evidence", [])):
            relation = item["relation"]
            subject = relation.get("subject") if isinstance(relation.get("subject"), dict) else {}
            is_inspected_repo = (
                subject.get("type") == "repo"
                and repo_urls.normalize_github_repo_url(str(subject.get("value") or ""))
                in inspected_repo_urls
            )
            if is_inspected_repo and relation.get("predicate") == "repository_homepage":
                continue
            retained_relations.append(item)
        project["relation_evidence"] = retained_relations
        trust_graph.remove_edges(
            project,
            predicate="repo_links_service",
            methods={"repository_homepage_pair"},
        )

    snapshot_lexicon_ids: dict[str, list[str]] = {}
    for record in bundle.records:
        if record.kind != "github_repo_snapshot" or not isinstance(record.value, dict):
            continue
        repo_url = repo_urls.normalize_github_repo_url(str(record.value.get("repo_url") or ""))
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
                relation.get("predicate") == "repository_homepage"
                and subject.get("type") == "repo"
                and obj.get("type") == "website"
            ):
                # Homepage is a page to inspect; its service role is not yet known.
                continue
            if str(record.source).startswith("github_readme:") and subject.get("type") == "repo":
                repo_url = repo_urls.normalize_github_repo_url(str(subject.get("value") or ""))
                object_value = str(obj.get("value") or "")
                if obj.get("type") == "repo":
                    normalized_repo = repo_urls.normalize_github_repo_url(object_value)
                    if (
                        normalized_repo
                        and normalized_repo != repo_url
                        and dependencies.repo_url_allowed_for_project(project, normalized_repo)
                    ):
                        dependencies.add_candidate_project_repository_url(
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
                    normalized_service = project_record.normalize_service_candidate_url(object_value)
                    if normalized_service and dependencies.repo_context_service_candidate_allowed(
                        project,
                        normalized_service,
                        repo_url=repo_url,
                        lexicon_ids=snapshot_lexicon_ids.get(repo_url, []),
                    ):
                        dependencies.add_candidate_url(project, "service_url", normalized_service)
    for record in bundle.records:
        if record.kind == "relation":
            continue
        if record.kind != "github_repo_snapshot" or not isinstance(record.value, dict):
            continue
        snapshot = record.value
        repo_url = str(snapshot.get("repo_url") or "")
        repo = dict(snapshot.get("repo") or {})
        description = str(repo.get("description") or "")
        full_name = str(repo.get("full_name") or "")
        repo_updated_at = str(repo.get("updated_at") or "")
        fork = bool(repo.get("fork"))
        fork_parent = repo.get("parent", {}) if isinstance(repo.get("parent", {}), dict) else {}
        fork_parent_url = repo_urls.normalize_github_repo_url(str(fork_parent.get("html_url") or ""))
        fork_parent_full_name = str(fork_parent.get("full_name") or "")

        if confirm_repo_snapshot:
            dependencies.confirm_project_repository_url(
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
            add_investigation_log(
                project,
                "github_repo_fork",
                repo_url=identifiers.normalize_url(repo_url),
                parent_url=fork_parent_url,
                parent_full_name=fork_parent_full_name,
            )
        project["evidence_urls"] = _add_unique(
            list(project.get("evidence_urls", [])),
            identifiers.normalize_url(repo_url),
        )
        if full_name:
            naming.add_name_candidate(project, full_name.split("/")[-1], f"github_repo_name:{full_name}")
        source_label = full_name or identifiers.normalize_url(repo_url)
        project_record.add_candidate_text(project, "summary", description, f"github:{source_label}")
        project_record.add_candidate_text(project, "tagline", description, f"github:{source_label}")

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
                add_investigation_log(
                    project,
                    "github_repo_lexicon_reference",
                    repo_url=identifiers.normalize_url(repo_url),
                    nsid=nsid,
                    path=str(item.get("path") or ""),
                )
                continue
            project["input_nsids"] = _add_unique(list(project.get("input_nsids", [])), nsid)
            authority_domain = identifiers.derive_authority_domain(nsid)
            if authority_domain:
                project["authority_domains"] = _add_unique(
                    list(project.get("authority_domains", [])),
                    authority_domain,
                )
                dependencies.add_authority_service_candidates(project, authority_domain)
            schema = item.get("schema")
            if isinstance(schema, dict):
                upsert_lexicon_schema_sample(project, nsid, schema)
            add_investigation_log(
                project,
                "github_repo_lexicon_hint",
                repo_url=identifiers.normalize_url(repo_url),
                nsid=nsid,
                path=str(item.get("path") or ""),
            )

        for url_value in snapshot.get("readme_urls", []):
            url = str(url_value or "")
            parsed = urllib.parse.urlparse(url)
            if parsed.netloc == "github.com":
                normalized_repo = repo_urls.normalize_github_repo_url(url)
                if not normalized_repo:
                    continue
                if normalized_repo == repo_urls.normalize_github_repo_url(repo_url):
                    dependencies.confirm_project_repository_url(
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
                    dependencies.add_candidate_project_repository_url(
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
                normalized_service = project_record.normalize_service_candidate_url(url)
                if normalized_service and dependencies.repo_context_service_candidate_allowed(
                    project,
                    normalized_service,
                    repo_url=repo_url,
                    lexicon_ids=repo_lexicon_ids,
                ):
                    project_record.record_trust_edge(
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


def _add_unique(values: list[str], value: str) -> list[str]:
    if value and value not in values:
        values.append(value)
    return values


def _accepted_github_repo_urls(
    project: dict[str, Any],
) -> set[str]:
    urls: set[str] = set()
    for value in (
        [str(project.get("project_repository_url") or ""), str(project.get("seed_value") or "")]
        + [str(item or "") for item in project.get("project_repository_urls", [])]
    ):
        normalized = repo_urls.normalize_github_repo_url(value)
        if normalized:
            urls.add(normalized)
    for seed in project.get("manual_seeds", []):
        if not isinstance(seed, dict) or str(seed.get("type") or "") != "repo_url":
            continue
        normalized = repo_urls.normalize_github_repo_url(str(seed.get("value") or ""))
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
    repo_url = repo_urls.normalize_github_repo_url(str(subject.get("value") or ""))
    nsid = str(obj.get("value") or "").strip()
    repo_accepted = bool(repo_url and repo_url in _accepted_github_repo_urls(project))
    nsid_accepted = bool(nsid and dependencies.project_owned_lexicon_nsid(project, nsid))
    if repo_accepted and nsid_accepted:
        return relation, "confirmed"

    downgraded = dict(relation)
    downgraded["predicate"] = "mentions_collection"
    return downgraded, "candidate"


def add_investigation_query(project: dict[str, Any], query: str) -> None:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return
    project["investigation_queries"] = project_record.add_unique(project.get("investigation_queries", []), normalized_query)


def add_investigation_log(project: dict[str, Any], kind: str, **payload: Any) -> None:
    project.setdefault("investigation_log", [])
    entry = {"kind": kind, **payload}
    project["investigation_log"].append(entry)


def note_failure(project: dict[str, Any], message: str) -> None:
    errors = project.setdefault("_run_errors", [])
    if message not in errors:
        errors.append(message)


def reset_investigation_state(project: dict[str, Any]) -> None:
    project["investigation_queries"] = []
    project["investigation_log"] = []


def project_explicitly_targets_pdsls(project: dict[str, Any]) -> bool:
    tokens = project_record.project_trusted_identity_tokens(project)
    return "pdsls" in tokens


def generic_platform_handles_to_ignore(project: dict[str, Any]) -> set[str]:
    ignored: set[str] = set()
    candidate_urls = [
        str(project.get("seed_value") or ""),
        *[str(item.get("value") or "") for item in project.get("manual_seeds", []) if isinstance(item, dict)],
        *[str(item or "") for item in project.get("project_repository_urls", [])],
        *[str(item or "") for item in project.get("candidate_project_repository_urls", [])],
        *[str(item or "") for item in project.get("service_urls", [])],
        *[str(item or "") for item in project_record.service_url_candidate_values(project)],
    ]
    for raw_url in candidate_urls:
        normalized = identifiers.normalize_url(raw_url)
        if not normalized:
            continue
        parsed = urllib.parse.urlparse(normalized)
        host = parsed.netloc.lower()
        segments = [segment for segment in parsed.path.split("/") if segment]
        if host in naming.GENERIC_PLATFORM_HOSTS and segments:
            ignored.add(host)
    return ignored


def project_identity_strings(project: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for value in project.get("authority_domains", []):
        lowered = str(value or "").strip().lower()
        if lowered and not project_record.is_shared_official_identity_host(lowered):
            values.add(lowered)
    for value in project_record.unique_list(list(project.get("input_nsids", [])) + list(project.get("resolved_nsids", []))):
        if value:
            values.add(str(value).lower())
    for value in project.get("service_urls", []):
        host = urllib.parse.urlparse(identifiers.normalize_url(value)).netloc
        if host:
            values.add(host.lower())
    if project.get("website_url"):
        host = urllib.parse.urlparse(identifiers.normalize_url(project["website_url"])).netloc
        if host:
            values.add(host.lower())
    for seed in project.get("manual_seeds", []):
        raw = str(seed.get("value") or "").strip().lower()
        if raw:
            values.add(raw)
            if raw.startswith("http"):
                host = urllib.parse.urlparse(identifiers.normalize_url(raw)).netloc
                if host:
                    values.add(host.lower())
    if project.get("name"):
        name = str(project["name"]).strip().lower()
        if name and ("." in name or "-" in name or "_" in name or len(name) >= 8):
            values.add(name)
    return {value for value in values if value}


def handle_matches_project(project: dict[str, Any], handle: str) -> bool:
    lowered = str(handle or "").strip().lower()
    if not lowered:
        return False
    identities = project_identity_strings(project)
    if lowered in identities:
        return True
    host = lowered
    if "." in host:
        if host in identities:
            return True
    return False


def keep_bluesky_account(project: dict[str, Any], handle: str, source: str = "") -> bool:
    normalized_handle = str(handle or "").strip().lower()
    if not normalized_handle:
        return False
    if handle_matches_project(project, normalized_handle):
        return True
    if str(source or "").startswith(("service_raw_url:", "service_page:", "service_follow_up:")):
        return False
    return True


def normalize_project_accounts(project: dict[str, Any]) -> None:
    project["developer_accounts"] = _retained_developer_accounts(project, project.get("developer_accounts", []))
    project["candidate_developer_accounts"] = [
        dict(item)
        for item in project_record.normalize_developer_accounts(project.get("candidate_developer_accounts", []))
        if str(item.get("handle") or "").strip().lower() not in generic_platform_handles_to_ignore(project)
        and not any(
            item.get("did") == existing.get("did") if item.get("did") else item.get("handle") == existing.get("handle")
            for existing in project.get("developer_accounts", []) if isinstance(existing, dict)
        )
    ]
    surviving_handles = {
        str(item.get("handle") or "").strip()
        for item in project.get("developer_accounts", [])
        if isinstance(item, dict) and str(item.get("handle") or "").strip()
    }
    project["bluesky_handles"] = project_record.unique_list(
        [
            handle
            for handle in project_record.unique_list(project.get("bluesky_handles", []))
            if str(handle or "").strip() in surviving_handles or handle_matches_project(project, str(handle or ""))
        ]
        + sorted(surviving_handles)
    )


def normalize_evidence_accounts(project: dict[str, Any], evidence: dict[str, Any]) -> None:
    evidence["developer_accounts"] = _retained_developer_accounts(project, evidence.get("developer_accounts", []))
    evidence["candidate_developer_accounts"] = [
        dict(item)
        for item in project_record.normalize_developer_accounts(evidence.get("candidate_developer_accounts", []))
        if str(item.get("handle") or "").strip().lower() not in generic_platform_handles_to_ignore(project)
    ]
    surviving_evidence_handles = {
        str(item.get("handle") or "").strip()
        for item in evidence.get("developer_accounts", [])
        if isinstance(item, dict) and str(item.get("handle") or "").strip()
    }
    evidence["bluesky_handles"] = [
        handle
        for handle in evidence.get("bluesky_handles", [])
        if str(handle or "").strip() in surviving_evidence_handles or handle_matches_project(project, str(handle or ""))
    ]
    ignored_handles = generic_platform_handles_to_ignore(project)
    if ignored_handles:
        evidence["bluesky_handles"] = [
            handle
            for handle in evidence.get("bluesky_handles", [])
            if str(handle or "").strip().lower() not in ignored_handles
        ]
        evidence["developer_accounts"] = [
            dict(item)
            for item in evidence.get("developer_accounts", [])
            if str(item.get("handle") or "").strip().lower() not in ignored_handles
        ]
        evidence["candidate_developer_accounts"] = [
            dict(item)
            for item in evidence.get("candidate_developer_accounts", [])
            if str(item.get("handle") or "").strip().lower() not in ignored_handles
        ]


def _retained_developer_accounts(project: dict[str, Any], accounts: list[Any]) -> list[dict[str, str]]:
    return [
        dict(item)
        for item in project_record.normalize_developer_accounts(accounts)
        if keep_bluesky_account(project, str(item.get("handle") or ""), str(item.get("source") or ""))
        or not str(item.get("handle") or "").strip()
    ]


def compact_json_value(value: Any, *, depth: int = 0, max_depth: int = 3, max_items: int = 8, max_string: int = 240) -> Any:
    if depth >= max_depth:
        if isinstance(value, dict):
            return f"... {len(value)} keys"
        if isinstance(value, list):
            return f"... {len(value)} items"
        if isinstance(value, str):
            return value[:max_string]
        return value
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                output["..."] = f"{len(value) - max_items} more keys"
                break
            output[str(key)] = compact_json_value(item, depth=depth + 1, max_depth=max_depth, max_items=max_items, max_string=max_string)
        return output
    if isinstance(value, list):
        items = [
            compact_json_value(item, depth=depth + 1, max_depth=max_depth, max_items=max_items, max_string=max_string)
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            items.append(f"... {len(value) - max_items} more items")
        return items
    if isinstance(value, str):
        return value[:max_string]
    return value


def summarize_lexicon_schema(nsid: str, schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("defs", {}) if isinstance(schema.get("defs"), dict) else {}
    return {
        "nsid": identifiers.normalize_nsid(nsid),
        "id": str(schema.get("id") or identifiers.normalize_nsid(nsid)),
        "lexicon": schema.get("lexicon"),
        "description": str(schema.get("description") or ""),
        "defs": compact_json_value(defs, max_depth=3, max_items=6, max_string=200),
    }


def upsert_lexicon_schema_sample(project: dict[str, Any], nsid: str, schema: dict[str, Any]) -> None:
    sample = summarize_lexicon_schema(nsid, schema)
    existing = [dict(item) for item in project.get("lexicon_schema_samples", []) if isinstance(item, dict)]
    replaced = False
    for index, entry in enumerate(existing):
        if identifiers.normalize_nsid(str(entry.get("nsid") or "")) == sample["nsid"]:
            existing[index] = sample
            replaced = True
            break
    if not replaced:
        existing.append(sample)
    project["lexicon_schema_samples"] = existing[:12]
