from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT  # noqa: F401 - initializes the repository import path
from project_registry_core import llm_model_evaluation as evaluation
from project_registry_core import llm_transport


def build_prompt(evidence: dict) -> str:
    return "Prompt\n" + json.dumps(evidence, ensure_ascii=False, sort_keys=True)


class LLMCorpusExportTest(unittest.TestCase):
    def test_revised_category_labels_are_all_prompt_adjustment_cases(self) -> None:
        label_set = json.loads(
            (ROOT / "tests/fixtures/llm_evaluation_category_labels_v3.json").read_text()
        )

        self.assertEqual(len(label_set["labels"]), 56)
        self.assertTrue(all(label["used_for_prompt_adjustment"] for label in label_set["labels"]))
        self.assertNotIn(
            "auth_only",
            {label["expected_output"]["category"] for label in label_set["labels"]},
        )

    def test_holdout_labels_are_disjoint_from_prompt_adjustment_cases(self) -> None:
        adjusted = json.loads(
            (ROOT / "tests/fixtures/llm_evaluation_category_labels_v3.json").read_text()
        )
        holdout = json.loads(
            (ROOT / "tests/fixtures/llm_evaluation_category_labels_v3_holdout.json").read_text()
        )
        adjusted_projects = {
            label["case_id"].rsplit(":", 1)[0] for label in adjusted["labels"]
        }
        holdout_projects = {
            label["case_id"].rsplit(":", 1)[0] for label in holdout["labels"]
        }

        self.assertEqual(len(holdout["labels"]), 21)
        self.assertTrue(all(not label["used_for_prompt_adjustment"] for label in holdout["labels"]))
        self.assertFalse(adjusted_projects & holdout_projects)

    def test_export_is_read_only_latest_per_project_and_prioritizes_library(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "survey.db"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE investigation_runs ("
                "run_id TEXT PRIMARY KEY, project_key TEXT, completed_at TEXT, output_project_json TEXT)"
            )
            rows = [
                ("old", "proj:app", "2026-01-01", {"name": "Old", "llm_source": {"evidence": {"name": "old"}}}),
                (
                    "new", "proj:app", "2026-01-02",
                    {
                        "name": "App", "category": "project", "tagline": "保存済みアプリ",
                        "summary": "Qwenが作成した説明。", "features": ["保存済み機能"],
                        "llm_source": {"evidence": {
                            "name": "app",
                            "current_profile": {"summary": "以前の説明"},
                            "lexicon_scope": "shared",
                            "lexicon_scope_evidence": ["llm_output:shared"],
                        }},
                    },
                ),
                (
                    "lib", "proj:lib", "2026-01-03",
                    {
                        "name": "Plugin", "category": "library", "summary": "AT Protocol用plugin",
                        "llm_source": {"evidence": {"name": "plugin"}},
                    },
                ),
            ]
            conn.executemany(
                "INSERT INTO investigation_runs VALUES (?, ?, ?, ?)",
                [(run_id, key, completed, json.dumps(project)) for run_id, key, completed, project in rows],
            )
            conn.commit()
            conn.close()

            corpus = evaluation.export_corpus(
                path, build_prompt=build_prompt, limit=2, library_min=1, category_min=1,
            )

            self.assertEqual(len(corpus["cases"]), 2)
            self.assertEqual(corpus["cases"][0]["source_run_id"], "lib")
            self.assertNotIn("category_summary_conflict", corpus["cases"][0]["selection_tags"])
            app = next(case for case in corpus["cases"] if case["project_key"] == "proj:app")
            self.assertEqual(app["source_run_id"], "new")
            self.assertNotIn("current_profile", app["evidence"])
            self.assertEqual(app["evidence"]["lexicon_scope"], "unknown")
            self.assertEqual(app["evidence"]["lexicon_scope_evidence"], [])
            self.assertEqual(
                app["current_profile"],
                {
                    "tagline": "保存済みアプリ",
                    "tagline_is_provisional": False,
                    "summary": "Qwenが作成した説明。",
                    "features": ["保存済み機能"],
                },
            )
            self.assertTrue(corpus["source"]["read_only"])

    def test_export_preserves_non_llm_lexicon_scope_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "survey.db"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE investigation_runs ("
                "run_id TEXT PRIMARY KEY, project_key TEXT, completed_at TEXT, output_project_json TEXT)"
            )
            project = {
                "name": "Shared schema",
                "category": "lexicon",
                "llm_source": {"evidence": {
                    "name": "Shared schema",
                    "lexicon_scope": "shared",
                    "lexicon_scope_evidence": ["shared_lexicon_positioning"],
                }},
            }
            conn.execute(
                "INSERT INTO investigation_runs VALUES (?, ?, ?, ?)",
                ("run", "proj:lexicon", "2026-01-01", json.dumps(project)),
            )
            conn.commit()
            conn.close()

            corpus = evaluation.export_corpus(
                path, build_prompt=build_prompt, limit=1, library_min=0, category_min=0,
            )

            self.assertEqual(corpus["cases"][0]["evidence"]["lexicon_scope"], "shared")
            self.assertEqual(
                corpus["cases"][0]["evidence"]["lexicon_scope_evidence"],
                ["shared_lexicon_positioning"],
            )

    def test_labels_require_matching_evidence_hash(self) -> None:
        corpus = {"schema_version": 1, "cases": [{"case_id": "case", "evidence_hash": "abc"}]}
        labels = {
            "schema_version": 2,
            "labels": [{
                "case_id": "case", "evidence_hash": "wrong",
                "expected_output": {"category": "project"},
                "used_for_prompt_adjustment": False,
            }],
        }

        with self.assertRaisesRegex(ValueError, "evidence hash mismatch"):
            evaluation.apply_labels(corpus, labels)

    def test_labels_are_applied_without_mutating_corpus(self) -> None:
        corpus = {"schema_version": 1, "cases": [{"case_id": "case", "evidence_hash": "abc"}]}
        labels = {
            "schema_version": 2,
            "labels": [{
                "case_id": "case", "evidence_hash": "abc", "status": "confirmed",
                "expected_output": {"category": "project"},
                "used_for_prompt_adjustment": False,
            }],
        }

        labeled = evaluation.apply_labels(corpus, labels)

        self.assertNotIn("expected_output", corpus["cases"][0])
        self.assertEqual(labeled["cases"][0]["expected_output"], {"category": "project"})
        self.assertFalse(labeled["cases"][0]["used_for_prompt_adjustment"])
        self.assertEqual(labeled["labels"]["label_count"], 1)

    def test_labels_require_prompt_adjustment_disclosure(self) -> None:
        corpus = {"schema_version": 1, "cases": [{"case_id": "case", "evidence_hash": "abc"}]}
        labels = {
            "schema_version": 2,
            "labels": [{
                "case_id": "case", "evidence_hash": "abc",
                "expected_output": {"category": "project"},
            }],
        }

        with self.assertRaisesRegex(ValueError, "used_for_prompt_adjustment must be boolean"):
            evaluation.apply_labels(corpus, labels)


class LLMOutputValidationTest(unittest.TestCase):
    def valid_payload(self) -> dict:
        return {
            "category": "project",
            "atproto_auth_only": False,
            "lexicon_scope": "unknown",
            "status": "candidate",
            "name": "Example",
            "tagline": "AT Protocol plugin",
            "tagline_is_provisional": True,
            "summary": "AT Protocol用plugin",
            "features": [],
            "inspired_by": [],
            "source_code_urls": ["https://example.com/repo"],
            "service_urls": [],
            "service_status": "none",
            "lexicons": [],
            "why_keep": "",
            "why_reject": "",
            "watch_reason": "",
            "technical_interest_level": "none",
            "technical_interest_reasons": [],
            "technical_interest_summary": "",
            "manual_review_recommended": False,
            "manual_review_kind": "other",
            "manual_review_priority": "none",
            "manual_review_reason": "",
            "repository_link_assessments": [],
            "confidence": 0.8,
        }

    def test_validation_detects_library_conflict_and_unsupported_identifier(self) -> None:
        payload = self.valid_payload()
        payload["category"] = "library"
        payload["summary"] = "PDSを保守するCLIです。"

        result = evaluation.validate_output(payload, {"name": "Example"})

        self.assertFalse(result["valid"])
        self.assertFalse(result["category_summary_consistent"])
        self.assertEqual(result["unsupported_identifiers"], ["https://example.com/repo"])

    def test_validation_rejects_removed_auth_only_category(self) -> None:
        payload = self.valid_payload()
        payload["category"] = "auth_only"

        result = evaluation.validate_output(payload, {"source_code_urls": ["https://example.com/repo"]})

        self.assertFalse(result["valid"])
        self.assertIn("invalid_enum:category:auth_only", result["errors"])

    def test_validation_requires_boolean_auth_only_signal(self) -> None:
        payload = self.valid_payload()
        payload["atproto_auth_only"] = "false"

        result = evaluation.validate_output(payload, {"source_code_urls": ["https://example.com/repo"]})

        self.assertFalse(result["valid"])
        self.assertIn("invalid_type:atproto_auth_only:bool", result["errors"])

    def test_validation_rejects_auth_only_signal_for_auth_library(self) -> None:
        payload = self.valid_payload()
        payload["category"] = "library"
        payload["atproto_auth_only"] = True

        result = evaluation.validate_output(payload, {"source_code_urls": ["https://example.com/repo"]})

        self.assertFalse(result["valid"])
        self.assertIn("atproto_auth_only_requires_independent_app_or_service", result["errors"])

    def test_runner_records_failures_without_aborting_other_cases(self) -> None:
        corpus = {
            "schema_version": 1,
            "cases": [
                {
                    "case_id": "ok",
                    "evidence": {"source_code_urls": ["https://example.com/repo"]},
                    "expected_output": {"category": "project"},
                    "label_status": "confirmed",
                    "used_for_prompt_adjustment": False,
                },
                {"case_id": "fail", "evidence": {"name": "Failure"}},
            ],
        }

        def run_llm(prompt: str) -> tuple[dict, dict]:
            if "Failure" in prompt:
                raise TimeoutError("timed out")
            return self.valid_payload(), {"model": "test"}

        checkpoints: list[int] = []
        report = evaluation.run_corpus(
            corpus,
            build_prompt=build_prompt,
            run_llm=run_llm,
            model_spec={
                "model": "test",
                "reasoning_effort_requested": "low",
                "reasoning_observation": "applied",
                "reasoning_observation_note": "runtime log confirmed it",
            },
            progress_callback=lambda results: checkpoints.append(len(results)),
        )

        self.assertEqual(report["summary"]["success_count"], 1)
        self.assertEqual(report["summary"]["failure_count"], 1)
        self.assertEqual(report["summary"]["schema_valid_count"], 1)
        self.assertEqual(report["summary"]["expected_category_match_count"], 1)
        self.assertEqual(report["summary"]["expected_category_accuracy"], 1.0)
        self.assertEqual(report["summary"]["reviewed_not_used_for_prompt_adjustment_case_count"], 1)
        self.assertEqual(report["summary"]["reviewed_not_used_for_prompt_adjustment_match_count"], 1)
        self.assertEqual(report["reasoning_observation"]["runtime_status"], "applied")
        self.assertEqual(report["summary"]["prompt_buckets"]["short"]["requests"], 2)
        self.assertTrue(report["results"][0]["reference_is_gold"] is False)
        self.assertEqual(report["runtime_observation"]["measurement_status"], "not_connected")
        self.assertEqual(report["status"], "complete")
        self.assertEqual(checkpoints, [1, 2])

        checkpoint = evaluation.checkpoint_report(corpus, {"model": "test"}, report["results"][:1])
        self.assertEqual(checkpoint["status"], "running")
        self.assertEqual(len(checkpoint["results"]), 1)

    def test_repeat_comparison_reports_field_and_identifier_differences(self) -> None:
        corpus = {
            "schema_version": 1,
            "cases": [{"case_id": "case", "evidence": {"name": "Example"}}],
        }
        calls = 0

        def run_llm(_prompt: str) -> tuple[dict, dict]:
            nonlocal calls
            calls += 1
            payload = self.valid_payload()
            payload["category"] = "project" if calls == 1 else "infra_tool"
            payload["source_code_urls"] = [] if calls == 1 else ["https://new.example/repo"]
            return payload, {"model": "test"}

        report = evaluation.run_corpus(
            corpus,
            build_prompt=build_prompt,
            run_llm=run_llm,
            model_spec={"model": "test"},
            repeat=2,
        )

        comparison = report["repeat_comparison"]
        self.assertEqual(comparison["compared_case_count"], 1)
        self.assertEqual(comparison["category_difference_case_count"], 1)
        self.assertEqual(comparison["identifier_difference_case_count"], 1)
        self.assertIn("category", comparison["cases"][0]["different_fields"])

    def test_runner_controls_current_profile_condition(self) -> None:
        corpus = {
            "schema_version": 1,
            "cases": [{
                "case_id": "case",
                "evidence": {"name": "Example", "current_profile": {"summary": "stale"}},
                "current_profile": {"summary": "Qwen profile", "tagline": "", "features": []},
            }],
        }
        prompts: list[str] = []

        def run_llm(prompt: str) -> tuple[dict, dict]:
            prompts.append(prompt)
            return self.valid_payload(), {"model": "test"}

        without = evaluation.run_corpus(
            corpus,
            build_prompt=build_prompt,
            run_llm=run_llm,
            model_spec={"model": "test", "current_profile_mode": "without"},
            current_profile_mode="without",
        )
        with_profile = evaluation.run_corpus(
            corpus,
            build_prompt=build_prompt,
            run_llm=run_llm,
            model_spec={"model": "test", "current_profile_mode": "with"},
            current_profile_mode="with",
        )

        self.assertNotIn("current_profile", prompts[0])
        self.assertIn("Qwen profile", prompts[1])
        self.assertNotIn("stale", prompts[1])
        self.assertEqual(without["input_condition"], {"current_profile": "without"})
        self.assertEqual(with_profile["input_condition"], {"current_profile": "with"})

    def test_report_comparison_keeps_non_automatic_checks_explicit(self) -> None:
        def report(model: str, *, unsupported: int = 0) -> dict:
            return {
                "schema_version": 2,
                "status": "complete",
                "corpus_hash": "same",
                "model_spec": {"model": model},
                "reasoning_observation": {"runtime_status": "not_checked"},
                "runtime_observation": {"measurement_status": "not_connected"},
                "repeat_comparison": {"compared_case_count": 0},
                "summary": {
                    "success_count": 1,
                    "schema_valid_count": 1,
                    "unsupported_identifier_result_count": unsupported,
                    "reviewed_not_used_for_prompt_adjustment_case_count": 0,
                    "reviewed_not_used_for_prompt_adjustment_match_count": 0,
                    "reviewed_not_used_for_prompt_adjustment_mismatch_count": 0,
                    "total_duration_ms": 10,
                },
                "results": [{"case_id": "case", "repeat_index": 0}],
            }

        comparison = evaluation.compare_reports(report("baseline"), report("candidate", unsupported=1))

        self.assertFalse(comparison["automatic_checks_pass"])
        self.assertIn("unsupported_identifier_result_count_increased", comparison["automatic_regressions"])
        self.assertIn("reasoning_request_not_checked_in_runtime", comparison["remaining_checks"])
        self.assertIn("copied_database_enrich_comparison_not_reviewed", comparison["remaining_checks"])

    def test_runner_rejects_unknown_case_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown corpus case IDs"):
            evaluation.run_corpus(
                {"schema_version": 1, "cases": [{"case_id": "known", "evidence": {"name": "Known"}}]},
                build_prompt=build_prompt,
                run_llm=lambda _prompt: ({}, {}),
                model_spec={"model": "test"},
                case_ids={"missing"},
            )

    def test_validation_treats_non_object_json_as_schema_failure(self) -> None:
        result = evaluation.validate_output(["parsed", "but", "wrong"], {"name": "Example"})

        self.assertFalse(result["valid"])
        self.assertEqual(result["errors"], ["invalid_type:top_level:object"])

    def test_library_conflict_ignores_app_segment_inside_nsid(self) -> None:
        self.assertFalse(evaluation.category_summary_conflict(
            "library", "NSID app.pkm.settingsは個人用設定データです。",
        ))

    def test_library_conflict_does_not_infer_role_from_plugin_word(self) -> None:
        self.assertFalse(evaluation.category_summary_conflict(
            "library", "AT Protocol用pluginです。",
        ))

    def test_library_conflict_detects_standalone_cli_without_reuse_role(self) -> None:
        self.assertTrue(evaluation.category_summary_conflict(
            "library", "PDSを保守するCLIです。",
        ))

    def test_validation_reports_mixed_script_spacing_as_warning(self) -> None:
        payload = self.valid_payload()
        payload["summary"] = "Grain.social の操作用CLIです。"

        result = evaluation.validate_output(payload, {"source_code_urls": ["https://example.com/repo"]})

        self.assertTrue(result["valid"])
        self.assertEqual(result["style_warnings"], ["mixed_script_spacing:summary"])


class LLMOutputCapTest(unittest.TestCase):
    def test_endpoint_payload_uses_api_specific_output_cap(self) -> None:
        observed: list[dict] = []

        def request(_url: str, payload: dict, **_kwargs) -> tuple[dict, str]:
            observed.append(payload)
            return {"output_text": json.dumps({"category": "project"}), "usage": {"output_tokens": 12}}, ""

        original = llm_transport.request_json_post_raw
        llm_transport.request_json_post_raw = request
        try:
            _payload, meta = llm_transport.run_local_llm_via_endpoint(
                "prompt", "http://localhost:1234", "test", "",
                reasoning_effort="low", api_style="responses", max_output_tokens=321,
            )
        finally:
            llm_transport.request_json_post_raw = original

        self.assertEqual(observed[0]["max_output_tokens"], 321)
        self.assertEqual(observed[0]["reasoning"], {"effort": "low"})
        self.assertEqual(meta["max_output_tokens"], 321)
        self.assertEqual(meta["usage"]["output_tokens"], 12)
        self.assertEqual(meta["reasoning_effort_requested"], "low")
        self.assertEqual(meta["reasoning_effort_applied"], "")
        self.assertEqual(meta["reasoning_effort_application_status"], "not_reported_by_endpoint")


if __name__ == "__main__":
    unittest.main()
