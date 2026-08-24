from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import collector_relation_policy, identifiers, repo_urls


REPORT_SCHEMA_VERSION = 1
POLICY_VERSION = "project-site-migration-shadow-v1"
CURRENT_REPOSITORY_ROLE = "source_repository"
PROJECT_UI_BASE = "http://127.0.0.1:8888/?project="
STATIC_NON_SITE_ROLES = {"account_profile", "funding", "invalid", "non_public", "package", "repository"}


def _counter(values: collections.Counter[str]) -> dict[str, int]:
    return {key: int(values[key]) for key in sorted(values)}


def _project_url(project_key: str) -> str:
    from urllib.parse import quote

    return f"{PROJECT_UI_BASE}{quote(project_key, safe='')}"


def _content_hash(report: dict[str, Any]) -> str:
    payload = {key: value for key, value in report.items() if key != "content_hash"}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resource_roles(state: dict[str, Any], url: str) -> list[str]:
    evidence = state.get("evidence") if isinstance(state.get("evidence"), dict) else {}
    values = evidence.get("resource_url_records", [])
    if not isinstance(values, list):
        return []
    roles = {
        str(item.get("role") or "unknown")
        for item in values
        if isinstance(item, dict)
        and identifiers.normalize_url(str(item.get("url") or "")) == url
    }
    return sorted(roles)


def evaluate_existing_website(item: dict[str, Any]) -> dict[str, Any]:
    url = identifiers.normalize_url(str(item.get("website_url") or ""))
    static_role = collector_relation_policy.classify_url_role(url)
    confirmed_services = set(item.get("confirmed_services") or [])
    candidate_services = set(item.get("candidate_services") or [])
    noncurrent_services = set(item.get("noncurrent_services") or [])
    collision_count = int(item.get("cross_project_count") or 1)
    cross_project_component_count = int(item.get("cross_project_component_count") or 0)
    if static_role in {"invalid", "non_public"}:
        disposition = "INVALID_WEBSITE"
        reason = f"static_role_is_{static_role}"
    elif collision_count > 1 or cross_project_component_count:
        disposition = "REVIEW_CROSS_PROJECT_COLLISION"
        reason = (
            "website_matches_another_project_component"
            if cross_project_component_count
            else "same_website_is_used_by_multiple_projects"
        )
    elif url in confirmed_services:
        disposition = "REUSE_CONFIRMED_SERVICE"
        reason = "website_exactly_matches_current_confirmed_service"
    elif url in candidate_services:
        disposition = "REVIEW_CANDIDATE_SERVICE"
        reason = "website_exactly_matches_current_candidate_service"
    elif url in noncurrent_services:
        disposition = "REVIEW_NONCURRENT_SERVICE"
        reason = "website_exactly_matches_noncurrent_service"
    elif static_role in STATIC_NON_SITE_ROLES:
        disposition = "REVIEW_NON_SITE_RESOURCE"
        reason = f"static_role_is_{static_role}"
    elif bool(item.get("bidirectional_repository_reference")):
        disposition = "REVIEW_PROJECT_SITE"
        reason = "independent_website_has_bidirectional_repository_reference"
    elif bool(item.get("repository_reference")):
        disposition = "REVIEW_PROJECT_SITE"
        reason = "independent_website_has_repository_reference"
    else:
        disposition = "REVIEW_PROJECT_SITE"
        reason = "independent_website_role_is_not_reconstructible"
    return {
        "disposition": disposition,
        "reason": reason,
        "normalized_url": url,
        "static_url_role": static_role,
        "resource_roles": sorted(set(item.get("resource_roles") or [])),
        "creates_project_site_identifier": False,
        "requires_operator_or_policy_adoption": disposition == "REVIEW_PROJECT_SITE",
    }


def evaluate_empty_website_candidate(item: dict[str, Any]) -> dict[str, Any]:
    url = identifiers.normalize_url(str(item.get("url") or ""))
    static_role = collector_relation_policy.classify_url_role(url)
    if static_role in STATIC_NON_SITE_ROLES:
        disposition = "NOT_PROJECT_SITE_CANDIDATE"
        reason = f"static_role_is_{static_role}"
    elif bool(item.get("cross_project_component")):
        disposition = "REVIEW_CROSS_PROJECT_COLLISION"
        reason = "repository_homepage_matches_another_project_component"
    elif bool(item.get("confirmed_service")):
        disposition = "REUSE_CONFIRMED_SERVICE"
        reason = "repository_homepage_matches_current_confirmed_service"
    elif bool(item.get("candidate_service")):
        disposition = "REVIEW_CANDIDATE_SERVICE"
        reason = "repository_homepage_matches_current_candidate_service"
    elif bool(item.get("bidirectional_repository_reference")):
        disposition = "REVIEW_PROJECT_SITE"
        reason = "repository_homepage_has_return_reference"
    else:
        disposition = "REVIEW_SITE_LEAD"
        reason = "one_way_repository_homepage_is_a_site_lead"
    return {
        "disposition": disposition,
        "reason": reason,
        "normalized_url": url,
        "static_url_role": static_role,
        "creates_project_site_identifier": False,
    }


