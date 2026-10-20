"""AT Protocol and Bluesky retrieval, authentication, and response decoding."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
from typing import Any

from . import http_transport
from ..identity import identifiers as identifier_core

DNS_RESOLVER_URL = "https://dns.google/resolve"
REQUEST_TIMEOUT_LEXICON_DNS = 12.0
REQUEST_TIMEOUT_LEXICON_HTTP = 45.0
BSKY_PUBLIC_API = "https://public.api.bsky.app"
BSKY_ENTRYWAY = "https://bsky.social"
BSKY_APPVIEW_PROXY = "did:web:api.bsky.app#bsky_appview"
_BSKY_SESSION_CACHE: dict[str, Any] = {}


def is_bsky_post_url(url: str) -> bool:
    normalized = identifier_core.normalize_url(str(url or "")) if url else ""
    if not normalized:
        return False
    parsed = urllib.parse.urlparse(normalized)
    if parsed.netloc != "bsky.app":
        return False
    return bool(re.match(r"^/profile/[^/]+/post/[^/]+$", parsed.path))


def bsky_auth_settings() -> dict[str, str]:
    return {
        "identifier": os.getenv("BSKY_HANDLE", "").strip(),
        "password": os.getenv("BSKY_APP_PASSWORD", "").strip(),
        "host": os.getenv("BSKY_AUTH_HOST", BSKY_ENTRYWAY).rstrip("/"),
    }


def bsky_session_cache_key() -> str:
    settings = bsky_auth_settings()
    return f"{settings['host']}|{settings['identifier']}"


def bsky_pds_url_from_session(session: dict[str, Any]) -> str:
    did_doc = session.get("didDoc")
    if isinstance(did_doc, dict):
        for service in did_doc.get("service", []):
            if not isinstance(service, dict):
                continue
            if str(service.get("id") or "") != "#atproto_pds":
                continue
            endpoint = str(service.get("serviceEndpoint") or "").rstrip("/")
            if endpoint.startswith("https://"):
                return endpoint
    return ""


def bsky_create_session() -> dict[str, Any]:
    settings = bsky_auth_settings()
    if not settings["identifier"] or not settings["password"]:
        raise RuntimeError("BSKY_HANDLE and BSKY_APP_PASSWORD are required for authenticated Bluesky search")
    cache_key = bsky_session_cache_key()
    cached = _BSKY_SESSION_CACHE.get(cache_key)
    if cached:
        return cached
    payload = http_transport.request_json_post(
        f"{settings['host']}/xrpc/com.atproto.server.createSession",
        {"identifier": settings["identifier"], "password": settings["password"]},
    )
    _BSKY_SESSION_CACHE[cache_key] = payload
    return payload


def bsky_access_token() -> str:
    session = bsky_create_session()
    token = str(session.get("accessJwt") or "")
    if not token:
        raise RuntimeError("Bluesky session did not return accessJwt")
    return token


def bsky_refresh_session() -> dict[str, Any]:
    cache_key = bsky_session_cache_key()
    session = bsky_create_session()
    refresh_token = str(session.get("refreshJwt") or "")
    if not refresh_token:
        raise RuntimeError("Bluesky session did not return refreshJwt")
    pds_url = bsky_pds_url_from_session(session)
    if not pds_url:
        settings = bsky_auth_settings()
        pds_url = settings["host"]
    _, body, _ = http_transport.request_bytes(
        f"{pds_url}/xrpc/com.atproto.server.refreshSession",
        headers={"Authorization": f"Bearer {refresh_token}"},
        method="POST",
    )
    refreshed = json.loads(body.decode("utf-8"))
    if not isinstance(refreshed, dict):
        raise RuntimeError("Bluesky refreshSession response was not a JSON object")
    if not refreshed.get("didDoc") and session.get("didDoc"):
        refreshed["didDoc"] = session["didDoc"]
    _BSKY_SESSION_CACHE[cache_key] = refreshed
    return refreshed


def bsky_pds_url() -> str:
    session = bsky_create_session()
    pds_url = bsky_pds_url_from_session(session)
    if pds_url:
        return pds_url
    settings = bsky_auth_settings()
    if settings["host"]:
        return settings["host"]
    raise RuntimeError("Bluesky session did not include a resolvable PDS endpoint")


def bsky_authenticated_app_url(path: str) -> str:
    base = bsky_pds_url()
    return f"{base.rstrip('/')}/xrpc/{path.lstrip('/')}"


def bsky_post_web_url(post: dict[str, Any]) -> str:
    author = post.get("author", {}) if isinstance(post.get("author"), dict) else {}
    handle = str(author.get("handle") or "").strip()
    uri = str(post.get("uri") or "").strip()
    rkey = uri.rsplit("/", 1)[-1] if uri else ""
    if handle and rkey:
        return f"https://bsky.app/profile/{handle}/post/{rkey}"
    return ""


def extract_post_link_entries(post: dict[str, Any]) -> list[dict[str, str]]:
    record = post.get("record", {}) if isinstance(post.get("record"), dict) else {}
    text = str(record.get("text") or "")
    raw = text.encode("utf-8")
    output: list[dict[str, str]] = []
    for facet in record.get("facets", []):
        if not isinstance(facet, dict):
            continue
        index_data = facet.get("index", {}) if isinstance(facet.get("index"), dict) else {}
        start = index_data.get("byteStart")
        end = index_data.get("byteEnd")
        label = ""
        if isinstance(start, int) and isinstance(end, int):
            try:
                label = raw[start:end].decode("utf-8", errors="replace").strip()
            except Exception:
                label = ""
        for feature in facet.get("features", []):
            if not isinstance(feature, dict):
                continue
            if feature.get("$type") != "app.bsky.richtext.facet#link":
                continue
            url = identifier_core.normalize_url(str(feature.get("uri") or ""))
            if not url:
                continue
            output.append(
                {
                    "label": label,
                    "url": url,
                    "post_uri": str(post.get("uri") or ""),
                    "post_web_url": bsky_post_web_url(post),
                }
            )
    return output


def search_bsky_actors(query: str, limit: int = 5, *, timeout: float = http_transport.REQUEST_TIMEOUT_DISCOVERY_SEARCH) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"q": query, "limit": max(1, min(limit, 10))})
    payload = http_transport.request_json(f"{BSKY_PUBLIC_API}/xrpc/app.bsky.actor.searchActors?{params}", timeout=timeout)
    results: list[dict[str, Any]] = []
    for item in payload.get("actors", [])[:limit]:
        results.append(
            {
                "handle": str(item.get("handle") or ""),
                "did": str(item.get("did") or ""),
                "display_name": str(item.get("displayName") or ""),
                "description": str(item.get("description") or ""),
            }
        )
    return results


def search_bsky_posts(query: str, limit: int = 5, *, timeout: float = http_transport.REQUEST_TIMEOUT_DISCOVERY_SEARCH) -> list[dict[str, Any]]:
    payload = search_bsky_posts_payload(query, limit=limit, timeout=timeout)
    results: list[dict[str, Any]] = []
    for item in payload.get("posts", [])[:limit]:
        author = item.get("author", {}) if isinstance(item.get("author"), dict) else {}
        record = item.get("record", {}) if isinstance(item.get("record"), dict) else {}
        text = str(record.get("text") or "")
        results.append(
            {
                "uri": str(item.get("uri") or ""),
                "text": text,
                "urls": identifier_core.extract_urls(text),
                "author_handle": str(author.get("handle") or ""),
                "author_did": str(author.get("did") or ""),
            }
        )
    return results


def search_bsky_posts_payload(
    query: str,
    *,
    limit: int = 25,
    cursor: str = "",
    since: str = "",
    timeout: float = http_transport.REQUEST_TIMEOUT_DISCOVERY_SEARCH,
) -> dict[str, Any]:
    params_dict: dict[str, Any] = {"q": query, "limit": max(1, min(limit, 100)), "sort": "latest"}
    if cursor:
        params_dict["cursor"] = cursor
    if since:
        params_dict["since"] = since
    params = urllib.parse.urlencode(params_dict)
    url = f"{bsky_authenticated_app_url('app.bsky.feed.searchPosts')}?{params}"
    headers = {"atproto-proxy": BSKY_APPVIEW_PROXY}
    try:
        payload = http_transport.request_json_authenticated(url, bsky_access_token(), headers=headers, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if exc.code == 401 or (exc.code == 400 and ("InvalidToken" in body or "ExpiredToken" in body)):
            refreshed = bsky_refresh_session()
            payload = http_transport.request_json_authenticated(url, str(refreshed.get("accessJwt") or ""), headers=headers, timeout=timeout)
        else:
            snippet = body[:300].strip()
            raise RuntimeError(f"Bluesky post search failed HTTP {exc.code}: {snippet or exc.reason}") from exc
    return payload if isinstance(payload, dict) else {}


def fetch_bsky_author_feed(actor: str, limit: int = 10, *, timeout: float = http_transport.REQUEST_TIMEOUT_DISCOVERY_SEARCH) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"actor": actor, "limit": max(1, min(limit, 20))})
    payload = http_transport.request_json(f"{BSKY_PUBLIC_API}/xrpc/app.bsky.feed.getAuthorFeed?{params}", timeout=timeout)
    results: list[dict[str, Any]] = []
    for item in payload.get("feed", [])[:limit]:
        post = item.get("post", {}) if isinstance(item.get("post"), dict) else {}
        author = post.get("author", {}) if isinstance(post.get("author"), dict) else {}
        record = post.get("record", {}) if isinstance(post.get("record"), dict) else {}
        text = str(record.get("text") or "")
        results.append(
            {
                "uri": str(post.get("uri") or ""),
                "text": text,
                "urls": bsky_record_urls(record),
                "author_handle": str(author.get("handle") or actor),
            }
        )
    return results


def fetch_bsky_profile(actor: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"actor": actor})
    payload = http_transport.request_json(f"{BSKY_PUBLIC_API}/xrpc/app.bsky.actor.getProfile?{params}")
    return payload if isinstance(payload, dict) else {}


def bsky_record_urls(record: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    facets = record.get("facets", []) if isinstance(record.get("facets", []), list) else []
    for facet in facets:
        if not isinstance(facet, dict):
            continue
        for feature in facet.get("features", []) if isinstance(facet.get("features", []), list) else []:
            if not isinstance(feature, dict):
                continue
            uri = str(feature.get("uri") or "").strip()
            if uri:
                urls.append(uri)
    urls.extend(identifier_core.extract_urls(str(record.get("text") or "")))
    return list(dict.fromkeys(value for value in urls if value))


def resolve_dns_txt(name: str, *, timeout: float = REQUEST_TIMEOUT_LEXICON_DNS) -> list[str]:
    params = urllib.parse.urlencode({"name": name, "type": "TXT"})
    payload = http_transport.request_json(f"{DNS_RESOLVER_URL}?{params}", timeout=timeout)
    values: list[str] = []
    for answer in payload.get("Answer", []):
        data = str(answer.get("data") or "").strip()
        if data.startswith('"') and data.endswith('"'):
            data = data[1:-1]
        values.append(data)
    return values


def resolve_did_document_url(did: str) -> str:
    if did.startswith("did:plc:"):
        return f"https://plc.directory/{urllib.parse.quote(did, safe=':')}"
    if did.startswith("did:web:"):
        body = did.removeprefix("did:web:")
        parts = body.split(":")
        if len(parts) == 1:
            return f"https://{parts[0]}/.well-known/did.json"
        host = parts[0]
        path = "/".join(parts[1:])
        return f"https://{host}/{path}/did.json"
    raise ValueError(f"unsupported DID method: {did}")


def resolve_did_document(did: str, *, timeout: float = REQUEST_TIMEOUT_LEXICON_HTTP) -> dict[str, Any]:
    return http_transport.request_json(resolve_did_document_url(did), timeout=timeout)


def extract_pds_url(did_doc: dict[str, Any]) -> str:
    for service in did_doc.get("service", []):
        service_type = str(service.get("type") or "")
        service_id = str(service.get("id") or "")
        if service_type == "AtprotoPersonalDataServer" or service_id.endswith("#atproto_pds"):
            return str(service.get("serviceEndpoint") or "").rstrip("/")
    return ""


def did_document_handles(did_doc: dict[str, Any]) -> list[str]:
    values = []
    for item in did_doc.get("alsoKnownAs", []) if isinstance(did_doc.get("alsoKnownAs"), list) else []:
        text = str(item or "").strip()
        if text.startswith("at://"):
            values.append(text.removeprefix("at://").strip().lower().rstrip("."))
    return list(dict.fromkeys(value for value in values if value))


def resolve_lexicon_authority_did(authority_domain: str, *, timeout: float = REQUEST_TIMEOUT_LEXICON_DNS) -> str:
    txt_name = f"_lexicon.{authority_domain}"
    for value in resolve_dns_txt(txt_name, timeout=timeout):
        if value.startswith("did="):
            return value.removeprefix("did=").strip()
    raise ValueError(f"no lexicon DID found for authority domain: {authority_domain}")


def fetch_lexicon_record(
    nsid: str,
    *,
    dns_timeout: float = REQUEST_TIMEOUT_LEXICON_DNS,
    http_timeout: float = REQUEST_TIMEOUT_LEXICON_HTTP,
    authority_cache: dict[str, str] | None = None,
    document_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = identifier_core.normalize_nsid(nsid)
    authority_domain = identifier_core.derive_authority_domain(normalized)
    if not authority_domain:
        raise ValueError(f"invalid NSID: {nsid}")
    did = (authority_cache or {}).get(authority_domain) or resolve_lexicon_authority_did(authority_domain, timeout=dns_timeout)
    if authority_cache is not None:
        authority_cache[authority_domain] = did
    did_doc = (document_cache or {}).get(did) or resolve_did_document(did, timeout=http_timeout)
    if document_cache is not None:
        document_cache[did] = did_doc
    pds_url = extract_pds_url(did_doc)
    if not pds_url:
        raise ValueError(f"no PDS endpoint found for DID: {did}")
    params = urllib.parse.urlencode(
        {
            "repo": did,
            "collection": "com.atproto.lexicon.schema",
            "rkey": normalized,
        }
    )
    payload = http_transport.request_json(f"{pds_url}/xrpc/com.atproto.repo.getRecord?{params}", timeout=http_timeout)
    return {
        "did": did,
        "did_doc": did_doc,
        "authority_domain": authority_domain,
        "pds_url": pds_url,
        "record": payload,
    }
