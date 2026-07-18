#!/usr/bin/env python3

from __future__ import annotations

import copy
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from project_registry_core import EvidenceBundle
from project_registry_core import storage as investigation_storage
from project_registry_core import relations as relation_core
from project_registry_core import collector_adapters as collector_adapter_core
from project_registry_core import runtime_view
from project_registry_core import llm_transport
from project_registry_core import identifiers as identifier_core
from project_registry_core import repo_urls as repo_url_core

from project_registry_core.registry import (
    ENRICH_IO_MAX_WORKERS,
    REQUEST_TIMEOUT_LEXICON_DNS,
    REQUEST_TIMEOUT_LEXICON_HTTP,
    add_investigation_log,
    apply_authority_service_probe_result,
    apply_candidate_repo_trust_evidence,
    apply_collector_snapshot_evidence,
    apply_github_repo_evidence,
    apply_investigation_evidence,
    apply_llm_output,
    apply_promotion_state,
    apply_service_evidence,
    apply_tangled_evidence,
    authority_probe_all_urls,
    authority_probe_url_chains,
    build_triage_prompt,
    candidate_repo_trust_probe_urls,
    clean_string_list,
    collect_github_repo_evidence,
    collect_investigation_evidence,
    collect_oauth_metadata_evidence,
    collect_service_evidence_with_followups,
    collect_tangled_account_evidence,
    collect_tangled_repo_evidence,
    compute_duplicate_candidates,
    compute_merge_recommendation,
    confirm_source_code_url,
    confirm_source_code_url_with_trust,
    derived_authority_probe_urls,
    enrich_from_resource_pages,
    failed_collector_bundle,
    fetch_lexicon_record,
    fetch_recent_record_samples,
    finalize_project_shape,
    load_config,
    note_failure,
    plan_investigation_queries,
    probe_service_url,
    record_promotion_transition,
    reset_investigation_state,
    select_authority_probe_urls,
    service_probe_timeout_for_project,
    should_refresh_lexicon_surface,
    should_refresh_recent_record_samples,
)
from project_registry_core.project_record import (
    add_unique,
    ensure_project_shape,
    iso_now,
    provider_repo_url_values,
    service_url_candidate_values,
    source_code_url_values,
    unique_list,
)