def _load_identifier_index(conn: sqlite3.Connection) -> dict[str, list[dict[str, str]]]:
    output: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for row in conn.execute(
        "SELECT project_key, kind, value, role, classification FROM project_identifiers"
    ):
        value = identifiers.normalize_url(str(row["value"])) if str(row["kind"]) in {"service_url", "repo_url"} else str(row["value"])
        output[str(row["project_key"])].append({
            "kind": str(row["kind"]),
            "value": value,
            "role": str(row["role"]),
            "classification": str(row["classification"]),
        })
    return output


def _load_noncurrent_services(conn: sqlite3.Connection) -> dict[str, set[str]]:
    output: dict[str, set[str]] = collections.defaultdict(set)
    tables = {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "project_noncurrent_identifiers" not in tables:
        return output
    for row in conn.execute(
        "SELECT project_key, kind, value, role FROM project_noncurrent_identifiers"
    ):
        if str(row["kind"]) == "service_url" and str(row["role"]) == "service":
            output[str(row["project_key"])].add(identifiers.normalize_url(str(row["value"])))
    return output


def compare_database(conn: sqlite3.Connection, *, example_limit: int = 20) -> dict[str, Any]:
    identifiers_by_project = _load_identifier_index(conn)
    noncurrent_by_project = _load_noncurrent_services(conn)
    projects: dict[str, dict[str, Any]] = {}
    website_projects: dict[str, set[str]] = collections.defaultdict(set)
    for row in conn.execute(
        "SELECT project_key, name, website_url, state_json FROM projects ORDER BY project_key"
    ):
        key = str(row["project_key"])
        website = identifiers.normalize_url(str(row["website_url"] or ""))
        projects[key] = {
            "project_key": key,
            "project_name": str(row["name"]),
            "website_url": website,
            "state": _json_object(str(row["state_json"] or "{}")),
        }
        if website:
            website_projects[website].add(key)

    confirmed_repositories: dict[str, set[str]] = collections.defaultdict(set)
    confirmed_services: dict[str, set[str]] = collections.defaultdict(set)
    candidate_services: dict[str, set[str]] = collections.defaultdict(set)
    for key, entries in identifiers_by_project.items():
        for entry in entries:
            if entry["kind"] == "repo_url" and entry["role"] == CURRENT_REPOSITORY_ROLE and entry["classification"] == "confirmed":
                confirmed_repositories[key].add(repo_urls.canonical_repo_url(entry["value"]) or entry["value"])
            if entry["kind"] == "service_url" and entry["role"] == "service":
                target = confirmed_services if entry["classification"] == "confirmed" else candidate_services
                target[key].add(entry["value"])

    confirmed_component_projects: dict[str, set[str]] = collections.defaultdict(set)
    for key, values in confirmed_services.items():
        for value in values:
            confirmed_component_projects[value].add(key)
    for value, keys in website_projects.items():
        confirmed_component_projects[value].update(keys)

    outward: set[tuple[str, str, str]] = set()
    homepages: list[dict[str, str]] = []
    for row in conn.execute(
        """
        SELECT project_key, subject_value, predicate, object_value, classification
        FROM project_relations
        WHERE classification='confirmed'
          AND predicate IN ('source_repository', 'repository_homepage')
        """
    ):
        key = str(row["project_key"])
        predicate = str(row["predicate"])
        if predicate == "repository_homepage":
            repo = repo_urls.canonical_repo_url(str(row["subject_value"]))
            if repo and repo in confirmed_repositories.get(key, set()):
                homepages.append({
                    "project_key": key,
                    "repo_url": repo,
                    "url": identifiers.normalize_url(str(row["object_value"])),
                })
        else:
            page = identifiers.normalize_url(str(row["subject_value"]))
            repo = repo_urls.canonical_repo_url(str(row["object_value"]))
            if page and repo and repo in confirmed_repositories.get(key, set()):
                outward.add((key, page, repo))

    homepage_pairs = {
        (item["project_key"], item["url"], item["repo_url"]) for item in homepages
    }
    existing_rows: list[dict[str, Any]] = []
    for project in projects.values():
        website = project["website_url"]
        if not website:
            continue
        key = project["project_key"]
        repo_reference = any(pair[0] == key and pair[1] == website for pair in outward | homepage_pairs)
        bidirectional = any(
            pair in outward and pair in homepage_pairs
            for pair in {(key, website, repo) for repo in confirmed_repositories.get(key, set())}
        )
        evaluation = evaluate_existing_website({
            "website_url": website,
            "confirmed_services": confirmed_services.get(key, set()),
            "candidate_services": candidate_services.get(key, set()),
            "noncurrent_services": noncurrent_by_project.get(key, set()),
            "cross_project_count": len(website_projects[website]),
            "cross_project_component_count": len(
                confirmed_component_projects.get(website, set()) - {key}
            ),
            "resource_roles": _resource_roles(project["state"], website),
            "repository_reference": repo_reference,
            "bidirectional_repository_reference": bidirectional,
        })
        existing_rows.append({
            "project_key": key,
            "project_name": project["project_name"],
            "project_url": _project_url(key),
            "website_url": website,
            **evaluation,
        })

    empty_candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for homepage in homepages:
        key = homepage["project_key"]
        if projects.get(key, {}).get("website_url"):
            continue
        url = homepage["url"]
        if not url:
            continue
        pair = (key, url)
        row = empty_candidates.setdefault(pair, {
            "project_key": key,
            "project_name": projects.get(key, {}).get("project_name", ""),
            "project_url": _project_url(key),
            "url": url,
            "repository_urls": set(),
        })
        row["repository_urls"].add(homepage["repo_url"])
    empty_rows: list[dict[str, Any]] = []
    for row in empty_candidates.values():
        key = str(row["project_key"])
        references = {
            (key, str(row["url"]), str(repo))
            for repo in row["repository_urls"]
        }
        evaluation = evaluate_empty_website_candidate({
            "url": row["url"],
            "confirmed_service": row["url"] in confirmed_services.get(key, set()),
            "candidate_service": row["url"] in candidate_services.get(key, set()),
            "cross_project_component": bool(
                confirmed_component_projects.get(str(row["url"]), set()) - {key}
            ),
            "bidirectional_repository_reference": bool(references & outward),
        })
        empty_rows.append({
            **{k: v for k, v in row.items() if k != "repository_urls"},
            "repository_urls": sorted(row["repository_urls"]),
            **evaluation,
        })

    existing_rows.sort(key=lambda item: (item["disposition"], item["project_name"].lower(), item["project_key"]))
    empty_rows.sort(key=lambda item: (item["disposition"], item["project_name"].lower(), item["url"]))
    existing_counts = collections.Counter(str(item["disposition"]) for item in existing_rows)
    existing_reason_counts = collections.Counter(str(item["reason"]) for item in existing_rows)
    empty_counts = collections.Counter(str(item["disposition"]) for item in empty_rows)
    empty_leads_by_project = collections.Counter(str(item["project_key"]) for item in empty_rows)
    examples = {
        disposition: [item for item in existing_rows if item["disposition"] == disposition][:example_limit]
        for disposition in sorted(existing_counts)
    }
    empty_examples = {
        disposition: [item for item in empty_rows if item["disposition"] == disposition][:example_limit]
        for disposition in sorted(empty_counts)
    }
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "input": {
            "project_count": len(projects),
            "website_count": len(existing_rows),
            "confirmed_service_count": sum(len(values) for values in confirmed_services.values()),
            "candidate_service_count": sum(len(values) for values in candidate_services.values()),
            "confirmed_repository_count": sum(len(values) for values in confirmed_repositories.values()),
            "confirmed_repository_homepage_count": len(homepages),
        },
        "existing_website_migration": {
            "disposition_counts": _counter(existing_counts),
            "reason_counts": _counter(existing_reason_counts),
            "automatic_project_site_creation_count": 0,
            "examples": examples,
        },
        "empty_website_repository_homepage_leads": {
            "lead_count": len(empty_rows),
            "project_count": len({str(item["project_key"]) for item in empty_rows}),
            "multiple_lead_project_count": sum(count > 1 for count in empty_leads_by_project.values()),
            "maximum_leads_per_project": max(empty_leads_by_project.values(), default=0),
            "disposition_counts": _counter(empty_counts),
            "automatic_project_site_creation_count": 0,
            "examples": empty_examples,
        },
        "review_gate": {
            "comparison_is_read_only": True,
            "production_role_added": False,
            "database_schema_changed": False,
            "ready_for_user_review": True,
        },
    }
    report["content_hash"] = _content_hash(report)
    return report


def compare_database_path(db_path: Path, *, example_limit: int = 20) -> dict[str, Any]:
    target = db_path.expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"database does not exist: {target}")
    conn = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        report = compare_database(conn, example_limit=max(0, int(example_limit)))
        conn.rollback()
    finally:
        conn.close()
    report["source"] = {"db_path": str(target), "read_only": True}
    report["content_hash"] = _content_hash(report)
    return report
