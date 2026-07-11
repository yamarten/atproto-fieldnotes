from __future__ import annotations

import re
import urllib.parse


GENERIC_ALIAS_TOKENS = {
    "post",
    "tool",
    "app",
    "site",
    "feed",
    "project",
    "service",
    "item",
    "comment",
    "document",
    "atproto",
    "bluesky",
    "tangled",
    "whtwnd",
}


def normalize_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except ValueError:
        return ""
    scheme = (parsed.scheme or "https").lower()
    host = parsed.netloc.lower()
    if scheme in {"http", "https"} and not host:
        return ""
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunparse((scheme, host, path, "", "", ""))


def sanitize_extracted_url(raw_url: str) -> str:
    value = str(raw_url or "").strip().strip("<>[]{}\"'")
    for marker in ("](", ")("):
        if marker in value:
            value = value.split(marker, 1)[0].rstrip()
    while value.endswith(("**", "__")):
        value = value[:-2].rstrip()
    while value.count("(") < value.count(")") and value.endswith(")"):
        value = value[:-1].rstrip()
    while value.endswith((".", ",", ";", ":", "!", "?", "]", "}", ">")):
        value = value[:-1].rstrip()
    return value


def normalize_nsid(nsid: str) -> str:
    value = nsid.strip()
    if value.startswith("at://"):
        value = value.removeprefix("at://")
    value = value.split("#", 1)[0]
    return value.strip("/")


def derive_authority_domain(nsid: str) -> str:
    parts = nsid.split(".")
    if len(parts) < 3:
        return ""
    authority = parts[:-1]
    return ".".join(reversed(authority))


def derive_authority_domain_from_namespace(namespace: str) -> str:
    parts = [part for part in normalize_nsid(namespace).split(".") if part]
    if len(parts) < 2:
        return ""
    return ".".join(reversed(parts))


def normalize_alias_token(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    if len(lowered) < 4 or lowered in GENERIC_ALIAS_TOKENS:
        return ""
    return lowered
