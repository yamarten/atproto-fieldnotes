from __future__ import annotations

import time
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.25
    max_delay: float = 2.0


def retryable_error(error: BaseException) -> bool:
    status = int(getattr(error, "code", 0) or 0)
    lowered = str(error).lower()
    return (
        isinstance(error, (TimeoutError, urllib.error.URLError))
        or status in {429}
        or status >= 500
        or "timed out" in lowered
        or "timeout" in lowered
    )


def retry_after_seconds(error: BaseException) -> float | None:
    headers = getattr(error, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After")
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def run_with_retry(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy = RetryPolicy(),
    sleep: Callable[[float], None] = time.sleep,
    metrics: dict[str, Any] | None = None,
) -> tuple[T, int]:
    attempts = 0
    while True:
        attempts += 1
        if metrics is not None:
            metrics["attempts"] = attempts
        try:
            return operation(), attempts
        except Exception as exc:
            if attempts >= max(1, policy.max_attempts) or not retryable_error(exc):
                raise
            retry_after = retry_after_seconds(exc)
            delay = retry_after if retry_after is not None else min(
                policy.max_delay,
                policy.base_delay * (2 ** (attempts - 1)),
            )
            started = time.perf_counter()
            try:
                sleep(delay)
            finally:
                if metrics is not None:
                    metrics["retry_wait_ms"] = metrics.get("retry_wait_ms", 0) + round((time.perf_counter() - started) * 1000, 3)


class TTLCache:
    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._values.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= self._now():
            self._values.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, *, ttl: float) -> None:
        self._values[key] = (self._now() + max(0.0, ttl), value)


@dataclass
class RequestBudget:
    limit: int
    used: int = 0

    def consume(self, count: int = 1) -> bool:
        if self.used + count > self.limit:
            return False
        self.used += count
        return True
