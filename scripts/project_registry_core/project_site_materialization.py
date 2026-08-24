from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import database, identifiers


SHADOW_SCHEMA_VERSION = 1
REVIEW_SOURCE = "operator_review:project_site_review_v1"
SUPPORTED_ACTIONS = {
    "add_project_site",
    "confirm_service",
    "keep_service_remove_conflict",
    "clear_website_keep_noncurrent",
}


def _row_id(prefix: str, *values: str) -> str:
    payload = "\0".join((prefix, *values))
    return f"{prefix}:{hashlib.sha256(payload.encode()).hexdigest()[:24]}"


def load_review_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported project site review manifest")
    if not str(payload.get("reviewed_at") or ""):
        raise ValueError("project site review manifest has no reviewed_at")
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("project site review manifest has no actions")
    keys: set[str] = set()
    for item in actions:
        if not isinstance(item, dict):
            raise ValueError("project site review action must be an object")
        key = str(item.get("project_key") or "")
        action = str(item.get("action") or "")
        website = identifiers.normalize_url(str(item.get("expected_website_url") or ""))
        if not key or key in keys or action not in SUPPORTED_ACTIONS or not website:
            raise ValueError(f"invalid project site review action: {item}")
        if action == "keep_service_remove_conflict" and not str(item.get("conflicting_project_key") or ""):
            raise ValueError("conflict removal requires conflicting_project_key")
        keys.add(key)
    return payload


def _clear_adoptions(
    conn: sqlite3.Connection,
    key: tuple[str, str, str, str],
    *,
    reviewed_at: str,
) -> None:
    conn.execute(
        """
        UPDATE project_identifier_adoptions
        SET cleared_at=CASE WHEN cleared_at='' THEN ? ELSE cleared_at END,
            review_reason=CASE WHEN review_reason='' THEN ? ELSE review_reason END
        WHERE project_key=? AND kind=? AND value=? AND role=?
        """,
        (reviewed_at, "superseded by project site review", *key),
    )


def _insert_adoption(
    conn: sqlite3.Connection,
    key: tuple[str, str, str, str],
    *,
    reviewed_at: str,
) -> None:
    adoption_id = _row_id("adoption", *key, REVIEW_SOURCE, reviewed_at)
    conn.execute(
        """
        INSERT OR IGNORE INTO project_identifier_adoptions(
            adoption_id, project_key, kind, value, role, adoption_kind, source,
            registration_origin, source_ref, policy_version, input_fingerprint,
            created_at, cleared_at, review_required, review_reason, origin_project_key
        ) VALUES (?, ?, ?, ?, ?, 'operator', ?, 1, '', '', '', ?, '', 0, '', ?)
        """,
        (adoption_id, *key, REVIEW_SOURCE, reviewed_at, key[0]),
    )


def _add_confirmed_claim(
    conn: sqlite3.Connection,
    key: tuple[str, str, str, str],
    *,
    reviewed_at: str,
) -> None:
    conn.execute(
        "DELETE FROM project_noncurrent_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        key,
    )
    row = conn.execute(
        "SELECT classification FROM project_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        key,
    ).fetchone()
    if row is None:
        ordinal = int(conn.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM project_identifiers WHERE project_key=?",
            (key[0],),
        ).fetchone()[0])
        conn.execute(
            """
            INSERT INTO project_identifiers(
                project_key, ordinal, kind, value, role, classification,
                is_primary, provider, metadata_json, observed_at
            ) VALUES (?, ?, ?, ?, ?, 'confirmed', 0, '', '{}', ?)
            """,
            (key[0], ordinal, key[1], key[2], key[3], reviewed_at),
        )
    elif str(row["classification"]) != "confirmed":
        conn.execute(
            "UPDATE project_identifiers SET classification='confirmed', observed_at=? "
            "WHERE project_key=? AND kind=? AND value=? AND role=?",
            (reviewed_at, *key),
        )
    _clear_adoptions(conn, key, reviewed_at=reviewed_at)
    _insert_adoption(conn, key, reviewed_at=reviewed_at)


def _remove_claim(
    conn: sqlite3.Connection,
    key: tuple[str, str, str, str],
    *,
    reviewed_at: str,
) -> int:
    _clear_adoptions(conn, key, reviewed_at=reviewed_at)
    cursor = conn.execute(
        "DELETE FROM project_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        key,
    )
    return int(cursor.rowcount)


def _validate_source_action(
    conn: sqlite3.Connection,
    item: dict[str, Any],
) -> tuple[str, str]:
    project_key = str(item["project_key"])
    expected = identifiers.normalize_url(str(item["expected_website_url"]))
    row = conn.execute(
        "SELECT website_url FROM projects WHERE project_key=?",
        (project_key,),
    ).fetchone()
    if row is None:
        raise ValueError(f"reviewed project is missing: {project_key}")
    actual = identifiers.normalize_url(str(row["website_url"] or ""))
    if actual != expected:
        raise ValueError(
            f"reviewed Website changed for {project_key}: expected {expected}, got {actual}"
        )
    return project_key, expected


