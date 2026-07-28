from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from helpers import TempRegistryDbTestCase, registry
import promotion_evaluation
from project_registry_core import database
from project_registry_core import runtime_view
from project_registry_core import project_record


class ProjectRegistryV2StorageTest(TempRegistryDbTestCase):
    def test_project_record_is_the_storage_boundary_without_registry_reexports(self) -> None:
        self.assertNotIn("registry", vars(project_record))
        self.assertNotIn("project_record", vars(database))
        for name in (
            "default_project_record",
            "ensure_project_shape",
            "fetch_project",
            "list_projects",
            "save_project",
            "find_project_key_by_identifier",
            "allocate_new_project_key",
        ):
            self.assertTrue(callable(getattr(project_record, name)))
            self.assertFalse(hasattr(registry, name))

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
        project_record.save_project(conn, project)
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
            existing = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        self.assertIsNotNone(alias_table)
        self.assertIsNotNone(existing)

    def test_loaded_normalized_rows_roundtrip_losslessly(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:v2lossless")
        project_record.save_project(conn, project)
        database.replace_identifier_rows(
            conn,
            project["project_key"],
            [
                runtime_view.ProjectIdentifier(
                    "nsid",
                    "app.example.post",
                    "owned_nsid",
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

        fetched = project_record.fetch_project(conn, project["project_key"])
        assert fetched is not None
        self.assertEqual(fetched["input_nsids"], ["app.example.post"])
        project_record.save_project(conn, fetched, preserve_timestamps=True)
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
        project_record.save_project(conn, project)

        fetched = project_record.fetch_project(conn, project["project_key"])
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
        project_record.save_project(conn, project)

        with self.assertRaisesRegex(ValueError, "complete runtime view"):
            project_record.save_project(conn, {
                "project_key": project["project_key"],
                "name": "Partial update",
            })

        fetched = project_record.fetch_project(conn, project["project_key"])
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
        project_record.save_project(conn, merged)
        fetched = project_record.fetch_project(conn, target["project_key"])
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

    def test_manual_gold_fixture_has_promotion_priority_coverage(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "manual-gold-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["promotion_cases"]

        self.assertEqual(len(cases), 14)
        self.assertEqual(len({item["case_id"] for item in cases}), len(cases))
        dispositions = [item["expected_promotion"]["disposition"] for item in cases]
        self.assertEqual(dispositions.count("exclude"), 8)
        self.assertEqual(dispositions.count("queue"), 2)
        self.assertEqual(dispositions.count("deprioritize"), 4)
        self.assertTrue(all(item["evidence_summary"] for item in cases))
        self.assertTrue(all(item["expected_policy"] for item in cases))
        expected_order = payload["promotion_queue_expectation"]["ordered_subject_refs"]
        queued_refs = {
            item["subject"]["ref"]
            for item in cases
            if item["expected_promotion"]["disposition"] != "exclude"
        }
        self.assertEqual(len(expected_order), len(set(expected_order)))
        self.assertEqual(set(expected_order), queued_refs)

        juntos = next(item for item in cases if item["subject"]["ref"] == "juntos")
        self.assertEqual(juntos["subject"]["service_status_confidence"], "low")
        self.assertEqual(
            juntos["subject"]["service_status_evidence"],
            [
                "http_200",
                "browser_password_form_detected",
                "browser_identity_form_detected",
                "browser_oauth_entry_detected",
            ],
        )
        self.assertEqual(
            juntos["expected_promotion"],
            {"disposition": "queue", "reason": "runtime_validation_required"},
        )
        excluded = [item for item in cases if item["expected_promotion"]["disposition"] == "exclude"]
        self.assertTrue(all(item["subject"]["tracking_state"] == "out_of_scope" for item in excluded))

        echosky = next(item for item in cases if item["subject"]["ref"] == "echosky")
        project = dict(echosky["subject"])
        project["website_url"] = project.pop("service_url")
        signal_kinds = {
            signal["kind"] for signal in registry.compute_showcase_signals(project)
        }
        self.assertIn("service_surface_confirmed", signal_kinds)

        project = dict(juntos["subject"])
        project["website_url"] = project.pop("service_url")
        signal_kinds = {
            signal["kind"] for signal in registry.compute_showcase_signals(project)
        }
        self.assertIn("service_candidate_needs_validation", signal_kinds)

        browser_path = Path(__file__).parent / "fixtures" / "project_registry" / "browser-service-calibration-v1.json"
        browser_payload = json.loads(browser_path.read_text(encoding="utf-8"))
        browser_case_ids = {item["case_id"] for item in browser_payload["cases"]}
        linked_cases = [item for item in cases if "browser_calibration_case_id" in item]
        self.assertEqual(len(linked_cases), 4)
        self.assertTrue(all(item["browser_calibration_case_id"] in browser_case_ids for item in linked_cases))

    def test_manual_gold_promotion_cases_project_machine_policy_without_priority_guessing(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "manual-gold-v1.json"
        cases = json.loads(path.read_text(encoding="utf-8"))["promotion_cases"]
        expected_service_groups = {
            "juntos": "needs_validation",
            "moat": "needs_validation",
            "gmstn-systems": "needs_validation",
            "echosky": "confirmed",
            "atshare": "needs_validation",
            "mathr": "confirmed",
        }

        for item in cases:
            project = dict(item["subject"])
            project["website_url"] = project.pop("service_url")
            shaped = registry.apply_promotion_state(project_record.ensure_project_shape(project))
            signals = registry.compute_showcase_signals(shaped)
            policy = registry.promotion_policy_projection(shaped, signals)
            disposition = item["expected_promotion"]["disposition"]

            if disposition == "exclude":
                self.assertEqual(policy["candidate_visibility"]["group"], "excluded")
                self.assertEqual(signals, [])
                self.assertEqual(policy["service_evidence_group"], "not_applicable")
            else:
                self.assertEqual(policy["candidate_visibility"], {"group": "eligible", "reasons": []})
                self.assertEqual(
                    policy["service_evidence_group"],
                    expected_service_groups[project["ref"]],
                )
                self.assertEqual(policy["priority_group"], "normal")
                if disposition == "deprioritize":
                    registry.set_promotion_priority_on_project(
                        shaped,
                        lowered=True,
                        reason=item["expected_promotion"]["reason"],
                    )
                    policy = registry.promotion_policy_projection(shaped, signals)
                    self.assertEqual(policy["priority_group"], "lowered")
                    self.assertEqual(
                        policy["priority_reason"],
                        item["expected_promotion"]["reason"],
                    )

        lowered_priority_cases = [
            item for item in cases
            if item["expected_promotion"]["disposition"] == "deprioritize"
        ]
        self.assertEqual(len(lowered_priority_cases), 4)

    def test_promotion_benchmark_executes_all_cases_and_detects_order_regression(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "manual-gold-v1.json"
        summary = promotion_evaluation.evaluate(path)

        self.assertEqual(summary["case_count"], 14)
        self.assertEqual(summary["passed_case_count"], 14)
        self.assertEqual(summary["regression_count"], 0)
        self.assertEqual(summary["actual_order"], summary["expected_order"])

        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["promotion_queue_expectation"]["ordered_subject_refs"][:2] = ["juntos", "echosky"]
        with tempfile.TemporaryDirectory() as tempdir:
            changed_path = Path(tempdir) / "manual-gold-v1.json"
            changed_path.write_text(json.dumps(payload), encoding="utf-8")
            changed_summary = promotion_evaluation.evaluate(changed_path)

        self.assertTrue(changed_summary["order_regression"])
        self.assertEqual(changed_summary["case_regression_count"], 0)
        self.assertEqual(changed_summary["regression_count"], 1)

        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["promotion_cases"][0]["expected_policy"]["score_breakdown"]["total"] += 1
        with tempfile.TemporaryDirectory() as tempdir:
            changed_path = Path(tempdir) / "manual-gold-v1.json"
            changed_path.write_text(json.dumps(payload), encoding="utf-8")
            changed_summary = promotion_evaluation.evaluate(changed_path)

        self.assertFalse(changed_summary["order_regression"])
        self.assertEqual(changed_summary["case_regression_count"], 1)
        self.assertEqual(changed_summary["results"][0]["diff"]["changes"][0]["field"], "score_breakdown")

    def test_manual_gold_fixture_has_showcase_reference_coverage(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "manual-gold-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["showcase_reference_cases"]

        self.assertEqual(len(cases), 5)
        self.assertEqual(len({item["case_id"] for item in cases}), len(cases))
        tiers = [item["expected_editorial"]["tier"] for item in cases]
        self.assertEqual(tiers.count("flagship"), 1)
        self.assertEqual(tiers.count("strong"), 2)
        self.assertEqual(tiers.count("technical_interest"), 2)
        self.assertTrue(all(item["evidence_summary"] for item in cases))

        meyasuat = next(item for item in cases if item["subject"]["ref"] == "meyasuat")
        self.assertIn("not an automated ranking signal", meyasuat["manual_context"][0])

    def test_excluded_promotion_cases_match_policy(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "manual-gold-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["promotion_cases"]
        excluded_cases = [
            item
            for item in cases
            if item["expected_promotion"]["disposition"] == "exclude"
        ]

        self.assertEqual(len(excluded_cases), 8)
        for item in excluded_cases:
            project = dict(item["subject"])
            project["website_url"] = project.pop("service_url")
            self.assertEqual(registry.compute_showcase_signals(project), [])

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
        assert_fixed(payload["showcase_reference_cases"])

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
