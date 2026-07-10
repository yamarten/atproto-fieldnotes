from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import database
from project_registry_core import runtime_view


class ProjectRegistryV2StorageTest(TempRegistryDbTestCase):
    def test_open_database_initialization_survives_close_without_caller_commit(self) -> None:
        conn = database.open_database(self.db_path)
        try:
            self.assertFalse(conn.in_transaction)
        finally:
            conn.close()

        reopened = database.open_database(self.db_path)
        try:
            self.assertEqual(database.database_schema_version(reopened), database.SCHEMA_VERSION)
            self.assertFalse(reopened.in_transaction)
        finally:
            reopened.close()

    def test_opening_existing_database_does_not_hold_writer_lock(self) -> None:
        initialized = registry.open_db()
        initialized.close()

        reader = database.open_database(self.db_path)
        writer = sqlite3.connect(self.db_path, timeout=0)
        try:
            self.assertFalse(reader.in_transaction)
            writer.execute("BEGIN IMMEDIATE")
            writer.rollback()
        finally:
            writer.close()
            reader.close()

    def test_opening_existing_v2_database_adds_project_alias_table(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:additivev2")
        registry.save_project(conn, project)
        conn.close()

        legacy_v2 = sqlite3.connect(self.db_path)
        legacy_v2.execute("DROP TABLE project_key_aliases")
        legacy_v2.commit()
        legacy_v2.close()

        conn = registry.open_db()
        try:
            alias_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_key_aliases'"
            ).fetchone()
            existing = registry.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        self.assertIsNotNone(alias_table)
        self.assertIsNotNone(existing)

    def test_loaded_normalized_rows_roundtrip_losslessly(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:v2lossless")
        registry.save_project(conn, project)
        database.replace_identifier_rows(
            conn,
            project["project_key"],
            [
                runtime_view.ProjectIdentifier(
                    "nsid",
                    "app.example.post",
                    "primary_nsid",
                    "candidate",
                    metadata={"source": "fixture"},
                ),
                runtime_view.ProjectIdentifier(
                    "account",
                    "did:plc:example",
                    "developer_account",
                    "candidate",
                    metadata={"did": "did:plc:example", "source": "legacy"},
                ),
                runtime_view.ProjectIdentifier(
                    "did",
                    "did:plc:example",
                    "developer_did",
                    "candidate",
                    metadata={"account": "did:plc:example"},
                ),
            ],
        )
        conn.commit()
        before = database.normalized_rows_snapshot(conn)

        fetched = registry.fetch_project(conn, project["project_key"])
        assert fetched is not None
        self.assertEqual(fetched["primary_nsid"], "")
        self.assertEqual(fetched["input_nsids"], ["app.example.post"])
        registry.save_project(conn, fetched, preserve_timestamps=True)
        after = database.normalized_rows_snapshot(conn)

        self.assertEqual(before, after)
        conn.close()

    def test_normalized_children_roundtrip(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:v2roundtrip")
        project["website_url"] = "https://example.com"
        project["service_urls"] = ["https://example.com"]
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["blocked_identifiers"] = [{
            "type": "nsid", "value": "app.example.noise", "note": "manual", "added_at": "2026-06-14T00:00:00+00:00",
        }]
        relation = {
            "relation": {
                "subject": {"type": "repo", "value": "https://github.com/example/app"},
                "predicate": "defines_schema",
                "object": {"type": "nsid", "value": "app.example.post"},
                "locator": "lexicons/app.example.post.json",
                "method": "lexicon_json",
            },
            "source": "github_source",
            "classification": "confirmed",
            "observed_at": "2026-06-14T00:00:00+00:00",
            "context": {},
        }
        project["relation_evidence"] = [relation, relation]
        registry.save_project(conn, project)

        fetched = registry.fetch_project(conn, project["project_key"])
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["source_code_urls"], ["https://github.com/example/app"])
        self.assertEqual(fetched["blocked_identifiers"][0]["value"], "app.example.noise")
        self.assertEqual(len(fetched["relation_evidence"]), 1)
        self.assertEqual(database.validate_database(conn)["relation_count"], 1)
        row = conn.execute(
            "SELECT profile_json, state_json FROM projects WHERE project_key = ?",
            (project["project_key"],),
        ).fetchone()
        self.assertNotIn(runtime_view.RUNTIME_VIEW_MARKER, str(row["profile_json"]))
        self.assertNotIn(runtime_view.RUNTIME_VIEW_MARKER, str(row["state_json"]))
        conn.close()

    def test_existing_project_update_rejects_partial_runtime_view(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:v2partial")
        project["service_url_candidates"] = ["https://candidate.example"]
        registry.save_project(conn, project)

        with self.assertRaisesRegex(ValueError, "complete runtime view"):
            registry.save_project(conn, {
                "project_key": project["project_key"],
                "name": "Partial update",
            })

        fetched = registry.fetch_project(conn, project["project_key"])
        assert fetched is not None
        self.assertTrue(runtime_view.is_complete(fetched))
        self.assertEqual(fetched["service_url_candidates"], ["https://candidate.example"])
        conn.close()

    def test_merge_preserves_operator_decisions_in_normalized_tables(self) -> None:
        conn = registry.open_db()
        target = self.make_project("proj:v2target")
        source = self.make_project("proj:v2source", "service_url", "https://source.example")
        target["blocked_identifiers"] = [{
            "type": "repo_url", "value": "https://github.com/example/noise", "note": "wrong repo", "added_at": "2026-06-14T00:00:00+00:00",
        }]
        source["deferred_identifiers"] = [{
            "type": "service_url", "value": "https://candidate.example", "note": "wait", "added_at": "2026-06-14T00:00:00+00:00",
            "expires_at": "2099-06-14T00:00:00+00:00",
        }]
        merged = registry.merge_project_records(target, source)
        registry.save_project(conn, merged)
        fetched = registry.fetch_project(conn, target["project_key"])
        assert fetched is not None
        self.assertEqual(len(fetched["blocked_identifiers"]), 1)
        self.assertEqual(len(fetched["deferred_identifiers"]), 1)
        self.assertTrue(registry.identifier_is_blocked(fetched, "repo_url", "https://github.com/example/noise"))
        self.assertTrue(registry.identifier_is_deferred(fetched, "service_url", "https://candidate.example"))
        conn.close()


class ProjectRegistryOperatorCorrectionFixtureTest(unittest.TestCase):
    def test_operator_correction_fixture_keeps_distinct_guard_cases(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "operator-corrections-v2.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = {item["case"]: item for item in payload["cases"]}
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(
            cases["erroneous_service_merge_residue"]["decisions"][0]["target_value"],
            "https://bookhive.buzz",
        )
        split = cases["tenant_services_split_from_platform"]["decisions"]
        self.assertGreaterEqual(len([item for item in split if item["kind"] == "separate_project"]), 3)
        deferred = cases["multiple_repository_candidates_deferred"]["decisions"]
        self.assertEqual(len({item["target_value"] for item in deferred}), 10)


if __name__ == "__main__":
    unittest.main()
