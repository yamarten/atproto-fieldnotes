from __future__ import annotations

import json
import threading
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import http_transport


RATE_LIMIT_HEADERS = {
    "x-ratelimit-limit": "limit",
    "x-ratelimit-remaining": "remaining",
    "x-ratelimit-used": "used",
    "x-ratelimit-reset": "reset",
    "x-ratelimit-resource": "resource",
    "retry-after": "retry_after",
}
INTEGER_RATE_LIMIT_FIELDS = {"limit", "remaining", "used", "reset"}


def rate_limit_observation(headers: Mapping[str, Any] | None) -> dict[str, int | str]:
    normalized = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    observation: dict[str, int | str] = {}
    for header, field_name in RATE_LIMIT_HEADERS.items():
        value = normalized.get(header, "").strip()
        if not value:
            continue
        if field_name in INTEGER_RATE_LIMIT_FIELDS:
            try:
                observation[field_name] = int(value)
                continue
            except ValueError:
                pass
        observation[field_name] = value
    return observation


def headers_indicate_rate_limit(status: int, headers: Mapping[str, Any] | None) -> bool:
    if status == 429:
        return True
    if status != 403:
        return False
    observation = rate_limit_observation(headers)
    return observation.get("remaining") == 0 or "retry_after" in observation


class GitHubRateLimitError(RuntimeError):
    rate_limited = True
    retryable = True

    def __init__(
        self,
        error: urllib.error.HTTPError,
        *,
        metrics: GitHubRequestMetrics | None = None,
    ) -> None:
        self.code = int(error.code or 0)
        self.headers = error.headers
        self.url = str(getattr(error, "url", "") or "")
        request_count, cache_hits, rate_limit = metrics.snapshot() if metrics is not None else (1, 0, {})
        self.request_count = request_count
        self.cache_hits = cache_hits
        self.rate_limit = rate_limit or rate_limit_observation(error.headers)
        super().__init__(f"GitHub API rate limit response ({self.code})")


@dataclass
class GitHubRequestMetrics:
    request_count: int = 0
    cache_hits: int = 0
    rate_limit: dict[str, int | str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def record_request(self) -> None:
        with self._lock:
            self.request_count += 1

    def record_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def record_headers(self, headers: Mapping[str, Any] | None) -> None:
        observation = rate_limit_observation(headers)
        if not observation:
            return
        with self._lock:
            self.rate_limit = observation

    def snapshot(self) -> tuple[int, int, dict[str, int | str]]:
        with self._lock:
            return self.request_count, self.cache_hits, dict(self.rate_limit)


def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = http_transport.REQUEST_TIMEOUT_DEFAULT,
    metrics: GitHubRequestMetrics | None = None,
) -> Any:
    if metrics is not None:
        metrics.record_request()
    try:
        _, body, response_headers = http_transport.request_bytes(
            url,
            headers=headers,
            timeout=timeout,
        )
    except urllib.error.HTTPError as exc:
        if metrics is not None:
            metrics.record_headers(exc.headers)
        if headers_indicate_rate_limit(int(exc.code or 0), exc.headers):
            raise GitHubRateLimitError(exc, metrics=metrics) from exc
        raise
    if metrics is not None:
        metrics.record_headers(response_headers)
    return json.loads(body.decode("utf-8"))
