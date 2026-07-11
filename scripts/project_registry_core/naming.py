from __future__ import annotations

import re
import urllib.parse

from . import identifiers


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
