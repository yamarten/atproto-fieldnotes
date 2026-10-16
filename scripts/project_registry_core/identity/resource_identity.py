"""Shared resource values and keys, independent of project roles and adoption."""
from __future__ import annotations

import urllib.parse

from . import identifiers
from ..repositories import repo_urls


URL_KINDS = {"website", "website_url", "service", "site", "site_url", "service_url", "service_url_candidate"}
REPO_KINDS = {"repo", "repo_url", "github_repo_url", "tangled_repo_url"}


def normalize_value(kind: str, value: str) -> str:
    text = str(value or "").strip()
    if kind in REPO_KINDS:
        return repo_urls.canonical_repo_url(text)
    if kind in URL_KINDS or kind == "oauth_client" or kind.endswith("_url"):
        return identifiers.normalize_url(text)
    if kind in {"nsid", "collection", "resolved_nsid"}:
        return identifiers.normalize_nsid(text)
    if kind in {"handle", "domain"}:
        return text.lower().rstrip(".")
    if kind == "account":
        account = identifiers.normalize_atproto_account_identifier(text)
        return account[1] if account else text
    return text


def key(kind: str, value: str) -> tuple[str, str]:
    """Resource equality; role and observation provenance are separate keys.

    A collection is a record collection, not an arbitrary Lexicon schema.
    Domain/handle and handle/DID likewise remain distinct naming systems.
    """
    if kind in REPO_KINDS:
        kind = "repo_url"
    elif kind in URL_KINDS:
        kind = "url"
    elif kind == "account":
        account = identifiers.normalize_atproto_account_identifier(value)
        if account:
            return account
    return kind, normalize_value(kind, value) if kind != "url" else identifiers.normalize_url(value)


def schema_node_kind(schema_type: str) -> str:
    return "collection" if schema_type == "record" else "nsid"


def matches_guard(kind: str, value: str, guard_value: str, *, descendants: bool = False) -> bool:
    """Exact resource identity, optionally extended by an explicit URL scope."""
    value_key, guard_key = key(kind, value), key(kind, guard_value)
    if not value_key[1] or not guard_key[1]:
        return False
    if value_key == guard_key:
        return True
    if not descendants or kind != "service_url":
        return False
    parsed, guard = (urllib.parse.urlparse(item[1]) for item in (value_key, guard_key))
    if (parsed.scheme, parsed.netloc) != (guard.scheme, guard.netloc):
        return False
    # A query/fragment names a particular surface, never a subtree scope.
    if guard.query or guard.fragment or guard.params:
        return False
    path = guard.path.rstrip("/")
    return not path or parsed.path.startswith(path + "/")