def _apply_manifest(conn: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, int]:
    reviewed_at = str(manifest["reviewed_at"])
    counts: collections.Counter[str] = collections.Counter()
    for item in manifest["actions"]:
        project_key, website = _validate_source_action(conn, item)
        action = str(item["action"])
        if action == "add_project_site":
            _add_confirmed_claim(
                conn,
                (project_key, "site_url", website, "project_site"),
                reviewed_at=reviewed_at,
            )
            candidate_service = (project_key, "service_url", website, "service")
            row = conn.execute(
                "SELECT classification FROM project_identifiers "
                "WHERE project_key=? AND kind=? AND value=? AND role=?",
                candidate_service,
            ).fetchone()
            if row is not None and str(row["classification"]) == "candidate":
                counts["candidate_service_removed"] += _remove_claim(
                    conn, candidate_service, reviewed_at=reviewed_at
                )
        elif action == "confirm_service":
            _add_confirmed_claim(
                conn,
                (project_key, "service_url", website, "service"),
                reviewed_at=reviewed_at,
            )
        elif action == "keep_service_remove_conflict":
            own_key = (project_key, "service_url", website, "service")
            own = conn.execute(
                "SELECT classification FROM project_identifiers "
                "WHERE project_key=? AND kind=? AND value=? AND role=?",
                own_key,
            ).fetchone()
            if own is None or str(own["classification"]) != "confirmed":
                raise ValueError(f"reviewed service is not confirmed: {own_key}")
            conflict_key = (
                str(item["conflicting_project_key"]),
                "service_url",
                website,
                "service",
            )
            removed = _remove_claim(
                conn, conflict_key, reviewed_at=reviewed_at
            )
            if not removed:
                raise ValueError(f"reviewed conflicting service is missing: {conflict_key}")
            counts["conflicting_service_removed"] += removed
        elif action == "clear_website_keep_noncurrent":
            cursor = conn.execute(
                "UPDATE projects SET website_url='' WHERE project_key=? AND website_url=?",
                (project_key, website),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"reviewed Website could not be cleared: {project_key}")
        counts[action] += 1
    return dict(sorted(counts.items()))


def _validation(conn: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, int | str]:
    reviewed_at = str(manifest["reviewed_at"])
    reviewed_site_keys = {
        (str(item["project_key"]), identifiers.normalize_url(str(item["expected_website_url"])))
        for item in manifest["actions"] if str(item["action"]) == "add_project_site"
    }
    site_count = sum(
        conn.execute(
            "SELECT COUNT(*) FROM project_identifiers WHERE project_key=? AND kind='site_url' "
            "AND value=? AND role='project_site' AND classification='confirmed'",
            key,
        ).fetchone()[0]
        for key in reviewed_site_keys
    )
    active_site_adoptions = int(conn.execute(
        "SELECT COUNT(*) FROM project_identifier_adoptions "
        "WHERE kind='site_url' AND role='project_site' AND source=? "
        "AND created_at=? AND cleared_at=''",
        (REVIEW_SOURCE, reviewed_at),
    ).fetchone()[0])
    site_service_overlap = int(conn.execute(
        """
        SELECT COUNT(*) FROM project_identifiers s
        JOIN project_identifiers v
          ON v.project_key=s.project_key AND v.value=s.value
        WHERE s.kind='site_url' AND s.role='project_site'
          AND v.kind='service_url' AND v.role='service'
        """
    ).fetchone()[0])
    return {
        "integrity": str(conn.execute("PRAGMA integrity_check").fetchone()[0]),
        "foreign_key_error_count": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "confirmed_project_site_count": site_count,
        "active_reviewed_project_site_adoption_count": active_site_adoptions,
        "current_site_service_exact_overlap_count": site_service_overlap,
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
                f"project site shadow requires schema {database.SCHEMA_VERSION}, got {source_version}"
            )
        source.backup(target)
        target.execute("PRAGMA foreign_keys=ON")
        before = {
            "project_count": int(target.execute("SELECT COUNT(*) FROM projects").fetchone()[0]),
            "identifier_count": int(target.execute("SELECT COUNT(*) FROM project_identifiers").fetchone()[0]),
            "website_count": int(target.execute("SELECT COUNT(*) FROM projects WHERE website_url<>''").fetchone()[0]),
        }
        target.execute("BEGIN")
        target.execute(
            "INSERT INTO registry_meta(key,value) VALUES('project_site_shadow_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SHADOW_SCHEMA_VERSION),),
        )
        action_counts = _apply_manifest(target, manifest)
        validation = _validation(target, manifest)
        after = {
            "project_count": int(target.execute("SELECT COUNT(*) FROM projects").fetchone()[0]),
            "identifier_count": int(target.execute("SELECT COUNT(*) FROM project_identifiers").fetchone()[0]),
            "website_count": int(target.execute("SELECT COUNT(*) FROM projects WHERE website_url<>''").fetchone()[0]),
        }
        expected_sites = sum(
            str(item["action"]) == "add_project_site" for item in manifest["actions"]
        )
        ready = (
            validation["integrity"] == "ok"
            and validation["foreign_key_error_count"] == 0
            and validation["confirmed_project_site_count"] == expected_sites
            and validation["active_reviewed_project_site_adoption_count"] == expected_sites
            and validation["current_site_service_exact_overlap_count"] == 0
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
    return {
        "shadow_schema_version": SHADOW_SCHEMA_VERSION,
        "source": {"db_path": str(source_path), "read_only": True, "schema_version": source_version},
        "target": {"db_path": str(target_path), "production_database": False},
        "manifest": str(manifest_path),
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
