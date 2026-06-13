from __future__ import annotations

import copy
from typing import Any, Callable

from .evidence import CollectorResult, EvidenceBundle, EvidenceRecord


RESOURCE_PATCH_FIELDS = (
    "resource_url_records", "service_url_candidates", "website_url_candidates",
    "candidate_source_code_urls", "source_repo_records", "developer_accounts",
    "candidate_developer_accounts", "bluesky_handles", "evidence_urls",
    "investigation_log", "service_status", "service_status_confidence", "service_status_evidence",
)


def collect_lexicon_result(project_key: str, nsid: str, result: dict[str, Any], observed_at: str) -> EvidenceBundle:
    bundle = EvidenceBundle(project_key=project_key, collected_at=observed_at)
    bundle.add_record(EvidenceRecord(
        kind="lexicon_resolution_snapshot", source=f"lexicon_resolution:{nsid}",
        classification="confirmed", observed_at=observed_at,
        value={"nsid": nsid, "result": result},
    ))
    bundle.add_collector_result(CollectorResult(
        source="lexicon_resolution", operation="resolve", status="success", observed_at=observed_at,
        request_count=3, result_count=1,
    ))
    return bundle


def collect_record_samples(project_key: str, collections: list[str], samples: list[dict[str, Any]], observed_at: str) -> EvidenceBundle:
    bundle = EvidenceBundle(project_key=project_key, collected_at=observed_at)
    bundle.add_record(EvidenceRecord(
        kind="record_samples_snapshot", source="ufos_record_samples", classification="derived",
        observed_at=observed_at, value={"collections": collections, "samples": samples},
    ))
    bundle.add_collector_result(CollectorResult(
        source="ufos_record_samples", operation="sample", status="success", observed_at=observed_at,
        request_count=1, result_count=len(samples),
    ))
    return bundle


def collect_resource_page_patch(
    project: dict[str, Any],
    mutate_copy: Callable[[dict[str, Any]], None],
    observed_at: str,
) -> EvidenceBundle:
    working = copy.deepcopy(project)
    mutate_copy(working)
    patch = {field: copy.deepcopy(working.get(field)) for field in RESOURCE_PATCH_FIELDS if working.get(field) != project.get(field)}
    bundle = EvidenceBundle(project_key=str(project.get("project_key") or ""), collected_at=observed_at)
    bundle.add_record(EvidenceRecord(
        kind="project_patch", source="resource_page", classification="derived",
        observed_at=observed_at, value={"fields": patch}, context={"allowed_fields": list(RESOURCE_PATCH_FIELDS)},
    ))
    bundle.add_collector_result(CollectorResult(
        source="resource_page", operation="deep_probe", status="success", observed_at=observed_at,
        request_count=1, result_count=len(patch),
    ))
    return bundle
