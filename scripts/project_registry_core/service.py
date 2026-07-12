from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

from .evidence import CollectorFailure, CollectorResult, EvidenceBundle, EvidenceRecord
from .relations import relation_record

SUPPORTING_PAGE_PATHS = {
    "about",
    "blog",
    "docs",
    "documentation",
    "faq",
    "help",
    "login",
    "register",
    "sign-in",
    "signin",
    "sign-up",
    "signup",
}
SERVICE_HTML_EXTRACT_MAX_BYTES = 12 * 1024
SERVICE_LINK_MAX_COUNT = 128
SERVICE_LINK_MAX_BYTES = 64 * 1024
SERVICE_LINK_TEXT_MAX_BYTES = 512
SERVICE_URL_MAX_COUNT = 192
SERVICE_URL_MAX_BYTES = 32 * 1024
SERVICE_URL_ITEM_MAX_BYTES = 2048
SERVICE_VERIFICATION_RESULT_MAX_COUNT = 32
SERVICE_LINK_PRIORITY_TOKENS = (
    "source",
    "repo",
    "repository",
    "code",
    "docs",
    "documentation",
    "blog",
    "support",
    "help",
    "status",
    "privacy",
    "terms",
    "login",
    "sign in",
    "signin",
    "signup",
    "register",
)


def service_verification_result_key(value: dict[str, Any]) -> tuple[str, str]:
    lane = str(value.get("lane") or "static").strip().lower()
    url = str(value.get("requested_url") or value.get("final_url") or "").strip().rstrip("/")
    return lane, url


