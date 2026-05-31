from __future__ import annotations

import json
import unittest
import datetime as dt
import tempfile
from pathlib import Path
from unittest import mock

from helpers import TempRegistryDbTestCase, registry


class ProjectRegistryStorageTest(TempRegistryDbTestCase):
    def test_sync_parsers_create_unmatched_by_default_with_opt_out(self) -> None:
        parser = registry.build_parser()

        for command in ("sync-collections", "sync-github-search", "sync-semble-collections"):
            self.assertTrue(parser.parse_args([command]).create_unmatched)
            self.assertFalse(parser.parse_args([command, "--no-create-unmatched"]).create_unmatched)

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

    def test_audit_source_config_parser_is_interactive_by_default(self) -> None:
        parser = registry.build_parser()

        args = parser.parse_args(["audit-source-config", "example.com"])
        dry_run_args = parser.parse_args(["audit-source-config", "example.com", "--no-interactive"])

        self.assertIs(args.func, registry.cmd_audit_source_config)
        self.assertTrue(args.interactive)
        self.assertFalse(dry_run_args.interactive)

    def test_promotion_queue_parser_accepts_technical_insight_kind(self) -> None:
        args = registry.build_parser().parse_args(
            ["promotion-queue", "--kinds", "technical_insight_candidate"]
        )

        self.assertEqual(args.kinds, ["technical_insight_candidate"])

    def test_add_configured_multi_tenant_suffix_updates_json_and_cache(self) -> None:
        original_config_path = registry.CONFIG_PATH
        original_suffixes = registry._CONFIGURED_MULTI_TENANT_SUFFIXES
        try:
            config_path = Path(self.tempdir.name) / "sources.json"
            config_path.write_text('{"custom_multi_tenant_suffixes": ["existing.example"]}\n')
            registry.CONFIG_PATH = config_path
            registry.reset_domain_config_cache()

            self.assertTrue(registry.add_configured_multi_tenant_suffix("Example.COM."))
            self.assertFalse(registry.add_configured_multi_tenant_suffix("example.com"))

            config = json.loads(config_path.read_text())
            self.assertEqual(config["custom_multi_tenant_suffixes"], ["existing.example", "example.com"])
            self.assertIn("example.com", registry.configured_multi_tenant_suffixes())
        finally:
            registry.CONFIG_PATH = original_config_path
            registry._CONFIGURED_MULTI_TENANT_SUFFIXES = original_suffixes
            registry.reset_domain_extractors()

    def test_source_config_audit_suggests_multi_tenant_project_split(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:tenanttenant", "service_url", "https://foo.example.com")
            project["name"] = "Mixed tenants"
            project["website_url"] = "https://foo.example.com"
            project["service_urls"] = ["https://foo.example.com", "https://bar.example.com"]
            project["input_nsids"] = ["com.example.foo.post", "com.example.bar.post"]
            registry.save_project(conn, project)
            conn.commit()

            suggestions = registry.build_source_config_split_suggestions(conn, suffixes=["example.com"])

            self.assertEqual(len(suggestions), 1)
            self.assertEqual(suggestions[0]["source_project_key"], "proj:tenanttenant")
            tenant_groups = {item["tenant"]: item for item in suggestions[0]["tenant_groups"]}
            self.assertEqual(set(tenant_groups), {"foo.example.com", "bar.example.com"})
            self.assertIn("https://foo.example.com", tenant_groups["foo.example.com"]["identifiers"]["service_urls"])
            self.assertTrue(tenant_groups["foo.example.com"]["suggested_split_commands"])
        finally:
            conn.close()

    def test_source_config_tenant_split_moves_group_atomically(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:tenanttenant", "service_url", "https://foo.example.com")
            registry.add_manual_seed(project, "service_url", "https://foo.example.com", source="test")
            registry.add_manual_seed(project, "service_url", "https://bar.example.com", source="test")
            registry.add_manual_seed(project, "nsid", "com.example.foo.post", source="test")
            registry.add_manual_seed(project, "nsid", "com.example.bar.post", source="test")
            registry.save_project(conn, project)
            conn.commit()

            result = registry.split_project_tenant_group(
                conn,
                "proj:tenanttenant",
                "bar.example.com",
                {
                    "service_urls": ["https://bar.example.com"],
                    "nsids": ["com.example.bar.post"],
                    "authority_domains": [],
                },
            )

            source = registry.fetch_project(conn, "proj:tenanttenant")
            target = registry.fetch_project(conn, result["target_project_key"])
            self.assertNotIn("https://bar.example.com", source["service_urls"])
            self.assertNotIn("com.example.bar.post", source["input_nsids"])
            self.assertIn("https://bar.example.com", target["service_urls"])
            self.assertIn("com.example.bar.post", target["input_nsids"])
            self.assertIn(target["project_key"], source["separate_project_keys"])
            self.assertIn(source["project_key"], target["separate_project_keys"])
            blocked = {(item["type"], item["value"]) for item in source["blocked_identifiers"]}
            self.assertIn(("service_url", "https://bar.example.com"), blocked)
            self.assertIn(("nsid", "com.example.bar.post"), blocked)
        finally:
            conn.close()

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
            project["supported_service_urls"] = ["https://example.com"]
            project["resolved_nsids"] = ["app.example.post"]
            project["technical_interest_level"] = "high"
            project["technical_interest_reasons"] = ["novel_design_pattern"]
            project["technical_interest_summary"] = "新しい設計パターン。"

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
            self.assertEqual(ops_payload["ops"]["lexicon_scope"], "service_specific")
            self.assertEqual(ops_payload["ops"]["technical_interest_level"], "high")
            self.assertEqual(ops_payload["ops"]["technical_interest_score"], 70)
            self.assertEqual(ops_payload["evidence"]["supported_service_urls"], ["https://example.com"])

            fetched = registry.fetch_project(conn, "proj:aaaaaaaaaaaa")
            self.assertEqual(fetched["summary"], "Example summary")
            self.assertEqual(fetched["name_candidates"][0]["source"], "service_title:https://example.com")
            self.assertEqual(fetched["atproto_contribution_level"], "core")
            self.assertEqual(fetched["lexicon_scope"], "service_specific")
            self.assertEqual(fetched["technical_interest_reasons"], ["novel_design_pattern"])
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

    def test_interactive_promotion_queue_applies_limit_after_global_sort(self) -> None:
        conn = registry.open_db()
        try:
            low = self.make_project("proj:111111111111", "service_url", "https://low.example")
            low.update(
                {
                    "name": "Low",
                    "category": "project",
                    "service_status": "live",
                    "summary": "Low summary",
                    "tagline": "Low tagline",
                }
            )
            registry.confirm_service_seed(low, "https://low.example", source="test")
            registry.save_project(conn, low)

            high = self.make_project("proj:zzzzzzzzzzzz", "service_url", "https://high.example")
            high.update(
                {
                    "name": "High",
                    "category": "project",
                    "service_status": "live",
                    "summary": "High summary",
                    "tagline": "High tagline",
                    "resolved_nsids": ["app.high.post"],
                }
            )
            registry.confirm_service_seed(high, "https://high.example", source="test")
            registry.save_project(conn, high)
            conn.commit()

            args = registry.build_parser().parse_args(["promotion-queue", "--interactive", "--limit", "1"])
            with (
                mock.patch.object(registry, "open_db", return_value=conn),
                mock.patch.object(registry, "output_project_queue", return_value=0) as output,
            ):
                registry.cmd_promotion_queue(args)

            queue = output.call_args.kwargs["queue"]
            self.assertEqual([item["project_key"] for item in queue], ["proj:zzzzzzzzzzzz"])
            self.assertIsNone(output.call_args.kwargs["load_more"])
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
