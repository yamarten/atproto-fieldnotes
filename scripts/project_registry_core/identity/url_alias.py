from __future__ import annotations

import urllib.parse
from typing import Any
from . import identifiers


def normalize_origin(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), "", "", "", ""))


def root_www_pair(first: str, second: str) -> bool:
    host_a = urllib.parse.urlparse(first).hostname or ""
    host_b = urllib.parse.urlparse(second).hostname or ""
    return bool(host_a and host_b and host_a.removeprefix("www.") == host_b.removeprefix("www.") and host_a != host_b)


def observed_url_aliases(project: dict[str, Any]) -> list[dict[str, str]]:
    aliases: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in project.get("relation_evidence", []):
        if not isinstance(item, dict) or str(item.get("classification") or "") != "confirmed":
            continue
        relation = item.get("relation") or {}
        if not isinstance(relation, dict) or relation.get("predicate") != "redirects_to":
            continue
        alias = identifiers.normalize_url(str((relation.get("subject") or {}).get("value") or ""))
        canonical = identifiers.normalize_url(str((relation.get("object") or {}).get("value") or ""))
        if alias and canonical and root_www_pair(alias, canonical) and (alias, canonical) not in seen:
            seen.add((alias, canonical))
            aliases.append({"alias": alias, "canonical": canonical, "reason": "confirmed_redirect", "confidence": "confirmed"})
    fingerprints: dict[str, list[str]] = {}
    for result in project.get("service_verification_results", []):
        if not isinstance(result, dict):
            continue
        digest = str(result.get("content_sha256") or "")
        url = identifiers.normalize_url(str(result.get("final_url") or result.get("requested_url") or ""))
        if digest and url:
            fingerprints.setdefault(digest, []).append(url)
    preferred = identifiers.normalize_url(str(project.get("website_url") or ""))
    for urls in fingerprints.values():
        unique = list(dict.fromkeys(urls))
        if len(unique) != 2 or not root_www_pair(unique[0], unique[1]):
            continue
        canonical = preferred if preferred in unique else next((url for url in unique if urllib.parse.urlparse(url).hostname.startswith("www.")), unique[0])
        alias = unique[1] if canonical == unique[0] else unique[0]
        if (alias, canonical) not in seen:
            seen.add((alias, canonical))
            aliases.append({"alias": alias, "canonical": canonical, "reason": "identical_content", "confidence": "confirmed"})
    return aliases


def canonical_alias_url(project: dict[str, Any], url: str) -> str:
    normalized = identifiers.normalize_url(url)
    for item in list(project.get("url_aliases", [])) + observed_url_aliases(project):
        if str(item.get("confidence") or "") == "confirmed" and identifiers.normalize_url(str(item.get("alias") or "")) == normalized:
            return identifiers.normalize_url(str(item.get("canonical") or "")) or normalized
    return normalized


def apply_url_aliases(project: dict[str, Any]) -> None:
    project["url_aliases"] = observed_url_aliases(project)
    canonical_urls = {
        identifiers.normalize_url(str(item.get("canonical") or ""))
        for item in project["url_aliases"]
        if item.get("confidence") == "confirmed"
    }
    values = project.get("service_url_candidates", [])
    project["service_url_candidates"] = list(dict.fromkeys(
        canonical_alias_url(project, str(item))
        for item in values
        if canonical_alias_url(project, str(item)) not in canonical_urls
        or canonical_alias_url(project, str(item)) not in {
            identifiers.normalize_url(str(project.get("website_url") or "")),
            *(identifiers.normalize_url(str(url)) for url in project.get("service_urls", [])),
        }
    ))
