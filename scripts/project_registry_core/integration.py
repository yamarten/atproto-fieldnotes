from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

from .evidence import EvidenceBundle


@dataclass(frozen=True)
class IntegrationDependencies:
    add_investigation_query: Callable[[dict[str, Any], str], None]
    add_investigation_log: Callable[..., None]
    add_candidate_source_code_url: Callable[..., None]
    add_candidate_url: Callable[[dict[str, Any], str, str], None]
    normalize_github_repo_url: Callable[[str], str | None]
    normalize_tangled_repo_url: Callable[[str], str | None]
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


def apply_investigation_evidence(
    project: dict[str, Any],
    bundle: EvidenceBundle,
    dependencies: IntegrationDependencies,
) -> None:
    if str(project.get("project_key") or "") != bundle.project_key:
        raise ValueError("evidence project_key does not match target project")

    for record in bundle.records:
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
            parsed = urllib.parse.urlparse(url)
            normalized = None
            if parsed.netloc == "github.com":
                normalized = dependencies.normalize_github_repo_url(url)
            elif parsed.netloc == "tangled.org":
                normalized = dependencies.normalize_tangled_repo_url(url)
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

    for record in bundle.records:
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
                    dependencies.add_candidate_url(project, "service_url", normalized_service)
                    dependencies.add_candidate_url(project, "website_url", normalized_service)


def _add_unique(values: list[str], value: str) -> list[str]:
    if value and value not in values:
        values.append(value)
    return values
