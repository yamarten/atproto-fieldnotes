#!/usr/bin/env python3

from __future__ import annotations

from . import lexicon_types

import copy
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from project_registry_core import CollectorResult, EvidenceBundle
from project_registry_core import storage as investigation_storage
from project_registry_core import relations as relation_core
from project_registry_core import collector_adapters as collector_adapter_core
from project_registry_core import runtime_view
from project_registry_core import llm_transport
from project_registry_core import triage_reuse, enrich_observations
from project_registry_core import identifiers as identifier_core
from project_registry_core import repo_urls as repo_url_core
from project_registry_core import repository_analysis as repository_analysis_core

from project_registry_core.registry import (
    ENRICH_IO_MAX_WORKERS,
    REQUEST_TIMEOUT_LEXICON_DNS,
    REQUEST_TIMEOUT_LEXICON_HTTP,
    add_investigation_log,
    apply_authority_service_probe_result,
    apply_candidate_repo_trust_evidence,
    apply_collector_snapshot_evidence,
    apply_github_repo_evidence,
    apply_homepage_role_evidence,
    apply_investigation_evidence,
    apply_llm_output,
    add_resource_url,
    apply_promotion_state,
    apply_service_evidence,
    apply_tangled_evidence,
    authority_probe_all_urls,
    authority_probe_url_chains,
    build_triage_prompt,
    candidate_repo_trust_probe_urls,
    clean_string_list,
    collect_github_repo_evidence,
    collect_homepage_role_evidence,
    apply_publication_candidates,
    collect_codeberg_repo_evidence,
    collect_investigation_evidence,
    collect_oauth_metadata_evidence,
    collect_service_evidence_with_followups,
    collect_tangled_account_evidence,
    collect_tangled_repo_evidence,
    compute_duplicate_candidates,
    compute_merge_recommendation,
    confirm_project_repository_url,
    confirm_project_repository_url_with_trust,
    derived_authority_probe_urls,
    enrich_from_resource_pages,
    exact_project_page_component_role,
    failed_collector_bundle,
    fetch_lexicon_record,
    fetch_recent_record_samples,
    finalize_project_shape,
    load_config,
    note_failure,
    plan_investigation_queries,
    probe_service_url,
    reconcile_trust_supported_url_candidates,
    record_promotion_transition,
    repository_homepage_probe_urls,
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
    project_repository_url_values,
    unique_list,
)


def append_triage_prompt_suffix(prompt: str, suffix: str | None) -> tuple[str, str]:
    normalized_suffix = str(suffix or "").strip()
    if not normalized_suffix:
        return prompt, ""
    rendered = f"{prompt}\n\nEvaluation prompt variant:\n{normalized_suffix}\n"
    return rendered, llm_transport.prompt_suffix_hash(normalized_suffix)


def record_repository_collection_failure(project: dict[str, Any], repo_url: str) -> None:
    material = repository_analysis_core.unavailable_material(
        repo_url, reason="provider_fetch_failed"
    )
    repository_analysis_core.mark_failure(
        project, material, iso_now(), "provider_fetch_failed"
    )


@dataclass
class InitialCollection:
    """Owned, DB/LLM-free state before repository analysis; never a completed run."""

    project: dict[str, Any]
    input_project: dict[str, Any]
    started_at: str
    elapsed_seconds: float
    evidence: EvidenceBundle
    operation_timer: enrich_observations.OperationTimer
    initial_repo_urls: list[str]
    collected_repo_evidence: dict[str, EvidenceBundle]
    trust_inspected_repo_urls: set[str]
    initial_service_urls: list[str]
    probe_results: dict[str, dict[str, Any]]
    homepage_results: dict[str, dict[str, Any]]


