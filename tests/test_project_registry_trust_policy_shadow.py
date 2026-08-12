from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT, TempRegistryDbTestCase
from project_registry_core import cli, trust_policy_shadow


FIXTURE = ROOT / "tests" / "fixtures" / "project_registry" / "trust-policy-cases-v1.json"


def claim(kind: str, value: str, role: str, classification: str = "candidate") -> dict:
    return {"node": (kind, value), "identifier_kind": kind, "role": role, "classification": classification, "provider": ""}


def edge(subject: tuple[str, str], predicate: str, obj: tuple[str, str], *, origin: str = "fixture") -> dict:
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "classification": "confirmed",
        "source": "test",
        "method": "test",
        "locator": "",
        "origin": origin,
    }


def decision(kind: str, target_type: str, target_value: str, source: str = "web_ui", cleared_at: str = "") -> dict:
    return {
        "kind": kind,
        "target_type": target_type,
        "target_value": target_value,
        "source": source,
        "cleared_at": cleared_at,
    }


class TrustPolicyShadowUnitTest(unittest.TestCase):
    def test_website_is_not_an_identifier_or_relation_endpoint_by_itself(self) -> None:
        self.assertEqual(
            trust_policy_shadow._snapshot_identifiers({"website_url": "https://example.com"}),
            set(),
        )
        relation = {
            "subject_type": "repo",
            "subject_value": "https://github.com/example/app",
            "predicate": "source_repository",
            "object_type": "website",
            "object_value": "https://example.com",
            "classification": "confirmed",
            "source": "test",
            "method": "github_homepage",
            "locator": "",
        }
        self.assertIsNone(trust_policy_shadow._normalize_relation_edge(relation))
        mapped = trust_policy_shadow._normalize_relation_edge(
            relation,
            confirmed_services={"https://example.com"},
        )
        assert mapped is not None
        self.assertEqual(mapped["object"], ("service_url", "https://example.com"))

    def test_case_catalog_passes_and_strategies_agree(self) -> None:
        result = trust_policy_shadow.evaluate_case_catalog(trust_policy_shadow.load_case_catalog(FIXTURE))
        self.assertEqual(result["case_count"], 12)
        self.assertEqual(result["failed_count"], 0, result["cases"])

    def test_dedupe_keeps_both_storage_origins(self) -> None:
        relation = edge(("service_url", "https://app.example"), "service_links_repo", ("repo_url", "https://github.com/e/app"), origin="project_relations")
        legacy = {**relation, "origin": "legacy_trust_graph"}
        deduped = trust_policy_shadow.dedupe_edges([relation, legacy])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["origins"], ["project_relations", "legacy_trust_graph"])

    def test_search_inverse_is_not_a_trust_edge(self) -> None:
        project = {
            "project_key": "inverse",
            "claims": [
                claim("service_url", "https://app.example", "service", "confirmed"),
                claim("repo_url", "https://github.com/e/app", "source_repository"),
            ],
            "decisions": [decision("confirm_identifier", "service_url", "https://app.example")],
            "edges": [edge(("service_url", "https://app.example"), "service_links_repo", ("repo_url", "https://github.com/e/app"))],
        }
        result = trust_policy_shadow.evaluate_project(project, profile="operator")
        repo = next(item for item in result["full_origin"] if item["claim"]["role"] == "source_repository")
        self.assertEqual(repo["verdict"], "INSUFFICIENT")

    def test_source_profiles_do_not_silently_promote_legacy_origin(self) -> None:
        project = {
            "project_key": "profiles",
            "claims": [claim("service_url", "https://app.example", "service", "confirmed")],
            "decisions": [decision("confirm_identifier", "service_url", "https://app.example", "legacy_manual_seed_migration")],
            "edges": [],
        }
        operator = trust_policy_shadow.evaluate_project(project, profile="operator")
        legacy = trust_policy_shadow.evaluate_project(project, profile="operator_plus_legacy")
        self.assertEqual(operator["full_origin"][0]["verdict"], "INSUFFICIENT")
        self.assertEqual(legacy["full_origin"][0]["reason"], "adopted_registration_origin")

    def test_all_current_sources_marks_confirmed_collection_as_implicit_bootstrap(self) -> None:
        project = {
            "project_key": "collection-bootstrap",
            "claims": [claim("collection", "com.example.post", "owned_nsid", "confirmed")],
            "decisions": [],
            "edges": [],
        }
        operator = trust_policy_shadow.evaluate_project(project, profile="operator")
        current = trust_policy_shadow.evaluate_project(project, profile="all_current_sources")
        self.assertEqual(operator["full_origin"][0]["verdict"], "INSUFFICIENT")
        self.assertEqual(current["full_origin"][0]["reason"], "adopted_registration_origin")
        self.assertEqual(current["origins"][0]["source"], "implicit_collection_inventory")

    def test_ambiguous_decision_role_is_reported_not_guessed(self) -> None:
        project = {
            "project_key": "ambiguous",
            "claims": [
                claim("repo_url", "https://github.com/e/app", "source_repository", "confirmed"),
                claim("repo_url", "https://github.com/e/app", "source_repository_record", "confirmed"),
            ],
            "decisions": [decision("confirm_identifier", "repo_url", "https://github.com/e/app")],
            "edges": [],
        }
        result = trust_policy_shadow.evaluate_project(project, profile="operator")
        self.assertEqual(len(result["origin_ambiguities"]), 1)
        self.assertEqual(result["origin_ambiguities"][0]["matching_claim_count"], 2)

    def test_manual_origin_survives_guard_but_auto_claim_conflicts(self) -> None:
        project = {
            "project_key": "guards",
            "claims": [
                claim("service_url", "https://app.example", "service", "confirmed"),
                claim("repo_url", "https://github.com/e/app", "source_repository"),
            ],
            "decisions": [
                decision("confirm_identifier", "service_url", "https://app.example"),
                decision("block_identifier", "service_url", "https://app.example"),
                decision("block_identifier", "repo_url", "https://github.com/e/app"),
            ],
            "edges": [
                edge(("service_url", "https://app.example"), "service_links_repo", ("repo_url", "https://github.com/e/app")),
                edge(("repo_url", "https://github.com/e/app"), "repo_links_service", ("service_url", "https://app.example")),
            ],
        }
        result = trust_policy_shadow.evaluate_project(project, profile="operator")
        verdicts = {item["claim"]["role"]: item["verdict"] for item in result["full_origin"]}
        self.assertEqual(verdicts, {"service": "NOT_EVALUATED", "source_repository": "CONFLICTING"})
        self.assertEqual(result["origin_guard_conflict_count"], 1)

    def test_admission_requires_confirmed_allowed_direct_relation(self) -> None:
        project = {
            "project_key": "admission",
            "claims": [claim("service_url", "https://app.example", "service", "confirmed")],
            "decisions": [decision("confirm_identifier", "service_url", "https://app.example")],
            "edges": [
                edge(("service_url", "https://app.example"), "service_links_repo", ("repo_url", "https://github.com/e/app")),
                edge(("service_url", "https://app.example"), "requests_permission", ("collection", "com.example.post")),
            ],
        }
        result = trust_policy_shadow.evaluate_project(project, profile="operator")
        self.assertEqual(result["admission_projection"], [{
            "node": ["repo_url", "https://github.com/e/app"],
            "role": "source_repository",
            "via": {"subject": ["service_url", "https://app.example"], "predicate": "service_links_repo", "source": "test"},
        }])
        self.assertEqual(result["admission_suppression_counts"], {"predicate_not_admissible:requests_permission": 1})

    def test_former_and_non_production_claims_are_not_origins_or_admission_targets(self) -> None:
        old_service = {
            **claim("service_url", "https://old.example", "service", "confirmed"),
            "standing": "former",
        }
        preview_repo = {
            **claim("repo_url", "https://github.com/e/preview", "source_repository", "confirmed"),
            "standing": "non_production",
        }
        project = {
            "project_key": "identifier-standing",
            "claims": [old_service, preview_repo],
            "decisions": [decision("confirm_identifier", "service_url", "https://old.example")],
            "edges": [
                edge(("service_url", "https://old.example"), "service_links_repo", ("repo_url", "https://github.com/e/preview")),
            ],
        }
        result = trust_policy_shadow.evaluate_project(project, profile="operator")
        self.assertEqual(result["origins"], [])
        self.assertEqual(result["origin_ambiguities"], [])
        self.assertEqual(len(result["origin_exclusions"]), 1)
        self.assertEqual(result["origin_exclusions"][0]["reason"], "identifier_standing_outside_current_policy")
        self.assertEqual(
            {item["claim"]["role"]: item["verdict"] for item in result["full_origin"]},
            {"service": "NOT_EVALUATED", "source_repository": "NOT_EVALUATED"},
        )

        current_project = {
            "project_key": "admission-standing",
            "claims": [
                claim("service_url", "https://app.example", "service", "confirmed"),
                preview_repo,
            ],
            "decisions": [decision("confirm_identifier", "service_url", "https://app.example")],
            "edges": [
                edge(("service_url", "https://app.example"), "service_links_repo", ("repo_url", "https://github.com/e/preview")),
            ],
        }
        current_result = trust_policy_shadow.evaluate_project(current_project, profile="operator")
        self.assertEqual(current_result["admission_projection"], [])
        self.assertEqual(
            current_result["admission_suppression_counts"],
            {"known_identifier_standing:non_production": 1},
        )

    def test_redirect_observation_classification_is_topological_not_temporal(self) -> None:
        classify = trust_policy_shadow.classify_redirect_observation
        self.assertEqual(classify("http://example.com", "https://example.com"), "url_normalization")
        self.assertEqual(classify("https://example.com", "https://www.example.com"), "root_www_alias_candidate")
        self.assertEqual(classify("https://example.com", "https://example.com/login"), "same_host_routing")
        self.assertEqual(classify("https://staging.example.com", "https://example.com"), "same_site_surface_redirect")
        self.assertEqual(
            classify("https://example.com", "https://github.com/example/project"),
            "resource_destination",
        )
        self.assertEqual(
            classify("https://example.com", "https://pub.dev:443/packages/example"),
            "resource_destination",
        )
        self.assertEqual(classify("https://old.example", "https://new.example.net"), "cross_site_redirect")
        self.assertEqual(classify("not-a-url", "https://example.com"), "invalid_url")
        self.assertEqual(
            trust_policy_shadow._redirect_url_identity("https://pub.dev:443/packages/example/"),
            "https://pub.dev/packages/example",
        )

    def test_cli_parser_exposes_read_only_comparison_arguments(self) -> None:
        args = cli.build_parser().parse_args([
            "compare-trust-policy", "--db-path", "input.db", "--output", "report.json",
        ])
        self.assertEqual(args.db_path, "input.db")
        self.assertEqual(args.output, "report.json")


