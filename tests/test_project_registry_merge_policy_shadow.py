from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from helpers import ROOT, TempRegistryDbTestCase
from project_registry_core import cli, merge_policy_shadow, project_record, registry


FIXTURE = ROOT / "tests" / "fixtures" / "project_registry" / "merge-policy-cases-v1.json"


class MergePolicyShadowUnitTest(unittest.TestCase):
    def test_case_catalog_passes(self) -> None:
        result = merge_policy_shadow.evaluate_case_catalog(merge_policy_shadow.load_case_catalog(FIXTURE))
        self.assertEqual(result["case_count"], 19)
        self.assertEqual(result["failed_count"], 0, result["cases"])

    def test_exclusive_collision_and_cross_record_relation_recommends_review_not_execution(self) -> None:
        catalog = merge_policy_shadow.load_case_catalog(FIXTURE)
        case = next(item for item in catalog["cases"] if item["case_id"] == "exclusive-service-with-direct-bridge")
        left = merge_policy_shadow._case_project(case["case_id"], "left", case["left"])
        right = merge_policy_shadow._case_project(case["case_id"], "right", case["right"])
        result = merge_policy_shadow.evaluate_pair(left, right)
        self.assertEqual(result["verdict"], "RECOMMEND_MERGE_REVIEW")
        self.assertFalse(result["automatic_execution_allowed"])
        self.assertEqual(len(result["signals"]["direct_component_relations"]), 1)

    def test_collection_fragment_recommendation_preserves_direction_and_requires_review(self) -> None:
        catalog = merge_policy_shadow.load_case_catalog(FIXTURE)
        case = next(item for item in catalog["cases"] if item["case_id"] == "exact-owned-collection-fragment")
        left = merge_policy_shadow._case_project(case["case_id"], "left", case["left"])
        right = merge_policy_shadow._case_project(case["case_id"], "right", case["right"])
        result = merge_policy_shadow.evaluate_pair(left, right)
        self.assertEqual(result["verdict"], "RECOMMEND_MERGE_REVIEW")
        self.assertEqual(result["recommendation_kind"], "fragment_absorption")
        self.assertFalse(result["automatic_execution_allowed"])
        self.assertEqual(result["signals"]["fragment_absorption_candidates"], [{
            "fragment_project_key": f"{case['case_id']}:left",
            "target_project_key": f"{case['case_id']}:right",
            "rule": "exact_owned_collection_subset",
            "fragment_collections": ["com.example.flow"],
            "target_collection_count": 2,
            "exact_collection_overlap": ["com.example.flow"],
            "shared_authority_namespaces": ["com.example"],
            "shared_direct_namespaces": ["com.example"],
            "shared_aliases": [],
            "fragment_name_matches_candidate_service": False,
            "target_name_in_fragment_name": True,
            "target_independent_components": [],
            "composition_relations": [],
        }])

    def test_sibling_collection_projects_without_encompassing_surface_remain_review(self) -> None:
        catalog = merge_policy_shadow.load_case_catalog(FIXTURE)
        case = next(item for item in catalog["cases"] if item["case_id"] == "independent-sibling-collection-projects")
        left = merge_policy_shadow._case_project(case["case_id"], "left", case["left"])
        right = merge_policy_shadow._case_project(case["case_id"], "right", case["right"])
        result = merge_policy_shadow.evaluate_pair(left, right)
        self.assertEqual(result["verdict"], "REVIEW")
        self.assertEqual(result["signals"]["fragment_absorption_candidates"], [])

    def test_candidate_service_host_name_is_not_merge_evidence(self) -> None:
        catalog = merge_policy_shadow.load_case_catalog(FIXTURE)
        case = next(
            item
            for item in catalog["cases"]
            if item["case_id"] == "candidate-service-name-is-not-absorption-evidence"
        )
        left = merge_policy_shadow._case_project(case["case_id"], "left", case["left"])
        right = merge_policy_shadow._case_project(case["case_id"], "right", case["right"])
        result = merge_policy_shadow.evaluate_pair(left, right)
        self.assertEqual(result["verdict"], "REVIEW")
        self.assertEqual(result["signals"]["fragment_absorption_candidates"], [])
        self.assertEqual(merge_policy_shadow._candidate_service_host_tokens(left), {"directoryexample"})

    def test_shared_operational_provenance_does_not_replace_external_identity_review(self) -> None:
        payload = {
            "name": "Duplicate",
            "claims": [{"kind": "repo_url", "value": "https://github.com/example/app", "role": "source_repository"}],
            "adoptions": [{
                "kind": "repo_url",
                "value": "https://github.com/example/app",
                "role": "source_repository",
                "source": "sync",
                "source_ref": "run:1",
                "input_fingerprint": "same",
            }],
        }
        left = merge_policy_shadow._case_project("provenance", "left", payload)
        right = merge_policy_shadow._case_project("provenance", "right", payload)
        result = merge_policy_shadow.evaluate_pair(left, right)
        self.assertEqual(result["verdict"], "REVIEW")
        self.assertEqual(len(result["signals"]["shared_adoption_origins"]), 1)

    def test_retrospective_shape_prefers_exact_component_over_weak_context(self) -> None:
        left = merge_policy_shadow._case_project(
            "shape",
            "left",
            {
                "name": "Same App",
                "website_url": "https://same.example",
                "claims": [
                    {"kind": "service_url", "value": "https://same.example", "role": "service"},
                ],
            },
        )
        right = merge_policy_shadow._case_project(
            "shape",
            "right",
            {
                "name": "Same App",
                "website_url": "https://same.example",
                "claims": [
                    {"kind": "service_url", "value": "https://same.example", "role": "service"},
                    {"kind": "nsid", "value": "com.example.post", "role": "owned_nsid"},
                ],
            },
        )
        features = merge_policy_shadow._retrospective_features(left, right)
        self.assertEqual(features["primary_cohort"], "confirmed_exclusive_component")
        self.assertIn("same_website", features["tags"])
        self.assertTrue(features["containment"]["left_components_contained_by_right"])

    def test_retrospective_shape_uses_recorded_origin_without_treating_it_as_auto_merge(self) -> None:
        left = merge_policy_shadow._case_project(
            "origin",
            "left",
            {
                "seed_type": "repo_url",
                "seed_value": "https://github.com/example/app",
            },
        )
        right = merge_policy_shadow._case_project(
            "origin",
            "right",
            {
                "claims": [
                    {"kind": "repo_url", "value": "https://github.com/example/app", "role": "source_repository"},
                ],
            },
        )
        features = merge_policy_shadow._retrospective_features(left, right)
        self.assertEqual(features["primary_cohort"], "recorded_origin_matches_component")
        self.assertEqual(
            features["origin_matches"]["left_origin_in_right"],
            [["repo_url", "https://github.com/example/app"]],
        )
        self.assertFalse(merge_policy_shadow.evaluate_pair(left, right)["automatic_execution_allowed"])

    def test_separate_decision_is_hard_guard(self) -> None:
        left = merge_policy_shadow._case_project(
            "separate",
            "left",
            {"project_key": "left", "separate_project_keys": ["right"]},
        )
        right = merge_policy_shadow._case_project("separate", "right", {"project_key": "right"})
        result = merge_policy_shadow.evaluate_pair(left, right)
        self.assertEqual(result["verdict"], "KEEP_SEPARATE")

    def test_cli_parser_exposes_read_only_comparison_arguments(self) -> None:
        args = cli.build_parser().parse_args([
            "compare-merge-policy", "--db-path", "input.db", "--output", "report.json",
        ])
        self.assertEqual(args.db_path, "input.db")
        self.assertEqual(args.output, "report.json")


