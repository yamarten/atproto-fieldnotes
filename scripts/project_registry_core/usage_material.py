"""Find review passages, without inferring actual usage from words or scopes."""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any

from .identity import identifiers
from . import oauth_metadata
from .repositories import repository_analysis

VERSION = "usage-passages-v2"
MAX_COLLECTIONS = 64
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


def collection_evidence(project: dict[str, Any]) -> dict[str, Any]:
    """Join saved observations by exact NSID, without inferring writer or use."""
    rows: dict[str, dict[str, Any]] = {}

    def row(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, str) or not identifiers.is_valid_nsid(value) or identifiers.normalize_nsid(value) != value:
            return None
        return rows.setdefault(value, {"collection": value, "permissions": [], "schemas": [],
                                       "record_samples": [], "record_observations": []})

    for client in oauth_metadata.normalize_results(project.get("oauth_client_metadata")):
        for permission in client["analysis"].get("permissions", []):
            if permission.get("kind") != "repo":
                continue
            for name in permission.get("collection", []):
                entry = row(name)
                if entry is not None:
                    entry["permissions"].append({"metadata_url": client["metadata_url"],
                        "observed_at": client.get("observed_at", ""), "actions": list(permission.get("action", []))})
    for sample in project.get("lexicon_schema_samples", []):
        name = sample.get("nsid") or sample.get("id")
        if sample.get("id") and sample["id"] != name:
            continue  # A mismatched schema must not describe this collection.
        defs = sample.get("defs")
        main = defs.get("main") if isinstance(defs, dict) else None
        if not isinstance(main, dict) or main.get("type") != "record":
            continue  # Procedures, queries and permission sets are not collections.
        entry = row(name)
        if entry is None:
            continue
        record = main.get("record")
        properties = record.get("properties") if isinstance(record, dict) else None
        fields = [{"name": key, "type": str(value.get("type") or "") if isinstance(value, dict) else "",
                   "description": str(value.get("description") or "")[:500] if isinstance(value, dict) else ""}
                  for key, value in (properties.items() if isinstance(properties, dict) else []) if key != "..."]
        entry["schemas"].append({"id": name, "description": str(sample.get("description") or "")[:1000],
            "record_description": str(main.get("description") or "")[:1000], "key": str(main.get("key") or ""),
            "fields": fields[:16], "fields_omitted": max(0, len(fields) - 16),
            "fields_status": "saved" if isinstance(properties, dict) else "not_saved",
            "fields_compacted": isinstance(properties, dict) and "..." in properties,
            "observed_at": str(sample.get("observed_at") or "")})
    for sample in project.get("recent_record_samples", []):
        entry = row(sample.get("collection"))
        if entry is None:
            continue
        record = sample.get("record")
        record = record if isinstance(record, dict) else {}
        entry["record_samples"].append({"did": str(sample.get("did") or ""), "rkey": str(sample.get("rkey") or ""),
            "declared_type": str(record.get("$type") or ""),
            "fields": [key for key in record if key not in {"$type", "..."}][:16],
            "fields_omitted": "..." in record or len(record) > 17,
            "time_us": sample.get("time_us"), "observed_at": str(sample.get("observed_at") or "")})
    for observation in project.get("collection_observations", []):
        entry = row(observation.get("collection"))
        if entry is not None:
            entry["record_observations"].append({key: observation.get(key) for key in
                ("count", "recent_count", "observed_at")})
    # Prefer rows with multiple kinds of evidence when the display is bounded.
    names = sorted(rows, key=lambda name: (-sum(bool(rows[name][key]) for key in
                    ("permissions", "schemas", "record_samples", "record_observations")), name))
    return {"rows": [rows[name] for name in names[:MAX_COLLECTIONS]], "omitted": max(0, len(names) - MAX_COLLECTIONS)}


def known_collections(project: dict[str, Any]) -> list[str]:
    return [row["collection"] for row in collection_evidence(project)["rows"]]


def from_html(html: str, collections: list[str] | None = None) -> dict[str, Any]:
    names = list(dict.fromkeys(collections or []))[:MAX_COLLECTIONS]
    return {**passages(page_text(html), names), "input_truncated": len(html) > MAX_HTML_CHARS,
            "format": "normalized_html_text", "searched_collections": names}


def review(project: dict[str, Any]) -> dict[str, Any]:
    """A read-only view; absence of a passage is not absence of protocol use."""
    catalog = collection_evidence(project)
    collections = [entry["collection"] for entry in catalog["rows"]]
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
                # New collection observations cannot recover discarded HTML.
                # Search the saved prefix separately, preserving its own offsets.
                missing = [name for name in collections if name not in material.get("searched_collections", [])]
                if missing and item.get("html_extract"):
                    supplement = from_html(str(item["html_extract"]), missing)
                    supplement["passages"] = [p for p in supplement["passages"] if
                        any(_collection_pattern(name).search(p["text"]) for name in missing) and
                        not any(p["text"] in old["text"] for old in material.get("passages", []))]
                    if supplement["passages"]:
                        add(url, "service", item, supplement, "saved_html_excerpt")
            elif item.get("html_extract"):
                add(url, "service", item, from_html(str(item["html_extract"]), collections), "saved_html_excerpt")
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
            "sources_omitted": max(0, len(matched) - 12), "collections": catalog,
            "status": "matches" if matched else "no_matches" if text_sources else "text_unavailable" if observed else "not_observed"}
