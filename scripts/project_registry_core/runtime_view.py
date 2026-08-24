from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import re
from typing import Any, Iterable, Mapping

from . import identifiers
from . import repo_urls


RUNTIME_VIEW_MARKER = "_registry_runtime_view_v2"
RUNTIME_IDENTIFIER_ROWS = "_registry_identifier_rows_v2"
RUNTIME_IDENTIFIER_BASELINE = "_registry_identifier_baseline_v2"
RUNTIME_RELATION_ROWS = "_registry_relation_rows_v2"
RUNTIME_RELATION_BASELINE = "_registry_relation_baseline_v2"
RUNTIME_DECISION_ROWS = "_registry_decision_rows_v2"
RUNTIME_DECISION_BASELINE = "_registry_decision_baseline_v2"
RUNTIME_ADOPTION_ROWS = "_registry_identifier_adoption_rows_v3"
RUNTIME_INVESTIGATION_RUN_ID = "_registry_investigation_run_id_v2"
RUNTIME_INTERNAL_FIELDS = {
    RUNTIME_VIEW_MARKER,
    RUNTIME_IDENTIFIER_ROWS,
    RUNTIME_IDENTIFIER_BASELINE,
    RUNTIME_RELATION_ROWS,
    RUNTIME_RELATION_BASELINE,
    RUNTIME_DECISION_ROWS,
    RUNTIME_DECISION_BASELINE,
    RUNTIME_ADOPTION_ROWS,
    RUNTIME_INVESTIGATION_RUN_ID,
}
ATPROTO_HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", re.IGNORECASE)
DID_RE = re.compile(r"^did:[a-z0-9]+:[A-Za-z0-9._:%-]+$", re.IGNORECASE)
CLASSIFICATIONS = {"confirmed", "candidate", "derived", "heuristic"}


def _unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _normalized_developer_account(item: Mapping[str, Any]) -> dict[str, str]:
    handle = str(item.get("handle") or "").strip()
    did = str(item.get("did") or "").strip()
    source = str(item.get("source") or "").strip()
    if DID_RE.fullmatch(handle):
        if not did or did.lower() == handle.lower():
            did = handle
        handle = ""
    if handle:
        handle = handle.rstrip(".")
    if handle and not ATPROTO_HANDLE_RE.fullmatch(handle):
        handle = ""
    if did and not DID_RE.fullmatch(did):
        did = ""
    if not handle and not did:
        return {}
    output: dict[str, str] = {}
    if handle:
        output["handle"] = handle.lower()
    if did:
        output["did"] = did
    if source:
        output["source"] = source
    return output


IDENTIFIER_FIELDS = {
    "input_nsids",
    "resolved_nsids",
    "lexicons",
    "external_lexicons",
    "recorded_lexicon_namespaces",
    "authority_domains",
    "service_urls",
    "service_url_candidates",
    "project_site_urls",
    "project_site_url_candidates",
    "source_code_url",
    "source_code_urls",
    "candidate_source_code_urls",
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
    "promotion_priority_adjustments",
    "dismissed_review_kinds",
    "separate_project_keys",
    "confirmed_identifiers",
    "review_target_decisions",
}

NORMALIZED_FIELDS = IDENTIFIER_FIELDS | DECISION_FIELDS | {"relation_evidence"}


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
    target_role: str = ""
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


def refresh_baselines(project: dict[str, Any]) -> dict[str, Any]:
    mark_complete(project)
    project[RUNTIME_IDENTIFIER_BASELINE] = [asdict(item) for item in _derive_project_identifiers(project)]
    project[RUNTIME_RELATION_BASELINE] = copy.deepcopy(project.get("relation_evidence", []))
    project[RUNTIME_DECISION_BASELINE] = [asdict(item) for item in _derive_project_decisions(project)]
    return project


def normalized_children_unchanged(project: Mapping[str, Any]) -> bool:
    identifier_baseline = project.get(RUNTIME_IDENTIFIER_BASELINE)
    relation_baseline = project.get(RUNTIME_RELATION_BASELINE)
    decision_baseline = project.get(RUNTIME_DECISION_BASELINE)
    if not all(isinstance(value, list) for value in (identifier_baseline, relation_baseline, decision_baseline)):
        return False
    identifiers = [asdict(item) for item in _derive_project_identifiers(project)]
    decisions = [asdict(item) for item in _derive_project_decisions(project)]
    from .relations import normalize_relation_records
    relations = normalize_relation_records(project.get("relation_evidence", []))
    return identifiers == identifier_baseline and relations == relation_baseline and decisions == decision_baseline


