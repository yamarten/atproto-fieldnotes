from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import database, identifiers, project_record, registry, relations, repo_urls


SHADOW_SCHEMA_VERSION = 1
REVIEW_SOURCE = "operator_review:project_repository_recollection_v1"
RELATION_METHOD = "bounded_first_party_link"


def load_review_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported project repository review manifest")
    if not str(payload.get("reviewed_at") or ""):
        raise ValueError("project repository review manifest has no reviewed_at")
    if not str(payload.get("source_report_hash") or ""):
        raise ValueError("project repository review manifest has no source_report_hash")
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("project repository review manifest has no actions")
    seen: set[tuple[str, str]] = set()
    for item in actions:
        if not isinstance(item, dict):
            raise ValueError("project repository review action must be an object")
        project_key = str(item.get("project_key") or "")
        component_url = identifiers.normalize_url(str(item.get("expected_component_url") or ""))
        repository_url = repo_urls.canonical_repo_url(str(item.get("repository_url") or ""))
        locator = identifiers.normalize_url(str(item.get("locator") or ""))
        key = (project_key, repo_urls.repo_identity_key(repository_url))
        if (
            not project_key
            or not component_url
            or not repository_url
            or not locator
            or key in seen
            or str(item.get("expected_repository_classification") or "") != "absent"
        ):
            raise ValueError(f"invalid project repository review action: {item}")
        seen.add(key)
    return payload


def _component_subject(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    component_url: str,
) -> str:
    rows = conn.execute(
        """
        SELECT kind, value, role
        FROM project_identifiers
        WHERE project_key=? AND classification='confirmed'
          AND ((kind='service_url' AND role='service')
               OR (kind='site_url' AND role='project_site'))
        """,
        (project_key,),
    ).fetchall()
    matching = [row for row in rows if identifiers.normalize_url(str(row["value"])) == component_url]
    if len(matching) != 1:
        raise ValueError(
            f"reviewed exact component changed for {project_key}: expected {component_url}"
        )
    return "service" if str(matching[0]["kind"]) == "service_url" else "website"


def _repository_classification(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    repository_url: str,
) -> str:
    target_identity = repo_urls.repo_identity_key(repository_url)
    classifications = [
        str(row["classification"])
        for row in conn.execute(
            "SELECT value, classification FROM project_identifiers "
            "WHERE project_key=? AND kind='repo_url' AND role='project_repository'",
            (project_key,),
        )
        if repo_urls.repo_identity_key(str(row["value"])) == target_identity
    ]
    if not classifications:
        return "absent"
    return max(classifications, key=lambda value: {"heuristic": 0, "candidate": 1, "derived": 2, "confirmed": 3}.get(value, -1))


def _cross_project_owners(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    repository_url: str,
) -> list[str]:
    target_identity = repo_urls.repo_identity_key(repository_url)
    return sorted({
        str(row["project_key"])
        for row in conn.execute(
            "SELECT project_key, value FROM project_identifiers "
            "WHERE project_key<>? AND kind='repo_url' AND role='project_repository'",
            (project_key,),
        )
        if repo_urls.repo_identity_key(str(row["value"])) == target_identity
    })


def _apply_manifest(conn: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, int]:
    reviewed_at = str(manifest["reviewed_at"])
    source_report_hash = str(manifest["source_report_hash"])
    counts: collections.Counter[str] = collections.Counter()
    existing_adoption_ids = {
        str(row["adoption_id"])
        for row in conn.execute("SELECT adoption_id FROM project_identifier_adoptions")
    }
    for item in manifest["actions"]:
        project_key = str(item["project_key"])
        component_url = identifiers.normalize_url(str(item["expected_component_url"]))
        repository_url = repo_urls.canonical_repo_url(str(item["repository_url"]))
        locator = identifiers.normalize_url(str(item["locator"]))
        subject_type = _component_subject(
            conn,
            project_key=project_key,
            component_url=component_url,
        )
        actual_classification = _repository_classification(
            conn,
            project_key=project_key,
            repository_url=repository_url,
        )
        if actual_classification != str(item["expected_repository_classification"]):
            raise ValueError(
                f"reviewed repository standing changed for {project_key} {repository_url}: "
                f"expected {item['expected_repository_classification']}, got {actual_classification}"
            )
        owners = _cross_project_owners(
            conn,
            project_key=project_key,
            repository_url=repository_url,
        )
        if owners:
            raise ValueError(
                f"reviewed repository is now attached to other projects: {repository_url}: {owners}"
            )
        project = project_record.fetch_project(conn, project_key)
        if project is None:
            raise ValueError(f"reviewed project is missing: {project_key}")
        if registry.identifier_is_blocked(project, "repo_url", repository_url) or registry.identifier_is_deferred(
            project, "repo_url", repository_url
        ):
            raise ValueError(f"reviewed repository is guarded: {project_key} {repository_url}")
        project["relation_evidence"] = relations.normalize_relation_records([
            *project.get("relation_evidence", []),
            {
                "relation": {
                    "subject": {"type": subject_type, "value": component_url},
                    "predicate": "project_repository",
                    "object": {"type": "repo", "value": repository_url},
                    "locator": locator,
                    "method": RELATION_METHOD,
                },
                "source": REVIEW_SOURCE,
                "classification": "confirmed",
                "observed_at": reviewed_at,
                "context": {
                    "reviewed": True,
                    "source_report_hash": source_report_hash,
                    "policy": "bounded_exact_component_v1",
                },
            },
        ])
        registry.confirm_project_repository_url(
            project,
            repository_url,
            REVIEW_SOURCE,
            relation="project_repository",
            reason="operator reviewed a direct repository link from an exact current project component",
            evidence_kind="bounded_first_party_link",
            evidence_url=component_url,
            updated_at=reviewed_at,
            operator_override=True,
        )
        registry.add_confirmed_identifier_decision(
            project,
            "repo_url",
            repository_url,
            source=REVIEW_SOURCE,
            note="repository relation and project attribution reviewed by operator",
            role="project_repository",
            registration_origin=False,
            source_ref=source_report_hash,
            policy_version="bounded_exact_component_v1",
        )
        project_record.save_project(
            conn,
            registry.finalize_project_shape(project),
            enriched=bool(project.get("last_enriched_at")),
            preserve_timestamps=True,
            commit=False,
        )
        counts["confirmed_identifier"] += 1
        counts["confirmed_relation"] += 1
    incidental_ids = [
        str(row["adoption_id"])
        for row in conn.execute(
            "SELECT adoption_id, source FROM project_identifier_adoptions"
        )
        if str(row["adoption_id"]) not in existing_adoption_ids
        and str(row["source"]) != REVIEW_SOURCE
    ]
    if incidental_ids:
        conn.executemany(
            "DELETE FROM project_identifier_adoptions WHERE adoption_id=?",
            [(value,) for value in incidental_ids],
        )
        counts["discarded_incidental_adoption"] = len(incidental_ids)
    return dict(sorted(counts.items()))


