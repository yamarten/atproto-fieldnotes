from __future__ import annotations

from typing import Any

from ..html_link_context import extract_link_contexts
from . import repo_urls


def _repository_target(locator: str) -> tuple[str, str] | None:
    if not repo_urls.is_supported_repo_url(locator):
        return None
    canonical = repo_urls.canonical_repo_url(locator)
    return (canonical, repo_urls.repo_identity_key(canonical)) if canonical else None


def extract_repository_link_contexts(html: str, base_url: str) -> list[dict[str, Any]]:
    return extract_link_contexts(html, base_url, target=_repository_target, kind="repository")