def sanitize_runtime_snapshot(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: sanitize_runtime_snapshot(item)
            for key, item in value.items()
            if key not in RUNTIME_INTERNAL_FIELDS
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
                if key not in RUNTIME_INTERNAL_FIELDS
            }
        if isinstance(value, list):
            return [strip_nested(item) for item in value]
        return value

    return {
        key: strip_nested(value)
        for key, value in payload.items()
        if key not in NORMALIZED_FIELDS and key not in RUNTIME_INTERNAL_FIELDS
    }


def _identifier_key(item: ProjectIdentifier) -> tuple[str, str, str]:
    return (item.kind, item.value, item.role)


def _identifier_from_dict(item: Mapping[str, Any]) -> ProjectIdentifier:
    return ProjectIdentifier(
        kind=str(item.get("kind") or ""),
        value=str(item.get("value") or ""),
        role=str(item.get("role") or ""),
        classification=str(item.get("classification") or "candidate"),
        is_primary=bool(item.get("is_primary")),
        provider=str(item.get("provider") or ""),
        metadata=copy.deepcopy(dict(item.get("metadata") or {})),
        observed_at=str(item.get("observed_at") or ""),
    )


def _derive_project_identifiers(project: Mapping[str, Any]) -> list[ProjectIdentifier]:
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
        if kind == "repo_url":
            text = repo_urls.canonical_repo_url(text)
        elif kind in {"service_url", "site_url"}:
            text = identifiers.normalize_url(text)
        elif kind == "nsid":
            text = identifiers.normalize_nsid(text)
        elif kind in {"handle", "domain"}:
            text = text.lower().rstrip(".")
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

    for value in project.get("input_nsids", []):
        add("nsid", value, "owned_nsid", "candidate")
    for value in project.get("resolved_nsids", []):
        add("nsid", value, "owned_nsid", "confirmed")
    for value in project.get("service_urls", []):
        add("service_url", value, "service", "confirmed")
    for value in project.get("service_url_candidates", []):
        add("service_url", value, "service", "candidate")
    for value in project.get("project_site_urls", []):
        add("site_url", value, "project_site", "confirmed")
    for value in project.get("project_site_url_candidates", []):
        add("site_url", value, "project_site", "candidate")

    add("repo_url", project.get("source_code_url"), "source_repository", "confirmed", primary=True)
    for value in project.get("source_code_urls", []):
        add("repo_url", value, "source_repository", "confirmed")
    for value in project.get("candidate_source_code_urls", []):
        add("repo_url", value, "source_repository", "candidate")
    for field, classification in (
        ("developer_accounts", "confirmed"),
        ("candidate_developer_accounts", "candidate"),
    ):
        for item in project.get(field, []):
            if not isinstance(item, dict):
                continue
            account = _normalized_developer_account(item)
            if not account:
                continue
            value = str(account.get("did") or account.get("handle") or "")
            add("account", value, "developer_account", classification, metadata=account)
            add("handle", account.get("handle"), "developer_handle", classification, metadata={"account": value})
            add("did", account.get("did"), "developer_did", classification, metadata={"account": value})
    add("did", project.get("brand_did"), "brand_did", "confirmed", primary=True)
    for field, provider in (("bluesky_handles", "bluesky"), ("tangled_handles", "tangled")):
        for value in project.get(field, []):
            value = str(value or "").strip().rstrip(".").lower()
            if not ATPROTO_HANDLE_RE.fullmatch(value):
                continue
            add("account", value, "project_account", "candidate", provider=provider)
            add("handle", value, "project_handle", "candidate", provider=provider)

    return output