def merge_service_verification_results(
    existing: Any,
    value: dict[str, Any],
) -> list[dict[str, Any]]:
    combined = [dict(item) for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    combined.append(dict(value))
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ordinal, item in reversed(list(enumerate(combined))):
        key = service_verification_result_key(item)
        resolved_key = key if key[1] else (key[0], f"__missing_url__:{ordinal}")
        if resolved_key in seen:
            continue
        seen.add(resolved_key)
        selected.append(item)
    selected.reverse()
    return selected[-SERVICE_VERIFICATION_RESULT_MAX_COUNT:]


def bounded_text(value: str, limit: int = 20000) -> str:
    return str(value or "")[:limit]


def bounded_utf8_text(value: str, max_bytes: int) -> str:
    encoded = str(value or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return str(value or "")
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def service_link_priority(base_url: str, url: str, text: str = "") -> int:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    base_host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    lowered = f"{text} {url}".lower()
    if host in {"github.com", "tangled.org", "bsky.app", "staging.bsky.app"}:
        return 0
    if any(token in lowered for token in SERVICE_LINK_PRIORITY_TOKENS):
        return 1
    if host and base_host and (
        host == base_host or host.endswith(f".{base_host}") or base_host.endswith(f".{host}")
    ):
        return 2
    return 3


def bounded_json_items(
    items: list[tuple[int, int, Any]],
    *,
    max_count: int,
    max_bytes: int,
) -> list[Any]:
    output: list[Any] = []
    used_bytes = 2
    for _priority, _ordinal, value in sorted(items, key=lambda item: (item[0], item[1])):
        encoded_bytes = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        separator_bytes = 2 if output else 0
        if len(output) >= max_count:
            break
        if used_bytes + separator_bytes + encoded_bytes > max_bytes:
            continue
        output.append(value)
        used_bytes += separator_bytes + encoded_bytes
    return output


def bounded_service_links(base_url: str, values: Any) -> list[dict[str, str]]:
    raw_links = values if isinstance(values, list) else []
    candidates: list[tuple[int, int, dict[str, str]]] = []
    seen: set[tuple[str, str]] = set()
    for ordinal, item in enumerate(raw_links):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("href") or "")
        if len(url.encode("utf-8")) > SERVICE_URL_ITEM_MAX_BYTES:
            continue
        text = bounded_utf8_text(str(item.get("text") or ""), SERVICE_LINK_TEXT_MAX_BYTES)
        if not url or (url, text) in seen:
            continue
        seen.add((url, text))
        candidates.append((service_link_priority(base_url, url, text), ordinal, {"url": url, "text": text}))
    return bounded_json_items(
        candidates,
        max_count=SERVICE_LINK_MAX_COUNT,
        max_bytes=SERVICE_LINK_MAX_BYTES,
    )


def bounded_service_urls(base_url: str, values: Any) -> list[str]:
    raw_urls = values if isinstance(values, list) else []
    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for ordinal, item in enumerate(raw_urls):
        url = str(item or "")
        if len(url.encode("utf-8")) > SERVICE_URL_ITEM_MAX_BYTES:
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append((service_link_priority(base_url, url), ordinal, url))
    return bounded_json_items(
        candidates,
        max_count=SERVICE_URL_MAX_COUNT,
        max_bytes=SERVICE_URL_MAX_BYTES,
    )


def service_probe_represents_service_identity(requested_url: str, lane: str) -> bool:
    if str(lane or "") == "follow_up":
        return False
    parsed = urllib.parse.urlparse(requested_url)
    first_path = parsed.path.strip("/").split("/", 1)[0].lower()
    return not first_path or first_path not in SUPPORTING_PAGE_PATHS


def collect_service_probe_evidence(
    project_key: str,
    requested_url: str,
    result: dict[str, Any],
    *,
    observed_at: str,
    lane: str = "static",
    supports_project: bool = False,
) -> EvidenceBundle:
    html = str(result.get("html") or "")
    value = {
        key: result.get(key)
        for key in (
            "status_code", "final_url", "title", "description", "fetch_error",
            "service_status", "service_status_confidence", "service_status_evidence",
            "redirected", "redirect_same_host", "redirect_same_site_family",
            "redirect_http_to_https", "redirect_cross_host",
            "authority_fallback_from", "authority_fallback_status_code",
            "cache_hit",
        )
        if key in result
    }
    value["requested_url"] = requested_url
    value["lane"] = lane
    raw_links = result.get("links") if isinstance(result.get("links"), list) else []
    raw_urls = result.get("urls") if isinstance(result.get("urls"), list) else []
    value["links"] = bounded_service_links(requested_url, raw_links)
    value["observed_link_count"] = len(raw_links)
    value["urls"] = bounded_service_urls(requested_url, raw_urls)
    value["observed_url_count"] = len(raw_urls)
    value["html_extract"] = bounded_utf8_text(html, SERVICE_HTML_EXTRACT_MAX_BYTES)
    value["observed_html_bytes"] = len(html.encode("utf-8"))
    value["content_sha256"] = hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest() if html else ""
    bundle = EvidenceBundle(project_key=project_key, collected_at=observed_at)
    bundle.add_record(EvidenceRecord(
        kind="service_verification",
        source=f"service_{lane}:{requested_url}",
        classification="confirmed" if int(result.get("status_code") or 0) else "candidate",
        observed_at=observed_at,
        value=value,
    ))
    if (
        int(result.get("status_code") or 0)
        and int(result.get("status_code") or 0) < 400
        and supports_project
        and service_probe_represents_service_identity(requested_url, lane)
    ):
        bundle.add_record(relation_record(
            source=f"service_{lane}:{requested_url}", classification="confirmed", observed_at=observed_at,
            subject_type="project", subject_value=project_key, predicate="operates_service",
            object_type="service", object_value=requested_url.rstrip("/"), locator=requested_url, method=f"{lane}_probe",
        ))
    for link in result.get("links", []) if isinstance(result.get("links"), list) else []:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or link.get("href") or "").rstrip("/")
        label = str(link.get("text") or "").lower()
        host = urllib.parse.urlparse(url).netloc.lower()
        if host in {"github.com", "tangled.org"} and any(token in label for token in ("source", "repo", "repository", "code")):
            bundle.add_record(relation_record(
                source=f"service_page:{requested_url}", classification="confirmed", observed_at=observed_at,
                subject_type="website", subject_value=requested_url.rstrip("/"), predicate="source_repository",
                object_type="repo", object_value=url, locator=requested_url, method="first_party_link",
            ))
        if host in {"bsky.app", "staging.bsky.app"} and "/profile/" in urllib.parse.urlparse(url).path:
            bundle.add_record(relation_record(
                source=f"service_page:{requested_url}", classification="confirmed", observed_at=observed_at,
                subject_type="website", subject_value=requested_url.rstrip("/"), predicate="official_account",
                object_type="account", object_value=url, locator=requested_url, method="first_party_link",
            ))
    final_url = str(result.get("final_url") or "")
    if final_url and final_url.rstrip("/") != requested_url.rstrip("/"):
        bundle.add_record(relation_record(
            source=f"service_{lane}:{requested_url}", classification="confirmed", observed_at=observed_at,
            subject_type="service", subject_value=requested_url.rstrip("/"), predicate="redirects_to",
            object_type="service", object_value=final_url.rstrip("/"), locator=requested_url, method="http_redirect",
        ))
    canonical_match = re.search(r'<link[^>]+rel=["\'][^"\']*canonical[^"\']*["\'][^>]+href=["\']([^"\']+)', html, re.IGNORECASE)
    if canonical_match:
        canonical_url = urllib.parse.urljoin(requested_url, canonical_match.group(1)).rstrip("/")
        if canonical_url and canonical_url != requested_url.rstrip("/"):
            bundle.add_record(relation_record(
                source=f"service_{lane}:{requested_url}", classification="confirmed", observed_at=observed_at,
                subject_type="service", subject_value=requested_url.rstrip("/"), predicate="redirects_to",
                object_type="service", object_value=canonical_url, locator=requested_url, method="html_canonical",
            ))
    status = "failed" if result.get("fetch_error") and not result.get("status_code") else "success"
    bundle.add_collector_result(CollectorResult(
        source=f"service_{lane}", operation="probe", status=status, observed_at=observed_at,
        request_count=1, result_count=1 if status == "success" else 0,
        stop_reason=str(result.get("fetch_error") or ""),
        cache_hits=1 if result.get("cache_hit") else 0,
    ))
    return bundle


