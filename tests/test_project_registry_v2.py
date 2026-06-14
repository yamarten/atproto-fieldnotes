from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import database


class ProjectRegistryV2StorageTest(TempRegistryDbTestCase):
    def test_normalized_children_roundtrip_without_compatibility_fields(self) -> None:
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
        for removed in ("website_url_candidates", "github_repo_urls", "tangled_repo_urls", "resource_urls", "introduced"):
            self.assertNotIn(removed, fetched)
        self.assertEqual(fetched["source_code_urls"], ["https://github.com/example/app"])
        self.assertEqual(fetched["blocked_identifiers"][0]["value"], "app.example.noise")
        self.assertEqual(len(fetched["relation_evidence"]), 1)
        self.assertEqual(database.validate_database(conn)["relation_count"], 1)
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


class ProjectRegistryV2MigrationTest(unittest.TestCase):
    def make_legacy_database(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE projects (
                project_key TEXT PRIMARY KEY, name TEXT NOT NULL, website_url TEXT NOT NULL,
                service_status TEXT NOT NULL, tracking_state TEXT NOT NULL, introduced_url TEXT NOT NULL,
                record_json TEXT NOT NULL, ops_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, last_enriched_at TEXT
            );
            CREATE TABLE sync_state (
                scope_key TEXT PRIMARY KEY, kind TEXT NOT NULL, scope_json TEXT NOT NULL,
                watermark_json TEXT NOT NULL, last_success_at TEXT NOT NULL
            );
            CREATE TABLE investigation_runs (
                run_id TEXT PRIMARY KEY, project_key TEXT NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL, evidence_schema_version INTEGER NOT NULL,
                evaluation_version TEXT NOT NULL, input_project_json TEXT NOT NULL,
                output_project_json TEXT NOT NULL, evaluation_json TEXT NOT NULL, bundle_json TEXT NOT NULL
            );
            """
        )
        ops = {
            "ops": {
                "blocked_identifiers": [{"type": "nsid", "value": "app.example.noise", "note": "manual", "added_at": "2026-06-14T00:00:00+00:00"}],
            },
            "evidence": {
                "input_nsids": ["app.example.post"],
                "service_url_candidates": ["https://app.example"],
                "website_url_candidates": ["https://legacy.example"],
                "source_code_urls": [],
                "github_repo_urls": ["https://github.com/example/app"],
                "resource_urls": ["https://example.com/docs"],
            },
        }
        conn.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj:legacy", "Legacy", "", "unknown", "active", "", "{}", json.dumps(ops),
             "2026-01-01T00:00:00+00:00", "2026-06-14T00:00:00+00:00", None),
        )
        snapshot = {"project_key": "proj:legacy", "name": "Legacy", "website_url_candidates": ["https://legacy.example"]}
        bundle = {
            "schema_version": 1,
            "project_key": "proj:legacy",
            "collected_at": "2026-06-14T00:00:00+00:00",
            "records": [{
                "kind": "investigation_log", "source": "github_repo", "classification": "derived",
                "observed_at": "2026-06-14T00:00:00+00:00", "value": "checked", "context": {},
            }],
            "failures": [],
        }
        conn.execute(
            "INSERT INTO investigation_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("inv:legacy", "proj:legacy", "2026-06-14T00:00:00+00:00", "2026-06-14T00:00:01+00:00", 1,
             "investigation-evaluation-v1", json.dumps(snapshot), json.dumps(snapshot), "{}", json.dumps(bundle)),
        )
        conn.execute(
            "INSERT INTO sync_state VALUES (?, ?, ?, ?, ?)",
            ("github:test", "github", "{}", json.dumps({"updated_at": "2026-06-14T00:00:00+00:00"}),
             "2026-06-14T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

    def test_migrate_db_replaces_source_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "survey.db"
            self.make_legacy_database(path)
            path.chmod(0o640)
            report = database.migrate_database(path)
            self.assertEqual(report["status"], "migrated")
            self.assertTrue(Path(report["backup_path"]).exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            conn = database.open_database(path, create=False)
            row = conn.execute("SELECT profile_json, state_json FROM projects WHERE project_key='proj:legacy'").fetchone()
            self.assertIsNotNone(row)
            project = registry.fetch_project(conn, "proj:legacy")
            assert project is not None
            self.assertEqual(project["service_url_candidates"], ["https://app.example", "https://legacy.example"])
            self.assertEqual(project["source_code_urls"], ["https://github.com/example/app"])
            self.assertEqual(project["resource_url_records"][0]["url"], "https://example.com/docs")
            self.assertEqual(project["blocked_identifiers"][0]["value"], "app.example.noise")
            run = conn.execute(
                "SELECT evidence_schema_version, input_project_json, bundle_json FROM investigation_runs WHERE run_id='inv:legacy'"
            ).fetchone()
            self.assertEqual(run["evidence_schema_version"], 2)
            self.assertNotIn("website_url_candidates", json.loads(run["input_project_json"]))
            self.assertEqual(json.loads(run["bundle_json"])["schema_version"], 2)
            self.assertTrue(json.loads(run["bundle_json"])["collector_results"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0], 1)
            conn.close()

            second = database.migrate_database(path)
            self.assertEqual(second["status"], "already_current")

    def test_dry_run_leaves_legacy_database_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "survey.db"
            self.make_legacy_database(path)
            report = database.migrate_database(path, dry_run=True)
            self.assertEqual(report["status"], "dry_run")
            conn = sqlite3.connect(path)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
            conn.close()
            self.assertIn("record_json", columns)
            self.assertNotIn("profile_json", columns)

    def test_failed_validation_keeps_legacy_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "survey.db"
            self.make_legacy_database(path)
            conn = sqlite3.connect(path)
            broken = {"schema_version": 99, "project_key": "proj:legacy", "collected_at": "2026-06-14T00:00:00+00:00"}
            conn.execute("UPDATE investigation_runs SET bundle_json = ?", (json.dumps(broken),))
            conn.commit()
            conn.close()

            with self.assertRaisesRegex(RuntimeError, "source unchanged"):
                database.migrate_database(path)

            conn = sqlite3.connect(path)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)
            conn.close()
            self.assertIn("record_json", columns)
            self.assertTrue(list(Path(directory).glob(".survey.db.v2-*.tmp")))


if __name__ == "__main__":
    unittest.main()
