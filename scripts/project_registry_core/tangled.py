from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

from .transport import atproto, http_transport
from . import project_record
from .identity import identifiers
from .repositories import repo_urls
from .evidence import CollectorFailure, CollectorResult, EvidenceBundle, EvidenceRecord
from .relations import relation_record
from .transport.reliability import RetryPolicy, run_with_retry


TANGLED_COLLECTION = "sh.tangled.repo"
REPOSITORY_POLICY_VERSION = "tangled-project-repository-v1"


@dataclass(frozen=True)
class TangledDependencies:
    now: Callable[[], str]
    resolve_handle: Callable[[str], dict[str, Any]]
    resolve_did_document: Callable[[str], dict[str, Any]]
    extract_pds_url: Callable[[dict[str, Any]], str]
    list_records: Callable[[str, str, str], dict[str, Any]]
    retry_policy: RetryPolicy = RetryPolicy()


def tangled_repo_url(handle: str, record: dict[str, Any], rkey: str) -> str:
    name = str(record.get("name") or rkey).strip().strip("/")
    owner = urllib.parse.quote(handle.strip().lower(), safe="._-")
    return f"https://tangled.org/{owner}/{urllib.parse.quote(name, safe='._-')}"


def repository_match_reason(project: dict[str, Any], url: str, record: dict[str, Any]) -> str:
    """Project-specific candidate evidence; account ownership alone is insufficient."""
    normalized = repo_urls.canonical_repo_url(url)
    if not normalized or not repo_urls.is_supported_repo_url(normalized):
        return ""
    if project_record.identifier_is_blocked(project, "repo_url", normalized) or project_record.identifier_is_deferred(project, "repo_url", normalized):
        return ""
    known = set(project_record.active_manual_repo_urls(project)) | set(project.get("project_repository_urls", []))
    if normalized in {repo_urls.canonical_repo_url(item) for item in known}:
        return "accepted_repository"
    # The canonical URL already contains the observed record's repository name.
    # Re-evaluate that same candidate basis when metadata is no longer present.
    name = str(record.get("name") or urllib.parse.unquote(
        urllib.parse.urlsplit(normalized).path.rsplit("/", 1)[-1]
    )).strip().lower()
    project_name = str(project.get("name") or "").strip().lower()
    if project_name and name in {project_name, project_name.replace(" ", "-")}:
        return "project_name_exact"
    website = identifiers.normalize_url(str(record.get("website") or ""))
    services = {identifiers.normalize_url(str(item)) for item in project.get("service_urls", [])}
    services.add(identifiers.normalize_url(str(project.get("website_url") or "")))
    if website and website in services and not project_record.identifier_is_blocked(project, "service_url", website) and not project_record.identifier_is_deferred(project, "service_url", website):
        return "project_website_exact"
    return ""


def repo_relevance(project: dict[str, Any], record: dict[str, Any], handle: str) -> int:
    url = tangled_repo_url(handle, record, str(record.get("name") or ""))
    reason = repository_match_reason(project, url, record)
    return {"accepted_repository": 5, "project_name_exact": 4, "project_website_exact": 5}.get(reason, 0)


def atproto_relevant(record: dict[str, Any]) -> bool:
    raw_topics = record.get("topics")
    topics = {
        str(item).lower()
        for item in (raw_topics if isinstance(raw_topics, list) else [])
        if str(item)
    }
    text = f"{record.get('name', '')} {record.get('description', '')}".lower()
    return bool(topics & {"atproto", "at-protocol", "bluesky", "pds", "lexicon"}) or any(
        token in text for token in ("atproto", "at protocol", "bluesky", "lexicon", "personal data server")
    )


