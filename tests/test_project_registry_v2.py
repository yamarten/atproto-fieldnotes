from __future__ import annotations

import copy
import json
import sqlite3
import unittest
from pathlib import Path

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import database
from project_registry_core import runtime_view


class ProjectRegistryV2StorageTest(TempRegistryDbTestCase):
    def test_open_database_initialization_survives_close_without_caller_commit(self) -> None:
        fresh_path = Path(self.tempdir.name) / "fresh.db"
        conn = database.open_database(fresh_path, create=True)
        try:
            self.assertFalse(conn.in_transaction)
        finally:
            conn.close()

        reopened = database.open_database(fresh_path, create=False)
        try:
            self.assertEqual(database.database_schema_version(reopened), database.SCHEMA_VERSION)
            self.assertFalse(reopened.in_transaction)
        finally:
            reopened.close()

    def test_open_database_create_false_does_not_create_missing_path(self) -> None:
        missing_path = Path(self.tempdir.name) / "missing" / "survey.db"

        with self.assertRaisesRegex(FileNotFoundError, "registry database not found"):
            database.open_database(missing_path, create=False)

        self.assertFalse(missing_path.exists())
        self.assertFalse(missing_path.parent.exists())

    def test_open_database_create_false_rejects_uninitialized_file(self) -> None:
        empty_path = Path(self.tempdir.name) / "empty.db"
        empty_path.touch()

        with self.assertRaisesRegex(database.SchemaMismatchError, "not initialized"):
            database.open_database(empty_path, create=False)

    def test_opening_existing_database_does_not_hold_writer_lock(self) -> None:
        initialized = registry.open_db()
        initialized.close()

        reader = database.open_database(self.db_path, create=False)
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

        incomplete_v2 = sqlite3.connect(self.db_path)
        incomplete_v2.execute("DROP TABLE project_key_aliases")
        incomplete_v2.commit()
        incomplete_v2.close()

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
                    metadata={"did": "did:plc:example", "source": "fixture"},
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
    def test_manual_gold_fixture_has_fixed_decision_coverage(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "manual-gold-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["cases"]
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(len(cases), 11)
        self.assertEqual(len({item["case_id"] for item in cases}), len(cases))

        action_counts: dict[str, int] = {}
        covered_areas: set[str] = set()
        allowed_actions = {
            "block_identifier",
            "confirm_identifier",
            "defer_identifier",
            "merge_project",
            "separate_project",
        }
        for case in cases:
            covered_areas.update(case["areas"])
            subjects_by_ref = {item["ref"]: item for item in case["subjects"]}
            subject_refs = set(subjects_by_ref)
            self.assertEqual(len(subject_refs), len(case["subjects"]))
            self.assertTrue(case["evidence_summary"])
            for decision in case["expected_decisions"]:
                self.assertIn(decision["action"], allowed_actions)
                self.assertIn(decision["subject_ref"], subject_refs)
                action_counts[decision["action"]] = action_counts.get(decision["action"], 0) + 1
                if decision["target_type"] == "project_ref":
                    self.assertIn(decision["target_value"], subject_refs)
                else:
                    candidates = {
                        (item["type"], item["value"])
                        for item in subjects_by_ref[decision["subject_ref"]]["candidate_identifiers"]
                    }
                    self.assertIn(
                        (decision["target_type"], decision["target_value"]),
                        candidates,
                    )

        self.assertEqual(
            covered_areas,
            {"identity", "repository_verification", "service_verification"},
        )
        self.assertGreaterEqual(action_counts["block_identifier"], 3)
        self.assertGreaterEqual(action_counts["confirm_identifier"], 3)
        self.assertGreaterEqual(action_counts["defer_identifier"], 2)
        self.assertGreaterEqual(action_counts["separate_project"], 3)
        self.assertEqual(action_counts["merge_project"], 3)

    def test_manual_gold_fixture_has_promotion_negative_coverage(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "manual-gold-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["promotion_cases"]

        self.assertEqual(len(cases), 9)
        self.assertEqual(len({item["case_id"] for item in cases}), len(cases))
        dispositions = [item["expected_promotion"]["disposition"] for item in cases]
        self.assertEqual(dispositions.count("exclude"), 8)
        self.assertEqual(dispositions.count("hold"), 1)
        self.assertTrue(all(item["evidence_summary"] for item in cases))

        juntos = next(item for item in cases if item["subject"]["ref"] == "juntos")
        self.assertEqual(juntos["subject"]["service_status_confidence"], "low")
        self.assertEqual(juntos["subject"]["service_status_evidence"], ["http_200"])
        self.assertEqual(
            juntos["expected_promotion"],
            {"disposition": "hold", "reason": "service_not_usable"},
        )
        excluded = [item for item in cases if item["expected_promotion"]["disposition"] == "exclude"]
        self.assertTrue(all(item["subject"]["tracking_state"] == "out_of_scope" for item in excluded))

        for item in cases:
            project = dict(item["subject"])
            project["website_url"] = project.pop("service_url")
            signal_kinds = {
                signal["kind"] for signal in registry.compute_showcase_signals(project)
            }
            self.assertNotIn("service_ready_for_introduction", signal_kinds)

    def test_manual_gold_fixture_omits_volatile_log_identity(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "manual-gold-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        forbidden_keys = {
            "project_key",
            "run_id",
            "created_at",
            "expires_at",
            "cleared_at",
            "operator_note",
        }

        def assert_fixed(value: object) -> None:
            if isinstance(value, dict):
                self.assertFalse(forbidden_keys & value.keys())
                for child in value.values():
                    assert_fixed(child)
            elif isinstance(value, list):
                for child in value:
                    assert_fixed(child)
            elif isinstance(value, str):
                self.assertFalse(value.startswith("proj:"))

        assert_fixed(payload["cases"])
        assert_fixed(payload["promotion_cases"])

    def test_operator_correction_benchmark_detects_removed_guard(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "operator-correction-benchmark-v1.json"
        cases = registry.load_benchmark_cases(path)
        passing = registry.replay_core.benchmark_cases(cases, registry.replay_dependencies())
        self.assertEqual(passing["regression_count"], 0)

        mutated = copy.deepcopy(cases)
        mutated[0]["input_project"]["blocked_identifiers"] = []
        regressed = registry.replay_core.benchmark_cases(mutated, registry.replay_dependencies())
        self.assertEqual(regressed["regression_count"], 1)
        self.assertEqual(regressed["results"][0]["case_id"], "operator-blocks-tenant-service")
        self.assertTrue(regressed["results"][0]["diff"]["changed"])

    def test_fixture_sanitizer_removes_secrets_raw_text_paths_and_url_queries(self) -> None:
        sanitized = registry.sanitize_fixture_value({
            "api_key": "secret",
            "nested": {"session_token": "secret", "headers": {"Authorization": "Bearer secret"}},
            "html": "<p>raw</p>",
            "path": "/Users/operator/private/file.json",
            "url": "https://user:pass@example.com/path?token=secret#fragment",
            "message": "Authorization: Bearer abc.def",
            "public_identifier": "app.example.record",
        })

        self.assertEqual(sanitized["api_key"], "[redacted]")
        self.assertEqual(sanitized["nested"]["session_token"], "[redacted]")
        self.assertNotIn("headers", sanitized["nested"])
        self.assertNotIn("html", sanitized)
        self.assertEqual(sanitized["path"], "[local-path]")
        self.assertEqual(sanitized["url"], "https://example.com/path")
        self.assertEqual(sanitized["message"], "Authorization: Bearer [redacted]")
        self.assertEqual(sanitized["public_identifier"], "app.example.record")


if __name__ == "__main__":
    unittest.main()
