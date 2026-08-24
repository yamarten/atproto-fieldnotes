from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT, TempRegistryDbTestCase
from project_registry_core import cli, collector_relation_shadow


FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "project_registry"
    / "collector-relation-policy-cases-v1.json"
)


class CollectorRelationPolicyUnitTest(unittest.TestCase):
    def test_case_catalog_passes(self) -> None:
        result = collector_relation_shadow.evaluate_case_catalog(
            collector_relation_shadow.load_case_catalog(FIXTURE)
        )
        self.assertEqual(result["case_count"], 22)
        self.assertEqual(result["failed_count"], 0, result["cases"])

    def test_source_word_inside_funding_phrase_is_not_an_explicit_source_link(self) -> None:
        self.assertEqual(
            collector_relation_shadow.classify_service_link_label(
                "GitHub Sponsors Fund open source developers"
            ),
            "non_source",
        )
        self.assertEqual(
            collector_relation_shadow.classify_service_link_label("repo ↗"),
            "explicit_source",
        )

    def test_candidate_relation_purpose_separates_source_from_other_link_uses(self) -> None:
        expectations = {
            "Source code": "explicit_source",
            "Run your own instance": "self_hosting_implementation",
            "Edit this page": "documentation_source",
            "LICENSE-APACHE": "license",
            "Report a bug": "issue_or_community",
            "Release notes": "release_or_download",
            "View our data schemas": "schema_or_specification",
            "GitHub": "provider_only",
            "View on GitHub": "other_ambiguous",
        }
        for label, expected in expectations.items():
            with self.subTest(label=label):
                self.assertEqual(
                    collector_relation_shadow.classify_relation_candidate_purpose(label),
                    expected,
                )

    def test_candidate_relation_promotion_does_not_use_reciprocal_relation(self) -> None:
        base = {
            "subject_has_component_role": True,
            "repo_url": "https://github.com/example/app",
            "link_text": "GitHub",
            "aria_label": "",
            "link_title": "",
        }
        without_reciprocal = collector_relation_shadow.evaluate_relation_candidate_promotion(base)
        with_reciprocal = collector_relation_shadow.evaluate_relation_candidate_promotion({
            **base,
            "reciprocal_direct_relation_supported": True,
        })
        self.assertEqual(without_reciprocal, with_reciprocal)
        self.assertEqual(with_reciprocal["disposition"], "REVIEW_SOURCE_REPOSITORY")

    def test_candidate_relation_promotion_keeps_non_source_observation(self) -> None:
        result = collector_relation_shadow.evaluate_relation_candidate_promotion({
            "subject_has_component_role": True,
            "repo_url": "https://github.com/example/app",
            "link_text": "LICENSE",
        })
        self.assertEqual(result["disposition"], "DO_NOT_PROMOTE")
        self.assertEqual(result["purpose"], "license")

    def test_presentation_channel_exposes_missing_visual_context(self) -> None:
        self.assertEqual(
            collector_relation_shadow.relation_candidate_presentation_channel({
                "link_text": "", "aria_label": "GitHub", "link_title": "",
            }),
            "accessibility_only",
        )
        self.assertEqual(
            collector_relation_shadow.relation_candidate_presentation_channel({}),
            "unlabeled",
        )

    def test_deep_repository_path_keeps_root_and_locator(self) -> None:
        result = collector_relation_shadow.endpoint_status(
            "https://github.com/example/app/tree/main/packages/web"
        )
        self.assertEqual(result["status"], "repository_locator")
        self.assertEqual(result["canonical_value"], "https://github.com/example/app")
        self.assertEqual(
            result["locator"], "https://github.com/example/app/tree/main/packages/web"
        )

    def test_generic_homepage_needs_relationship_assessment_even_when_surface_is_observed(self) -> None:
        observation = {
            "direction": "repo_to_url",
            "object_value": "https://app.example.com",
            "target_surface": "observed",
            "context_reconstructible": True,
        }
        result = collector_relation_shadow.evaluate_observation(observation)
        self.assertEqual(result["verdict"], "NEEDS_ASSESSMENT")
        strict = collector_relation_shadow.evaluate_strict_projection(
            observation, result, reciprocal_deterministic_support=True,
        )
        self.assertEqual(strict["verdict"], "SUPPORTED")

    def test_homepage_axes_do_not_treat_a_direct_profile_reference_as_a_service(self) -> None:
        observation = {
            "direction": "repo_to_url",
            "method": "github_homepage",
            "subject_value": "https://github.com/example/app",
            "object_value": "https://bsky.app/profile/example.com",
            "target_surface": "none",
            "subject_identifiers": [{
                "role": "source_repository",
                "classification": "confirmed",
                "adoptions": [{"source": "legacy_manual_seed_migration"}],
            }],
        }
        result = collector_relation_shadow.evaluate_homepage_candidate_profiles(observation)
        self.assertEqual(
            result["repository_project_attribution"],
            {"verdict": "NEEDS_ASSESSMENT", "evidence": "legacy_bootstrap_only"},
        )
        self.assertEqual(result["repository_url_reference"]["verdict"], "SUPPORTED")
        self.assertEqual(result["target_role"]["role"], "account_profile")
        self.assertEqual(result["candidate_projection"]["current_membership"], "NOT_SUPPORTED")

    def test_current_membership_and_traceable_attribution_are_separate_profiles(self) -> None:
        observation = {
            "direction": "repo_to_url",
            "method": "github_homepage",
            "project_name": "Example App",
            "subject_value": "https://github.com/example/example-app",
            "object_value": "https://example-app.pages.dev",
            "target_surface": "observed",
            "subject_identifiers": [{
                "role": "source_repository",
                "classification": "confirmed",
                "adoptions": [{"source": "legacy_manual_seed_migration"}],
            }],
        }
        legacy = collector_relation_shadow.evaluate_homepage_candidate_profiles(observation)
        self.assertEqual(legacy["target_role"]["role"], "service_surface")
        self.assertEqual(legacy["candidate_projection"]["current_membership"], "SUPPORTED")
        self.assertEqual(legacy["candidate_projection"]["traceable_attribution"], "NEEDS_ASSESSMENT")

        observation["subject_identifiers"][0]["adoptions"] = [{"source": "trust_policy"}]
        traceable = collector_relation_shadow.evaluate_homepage_candidate_profiles(observation)
        self.assertEqual(traceable["repository_project_attribution"]["verdict"], "SUPPORTED")
        self.assertEqual(traceable["candidate_projection"]["traceable_attribution"], "SUPPORTED")

    def test_primary_website_is_not_implicitly_a_service_surface(self) -> None:
        result = collector_relation_shadow.evaluate_homepage_target_role({
            "object_value": "https://example.com",
            "target_surface": "none",
            "target_is_primary_website": True,
        })
        self.assertEqual(result["role"], "primary_website")
        self.assertEqual(result["verdict"], "NEEDS_ASSESSMENT")

    def test_github_search_attachment_requires_a_confirmed_reciprocal_match(self) -> None:
        self.assertEqual(
            collector_relation_shadow.evaluate_component_attachment_projection({
                "best_match_count": 1,
                "best_match_standing": "confirmed",
                "outward_relation_supported": True,
                "return_relation_supported": True,
            })["disposition"],
            "AUTO_ATTACH",
        )
        self.assertEqual(
            collector_relation_shadow.evaluate_component_attachment_projection({
                "best_match_count": 1,
                "best_match_standing": "candidate",
                "outward_relation_supported": True,
                "return_relation_supported": True,
            })["disposition"],
            "REVIEW",
        )

    def test_relation_assessment_requires_bounded_material_from_both_endpoints(self) -> None:
        self.assertEqual(
            collector_relation_shadow.route_relation_assessment({
                "subject_has_component_role": True,
                "deterministic_verdict": "NEEDS_ASSESSMENT",
                "source_observation_available": True,
                "bounded_target_material_available": False,
            })["route"],
            "NEEDS_PROBE",
        )
        self.assertEqual(
            collector_relation_shadow.route_relation_assessment({
                "subject_has_component_role": True,
                "deterministic_verdict": "NEEDS_ASSESSMENT",
                "source_observation_available": True,
                "bounded_target_material_available": True,
            })["route"],
            "ASSESS",
        )

    def test_website_projection_keeps_docs_but_excludes_profiles_and_funding(self) -> None:
        self.assertTrue(collector_relation_shadow.homepage_is_website_candidate({
            "object_value": "https://docs.example.com",
        }))
        self.assertFalse(collector_relation_shadow.homepage_is_website_candidate({
            "object_value": "https://bsky.app/profile/example.com",
        }))
        self.assertFalse(collector_relation_shadow.homepage_is_website_candidate({
            "object_value": "https://patreon.com/example",
        }))

    def test_cli_contract(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args([
            "compare-collector-relation-policy",
            "--db-path", "input.db",
            "--output", "report.json",
            "--example-limit", "7",
        ])
        self.assertEqual(args.func.__name__, "cmd_compare_collector_relation_policy")
        self.assertEqual(args.example_limit, 7)


class CollectorRelationPolicyDatabaseTest(TempRegistryDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        conn = sqlite3.connect(self.db_path)
        try:
            service_state = {
                "evidence": {
                    "service_verification_results": [{
                        "requested_url": "https://app.example.com",
                        "final_url": "https://app.example.com",
                        "title": "Example App",
                        "description": "Example application",
                        "links": [{
                            "url": "https://github.com/example/app/tree/main/packages/web",
                            "text": "repo ↗",
                        }],
                        "content_sha256": "page-material",
                        "service_status_evidence": ["app_shell_detected"],
                    }],
                    "llm_source": {
                        "evidence": {
                            "repository_link_candidates": [{
                                "service_url": "https://app.example.com",
                                "repo_url": "https://github.com/example/app",
                                "link_text": "repo ↗",
                                "aria_label": "",
                                "link_title": "",
                                "page_title": "Example App",
                                "page_description": "Example application",
                            }],
                            "repository_analysis_results": [],
                        }
                    },
                    "llm_output": {
                        "repository_link_assessments": [{
                            "service_url": "https://app.example.com",
                            "repo_url": "https://github.com/example/app",
                            "relationship": "same_project",
                            "reason": "Legacy mixed triage decision",
                        }]
                    },
                }
            }
            conn.execute(
                """
                INSERT INTO projects(
                    project_key, name, website_url, service_status, profile_json, state_json,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, '', 'live', '{}', ?, '2026-08-17', '2026-08-17')
                """,
                ("proj:collector", "Collector Example", json.dumps(service_state)),
            )
            conn.executemany(
                """
                INSERT INTO project_identifiers(
                    project_key, ordinal, kind, value, role, classification, observed_at
                ) VALUES ('proj:collector', ?, ?, ?, ?, ?, '2026-08-17')
                """,
                [
                    (0, "service_url", "https://app.example.com", "service", "confirmed"),
                    (1, "repo_url", "https://github.com/example/app", "source_repository", "candidate"),
                ],
            )
            conn.executemany(
                """
                INSERT INTO project_relations(
                    project_key, ordinal, subject_type, subject_value, predicate,
                    object_type, object_value, classification, source, locator, method,
                    context_json, observed_at
                ) VALUES ('proj:collector', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', '2026-08-17')
                """,
                [
                    (
                        0,
                        "website",
                        "https://app.example.com",
                        "source_repository",
                        "repo",
                        "https://github.com/example/app/tree/main/packages/web",
                        "confirmed",
                        "service_page:https://app.example.com",
                        "https://app.example.com",
                        "first_party_link",
                    ),
                    (
                        1,
                        "repo",
                        "https://github.com/example/app",
                        "repository_homepage",
                        "website",
                        "https://app.example.com",
                        "confirmed",
                        "github_repo_metadata:https://github.com/example/app",
                        "https://github.com/example/app",
                        "github_homepage",
                    ),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_database_comparison_reconstructs_service_context_and_keeps_input_read_only(self) -> None:
        before = self.db_path.read_bytes()
        report = collector_relation_shadow.compare_database_path(
            self.db_path,
            case_catalog_path=FIXTURE,
            example_limit=5,
        )
        after = self.db_path.read_bytes()

        self.assertEqual(before, after)
        self.assertTrue(report["source"]["read_only"])
        self.assertEqual(report["input"]["relation_observation_count"], 2)
        self.assertEqual(report["input"]["source_repository_relation_count"], 1)
        self.assertEqual(
            report["input"]["relation_predicate_counts"],
            {"repository_homepage": 1, "source_repository": 1},
        )
        self.assertEqual(report["deterministic_policy"]["verdict_counts"]["SUPPORTED"], 1)
        self.assertEqual(report["strict_projection"]["verdict_counts"]["SUPPORTED"], 2)
        self.assertEqual(report["candidate_admission"]["current_candidate_claim_count"], 1)
        self.assertEqual(report["candidate_admission"]["strict_supported_claim_count"], 1)
        self.assertEqual(
            report["github_homepage_three_axis"]["confirmed_repository_to_unconfirmed_url_claim_count"],
            0,
        )
        self.assertEqual(
            report["github_homepage_storage_projection"]["current_source_repository_row_count"], 0,
        )
        self.assertEqual(
            report["github_homepage_storage_projection"]["current_repository_homepage_row_count"], 1,
        )
        self.assertEqual(
            report["github_homepage_storage_projection"]["proposed_repository_homepage_claim_count"],
            1,
        )
        self.assertEqual(
            report["github_homepage_storage_projection"]["proposed_relation_classification"],
            "confirmed",
        )
        self.assertEqual(
            report["github_search_homepage_attachment"]["shadow_disposition_counts"],
            {"AUTO_ATTACH": 1},
        )
        self.assertFalse(
            report["github_search_homepage_attachment"]["historical_match_kind_reconstructible"]
        )
        self.assertEqual(report["relation_assessor_inventory"]["current_proposal_count"], 1)
        self.assertEqual(
            report["relation_assessor_inventory"]["route_counts"],
            {"POLICY_DECIDED": 1},
        )
        promotion = report["relation_assessor_inventory"]["candidate_relation_promotion"]
        self.assertEqual(
            promotion["disposition_counts"],
            {"CONFIRM_SOURCE_REPOSITORY": 1},
        )
        self.assertFalse(promotion["reciprocal_relation_is_promotion_input"])
        self.assertEqual(
            report["examples"]["deterministic_supported"][0]["link_label"],
            "repo ↗",
        )

    def test_output_path_cannot_replace_input_database(self) -> None:
        from argparse import Namespace
        from project_registry_core.commands import maintenance

        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            maintenance.cmd_compare_collector_relation_policy(Namespace(
                db_path=str(self.db_path),
                output=str(self.db_path),
                example_limit=1,
            ))


if __name__ == "__main__":
    unittest.main()
