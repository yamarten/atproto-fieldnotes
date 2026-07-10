from __future__ import annotations

import datetime as dt
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import TempRegistryDbTestCase, captured_stdout, registry
from project_registry_core import cli, database, domain_policy
from project_registry_core.commands import assist as assist_commands
from project_registry_core.commands import maintenance as maintenance_commands
from project_registry_core.commands import merge as merge_commands
from project_registry_core.commands import review as review_commands


class ProjectRegistryStorageTest(TempRegistryDbTestCase):
    def test_split_project_parser_has_no_explicit_new_key_option(self) -> None:
        args = cli.build_parser().parse_args([
            "split-project",
            "--project-key",
            "proj:source",
            "--identifier-type",
            "nsid",
            "--value",
            "app.example.post",
        ])

        self.assertFalse(hasattr(args, "new_project_key"))

    def test_public_wrappers_reject_unsupported_python_before_import(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        for wrapper_name in ("project-registry", "project-registry-web"):
            with self.subTest(wrapper=wrapper_name):
                fake_root = Path(self.tempdir.name) / wrapper_name
                wrapper_path = fake_root / "bin" / wrapper_name
                python_path = fake_root / ".venv" / "bin" / "python"
                wrapper_path.parent.mkdir(parents=True)
                python_path.parent.mkdir(parents=True)
                shutil.copy2(source_root / "bin" / wrapper_name, wrapper_path)
                python_path.write_text(
                    """#!/bin/sh
case "$2" in
  *sys.version_info*) exit 1 ;;
  *platform.python_version*) printf '3.9.18\\n'; exit 0 ;;
esac
exit 99
""",
                    encoding="utf-8",
                )
                python_path.chmod(0o755)

                result = subprocess.run(
                    [str(wrapper_path), "--help"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn("requires Python 3.10 or newer (found 3.9.18)", result.stderr)
                self.assertNotIn("SyntaxError", result.stderr)

    def test_init_db_parser_accepts_custom_path(self) -> None:
        args = cli.build_parser().parse_args(["init-db", "--db-path", "custom.db"])
        self.assertEqual(args.db_path, Path("custom.db"))

    def test_init_db_command_explicitly_creates_custom_database(self) -> None:
        custom_path = Path(self.tempdir.name) / "custom" / "survey.db"
        args = cli.build_parser().parse_args(["init-db", "--db-path", str(custom_path)])

        with captured_stdout() as output:
            result = maintenance_commands.cmd_init_db(args)

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["db_path"], str(custom_path.resolve()))
        conn = database.open_database(custom_path, create=False)
        try:
            self.assertEqual(database.database_schema_version(conn), database.SCHEMA_VERSION)
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'investigation_runs'"
            ).fetchone())
        finally:
            conn.close()

    def test_open_db_uses_busy_timeout_and_wal(self) -> None:
        conn = registry.open_db()
        try:
            busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            conn.close()

        self.assertGreaterEqual(busy_timeout_ms, 30000)
        self.assertEqual(journal_mode, "wal")

    def test_explain_parser_accepts_topic_and_format(self) -> None:
        args = cli.build_parser().parse_args([
            "explain", "--project-key", "proj:abc", "--topic", "ownership", "--format", "json"
        ])
        self.assertEqual(args.topic, "ownership")
        self.assertEqual(args.format, "json")

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

    def test_collection_import_attaches_by_member_parent_namespace(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:homeport", "nsid", "social.homeport.activitypub.announce")
            parent_only_project = dict(project)
            parent_only_project["primary_nsid"] = ""
            parent_only_project["input_nsids"] = []
            parent_only_project["resolved_nsids"] = []
            parent_only_project["recorded_lexicon_namespaces"] = []
            self.assertEqual(
                registry.safe_collection_member_namespace_match(
                    [parent_only_project],
                    ["social.homeport.activitypub.note"],
                ),
                ("proj:homeport", "member_seed_parent_namespace"),
            )
            registry.add_manual_seed(project, "nsid", "social.homeport.activitypub.announce", source="test")
            registry.save_project(conn, project)

            result = registry.import_collection_inventory_payload(
                conn,
                [
                    {
                        "collection": "social.homeport.activitypub.note",
                        "count": 10,
                        "recent_count": 2,
                        "min": "2026-07-01T00:00:00+00:00",
                        "max": "2026-07-04T00:00:00+00:00",
                    }
                ],
                execute=True,
                create_unmatched=True,
                source="test_collection_inventory",
            )
            fetched = registry.fetch_project(conn, "proj:homeport")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 1)
        self.assertIn("social.homeport.activitypub.note", fetched["input_nsids"])
        self.assertIn("social.homeport.activitypub.note", [item["value"] for item in fetched["manual_seeds"]])

    def test_collection_import_attaches_all_aggregated_members(self) -> None:
        conn = registry.open_db()
        try:
            result = registry.import_collection_inventory_payload(
                conn,
                [
                    {
                        "collection": "link.latr.saved.external",
                        "count": 10,
                        "recent_count": 2,
                        "min": "2026-07-01T00:00:00+00:00",
                        "max": "2026-07-04T00:00:00+00:00",
                    },
                    {
                        "collection": "link.latr.saved.item",
                        "count": 3,
                        "recent_count": 1,
                        "min": "2026-07-02T00:00:00+00:00",
                        "max": "2026-07-05T00:00:00+00:00",
                    },
                ],
                execute=True,
                create_unmatched=True,
                source="test_collection_inventory",
            )
            fetched = registry.fetch_project(conn, result["imported_project_keys"][0])
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(result["created"], 1)
        self.assertEqual(fetched["collection_observations"][0]["members"], ["link.latr.saved.external", "link.latr.saved.item"])
        self.assertIn("link.latr.saved.external", fetched["input_nsids"])
        self.assertIn("link.latr.saved.item", fetched["input_nsids"])
        self.assertIn("link.latr.saved.external", [item["value"] for item in fetched["manual_seeds"]])
        self.assertIn("link.latr.saved.item", [item["value"] for item in fetched["manual_seeds"]])

    def test_repair_db_attaches_missing_collection_observation_members(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:latr", "nsid", "link.latr.saved.external")
            registry.add_manual_seed(project, "nsid", "link.latr.saved.external", source="collection_inventory_import")
            project["collection_observations"] = [
                {
                    "collection": "link.latr",
                    "count": 13,
                    "recent_count": 3,
                    "min": "2026-07-01T00:00:00+00:00",
                    "max": "2026-07-05T00:00:00+00:00",
                    "members": ["link.latr.saved.external", "link.latr.saved.item"],
                    "source": "collection_inventory_import",
                }
            ]
            registry.save_project(conn, project)
            dry_run = registry.audit_and_repair_identifiers(conn, execute=False)
            before = registry.fetch_project(conn, "proj:latr")
            executed = registry.audit_and_repair_identifiers(conn, execute=True)
            after = registry.fetch_project(conn, "proj:latr")
        finally:
            conn.close()

        assert before is not None
        assert after is not None
        self.assertEqual(dry_run["collection_member_seed_repair_count"], 1)
        self.assertNotIn("link.latr.saved.item", before["input_nsids"])
        self.assertEqual(executed["collection_member_seed_repair_count"], 1)
        self.assertIn("link.latr.saved.item", after["input_nsids"])
        self.assertIn("link.latr.saved.item", [item["value"] for item in after["manual_seeds"]])

    def test_collection_import_does_not_attach_by_service_host_family(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:bakery", "service_url", "https://bakery.minomobi.com")
            self.assertEqual(
                registry.safe_collection_member_namespace_match(
                    [project],
                    ["com.minomobi.hoop.story.pulse"],
                ),
                (None, ""),
            )
            registry.add_manual_seed(project, "service_url", "https://bakery.minomobi.com", source="test")
            registry.save_project(conn, project)

            result = registry.import_collection_inventory_payload(
                conn,
                [
                    {
                        "collection": "com.minomobi.hoop.story.pulse",
                        "count": 10,
                        "recent_count": 2,
                        "min": "2026-07-01T00:00:00+00:00",
                        "max": "2026-07-04T00:00:00+00:00",
                    }
                ],
                execute=True,
                create_unmatched=True,
                source="test_collection_inventory",
            )
            fetched = registry.fetch_project(conn, "proj:bakery")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(fetched["input_nsids"], [])

    def test_semble_subpage_attaches_as_evidence_without_seed(self) -> None:
        conn = registry.open_db()
        original_tracked_collections = registry.SEMBLE_TRACKED_COLLECTIONS
        try:
            project = self.make_project("proj:cred", "service_url", "https://cred.blue")
            registry.add_manual_seed(project, "service_url", "https://cred.blue", source="test")
            registry.save_project(conn, project)
            registry.SEMBLE_TRACKED_COLLECTIONS = (
                {"handle": "example.test", "rkey": "collection", "label": "apps"},
            )
            scan = {
                "source_key": "example.test/collection",
                "results": [],
                "evidence_only": [
                    {
                        "evidence_url": "https://cred.blue/gov.glados.computer",
                        "root_service_url": "https://cred.blue",
                        "reason": "semble_service_subpage",
                        "card_uri": "at://did:plc:example/network.cosmik.card/card123",
                        "card_updated_at": "2026-06-01T00:00:00Z",
                        "card_created_at": "2026-05-30T00:00:00Z",
                        "title": "cred.blue",
                        "description": "Score page",
                        "collection_label": "apps",
                        "collection_rkey": "collection",
                    }
                ],
                "pages": 1,
                "fetched_cards": 1,
                "skipped_cards": 0,
                "ordered_desc": True,
                "boundary_reached": False,
                "scan_complete": True,
                "newest_timestamp": "2026-06-01T00:00:00+00:00",
            }
            with mock.patch.object(registry, "fetch_semble_collection_source_sync_results", return_value=scan):
                result = registry.sync_semble_collections(
                    conn,
                    execute=True,
                    create_unmatched=True,
                    pages=1,
                    per_page=20,
                    lookback_days=None,
                    incremental=False,
                )
            fetched = registry.fetch_project(conn, "proj:cred")
        finally:
            registry.SEMBLE_TRACKED_COLLECTIONS = original_tracked_collections
            conn.close()

        assert fetched is not None
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["evidence_only_attached"], 1)
        self.assertIn("https://cred.blue/gov.glados.computer", fetched["evidence_urls"])
        self.assertIn("at://did:plc:example/network.cosmik.card/card123", fetched["evidence_urls"])
        self.assertNotIn("https://cred.blue/gov.glados.computer", [item["value"] for item in fetched["manual_seeds"]])
        self.assertNotIn("https://cred.blue/gov.glados.computer", fetched["service_url_candidates"])

    def test_semble_candidate_service_match_is_suggestion_only(self) -> None:
        conn = registry.open_db()
        original_tracked_collections = registry.SEMBLE_TRACKED_COLLECTIONS
        try:
            project = self.make_project("proj:candidate", "repo_url", "https://github.com/example/candidate")
            registry.set_service_url_candidate_values(project, ["https://candidate.example"])
            registry.save_project(conn, project)
            registry.SEMBLE_TRACKED_COLLECTIONS = (
                {"handle": "example.test", "rkey": "collection", "label": "apps"},
            )
            scan = {
                "source_key": "example.test/collection",
                "results": [{
                    "seed_type": "service_url",
                    "seed_value": "https://candidate.example",
                    "raw_url": "https://candidate.example",
                    "card_uri": "at://did:plc:example/network.cosmik.card/root",
                    "card_updated_at": "2026-06-01T00:00:00Z",
                    "card_created_at": "2026-05-30T00:00:00Z",
                    "title": "Candidate",
                    "description": "Candidate root",
                    "collection_label": "apps",
                    "collection_rkey": "collection",
                }],
                "evidence_only": [{
                    "evidence_url": "https://candidate.example/detail",
                    "root_service_url": "https://candidate.example",
                    "reason": "semble_service_subpage",
                    "card_uri": "at://did:plc:example/network.cosmik.card/detail",
                    "card_updated_at": "2026-06-01T00:00:00Z",
                    "card_created_at": "2026-05-30T00:00:00Z",
                    "title": "Candidate detail",
                    "description": "Candidate detail page",
                    "collection_label": "apps",
                    "collection_rkey": "collection",
                }],
                "pages": 1,
                "fetched_cards": 2,
                "skipped_cards": 0,
                "ordered_desc": True,
                "boundary_reached": False,
                "scan_complete": True,
                "newest_timestamp": "2026-06-01T00:00:00+00:00",
            }
            with mock.patch.object(registry, "fetch_semble_collection_source_sync_results", return_value=scan):
                result = registry.sync_semble_collections(
                    conn,
                    execute=True,
                    create_unmatched=False,
                    pages=1,
                    per_page=20,
                    lookback_days=None,
                    incremental=False,
                )
            fetched = registry.fetch_project(conn, "proj:candidate")
        finally:
            registry.SEMBLE_TRACKED_COLLECTIONS = original_tracked_collections
            conn.close()

        assert fetched is not None
        self.assertEqual(result["matched_existing"], 0)
        self.assertEqual(result["unmatched"], 1)
        self.assertEqual(result["evidence_only_attached"], 0)
        self.assertEqual(result["evidence_only_unmatched"], 1)
        self.assertEqual(result["suggested_existing_matches"], 2)
        self.assertTrue(all(item["suggested_project_key"] == "proj:candidate" for item in result["suggested_entries"]))
        self.assertNotIn("https://candidate.example/detail", fetched["evidence_urls"])
        self.assertNotIn(
            "https://candidate.example",
            [item["value"] for item in fetched["manual_seeds"]],
        )

    def test_cleanup_sync_notes_removes_only_machine_generated_lines(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:syncnotes", "repo_url", "https://github.com/example/app")
        project["notes"] = "\n".join([
            "operator note",
            "github search sync matched query=atproto updated_at=2026-06-10T00:00:00Z stars=1",
            "collection import matched by exact_namespace_scan: app.example",
            "keep this sync-related manual note",
            "semble collection sync matched atmosphere-apps updated_at=2026-06-10T00:00:00Z",
        ])
        registry.save_project(conn, project)
        try:
            dry_run = registry.cleanup_project_sync_notes(conn, execute=False)
            self.assertEqual(dry_run["changed_project_count"], 1)
            self.assertEqual(dry_run["removed_line_count"], 3)

            result = registry.cleanup_project_sync_notes(conn, execute=True)
            saved = registry.fetch_project(conn, "proj:syncnotes")
        finally:
            conn.close()

        self.assertEqual(result["changed_project_count"], 1)
        self.assertEqual(saved["notes"], "operator note\nkeep this sync-related manual note")

    def test_cleanup_sync_notes_parser(self) -> None:
        args = cli.build_parser().parse_args(["cleanup-sync-notes", "--execute", "--example-limit", "3"])
        self.assertTrue(args.execute)
        self.assertEqual(args.example_limit, 3)
        self.assertEqual(args.func.__name__, "cmd_cleanup_sync_notes")

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

    def test_github_create_unmatched_keeps_operator_notes_empty(self) -> None:
        conn = registry.open_db()
        query = "atproto"
        repo_url = "https://github.com/example/new-project"
        scan = {
            "query": query,
            "results": [
                {
                    "url": repo_url,
                    "full_name": "example/new-project",
                    "description": "",
                    "homepage": "",
                    "updated_at": "2026-06-10T00:00:00Z",
                    "stargazers_count": 12,
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
                    create_unmatched=True,
                    pages=1,
                    per_page=10,
                    lookback_days=10,
                )
            projects = registry.all_projects(conn)
        finally:
            conn.close()

        self.assertEqual(summary["created"], 1)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["notes"], "")
        self.assertIn("github_repo_search_sync", projects[0]["discovery_sources"])
        self.assertEqual(projects[0]["manual_seeds"][0]["value"], repo_url)

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
        self.assertIsNone(args.days)

    def test_parser_accepts_audit_url_fields_command(self) -> None:
        args = cli.build_parser().parse_args(["audit-url-fields", "--example-limit", "3"])

        self.assertEqual(args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertEqual(args.example_limit, 3)

    def test_parser_accepts_cleanup_app_links_command(self) -> None:
        parser = cli.build_parser()

        cleanup_args = parser.parse_args(["cleanup-app-links", "--execute"])

        self.assertEqual(cleanup_args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertTrue(cleanup_args.execute)

        storage_cleanup_args = parser.parse_args([
            "cleanup-storage-payloads",
            "--execute",
            "--example-limit",
            "3",
            "--vacuum",
        ])

        self.assertEqual(
            storage_cleanup_args.func.__module__,
            "project_registry_core.commands.maintenance",
        )
        self.assertTrue(storage_cleanup_args.execute)
        self.assertEqual(storage_cleanup_args.example_limit, 3)
        self.assertTrue(storage_cleanup_args.vacuum)

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

    def test_interactive_stalled_review_runs_from_command_adapter(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:interactive-stalled")
            registry.save_project(conn, project)
            conn.commit()
            args = cli.build_parser().parse_args(["review-stalled", "--interactive"])
            stdout = io.StringIO()
            stderr = io.StringIO()
            stdout.isatty = lambda: True
            with (
                mock.patch.object(registry, "open_db", return_value=conn),
                mock.patch.object(
                    registry,
                    "stalled_review_entry",
                    return_value={
                        "project_key": project["project_key"],
                        "name": "Example",
                        "suggested_tracking_state": "stalled",
                    },
                ),
                mock.patch.object(review_commands.sys, "stdout", stdout),
                mock.patch.object(review_commands.sys, "stderr", stderr),
                mock.patch.object(review_commands.sys.stdin, "isatty", return_value=True),
                mock.patch("builtins.input", return_value="y"),
            ):
                result = review_commands.cmd_review_stalled(args)

            updated = registry.fetch_project(conn, project["project_key"])
            assert updated is not None
            self.assertEqual(result, 0)
            self.assertEqual(updated["tracking_state"], "stalled")
        finally:
            conn.close()

    def test_interactive_ignore_review_runs_from_command_adapter(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:interactive-ignore")
            registry.save_project(conn, project)
            conn.commit()
            args = cli.build_parser().parse_args(["review-ignore", "--interactive", "--limit", "1"])
            stdout = io.StringIO()
            stderr = io.StringIO()
            stdout.isatty = lambda: True
            with (
                mock.patch.object(registry, "open_db", return_value=conn),
                mock.patch.object(
                    registry,
                    "ignore_review_entry",
                    return_value={
                        "project_key": project["project_key"],
                        "name": "Example",
                        "kind": "fork_without_independent_identity",
                        "suggested_tracking_state": "ignore",
                    },
                ),
                mock.patch.object(review_commands.sys, "stdout", stdout),
                mock.patch.object(review_commands.sys, "stderr", stderr),
                mock.patch.object(review_commands.sys.stdin, "isatty", return_value=True),
                mock.patch("builtins.input", side_effect=["y", "", ""]),
            ):
                result = review_commands.cmd_review_ignore(args)

            updated = registry.fetch_project(conn, project["project_key"])
            assert updated is not None
            self.assertEqual(result, 0)
            self.assertEqual(updated["tracking_state"], "ignore")
            self.assertEqual(updated["tracking_note"], "review-ignore: 独自性の薄いfork")
        finally:
            conn.close()

    def test_interactive_merge_batch_runs_from_command_adapter(self) -> None:
        conn = registry.open_db()
        try:
            source = self.make_project("proj:merge-batch-source", "service_url", "https://source.example")
            target = self.make_project("proj:merge-batch-target", "service_url", "https://target.example")
            registry.save_project(conn, source)
            registry.save_project(conn, target)
            conn.commit()
            args = cli.build_parser().parse_args(["merge-batch", "--interactive", "--max-merges", "1"])
            stdout = io.StringIO()
            stderr = io.StringIO()
            stdout.isatty = lambda: True
            plan = {
                "source_project_key": source["project_key"],
                "target_project_key": target["project_key"],
                "reasons": {"manual_test": ["same fixture"]},
            }
            with (
                mock.patch.object(registry, "open_db", return_value=conn),
                mock.patch.object(registry, "build_batch_merge_plan", return_value=([plan], [])),
                mock.patch.object(merge_commands.sys, "stdout", stdout),
                mock.patch.object(merge_commands.sys, "stderr", stderr),
                mock.patch.object(merge_commands.sys.stdin, "isatty", return_value=True),
                mock.patch("builtins.input", return_value="n"),
            ):
                result = merge_commands.cmd_merge_batch(args)

            self.assertIs(args.func, merge_commands.cmd_merge_batch)
            self.assertEqual(result, 0)
            self.assertIsNotNone(registry.fetch_project(conn, source["project_key"]))
            self.assertIsNotNone(registry.fetch_project(conn, target["project_key"]))
            self.assertIn('"planned_merges": 1', stdout.getvalue())
            self.assertIn('"executed_merges": 0', stdout.getvalue())
        finally:
            conn.close()

    def test_assisted_tracking_edit_runs_from_command_adapter(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:assist-adapter")
            registry.save_project(conn, project)
            conn.commit()
            args = cli.build_parser().parse_args([
                "assist-db-edit",
                "set fixture stable",
                "--execute",
            ])
            with (
                mock.patch.object(registry, "open_db", return_value=conn),
                mock.patch.object(
                    registry,
                    "parse_db_edit_request",
                    return_value=(
                        {
                            "action": "set_tracking_state",
                            "project_query": project["project_key"],
                            "tracking_state": "stable",
                            "note": "adapter characterization",
                        },
                        {"transport": "fixture"},
                    ),
                ),
                captured_stdout() as stdout,
            ):
                result = assist_commands.cmd_assist_db_edit(args)

            updated = registry.fetch_project(conn, project["project_key"])
            assert updated is not None
            payload = json.loads(stdout.getvalue())
            self.assertIs(args.func, assist_commands.cmd_assist_db_edit)
            self.assertEqual(result, 0)
            self.assertEqual(payload["mode"], "execute")
            self.assertEqual(updated["tracking_state"], "stable")
            self.assertEqual(updated["tracking_note"], "adapter characterization")
        finally:
            conn.close()

    def test_promotion_queue_parser_accepts_technical_insight_kind(self) -> None:
        args = cli.build_parser().parse_args(
            ["promotion-queue", "--kinds", "technical_insight_candidate"]
        )

        self.assertEqual(args.kinds, ["technical_insight_candidate"])

    def test_add_configured_multi_tenant_suffix_updates_domain_overrides_and_cache(self) -> None:
        original_config_path = registry.CONFIG_PATH
        original_domain_overrides_path = domain_policy.DOMAIN_OVERRIDES_PATH
        original_suffixes = domain_policy._CONFIGURED_MULTI_TENANT_SUFFIXES
        try:
            sources_path = Path(self.tempdir.name) / "sources.json"
            sources_path.write_text('{"collection_inventory": {}}\n')
            domain_overrides_path = Path(self.tempdir.name) / "domain-overrides.json"
            domain_overrides_path.write_text('{"custom_multi_tenant_suffixes": ["existing.example"]}\n')
            registry.CONFIG_PATH = sources_path
            domain_policy.DOMAIN_OVERRIDES_PATH = domain_overrides_path
            domain_policy.reset_domain_config_cache()

            self.assertTrue(domain_policy.add_configured_multi_tenant_suffix("Example.COM."))
            self.assertFalse(domain_policy.add_configured_multi_tenant_suffix("example.com"))

            config = json.loads(domain_overrides_path.read_text())
            self.assertEqual(config["custom_multi_tenant_suffixes"], ["existing.example", "example.com"])
            self.assertIn("example.com", domain_policy.configured_multi_tenant_suffixes())
            self.assertEqual(json.loads(sources_path.read_text()), {"collection_inventory": {}})
        finally:
            registry.CONFIG_PATH = original_config_path
            domain_policy.DOMAIN_OVERRIDES_PATH = original_domain_overrides_path
            domain_policy._CONFIGURED_MULTI_TENANT_SUFFIXES = original_suffixes
            domain_policy.reset_domain_extractors()

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

    def test_merge_pair_suggests_unconfigured_multi_tenant_suffix(self) -> None:
        source = self.make_project("proj:foo", "service_url", "https://foo.example.com")
        registry.add_manual_seed(source, "service_url", "https://foo.example.com", source="test")
        target = self.make_project("proj:bar", "service_url", "https://bar.example.com")
        registry.add_manual_seed(target, "service_url", "https://bar.example.com", source="test")

        suggestions = registry.merge_pair_multi_tenant_suffix_suggestions(source, target)

        self.assertEqual([item["suffix"] for item in suggestions], ["example.com"])
        self.assertEqual(suggestions[0]["source_tenants"], ["foo.example.com"])
        self.assertEqual(suggestions[0]["target_tenants"], ["bar.example.com"])

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

    def test_manual_repo_seed_stays_confirmed_source_repository(self) -> None:
        project = self.make_project("proj:manualrepo", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")

        project = registry.finalize_project_shape(project)

        self.assertIn("https://github.com/example/app", project["source_code_urls"])
        self.assertNotIn("https://github.com/example/app", project["candidate_source_code_urls"])
        self.assertIn(
            {"kind": "repo_url", "value": "https://github.com/example/app"},
            registry.trusted_anchor_nodes(project),
        )

    def test_manual_repo_seed_candidate_shape_is_repaired(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:oldrepo", "repo_url", "https://github.com/example/app")
            project["manual_seeds"] = [
                {"type": "repo_url", "value": "https://github.com/example/app", "added_at": "2026-01-01T00:00:00+00:00"}
            ]
            project["source_code_urls"] = []
            project["candidate_source_code_urls"] = ["https://github.com/example/app"]
            project["source_repo_records"] = [
                {
                    "url": "https://github.com/example/app",
                    "provider": "github",
                    "confidence": "candidate",
                    "relation": "repo_seed",
                    "source": "github_repo_seed",
                    "reason": "registered manually as repository seed",
                    "evidence_kind": "manual_seed",
                    "evidence_url": "https://github.com/example/app",
                    "recorded_at": "2026-01-01T00:00:00+00:00",
                }
            ]
            registry.save_project(conn, project)
            before = registry.fetch_project(conn, "proj:oldrepo")
            dry_run = registry.audit_and_repair_identifiers(conn, execute=False)
            executed = registry.audit_and_repair_identifiers(conn, execute=True)
            after = registry.fetch_project(conn, "proj:oldrepo")
        finally:
            conn.close()

        assert before is not None
        assert after is not None
        self.assertNotIn("https://github.com/example/app", before["source_code_urls"])
        self.assertNotIn(
            {"kind": "repo_url", "value": "https://github.com/example/app"},
            registry.trusted_anchor_nodes(before),
        )
        self.assertEqual(dry_run["manual_repo_seed_repair_count"], 1)
        self.assertEqual(executed["manual_repo_seed_repair_count"], 1)
        self.assertIn("https://github.com/example/app", after["source_code_urls"])
        self.assertNotIn("https://github.com/example/app", after["candidate_source_code_urls"])
        self.assertNotIn(
            "candidate_repo_needs_confirmation",
            {signal["kind"] for signal in registry.compute_review_signals(after)},
        )

    def test_deferred_manual_repo_seed_is_not_trusted_anchor(self) -> None:
        project = self.make_project("proj:deferredrepo", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
        registry.defer_identifier_on_project(project, "repo_url", "https://github.com/example/app", days=14)

        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://github.com/example/app", project["source_code_urls"])
        self.assertIn("https://github.com/example/app", project["candidate_source_code_urls"])
        self.assertNotIn(
            {"kind": "repo_url", "value": "https://github.com/example/app"},
            registry.trusted_anchor_nodes(project),
        )

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

    def test_promotion_deferral_days_are_suggested_from_age_and_history(self) -> None:
        project = self.make_project("proj:promotionhold", "service_url", "https://example.com")
        project["first_seen_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat()

        self.assertEqual(registry.suggested_promotion_defer_days(project), 14)
        registry.defer_promotion_on_project(project, reason="young project")
        self.assertEqual(registry.suggested_promotion_defer_days(project), 45)
        registry.clear_active_promotion_deferrals(project)
        registry.defer_promotion_on_project(project)
        self.assertEqual(registry.suggested_promotion_defer_days(project), 60)

    def test_promotion_deferral_uses_explicit_days_when_provided(self) -> None:
        project = self.make_project("proj:promotionhold", "service_url", "https://example.com")

        entry = registry.defer_promotion_on_project(project, reason="explicit", days=7)
        expires = registry.parse_iso(entry["expires_at"])
        added = registry.parse_iso(entry["added_at"])

        self.assertIsNotNone(expires)
        self.assertIsNotNone(added)
        self.assertAlmostEqual((expires - added).total_seconds(), 7 * 24 * 60 * 60, delta=5)

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
