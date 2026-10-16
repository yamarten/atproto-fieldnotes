from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .html_link_context import extract_link_contexts
from .evidence import EvidenceRecord
from .relations import relation_record


POLICY_VERSION = "official-account-v1"
EXTERNAL_CONTEXT = re.compile(
    r"\b(?:testimonial[s]?|quot(?:e[sd]?|ation[s]?)|post[s]?|feed[s]?|comment[s]?|"
    r"author[s]?|team|member[s]?|contributor[s]?|sponsor[s]?|credit[s]?|customer[s]?|"
    r"user[s]?|review[s]?|related|partner[s]?|integration[s]?|example[s]?)\b|利用者|引用|寄稿|開発者|作者|推薦",
    re.IGNORECASE,
)
CREDIT_TEXT = re.compile(r"\b(?:built|made|created|developed|powered)\s+(?:with\s+.*?)?by\b", re.IGNORECASE)
SELF_LABEL = re.compile(
    r"^(?:(?:follow|find|join|contact)\s+us\s+(?:on\s+)?bluesky|"
    r"our\s+bluesky(?:\s+(?:account|profile))?|"
    r"our\s+official\s+bluesky(?:\s+(?:account|profile))?)$",
    re.IGNORECASE,
)
SOCIAL_HINT = re.compile(r"\b(?:social[s]?|sociallinks|socialicons)\b", re.IGNORECASE)
GENERIC_LABEL = re.compile(
    r"^(?:(?:bluesky\s*){1,2}|bluesky\s+(?:butterfly\s+)?(?:logo|icon)|"
    r"(?:follow|find)\s+(?:on\s+)?bluesky|official\s+bluesky(?:\s+(?:account|profile))?|"
    r"bluesky\s+(?:公式アカウント|公式)|公式\s*bluesky(?:アカウント)?)$", re.IGNORECASE,
)


def bluesky_profile_url(value: str) -> str:
    """Accept an account endpoint only; never derive one from a post/feed URL."""
    try:
        parsed = urllib.parse.urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or parsed.netloc.lower() not in {"bsky.app", "staging.bsky.app"}:
        return ""
    path = parsed.path.rstrip("/")
    if not re.fullmatch(r"/profile/[A-Za-z0-9.:-]+", urllib.parse.unquote(path)):
        return ""
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def _account_target(locator: str) -> tuple[str, str] | None:
    url = bluesky_profile_url(locator)
    return (url, url) if url else None


def account_link_reason(observation: dict[str, Any], requested_url: str) -> str:
    tags = set(observation.get("ancestor_tags") or [])
    hints = " ".join(observation.get("ancestor_hints") or [])
    heading = str(observation.get("nearest_heading") or "")
    if tags.intersection({"article", "blockquote", "figure"}) or EXTERNAL_CONTEXT.search(f"{hints} {heading}"):
        return ""
    local_texts = [str(observation.get(key) or "") for key in ("container_text", "group_text") if observation.get(key)]
    local_text = min(local_texts, key=len) if local_texts else ""
    if CREDIT_TEXT.search(local_text):
        return ""
    labels = [re.sub(r"\s+", " ", str(observation.get(key) or "")).strip() for key in
              ("text", "aria_label", "title", "descendant_label")]
    if "me" in str(observation.get("rel") or "").lower().split():
        return "rel_me"
    if any(SELF_LABEL.fullmatch(label) for label in labels if label):
        return "explicit_self_account"
    social_navigation = bool(tags.intersection({"footer", "header", "nav"}) or SOCIAL_HINT.search(hints))
    if social_navigation and any(GENERIC_LABEL.fullmatch(label) for label in labels if label):
        return "social_navigation"
    site_host = (urllib.parse.urlsplit(requested_url).hostname or "").removeprefix("www.")
    account = str(observation.get("account_url") or "").rsplit("/", 1)[-1]
    if social_navigation and site_host and account.lower() == site_host.lower() and any(
        label.lower() in {account.lower(), f"@{account.lower()}"} for label in labels
    ):
        return "site_handle_navigation"
    return ""


def official_account_relation_records(
    requested_url: str, html: str, *, observed_at: str,
) -> list[EvidenceRecord]:
    by_account: dict[str, list[dict[str, Any]]] = {}
    for observation in extract_link_contexts(html, requested_url, target=_account_target, kind="account"):
        reason = account_link_reason(observation, requested_url)
        if not reason:
            continue
        context = {key: observation[key] for key in (
            "locator", "text", "aria_label", "title", "descendant_label", "rel",
            "nearest_heading", "ancestor_tags", "ancestor_hints", "container_text", "group_text",
        ) if observation.get(key)}
        by_account.setdefault(observation["account_url"], []).append({**context, "reason": reason})
    return [relation_record(
        source=f"service_page:{requested_url}", classification="confirmed", observed_at=observed_at,
        subject_type="website", subject_value=requested_url.rstrip("/"), predicate="official_account",
        object_type="account", object_value=url, locator=requested_url, method="bounded_first_party_link",
        context={"policy_version": POLICY_VERSION, "observations": observations[:8]},
    ) for url, observations in by_account.items()]
