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
