from __future__ import annotations

import json
import unittest

from helpers import TempRegistryDbTestCase, registry


class ProjectRegistryStorageTest(TempRegistryDbTestCase):
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

            fetched = registry.fetch_project(conn, "proj:aaaaaaaaaaaa")
            self.assertEqual(fetched["summary"], "Example summary")
            self.assertEqual(fetched["name_candidates"][0]["source"], "service_title:https://example.com")
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
