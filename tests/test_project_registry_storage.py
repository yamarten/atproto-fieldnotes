from __future__ import annotations

import json
import unittest
import datetime as dt

from helpers import TempRegistryDbTestCase, registry


class ProjectRegistryStorageTest(TempRegistryDbTestCase):
    def test_parser_accepts_defer_identifier_command(self) -> None:
        args = registry.build_parser().parse_args(
            [
                "defer-identifier",
                "--project-key",
                "proj:121212121212",
                "--identifier-type",
                "service_url",
                "--value",
                "https://example.com",
                "--days",
                "14",
            ]
        )

        self.assertIs(args.func, registry.cmd_defer_identifier)
        self.assertEqual(args.days, 14)

    def test_defer_service_url_downgrades_to_candidate_and_blocks_confirmation(self) -> None:
        project = self.make_project("proj:121212121212", "service_url", "https://example.com")
        project["website_url"] = "https://example.com"
        project["service_urls"] = ["https://example.com"]

        registry.defer_identifier_on_project(project, "service_url", "https://example.com", days=30)
        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://example.com", project["service_urls"])
        self.assertNotEqual(project["website_url"], "https://example.com")
        self.assertIn("https://example.com", project["service_url_candidates"])
        registry.confirm_service_seed(project, "https://example.com", source="test")
        self.assertNotIn("https://example.com", project["service_urls"])

    def test_defer_repo_url_downgrades_to_candidate(self) -> None:
        project = self.make_project("proj:343434343434", "repo_url", "https://github.com/example/app")
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["github_repo_urls"] = ["https://github.com/example/app"]

        registry.defer_identifier_on_project(project, "repo_url", "https://github.com/example/app", days=14)
        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://github.com/example/app", project["source_code_urls"])
        self.assertIn("https://github.com/example/app", project["candidate_source_code_urls"])
        self.assertEqual(project["deferred_identifiers"][0]["type"], "repo_url")

    def test_defer_nsid_keeps_value_in_temporary_category(self) -> None:
        project = self.make_project("proj:565656565656", "nsid", "app.example.post")
        project["primary_nsid"] = "app.example.post"
        project["input_nsids"] = ["app.example.post"]

        registry.defer_identifier_on_project(project, "nsid", "app.example.post", days=30)
        project = registry.finalize_project_shape(project)

        self.assertNotIn("app.example.post", project["input_nsids"])
        self.assertEqual(project["deferred_identifiers"][0]["value"], "app.example.post")

    def test_expired_deferred_identifier_stops_blocking_confirmation(self) -> None:
        project = self.make_project("proj:defdefdefdef", "service_url", "https://example.com")
        project["deferred_identifiers"] = [
            {
                "type": "service_url",
                "value": "https://example.com",
                "added_at": "2026-01-01T00:00:00+00:00",
                "expires_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat(),
            }
        ]

        project = registry.ensure_project_shape(project)
        registry.confirm_service_seed(project, "https://example.com", source="test")

        self.assertEqual(project["deferred_identifiers"], [])
        self.assertIn("https://example.com", project["service_urls"])

    def test_save_project_splits_scalars_record_and_ops_payloads(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:aaaaaaaaaaaa", "service_url", "https://example.com")
            project["name"] = "Example App"
            project["website_url"] = "https://example.com"
            project["service_status"] = "live"
            project["summary"] = "Example summary"
            project["tagline"] = "Example tagline"
            project["name_candidates"] = [{"value": "Example App", "source": "service_title:https://example.com"}]
            project["service_status_confidence"] = "medium"
            project["service_status_evidence"] = ["http_200", "login_surface_detected"]
            project["resolved_nsids"] = ["app.example.post"]

            registry.save_project(conn, project, enriched=True)
            conn.commit()

            row = conn.execute("SELECT * FROM projects WHERE project_key = ?", ("proj:aaaaaaaaaaaa",)).fetchone()
            self.assertEqual(row["name"], "Example App")
            self.assertEqual(row["website_url"], "https://example.com")
            self.assertEqual(row["service_status"], "live")
            self.assertEqual(row["tracking_state"], "active")
            self.assertTrue(row["last_enriched_at"])

            record_payload = json.loads(row["record_json"])
            ops_payload = json.loads(row["ops_json"])
            self.assertEqual(record_payload["core"]["summary"], "Example summary")
            self.assertEqual(record_payload["core"]["tagline"], "Example tagline")
            self.assertEqual(ops_payload["evidence"]["name_candidates"][0]["value"], "Example App")
            self.assertEqual(ops_payload["ops"]["service_status_confidence"], "medium")
            self.assertEqual(ops_payload["ops"]["atproto_contribution_level"], "core")
            self.assertEqual(ops_payload["ops"]["atproto_contribution_score"], 90)

            fetched = registry.fetch_project(conn, "proj:aaaaaaaaaaaa")
            self.assertEqual(fetched["summary"], "Example summary")
            self.assertEqual(fetched["name_candidates"][0]["source"], "service_title:https://example.com")
            self.assertEqual(fetched["atproto_contribution_level"], "core")
        finally:
            conn.close()

    def test_save_project_preserves_last_enriched_at_on_non_enriched_update(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:bbbbbbbbbbbb", "nsid", "app.example.post")
            project["name"] = "Example App"
            registry.save_project(conn, project, enriched=True)
            conn.commit()
            first_last_enriched = conn.execute(
                "SELECT last_enriched_at FROM projects WHERE project_key = ?",
                ("proj:bbbbbbbbbbbb",),
            ).fetchone()["last_enriched_at"]

            fetched = registry.fetch_project(conn, "proj:bbbbbbbbbbbb")
            fetched["summary"] = "Updated without enrich"
            registry.save_project(conn, fetched, enriched=False)
            conn.commit()
            second_last_enriched = conn.execute(
                "SELECT last_enriched_at FROM projects WHERE project_key = ?",
                ("proj:bbbbbbbbbbbb",),
            ).fetchone()["last_enriched_at"]

            self.assertEqual(second_last_enriched, first_last_enriched)
        finally:
            conn.close()

    def test_audit_recomputes_duplicate_candidates_and_merge_recommendations(self) -> None:
        conn = registry.open_db()
        try:
            source = self.make_project("proj:cccccccccccc", "service_url", "https://example.com")
            source["name"] = "Example Source"
            source["website_url"] = "https://example.com"
            source["duplicate_candidates"] = []
            source["merge_recommendation"] = {}
            registry.save_project(conn, source)

            target = self.make_project("proj:dddddddddddd", "service_url", "https://example.com")
            target["name"] = "Example Target"
            target["website_url"] = "https://example.com"
            target["duplicate_candidates"] = []
            target["merge_recommendation"] = {}
            registry.save_project(conn, target)
            conn.commit()

            summary = registry.audit_and_repair_identifiers(conn)
            self.assertGreaterEqual(summary["refreshed_derived_project_count"], 1)

            repaired = registry.fetch_project(conn, "proj:cccccccccccc")
            duplicate_keys = {item["project_key"] for item in repaired["duplicate_candidates"]}
            self.assertIn("proj:dddddddddddd", duplicate_keys)
            self.assertEqual(repaired["merge_recommendation"]["merge_confidence"], "high")
            self.assertTrue(repaired["merge_recommendation"]["auto_merge_ready"])
        finally:
            conn.close()

    def test_audit_persists_missing_atproto_contribution_fields(self) -> None:
        conn = registry.open_db()
        try:
            now = registry.iso_now()
            conn.execute(
                """
                INSERT INTO projects (
                    project_key, name, website_url, service_status, tracking_state,
                    introduced_url, record_json, ops_json, first_seen_at, last_seen_at, last_enriched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "proj:legacycontri",
                    "Legacy Contribution",
                    "",
                    "unknown",
                    "active",
                    "",
                    "{}",
                    json.dumps({"evidence": {"input_nsids": ["app.example.post"]}}),
                    now,
                    now,
                    None,
                ),
            )
            conn.commit()

            registry.audit_and_repair_identifiers(conn)

            row = conn.execute("SELECT ops_json FROM projects WHERE project_key = ?", ("proj:legacycontri",)).fetchone()
            ops_payload = json.loads(row["ops_json"])
            self.assertEqual(ops_payload["ops"]["atproto_contribution_level"], "optional")
            self.assertEqual(ops_payload["ops"]["atproto_contribution_score"], 40)
        finally:
            conn.close()

    def test_review_queue_does_not_refresh_duplicate_candidates(self) -> None:
        conn = registry.open_db()
        original_compute = registry.compute_duplicate_candidates
        try:
            project = self.make_project("proj:cafecafecafe", "nsid", "app.example.post")
            project["name"] = "Candidate Service"
            project["service_url_candidates"] = ["https://candidate.example"]
            registry.save_project(conn, project)
            conn.commit()

            def fail_compute(*args, **kwargs):
                raise AssertionError("review queue should use stored derived merge fields")

            registry.compute_duplicate_candidates = fail_compute
            queue = registry.build_review_queue(conn, tracking_states={"active"}, limit=5)
            batch, next_index = registry.build_review_queue_batch(conn, tracking_states={"active"}, batch_size=5)
            item = registry.build_review_queue_item(conn, "proj:cafecafecafe")

            self.assertEqual(queue[0]["project_key"], "proj:cafecafecafe")
            self.assertEqual(batch[0]["project_key"], "proj:cafecafecafe")
            self.assertGreaterEqual(next_index, 1)
            self.assertEqual(item["project_key"], "proj:cafecafecafe")
        finally:
            registry.compute_duplicate_candidates = original_compute
            conn.close()

    def test_lightweight_review_shape_matches_full_review_signals(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:abababababab", "nsid", "app.example.post")
            project["name"] = "Review Candidate"
            project["manual_review_recommended"] = True
            project["manual_review_kind"] = "identity_resolution"
            project["manual_review_priority"] = "high"
            project["service_url_candidates"] = ["https://candidate.example"]
            project["candidate_source_code_urls"] = ["https://github.com/example/candidate"]
            project["collection_observations"] = [{"recent_count": 2, "observed_at": "2026-05-25T00:00:00+00:00"}]
            registry.save_project(conn, project)
            conn.commit()

            row = conn.execute("SELECT * FROM projects WHERE project_key = ?", ("proj:abababababab",)).fetchone()
            payload = registry.project_from_row_payload(row)
            full = registry.apply_promotion_state(registry.ensure_project_shape(dict(payload)))
            light = registry.apply_promotion_state(registry.lightweight_review_project_shape(dict(payload)))
            full_signals = registry.compute_review_signals(full, assume_shaped=True, apply_promotion=False)
            light_signals = registry.compute_review_signals(light, assume_shaped=True, apply_promotion=False)

            self.assertEqual(
                [item["kind"] for item in light_signals],
                [item["kind"] for item in full_signals],
            )
            self.assertEqual(registry.build_review_queue(conn, tracking_states={"active"}, limit=1)[0]["project_key"], "proj:abababababab")
        finally:
            conn.close()

    def test_review_queue_prioritizes_atproto_contribution_for_equal_signals(self) -> None:
        conn = registry.open_db()
        try:
            optional = self.make_project("proj:111111111111", "nsid", "app.example.optional")
            optional["name"] = "Optional"
            optional["manual_review_recommended"] = True
            optional["manual_review_kind"] = "identity_resolution"
            optional["manual_review_priority"] = "medium"
            optional["input_nsids"] = ["app.example.optional"]
            registry.save_project(conn, optional)

            substantial = self.make_project("proj:222222222222", "nsid", "app.example.substantial")
            substantial["name"] = "Substantial"
            substantial["manual_review_recommended"] = True
            substantial["manual_review_kind"] = "identity_resolution"
            substantial["manual_review_priority"] = "medium"
            substantial["collection_observations"] = [{"collection": "app.example.substantial", "count": 5}]
            registry.save_project(conn, substantial)
            conn.commit()

            queue = registry.build_review_queue(conn, tracking_states={"active"}, limit=2)

            self.assertEqual([item["project_key"] for item in queue], ["proj:222222222222", "proj:111111111111"])
            self.assertEqual(queue[0]["atproto_contribution_level"], "substantial")
            self.assertGreater(queue[0]["sort_score"], queue[1]["sort_score"])
        finally:
            conn.close()

    def test_audit_reports_confirmed_identifier_collisions(self) -> None:
        conn = registry.open_db()
        try:
            first = self.make_project("proj:eeeeeeeeeeee", "service_url", "https://collision.example")
            first["name"] = "First"
            first["website_url"] = "https://collision.example"
            registry.save_project(conn, first)

            second = self.make_project("proj:ffffffffffff", "service_url", "https://collision.example")
            second["name"] = "Second"
            second["website_url"] = "https://collision.example"
            registry.save_project(conn, second)
            conn.commit()

            summary = registry.audit_and_repair_identifiers(conn)
            collisions = {
                (item["identifier_type"], item["identifier_value"]): set(item["project_keys"])
                for item in summary["collisions"]
            }

            self.assertIn(("service_url", "https://collision.example"), collisions)
            self.assertEqual(
                collisions[("service_url", "https://collision.example")],
                {"proj:eeeeeeeeeeee", "proj:ffffffffffff"},
            )
        finally:
            conn.close()

    def test_invalid_json_is_not_auto_repaired(self) -> None:
        conn = registry.open_db()
        try:
            now = registry.iso_now()
            conn.execute(
                """
                INSERT INTO projects (
                    project_key, name, website_url, service_status, tracking_state,
                    introduced_url, record_json, ops_json, first_seen_at, last_seen_at, last_enriched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "proj:badbadbadbad",
                    "Broken",
                    "",
                    "unknown",
                    "active",
                    "",
                    "{bad json",
                    "{}",
                    now,
                    now,
                    None,
                ),
            )
            conn.commit()

            with self.assertRaises(json.JSONDecodeError):
                registry.fetch_project(conn, "proj:badbadbadbad")
            with self.assertRaises(json.JSONDecodeError):
                registry.audit_and_repair_identifiers(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