@dataclass(frozen=True)
class BrowserDependencies:
    navigate: Callable[[str], dict[str, Any]]
    now: Callable[[], str]


def same_site_url(base_url: str, candidate: str) -> bool:
    base = urllib.parse.urlparse(base_url)
    parsed = urllib.parse.urlparse(candidate)
    return bool(base.hostname and parsed.hostname and (
        base.hostname == parsed.hostname
        or base.hostname.endswith(f".{parsed.hostname}")
        or parsed.hostname.endswith(f".{base.hostname}")
    ))


def collect_browser_service_evidence(
    project: dict[str, Any],
    service_url: str,
    dependencies: BrowserDependencies,
) -> EvidenceBundle:
    observed_at = dependencies.now()
    bundle = EvidenceBundle(project_key=str(project.get("project_key") or ""), collected_at=observed_at)
    try:
        pages: list[dict[str, Any]] = []
        first = dict(dependencies.navigate(service_url))
        pages.append(first)
        candidates: list[str] = []
        for link in first.get("links", []):
            href = str(link.get("href") if isinstance(link, dict) else link or "")
            text = str(link.get("text") if isinstance(link, dict) else "").lower()
            if href and same_site_url(service_url, href) and any(token in f"{text} {href.lower()}" for token in ("login", "log in", "sign in", "signup", "sign up", "register")):
                candidates.append(href)
        for url in list(dict.fromkeys(candidates))[:2]:
            pages.append(dict(dependencies.navigate(url)))
        value = {
            "requested_url": service_url,
            "lane": "browser",
            "pages": [
                {
                    "url": str(page.get("url") or ""),
                    "title": str(page.get("title") or ""),
                    "text_extract": bounded_text(str(page.get("text") or ""), 6000),
                    "network_errors": list(page.get("network_errors") or [])[:20],
                    "console_errors": list(page.get("console_errors") or [])[:20],
                }
                for page in pages
            ],
            "login_surface_reached": len(pages) > 1,
            "js_rendered": bool(first.get("js_rendered", True)),
        }
        bundle.add_record(EvidenceRecord(
            kind="service_verification", source=f"service_browser:{service_url}",
            classification="confirmed", observed_at=observed_at, value=value,
        ))
        bundle.add_collector_result(CollectorResult(
            source="service_browser", operation="anonymous_navigation", status="success",
            observed_at=observed_at, request_count=len(pages), result_count=len(pages),
        ))
    except Exception as exc:
        bundle.add_failure(CollectorFailure(
            source="service_browser", operation="anonymous_navigation", target=service_url,
            category="browser_error", message=f"browser verification failed for {service_url}: {exc}", retryable=False,
        ))
        bundle.add_collector_result(CollectorResult(
            source="service_browser", operation="anonymous_navigation", status="failed",
            observed_at=observed_at, request_count=1, stop_reason="browser_error",
        ))
    return bundle


def playwright_navigate(url: str, *, timeout_ms: int = 30000) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("browser verification requires requirements-browser.txt") from exc
    with sync_playwright() as playwright:  # pragma: no cover - integration path
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        network_errors: list[str] = []
        console_errors: list[str] = []
        page.on("requestfailed", lambda request: network_errors.append(f"{request.method} {request.url}"))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        payload = {
            "url": page.url,
            "title": page.title(),
            "text": page.locator("body").inner_text(timeout=timeout_ms),
            "links": page.locator("a[href]").evaluate_all(
                "els => els.slice(0, 100).map(a => ({href: a.href, text: (a.innerText || '').trim()}))"
            ),
            "network_errors": network_errors,
            "console_errors": console_errors,
            "js_rendered": True,
        }
        browser.close()
        return payload
