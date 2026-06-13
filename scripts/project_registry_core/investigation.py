from __future__ import annotations

import re
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable

from .evidence import CollectorFailure, EvidenceBundle, EvidenceRecord


def unique_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def clean_string_list(values: Any, *, limit: int | None = None) -> list[str]:
    output = [str(item).strip() for item in values] if isinstance(values, list) else []
    output = unique_strings([item for item in output if item])
    return output[:limit] if limit is not None else output


def clean_search_query(value: str) -> str:
    query = str(value or "").strip()
    if not query:
        return ""
    query = re.sub(r"\bsite:[^\s]+", "", query)
    query = re.sub(r"\b(?:github|gitlab|bitbucket|bluesky)\b", "", query, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", query).strip()


def github_search_term(value: str) -> str:
    query = clean_search_query(value)
    if not query:
        return ""
    return f'"{query}"' if re.search(r"\s", query) else query


def build_github_repo_search_queries(plan: dict[str, Any]) -> list[str]:
    raw_queries = clean_string_list(plan.get("repo_search_queries"), limit=6)
    normalized: list[str] = []
    for raw in raw_queries:
        cleaned = clean_search_query(raw)
        if not cleaned:
            continue
        if re.search(r"\bOR\b", cleaned, flags=re.IGNORECASE):
            normalized.append(cleaned)
            continue
        term = github_search_term(cleaned)
        if term:
            normalized.append(term)
    unique_queries = unique_strings(normalized)
    if len(unique_queries) <= 1:
        return unique_queries

    bundled: list[str] = []
    for index in range(0, min(len(unique_queries), 6), 3):
        bucket = unique_queries[index:index + 3]
        if bucket:
            bundled.append(" OR ".join(bucket))
    return unique_strings(bundled)[:2]


def query_contains_kana(value: str) -> bool:
    return bool(re.search(r"[\u3040-\u309f\u30a0-\u30ff]", str(value or "")))


def bsky_search_term(value: str) -> str:
    query = clean_search_query(value)
    if not query:
        return ""
    if re.search(r"[\s@#:\$\|/]", query):
        escaped = query.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return query


def build_bsky_search_queries(plan: dict[str, Any], key: str, *, max_queries: int = 2) -> list[str]:
    raw_queries = unique_strings(
        [
            cleaned
            for cleaned in (
                clean_search_query(raw)
                for raw in clean_string_list(plan.get(key), limit=max_queries * 3)
            )
            if cleaned
        ]
    )
    if not raw_queries:
        return []

    grouped: dict[bool, list[str]] = {True: [], False: []}
    for query in raw_queries:
        grouped[query_contains_kana(query)].append(query)

    bundled: list[str] = []
    for has_kana in (False, True):
        bucket = unique_strings(grouped[has_kana])
        terms = [term for term in (bsky_search_term(item) for item in bucket) if term]
        if not terms:
            continue
        bundled.append(terms[0] if len(terms) == 1 else " | ".join(terms))
    return bundled[:max_queries]


def seed_investigation_queries(project: dict[str, Any]) -> dict[str, list[str]]:
    repo_queries: list[str] = []
    actor_queries: list[str] = []
    post_queries: list[str] = []
    focus_terms: list[str] = []

    if project.get("name"):
        repo_queries.append(str(project["name"]))
        actor_queries.append(str(project["name"]))
        post_queries.append(str(project["name"]))
        focus_terms.append(str(project["name"]))
    if project.get("primary_nsid"):
        repo_queries.append(str(project["primary_nsid"]))
        actor_queries.append(str(project["primary_nsid"]))
        post_queries.append(str(project["primary_nsid"]))
        focus_terms.append(str(project["primary_nsid"]))
    for domain in project.get("authority_domains", [])[:3]:
        repo_queries.append(str(domain))
        actor_queries.append(str(domain))
        post_queries.append(str(domain))
        focus_terms.append(str(domain))
    for handle in project.get("bluesky_handles", [])[:3]:
        actor_queries.append(str(handle))
        post_queries.append(str(handle))
        focus_terms.append(str(handle))

    return {
        "repo_search_queries": unique_strings(repo_queries)[:5],
        "actor_search_queries": unique_strings(actor_queries)[:5],
        "post_search_queries": unique_strings(post_queries)[:5],
        "focus_terms": unique_strings(focus_terms)[:8],
    }


@dataclass(frozen=True)
class PlanningDependencies:
    build_prompt: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    run_llm: Callable[..., tuple[dict[str, Any], dict[str, Any]]]
    note_failure: Callable[[dict[str, Any], str], None]


def plan_investigation_queries(
    project: dict[str, Any],
    dependencies: PlanningDependencies,
    *,
    llm_endpoint: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    llm_reasoning_effort: str | None = None,
    llm_api_style: str | None = None,
) -> dict[str, Any]:
    seed_plan = seed_investigation_queries(project)
    try:
        prompt, evidence = dependencies.build_prompt(project)
        payload, meta = dependencies.run_llm(
            prompt,
            llm_endpoint=llm_endpoint,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
            llm_reasoning_effort=llm_reasoning_effort,
            llm_api_style=llm_api_style,
        )
        return {
            "repo_search_queries": unique_strings(
                [clean_search_query(item) for item in clean_string_list(payload.get("repo_search_queries"), limit=6) if clean_search_query(item)]
            ) or seed_plan["repo_search_queries"],
            "actor_search_queries": unique_strings(
                [clean_search_query(item) for item in clean_string_list(payload.get("actor_search_queries"), limit=6) if clean_search_query(item)]
            ) or seed_plan["actor_search_queries"],
            "post_search_queries": unique_strings(
                [clean_search_query(item) for item in clean_string_list(payload.get("post_search_queries"), limit=6) if clean_search_query(item)]
            ) or seed_plan["post_search_queries"],
            "focus_terms": clean_string_list(payload.get("focus_terms"), limit=8) or seed_plan["focus_terms"],
            "reason": str(payload.get("reason") or "").strip(),
            "llm_source": {**meta, "evidence": evidence},
        }
    except Exception as exc:
        dependencies.note_failure(project, f"investigation planning failed: {exc}")
        return {**seed_plan, "reason": "fallback deterministic plan", "llm_source": {}}


@dataclass(frozen=True)
class SearchCollectorDependencies:
    now: Callable[[], str]
    search_github_repositories: Callable[..., list[dict[str, Any]]]
    github_repo_relevance: Callable[[dict[str, Any], dict[str, Any]], int]
    repo_candidate_allowed: Callable[[dict[str, Any], str], bool]
    search_bsky_actors: Callable[..., list[dict[str, Any]]]
    bsky_actor_relevance: Callable[[dict[str, Any], dict[str, Any]], int]
    search_bsky_posts: Callable[..., list[dict[str, Any]]]
    handle_matches_project: Callable[[dict[str, Any], str], bool]
    fetch_bsky_author_feed: Callable[..., list[dict[str, Any]]]
    timeout: float


def collector_failure(
    *,
    source: str,
    operation: str,
    target: str,
    message: str,
    error: BaseException,
) -> CollectorFailure:
    status_code = int(getattr(error, "code", 0) or 0)
    lowered = str(error).lower()
    if isinstance(error, TimeoutError) or "timed out" in lowered or "timeout" in lowered:
        category, retryable = "timeout", True
    elif status_code in {403, 429}:
        category, retryable = "rate_limit", True
    elif status_code >= 500:
        category, retryable = "remote_server_error", True
    elif isinstance(error, urllib.error.URLError):
        category, retryable = "network_error", True
    else:
        category, retryable = "collector_error", False
    return CollectorFailure(
        source=source,
        operation=operation,
        target=target,
        category=category,
        message=message,
        retryable=retryable,
    )


def evidence_record(
    *,
    kind: str,
    source: str,
    classification: str,
    observed_at: str,
    value: Any,
    context: dict[str, Any] | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        kind=kind,
        source=source,
        classification=classification,  # type: ignore[arg-type]
        observed_at=observed_at,
        value=value,
        context=context or {},
    )


def collect_investigation_evidence(
    project: dict[str, Any],
    plan: dict[str, Any],
    dependencies: SearchCollectorDependencies,
) -> EvidenceBundle:
    bundle = EvidenceBundle(
        project_key=str(project.get("project_key") or ""),
        collected_at=dependencies.now(),
    )

    for query in build_github_repo_search_queries(plan):
        observed_at = dependencies.now()
        bundle.add_record(evidence_record(
            kind="investigation_query",
            source="github_search",
            classification="derived",
            observed_at=observed_at,
            value=query,
        ))
        try:
            repos = dependencies.search_github_repositories(query, limit=5, timeout=dependencies.timeout)
            scored_repos = [
                (dependencies.github_repo_relevance(project, repo), repo)
                for repo in repos
            ]
            scored_repos = [(score, repo) for score, repo in scored_repos if score >= 3]
            scored_repos.sort(key=lambda item: item[0], reverse=True)
            accepted_repos: list[tuple[int, dict[str, Any]]] = []
            if scored_repos:
                threshold = max(6, scored_repos[0][0] - 2)
                accepted_repos = [
                    (score, repo)
                    for score, repo in scored_repos
                    if score >= threshold
                    and dependencies.repo_candidate_allowed(project, str(repo.get("url") or ""))
                ][:1]
            for score, repo in accepted_repos:
                bundle.add_record(evidence_record(
                    kind="github_repo_search_candidate",
                    source=f"github_search:{query}",
                    classification="candidate",
                    observed_at=observed_at,
                    value=dict(repo),
                    context={"query": query, "score": score},
                ))
            bundle.add_record(evidence_record(
                kind="investigation_log",
                source="github_search",
                classification="derived",
                observed_at=observed_at,
                value={
                    "kind": "github_repo_search",
                    "query": query,
                    "result_count": len(repos),
                    "accepted_count": len(accepted_repos),
                    "at": observed_at,
                },
            ))
            if accepted_repos:
                break
        except Exception as exc:
            bundle.add_failure(collector_failure(
                source="github",
                operation="repo_search",
                target=query,
                message=f"github repo search failed for {query}: {exc}",
                error=exc,
            ))

    actor_handles_to_probe: list[str] = []
    for query in build_bsky_search_queries(plan, "actor_search_queries", max_queries=2):
        observed_at = dependencies.now()
        bundle.add_record(evidence_record(
            kind="investigation_query",
            source="bsky_actor_search",
            classification="derived",
            observed_at=observed_at,
            value=query,
        ))
        try:
            actors = dependencies.search_bsky_actors(query, limit=5, timeout=dependencies.timeout)
            accepted_actors = [
                item for item in actors
                if dependencies.bsky_actor_relevance(project, item) >= 4
            ]
            bundle.add_record(evidence_record(
                kind="investigation_log",
                source="bsky_actor_search",
                classification="derived",
                observed_at=observed_at,
                value={
                    "kind": "bsky_actor_search",
                    "query": query,
                    "result_count": len(actors),
                    "accepted_count": len(accepted_actors),
                    "at": observed_at,
                },
            ))
            for actor in accepted_actors:
                handle = str(actor.get("handle") or "")
                if not handle:
                    continue
                bundle.add_record(evidence_record(
                    kind="developer_handle_candidate",
                    source=f"bsky_actor_search:{query}",
                    classification="candidate",
                    observed_at=observed_at,
                    value=handle,
                ))
                actor_handles_to_probe.append(handle)
            if len(unique_strings(actor_handles_to_probe)) >= 2:
                break
        except Exception as exc:
            bundle.add_failure(collector_failure(
                source="bluesky",
                operation="actor_search",
                target=query,
                message=f"Bluesky actor search failed for {query}: {exc}",
                error=exc,
            ))

    for query in build_bsky_search_queries(plan, "post_search_queries", max_queries=2):
        observed_at = dependencies.now()
        bundle.add_record(evidence_record(
            kind="investigation_query",
            source="bsky_post_search",
            classification="derived",
            observed_at=observed_at,
            value=query,
        ))
        try:
            posts = dependencies.search_bsky_posts(query, limit=5, timeout=dependencies.timeout)
            bundle.add_record(evidence_record(
                kind="investigation_log",
                source="bsky_post_search",
                classification="derived",
                observed_at=observed_at,
                value={
                    "kind": "bsky_post_search",
                    "query": query,
                    "result_count": len(posts),
                    "at": observed_at,
                },
            ))
            found_urls = False
            for post in posts:
                for url in post.get("urls", []):
                    found_urls = True
                    bundle.add_record(evidence_record(
                        kind="source_code_reference",
                        source=f"bsky_post_search:{query}",
                        classification="candidate",
                        observed_at=observed_at,
                        value=str(url),
                    ))
                handle = str(post.get("author_handle") or "")
                if handle and dependencies.handle_matches_project(project, handle):
                    bundle.add_record(evidence_record(
                        kind="developer_handle_candidate",
                        source=f"bsky_post_search:{query}",
                        classification="candidate",
                        observed_at=observed_at,
                        value=handle,
                    ))
                    actor_handles_to_probe.append(handle)
            if found_urls or len(unique_strings(actor_handles_to_probe)) >= 2:
                break
        except Exception as exc:
            bundle.add_failure(collector_failure(
                source="bluesky",
                operation="post_search",
                target=query,
                message=f"Bluesky post search failed for {query}: {exc}",
                error=exc,
            ))

    for handle in unique_strings(actor_handles_to_probe)[:2]:
        observed_at = dependencies.now()
        try:
            feed = dependencies.fetch_bsky_author_feed(handle, limit=8, timeout=dependencies.timeout)
            bundle.add_record(evidence_record(
                kind="investigation_log",
                source="bsky_author_feed",
                classification="derived",
                observed_at=observed_at,
                value={
                    "kind": "bsky_author_feed",
                    "actor": handle,
                    "result_count": len(feed),
                    "at": observed_at,
                },
            ))
            for item in feed:
                for url in item.get("urls", []):
                    bundle.add_record(evidence_record(
                        kind="source_code_reference",
                        source=f"bsky_author_feed:{handle}",
                        classification="candidate",
                        observed_at=observed_at,
                        value=str(url),
                    ))
            bundle.add_record(evidence_record(
                kind="developer_handle_candidate",
                source=f"bsky_author_feed:{handle}",
                classification="candidate",
                observed_at=observed_at,
                value=handle,
            ))
        except Exception as exc:
            bundle.add_failure(collector_failure(
                source="bluesky",
                operation="author_feed",
                target=handle,
                message=f"Bluesky author feed failed for {handle}: {exc}",
                error=exc,
            ))

    return bundle
