"""Observed client declarations, not granted permissions or actual data usage."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

PARSER_VERSION = "@atproto/oauth-scopes@0.5.12/1"
MAX_SCOPE_LENGTH = 16384


@lru_cache(maxsize=128)
def _parse_json(scope: str) -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("OAuth scope parsing requires Node.js and npm install")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("oauth_scope_parser.mjs"))],
        input=json.dumps({"scope": scope}), text=True, capture_output=True,
        timeout=5, check=False,
    )
    if result.returncode:
        raise RuntimeError("OAuth scope parser failed; check Node.js and npm install")
    # Cache serialized results so callers cannot mutate another observation.
    json.loads(result.stdout)
    return result.stdout


def analyze_scope(scope: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "parser_version": PARSER_VERSION, "permissions": [], "unrecognized": [],
        "atproto_only": False,
    }
    if not isinstance(scope, str):
        return {**result, "status": "missing" if scope is None else "invalid_type"}
    if len(scope) > MAX_SCOPE_LENGTH:
        return {**result, "status": "too_large"}
    try:
        return {**result, **json.loads(_parse_json(scope))}
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        return {**result, "status": "unavailable", "error": str(exc)}


def observe(metadata_url: str, metadata: dict[str, Any], observed_at: str) -> dict[str, Any]:
    scope = metadata.get("scope")
    return {
        "metadata_url": metadata_url, "observed_at": observed_at,
        "declared_client_id": str(metadata.get("client_id") or ""),
        "declared_client_uri": str(metadata.get("client_uri") or ""),
        "scope": scope[:MAX_SCOPE_LENGTH] if isinstance(scope, str) else None,
        "scope_truncated": isinstance(scope, str) and len(scope) > MAX_SCOPE_LENGTH,
        "analysis": analyze_scope(scope),
    }


def _time(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def normalize_results(values: Any) -> list[dict[str, Any]]:
    """Keep the newest snapshot per metadata URL, including empty/failed parses."""
    latest: dict[str, dict[str, Any]] = {}
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict) or not item.get("metadata_url") or not isinstance(item.get("analysis"), dict):
            continue
        url = str(item["metadata_url"])
        if url not in latest or _time(item.get("observed_at")) >= _time(latest[url].get("observed_at")):
            latest[url] = dict(item)
    return list(latest.values())


def reconcile_relations(project: dict[str, Any]) -> None:
    # Replace old declarations, rather than retaining permissions a client has
    # removed. Hydration/replay use saved analysis and never start Node or fetch.
    from .relations import normalize_relation_records, oauth_analysis_relation_records

    observations = normalize_results(project.get("oauth_client_metadata", []))
    project["oauth_client_metadata"] = observations
    sources = {f"oauth_client_metadata:{item['metadata_url']}" for item in observations}
    relations = [item for item in project.get("relation_evidence", []) if item.get("source") not in sources]
    for item in observations:
        for record in oauth_analysis_relation_records(item["metadata_url"], item["analysis"], item.get("observed_at", "")):
            relations.append({"relation": record.value, "source": record.source,
                              "classification": record.classification, "observed_at": record.observed_at,
                              "context": record.context})
    project["relation_evidence"] = normalize_relation_records(relations)
