from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import identifiers


DEFAULT_HEADERS = {
    "Accept": "application/json, text/html;q=0.9",
    "User-Agent": "atproto-survey-bot/0.3",
}
REQUEST_TIMEOUT_DEFAULT = 60.0
MAX_HTTP_REDIRECTS = 5


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirectHandler)


def outbound_host_is_blocked(host: str) -> bool:
    lowered = str(host or "").strip().lower().rstrip(".")
    if not lowered:
        return True
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return True
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(lowered, None)}
    except socket.gaierror:
        addresses = {lowered}
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            parsed.is_loopback
            or parsed.is_private
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_unspecified
            or parsed.is_reserved
            or parsed in ipaddress.ip_network("100.64.0.0/10")
        ):
            return True
    return False


def normalize_outbound_fetch_url(url: str) -> str:
    stripped = str(url or "").strip()
    try:
        parsed = urllib.parse.urlparse(stripped)
    except ValueError:
        return ""
    if not parsed.scheme and not parsed.netloc:
        try:
            parsed = urllib.parse.urlparse(f"https://{stripped}")
        except ValueError:
            return ""
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    return urllib.parse.urlunparse((scheme, netloc, path, "", parsed.query, ""))


def validate_outbound_fetch_url(url: str, *, allow_blocked_hosts: bool = False) -> str:
    normalized = normalize_outbound_fetch_url(url)
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"unsupported outbound URL: {url}")
    if not allow_blocked_hosts and outbound_host_is_blocked(parsed.hostname):
        raise ValueError(f"blocked internal outbound URL: {normalized}")
    return normalized


def request_bytes(
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: float = REQUEST_TIMEOUT_DEFAULT,
    allow_blocked_hosts: bool = False,
) -> tuple[int, bytes, dict[str, str]]:
    status, body, response_headers, _final_url = request_bytes_with_final_url(
        url,
        headers=headers,
        data=data,
        method=method,
        timeout=timeout,
        allow_blocked_hosts=allow_blocked_hosts,
    )
    return status, body, response_headers


def request_bytes_with_final_url(
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: float = REQUEST_TIMEOUT_DEFAULT,
    allow_blocked_hosts: bool = False,
) -> tuple[int, bytes, dict[str, str], str]:
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)
    current_url = validate_outbound_fetch_url(url, allow_blocked_hosts=allow_blocked_hosts)
    for _ in range(MAX_HTTP_REDIRECTS + 1):
        req = urllib.request.Request(current_url, headers=merged_headers, data=data, method=method)
        try:
            response_context = NO_REDIRECT_OPENER.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            status = int(exc.code or 0)
            if status in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location", "") if exc.headers else ""
                if not location:
                    body = exc.read()
                    exc.close()
                    response_headers = {key.lower(): value for key, value in exc.headers.items()} if exc.headers else {}
                    return status, body, response_headers, current_url
                try:
                    next_url = validate_outbound_fetch_url(
                        urllib.parse.urljoin(current_url, location),
                        allow_blocked_hosts=allow_blocked_hosts,
                    )
                finally:
                    exc.close()
                current_url = next_url
                if status == 303:
                    data = None
                    method = "GET"
                continue
            raise
        with response_context as response:
            status = getattr(response, "status", 200)
            body = response.read()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            final_url = identifiers.normalize_url(str(response.geturl() or current_url))
            return status, body, response_headers, final_url
    raise ValueError(f"too many redirects for outbound URL: {url}")


def request_json(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    timeout: float = REQUEST_TIMEOUT_DEFAULT,
) -> Any:
    _, body, _ = request_bytes(url, headers=headers, timeout=timeout)
    return json.loads(body.decode("utf-8"))


def request_json_post(
    url: str,
    payload: Any,
    headers: dict[str, str] | None = None,
    *,
    timeout: float = REQUEST_TIMEOUT_DEFAULT,
) -> Any:
    merged_headers = {"Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)
    _, body, _ = request_bytes(
        url,
        headers=merged_headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        timeout=timeout,
    )
    return json.loads(body.decode("utf-8"))


def request_json_authenticated(
    url: str,
    token: str,
    headers: dict[str, str] | None = None,
    *,
    timeout: float = REQUEST_TIMEOUT_DEFAULT,
) -> Any:
    merged_headers = {"Authorization": f"Bearer {token}"}
    if headers:
        merged_headers.update(headers)
    return request_json(url, headers=merged_headers, timeout=timeout)
