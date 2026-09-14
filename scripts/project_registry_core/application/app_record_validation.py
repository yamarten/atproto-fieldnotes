from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .. import database, identifiers, project_record


REPORT_SCHEMA_VERSION = 1
VALIDATION_CONTRACT_VERSION = "community-app-entry-readiness-v1"
EXPORT_SCHEMA_VERSION = 1
EXPORT_CONTRACT_VERSION = "community-app-entry-export-v1"
TARGET_NSID = "community.lexicon.app.entry"
TARGET_SCHEMA_REPOSITORY = "https://tangled.org/lexicon.community/lexicons"
TARGET_SCHEMA_REVISION = "c8552ebbf7f2d1cc13e14870f6908cdc29796008"

LINK_ROLE_MAP = {
    "website": "community.lexicon.app.defs#linkRoleWebsite",
    "webManifest": "community.lexicon.app.defs#linkRoleWebManifest",
    "privacyPolicy": "community.lexicon.app.defs#linkRolePrivacyPolicy",
    "termsOfService": "community.lexicon.app.defs#linkRoleTermsOfService",
    "support": "community.lexicon.app.defs#linkRoleSupport",
    "repository": "community.lexicon.app.defs#linkRoleSourceCode",
    "docs": "community.lexicon.app.defs#linkRoleDocs",
    "changelog": "community.lexicon.app.defs#linkRoleChangelog",
    "status": "community.lexicon.app.defs#linkRoleStatus",
    "appStore": "community.lexicon.app.defs#linkRoleAppStore",
    "playStore": "community.lexicon.app.defs#linkRolePlayStore",
    "fdroid": "community.lexicon.app.defs#linkRoleFDroid",
}

PLATFORM_MAP = {
    "web": "community.lexicon.app.defs#platformWeb",
    "ios": "community.lexicon.app.defs#platformIOS",
    "android": "community.lexicon.app.defs#platformAndroid",
    "macos": "community.lexicon.app.defs#platformMacOS",
    "windows": "community.lexicon.app.defs#platformWindows",
    "linux": "community.lexicon.app.defs#platformLinux",
    "cli": "community.lexicon.app.defs#platformCLI",
}

REQUIRED_FIELDS = ("name", "links", "createdAt")
OPTIONAL_MAPPED_FIELDS = ("description", "tags", "platforms")
SPECIFICATION_UNSETTLED_FIELDS = (
    "images",
    "status",
    "langs",
    "lexicons",
    "accountIndicators",
)

_UTC_DATETIME_RE = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?Z$"
)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _content_hash(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "content_hash"}
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def open_registry_readonly(path: Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"registry database does not exist: {resolved}")
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    database.configure_connection(conn)
    version = database.database_schema_version(conn)
    if version != database.SCHEMA_VERSION:
        conn.close()
        raise database.SchemaMismatchError(
            f"expected schema version {database.SCHEMA_VERSION}, got {version}"
        )
    return conn


def _text_status(value: str, *, max_bytes: int, max_graphemes: int) -> tuple[str, str]:
    if len(value.encode("utf-8")) > max_bytes:
        return "unconvertible", f"utf8_length_exceeds_{max_bytes}"
    if len(value) <= max_graphemes:
        # Unicode code points are an upper bound on grapheme clusters. Passing
        # this check therefore proves the grapheme limit without a dependency.
        return "supported", ""
    if value.isascii():
        return "unconvertible", f"grapheme_length_exceeds_{max_graphemes}"
    return "specification_uncertain", "exact_grapheme_count_not_available"


def _field(
    name: str,
    status: str,
    *,
    value: Any = None,
    evidence: Iterable[dict[str, Any]] = (),
    reason: str = "",
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "field": name,
        "status": status,
        "evidence": list(evidence),
    }
    if value is not None:
        output["candidate_value"] = value
    if reason:
        output["reason"] = reason
    return output


def _identifier_rows(conn: sqlite3.Connection, project_key: str) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": int(row["ordinal"]),
            "kind": str(row["kind"]),
            "value": str(row["value"]),
            "role": str(row["role"]),
            "classification": str(row["classification"]),
            "is_primary": bool(row["is_primary"]),
            "provider": str(row["provider"]),
            "observed_at": str(row["observed_at"]),
        }
        for row in conn.execute(
            "SELECT ordinal, kind, value, role, classification, is_primary, provider, observed_at "
            "FROM project_identifiers WHERE project_key=? ORDER BY ordinal",
            (project_key,),
        )
    ]


