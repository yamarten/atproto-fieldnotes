from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from helpers import TempRegistryDbTestCase
from project_registry_core import cli, project_record
from project_registry_core.maintenance import project_repository_materialization, project_repository_review_materialization, project_repository_shadow


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
        self.assertIsNone(args.materialized_db)

    def test_cli_exposes_bounded_recollection_inputs(self) -> None:
        args = cli.build_parser().parse_args([
            "compare-project-repository-recollection",
            "--legacy-db-path", "legacy.db",
            "--db-path", "current.db",
            "--output", "report.json",
            "--page-limit", "25",
            "--concurrency", "4",
        ])
        self.assertEqual(args.legacy_db_path, "legacy.db")
        self.assertEqual(args.db_path, "current.db")
        self.assertEqual(args.page_limit, 25)
        self.assertEqual(args.concurrency, 4)
        self.assertEqual(args.retries, 1)

    def test_cli_exposes_reviewed_repository_materialization(self) -> None:
        args = cli.build_parser().parse_args([
            "materialize-project-repository-review",
            "--db-path", "input.db",
            "--materialized-db", "shadow.db",
            "--review-manifest", "review.json",
            "--output", "report.json",
        ])
        self.assertEqual(args.db_path, "input.db")
        self.assertEqual(args.materialized_db, "shadow.db")
        self.assertEqual(args.review_manifest, "review.json")
        self.assertEqual(args.output, "report.json")


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
                ) VALUES (
                    'proj:test', 'Test', '', 'unknown',
                    '{"app":{"links":[{"uri":"https://github.com/example/repo","role":"sourceCode","official":true}]}}',
                    '{"ops":{"atproto_contribution_evidence":[{"kind":"relation_predicates","count":2,"values":["source_repository","uses_collection"]}]}}',
                    '2026-08-25', '2026-08-25'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO project_identifier_adoptions(
                    adoption_id, project_key, kind, value, role, adoption_kind, source, cleared_at
                ) VALUES ('adopt:former', 'proj:test', 'repo_url', 'https://github.com/example/former',
                          'source_repository', 'operator', 'web_confirm', '2026-08-25')
                """
            )
            conn.execute(
                """
                INSERT INTO project_noncurrent_identifiers(
                    project_key, kind, value, role, standing, reason
                ) VALUES ('proj:test', 'repo_url', 'https://github.com/example/former',
                          'source_repository', 'former', 'test')
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
                    project_key, ordinal, kind, target_type, target_value, target_role, metadata_json
                ) VALUES ('proj:test', 0, 'confirm_identifier', 'repo_url',
                          'https://github.com/example/repo', 'source_repository',
                          '{"type":"repo_url","value":"https://github.com/Example/Repo","role":"source_repository","source":"test"}')
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
        self.assertEqual(first["dependent_role_references"]["project_identifier_adoptions"], 2)
        self.assertEqual(first["dependent_role_references"]["project_noncurrent_identifiers"], 1)
        self.assertEqual(first["dependent_role_references"]["project_decisions"], 1)
        self.assertEqual(first["relation_disposition"]["disposition_counts"], {
            "DROP_LEGACY_README_MENTION": 1,
            "KEEP_AS_REPOSITORY_OWNERSHIP_OBSERVATION": 1,
            "RECOLLECT_FIRST_PARTY_CONTEXT": 1,
        })
        self.assertTrue(first["review_gate"]["ready_for_user_review"])

    def test_recollection_claims_are_canonical_and_annotated_from_current_state(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            claims = project_repository_shadow.legacy_first_party_recollection_claims(conn)
            annotated = project_repository_shadow.annotate_recollection_claims(conn, claims)
        finally:
            conn.close()
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["repository_url"], "https://github.com/example/repo")
        self.assertEqual(claims[0]["legacy_row_count"], 1)
        self.assertEqual(annotated[0]["page_standing"], "exact_current_component")
        self.assertEqual(annotated[0]["component_url"], "https://example.org")
        self.assertEqual(annotated[0]["repository_classification"], "absent")

    def test_materialized_copy_renames_roles_deduplicates_and_drops_legacy_relations(self) -> None:
        before = self.db_path.read_bytes()
        target = Path(self.tempdir.name) / "project-repository.db"
        result = project_repository_materialization.materialize_copy(self.db_path, target)
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertTrue(result["review_gate"]["ready"])
        self.assertEqual(result["changes"]["identifier_input_count"], 2)
        self.assertEqual(result["changes"]["identifier_output_count"], 1)
        self.assertEqual(result["changes"]["removed_legacy_relation_count"], 3)

        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
        try:
            roles = conn.execute(
                "SELECT role, classification, value FROM project_identifiers WHERE kind='repo_url'"
            ).fetchall()
            self.assertEqual(
                [(row["role"], row["classification"], row["value"]) for row in roles],
                [("project_repository", "confirmed", "https://github.com/example/repo")],
            )
            adoption = conn.execute(
                "SELECT role, value FROM project_identifier_adoptions WHERE adoption_id='adopt:test'"
            ).fetchone()
            self.assertEqual(
                (adoption["role"], adoption["value"]),
                ("project_repository", "https://github.com/example/repo"),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM project_identifier_adoptions WHERE role='project_repository'"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT role FROM project_noncurrent_identifiers"
                ).fetchone()[0],
                "project_repository",
            )
            decision = conn.execute(
                "SELECT target_value, target_role, metadata_json FROM project_decisions"
            ).fetchone()
            decision_metadata = json.loads(decision["metadata_json"])
            self.assertEqual(
                (decision["target_value"], decision["target_role"]),
                ("https://github.com/example/repo", "project_repository"),
            )
            self.assertEqual(decision_metadata["value"], "https://github.com/example/repo")
            self.assertEqual(decision_metadata["role"], "project_repository")
            project_row = conn.execute(
                "SELECT profile_json, state_json FROM projects WHERE project_key='proj:test'"
            ).fetchone()
            profile = json.loads(project_row["profile_json"])
            state = json.loads(project_row["state_json"])
            self.assertEqual(profile["app"]["links"][0]["role"], "repository")
            self.assertEqual(
                state["ops"]["atproto_contribution_evidence"][0]["values"],
                ["uses_collection"],
            )
            loaded = project_record.fetch_project(conn, "proj:test")
            self.assertEqual(loaded["project_repository_urls"], ["https://github.com/example/repo"])
            self.assertEqual(loaded["confirmed_identifiers"][0]["role"], "project_repository")
            self.assertNotIn("source_code_urls", loaded)
            self.assertEqual(loaded["relation_evidence"], [])
        finally:
            conn.close()

    def test_reviewed_repository_materialization_confirms_relation_and_identifier_in_copy(self) -> None:
        before = self.db_path.read_bytes()
        manifest_path = Path(self.tempdir.name) / "review.json"
        manifest_path.write_text(json.dumps({
            "schema_version": 1,
            "reviewed_at": "2026-08-27",
            "source_report_hash": "example-report-hash",
            "actions": [{
                "project_key": "proj:test",
                "expected_component_url": "https://example.org",
                "repository_url": "https://github.com/example/new-project",
                "locator": "https://github.com/example/new-project/tree/main/app",
                "expected_repository_classification": "absent",
            }],
        }), encoding="utf-8")
        target = Path(self.tempdir.name) / "reviewed.db"

        result = project_repository_review_materialization.materialize_reviewed_copy(
            self.db_path,
            target,
            manifest_path,
        )

        self.assertEqual(before, self.db_path.read_bytes())
        self.assertTrue(result["review_gate"]["ready"])
        self.assertEqual(result["action_counts"], {
            "confirmed_identifier": 1,
            "confirmed_relation": 1,
        })
        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
        try:
            identifier = conn.execute(
                "SELECT classification FROM project_identifiers WHERE project_key='proj:test' "
                "AND kind='repo_url' AND value='https://github.com/example/new-project' "
                "AND role='project_repository'"
            ).fetchone()
            self.assertEqual(identifier["classification"], "confirmed")
            relation = conn.execute(
                "SELECT subject_type, subject_value, classification, source, locator, method "
                "FROM project_relations WHERE project_key='proj:test' "
                "AND predicate='project_repository' AND object_value='https://github.com/example/new-project'"
            ).fetchone()
            self.assertEqual(tuple(relation), (
                "service",
                "https://example.org",
                "confirmed",
                project_repository_review_materialization.REVIEW_SOURCE,
                "https://github.com/example/new-project/tree/main/app",
                project_repository_review_materialization.RELATION_METHOD,
            ))
            decision = conn.execute(
                "SELECT target_role, metadata_json FROM project_decisions WHERE project_key='proj:test' "
                "AND kind='confirm_identifier' AND target_value='https://github.com/example/new-project'"
            ).fetchone()
            self.assertEqual(decision["target_role"], "project_repository")
            self.assertFalse(json.loads(decision["metadata_json"])["registration_origin"])
            adoption = conn.execute(
                "SELECT adoption_kind, source, registration_origin FROM project_identifier_adoptions "
                "WHERE project_key='proj:test' AND value='https://github.com/example/new-project' "
                "AND role='project_repository' AND cleared_at=''"
            ).fetchone()
            self.assertEqual(tuple(adoption), (
                "operator",
                project_repository_review_materialization.REVIEW_SOURCE,
                0,
            ))
        finally:
            conn.close()

    def test_reviewed_repository_materialization_refuses_a_stale_component(self) -> None:
        before = self.db_path.read_bytes()
        manifest_path = Path(self.tempdir.name) / "stale-review.json"
        manifest_path.write_text(json.dumps({
            "schema_version": 1,
            "reviewed_at": "2026-08-27",
            "source_report_hash": "example-report-hash",
            "actions": [{
                "project_key": "proj:test",
                "expected_component_url": "https://changed.example.org",
                "repository_url": "https://github.com/example/new-project",
                "locator": "https://github.com/example/new-project",
                "expected_repository_classification": "absent",
            }],
        }), encoding="utf-8")
        target = Path(self.tempdir.name) / "stale.db"

        with self.assertRaisesRegex(ValueError, "exact component changed"):
            project_repository_review_materialization.materialize_reviewed_copy(
                self.db_path,
                target,
                manifest_path,
            )

        self.assertEqual(before, self.db_path.read_bytes())
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
