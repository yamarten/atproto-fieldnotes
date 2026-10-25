"""Deterministic responses for overlap contract tests and elapsed-time experiments.

Real pipeline, application, storage and transport metrics; no external requests.
Only the response-producing collectors and LLM backend are replaced.
"""
from __future__ import annotations

import os
import threading
import time
from collections import Counter
from contextlib import ExitStack, contextmanager
from unittest import mock

from project_registry_core import enrichment_pipeline as pipeline
from project_registry_core.transport import http_transport, llm_transport
from project_registry_core import project_record, registry
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord, CollectorResult

NOW = "2026-09-26T00:00:00+00:00"
KEYS = [f"proj:{i:012d}" for i in range(1, 9)]


def seed(conn, count=3):
    for i, key in enumerate(KEYS[:count], 1):
        project = project_record.default_project_record(key, "service_url", f"https://overlap-{i}.example.com")
        project["name"] = f"Overlap {i}"
        registry.confirm_service_seed(project, f"https://overlap-{i}.example.com", source="overlap_fixture")
        project_record.save_project(conn, project)
    conn.commit()


class Responses:
    def __init__(self, *, collection_delay=0.0, llm_delay=0.0, payload_bytes=0):
        self.collection_delay = collection_delay
        self.llm_delay = llm_delay
        self.payload_bytes = payload_bytes
        self.calls = Counter()
        self.timeline = []
        self.lock = threading.Lock()
        self.on_probe = None
        self.on_llm = None
        self.on_search = None

    @contextmanager
    def operation(self, kind, target):
        started = time.perf_counter()
        with self.lock:
            self.calls[kind] += 1
        try:
            yield
        finally:
            with self.lock:
                self.timeline.append((kind, target, started, time.perf_counter(), threading.get_ident()))

    def probe(self, url, **kwargs):
        with self.operation("probe", url):
            if self.on_probe:
                self.on_probe(url)
            time.sleep(self.collection_delay)
            return {"url": url, "requested_url": url, "final_url": url, "status_code": 200,
                    "title": "Overlap app", "description": "An AT Protocol demonstration app.",
                    "html": "<p>AT Protocol demonstration</p>" + "x" * self.payload_bytes,
                    "links": [], "urls": [], "headers": {}, "cache_hit": False,
                    "service_status": "live", "service_status_confidence": "confirmed", "service_status_evidence": ["http_200"]}

    def search(self, project, plan, *, observation=None):
        with self.operation("search", project["project_key"]):
            if self.on_search:
                self.on_search(project)
            bundle = EvidenceBundle(project_key=project["project_key"], collected_at=NOW)
            bundle.add_record(EvidenceRecord(
                kind="github_repo_search_candidate", source="github_search:Overlap", classification="candidate",
                observed_at=NOW, value={"url": "https://github.com/demo/overlap-candidate", "description": "AT Protocol demo"}))
            bundle.add_collector_result(CollectorResult(source="github_search", operation="search", status="success",
                                                       observed_at=NOW, request_count=1, result_count=1))
            if observation is not None:
                observation.update(github_request_budget=6, bluesky_request_budget=12, operations=[])
            return bundle

    def llm(self, prompt, **kwargs):
        purpose = ("repository_analysis" if "Analyze a confirmed project repository" in prompt else
                   "triage")
        with self.operation(purpose, prompt):
            if self.on_llm:
                self.on_llm(purpose)
            time.sleep(self.llm_delay)
            payload = {
                "category": "name_only", "why_keep": "No implementation evidence yet."}
            if purpose == "repository_analysis":
                payload = {"summary": "An AT Protocol demo.", "summary_evidence": ["E01"],
                           "observations": [], "capabilities": [], "collections": []}
            return payload, {"model": "test-model", "generated_at": NOW, "usage": {"input_tokens": 100, "output_tokens": 20}}

    def reset(self):
        self.calls.clear()
        self.timeline.clear()

    def summary(self):
        intervals = list(self.timeline)
        probes = [row for row in intervals if row[0] == "probe"]
        llms = [row for row in intervals if row[0] in {"triage", "repository_analysis"}]
        def peak(rows):
            events = sorted([(row[2], 1) for row in rows] + [(row[3], -1) for row in rows])
            active = result = 0
            for _, delta in events:
                active += delta
                result = max(result, active)
            return result
        return {"calls": dict(self.calls), "max_llm_active": peak(llms),
                "max_probe_active": peak(probes),
                "probe_llm_overlap": any(a[2] < b[3] and b[2] < a[3] for a in probes for b in llms)}

    def patches(self):
        stack = ExitStack()
        stack.enter_context(mock.patch.dict(os.environ, {
            "ATPROTO_SURVEY_LLM_COMMAND": "", "ATPROTO_SURVEY_TRIAGE_REUSE": "1",
            "ATPROTO_SURVEY_LLM_ENDPOINT": "https://llm.example.test/v1", "ATPROTO_SURVEY_LLM_MODEL": "test-model",
            "ATPROTO_SURVEY_LLM_TRIAGE_PROMPT_SUFFIX_FILE": "",
        }))
        for module in (pipeline, project_record):
            stack.enter_context(mock.patch.object(module, "iso_now", return_value=NOW))
        stack.enter_context(mock.patch.object(pipeline, "should_refresh_lexicon_surface", return_value=False))
        stack.enter_context(mock.patch.object(pipeline, "should_refresh_recent_record_samples", return_value=False))
        stack.enter_context(mock.patch.object(pipeline, "load_config", return_value={"tangled_repo_discovery": {"enabled": False}}))
        stack.enter_context(mock.patch.object(pipeline, "probe_service_url", side_effect=self.probe))
        stack.enter_context(mock.patch.object(pipeline, "collect_investigation_evidence", side_effect=self.search))
        stack.enter_context(mock.patch.object(llm_transport, "run_local_llm", side_effect=self.llm))
        stack.enter_context(mock.patch.object(http_transport, "request_bytes", side_effect=AssertionError("unexpected external request")))
        def metadata_response(url, **kwargs):
            from project_registry_core.oauth_metadata import CONVENTIONAL_PATHS
            if any(url.endswith(path) for path in CONVENTIONAL_PATHS):
                return 404, b"", {}, url
            raise AssertionError("unexpected external request: " + url)
        stack.enter_context(mock.patch.object(http_transport, "request_bytes_with_final_url", side_effect=metadata_response))
        return stack


def semantic(value):
    """Remove only run IDs and elapsed measurements."""
    ignored = {"run_id", "investigation_run_id", "_runtime_pending_investigation_run", "_runtime_investigation_run_id",
               "_runtime_project_revision", "_runtime_persistence_baseline", "_run_metrics", "metrics",
               "duration_ms", "initial_collection_ms", "total_ms"}
    if isinstance(value, dict):
        return {key: semantic(item) for key, item in value.items() if key not in ignored and not key.startswith("_runtime_")}
    if isinstance(value, list):
        return [semantic(item) for item in value]
    return value