def _validation(conn: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, int | str]:
    reviewed_at = str(manifest["reviewed_at"])
    expected = len(manifest["actions"])
    relations_count = int(conn.execute(
        "SELECT COUNT(*) FROM project_relations WHERE predicate='project_repository' "
        "AND classification='confirmed' AND source=? AND observed_at=? AND method=?",
        (REVIEW_SOURCE, reviewed_at, RELATION_METHOD),
    ).fetchone()[0])
    identifiers_count = int(conn.execute(
        "SELECT COUNT(*) FROM project_identifiers i WHERE i.kind='repo_url' "
        "AND i.role='project_repository' AND i.classification='confirmed' AND EXISTS ("
        "SELECT 1 FROM project_identifier_adoptions a WHERE a.project_key=i.project_key "
        "AND a.kind=i.kind AND a.value=i.value AND a.role=i.role AND a.source=? AND a.cleared_at='')",
        (REVIEW_SOURCE,),
    ).fetchone()[0])
    return {
        "integrity": str(conn.execute("PRAGMA integrity_check").fetchone()[0]),
        "foreign_key_error_count": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "reviewed_confirmed_relation_count": relations_count,
        "reviewed_confirmed_identifier_count": identifiers_count,
        "expected_action_count": expected,
    }


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "project_count": int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]),
        "identifier_count": int(conn.execute("SELECT COUNT(*) FROM project_identifiers").fetchone()[0]),
        "relation_count": int(conn.execute("SELECT COUNT(*) FROM project_relations").fetchone()[0]),
        "decision_count": int(conn.execute("SELECT COUNT(*) FROM project_decisions").fetchone()[0]),
        "adoption_count": int(conn.execute("SELECT COUNT(*) FROM project_identifier_adoptions").fetchone()[0]),
    }


def materialize_reviewed_copy(
    source_path: Path,
    target_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    source_path = source_path.expanduser().resolve()
    target_path = target_path.expanduser().resolve()
    manifest_path = manifest_path.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"registry database not found: {source_path}")
    if target_path.exists():
        raise FileExistsError(f"shadow database already exists: {target_path}")
    manifest = load_review_manifest(manifest_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(target_path)
    target.row_factory = sqlite3.Row
    try:
        source.execute("PRAGMA query_only=ON")
        source_version = database.database_schema_version(source)
        if source_version != database.SCHEMA_VERSION:
            raise database.SchemaMismatchError(
                f"project repository review requires schema {database.SCHEMA_VERSION}, got {source_version}"
            )
        source.backup(target)
        target.execute("PRAGMA foreign_keys=ON")
        before = _counts(target)
        target.execute("BEGIN")
        target.execute(
            "INSERT INTO registry_meta(key,value) VALUES('project_repository_review_shadow_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SHADOW_SCHEMA_VERSION),),
        )
        action_counts = _apply_manifest(target, manifest)
        validation = _validation(target, manifest)
        after = _counts(target)
        ready = (
            validation["integrity"] == "ok"
            and validation["foreign_key_error_count"] == 0
            and validation["reviewed_confirmed_relation_count"] == len(manifest["actions"])
            and validation["reviewed_confirmed_identifier_count"] == len(manifest["actions"])
        )
        target.commit()
    except Exception:
        target.rollback()
        target.close()
        source.close()
        target_path.unlink(missing_ok=True)
        raise
    else:
        target.close()
        source.close()
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return {
        "shadow_schema_version": SHADOW_SCHEMA_VERSION,
        "source": {"db_path": str(source_path), "read_only": True, "schema_version": source_version},
        "target": {"db_path": str(target_path), "production_database": False},
        "manifest": str(manifest_path),
        "manifest_hash": manifest_hash,
        "reviewed_action_count": len(manifest["actions"]),
        "action_counts": action_counts,
        "before": before,
        "after": after,
        "validation": validation,
        "review_gate": {
            "ready": ready,
            "production_schema_changed": False,
            "production_database_changed": False,
        },
    }
