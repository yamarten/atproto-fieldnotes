from __future__ import annotations

import json
import sqlite3
from argparse import Namespace

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase
from project_registry_core import cli
from project_registry_core.maintenance import identifier_storage_materialization, identifier_storage_shadow
from project_registry_core.commands import maintenance


class _IdentifierStorageShadowFixtures:
    def _seed_comparison_rows(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO projects(
                    project_key, name, website_url, service_status, first_seen_at, last_seen_at
                ) VALUES ('project', 'Project', 'https://example.com', 'live', '2026-01-01', '2026-01-01')
                """
            )
            conn.execute(
                """
                INSERT INTO projects(
                    project_key, name, website_url, service_status, state_json,
                    first_seen_at, last_seen_at
                ) VALUES (
                    'legacy', 'Legacy', 'https://legacy.example.com', 'live', ?,
                    '2026-01-01', '2026-01-01'
                )
                """,
                (json.dumps({"evidence": {"discovery_sources": ["manual_seed"]}}),),
            )
            conn.executemany(
                """
                INSERT INTO project_identifiers(
                    project_key, ordinal, kind, value, role, classification
                ) VALUES ('project', ?, ?, ?, ?, ?)
                """,
                [
                    (0, "service_url", "https://example.com", "service", "confirmed"),
                    (1, "nsid", "com.example.post", "owned_nsid", "confirmed"),
                    (2, "repo_url", "https://github.com/example/project", "source_repository_record", "confirmed"),
                    (3, "handle", "developer.example.com", "developer_handle", "confirmed"),
                    (4, "service_url", "https://blocked.example.com", "service", "confirmed"),
                    (5, "repo_url", "https://github.com/example/project", "project_repository", "confirmed"),
                ],
            )
            conn.execute(
                """
                INSERT INTO project_identifiers(
                    project_key, ordinal, kind, value, role, classification
                ) VALUES (
                    'legacy', 0, 'service_url', 'https://legacy.example.com', 'service', 'confirmed'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO project_relations(
                    project_key, ordinal, subject_type, subject_value, predicate,
                    object_type, object_value, classification, source, method
                ) VALUES (
                    'project', 0, 'repo', 'https://github.com/example/project',
                    'project_repository', 'website', 'https://example.com',
                    'confirmed', 'github_repo_metadata:https://github.com/example/project',
                    'github_homepage'
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO project_decisions(
                    project_key, ordinal, kind, target_type, target_value, created_at, metadata_json
                ) VALUES ('project', ?, ?, ?, ?, ?, ?)
                """,
                [
                    (0, "confirm_identifier", "service_url", "https://example.com", "2026-01-01", json.dumps({"source": "web_ui"})),
                    (1, "confirm_identifier", "service_url", "https://blocked.example.com", "2026-01-01", json.dumps({"source": "web_ui"})),
                    (2, "block_identifier", "service_url", "https://blocked.example.com", "2026-02-01", json.dumps({"source": "web_ui"})),
                ],
            )
            conn.execute(
                """
                INSERT INTO project_decisions(
                    project_key, ordinal, kind, target_type, target_value,
                    created_at, expires_at, metadata_json
                ) VALUES (
                    'project', 3, 'defer_identifier', 'collection', 'com.example.post',
                    '2020-01-01', '2020-02-01', ?
                )
                """,
                (json.dumps({"source": "web_ui"}),),
            )
            conn.commit()
        finally:
            conn.close()


class IdentifierStorageShadowUnitTest(_IdentifierStorageShadowFixtures, ProjectRecordTestCase):
    def test_prototype_cases_cover_search_sync_reactivation_merge_and_exclusion(self) -> None:
        result = identifier_storage_shadow.evaluate_prototype_cases()
        self.assertEqual(result["case_count"], 8)
        self.assertEqual(result["failed_count"], 0, result["cases"])


class IdentifierStorageShadowTest(_IdentifierStorageShadowFixtures, TempRegistryDbTestCase):
    def test_migration_projection_maps_explicit_and_inferred_adoptions(self) -> None:
        self._seed_comparison_rows()
        report = identifier_storage_shadow.compare_database_path(self.db_path, example_limit=5)
        self.assertEqual(report["decision_role_mapping"]["status_counts"], {"resolved": 4})
        projection = report["adoption_projection"]
        self.assertEqual(projection["confirmed_component_claim_count"], 5)
        self.assertEqual(projection["covered_claim_count"], 4)
        self.assertEqual(projection["guarded_confirmed_claim_count"], 1)
        self.assertEqual(projection["uncovered_claim_count"], 0)
        self.assertEqual(projection["adoption_kind_counts"], {"bootstrap": 3, "operator": 1})
        self.assertEqual(projection["relation_recovered_claim_count"], 1)
        self.assertEqual(projection["legacy_context_recovered_claim_count"], 1)
        self.assertEqual(
            projection["excluded_confirmed_role_counts"],
            {"developer_handle": 1, "source_repository_record": 1},
        )

    def test_comparison_is_read_only_and_reproducible(self) -> None:
        self._seed_comparison_rows()
        before = self.db_path.read_bytes()
        first = identifier_storage_shadow.compare_database_path(self.db_path, example_limit=2)
        after = self.db_path.read_bytes()
        second = identifier_storage_shadow.compare_database_path(self.db_path, example_limit=2)
        self.assertEqual(before, after)
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertTrue(first["source"]["read_only"])
        self.assertTrue(first["source"]["single_read_transaction"])
        self.assertTrue(first["review_gate"]["ready_for_user_review"])

    def test_cli_parser_exposes_identifier_storage_comparison(self) -> None:
        args = cli.build_parser().parse_args([
            "compare-identifier-storage",
            "--db-path",
            "input.db",
            "--output",
            "report.json",
        ])
        self.assertEqual(args.db_path, "input.db")
        self.assertEqual(args.output, "report.json")
        self.assertIsNone(args.materialized_db)

    def test_reviewed_shadow_materialization_keeps_source_read_only(self) -> None:
        self._seed_comparison_rows()
        reviewed_claims = [
            {
                "project_key": "legacy",
                "kind": "service_url",
                "value": "https://legacy.example.com",
                "role": "service",
            },
            {
                "project_key": "project",
                "kind": "repo_url",
                "value": "https://github.com/example/project",
                "role": "project_repository",
            },
        ]
        manifest = {
            "schema_version": 1,
            "reviewed_at": "2026-08-13",
            "reviewed_projection": {
                "claim_count": 2,
                "claim_key_sha256": identifier_storage_materialization._claim_key_hash(reviewed_claims),
                "default_action": "keep_current_with_operator_adoption",
            },
            "claim_actions": [
                {
                    **reviewed_claims[0],
                    "action": "website_only",
                }
            ],
            "additional_claim_actions": [],
            "add_current_claims": [],
            "project_updates": [],
            "separate_project_actions": [],
        }
        manifest_path = self.db_path.parent / "review.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        target_path = self.db_path.parent / "materialized.db"
        before = self.db_path.read_bytes()

        report = identifier_storage_materialization.materialize_reviewed_copy(
            self.db_path,
            target_path,
            manifest_path,
        )

        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertTrue(report["source"]["read_only"])
        self.assertTrue(report["review_gate"]["ready"], report)
        self.assertEqual(report["active_guard_removed_claim_count"], 1)
        self.assertEqual(report["service_status_resets"], [
            {
                "project_key": "legacy",
                "project_name": "Legacy",
                "previous_status": "live",
            }
        ])
        self.assertEqual(report["review"]["action_counts"], {
            "keep_current_with_operator_adoption": 1,
            "website_only": 1,
        })
        conn = sqlite3.connect(target_path)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM project_identifiers WHERE project_key='legacy' AND role='service'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT website_url FROM projects WHERE project_key='legacy'").fetchone()[0],
                "https://legacy.example.com",
            )
            self.assertEqual(
                conn.execute("SELECT service_status FROM projects WHERE project_key='legacy'").fetchone()[0],
                "none",
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM project_identifier_adoptions").fetchone()[0],
                5,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT source FROM project_identifier_adoptions "
                    "WHERE project_key='project' AND kind='repo_url' "
                    "AND value='https://github.com/example/project' "
                    "AND role='project_repository' AND cleared_at=''"
                ).fetchall(),
                [(identifier_storage_materialization.REVIEW_SOURCE,)],
            )
        finally:
            conn.close()

    def test_cli_refuses_to_overwrite_source_database(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            maintenance.cmd_compare_identifier_storage(Namespace(
                db_path=str(self.db_path),
                output=str(self.db_path),
                example_limit=5,
            ))


if __name__ == "__main__":
    import unittest

    unittest.main()
