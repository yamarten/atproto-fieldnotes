from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any

from . import identifiers, repo_urls


TEXT_LIMIT = 1000
BLOCK_TAGS = {"address", "article", "aside", "div", "figcaption", "li", "p", "section", "td"}
GROUP_TAGS = {"article", "aside", "footer", "header", "main", "nav", "ol", "section", "table", "ul"}
LANDMARK_TAGS = {"article", "aside", "footer", "header", "main", "nav", "section"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _text(value: str, limit: int = TEXT_LIMIT) -> str:
    return unescape(re.sub(r"\s+", " ", str(value or "")).strip())[:limit]


@dataclass
class _Frame:
    tag: str
    hint: str = ""
    heading: str = ""
    text_parts: list[str] = field(default_factory=list)
    link_indexes: list[int] = field(default_factory=list)

    def text(self) -> str:
        return _text(" ".join(self.text_parts))


class RepositoryLinkContextParser(HTMLParser):
    """Extract bounded local context for links to supported repository hosts.

    It intentionally keeps short semantic excerpts rather than the full page or a
    serialized DOM so the same bounded observation can be saved and replayed.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = identifiers.normalize_url(base_url)
        self.stack: list[_Frame] = []
        self.links: list[dict[str, Any]] = []
        self.current_anchor: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attr_map = {str(key).lower(): str(value or "") for key, value in attrs}
        hint = _text(" ".join(filter(None, (attr_map.get("id"), attr_map.get("class")))), 300)
        self.stack.append(_Frame(tag=lowered, hint=hint))
        if self.current_anchor is not None and lowered in {"img", "svg"}:
            descendant_label = attr_map.get("alt") or attr_map.get("aria-label") or attr_map.get("title")
            if descendant_label:
                self.current_anchor["descendant_label_parts"].append(_text(descendant_label, 300))
        if lowered != "a" or self.current_anchor is not None:
            return
        href = attr_map.get("href", "").strip()
        if not href:
            return
        locator = identifiers.normalize_url(urllib.parse.urljoin(self.base_url, href))
        if not repo_urls.is_supported_repo_url(locator):
            return
        canonical = repo_urls.canonical_repo_url(locator)
        if not canonical:
            return
        self.current_anchor = {
            "locator": locator,
            "repository_url": canonical,
            "repository_identity": repo_urls.repo_identity_key(canonical),
            "text_parts": [],
            "aria_label": _text(attr_map.get("aria-label", ""), 300),
            "title": _text(attr_map.get("title", ""), 300),
            "descendant_label_parts": [],
            "nearest_heading": next((frame.heading for frame in reversed(self.stack) if frame.heading), ""),
            "ancestor_tags": [frame.tag for frame in self.stack],
            "landmarks": [frame.tag for frame in self.stack if frame.tag in LANDMARK_TAGS],
            "container_text": "",
            "container_hint": "",
            "group_text": "",
            "group_hint": "",
            "group_repository_count": 0,
        }

    def handle_data(self, data: str) -> None:
        cleaned = _text(data)
        if not cleaned:
            return
        for frame in self.stack:
            if sum(len(item) for item in frame.text_parts) < TEXT_LIMIT * 2:
                frame.text_parts.append(cleaned)
        if self.current_anchor is not None:
            self.current_anchor["text_parts"].append(cleaned)

    def _finish_anchor(self) -> None:
        if self.current_anchor is None:
            return
        link = dict(self.current_anchor)
        link["text"] = _text(" ".join(link.pop("text_parts", [])), 300)
        link["descendant_label"] = _text(" ".join(link.pop("descendant_label_parts", [])), 300)
        index = len(self.links)
        self.links.append(link)
        for frame in self.stack:
            frame.link_indexes.append(index)
        self.current_anchor = None

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "a":
            self._finish_anchor()
        if not self.stack:
            return
        match = next((index for index in range(len(self.stack) - 1, -1, -1) if self.stack[index].tag == lowered), -1)
        if match < 0:
            return
        closing = self.stack[match:]
        del self.stack[match:]
        for frame in reversed(closing):
            frame_text = frame.text()
            if frame.tag in HEADING_TAGS and frame_text:
                for parent in reversed(self.stack):
                    if parent.tag in GROUP_TAGS or parent.tag in BLOCK_TAGS:
                        parent.heading = frame_text[:300]
                        break
            if frame.tag in BLOCK_TAGS:
                for index in frame.link_indexes:
                    if not self.links[index]["container_text"] and frame_text:
                        self.links[index]["container_text"] = frame_text
                        self.links[index]["container_hint"] = frame.hint
            if frame.tag in GROUP_TAGS:
                repositories = {
                    self.links[index]["repository_identity"]
                    for index in frame.link_indexes
                    if index < len(self.links)
                }
                for index in frame.link_indexes:
                    if self.links[index]["group_repository_count"] == 0:
                        self.links[index]["group_repository_count"] = len(repositories)
                        self.links[index]["group_text"] = frame_text
                        self.links[index]["group_hint"] = frame.hint

    def close(self) -> None:
        super().close()
        self._finish_anchor()
        repository_count = len({item["repository_identity"] for item in self.links})
        for item in self.links:
            item["page_repository_count"] = repository_count


def extract_repository_link_contexts(html: str, base_url: str) -> list[dict[str, Any]]:
    parser = RepositoryLinkContextParser(base_url)
    parser.feed(str(html or ""))
    parser.close()
    seen: set[tuple[Any, ...]] = set()
    output: list[dict[str, Any]] = []
    for item in parser.links:
        key = (
            item["locator"], item["text"], item["aria_label"], item["title"],
            item["descendant_label"], item["nearest_heading"], item["container_text"],
            tuple(item["ancestor_tags"]), tuple(item["landmarks"]),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output
