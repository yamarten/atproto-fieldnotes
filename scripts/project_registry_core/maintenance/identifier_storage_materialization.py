from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from project_registry_core import database
from project_registry_core.maintenance import identifier_storage_shadow


SHADOW_SCHEMA_VERSION = 1
REVIEW_SOURCE = "operator_review:identifier_adoption_review_v1"
COMPONENT_ROLES = identifier_storage_shadow.COMPONENT_ROLES
OBSERVATION_ONLY_ROLES = identifier_storage_shadow.OBSERVATION_ONLY_ROLES


def _json(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _claim_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("project_key") or ""),
        str(item.get("kind") or ""),
        str(item.get("value") or ""),
        str(item.get("role") or ""),
    )


def _claim_key_hash(items: list[dict[str, Any]]) -> str:
    lines = ["\t".join(key) for key in sorted(_claim_key(item) for item in items)]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def _row_id(prefix: str, *values: str) -> str:
    payload = "\0".join((prefix, *values))
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _create_shadow_schema(conn: sqlite3.Connection) -> None:
    version = database.database_schema_version(conn)
    if version == 2:
        from project_registry_core.storage import ensure_investigation_tables

        decision_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(project_decisions)")}
        if "target_role" not in decision_columns:
            conn.execute("ALTER TABLE project_decisions ADD COLUMN target_role TEXT NOT NULL DEFAULT ''")
        ensure_investigation_tables(conn)
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(investigation_runs)")}
        for name, definition in (
            ("application_status", "TEXT NOT NULL DEFAULT 'applied'"),
            ("conflict_reason", "TEXT NOT NULL DEFAULT ''"),
            ("resolved_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE investigation_runs ADD COLUMN {name} {definition}")
        database.create_schema(conn)
    elif version == database.SCHEMA_VERSION:
        database.ensure_schema(conn)
    else:
        raise database.SchemaMismatchError(f"unsupported source schema version: {version}")
    conn.execute(
        "INSERT INTO registry_meta(key, value) VALUES('identifier_storage_shadow_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SHADOW_SCHEMA_VERSION),),
    )


def _projected_adoptions(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    decision_analysis = identifier_storage_shadow._decision_role_analysis(conn, example_limit=20)
    projection = identifier_storage_shadow._adoption_projection(
        conn,
        decision_analysis,
        example_limit=20,
        include_rows=True,
    )
    rows = list(projection.pop("adoption_rows"))
    return rows, decision_analysis


def _insert_adoption(conn: sqlite3.Connection, item: dict[str, Any]) -> None:
    project_key, kind, value, role = _claim_key(item)
    adoption_kind = str(item.get("adoption_kind") or "bootstrap")
    source = str(item.get("source") or "")
    source_ref = str(item.get("source_ref") or "")
    created_at = str(item.get("created_at") or "")
    adoption_id = _row_id(
        "adoption",
        project_key,
        kind,
        value,
        role,
        adoption_kind,
        source,
        source_ref,
        created_at,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO project_identifier_adoptions(
            adoption_id, project_key, kind, value, role, adoption_kind, source,
            registration_origin, source_ref, policy_version, input_fingerprint,
            created_at, cleared_at, review_required, review_reason, origin_project_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            adoption_id,
            project_key,
            kind,
            value,
            role,
            adoption_kind,
            source,
            int(bool(item.get("registration_origin"))),
            source_ref,
            str(item.get("policy_version") or ""),
            str(item.get("input_fingerprint") or ""),
            created_at,
            str(item.get("cleared_at") or ""),
            int(bool(item.get("review_required"))),
            str(item.get("review_reason") or ""),
            str(item.get("origin_project_key") or project_key),
        ),
    )


def _apply_decision_roles(conn: sqlite3.Connection, decision_analysis: dict[str, Any]) -> collections.Counter[str]:
    counts: collections.Counter[str] = collections.Counter()
    for item in decision_analysis["mappings"]:
        if item["status"] != "resolved":
            counts[str(item["status"])] += 1
            continue
        conn.execute(
            "UPDATE project_decisions SET target_role=? WHERE project_key=? AND ordinal=?",
            (str(item["target_role"]), str(item["project_key"]), int(item["ordinal"])),
        )
        counts["resolved"] += 1
    return counts


def _apply_active_guards(conn: sqlite3.Connection, decision_analysis: dict[str, Any]) -> int:
    removed = 0
    for item in decision_analysis["mappings"]:
        if (
            item["kind"] not in {"block_identifier", "defer_identifier"}
            or item["state"] not in {"active", "active_invalid_expiry"}
            or not item["target_kind"]
        ):
            continue
        roles = [str(item["target_role"])] if item["status"] == "resolved" else [
            str(row["role"])
            for row in conn.execute(
                "SELECT role FROM project_identifiers WHERE project_key=? AND kind=? AND value=?",
                (str(item["project_key"]), str(item["target_kind"]), str(item["target_value"])),
            ).fetchall()
            if str(row["role"]) in COMPONENT_ROLES
        ]
        for role in roles:
            key = (
                str(item["project_key"]),
                str(item["target_kind"]),
                str(item["target_value"]),
                role,
            )
            if _current_claim(conn, key) is None:
                continue
            _clear_adoptions(
                conn,
                key,
                cleared_at=str(item["created_at"]),
                reason=f"active {item['kind']} during migration",
            )
            conn.execute(
                "DELETE FROM project_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
                key,
            )
            removed += 1
    return removed


def _record_observation(conn: sqlite3.Connection, project_key: str, field: str, value: Any) -> None:
    row = conn.execute("SELECT state_json FROM projects WHERE project_key=?", (project_key,)).fetchone()
    state = _json(str(row["state_json"]), {}) if row else {}
    evidence = state.setdefault("evidence", {})
    values = list(evidence.get(field) or [])
    if value not in values:
        values.append(value)
    evidence[field] = values
    conn.execute("UPDATE projects SET state_json=? WHERE project_key=?", (_dump(state), project_key))


def _move_observation_only_identifiers(conn: sqlite3.Connection) -> collections.Counter[str]:
    counts: collections.Counter[str] = collections.Counter()
    rows = conn.execute(
        "SELECT * FROM project_identifiers WHERE role IN ('source_repository_record', 'authority_domain') ORDER BY project_key, ordinal"
    ).fetchall()
    for row in rows:
        role = str(row["role"])
        if role == "source_repository_record":
            metadata = _json(str(row["metadata_json"]), {})
            metadata.setdefault("url", str(row["value"]))
            metadata.setdefault("classification", str(row["classification"]))
            metadata.setdefault("observed_at", str(row["observed_at"]))
            _record_observation(conn, str(row["project_key"]), "source_repo_records", metadata)
        conn.execute(
            "DELETE FROM project_identifiers WHERE project_key=? AND ordinal=?",
            (str(row["project_key"]), int(row["ordinal"])),
        )
        counts[role] += 1
    return counts


def _compact_child_ordinals(conn: sqlite3.Connection) -> None:
    for table in ("project_identifiers", "project_relations", "project_decisions"):
        rows = conn.execute(
            f"SELECT rowid, project_key FROM {table} ORDER BY project_key, ordinal"
        ).fetchall()
        next_ordinal: dict[str, int] = collections.defaultdict(int)
        assignments: list[tuple[int, int]] = []
        for row in rows:
            project_key = str(row["project_key"])
            ordinal = next_ordinal[project_key]
            next_ordinal[project_key] += 1
            assignments.append((int(row["rowid"]), ordinal))
        for index, (rowid, _ordinal) in enumerate(assignments):
            conn.execute(f"UPDATE {table} SET ordinal=? WHERE rowid=?", (-index - 1, rowid))
        for rowid, ordinal in assignments:
            conn.execute(f"UPDATE {table} SET ordinal=? WHERE rowid=?", (ordinal, rowid))


def _current_claim(conn: sqlite3.Connection, key: tuple[str, str, str, str]) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM project_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        key,
    ).fetchone()


def _clear_adoptions(conn: sqlite3.Connection, key: tuple[str, str, str, str], *, cleared_at: str, reason: str) -> None:
    conn.execute(
        """
        UPDATE project_identifier_adoptions
        SET cleared_at=CASE WHEN cleared_at='' THEN ? ELSE cleared_at END,
            review_reason=CASE WHEN review_reason='' THEN ? ELSE review_reason END
        WHERE project_key=? AND kind=? AND value=? AND role=?
        """,
        (cleared_at, reason, *key),
    )


def _move_to_noncurrent(
    conn: sqlite3.Connection,
    key: tuple[str, str, str, str],
    *,
    standing: str,
    reason: str,
    recorded_at: str,
) -> None:
    row = _current_claim(conn, key)
    if row is None:
        raise ValueError(f"reviewed current claim not found: {key}")
    conn.execute(
        """
        INSERT INTO project_noncurrent_identifiers(
            project_key, kind, value, role, standing, reason, recorded_at,
            origin_project_key, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (*key, standing, reason, recorded_at, key[0], str(row["metadata_json"])),
    )
    conn.execute(
        "DELETE FROM project_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?",
        key,
    )
    _clear_adoptions(
        conn,
        key,
        cleared_at=recorded_at,
        reason=f"moved to {standing}",
    )


def _add_current_claim(conn: sqlite3.Connection, item: dict[str, Any], *, created_at: str) -> None:
    key = _claim_key(item)
    existing = _current_claim(conn, key)
    if existing is None:
        ordinal = int(
            conn.execute(
                "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM project_identifiers WHERE project_key=?",
                (key[0],),
            ).fetchone()[0]
        )
        provider = "github" if "github.com/" in key[2].lower() else "tangled" if "tangled.org/" in key[2].lower() else ""
        conn.execute(
            """
            INSERT INTO project_identifiers(
                project_key, ordinal, kind, value, role, classification,
                is_primary, provider, metadata_json, observed_at
            ) VALUES (?, ?, ?, ?, ?, 'confirmed', 0, ?, '{}', ?)
            """,
            (key[0], ordinal, key[1], key[2], key[3], provider, created_at),
        )
    elif str(existing["classification"]) != "confirmed":
        conn.execute(
            "UPDATE project_identifiers SET classification='confirmed', observed_at=? "
            "WHERE project_key=? AND kind=? AND value=? AND role=?",
            (created_at, *key),
        )
    _insert_adoption(
        conn,
        {
            **item,
            "adoption_kind": "operator",
            "source": REVIEW_SOURCE,
            "created_at": created_at,
        },
    )


def _new_shadow_project_key(conn: sqlite3.Connection, ref: str) -> str:
    base = f"proj:{hashlib.sha256(f'identifier-review:{ref}'.encode('utf-8')).hexdigest()[:12]}"
    if conn.execute("SELECT 1 FROM projects WHERE project_key=?", (base,)).fetchone() is not None:
        raise ValueError(f"deterministic review project key collides: {base}")
    return base


def _insert_separate_project_decision(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    other_project_key: str,
    created_at: str,
) -> None:
    existing = conn.execute(
        """
        SELECT 1 FROM project_decisions
        WHERE project_key=? AND kind='separate_project'
          AND target_type='project_key' AND target_value=? AND cleared_at=''
        """,
        (project_key, other_project_key),
    ).fetchone()
    if existing is not None:
        return
    ordinal = int(
        conn.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM project_decisions WHERE project_key=?",
            (project_key,),
        ).fetchone()[0]
    )
    conn.execute(
        """
        INSERT INTO project_decisions(
            project_key, ordinal, kind, target_type, target_value, target_role,
            note, created_at, expires_at, cleared_at, metadata_json
        ) VALUES (?, ?, 'separate_project', 'project_key', ?, '', ?, ?, '', '', ?)
        """,
        (
            project_key,
            ordinal,
            other_project_key,
            "separated by identifier adoption review",
            created_at,
            _dump({"source": REVIEW_SOURCE}),
        ),
    )


def _create_split_project(conn: sqlite3.Connection, action: dict[str, Any], *, created_at: str) -> dict[str, Any]:
    source_key = str(action["source_project_key"])
    target_key = _new_shadow_project_key(conn, str(action["ref"]))
    source = conn.execute("SELECT * FROM projects WHERE project_key=?", (source_key,)).fetchone()
    if source is None:
        raise ValueError(f"split source project not found: {source_key}")
    website_url = str(action.get("include_related_service") or "")
    conn.execute(
        """
        INSERT INTO projects(
            project_key, name, website_url, service_status, tracking_state,
            introduced_url, profile_json, state_json, first_seen_at, last_seen_at, last_enriched_at
        ) VALUES (?, ?, ?, 'unknown', 'active', '', '{}', '{}', ?, ?, NULL)
        """,
        (target_key, str(action["name"]), website_url, created_at, created_at),
    )
    exact_values = {str(value) for value in action.get("move_values", [])}
    if action.get("include_related_service"):
        exact_values.add(str(action["include_related_service"]))
    prefix = str(action.get("move_nsid_prefix") or "")
    rows = conn.execute("SELECT * FROM project_identifiers WHERE project_key=? ORDER BY ordinal", (source_key,)).fetchall()
    moved_keys: list[tuple[str, str, str, str]] = []
    for row in rows:
        value = str(row["value"])
        matches = value in exact_values or (prefix and str(row["kind"]) == "nsid" and value.startswith(prefix))
        if not matches:
            continue
        old_key = (source_key, str(row["kind"]), value, str(row["role"]))
        ordinal = int(
            conn.execute(
                "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM project_identifiers WHERE project_key=?",
                (target_key,),
            ).fetchone()[0]
        )
        conn.execute(
            """
            INSERT INTO project_identifiers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_key,
                ordinal,
                str(row["kind"]),
                value,
                str(row["role"]),
                str(row["classification"]),
                int(row["is_primary"]),
                str(row["provider"]),
                str(row["metadata_json"]),
                str(row["observed_at"]),
            ),
        )
        conn.execute("DELETE FROM project_identifiers WHERE project_key=? AND ordinal=?", (source_key, int(row["ordinal"])))
        if str(row["role"]) not in OBSERVATION_ONLY_ROLES:
            conn.execute(
                """
                UPDATE project_identifier_adoptions
                SET project_key=?, origin_project_key=CASE WHEN origin_project_key='' THEN ? ELSE origin_project_key END
                WHERE project_key=? AND kind=? AND value=? AND role=?
                """,
                (target_key, source_key, *old_key),
            )
            _insert_adoption(
                conn,
                {
                    "project_key": target_key,
                    "kind": str(row["kind"]),
                    "value": value,
                    "role": str(row["role"]),
                    "adoption_kind": "operator",
                    "source": REVIEW_SOURCE,
                    "created_at": created_at,
                    "origin_project_key": source_key,
                },
            )
        moved_keys.append(old_key)
    related_service = str(action.get("include_related_service") or "")
    if related_service:
        _add_current_claim(
            conn,
            {"project_key": target_key, "kind": "service_url", "value": related_service, "role": "service"},
            created_at=created_at,
        )
    moved_values = exact_values | {key[2] for key in moved_keys}
    relation_rows = conn.execute("SELECT * FROM project_relations WHERE project_key=? ORDER BY ordinal", (source_key,)).fetchall()
    moved_relation_count = 0
    for row in relation_rows:
        if str(row["subject_value"]) not in moved_values and str(row["object_value"]) not in moved_values:
            continue
        ordinal = int(
            conn.execute(
                "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM project_relations WHERE project_key=?",
                (target_key,),
            ).fetchone()[0]
        )
        values = [row[index] for index in range(len(row))]
        values[0] = target_key
        values[1] = ordinal
        if str(values[2]) == "project" and str(values[3]) == source_key:
            values[3] = target_key
        conn.execute(
            "INSERT OR IGNORE INTO project_relations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(values),
        )
        conn.execute("DELETE FROM project_relations WHERE project_key=? AND ordinal=?", (source_key, int(row["ordinal"])))
        moved_relation_count += 1
    _insert_separate_project_decision(
        conn,
        project_key=source_key,
        other_project_key=target_key,
        created_at=created_at,
    )
    _insert_separate_project_decision(
        conn,
        project_key=target_key,
        other_project_key=source_key,
        created_at=created_at,
    )
    return {
        "ref": str(action["ref"]),
        "source_project_key": source_key,
        "target_project_key": target_key,
        "moved_identifier_count": len(moved_keys),
        "moved_relation_count": moved_relation_count,
    }


