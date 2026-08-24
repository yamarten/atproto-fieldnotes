from __future__ import annotations

import collections
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import database, repo_urls


MATERIALIZATION_VERSION = 1
OLD_ROLE = "source_repository"
NEW_ROLE = "project_repository"


def _classification_rank(value: str) -> int:
    return {"heuristic": 0, "candidate": 1, "derived": 2, "confirmed": 3}.get(value, -1)


def _merge_json_objects(values: list[str]) -> str:
    merged: dict[str, Any] = {}
    for value in values:
        try:
            item = json.loads(value or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict):
            merged.update(item)
    return json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _rewrite_current_json_views(conn: sqlite3.Connection) -> dict[str, int]:
    decision_metadata_count = 0
    for row in conn.execute(
        "SELECT rowid AS decision_rowid, target_value, metadata_json FROM project_decisions "
        "WHERE target_type='repo_url' AND target_role=?",
        (NEW_ROLE,),
    ):
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        rewritten = dict(metadata)
        rewritten.update({
            "type": "repo_url",
            "value": str(row["target_value"]),
            "role": NEW_ROLE,
        })
        encoded = _dump_json(rewritten)
        if encoded != str(row["metadata_json"] or "{}"):
            conn.execute(
                "UPDATE project_decisions SET metadata_json=? WHERE rowid=?",
                (encoded, int(row["decision_rowid"])),
            )
            decision_metadata_count += 1

    profile_link_count = 0
    current_relation_cache_count = 0
    for row in conn.execute("SELECT project_key, profile_json, state_json FROM projects"):
        project_key = str(row["project_key"])
        try:
            profile = json.loads(str(row["profile_json"] or "{}"))
        except (TypeError, ValueError):
            profile = {}
        if isinstance(profile, dict):
            app = profile.get("app")
            links = app.get("links") if isinstance(app, dict) else None
            if isinstance(links, list):
                changed = False
                rewritten_links: list[Any] = []
                for item in links:
                    if isinstance(item, dict) and str(item.get("role") or "") == "sourceCode":
                        item = {**item, "role": "repository"}
                        profile_link_count += 1
                        changed = True
                    rewritten_links.append(item)
                if changed:
                    app["links"] = rewritten_links
                    conn.execute(
                        "UPDATE projects SET profile_json=? WHERE project_key=?",
                        (_dump_json(profile), project_key),
                    )

        try:
            state = json.loads(str(row["state_json"] or "{}"))
        except (TypeError, ValueError):
            state = {}
        ops = state.get("ops") if isinstance(state, dict) else None
        evidence = ops.get("atproto_contribution_evidence") if isinstance(ops, dict) else None
        if not isinstance(evidence, list):
            continue
        changed = False
        rewritten_evidence: list[Any] = []
        for item in evidence:
            if not isinstance(item, dict) or str(item.get("kind") or "") != "relation_predicates":
                rewritten_evidence.append(item)
                continue
            values = [
                value for value in item.get("values", [])
                if str(value) != OLD_ROLE
            ] if isinstance(item.get("values"), list) else []
            if values == item.get("values"):
                rewritten_evidence.append(item)
                continue
            current_relation_cache_count += 1
            changed = True
            if values:
                rewritten_evidence.append({**item, "values": values, "count": len(values)})
        if changed:
            ops["atproto_contribution_evidence"] = rewritten_evidence
            conn.execute(
                "UPDATE projects SET state_json=? WHERE project_key=?",
                (_dump_json(state), project_key),
            )
    return {
        "rewritten_decision_metadata_count": decision_metadata_count,
        "rewritten_profile_link_count": profile_link_count,
        "removed_current_relation_cache_count": current_relation_cache_count,
    }


