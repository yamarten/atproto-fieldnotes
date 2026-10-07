from __future__ import annotations

import re
import urllib.parse

from ..identity import identifiers


STRICT_REPO_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
KNOWN_FORGE_REPO_HOSTS = {"codeberg.org", "gitlab.com"}
KNOWN_FORGE_REPO_HOSTS_WITH_USER_PREFIX = {"git.sr.ht"}


def extract_github_slug(repo_url: str) -> str | None:
    normalized = identifiers.normalize_url(identifiers.sanitize_extracted_url(repo_url))
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc != "github.com":
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return None
    return f"{segments[0]}/{segments[1]}"


def normalize_github_repo_url(url: str) -> str | None:
    cleaned = identifiers.sanitize_extracted_url(url)
    slug = extract_github_slug(cleaned)
    if slug is None:
        return None
    owner, repo = slug.split("/", 1)
    if not STRICT_REPO_SEGMENT_RE.fullmatch(owner) or not STRICT_REPO_SEGMENT_RE.fullmatch(repo):
        return None
    return f"https://github.com/{owner}/{repo}"


def normalize_tangled_repo_url(url: str) -> str | None:
    normalized = identifiers.normalize_url(identifiers.sanitize_extracted_url(url))
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc not in {"tangled.org", "tangled.sh"}:
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return None
    if segments[0].startswith("@"):
        segments = [segments[0].lstrip("@"), *segments[1:]]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    if any(segment in {"issues", "pulls", "pull", "brand", "search", "about", "blob", "tree"} for segment in (owner, repo)):
        return None
    owner_is_valid = bool(STRICT_REPO_SEGMENT_RE.fullmatch(owner) or owner.startswith("did:plc:"))
    if not owner_is_valid or not STRICT_REPO_SEGMENT_RE.fullmatch(repo):
        return None
    return f"https://tangled.org/{owner}/{repo}"


def normalize_forge_repo_url(url: str) -> str | None:
    normalized = identifiers.normalize_url(identifiers.sanitize_extracted_url(url))
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.netloc.lower()
    segments = [segment for segment in parsed.path.split("/") if segment]
    if host in KNOWN_FORGE_REPO_HOSTS:
        if len(segments) < 2:
            return None
        owner, repo = segments[0], segments[1]
        if any(segment in {"issues", "pulls", "pull", "search", "about", "src", "tree", "blob", "-", "explore"} for segment in (owner, repo)):
            return None
        if not STRICT_REPO_SEGMENT_RE.fullmatch(owner) or not STRICT_REPO_SEGMENT_RE.fullmatch(repo):
            return None
        return f"https://{host}/{owner}/{repo}"
    if host in KNOWN_FORGE_REPO_HOSTS_WITH_USER_PREFIX:
        if len(segments) < 2:
            return None
        owner, repo = segments[0], segments[1]
        owner_valid = owner.startswith("~") and STRICT_REPO_SEGMENT_RE.fullmatch(owner[1:])
        if not owner_valid or not STRICT_REPO_SEGMENT_RE.fullmatch(repo):
            return None
        return f"https://{host}/{owner}/{repo}"
    return None


def is_supported_repo_url(url: str) -> bool:
    return bool(normalize_github_repo_url(url) or normalize_tangled_repo_url(url) or normalize_forge_repo_url(url))


def infer_repo_provider(url: str) -> str:
    parsed = urllib.parse.urlparse(identifiers.normalize_url(url))
    if parsed.netloc == "github.com":
        return "github"
    if parsed.netloc in {"tangled.org", "tangled.sh"}:
        return "tangled"
    return parsed.netloc or "unknown"


def canonical_repo_url(url: str) -> str:
    provider = infer_repo_provider(url)
    if provider == "github":
        return normalize_github_repo_url(url) or identifiers.normalize_url(url)
    if provider == "tangled":
        return normalize_tangled_repo_url(url) or identifiers.normalize_url(url)
    if provider in KNOWN_FORGE_REPO_HOSTS or provider in KNOWN_FORGE_REPO_HOSTS_WITH_USER_PREFIX:
        return normalize_forge_repo_url(url) or identifiers.normalize_url(url)
    return identifiers.normalize_url(url)


def repo_identity_key(url: str) -> str:
    """Return a comparison key without changing the displayed canonical URL."""
    canonical = canonical_repo_url(url)
    parsed = urllib.parse.urlparse(canonical)
    if parsed.netloc.lower() == "github.com":
        return canonical.lower()
    return canonical


def repo_owner_identity(url: str) -> str:
    parsed = urllib.parse.urlparse(identifiers.normalize_url(url))
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.netloc in {"github.com", "tangled.org"} and segments:
        return segments[0].lower()
    if parsed.netloc in KNOWN_FORGE_REPO_HOSTS | KNOWN_FORGE_REPO_HOSTS_WITH_USER_PREFIX and segments:
        return segments[0].lower().removeprefix("~")
    return ""


def normalize_repo_collection(urls: list[str], *, provider: str | None = None) -> list[str]:
    normalized_urls: list[str] = []
    for url in urls:
        parsed = urllib.parse.urlparse(identifiers.normalize_url(url))
        if provider == "github" or parsed.netloc == "github.com":
            normalized = normalize_github_repo_url(url)
        elif provider == "tangled" or parsed.netloc == "tangled.org":
            normalized = normalize_tangled_repo_url(url)
        elif parsed.netloc in KNOWN_FORGE_REPO_HOSTS | KNOWN_FORGE_REPO_HOSTS_WITH_USER_PREFIX:
            normalized = normalize_forge_repo_url(url)
        else:
            normalized = identifiers.normalize_url(url)
        if normalized and normalized not in normalized_urls:
            normalized_urls.append(normalized)
    return normalized_urls