def _apply_review_manifest(
    conn: sqlite3.Connection,
    manifest: dict[str, Any],
    reviewed_claims: list[dict[str, Any]],
) -> dict[str, Any]:
    reviewed_at = str(manifest["reviewed_at"])
    expected = dict(manifest["reviewed_projection"])
    if expected.get("default_action") != "keep_current_with_operator_adoption":
        raise ValueError("unsupported default review action")
    actual_hash = _claim_key_hash(reviewed_claims)
    if len(reviewed_claims) != int(expected["claim_count"]) or actual_hash != str(expected["claim_key_sha256"]):
        raise ValueError(
            "reviewed projection changed: "
            f"expected {expected['claim_count']} {expected['claim_key_sha256']}, "
            f"got {len(reviewed_claims)} {actual_hash}"
        )
    action_items = list(manifest.get("claim_actions", []))
    actions = {_claim_key(item): dict(item) for item in action_items}
    if len(actions) != len(action_items):
        raise ValueError("review manifest contains duplicate claim actions")
    reviewed_keys = {_claim_key(item) for item in reviewed_claims}
    if not set(actions).issubset(reviewed_keys):
        unexpected = sorted(set(actions) - reviewed_keys)
        raise ValueError(f"review manifest action is outside reviewed projection: {unexpected[:3]}")
    counts: collections.Counter[str] = collections.Counter()
    missing: list[tuple[str, str, str, str]] = []
    for claim in reviewed_claims:
        key = _claim_key(claim)
        action = str(actions.get(key, {}).get("action") or "keep_current_with_operator_adoption")
        if _current_claim(conn, key) is None:
            missing.append(key)
            continue
        _clear_adoptions(
            conn,
            key,
            cleared_at=reviewed_at,
            reason="superseded by operator review",
        )
        if action == "keep_current_with_operator_adoption":
            _insert_adoption(
                conn,
                {**claim, "adoption_kind": "operator", "source": REVIEW_SOURCE, "created_at": reviewed_at},
            )
        elif action == "website_only":
            conn.execute("DELETE FROM project_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?", key)
        elif action in {"remove_unrelated", "discard_normalized_alias"}:
            conn.execute("DELETE FROM project_identifiers WHERE project_key=? AND kind=? AND value=? AND role=?", key)
            if action == "remove_unrelated":
                conn.execute("UPDATE projects SET website_url='' WHERE project_key=? AND website_url=?", (key[0], key[2]))
        elif action in {"former", "non_production"}:
            _move_to_noncurrent(conn, key, standing=action, reason=REVIEW_SOURCE, recorded_at=reviewed_at)
            _insert_adoption(
                conn,
                {
                    **claim,
                    "adoption_kind": "operator",
                    "source": REVIEW_SOURCE,
                    "created_at": reviewed_at,
                    "cleared_at": reviewed_at,
                    "review_reason": f"moved to {action}",
                },
            )
        elif action == "move_to_separate_project":
            pass
        else:
            raise ValueError(f"unsupported review action: {action}")
        counts[action] += 1
    for item in manifest.get("additional_claim_actions", []):
        key = _claim_key(item)
        action = str(item["action"])
        _move_to_noncurrent(conn, key, standing=action, reason=REVIEW_SOURCE, recorded_at=reviewed_at)
        _insert_adoption(
            conn,
            {
                **item,
                "adoption_kind": "operator",
                "source": REVIEW_SOURCE,
                "created_at": reviewed_at,
                "cleared_at": reviewed_at,
                "review_reason": f"moved to {action}",
            },
        )
        counts[f"additional_{action}"] += 1
    for item in manifest.get("add_current_claims", []):
        _add_current_claim(conn, item, created_at=reviewed_at)
        counts["add_current"] += 1
    for item in manifest.get("project_updates", []):
        assignments: list[str] = []
        values: list[str] = []
        for field in ("name", "tracking_state"):
            if field in item:
                assignments.append(f"{field}=?")
                values.append(str(item[field]))
        if assignments:
            conn.execute(
                f"UPDATE projects SET {', '.join(assignments)} WHERE project_key=?",
                (*values, str(item["project_key"])),
            )
            counts["project_update"] += 1
    split_results = [
        _create_split_project(conn, dict(action), created_at=reviewed_at)
        for action in manifest.get("separate_project_actions", [])
    ]
    if missing:
        raise ValueError(f"reviewed claims missing from copied database: {missing[:3]}")
    return {
        "reviewed_claim_count": len(reviewed_claims) + len(manifest.get("additional_claim_actions", [])),
        "reviewed_projection_hash": actual_hash,
        "action_counts": dict(sorted(counts.items())),
        "split_results": split_results,
    }