def project_identifiers(project: Mapping[str, Any]) -> list[ProjectIdentifier]:
    current = _derive_project_identifiers(project)
    original_payload = project.get(RUNTIME_IDENTIFIER_ROWS)
    baseline_payload = project.get(RUNTIME_IDENTIFIER_BASELINE)
    if not isinstance(original_payload, list) or not isinstance(baseline_payload, list):
        return current
    original = [_identifier_from_dict(item) for item in original_payload if isinstance(item, Mapping)]
    baseline = [_identifier_from_dict(item) for item in baseline_payload if isinstance(item, Mapping)]
    if current == baseline:
        return original

    baseline_by_key = {_identifier_key(item): item for item in baseline}
    current_by_key = {_identifier_key(item): item for item in current}
    original_keys = {_identifier_key(item) for item in original}
    removed_accounts = {
        item.value
        for item in baseline
        if item.kind == "account"
        and item.role == "developer_account"
        and _identifier_key(item) not in current_by_key
    }
    output: list[ProjectIdentifier] = []
    emitted: set[tuple[str, str, str]] = set()
    for item in original:
        key = _identifier_key(item)
        if (
            item.kind == "did"
            and item.role == "developer_did"
            and str((item.metadata or {}).get("account") or "") in removed_accounts
        ):
            continue
        if key in baseline_by_key:
            replacement = current_by_key.get(key)
            if replacement is None:
                continue
            output.append(item if replacement == baseline_by_key[key] else replacement)
            emitted.add(key)
            continue
        output.append(item)
        emitted.add(key)
    for item in current:
        key = _identifier_key(item)
        if key in emitted:
            continue
        if key in baseline_by_key and item == baseline_by_key[key] and key not in original_keys:
            continue
        output.append(item)
        emitted.add(key)
    return output


def _derive_project_decisions(project: Mapping[str, Any]) -> list[ProjectDecision]:
    output: list[ProjectDecision] = []
    for item in project.get("blocked_identifiers", []):
        if isinstance(item, dict):
            output.append(ProjectDecision(
                "block_identifier",
                str(item.get("type") or ""),
                str(item.get("value") or ""),
                str(item.get("role") or ""),
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
                str(item.get("role") or ""),
                str(item.get("note") or ""),
                str(item.get("added_at") or ""),
                str(item.get("expires_at") or ""),
                metadata=dict(item),
            ))
    for item in project.get("promotion_deferrals", []):
        if isinstance(item, dict):
            output.append(ProjectDecision(
                "defer_promotion",
                target_role="",
                note=str(item.get("reason") or ""),
                created_at=str(item.get("added_at") or ""),
                expires_at=str(item.get("expires_at") or ""),
                cleared_at=str(item.get("cleared_at") or ""),
                metadata=dict(item),
            ))
    for item in project.get("promotion_priority_adjustments", []):
        if isinstance(item, dict):
            output.append(ProjectDecision(
                "lower_promotion_priority",
                "promotion_priority",
                "lowered",
                "",
                str(item.get("reason") or ""),
                str(item.get("added_at") or ""),
                cleared_at=str(item.get("cleared_at") or ""),
                metadata=dict(item),
            ))
    for value in project.get("dismissed_review_kinds", []):
        output.append(ProjectDecision("dismiss_review", "review_kind", str(value), ""))
    for value in project.get("separate_project_keys", []):
        output.append(ProjectDecision("separate_project", "project_key", str(value), ""))
    for item in project.get("confirmed_identifiers", []):
        if isinstance(item, dict):
            output.append(ProjectDecision(
                "confirm_identifier",
                str(item.get("type") or ""),
                str(item.get("value") or ""),
                str(item.get("role") or ""),
                str(item.get("note") or item.get("source") or ""),
                str(item.get("added_at") or ""),
                cleared_at=str(item.get("cleared_at") or ""),
                metadata=dict(item),
            ))
    for item in project.get("review_target_decisions", []):
        if isinstance(item, Mapping) and str(item.get("kind") or "") in {"dismiss_review_target", "defer_review_target"}:
            output.append(ProjectDecision(
                str(item.get("kind")),
                str(item.get("target_type") or ""),
                str(item.get("target_value") or ""),
                str(item.get("target_role") or ""),
                str(item.get("note") or ""),
                str(item.get("created_at") or ""),
                str(item.get("expires_at") or ""),
                str(item.get("cleared_at") or ""),
                metadata=copy.deepcopy(dict(item.get("metadata") or {})),
            ))
    return output