def _confirmed_relation_rows(conn: sqlite3.Connection, project_key: str) -> list[dict[str, str]]:
    return [
        {
            "subject_type": str(row["subject_type"]),
            "subject_value": str(row["subject_value"]),
            "predicate": str(row["predicate"]),
            "object_type": str(row["object_type"]),
            "object_value": str(row["object_value"]),
            "source": str(row["source"]),
            "locator": str(row["locator"]),
        }
        for row in conn.execute(
            "SELECT subject_type, subject_value, predicate, object_type, object_value, source, locator "
            "FROM project_relations WHERE project_key=? AND classification='confirmed' ORDER BY ordinal",
            (project_key,),
        )
    ]


def _link_evidence(
    link: dict[str, Any],
    project: dict[str, Any],
    confirmed_identifiers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    uri = identifiers.normalize_url(str(link.get("uri") or ""))
    role = str(link.get("role") or "")
    if role == "website" and uri == identifiers.normalize_url(str(project.get("website_url") or "")):
        return [{"source": "projects.website_url", "kind": "canonical_scalar", "value": uri}]
    kind_role = {
        "repository": ("repo_url", "project_repository"),
        "webApp": ("service_url", "service"),
    }.get(role)
    if kind_role:
        kind, identifier_role = kind_role
        matches = [
            row for row in confirmed_identifiers
            if row["kind"] == kind
            and row["role"] == identifier_role
            and identifiers.normalize_url(row["value"]) == uri
        ]
        if matches:
            return [
                {
                    "source": "project_identifiers",
                    "kind": row["kind"],
                    "role": row["role"],
                    "classification": "confirmed",
                    "value": row["value"],
                    "ordinal": row["ordinal"],
                }
                for row in matches
            ]
    resource_matches = [
        record for record in project_record.normalize_resource_url_records(
            project.get("resource_url_records", [])
        )
        if record.get("confidence") == "confirmed"
        and identifiers.normalize_url(str(record.get("url") or "")) == uri
        and str(record.get("role") or "") == role
    ]
    return [
        {
            "source": "state_json.evidence.resource_url_records",
            "kind": "confirmed_resource",
            "role": str(record.get("role") or ""),
            "value": str(record.get("url") or ""),
            "observation_source": str(record.get("source") or ""),
            "observed_at": str(record.get("observed_at") or ""),
        }
        for record in resource_matches
    ]


def _links_field(
    project: dict[str, Any], confirmed_identifiers: list[dict[str, Any]]
) -> dict[str, Any]:
    source_links = project_record.build_project_app_links(project)
    if not source_links:
        return _field("links", "missing_required", reason="no_publishable_confirmed_link")
    output: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    losses: list[dict[str, str]] = []
    for source_link in source_links:
        uri = identifiers.normalize_url(str(source_link.get("uri") or ""))
        if not uri:
            return _field(
                "links", "unconvertible", reason="derived_link_has_invalid_uri"
            )
        item: dict[str, Any] = {"uri": uri}
        source_role = str(source_link.get("role") or "")
        target_role = LINK_ROLE_MAP.get(source_role)
        if target_role:
            item["role"] = target_role
        else:
            losses.append({
                "uri": uri,
                "source_role": source_role,
                "reason": "no_approved_target_role_mapping",
            })
        label = str(source_link.get("label") or "").strip()
        if label:
            label_status, reason = _text_status(label, max_bytes=100, max_graphemes=50)
            if label_status != "supported":
                return _field("links", label_status, reason=f"link_label:{reason}")
            item["label"] = label
        output.append(item)
        evidence.extend(_link_evidence(source_link, project, confirmed_identifiers))
    if len(output) > 12:
        return _field(
            "links",
            "unconvertible",
            value=output,
            evidence=evidence,
            reason="link_count_exceeds_12",
        )
    field = _field("links", "supported", value=output, evidence=evidence)
    if losses:
        field["losses"] = losses
    return field


def _candidate_identifier_exclusions(identifier_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source": "project_identifiers",
            "kind": row["kind"],
            "value": row["value"],
            "role": row["role"],
            "classification": row["classification"],
            "reason": "classification_is_not_confirmed",
        }
        for row in identifier_rows
        if row["classification"] != "confirmed"
    ]


