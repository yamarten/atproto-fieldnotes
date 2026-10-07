from __future__ import annotations

import json
import unittest

from helpers import ROOT
from project_registry_core import cli
from project_registry_core.model_evaluation import relation_assessment


CORPUS_PATH = ROOT / "tests/fixtures/project_registry/relation-assessor-cases-v1.json"


class RelationAssessmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        self.case = self.corpus["cases"][0]

    def test_corpus_is_balanced_and_all_inputs_build_prompts(self) -> None:
        expected = {case["expected_verdict"] for case in self.corpus["cases"]}
        self.assertEqual(expected, relation_assessment.VERDICTS)
        for case in self.corpus["cases"]:
            prompt = relation_assessment.build_prompt(case["input"])
            self.assertIn("Do not decide whether two items broadly belong to the same project", prompt)
            self.assertNotIn("project_key", prompt)
            html_links = [
                item for item in relation_assessment.normalize_input(case["input"])["source_evidence"]
                if item["kind"] == "html_link"
            ]
            self.assertTrue(all(item["target"] == case["input"]["object"]["identifier"] for item in html_links))

    def test_input_rejects_project_context_and_unsupported_direction(self) -> None:
        value = dict(self.case["input"])
        value["project_name"] = "Circular context"
        with self.assertRaisesRegex(ValueError, "forbidden field: project_name"):
            relation_assessment.build_prompt(value)
        value = dict(self.case["input"])
        value["direction"] = "repo_to_component"
        with self.assertRaisesRegex(ValueError, "unsupported relation direction"):
            relation_assessment.build_prompt(value)
        value = json.loads(json.dumps(self.case["input"]))
        value["object"]["standing"] = "historical"
        with self.assertRaisesRegex(ValueError, "outside relation assessment scope"):
            relation_assessment.build_prompt(value)

    def test_supported_requires_supplied_evidence_from_both_endpoints(self) -> None:
        validation = relation_assessment.validate_output(
            {"verdict": "SUPPORTED", "reason": "looks right", "evidence_refs": ["S1"]},
            self.case["input"],
        )
        self.assertFalse(validation["valid"])
        self.assertIn("supported_requires_both_endpoints", validation["errors"])
        validation = relation_assessment.validate_output(
            {"verdict": "SUPPORTED", "reason": "direct references", "evidence_refs": ["S1", "R1"]},
            self.case["input"],
        )
        self.assertTrue(validation["valid"])

    def test_direct_any_mode_accepts_one_supplied_reference(self) -> None:
        validation = relation_assessment.validate_output(
            {"verdict": "SUPPORTED", "reason": "direct statement", "evidence_refs": ["R1"]},
            self.case["input"],
            support_evidence_mode="direct_any",
        )
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["normalized_result"]["support_evidence_mode"], "direct_any")
        prompt = relation_assessment.build_prompt(
            self.case["input"], support_evidence_mode="direct_any",
        )
        self.assertIn("decisive reference may come from either endpoint", prompt)

    def test_output_rejects_unknown_evidence_and_extra_claims(self) -> None:
        validation = relation_assessment.validate_output(
            {
                "verdict": "NOT_SUPPORTED", "reason": "different relation",
                "evidence_refs": ["R99"], "reverse_relation": "website_of",
            },
            self.case["input"],
        )
        self.assertFalse(validation["valid"])
        self.assertIn("extra_fields:reverse_relation", validation["errors"])
        self.assertIn("unsupported_evidence_refs:R99", validation["errors"])

    def test_run_corpus_reports_expected_and_legacy_matches_separately(self) -> None:
        cases = [dict(case, legacy_verdict=case["expected_verdict"]) for case in self.corpus["cases"][:2]]
        selected = {"schema_version": 1, "cases": cases}
        answers = iter([
            {"verdict": cases[0]["expected_verdict"], "reason": "supported", "evidence_refs": ["S1", "R1"]},
            {"verdict": cases[1]["expected_verdict"], "reason": "supported", "evidence_refs": ["S1", "R1"]},
        ])
        report = relation_assessment.run_corpus(
            selected,
            run_llm=lambda _prompt: (next(answers), {"model": "fake"}),
            model_spec={"model": "fake"},
        )
        self.assertEqual(report["summary"]["valid_count"], 2)
        self.assertEqual(report["summary"]["expected_comparable_count"], 2)
        self.assertEqual(report["summary"]["expected_match_count"], 2)
        self.assertEqual(report["summary"]["legacy_comparable_count"], 2)
        self.assertEqual(report["summary"]["legacy_match_count"], 2)

    def test_cli_exposes_shadow_command(self) -> None:
        args = cli.build_parser().parse_args([
            "evaluate-relation-assessment-corpus",
            "--corpus", str(CORPUS_PATH),
            "--output", ".tmp/report.json",
        ])
        self.assertEqual(args.output_cap, 512)
        self.assertEqual(args.support_evidence_mode, "both_endpoints")
        self.assertEqual(args.func.__name__, "cmd_evaluate_relation_assessment_corpus")

    def test_database_case_uses_raw_excerpts_without_project_context_in_input(self) -> None:
        row = {
            "project_key": "proj:test", "project_url": "http://example.test/project",
            "service_url": "https://service.example/", "repo_url": "https://github.com/example/repo",
            "link_text": "GitHub", "aria_label": "", "link_title": "",
            "page_title": "Service", "page_description": "A service", "subject_standing": "confirmed",
            "current_relationship": "same_project",
        }
        state = {"evidence": {"llm_source": {"evidence": {"repository_analysis_results": [{
            "repo_url": "https://github.com/example/repo", "stale": False,
            "summary": "model summary must not be copied",
            "evidence_excerpts": [{"path": "README.md", "excerpt": "Live at service.example"}],
        }]}}}}
        case = relation_assessment._database_case(row, state)
        self.assertEqual(case["legacy_verdict"], "SUPPORTED")
        self.assertNotIn("project_key", case["input"])
        self.assertEqual(case["input"]["target_evidence"][0]["excerpt"], "Live at service.example")
        self.assertEqual(case["input"]["source_evidence"][0]["target"], row["repo_url"])
        self.assertNotIn("model summary", json.dumps(case["input"]))

    def test_cli_exposes_database_corpus_export(self) -> None:
        args = cli.build_parser().parse_args([
            "export-relation-assessment-corpus", "--db-path", "survey.db", "--output", "corpus.json",
        ])
        self.assertEqual(args.func.__name__, "cmd_export_relation_assessment_corpus")

    def test_recollection_export_routes_page_attribution_before_relation_assessment(self) -> None:
        report = {
            "policy": "project-repository-bounded-recollection-v1",
            "content_hash": "report-hash",
            "database_inputs_read_only": True,
            "projection": {"review_claim_count": 3},
            "pages": [{
                "page_url": "https://example.test/",
                "bounded_observations": [{
                    "repository_identity": "https://github.com/example/app",
                    "text": "GitHub", "title": "Repository", "container_text": "Project links",
                    "locator": "https://github.com/example/app",
                }],
                "matches": [
                    {
                        "policy_disposition": "REVIEW_PROJECT_REPOSITORY",
                        "current_disposition": "REVIEW_PROJECT_REPOSITORY",
                        "page_standing": "exact_current_component",
                        "project_key": "proj:test", "project_name": "Example",
                        "project_url": "http://example.test/project", "page_url": "https://example.test/",
                        "component_url": "https://example.test/",
                        "repository_url": "https://github.com/example/app",
                        "repository_identity": "https://github.com/example/app",
                        "repository_classification": "absent", "reasons": ["ambiguous"],
                    },
                    {
                        "policy_disposition": "REVIEW_PROJECT_REPOSITORY",
                        "current_disposition": "REVIEW_PROJECT_REPOSITORY",
                        "page_standing": "outside_current_component",
                    },
                ],
            }],
        }
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            corpus = relation_assessment.export_recollection_corpus(report_path)
        self.assertEqual(corpus["selection"]["relation_assessment_count"], 1)
        self.assertEqual(corpus["selection"]["component_attribution_review_count"], 2)
        self.assertEqual(
            corpus["selection"]["component_attribution_review_counts"],
            {"nonexact_raw_strong": 1, "outside_current_component": 1},
        )
        self.assertEqual(corpus["cases"][0]["input"]["direction"], "component_to_repo")
        self.assertEqual(corpus["cases"][0]["input"]["source_evidence"][0]["target"], "https://github.com/example/app")

    def test_cli_exposes_recollection_corpus_export(self) -> None:
        args = cli.build_parser().parse_args([
            "export-recollection-relation-assessment-corpus",
            "--report", "recollection.json", "--output", "corpus.json",
        ])
        self.assertEqual(
            args.func.__name__, "cmd_export_recollection_relation_assessment_corpus"
        )


if __name__ == "__main__":
    unittest.main()
