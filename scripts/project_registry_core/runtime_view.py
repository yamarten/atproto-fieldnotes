from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


RUNTIME_VIEW_MARKER = "_registry_runtime_view_v2"
CLASSIFICATIONS = {"confirmed", "candidate", "derived", "heuristic"}

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

REMOVED_COMPATIBILITY_FIELDS = {
    "website_url_candidates",
    "github_repo_urls",
    "tangled_repo_urls",
    "resource_urls",
    "introduced",
}

NORMALIZED_FIELDS = IDENTIFIER_FIELDS | DECISION_FIELDS | {"relation_evidence"} | REMOVED_COMPATIBILITY_FIELDS


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


def _provider(value: str) -> str:
    lowered = value.lower()
    if "github.com/" in lowered:
        return "github"
    if "tangled.org/" in lowered or "tangled.sh/" in lowered:
        return "tangled"
    if lowered.startswith("did:"):
        return "atproto"
    return ""


def mark_complete(project: dict[str, Any]) -> dict[str, Any]:
    project[RUNTIME_VIEW_MARKER] = True
    return project


def is_complete(project: Mapping[str, Any]) -> bool:
    return project.get(RUNTIME_VIEW_MARKER) is True


def require_complete_for_update(project: Mapping[str, Any], *, exists: bool) -> None:
    if exists and not is_complete(project):
        raise ValueError(
            "existing project updates require a complete runtime view loaded from the registry"
        )


def sanitize_runtime_snapshot(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: sanitize_runtime_snapshot(item)
            for key, item in value.items()
            if key not in REMOVED_COMPATIBILITY_FIELDS and key != RUNTIME_VIEW_MARKER
        }
    if isinstance(value, list):
        return [sanitize_runtime_snapshot(item) for item in value]
    return value


def strip_runtime_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    def strip_nested(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: strip_nested(item)
                for key, item in value.items()
                if key not in REMOVED_COMPATIBILITY_FIELDS and key != RUNTIME_VIEW_MARKER
            }
        if isinstance(value, list):
            return [strip_nested(item) for item in value]
        return value

    return {
        key: strip_nested(value)
        for key, value in payload.items()
        if key not in NORMALIZED_FIELDS and key != RUNTIME_VIEW_MARKER
    }


def project_identifiers(project: Mapping[str, Any]) -> list[ProjectIdentifier]:
    output: list[ProjectIdentifier] = []
    seen: set[tuple[str, str, str]] = set()

    def add(
        kind: str,
        value: Any,
        role: str,
        classification: str,
        *,
        primary: bool = False,
        provider: str = "",
        metadata: dict[str, Any] | None = None,
        observed_at: str = "",
    ) -> None:
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

    add("nsid", project.get("primary_nsid"), "primary_nsid", "confirmed", primary=True)
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

    add("service_url", project.get("website_url"), "website", "confirmed", primary=True)
    for value in project.get("service_urls", []):
        add("service_url", value, "service", "confirmed")
    for value in project.get("service_url_candidates", []):
        add("service_url", value, "service", "candidate")

    add("repo_url", project.get("source_code_url"), "source_repository", "confirmed", primary=True)
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

    for field, classification in (
        ("developer_accounts", "confirmed"),
        ("candidate_developer_accounts", "candidate"),
    ):
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
            add(
                kind,
                item.get("value"),
                "manual_seed",
                "confirmed",
                metadata=dict(item),
                observed_at=str(item.get("added_at") or ""),
            )
    return output


def project_decisions(project: Mapping[str, Any]) -> list[ProjectDecision]:
    output: list[ProjectDecision] = []
    for item in project.get("blocked_identifiers", []):
        if isinstance(item, dict):
            output.append(ProjectDecision(
                "block_identifier",
                str(item.get("type") or ""),
                str(item.get("value") or ""),
                str(item.get("note") or ""),
                str(item.get("added_at") or ""),
                metadata=dict(item),
            ))
    for item in project.get("deferred_identifiers", []):
        if isinstance(item, dict):
            output.append(ProjectDecision(
                "defer_identifier",
                str(item.get("type") or ""),
                str(item.get("value") or ""),
                str(item.get("note") or ""),
                str(item.get("added_at") or ""),
                str(item.get("expires_at") or ""),
                metadata=dict(item),
            ))
    for item in project.get("promotion_deferrals", []):
        if isinstance(item, dict):
            output.append(ProjectDecision(
                "defer_promotion",
                note=str(item.get("reason") or ""),
                created_at=str(item.get("added_at") or ""),
                expires_at=str(item.get("expires_at") or ""),
                cleared_at=str(item.get("cleared_at") or ""),
                metadata=dict(item),
            ))
    for value in project.get("dismissed_review_kinds", []):
        output.append(ProjectDecision("dismiss_review", "review_kind", str(value)))
    for value in project.get("separate_project_keys", []):
        output.append(ProjectDecision("separate_project", "project_key", str(value)))
    return output


