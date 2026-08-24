from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from helpers import TempRegistryDbTestCase
from project_registry_core import cli, project_repository_shadow


class ProjectRepositoryShadowUnitTest(unittest.TestCase):
    def test_relation_shapes_are_not_mass_renamed(self) -> None:
        first_party = project_repository_shadow.evaluate_relation_row({
            "subject_type": "website",
            "subject_value": "https://docs.example.org",
            "object_type": "repo",
            "object_value": "https://github.com/example/project/issues/12",
            "classification": "confirmed",
            "method": "first_party_link",
        })
        mention = project_repository_shadow.evaluate_relation_row({
            "subject_type": "repo",
            "subject_value": "https://github.com/example/list",
            "object_type": "repo",
            "object_value": "https://github.com/example/project",
            "classification": "candidate",
            "method": "readme_link",
        })
        ownership = project_repository_shadow.evaluate_relation_row({
            "subject_type": "did",
            "subject_value": "did:plc:example",
            "object_type": "repo",
            "object_value": "https://tangled.org/example.com/project",
            "classification": "confirmed",
            "method": "pds_list_records",
        })
        self.assertEqual(first_party["disposition"], "RECOLLECT_FIRST_PARTY_CONTEXT")
        self.assertEqual(first_party["canonical_repository"], "https://github.com/example/project")
        self.assertEqual(mention["disposition"], "DROP_LEGACY_README_MENTION")
        self.assertEqual(ownership["disposition"], "KEEP_AS_REPOSITORY_OWNERSHIP_OBSERVATION")

    def test_provider_root_is_not_a_repository_endpoint(self) -> None:
        result = project_repository_shadow.evaluate_relation_row({
            "subject_type": "website",
            "subject_value": "https://example.org",
            "object_type": "repo",
            "object_value": "https://github.com/example",
            "classification": "confirmed",
            "method": "first_party_link",
        })
        self.assertEqual(result["disposition"], "DROP_INVALID_REPOSITORY_ENDPOINT")

    def test_cli_exposes_project_repository_migration_comparison(self) -> None:
        args = cli.build_parser().parse_args([
            "compare-project-repository-migration",
            "--db-path", "input.db",
            "--output", "report.json",
            "--example-limit", "7",
        ])
        self.assertEqual(args.db_path, "input.db")
        self.assertEqual(args.output, "report.json")
        self.assertEqual(args.example_limit, 7)


class ProjectRepositoryShadowDatabaseTest(TempRegistryDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO projects(
                    project_key, name, website_url, service_status, profile_json, state_json,
                    first_seen_at, last_seen_at
                ) VALUES ('proj:test', 'Test', '', 'unknown', '{}', '{}', '2026-08-25', '2026-08-25')
                """
            )
            conn.executemany(
                """
                INSERT INTO project_identifiers(
                    project_key, ordinal, kind, value, role, classification, is_primary
                ) VALUES ('proj:test', ?, ?, ?, ?, ?, ?)
                """,
                [
                    (0, "service_url", "https://example.org", "service", "confirmed", 1),
                    (1, "repo_url", "https://github.com/Example/Repo", "source_repository", "candidate", 0),
                    (2, "repo_url", "https://github.com/example/repo", "source_repository", "confirmed", 1),
                ],
            )
            conn.execute(
                """
                INSERT INTO project_identifier_adoptions(
                    adoption_id, project_key, kind, value, role, adoption_kind, source
                ) VALUES ('adopt:test', 'proj:test', 'repo_url', 'https://github.com/example/repo',
                          'source_repository', 'operator', 'web_confirm')
                """
            )
            conn.execute(
                """
                INSERT INTO project_decisions(
                    project_key, ordinal, kind, target_type, target_value, target_role
                ) VALUES ('proj:test', 0, 'confirm', 'repo_url',
                          'https://github.com/example/repo', 'source_repository')
                """
            )
            relations = [
                (0, "website", "https://example.org", "repo", "https://github.com/example/repo", "confirmed", "service_page:test", "first_party_link"),
                (1, "repo", "https://github.com/example/list", "repo", "https://github.com/example/repo", "candidate", "github_readme:test", "readme_link"),
                (2, "did", "did:plc:test", "repo", "https://tangled.org/example.com/repo", "confirmed", "tangled_pds:test", "pds_list_records"),
            ]
            conn.executemany(
                """
                INSERT INTO project_relations(
                    project_key, ordinal, subject_type, subject_value, predicate,
                    object_type, object_value, classification, source, method
                ) VALUES ('proj:test', ?, ?, ?, 'source_repository', ?, ?, ?, ?, ?)
                """,
                relations,
            )
            conn.commit()
        finally:
            conn.close()

    def test_comparison_is_read_only_and_separates_relation_meanings(self) -> None:
        before = self.db_path.read_bytes()
        first = project_repository_shadow.compare_database_path(self.db_path, example_limit=10)
        after = self.db_path.read_bytes()
        second = project_repository_shadow.compare_database_path(self.db_path, example_limit=10)
        self.assertEqual(before, after)
        self.assertEqual(first, second)
        self.assertEqual(first["identifier_role_projection"]["current_row_count"], 2)
        self.assertEqual(first["identifier_role_projection"]["projected_row_count"], 1)
        self.assertEqual(
            first["identifier_role_projection"]["duplicate_examples"][0]["projected_classification"],
            "confirmed",
        )
        self.assertEqual(first["dependent_role_references"]["project_identifier_adoptions"], 1)
        self.assertEqual(first["dependent_role_references"]["project_decisions"], 1)
        self.assertEqual(first["relation_disposition"]["disposition_counts"], {
            "DROP_LEGACY_README_MENTION": 1,
            "KEEP_AS_REPOSITORY_OWNERSHIP_OBSERVATION": 1,
            "RECOLLECT_FIRST_PARTY_CONTEXT": 1,
        })
        self.assertTrue(first["review_gate"]["ready_for_user_review"])


if __name__ == "__main__":
    unittest.main()
