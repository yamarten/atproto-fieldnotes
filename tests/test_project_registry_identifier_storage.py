from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import database
from project_registry_core import identifier_storage
from project_registry_core.maintenance import identifier_storage_materialization
from project_registry_core import project_record
from project_registry_core import source_sync
from project_registry_core.application import decisions
from project_registry_core.application import presentation


class IdentifierStorageV3Test(TempRegistryDbTestCase):
    def _save_manual_service(self, project_key: str, url: str) -> dict:
        conn = registry.open_db()
        try:
            project = self.make_project(project_key, "service_url", url)
            registry.add_manual_seed(project, "service_url", url, source="operator_add_seed")
            project_record.save_project(conn, project)
            return project
        finally:
            conn.close()

    def test_observation_values_are_not_component_identifier_rows(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:observations")
            project["resolved_nsids"] = ["app.example.post"]
            project["authority_domains"] = ["unrelated.example"]
            project["source_repo_records"] = [{
                "url": "https://github.com/example/project",
                "source": "github_search",
                "evidence_url": "https://github.com/example/project",
            }]
            project_record.save_project(conn, project)

            roles = {
                str(row[0])
                for row in conn.execute(
                    "SELECT role FROM project_identifiers WHERE project_key=?",
                    (project["project_key"],),
                )
            }
            state = json.loads(conn.execute(
                "SELECT state_json FROM projects WHERE project_key=?",
                (project["project_key"],),
            ).fetchone()[0])
            fetched = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        self.assertNotIn("authority_domain", roles)
        self.assertNotIn("source_repository_record", roles)
        self.assertEqual(fetched["authority_domains"], ["example.app"])
        self.assertEqual(
            state["evidence"]["source_repo_records"][0]["url"],
            "https://github.com/example/project",
        )

    def test_operator_seed_creates_adoption_and_anchor_requires_it(self) -> None:
        url = "https://example.com"
        project = self._save_manual_service("proj:adoption", url)
        conn = registry.open_db()
        try:
            adoption = conn.execute(
                """
                SELECT adoption_kind, source, registration_origin
                FROM project_identifier_adoptions
                WHERE project_key=? AND kind='service_url' AND value=? AND role='service' AND cleared_at=''
                """,
                (project["project_key"], url),
            ).fetchone()
            fetched = project_record.fetch_project(conn, project["project_key"])
            self.assertIn(
                {"kind": "service_url", "value": url},
                registry.trusted_anchor_nodes(fetched),
            )
            conn.execute(
                "UPDATE project_identifier_adoptions SET cleared_at='2026-08-13T00:00:00+00:00' WHERE project_key=?",
                (project["project_key"],),
            )
            conn.commit()
            fetched_without_adoption = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        self.assertEqual(tuple(adoption), ("operator", "operator_add_seed", 1))
        self.assertNotIn(
            {"kind": "service_url", "value": url},
            registry.trusted_anchor_nodes(fetched_without_adoption),
        )

    def test_former_identifier_remains_searchable_but_is_not_an_anchor(self) -> None:
        url = "https://former.example.com"
        project = self._save_manual_service("proj:former", url)
        conn = registry.open_db()
        try:
            identifier_storage.move_current_to_noncurrent(
                conn,
                project_key=project["project_key"],
                kind="service_url",
                value=url,
                role="service",
                standing="former",
                reason="service moved",
                recorded_at="2026-08-13T00:00:00+00:00",
            )
            conn.commit()
            fetched = project_record.fetch_project(conn, project["project_key"])
            matches = project_record.indexed_identifier_matches(conn, "service_url", url)
        finally:
            conn.close()

        self.assertEqual(matches, [(2, project["project_key"])])
        self.assertNotIn(
            {"kind": "service_url", "value": url},
            registry.trusted_anchor_nodes(fetched),
        )

    def test_readding_current_claim_removes_noncurrent_copy(self) -> None:
        url = "https://reactivated.example.com"
        project = self._save_manual_service("proj:reactivated", url)
        conn = registry.open_db()
        try:
            identifier_storage.move_current_to_noncurrent(
                conn,
                project_key=project["project_key"],
                kind="service_url",
                value=url,
                role="service",
                standing="non_production",
                reason="temporary deployment",
                recorded_at="2026-08-13T00:00:00+00:00",
            )
            fetched = project_record.fetch_project(conn, project["project_key"])
            registry.add_manual_seed(fetched, "service_url", url, source="operator_add_seed")
            project_record.save_project(conn, fetched)
            remaining = conn.execute(
                "SELECT COUNT(*) FROM project_noncurrent_identifiers WHERE project_key=? AND value=?",
                (project["project_key"], url),
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(remaining, 0)

    def test_collection_suppression_is_exact_for_noncurrent_and_exclusion(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:suppression")
            project_record.save_project(conn, project)
            conn.execute(
                """
                INSERT INTO project_noncurrent_identifiers(
                    project_key, kind, value, role, standing, reason, recorded_at, origin_project_key
                ) VALUES (?, 'nsid', 'com.example.test', 'owned_nsid', 'non_production', 'test namespace', ?, ?)
                """,
                (project["project_key"], "2026-08-13T00:00:00+00:00", project["project_key"]),
            )
            identifier_storage.set_exclusion(
                conn,
                kind="nsid",
                value="app.bsky.post",
                reason="common typo",
                source="operator",
                created_at="2026-08-13T00:00:00+00:00",
            )
            suppressed = (
                identifier_storage.known_noncurrent_collection_nsids(conn)
                | identifier_storage.active_exclusions(conn, "nsid")
            )
            payload = [
                {"collection": "com.example.test", "max": "2026-08-13T00:00:00+00:00"},
                {"collection": "app.bsky.post", "max": "2026-08-13T00:00:00+00:00"},
                {"collection": "app.bsky.feed.post", "max": "2026-08-13T00:00:00+00:00"},
                {"collection": "com.example.tests", "max": "2026-08-13T00:00:00+00:00"},
            ]
            filtered, _ = source_sync.filter_sync_collection_inventory_payload(
                payload,
                [],
                lookback_days=1,
                all_history=True,
                suppressed_nsids=suppressed,
            )
        finally:
            conn.close()

        self.assertEqual(
            [item["collection"] for item in filtered],
            ["app.bsky.feed.post", "com.example.tests"],
        )

    def test_transition_requires_explicit_evidence_and_is_not_inferred(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:transition")
            project_record.save_project(conn, project)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM project_identifier_transitions").fetchone()[0],
                0,
            )
            with self.assertRaisesRegex(ValueError, "evidence_ref"):
                identifier_storage.record_confirmed_transition(
                    conn,
                    project_key=project["project_key"],
                    from_kind="nsid",
                    from_value="com.example.post",
                    from_role="owned_nsid",
                    to_kind="nsid",
                    to_value="com.example.feed.post",
                    to_role="owned_nsid",
                    relation_kind="renamed_to",
                    evidence_ref="",
                    effective_at="2026-08-13T00:00:00+00:00",
                )
            identifier_storage.record_confirmed_transition(
                conn,
                project_key=project["project_key"],
                from_kind="nsid",
                from_value="com.example.post",
                from_role="owned_nsid",
                to_kind="nsid",
                to_value="com.example.feed.post",
                to_role="owned_nsid",
                relation_kind="renamed_to",
                evidence_ref="operator_review:example",
                effective_at="2026-08-13T00:00:00+00:00",
            )
            count = conn.execute("SELECT COUNT(*) FROM project_identifier_transitions").fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 1)

    def test_guard_decision_has_role_and_operator_confirmation_clears_defer(self) -> None:
        url = "https://candidate.example.com"
        conn = registry.open_db()
        try:
            project = self.make_project("proj:guard-role")
            project["service_url_candidates"] = [url]
            project_record.save_project(conn, project)
            decisions.set_identifier_defer(
                conn,
                project_key=project["project_key"],
                identifier_type="service_url",
                value=url,
                note="wait",
                days=30,
            )
            deferred_role = conn.execute(
                """
                SELECT target_role FROM project_decisions
                WHERE project_key=? AND kind='defer_identifier' AND target_value=?
                """,
                (project["project_key"], url),
            ).fetchone()[0]
            presentation.confirm_candidate(
                conn,
                project_key=project["project_key"],
                identifier_type="service_url",
                value=url,
            )
            fetched = project_record.fetch_project(conn, project["project_key"])
            confirm_role = conn.execute(
                """
                SELECT target_role FROM project_decisions
                WHERE project_key=? AND kind='confirm_identifier' AND target_value=?
                """,
                (project["project_key"], url),
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(deferred_role, "service")
        self.assertEqual(confirm_role, "service")
        self.assertEqual(fetched["deferred_identifiers"], [])
        self.assertIn(url, fetched["service_urls"])

    def test_merge_preserves_ancillary_origin_project(self) -> None:
        conn = registry.open_db()
        try:
            source = self.make_project("proj:merge-source")
            target = self.make_project("proj:merge-target")
            project_record.save_project(conn, source)
            project_record.save_project(conn, target)
            conn.execute(
                """
                INSERT INTO project_noncurrent_identifiers(
                    project_key, kind, value, role, standing, reason, recorded_at, origin_project_key
                ) VALUES (?, 'nsid', 'com.example.old', 'owned_nsid', 'former', 'renamed', ?, ?)
                """,
                (source["project_key"], "2026-08-13T00:00:00+00:00", source["project_key"]),
            )
            identifier_storage.merge_project_storage(conn, source["project_key"], target["project_key"])
            row = conn.execute(
                "SELECT project_key, origin_project_key FROM project_noncurrent_identifiers WHERE value='com.example.old'"
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(tuple(row), (target["project_key"], source["project_key"]))

    def test_review_split_records_mutual_project_separation(self) -> None:
        repo_url = "https://github.com/example/child"
        service_url = "https://child.example.com"
        conn = registry.open_db()
        try:
            source = self.make_project("proj:split-source", "repo_url", repo_url)
            registry.add_manual_seed(source, "repo_url", repo_url, source="operator_add_seed")
            registry.confirm_service_seed(source, service_url, source="operator_add_seed")
            project_record.save_project(conn, source)
            result = identifier_storage_materialization._create_split_project(
                conn,
                {
                    "ref": "child",
                    "source_project_key": source["project_key"],
                    "name": "Child",
                    "move_values": [repo_url],
                    "include_related_service": service_url,
                },
                created_at="2026-08-14T00:00:00+00:00",
            )
            conn.commit()
            source_after = project_record.fetch_project(conn, source["project_key"])
            target_after = project_record.fetch_project(conn, result["target_project_key"])
            separation_rows = conn.execute(
                """
                SELECT project_key, target_value, metadata_json
                FROM project_decisions
                WHERE kind='separate_project'
                ORDER BY project_key
                """
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(source_after["separate_project_keys"], [target_after["project_key"]])
        self.assertEqual(target_after["separate_project_keys"], [source_after["project_key"]])
        self.assertEqual(
            {(str(row[0]), str(row[1])) for row in separation_rows},
            {
                (source_after["project_key"], target_after["project_key"]),
                (target_after["project_key"], source_after["project_key"]),
            },
        )
        self.assertTrue(all(json.loads(str(row[2]))["source"] == identifier_storage_materialization.REVIEW_SOURCE for row in separation_rows))

    def test_ordinary_open_rejects_v2_and_materializer_migrates_only_the_copy(self) -> None:
        project = self._save_manual_service("proj:migrated-save", "https://migrated.example.com")
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE registry_meta SET value='2' WHERE key='schema_version'")
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'revision_%'").fetchall():
                conn.execute(f"DROP TRIGGER {row[0]}")
            conn.execute("DROP TABLE project_revisions")
            for column in ("application_status", "conflict_reason", "resolved_at"):
                conn.execute(f"ALTER TABLE investigation_runs DROP COLUMN {column}")
            for table in (
                "project_noncurrent_identifiers",
                "project_identifier_adoptions",
                "project_identifier_transitions",
                "registry_identifier_exclusions",
            ):
                conn.execute(f"DROP TABLE {table}")
            conn.commit()
        finally:
            conn.close()

        with self.assertRaisesRegex(database.SchemaMismatchError, "schema version 2"):
            database.open_database(self.db_path)

        manifest_path = Path(self.tempdir.name) / "review.json"
        manifest_path.write_text(json.dumps({
            "schema_version": 1,
            "reviewed_at": "2026-08-13",
            "reviewed_projection": {
                "claim_count": 0,
                "claim_key_sha256": identifier_storage_materialization._claim_key_hash([]),
                "default_action": "keep_current_with_operator_adoption",
            },
            "claim_actions": [],
            "additional_claim_actions": [],
            "add_current_claims": [],
            "project_updates": [],
            "separate_project_actions": [],
        }), encoding="utf-8")
        target_path = Path(self.tempdir.name) / "migrated.db"
        before = self.db_path.read_bytes()
        identifier_storage_materialization.materialize_reviewed_copy(
            self.db_path,
            target_path,
            manifest_path,
        )
        migrated = database.open_database(target_path)
        try:
            self.assertEqual(database.database_schema_version(migrated), database.SCHEMA_VERSION)
            fetched = project_record.fetch_project(migrated, project["project_key"])
            project_record.save_project(migrated, fetched, preserve_timestamps=True)
            decision = migrated.execute(
                """
                SELECT target_role, metadata_json, json_valid(metadata_json)
                FROM project_decisions
                WHERE project_key=? AND kind='confirm_identifier'
                """,
                (project["project_key"],),
            ).fetchone()
        finally:
            migrated.close()
        self.assertEqual(str(decision[0]), "service")
        self.assertEqual(int(decision[2]), 1)
        self.assertEqual(json.loads(str(decision[1]))["source"], "operator_add_seed")
        self.assertEqual(self.db_path.read_bytes(), before)


if __name__ == "__main__":
    import unittest

    unittest.main()