def _nonconfirmed_resource_exclusions(project: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source": "state_json.evidence.resource_url_records",
            "value": str(record.get("url") or ""),
            "role": str(record.get("role") or ""),
            "classification": str(record.get("confidence") or ""),
            "reason": "confidence_is_not_confirmed",
        }
        for record in project_record.normalize_resource_url_records(
            project.get("resource_url_records", [])
        )
        if record.get("confidence") != "confirmed"
    ]


def validate_project(conn: sqlite3.Connection, project: dict[str, Any]) -> dict[str, Any]:
    project_key = str(project.get("project_key") or "")
    identifier_rows = _identifier_rows(conn, project_key)
    confirmed_identifiers = [row for row in identifier_rows if row["classification"] == "confirmed"]
    confirmed_relations = [
        row for row in _confirmed_relation_rows(conn, project_key)
        if row["subject_type"] == "nsid" or row["object_type"] == "nsid"
    ]
    fields: list[dict[str, Any]] = []

    name = str(project.get("name") or "").strip()
    if not name:
        fields.append(_field("name", "missing_required", reason="canonical_name_is_empty"))
    else:
        status, reason = _text_status(name, max_bytes=200, max_graphemes=100)
        fields.append(_field(
            "name",
            status,
            value=name,
            evidence=[{"source": "projects.name", "kind": "canonical_scalar", "value": name}],
            reason=reason,
        ))

    description = str(project.get("summary") or "").strip()
    if not description:
        fields.append(_field("description", "missing_optional", reason="profile_core_summary_is_empty"))
    else:
        status, reason = _text_status(description, max_bytes=3000, max_graphemes=300)
        fields.append(_field(
            "description",
            status,
            value=description,
            evidence=[{
                "source": "profile_json.core.summary",
                "kind": "introduction_profile",
                "value": description,
            }],
            reason=reason,
        ))

    fields.append(_links_field(project, confirmed_identifiers))

    tags = [str(item).strip() for item in project.get("tags", []) if str(item).strip()]
    if not tags:
        fields.append(_field("tags", "missing_optional", reason="profile_app_tags_are_empty"))
    elif len(tags) > 10:
        fields.append(_field("tags", "unconvertible", value=tags, reason="tag_count_exceeds_10"))
    else:
        tag_checks = [_text_status(item, max_bytes=64, max_graphemes=32) for item in tags]
        tag_status = next((status for status, _ in tag_checks if status != "supported"), "supported")
        tag_reason = next((reason for status, reason in tag_checks if status != "supported"), "")
        fields.append(_field(
            "tags",
            tag_status,
            value=tags,
            evidence=[{"source": "profile_json.app.tags", "kind": "introduction_profile", "value": tags}],
            reason=tag_reason,
        ))

    platforms = [str(item).strip() for item in project.get("platforms", []) if str(item).strip()]
    if not platforms:
        fields.append(_field("platforms", "missing_optional", reason="profile_app_platforms_are_empty"))
    elif len(platforms) > 12:
        fields.append(_field(
            "platforms",
            "unconvertible",
            value=platforms,
            reason="platform_count_exceeds_12",
        ))
    else:
        mapped: list[str] = []
        unknown: list[str] = []
        for item in platforms:
            normalized = item.lower().replace(" ", "").replace("-", "")
            token = PLATFORM_MAP.get(normalized)
            if token:
                mapped.append(token)
            else:
                unknown.append(item)
        fields.append(_field(
            "platforms",
            "specification_uncertain" if unknown else "supported",
            value=mapped,
            evidence=[{
                "source": "profile_json.app.platforms",
                "kind": "introduction_profile",
                "value": platforms,
            }],
            reason=("unmapped_platform_values:" + ",".join(unknown)) if unknown else "",
        ))

    fields.append(_field(
        "createdAt",
        "publication_context_required",
        reason="entry_creation_time_must_be_supplied_by_export",
    ))
    fields.append(_field(
        "updatedAt",
        "missing_optional",
        reason="omit_until_a_published_entry_is_updated",
    ))
    for field_name in SPECIFICATION_UNSETTLED_FIELDS:
        field = _field(
            field_name,
            "specification_uncertain",
            reason="source_and_conversion_contract_not_approved",
        )
        if field_name in {"lexicons", "accountIndicators"}:
            field["evidence_ref"] = "diagnostic_evidence.interop"
        fields.append(field)

    excluded = _candidate_identifier_exclusions(identifier_rows)
    excluded_resources = _nonconfirmed_resource_exclusions(project)
    excluded_link_sources = [
        item
        for item in excluded
        if (item["kind"], item["role"])
        in {("repo_url", "project_repository"), ("service_url", "service")}
    ] + excluded_resources
    category = str(project.get("category") or "").strip()
    tracking_state = str(project.get("tracking_state") or "").strip()
    if category == "reject" or tracking_state == "ignore":
        publication_scope = {
            "status": "excluded",
            "reason": "category_reject" if category == "reject" else "tracking_state_ignore",
            "evidence": [
                {"source": "state_json.ops.category", "value": category},
                {"source": "projects.tracking_state", "value": tracking_state},
            ],
        }
    elif category in {"", "name_only"} or tracking_state == "out_of_scope":
        reasons = []
        if category in {"", "name_only"}:
            reasons.append("category_does_not_establish_publishable_project_kind")
        if tracking_state == "out_of_scope":
            reasons.append("tracking_state_out_of_scope_needs_publication_decision")
        publication_scope = {
            "status": "uncertain",
            "reason": ";".join(reasons),
            "evidence": [
                {"source": "state_json.ops.category", "value": category},
                {"source": "projects.tracking_state", "value": tracking_state},
            ],
        }
    else:
        publication_scope = {
            "status": "eligible",
            "reason": "current_category_and_tracking_state_are_in_scope",
            "evidence": [
                {"source": "state_json.ops.category", "value": category},
                {"source": "projects.tracking_state", "value": tracking_state},
            ],
        }
    missing_required = [field["field"] for field in fields if field["status"] == "missing_required"]
    missing_optional = [field["field"] for field in fields if field["status"] == "missing_optional"]
    insufficient_evidence = [
        {
            "field": "links",
            "reason": "non_confirmed_link_source_was_excluded",
            "evidence_ref": "excluded_inputs.non_confirmed_link_sources",
        }
    ] if excluded_link_sources and "links" in missing_required else []
    unconvertible = [field["field"] for field in fields if field["status"] == "unconvertible"]
    specification_uncertain = [
        field["field"] for field in fields if field["status"] == "specification_uncertain"
    ]
    supported_required = all(
        next(field for field in fields if field["field"] == name)["status"] == "supported"
        for name in ("name", "links")
    )
    if publication_scope["status"] == "excluded":
        readiness = "not_applicable"
    elif publication_scope["status"] == "uncertain":
        readiness = "scope_uncertain"
    elif unconvertible:
        readiness = "unconvertible"
    elif missing_required and insufficient_evidence and set(missing_required) <= {
        issue["field"] for issue in insufficient_evidence
    }:
        readiness = "insufficient_evidence"
    elif missing_required:
        readiness = "missing_required"
    elif not supported_required:
        readiness = "specification_uncertain"
    else:
        readiness = "needs_publication_context"

    candidate_record = {
        field["field"]: field["candidate_value"]
        for field in fields
        if field["status"] == "supported" and "candidate_value" in field
    }
    return {
        "project_key": project_key,
        "tracking_state": tracking_state,
        "category": category,
        "publication_scope": publication_scope,
        "readiness": readiness,
        "record_ready": readiness == "ready",
        "content_ready": readiness == "needs_publication_context",
        "candidate_record": candidate_record,
        "diagnostic_evidence": {
            "interop": [
                {
                    "source": "project_identifiers",
                    "kind": row["kind"],
                    "role": row["role"],
                    "classification": "confirmed",
                    "value": row["value"],
                }
                for row in confirmed_identifiers
                if row["kind"] == "nsid"
            ] + [{"source": "project_relations", **row} for row in confirmed_relations],
        },
        "fields": fields,
        "issues": {
            "missing_required": missing_required,
            "missing_optional": missing_optional,
            "insufficient_evidence": insufficient_evidence,
            "unconvertible": unconvertible,
            "specification_uncertain": specification_uncertain,
            "publication_context_required": ["createdAt"],
            "publication_scope": (
                [] if publication_scope["status"] == "eligible" else [publication_scope["reason"]]
            ),
        },
        "excluded_inputs": {
            "non_confirmed_current_identifiers": excluded,
            "non_confirmed_resources": excluded_resources,
            "non_confirmed_link_sources": excluded_link_sources,
            "noncurrent_identifiers": int(conn.execute(
                "SELECT COUNT(*) FROM project_noncurrent_identifiers WHERE project_key=?",
                (project_key,),
            ).fetchone()[0]),
        },
    }


