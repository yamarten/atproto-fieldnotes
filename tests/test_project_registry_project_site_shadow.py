from __future__ import annotations

import json
import sqlite3
import unittest
from argparse import Namespace

from helpers import TempRegistryDbTestCase
from project_registry_core import cli, project_site_shadow
from project_registry_core.commands import maintenance


class ProjectSiteShadowUnitTest(unittest.TestCase):
    def test_existing_website_reuses_service_without_creating_duplicate_site(self) -> None:
        result = project_site_shadow.evaluate_existing_website({
            "website_url": "https://example.com/",
            "confirmed_services": {"https://example.com"},
        })
        self.assertEqual(result["disposition"], "REUSE_CONFIRMED_SERVICE")
        self.assertFalse(result["creates_project_site_identifier"])

    def test_documentation_url_can_be_project_site_review_candidate(self) -> None:
        result = project_site_shadow.evaluate_existing_website({
            "website_url": "https://docs.example.org/guide",
            "repository_reference": True,
        })
        self.assertEqual(result["disposition"], "REVIEW_PROJECT_SITE")
        self.assertEqual(result["static_url_role"], "documentation")

    def test_account_profile_is_not_project_site_candidate(self) -> None:
        result = project_site_shadow.evaluate_empty_website_candidate({
            "url": "https://bsky.app/profile/example.com",
        })
        self.assertEqual(result["disposition"], "NOT_PROJECT_SITE_CANDIDATE")

    def test_cli_exposes_project_site_migration_comparison(self) -> None:
        args = cli.build_parser().parse_args([
            "compare-project-site-migration",
            "--db-path", "input.db",
            "--output", "report.json",
            "--example-limit", "7",
        ])
        self.assertEqual(args.db_path, "input.db")
        self.assertEqual(args.output, "report.json")
        self.assertEqual(args.example_limit, 7)


class ProjectSiteShadowDatabaseTest(TempRegistryDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        conn = sqlite3.connect(self.db_path)
        try:
            projects = [
                ("proj:service", "Service", "https://service.example.org"),
                ("proj:site", "Site", "https://docs.site.example.org"),
                ("proj:candidate", "Candidate", "https://candidate.example.org"),
                ("proj:empty", "Empty", ""),
                ("proj:collision-a", "Collision A", "https://shared.example.org"),
                ("proj:collision-b", "Collision B", "https://shared.example.org"),
            ]
            conn.executemany(
                """
                INSERT INTO projects(
                    project_key, name, website_url, service_status, profile_json, state_json,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, 'unknown', '{}', ?, '2026-08-24', '2026-08-24')
                """,
                [
                    (key, name, website, json.dumps({"evidence": {"resource_url_records": []}}))
                    for key, name, website in projects
                ],
            )
            conn.executemany(
                """
                INSERT INTO project_identifiers(
                    project_key, ordinal, kind, value, role, classification, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, '2026-08-24')
                """,
                [
                    ("proj:service", 0, "service_url", "https://service.example.org", "service", "confirmed"),
                    ("proj:candidate", 0, "service_url", "https://candidate.example.org", "service", "candidate"),
                    ("proj:site", 0, "repo_url", "https://github.com/example/site", "source_repository", "confirmed"),
                    ("proj:empty", 0, "repo_url", "https://github.com/example/empty", "source_repository", "confirmed"),
                ],
            )
            conn.executemany(
                """
                INSERT INTO project_relations(
                    project_key, ordinal, subject_type, subject_value, predicate,
                    object_type, object_value, classification, source, locator, method,
                    context_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', 'test', '', 'test', '{}', '2026-08-24')
                """,
                [
                    (
                        "proj:site", 0, "website", "https://docs.site.example.org",
                        "source_repository", "repo", "https://github.com/example/site",
                    ),
                    (
                        "proj:site", 1, "repo", "https://github.com/example/site",
                        "repository_homepage", "website", "https://docs.site.example.org",
                    ),
                    (
                        "proj:empty", 0, "repo", "https://github.com/example/empty",
                        "repository_homepage", "website", "https://empty.example.org",
                    ),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_database_comparison_is_read_only_and_reproducible(self) -> None:
        before = self.db_path.read_bytes()
        first = project_site_shadow.compare_database_path(self.db_path, example_limit=10)
        after = self.db_path.read_bytes()
        second = project_site_shadow.compare_database_path(self.db_path, example_limit=10)
        self.assertEqual(before, after)
        self.assertEqual(first, second)
        self.assertTrue(first["source"]["read_only"])
        self.assertEqual(
            first["existing_website_migration"]["disposition_counts"],
            {
                "REUSE_CONFIRMED_SERVICE": 1,
                "REVIEW_CANDIDATE_SERVICE": 1,
                "REVIEW_CROSS_PROJECT_COLLISION": 2,
                "REVIEW_PROJECT_SITE": 1,
            },
        )
        self.assertEqual(
            first["empty_website_repository_homepage_leads"]["disposition_counts"],
            {"REVIEW_SITE_LEAD": 1},
        )
        self.assertEqual(
            first["existing_website_migration"]["automatic_project_site_creation_count"],
            0,
        )

    def test_output_path_cannot_replace_input_database(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            maintenance.cmd_compare_project_site_migration(Namespace(
                db_path=str(self.db_path),
                output=str(self.db_path),
                example_limit=1,
            ))


if __name__ == "__main__":
    unittest.main()