def _normalize_reviewed_websites(conn: sqlite3.Connection) -> None:
    replacements = {
        ("proj:3efb7d27c3ef", "http://moat.social"): "https://moat.social",
        ("proj:6b4a455e4168", "http://close.community"): "https://close.community",
        ("proj:c292e0ca6655", "http://docs.bridgebeats.link"): "https://docs.bridgebeats.link",
        ("proj:f9a4c3238ea0", "http://trotsky.pirhoo.com"): "https://trotsky.pirhoo.com",
    }
    for (project_key, old), new in replacements.items():
        conn.execute(
            "UPDATE projects SET website_url=? WHERE project_key=? AND website_url=?",
            (new, project_key, old),
        )


def _reset_service_status_for_nonservice_reviews(
    conn: sqlite3.Connection,
    manifest: dict[str, Any],
) -> list[dict[str, str]]:
    project_keys = {
        str(item["project_key"])
        for item in manifest.get("claim_actions", [])
        if item.get("kind") == "service_url"
        and item.get("action") in {"website_only", "remove_unrelated", "non_production"}
    }
    reset: list[dict[str, str]] = []
    for project_key in sorted(project_keys):
        has_confirmed_service = conn.execute(
            "SELECT 1 FROM project_identifiers "
            "WHERE project_key=? AND role='service' AND classification='confirmed' LIMIT 1",
            (project_key,),
        ).fetchone()
        if has_confirmed_service is not None:
            continue
        row = conn.execute(
            "SELECT name, service_status, state_json FROM projects WHERE project_key=?",
            (project_key,),
        ).fetchone()
        if row is None:
            continue
        state = _json(str(row["state_json"]), {})
        ops = state.get("ops")
        if isinstance(ops, dict):
            ops["service_status_confidence"] = "none"
            ops["service_status_evidence"] = []
        conn.execute(
            "UPDATE projects SET service_status='none', state_json=? WHERE project_key=?",
            (_dump(state), project_key),
        )
        reset.append(
            {
                "project_key": project_key,
                "project_name": str(row["name"]),
                "previous_status": str(row["service_status"]),
            }
        )
    return reset


