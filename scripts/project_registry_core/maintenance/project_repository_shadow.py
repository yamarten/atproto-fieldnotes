from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from project_registry_core.identity import identifiers
from project_registry_core.repositories import repo_urls


REPORT_SCHEMA_VERSION = 1
POLICY_VERSION = "project-repository-migration-shadow-v1"
CURRENT_ROLE = "source_repository"
PROPOSED_ROLE = "project_repository"
PROJECT_UI_BASE = "http://127.0.0.1:8888/?project="


def _counter(values: collections.Counter[str]) -> dict[str, int]:
    return {key: int(values[key]) for key in sorted(values)}


def _content_hash(report: dict[str, Any]) -> str:
    payload = {key: value for key, value in report.items() if key != "content_hash"}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _project_url(project_key: str) -> str:
    return f"{PROJECT_UI_BASE}{quote(project_key, safe='')}"


def evaluate_relation_row(row: dict[str, Any]) -> dict[str, str]:
    method = str(row.get("method") or "")
    classification = str(row.get("classification") or "")
    subject_type = str(row.get("subject_type") or "")
    object_type = str(row.get("object_type") or "")
    subject_value = str(row.get("subject_value") or "")
    object_value = str(row.get("object_value") or "")

    if method == "first_party_link" and object_type == "repo":
        canonical = repo_urls.canonical_repo_url(object_value)
        if not repo_urls.is_supported_repo_url(object_value):
            return {
                "disposition": "DROP_INVALID_REPOSITORY_ENDPOINT",
                "reason": "target_is_not_a_supported_repository_root_or_locator",
                "canonical_repository": canonical,
            }
        return {
            "disposition": "RECOLLECT_FIRST_PARTY_CONTEXT",
            "reason": "page_link_needs_bounded_context_before_project_repository_relation",
            "canonical_repository": canonical,
        }
    if method == "readme_link":
        return {
            "disposition": (
                "REVIEW_CONFIRMED_README_CONTEXT"
                if classification == "confirmed"
                else "DROP_LEGACY_README_MENTION"
            ),
            "reason": (
                "previously_confirmed_readme_link_needs_bounded_context"
                if classification == "confirmed"
                else "readme_mention_does_not_establish_project_repository"
            ),
            "canonical_repository": (
                repo_urls.canonical_repo_url(subject_value)
                if subject_type == "repo"
                else repo_urls.canonical_repo_url(object_value)
            ),
        }
    if method == "pds_list_records" and subject_type == "did" and object_type == "repo":
        return {
            "disposition": "KEEP_AS_REPOSITORY_OWNERSHIP_OBSERVATION",
            "reason": "tangled_record_links_an_account_to_a_repository_not_a_project_to_a_repository",
            "canonical_repository": repo_urls.canonical_repo_url(object_value),
        }
    return {
        "disposition": "REVIEW_UNMAPPED_LEGACY_RELATION",
        "reason": "legacy_source_repository_shape_is_not_mapped",
        "canonical_repository": "",
    }


def _classification_rank(value: str) -> int:
    return {"heuristic": 0, "candidate": 1, "derived": 2, "confirmed": 3}.get(value, -1)


def _is_same_or_descendant_page(page: str, surface: str) -> bool:
    page_url = urlparse(identifiers.normalize_url(page))
    surface_url = urlparse(identifiers.normalize_url(surface))
    if not page_url.netloc or page_url.netloc.lower() != surface_url.netloc.lower():
        return False
    surface_path = surface_url.path.rstrip("/")
    page_path = page_url.path.rstrip("/")
    return page_path == surface_path or (surface_path and page_path.startswith(f"{surface_path}/")) or not surface_path


