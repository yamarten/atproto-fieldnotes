from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

from . import domain_policy, identifiers
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
SERVICE_BROWSER_FORM_MAX_COUNT = 12
SERVICE_BROWSER_INPUT_MAX_COUNT = 24
SERVICE_BROWSER_ACTION_MAX_COUNT = 24
SERVICE_BROWSER_PUBLIC_CONTENT_MIN_COUNT = 3
SERVICE_BROWSER_PUBLIC_ACTION_PHRASES = (
    "post new entry",
    "new entry",
    "new post",
    "create entry",
    "create post",
    "write entry",
    "write post",
    "compose",
    "publish",
    "upload",
)
SERVICE_BROWSER_POSITIVE_CONTRADICTIONS = {
    "coming_soon_detected",
    "docs_only_surface_detected",
    "mixed_launch_signals_detected",
}
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


def normalize_redirect_same_site_policy(value: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(value)
    if "redirect_same_site_family" not in normalized:
        return normalized
    requested = identifiers.normalize_url(str(
        normalized.get("requested_url") or normalized.get("service_url") or normalized.get("url") or ""
    ))
    final = identifiers.normalize_url(str(normalized.get("final_url") or ""))
    if not requested or not final:
        return normalized
    corrected = bool(requested != final and domain_policy.is_same_site_family(requested, final))
    normalized["redirect_same_site_family"] = corrected
    evidence = [str(item) for item in normalized.get("service_status_evidence", []) if str(item)]
    normalized["service_status_evidence"] = (
        list(dict.fromkeys([*evidence, "redirect_same_site_family"]))
        if corrected
        else [item for item in evidence if item != "redirect_same_site_family"]
    )
    return normalized


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
    supports_project: bool | None = None,
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
    if supports_project is not None:
        value["supports_project"] = bool(supports_project)
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


def normalized_browser_forms(value: Any) -> list[dict[str, Any]]:
    forms: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        inputs = []
        for raw_input in item.get("inputs", []) if isinstance(item.get("inputs"), list) else []:
            if not isinstance(raw_input, dict):
                continue
            inputs.append({
                "type": bounded_text(str(raw_input.get("type") or "text").lower(), 32),
                "name": bounded_text(str(raw_input.get("name") or ""), 128),
                "autocomplete": bounded_text(str(raw_input.get("autocomplete") or ""), 64),
                "placeholder": bounded_text(str(raw_input.get("placeholder") or ""), 256),
            })
        forms.append({
            "action": bounded_text(str(item.get("action") or ""), 2048),
            "method": bounded_text(str(item.get("method") or "get").lower(), 16),
            "inputs": inputs[:SERVICE_BROWSER_INPUT_MAX_COUNT],
            "submit_texts": [
                bounded_text(str(text), 256)
                for text in (item.get("submit_texts", []) if isinstance(item.get("submit_texts"), list) else [])
                if str(text).strip()
            ][:8],
        })
        if len(forms) >= SERVICE_BROWSER_FORM_MAX_COUNT:
            break
    return forms


def normalized_browser_auth_actions(value: Any) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        actions.append({
            "kind": bounded_text(str(item.get("kind") or ""), 32),
            "href": bounded_text(str(item.get("href") or ""), 2048),
            "text": bounded_text(str(item.get("text") or ""), 256),
        })
        if len(actions) >= SERVICE_BROWSER_ACTION_MAX_COUNT:
            break
    return actions


def normalized_browser_service_actions(value: Any) -> list[dict[str, str]]:
    return normalized_browser_auth_actions(value)


def browser_page_auth_features(page: dict[str, Any]) -> dict[str, bool]:
    forms = normalized_browser_forms(page.get("forms"))
    page_context = " ".join((
        str(page.get("url") or ""),
        str(page.get("title") or ""),
    )).lower()
    auth_context_tokens = ("login", "log in", "sign in", "signin", "signup", "sign up", "register")
    password_form_detected = False
    identity_form_detected = False
    for form in forms:
        inputs = list(form.get("inputs") or [])
        submit_text = " ".join(str(item) for item in form.get("submit_texts") or []).lower()
        form_context = f"{page_context} {form.get('action', '')} {submit_text}".lower()
        has_submit = bool(form.get("submit_texts")) or any(
            str(item.get("type") or "").lower() in {"submit", "image"}
            for item in inputs if isinstance(item, dict)
        )
        has_password = any(
            str(item.get("type") or "").lower() == "password"
            for item in inputs if isinstance(item, dict)
        )
        has_identity = any(
            str(item.get("type") or "").lower() == "email"
            or any(
                token in " ".join(str(item.get(field) or "") for field in ("name", "autocomplete", "placeholder")).lower()
                for token in ("email", "username", "user name", "handle", "identifier", "login")
            )
            for item in inputs if isinstance(item, dict)
        )
        password_form_detected = password_form_detected or (has_password and has_submit)
        identity_form_detected = identity_form_detected or (
            has_identity and has_submit and any(token in form_context for token in auth_context_tokens)
        )

    oauth_entry_detected = False
    for action in normalized_browser_auth_actions(page.get("auth_actions")):
        text = str(action.get("text") or "").lower()
        href = str(action.get("href") or "").lower()
        provider = any(token in f"{text} {href}" for token in (
            "atproto", "bluesky", "github", "google", "apple", "discord", "microsoft",
        ))
        oauth_entry_detected = oauth_entry_detected or (
            "oauth" in href
            or "/authorize" in href
            or (provider and any(token in text for token in ("continue", "sign in", "log in", "login")))
        )
    return {
        "password_form_detected": password_form_detected,
        "identity_form_detected": identity_form_detected,
        "oauth_entry_detected": oauth_entry_detected,
    }


def browser_page_public_surface_features(page: dict[str, Any], service_url: str) -> dict[str, Any]:
    service_actions = normalized_browser_service_actions(page.get("service_actions"))
    service_action_detected = any(
        any(
            phrase in str(action.get("text") or "").strip().lower()
            for phrase in SERVICE_BROWSER_PUBLIC_ACTION_PHRASES
        )
        for action in service_actions
    )

    page_url = str(page.get("url") or service_url)
    content_urls: set[str] = set()
    for item in page.get("links", []) if isinstance(page.get("links"), list) else []:
        href = str(item.get("href") or item.get("url") or "") if isinstance(item, dict) else str(item or "")
        resolved = urllib.parse.urljoin(page_url, href)
        if not resolved or not same_site_url(service_url, resolved):
            continue
        path_parts = [part for part in urllib.parse.urlparse(resolved).path.split("/") if part]
        if len(path_parts) < 2 or path_parts[0].lower() in SUPPORTING_PAGE_PATHS:
            continue
        content_urls.add(resolved.rstrip("/"))

    public_content_item_count = len(content_urls)
    return {
        "service_action_detected": service_action_detected,
        "public_content_item_count": public_content_item_count,
        "public_app_surface_detected": (
            service_action_detected
            and public_content_item_count >= SERVICE_BROWSER_PUBLIC_CONTENT_MIN_COUNT
        ),
    }


def same_site_url(base_url: str, candidate: str) -> bool:
    base = urllib.parse.urlparse(base_url)
    parsed = urllib.parse.urlparse(candidate)
    return bool(base.hostname and parsed.hostname and (
        base.hostname == parsed.hostname
        or base.hostname.endswith(f".{parsed.hostname}")
        or parsed.hostname.endswith(f".{base.hostname}")
    ))


def browser_service_observation(value: dict[str, Any]) -> dict[str, Any]:
    pages = [item for item in value.get("pages", []) if isinstance(item, dict)]
    evidence: list[str] = []
    if value.get("password_form_detected"):
        evidence.append("browser_password_form_detected")
    if value.get("identity_form_detected"):
        evidence.append("browser_identity_form_detected")
    if value.get("oauth_entry_detected"):
        evidence.append("browser_oauth_entry_detected")
    if value.get("public_app_surface_detected"):
        evidence.append("browser_public_app_surface_detected")
    positive = bool(value.get("login_surface_reached") or value.get("public_app_surface_detected"))
    if positive:
        evidence.insert(0, "browser_login_surface_reached")
    elif pages and not any(page.get("network_errors") for page in pages):
        evidence.append("browser_rendered_surface")
    if value.get("rendered_error_detected"):
        evidence.append("browser_rendered_error_detected")
    return {
        "service_status": "live" if positive else "unknown",
        "service_status_confidence": "high" if positive else "none",
        "service_status_evidence": list(dict.fromkeys(evidence)),
        "positive_surface": positive,
    }


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
        first_url = str(first.get("url") or service_url).rstrip("/")
        for url in list(dict.fromkeys(candidates))[:2]:
            if url.rstrip("/") == first_url:
                continue
            pages.append(dict(dependencies.navigate(url)))
        page_features = [browser_page_auth_features(page) for page in pages]
        public_surface_features = [browser_page_public_surface_features(page, service_url) for page in pages]
        password_form_detected = any(item["password_form_detected"] for item in page_features)
        identity_form_detected = any(item["identity_form_detected"] for item in page_features)
        oauth_entry_detected = any(item["oauth_entry_detected"] for item in page_features)
        auth_menu_expanded = any(int(page.get("menu_expanded_count") or 0) > 0 for page in pages)
        service_action_detected = any(item["service_action_detected"] for item in public_surface_features)
        public_content_item_count = max(
            (int(item["public_content_item_count"]) for item in public_surface_features),
            default=0,
        )
        public_app_surface_detected = any(item["public_app_surface_detected"] for item in public_surface_features)
        rendered_error_detected = any(
            page.get("network_errors") or page.get("console_errors")
            for page in pages
        )
        browser_transport_fallbacks = list(dict.fromkeys(
            str(page.get("transport_fallback") or "")
            for page in pages
            if str(page.get("transport_fallback") or "")
        ))
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
                    "forms": normalized_browser_forms(page.get("forms")),
                    "auth_actions": normalized_browser_auth_actions(page.get("auth_actions")),
                    "service_actions": normalized_browser_service_actions(page.get("service_actions")),
                    "links": bounded_service_links(
                        str(page.get("url") or service_url),
                        page.get("links"),
                    ),
                    "menu_expanded_count": int(page.get("menu_expanded_count") or 0),
                    "transport_fallback": str(page.get("transport_fallback") or ""),
                }
                for page in pages
            ],
            "auth_link_followed": len(pages) > 1,
            "password_form_detected": password_form_detected,
            "identity_form_detected": identity_form_detected,
            "oauth_entry_detected": oauth_entry_detected,
            "auth_menu_expanded": auth_menu_expanded,
            "login_surface_reached": password_form_detected or identity_form_detected or oauth_entry_detected,
            "service_action_detected": service_action_detected,
            "public_content_item_count": public_content_item_count,
            "public_app_surface_detected": public_app_surface_detected,
            "rendered_error_detected": rendered_error_detected,
            "browser_transport_fallbacks": browser_transport_fallbacks,
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