class MergePolicyShadowDatabaseTest(TempRegistryDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        conn = registry.open_db()
        try:
            left = project_record.default_project_record("proj:left", "service_url", "https://same.example")
            left["name"] = "Same App"
            left["service_urls"] = ["https://same.example"]
            left["source_code_urls"] = ["https://github.com/example/app"]
            project_record.save_project(conn, left)

            right = project_record.default_project_record("proj:right", "service_url", "https://same.example")
            right["name"] = "Same App"
            right["service_urls"] = ["https://same.example"]
            right["resolved_nsids"] = ["com.example.post"]
            project_record.save_project(conn, right)

            conn.execute(
                """
                INSERT INTO project_relations(
                    project_key, ordinal, subject_type, subject_value, predicate,
                    object_type, object_value, classification, source, locator,
                    method, context_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, '{}', '')
                """,
                (
                    "proj:left",
                    0,
                    "repo",
                    "https://github.com/example/app",
                    "defines_schema",
                    "collection",
                    "com.example.post",
                    "confirmed",
                    "test",
                    "fixture",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def test_database_comparison_is_read_only_and_reproducible(self) -> None:
        before = self.db_path.read_bytes()
        first = merge_policy_shadow.compare_database_path(
            self.db_path,
            case_catalog_path=FIXTURE,
            example_limit=3,
        )
        after = self.db_path.read_bytes()
        second = merge_policy_shadow.compare_database_path(
            self.db_path,
            case_catalog_path=FIXTURE,
            example_limit=3,
        )
        self.assertEqual(before, after)
        self.assertTrue(first["source"]["read_only"])
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(first["current_and_shadow"], second["current_and_shadow"])
        self.assertEqual(
            first["current_and_shadow"]["shadow_verdict_counts"],
            {"RECOMMEND_MERGE_REVIEW": 1},
            first["current_and_shadow"],
        )

    def test_read_only_connection_rejects_writes(self) -> None:
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE forbidden(value TEXT)")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
