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
    def test_case_catalog_passes_and_strategies_agree(self) -> None:
        result = trust_policy_shadow.evaluate_case_catalog(trust_policy_shadow.load_case_catalog(FIXTURE))
        self.assertEqual(result["case_count"], 10)
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

    def test_cli_parser_exposes_read_only_comparison_arguments(self) -> None:
        args = cli.build_parser().parse_args([
            "compare-trust-policy", "--db-path", "input.db", "--output", "report.json",
        ])
        self.assertEqual(args.db_path, "input.db")
        self.assertEqual(args.output, "report.json")


class TrustPolicyShadowDatabaseTest(TempRegistryDbTestCase):
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
