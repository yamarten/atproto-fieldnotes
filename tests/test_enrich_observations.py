from __future__ import annotations

import copy
import json
from unittest import mock

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry
from project_registry_core import enrich_observations as obs
from project_registry_core import investigation
from project_registry_core.transport import llm_transport
from project_registry_core.persistence import storage
from project_registry_core import replay
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord
from project_registry_core.transport.reliability import RetryPolicy
from project_registry_core.application import investigation as investigation_service
from project_registry_core.maintenance import storage_cleanup
from project_registry_core import project_record
import enrich_observation_report as report


class ObservationUnitTest(ProjectRecordTestCase):
    def test_plan_records_selection_input_without_llm_material(self):
        project = self.make_project("proj:plan")
        project.update(name="Example App", website_url="https://example.test/login")
        project["summary"] = "not a search input"
        before = copy.deepcopy(project)
        captured = {}
        with mock.patch.object(llm_transport, "run_local_llm") as llm:
            plan = registry.plan_investigation_queries(project, observation=captured)
        llm.assert_not_called()
        self.assertEqual(project, before)
        self.assertEqual(captured["mode"], "deterministic")
        self.assertEqual(captured["input"]["service_hosts"], ["example.test"])
        self.assertNotIn("not a search input", json.dumps(captured))
        self.assertNotIn("prompt", captured)
        self.assertNotIn("llm_output", captured)
        self.assertEqual(captured["plan"], plan)
        self.assertEqual(captured["search_queries"]["github_search"], ["example.test", '\"Example App\"'])

    def test_report_distinguishes_fixed_inputs_missing_inputs_and_legacy_planning(self):
        def row(number, planning):
            return {"run_id": f"inv:{number}", "project_key": "proj:fixed", "completed_at": str(number),
                    "application_status": "applied", "bundle_json": "{}",
                    "evaluation_json": json.dumps({obs.STORAGE_KEY: obs.pack({
                        "planning": planning, "search_execution": {"github_request_budget": 6}})})}
        first = {"mode": "deterministic", "status": "success", "input": {"name": "a"}, "plan": {}}
        changed = {**first, "input": {"name": "b"}}
        legacy = {"status": "success", "prompt": "b", "plan": {}}
        missing = {"mode": "deterministic", "status": "success", "plan": {}}
        result = report.summarize([row(1, first), row(2, first), row(3, changed), row(4, legacy), row(5, missing), row(6, missing)], details=True)
        self.assertEqual(result["consecutive_pairs"]["comparable"], 2)
        self.assertTrue(result["pairs"][0]["same_input"])
        self.assertFalse(result["pairs"][1]["same_input"])

    def test_bounded_and_redacted_sections_are_never_complete(self):
        complete = obs.pack({"planning": {"prompt": "ordinary public evidence"}})
        self.assertTrue(complete["planning"]["complete"])
        ordinary_url = "https://example.test/search?q=ordinary#section"
        self.assertEqual(obs.bounded_section(ordinary_url, 1000)["data"], ordinary_url)
        self.assertTrue(obs.bounded_section(ordinary_url, 1000)["complete"])
        altered = obs.pack({"planning": {"prompt": "https://user:password@example.test/a?token=secret Bearer abc123", "api_key": "credential"}})
        self.assertFalse(altered["planning"]["complete"])
        self.assertTrue(altered["planning"]["redacted"])
        self.assertNotIn("credential", json.dumps(altered))
        self.assertNotIn("abc123", json.dumps(altered))
        self.assertNotIn("never-save", json.dumps(obs.bounded_section("https://example.test/#id_token=never-save", 1000)))
        large = obs.pack({"planning": {"prompt": "a" * 100000}})
        self.assertTrue(large["planning"]["omitted"])
        self.assertNotIn("data", large["planning"])

    def test_candidate_discovery_and_adoption_are_separate(self):
        start = {"project_repository_urls": ["https://github.com/demo/known"]}
        before = {**start, "candidate_project_repository_urls": ["https://github.com/demo/initial"]}
        final = {"project_repository_urls": ["https://github.com/demo/known", "https://github.com/demo/new"],
                 "candidate_project_repository_urls": ["https://github.com/demo/initial"]}
        bundle = EvidenceBundle(project_key="proj:candidates", collected_at="2026-09-01")
        for name in ("known", "initial", "new"):
            bundle.add_record(EvidenceRecord(kind="github_repo_search_candidate", source="github_search:Demo",
                classification="candidate", observed_at=bundle.collected_at, value={"url": "https://github.com/demo/" + name}))
        rows = obs.search_outcomes(bundle, obs.candidate_inventory(start), obs.candidate_inventory(before), final)
        self.assertTrue(rows[0]["known_at_start"])
        self.assertTrue(rows[1]["known_before_search"])
        self.assertFalse(rows[1]["known_at_start"])
        self.assertFalse(rows[1]["confirmed_at_end"])
        self.assertFalse(rows[2]["known_before_search"])
        self.assertTrue(rows[2]["confirmed_at_end"])

    def test_search_retry_and_failure_observations_do_not_change_bundle(self):
        def dependencies():
            return investigation.SearchCollectorDependencies(now=lambda: "2026-09-01",
                search_github_repositories=mock.Mock(side_effect=[TimeoutError(), []]),
                github_repo_relevance=lambda *_: 0, repo_candidate_allowed=lambda *_: True,
                search_bsky_actors=lambda *_, **__: [], bsky_actor_relevance=lambda *_: 0,
                search_bsky_posts=lambda *_, **__: [], handle_matches_project=lambda *_: False,
                fetch_bsky_author_feed=lambda *_, **__: [], timeout=1,
                retry_policy=RetryPolicy(max_attempts=2, base_delay=0, max_delay=0))
        plan = {"repo_search_queries": ["demo"]}
        recorded = {}
        project = self.make_project("proj:search")
        a = investigation.collect_investigation_evidence(project, plan, dependencies(), observation=recorded)
        b = investigation.collect_investigation_evidence(project, plan, dependencies())
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual(recorded["operations"][0]["attempts"], 2)
        self.assertEqual(recorded["operations"][0]["status"], "success")
        failure = dependencies()
        failure.search_github_repositories.side_effect = TimeoutError()
        investigation.collect_investigation_evidence(project, plan, failure, observation=recorded)
        self.assertEqual(recorded["operations"][0]["status"], "failed")
        self.assertEqual(recorded["operations"][0]["attempts"], 2)

    def test_endpoint_usage_and_failed_output_capture_no_raw_credentials(self):
        for usage in ({"prompt_tokens": 13, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 3}},
                      {"input_tokens": 13, "output_tokens": 7, "input_tokens_details": {"cached_tokens": 3}}):
            response = {"choices": [{"message": {"content": "{\"ok\": true}"}}], "usage": usage}
            with mock.patch.object(llm_transport, "request_json_post_raw", return_value=(response, {})):
                requests = []
                llm_transport.run_llm_with_metrics("prompt", purpose="planning", requests=requests,
                    llm_endpoint="https://example.test/v1", llm_model="model", llm_api_style="chat", llm_api_key="never-save")
                self.assertEqual(requests[0]["usage"], {"input_tokens": 13, "output_tokens": 7, "cached_input_tokens": 3})
                self.assertNotIn("never-save", json.dumps(requests))
                self.assertEqual(requests[0]["http_attempts"], 1)
                response["choices"][0]["message"]["content"] = "{broken"
                with self.assertRaises(llm_transport.LLMResponseError):
                    llm_transport.run_llm_with_metrics("prompt", purpose="triage", requests=requests,
                        llm_endpoint="https://example.test/v1", llm_model="model", llm_api_style="chat")
                self.assertEqual(requests[-1]["usage"]["input_tokens"], 13)
                self.assertEqual(requests[-1]["usage_status"], "endpoint_reported")

    def test_report_counts_missing_and_only_comparable_consecutive_pairs(self):
        def row(number, observations):
            return {"run_id": f"inv:{number}", "project_key": "proj:report", "completed_at": f"2026-09-0{number}",
                "application_status": "applied", "bundle_json": "{}",
                "evaluation_json": json.dumps({obs.STORAGE_KEY: observations, storage.RUN_METRICS_KEY: {
                    "llm_requests": [{"purpose": "planning", "status": "success"}]}})}
        recorded = obs.pack({"planning": {"status": "success", "prompt": "exact", "plan": {"reason": "a"}, "policy": {}},
                             "search_execution": {"github_request_budget": 6}})
        changed = copy.deepcopy(recorded)
        changed["planning"]["data"]["plan"]["reason"] = "b"
        rows = [row(3, changed), row(2, recorded), row(1, {})]
        result = report.summarize(rows, details=True)
        self.assertEqual(result["consecutive_pairs"]["comparable"], 1)
        self.assertEqual(result["consecutive_pairs"]["same_conditions_and_input_changed_plan"], 1)
        self.assertEqual(result["coverage"]["planning_missing"], 1)
        self.assertEqual(result["llm_endpoint_reported"]["planning"]["input_tokens_missing_count"], 3)