def relation_rows(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .relations import normalize_relation_records

    rows: list[dict[str, Any]] = []
    for item in normalize_relation_records(project.get("relation_evidence", [])):
        relation = item["relation"]
        rows.append({
            "subject_type": relation["subject"]["type"],
            "subject_value": relation["subject"]["value"],
            "predicate": relation["predicate"],
            "object_type": relation["object"]["type"],
            "object_value": relation["object"]["value"],
            "classification": item["classification"],
            "source": item["source"],
            "locator": relation["locator"],
            "method": relation["method"],
            "context": item["context"],
            "observed_at": item["observed_at"],
        })
    return rows


def hydrate(
    project: dict[str, Any],
    *,
    identifier_rows: Iterable[Mapping[str, Any]],
    relation_rows: Iterable[Mapping[str, Any]],
    decision_rows: Iterable[Mapping[str, Any]],
    load_json: Any,
) -> dict[str, Any]:
    project.update({
        "input_nsids": [], "resolved_nsids": [], "lexicons": [], "external_lexicons": [],
        "recorded_lexicon_namespaces": [], "authority_domains": [], "service_urls": [],
        "service_url_candidates": [], "source_code_urls": [], "candidate_source_code_urls": [],
        "source_repo_records": [], "developer_accounts": [], "candidate_developer_accounts": [],
        "bluesky_handles": [], "tangled_handles": [], "manual_seeds": [],
    })
    for row in identifier_rows:
        kind, value, role = str(row["kind"]), str(row["value"]), str(row["role"])
        classification = str(row["classification"])
        metadata = load_json(str(row["metadata_json"]), {})
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
            target = "service_urls" if classification == "confirmed" else "service_url_candidates"
            project[target].append(value)
        elif kind == "repo_url":
            if bool(row["is_primary"]): project["source_code_url"] = value
            target = "source_code_urls" if classification == "confirmed" else "candidate_source_code_urls"
            project[target].append(value)
            if metadata and role == "source_repository_record": project["source_repo_records"].append(metadata)
        elif kind == "account":
            if role == "developer_account":
                target = "developer_accounts" if classification == "confirmed" else "candidate_developer_accounts"
                project[target].append(metadata or {"handle": value})
            elif str(row["provider"]) == "bluesky": project["bluesky_handles"].append(value)
            elif str(row["provider"]) == "tangled": project["tangled_handles"].append(value)
        elif kind == "did" and role == "brand_did": project["brand_did"] = value

    project["relation_evidence"] = [{
        "relation": {
            "subject": {"type": str(row["subject_type"]), "value": str(row["subject_value"])},
            "predicate": str(row["predicate"]),
            "object": {"type": str(row["object_type"]), "value": str(row["object_value"])},
            "locator": str(row["locator"]),
            "method": str(row["method"]),
        },
        "source": str(row["source"]),
        "classification": str(row["classification"]),
        "observed_at": str(row["observed_at"]),
        "context": load_json(str(row["context_json"]), {}),
    } for row in relation_rows]

    project.update({
        "blocked_identifiers": [], "deferred_identifiers": [], "promotion_deferrals": [],
        "dismissed_review_kinds": [], "separate_project_keys": [],
    })
    for row in decision_rows:
        kind = str(row["kind"])
        metadata = load_json(str(row["metadata_json"]), {})
        if kind == "block_identifier":
            project["blocked_identifiers"].append(metadata or {
                "type": str(row["target_type"]), "value": str(row["target_value"]),
                "note": str(row["note"]), "added_at": str(row["created_at"]),
            })
        elif kind == "defer_identifier":
            project["deferred_identifiers"].append(metadata or {
                "type": str(row["target_type"]), "value": str(row["target_value"]),
                "note": str(row["note"]), "added_at": str(row["created_at"]),
                "expires_at": str(row["expires_at"]),
            })
        elif kind == "defer_promotion":
            project["promotion_deferrals"].append(metadata or {
                "reason": str(row["note"]), "added_at": str(row["created_at"]),
                "expires_at": str(row["expires_at"]), "cleared_at": str(row["cleared_at"]),
            })
        elif kind == "dismiss_review":
            project["dismissed_review_kinds"].append(str(row["target_value"]))
        elif kind == "separate_project":
            project["separate_project_keys"].append(str(row["target_value"]))
    return mark_complete(project)
