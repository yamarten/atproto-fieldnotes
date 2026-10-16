"""Bounded observations that support the developer role, never account adoption."""
from __future__ import annotations

import re
from typing import Any, Iterable

from . import account_links
from .html_link_context import extract_link_contexts
from .identity import identifiers


POLICY_VERSION = "developer-account-v1"
MAX_ACCOUNTS = 32
MAX_WITNESSES = 4
EXTERNAL_CONTEXT = re.compile(
    r"\b(?:testimonials?|quotes?|customers?|users?|sponsors?|partners?|examples?|"
    r"tutorials?|feeds?|posts?|blog|related|dependencies)\b|利用者|引用|導入事例|例示|サンプル",
    re.IGNORECASE,
)
DEVELOPER_ROLE = re.compile(r"\b(?:developers?|maintainers?|development team)\b|開発者|開発チーム|メンテナ|保守担当", re.IGNORECASE)
CREDIT = re.compile(r"\b(?:built|made|created|developed|maintained)\s+by\b|(?:開発|制作|保守)\s*[:：]|(?:が開発|が制作)", re.IGNORECASE)
NEGATION = re.compile(r"\b(?:not|former|formerly|previously)\b|無関係|元開発者|開発者では", re.IGNORECASE)


def link_reason(observation: dict[str, Any]) -> str:
    if set(observation.get("ancestor_tags") or []).intersection({"blockquote", "figure", "script", "template"}):
        return ""
    hints = " ".join(observation.get("ancestor_hints") or [])
    heading = str(observation.get("nearest_heading") or "")
    if EXTERNAL_CONTEXT.search(hints + " " + heading):
        return ""
    container = str(observation.get("container_text") or "")
    text = container or str(observation.get("group_text") or "")
    count = observation.get("container_account_count") if container else observation.get("group_account_count")
    if NEGATION.search(text):
        return ""
    labels = " ".join(str(observation.get(key) or "") for key in ("text", "aria_label", "title", "descendant_label"))
    if any(account_links.SELF_LABEL.fullmatch(str(observation.get(key) or "").strip()) for key in ("text", "aria_label", "title")):
        return ""
    if CREDIT.search(labels):
        return "explicit_developer_credit"
    if count != 1:
        return ""
    if CREDIT.search(text):
        return "local_developer_credit"
    if DEVELOPER_ROLE.search(heading) or DEVELOPER_ROLE.search(text):
        return "local_developer_role"
    return ""


def observe_page(url: str, html: str) -> dict[str, Any]:
    accounts: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for observation in extract_link_contexts(html, url, target=account_links.account_link_target, kind="account"):
        reason = link_reason(observation)
        if not reason:
            continue
        kind, value = next(iter(identifiers.extract_bluesky_profile_subject(observation["account_url"]).items()))
        witness = {key: observation[key] for key in (
            "locator", "text", "aria_label", "title", "nearest_heading", "container_text", "group_text", "ancestor_hints"
        ) if observation.get(key)}
        accounts.setdefault((kind, value), []).append({**witness, "reason": reason})
    return {
        "policy_version": POLICY_VERSION, "complete": len(accounts) <= MAX_ACCOUNTS,
        "account_count": len(accounts),
        "accounts": [{"kind": kind, "value": value, "observations": witnesses[:MAX_WITNESSES]}
                     for (kind, value), witnesses in list(accounts.items())[:MAX_ACCOUNTS]],
    }


def profile_claim(description: str, project_names: Iterable[str]) -> str:
    """An explicit self-claim about this project supports a candidate only."""
    text = str(description or "")[:2000]
    if NEGATION.search(text):
        return ""
    for name in project_names:
        if len(re.sub(r"\W", "", name)) < 4 or name.lower() in {"app", "application", "project", "service", "unknown"}:
            continue
        subject = re.escape(name)
        if re.search(rf"\b(?:developer|maintainer|creator|author)\s+(?:of|for)\s+(?:the\s+)?{subject}(?![\w.-])", text, re.IGNORECASE):
            return "profile_developer_claim"
        if re.search(rf"{subject}(?:の|を)(?:開発|制作|保守)", text):
            return "profile_developer_claim"
    return ""