def playwright_page_payload(
    playwright: Any,
    url: str,
    *,
    timeout_ms: int,
    browser_args: list[str] | None = None,
    transport_fallback: str = "",
) -> dict[str, Any]:
    browser = playwright.chromium.launch(headless=True, args=list(browser_args or []))
    try:
        page = browser.new_page()
        network_errors: list[str] = []
        console_errors: list[str] = []
        page.on("requestfailed", lambda request: network_errors.append(f"{request.method} {request.url}"))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        menu_expanded_count = page.locator('button[aria-haspopup="menu"]').evaluate_all(
            """els => {
                const visible = els.filter(item => item.offsetWidth || item.offsetHeight || item.getClientRects().length);
                const targets = visible.filter(item => item.getAttribute('aria-expanded') !== 'true').slice(0, 3);
                targets.forEach(item => item.click());
                return targets.length;
            }"""
        )
        if menu_expanded_count:
            page.wait_for_timeout(200)
        return {
            "url": page.url,
            "title": page.title(),
            "text": page.locator("body").inner_text(timeout=timeout_ms),
            "links": page.locator("a[href]").evaluate_all(
                "els => els.slice(0, 100).map(a => ({href: a.href, text: (a.innerText || '').trim()}))"
            ),
            "forms": page.locator("form").evaluate_all(
                """els => els.slice(0, 12).map(form => ({
                    action: form.action || '',
                    method: (form.method || 'get').toLowerCase(),
                    inputs: Array.from(form.querySelectorAll('input')).slice(0, 24).map(input => ({
                        type: (input.type || 'text').toLowerCase(),
                        name: input.name || '',
                        autocomplete: input.autocomplete || '',
                        placeholder: input.placeholder || ''
                    })),
                    submit_texts: Array.from(form.querySelectorAll('button, input[type=submit], input[type=image]'))
                        .slice(0, 8).map(item => (item.innerText || item.value || item.getAttribute('aria-label') || '').trim())
                }))"""
            ),
            "auth_actions": page.locator("a[href], button").evaluate_all(
                """els => els.map(item => ({
                    kind: item.tagName.toLowerCase(),
                    href: item.href || '',
                    text: (item.innerText || item.getAttribute('aria-label') || '').trim()
                })).filter(item => /oauth|authorize|continue|log[ -]?in|sign[ -]?in|github|google|apple|bluesky|atproto|discord|microsoft/i.test(`${item.href} ${item.text}`)).slice(0, 24)"""
            ),
            "service_actions": page.locator("a[href], button").evaluate_all(
                """els => els.map(item => ({
                    kind: item.tagName.toLowerCase(),
                    href: item.href || '',
                    text: (item.innerText || item.getAttribute('aria-label') || '').trim()
                })).filter(item => /post new entry|new entry|new post|create (entry|post)|write (entry|post)|compose|publish|upload/i.test(item.text)).slice(0, 24)"""
            ),
            "network_errors": network_errors,
            "console_errors": console_errors,
            "menu_expanded_count": menu_expanded_count,
            "transport_fallback": transport_fallback,
            "js_rendered": True,
        }
    finally:
        browser.close()


def playwright_navigate(url: str, *, timeout_ms: int = 30000) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("browser verification requires requirements-browser.txt") from exc
    with sync_playwright() as playwright:  # pragma: no cover - integration path
        try:
            return playwright_page_payload(playwright, url, timeout_ms=timeout_ms)
        except Exception as exc:
            if "ERR_SOCKET_NOT_CONNECTED" not in str(exc):
                raise
            return playwright_page_payload(
                playwright,
                url,
                timeout_ms=timeout_ms,
                browser_args=["--disable-http2"],
                transport_fallback="disable_http2_after_socket_not_connected",
            )
