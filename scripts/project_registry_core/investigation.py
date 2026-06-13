from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


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
