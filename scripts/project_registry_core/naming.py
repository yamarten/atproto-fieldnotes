from __future__ import annotations

import re
import urllib.parse

from .identity import identifiers
from . import project_record
from .identity import domain_policy
from .repositories import repo_urls
from typing import Any


GENERIC_PLATFORM_HOSTS = {
    "github.com",
    "gist.github.com",
    "tangled.org",
    "tangled.sh",
}


def clean_title(title: str) -> str:
    text = str(title or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s*\|\s*.*$", "", text)
    text = re.sub(r"\s+[—–\-]\s+.*$", "", text)
    text = re.sub(r"[:：]\s+.*$", "", text)
    text = re.sub(r"\s+[·•]\s+.*$", "", text)
    text = re.sub(r"\s+>>\s+.*$", "", text)
    text = re.sub(r"\s+//\s+.*$", "", text)
    return text.strip()


def service_seed_name_hint(url: str) -> str:
    normalized = identifiers.normalize_url(url)
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.netloc.lower()
    segments = [segment for segment in parsed.path.split("/") if segment]
    if host in GENERIC_PLATFORM_HOSTS and segments:
        return segments[-1]
    return host or normalized


def normalize_name_candidate(value: str) -> str:
    text = clean_title(str(value or "").strip())
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip(" -_:|")
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        normalized_url = identifiers.normalize_url(text)
        if normalized_url:
            return urllib.parse.urlparse(normalized_url).netloc.lower()
    if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", text.lower()):
        return text.lower()
    return text[:80].strip()


def source_is_repo(source: str) -> bool:
    return (
        source.startswith("seed:repo_url:")
        or source.startswith("github_repo_name:")
        or source.startswith("repo_name:")
    )


def source_is_service_or_discovery(source: str) -> bool:
    return (
        source.startswith("service_title:")
        or source.startswith("resource_title:")
        or source == "llm_output"
        or source.startswith("lexicon_name:")
    )


def source_url(source: str) -> str:
    for prefix in ("service_title:", "resource_title:"):
        if source.startswith(prefix):
            return identifiers.normalize_url(source[len(prefix) :])
    return ""


GENERIC_NSID_NAME_TOKENS = {
    "app",
    "client",
    "collection",
    "comment",
    "entry",
    "feed",
    "item",
    "object",
    "page",
    "post",
    "profile",
    "record",
    "test",
}


GENERIC_PLATFORM_NAME_VALUES = {
    "atproto",
    "bluesky",
    "bsky",
}


GENERIC_PLATFORM_NAME_ALIASES = {
    "atproto": {"atproto"},
    "bluesky": {"bluesky", "bsky"},
    "bsky": {"bluesky", "bsky"},
}


GENERIC_ERROR_PAGE_TITLE_VALUES = {
    "access denied",
    "bad gateway",
    "error",
    "forbidden",
    "gateway timeout",
    "not found",
    "service unavailable",
    "unauthorized",
}


GENERIC_NAVIGATION_PAGE_TITLE_VALUES = {
    "about",
    "blog",
    "contact",
    "docs",
    "documentation",
    "home",
    "index",
    "login",
    "privacy",
    "sign in",
    "signin",
    "terms",
}


def name_is_safe_for_record(project: dict[str, Any]) -> bool:
    name = str(project.get("name") or "").strip()
    if not name:
        return False
    normalized_name = identifiers.normalize_alias_token(name)
    if not normalized_name:
        return False
    llm_output = project.get("llm_output") if isinstance(project.get("llm_output"), dict) else {}
    if isinstance(llm_output, dict) and str(llm_output.get("name") or "").strip() == name:
        return True
    if project.get("website_url") or project.get("project_repository_urls"):
        nsid_tails = {
            identifiers.normalize_alias_token(str(item or "").split(".")[-1])
            for item in list(project.get("input_nsids", [])) + list(project.get("resolved_nsids", []))
            if str(item or "").strip()
        }
        if normalized_name in GENERIC_NSID_NAME_TOKENS and normalized_name in nsid_tails:
            return False
        return True
    return False


def add_name_candidate(project: dict[str, Any], value: str, source: str) -> None:
    normalized = normalize_name_candidate(value)
    if not normalized:
        return
    project_record.add_candidate_text(project, "name", normalized, source)


def sync_current_llm_name_candidate(project: dict[str, Any]) -> None:
    llm_output = project.get("llm_output") if isinstance(project.get("llm_output"), dict) else {}
    current_name = normalize_name_candidate(str(llm_output.get("name") or ""))
    retained = [
        dict(item)
        for item in project.get("name_candidates", [])
        if isinstance(item, dict) and str(item.get("source") or "") != "llm_output"
    ]
    current_candidate = next(
        (
            dict(item)
            for item in project.get("name_candidates", [])
            if isinstance(item, dict)
            and str(item.get("source") or "") == "llm_output"
            and normalize_name_candidate(str(item.get("value") or "")) == current_name
        ),
        None,
    )
    project["name_candidates"] = retained
    if not current_name:
        return
    if current_candidate is not None:
        current_candidate["value"] = current_name
        project["name_candidates"].append(current_candidate)
        return
    add_name_candidate(project, current_name, "llm_output")


def nsid_authority_service_origins(project: dict[str, Any]) -> set[str]:
    origins: set[str] = set()
    nsids = project_record.unique_list(
        [str(item or "") for item in project.get("input_nsids", [])]
        + [str(item or "") for item in project.get("resolved_nsids", [])]
    )
    for raw_nsid in nsids:
        nsid = identifiers.normalize_nsid(raw_nsid)
        authority_domain = identifiers.derive_authority_domain(nsid)
        if not authority_domain:
            continue
        origins.update(domain_policy.candidate_service_origins_for_authority_domain(authority_domain))
    return {identifiers.normalize_url(url) for url in origins if identifiers.normalize_url(url)}


def nsid_seed_name_tokens(project: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    nsids = project_record.unique_list(
        [str(item or "") for item in project.get("input_nsids", [])]
        + [str(item or "") for item in project.get("resolved_nsids", [])]
    )
    for raw_nsid in nsids:
        nsid = identifiers.normalize_nsid(raw_nsid)
        if not nsid:
            continue
        tokens.update(project_record.seed_value_identity_tokens(nsid))
    return {token for token in tokens if token and len(token) >= 4 and token not in GENERIC_NSID_NAME_TOKENS}


def name_value_matches_nsid_seed(project: dict[str, Any], value: str) -> bool:
    compact = identifiers.normalize_alias_token(clean_title(value))
    if not compact:
        return False
    return any(token in compact for token in nsid_seed_name_tokens(project))


def unsupported_nsid_authority_title_candidate(project: dict[str, Any], value: str, source: str) -> bool:
    candidate_url = source_url(source)
    if not candidate_url or candidate_url not in nsid_authority_service_origins(project):
        return False
    supported_service_urls = {
        identifiers.normalize_url(str(url or ""))
        for url in project.get("supported_service_urls", [])
        if identifiers.normalize_url(str(url or ""))
    }
    if identifiers.normalize_url(candidate_url) in supported_service_urls:
        return False
    return not name_value_matches_nsid_seed(project, value)


def value_matches_unsupported_nsid_authority_title(project: dict[str, Any], value: str) -> bool:
    normalized_value = normalize_name_candidate(value)
    if not normalized_value:
        return False
    for item in project.get("name_candidates", []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        raw_value = str(item.get("value") or "")
        if not unsupported_nsid_authority_title_candidate(project, raw_value, source):
            continue
        if normalize_name_candidate(raw_value) == normalized_value:
            return True
    return False


def generic_error_page_title_candidate(value: str, source: str) -> bool:
    lowered = normalize_name_candidate(value).lower()
    if lowered in GENERIC_ERROR_PAGE_TITLE_VALUES:
        return source == "existing_name" or source.startswith("service_title:") or source.startswith("resource_title:")
    return bool(re.fullmatch(r"(?:[45]\d\d\s+)?(?:error|not found|forbidden|unauthorized)", lowered))


def generic_navigation_page_title_candidate(value: str, source: str) -> bool:
    lowered = normalize_name_candidate(value).lower()
    if lowered not in GENERIC_NAVIGATION_PAGE_TITLE_VALUES:
        return False
    return source == "existing_name" or source.startswith("service_title:") or source.startswith("resource_title:")


def value_matches_generic_navigation_title(project: dict[str, Any], value: str) -> bool:
    normalized_value = normalize_name_candidate(value)
    if not normalized_value:
        return False
    for item in project.get("name_candidates", []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        raw_value = str(item.get("value") or "")
        if not generic_navigation_page_title_candidate(raw_value, source):
            continue
        if normalize_name_candidate(raw_value) == normalized_value:
            return True
    return False


def title_identity_parts(value: str) -> set[str]:
    parts = {
        identifiers.normalize_alias_token(part)
        for part in re.split(r"[\s./:_-]+", clean_title(value))
        if part.strip()
    }
    return {token for token in parts if token and len(token) >= 4}


def service_title_matches_project_identity(project: dict[str, Any], value: str, source: str) -> bool:
    if generic_platform_name_supported_by_source(value, source):
        return True
    title_parts = title_identity_parts(value)
    if not title_parts:
        return False
    identity_tokens = set(project_record.project_trusted_identity_tokens(project))
    expanded_identity: set[str] = set()
    for token in identity_tokens:
        if not token:
            continue
        expanded_identity.add(token)
        if len(token) > 4 and token.endswith("s"):
            expanded_identity.add(token[:-1])
    identity_tokens = {token for token in expanded_identity if token and len(token) >= 4}
    if not identity_tokens:
        return False
    if identifiers.normalize_alias_token(value) in identity_tokens:
        return True
    matched = {
        token
        for token in title_parts
        if token in identity_tokens or (len(token) > 4 and token.endswith("s") and token[:-1] in identity_tokens)
    }
    if not matched:
        return False
    return len(matched) / len(title_parts) >= 0.66


def weak_service_page_title_candidate(project: dict[str, Any], value: str, source: str) -> bool:
    if not (source.startswith("service_title:") or source.startswith("resource_title:")):
        return False
    return not service_title_matches_project_identity(project, value, source)


def value_matches_weak_service_page_title(project: dict[str, Any], value: str) -> bool:
    normalized_value = normalize_name_candidate(value)
    if not normalized_value:
        return False
    for item in project.get("name_candidates", []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        raw_value = str(item.get("value") or "")
        if not weak_service_page_title_candidate(project, raw_value, source):
            continue
        if normalize_name_candidate(raw_value) == normalized_value:
            return True
    return False


def generic_platform_name_supported_by_source(value: str, source: str) -> bool:
    normalized = normalize_name_candidate(value).lower()
    aliases = GENERIC_PLATFORM_NAME_ALIASES.get(normalized, {normalized})
    candidate_url = source_url(source)
    if not candidate_url:
        return False
    parsed = urllib.parse.urlparse(candidate_url)
    host = parsed.netloc.lower()
    host_labels = {part for part in host.split(".") if part}
    host_tokens = {identifiers.normalize_alias_token(host)}
    host_tokens.update(identifiers.normalize_alias_token(part) for part in host_labels)
    return bool(aliases & {token for token in host_tokens if token})


def generic_platform_name_candidate(project: dict[str, Any], value: str, source: str) -> bool:
    normalized = normalize_name_candidate(value).lower()
    if normalized not in GENERIC_PLATFORM_NAME_VALUES:
        return False
    if not source_is_service_or_discovery(source):
        return False
    if generic_platform_name_supported_by_source(value, source):
        return False
    repo_names = {
        normalize_name_candidate(urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]).lower()
        for url in project_record.project_repository_url_values(project)
        if urllib.parse.urlparse(identifiers.normalize_url(str(url or ""))).path.rstrip("/")
    }
    return normalized not in repo_names


def current_name_candidates(project: dict[str, Any]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    current_name = normalize_name_candidate(clean_title(str(project.get("name") or "")))
    if (
        current_name
        and not weak_nsid_derived_name(project, current_name)
        and not generic_error_page_title_candidate(current_name, "existing_name")
        and not generic_navigation_page_title_candidate(current_name, "existing_name")
        and not value_matches_unsupported_nsid_authority_title(project, current_name)
        and not value_matches_weak_service_page_title(project, current_name)
    ):
        candidates.append({"value": current_name, "source": "existing_name"})
        seen.add((current_name, "existing_name"))
    for item in project.get("name_candidates", []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        raw_value = str(item.get("value") or "")
        if source.startswith("service_title:") or source.startswith("resource_title:"):
            raw_value = clean_title(raw_value)
        value = normalize_name_candidate(raw_value)
        if not value or not source:
            continue
        if not source.startswith("lexicon_name:") and weak_nsid_derived_name(project, value):
            continue
        if generic_error_page_title_candidate(value, source):
            continue
        if generic_navigation_page_title_candidate(value, source):
            continue
        if weak_service_page_title_candidate(project, value, source):
            continue
        if unsupported_nsid_authority_title_candidate(project, value, source):
            continue
        if source == "llm_output" and value_matches_unsupported_nsid_authority_title(project, value):
            continue
        if source == "llm_output" and value_matches_generic_navigation_title(project, value):
            continue
        if source == "llm_output" and value_matches_weak_service_page_title(project, value):
            continue
        if generic_platform_name_candidate(project, value, source):
            continue
        key = (value, source)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({"value": value, "source": source})
    return candidates


def weak_nsid_derived_name(project: dict[str, Any], value: str) -> bool:
    text = normalize_name_candidate(value)
    if not text:
        return False
    lowered = text.lower()
    normalized_token = identifiers.normalize_alias_token(text)
    nsids = [
        identifiers.normalize_nsid(str(item or ""))
        for item in list(project.get("input_nsids", [])) + list(project.get("resolved_nsids", []))
        if str(item or "").strip()
    ]
    if lowered in nsids:
        return True
    nsid_tails = {identifiers.normalize_alias_token(item.split(".")[-1]) for item in nsids if item}
    if normalized_token and normalized_token in nsid_tails and not name_is_safe_for_record(project):
        return True
    if normalized_token and normalized_token in nsid_tails and normalized_token in GENERIC_NSID_NAME_TOKENS:
        return True
    return False


def name_candidate_score(project: dict[str, Any], candidate: dict[str, str]) -> tuple[int, int, int, int, str]:
    value = normalize_name_candidate(str(candidate.get("value") or ""))
    source = str(candidate.get("source") or "")
    lowered = value.lower()
    normalized_token = identifiers.normalize_alias_token(value)
    source_rank = 10
    if source == "operator_edit":
        source_rank = 100
    elif source.startswith("service_title:"):
        source_rank = 90
    elif source == "llm_output":
        source_rank = 80
    elif source.startswith("resource_title:"):
        source_rank = 70
    elif source_is_repo(source):
        source_rank = 60
    elif source == "existing_name":
        source_rank = 50
    elif source.startswith("lexicon_name:"):
        source_rank = 30
    elif source.startswith("seed:"):
        source_rank = 20

    quality = 0
    if " " in value:
        quality += 6
    if len(value) >= 4:
        quality += 3
    if len(value) > 40:
        quality -= 4
    if "." in lowered and " " not in value:
        quality -= 8
    if normalized_token in GENERIC_NSID_NAME_TOKENS:
        quality -= 12
    if lowered in GENERIC_PLATFORM_NAME_VALUES and not source_is_repo(source):
        quality -= 30
    if source_is_repo(source) and ("-" in value or "_" in value):
        quality += 8

    identity_bonus = 0
    identity_tokens = project_record.project_identity_anchor_tokens(project)
    if normalized_token and normalized_token in identity_tokens:
        identity_bonus += 8
    host_bonus = 0
    for host in project_record.ownership_anchor_service_hosts(project):
        host_token = identifiers.normalize_alias_token(host)
        if host_token and normalized_token and host_token == normalized_token:
            host_bonus = 6
            break

    return (source_rank, identity_bonus, host_bonus, quality, value.lower())


def choose_best_project_name(project: dict[str, Any]) -> str:
    candidates = current_name_candidates(project)
    if not candidates:
        return ""
    best = max(candidates, key=lambda item: name_candidate_score(project, item))
    return normalize_name_candidate(str(best.get("value") or ""))


def apply_project_name(project: dict[str, Any]) -> None:
    sync_current_llm_name_candidate(project)
    chosen_name = choose_best_project_name(project)
    if chosen_name:
        project["name"] = chosen_name
    if not project.get("name"):
        if project.get("website_url"):
            project["name"] = urllib.parse.urlparse(project["website_url"]).netloc
        elif project_record.service_url_candidate_values(project):
            project["name"] = urllib.parse.urlparse(project_record.service_url_candidate_values(project)[0]).netloc
        elif project_record.provider_repo_url_values(project, "github"):
            slug = repo_urls.extract_github_slug(project_record.provider_repo_url_values(project, "github")[0])
            if slug:
                project["name"] = slug.split("/")[-1]
