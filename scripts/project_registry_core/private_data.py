"""Credential removal shared by private evaluation snapshots."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
INLINE_SECRET = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;&]+")
SECRET_KEYS = {"authorization", "cookie", "cookies", "set_cookie", "password", "secret", "api_key", "access_token", "refresh_token", "private_key"}


def redact(value: Any, at: str, changed: list[str], *, preserve_url_details: bool = False) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string JSON key at {at}")
            path = f"{at}.{key}"
            lowered = key.lower().replace("-", "_")
            if lowered in SECRET_KEYS or lowered.endswith(("_api_key", "_password", "_secret", "_token")):
                result[key] = "[redacted]"
                if item != "[redacted]":
                    changed.append(path)
            else:
                result[key] = redact(item, path, changed, preserve_url_details=preserve_url_details)
        return result
    if isinstance(value, list):
        return [redact(item, f"{at}[{index}]", changed, preserve_url_details=preserve_url_details) for index, item in enumerate(value)]
    if not isinstance(value, str):
        return value

    def clean_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        trailing = raw[len(raw.rstrip(".,;!?)")):]
        raw = raw.rstrip(".,;!?)")
        try:
            parsed = urlsplit(raw)
            if not parsed.hostname:
                return match.group(0)
            query, fragment = "", ""
            if preserve_url_details:
                query, fragment = parsed.query, parsed.fragment
                def secret_parameter(key: str) -> bool:
                    lowered = key.lower().replace("-", "_")
                    return lowered in SECRET_KEYS | {"token", "auth", "signature", "sig", "key"} or lowered.endswith(("_token", "_signature", "_credential"))
                def clean_parameters(text: str) -> str:
                    pairs = parse_qsl(text, keep_blank_values=True)
                    if any(secret_parameter(key) for key, _ in pairs):
                        return urlencode([(key, "[redacted]" if secret_parameter(key) else item) for key, item in pairs])
                    return text
                query, fragment = clean_parameters(query), clean_parameters(fragment)
                if not parsed.username and not parsed.password and query == parsed.query and fragment == parsed.fragment:
                    return match.group(0)
            host = parsed.hostname
            if ":" in host:
                host = f"[{host}]"
            if parsed.port:
                host = f"{host}:{parsed.port}"
            cleaned = urlunsplit((parsed.scheme, host, parsed.path, query, fragment))
            return cleaned + trailing
        except ValueError:
            return match.group(0)

    result = URL.sub(clean_url, value)
    result = BEARER.sub("Bearer [redacted]", result)
    result = INLINE_SECRET.sub(lambda match: match.group(1) + "=[redacted]", result)
    if result != value:
        changed.append(at)
    return result
