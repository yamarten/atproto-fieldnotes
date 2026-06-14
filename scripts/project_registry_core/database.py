from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
CLASSIFICATIONS = {"confirmed", "candidate", "derived", "heuristic"}


class SchemaMismatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectIdentifier:
    kind: str
    value: str
    role: str
    classification: str
    is_primary: bool = False
    provider: str = ""
    metadata: dict[str, Any] | None = None
    observed_at: str = ""


@dataclass(frozen=True)
class ProjectDecision:
    kind: str
    target_type: str = ""
    target_value: str = ""
    note: str = ""
    created_at: str = ""
    expires_at: str = ""
    cleared_at: str = ""
    metadata: dict[str, Any] | None = None


IDENTIFIER_FIELDS = {
    "primary_nsid",
    "input_nsids",
    "resolved_nsids",
    "lexicons",
    "external_lexicons",
    "recorded_lexicon_namespaces",
    "authority_domains",
    "website_url_candidates",
    "service_urls",
    "service_url_candidates",
    "source_code_url",
    "source_code_urls",
    "candidate_source_code_urls",
    "github_repo_urls",
    "tangled_repo_urls",
    "source_repo_records",
    "developer_accounts",
    "candidate_developer_accounts",
    "bluesky_handles",
    "tangled_handles",
    "manual_seeds",
    "brand_did",
}

DECISION_FIELDS = {
    "blocked_identifiers",
    "deferred_identifiers",
    "promotion_deferrals",
    "dismissed_review_kinds",
    "separate_project_keys",
}