def _rewrite_dependent_claim(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    old_value: str,
    new_value: str,
) -> None:
    conn.execute(
        "UPDATE project_identifier_adoptions SET value=?, role=? "
        "WHERE project_key=? AND kind='repo_url' AND value=? AND role=?",
        (new_value, NEW_ROLE, project_key, old_value, OLD_ROLE),
    )
    conn.execute(
        "UPDATE project_decisions SET target_value=?, target_role=? "
        "WHERE project_key=? AND target_type='repo_url' AND target_value=? AND target_role=?",
        (new_value, NEW_ROLE, project_key, old_value, OLD_ROLE),
    )
    old_noncurrent = conn.execute(
        "SELECT * FROM project_noncurrent_identifiers WHERE project_key=? AND kind='repo_url' "
        "AND value=? AND role=?",
        (project_key, old_value, OLD_ROLE),
    ).fetchone()
    if old_noncurrent is not None:
        conn.execute(
            """
            INSERT INTO project_noncurrent_identifiers(
                project_key, kind, value, role, standing, reason, recorded_at,
                origin_project_key, metadata_json
            ) VALUES (?, 'repo_url', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_key, kind, value, role) DO UPDATE SET
                standing=excluded.standing,
                reason=CASE WHEN project_noncurrent_identifiers.reason='' THEN excluded.reason
                            ELSE project_noncurrent_identifiers.reason END,
                recorded_at=MAX(project_noncurrent_identifiers.recorded_at, excluded.recorded_at),
                metadata_json=excluded.metadata_json
            """,
            (
                project_key, new_value, NEW_ROLE, str(old_noncurrent["standing"]),
                str(old_noncurrent["reason"]), str(old_noncurrent["recorded_at"]),
                str(old_noncurrent["origin_project_key"]), str(old_noncurrent["metadata_json"]),
            ),
        )
        conn.execute(
            "DELETE FROM project_noncurrent_identifiers WHERE project_key=? AND kind='repo_url' "
            "AND value=? AND role=?",
            (project_key, old_value, OLD_ROLE),
        )