def _input_fingerprint(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    queries = (
        "SELECT project_key, name, website_url, tracking_state, profile_json, state_json, first_seen_at, last_seen_at FROM projects ORDER BY project_key",
        "SELECT project_key, ordinal, kind, value, role, classification, is_primary, provider, observed_at FROM project_identifiers ORDER BY project_key, ordinal",
        "SELECT project_key, ordinal, subject_type, subject_value, predicate, object_type, object_value, classification, source, locator FROM project_relations ORDER BY project_key, ordinal",
        "SELECT project_key, kind, value, role, standing, reason, recorded_at, origin_project_key FROM project_noncurrent_identifiers ORDER BY project_key, kind, value, role",
    )
    for query in queries:
        for row in conn.execute(query):
            digest.update(_json_bytes(list(row)))
            digest.update(b"\n")
    return digest.hexdigest()


def build_report(conn: sqlite3.Connection) -> dict[str, Any]:
    if database.database_schema_version(conn) != database.SCHEMA_VERSION:
        raise database.SchemaMismatchError("app record validation requires schema version 4")
    fingerprint = _input_fingerprint(conn)
    projects = [validate_project(conn, project) for project in project_record.list_projects(conn)]
    projects.sort(key=lambda item: item["project_key"])
    readiness_counts = collections.Counter(str(item["readiness"]) for item in projects)
    issue_counts: collections.Counter[str] = collections.Counter()
    field_status_counts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for item in projects:
        for key, values in item["issues"].items():
            if values:
                issue_counts[key] += 1
        for field in item["fields"]:
            field_status_counts[str(field["field"])][str(field["status"])] += 1
    report: dict[str, Any] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "validation_contract_version": VALIDATION_CONTRACT_VERSION,
        "target": {
            "nsid": TARGET_NSID,
            "schema_repository": TARGET_SCHEMA_REPOSITORY,
            "schema_revision": TARGET_SCHEMA_REVISION,
            "required_fields": list(REQUIRED_FIELDS),
            "optional_mapped_fields": list(OPTIONAL_MAPPED_FIELDS),
            "specification_unsettled_fields": list(SPECIFICATION_UNSETTLED_FIELDS),
            "excluded_by_policy": ["profileDid"],
        },
        "input_policy": {
            "included": [
                "introduction_profile",
                "canonical_scalars",
                "confirmed_current_identifiers",
                "confirmed_resources",
                "confirmed_relations_for_unsettled_field_diagnostics",
            ],
            "excluded": [
                "candidate_identifiers",
                "derived_identifiers",
                "heuristic_identifiers",
                "noncurrent_identifiers",
                "noise",
                "unpromoted_llm_output",
            ],
            "writes_database": False,
            "network_used": False,
        },
        "snapshot": {
            "schema_version": database.database_schema_version(conn),
            "input_fingerprint": fingerprint,
            "project_count": len(projects),
            "identifier_count": int(conn.execute("SELECT COUNT(*) FROM project_identifiers").fetchone()[0]),
            "relation_count": int(conn.execute("SELECT COUNT(*) FROM project_relations").fetchone()[0]),
            "last_seen_at_max": str(conn.execute("SELECT COALESCE(MAX(last_seen_at), '') FROM projects").fetchone()[0]),
        },
        "summary": {
            "readiness_counts": {key: int(readiness_counts[key]) for key in sorted(readiness_counts)},
            "project_issue_counts": {key: int(issue_counts[key]) for key in sorted(issue_counts)},
            "field_status_counts": {
                field_name: {
                    status: int(counts[status]) for status in sorted(counts)
                }
                for field_name, counts in sorted(field_status_counts.items())
            },
        },
        "projects": projects,
    }
    report["content_hash"] = _content_hash(report)
    return report


