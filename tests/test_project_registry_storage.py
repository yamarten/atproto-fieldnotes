from __future__ import annotations

import json
import unittest
import datetime as dt
import io
import tempfile
from pathlib import Path
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import cli
from project_registry_core.commands import review as review_commands


class ProjectRegistryStorageTest(TempRegistryDbTestCase):
    def test_sync_parsers_create_unmatched_by_default_with_opt_out(self) -> None:
        parser = cli.build_parser()

        for command in ("sync-collections", "sync-github-search", "sync-semble-collections"):
            self.assertTrue(parser.parse_args([command]).create_unmatched)
            self.assertFalse(parser.parse_args([command, "--no-create-unmatched"]).create_unmatched)

    def test_sync_parsers_are_incremental_by_default(self) -> None:
        parser = cli.build_parser()

        for command in ("sync-introduced", "sync-collections", "sync-github-search", "sync-semble-collections"):
            self.assertTrue(parser.parse_args([command]).incremental)
            self.assertFalse(parser.parse_args([command, "--no-incremental"]).incremental)

        introduced = parser.parse_args(["sync-introduced"])
        self.assertEqual(introduced.lookback_days, 7)
        self.assertEqual(introduced.limit, 10)
        self.assertEqual(parser.parse_args(["sync-semble-collections"]).pages, 100)

    def test_sync_state_roundtrip_is_scoped(self) -> None:
        conn = registry.open_db()
        try:
            first_scope = {"query": "first"}
            second_scope = {"query": "second"}
            registry.write_sync_state(conn, "introduced", first_scope, {"timestamp": "2026-06-01T00:00:00+00:00"})
            conn.commit()

            first = registry.read_sync_state(conn, "introduced", first_scope)
            second = registry.read_sync_state(conn, "introduced", second_scope)
        finally:
            conn.close()

        self.assertEqual(first["watermark"]["timestamp"], "2026-06-01T00:00:00+00:00")
        self.assertEqual(second["watermark"], {})
        self.assertNotEqual(first["scope_key"], second["scope_key"])

    def test_introduced_sync_retains_unmatched_entry_for_later_project(self) -> None:
        conn = registry.open_db()
        query = "from:example.test #atproto"
        entry = {
            "seed_type": "nsid",
            "seed_value": "app.example.post",
            "labels": ["Example"],
            "post_web_urls": ["https://bsky.app/profile/example.test/post/abc"],
        }
        post = {"indexedAt": "2026-06-10T00:00:00Z", "record": {"createdAt": "2026-06-10T00:00:00Z"}}
        seen_since: list[str] = []

        def fake_search(*args, **kwargs):
            seen_since.append(str(kwargs.get("since") or ""))
            return {"posts": [post]}

        try:
            with mock.patch.object(registry, "search_bsky_posts_payload", side_effect=fake_search), mock.patch.object(
                registry,
                "collect_applog_entries",
                return_value={(entry["seed_type"], entry["seed_value"]): entry},
            ):
                first = registry.sync_introduced_entries(conn, query=query, execute=True, lookback_days=7)

            state = registry.read_sync_state(conn, "introduced", {"query": query})
            self.assertEqual(first["unmatched"], 1)
            self.assertEqual(len(state["watermark"]["pending_entries"]), 1)
            self.assertTrue(seen_since[0])

            project = self.make_project("proj:introduced01", "nsid", "app.example.post")
            registry.add_manual_seed(project, "nsid", "app.example.post", source="test")
            registry.save_project(conn, project)
            conn.commit()

            with mock.patch.object(registry, "search_bsky_posts_payload", return_value={"posts": []}):
                second = registry.sync_introduced_entries(conn, query=query, execute=True, lookback_days=7)
            saved = registry.fetch_project(conn, "proj:introduced01")
            state = registry.read_sync_state(conn, "introduced", {"query": query})
        finally:
            conn.close()

        self.assertEqual(second["pending_before"], 1)
        self.assertEqual(second["pending_after"], 0)
        self.assertEqual(saved["introduced_url"], "https://bsky.app/profile/example.test/post/abc")
        self.assertEqual(state["watermark"]["pending_entries"], [])

    def test_introduced_dry_run_does_not_write_sync_state(self) -> None:
        conn = registry.open_db()
        query = "dry-run"
        try:
            with mock.patch.object(registry, "search_bsky_posts_payload", return_value={"posts": []}):
                summary = registry.sync_introduced_entries(conn, query=query, execute=False)
            state = registry.read_sync_state(conn, "introduced", {"query": query})
        finally:
            conn.close()

        self.assertFalse(summary["state_updated"])
        self.assertEqual(state["watermark"], {})

    def test_github_no_create_keeps_watermark_when_unmatched_remains(self) -> None:
        conn = registry.open_db()
        query = "atproto"
        scan = {
            "query": query,
            "results": [
                {
                    "url": "https://github.com/example/new-project",
                    "full_name": "example/new-project",
                    "description": "",
                    "homepage": "",
                    "updated_at": "2026-06-10T00:00:00Z",
                    "stargazers_count": 0,
                    "query": query,
                }
            ],
            "pages": 1,
            "boundary_reached": True,
            "scan_complete": True,
            "newest_timestamp": "2026-06-10T00:00:00+00:00",
        }
        try:
            with mock.patch.object(registry, "fetch_github_search_query_sync_results", return_value=scan):
                summary = registry.sync_github_search(
                    conn,
                    queries=[query],
                    execute=True,
                    create_unmatched=False,
                    pages=1,
                    per_page=10,
                    lookback_days=10,
                )
            state = registry.read_sync_state(conn, "github-search", {"queries": [query]})
        finally:
            conn.close()

        self.assertEqual(summary["unmatched"], 1)
        self.assertFalse(summary["state_updated"])
        self.assertEqual(state["watermark"], {})

    def test_github_screen_failure_does_not_advance_watermark(self) -> None:
        conn = registry.open_db()
        query = "atproto"
        scan = {
            "query": query,
            "results": [
                {
                    "url": "https://github.com/example/screen-failure",
                    "full_name": "example/screen-failure",
                    "description": "",
                    "homepage": "",
                    "updated_at": "2026-06-10T00:00:00Z",
                    "stargazers_count": 0,
                    "query": query,
                }
            ],
            "pages": 1,
            "boundary_reached": True,
            "scan_complete": True,
            "newest_timestamp": "2026-06-10T00:00:00+00:00",
        }
        try:
            with mock.patch.object(registry, "fetch_github_search_query_sync_results", return_value=scan), mock.patch.object(
                registry,
                "screen_github_repo_sync_candidate",
                side_effect=RuntimeError("temporary failure"),
            ):
                summary = registry.sync_github_search(
                    conn,
                    queries=[query],
                    execute=True,
                    create_unmatched=True,
                    pages=1,
                    per_page=10,
                    lookback_days=10,
                    screen_unmatched=True,
                )
            state = registry.read_sync_state(conn, "github-search", {"queries": [query]})
        finally:
            conn.close()

        self.assertEqual(summary["screen_reason_counts"]["screen_failed"], 1)
        self.assertFalse(summary["state_updated"])
        self.assertEqual(state["watermark"], {})

    def test_parser_accepts_defer_identifier_command(self) -> None:
        args = cli.build_parser().parse_args(
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

        self.assertEqual(args.func.__module__, "project_registry_core.commands.decisions")
        self.assertEqual(args.days, 14)

    def test_parser_accepts_defer_promotion_command(self) -> None:
        args = cli.build_parser().parse_args(
            [
                "defer-promotion",
                "--project-key",
                "proj:121212121212",
                "--reason",
                "中核機能が未実装",
            ]
        )

        self.assertEqual(args.func.__module__, "project_registry_core.commands.decisions")
        self.assertEqual(args.days, 7)

    def test_parser_accepts_audit_url_fields_command(self) -> None:
        args = cli.build_parser().parse_args(["audit-url-fields", "--example-limit", "3"])

        self.assertEqual(args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertEqual(args.example_limit, 3)

    def test_parser_accepts_cleanup_app_links_command(self) -> None:
        parser = cli.build_parser()

        cleanup_args = parser.parse_args(["cleanup-app-links", "--execute"])

        self.assertEqual(cleanup_args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertTrue(cleanup_args.execute)

        repair_args = parser.parse_args(["repair-db", "--execute"])

        self.assertEqual(repair_args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertTrue(repair_args.execute)

        prune_args = parser.parse_args([
            "prune-investigation-history",
            "--retention-days",
            "30",
            "--keep-latest-per-project",
            "2",
            "--execute",
            "--vacuum",
        ])

        self.assertEqual(prune_args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertEqual(prune_args.retention_days, 30)
        self.assertEqual(prune_args.keep_latest_per_project, 2)
        self.assertTrue(prune_args.execute)
        self.assertTrue(prune_args.vacuum)

    def test_audit_source_config_parser_is_interactive_by_default(self) -> None:
        parser = cli.build_parser()

        args = parser.parse_args(["audit-source-config", "example.com"])
        dry_run_args = parser.parse_args(["audit-source-config", "example.com", "--no-interactive"])

        self.assertEqual(args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertTrue(args.interactive)
        self.assertFalse(dry_run_args.interactive)

    def test_out_of_scope_tracking_state_is_available_to_cli(self) -> None:
        parser = cli.build_parser()

        state_args = parser.parse_args(
            ["set-tracking-state", "--project-key", "proj:121212121212", "--tracking-state", "out_of_scope"]
        )
        review_args = parser.parse_args(["review-out-of-scope"])
        enrich_args = parser.parse_args(["enrich", "--all", "--include-out-of-scope"])

        self.assertEqual(state_args.tracking_state, "out_of_scope")
        self.assertIs(review_args.func, review_commands.cmd_review_out_of_scope)
        self.assertTrue(enrich_args.include_out_of_scope)

    def test_review_stalled_parser_accepts_freshness_option(self) -> None:
        parser = cli.build_parser()

        args = parser.parse_args(["review-stalled", "--max-enrich-age-days", "21"])

        self.assertEqual(args.max_enrich_age_days, 21)

    def test_promotion_queue_parser_accepts_technical_insight_kind(self) -> None:
        args = cli.build_parser().parse_args(
            ["promotion-queue", "--kinds", "technical_insight_candidate"]
        )

        self.assertEqual(args.kinds, ["technical_insight_candidate"])

    def test_add_configured_multi_tenant_suffix_updates_domain_overrides_and_cache(self) -> None:
        original_config_path = registry.CONFIG_PATH
        original_domain_overrides_path = registry.DOMAIN_OVERRIDES_PATH
        original_suffixes = registry._CONFIGURED_MULTI_TENANT_SUFFIXES
        try:
            sources_path = Path(self.tempdir.name) / "sources.json"
            sources_path.write_text('{"collection_inventory": {}}\n')
            domain_overrides_path = Path(self.tempdir.name) / "domain-overrides.json"
            domain_overrides_path.write_text('{"custom_multi_tenant_suffixes": ["existing.example"]}\n')
            registry.CONFIG_PATH = sources_path
            registry.DOMAIN_OVERRIDES_PATH = domain_overrides_path
            registry.reset_domain_config_cache()

            self.assertTrue(registry.add_configured_multi_tenant_suffix("Example.COM."))
            self.assertFalse(registry.add_configured_multi_tenant_suffix("example.com"))

            config = json.loads(domain_overrides_path.read_text())
            self.assertEqual(config["custom_multi_tenant_suffixes"], ["existing.example", "example.com"])
            self.assertIn("example.com", registry.configured_multi_tenant_suffixes())
            self.assertEqual(json.loads(sources_path.read_text()), {"collection_inventory": {}})
        finally:
            registry.CONFIG_PATH = original_config_path
            registry.DOMAIN_OVERRIDES_PATH = original_domain_overrides_path
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

    def test_interactive_source_config_audit_rechecks_project_after_each_split(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:threetenants", "service_url", "https://foo.example.com")
            for url in (
                "https://foo.example.com",
                "https://bar.example.com",
                "https://baz.example.com",
            ):
                registry.add_manual_seed(project, "service_url", url, source="test")
            registry.save_project(conn, project)
            conn.commit()

            initial_suggestions = registry.build_source_config_split_suggestions(conn, suffixes=["example.com"])
            with (
                mock.patch("builtins.input", side_effect=["1", "1"]),
                mock.patch("sys.stdout", new=io.StringIO()),
            ):
                result = registry.review_source_config_split_suggestions(
                    conn,
                    ["example.com"],
                    initial_suggestions=initial_suggestions,
                )

            source = registry.fetch_project(conn, "proj:threetenants")
            remaining_groups = registry.project_split_identifiers_by_tenant(source, "example.com")
            self.assertEqual(result["split_count"], 2)
            self.assertEqual(
                [item["tenant"] for item in result["split_results"]],
                ["bar.example.com", "baz.example.com"],
            )
            self.assertEqual(set(remaining_groups), {"foo.example.com"})
            self.assertEqual(result["remaining_suggestion_count"], 0)
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

    def test_promotion_deferral_hides_queue_item_until_expiry(self) -> None:
        project = self.make_project("proj:promotionhold", "service_url", "https://example.com")
        project.update({"category": "project", "service_status": "live"})
        registry.confirm_service_seed(project, "https://example.com", source="test")

        registry.defer_promotion_on_project(project, reason="中核機能が未実装")

        self.assertEqual(registry.compute_showcase_signals(project), [])
        project["promotion_deferrals"][0]["expires_at"] = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
        ).isoformat()
        self.assertEqual(
            [signal["kind"] for signal in registry.compute_showcase_signals(project)],
            ["service_ready_for_introduction"],
        )

    def test_out_of_scope_project_is_not_promoted(self) -> None:
        project = self.make_project("proj:outofscope", "service_url", "https://example.com")
        project.update({"category": "project", "service_status": "live", "tracking_state": "out_of_scope"})
        registry.confirm_service_seed(project, "https://example.com", source="test")

        self.assertEqual(registry.compute_showcase_signals(project), [])

    def test_promotion_deferral_reuses_last_reason_and_clear_keeps_history(self) -> None:
        project = self.make_project("proj:promotionhold", "service_url", "https://example.com")

        registry.defer_promotion_on_project(project, reason="中核機能が未実装")
        registry.clear_active_promotion_deferrals(project)
        entry = registry.defer_promotion_on_project(project)

        self.assertEqual(entry["reason"], "中核機能が未実装")
        self.assertEqual(len(project["promotion_deferrals"]), 2)
        self.assertTrue(project["promotion_deferrals"][0]["cleared_at"])
        self.assertEqual(registry.active_promotion_deferral(project)["reason"], "中核機能が未実装")

    def test_save_project_splits_scalars_profile_state_and_normalized_children(self) -> None:
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
            registry.add_resource_url(
                project,
                "https://example.com/docs",
                role="docs",
                source="service_page:https://example.com",
                confidence="confirmed",
            )
            registry.defer_promotion_on_project(project, reason="中核機能が未実装")

            registry.save_project(conn, project, enriched=True)
            conn.commit()

            row = conn.execute("SELECT * FROM projects WHERE project_key = ?", ("proj:aaaaaaaaaaaa",)).fetchone()
            self.assertEqual(row["name"], "Example App")
            self.assertEqual(row["website_url"], "https://example.com")
            self.assertEqual(row["service_status"], "live")
            self.assertEqual(row["tracking_state"], "active")
            self.assertTrue(row["last_enriched_at"])

            profile_payload = json.loads(row["profile_json"])
            state_payload = json.loads(row["state_json"])
            self.assertEqual(profile_payload["core"]["summary"], "Example summary")
            self.assertEqual(profile_payload["core"]["tagline"], "Example tagline")
            self.assertEqual(state_payload["evidence"]["name_candidates"][0]["value"], "Example App")
            self.assertEqual(state_payload["ops"]["service_status_confidence"], "medium")
            self.assertEqual(state_payload["ops"]["atproto_contribution_level"], "core")
            self.assertEqual(state_payload["ops"]["atproto_contribution_score"], 90)
            self.assertEqual(state_payload["ops"]["lexicon_scope"], "service_specific")
            self.assertEqual(state_payload["ops"]["technical_interest_level"], "high")
            self.assertEqual(state_payload["ops"]["technical_interest_score"], 70)
            self.assertNotIn("promotion_deferrals", state_payload["ops"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_decisions WHERE project_key = ?", (project["project_key"],)).fetchone()[0], 1)
            self.assertEqual(state_payload["evidence"]["supported_service_urls"], ["https://example.com"])
            self.assertEqual(state_payload["evidence"]["resource_url_records"][0]["role"], "docs")

            fetched = registry.fetch_project(conn, "proj:aaaaaaaaaaaa")
            self.assertEqual(fetched["summary"], "Example summary")
            self.assertEqual(fetched["name_candidates"][0]["source"], "service_title:https://example.com")
            self.assertEqual(fetched["atproto_contribution_level"], "core")
            self.assertEqual(fetched["lexicon_scope"], "service_specific")
            self.assertEqual(fetched["technical_interest_reasons"], ["novel_design_pattern"])
            self.assertEqual(registry.resource_url_values(fetched), ["https://example.com/docs"])
        finally:
            conn.close()

    def test_cleanup_project_app_links_rebuilds_only_from_canonical_sources(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:applinkcleanup", "service_url", "https://example.com")
            project["website_url"] = "https://example.com"
            project["service_urls"] = ["https://example.com"]
            project["links"] = [
                {"uri": "https://example.com", "role": "website", "official": True},
                {"uri": "https://example.com/old-docs", "role": "docs", "official": True},
            ]
            registry.add_resource_url(
                project,
                "https://example.com/docs",
                role="docs",
                source="service_page:https://example.com",
                confidence="confirmed",
            )
            registry.save_project(conn, project)

            dry_run = registry.cleanup_project_app_links(conn)
            stored_before = registry.fetch_project(conn, "proj:applinkcleanup")
            self.assertEqual(dry_run["mode"], "dry_run")
            self.assertEqual(dry_run["removed_link_count"], 1)
            self.assertEqual(len(stored_before["links"]), 3)

            executed = registry.cleanup_project_app_links(conn, execute=True)
            stored_after = registry.fetch_project(conn, "proj:applinkcleanup")
            self.assertEqual(executed["mode"], "execute")
            self.assertEqual(
                stored_after["links"],
                [
                    {"uri": "https://example.com", "role": "website", "official": True},
                    {"uri": "https://example.com/docs", "role": "docs", "official": True},
                ],
            )
            self.assertEqual(
                registry.cleanup_project_app_links(conn, execute=True)["changed_project_count"],
                0,
            )
        finally:
            conn.close()

    def test_url_field_audit_reports_only_canonical_fields(self) -> None:
        conn = registry.open_db()
        try:
            first = self.make_project("proj:urlfieldaudit1", "service_url", "https://example.com")
            first["website_url"] = "https://example.com"
            first["service_urls"] = ["https://example.com"]
            first["service_url_candidates"] = ["https://candidate.example.com"]
            first["source_code_url"] = "https://github.com/example/app"
            first["source_code_urls"] = ["https://github.com/example/app"]
            registry.save_project(conn, first)

            second = self.make_project("proj:urlfieldaudit2", "service_url", "https://broken.example")
            second["website_url"] = "https://broken.example"
            second["service_url_candidates"] = ["https://two.example"]
            registry.save_project(conn, second)

            summary = registry.build_url_field_audit(conn)
            aliases = {(item["left"], item["right"]): item for item in summary["alias_relationships"]}
            anomalies = {item["kind"]: item for item in summary["anomalies"]}

            self.assertEqual(summary["project_count"], 2)
            self.assertEqual(aliases[("website_url", "service_urls")]["left_subset_projects"], 2)
            self.assertEqual(anomalies["website_url_missing_from_service_urls"]["project_count"], 0)
            self.assertEqual(summary["migration_candidates"], [])
            fields = {item["field"] for item in summary["field_inventory"]}
            self.assertNotIn("website_url_candidates", fields)
            self.assertNotIn("github_repo_urls", fields)
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
            self.assertEqual(summary["mode"], "dry_run")

            repaired = registry.fetch_project(conn, "proj:cccccccccccc")
            self.assertEqual(repaired["duplicate_candidates"], [])
            self.assertEqual(repaired["merge_recommendation"], {})

            executed = registry.audit_and_repair_identifiers(conn, execute=True)
            self.assertEqual(executed["mode"], "execute")

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
                    introduced_url, profile_json, state_json, first_seen_at, last_seen_at, last_enriched_at
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
                    json.dumps({"evidence": {"atproto_contribution_level": "unknown"}}),
                    now,
                    now,
                    None,
                ),
            )
            conn.execute(
                "INSERT INTO project_identifiers(project_key, ordinal, kind, value, role, classification) VALUES (?, ?, ?, ?, ?, ?)",
                ("proj:legacycontri", 0, "nsid", "app.example.post", "input_nsid", "candidate"),
            )
            conn.commit()

            summary = registry.audit_and_repair_identifiers(conn, execute=True)
            self.assertEqual(summary["mode"], "execute")

            row = conn.execute("SELECT state_json FROM projects WHERE project_key = ?", ("proj:legacycontri",)).fetchone()
            state_payload = json.loads(row["state_json"])
            self.assertEqual(state_payload["ops"]["atproto_contribution_level"], "optional")
            self.assertEqual(state_payload["ops"]["atproto_contribution_score"], 40)
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

            args = cli.build_parser().parse_args(["promotion-queue", "--interactive", "--limit", "1"])
            with (
                mock.patch.object(registry, "open_db", return_value=conn),
                mock.patch.object(registry, "output_project_queue", return_value=0) as output,
            ):
                review_commands.cmd_promotion_queue(args)

            queue = output.call_args.kwargs["queue"]
            self.assertEqual([item["project_key"] for item in queue], ["proj:zzzzzzzzzzzz"])
            self.assertIsNone(output.call_args.kwargs["load_more"])
        finally:
            conn.close()

    def test_interactive_promotion_queue_can_hold_item_for_seven_days(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:promotionhold", "service_url", "https://example.com")
            project.update({"category": "project", "service_status": "live"})
            registry.confirm_service_seed(project, "https://example.com", source="test")
            registry.save_project(conn, project)
            conn.commit()
            queue = registry.build_showcase_queue(conn, tracking_states={"active"})
            stdout = io.StringIO()
            stderr = io.StringIO()
            stdout.isatty = lambda: True

            with (
                mock.patch.object(registry.sys, "stdout", stdout),
                mock.patch.object(registry.sys, "stderr", stderr),
                mock.patch.object(registry.sys.stdin, "isatty", return_value=True),
                mock.patch("builtins.input", side_effect=["h", "中核機能が未実装"]),
            ):
                registry.output_project_queue(
                    conn,
                    queue_name="promotion",
                    queue=queue,
                    tracking_states={"active"},
                    kinds=None,
                    interactive=True,
                )

            fetched = registry.fetch_project(conn, "proj:promotionhold")
            active = registry.active_promotion_deferral(fetched)
            self.assertEqual(active["reason"], "中核機能が未実装")
            self.assertGreater(
                registry.parse_iso(active["expires_at"]),
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=6),
            )
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
                    introduced_url, profile_json, state_json, first_seen_at, last_seen_at, last_enriched_at
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
