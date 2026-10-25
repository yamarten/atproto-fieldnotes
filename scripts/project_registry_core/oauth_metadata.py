"""Observed client declarations, not granted permissions or actual data usage."""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

PARSER_VERSION = "@atproto/oauth-scopes@0.5.12/1"
MAX_SCOPE_LENGTH = 16384
DISCOVERY_VERSION = "service-oauth-discovery-v1"
CONVENTIONAL_PATHS = ("/client-metadata.json", "/oauth-client-metadata.json", "/oauth/client-metadata.json")
MAX_RESPONSE_BYTES = 256 * 1024


@lru_cache(maxsize=128)
def _parse_json(scope: str) -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("OAuth scope parsing requires Node.js and npm install")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("oauth_scope_parser.mjs"))],
        input=json.dumps({"scope": scope}), text=True, capture_output=True,
        timeout=5, check=False,
    )
    if result.returncode:
        raise RuntimeError("OAuth scope parser failed; check Node.js and npm install")
    # Cache serialized results so callers cannot mutate another observation.
    json.loads(result.stdout)
    return result.stdout


def analyze_scope(scope: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "parser_version": PARSER_VERSION, "permissions": [], "unrecognized": [],
        "atproto_only": False,
    }
    if not isinstance(scope, str):
        return {**result, "status": "missing" if scope is None else "invalid_type"}
    if len(scope) > MAX_SCOPE_LENGTH:
        return {**result, "status": "too_large"}
    try:
        return {**result, **json.loads(_parse_json(scope))}
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        return {**result, "status": "unavailable", "error": str(exc)}


def observe(metadata_url: str, metadata: dict[str, Any], observed_at: str, *,
            response: dict[str, Any] | None = None, discovery: dict[str, Any] | None = None) -> dict[str, Any]:
    scope = metadata.get("scope")
    return {
        "metadata_url": metadata_url, "observed_at": observed_at,
        "declared_client_id": str(metadata.get("client_id") or ""),
        "declared_client_uri": str(metadata.get("client_uri") or ""),
        "application_type": metadata.get("application_type", "web"),
        "redirect_uris": metadata.get("redirect_uris", []),
        "grant_types": metadata.get("grant_types", []),
        "response_types": metadata.get("response_types", []),
        "dpop_bound_access_tokens": metadata.get("dpop_bound_access_tokens"),
        "response": dict(response or {}),
        "discovery": dict(discovery or {}),
        "scope": scope[:MAX_SCOPE_LENGTH] if isinstance(scope, str) else None,
        "scope_truncated": isinstance(scope, str) and len(scope) > MAX_SCOPE_LENGTH,
        "analysis": analyze_scope(scope),
    }


