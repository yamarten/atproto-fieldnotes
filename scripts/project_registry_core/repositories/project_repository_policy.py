from __future__ import annotations

import collections
import re
from typing import Any

from ..identity import identifiers
from . import repository_link_context


POLICY_VERSION = "project-repository-link-v2"
EXPLICIT_SOURCE_LABELS = {
    "code", "code repository", "github repository", "repo", "repository", "source",
    "source code", "source repo", "source repository", "tangled repository",
    "view code", "view source", "view source code",
}
REPOSITORY_PROVIDER_PATTERN = r"(?:github|tangled|codeberg|gitlab)"
EXPLICIT_PROJECT_REPOSITORY_LABEL_PATTERN = re.compile(
    rf"(?:\bview (?:the )?(?:source|code)(?: code)?(?: on {REPOSITORY_PROVIDER_PATTERN})?\b|"
    rf"\bopen source(?: on {REPOSITORY_PROVIDER_PATTERN})?\b|"
    rf"\b{REPOSITORY_PROVIDER_PATTERN} repository\b)"
)
SOURCE_ONLY_PRESENTATION_PATTERN = re.compile(
    r"^(?:source(?: code)?|code|repo|repository)"
    r"(?: (?:source(?: code)?|code|repo|repository))?"
    r"(?: opens? in (?:a )?new tab| external link)?$"
)
NON_SOURCE_LABEL_WORDS = {
    "backer", "backers", "donate", "donation", "fund", "funding", "patreon",
    "sponsor", "sponsors", "sponsorship",
}
ISSUE_AND_COMMUNITY_LABEL_PATTERN = re.compile(
    r"(?:advisory|bug|contribut|discussion|feedback|feature request|issue|questions?)"
)
LICENSE_LABEL_PATTERN = re.compile(r"(?:^|\s)(?:agpl|apache|gpl|license|mit)(?:$|\s|v?\d)")
RELEASE_LABEL_PATTERN = re.compile(r"(?:download|release|release notes|releases page|^v\d+(?:\s|$))")
DOCUMENTATION_SOURCE_LABEL_PATTERN = re.compile(r"(?:edit (?:this page|on tangled)|site contents)")
SCHEMA_REFERENCE_LABEL_PATTERN = re.compile(r"(?:data schemas?|lexicon definitions|lexicons|openapi specification)")
EXTERNAL_SCHEMA_REFERENCE_CONTEXT_PATTERN = re.compile(
    r"(?:uses? (?:the )?(?:official )?(?:lexicons?|schemas?)|"
    r"bundl(?:e|ed|es|ing) (?:the )?(?:official )?(?:lexicons?|schemas?))"
)
SELF_HOSTING_LABEL_PATTERN = re.compile(r"(?:host (?:a |an )?.+ server|run your own|self[ -]?host)")
EXTERNAL_REFERENCE_CONTEXT_PATTERN = re.compile(
    r"(?:awesome(?:\s+list)?|related (?:projects?|tools?|resources?)|"
    r"community projects?|projects? (?:implementing|using)\b|"
    r"(?:feature|submit|add|list) (?:your|a) projects?\b|"
    r"dependencies|dependency|built with|powered by|"
    r"acknowledg(?:e)?ments?|credits?|third[ -]party|ecosystem projects?)"
)
DIRECT_PROJECT_REPOSITORY_CONTEXT_PATTERN = re.compile(
    r"(?:source code|view (?:the )?(?:source|code)|"
    r"open source.{0,240}\bcode\b|\bcode\b.{0,240}(?:runs|powers) (?:this|the) (?:app|site|service))"
)


def _counter(values: Any) -> dict[str, int]:
    counter = values if isinstance(values, collections.Counter) else collections.Counter(values)
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda item: str(item[0]))}


def normalized_label(value: str) -> str:
    lowered = str(value or "").lower()
    lowered = re.sub(r"[↗→›»]+", " ", lowered)
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def classify_service_link_label(value: str) -> str:
    label = normalized_label(value)
    if not label:
        return "unlabeled"
    words = set(label.split())
    if words.intersection(NON_SOURCE_LABEL_WORDS) or re.search(r"(?:backer|donat|fund|patreon|sponsor)", label):
        return "non_source"
    if label == "open source" or label.startswith("open source "):
        return "ambiguous"
    if label in EXPLICIT_SOURCE_LABELS:
        return "explicit_source"
    if label in {"github", "tangled", "codeberg", "gitlab"}:
        return "provider_only"
    return "ambiguous"


def classify_relation_candidate_purpose(value: str) -> str:
    label = normalized_label(value)
    if not label:
        return "unlabeled"
    if classify_service_link_label(label) == "non_source":
        return "funding_or_support"
    if SELF_HOSTING_LABEL_PATTERN.search(label):
        return "self_hosting_implementation"
    if (
        label in EXPLICIT_SOURCE_LABELS
        or SOURCE_ONLY_PRESENTATION_PATTERN.fullmatch(label)
        or EXPLICIT_PROJECT_REPOSITORY_LABEL_PATTERN.search(label)
    ):
        return "explicit_source"
    if DOCUMENTATION_SOURCE_LABEL_PATTERN.search(label):
        return "documentation_source"
    if LICENSE_LABEL_PATTERN.search(label):
        return "license"
    if RELEASE_LABEL_PATTERN.search(label):
        return "release_or_download"
    if ISSUE_AND_COMMUNITY_LABEL_PATTERN.search(label):
        return "issue_or_community"
    if SCHEMA_REFERENCE_LABEL_PATTERN.search(label):
        return "schema_or_specification"
    if label in {"github", "tangled", "codeberg", "gitlab"}:
        return "provider_only"
    return "other_ambiguous"