NORMALIZED_FIELDS = IDENTIFIER_FIELDS | DECISION_FIELDS | {"relation_evidence", "resource_urls", "introduced"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    parsed = json.loads(value)
    return parsed


def database_schema_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'registry_meta'"
    ).fetchone()
    if row is None:
        return None
    value = conn.execute("SELECT value FROM registry_meta WHERE key = 'schema_version'").fetchone()
    return int(value[0]) if value else None


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS registry_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            project_key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            website_url TEXT NOT NULL,
            service_status TEXT NOT NULL,
            tracking_state TEXT NOT NULL DEFAULT 'active',
            introduced_url TEXT NOT NULL DEFAULT '',
            profile_json TEXT NOT NULL DEFAULT '{}',
            state_json TEXT NOT NULL DEFAULT '{}',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_enriched_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_projects_tracking_state ON projects(tracking_state);
        CREATE TABLE IF NOT EXISTS project_identifiers (
            project_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            role TEXT NOT NULL,
            classification TEXT NOT NULL,
            is_primary INTEGER NOT NULL DEFAULT 0,
            provider TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            observed_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(project_key, ordinal),
            UNIQUE(project_key, kind, value, role),
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_project_identifiers_lookup
            ON project_identifiers(kind, value, classification);
        CREATE TABLE IF NOT EXISTS project_relations (
            project_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            subject_type TEXT NOT NULL,
            subject_value TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_value TEXT NOT NULL,
            classification TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            locator TEXT NOT NULL DEFAULT '',
            method TEXT NOT NULL DEFAULT '',
            context_json TEXT NOT NULL DEFAULT '{}',
            observed_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(project_key, ordinal),
            UNIQUE(project_key, subject_type, subject_value, predicate, object_type, object_value, source, locator, method),
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_project_relations_object
            ON project_relations(predicate, object_type, object_value, classification);
        CREATE TABLE IF NOT EXISTS project_decisions (
            project_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT '',
            target_value TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL DEFAULT '',
            cleared_at TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(project_key, ordinal),
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_project_decisions_kind
            ON project_decisions(project_key, kind, expires_at);
        CREATE TABLE IF NOT EXISTS sync_state (
            scope_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            scope_json TEXT NOT NULL DEFAULT '{}',
            watermark_json TEXT NOT NULL DEFAULT '{}',
            last_success_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO registry_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )


def open_database(path: Path, *, create: bool = True) -> sqlite3.Connection:
    path = Path(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    version = database_schema_version(conn)
    has_projects = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
    ).fetchone() is not None
    if version is None and has_projects:
        conn.close()
        raise SchemaMismatchError(f"legacy registry database requires migrate-db: {path}")
    if version not in {None, SCHEMA_VERSION}:
        conn.close()
        raise SchemaMismatchError(f"unsupported registry schema version {version}: {path}")
    create_schema(conn)
    return conn


def _provider(value: str) -> str:
    lowered = value.lower()
    if "github.com/" in lowered:
        return "github"
    if "tangled.org/" in lowered or "tangled.sh/" in lowered:
        return "tangled"
    if lowered.startswith("did:"):
        return "atproto"
    return ""


def project_identifiers(project: dict[str, Any]) -> list[ProjectIdentifier]:
    output: list[ProjectIdentifier] = []
    seen: set[tuple[str, str, str]] = set()

    def add(kind: str, value: Any, role: str, classification: str, *, primary: bool = False,
            provider: str = "", metadata: dict[str, Any] | None = None, observed_at: str = "") -> None:
        text = str(value or "").strip()
        if not text or classification not in CLASSIFICATIONS:
            return
        key = (kind, text, role)
        if key in seen:
            return
        seen.add(key)
        output.append(ProjectIdentifier(
            kind=kind,
            value=text,
            role=role,
            classification=classification,
            is_primary=primary,
            provider=provider or _provider(text),
            metadata=metadata or {},
            observed_at=observed_at,
        ))

    primary_nsid = str(project.get("primary_nsid") or "")
    add("nsid", primary_nsid, "primary_nsid", "confirmed", primary=True)
    for value in project.get("input_nsids", []):
        add("nsid", value, "input_nsid", "candidate")
    for value in project.get("resolved_nsids", []):
        add("nsid", value, "owned_nsid", "confirmed")
    for value in project.get("lexicons", []):
        add("nsid", value, "observed_lexicon", "candidate")
    for value in project.get("external_lexicons", []):
        add("nsid", value, "referenced_nsid", "candidate")
    for value in project.get("recorded_lexicon_namespaces", []):
        add("nsid", value, "recorded_namespace", "derived")
    for value in project.get("authority_domains", []):
        add("domain", value, "authority_domain", "derived")

    website = str(project.get("website_url") or "")
    add("service_url", website, "website", "confirmed", primary=True)
    for value in project.get("service_urls", []):
        add("service_url", value, "service", "confirmed")
    for value in project.get("service_url_candidates", []):
        add("service_url", value, "service", "candidate")

    source = str(project.get("source_code_url") or "")
    add("repo_url", source, "source_repository", "confirmed", primary=True)
    for value in project.get("source_code_urls", []):
        add("repo_url", value, "source_repository", "confirmed")
    for value in project.get("candidate_source_code_urls", []):
        add("repo_url", value, "source_repository", "candidate")
    for item in project.get("source_repo_records", []):
        if not isinstance(item, dict):
            continue
        classification = str(item.get("confidence") or item.get("classification") or "candidate")
        if classification not in CLASSIFICATIONS:
            classification = "confirmed" if classification == "owned" else "candidate"
        add(
            "repo_url",
            item.get("url"),
            "source_repository_record",
            classification,
            metadata=dict(item),
            observed_at=str(item.get("observed_at") or ""),
        )

    for field, classification in (("developer_accounts", "confirmed"), ("candidate_developer_accounts", "candidate")):
        for item in project.get(field, []):
            if not isinstance(item, dict):
                continue
            value = str(item.get("did") or item.get("handle") or item.get("url") or "")
            add("account", value, "developer_account", classification, metadata=dict(item))
            add("did", item.get("did"), "developer_did", classification, metadata={"account": value})
    add("did", project.get("brand_did"), "brand_did", "confirmed", primary=True)
    for field, provider in (("bluesky_handles", "bluesky"), ("tangled_handles", "tangled")):
        for value in project.get(field, []):
            add("account", value, "project_account", "candidate", provider=provider)

    seed_kind = {"service_url": "service_url", "repo_url": "repo_url", "nsid": "nsid"}
    for item in project.get("manual_seeds", []):
        if not isinstance(item, dict):
            continue
        kind = seed_kind.get(str(item.get("type") or ""))
        if kind:
            add(kind, item.get("value"), "manual_seed", "confirmed", metadata=dict(item), observed_at=str(item.get("added_at") or ""))
    return output


def project_decisions(project: dict[str, Any]) -> list[ProjectDecision]:
    output: list[ProjectDecision] = []
    for item in project.get("blocked_identifiers", []):
        if isinstance(item, dict):
            output.append(ProjectDecision("block_identifier", str(item.get("type") or ""), str(item.get("value") or ""),
                                          str(item.get("note") or ""), str(item.get("added_at") or ""), metadata=dict(item)))
    for item in project.get("deferred_identifiers", []):
        if isinstance(item, dict):
            output.append(ProjectDecision("defer_identifier", str(item.get("type") or ""), str(item.get("value") or ""),
                                          str(item.get("note") or ""), str(item.get("added_at") or ""),
                                          str(item.get("expires_at") or ""), metadata=dict(item)))
    for item in project.get("promotion_deferrals", []):
        if isinstance(item, dict):
            output.append(ProjectDecision("defer_promotion", note=str(item.get("reason") or ""),
                                          created_at=str(item.get("added_at") or ""), expires_at=str(item.get("expires_at") or ""),
                                          cleared_at=str(item.get("cleared_at") or ""), metadata=dict(item)))
    for value in project.get("dismissed_review_kinds", []):
        output.append(ProjectDecision("dismiss_review", "review_kind", str(value)))
    for value in project.get("separate_project_keys", []):
        output.append(ProjectDecision("separate_project", "project_key", str(value)))
    return output


def strip_normalized_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in NORMALIZED_FIELDS}


def replace_project_children(conn: sqlite3.Connection, project: dict[str, Any]) -> None:
    project_key = str(project["project_key"])
    conn.execute("DELETE FROM project_identifiers WHERE project_key = ?", (project_key,))
    for ordinal, item in enumerate(project_identifiers(project)):
        conn.execute(
            "INSERT INTO project_identifiers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_key, ordinal, item.kind, item.value, item.role, item.classification, int(item.is_primary),
             item.provider, _json(item.metadata or {}), item.observed_at),
        )

    conn.execute("DELETE FROM project_relations WHERE project_key = ?", (project_key,))
    seen_relations: set[tuple[str, ...]] = set()
    ordinal = 0
    for item in project.get("relation_evidence", []):
        if not isinstance(item, dict):
            continue
        relation = item.get("relation") or item.get("value") or item
        if not isinstance(relation, dict):
            continue
        subject = relation.get("subject") or {}
        obj = relation.get("object") or {}
        values = (
            str(subject.get("type") or ""), str(subject.get("value") or ""), str(relation.get("predicate") or ""),
            str(obj.get("type") or ""), str(obj.get("value") or ""), str(item.get("source") or ""),
            str(relation.get("locator") or ""), str(relation.get("method") or ""),
        )
        if not all(values[:5]) or values in seen_relations:
            continue
        seen_relations.add(values)
        classification = str(item.get("classification") or "candidate")
        if classification not in CLASSIFICATIONS:
            classification = "candidate"
        conn.execute(
            "INSERT INTO project_relations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_key, ordinal, *values[:5], classification, values[5], values[6], values[7],
             _json(item.get("context") or {}), str(item.get("observed_at") or "")),
        )
        ordinal += 1

    conn.execute("DELETE FROM project_decisions WHERE project_key = ?", (project_key,))
    for ordinal, item in enumerate(project_decisions(project)):
        conn.execute(
            "INSERT INTO project_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_key, ordinal, item.kind, item.target_type, item.target_value, item.note,
             item.created_at, item.expires_at, item.cleared_at, _json(item.metadata or {})),
        )