def origin(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
        port = parsed.port
        return f"https://{parsed.hostname.lower()}" + (f":{port}" if port and port != 443 else "")
    except ValueError:
        return ""


def web_surface(observation: dict[str, Any]) -> str:
    """Bind a *declaration* to its exact homepage, never infer actual OAuth use."""
    from .identity import identifiers
    url = str(observation.get("metadata_url") or "")
    surface = str(observation.get("declared_client_uri") or "")
    response = observation.get("response", {})
    scopes = observation.get("analysis", {}).get("permissions", [])
    grants = observation.get("grant_types")
    responses = observation.get("response_types")
    discovery = observation.get("discovery", {})
    if (not origin(url) or origin(surface) != origin(url)
            or origin(str(discovery.get("service_url") or "")) != origin(url)
            or urllib.parse.urlsplit(surface).fragment or urllib.parse.urlsplit(url).fragment
            or observation.get("declared_client_id") != url
            or response.get("status_code") != 200 or response.get("redirected") is not False
            or response.get("content_type", "").split(";", 1)[0].strip().lower() != "application/json"
            or observation.get("application_type") != "web"
            or observation.get("dpop_bound_access_tokens") is not True
            or not isinstance(grants, list) or "authorization_code" not in grants
            or not isinstance(responses, list) or "code" not in responses
            or not isinstance(observation.get("redirect_uris"), list)
            or not observation["redirect_uris"]
            or not all(isinstance(uri, str) and origin(uri) == origin(url)
                       and not urllib.parse.urlsplit(uri).fragment for uri in observation["redirect_uris"])
            or not any(item.get("kind") == "atproto" for item in scopes)):
        return ""
    return identifiers.normalize_url(surface)


def supports_candidate(project: dict[str, Any], url: str) -> bool:
    return any(web_surface(item) == url for item in usable_observations(project, fresh=True))


def normalize_attempts(values: Any) -> list[dict[str, Any]]:
    latest = {}
    for item in values if isinstance(values, list) else []:
        if isinstance(item, dict) and item.get("metadata_url"):
            url = item["metadata_url"]
            if url not in latest or _time(item.get("observed_at")) >= _time(latest[url].get("observed_at")):
                latest[url] = dict(item)
    return sorted(latest.values(), key=lambda item: _time(item.get("observed_at")))[-32:]


def usable_observations(project: dict[str, Any], *, fresh: bool = False) -> list[dict[str, Any]]:
    attempts = {item["metadata_url"]: item for item in normalize_attempts(project.get("oauth_metadata_attempts"))}
    now = datetime.now(timezone.utc)
    result = []
    for item in normalize_results(project.get("oauth_client_metadata")):
        attempt = attempts.get(item["metadata_url"], {})
        if (attempt.get("status") in {"not_metadata", "missing", "redirected"}
                and _time(attempt.get("observed_at")) >= _time(item.get("observed_at"))):
            continue
        if fresh and not 0 <= (now - _time(item.get("observed_at"))).total_seconds() <= 7 * 86400:
            continue
        if web_surface(item):
            result.append(item)
    return result


def discovery_plan(project: dict[str, Any], service_url: str, links: Any, html: str, *,
                   conventional: bool, observed_at: str) -> list[dict[str, str]]:
    from .relations import oauth_metadata_candidates
    service_origin = origin(service_url)
    known = [item["metadata_url"] for item in normalize_results(project.get("oauth_client_metadata"))
             if web_surface(item) == service_url or item.get("discovery", {}).get("service_url") == service_url]
    saved_links = [link for value in project.get("service_verification_results", [])
                   if value.get("requested_url") == service_url
                   for page in value.get("pages", []) for link in
                   list(page.get("links", [])) + [{"url": url, "text": "OAuth client metadata"} for url in page.get("metadata_urls", [])]]
    explicit = oauth_metadata_candidates(service_url, list(links or []) + saved_links, html)
    guesses = [service_origin + path for path in CONVENTIONAL_PATHS] if service_origin and conventional else []
    attempts = {item["metadata_url"]: item for item in normalize_attempts(project.get("oauth_metadata_attempts"))}
    result, seen = [], set()
    for method, urls in (("known", known), ("page_reference", explicit), ("conventional_path", guesses)):
        for url in urls:
            if url in seen or not origin(url):
                continue
            seen.add(url)
            attempt = attempts.get(url, {})
            if (method == "conventional_path" and attempt.get("status") in {"missing", "not_metadata", "redirected"}
                    and (_time(observed_at) - _time(attempt.get("observed_at"))).total_seconds() < 30 * 86400):
                continue
            result.append({"metadata_url": url, "method": method, "service_url": service_url,
                           "policy_version": DISCOVERY_VERSION})
    return result[:3]


def _time(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def normalize_results(values: Any) -> list[dict[str, Any]]:
    """Keep the newest snapshot per metadata URL, including empty/failed parses."""
    latest: dict[str, dict[str, Any]] = {}
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict) or not item.get("metadata_url") or not isinstance(item.get("analysis"), dict):
            continue
        url = str(item["metadata_url"])
        if url not in latest or _time(item.get("observed_at")) >= _time(latest[url].get("observed_at")):
            latest[url] = dict(item)
    return list(latest.values())


def reconcile_relations(project: dict[str, Any]) -> None:
    # Replace old declarations, rather than retaining permissions a client has
    # removed. Hydration/replay use saved analysis and never start Node or fetch.
    from .relations import normalize_relation_records, oauth_analysis_relation_records

    observations = normalize_results(project.get("oauth_client_metadata", []))
    project["oauth_client_metadata"] = observations
    sources = {f"oauth_client_metadata:{item['metadata_url']}" for item in observations}
    relations = [item for item in project.get("relation_evidence", []) if item.get("source") not in sources]
    for item in observations:
        for record in oauth_analysis_relation_records(item["metadata_url"], item["analysis"], item.get("observed_at", "")):
            relations.append({"relation": record.value, "source": record.source,
                              "classification": record.classification, "observed_at": record.observed_at,
                              "context": record.context})
    project["relation_evidence"] = normalize_relation_records(relations)