def build_report_from_path(path: Path) -> dict[str, Any]:
    conn = open_registry_readonly(path)
    try:
        return build_report(conn)
    finally:
        conn.close()


def normalize_publication_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("publication manifest must be a JSON object")
    allowed_keys = {
        "manifest_schema_version",
        "project_keys",
        "allow_scope_uncertain_project_keys",
        "publication_context",
    }
    unknown_keys = sorted(set(value) - allowed_keys)
    if unknown_keys:
        raise ValueError(
            "publication manifest has unknown fields: " + ", ".join(unknown_keys)
        )
    if value.get("manifest_schema_version") != 1:
        raise ValueError("publication manifest requires manifest_schema_version=1")

    project_keys = value.get("project_keys")
    if not isinstance(project_keys, list) or not project_keys:
        raise ValueError("publication manifest project_keys must be a non-empty array")
    if not all(isinstance(item, str) and item for item in project_keys):
        raise ValueError("publication manifest project_keys must contain non-empty strings")
    if len(project_keys) != len(set(project_keys)):
        raise ValueError("publication manifest project_keys must not contain duplicates")

    uncertain_keys = value.get("allow_scope_uncertain_project_keys", [])
    if not isinstance(uncertain_keys, list) or not all(
        isinstance(item, str) and item for item in uncertain_keys
    ):
        raise ValueError(
            "publication manifest allow_scope_uncertain_project_keys must be an array of non-empty strings"
        )
    if len(uncertain_keys) != len(set(uncertain_keys)):
        raise ValueError(
            "publication manifest allow_scope_uncertain_project_keys must not contain duplicates"
        )
    unselected_uncertain = sorted(set(uncertain_keys) - set(project_keys))
    if unselected_uncertain:
        raise ValueError(
            "scope-uncertain allowlist contains unselected project keys: "
            + ", ".join(unselected_uncertain)
        )

    publication_context = value.get("publication_context")
    if not isinstance(publication_context, dict):
        raise ValueError("publication manifest publication_context must be an object")
    unknown_context = sorted(set(publication_context) - {"createdAt"})
    if unknown_context:
        raise ValueError(
            "publication_context has unsupported fields: " + ", ".join(unknown_context)
        )
    created_at = publication_context.get("createdAt")
    if not isinstance(created_at, str) or not _UTC_DATETIME_RE.fullmatch(created_at):
        raise ValueError(
            "publication_context.createdAt must be an RFC 3339 UTC datetime ending in Z"
        )
    if len(created_at) > 64:
        raise ValueError("publication_context.createdAt must not exceed 64 characters")
    try:
        parsed = dt.datetime.fromisoformat(created_at[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(
            "publication_context.createdAt must be a valid calendar datetime"
        ) from error
    if parsed.year > 9999:
        raise ValueError("publication_context.createdAt year must not exceed 9999")

    return {
        "manifest_schema_version": 1,
        "project_keys": sorted(project_keys),
        "allow_scope_uncertain_project_keys": sorted(uncertain_keys),
        "publication_context": {"createdAt": created_at},
    }


def build_export(
    conn: sqlite3.Connection, publication_manifest: dict[str, Any]
) -> dict[str, Any]:
    manifest = normalize_publication_manifest(publication_manifest)
    report = build_report(conn)
    projects_by_key = {item["project_key"]: item for item in report["projects"]}
    missing_keys = [
        key for key in manifest["project_keys"] if key not in projects_by_key
    ]
    if missing_keys:
        raise ValueError("publication manifest has unknown project keys: " + ", ".join(missing_keys))

    allow_uncertain = set(manifest["allow_scope_uncertain_project_keys"])
    rejected: list[str] = []
    records: list[dict[str, Any]] = []
    for project_key in manifest["project_keys"]:
        item = projects_by_key[project_key]
        readiness = str(item["readiness"])
        required_statuses = {
            field["field"]: field["status"]
            for field in item["fields"]
            if field["field"] in {"name", "links"}
        }
        required_content_ready = all(
            required_statuses.get(field_name) == "supported"
            for field_name in ("name", "links")
        )
        if readiness == "needs_publication_context":
            pass
        elif (
            readiness == "scope_uncertain"
            and project_key in allow_uncertain
            and required_content_ready
            and not item["issues"]["unconvertible"]
        ):
            pass
        else:
            rejected.append(f"{project_key} ({readiness})")
            continue

        record = {
            "$type": TARGET_NSID,
            **item["candidate_record"],
            "createdAt": manifest["publication_context"]["createdAt"],
        }
        records.append(record)
    if rejected:
        raise ValueError(
            "selected projects are not exportable under this manifest: " + ", ".join(rejected)
        )

    export: dict[str, Any] = {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "export_contract_version": EXPORT_CONTRACT_VERSION,
        "source_snapshot": {
            "schema_version": report["snapshot"]["schema_version"],
            "input_fingerprint": report["snapshot"]["input_fingerprint"],
        },
        "publication_manifest": manifest,
        "records": records,
    }
    export["content_hash"] = _content_hash(export)
    return export


def build_export_from_path(
    path: Path, publication_manifest: dict[str, Any]
) -> dict[str, Any]:
    conn = open_registry_readonly(path)
    try:
        return build_export(conn, publication_manifest)
    finally:
        conn.close()
