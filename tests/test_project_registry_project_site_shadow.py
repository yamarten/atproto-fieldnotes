from __future__ import annotations

import json
import sqlite3
import unittest
from argparse import Namespace
from pathlib import Path

from helpers import TempRegistryDbTestCase

from project_registry_core.application import merge as application_merge
from project_registry_core import cli
from project_registry_core.persistence import database
from project_registry_core import project_record
from project_registry_core.maintenance import project_site_materialization, project_site_shadow
from project_registry_core.application import presentation
from project_registry_core.commands import maintenance


class ProjectSiteShadowUnitTest(unittest.TestCase):
    def test_review_manifest_fixes_all_sixteen_operator_decisions(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "project_registry" / "project-site-review-v1.json"
        manifest = project_site_materialization.load_review_manifest(fixture)
        counts: dict[str, int] = {}
        for item in manifest["actions"]:
            counts[item["action"]] = counts.get(item["action"], 0) + 1
        self.assertEqual(len(manifest["actions"]), 16)
        self.assertEqual(counts, {
            "add_project_site": 9,
            "clear_website_keep_noncurrent": 5,
            "confirm_service": 1,
            "keep_service_remove_conflict": 1,
        })

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

    def test_existing_project_site_is_reused(self) -> None:
        result = project_site_shadow.evaluate_existing_website({
            "website_url": "https://docs.example.org",
            "confirmed_project_sites": {"https://docs.example.org"},
        })
        self.assertEqual(result["disposition"], "REUSE_CONFIRMED_PROJECT_SITE")

    def test_merge_preserves_confirmed_and_candidate_project_sites(self) -> None:
        target = project_record.default_project_record("proj:target", "nsid", "app.target.post")
        source = project_record.default_project_record("proj:source", "nsid", "app.source.post")
        target["project_site_urls"] = ["https://target.example"]
        source["project_site_urls"] = ["https://source.example"]
        source["project_site_url_candidates"] = ["https://candidate.example"]
        merged = application_merge.merge_project_records(target, source)
        self.assertEqual(
            merged["project_site_urls"],
            ["https://target.example", "https://source.example"],
        )
        self.assertEqual(merged["project_site_url_candidates"], ["https://candidate.example"])

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
        self.assertIsNone(args.materialized_db)


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
                    ("proj:site", 0, "repo_url", "https://github.com/example/site", "project_repository", "confirmed"),
                    ("proj:empty", 0, "repo_url", "https://github.com/example/empty", "project_repository", "confirmed"),
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
                        "project_repository", "repo", "https://github.com/example/site",
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
        collision = first["existing_website_migration"]["examples"][
            "REVIEW_CROSS_PROJECT_COLLISION"
        ][0]
        self.assertEqual(
            [item["project_key"] for item in collision["conflicting_projects"]],
            ["proj:collision-b"] if collision["project_key"] == "proj:collision-a" else ["proj:collision-a"],
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

    def test_reviewed_decisions_materialize_only_into_new_shadow_database(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE projects SET website_url='https://old.example.org' WHERE project_key='proj:empty'")
            conn.executemany(
                """
                INSERT INTO project_identifiers(
                    project_key, ordinal, kind, value, role, classification, observed_at
                ) VALUES (?, ?, 'service_url', 'https://shared.example.org', 'service', 'confirmed', '2026-08-24')
                """,
                [("proj:collision-a", 0), ("proj:collision-b", 0)],
            )
            conn.execute(
                """
                INSERT INTO project_noncurrent_identifiers(
                    project_key, kind, value, role, standing, reason, recorded_at, origin_project_key
                ) VALUES ('proj:empty', 'service_url', 'https://old.example.org', 'service',
                          'former', 'test', '2026-08-24', 'proj:empty')
                """
            )
            conn.commit()
        finally:
            conn.close()
        before = self.db_path.read_bytes()
        manifest_path = Path(self.tempdir.name) / "review.json"
        manifest_path.write_text(json.dumps({
            "schema_version": 1,
            "reviewed_at": "2026-08-25",
            "actions": [
                {"project_key": "proj:site", "expected_website_url": "https://docs.site.example.org", "action": "add_project_site"},
                {"project_key": "proj:candidate", "expected_website_url": "https://candidate.example.org", "action": "add_project_site"},
                {"project_key": "proj:collision-a", "expected_website_url": "https://shared.example.org", "action": "keep_service_remove_conflict", "conflicting_project_key": "proj:collision-b"},
                {"project_key": "proj:empty", "expected_website_url": "https://old.example.org", "action": "clear_website_keep_noncurrent"},
            ],
        }), encoding="utf-8")
        target = Path(self.tempdir.name) / "shadow.db"

        result = project_site_materialization.materialize_reviewed_copy(
            self.db_path, target, manifest_path
        )

        self.assertEqual(before, self.db_path.read_bytes())
        self.assertTrue(result["review_gate"]["ready"])
        self.assertEqual(result["action_counts"]["candidate_service_removed"], 1)
        self.assertEqual(result["action_counts"]["conflicting_service_removed"], 1)
        shadow = sqlite3.connect(target)
        try:
            self.assertEqual(shadow.execute(
                "SELECT COUNT(*) FROM project_identifiers WHERE kind='site_url' AND role='project_site'"
            ).fetchone()[0], 2)
            self.assertEqual(shadow.execute(
                "SELECT COUNT(*) FROM project_identifiers WHERE project_key='proj:candidate' AND kind='service_url'"
            ).fetchone()[0], 0)
            self.assertEqual(shadow.execute(
                "SELECT COUNT(*) FROM project_identifiers WHERE project_key='proj:collision-b' AND value='https://shared.example.org'"
            ).fetchone()[0], 0)
            self.assertEqual(shadow.execute(
                "SELECT website_url FROM projects WHERE project_key='proj:empty'"
            ).fetchone()[0], "")
            self.assertEqual(shadow.execute(
                "SELECT COUNT(*) FROM project_noncurrent_identifiers WHERE project_key='proj:empty'"
            ).fetchone()[0], 1)
        finally:
            shadow.close()

        runtime = database.open_database(target, create=False)
        try:
            project = project_record.fetch_project(runtime, "proj:site")
            self.assertIsNotNone(project)
            assert project is not None
            self.assertEqual(project["project_site_urls"], ["https://docs.site.example.org"])
            project_record.save_project(runtime, project)
            runtime.commit()
            self.assertEqual(runtime.execute(
                "SELECT COUNT(*) FROM project_identifier_adoptions WHERE project_key='proj:site' "
                "AND kind='site_url' AND role='project_site' AND cleared_at=''"
            ).fetchone()[0], 1)
            detail = presentation.project_detail(runtime, project_key="proj:site")
            site_group = next(item for item in detail["identifiers"] if item["family"] == "site")
            self.assertEqual(site_group["value"], "https://docs.site.example.org")
            self.assertEqual(site_group["role_labels"], ["project site"])
            self.assertEqual(detail["overview"]["project_sites"], ["https://docs.site.example.org"])
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
