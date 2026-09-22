"""Find review passages, without inferring actual usage from words or scopes."""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any

from . import oauth_metadata, repository_analysis

VERSION = "usage-passages-v1"
MAX_HTML_CHARS = 1024 * 1024
MAX_PASSAGES = 4
PASSAGE_CHARS = 800
PROTOCOL_TERMS = re.compile(
    r"\bpersonal data repositor(?:y|ies)\b|\bPDS\b|\bat[ -]?proto(?:col)?\b|"
    r"\bBluesky\b|\bJetstream\b|\bfirehose\b|個人データリポジトリ|ATプロトコル",
    re.IGNORECASE | re.ASCII,
)


class _PageText(HTMLParser):
    """Use the standard HTML parser; do not execute scripts or interpret CSS."""
    ignored = {"script", "style", "template", "noscript", "svg", "head"}
    blocks = {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "br", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.ignored:
            self.skip.append(tag)
        if not self.skip and tag in self.blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag in self.blocks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def page_text(html: str) -> str:
    parser = _PageText()
    parser.feed(html[:MAX_HTML_CHARS])
    parser.close()
    return "\n".join(line for part in "".join(parser.parts).splitlines()
                     if (line := " ".join(part.split())))


def _collection_pattern(collection: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9_.-])" + re.escape(collection) + r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])")


def passages(text: str, collections: list[str] | None = None) -> dict[str, Any]:
    """Retain exact contiguous text around literal matches, including negation."""
    matches = [(match.start(), match.end(), match.group(),
                3 if re.search(r"pds|repositor|個人", match.group(), re.IGNORECASE) else
                1 if match.group().lower() == "bluesky" else 2)
               for match in PROTOCOL_TERMS.finditer(text)]
    for collection in collections or []:
        pattern = _collection_pattern(collection)
        matches.extend((m.start(), m.end(), collection, 4) for m in pattern.finditer(text))
    selected: list[tuple[int, int]] = []
    for start, end, _, _priority in sorted(matches, key=lambda m: (-m[3], m[0])):
        if any(left <= start and end <= right for left, right in selected):
            continue
        left = max(0, start - 260)
        right = min(len(text), max(end, left + PASSAGE_CHARS))
        selected.append((left, right))
        if len(selected) == MAX_PASSAGES:
            break
    return {
        "version": VERSION, "text_chars": len(text),
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "match_count": len(matches),
        "passages": [{"start": left, "end": right, "text": text[left:right],
                      "matches": sorted({term for start, end, term, _ in matches if left <= start and end <= right}),
                      "before_omitted": left > 0, "after_omitted": right < len(text)}
                     for left, right in sorted(selected)],
        "matches_omitted": any(not any(left <= start and end <= right for left, right in selected)
                               for start, end, _, _ in matches),
    }


def from_html(html: str) -> dict[str, Any]:
    return {**passages(page_text(html)), "input_truncated": len(html) > MAX_HTML_CHARS,
            "format": "normalized_html_text"}


def review(project: dict[str, Any]) -> dict[str, Any]:
    """A read-only view; absence of a passage is not absence of protocol use."""
    collections = sorted({collection
                          for client in oauth_metadata.normalize_results(project.get("oauth_client_metadata"))
                          for permission in client["analysis"].get("permissions", []) if permission.get("kind") == "repo"
                          for collection in permission.get("collection", []) if collection != "*"})
    sources: list[dict[str, Any]] = []

    def add(locator: str, kind: str, observation: dict[str, Any], material: dict[str, Any], coverage: str) -> None:
        sources.append({"locator": locator, "kind": kind, "observed_at": observation.get("observed_at", ""),
                        "coverage": coverage, **material})

    for item in project.get("service_verification_results", []):
        if not item.get("supports_project") or item.get("fetch_error"):
            continue
        url = str(item.get("requested_url") or "")
        if item.get("lane") == "browser":
            for page in item.get("pages", []):
                text = str(page.get("text_extract") or "")
                if text:
                    add(str(page.get("url") or url), "browser", item, passages(text, collections), "saved_excerpt")
        elif 0 < int(item.get("status_code") or 0) < 400:
            description = str(item.get("description") or "")
            if description:
                add(url, "service_description", item, passages(description, collections), "saved_description")
            material = item.get("atproto_text_candidates")
            if isinstance(material, dict):
                add(url, "service", item, material, "collected_html")
            elif item.get("html_extract"):
                add(url, "service", item, from_html(str(item["html_extract"])), "saved_html_excerpt")
    for result in repository_analysis.select_for_triage(project):
        if result.get("stale"):
            continue
        for excerpt in result.get("evidence_excerpts", []):
            if "readme" not in str(excerpt.get("path") or "").lower():
                continue
            text = str(excerpt.get("excerpt") or "")
            locator = f"{result['repo_url']}@{result.get('source_revision', '')}#{excerpt.get('path')}:{excerpt.get('line', '')}"
            add(locator, "repository", result, passages(text, collections), "saved_readme_excerpt")
            sources[-1]["url"] = result["repo_url"]
    observed = len(sources)
    matched = [source for source in sources if source.get("passages")]
    # Match references are literal co-occurrence, never a semantic association.
    for source in matched:
        source["literal_collections"] = [name for name in collections if any(
            _collection_pattern(name).search(p["text"])
            for p in source["passages"])]
    text_sources = sum(bool(source.get("text_chars")) for source in sources)
    return {"sources": matched[:12], "sources_observed": observed, "text_sources_observed": text_sources,
            "sources_with_matches": len(matched),
            "sources_omitted": max(0, len(matched) - 12), "requested_collections": collections,
            "status": "matches" if matched else "no_matches" if text_sources else "text_unavailable" if observed else "not_observed"}
