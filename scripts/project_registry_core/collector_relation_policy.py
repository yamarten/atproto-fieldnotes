from __future__ import annotations

import urllib.parse

from . import identifiers
from . import repo_urls


FUNDING_HOSTS = {
    "buymeacoffee.com",
    "ko-fi.com",
    "opencollective.com",
    "patreon.com",
}
PACKAGE_HOSTS = {
    "crates.io",
    "greasyfork.org",
    "jsr.io",
    "metacpan.org",
    "npmjs.com",
    "packagist.org",
    "pkg.go.dev",
    "pypi.org",
    "pub.dev",
    "swiftpackageindex.com",
}
DOCUMENTATION_HOSTS = {
    "docs.rs",
    "hexdocs.pm",
    "readthedocs.io",
}
SERVICE_CANDIDATE_EXCLUDED_ROLES = {
    "account_profile",
    "documentation",
    "funding",
    "invalid",
    "non_public",
    "package",
    "repository",
}
WEBSITE_CANDIDATE_EXCLUDED_ROLES = {
    "account_profile",
    "funding",
    "invalid",
    "non_public",
    "repository",
}


def classify_url_role(value: str) -> str:
    normalized = identifiers.normalize_url(
        identifiers.sanitize_extracted_url(str(value or ""))
    )
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower().rstrip("/")
    if not normalized or parsed.scheme not in {"http", "https"} or not host:
        return "invalid"
    if (
        "." not in host
        or host == "localhost"
        or host.endswith((".localhost", ".example", ".invalid", ".local", ".test"))
    ):
        return "non_public"
    if repo_urls.is_supported_repo_url(normalized):
        return "repository"
    if host == "github.com" and path.startswith("/sponsors"):
        return "funding"
    if host in FUNDING_HOSTS:
        return "funding"
    if host in PACKAGE_HOSTS:
        return "package"
    if host == "wordpress.org" and path.startswith("/plugins/"):
        return "package"
    if host == "bsky.app" and path.startswith("/profile/"):
        return "account_profile"
    if (
        host in DOCUMENTATION_HOSTS
        or host.startswith("docs.")
        or path == "/docs"
        or path.startswith("/docs/")
        or path == "/documentation"
        or path.startswith("/documentation/")
    ):
        return "documentation"
    return "unknown"


def normalize_repository_homepage(value: str) -> str:
    normalized = identifiers.normalize_url(
        identifiers.sanitize_extracted_url(str(value or ""))
    )
    return normalized if classify_url_role(normalized) not in {"invalid", "non_public"} else ""


def repository_homepage_service_candidate(value: str) -> str:
    normalized = normalize_repository_homepage(value)
    if not normalized or classify_url_role(normalized) in SERVICE_CANDIDATE_EXCLUDED_ROLES:
        return ""
    parsed = urllib.parse.urlparse(normalized)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def repository_homepage_is_website_candidate(value: str) -> bool:
    normalized = normalize_repository_homepage(value)
    return bool(
        normalized
        and classify_url_role(normalized) not in WEBSITE_CANDIDATE_EXCLUDED_ROLES
    )
