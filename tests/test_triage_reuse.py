from __future__ import annotations

import copy
import os
from contextlib import ExitStack
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import enrichment_pipeline, llm_evidence
from project_registry_core.transport import llm_transport
from project_registry_core import project_record
from project_registry_core.persistence import storage
from project_registry_core import triage_reuse, editorial
from project_registry_core.evidence import EvidenceBundle, CollectorFailure, CollectorResult


class TriageReuseTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {
            "ATPROTO_SURVEY_LLM_COMMAND": "", "ATPROTO_SURVEY_TRIAGE_REUSE": "1",
            "ATPROTO_SURVEY_LLM_ENDPOINT": "https://llm.example.test/v1",
            "ATPROTO_SURVEY_LLM_MODEL": "test-model",
        }))
        self.now = "2026-09-01T00:00:00+00:00"
        self.stack.enter_context(mock.patch.object(enrichment_pipeline, "iso_now", side_effect=lambda: self.now))
        self.stack.enter_context(mock.patch.object(enrichment_pipeline, "should_refresh_lexicon_surface", return_value=False))
        self.stack.enter_context(mock.patch.object(enrichment_pipeline, "should_refresh_recent_record_samples", return_value=False))
        self.stack.enter_context(mock.patch.object(enrichment_pipeline, "load_config", return_value={"tangled_repo_discovery": {"enabled": False}}))
        self.bundle = EvidenceBundle(project_key="proj:reuse", collected_at=self.now)
        self.collection = self.stack.enter_context(mock.patch.object(
            enrichment_pipeline, "collect_investigation_evidence", side_effect=lambda *_, **__: self.bundle))
        self.requests = []
        self.project_number = 0
        self.payload = {"category": "name_only", "why_keep": "No implementation evidence yet."}
        self.runner = self.stack.enter_context(mock.patch.object(llm_transport, "run_local_llm", side_effect=self.run_llm))

    def run_llm(self, prompt, **_):
        purpose = "planning" if "planning a focused investigation" in prompt else "triage"
        self.requests.append(purpose)
        payload = {"repo_search_queries": ["Example"]} if purpose == "planning" else copy.deepcopy(self.payload)
        return payload, {"model": "test-model", "generated_at": self.now}

    def run_saved(self, project, **kwargs):
        self.bundle.project_key = project["project_key"]
        conn = registry.open_db()
        try:
            result = enrichment_pipeline.enrich_project(conn, project, **kwargs)
            project_record.save_project(conn, result, enriched=True)
            conn.commit()
            saved = project_record.fetch_project(conn, project["project_key"])
            run = storage.latest_investigation_run(conn, project["project_key"])
            return saved, run
        finally:
            conn.close()

    def make_reusable(self):
        self.project_number += 1
        project = self.make_project(f"proj:reuse{self.project_number}")
        # Stabilize the profile/name feedback via the real prompt and pipeline.
        for _ in range(3):
            project, run = self.run_saved(project)
        self.assertEqual(run["metrics"]["triage_reuse"]["status"], "hit")
        return project

    def test_normal_pipeline_persists_reuse_but_keeps_planning_collection_and_evaluation(self):
        project = self.make_reusable()
        before = len(self.requests)
        generated = project["last_llm_review_at"]
        self.now = "2026-09-08T00:00:00+00:00"
        project, run = self.run_saved(project)
        self.assertEqual(self.requests[before:], ["planning"])
        self.assertEqual(self.collection.call_count, 4)
        self.assertEqual(run["metrics"]["triage_reuse"]["status"], "hit")
        self.assertNotIn("triage", run["metrics"]["llm_request_summary"])
        self.assertEqual(project["last_llm_review_at"], generated)
        self.assertEqual(project["llm_output"], self.payload)
        self.assertTrue(run["evaluation"])
        self.assertTrue(run["observations"]["planning"]["complete"])
        plan = run["observations"]["planning"]["data"]
        self.assertIn("planning a focused investigation", plan["prompt"])
        self.assertEqual(plan["llm_output"]["repo_search_queries"], ["Example"])
        self.assertIn("actor_search_queries", plan["plan"])
        self.assertEqual(run["observations"]["operations"]["data"]["collect_investigation_evidence"]["calls"], 1)
        self.assertEqual(run["observations"]["triage_changes"]["data"]["changed_components"], [])

    def test_changed_material_and_manual_decisions_recompute(self):
        for mutation in (
            lambda p: p.update(name="Renamed"),
            lambda p: p["summary_candidates"].append({"source": "service_meta:https://example.test", "value": "New capability", "status": "candidate"}),
            lambda p: p["blocked_identifiers"].append({"type": "repo_url", "value": "https://github.com/example/app", "note": "Different owner"}),
            lambda p: editorial.record_edit(p, allow_automatic=False, updated_at=self.now),
            lambda p: p.update(category="library"),
        ):
            with self.subTest(mutation=mutation):
                project = self.make_reusable()
                mutation(project)
                before = len(self.requests)
                _, run = self.run_saved(project)
                self.assertEqual(self.requests[before:], ["planning", "triage"])
                self.assertEqual(run["metrics"]["triage_reuse"]["status"], "miss")

    def test_config_prompt_and_age_changes_recompute(self):
        for options in (
            {"llm_model": "other-model"}, {"llm_endpoint": "https://other.example.test/v1"},
            {"llm_reasoning_effort": "high"}, {"llm_api_style": "chat"},
            {"llm_max_output_tokens": 2048}, {"llm_triage_prompt_suffix": "Additional rule"},
        ):
            with self.subTest(options=options):
                project = self.make_reusable()
                _, run = self.run_saved(project, **options)
                self.assertEqual(run["metrics"]["triage_reuse"]["status"], "miss")
        project = self.make_reusable()
        self.now = "2026-10-01T00:00:00+00:00"
        _, run = self.run_saved(project)
        self.assertEqual(run["metrics"]["triage_reuse"]["reason"], "expired")

    def test_failure_removes_entry_and_recovery_makes_a_real_request(self):
        project = self.make_reusable()
        with mock.patch.dict(os.environ, {"ATPROTO_SURVEY_TRIAGE_REUSE": "0"}):
            with mock.patch.object(llm_transport, "run_local_llm", side_effect=TimeoutError("timeout")):
                project, run = self.run_saved(project)
        self.assertNotIn(triage_reuse.CACHE_KEY, project["llm_source"])
        self.assertEqual(run["metrics"]["triage_reuse"]["reason"], "triage_failed")
        _, run = self.run_saved(project)
        self.assertEqual(run["metrics"]["triage_reuse"]["status"], "miss")

    def test_empty_or_incomplete_payload_is_not_cached(self):
        for payload in ({}, {"category": "invalid"}, {"summary": "Only a summary"}):
            project = self.make_reusable()
            self.payload = payload
            with mock.patch.dict(os.environ, {"ATPROTO_SURVEY_TRIAGE_REUSE": "0"}):
                project, _ = self.run_saved(project)
            project, _ = self.run_saved(project)
            self.assertNotIn(triage_reuse.CACHE_KEY, project["llm_source"])
            self.payload = {"category": "name_only", "why_keep": "No implementation evidence yet."}

    def test_collection_failure_transition_repeated_failure_and_recovery(self):
        project = self.make_reusable()
        self.bundle.add_failure(CollectorFailure(source="github", operation="search", target="example", message="timeout", category="timeout", retryable=True))
        self.bundle.add_collector_result(CollectorResult(source="github", operation="search", status="failed", observed_at=self.now))
        project, run = self.run_saved(project)
        self.assertEqual(run["metrics"]["triage_reuse"]["reason"], "input_or_policy_changed")
        self.assertTrue(run["observations"]["triage_changes"]["data"]["previous_components_available"])
        self.assertEqual(run["metrics"]["llm_request_summary"]["triage"]["count"], 1)
        project, run = self.run_saved(project)
        self.assertEqual(run["metrics"]["triage_reuse"]["status"], "hit")
        self.assertTrue(project["last_run_errors"])
        self.assertTrue(run["bundle"].failures)
        self.bundle = EvidenceBundle(project_key=project["project_key"], collected_at=self.now)
        _, run = self.run_saved(project)
        self.assertEqual(run["metrics"]["triage_reuse"]["status"], "miss")

    def test_only_explicit_observation_metadata_is_ignored(self):
        evidence = {
            "description_sources": [{"observed_at": self.now, "id": "source", "content": "New in 2026", "locator": "https://example.test"}],
            "summary_candidates": [{"added_at": self.now, "value": "Text", "source": "service_meta"}],
            "tagline_candidates": [{"added_at": self.now, "value": "Tagline"}],
            "lexicon_resolution_entries": [{"resolved_at": self.now, "status": "resolved"}],
            "recent_record_samples": [{"value": {"createdAt": self.now, "observed_at": self.now}}],
        }
        def key(e, prefix="Prompt\n"):
            return triage_reuse.request_fingerprint(prefix + llm_evidence.evidence_json(e), e)
        expected = key(evidence)
        changed = copy.deepcopy(evidence)
        for field, names in triage_reuse.OBSERVATION_FIELDS.items():
            for name in names:
                changed[field][0][name] = "2026-09-08T00:00:00+00:00"
        self.assertEqual(key(changed), expected)
        self.assertEqual(evidence["description_sources"][0]["observed_at"], self.now)
        for field, name, value in (
            ("description_sources", "content", "A new feature"),
            ("description_sources", "locator", "https://different.test"),
            ("lexicon_resolution_entries", "status", "failed"),
            ("recent_record_samples", "value", {"createdAt": "2026-09-08"}),
        ):
            altered = copy.deepcopy(changed)
            altered[field][0][name] = value
            self.assertNotEqual(key(altered), expected)
        self.assertNotEqual(key(evidence, "Changed rule\n"), expected)
        with mock.patch.object(triage_reuse, "policy_digest", return_value="new-policy"):
            self.assertNotEqual(key(evidence), expected)

    def test_disabled_and_command_transport_never_reuse(self):
        for env, reason in (({"ATPROTO_SURVEY_TRIAGE_REUSE": "0"}, "disabled"),
                            ({"ATPROTO_SURVEY_LLM_COMMAND": "custom-command"}, "command_transport")):
            project = self.make_reusable()
            with mock.patch.dict(os.environ, env):
                project, run = self.run_saved(project)
            self.assertEqual(run["metrics"]["triage_reuse"]["reason"], reason)
            self.assertNotIn(triage_reuse.CACHE_KEY, project["llm_source"])

    def test_cached_and_fresh_output_have_identical_semantic_application(self):
        self.payload = {
            "category": "lexicon", "summary": "共通の投稿形式を定義するLexicon。",
            "tagline": "投稿の共通形式", "features": ["投稿形式の定義"],
            "technical_interest_level": "notable", "technical_interest_reasons": ["shared_lexicon"],
        }
        project = self.make_project("proj:semantic")
        project["resolved_nsids"] = ["app.example.post"]
        project["lexicon_schema_samples"] = [{"nsid": "app.example.post", "description": "A shared post format"}]
        for _ in range(3):
            project, _ = self.run_saved(project)
        cached, cached_run = self.run_saved(project)
        self.assertEqual(cached_run["metrics"]["triage_reuse"]["status"], "hit")
        # Compare against a fresh generation of the same fixed output, using the
        # same pre-triage state. No live LLM or external requests are involved.
        conn = registry.open_db()
        try:
            with mock.patch.dict(os.environ, {"ATPROTO_SURVEY_TRIAGE_REUSE": "0"}):
                fresh = enrichment_pipeline.enrich_project(conn, copy.deepcopy(project))
        finally:
            conn.close()
        for field in ("llm_output", "category", "summary", "tagline", "features", "editorial",
                      "technical_interest_level", "technical_interest_score", "service_status",
                      "project_repository_urls", "promotion_ready", "promotion_blockers"):
            self.assertEqual(cached[field], fresh[field], field)

    def test_reobserved_description_hits_and_new_content_misses_in_pipeline(self):
        project = self.make_reusable()
        # The real evidence builder consumes saved service observations even
        # when there is no service URL selected for a network probe this run.
        project["service_verification_results"] = [{
            "requested_url": "https://example.test", "lane": "static", "supports_project": True,
            "status_code": 200, "html_extract": "A small community website", "observed_at": self.now,
        }]
        project, _ = self.run_saved(project)
        generation_observed_at = project["llm_source"]["evidence"]["description_sources"][0]["observed_at"]
        self.now = "2026-09-08T00:00:00+00:00"
        project["service_verification_results"][0]["observed_at"] = self.now
        project, run = self.run_saved(project)
        self.assertEqual(run["metrics"]["triage_reuse"]["status"], "hit")
        self.assertEqual(project["llm_source"]["evidence"]["description_sources"][0]["observed_at"], generation_observed_at)
        self.assertEqual(project["service_verification_results"][0]["observed_at"], self.now)
        project["service_verification_results"][0]["html_extract"] = "Now supports publishing articles"
        _, run = self.run_saved(project)
        self.assertEqual(run["metrics"]["triage_reuse"]["status"], "miss")