def project_decisions(project: Mapping[str, Any]) -> list[ProjectDecision]:
    current = _derive_project_decisions(project)
    original_payload = project.get(RUNTIME_DECISION_ROWS)
    baseline_payload = project.get(RUNTIME_DECISION_BASELINE)
    if isinstance(original_payload, list) and isinstance(baseline_payload, list):
        baseline = [ProjectDecision(**dict(item)) for item in baseline_payload if isinstance(item, Mapping)]
        if current == baseline:
            return [ProjectDecision(**dict(item)) for item in original_payload if isinstance(item, Mapping)]
    return current


def relation_rows(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .relations import normalize_relation_records

    original = project.get(RUNTIME_RELATION_ROWS)
    baseline = project.get(RUNTIME_RELATION_BASELINE)
    current_relations = normalize_relation_records(project.get("relation_evidence", []))
    if isinstance(original, list) and isinstance(baseline, list) and current_relations == baseline:
        return copy.deepcopy([dict(item) for item in original if isinstance(item, Mapping)])
    rows: list[dict[str, Any]] = []
    for item in current_relations:
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
    adoption_rows: Iterable[Mapping[str, Any]] = (),
    load_json: Any,
) -> dict[str, Any]:
    identifier_payload = [
        {
            "kind": str(row["kind"]),
            "value": str(row["value"]),
            "role": str(row["role"]),
            "classification": str(row["classification"]),
            "is_primary": bool(row["is_primary"]),
            "provider": str(row["provider"]),
            "metadata": load_json(str(row["metadata_json"]), {}),
            "observed_at": str(row["observed_at"]),
        }
        for row in identifier_rows
    ]
    relation_payload = [
        {
            "subject_type": str(row["subject_type"]),
            "subject_value": str(row["subject_value"]),
            "predicate": str(row["predicate"]),
            "object_type": str(row["object_type"]),
            "object_value": str(row["object_value"]),
            "classification": str(row["classification"]),
            "source": str(row["source"]),
            "locator": str(row["locator"]),
            "method": str(row["method"]),
            "context": load_json(str(row["context_json"]), {}),
            "observed_at": str(row["observed_at"]),
        }
        for row in relation_rows
    ]
    decision_payload = [
        {
            "kind": str(row["kind"]),
            "target_type": str(row["target_type"]),
            "target_value": str(row["target_value"]),
            "target_role": str(row["target_role"]),
            "note": str(row["note"]),
            "created_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"]),
            "cleared_at": str(row["cleared_at"]),
            "metadata": load_json(str(row["metadata_json"]), {}),
        }
        for row in decision_rows
    ]
    project.update({
        "input_nsids": [], "resolved_nsids": [], "lexicons": [], "external_lexicons": [],
        "recorded_lexicon_namespaces": [], "authority_domains": [], "service_urls": [],
        "service_url_candidates": [], "project_site_urls": [], "project_site_url_candidates": [],
        "source_code_urls": [], "candidate_source_code_urls": [],
        "developer_accounts": [], "candidate_developer_accounts": [],
        "bluesky_handles": [], "tangled_handles": [], "manual_seeds": [],
    })
    for row in identifier_payload:
        kind, value, role = row["kind"], row["value"], row["role"]
        classification = row["classification"]
        metadata = row["metadata"]
        if kind == "nsid":
            if role == "owned_nsid":
                target = "resolved_nsids" if classification == "confirmed" else "input_nsids"
                project[target].append(value)
        elif kind == "service_url":
            target = "service_urls" if classification == "confirmed" else "service_url_candidates"
            project[target].append(value)
        elif kind == "site_url" and role == "project_site":
            target = "project_site_urls" if classification == "confirmed" else "project_site_url_candidates"
            project[target].append(value)
        elif kind == "repo_url":
            if bool(row["is_primary"]) and classification == "confirmed": project["source_code_url"] = value
            target = "source_code_urls" if classification == "confirmed" else "candidate_source_code_urls"
            project[target].append(value)
        elif kind == "account":
            if role == "developer_account":
                account = _normalized_developer_account(copy.deepcopy(metadata) or {"handle": value})
                if account:
                    target = "developer_accounts" if classification == "confirmed" else "candidate_developer_accounts"
                    project[target].append(account)
            elif str(row["provider"]) == "bluesky" and ATPROTO_HANDLE_RE.fullmatch(value): project["bluesky_handles"].append(value)
            elif str(row["provider"]) == "tangled" and ATPROTO_HANDLE_RE.fullmatch(value): project["tangled_handles"].append(value)
        elif kind == "did" and role == "brand_did" and classification == "confirmed":
            project["brand_did"] = value

    project["service_urls"] = _unique([str(item) for item in project["service_urls"] if str(item)])
    confirmed_service_urls = set(project["service_urls"])
    project["service_url_candidates"] = _unique([
        str(item)
        for item in project["service_url_candidates"]
        if str(item) and str(item) not in confirmed_service_urls
    ])
    project["project_site_urls"] = _unique([
        str(item) for item in project["project_site_urls"] if str(item)
    ])
    confirmed_project_site_urls = set(project["project_site_urls"])
    project["project_site_url_candidates"] = _unique([
        str(item)
        for item in project["project_site_url_candidates"]
        if str(item) and str(item) not in confirmed_project_site_urls
    ])
    project["resolved_nsids"] = _unique([str(item) for item in project["resolved_nsids"] if str(item)])
    project["authority_domains"] = _unique([
        authority
        for nsid in project["resolved_nsids"]
        if (authority := identifiers.derive_authority_domain(nsid))
    ])
    confirmed_nsids = set(project["resolved_nsids"])
    project["input_nsids"] = _unique([
        str(item)
        for item in project["input_nsids"]
        if str(item) and str(item) not in confirmed_nsids
    ])

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
        "context": copy.deepcopy(row["context"]),
    } for row in relation_payload]

    project.update({
        "blocked_identifiers": [], "deferred_identifiers": [], "promotion_deferrals": [],
        "promotion_priority_adjustments": [],
        "dismissed_review_kinds": [], "separate_project_keys": [], "confirmed_identifiers": [],
        "review_target_decisions": [],
    })
    for row in decision_payload:
        kind = row["kind"]
        metadata = row["metadata"]
        if kind == "block_identifier":
            project["blocked_identifiers"].append(metadata or {
                "type": str(row["target_type"]), "value": str(row["target_value"]),
                "role": str(row["target_role"]),
                "note": str(row["note"]), "added_at": str(row["created_at"]),
            })
        elif kind == "defer_identifier":
            project["deferred_identifiers"].append(metadata or {
                "type": str(row["target_type"]), "value": str(row["target_value"]),
                "role": str(row["target_role"]),
                "note": str(row["note"]), "added_at": str(row["created_at"]),
                "expires_at": str(row["expires_at"]),
            })
        elif kind == "defer_promotion":
            project["promotion_deferrals"].append(metadata or {
                "reason": str(row["note"]), "added_at": str(row["created_at"]),
                "expires_at": str(row["expires_at"]), "cleared_at": str(row["cleared_at"]),
            })
        elif kind == "lower_promotion_priority":
            project["promotion_priority_adjustments"].append(metadata or {
                "reason": str(row["note"]),
                "added_at": str(row["created_at"]), "cleared_at": str(row["cleared_at"]),
            })
        elif kind == "dismiss_review":
            project["dismissed_review_kinds"].append(str(row["target_value"]))
        elif kind == "separate_project":
            project["separate_project_keys"].append(str(row["target_value"]))
        elif kind == "confirm_identifier":
            project["confirmed_identifiers"].append(metadata or {
                "type": str(row["target_type"]), "value": str(row["target_value"]),
                "note": str(row["note"]), "added_at": str(row["created_at"]),
                "cleared_at": str(row["cleared_at"]),
            })
        elif kind in {"dismiss_review_target", "defer_review_target"}:
            project["review_target_decisions"].append({
                "kind": kind,
                "target_type": str(row["target_type"]),
                "target_value": str(row["target_value"]),
                "note": str(row["note"]),
                "created_at": str(row["created_at"]),
                "expires_at": str(row["expires_at"]),
                "cleared_at": str(row["cleared_at"]),
                "metadata": copy.deepcopy(metadata or {}),
            })
    project[RUNTIME_IDENTIFIER_ROWS] = copy.deepcopy(identifier_payload)
    project[RUNTIME_RELATION_ROWS] = copy.deepcopy(relation_payload)
    project[RUNTIME_DECISION_ROWS] = [asdict(ProjectDecision(**item)) for item in decision_payload]
    project[RUNTIME_ADOPTION_ROWS] = [dict(row) for row in adoption_rows]
    return refresh_baselines(project)