class TrustPolicyShadowDatabaseTest(TempRegistryDbTestCase):
    def test_redirect_summary_separates_resource_and_multiple_target_observations(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO projects(
                    project_key, name, website_url, service_status, profile_json, state_json,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, '{}', '{}', ?, ?)
                """,
                ("proj:redirect", "Redirect", "https://staging.example.com", "live", "2026-08-11", "2026-08-11"),
            )
            conn.execute(
                """
                INSERT INTO project_identifiers(
                    project_key, ordinal, kind, value, role, classification, observed_at
                ) VALUES (?, 0, 'service_url', ?, 'service', 'confirmed', ?)
                """,
                ("proj:redirect", "https://staging.example.com", "2026-08-11"),
            )
            conn.execute(
                """
                INSERT INTO project_relations(
                    project_key, ordinal, subject_type, subject_value, predicate,
                    object_type, object_value, classification, source, locator, method, observed_at
                ) VALUES (?, 0, 'service', ?, 'redirects_to', 'service', ?, 'confirmed', 'test', ?, 'html_canonical', ?)
                """,
                (
                    "proj:redirect",
                    "https://staging.example.com",
                    "https://example.com",
                    "https://staging.example.com",
                    "2026-08-11",
                ),
            )
            conn.executemany(
                """
                INSERT INTO project_relations(
                    project_key, ordinal, subject_type, subject_value, predicate,
                    object_type, object_value, classification, source, locator, method, observed_at
                ) VALUES (?, ?, 'service', ?, 'redirects_to', 'service', ?, 'confirmed', 'test', ?, 'http_redirect', ?)
                """,
                [
                    (
                        "proj:redirect",
                        1,
                        "https://staging.example.com",
                        "https://other.example",
                        "https://staging.example.com",
                        "2026-08-11",
                    ),
                    (
                        "proj:redirect",
                        2,
                        "https://staging.example.com",
                        "https://github.com/example/project",
                        "https://staging.example.com",
                        "2026-08-11",
                    ),
                ],
            )
            conn.commit()
        finally:
            conn.close()
        report = trust_policy_shadow.compare_database_path(
            self.db_path, case_catalog_path=FIXTURE, example_limit=5,
        )
        redirects = report["redirect_observations"]
        self.assertEqual(
            redirects["category_counts"],
            {"cross_site_redirect": 1, "resource_destination": 1, "same_site_surface_redirect": 1},
        )
        self.assertEqual(redirects["review_candidate_count"], 2)
        self.assertEqual(
            redirects["review_reason_counts"],
            {"multiple_observed_targets_from_current_or_former_confirmed_service": 2},
        )
        self.assertEqual(
            {item["subject_target_count"] for item in redirects["review_examples"]},
            {2},
        )
        self.assertIn("not confirmed aliases or moves", redirects["interpretation"])

    def test_database_path_comparison_is_read_only_and_reproducible(self) -> None:
        before = self.db_path.read_bytes()
        first = trust_policy_shadow.compare_database_path(
            self.db_path, case_catalog_path=FIXTURE, example_limit=2,
        )
        after = self.db_path.read_bytes()
        second = trust_policy_shadow.compare_database_path(
            self.db_path, case_catalog_path=FIXTURE, example_limit=2,
        )
        self.assertEqual(before, after)
        self.assertTrue(first["source"]["read_only"])
        self.assertEqual(first["input"], second["input"])
        self.assertEqual(first["profiles"], second["profiles"])
        self.assertEqual(first["case_catalog"], second["case_catalog"])
        self.assertEqual(first["redirect_observations"], second["redirect_observations"])

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