def hydrate_project_children(conn: sqlite3.Connection, project: dict[str, Any]) -> dict[str, Any]:
    project_key = str(project.get("project_key") or "")
    identifiers = conn.execute(
        "SELECT * FROM project_identifiers WHERE project_key = ? ORDER BY ordinal", (project_key,)
    ).fetchall()
    project.update({
        "input_nsids": [], "resolved_nsids": [], "lexicons": [], "external_lexicons": [],
        "recorded_lexicon_namespaces": [], "authority_domains": [], "service_urls": [],
        "service_url_candidates": [], "source_code_urls": [], "candidate_source_code_urls": [],
        "source_repo_records": [], "developer_accounts": [], "candidate_developer_accounts": [],
        "bluesky_handles": [], "tangled_handles": [], "manual_seeds": [],
    })
    for row in identifiers:
        kind, value, role = str(row["kind"]), str(row["value"]), str(row["role"])
        classification = str(row["classification"])
        metadata = _load_json(str(row["metadata_json"]), {})
        if role == "manual_seed":
            project["manual_seeds"].append(metadata or {"type": kind, "value": value})
            continue
        if kind == "nsid":
            if role == "primary_nsid": project["primary_nsid"] = value
            elif role == "input_nsid": project["input_nsids"].append(value)
            elif role == "owned_nsid": project["resolved_nsids"].append(value)
            elif role == "observed_lexicon": project["lexicons"].append(value)
            elif role == "referenced_nsid": project["external_lexicons"].append(value)
            elif role == "recorded_namespace": project["recorded_lexicon_namespaces"].append(value)
        elif kind == "domain" and role == "authority_domain": project["authority_domains"].append(value)
        elif kind == "service_url":
            if classification == "confirmed": project["service_urls"].append(value)
            else: project["service_url_candidates"].append(value)
        elif kind == "repo_url":
            if bool(row["is_primary"]): project["source_code_url"] = value
            if classification == "confirmed": project["source_code_urls"].append(value)
            else: project["candidate_source_code_urls"].append(value)
            if metadata and role == "source_repository_record": project["source_repo_records"].append(metadata)
        elif kind == "account":
            if role == "developer_account":
                target = "developer_accounts" if classification == "confirmed" else "candidate_developer_accounts"
                project[target].append(metadata or {"handle": value})
            elif str(row["provider"]) == "bluesky": project["bluesky_handles"].append(value)
            elif str(row["provider"]) == "tangled": project["tangled_handles"].append(value)
        elif kind == "did" and role == "brand_did": project["brand_did"] = value

    relations = []
    for row in conn.execute("SELECT * FROM project_relations WHERE project_key = ? ORDER BY ordinal", (project_key,)):
        relations.append({
            "relation": {
                "subject": {"type": str(row["subject_type"]), "value": str(row["subject_value"])},
                "predicate": str(row["predicate"]),
                "object": {"type": str(row["object_type"]), "value": str(row["object_value"])},
                "locator": str(row["locator"]), "method": str(row["method"]),
            },
            "source": str(row["source"]), "classification": str(row["classification"]),
            "observed_at": str(row["observed_at"]), "context": _load_json(str(row["context_json"]), {}),
        })
    project["relation_evidence"] = relations

    project.update({"blocked_identifiers": [], "deferred_identifiers": [], "promotion_deferrals": [],
                    "dismissed_review_kinds": [], "separate_project_keys": []})
    for row in conn.execute("SELECT * FROM project_decisions WHERE project_key = ? ORDER BY ordinal", (project_key,)):
        kind = str(row["kind"])
        metadata = _load_json(str(row["metadata_json"]), {})
        if kind == "block_identifier": project["blocked_identifiers"].append(metadata or {
            "type": str(row["target_type"]), "value": str(row["target_value"]), "note": str(row["note"]), "added_at": str(row["created_at"])
        })
        elif kind == "defer_identifier": project["deferred_identifiers"].append(metadata or {
            "type": str(row["target_type"]), "value": str(row["target_value"]), "note": str(row["note"]),
            "added_at": str(row["created_at"]), "expires_at": str(row["expires_at"])
        })
        elif kind == "defer_promotion": project["promotion_deferrals"].append(metadata or {
            "reason": str(row["note"]), "added_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"]), "cleared_at": str(row["cleared_at"])
        })
        elif kind == "dismiss_review": project["dismissed_review_kinds"].append(str(row["target_value"]))
        elif kind == "separate_project": project["separate_project_keys"].append(str(row["target_value"]))
    return project