def evaluate_project_repository_context(context: dict[str, Any]) -> dict[str, Any]:
    """Classify what one bounded project-page link says about its repository target."""
    label = " ".join(
        str(context.get(key) or "").strip()
        for key in ("text", "aria_label", "title", "descendant_label")
        if str(context.get(key) or "").strip()
    )
    purpose = classify_relation_candidate_purpose(label)
    surrounding = normalized_label(" ".join(
        str(context.get(key) or "")
        for key in ("nearest_heading", "container_text", "container_hint", "group_hint")
    ))
    if EXTERNAL_REFERENCE_CONTEXT_PATTERN.search(surrounding):
        return {"disposition": "DO_NOT_PROMOTE", "reason": "surrounding_context_identifies_external_reference", "purpose": purpose}
    if purpose == "schema_or_specification" and EXTERNAL_SCHEMA_REFERENCE_CONTEXT_PATTERN.search(surrounding):
        return {"disposition": "DO_NOT_PROMOTE", "reason": "surrounding_context_identifies_external_schema_reference", "purpose": purpose}
    if purpose == "funding_or_support":
        return {"disposition": "DO_NOT_PROMOTE", "reason": "link_identifies_funding_or_support", "purpose": purpose}
    if DIRECT_PROJECT_REPOSITORY_CONTEXT_PATTERN.search(surrounding):
        return {"disposition": "CONFIRM_PROJECT_REPOSITORY", "reason": "surrounding_context_directly_identifies_project_repository", "purpose": purpose}
    accepted_purposes = {
        "documentation_source", "explicit_source", "issue_or_community", "license",
        "release_or_download", "schema_or_specification", "self_hosting_implementation",
    }
    visibly_unlabeled = not str(context.get("text") or "").strip()
    ancestor_tags = {str(item) for item in context.get("ancestor_tags", []) if str(item)}
    if visibly_unlabeled:
        if (
            purpose in accepted_purposes.union({"provider_only", "unlabeled"})
            and int(context.get("page_repository_count") or 0) == 1
            and not ancestor_tags.intersection({"li", "ol", "ul"})
        ):
            return {"disposition": "CONFIRM_PROJECT_REPOSITORY", "reason": "visually_unlabeled_single_repository_link", "purpose": purpose}
        return {"disposition": "REVIEW_PROJECT_REPOSITORY", "reason": "visually_unlabeled_link_requires_context", "purpose": purpose}
    if purpose in accepted_purposes:
        return {"disposition": "CONFIRM_PROJECT_REPOSITORY", "reason": f"presentation_identifies_{purpose}", "purpose": purpose}
    landmarks = {str(item) for item in context.get("landmarks", []) if str(item)}
    if purpose in {"provider_only", "unlabeled"} and landmarks.intersection({"header", "footer", "nav"}):
        if int(context.get("page_repository_count") or 0) == 1:
            return {"disposition": "CONFIRM_PROJECT_REPOSITORY", "reason": "global_chrome_has_one_repository_target", "purpose": purpose}
    if int(context.get("group_repository_count") or 0) > 1:
        return {"disposition": "REVIEW_PROJECT_REPOSITORY", "reason": "link_is_in_multi_repository_group", "purpose": purpose}
    return {"disposition": "REVIEW_PROJECT_REPOSITORY", "reason": "local_context_does_not_determine_repository_role", "purpose": purpose}


def evaluate_project_repository_html(html: str, base_url: str) -> dict[str, Any]:
    contexts = repository_link_context.extract_repository_link_contexts(html, base_url)
    rows = [{**context, **evaluate_project_repository_context(context)} for context in contexts]
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        grouped[str(row["repository_identity"])].append(row)
    priority = {"CONFIRM_PROJECT_REPOSITORY": 3, "REVIEW_PROJECT_REPOSITORY": 2, "DO_NOT_PROMOTE": 1}
    pairs: list[dict[str, Any]] = []
    for repository_identity, observations in sorted(grouped.items()):
        disposition = max((str(item["disposition"]) for item in observations), key=lambda value: priority[value])
        pairs.append({
            "repository_url": str(observations[0]["repository_url"]),
            "repository_identity": repository_identity,
            "disposition": disposition,
            "observation_count": len(observations),
            "reasons": sorted({str(item["reason"]) for item in observations}),
            "locators": sorted({str(item["locator"]) for item in observations}),
        })
    return {
        "base_url": identifiers.normalize_url(base_url),
        "repository_link_count": len(rows),
        "rows": rows,
        "disposition_counts": _counter(row["disposition"] for row in rows),
        "repository_pair_count": len(pairs),
        "pairs": pairs,
        "pair_disposition_counts": _counter(pair["disposition"] for pair in pairs),
    }