def enrich_project(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    *,
    llm_endpoint: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    llm_reasoning_effort: str | None = None,
    llm_api_style: str | None = None,
    deep_investigation: bool = False,
) -> dict[str, Any]:
    run_clock_started = time.perf_counter()
    phase_started = run_clock_started
    phase_timings: dict[str, int] = {}
    llm_requests: list[dict[str, Any]] = []
    project = ensure_project_shape(project)
    run_started_at = iso_now()
    run_input_project = copy.deepcopy(project)
    run_evidence = EvidenceBundle(project_key=str(project.get("project_key") or ""), collected_at=run_started_at)
    previous_status = str(project.get("status") or "draft")
    project["_run_errors"] = []
    reset_investigation_state(project)

    initial_nsids = unique_list(project.get("input_nsids", []) + project.get("resolved_nsids", []))
    if should_refresh_lexicon_surface(project, initial_nsids):
        with ThreadPoolExecutor(max_workers=min(ENRICH_IO_MAX_WORKERS, len(initial_nsids))) as executor:
            lexicon_futures = {
                nsid: executor.submit(
                    fetch_lexicon_record,
                    nsid,
                    dns_timeout=REQUEST_TIMEOUT_LEXICON_DNS,
                    http_timeout=REQUEST_TIMEOUT_LEXICON_HTTP,
                )
                for nsid in initial_nsids
            }
            for nsid in initial_nsids:
                try:
                    lexicon_evidence = collector_adapter_core.collect_lexicon_result(
                        str(project.get("project_key") or ""), nsid, lexicon_futures[nsid].result(), iso_now()
                    )
                    apply_collector_snapshot_evidence(project, lexicon_evidence)
                    run_evidence.extend(lexicon_evidence)
                except Exception as exc:
                    note_failure(project, f"lexicon enrichment failed for {nsid}: {exc}")
                    run_evidence.extend(failed_collector_bundle(
                        project, source="lexicon_resolution", operation="resolve", target=nsid, exc=exc,
                    ))

    if should_refresh_recent_record_samples(project):
        try:
            collections = unique_list(project.get("resolved_nsids", []) + project.get("input_nsids", []))[:6]
            sample_evidence = collector_adapter_core.collect_record_samples(
                str(project.get("project_key") or ""), collections,
                fetch_recent_record_samples(collections, timeout=REQUEST_TIMEOUT_LEXICON_HTTP), iso_now(),
            )
            apply_collector_snapshot_evidence(project, sample_evidence)
            run_evidence.extend(sample_evidence)
        except Exception as exc:
            note_failure(project, f"recent record sampling failed: {exc}")
            run_evidence.extend(failed_collector_bundle(
                project, source="ufos_record_samples", operation="sample", target=str(project.get("project_key") or ""), exc=exc,
            ))

    initial_repo_urls = unique_list(source_code_url_values(project))
    collected_github_repo_urls: set[str] = set()
    collected_tangled_repo_urls: set[str] = set()
    trust_inspected_repo_urls: set[str] = set()
    for repo_url in initial_repo_urls:
        if repo_url_core.extract_github_slug(repo_url) is not None:
            normalized_repo_url = repo_url_core.canonical_repo_url(repo_url)
            if normalized_repo_url:
                collected_github_repo_urls.add(normalized_repo_url)
            try:
                repo_evidence = collect_github_repo_evidence(project, repo_url)
                apply_github_repo_evidence(project, repo_evidence)
                run_evidence.extend(repo_evidence)
            except Exception as exc:
                note_failure(project, f"github enrichment failed for {repo_url}: {exc}")
                run_evidence.extend(failed_collector_bundle(
                    project, source="github_repo", operation="inspect", target=repo_url, exc=exc,
                ))
        elif normalized_tangled_repo_url := repo_url_core.normalize_tangled_repo_url(repo_url):
            collected_tangled_repo_urls.add(normalized_tangled_repo_url)
            try:
                repo_evidence = collect_tangled_repo_evidence(project, repo_url)
                apply_tangled_evidence(project, repo_evidence)
                run_evidence.extend(repo_evidence)
            except Exception as exc:
                note_failure(project, f"tangled repo enrichment failed for {repo_url}: {exc}")
                run_evidence.extend(failed_collector_bundle(
                    project, source="tangled_repo", operation="inspect", target=repo_url, exc=exc,
                ))

    discovered_nsids = [
        nsid
        for nsid in unique_list(project.get("input_nsids", []) + project.get("resolved_nsids", []))
        if nsid and nsid not in initial_nsids
    ]
    if should_refresh_lexicon_surface(project, discovered_nsids):
        for nsid in discovered_nsids:
            try:
                lexicon_evidence = collector_adapter_core.collect_lexicon_result(
                    str(project.get("project_key") or ""), nsid,
                    fetch_lexicon_record(nsid, dns_timeout=REQUEST_TIMEOUT_LEXICON_DNS, http_timeout=REQUEST_TIMEOUT_LEXICON_HTTP),
                    iso_now(),
                )
                apply_collector_snapshot_evidence(project, lexicon_evidence)
                run_evidence.extend(lexicon_evidence)
            except Exception as exc:
                note_failure(project, f"lexicon enrichment failed for discovered repo hint {nsid}: {exc}")
                run_evidence.extend(failed_collector_bundle(
                    project, source="lexicon_resolution", operation="resolve", target=nsid, exc=exc,
                ))

    authority_probe_chains = authority_probe_url_chains(project)
    authority_probe_urls = set(authority_probe_all_urls(authority_probe_chains))
    authority_probe_urls.update(derived_authority_probe_urls(project))
    initial_service_urls = unique_list(
        ([project.get("website_url")] if project.get("website_url") else [])
        + project.get("service_urls", [])
        + service_url_candidate_values(project)
        + list(authority_probe_urls)
    )
    if initial_service_urls:
        with ThreadPoolExecutor(max_workers=min(ENRICH_IO_MAX_WORKERS, len(initial_service_urls))) as executor:
            service_futures = {
                service_url: executor.submit(
                    probe_service_url,
                    service_url,
                    timeout=service_probe_timeout_for_project(project, service_url),
                )
                for service_url in initial_service_urls
            }
            probe_results: dict[str, dict[str, Any]] = {}
            probe_errors: dict[str, Exception] = {}
            for service_url in initial_service_urls:
                try:
                    probe_results[service_url] = service_futures[service_url].result()
                except Exception as exc:
                    probe_errors[service_url] = exc
            selected_authority_probe_urls = select_authority_probe_urls(authority_probe_chains, probe_results)
            for service_url in initial_service_urls:
                try:
                    if service_url in probe_errors:
                        raise probe_errors[service_url]
                    probe_result = probe_results[service_url]
                    normalized_service_url = identifier_core.normalize_url(service_url)
                    is_authority_probe = normalized_service_url in authority_probe_urls
                    if is_authority_probe and normalized_service_url not in selected_authority_probe_urls:
                        continue
                    lane = "authority" if is_authority_probe else "static"
                    service_evidence = collect_service_evidence_with_followups(project, service_url, probe_result, lane=lane)
                    apply_service_evidence(project, service_evidence)
                    if lane == "authority":
                        apply_authority_service_probe_result(project, service_url, probe_result)
                    run_evidence.extend(service_evidence)
                    for metadata_url in relation_core.oauth_metadata_candidates(
                        service_url,
                        probe_result.get("links", []),
                        str(probe_result.get("html") or ""),
                    ):
                        try:
                            oauth_evidence = collect_oauth_metadata_evidence(project, metadata_url)
                            apply_investigation_evidence(project, oauth_evidence)
                            run_evidence.extend(oauth_evidence)
                        except Exception as exc:
                            note_failure(project, f"OAuth client metadata failed for {metadata_url}: {exc}")
                            run_evidence.extend(failed_collector_bundle(
                                project, source="oauth_client_metadata", operation="fetch", target=metadata_url, exc=exc,
                            ))
                except Exception as exc:
                    normalized_service_url = identifier_core.normalize_url(service_url)
                    if normalized_service_url and normalized_service_url not in authority_probe_urls:
                        project["failed_service_urls"] = add_unique(project.get("failed_service_urls", []), normalized_service_url)
                    note_failure(project, f"service probe failed for {service_url}: {exc}")
                    run_evidence.extend(failed_collector_bundle(
                        project, source="service_static", operation="probe", target=service_url, exc=exc,
                    ))

    for repo_url in candidate_repo_trust_probe_urls(project):
        trust_inspected_repo_urls.add(repo_url)
        provider = repo_url_core.infer_repo_provider(repo_url)
        try:
            if provider == "github":
                trust_evidence = collect_github_repo_evidence(project, repo_url, trust_only=True)
            elif provider == "tangled":
                trust_evidence = collect_tangled_repo_evidence(project, repo_url, trust_only=True)
            else:
                continue
            run_evidence.extend(trust_evidence)
            apply_candidate_repo_trust_evidence(project, repo_url, trust_evidence)
            confirmed = confirm_source_code_url_with_trust(
                project,
                repo_url,
                "candidate_repo_trust_inspection",
                relation="mutual_first_party_link",
                reason="first-party service and candidate repository link to each other",
                evidence_kind="candidate_repo_trust_inspection",
                evidence_url=repo_url,
            )
            if not confirmed:
                continue
            if provider == "github":
                collected_github_repo_urls.add(repo_url_core.canonical_repo_url(repo_url))
                repo_evidence = collect_github_repo_evidence(project, repo_url)
                apply_github_repo_evidence(project, repo_evidence, confirm_repo_snapshot=False)
            else:
                collected_tangled_repo_urls.add(repo_url_core.normalize_tangled_repo_url(repo_url) or repo_url)
                repo_evidence = collect_tangled_repo_evidence(project, repo_url)
                apply_tangled_evidence(project, repo_evidence)
            run_evidence.extend(repo_evidence)
        except Exception as exc:
            note_failure(project, f"candidate repository trust inspection failed for {repo_url}: {exc}")
            run_evidence.extend(failed_collector_bundle(
                project, source=f"{provider}_repo", operation="trust_inspect", target=repo_url, exc=exc,
            ))

    secondary_repo_urls = [
        repo_url
        for repo_url in unique_list(provider_repo_url_values(project, "github"))
        if repo_url_core.canonical_repo_url(repo_url) not in collected_github_repo_urls
    ]
    for repo_url in secondary_repo_urls:
        try:
            repo_evidence = collect_github_repo_evidence(project, repo_url)
            apply_github_repo_evidence(project, repo_evidence)
            run_evidence.extend(repo_evidence)
        except Exception as exc:
            note_failure(project, f"github secondary enrichment failed for {repo_url}: {exc}")
            run_evidence.extend(failed_collector_bundle(
                project, source="github_repo", operation="inspect", target=repo_url, exc=exc,
            ))
    secondary_tangled_repo_urls = [
        repo_url
        for repo_url in unique_list(provider_repo_url_values(project, "tangled"))
        if repo_url_core.normalize_tangled_repo_url(repo_url) not in collected_tangled_repo_urls
    ]
    for repo_url in secondary_tangled_repo_urls:
        try:
            repo_evidence = collect_tangled_repo_evidence(project, repo_url)
            apply_tangled_evidence(project, repo_evidence)
            run_evidence.extend(repo_evidence)
        except Exception as exc:
            note_failure(project, f"tangled secondary enrichment failed for {repo_url}: {exc}")
            run_evidence.extend(failed_collector_bundle(
                project, source="tangled_repo", operation="inspect", target=repo_url, exc=exc,
            ))

    if deep_investigation:
        try:
            resource_evidence = collector_adapter_core.collect_resource_page_patch(
                project, enrich_from_resource_pages, iso_now()
            )
            apply_collector_snapshot_evidence(project, resource_evidence)
            run_evidence.extend(resource_evidence)
        except Exception as exc:
            note_failure(project, f"resource page enrichment failed: {exc}")
            run_evidence.extend(failed_collector_bundle(
                project, source="resource_page", operation="deep_probe", target=str(project.get("project_key") or ""), exc=exc,
            ))

    for repo_url in relation_core.corroborate_source_repositories(project):
        confirm_source_code_url(
            project, repo_url, "relation_corroboration", relation="mutual_first_party_link",
            reason="repository and first-party application independently link to each other",
            evidence_kind="relation_corroboration", evidence_url=repo_url,
        )

    project = finalize_project_shape(project)
    phase_timings["initial_collection_ms"] = max(0, round((time.perf_counter() - phase_started) * 1000))
    phase_started = time.perf_counter()

    investigation_plan = plan_investigation_queries(
        project,
        llm_endpoint=llm_endpoint,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        llm_reasoning_effort=llm_reasoning_effort,
        llm_api_style=llm_api_style,
        llm_runner=lambda prompt, **kwargs: llm_transport.run_llm_with_metrics(
            prompt, purpose="planning", requests=llm_requests, **kwargs
        ),
    )
    add_investigation_log(
        project,
        "investigation_plan",
        reason=str(investigation_plan.get("reason") or ""),
        repo_search_queries=clean_string_list(investigation_plan.get("repo_search_queries"), limit=6),
        post_search_queries=clean_string_list(investigation_plan.get("post_search_queries"), limit=6),
        at=iso_now(),
    )
    phase_timings["planning_ms"] = max(0, round((time.perf_counter() - phase_started) * 1000))
    phase_started = time.perf_counter()
    investigation_evidence = collect_investigation_evidence(project, investigation_plan)
    apply_investigation_evidence(project, investigation_evidence)
    run_evidence.extend(investigation_evidence)

    tangled_config = dict(load_config().get("tangled_repo_discovery") or {})
    if tangled_config.get("enabled", True):
        confirmed_handles = unique_list([
            str(item.get("handle") or "")
            for item in project.get("developer_accounts", [])
            if isinstance(item, dict) and str(item.get("handle") or "")
        ])[:2]
        for handle in confirmed_handles:
            tangled_evidence = collect_tangled_account_evidence(project, handle)
            apply_tangled_evidence(project, tangled_evidence)
            run_evidence.extend(tangled_evidence)

    phase_timings["investigation_collection_ms"] = max(
        0, round((time.perf_counter() - phase_started) * 1000)
    )
    phase_started = time.perf_counter()
    try:
        prompt, evidence = build_triage_prompt(project)
        payload, meta = llm_transport.run_llm_with_metrics(
            prompt,
            purpose="triage",
            requests=llm_requests,
            llm_endpoint=llm_endpoint,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
            llm_reasoning_effort=llm_reasoning_effort,
            llm_api_style=llm_api_style,
        )
        apply_llm_output(project, payload, meta, evidence)
    except Exception as exc:
        note_failure(project, f"LLM review failed: {exc}")

    for repo_url in candidate_repo_trust_probe_urls(project):
        if repo_url in trust_inspected_repo_urls or repo_url in source_code_url_values(project):
            continue
        trust_inspected_repo_urls.add(repo_url)
        provider = repo_url_core.infer_repo_provider(repo_url)
        try:
            if provider == "github":
                trust_evidence = collect_github_repo_evidence(project, repo_url, trust_only=True)
            elif provider == "tangled":
                trust_evidence = collect_tangled_repo_evidence(project, repo_url, trust_only=True)
            else:
                continue
            run_evidence.extend(trust_evidence)
            apply_candidate_repo_trust_evidence(project, repo_url, trust_evidence)
            confirm_source_code_url_with_trust(
                project,
                repo_url,
                "candidate_repo_trust_inspection",
                relation="mutual_first_party_link",
                reason="LLM relationship assessment and current reverse link support the same project",
                evidence_kind="candidate_repo_trust_inspection",
                evidence_url=repo_url,
            )
        except Exception as exc:
            note_failure(project, f"candidate repository trust inspection failed for {repo_url}: {exc}")

    phase_timings["triage_ms"] = max(0, round((time.perf_counter() - phase_started) * 1000))
    phase_started = time.perf_counter()
    project = finalize_project_shape(project)
    project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
    project["merge_recommendation"] = compute_merge_recommendation(project)
    project["last_run_errors"] = unique_list(project.pop("_run_errors", []))
    project = apply_promotion_state(project)
    project = record_promotion_transition(project, previous_status)
    phase_timings["evaluation_ms"] = max(0, round((time.perf_counter() - phase_started) * 1000))
    run_id = investigation_storage.save_investigation_run(
        conn,
        project_key=str(project.get("project_key") or ""),
        started_at=run_started_at,
        completed_at=iso_now(),
        input_project=run_input_project,
        output_project=project,
        bundle=run_evidence,
        run_metrics={
            "total_ms": max(0, round((time.perf_counter() - run_clock_started) * 1000)),
            "phases": phase_timings,
            "llm_requests": llm_requests,
        },
    )
    project[runtime_view.RUNTIME_INVESTIGATION_RUN_ID] = run_id
    return project