def copy_table_rows(source: sqlite3.Connection, target: sqlite3.Connection, table: str) -> None:
    exists = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not exists:
        return
    rows = source.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return
    columns = [str(item[1]) for item in source.execute(f"PRAGMA table_info({table})")]
    placeholders = ",".join("?" for _ in columns)
    target.executemany(
        f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def legacy_flatten(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    flattened: dict[str, Any] = {}
    for value in payload.values():
        if isinstance(value, dict):
            flattened.update(value)
    return flattened


def legacy_project_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = legacy_flatten(_load_json(str(row["record_json"] or "{}"), {}))
    payload.update(legacy_flatten(_load_json(str(row["ops_json"] or "{}"), {})))
    for field in ("project_key", "name", "website_url", "service_status", "tracking_state", "introduced_url",
                  "first_seen_at", "last_seen_at", "last_enriched_at"):
        if field in row.keys():
            payload[field] = row[field]
    payload["service_url_candidates"] = list(dict.fromkeys(
        list(payload.get("service_url_candidates") or []) + list(payload.get("website_url_candidates") or [])
    ))
    payload["source_code_urls"] = list(dict.fromkeys(
        list(payload.get("source_code_urls") or []) + list(payload.get("github_repo_urls") or []) + list(payload.get("tangled_repo_urls") or [])
    ))
    if payload.get("resource_urls") and not payload.get("resource_url_records"):
        payload["resource_url_records"] = [
            {"url": value, "role": "unknown", "source": "legacy_resource_urls", "observed_at": "", "confidence": "legacy"}
            for value in payload["resource_urls"]
        ]
    return payload


def migrate_legacy_project_snapshot(payload: Any) -> dict[str, Any]:
    project = dict(payload) if isinstance(payload, dict) else {}
    project["service_url_candidates"] = list(dict.fromkeys(
        list(project.get("service_url_candidates") or []) + list(project.get("website_url_candidates") or [])
    ))
    project["source_code_urls"] = list(dict.fromkeys(
        list(project.get("source_code_urls") or [])
        + list(project.get("github_repo_urls") or [])
        + list(project.get("tangled_repo_urls") or [])
    ))
    if project.get("resource_urls") and not project.get("resource_url_records"):
        project["resource_url_records"] = [
            {"url": value, "role": "unknown", "source": "legacy_resource_urls", "observed_at": "", "confidence": "legacy"}
            for value in project["resource_urls"]
        ]
    for field in ("website_url_candidates", "github_repo_urls", "tangled_repo_urls", "resource_urls", "introduced"):
        project.pop(field, None)
    return project


def migrate_legacy_bundle(content: str) -> str:
    from .evidence import EvidenceBundle, populate_collector_results

    payload = _load_json(content, {})
    if not isinstance(payload, dict):
        raise ValueError("investigation bundle must be an object")
    if int(payload.get("schema_version") or 0) == 1:
        payload["schema_version"] = 2
        payload.setdefault("collector_results", [])
    bundle = EvidenceBundle.from_dict(payload)
    populate_collector_results(bundle)
    return _json(bundle.to_dict())


def migrate_investigation_rows(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    exists = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_runs'"
    ).fetchone()
    if not exists:
        return
    for row in source.execute("SELECT * FROM investigation_runs ORDER BY rowid"):
        bundle_json = migrate_legacy_bundle(str(row["bundle_json"]))
        target.execute(
            """
            INSERT INTO investigation_runs(
                run_id, project_key, started_at, completed_at, evidence_schema_version,
                evaluation_version, input_project_json, output_project_json, evaluation_json, bundle_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["run_id"], row["project_key"], row["started_at"], row["completed_at"], 2,
                row["evaluation_version"],
                _json(migrate_legacy_project_snapshot(_load_json(str(row["input_project_json"]), {}))),
                _json(migrate_legacy_project_snapshot(_load_json(str(row["output_project_json"]), {}))),
                row["evaluation_json"], bundle_json,
            ),
        )


def validate_migrated_projects(
    source_projects: list[dict[str, Any]],
    target: sqlite3.Connection,
) -> None:
    from . import registry

    expected_keys = [str(project.get("project_key") or "") for project in source_projects]
    actual_keys = [str(row[0]) for row in target.execute("SELECT project_key FROM projects ORDER BY project_key")]
    if sorted(expected_keys) != actual_keys:
        raise RuntimeError("project key verification failed")
    for source_project in source_projects:
        project_key = str(source_project.get("project_key") or "")
        migrated = registry.fetch_project(target, project_key)
        if migrated is None:
            raise RuntimeError(f"migrated project missing: {project_key}")
        for field in ("name", "website_url", "service_status", "tracking_state", "introduced_url"):
            if str(migrated.get(field) or "") != str(source_project.get(field) or ""):
                raise RuntimeError(f"canonical field mismatch: {project_key} {field}")
        for field in ("service_url_candidates", "source_code_urls"):
            expected_values = {str(value) for value in source_project.get(field, []) if str(value)}
            actual_values = {str(value) for value in migrated.get(field, []) if str(value)}
            if not expected_values.issubset(actual_values):
                raise RuntimeError(f"canonical identifier mismatch: {project_key} {field}")


def validate_database(conn: sqlite3.Connection, *, expected_projects: int | None = None) -> dict[str, Any]:
    integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    project_count = int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    if integrity != "ok" or foreign_keys:
        raise RuntimeError(f"database validation failed: integrity={integrity} foreign_keys={len(foreign_keys)}")
    if expected_projects is not None and project_count != expected_projects:
        raise RuntimeError(f"project count mismatch: expected={expected_projects} actual={project_count}")
    return {
        "schema_version": database_schema_version(conn),
        "project_count": project_count,
        "identifier_count": int(conn.execute("SELECT COUNT(*) FROM project_identifiers").fetchone()[0]),
        "relation_count": int(conn.execute("SELECT COUNT(*) FROM project_relations").fetchone()[0]),
        "decision_count": int(conn.execute("SELECT COUNT(*) FROM project_decisions").fetchone()[0]),
        "integrity": integrity,
    }


def backup_path(path: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.v1-backup-{timestamp}")
    ordinal = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.v1-backup-{timestamp}-{ordinal}")
        ordinal += 1
    return candidate


def migrate_database(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    source_mode = stat.S_IMODE(path.stat().st_mode)
    probe = sqlite3.connect(path)
    version = database_schema_version(probe)
    probe.close()
    if version == SCHEMA_VERSION:
        conn = open_database(path, create=False)
        try:
            return {"status": "already_current", "path": str(path), **validate_database(conn)}
        finally:
            conn.close()
    if version is not None:
        raise SchemaMismatchError(f"unsupported source schema version {version}: {path}")

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.v2-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    source = sqlite3.connect(path)
    source.row_factory = sqlite3.Row
    target = open_database(temp_path)
    try:
        from . import registry
        from . import storage as investigation_storage

        investigation_storage.ensure_investigation_tables(target)

        rows = source.execute("SELECT * FROM projects ORDER BY project_key").fetchall()
        source_projects = [legacy_project_payload(row) for row in rows]
        for project_payload in source_projects:
            project = registry.ensure_project_shape(project_payload)
            registry.save_project(target, project, enriched=False, preserve_timestamps=True)
        copy_table_rows(source, target, "sync_state")
        migrate_investigation_rows(source, target)
        copy_table_rows(source, target, "collector_runs")
        target.commit()
        report = validate_database(target, expected_projects=len(rows))
        validate_migrated_projects(source_projects, target)
        report["canonical_snapshot_verified"] = True
    except Exception as exc:
        target.close()
        source.close()
        raise RuntimeError(
            f"migration failed; source unchanged; temporary database retained at {temp_path}"
        ) from exc
    target.close()
    source.close()

    if dry_run:
        temp_path.unlink(missing_ok=True)
        return {"status": "dry_run", "path": str(path), "backup_path": "", **report}

    backup = backup_path(path)
    try:
        os.chmod(temp_path, source_mode)
        os.replace(path, backup)
        os.replace(temp_path, path)
    except Exception:
        if not path.exists() and backup.exists():
            os.replace(backup, path)
        raise
    return {"status": "migrated", "path": str(path), "backup_path": str(backup), **report}