def _validation(conn: sqlite3.Connection) -> dict[str, Any]:
    confirmed_without_adoption = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM project_identifiers i
            WHERE i.classification='confirmed'
              AND i.role IN ('service', 'project_repository', 'owned_nsid', 'project_account', 'project_handle', 'brand_did')
              AND NOT EXISTS (
                  SELECT 1 FROM project_identifier_adoptions a
                  WHERE a.project_key=i.project_key AND a.kind=i.kind AND a.value=i.value AND a.role=i.role
                    AND a.cleared_at='' AND a.review_required=0
              )
            """
        ).fetchone()[0]
    )
    return {
        "integrity": str(conn.execute("PRAGMA integrity_check").fetchone()[0]),
        "foreign_key_error_count": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "current_noncurrent_overlap_count": int(
            conn.execute(
                """
                SELECT COUNT(*) FROM project_identifiers c
                JOIN project_noncurrent_identifiers n
                  ON n.project_key=c.project_key AND n.kind=c.kind AND n.value=c.value AND n.role=c.role
                """
            ).fetchone()[0]
        ),
        "confirmed_component_without_active_adoption_count": confirmed_without_adoption,
    }


def materialize_reviewed_copy(
    source_path: Path,
    target_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    source_path = Path(source_path).expanduser().resolve()
    target_path = Path(target_path).expanduser().resolve()
    manifest_path = Path(manifest_path).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version", 0)) != 1:
        raise ValueError("unsupported identifier adoption review manifest")
    with database.materialized_database_copy(
        source_path, target_path,
    ) as (target, source_schema_version):
        before = {
            "project_count": int(target.execute("SELECT COUNT(*) FROM projects").fetchone()[0]),
            "identifier_count": int(target.execute("SELECT COUNT(*) FROM project_identifiers").fetchone()[0]),
            "confirmed_component_count": int(
                target.execute(
                    "SELECT COUNT(*) FROM project_identifiers WHERE classification='confirmed' "
                    "AND role IN ('service', 'project_repository', 'owned_nsid', 'project_account', 'project_handle', 'brand_did')"
                ).fetchone()[0]
            ),
        }
        _create_shadow_schema(target)
        adoption_rows, decision_analysis = _projected_adoptions(target)
        reviewed_claims = [
            row
            for row in adoption_rows
            if str(row.get("source") or "") in {"migration_confirmed_relation", "migration_legacy_project_context"}
        ]
        for item in adoption_rows:
            _insert_adoption(target, item)
        decision_counts = _apply_decision_roles(target, decision_analysis)
        active_guard_removed_count = _apply_active_guards(target, decision_analysis)
        review = _apply_review_manifest(target, manifest, reviewed_claims)
        _normalize_reviewed_websites(target)
        service_status_resets = _reset_service_status_for_nonservice_reviews(target, manifest)
        observation_counts = _move_observation_only_identifiers(target)
        _compact_child_ordinals(target)
        validation = _validation(target)
        after = {
            "project_count": int(target.execute("SELECT COUNT(*) FROM projects").fetchone()[0]),
            "identifier_count": int(target.execute("SELECT COUNT(*) FROM project_identifiers").fetchone()[0]),
            "confirmed_component_count": int(
                target.execute(
                    "SELECT COUNT(*) FROM project_identifiers WHERE classification='confirmed' "
                    "AND role IN ('service', 'project_repository', 'owned_nsid', 'project_account', 'project_handle', 'brand_did')"
                ).fetchone()[0]
            ),
            "noncurrent_identifier_count": int(target.execute("SELECT COUNT(*) FROM project_noncurrent_identifiers").fetchone()[0]),
            "adoption_count": int(target.execute("SELECT COUNT(*) FROM project_identifier_adoptions").fetchone()[0]),
        }
    return {
        "shadow_schema_version": SHADOW_SCHEMA_VERSION,
        "source": {
            "db_path": str(source_path),
            "read_only": True,
            "schema_version": source_schema_version,
        },
        "target": {
            "db_path": str(target_path),
            "production_database": False,
            "schema_version": database.SCHEMA_VERSION,
        },
        "manifest": str(manifest_path),
        "before": before,
        "after": after,
        "decision_role_mapping": dict(sorted(decision_counts.items())),
        "active_guard_removed_claim_count": active_guard_removed_count,
        "service_status_resets": service_status_resets,
        "observation_role_cleanup": dict(sorted(observation_counts.items())),
        "review": review,
        "validation": validation,
        "review_gate": {
            "ready": validation == {
                "integrity": "ok",
                "foreign_key_error_count": 0,
                "current_noncurrent_overlap_count": 0,
                "confirmed_component_without_active_adoption_count": 0,
            },
            "production_schema_changed": False,
            "production_database_changed": False,
        },
    }