def collect_initial(project: dict[str, Any]) -> InitialCollection:
    """Collect on an owned snapshot. No connection, LLM, save or checkpoint access."""
    started = time.perf_counter()
    project = ensure_project_shape(project)
    run_started_at = iso_now()
    run_input_project = copy.deepcopy(project)
    run_evidence = EvidenceBundle(project_key=str(project.get("project_key") or ""), collected_at=run_started_at)
    operation_timer = enrich_observations.OperationTimer()
    project["_run_errors"] = []
    reset_investigation_state(project)

    initial_nsids = unique_list(project.get("input_nsids", []) + project.get("resolved_nsids", []))
    if should_refresh_lexicon_surface(project, initial_nsids):
        with ThreadPoolExecutor(max_workers=min(ENRICH_IO_MAX_WORKERS, len(initial_nsids))) as executor:
            lexicon_futures = {
                nsid: executor.submit(
                    operation_timer.call, "fetch_lexicon_record", fetch_lexicon_record,
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
            collections = lexicon_types.record_sample_nsids(project)[:6]
            sample_evidence = collector_adapter_core.collect_record_samples(
                str(project.get("project_key") or ""), collections,
                operation_timer.call("fetch_recent_record_samples", fetch_recent_record_samples, collections, timeout=REQUEST_TIMEOUT_LEXICON_HTTP), iso_now(),
            )
            apply_collector_snapshot_evidence(project, sample_evidence)
            run_evidence.extend(sample_evidence)
        except Exception as exc:
            note_failure(project, f"recent record sampling failed: {exc}")
            run_evidence.extend(failed_collector_bundle(
                project, source="ufos_record_samples", operation="sample", target=str(project.get("project_key") or ""), exc=exc,
            ))

    initial_repo_urls = unique_list(project_repository_url_values(project))
    collected_github_repo_urls: set[str] = set()
    collected_tangled_repo_urls: set[str] = set()
    collected_repo_evidence: dict[str, EvidenceBundle] = {}
    trust_inspected_repo_urls: set[str] = set()

    def collect_adopted_repo(repo_url: str, provider: str, failure_context: str) -> None:
        normalized_repo_url = repo_url_core.canonical_repo_url(repo_url)
        try:
            if provider == "github":
                repo_evidence = operation_timer.call("collect_github_repo_evidence", collect_github_repo_evidence, project, repo_url)
                apply_github_repo_evidence(project, repo_evidence)
            elif provider == "tangled":
                repo_evidence = operation_timer.call("collect_tangled_repo_evidence", collect_tangled_repo_evidence, project, repo_url)
                apply_tangled_evidence(project, repo_evidence)
            else:
                repo_evidence = operation_timer.call("collect_codeberg_repo_evidence", collect_codeberg_repo_evidence, project, normalized_repo_url)
            run_evidence.extend(repo_evidence)
            collected_repo_evidence[normalized_repo_url] = repo_evidence
        except Exception as exc:
            record_repository_collection_failure(project, repo_url)
            note_failure(project, f"{failure_context} failed for {repo_url}: {exc}")
            run_evidence.extend(failed_collector_bundle(
                project, source=f"{provider}_repo", operation="inspect", target=repo_url, exc=exc,
            ))

    for repo_url in initial_repo_urls:
        if repo_url_core.extract_github_slug(repo_url) is not None:
            normalized_repo_url = repo_url_core.canonical_repo_url(repo_url)
            if normalized_repo_url:
                collected_github_repo_urls.add(normalized_repo_url)
            collect_adopted_repo(repo_url, "github", "github enrichment")
        elif normalized_tangled_repo_url := repo_url_core.normalize_tangled_repo_url(repo_url):
            collected_tangled_repo_urls.add(normalized_tangled_repo_url)
            collect_adopted_repo(repo_url, "tangled", "tangled repo enrichment")
        elif repo_url_core.infer_repo_provider(repo_url) == "codeberg.org":
            collect_adopted_repo(repo_url, "codeberg", "codeberg repo enrichment")

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
    homepage_probe_urls = set(repository_homepage_probe_urls(project))
    homepage_results: dict[str, dict[str, Any]] = {}
    initial_page_urls = (
        ([project.get("website_url")] if project.get("website_url") else [])
        + project.get("service_urls", [])
        + service_url_candidate_values(project)
        + list(authority_probe_urls)
        + sorted(homepage_probe_urls)
    )
    initial_service_urls = unique_list(
        identifier_core.normalize_url(url) for url in initial_page_urls
        if identifier_core.normalize_url(url)
    )
    probe_results: dict[str, dict[str, Any]] = {}
    if initial_service_urls:
        with ThreadPoolExecutor(max_workers=min(ENRICH_IO_MAX_WORKERS, len(initial_service_urls))) as executor:
            service_futures = {
                service_url: executor.submit(
                    operation_timer.call, "probe_service_url", probe_service_url,
                    service_url,
                    timeout=service_probe_timeout_for_project(project, service_url),
                )
                for service_url in initial_service_urls
            }
            probe_errors: dict[str, Exception] = {}
            for service_url in initial_service_urls:
                try:
                    probe_results[service_url] = service_futures[service_url].result()
                except Exception as exc:
                    probe_errors[service_url] = exc
            selected_authority_probe_urls = select_authority_probe_urls(authority_probe_chains, probe_results)
            for service_url in initial_service_urls:
                normalized_service_url = identifier_core.normalize_url(service_url)
                is_authority_probe = normalized_service_url in authority_probe_urls
                lane = "authority" if is_authority_probe else "static"
                if (
                    not is_authority_probe and normalized_service_url in homepage_probe_urls
                    and exact_project_page_component_role(project, normalized_service_url) != "service"
                ):
                    lane = "repository_homepage"
                try:
                    if service_url in probe_errors:
                        raise probe_errors[service_url]
                    probe_result = probe_results[service_url]
                    if is_authority_probe and normalized_service_url not in selected_authority_probe_urls:
                        continue
                    service_evidence = operation_timer.call("collect_service_evidence_with_followups", collect_service_evidence_with_followups, project, service_url, probe_result, lane=lane)
                    apply_service_evidence(project, service_evidence)
                    if lane == "authority":
                        apply_authority_service_probe_result(project, service_url, probe_result)
                    run_evidence.extend(service_evidence)
                    if lane == "repository_homepage":
                        homepage_results[service_url] = probe_result
                        continue
                    for metadata_url in relation_core.oauth_metadata_candidates(
                        service_url,
                        probe_result.get("links", []),
                        str(probe_result.get("html") or ""),
                    ):
                        try:
                            oauth_evidence = operation_timer.call("collect_oauth_metadata_evidence", collect_oauth_metadata_evidence, project, metadata_url)
                            apply_investigation_evidence(project, oauth_evidence)
                            run_evidence.extend(oauth_evidence)
                        except Exception as exc:
                            note_failure(project, f"OAuth client metadata failed for {metadata_url}: {exc}")
                            run_evidence.extend(failed_collector_bundle(
                                project, source="oauth_client_metadata", operation="fetch", target=metadata_url, exc=exc,
                            ))
                except Exception as exc:
                    normalized_service_url = identifier_core.normalize_url(service_url)
                    if normalized_service_url and lane not in {"authority", "repository_homepage"}:
                        project["failed_service_urls"] = add_unique(project.get("failed_service_urls", []), normalized_service_url)
                    note_failure(project, f"service probe failed for {service_url}: {exc}")
                    run_evidence.extend(failed_collector_bundle(
                        project, source=f"service_{lane}", operation="probe", target=service_url, exc=exc,
                    ))

    # Candidate inspection owns a different failure record and must establish
    # trust before full collection; it is not an adopted-repository refresh.
    for repo_url in candidate_repo_trust_probe_urls(project):
        trust_inspected_repo_urls.add(repo_url)
        provider = repo_url_core.infer_repo_provider(repo_url)
        try:
            if provider == "github":
                trust_evidence = operation_timer.call("collect_github_repo_evidence:trust_inspect", collect_github_repo_evidence, project, repo_url, trust_only=True)
            elif provider == "tangled":
                trust_evidence = operation_timer.call("collect_tangled_repo_evidence:trust_inspect", collect_tangled_repo_evidence, project, repo_url, trust_only=True)
            else:
                continue
            run_evidence.extend(trust_evidence)
            apply_candidate_repo_trust_evidence(project, repo_url, trust_evidence)
            confirmed = confirm_project_repository_url_with_trust(
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
                repo_evidence = operation_timer.call("collect_github_repo_evidence", collect_github_repo_evidence, project, repo_url)
                apply_github_repo_evidence(project, repo_evidence, confirm_repo_snapshot=False)
            else:
                collected_tangled_repo_urls.add(repo_url_core.normalize_tangled_repo_url(repo_url) or repo_url)
                repo_evidence = operation_timer.call("collect_tangled_repo_evidence", collect_tangled_repo_evidence, project, repo_url)
                apply_tangled_evidence(project, repo_evidence)
            run_evidence.extend(repo_evidence)
            collected_repo_evidence[repo_url_core.canonical_repo_url(repo_url)] = repo_evidence
        except Exception as exc:
            if repo_url_core.canonical_repo_url(repo_url) in project_repository_url_values(project):
                record_repository_collection_failure(project, repo_url)
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
        collect_adopted_repo(repo_url, "github", "github secondary enrichment")
    secondary_tangled_repo_urls = [
        repo_url
        for repo_url in unique_list(provider_repo_url_values(project, "tangled"))
        if repo_url_core.normalize_tangled_repo_url(repo_url) not in collected_tangled_repo_urls
    ]
    for repo_url in secondary_tangled_repo_urls:
        collect_adopted_repo(repo_url, "tangled", "tangled secondary enrichment")

    return InitialCollection(
        project, run_input_project, run_started_at, time.perf_counter() - started,
        run_evidence, operation_timer, initial_repo_urls, collected_repo_evidence,
        trust_inspected_repo_urls, initial_service_urls, probe_results, homepage_results,
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
    llm_max_output_tokens: int | None = None,
    llm_triage_prompt_suffix: str | None = None,
    deep_investigation: bool = False,
    initial_collection: InitialCollection | None = None,
    llm_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    initial = initial_collection if initial_collection is not None else collect_initial(project)
    # Exclude time sitting in the one-item prefetch slot from per-project timings.
    run_clock_started = time.perf_counter() - initial.elapsed_seconds
    phase_started = run_clock_started
    phase_timings: dict[str, int] = {}
    llm_requests: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    operation_timer = initial.operation_timer
    project = initial.project
    run_started_at = initial.started_at
    run_input_project = initial.input_project
    run_evidence = initial.evidence
    start_inventory = enrich_observations.candidate_inventory(run_input_project)
    previous_status = str(run_input_project.get("status") or "draft")
    initial_repo_urls = initial.initial_repo_urls
    collected_repo_evidence = initial.collected_repo_evidence
    trust_inspected_repo_urls = initial.trust_inspected_repo_urls
    initial_service_urls = initial.initial_service_urls
    probe_results = initial.probe_results
    homepage_results = initial.homepage_results
    homepage_browser_attempted = False
    run_llm = llm_runner or llm_transport.run_llm_with_metrics

    for repo_url, repo_evidence in collected_repo_evidence.items():
        observed_at = iso_now()
        material = repository_analysis_core.build_material(repo_url, repo_evidence)
        run_evidence.add_record(repository_analysis_core.material_record(material, observed_at))
        cached = repository_analysis_core.cached_result(project, material)
        if cached is not None:
            for docs_url in repository_analysis_core.documentation_urls(cached):
                add_resource_url(
                    project, docs_url, role="docs",
                    source=f"repository_analysis:{material.get('repo_url')}", confidence="candidate",
                )
            run_evidence.add_record(repository_analysis_core.evidence_record(
                material, cached, observed_at, cache_hit=True,
            ))
            run_evidence.add_collector_result(CollectorResult(
                source="repository_analysis", operation="analyze", status="skipped",
                observed_at=observed_at, request_count=0, result_count=1,
            ))
            continue
        if repository_analysis_core.retry_deferred(project, material, observed_at):
            run_evidence.add_collector_result(CollectorResult(
                source="repository_analysis", operation="analyze", status="skipped",
                observed_at=observed_at, request_count=0, result_count=0,
            ))
            continue
        try:
            payload, meta = run_llm(
                repository_analysis_core.build_prompt(project, material),
                purpose="repository_analysis",
                requests=llm_requests,
                prompt_version=repository_analysis_core.POLICY_VERSION,
                llm_endpoint=llm_endpoint,
                llm_model=llm_model,
                llm_api_key=llm_api_key,
                llm_reasoning_effort=llm_reasoning_effort,
                llm_api_style=llm_api_style,
                llm_max_output_tokens=llm_max_output_tokens,
            )
            result = repository_analysis_core.normalize_result(material, payload, meta, observed_at)
            repository_analysis_core.store_result(project, result)
            for docs_url in repository_analysis_core.documentation_urls(result):
                add_resource_url(
                    project, docs_url, role="docs",
                    source=f"repository_analysis:{material.get('repo_url')}", confidence="candidate",
                )
            run_evidence.add_record(repository_analysis_core.evidence_record(
                material, result, observed_at, cache_hit=False,
            ))
            run_evidence.add_collector_result(CollectorResult(
                source="repository_analysis", operation="analyze", status="success",
                observed_at=observed_at, request_count=1, result_count=1,
            ))
        except Exception as exc:
            category = llm_transport.classify_llm_request_error(exc)
            repository_analysis_core.mark_failure(project, material, observed_at, category)
            note_failure(project, f"repository analysis failed for {repo_url}: {category}")
            run_evidence.add_collector_result(CollectorResult(
                source="repository_analysis", operation="analyze", status="failed",
                observed_at=observed_at, request_count=1, result_count=0,
            ))

    apply_publication_candidates(project)
    # New declarations can appear after repository analysis in this same run.
    for url in repository_homepage_probe_urls(project):
        if url in homepage_results or url in project.get("service_urls", []):
            continue
        if url in initial_service_urls:
            if url not in probe_results:
                continue
            result = probe_results[url]
        else:
            try:
                result = operation_timer.call("probe_service_url", probe_service_url, url, timeout=service_probe_timeout_for_project(project, url))
            except Exception as exc:
                run_evidence.extend(failed_collector_bundle(
                    project, source="service_repository_homepage", operation="probe", target=url, exc=exc,
                ))
                continue
        evidence = operation_timer.call("collect_service_evidence_with_followups", collect_service_evidence_with_followups, project, url, result, lane="repository_homepage")
        apply_service_evidence(project, evidence)
        run_evidence.extend(evidence)
        homepage_results[url] = result

    previous_homepage_browser_order = {
        str(value.get("requested_url") or ""): index
        for index, value in enumerate(project.get("service_verification_results", []))
        if value.get("lane") in {"repository_homepage_browser", "repository_homepage_protocol"}
    }
    for url in sorted(homepage_results, key=lambda value: (previous_homepage_browser_order.get(value, -1), value)):
        role_evidence = operation_timer.call("collect_homepage_role_evidence", collect_homepage_role_evidence,
            conn, project, url, homepage_results[url], allow_browser=not homepage_browser_attempted,
        )
        homepage_browser_attempted = homepage_browser_attempted or bool(role_evidence.collector_results)
        apply_service_evidence(project, role_evidence)
        apply_homepage_role_evidence(project, role_evidence)
        run_evidence.extend(role_evidence)

    collected_repo_urls = set(collected_repo_evidence)
    for repo_url in initial_repo_urls:
        normalized_repo_url = repo_url_core.canonical_repo_url(repo_url)
        if not normalized_repo_url or normalized_repo_url in collected_repo_urls:
            continue
        provider = repo_url_core.infer_repo_provider(normalized_repo_url)
        if provider in {"github", "tangled", "codeberg.org"}:
            continue
        observed_at = iso_now()
        material = repository_analysis_core.unavailable_material(normalized_repo_url)
        result = repository_analysis_core.store_unavailable(project, material, observed_at)
        run_evidence.add_record(repository_analysis_core.material_record(material, observed_at))
        run_evidence.add_record(repository_analysis_core.evidence_record(
            material, result, observed_at, cache_hit=False,
        ))
        run_evidence.add_collector_result(CollectorResult(
            source="repository_analysis", operation="analyze", status="skipped",
            observed_at=observed_at, request_count=0, result_count=0,
            stop_reason="unsupported_provider",
        ))

    if deep_investigation:
        try:
            resource_evidence = collector_adapter_core.collect_resource_page_patch(
                project, lambda value: operation_timer.call("enrich_from_resource_pages", enrich_from_resource_pages, value), iso_now()
            )
            apply_collector_snapshot_evidence(project, resource_evidence)
            run_evidence.extend(resource_evidence)
        except Exception as exc:
            note_failure(project, f"resource page enrichment failed: {exc}")
            run_evidence.extend(failed_collector_bundle(
                project, source="resource_page", operation="deep_probe", target=str(project.get("project_key") or ""), exc=exc,
            ))

    for repo_url in relation_core.corroborate_source_repositories(project):
        confirm_project_repository_url_with_trust(
            project, repo_url, "relation_corroboration", relation="mutual_first_party_link",
            reason="repository and first-party application independently link to each other",
            evidence_kind="relation_corroboration", evidence_url=repo_url,
        )

    project = finalize_project_shape(project)
    phase_timings["initial_collection_ms"] = max(0, round((time.perf_counter() - phase_started) * 1000))
    phase_started = time.perf_counter()

    before_search_inventory = enrich_observations.candidate_inventory(project)
    observations["planning"] = {"policy": enrich_observations.planning_policy()}
    investigation_plan = plan_investigation_queries(
        project,
        llm_endpoint=llm_endpoint,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        llm_reasoning_effort=llm_reasoning_effort,
        llm_api_style=llm_api_style,
        llm_max_output_tokens=llm_max_output_tokens,
        observation=observations["planning"],
        llm_runner=lambda prompt, **kwargs: run_llm(
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
    observations["search_execution"] = {}
    investigation_evidence = operation_timer.call("collect_investigation_evidence", collect_investigation_evidence, project, investigation_plan, observation=observations["search_execution"])
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
            tangled_evidence = operation_timer.call("collect_tangled_account_evidence", collect_tangled_account_evidence, project, handle)
            apply_tangled_evidence(project, tangled_evidence)
            run_evidence.extend(tangled_evidence)

    phase_timings["investigation_collection_ms"] = max(
        0, round((time.perf_counter() - phase_started) * 1000)
    )
    phase_started = time.perf_counter()
    triage_reuse_metrics: dict[str, Any] = {"status": "miss", "reason": "not_attempted"}
    try:
        prompt, evidence = build_triage_prompt(project)
        observations["triage_changes"] = enrich_observations.triage_changes(project, evidence)
        prompt, prompt_suffix_hash = append_triage_prompt_suffix(prompt, llm_triage_prompt_suffix)
        options = {
            "llm_endpoint": llm_endpoint,
            "llm_model": llm_model,
            "llm_api_key": llm_api_key,
            "llm_reasoning_effort": llm_reasoning_effort,
            "llm_api_style": llm_api_style,
            "llm_max_output_tokens": llm_max_output_tokens,
        }
        reason = triage_reuse.eligibility_reason(**options)
        collection_state = {
            "failures": [failure.to_dict() for failure in run_evidence.failures],
            "errors": list(project.get("_run_errors", [])),
        }
        components = enrich_observations.triage_components(prompt, evidence, options, collection_state)
        previous_components = ((project.get("llm_source") or {}).get(triage_reuse.CACHE_KEY) or {}).get("observation_components")
        observations["triage_changes"]["previous_components_available"] = isinstance(previous_components, dict)
        if isinstance(previous_components, dict):
            observations["triage_changes"]["changed_components"] = [
                key for key in sorted(components.keys() | previous_components.keys())
                if components.get(key) != previous_components.get(key)]
        fingerprint = triage_reuse.request_fingerprint(
            prompt, evidence, **options, collection_state=collection_state,
        ) if not reason else ""
        cached = None
        if not reason:
            cached, reason = triage_reuse.lookup(project, fingerprint, iso_now())
        triage_reuse_metrics.update(status="hit" if cached else "miss", reason=reason)
        # A failed new attempt must not leave an older successful cache usable.
        triage_reuse.invalidate(project)
        if cached:
            payload, meta = cached
        else:
            payload, meta = run_llm(
                prompt, purpose="triage", requests=llm_requests,
                prompt_suffix_hash=prompt_suffix_hash, **options,
            )
        if prompt_suffix_hash:
            meta["prompt_suffix_hash"] = prompt_suffix_hash
        apply_llm_output(project, payload, meta, evidence)
        if cached:
            # Keep the original generation input as provenance. The normal
            # application above still consumes today's equivalent observation.
            project["llm_source"]["evidence"] = copy.deepcopy(meta["evidence"])
        triage_reuse.invalidate(project)
        if fingerprint:
            triage_reuse.remember(project, fingerprint)
            if triage_reuse.CACHE_KEY in project["llm_source"]:
                project["llm_source"][triage_reuse.CACHE_KEY]["observation_components"] = components
        triage_reuse_metrics["generated_at"] = str(meta.get("generated_at") or "")
    except Exception as exc:
        triage_reuse.invalidate(project)
        triage_reuse_metrics.update(status="miss", reason="triage_failed")
        note_failure(project, f"LLM review failed: {exc}")

    for repo_url in candidate_repo_trust_probe_urls(project):
        if repo_url in trust_inspected_repo_urls or repo_url in project_repository_url_values(project):
            continue
        trust_inspected_repo_urls.add(repo_url)
        provider = repo_url_core.infer_repo_provider(repo_url)
        try:
            if provider == "github":
                trust_evidence = operation_timer.call("collect_github_repo_evidence:trust_inspect", collect_github_repo_evidence, project, repo_url, trust_only=True)
            elif provider == "tangled":
                trust_evidence = operation_timer.call("collect_tangled_repo_evidence:trust_inspect", collect_tangled_repo_evidence, project, repo_url, trust_only=True)
            else:
                continue
            run_evidence.extend(trust_evidence)
            apply_candidate_repo_trust_evidence(project, repo_url, trust_evidence)
            confirm_project_repository_url_with_trust(
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
    reconcile_trust_supported_url_candidates(project)
    project["duplicate_candidates"] = compute_duplicate_candidates(conn, project)
    project["merge_recommendation"] = compute_merge_recommendation(project)
    project["last_run_errors"] = unique_list(project.pop("_run_errors", []))
    project = apply_promotion_state(project)
    project = record_promotion_transition(project, previous_status)
    phase_timings["evaluation_ms"] = max(0, round((time.perf_counter() - phase_started) * 1000))
    observations["operations"] = operation_timer.rows
    observations["search_outcomes"] = enrich_observations.search_outcomes(
        investigation_evidence, start_inventory, before_search_inventory, project)
    observations["planning"]["request"] = next((dict(item) for item in llm_requests if item.get("purpose") == "planning"), {})
    run_id = investigation_storage.stage_investigation_run(
        project,
        project_key=str(project.get("project_key") or ""),
        started_at=run_started_at,
        completed_at=iso_now(),
        input_project=run_input_project,
        output_project=project,
        bundle=run_evidence,
        observations=enrich_observations.pack(observations),
        run_metrics={
            "total_ms": max(0, round((time.perf_counter() - run_clock_started) * 1000)),
            "phases": phase_timings,
            "llm_requests": llm_requests,
            "triage_reuse": triage_reuse_metrics,
            "initial_collection": {
                "duration_ms": round(initial.elapsed_seconds * 1000),
                "prefetched": initial_collection is not None,
            },
        },
    )
    project[runtime_view.RUNTIME_INVESTIGATION_RUN_ID] = run_id
    return project
