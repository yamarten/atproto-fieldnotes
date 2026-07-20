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
                ("new", "proj:app", "2026-01-02", {"name": "App", "category": "project", "llm_source": {"evidence": {"name": "app"}}}),
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
            self.assertIn("category_summary_conflict", corpus["cases"][0]["selection_tags"])
            app = next(case for case in corpus["cases"] if case["project_key"] == "proj:app")
            self.assertEqual(app["source_run_id"], "new")
            self.assertTrue(corpus["source"]["read_only"])

    def test_labels_require_matching_evidence_hash(self) -> None:
        corpus = {"schema_version": 1, "cases": [{"case_id": "case", "evidence_hash": "abc"}]}
        labels = {
            "schema_version": 1,
            "labels": [{
                "case_id": "case", "evidence_hash": "wrong",
                "expected_output": {"category": "project"},
            }],
        }

        with self.assertRaisesRegex(ValueError, "evidence hash mismatch"):
            evaluation.apply_labels(corpus, labels)

    def test_labels_are_applied_without_mutating_corpus(self) -> None:
        corpus = {"schema_version": 1, "cases": [{"case_id": "case", "evidence_hash": "abc"}]}
        labels = {
            "schema_version": 1,
            "labels": [{
                "case_id": "case", "evidence_hash": "abc", "status": "confirmed",
                "expected_output": {"category": "project"},
            }],
        }

        labeled = evaluation.apply_labels(corpus, labels)

        self.assertNotIn("expected_output", corpus["cases"][0])
        self.assertEqual(labeled["cases"][0]["expected_output"], {"category": "project"})
        self.assertEqual(labeled["labels"]["label_count"], 1)


class LLMOutputValidationTest(unittest.TestCase):
    def valid_payload(self) -> dict:
        return {
            "category": "project",
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

        result = evaluation.validate_output(payload, {"name": "Example"})

        self.assertFalse(result["valid"])
        self.assertFalse(result["category_summary_consistent"])
        self.assertEqual(result["unsupported_identifiers"], ["https://example.com/repo"])

    def test_runner_records_failures_without_aborting_other_cases(self) -> None:
        corpus = {
            "schema_version": 1,
            "cases": [
                {
                    "case_id": "ok",
                    "evidence": {"source_code_urls": ["https://example.com/repo"]},
                    "expected_output": {"category": "project"},
                    "label_status": "confirmed",
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
            model_spec={"model": "test"},
            progress_callback=lambda results: checkpoints.append(len(results)),
        )

        self.assertEqual(report["summary"]["success_count"], 1)
        self.assertEqual(report["summary"]["failure_count"], 1)
        self.assertEqual(report["summary"]["schema_valid_count"], 1)
        self.assertEqual(report["summary"]["expected_category_match_count"], 1)
        self.assertEqual(report["summary"]["expected_category_accuracy"], 1.0)
        self.assertEqual(report["summary"]["prompt_buckets"]["short"]["requests"], 2)
        self.assertTrue(report["results"][0]["reference_is_gold"] is False)
        self.assertEqual(report["runtime_observation"]["measurement_status"], "not_connected")
        self.assertEqual(report["status"], "complete")
        self.assertEqual(checkpoints, [1, 2])

        checkpoint = evaluation.checkpoint_report(corpus, {"model": "test"}, report["results"][:1])
        self.assertEqual(checkpoint["status"], "running")
        self.assertEqual(len(checkpoint["results"]), 1)

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
                api_style="responses", max_output_tokens=321,
            )
        finally:
            llm_transport.request_json_post_raw = original

        self.assertEqual(observed[0]["max_output_tokens"], 321)
        self.assertEqual(meta["max_output_tokens"], 321)
        self.assertEqual(meta["usage"]["output_tokens"], 12)


if __name__ == "__main__":
    unittest.main()