class ObservationTest(TempRegistryDbTestCase):
    def test_token_usage_coverage_survives_save_load_and_phase_update(self):
        requests = [{"purpose": "repository_analysis", "status": "success", "duration_ms": 2,
                     "usage_status": "endpoint_reported", "usage": {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 10}}
                    for _ in range(20)]
        requests += [{"purpose": "planning", "status": "failed", "usage_status": "missing"}]
        metrics = storage.normalize_run_metrics({"llm_requests": requests})
        self.assertEqual(storage.normalize_run_metrics(metrics), metrics)
        conn = registry.open_db()
        try:
            project = self.make_project("proj:metrics")
            bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-09-01")
            recorded = obs.pack({"planning": {"prompt": "full input"}})
            run_id = storage.save_investigation_run(conn, project_key=project["project_key"], started_at="2026-09-01", completed_at="2026-09-01",
                input_project=project, output_project=project, bundle=bundle, run_metrics=metrics, observations=recorded)
            storage.update_run_metrics_phase(conn, run_id, phase="project_save_ms", duration_ms=10)
            run = storage.load_investigation_run(conn, run_id)
            summary = run["metrics"]["llm_request_summary"]["repository_analysis"]
            self.assertEqual(summary["count"], 20)
            self.assertEqual(summary["input_tokens"], 2000)
            self.assertEqual(summary["input_tokens_reported_count"], 20)
            self.assertEqual(summary["cached_input_tokens"], 200)
            self.assertEqual(run["metrics"]["llm_request_rows_omitted"], 5)
            self.assertEqual(run["observations"], recorded)
            self.assertNotIn(obs.STORAGE_KEY, run["evaluation"])
            expected = storage.evaluation_snapshot(project)
            self.assertFalse(replay.semantic_diff(expected, run["evaluation"])["changed"])
            before = investigation_service.replay(conn, run_id=run_id)
            exported = investigation_service.export_fixture(conn, run_id=run_id)
            self.assertNotIn("full input", exported)
            conn.commit()
            storage_cleanup.compact_registry_storage(conn, execute=True,
                normalize_resource_url_records=project_record.normalize_resource_url_records)
            after = investigation_service.replay(conn, run_id=run_id)
            self.assertEqual(before["diff"], after["diff"])
            self.assertEqual(storage.load_investigation_run(conn, run_id)["observations"], recorded)
        finally:
            conn.close()