def _collected_pages_by_project(conn: sqlite3.Connection) -> dict[str, set[str]]:
    output: dict[str, set[str]] = collections.defaultdict(set)
    for row in conn.execute("SELECT project_key, state_json FROM projects"):
        try:
            state = json.loads(str(row["state_json"] or "{}"))
        except (TypeError, ValueError):
            continue
        if not isinstance(state, dict):
            continue
        evidence = state.get("evidence") if isinstance(state.get("evidence"), dict) else {}
        results = evidence.get("service_verification_results", state.get("service_verification_results", []))
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            for key in ("requested_url", "final_url"):
                value = identifiers.normalize_url(str(result.get(key) or ""))
                if value:
                    output[str(row["project_key"])].add(value)
    return output


def legacy_first_party_recollection_claims(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return canonical page-to-repository claims from the pre-migration relation table."""
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in conn.execute(
        """
        SELECT r.project_key, p.name AS project_name, r.subject_type, r.subject_value,
               r.object_value, r.classification, r.source, r.locator
        FROM project_relations AS r
        LEFT JOIN projects AS p ON p.project_key = r.project_key
        WHERE r.predicate='source_repository' AND r.method='first_party_link'
        ORDER BY r.project_key, r.ordinal
        """
    ):
        evaluation = evaluate_relation_row({
            **dict(row),
            "object_type": "repo",
            "method": "first_party_link",
        })
        if evaluation["disposition"] != "RECOLLECT_FIRST_PARTY_CONTEXT":
            continue
        page_url = identifiers.normalize_url(str(row["subject_value"] or ""))
        repository_url = str(evaluation["canonical_repository"] or "")
        if not page_url or not repository_url:
            continue
        grouped[(
            str(row["project_key"]),
            page_url,
            repo_urls.repo_identity_key(repository_url),
        )].append({
            "project_name": str(row["project_name"] or ""),
            "subject_type": str(row["subject_type"] or ""),
            "repository_url": repository_url,
            "classification": str(row["classification"] or ""),
            "source": str(row["source"] or ""),
            "locator": str(row["locator"] or ""),
        })

    claims: list[dict[str, Any]] = []
    for (project_key, page_url, repository_identity), rows in sorted(grouped.items()):
        claims.append({
            "project_key": project_key,
            "project_name": str(rows[0]["project_name"]),
            "project_url": _project_url(project_key),
            "page_url": page_url,
            "subject_types": sorted({str(row["subject_type"]) for row in rows}),
            "repository_url": str(rows[0]["repository_url"]),
            "repository_identity": repository_identity,
            "legacy_row_count": len(rows),
            "legacy_classifications": sorted({str(row["classification"]) for row in rows}),
            "legacy_sources": sorted({str(row["source"]) for row in rows if row["source"]}),
            "legacy_locators": sorted({str(row["locator"]) for row in rows if row["locator"]}),
        })
    return claims


def annotate_recollection_claims(
    conn: sqlite3.Connection,
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Annotate legacy claims with current project, component, and repository standing."""
    projects = {
        str(row["project_key"]): str(row["name"] or "")
        for row in conn.execute("SELECT project_key, name FROM projects")
    }
    components: dict[str, set[str]] = collections.defaultdict(set)
    repositories: dict[tuple[str, str], str] = {}
    for row in conn.execute(
        """
        SELECT project_key, kind, value, role, classification
        FROM project_identifiers
        WHERE (classification='confirmed' AND
               ((kind='service_url' AND role='service') OR
                (kind='site_url' AND role='project_site')))
           OR (kind='repo_url' AND role='project_repository')
        """
    ):
        project_key = str(row["project_key"])
        if str(row["kind"]) in {"service_url", "site_url"}:
            components[project_key].add(identifiers.normalize_url(str(row["value"])))
            continue
        key = (project_key, repo_urls.repo_identity_key(str(row["value"])))
        classification = str(row["classification"])
        previous = repositories.get(key, "")
        if _classification_rank(classification) > _classification_rank(previous):
            repositories[key] = classification

    output: list[dict[str, Any]] = []
    for source in claims:
        claim = dict(source)
        project_key = str(claim.get("project_key") or "")
        page_url = identifiers.normalize_url(str(claim.get("page_url") or ""))
        current_components = components.get(project_key, set())
        component_url = ""
        if project_key not in projects:
            page_standing = "project_missing"
        elif page_url in current_components:
            page_standing = "exact_current_component"
            component_url = page_url
        elif matching_components := [
            value for value in current_components
            if _is_same_or_descendant_page(page_url, value)
        ]:
            page_standing = "below_current_component"
            component_url = max(matching_components, key=len)
        else:
            page_standing = "outside_current_component"
        repository_classification = repositories.get(
            (project_key, str(claim.get("repository_identity") or "")),
            "absent",
        )
        claim.update({
            "project_name": projects.get(project_key, str(claim.get("project_name") or "")),
            "current_project_exists": project_key in projects,
            "page_standing": page_standing,
            "component_url": component_url,
            "repository_classification": repository_classification,
        })
        output.append(claim)
    return output


def recollection_claims_path(
    legacy_db_path: Path,
    current_db_path: Path,
) -> list[dict[str, Any]]:
    legacy_target = legacy_db_path.expanduser().resolve()
    current_target = current_db_path.expanduser().resolve()
    if not legacy_target.is_file():
        raise FileNotFoundError(f"legacy database does not exist: {legacy_target}")
    if not current_target.is_file():
        raise FileNotFoundError(f"current database does not exist: {current_target}")
    legacy_conn = sqlite3.connect(f"{legacy_target.as_uri()}?mode=ro", uri=True)
    current_conn = sqlite3.connect(f"{current_target.as_uri()}?mode=ro", uri=True)
    legacy_conn.row_factory = sqlite3.Row
    current_conn.row_factory = sqlite3.Row
    try:
        legacy_conn.execute("PRAGMA query_only = ON")
        current_conn.execute("PRAGMA query_only = ON")
        legacy_conn.execute("BEGIN")
        current_conn.execute("BEGIN")
        claims = legacy_first_party_recollection_claims(legacy_conn)
        return annotate_recollection_claims(current_conn, claims)
    finally:
        current_conn.close()
        legacy_conn.close()


def _role_projection(rows: list[sqlite3.Row]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[sqlite3.Row]] = collections.defaultdict(list)
    classification_counts: collections.Counter[str] = collections.Counter()
    for row in rows:
        classification_counts[str(row["classification"])] += 1
        groups[(
            str(row["project_key"]),
            str(row["kind"]),
            repo_urls.repo_identity_key(str(row["value"])),
        )].append(row)

    duplicate_groups = [items for items in groups.values() if len(items) > 1]
    examples = []
    for items in duplicate_groups:
        chosen = max(items, key=lambda item: (_classification_rank(str(item["classification"])), int(item["is_primary"])))
        examples.append({
            "project_key": str(chosen["project_key"]),
            "project_url": _project_url(str(chosen["project_key"])),
            "values": sorted(str(item["value"]) for item in items),
            "projected_value": str(chosen["value"]),
            "projected_classification": str(chosen["classification"]),
        })
    return {
        "current_row_count": len(rows),
        "current_classification_counts": _counter(classification_counts),
        "projected_row_count": len(groups),
        "resource_identity_duplicate_group_count": len(duplicate_groups),
        "rows_removed_by_resource_identity_deduplication": len(rows) - len(groups),
        "proposed_role": PROPOSED_ROLE,
        "classification_preserved_except_duplicate_resolution": True,
        "duplicate_examples": examples,
    }


def compare_database(conn: sqlite3.Connection, *, example_limit: int = 20) -> dict[str, Any]:
    identifier_rows = list(conn.execute(
        """
        SELECT project_key, kind, value, role, classification, is_primary
        FROM project_identifiers
        WHERE role=?
        ORDER BY project_key, ordinal
        """,
        (CURRENT_ROLE,),
    ))
    role_projection = _role_projection(identifier_rows)

    dependent_counts: dict[str, int] = {}
    for table, column in (
        ("project_identifier_adoptions", "role"),
        ("project_noncurrent_identifiers", "role"),
        ("project_decisions", "target_role"),
    ):
        dependent_counts[table] = int(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (CURRENT_ROLE,)
        ).fetchone()[0])

    confirmed_components = {
        (str(row["project_key"]), str(row["value"]))
        for row in conn.execute(
            """
            SELECT project_key, value FROM project_identifiers
            WHERE classification='confirmed'
              AND ((kind='service_url' AND role='service')
                   OR (kind='site_url' AND role='project_site'))
            """
        )
    }
    components_by_project: dict[str, set[str]] = collections.defaultdict(set)
    for project_key, value in confirmed_components:
        components_by_project[project_key].add(value)
    collected_pages = _collected_pages_by_project(conn)
    relation_rows = list(conn.execute(
        """
        SELECT project_key, subject_type, subject_value, predicate, object_type,
               object_value, classification, source, locator, method
        FROM project_relations
        WHERE predicate='source_repository'
        ORDER BY project_key, ordinal
        """
    ))
    disposition_counts: collections.Counter[str] = collections.Counter()
    method_counts: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    first_party_subject_counts: collections.Counter[str] = collections.Counter()
    canonical_claims: set[tuple[str, str, str]] = set()
    for row in relation_rows:
        evaluation = evaluate_relation_row(dict(row))
        disposition = evaluation["disposition"]
        disposition_counts[disposition] += 1
        method_counts[str(row["method"] or "(missing)")] += 1
        if str(row["method"]) == "first_party_link":
            subject_key = (str(row["project_key"]), str(row["subject_value"]))
            project_key, subject_value = subject_key
            if subject_key in confirmed_components:
                subject_status = "exact_confirmed_component"
            elif any(
                _is_same_or_descendant_page(subject_value, surface)
                for surface in components_by_project.get(project_key, set())
            ):
                subject_status = "page_below_confirmed_component"
            elif subject_value in collected_pages.get(project_key, set()):
                subject_status = "collected_page_without_component_match"
            else:
                subject_status = "not_reconstructible_from_current_state"
            first_party_subject_counts[subject_status] += 1
            if disposition == "RECOLLECT_FIRST_PARTY_CONTEXT":
                canonical_claims.add((
                    str(row["project_key"]),
                    str(row["subject_value"]),
                    evaluation["canonical_repository"],
                ))
        if len(examples[disposition]) < example_limit:
            examples[disposition].append({
                "project_key": str(row["project_key"]),
                "project_url": _project_url(str(row["project_key"])),
                "subject": {"type": str(row["subject_type"]), "value": str(row["subject_value"])},
                "object": {"type": str(row["object_type"]), "value": str(row["object_value"])},
                "classification": str(row["classification"]),
                "source": str(row["source"]),
                "locator": str(row["locator"]),
                "method": str(row["method"]),
                **evaluation,
            })

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "source": {"read_only": True},
        "identifier_role_projection": role_projection,
        "dependent_role_references": dependent_counts,
        "relation_disposition": {
            "current_row_count": len(relation_rows),
            "method_counts": _counter(method_counts),
            "disposition_counts": _counter(disposition_counts),
            "recollectible_first_party_claim_count": len(canonical_claims),
            "first_party_subject_status_counts": _counter(first_party_subject_counts),
            "mass_predicate_rename_is_safe": False,
        },
        "examples": {key: value for key, value in sorted(examples.items())},
        "migration_boundary": {
            "identifier_role_rename_ready": True,
            "dependent_role_references_migrate_with_identifier_role": True,
            "existing_identifier_classification_is_rejudged": False,
            "relation_predicate_mass_rename_ready": False,
            "first_party_links_require_bounded_recollection": True,
            "readme_mentions_become_project_repository": False,
            "tangled_ownership_observations_become_project_repository": False,
            "production_database_changed": False,
        },
        "review_gate": {
            "comparison_is_read_only": True,
            "ready_for_user_review": not disposition_counts["REVIEW_UNMAPPED_LEGACY_RELATION"],
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
    report["source"]["db_path"] = str(target)
    report["content_hash"] = _content_hash(report)
    return report