def apply_migration(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = list(conn.execute(
        "SELECT * FROM project_identifiers WHERE kind='repo_url' AND role=? "
        "ORDER BY project_key, ordinal",
        (OLD_ROLE,),
    ))
    groups: dict[tuple[str, str], list[sqlite3.Row]] = collections.defaultdict(list)
    for row in rows:
        groups[(str(row["project_key"]), repo_urls.repo_identity_key(str(row["value"])))].append(row)

    duplicate_group_count = 0
    removed_identifier_count = 0
    for (project_key, _identity), items in groups.items():
        representative = max(
            items,
            key=lambda item: (
                _classification_rank(str(item["classification"])),
                int(item["is_primary"]),
                str(item["observed_at"]),
                -int(item["ordinal"]),
            ),
        )
        representative_value = str(representative["value"])
        if len(items) > 1:
            duplicate_group_count += 1
        for item in items:
            _rewrite_dependent_claim(
                conn,
                project_key=project_key,
                old_value=str(item["value"]),
                new_value=representative_value,
            )
        merged_metadata = _merge_json_objects([str(item["metadata_json"]) for item in items])
        conn.execute(
            "DELETE FROM project_identifiers WHERE project_key=? AND kind='repo_url' AND role=?",
            (project_key, OLD_ROLE),
        )
        conn.execute(
            """
            INSERT INTO project_identifiers(
                project_key, ordinal, kind, value, role, classification, is_primary,
                provider, metadata_json, observed_at
            ) VALUES (?, ?, 'repo_url', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_key, int(representative["ordinal"]), representative_value, NEW_ROLE,
                str(representative["classification"]),
                int(
                    str(representative["classification"]) == "confirmed"
                    and any(bool(item["is_primary"]) for item in items)
                ),
                str(representative["provider"]), merged_metadata,
                max(str(item["observed_at"]) for item in items),
            ),
        )
        removed_identifier_count += len(items) - 1

    # Adoption and non-current rows can legitimately outlive a current identifier.
    # They still describe the same repository role and must not be left behind.
    conn.execute(
        "UPDATE project_identifier_adoptions SET role=? WHERE role=?",
        (NEW_ROLE, OLD_ROLE),
    )
    conn.execute(
        "UPDATE project_noncurrent_identifiers SET role=? WHERE role=?",
        (NEW_ROLE, OLD_ROLE),
    )
    conn.execute(
        "UPDATE project_decisions SET target_role=? WHERE target_role=?",
        (NEW_ROLE, OLD_ROLE),
    )
    json_view_changes = _rewrite_current_json_views(conn)

    relation_counts = {
        str(row["method"] or "(missing)"): int(row["count"])
        for row in conn.execute(
            "SELECT method, COUNT(*) AS count FROM project_relations "
            "WHERE predicate=? GROUP BY method",
            (OLD_ROLE,),
        )
    }
    relation_cursor = conn.execute(
        "DELETE FROM project_relations WHERE predicate=?",
        (OLD_ROLE,),
    )
    conn.execute(
        "INSERT INTO registry_meta(key,value) VALUES('project_repository_materialization_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(MATERIALIZATION_VERSION),),
    )
    return {
        "identifier_input_count": len(rows),
        "identifier_output_count": len(groups),
        "duplicate_group_count": duplicate_group_count,
        "removed_duplicate_identifier_count": removed_identifier_count,
        "removed_legacy_relation_count": int(relation_cursor.rowcount),
        "removed_legacy_relation_counts_by_method": dict(sorted(relation_counts.items())),
        **json_view_changes,
    }


def validation(conn: sqlite3.Connection) -> dict[str, Any]:
    role_counts = {
        str(row["role"]): int(row["count"])
        for row in conn.execute(
            "SELECT role, COUNT(*) AS count FROM project_identifiers "
            "WHERE kind='repo_url' GROUP BY role"
        )
    }
    return {
        "integrity": str(conn.execute("PRAGMA integrity_check").fetchone()[0]),
        "foreign_key_error_count": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "repository_role_counts": role_counts,
        "legacy_identifier_role_count": int(conn.execute(
            "SELECT COUNT(*) FROM project_identifiers WHERE role=?", (OLD_ROLE,)
        ).fetchone()[0]),
        "legacy_adoption_role_count": int(conn.execute(
            "SELECT COUNT(*) FROM project_identifier_adoptions WHERE role=?", (OLD_ROLE,)
        ).fetchone()[0]),
        "legacy_noncurrent_role_count": int(conn.execute(
            "SELECT COUNT(*) FROM project_noncurrent_identifiers WHERE role=?", (OLD_ROLE,)
        ).fetchone()[0]),
        "legacy_decision_role_count": int(conn.execute(
            "SELECT COUNT(*) FROM project_decisions WHERE target_role=?", (OLD_ROLE,)
        ).fetchone()[0]),
        "legacy_relation_predicate_count": int(conn.execute(
            "SELECT COUNT(*) FROM project_relations WHERE predicate=?", (OLD_ROLE,)
        ).fetchone()[0]),
        "legacy_decision_metadata_role_count": int(conn.execute(
            "SELECT COUNT(*) FROM project_decisions "
            "WHERE target_type='repo_url' AND json_extract(metadata_json, '$.role')=?",
            (OLD_ROLE,),
        ).fetchone()[0]),
        "legacy_profile_link_role_count": int(conn.execute(
            "SELECT COUNT(*) FROM projects, json_each(projects.profile_json, '$.app.links') AS link "
            "WHERE json_extract(link.value, '$.role')='sourceCode'"
        ).fetchone()[0]),
    }


def materialize_copy(source_path: Path, target_path: Path) -> dict[str, Any]:
    source_path = source_path.expanduser().resolve()
    target_path = target_path.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"registry database not found: {source_path}")
    if target_path.exists():
        raise FileExistsError(f"shadow database already exists: {target_path}")
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
                f"project repository materialization requires schema {database.SCHEMA_VERSION}, got {source_version}"
            )
        source.backup(target)
        target.execute("PRAGMA foreign_keys=ON")
        target.execute("BEGIN")
        changes = apply_migration(target)
        checks = validation(target)
        ready = (
            checks["integrity"] == "ok"
            and checks["foreign_key_error_count"] == 0
            and not any(
                checks[key]
                for key in (
                    "legacy_identifier_role_count", "legacy_adoption_role_count",
                    "legacy_noncurrent_role_count", "legacy_decision_role_count",
                    "legacy_relation_predicate_count", "legacy_decision_metadata_role_count",
                    "legacy_profile_link_role_count",
                )
            )
        )
        if not ready:
            raise ValueError(f"project repository materialization validation failed: {checks}")
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
        "materialization_version": MATERIALIZATION_VERSION,
        "source": {"db_path": str(source_path), "read_only": True},
        "target": {"db_path": str(target_path), "production_database": False},
        "changes": changes,
        "validation": checks,
        "review_gate": {"ready": ready, "production_database_changed": False},
    }