def collect_account_repos(project: dict[str, Any], handle: str, dependencies: TangledDependencies) -> EvidenceBundle:
    observed_at = dependencies.now()
    bundle = EvidenceBundle(project_key=str(project.get("project_key") or ""), collected_at=observed_at)
    request_count = 0
    try:
        profile, attempts = run_with_retry(lambda: dependencies.resolve_handle(handle), policy=dependencies.retry_policy)
        request_count += attempts
        did = str(profile.get("did") or "")
        if not did:
            raise ValueError(f"no DID resolved for {handle}")
        did_doc, attempts = run_with_retry(lambda: dependencies.resolve_did_document(did), policy=dependencies.retry_policy)
        request_count += attempts
        pds_url = dependencies.extract_pds_url(did_doc)
        if not pds_url:
            raise ValueError(f"no PDS endpoint for {did}")
        cursor = ""
        record_count = 0
        while True:
            payload, attempts = run_with_retry(
                lambda: dependencies.list_records(pds_url, did, cursor), policy=dependencies.retry_policy
            )
            request_count += attempts
            for item in payload.get("records", []) if isinstance(payload, dict) else []:
                if not isinstance(item, dict):
                    continue
                uri = str(item.get("uri") or "")
                rkey = uri.rsplit("/", 1)[-1]
                record = dict(item.get("value") or {})
                url = tangled_repo_url(handle, record, rkey)
                score = repo_relevance(project, record, handle)
                bundle.add_record(EvidenceRecord(
                    kind="tangled_repo_snapshot", source=f"tangled_pds:{handle}",
                    classification="candidate", observed_at=observed_at,
                    value={"owner_handle": handle, "owner_did": did, "repo_url": url,
                           "repo_did": str(record.get("repoDid") or ""), "record": record, "score": score},
                    context={"policy_version": REPOSITORY_POLICY_VERSION,
                             "project_match_reason": repository_match_reason(project, url, record)},
                ))
                bundle.add_record(relation_record(
                    source=f"tangled_pds:{handle}", classification="confirmed", observed_at=observed_at,
                    subject_type="did", subject_value=did, predicate="owns_repository",
                    object_type="repo", object_value=url, locator=uri, method="pds_list_records",
                ))
                record_count += 1
            cursor = str(payload.get("cursor") or "") if isinstance(payload, dict) else ""
            if not cursor:
                break
        bundle.add_collector_result(CollectorResult(
            source="tangled_pds", operation="list_account_repos", status="success", observed_at=observed_at,
            request_count=request_count, result_count=record_count,
        ))
    except Exception as exc:
        bundle.add_failure(CollectorFailure(
            source="tangled_pds", operation="list_account_repos", target=handle,
            category="collector_error", message=f"Tangled account repo collection failed for {handle}: {exc}", retryable=False,
        ))
        bundle.add_collector_result(CollectorResult(
            source="tangled_pds", operation="list_account_repos", status="failed", observed_at=observed_at,
            request_count=max(1, request_count), stop_reason="collector_error",
        ))
    return bundle


def normalize_jetstream_event(value: Any) -> dict[str, Any] | None:
    item = dict(value) if isinstance(value, dict) else {}
    if item.get("type") != "event" or str(item.get("collection") or "") != TANGLED_COLLECTION:
        return None
    operation = str(item.get("operation") or "")
    if operation not in {"create", "update", "delete"}:
        return None
    owner_did = str(item.get("did") or "").strip()
    rkey = str(item.get("rkey") or "").strip()
    seq = int(item.get("seq") or 0)
    if not owner_did or not rkey or seq <= 0:
        return None
    record = dict(item.get("record") or {}) if operation != "delete" else {}
    return {
        "owner_did": owner_did,
        "rkey": rkey,
        "seq": seq,
        "time": str(item.get("time") or ""),
        "operation": operation,
        "record": record,
        "atproto_relevant": operation != "delete" and atproto_relevant(record),
    }


def list_atproto_records(pds_url: str, did: str, cursor: str = "") -> dict[str, Any]:
    params: dict[str, Any] = {"repo": did, "collection": TANGLED_COLLECTION, "limit": 100}
    if cursor:
        params["cursor"] = cursor
    payload = http_transport.request_json(
        f"{pds_url}/xrpc/com.atproto.repo.listRecords?{urllib.parse.urlencode(params)}",
        timeout=atproto.REQUEST_TIMEOUT_LEXICON_HTTP,
    )
    return payload if isinstance(payload, dict) else {}
