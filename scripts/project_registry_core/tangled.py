from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

from .evidence import CollectorFailure, CollectorResult, EvidenceBundle, EvidenceRecord
from .relations import relation_record
from .reliability import RetryPolicy, run_with_retry


TANGLED_COLLECTION = "sh.tangled.repo"


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


def repo_relevance(project: dict[str, Any], record: dict[str, Any], handle: str) -> int:
    score = 0
    name = str(record.get("name") or "").lower()
    project_name = str(project.get("name") or "").lower()
    description = str(record.get("description") or "").lower()
    website = str(record.get("website") or "").rstrip("/")
    if project_name and name and (project_name == name or project_name.replace(" ", "-") == name):
        score += 4
    if website and website in {
        str(project.get("website_url") or "").rstrip("/"),
        *(str(item).rstrip("/") for item in project.get("service_urls", [])),
    }:
        score += 5
    identity_tokens = {token for token in project_name.replace("-", " ").split() if len(token) >= 4}
    if any(token in description for token in identity_tokens):
        score += 2
    if handle and any(str(item.get("handle") or "").lower() == handle.lower() for item in project.get("developer_accounts", []) if isinstance(item, dict)):
        score += 1
    return score


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
                    classification="confirmed" if score >= 5 else "candidate", observed_at=observed_at,
                    value={"owner_handle": handle, "owner_did": did, "repo_url": url,
                           "repo_did": str(record.get("repoDid") or ""), "record": record, "score": score},
                ))
                bundle.add_record(relation_record(
                    source=f"tangled_pds:{handle}", classification="confirmed", observed_at=observed_at,
                    subject_type="did", subject_value=did, predicate="project_repository",
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
