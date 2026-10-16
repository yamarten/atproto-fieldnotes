from __future__ import annotations

import re
import urllib.parse
from typing import Any, Mapping


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


NSID_AUTHORITY_SEGMENT_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
NSID_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
NSID_TEXT_TOKEN_RE = re.compile(r"[\w.-]+")
ATPROTO_HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", re.IGNORECASE)
DID_RE = re.compile(r"^did:[a-z0-9]+:[A-Za-z0-9._:%-]+$", re.IGNORECASE)


def normalize_atproto_account_identifier(value: str) -> tuple[str, str] | None:
    """Normalize one observed identifier without resolving handle/DID identity."""
    text = str(value or "").strip()
    if DID_RE.fullmatch(text):
        return "did", text
    handle = text.rstrip(".").lower()
    if ATPROTO_HANDLE_RE.fullmatch(handle):
        return "handle", handle
    return None


def normalize_atproto_account(item: Mapping[str, Any]) -> dict[str, str]:
    handle = str(item.get("handle") or "").strip()
    did = str(item.get("did") or "").strip()
    source = str(item.get("source") or "").strip()
    if DID_RE.fullmatch(handle):
        if not did or did == handle:
            did = handle
        handle = ""
    output: dict[str, str] = {}
    for kind, value in (("handle", handle), ("did", did)):
        normalized = normalize_atproto_account_identifier(value)
        if normalized and normalized[0] == kind:
            output[kind] = normalized[1]
    if output and source:
        output["source"] = source
    return output


def extract_bluesky_profile_subject(url: str, *, allow_subpaths: bool = False) -> dict[str, str]:
    """Read the identifier written in a profile URL; subpaths require opt-in."""
    normalized = normalize_url(url)
    parsed = urllib.parse.urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or parsed.netloc not in {"bsky.app", "staging.bsky.app"}:
        return {}
    pattern = r"/profile/([^/]+)(?:/.*)?" if allow_subpaths else r"/profile/([^/]+)/?"
    match = re.fullmatch(pattern, parsed.path)
    if not match:
        return {}
    subject = normalize_atproto_account_identifier(urllib.parse.unquote(match.group(1)))
    return {subject[0]: subject[1]} if subject else {}


def normalize_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
        scheme = (parsed.scheme or "https").lower()
        port = parsed.port if scheme in {"http", "https"} else None
    except ValueError:
        return ""
    userinfo, separator, authority = parsed.netloc.rpartition("@")
    host = authority if separator else parsed.netloc
    if scheme in {"http", "https"}:
        host = host.lower()
    if (scheme, port) in {("http", 80), ("https", 443)}:
        host = host.rsplit(":", 1)[0]
    if separator:
        host = userinfo + "@" + host
    if scheme in {"http", "https"} and not host:
        return ""
    # An empty HTTP path and '/' name the same resource. Other trailing
    # slashes, query parameters, and fragments can identify different targets.
    path = "" if parsed.path == "/" and scheme in {"http", "https"} else parsed.path
    return urllib.parse.urlunparse((scheme, host, path, parsed.params, parsed.query, parsed.fragment))


def sanitize_extracted_url(raw_url: str) -> str:
    value = str(raw_url or "").strip().strip("<>[]{}\"'")
    if "${" in value or "`" in value:
        return ""
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


def is_valid_nsid(nsid: str) -> bool:
    value = normalize_nsid(nsid)
    if not value or len(value) > 317:
        return False
    segments = value.split(".")
    if len(segments) < 3 or any(not segment or len(segment) > 63 for segment in segments):
        return False
    authority, name = segments[:-1], segments[-1]
    return bool(
        len(".".join(authority)) <= 253
        and authority[0][0].isascii() and authority[0][0].isalpha()
        and all(NSID_AUTHORITY_SEGMENT_RE.fullmatch(segment) for segment in authority)
        and NSID_NAME_RE.fullmatch(name)
    )


def extract_nsids_from_text(text: str) -> list[str]:
    # Validate whole dotted tokens: splitting at '_' can turn an invalid token
    # (including a JWT) into a valid-looking NSID suffix. A final prose period
    # is punctuation, while internal empty segments remain invalid.
    candidates = (match.group().rstrip(".") for match in NSID_TEXT_TOKEN_RE.finditer(text))
    return list(dict.fromkeys(candidate for candidate in candidates if is_valid_nsid(candidate)))


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


def extract_urls(html: str) -> list[str]:
    pattern = r'https?://[^\s"\'<>]+'
    output: list[str] = []
    for raw in re.findall(pattern, html):
        cleaned = sanitize_extracted_url(raw)
        parsed = urllib.parse.urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        output.append(normalize_url(cleaned))
    for raw in re.findall(r'\]\((https?://[^)\s]+)\)', html):
        cleaned = sanitize_extracted_url(raw)
        parsed = urllib.parse.urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        output.append(normalize_url(cleaned))
    return list(dict.fromkeys(value for value in output if value))
