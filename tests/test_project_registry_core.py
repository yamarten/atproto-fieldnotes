from __future__ import annotations

import argparse
import datetime as dt
import re
import socket
import unittest
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from unittest import mock

from helpers import ROOT, registry
from project_registry_core.cli import build_parser
from project_registry_core import domain_policy, source_sync
from project_registry_core import http_transport as http_transport_core
from project_registry_core import identifiers as identifier_core
from project_registry_core import llm_evidence as llm_evidence_core
from project_registry_core import llm_transport as llm_transport_core
from project_registry_core import repo_urls as repo_url_core
from project_registry_core import naming as naming_core
from project_registry_core import project_record
from project_registry_core import repository_material
from project_registry_core import repository_sources


class ProjectRegistryCompletionTest(unittest.TestCase):
    def test_zsh_completion_matches_public_cli_commands_and_long_options(self) -> None:
        completion = (ROOT / "completions" / "_project-registry").read_text()
        parser = build_parser()
        subparsers = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        listed_commands = set(re.findall(r"^  '([a-z][a-z0-9-]*):", completion, re.MULTILINE))
        self.assertEqual(listed_commands, set(subparsers.choices))
        tracking_match = re.search(r"^tracking_states=\(([^)]*)\)$", completion, re.MULTILINE)
        self.assertIsNotNone(tracking_match)
        self.assertEqual(set(tracking_match.group(1).split()), project_record.TRACKING_STATES)

        for command, subparser in subparsers.choices.items():
            match = re.search(
                rf"^  {re.escape(command)}\)\n(.*?)(?=^    ;;)",
                completion,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, command)
            completed_options = set(re.findall(r"--[a-z][a-z0-9-]*", match.group(1)))
            parser_options = {
                option
                for action in subparser._actions
                for option in action.option_strings
                if option.startswith("--") and option != "--help"
            }
            self.assertEqual(completed_options, parser_options, command)


class ProjectRegistryIdentifierHelperTest(unittest.TestCase):
    def test_legacy_service_seed_scalar_is_not_an_ownership_anchor(self) -> None:
        project = project_record.default_project_record(
            "proj:legacywebsite",
            "service_url",
            "https://example.com",
        )
        project["website_url"] = "https://example.com"
        project["service_urls"] = []
        project["manual_seeds"] = []

        self.assertEqual(project_record.ownership_anchor_service_hosts(project), set())

    def test_promotion_does_not_require_legacy_seed_role(self) -> None:
        project = project_record.default_project_record("proj:promotionseedless", "service_url", "https://example.com")
        project.update({
            "website_url": "https://example.com",
            "service_urls": ["https://example.com"],
            "service_status": "live",
            "summary": "Summary",
            "tagline": "Tagline",
            "features": ["one", "two", "three"],
            "category": "project",
            "llm_source": {"model": "fixture"},
            "manual_seeds": [],
        })

        self.assertEqual(registry.evaluate_promotion(project), [])

    def test_normalize_url_preserves_path_but_drops_query_and_fragment(self) -> None:
        self.assertEqual(
            identifier_core.normalize_url("HTTPS://Example.COM/docs/?page=1#intro"),
            "https://example.com/docs",
        )

    def test_normalize_nsid_accepts_at_uri_and_fragment(self) -> None:
        self.assertEqual(
            identifier_core.normalize_nsid("at://com.example.app/#main"),
            "com.example.app",
        )
        self.assertEqual(
            identifier_core.normalize_nsid("at://com.example.profileMusic/#main"),
            "com.example.profileMusic",
        )

    def test_nsid_name_case_is_preserved_for_seed_extraction(self) -> None:
        nsid = "com.bartenderSky.preferences.profileMusic"
        self.assertEqual(project_record.extract_collection_nsid(nsid), nsid)
        self.assertEqual(project_record.extract_collection_nsid(f"at://did:plc:example/{nsid}/self"), nsid)
        self.assertEqual(registry.detect_seed_type(nsid), "nsid")
        self.assertEqual(project_record.canonical_seed_value("nsid", nsid), nsid)

    def test_manual_nsid_seed_keeps_name_case(self) -> None:
        nsid = "com.bartenderSky.preferences.profileMusic"
        project = project_record.default_project_record("proj:nsidcase", "nsid", nsid)
        registry.apply_seed_to_project(project, "nsid", nsid)
        shaped = project_record.ensure_project_shape(project)

        self.assertEqual(shaped["resolved_nsids"], [nsid])

    def test_text_seed_and_lexicon_detection_accept_case_sensitive_nsid(self) -> None:
        nsid = "com.bartenderSky.preferences.profileMusic"

        self.assertEqual(registry.parse_text_seed_line_values(nsid), [nsid])
        self.assertTrue(
            repository_material.repo_json_looks_like_lexicon(
                "lexicons/com/bartenderSky/preferences/profileMusic.json",
                {"lexicon": 1, "defs": {}},
                nsid,
            )
        )

    def test_authority_domain_derivation_keeps_collection_and_namespace_contracts(self) -> None:
        self.assertEqual(identifier_core.derive_authority_domain("com.example.app"), "example.com")
        self.assertEqual(identifier_core.derive_authority_domain_from_namespace("com.example"), "example.com")

    def test_alias_token_rejects_generic_values(self) -> None:
        self.assertEqual(identifier_core.normalize_alias_token("Example App"), "exampleapp")
        self.assertEqual(identifier_core.normalize_alias_token("Bluesky"), "")


class ProjectRegistryCanonicalUrlTest(unittest.TestCase):
    def test_provider_repo_views_are_derived_from_confirmed_source_records(self) -> None:
        project = project_record.default_project_record("proj:repoview", "nsid", "app.example.post")
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["source_repo_records"] = [
            {
                "url": "https://tangled.sh/@example/lexicons",
                "provider": "tangled",
                "confidence": "confirmed",
                "relation": "source_repo",
                "source": "test",
            }
        ]

        shaped = project_record.ensure_project_shape(project)

        self.assertEqual(project_record.provider_repo_url_values(shaped, "github"), ["https://github.com/example/app"])
        self.assertEqual(project_record.provider_repo_url_values(shaped, "tangled"), ["https://tangled.org/example/lexicons"])
        self.assertIn("https://tangled.org/example/lexicons", shaped["source_code_urls"])

    def test_codeberg_repo_seed_is_generic_repo_url(self) -> None:
        project = project_record.default_project_record("proj:codeberg", "repo_url", "https://codeberg.org/cartes/web/src/branch/main")
        registry.confirm_repo_seed(project, "https://codeberg.org/cartes/web/src/branch/main", source="test")
        shaped = project_record.ensure_project_shape(project)

        self.assertEqual(registry.detect_seed_type("https://codeberg.org/cartes/web"), "repo_url")
        self.assertEqual(project_record.canonical_seed_value("repo_url", "https://codeberg.org/cartes/web/src/branch/main"), "https://codeberg.org/cartes/web")
        self.assertEqual(shaped["source_code_urls"], ["https://codeberg.org/cartes/web"])
        self.assertEqual(repo_url_core.infer_repo_provider(shaped["source_code_urls"][0]), "codeberg.org")

    def test_sourcehut_repo_seed_is_generic_repo_url(self) -> None:
        project = project_record.default_project_record("proj:sourcehut", "repo_url", "https://git.sr.ht/~example/app/tree/main")
        registry.confirm_repo_seed(project, "https://git.sr.ht/~example/app/tree/main", source="test")
        shaped = project_record.ensure_project_shape(project)

        self.assertEqual(registry.detect_seed_type("https://git.sr.ht/~example/app"), "repo_url")
        self.assertEqual(shaped["source_code_urls"], ["https://git.sr.ht/~example/app"])
        self.assertEqual(repo_url_core.infer_repo_provider(shaped["source_code_urls"][0]), "git.sr.ht")

    def test_provider_repo_url_helpers_are_canonical_and_strict(self) -> None:
        self.assertEqual(
            repo_url_core.normalize_github_repo_url("https://github.com/example/project/issues/1"),
            "https://github.com/example/project",
        )
        self.assertEqual(
            repo_url_core.normalize_tangled_repo_url("https://tangled.sh/@dev.example/project/tree/main"),
            "https://tangled.org/dev.example/project",
        )
        self.assertEqual(
            repo_url_core.normalize_forge_repo_url("https://codeberg.org/example/project/src/branch/main"),
            "https://codeberg.org/example/project",
        )
        self.assertIsNone(repo_url_core.normalize_forge_repo_url("https://codeberg.org/explore/repos"))

    def test_provider_repo_views_ignore_candidate_source_records(self) -> None:
        project = project_record.default_project_record("proj:repocandidateview", "nsid", "app.example.post")
        registry.add_candidate_source_code_url(
            project,
            "https://github.com/example/candidate",
            "test",
            reason="candidate only",
        )
        project["candidate_source_code_urls"] = ["https://github.com/example/candidate"]

        shaped = project_record.ensure_project_shape(project)

        self.assertEqual(shaped["source_code_urls"], [])
        self.assertEqual(project_record.provider_repo_url_values(shaped, "github"), [])
        self.assertEqual(shaped["candidate_source_code_urls"], ["https://github.com/example/candidate"])


class ProjectRegistryOutboundFetchSafetyTest(unittest.TestCase):
    def test_validate_outbound_fetch_url_blocks_internal_hosts(self) -> None:
        for url in (
            "http://127.0.0.1:8000/secret",
            "http://localhost:8000/secret",
            "http://192.168.0.1/admin",
            "http://100.64.0.1/status",
            "javascript:alert(1)",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    http_transport_core.validate_outbound_fetch_url(url)

    def test_validate_outbound_fetch_url_preserves_query_string(self) -> None:
        url = "https://api.github.com/search/repositories?q=Skyrdle+OR+farm.smol.games.skyrdle.score&per_page=5"

        self.assertEqual(http_transport_core.validate_outbound_fetch_url(url), url)

    def test_request_bytes_revalidates_redirect_targets(self) -> None:
        class RedirectOpener:
            def open(self, req: urllib.request.Request, timeout: float = 0):
                raise urllib.error.HTTPError(
                    req.full_url,
                    302,
                    "Found",
                    {"Location": "http://127.0.0.1:8765/private"},
                    BytesIO(b""),
                )

        original_opener = http_transport_core.NO_REDIRECT_OPENER
        original_blocker = http_transport_core.outbound_host_is_blocked
        http_transport_core.NO_REDIRECT_OPENER = RedirectOpener()
        http_transport_core.outbound_host_is_blocked = lambda host: host == "127.0.0.1"
        try:
            with self.assertRaises(ValueError):
                http_transport_core.request_bytes_with_final_url("https://example.com/redirect")
        finally:
            http_transport_core.NO_REDIRECT_OPENER = original_opener
            http_transport_core.outbound_host_is_blocked = original_blocker

    def test_request_bytes_can_allow_trusted_operator_endpoint(self) -> None:
        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return b"{}"

            def geturl(self) -> str:
                return "http://192.168.1.2:1234/v1/responses"

        class LocalOpener:
            def open(self, req: urllib.request.Request, timeout: float = 0):
                return Response()

        original_opener = http_transport_core.NO_REDIRECT_OPENER
        http_transport_core.NO_REDIRECT_OPENER = LocalOpener()
        try:
            with self.assertRaises(ValueError):
                http_transport_core.request_bytes_with_final_url("http://192.168.1.2:1234/v1/responses")
            status, body, _headers, final_url = http_transport_core.request_bytes_with_final_url(
                "http://192.168.1.2:1234/v1/responses",
                allow_blocked_hosts=True,
            )
        finally:
            http_transport_core.NO_REDIRECT_OPENER = original_opener

        self.assertEqual(status, 200)
        self.assertEqual(body, b"{}")
        self.assertEqual(final_url, "http://192.168.1.2:1234/v1/responses")

    def test_request_bytes_sends_query_string(self) -> None:
        seen_urls: list[str] = []

        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return b"{}"

            def geturl(self) -> str:
                return seen_urls[-1]

        class RecordingOpener:
            def open(self, req: urllib.request.Request, timeout: float = 0):
                seen_urls.append(req.full_url)
                return Response()

        original_opener = http_transport_core.NO_REDIRECT_OPENER
        http_transport_core.NO_REDIRECT_OPENER = RecordingOpener()
        try:
            http_transport_core.request_bytes_with_final_url(
                "https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors?q=Skyrdle+%7C+skyrdle.com&limit=5",
            )
        finally:
            http_transport_core.NO_REDIRECT_OPENER = original_opener

        self.assertEqual(
            seen_urls,
            ["https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors?q=Skyrdle+%7C+skyrdle.com&limit=5"],
        )


class ProjectRegistrySembleSyncTest(unittest.TestCase):
    def test_fetch_semble_collection_uses_current_xrpc_endpoint(self) -> None:
        seen_urls: list[str] = []

        def fake_request_json(url: str, headers=None, *, timeout=0):
            seen_urls.append(url)
            return {
                "uri": "at://did:plc:example/network.cosmik.collection/rkey123",
                "name": "current collection name",
                "urlCards": [
                    {
                        "url": "https://github.com/example/project",
                        "uri": "at://did:plc:example/network.cosmik.card/card123",
                        "updatedAt": "2026-06-01T00:00:00Z",
                        "createdAt": "2026-05-30T00:00:00Z",
                        "cardContent": {
                            "title": "Example Project",
                            "description": "Example description",
                            "siteName": "GitHub",
                        },
                    }
                ],
                "pagination": {"hasMore": False},
            }

        original_request_json = http_transport_core.request_json
        original_tracked_collections = source_sync.SEMBLE_TRACKED_COLLECTIONS
        http_transport_core.request_json = fake_request_json
        source_sync.SEMBLE_TRACKED_COLLECTIONS = (
            {"handle": "byarielm.fyi", "rkey": "rkey123", "label": "stale label"},
        )
        try:
            results = source_sync.fetch_semble_collection_sync_results(pages=1, per_page=20)
        finally:
            http_transport_core.request_json = original_request_json
            source_sync.SEMBLE_TRACKED_COLLECTIONS = original_tracked_collections

        self.assertEqual(
            seen_urls,
            [
                "https://api.semble.so/xrpc/network.cosmik.collection.getByAtUri"
                "?handle=byarielm.fyi&recordKey=rkey123&page=1&limit=20"
            ],
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["seed_type"], "repo_url")
        self.assertEqual(results[0]["seed_value"], "https://github.com/example/project")
        self.assertEqual(results[0]["collection_label"], "current collection name")

    def test_semble_service_subpage_is_evidence_only(self) -> None:
        source = {"handle": "example.test", "rkey": "collection", "label": "apps"}
        with mock.patch.object(
            http_transport_core,
            "request_json",
            return_value={
                "uri": "at://did:plc:example/network.cosmik.collection/collection",
                "name": "apps",
                "urlCards": [
                    {
                        "url": "https://cred.blue/gov.glados.computer",
                        "uri": "at://did:plc:example/network.cosmik.card/card123",
                        "updatedAt": "2026-06-01T00:00:00Z",
                        "createdAt": "2026-05-30T00:00:00Z",
                        "cardContent": {"title": "cred.blue", "description": "Score page"},
                    }
                ],
                "pagination": {"hasMore": False},
            },
        ):
            scan = source_sync.fetch_semble_collection_source_sync_results(
                source,
                pages=1,
                per_page=20,
                cutoff=None,
            )

        self.assertEqual(scan["results"], [])
        self.assertEqual(len(scan["evidence_only"]), 1)
        self.assertEqual(scan["evidence_only"][0]["evidence_url"], "https://cred.blue/gov.glados.computer")
        self.assertEqual(scan["evidence_only"][0]["root_service_url"], "https://cred.blue")

    def test_semble_incremental_scan_stops_only_when_sort_order_is_confirmed(self) -> None:
        cutoff = dt.datetime(2026, 6, 5, tzinfo=dt.timezone.utc)

        def payload(*, ordered: bool) -> dict:
            return {
                "name": "apps",
                "urlCards": [
                    {
                        "url": "https://new.example/",
                        "updatedAt": "2026-06-06T00:00:00Z",
                        "createdAt": "2026-06-06T00:00:00Z",
                    },
                    {
                        "url": "https://old.example/",
                        "updatedAt": "2026-06-04T00:00:00Z",
                        "createdAt": "2026-06-04T00:00:00Z",
                    },
                ],
                "pagination": {"hasMore": True},
                **({"sorting": {"sortBy": "updatedAt", "sortOrder": "desc"}} if ordered else {}),
            }

        source = {"handle": "example.test", "rkey": "collection", "label": "apps"}
        with mock.patch.object(http_transport_core, "request_json", return_value=payload(ordered=True)):
            ordered_scan = source_sync.fetch_semble_collection_source_sync_results(
                source,
                pages=2,
                per_page=2,
                cutoff=cutoff,
            )
        with mock.patch.object(http_transport_core, "request_json", side_effect=[payload(ordered=False), {**payload(ordered=False), "urlCards": [], "pagination": {"hasMore": False}}]):
            unordered_scan = source_sync.fetch_semble_collection_source_sync_results(
                source,
                pages=2,
                per_page=2,
                cutoff=cutoff,
            )

        self.assertTrue(ordered_scan["boundary_reached"])
        self.assertEqual(ordered_scan["pages"], 1)
        self.assertFalse(unordered_scan["boundary_reached"])
        self.assertEqual(unordered_scan["pages"], 2)
        self.assertTrue(unordered_scan["scan_complete"])


class ProjectRegistryIncrementalSyncTest(unittest.TestCase):
    def test_bsky_search_requests_latest_posts_since_watermark(self) -> None:
        seen_urls: list[str] = []

        def fake_request(url: str, token: str, headers=None, *, timeout=0):
            seen_urls.append(url)
            return {"posts": []}

        with mock.patch.object(registry, "bsky_authenticated_app_url", return_value="https://example.test/xrpc/search"), mock.patch.object(
            registry,
            "bsky_access_token",
            return_value="token",
        ), mock.patch.object(http_transport_core, "request_json_authenticated", side_effect=fake_request):
            registry.search_bsky_posts_payload(
                "from:example.test",
                limit=5,
                since="2026-06-01T00:00:00+00:00",
            )

        query = urllib.parse.parse_qs(urllib.parse.urlparse(seen_urls[0]).query)
        self.assertEqual(query["sort"], ["latest"])
        self.assertEqual(query["limit"], ["5"])
        self.assertEqual(query["since"], ["2026-06-01T00:00:00+00:00"])

    def test_github_scan_includes_watermark_timestamp_and_stops_below_it(self) -> None:
        cutoff = dt.datetime(2026, 6, 5, tzinfo=dt.timezone.utc)
        seen_urls: list[str] = []
        payload = {
            "items": [
                {"html_url": "https://github.com/example/new", "updated_at": "2026-06-06T00:00:00Z"},
                {"html_url": "https://github.com/example/equal", "updated_at": "2026-06-05T00:00:00Z"},
                {"html_url": "https://github.com/example/old", "updated_at": "2026-06-04T00:00:00Z"},
            ]
        }

        def fake_request(url: str, headers=None):
            seen_urls.append(url)
            return payload

        with mock.patch.object(http_transport_core, "request_json", side_effect=fake_request):
            scan = source_sync.fetch_github_search_query_sync_results(
                "atproto",
                pages=3,
                per_page=3,
                cutoff=cutoff,
            )

        self.assertEqual([item["url"] for item in scan["results"]], [
            "https://github.com/example/new",
            "https://github.com/example/equal",
        ])
        self.assertTrue(scan["boundary_reached"])
        self.assertTrue(scan["scan_complete"])
        query = urllib.parse.parse_qs(urllib.parse.urlparse(seen_urls[0]).query)["q"][0]
        self.assertIn("updated:>=2026-06-05", query)

    def test_collection_filter_uses_latest_activity_timestamp(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        payload = [
            {
                "collection": "app.example.post",
                "min": (now - dt.timedelta(days=100)).isoformat(),
                "max": (now - dt.timedelta(days=1)).isoformat(),
            }
        ]

        filtered, stats = source_sync.filter_sync_collection_inventory_payload(payload, [], lookback_days=7)

        self.assertEqual(filtered, payload)
        self.assertEqual(stats["cutoff_source"], "lookback")

    def test_collection_filter_all_history_reprocesses_known_identifier(self) -> None:
        payload = [{"collection": "site.filae.chorus.note", "max": "2026-05-01T00:00:00+00:00"}]
        projects = [{"input_nsids": ["site.filae.chorus.note"]}]

        incremental, incremental_stats = source_sync.filter_sync_collection_inventory_payload(
            payload,
            projects,
            lookback_days=7,
        )
        all_history, all_history_stats = source_sync.filter_sync_collection_inventory_payload(
            payload,
            projects,
            lookback_days=7,
            all_history=True,
        )

        self.assertEqual(incremental, [])
        self.assertEqual(incremental_stats["skipped_known"], 1)
        self.assertEqual(all_history, payload)
        self.assertEqual(all_history_stats["skipped_known"], 0)


class ProjectRegistryNameCandidateTest(unittest.TestCase):
    def test_pure_naming_helpers_normalize_titles_and_sources(self) -> None:
        self.assertEqual(naming_core.clean_title("Example — ATProto client"), "Example")
        self.assertEqual(naming_core.normalize_name_candidate("https://example.com/app"), "example.com")
        self.assertEqual(naming_core.service_seed_name_hint("https://github.com/example/project"), "project")
        self.assertTrue(naming_core.source_is_repo("github_repo_name:https://github.com/example/project"))
        self.assertEqual(naming_core.source_url("service_title:https://example.com"), "https://example.com")

    def project_with_candidates(self, candidates: list[dict[str, str]], *, name: str = "") -> dict:
        project = project_record.default_project_record("proj:aaaaaaaaaaaa", "nsid", "app.example.post")
        project["name"] = name
        project["name_candidates"] = candidates
        return project

    def test_error_page_titles_are_filtered_from_service_and_existing_names(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "Error", "source": "service_title:https://api.bsky.chat"},
                {"value": "Not Found", "source": "resource_title:https://example.com/missing"},
                {"value": "Forbidden", "source": "service_title:https://example.com/private"},
                {"value": "Example", "source": "service_title:https://example.com"},
            ],
            name="Error",
        )

        values = [item["value"] for item in registry.current_name_candidates(project)]

        self.assertEqual(values, ["Example"])
        self.assertEqual(registry.choose_best_project_name(project), "Example")

    def test_bluesky_title_is_kept_when_source_host_supports_it(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "Bluesky", "source": "service_title:https://bsky.app"},
                {"value": "Bluesky", "source": "service_title:https://bsky.social"},
            ]
        )

        values = [item["value"] for item in registry.current_name_candidates(project)]

        self.assertEqual(values, ["Bluesky", "Bluesky"])
        self.assertEqual(registry.choose_best_project_name(project), "Bluesky")

    def test_generic_platform_names_without_source_identity_are_filtered(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "Bluesky", "source": "service_title:https://example.com"},
                {"value": "atproto", "source": "resource_title:https://docs.example.com"},
                {"value": "bsky", "source": "llm_output"},
                {"value": "Specific Tool", "source": "llm_output"},
            ]
        )

        values = [item["value"] for item in registry.current_name_candidates(project)]

        self.assertEqual(values, ["Specific Tool"])

    def test_repo_slug_is_not_cut_down_to_generic_prefix(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "atproto-visualizers", "source": "github_repo_name:example/atproto-visualizers"},
                {"value": "atproto", "source": "service_title:https://example.com"},
            ]
        )

        self.assertEqual(registry.choose_best_project_name(project), "atproto-visualizers")

    def test_operator_edited_name_outranks_generated_candidates(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "mastrojs/atproto", "source": "operator_edit"},
                {"value": "atproto", "source": "llm_output"},
                {"value": "atproto", "source": "github_repo_name:mastrojs/atproto"},
            ]
        )

        self.assertEqual(registry.choose_best_project_name(project), "mastrojs/atproto")

    def test_successful_llm_review_replaces_previous_llm_name_candidate(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "Orbita Shelf", "source": "llm_output", "added_at": "2026-07-18T00:00:00+00:00"},
                {"value": "orbita", "source": "github_repo_name:example/orbita"},
            ],
            name="Orbita Shelf",
        )
        project["llm_output"] = {"name": "Orbita Shelf"}

        registry.apply_llm_output(project, {"name": "Orbita"}, {}, {})
        finalized = registry.finalize_project_shape(project)

        self.assertEqual(
            [
                item["value"]
                for item in finalized["name_candidates"]
                if item.get("source") == "llm_output"
            ],
            ["Orbita"],
        )
        self.assertEqual(finalized["name"], "Orbita")
        self.assertIn("orbita", [item["value"] for item in finalized["name_candidates"]])

    def test_finalize_repairs_stale_llm_name_candidates_from_current_output(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "Old Guess", "source": "llm_output"},
                {"value": "Current Name", "source": "llm_output", "added_at": "2026-07-25T00:00:00+00:00"},
                {"value": "Manual Name", "source": "operator_edit"},
            ],
            name="Manual Name",
        )
        project["llm_output"] = {"name": "Current Name"}

        finalized = registry.finalize_project_shape(project)

        self.assertEqual(
            [
                item["value"]
                for item in finalized["name_candidates"]
                if item.get("source") == "llm_output"
            ],
            ["Current Name"],
        )
        self.assertEqual(finalized["name"], "Manual Name")

    def test_service_title_tagline_is_trimmed_for_name_selection(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "Relay.app: Turn plain English into reliable automations", "source": "service_title:https://relay.app"},
                {"value": "relay.app", "source": "llm_output"},
            ],
            name="Relay.app: Turn plain English into reliable automations",
        )
        project["authority_domains"] = ["relay.app"]

        values = [item["value"] for item in registry.current_name_candidates(project)]

        self.assertEqual(values, ["relay.app", "relay.app", "relay.app"])
        self.assertEqual(registry.choose_best_project_name(project), "relay.app")

    def test_nsid_authority_title_unrelated_to_seed_is_filtered(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "@hectorsector.com on Bluesky", "source": "service_title:https://hectorsector.com"},
                {"value": "status", "source": "lexicon_name:com.hectorsector.book.status"},
                {"value": "com.hectorsector.book.status", "source": "llm_output"},
            ],
            name="@hectorsector.com on Bluesky",
        )
        project["seed_type"] = "nsid"
        project["seed_value"] = "com.hectorsector.book.status"
        project["input_nsids"] = ["com.hectorsector.book.status"]
        project["authority_domains"] = ["book.hectorsector.com"]

        values = [item["value"] for item in registry.current_name_candidates(project)]

        self.assertEqual(values, ["status"])
        self.assertEqual(registry.choose_best_project_name(project), "status")

    def test_nsid_authority_title_filters_copied_llm_output(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "niko's cool and awesome website!", "source": "service_title:https://meowing.zip"},
                {"value": "niko's cool and awesome website!", "source": "llm_output"},
                {"value": "meowing.zip", "source": "llm_output"},
            ],
            name="niko's cool and awesome website!",
        )
        project["seed_type"] = "nsid"
        project["seed_value"] = "zip.meowing.isfox"
        project["input_nsids"] = ["zip.meowing.isfox"]
        project["authority_domains"] = ["meowing.zip"]

        values = [item["value"] for item in registry.current_name_candidates(project)]

        self.assertEqual(values, ["meowing.zip"])
        self.assertEqual(registry.choose_best_project_name(project), "meowing.zip")

    def test_nsid_authority_title_matching_seed_token_is_kept(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "Book Status", "source": "service_title:https://book.hectorsector.com"},
            ]
        )
        project["seed_type"] = "nsid"
        project["seed_value"] = "com.hectorsector.book.status"
        project["input_nsids"] = ["com.hectorsector.book.status"]
        project["authority_domains"] = ["book.hectorsector.com"]

        self.assertEqual(registry.choose_best_project_name(project), "Book Status")

    def test_confirmed_service_title_is_not_filtered_as_nsid_authority_guess(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "Sepia", "source": "service_title:https://sepia.press"},
                {"value": "Sepia", "source": "llm_output"},
            ],
            name="Sepia Prefs",
        )
        project["seed_type"] = "nsid"
        project["seed_value"] = "press.sepia.app.prefs"
        project["input_nsids"] = ["press.sepia.app.prefs"]
        project["supported_service_urls"] = ["https://sepia.press"]

        self.assertEqual(registry.choose_best_project_name(project), "Sepia")

    def test_generic_navigation_titles_from_pages_are_filtered(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "Home", "source": "service_title:https://meowing.zip"},
                {"value": "Home", "source": "llm_output"},
                {"value": "meowing.zip", "source": "llm_output"},
            ],
            name="Home",
        )
        project["seed_type"] = "nsid"
        project["seed_value"] = "zip.meowing.isfox"
        project["input_nsids"] = ["zip.meowing.isfox"]
        project["authority_domains"] = ["meowing.zip"]

        values = [item["value"] for item in registry.current_name_candidates(project)]

        self.assertEqual(values, ["meowing.zip"])
        self.assertEqual(registry.choose_best_project_name(project), "meowing.zip")

    def test_generic_navigation_title_from_repo_source_is_kept(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "home", "source": "github_repo_name:example/home"},
            ]
        )

        self.assertEqual(registry.choose_best_project_name(project), "home")

    def test_weak_service_page_title_is_filtered_by_identity_ratio(self) -> None:
        project = self.project_with_candidates(
            [
                {"value": "marketplace", "source": "github_repo_name:openmkt-app/marketplace"},
                {"value": "Browse Listings", "source": "service_title:https://openmkt.app"},
                {"value": "Open Market", "source": "llm_output"},
            ],
            name="Browse Listings",
        )
        project["seed_type"] = "repo_url"
        project["seed_value"] = "https://github.com/openmkt-app/marketplace"
        project["input_nsids"] = ["app.atprotomkt.marketplace.listing", "app.openmkt.marketplace.listing"]
        project["resolved_nsids"] = ["app.openmkt.marketplace.listing"]
        project["authority_domains"] = ["marketplace.atprotomkt.app", "marketplace.openmkt.app"]
        project["source_code_urls"] = ["https://github.com/openmkt-app/marketplace"]

        values = [item["value"] for item in registry.current_name_candidates(project)]

        self.assertEqual(values, ["marketplace", "Open Market"])
        self.assertEqual(registry.choose_best_project_name(project), "Open Market")


class ProjectRegistryServiceStatusTest(unittest.TestCase):
    def classify(
        self,
        *,
        status_code: int = 200,
        title: str = "",
        description: str = "",
        html: str = "",
        links: list[dict[str, str]] | None = None,
        fetch_error: str = "",
    ) -> tuple[str, str, list[str]]:
        return registry.classify_service_probe_result(
            status_code=status_code,
            title=title,
            description=description,
            html=html,
            links=links or [],
            fetch_error=fetch_error,
        )

    def test_login_surface_is_live(self) -> None:
        status, confidence, evidence = self.classify(html='<form><input type="password"><button>Log in</button></form>')

        self.assertEqual(status, "live")
        self.assertEqual(confidence, "medium")
        self.assertIn("login_surface_detected", evidence)

    def test_docs_only_surface_is_none(self) -> None:
        status, confidence, evidence = self.classify(
            links=[
                {"url": "https://example.com/docs", "text": "Docs"},
                {"url": "https://example.com/about", "text": "About"},
                {"url": "https://example.com/blog", "text": "Blog"},
            ]
        )

        self.assertEqual(status, "none")
        self.assertEqual(confidence, "medium")
        self.assertIn("docs_only_surface_detected", evidence)

    def test_http_error_statuses_are_classified(self) -> None:
        self.assertEqual(self.classify(status_code=404)[0], "dead")
        self.assertEqual(self.classify(status_code=410)[0], "dead")
        self.assertEqual(self.classify(status_code=401)[0], "private")
        self.assertEqual(self.classify(status_code=403)[0], "private")
        self.assertEqual(self.classify(status_code=503)[0], "unverified")

    def test_launch_and_access_markers_are_classified(self) -> None:
        self.assertEqual(self.classify(html="Join waitlist. Request invite.")[0], "invite_only")
        self.assertEqual(self.classify(html="Private beta now open.")[0], "beta")
        self.assertEqual(self.classify(html="Coming soon.")[0], "unreleased")

        status, confidence, evidence = self.classify(html="Coming soon. Open app.")
        self.assertEqual(status, "unverified")
        self.assertEqual(confidence, "medium")
        self.assertIn("mixed_launch_signals_detected", evidence)

    def test_probe_errors_are_classified(self) -> None:
        self.assertEqual(registry.classify_service_probe_error(urllib.error.URLError("Name or service not known")), "dns_failed")
        self.assertEqual(
            registry.classify_service_probe_error(urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "not known"))),
            "dns_failed",
        )
        self.assertEqual(
            registry.classify_service_probe_error(urllib.error.URLError("Temporary failure in name resolution")),
            "dns_temporary_failure",
        )
        self.assertEqual(
            registry.classify_service_probe_error(urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "try again"))),
            "dns_temporary_failure",
        )
        self.assertEqual(registry.classify_service_probe_error(urllib.error.URLError("TLS certificate verify failed")), "tls_failed")
        self.assertEqual(registry.classify_service_probe_error(socket.timeout("timed out")), "timeout")
        self.assertEqual(registry.classify_service_probe_error(urllib.error.URLError("Connection refused")), "connection_refused")


class ProjectRegistrySameSiteTest(unittest.TestCase):
    def test_extract_links_preserves_accessible_icon_label(self) -> None:
        links = registry.extract_links(
            '<a href="https://github.com/example/app" aria-label="Source code" title="GitHub"></a>',
            "https://app.example.com",
        )

        self.assertEqual(links, [{
            "url": "https://github.com/example/app",
            "text": "",
            "aria_label": "Source code",
            "title": "GitHub",
        }])

    def test_registrable_domain_uses_vendored_psl_rules(self) -> None:
        self.assertEqual(domain_policy.root_domain("api.example.co.uk"), "example.co.uk")
        self.assertEqual(domain_policy.root_domain("www.city.kawasaki.jp"), "city.kawasaki.jp")

    def test_same_first_label_on_unrelated_registrable_domains_is_not_same_site(self) -> None:
        self.assertFalse(domain_policy.is_same_site_family(
            "https://app.example.com",
            "https://app.attacker.net",
        ))

    def test_subdomains_of_same_registrable_domain_are_same_site(self) -> None:
        self.assertTrue(domain_policy.is_same_site_family(
            "https://www.example.com",
            "https://docs.example.com/guide",
        ))

    def test_unrelated_same_label_link_is_not_classified_as_same_site(self) -> None:
        self.assertEqual(
            registry.classify_official_link(
                "https://app.example.com",
                "https://app.attacker.net/about",
                "About this project",
            ),
            "other",
        )

    def test_report_link_is_not_mistaken_for_repo_marker(self) -> None:
        self.assertEqual(
            registry.classify_official_link(
                "https://app.example.com",
                "https://github.com/example/framework",
                "Report a bug",
            ),
            "other",
        )

    def test_unrelated_same_label_repo_homepage_gets_no_site_bonus(self) -> None:
        project = project_record.default_project_record(
            "proj:samesiterepo",
            "service_url",
            "https://app.example.com",
        )
        project["website_url"] = "https://app.example.com"
        project["service_urls"] = ["https://app.example.com"]

        score = registry.github_repo_relevance(project, {
            "url": "https://github.com/unrelated/package",
            "homepage": "https://app.attacker.net",
        })

        self.assertEqual(score, 0)


class ProjectRegistryServiceRedirectTest(unittest.TestCase):
    def test_redirect_metadata_marks_same_host_http_to_https(self) -> None:
        metadata = registry.service_probe_redirect_metadata("http://example.com", "https://example.com")

        self.assertTrue(metadata["redirected"])
        self.assertTrue(metadata["redirect_same_host"])
        self.assertTrue(metadata["redirect_same_site_family"])
        self.assertTrue(metadata["redirect_http_to_https"])
        self.assertFalse(metadata["redirect_cross_host"])

    def test_redirect_metadata_marks_cross_host_redirect(self) -> None:
        metadata = registry.service_probe_redirect_metadata("https://example.com", "https://other.example.net")

        self.assertTrue(metadata["redirected"])
        self.assertFalse(metadata["redirect_same_host"])
        self.assertFalse(metadata["redirect_same_site_family"])
        self.assertTrue(metadata["redirect_cross_host"])

    def test_redirect_metadata_rejects_same_label_on_unrelated_domains(self) -> None:
        metadata = registry.service_probe_redirect_metadata(
            "https://app.example.com",
            "https://app.attacker.net",
        )

        self.assertTrue(metadata["redirected"])
        self.assertFalse(metadata["redirect_same_host"])
        self.assertFalse(metadata["redirect_same_site_family"])
        self.assertTrue(metadata["redirect_cross_host"])

    def test_same_host_http_to_https_redirect_canonicalizes_service_url(self) -> None:
        project = project_record.default_project_record("proj:redirecttest", "service_url", "http://example.com")
        registry.add_confirmed_identifier_decision(project, "service_url", "http://example.com", source="test")
        registry.add_manual_seed(project, "service_url", "http://example.com", source="test")
        result = {
            "requested_url": "http://example.com",
            "final_url": "https://example.com",
            "redirected": True,
            "redirect_same_host": True,
            "redirect_same_site_family": True,
            "redirect_http_to_https": True,
            "redirect_https_to_http": False,
            "redirect_cross_host": False,
            "status_code": 200,
            "title": "Example",
            "description": "",
            "urls": [],
            "links": [],
            "html": "Example app",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "redirect_detected", "redirect_http_to_https"],
            "fetch_error": "",
        }

        registry.apply_service_probe_result(project, "http://example.com", result)

        self.assertIn("https://example.com", project["service_urls"])
        self.assertNotIn("http://example.com", project["service_urls"])
        self.assertEqual(project["website_url"], "")
        redirect_logs = [item for item in project["investigation_log"] if item.get("kind") == "service_redirect"]
        self.assertEqual(redirect_logs[0]["final_url"], "https://example.com")

    def test_cross_host_redirect_does_not_canonicalize_service_url(self) -> None:
        project = project_record.default_project_record("proj:redirectcross", "service_url", "https://example.com")
        registry.add_confirmed_identifier_decision(project, "service_url", "https://example.com", source="test")
        registry.add_manual_seed(project, "service_url", "https://example.com", source="test")
        result = {
            "requested_url": "https://example.com",
            "final_url": "https://other.example.net",
            "redirected": True,
            "redirect_same_host": False,
            "redirect_same_site_family": False,
            "redirect_http_to_https": False,
            "redirect_https_to_http": False,
            "redirect_cross_host": True,
            "status_code": 200,
            "title": "Example",
            "description": "",
            "urls": [],
            "links": [],
            "html": "Example app",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "redirect_detected", "redirect_cross_host"],
            "fetch_error": "",
        }

        registry.apply_service_probe_result(project, "https://example.com", result)

        self.assertIn("https://example.com", project["service_urls"])
        self.assertNotIn("https://other.example.net", project["service_urls"])
        self.assertNotIn("https://other.example.net", project["service_url_candidates"])

    def test_nsid_mention_cross_host_redirect_does_not_add_final_url_as_candidate(self) -> None:
        project = project_record.default_project_record("proj:redirectcandidate", "service_url", "https://example.com")
        registry.add_confirmed_identifier_decision(project, "service_url", "https://example.com", source="test")
        project["input_nsids"] = ["app.example.post"]
        registry.add_manual_seed(project, "service_url", "https://example.com", source="test")
        result = {
            "requested_url": "https://example.com",
            "final_url": "https://other.example.net",
            "redirected": True,
            "redirect_same_host": False,
            "redirect_same_site_family": False,
            "redirect_http_to_https": False,
            "redirect_https_to_http": False,
            "redirect_cross_host": True,
            "status_code": 200,
            "title": "Example",
            "description": "",
            "urls": [],
            "links": [],
            "html": "Example app for app.example.post",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "redirect_detected", "redirect_cross_host"],
            "fetch_error": "",
        }

        registry.apply_service_probe_result(project, "https://example.com", result)

        self.assertNotIn("https://other.example.net", project["service_urls"])
        self.assertNotIn("https://other.example.net", project["service_url_candidates"])
        self.assertNotIn("https://other.example.net", project["evidence_urls"])

    def test_supported_same_site_redirect_does_not_set_empty_website_url(self) -> None:
        project = project_record.default_project_record("proj:redirectsamefamily", "service_url", "https://example.com")
        registry.add_confirmed_identifier_decision(project, "service_url", "https://example.com", source="test")
        project["input_nsids"] = ["app.example.post"]
        registry.add_manual_seed(project, "service_url", "https://example.com", source="test")
        result = {
            "requested_url": "https://example.com",
            "final_url": "https://www.example.com",
            "redirected": True,
            "redirect_same_host": False,
            "redirect_same_site_family": True,
            "redirect_http_to_https": False,
            "redirect_https_to_http": False,
            "redirect_cross_host": True,
            "status_code": 200,
            "title": "Example",
            "description": "",
            "urls": [],
            "links": [],
            "html": "Example app for app.example.post",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "redirect_detected", "redirect_cross_host"],
            "fetch_error": "",
        }

        registry.apply_service_probe_result(project, "https://example.com", result)

        self.assertEqual(project["website_url"], "")
        self.assertIn("https://www.example.com", project["service_urls"])

    def test_supported_same_site_redirect_preserves_website_and_updates_service(self) -> None:
        project = project_record.default_project_record("proj:redirectsamefamilyexisting", "service_url", "https://example.com")
        registry.add_confirmed_identifier_decision(project, "service_url", "https://example.com", source="test")
        project["input_nsids"] = ["app.example.post"]
        project["website_url"] = "https://current.example.com"
        registry.add_manual_seed(project, "service_url", "https://example.com", source="test")
        result = {
            "requested_url": "https://example.com",
            "final_url": "https://www.example.com",
            "redirected": True,
            "redirect_same_host": False,
            "redirect_same_site_family": True,
            "redirect_http_to_https": False,
            "redirect_https_to_http": False,
            "redirect_cross_host": True,
            "status_code": 200,
            "title": "Example",
            "description": "",
            "urls": [],
            "links": [],
            "html": "Example app for app.example.post",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "redirect_detected", "redirect_cross_host"],
            "fetch_error": "",
        }

        registry.apply_service_probe_result(project, "https://example.com", result)

        self.assertEqual(project["website_url"], "https://current.example.com")
        self.assertIn("https://www.example.com", project["service_urls"])
        self.assertNotIn("https://www.example.com", project["service_url_candidates"])


class ProjectRegistryAuthorityServiceCandidateTest(unittest.TestCase):
    def probe_result(self, html: str) -> dict:
        return {
            "requested_url": "https://tz2at.xyz",
            "final_url": "https://tz2at.xyz",
            "redirected": False,
            "redirect_same_host": False,
            "redirect_same_site_family": False,
            "redirect_http_to_https": False,
            "redirect_https_to_http": False,
            "redirect_cross_host": False,
            "status_code": 200,
            "title": "TZ2AT",
            "description": "",
            "urls": [],
            "links": [],
            "html": html,
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "app_shell_detected"],
            "fetch_error": "",
        }

    def test_nsid_seed_does_not_create_authority_service_candidates(self) -> None:
        project = project_record.default_project_record("proj:authorityseed", "nsid", "fm.freemix.release.track")

        registry.apply_seed_to_project(project, "nsid", "fm.freemix.release.track")
        self.assertIn("release.freemix.fm", project["authority_domains"])
        self.assertEqual(project["service_url_candidates"], [])
        self.assertEqual(registry.authority_probe_url_chains(project), [["https://release.freemix.fm", "https://freemix.fm"]])

        project = registry.finalize_project_shape(project)

        self.assertEqual(project["service_url_candidates"], [])
        self.assertEqual(registry.derived_authority_probe_urls(project), [])

    def test_authority_probe_chain_selects_parent_only_after_exact_4xx(self) -> None:
        chains = [["https://actor.thermals.cloud", "https://thermals.cloud"]]
        exact_404 = {"status_code": 404}
        parent_200 = {"status_code": 200}

        selected = registry.select_authority_probe_urls(
            chains,
            {
                "https://actor.thermals.cloud": exact_404,
                "https://thermals.cloud": parent_200,
            },
        )

        self.assertEqual(selected, {"https://thermals.cloud"})
        self.assertEqual(parent_200["authority_fallback_from"], "https://actor.thermals.cloud")
        self.assertEqual(parent_200["authority_fallback_status_code"], 404)

    def test_authority_probe_chain_selects_parent_after_exact_dns_failure(self) -> None:
        chains = [["https://compute.cocore.dev", "https://cocore.dev"]]
        parent_200 = {"status_code": 200}

        selected = registry.select_authority_probe_urls(
            chains,
            {
                "https://compute.cocore.dev": {"status_code": 0, "fetch_error": "dns_failed"},
                "https://cocore.dev": parent_200,
            },
        )

        self.assertEqual(selected, {"https://cocore.dev"})
        self.assertEqual(parent_200["authority_fallback_from"], "https://compute.cocore.dev")
        self.assertEqual(parent_200["authority_fallback_status_code"], 0)
        self.assertEqual(parent_200["authority_fallback_fetch_error"], "dns_failed")

    def test_authority_probe_chain_does_not_select_parent_after_exact_timeout(self) -> None:
        for exact_result in (
            {"status_code": 0, "fetch_error": "timeout"},
            {"status_code": 0, "fetch_error": "dns_temporary_failure"},
            {"status_code": 0, "fetch_error": "tls_failed"},
            {"status_code": 0, "fetch_error": "connection_refused"},
            {"status_code": 0, "fetch_error": "network_error"},
            {"status_code": 503, "fetch_error": ""},
        ):
            with self.subTest(exact_result=exact_result):
                selected = registry.select_authority_probe_urls(
                    [["https://compute.cocore.dev", "https://cocore.dev"]],
                    {
                        "https://compute.cocore.dev": exact_result,
                        "https://cocore.dev": {"status_code": 200},
                    },
                )

                self.assertEqual(selected, set())

    def test_authority_probe_chain_uses_normal_dns_failure_probe_result(self) -> None:
        with mock.patch.object(
            registry.http_transport,
            "request_bytes_with_final_url",
            side_effect=urllib.error.URLError("Name or service not known"),
        ):
            exact_result = registry.probe_service_url("https://missing-authority.cocore.test")

        selected = registry.select_authority_probe_urls(
            [["https://missing-authority.cocore.test", "https://cocore.test"]],
            {
                "https://missing-authority.cocore.test": exact_result,
                "https://cocore.test": {"status_code": 200},
            },
        )

        self.assertEqual(exact_result["fetch_error"], "dns_failed")
        self.assertEqual(selected, {"https://cocore.test"})

    def test_authority_same_site_parent_redirect_keeps_only_final_candidate(self) -> None:
        project = project_record.default_project_record("proj:authorityredirect", "nsid", "dev.cocore.compute.profile")
        project["authority_domains"] = ["compute.cocore.dev"]
        project["recent_record_samples"] = [{"record": {"url": "https://compute.cocore.dev/item/1"}}]
        result = {
            "status_code": 200,
            "final_url": "https://cocore.dev",
            "redirected": True,
            "redirect_same_host": False,
            "redirect_same_site_family": True,
            "redirect_http_to_https": False,
            "redirect_cross_host": True,
            "title": "Cocore",
            "description": "AT Protocol service",
            "html": "<title>Cocore</title>",
            "links": [],
            "urls": [],
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "redirect_detected", "redirect_same_site_family"],
            "fetch_error": "",
            "supports_project": True,
            "lane": "authority",
        }

        registry.apply_service_probe_result(project, "https://compute.cocore.dev", result, collect_follow_ups=False)
        registry.apply_authority_service_probe_result(project, "https://compute.cocore.dev", result)

        self.assertIn("https://cocore.dev", project["service_url_candidates"])
        self.assertNotIn("https://compute.cocore.dev", project["service_url_candidates"])

    def test_authority_probe_chain_discards_parent_when_exact_succeeds(self) -> None:
        selected = registry.select_authority_probe_urls(
            [["https://actor.thermals.cloud", "https://thermals.cloud"]],
            {
                "https://actor.thermals.cloud": {"status_code": 200},
                "https://thermals.cloud": {"status_code": 200},
            },
        )

        self.assertEqual(selected, {"https://actor.thermals.cloud"})

    def test_finalize_prunes_stored_nsid_authority_service_guesses(self) -> None:
        project = project_record.default_project_record("proj:authoritycleanup", "nsid", "fm.freemix.release.track")
        project["input_nsids"] = ["fm.freemix.release.track"]
        project["authority_domains"] = ["release.freemix.fm"]
        project["service_url_candidates"] = ["https://release.freemix.fm"]
        project["failed_service_urls"] = ["https://release.freemix.fm"]
        project["llm_source"] = {
            "evidence": {
                "input_nsids": ["fm.freemix.release.track"],
                "authority_domains": ["release.freemix.fm"],
                "failed_service_urls": ["https://release.freemix.fm"],
            }
        }
        project["trust_graph"] = {
            "version": 1,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "edges": [],
            "evaluations": [
                {
                    "candidate": {"kind": "service_url", "value": "https://release.freemix.fm"},
                    "auto_confirm": False,
                    "reason": "no_mutual_trust_path",
                }
            ],
        }

        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://release.freemix.fm", project["service_url_candidates"])
        self.assertNotIn("https://release.freemix.fm", project["failed_service_urls"])
        self.assertNotIn("https://release.freemix.fm", project["llm_source"]["evidence"]["failed_service_urls"])
        evaluated_service_urls = [
            item["candidate"]["value"]
            for item in project.get("trust_graph", {}).get("evaluations", [])
            if item.get("candidate", {}).get("kind") == "service_url"
        ]
        self.assertNotIn("https://release.freemix.fm", evaluated_service_urls)

    def test_finalize_keeps_confirmed_authority_service_url(self) -> None:
        project = project_record.default_project_record("proj:authorityconfirmed", "nsid", "fm.freemix.release.track")
        project["input_nsids"] = ["fm.freemix.release.track"]
        project["authority_domains"] = ["release.freemix.fm"]
        project["service_urls"] = ["https://release.freemix.fm"]
        project["supported_service_urls"] = ["https://release.freemix.fm"]
        project["failed_service_urls"] = ["https://release.freemix.fm"]

        project = registry.finalize_project_shape(project)

        self.assertIn("https://release.freemix.fm", project["service_urls"])
        self.assertIn("https://release.freemix.fm", project["supported_service_urls"])

    def test_authority_service_probe_keeps_candidate_when_record_urls_match_host(self) -> None:
        project = project_record.default_project_record("proj:authoritycandidate", "nsid", "com.suibari.meyasuat.answer")
        project["input_nsids"] = ["com.suibari.meyasuat.answer"]
        project["authority_domains"] = ["meyasuat.suibari.com"]
        project["recorded_lexicon_namespaces"] = ["com.suibari.meyasuat"]
        project["manual_seeds"] = [{"type": "nsid", "value": "com.suibari.meyasuat.answer"}]
        project["collection_observations"] = [
            {
                "collection": "com.suibari.meyasuat",
                "members": ["com.suibari.meyasuat.answer"],
                "count": 1,
                "recent_count": 1,
                "source": "test",
            }
        ]
        project["recent_record_samples"] = [
            {
                "collection": "com.suibari.meyasuat.answer",
                "record": {
                    "$type": "com.suibari.meyasuat.answer",
                    "url": "https://meyasuat.suibari.com/u/alice.example/m/123",
                },
            }
        ]
        result = self.probe_result(
            '<title>めやすあっと</title><meta name="description" content="Bluesky users can receive anonymous messages">'
        )
        result["requested_url"] = "https://meyasuat.suibari.com"
        result["final_url"] = "https://meyasuat.suibari.com"
        result["service_status"] = "live"
        result["service_status_confidence"] = "medium"
        result["service_status_evidence"] = ["http_200", "login_surface_detected"]

        self.assertEqual(registry.derived_authority_probe_urls(project), ["https://meyasuat.suibari.com"])
        registry.apply_authority_service_probe_result(project, "https://meyasuat.suibari.com", result)
        project = registry.finalize_project_shape(project)

        self.assertIn("https://meyasuat.suibari.com", project["service_url_candidates"])
        self.assertNotIn("https://meyasuat.suibari.com", project["service_urls"])
        self.assertNotIn("https://meyasuat.suibari.com", project["supported_service_urls"])

    def test_authority_service_probe_keeps_exact_host_with_atproto_page_evidence(self) -> None:
        project = project_record.default_project_record("proj:studynext", "nsid", "app.studynext.cheer")
        project["input_nsids"] = ["app.studynext.cheer"]
        project["manual_seeds"] = [{"type": "nsid", "value": "app.studynext.cheer"}]
        project["authority_domains"] = ["studynext.app"]
        result = self.probe_result(
            '<title>StudyNext</title><meta name="description" content="Learning plans stored in your PDS">'
            '<p>Data is stored with AT Protocol.</p>'
        )
        result["requested_url"] = "https://studynext.app"
        result["final_url"] = "https://studynext.app"
        result["service_status"] = "unverified"
        result["service_status_confidence"] = "low"
        result["service_status_evidence"] = ["http_200"]
        project["service_verification_results"] = [
            {**result, "lane": "authority", "html_extract": result["html"]}
        ]

        registry.apply_authority_service_probe_result(project, "https://studynext.app", result)
        project = registry.finalize_project_shape(project)

        self.assertIn("https://studynext.app", project["service_url_candidates"])
        self.assertNotIn("https://studynext.app", project["service_urls"])
        self.assertNotIn("https://studynext.app", project["supported_service_urls"])

    def test_authority_service_probe_rejects_exact_host_without_atproto_page_evidence(self) -> None:
        project = project_record.default_project_record("proj:authoritygeneric", "nsid", "com.example.post")
        project["input_nsids"] = ["com.example.post"]
        project["manual_seeds"] = [{"type": "nsid", "value": "com.example.post"}]
        project["authority_domains"] = ["example.com"]
        result = self.probe_result(
            '<title>Example Consulting</title><meta name="description" content="Business consulting services">'
            '<a href="https://bsky.app/profile/example.com">Bluesky</a>'
        )
        result["requested_url"] = "https://example.com"
        result["final_url"] = "https://example.com"
        result["service_status"] = "unverified"

        registry.apply_authority_service_probe_result(project, "https://example.com", result)

        self.assertNotIn("https://example.com", project["service_url_candidates"])

    def test_finalize_restores_authority_candidate_from_saved_probe_result(self) -> None:
        project = project_record.default_project_record("proj:authoritysavedprobe", "nsid", "com.suibari.meyasuat.answer")
        project["input_nsids"] = ["com.suibari.meyasuat.answer"]
        project["manual_seeds"] = [{"type": "nsid", "value": "com.suibari.meyasuat.answer"}]
        project["authority_domains"] = ["meyasuat.suibari.com"]
        project["recent_record_samples"] = [
            {
                "collection": "com.suibari.meyasuat.answer",
                "record": {"url": "https://meyasuat.suibari.com/u/alice.example/m/123"},
            }
        ]
        project["service_verification_results"] = [
            {
                "requested_url": "https://meyasuat.suibari.com",
                "final_url": "https://meyasuat.suibari.com",
                "status_code": 200,
                "title": "めやすあっと",
                "description": "Bluesky users can receive anonymous messages",
                "service_status": "live",
                "service_status_confidence": "medium",
                "service_status_evidence": ["http_200", "login_surface_detected"],
                "html_extract": "<title>めやすあっと</title>",
            }
        ]

        project = registry.finalize_project_shape(project)

        self.assertIn("https://meyasuat.suibari.com", project["service_url_candidates"])

    def test_finalize_restores_parent_authority_candidate_from_saved_fallback_probe(self) -> None:
        project = project_record.default_project_record("proj:authorityfallback", "nsid", "cloud.thermals.actor.profile")
        project["input_nsids"] = ["cloud.thermals.actor.profile"]
        project["manual_seeds"] = [{"type": "nsid", "value": "cloud.thermals.actor.profile"}]
        project["authority_domains"] = ["actor.thermals.cloud"]
        project["service_url_candidates"] = ["https://thermals.cloud"]
        project["service_verification_results"] = [
            {
                "requested_url": "https://thermals.cloud",
                "final_url": "https://thermals.cloud",
                "status_code": 200,
                "title": "Thermals",
                "description": "AT Protocol thermal flying service",
                "service_status": "live",
                "service_status_confidence": "medium",
                "service_status_evidence": ["http_200", "app_shell_detected"],
                "authority_fallback_from": "https://actor.thermals.cloud",
                "authority_fallback_status_code": 404,
                "html_extract": "<title>Thermals</title>",
            }
        ]

        project = registry.finalize_project_shape(project)

        self.assertIn("https://thermals.cloud", project["service_url_candidates"])

    def test_finalize_restores_parent_authority_candidate_from_saved_dns_fallback_probe(self) -> None:
        project = project_record.default_project_record("proj:authoritydnsfallback", "nsid", "dev.cocore.compute.profile")
        project["input_nsids"] = ["dev.cocore.compute.profile"]
        project["manual_seeds"] = [{"type": "nsid", "value": "dev.cocore.compute.profile"}]
        project["authority_domains"] = ["compute.cocore.dev"]
        project["service_url_candidates"] = ["https://cocore.dev"]
        project["service_verification_results"] = [
            {
                "requested_url": "https://cocore.dev",
                "final_url": "https://cocore.dev",
                "status_code": 200,
                "title": "cocore",
                "description": "AT Protocol service",
                "service_status": "live",
                "service_status_confidence": "medium",
                "service_status_evidence": ["http_200", "app_shell_detected"],
                "authority_fallback_from": "https://compute.cocore.dev",
                "authority_fallback_status_code": 0,
                "authority_fallback_fetch_error": "dns_failed",
                "html_extract": "<title>cocore</title>",
            }
        ]

        project = registry.finalize_project_shape(project)

        self.assertIn("https://cocore.dev", project["service_url_candidates"])

    def test_nsid_mention_authority_root_does_not_survive_unverified_guess_prune(self) -> None:
        project = project_record.default_project_record("proj:authorityroot", "nsid", "xyz.tz2at.raw.observation")
        project["input_nsids"] = ["xyz.tz2at.raw.observation"]
        project["authority_domains"] = ["raw.tz2at.xyz"]

        registry.apply_service_probe_result(
            project,
            "https://tz2at.xyz",
            self.probe_result("TZ2AT app for xyz.tz2at.raw.observation"),
        )
        project = registry.finalize_project_shape(project)

        retained_urls = set(project["service_urls"]) | set(project["service_url_candidates"])
        self.assertNotIn("https://tz2at.xyz", project["supported_service_urls"])
        self.assertNotIn("https://tz2at.xyz", retained_urls)

    def test_verified_exact_authority_does_not_confirm_service_without_reverse_evidence(self) -> None:
        project = project_record.default_project_record("proj:verifiedauthority", "nsid", "app.userinput.vote")
        project["input_nsids"] = ["app.userinput.vote"]
        project["resolved_nsids"] = ["app.userinput.vote"]
        project["authority_domains"] = ["userinput.app"]
        project["lexicon_resolution_entries"] = [
            {
                "input_nsid": "app.userinput.vote",
                "resolved_nsid": "app.userinput.vote",
                "authority_domain": "userinput.app",
                "record_uri": "at://did:plc:example/com.atproto.lexicon.schema/app.userinput.vote",
            }
        ]
        result = self.probe_result(
            '<title>userinput.app</title><meta name="description" content="Collect feedback and vote on ideas">'
        )
        result["title"] = "userinput.app"
        result["description"] = "Collect feedback and vote on ideas"
        result["service_status"] = "unverified"
        result["service_status_confidence"] = "low"
        result["service_status_evidence"] = ["http_200"]

        registry.apply_service_probe_result(project, "https://userinput.app", result)
        project = registry.finalize_project_shape(project)

        self.assertEqual(project["website_url"], "")
        self.assertNotIn("https://userinput.app", project["supported_service_urls"])
        self.assertNotIn("https://userinput.app", project["service_urls"])
        self.assertEqual(project["service_status"], "unknown")

    def test_unverified_exact_authority_still_requires_page_evidence(self) -> None:
        project = project_record.default_project_record("proj:unverifiedauthority", "nsid", "app.userinput.vote")
        project["input_nsids"] = ["app.userinput.vote"]
        project["authority_domains"] = ["userinput.app"]
        project["service_url_candidates"] = ["https://userinput.app"]
        result = self.probe_result("<title>userinput.app</title>")
        result["title"] = "userinput.app"

        registry.apply_service_probe_result(project, "https://userinput.app", result)
        project = registry.finalize_project_shape(project)

        self.assertEqual(project["website_url"], "")
        self.assertNotIn("https://userinput.app", project["supported_service_urls"])
        self.assertNotIn("https://userinput.app", project["service_url_candidates"])

    def test_unsupported_authority_root_is_still_pruned(self) -> None:
        project = project_record.default_project_record("proj:authorityroot", "nsid", "xyz.tz2at.raw.observation")
        project["input_nsids"] = ["xyz.tz2at.raw.observation"]
        project["authority_domains"] = ["raw.tz2at.xyz"]
        project["service_url_candidates"] = ["https://tz2at.xyz"]

        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://tz2at.xyz", project["service_url_candidates"])

    def test_confirmed_service_url_is_removed_from_candidate_views(self) -> None:
        project = project_record.default_project_record("proj:serviceoverlap", "service_url", "https://example.com")
        project["service_urls"] = ["https://example.com", "https://app.example.com"]
        project["service_url_candidates"] = ["https://app.example.com"]

        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://app.example.com", project["service_url_candidates"])


class ProjectRegistryProfileUrlTest(unittest.TestCase):
    def test_bluesky_profile_subject_distinguishes_handle_and_did(self) -> None:
        self.assertEqual(
            project_record.extract_bluesky_profile_subject("https://bsky.app/profile/alice.example.com"),
            {"handle": "alice.example.com"},
        )
        self.assertEqual(
            project_record.extract_bluesky_profile_subject("https://bsky.app/profile/did:plc:abcdefghijklmnopqrstuvwx"),
            {"did": "did:plc:abcdefghijklmnopqrstuvwx"},
        )
        self.assertEqual(
            project_record.extract_bluesky_profile_subject("https://bsky.app/profile/did:web:alice.example.com"),
            {"did": "did:web:alice.example.com"},
        )
        self.assertEqual(project_record.extract_bluesky_profile_subject("https://bsky.app/profile/not-a-handle"), {})
        self.assertEqual(
            project_record.extract_bluesky_profile_subject("https://bsky.app/profile/${encodeURIComponent(profileData.handle)}`"),
            {},
        )

    def test_extracted_template_profile_url_does_not_create_developer_candidate(self) -> None:
        project = project_record.default_project_record("proj:templateprofile", "service_url", "https://skyspace.me")

        project_record.record_developer_profile_url(
            project,
            "https://bsky.app/profile/${encodeURIComponent(profileData.handle)}`",
            "service_raw_url:https://skyspace.me",
        )

        self.assertEqual(project["candidate_developer_accounts"], [])
        self.assertNotIn(
            "https://bsky.app/profile/${encodeURIComponent(profileData.handle)}`",
            registry.extract_urls('const url = `https://bsky.app/profile/${encodeURIComponent(profileData.handle)}`;'),
        )

    def test_did_profile_url_becomes_candidate_account_not_service(self) -> None:
        profile_url = "https://bsky.app/profile/did:plc:abcdefghijklmnopqrstuvwx"
        project = project_record.default_project_record("proj:didprofile", "repo_url", "https://github.com/example/app")
        project["website_url"] = profile_url
        project["service_urls"] = [profile_url]

        project = registry.finalize_project_shape(project)

        self.assertEqual(project["website_url"], "")
        self.assertNotIn(profile_url, project["service_urls"])
        self.assertIn(
            {"did": "did:plc:abcdefghijklmnopqrstuvwx", "source": "service_urls:profile_url"},
            project["candidate_developer_accounts"],
        )

    def test_developer_account_normalization_moves_did_out_of_handle_field(self) -> None:
        self.assertEqual(
            project_record.normalize_developer_accounts([
                {"handle": "did:plc:abcdefghijklmnopqrstuvwx", "source": "fixture"},
                "did:plc:zyxwvutsrqponmlkjihgfedc",
            ]),
            [
                {"did": "did:plc:abcdefghijklmnopqrstuvwx", "source": "fixture"},
                {"did": "did:plc:zyxwvutsrqponmlkjihgfedc"},
            ],
        )

    def test_developer_account_normalization_drops_template_handles(self) -> None:
        self.assertEqual(
            project_record.normalize_developer_accounts([
                {"handle": "${encodeURIComponent(profileData.handle)}`", "source": "service_raw_url:https://skyspace.me"},
                {"handle": "Alice.Example.COM", "source": "fixture"},
            ]),
            [{"handle": "alice.example.com", "source": "fixture"}],
        )

    def test_project_shape_drops_template_handles_from_llm_source_evidence(self) -> None:
        project = project_record.default_project_record("proj:templateevidence", "service_url", "https://skyspace.me")
        project["llm_source"] = {
            "evidence": {
                "candidate_developer_accounts": [
                    {"handle": "${encodeURIComponent(profileData.handle)}`", "source": "service_raw_url:https://skyspace.me"},
                    {"handle": "Alice.Example.COM", "source": "fixture"},
                ]
            }
        }

        shaped = project_record.ensure_project_shape(project)

        self.assertEqual(
            shaped["llm_source"]["evidence"]["candidate_developer_accounts"],
            [{"handle": "alice.example.com", "source": "fixture"}],
        )


class ProjectRegistryResourceUrlRecordTest(unittest.TestCase):
    def test_app_resource_roles_cover_distribution_metadata_and_open_union(self) -> None:
        records = project_record.normalize_resource_url_records([
            {"url": "https://example.com/manifest.webmanifest", "role": "webManifest", "confidence": "confirmed"},
            {"url": "https://example.com/changelog", "role": "changelog", "confidence": "confirmed"},
            {"url": "https://apps.apple.com/app/id123", "role": "appStore", "confidence": "confirmed"},
            {"url": "https://play.google.com/store/apps/details?id=example", "role": "playStore", "confidence": "confirmed"},
            {"url": "https://f-droid.org/packages/example", "role": "fdroid", "confidence": "confirmed"},
            {"url": "https://downloads.example.com/app.zip", "role": "distribution", "confidence": "confirmed"},
            {"url": "https://example.com/background", "role": "developmentBackground", "confidence": "candidate"},
        ])

        self.assertEqual(
            [record["role"] for record in records],
            ["appStore", "playStore", "fdroid", "distribution", "changelog", "webManifest", "developmentBackground"],
        )

    def test_resource_role_inference_recognizes_store_and_distribution_urls(self) -> None:
        self.assertEqual(project_record.app_link_role_from_resource_url("https://apps.apple.com/app/id123"), "appStore")
        self.assertEqual(project_record.app_link_role_from_resource_url("https://play.google.com/store/apps/details?id=example"), "playStore")
        self.assertEqual(project_record.app_link_role_from_resource_url("https://f-droid.org/packages/example"), "fdroid")
        self.assertEqual(project_record.app_link_role_from_resource_url("https://example.itch.io/app"), "distribution")

    def test_resource_url_values_use_records_without_compat_view(self) -> None:
        project = project_record.default_project_record("proj:resourcecanonical", "service_url", "https://example.com")
        project["resource_url_records"] = [
            {
                "url": "https://example.com/docs",
                "role": "docs",
                "source": "service_page:https://example.com",
                "observed_at": "",
                "confidence": "confirmed",
            }
        ]

        self.assertEqual(project_record.resource_url_values(project), ["https://example.com/docs"])
        self.assertEqual(
            project_record.resource_url_record_values(project),
            [
                {
                    "url": "https://example.com/docs",
                    "role": "docs",
                    "source": "service_page:https://example.com",
                    "observed_at": "",
                    "confidence": "confirmed",
                }
            ],
        )

    def test_deep_investigation_page_urls_prioritize_resource_records_then_service_paths(self) -> None:
        project = project_record.default_project_record("proj:deepurls", "service_url", "https://example.com")
        project["website_url"] = "https://example.com/app"
        project["service_urls"] = ["https://example.com/app"]
        project["resource_url_records"] = [
            {
                "url": "https://example.com/docs",
                "role": "docs",
                "source": "service_page:https://example.com",
                "observed_at": "",
                "confidence": "confirmed",
            }
        ]

        self.assertEqual(
            registry.deep_investigation_page_urls(project, max_pages=4),
            [
                "https://example.com/docs",
                "https://example.com/about",
                "https://example.com/faq",
                "https://example.com/app",
            ],
        )

    def test_deep_investigation_page_urls_can_use_service_candidates(self) -> None:
        project = project_record.default_project_record("proj:deepcandidate", "nsid", "app.example.post")
        project["service_url_candidates"] = ["https://candidate.example"]

        self.assertEqual(
            registry.deep_investigation_page_urls(project, max_pages=3),
            [
                "https://candidate.example/about",
                "https://candidate.example/docs",
                "https://candidate.example/faq",
            ],
        )

    def test_deep_investigation_page_urls_skip_profile_candidates(self) -> None:
        project = project_record.default_project_record("proj:deepprofile", "repo_url", "https://github.com/example/app")
        project["service_url_candidates"] = ["https://bsky.app/profile/did:plc:abcdefghijklmnopqrstuvwx"]

        self.assertEqual(registry.deep_investigation_page_urls(project), [])

    def test_apply_resource_page_probe_status_records_page_kind_without_confirming_url(self) -> None:
        project = project_record.default_project_record("proj:deeplogin", "service_url", "https://example.com")
        project["service_urls"] = []

        registry.apply_resource_page_probe_status(
            project,
            "https://example.com/login",
            {
                "service_status": "live",
                "service_status_confidence": "medium",
                "service_status_evidence": ["login_surface_detected"],
            },
        )

        self.assertEqual(project["service_status"], "live")
        self.assertEqual(project["service_status_confidence"], "medium")
        self.assertEqual(project["service_status_evidence"], ["login_surface_detected", "deep_page:login"])
        self.assertEqual(project["service_urls"], [])

    def test_confirmed_resource_record_adds_app_link(self) -> None:
        project = project_record.default_project_record("proj:resourceconfirmed", "service_url", "https://example.com")
        registry.add_resource_url(
            project,
            "https://example.com/docs",
            role="docs",
            source="service_page:https://example.com",
            confidence="confirmed",
        )

        links = project_record.build_project_app_links(project)

        self.assertIn({"uri": "https://example.com/docs", "role": "docs", "official": True}, links)

    def test_candidate_resource_record_does_not_add_app_link(self) -> None:
        project = project_record.default_project_record("proj:resourcecandidate", "service_url", "https://example.com")
        registry.add_resource_url(
            project,
            "https://external.example.net/docs",
            role="docs",
            source="service_page:https://example.com",
            confidence="candidate",
        )

        self.assertEqual(project_record.build_project_app_links(project), [])

    def test_confirmed_resource_record_replaces_candidate_record_for_same_url(self) -> None:
        project = project_record.default_project_record("proj:resourcepromote", "service_url", "https://example.com")
        project = project_record.ensure_project_shape(project)

        registry.add_resource_url(
            project,
            "https://example.com/docs",
            role="docs",
            source="resource_page:https://example.com/docs",
            confidence="candidate",
        )
        registry.add_resource_url(
            project,
            "https://example.com/docs",
            role="docs",
            source="service_page:https://example.com",
            confidence="confirmed",
        )

        self.assertEqual(
            project["resource_url_records"],
            [
                {
                    "url": "https://example.com/docs",
                    "role": "docs",
                    "source": "service_page:https://example.com",
                    "observed_at": project["resource_url_records"][0]["observed_at"],
                    "confidence": "confirmed",
                }
            ],
        )

    def test_resource_url_records_keep_representative_budget_per_host_and_role(self) -> None:
        project = project_record.default_project_record("proj:resourcebudget", "service_url", "https://example.com")

        for index in range(10):
            registry.add_resource_url(
                project,
                f"https://example.com/docs/page-{index}",
                role="docs",
                source="service_page:https://example.com",
                confidence="confirmed",
            )
        registry.add_resource_url(
            project,
            "https://example.com/docs",
            role="docs",
            source="service_page:https://example.com",
            confidence="confirmed",
        )

        docs_records = [record for record in project["resource_url_records"] if record["role"] == "docs"]
        self.assertEqual(len(docs_records), 4)
        self.assertEqual(docs_records[0]["url"], "https://example.com/docs")

    def test_first_party_link_collection_records_same_site_docs_as_confirmed(self) -> None:
        project = project_record.default_project_record("proj:resourcefirstparty", "service_url", "https://example.com")

        registry.collect_first_party_links(
            project,
            "https://example.com",
            [
                {"url": "https://example.com/docs", "text": "Docs"},
                {"url": "https://external.example.net/docs", "text": "External docs"},
                {"url": "https://example.attacker.net/docs", "text": "Same label external docs"},
            ],
            "service_page:https://example.com",
        )

        records = {record["url"]: record for record in project["resource_url_records"]}
        self.assertEqual(records["https://example.com/docs"]["confidence"], "confirmed")
        self.assertEqual(records["https://example.com/docs"]["role"], "docs")
        self.assertEqual(records["https://external.example.net/docs"]["confidence"], "candidate")
        self.assertEqual(records["https://example.attacker.net/docs"]["confidence"], "candidate")

    def test_unsupported_service_only_contributes_repository_relationship_evidence(self) -> None:
        project = project_record.default_project_record(
            "proj:relatedsite", "repo_url", "https://github.com/example/adapter"
        )
        registry.confirm_source_code_url(project, "https://github.com/example/adapter", "test")
        project["resource_url_records"] = [
            {
                "url": "https://framework.example/docs/adapter",
                "role": "docs",
                "source": "service_follow_up:https://framework.example/docs",
                "confidence": "confirmed",
            }
        ]
        project["candidate_developer_accounts"] = [
            {"handle": "framework.bsky.social", "source": "service_raw_url:https://framework.example"}
        ]
        project["bluesky_handles"] = ["framework.bsky.social"]
        project["summary_candidates"] = [
            {"value": "Framework docs", "source": "service_meta:https://framework.example"}
        ]

        registry.apply_service_probe_result(
            project,
            "https://framework.example",
            {
                "supports_project": False,
                "status_code": 200,
                "final_url": "https://framework.example",
                "title": "Framework",
                "description": "Framework docs",
                "service_status": "live",
                "service_status_confidence": "medium",
                "service_status_evidence": ["http_200"],
                "redirected": False,
                "urls": ["https://bsky.app/profile/framework.bsky.social"],
                "links": [
                    {"url": "https://github.com/example/adapter", "text": "Source code"},
                    {"url": "https://framework.example/docs", "text": "Docs"},
                    {"url": "https://bsky.app/profile/framework.bsky.social", "text": "Bluesky"},
                ],
            },
        )

        repo_edges = [
            edge
            for edge in registry.trust_graph_core.graph(project).get("edges", [])
            if edge.get("predicate") == "service_links_repo"
        ]
        self.assertEqual(len(repo_edges), 1)
        self.assertEqual(repo_edges[0]["object"]["value"], "https://github.com/example/adapter")
        self.assertEqual(project["resource_url_records"], [])
        self.assertEqual(project["candidate_developer_accounts"], [])
        self.assertEqual(project["bluesky_handles"], [])
        self.assertEqual(project["summary_candidates"], [])

    def test_finalize_removes_public_surfaces_for_saved_related_repository_site(self) -> None:
        project = project_record.default_project_record(
            "proj:relatedsaved", "repo_url", "https://github.com/example/adapter"
        )
        project["resource_url_records"] = [
            {
                "url": "https://framework.example/docs",
                "role": "docs",
                "source": "service_page:https://framework.example",
                "confidence": "confirmed",
            }
        ]
        project["candidate_developer_accounts"] = [
            {"handle": "framework.bsky.social", "source": "service_raw_url:https://framework.example"}
        ]
        project["bluesky_handles"] = ["framework.bsky.social"]
        project["llm_output"] = {
            "repository_link_assessments": [
                {
                    "service_url": "https://framework.example",
                    "repo_url": "https://github.com/example/adapter",
                    "relationship": "related",
                }
            ]
        }

        registry.finalize_project_shape(project)

        self.assertEqual(project["resource_url_records"], [])
        self.assertEqual(project["candidate_developer_accounts"], [])
        self.assertEqual(project["bluesky_handles"], [])

    def test_first_party_link_collection_skips_same_site_content_pages_without_resource_role(self) -> None:
        project = project_record.default_project_record("proj:resourcecontent", "service_url", "https://example.com")

        registry.collect_first_party_links(
            project,
            "https://example.com",
            [
                {"url": "https://example.com/post/did:plc:abc/123", "text": "A post"},
                {"url": "https://example.com/profile/alice", "text": "Alice"},
                {"url": "https://example.com/support/contact", "text": "Support"},
            ],
            "service_page:https://example.com",
        )

        records = {record["url"]: record for record in project["resource_url_records"]}
        self.assertNotIn("https://example.com/post/did:plc:abc/123", records)
        self.assertNotIn("https://example.com/profile/alice", records)
        self.assertEqual(records["https://example.com/support/contact"]["role"], "support")

    def test_deferred_service_origin_blocks_supporting_page_resource(self) -> None:
        project = project_record.default_project_record("proj:deferredresource", "service_url", "https://example.com")
        registry.defer_identifier_on_project(project, "service_url", "https://example.com", note="not this project")

        registry.add_resource_url(
            project,
            "https://example.com/docs",
            role="docs",
            source="service_page:https://example.com",
            confidence="confirmed",
        )

        self.assertTrue(registry.identifier_is_deferred(project, "service_url", "https://example.com/docs"))
        self.assertEqual(project["resource_url_records"], [])

    def test_follow_up_service_probe_records_resource_not_service_url(self) -> None:
        project = project_record.default_project_record("proj:followupresource", "service_url", "https://example.com")
        project["manual_seeds"] = [{"type": "service_url", "value": "https://example.com"}]
        project["website_url"] = "https://example.com"
        project["service_urls"] = ["https://example.com"]

        registry.apply_service_probe_result(
            project,
            "https://example.com/docs",
            {
                "lane": "follow_up",
                "status_code": 200,
                "final_url": "https://example.com/docs",
                "title": "Docs",
                "description": "Docs for Example",
                "service_status": "live",
                "service_status_confidence": "medium",
                "service_status_evidence": ["docs_only_surface_detected"],
                "redirected": False,
                "urls": [],
                "links": [],
            },
        )

        self.assertEqual(project["service_urls"], ["https://example.com"])
        self.assertEqual(project_record.resource_url_values(project), ["https://example.com/docs"])

    def test_future_service_identity_excludes_named_noncanonical_pages(self) -> None:
        self.assertTrue(registry.service_url_is_supporting_page("https://example.com/docs/intro"))
        self.assertTrue(registry.service_url_is_supporting_page("https://example.com/about"))
        self.assertTrue(registry.service_url_is_supporting_page("https://example.com/login"))
        self.assertTrue(registry.service_url_is_supporting_page("https://example.com/signup"))
        self.assertTrue(registry.service_url_is_supporting_page("https://example.com/register"))
        self.assertFalse(registry.service_url_is_supporting_page("https://example.com/app"))
        self.assertFalse(registry.service_url_is_supporting_page("https://example.com/app/login"))

    def test_finalize_moves_supporting_service_paths_to_resource_records(self) -> None:
        project = project_record.default_project_record("proj:supportingpagecleanup", "service_url", "https://example.com")
        project["website_url"] = "https://example.com/docs"
        project["service_urls"] = ["https://example.com", "https://example.com/docs", "https://example.com/about"]
        project["supported_service_urls"] = ["https://example.com/docs"]
        project["relation_evidence"] = [
            {
                "relation": {
                    "subject": {"type": "project", "value": "proj:supportingpagecleanup"},
                    "predicate": "operates_service",
                    "object": {"type": "service", "value": "https://example.com/docs"},
                    "locator": "https://example.com/docs",
                    "method": "static_probe",
                },
                "source": "service_static:https://example.com/docs",
                "classification": "confirmed",
            }
        ]

        finalized = registry.finalize_project_shape(project)

        self.assertEqual(finalized["website_url"], "https://example.com/docs")
        self.assertEqual(finalized["service_urls"], ["https://example.com"])
        self.assertEqual(finalized["supported_service_urls"], [])
        self.assertEqual(finalized["relation_evidence"], [])
        resource_urls = project_record.resource_url_values(finalized)
        self.assertNotIn("https://example.com/docs", resource_urls)
        self.assertIn("https://example.com/about", resource_urls)

    def test_candidate_repo_link_does_not_support_service_probe(self) -> None:
        project = project_record.default_project_record("proj:candidatereposervice", "service_url", "https://kimbia.app")
        project["name"] = "Kimbia"
        project["manual_seeds"] = [{"type": "service_url", "value": "https://kimbia.app"}]
        project["candidate_source_code_urls"] = ["https://github.com/mistweaverco/kimbia"]
        result = {
            "status_code": 200,
            "title": "Kimbia",
            "description": "A minimal task runner.",
            "html": "A minimal task runner.",
            "links": [{"url": "https://github.com/mistweaverco/kimbia", "text": "Source"}],
        }

        self.assertFalse(registry.service_probe_supports_project(project, "https://kimbia.mwco.app", result))

    def test_unlabelled_first_party_tangled_repo_link_waits_for_llm_before_trust_probe(self) -> None:
        project = project_record.default_project_record("proj:seams", "service_url", "https://seams.so")
        project["name"] = "Seams"
        project["website_url"] = "https://seams.so"
        project["service_urls"] = ["https://seams.so"]

        registry.collect_first_party_links(
            project,
            "https://seams.so",
            [{"url": "https://tangled.org/@sealight.xyz/seams.so", "text": ""}],
            "service_page:https://seams.so",
        )

        self.assertEqual(
            project["candidate_source_code_urls"],
            ["https://tangled.org/sealight.xyz/seams.so"],
        )
        self.assertEqual(registry.candidate_repo_trust_probe_urls(project), [])
        evidence = registry.build_llm_evidence_bundle(project)
        registry.apply_repository_link_assessments(project, {
            "repository_link_assessments": [{
                "service_url": "https://seams.so",
                "repo_url": "https://tangled.org/sealight.xyz/seams.so",
                "relationship": "same_project",
                "reason": "The names and first-party link identify the same project.",
            }],
        }, evidence)
        self.assertEqual(
            registry.candidate_repo_trust_probe_urls(project),
            ["https://tangled.org/sealight.xyz/seams.so"],
        )

    def test_candidate_repo_trust_inspection_confirms_mutual_link_without_applying_content(self) -> None:
        project = project_record.default_project_record("proj:seams", "service_url", "https://seams.so")
        project["name"] = "Seams"
        project["website_url"] = "https://seams.so"
        project["service_urls"] = ["https://seams.so"]
        registry.add_manual_seed(project, "service_url", "https://seams.so", source="test")
        registry.add_confirmed_identifier_decision(project, "service_url", "https://seams.so", source="test")
        registry.collect_first_party_links(
            project,
            "https://seams.so",
            [{"url": "https://tangled.org/@sealight.xyz/seams.so", "text": ""}],
            "service_page:https://seams.so",
        )
        repo_url = project["candidate_source_code_urls"][0]
        evidence = registry.build_llm_evidence_bundle(project)
        registry.apply_repository_link_assessments(project, {
            "repository_link_assessments": [{
                "service_url": "https://seams.so",
                "repo_url": repo_url,
                "relationship": "same_project",
                "reason": "The names and first-party link identify the same project.",
            }],
        }, evidence)

        with mock.patch.object(
            repository_sources,
            "fetch_tangled_repo",
            return_value={
                "repo": {
                    "full_name": "sealight.xyz/seams.so",
                    "name": "seams.so",
                    "description": "Candidate content must not be applied before confirmation.",
                },
                "readme_text": "Project website: https://seams.so",
                "tree": [],
                "default_branch": "main",
            },
        ):
            bundle = registry.collect_tangled_repo_evidence(project, repo_url, trust_only=True)

        self.assertFalse(any(record.kind == "tangled_repo_inspection" for record in bundle.records))
        self.assertEqual(project["summary_candidates"], [])
        self.assertEqual(registry.apply_candidate_repo_trust_evidence(project, repo_url, bundle), 1)
        self.assertTrue(
            registry.confirm_source_code_url_with_trust(
                project,
                repo_url,
                "test",
                relation="mutual_first_party_link",
            )
        )
        self.assertEqual(project["source_code_urls"], [repo_url])
        self.assertEqual(project["summary_candidates"], [])

    def test_candidate_repo_trust_inspection_rejects_unrelated_reverse_link(self) -> None:
        project = project_record.default_project_record("proj:seams", "service_url", "https://seams.so")
        project["name"] = "Seams"
        project["website_url"] = "https://seams.so"
        project["service_urls"] = ["https://seams.so"]
        registry.add_manual_seed(project, "service_url", "https://seams.so", source="test")
        registry.add_confirmed_identifier_decision(project, "service_url", "https://seams.so", source="test")
        registry.collect_first_party_links(
            project,
            "https://seams.so",
            [{"url": "https://tangled.org/@sealight.xyz/seams.so", "text": ""}],
            "service_page:https://seams.so",
        )
        repo_url = project["candidate_source_code_urls"][0]
        bundle = registry.EvidenceBundle(project_key=project["project_key"], collected_at="2026-07-18T00:00:00+00:00")
        bundle.add_record(registry.investigation_core.evidence_record(
            kind="relation",
            source=f"tangled_readme:{repo_url}",
            classification="candidate",
            observed_at="2026-07-18T00:00:00+00:00",
            value={
                "subject": {"type": "repo", "value": repo_url},
                "predicate": "source_repository",
                "object": {"type": "website", "value": "https://unrelated.example"},
                "locator": repo_url,
                "method": "readme_link",
            },
        ))

        self.assertEqual(registry.apply_candidate_repo_trust_evidence(project, repo_url, bundle), 0)
        self.assertFalse(
            registry.confirm_source_code_url_with_trust(
                project,
                repo_url,
                "test",
                relation="mutual_first_party_link",
            )
        )
        self.assertEqual(project["source_code_urls"], [])

    def test_candidate_repo_does_not_supply_project_identity_tokens(self) -> None:
        project = project_record.default_project_record("proj:example", "service_url", "https://example.com")
        project["name"] = "Example"
        project["candidate_source_code_urls"] = ["https://github.com/candidate-owner/untrusted-repo-name"]

        tokens = registry.project_identity_anchor_tokens(project)

        self.assertNotIn("candidateowner", tokens)
        self.assertNotIn("untrustedreponame", tokens)

    def test_confirmed_repo_link_supports_service_probe(self) -> None:
        project = project_record.default_project_record("proj:confirmedreposervice", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
        registry.add_confirmed_identifier_decision(project, "repo_url", "https://github.com/example/app", source="test")
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["relation_evidence"] = [
            {
                "relation": {
                    "subject": {"type": "repo", "value": "https://github.com/example/app"},
                    "predicate": "repository_homepage",
                    "object": {"type": "website", "value": "https://app.example.com"},
                    "locator": "README",
                    "method": "github_homepage",
                },
                "source": "github_repo_metadata:https://github.com/example/app",
                "classification": "confirmed",
            }
        ]
        result = {
            "status_code": 200,
            "title": "Example App",
            "description": "Project homepage.",
            "html": "Project homepage.",
            "links": [{"url": "https://github.com/example/app", "text": "Source"}],
        }

        self.assertTrue(registry.service_probe_supports_project(project, "https://app.example.com", result))

    def test_confirmed_repo_link_without_repo_to_service_reference_does_not_support_service_probe(self) -> None:
        project = project_record.default_project_record("proj:repoonlyservice", "repo_url", "https://github.com/example/app")
        project["source_code_urls"] = ["https://github.com/example/app"]
        result = {
            "status_code": 200,
            "title": "Example App",
            "description": "Project homepage.",
            "html": "Project homepage.",
            "links": [{"url": "https://github.com/example/app", "text": "Source"}],
        }

        self.assertFalse(registry.service_probe_supports_project(project, "https://app.example.com", result))

    def test_ambiguous_repo_link_waits_for_llm_relationship_assessment(self) -> None:
        project = project_record.default_project_record("proj:ambiguousreposervice", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
        registry.add_confirmed_identifier_decision(project, "repo_url", "https://github.com/example/app", source="test")
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["relation_evidence"] = [{
            "relation": {
                "subject": {"type": "repo", "value": "https://github.com/example/app"},
                "predicate": "repository_homepage",
                "object": {"type": "website", "value": "https://app.example.com"},
                "locator": "README",
                "method": "github_homepage",
            },
            "source": "github_repo_metadata:https://github.com/example/app",
            "classification": "confirmed",
        }]
        result = {
            "status_code": 200,
            "title": "Example App",
            "description": "Project homepage.",
            "html": "Project homepage.",
            "links": [{"url": "https://github.com/example/app", "text": "GitHub"}],
        }

        self.assertFalse(registry.service_probe_supports_project(project, "https://app.example.com", result))
        evidence = registry.build_llm_evidence_bundle(project)
        self.assertEqual(len(evidence["repository_link_candidates"]), 1)

        registry.apply_llm_output(project, {
            "repository_link_assessments": [{
                "service_url": "https://app.example.com",
                "repo_url": "https://github.com/example/app",
                "relationship": "same_project",
                "reason": "The page and repository describe Example App.",
            }],
        }, {}, evidence)

        self.assertEqual(project["website_url"], "")
        self.assertIn("https://app.example.com", project["service_urls"])

    def test_related_repo_link_does_not_complete_trust_path(self) -> None:
        project = project_record.default_project_record("proj:relatedreposervice", "repo_url", "https://github.com/example/plugin")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/plugin", source="test")
        registry.add_confirmed_identifier_decision(project, "repo_url", "https://github.com/example/plugin", source="test")
        project["source_code_urls"] = ["https://github.com/example/plugin"]
        project["relation_evidence"] = [{
            "relation": {
                "subject": {"type": "repo", "value": "https://github.com/example/plugin"},
                "predicate": "repository_homepage",
                "object": {"type": "website", "value": "https://framework.example"},
                "locator": "README",
                "method": "github_homepage",
            },
            "source": "github_repo_metadata:https://github.com/example/plugin",
            "classification": "confirmed",
        }]
        registry.service_probe_supports_project(project, "https://framework.example", {
            "status_code": 200,
            "title": "Framework",
            "description": "Extensions for Framework.",
            "html": "Extensions for Framework.",
            "links": [{"url": "https://github.com/example/plugin", "text": "plugin"}],
        })
        evidence = registry.build_llm_evidence_bundle(project)

        registry.apply_llm_output(project, {
            "repository_link_assessments": [{
                "service_url": "https://framework.example",
                "repo_url": "https://github.com/example/plugin",
                "relationship": "related",
                "reason": "The repository is one extension listed by the framework site.",
            }],
        }, {}, evidence)

        self.assertNotEqual(project["website_url"], "https://framework.example")
        self.assertNotIn("https://framework.example", project["service_urls"])

    def test_reprobe_replaces_previous_explicit_source_link_classification(self) -> None:
        project = project_record.default_project_record("proj:reprobeservice", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
        registry.add_confirmed_identifier_decision(project, "repo_url", "https://github.com/example/app", source="test")
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["relation_evidence"] = [{
            "relation": {
                "subject": {"type": "repo", "value": "https://github.com/example/app"},
                "predicate": "repository_homepage",
                "object": {"type": "website", "value": "https://app.example.com"},
                "locator": "README",
                "method": "github_homepage",
            },
            "source": "github_repo_metadata:https://github.com/example/app",
            "classification": "confirmed",
        }]
        base_result = {
            "status_code": 200,
            "title": "Example App",
            "description": "Project homepage.",
            "html": "Project homepage.",
        }

        self.assertTrue(registry.service_probe_supports_project(project, "https://app.example.com", {
            **base_result,
            "links": [{"url": "https://github.com/example/app", "text": "Source"}],
        }))
        registry.service_probe_supports_project(project, "https://app.example.com", {
            **base_result,
            "links": [{"url": "https://github.com/example/app", "text": "plugin"}],
        })
        matching_edges = [
            edge
            for edge in registry.trust_graph_core.graph(project).get("edges", [])
            if edge.get("predicate") == "service_links_repo"
            and edge.get("source") == "service_probe:https://app.example.com"
        ]
        self.assertEqual(len(matching_edges), 1)
        self.assertEqual(matching_edges[0]["classification"], "candidate")
        self.assertFalse(matching_edges[0]["eligible_for_auto_confirm"])

    def test_saved_service_observation_replaces_stale_source_relation(self) -> None:
        project = project_record.default_project_record("proj:relationreprobe", "service_url", "https://app.example.com")
        project["name"] = "Example"
        project["website_url"] = "https://app.example.com"
        project["service_urls"] = ["https://app.example.com"]
        project["candidate_source_code_urls"] = ["https://tangled.org/example.com/example"]
        project["relation_evidence"] = [{
            "relation": {
                "subject": {"type": "website", "value": "https://app.example.com"},
                "predicate": "source_repository",
                "object": {"type": "repo", "value": "https://tangled.org/example.com/example"},
                "locator": "https://app.example.com",
                "method": "first_party_link",
            },
            "source": "service_page:https://app.example.com",
            "classification": "confirmed",
            "observed_at": "2026-07-01T00:00:00+00:00",
        }]
        bundle = registry.service_core.collect_service_probe_evidence(
            project["project_key"],
            "https://app.example.com",
            {
                "status_code": 200,
                "final_url": "https://app.example.com",
                "title": "Example",
                "description": "Example service.",
                "links": [{"url": "https://tangled.org/example.com/example", "text": ""}],
                "urls": [],
                "html": "Example service.",
            },
            observed_at="2026-07-18T00:00:00+00:00",
            supports_project=True,
        )

        registry.apply_service_evidence(project, bundle)

        source_relations = [
            item
            for item in project["relation_evidence"]
            if (item.get("relation") or {}).get("predicate") == "source_repository"
            and item.get("source") == "service_page:https://app.example.com"
        ]
        self.assertEqual(source_relations, [])
        self.assertEqual(len(registry.build_llm_evidence_bundle(project)["repository_link_candidates"]), 1)

    def test_nsid_text_mention_alone_does_not_support_service_probe(self) -> None:
        project = project_record.default_project_record("proj:whitewind", "nsid", "com.whtwnd.blog.entry")
        project["input_nsids"] = ["com.whtwnd.blog.entry"]
        result = {
            "status_code": 200,
            "title": "GreenGale",
            "description": "WhiteWind-compatible editor.",
            "html": "GreenGale supports com.whtwnd.blog.entry records.",
            "links": [],
        }

        self.assertFalse(registry.service_probe_supports_project(project, "https://greengale.example.com", result))

    def test_verified_authority_domain_alone_does_not_support_service_probe(self) -> None:
        project = project_record.default_project_record("proj:authorityonlyservice", "repo_url", "https://github.com/example/app")
        project["lexicon_resolution_entries"] = [
            {
                "input_nsid": "app.example.post",
                "resolved_nsid": "app.example.post",
                "repo_did": "did:plc:example",
                "authority_domain": "example.com",
                "record_uri": "at://did:plc:example/com.atproto.lexicon.schema/app.example.post",
            }
        ]
        project["authority_domains"] = ["example.com"]
        result = {
            "status_code": 200,
            "title": "Example",
            "description": "A live website.",
            "html": "A live website.",
            "links": [],
        }

        self.assertFalse(registry.service_probe_supports_project(project, "https://example.com", result))

    def test_bluesky_profile_link_alone_does_not_support_service_probe(self) -> None:
        project = project_record.default_project_record("proj:profilelinkservice", "repo_url", "https://github.com/example/app")
        project["bluesky_handles"] = ["example.com"]
        result = {
            "status_code": 200,
            "title": "Example",
            "description": "A live website.",
            "html": "A live website.",
            "links": [{"url": "https://bsky.app/profile/example.com", "text": "Bluesky"}],
        }

        self.assertFalse(registry.service_probe_supports_project(project, "https://example.com", result))

    def test_self_claimed_atproto_markers_do_not_support_service_probe(self) -> None:
        project = project_record.default_project_record("proj:selfclaimservice", "repo_url", "https://github.com/example/app")
        project["name"] = "Example"
        project["authority_domains"] = ["example.com"]
        result = {
            "status_code": 200,
            "title": "Example",
            "description": "Example is an atproto service for example.com.",
            "html": "Example is an atproto service for example.com.",
            "links": [],
        }

        self.assertFalse(registry.service_probe_supports_project(project, "https://example.com", result))

    def test_finalize_removes_deferred_service_operates_relation(self) -> None:
        project = project_record.default_project_record("proj:guardedrelation", "service_url", "https://example.com")
        registry.defer_identifier_on_project(project, "service_url", "https://other.example.com")
        project["service_urls"] = ["https://other.example.com"]
        project["supported_service_urls"] = ["https://other.example.com/docs"]
        project["resource_url_records"] = [
            {
                "url": "https://other.example.com/docs",
                "role": "docs",
                "source": "service_follow_up:https://other.example.com/docs",
                "observed_at": "2026-06-25T00:00:00+00:00",
                "confidence": "confirmed",
            }
        ]
        project["evidence_urls"] = ["https://other.example.com/docs"]
        project["relation_evidence"] = [
            {
                "relation": {
                    "subject": {"type": "project", "value": "proj:guardedrelation"},
                    "predicate": "operates_service",
                    "object": {"type": "service", "value": "https://other.example.com/docs"},
                    "locator": "https://other.example.com/docs",
                    "method": "follow_up_probe",
                },
                "source": "service_follow_up:https://other.example.com/docs",
                "classification": "confirmed",
            }
        ]

        finalized = registry.finalize_project_shape(project)

        self.assertEqual(finalized["relation_evidence"], [])
        self.assertNotIn("https://other.example.com", finalized["service_urls"])
        self.assertEqual(finalized["supported_service_urls"], [])
        self.assertEqual(
            [record["url"] for record in finalized["resource_url_records"]],
            ["https://other.example.com/docs"],
        )
        self.assertIn("https://other.example.com/docs", finalized["evidence_urls"])


class ProjectRegistryLexiconScopeTest(unittest.TestCase):
    def nsid_project(self) -> dict:
        project = project_record.default_project_record("proj:lexiconscope", "nsid", "xyz.tz2at.raw.observation")
        project["input_nsids"] = ["xyz.tz2at.raw.observation"]
        project["authority_domains"] = ["raw.tz2at.xyz"]
        project["category"] = "lexicon"
        return project

    def test_shared_lexicon_positioning_preserves_lexicon_category(self) -> None:
        project = self.nsid_project()
        project["summary"] = "第三者の相互運用を目的とする共有レキシコン。"
        project["website_url"] = "https://standard.site"

        project_record.reclassify_service_specific_lexicon(project)

        self.assertEqual(project["lexicon_scope"], "shared")
        self.assertEqual(project["category"], "lexicon")

    def test_matching_service_surface_reclassifies_service_specific_lexicon(self) -> None:
        project = self.nsid_project()
        project["supported_service_urls"] = ["https://tz2at.xyz"]
        project["service_url_candidates"] = ["https://tz2at.xyz"]

        project_record.reclassify_service_specific_lexicon(project)

        self.assertEqual(project["lexicon_scope"], "service_specific")
        self.assertEqual(project["category"], "project")

    def test_resource_url_records_do_not_count_as_service_surface(self) -> None:
        project = self.nsid_project()
        project["resource_url_records"] = [
            {
                "url": "https://tz2at.xyz/docs",
                "role": "docs",
                "source": "service_page:https://tz2at.xyz",
                "observed_at": "",
                "confidence": "confirmed",
            }
        ]

        project_record.reclassify_service_specific_lexicon(project)

        self.assertEqual(project["lexicon_scope"], "unknown")
        self.assertEqual(project["category"], "lexicon")

    def test_nsid_only_candidate_keeps_unknown_scope(self) -> None:
        project = self.nsid_project()

        project_record.reclassify_service_specific_lexicon(project)

        self.assertEqual(project["lexicon_scope"], "unknown")
        self.assertEqual(project["category"], "lexicon")
        self.assertIn("shared_lexicon_positioning_not_found", project["lexicon_scope_evidence"])

    def test_finalize_downgrades_unaccepted_repo_defines_schema_relation(self) -> None:
        project = project_record.default_project_record("proj:repoonlyschema", "repo_url", "https://github.com/example/app")
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["relation_evidence"] = [
            {
                "relation": {
                    "subject": {"type": "repo", "value": "https://github.com/example/app"},
                    "predicate": "defines_schema",
                    "object": {"type": "nsid", "value": "org.other.post"},
                    "locator": "lexicons/org/other/post.json",
                    "method": "lexicon_json",
                },
                "source": "github_repo:https://github.com/example/app",
                "classification": "confirmed",
                "observed_at": "2026-06-25T00:00:00+00:00",
            }
        ]

        finalized = registry.finalize_project_shape(project)

        relation_record = finalized["relation_evidence"][0]
        self.assertEqual(relation_record["relation"]["predicate"], "mentions_collection")
        self.assertEqual(relation_record["classification"], "candidate")
        self.assertEqual(relation_record["context"]["downgraded_from"], "defines_schema")

    def test_finalize_keeps_defines_schema_for_accepted_repo_and_nsid(self) -> None:
        project = project_record.default_project_record("proj:acceptedschema", "repo_url", "https://github.com/example/app")
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["manual_seeds"] = [
            {"type": "repo_url", "value": "https://github.com/example/app"},
            {"type": "nsid", "value": "app.example.post"},
        ]
        project["input_nsids"] = ["app.example.post"]
        project["relation_evidence"] = [
            {
                "relation": {
                    "subject": {"type": "repo", "value": "https://github.com/example/app"},
                    "predicate": "defines_schema",
                    "object": {"type": "nsid", "value": "app.example.post"},
                    "locator": "lexicons/app/example/post.json",
                    "method": "lexicon_json",
                },
                "source": "github_repo:https://github.com/example/app",
                "classification": "candidate",
                "observed_at": "2026-06-25T00:00:00+00:00",
            }
        ]

        finalized = registry.finalize_project_shape(project)

        relation_record = finalized["relation_evidence"][0]
        self.assertEqual(relation_record["relation"]["predicate"], "defines_schema")
        self.assertEqual(relation_record["classification"], "confirmed")

    def test_llm_evidence_exposes_lexicon_scope(self) -> None:
        project = self.nsid_project()
        project["resource_url_records"] = [
            {
                "url": "https://tz2at.xyz/docs",
                "role": "docs",
                "source": "service_page:https://tz2at.xyz",
                "observed_at": "",
                "confidence": "confirmed",
            }
        ]
        project["candidate_developer_accounts"] = [
            {"handle": "tz2at.bsky.social", "source": "bsky_actor_search:tz2at"}
        ]
        project_record.apply_lexicon_scope_state(project)

        evidence = registry.build_llm_evidence_bundle(project)

        self.assertEqual(evidence["resource_url_records"][0]["confidence"], "confirmed")
        self.assertEqual(evidence["lexicon_scope"], "unknown")
        self.assertEqual(
            evidence["lexicon_scope_evidence"],
            ["shared_lexicon_positioning_not_found"],
        )
        self.assertEqual(
            evidence["candidate_developer_accounts"],
            [{"handle": "tz2at.bsky.social", "source": "bsky_actor_search:tz2at"}],
        )
        self.assertNotIn("evidence_budget", evidence)

    def test_llm_evidence_does_not_feed_prior_llm_scope_back_into_prompt(self) -> None:
        project = self.nsid_project()
        project["lexicon_scope"] = "shared"
        project["lexicon_scope_evidence"] = ["llm_output:shared"]

        evidence = registry.build_llm_evidence_bundle(project)

        self.assertEqual(evidence["lexicon_scope"], "unknown")
        self.assertEqual(evidence["lexicon_scope_evidence"], [])

    def test_llm_evidence_exposes_current_saved_profile(self) -> None:
        project = self.nsid_project()
        project.update(
            {
                "tagline": "プロフィールスキーマ",
                "tagline_is_provisional": True,
                "summary": "プロフィール用レコードスキーマを定義する。",
                "features": ["表示名", "自己紹介"],
            }
        )

        evidence = registry.build_llm_evidence_bundle(project)

        self.assertEqual(
            evidence["current_profile"],
            {
                "tagline": "プロフィールスキーマ",
                "tagline_is_provisional": True,
                "summary": "プロフィール用レコードスキーマを定義する。",
                "features": ["表示名", "自己紹介"],
            },
        )

    def test_llm_evidence_has_deterministic_byte_budget(self) -> None:
        project = self.nsid_project()
        project["candidate_developer_accounts"] = [
            {
                "handle": f"candidate-{index}.example.com",
                "source": f"bsky_actor_search:candidate-{index}",
            }
            for index in range(500)
        ]
        project["summary_candidates"] = [
            {"value": f"Summary candidate {index} " + "x" * 300, "source": "test"}
            for index in range(100)
        ]
        project["summary_candidates"][0]["value"] = "x" * 10_000
        project["tagline_candidates"] = [
            {"value": f"Tagline candidate {index} " + "y" * 200, "source": "test"}
            for index in range(100)
        ]
        project["evidence_urls"] = [
            f"https://example.com/evidence/{index}" for index in range(500)
        ]

        evidence = registry.build_llm_evidence_bundle(project)
        repeated = registry.build_llm_evidence_bundle(project)
        triage_prompt, triage_evidence = registry.build_triage_prompt(project)
        planning_prompt, planning_evidence = registry.build_investigation_prompt(project)

        self.assertEqual(evidence, repeated)
        self.assertEqual(evidence, triage_evidence)
        self.assertEqual(evidence["candidate_source_code_urls"], [])
        self.assertEqual(planning_evidence["candidate_source_code_urls"], [])
        self.assertLessEqual(
            llm_evidence_core.evidence_bytes(evidence),
            llm_evidence_core.LLM_EVIDENCE_MAX_BYTES,
        )
        self.assertLessEqual(len(triage_prompt.encode("utf-8")), 32 * 1024)
        self.assertLessEqual(len(planning_prompt.encode("utf-8")), 32 * 1024)
        self.assertEqual(evidence["project_key"], project["project_key"])
        self.assertEqual(evidence["input_nsids"], project["input_nsids"])
        budget = evidence["evidence_budget"]
        self.assertEqual(budget["max_bytes"], llm_evidence_core.LLM_EVIDENCE_MAX_BYTES)
        self.assertEqual(
            budget["truncated_fields"]["candidate_developer_accounts"]["observed_count"],
            500,
        )
        self.assertLess(
            budget["truncated_fields"]["candidate_developer_accounts"]["included_count"],
            500,
        )
        self.assertLessEqual(
            budget["truncated_fields"]["candidate_developer_accounts"]["included_count"],
            llm_evidence_core.LLM_EVIDENCE_COUNT_LIMITS["candidate_developer_accounts"],
        )
        self.assertEqual(evidence["candidate_developer_accounts"][0]["handle"], "candidate-0.example.com")
        self.assertEqual(evidence["candidate_developer_accounts"][-1]["handle"], "candidate-499.example.com")
        self.assertGreater(budget["string_values_truncated"], 0)
        self.assertLessEqual(
            len(evidence["summary_candidates"][0]["value"].encode("utf-8")),
            llm_evidence_core.LLM_EVIDENCE_STRING_MAX_BYTES,
        )

    def test_candidate_repo_is_available_to_investigation_planning_but_not_triage(self) -> None:
        project = self.nsid_project()
        project["name"] = "Candidate"
        project["candidate_source_code_urls"] = ["https://github.com/example/candidate"]

        _triage_prompt, triage_evidence = registry.build_triage_prompt(project)
        _planning_prompt, planning_evidence = registry.build_investigation_prompt(project)

        self.assertEqual(triage_evidence["candidate_source_code_urls"], [])
        self.assertEqual(
            planning_evidence["candidate_source_code_urls"],
            ["https://github.com/example/candidate"],
        )

    def test_triage_prompt_defines_library_by_role(self) -> None:
        prompt, _evidence = registry.build_triage_prompt(self.nsid_project())

        self.assertIn("Category contract:", prompt)
        self.assertIn("primary use is importing, linking, or embedding reusable code", prompt)
        self.assertIn("`infra_tool` is not limited to hosted or continuously running infrastructure", prompt)
        self.assertIn("A plugin is `project` when end users use it directly", prompt)
        self.assertIn("The selected category must agree with `summary`", prompt)
        self.assertIn("Do not use `category=lexicon` merely because the seed is an NSID", prompt)
        self.assertIn("`current_profile` is previous editorial wording", prompt)
        self.assertIn("independent app or service only uses ATProto", prompt)
        self.assertIn("Before returning JSON, check `category` against `summary`", prompt)

    def test_triage_prompt_limits_lexicon_category_to_documented_shared_schema(self) -> None:
        prompt, _evidence = registry.build_triage_prompt(self.nsid_project())

        self.assertIn("shared convention intended for third-party implementation", prompt)
        self.assertIn("schema sample, record sample, or unresolved schema namespace alone", prompt)
        self.assertIn("schema structure and namespace naming alone are insufficient", prompt)


class ProjectRegistryLlmParserTest(unittest.TestCase):
    def test_strip_json_fence_extracts_harmony_final_channel(self) -> None:
        content = (
            "<|channel|>analysis<|message|>Reasoning"
            "<|end|><|start|>assistant<|channel|>final<|message|>"
            "{\"category\":\"project\"}<|end|>"
        )

        self.assertEqual(
            llm_transport_core.strip_json_fence(content),
            '{"category":"project"}',
        )

    def test_parse_chat_completion_message_content(self) -> None:
        payload = {"choices": [{"message": {"content": "chat text"}}]}

        self.assertEqual(llm_transport_core.parse_openai_compatible_response(payload), "chat text")

    def test_parse_responses_output_text(self) -> None:
        payload = {"output_text": "responses text"}

        self.assertEqual(llm_transport_core.parse_openai_compatible_response(payload), "responses text")

    def test_parse_responses_output_content_text(self) -> None:
        payload = {"output": [{"content": [{"type": "output_text", "text": "nested text"}]}]}

        self.assertEqual(llm_transport_core.parse_openai_compatible_response(payload), "nested text")

    def test_parse_responses_ignores_reasoning_output(self) -> None:
        payload = {
            "output": [
                {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "analysis"}]},
                {"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]},
            ]
        }

        self.assertEqual(llm_transport_core.parse_openai_compatible_response(payload), '{"ok":true}')

    def test_parse_completion_text(self) -> None:
        payload = {"choices": [{"text": "completion text"}]}

        self.assertEqual(llm_transport_core.parse_openai_compatible_response(payload), "completion text")

    def test_parse_empty_response_raises(self) -> None:
        with self.assertRaises(ValueError):
            llm_transport_core.parse_openai_compatible_response({"output": [{"content": []}]})


class ProjectRegistryMergeScoringTest(unittest.TestCase):
    def confidence_for(self, reasons: dict[str, list[str]]) -> tuple[str, bool]:
        score = registry.merge_score_from_reasons(reasons)
        return registry.merge_confidence_from_reasons(reasons, score)

    def test_same_service_url_is_high_confidence_auto_merge(self) -> None:
        self.assertEqual(self.confidence_for({"service_urls": ["https://example.com"]}), ("high", True))

    def test_repo_only_match_is_medium_confidence_without_auto_merge(self) -> None:
        self.assertEqual(self.confidence_for({"source_code_urls": ["https://github.com/example/app"]}), ("medium", False))

    def test_domain_identity_with_alias_is_medium_confidence_auto_merge(self) -> None:
        self.assertEqual(
            self.confidence_for({"domain_identity_tokens": ["example"], "merge_aliases": ["example"]}),
            ("medium", True),
        )

    def test_observed_lexicon_authority_does_not_create_medium_merge_candidate(self) -> None:
        source = project_record.default_project_record("proj:sourceleaflet", "repo_url", "https://github.com/WaveringAna/atproto-ui")
        source["name"] = "Leaflet"
        source["authority_domains"] = ["leaflet.pub", "blocks.leaflet.pub", "graph.leaflet.pub"]
        source["input_nsids"] = ["pub.leaflet.document", "pub.leaflet.comment"]
        source["lexicons"] = ["pub.leaflet.document", "pub.leaflet.comment"]
        source["service_url_candidates"] = ["https://leaflet.pub", "https://blocks.leaflet.pub"]
        source["source_code_urls"] = ["https://github.com/WaveringAna/atproto-ui"]

        target = project_record.default_project_record("proj:targetleaflet", "repo_url", "https://github.com/ETJeanMachine/jeanmachine.dev")
        target["name"] = "Leaflet"
        target["authority_domains"] = ["leaflet.pub"]
        target["input_nsids"] = ["pub.leaflet.document"]
        target["lexicons"] = ["pub.leaflet.document"]
        target["service_url_candidates"] = ["https://leaflet.pub"]
        target["source_code_urls"] = ["https://github.com/ETJeanMachine/jeanmachine.dev"]

        candidates = registry.compute_duplicate_candidates(None, source, projects=[source, target])

        pair = next(item for item in candidates if item["project_key"] == "proj:targetleaflet")
        self.assertEqual(pair["reasons"], {"merge_aliases": ["leaflet"]})
        self.assertEqual(pair["merge_confidence"], "low")
        self.assertFalse(pair["auto_merge_ready"])

    def test_inspect_merge_pair_does_not_build_global_duplicate_index(self) -> None:
        source = project_record.default_project_record("proj:mergesource", "repo_url", "https://github.com/example/source")
        source["name"] = "Example Source"
        target = project_record.default_project_record("proj:mergetarget", "repo_url", "https://github.com/example/target")
        target["name"] = "Example Target"

        original_list_projects = project_record.list_projects
        original_build_index = registry.build_duplicate_candidate_index

        def fail_list_projects(_conn: object) -> list[dict[str, object]]:
            raise AssertionError("pair inspection should not load all projects")

        def fail_build_index(_items: list[dict[str, object]]) -> tuple[dict[str, dict[str, set[str]]], dict[str, dict[str, set[str]]]]:
            raise AssertionError("pair inspection should not build the global duplicate index")

        try:
            project_record.list_projects = fail_list_projects
            registry.build_duplicate_candidate_index = fail_build_index

            payload = registry.inspect_merge_pair_payload(None, source, target)
            self.assertIn("source_pair_candidate", payload)
            self.assertNotIn("source_recommendation", payload)
        finally:
            project_record.list_projects = original_list_projects
            registry.build_duplicate_candidate_index = original_build_index

    def test_inspect_merge_pair_can_include_first_creator_details_without_primary_signal(self) -> None:
        source = project_record.default_project_record("proj:sourcefirstcreator", "repo_url", "https://github.com/example/source")
        source["name"] = "Example"
        source["input_nsids"] = ["app.example.post"]
        target = project_record.default_project_record("proj:targetfirstcreator", "repo_url", "https://github.com/example/target")
        target["name"] = "Example"
        target["input_nsids"] = ["app.example.comment"]
        original_fetch = registry.fetch_collection_first_creator_did

        def fake_fetch(_collection: str) -> str:
            return "did:plc:shared"

        try:
            registry.fetch_collection_first_creator_did = fake_fetch

            payload = registry.inspect_merge_pair_payload(None, source, target, include_first_creator_details=True)
        finally:
            registry.fetch_collection_first_creator_did = original_fetch

        self.assertTrue(payload["first_creator_details_included"])
        self.assertEqual(payload["first_creator_overlap"]["collection_first_record_creator_dids"], ["did:plc:shared"])
        self.assertNotIn("collection_first_record_creator_dids", payload["source_pair_candidate"]["reasons"])

    def test_first_creator_details_use_input_collection_case(self) -> None:
        source = project_record.default_project_record("proj:sourcefirstcreatorcase", "nsid", "app.alpha.record")
        source["name"] = "Alpha Project"
        source["input_nsids"] = ["app.alpha.record", "app.alpha.recordMixed"]
        target = project_record.default_project_record("proj:targetfirstcreatorcase", "nsid", "net.beta.entry")
        target["name"] = "Beta Project"
        target["input_nsids"] = ["net.beta.entry"]
        original_fetch = registry.fetch_collection_first_creator_did
        calls: list[str] = []

        def fake_fetch(collection: str) -> str:
            calls.append(collection)
            if collection == "app.alpha.recordMixed":
                return "did:plc:mixedcase"
            if collection == "net.beta.entry":
                return "did:plc:mixedcase"
            return ""

        try:
            registry.fetch_collection_first_creator_did = fake_fetch

            payload = registry.inspect_merge_pair_payload(None, source, target, include_first_creator_details=True)
        finally:
            registry.fetch_collection_first_creator_did = original_fetch

        self.assertIn("app.alpha.recordMixed", calls)
        self.assertEqual(payload["first_creator_overlap"]["collection_first_record_creator_dids"], ["did:plc:mixedcase"])
        self.assertNotIn("alias_overlap", payload["first_creator_overlap"])
        self.assertEqual(
            payload["source_collection_first_creator_details"]["app.alpha.recordMixed"],
            {"status": "found", "did": "did:plc:mixedcase"},
        )
        self.assertEqual(
            payload["source_collection_first_creator_details"]["app.alpha.record"],
            {"status": "not_found", "did": ""},
        )

    def test_compute_external_lexicons_uses_saved_trust_state_only(self) -> None:
        project = project_record.default_project_record("proj:externallexicon", "repo_url", "https://github.com/example/app")
        project["input_nsids"] = ["org.other.post"]

        original_owned = registry.project_owned_lexicon_nsid

        def fail_owned(_project: dict[str, object], _nsid: str) -> bool:
            raise AssertionError("compute_external_lexicons should not evaluate trust ownership")

        try:
            registry.project_owned_lexicon_nsid = fail_owned

            self.assertEqual(project_record.compute_external_lexicons(project), ["org.other"])
        finally:
            registry.project_owned_lexicon_nsid = original_owned

    def test_record_creator_with_namespace_lineage_is_high_confidence_auto_merge(self) -> None:
        self.assertEqual(
            self.confidence_for({"record_creators_and_namespace_lineage": ["app.example"]}),
            ("high", True),
        )


class ProjectRegistryMergeRecordTest(unittest.TestCase):
    def test_merge_reapplies_resource_url_budget(self) -> None:
        target = project_record.default_project_record("proj:mergetarget", "service_url", "https://example.com")
        source = project_record.default_project_record("proj:mergesource", "repo_url", "https://github.com/example/app")
        target["resource_url_records"] = [
            {"url": f"https://example.com/docs/target-{index}", "role": "docs", "confidence": "confirmed"}
            for index in range(4)
        ]
        source["resource_url_records"] = [
            {"url": f"https://example.com/docs/source-{index}", "role": "docs", "confidence": "confirmed"}
            for index in range(4)
        ]

        merged = registry.merge_project_records(target, source)

        self.assertEqual(len(merged["resource_url_records"]), 4)

    def test_merge_preserves_candidate_developer_accounts_without_promoting_them(self) -> None:
        target = project_record.default_project_record("proj:mergetarget", "service_url", "https://example.com")
        target["developer_accounts"] = [{"handle": "alice.example.com", "source": "manual"}]
        source = project_record.default_project_record("proj:mergesource", "repo_url", "https://github.com/example/app")
        source["candidate_developer_accounts"] = [{"handle": "candidate.bsky.social", "source": "bsky_actor_search:candidate"}]

        merged = registry.merge_project_records(target, source)

        self.assertIn({"handle": "alice.example.com", "source": "manual"}, merged["developer_accounts"])
        self.assertNotIn({"handle": "candidate.bsky.social", "source": "bsky_actor_search:candidate"}, merged["developer_accounts"])
        self.assertIn(
            {"handle": "candidate.bsky.social", "source": "bsky_actor_search:candidate"},
            merged["candidate_developer_accounts"],
        )

    def test_merge_keeps_machine_history_out_of_operator_notes(self) -> None:
        target = project_record.default_project_record("proj:mergetarget", "service_url", "https://example.com")
        target["notes"] = "target operator note"
        target["system_notes"] = ["target system note"]
        source = project_record.default_project_record("proj:mergesource", "repo_url", "https://github.com/example/app")
        source["notes"] = "source operator note"
        source["system_notes"] = ["source system note"]

        merged = registry.merge_project_records(target, source)

        self.assertEqual(merged["notes"], "target operator note\nsource operator note")
        self.assertIn("target system note", merged["system_notes"])
        self.assertIn("source system note", merged["system_notes"])
        self.assertTrue(any(item.startswith("merged from proj:mergesource at ") for item in merged["system_notes"]))
        self.assertFalse(any("merged from" in line for line in merged["notes"].splitlines()))

class ProjectRegistryAtprotoContributionTest(unittest.TestCase):
    def project(self) -> dict:
        return project_record.default_project_record("proj:contribution", "service_url", "https://example.com")

    def test_resolved_nsid_is_core(self) -> None:
        result = project_record.compute_atproto_contribution(dict(self.project(), resolved_nsids=["app.example.post"]))

        self.assertEqual(result["atproto_contribution_level"], "core")
        self.assertEqual(result["atproto_contribution_score"], 90)

    def test_record_observation_is_substantial(self) -> None:
        result = project_record.compute_atproto_contribution(
            dict(self.project(), collection_observations=[{"collection": "app.example.post", "count": 12, "recent_count": 3}])
        )

        self.assertEqual(result["atproto_contribution_level"], "substantial")
        self.assertEqual(result["atproto_contribution_score"], 70)

    def test_nsid_hint_only_is_optional(self) -> None:
        result = project_record.compute_atproto_contribution(dict(self.project(), input_nsids=["app.example.post"]))

        self.assertEqual(result["atproto_contribution_level"], "optional")
        self.assertEqual(result["atproto_contribution_score"], 40)

    def test_auth_only_signal_is_kept_without_protocol_surface(self) -> None:
        result = project_record.compute_atproto_contribution(
            dict(self.project(), category="project", atproto_auth_only=True)
        )

        self.assertEqual(result["atproto_contribution_level"], "auth_only")
        self.assertEqual(result["atproto_contribution_score"], 20)
        self.assertEqual(result["atproto_contribution_evidence"], [{"kind": "atproto_auth_only", "count": 1}])

    def test_auth_library_does_not_use_independent_app_auth_signal(self) -> None:
        result = project_record.compute_atproto_contribution(
            dict(self.project(), category="library", atproto_auth_only=True)
        )

        self.assertEqual(result["atproto_contribution_level"], "unknown")

    def test_llm_output_stores_auth_only_separately_from_category(self) -> None:
        project = self.project()

        registry.apply_llm_output(
            project,
            {"category": "project", "atproto_auth_only": True},
            {},
            {},
        )
        project_record.apply_atproto_contribution_state(project)

        self.assertEqual(project["category"], "project")
        self.assertTrue(project["atproto_auth_only"])
        self.assertEqual(project["atproto_contribution_level"], "auth_only")

    def test_llm_output_does_not_mark_auth_library_as_auth_only_integration(self) -> None:
        project = self.project()

        registry.apply_llm_output(
            project,
            {"category": "library", "atproto_auth_only": True},
            {},
            {},
        )
        project_record.apply_atproto_contribution_state(project)

        self.assertEqual(project["category"], "library")
        self.assertFalse(project["atproto_auth_only"])
        self.assertEqual(project["atproto_contribution_level"], "unknown")

    def test_llm_output_does_not_restore_removed_auth_only_category(self) -> None:
        project = self.project()
        project["category"] = "library"
        project["atproto_auth_only"] = True

        registry.apply_llm_output(project, {"category": "auth_only"}, {}, {})

        self.assertEqual(project["category"], "library")
        self.assertTrue(project["atproto_auth_only"])

    def test_strong_evidence_overrides_auth_only_signal(self) -> None:
        result = project_record.compute_atproto_contribution(
            dict(self.project(), atproto_auth_only=True, recent_record_samples=[{"collection": "app.example.post"}])
        )

        self.assertEqual(result["atproto_contribution_level"], "substantial")

    def test_review_sort_score_prioritizes_protocol_contribution(self) -> None:
        signals = [{"kind": "llm_manual_review_recommended", "priority": 7}]

        self.assertGreater(
            registry.review_queue_sort_score({"atproto_contribution_score": 90}, signals),
            registry.review_queue_sort_score({"atproto_contribution_score": 20}, signals),
        )


class ProjectRegistryOutOfScopeTest(unittest.TestCase):
    def test_auth_only_without_owned_protocol_surface_is_candidate(self) -> None:
        project = project_record.default_project_record("proj:authonly", "repo_url", "https://github.com/example/auth")
        project["category"] = "project"
        project["atproto_auth_only"] = True

        entry = registry.out_of_scope_review_entry(project)

        self.assertIsNotNone(entry)
        self.assertEqual(entry["kind"], "auth_only_without_owned_protocol_surface")
        self.assertEqual(entry["suggested_tracking_state"], "out_of_scope")

    def test_auth_only_with_owned_protocol_surface_is_not_automatic_candidate(self) -> None:
        project = project_record.default_project_record("proj:authwithnsid", "nsid", "app.example.record")
        project["category"] = "project"
        project["atproto_auth_only"] = True
        project["resolved_nsids"] = ["app.example.record"]

        self.assertIsNone(registry.out_of_scope_review_entry(project))

    def test_out_of_scope_is_not_an_ignore_candidate(self) -> None:
        project = project_record.default_project_record("proj:scoped", "repo_url", "https://github.com/example/fork")
        project["tracking_state"] = "out_of_scope"

        self.assertIsNone(registry.ignore_review_entry(project))

    def test_out_of_scope_with_owned_protocol_surface_is_reactivation_candidate(self) -> None:
        project = project_record.default_project_record("proj:reactivate", "nsid", "app.example.record")
        project["tracking_state"] = "out_of_scope"
        project["resolved_nsids"] = ["app.example.record"]

        entry = registry.out_of_scope_review_entry(project)

        self.assertIsNotNone(entry)
        self.assertEqual(entry["kind"], "owned_protocol_surface_detected")
        self.assertEqual(entry["suggested_tracking_state"], "active")


class ProjectRegistryStalledReviewTest(unittest.TestCase):
    NOW = dt.datetime(2026, 6, 12, tzinfo=dt.timezone.utc)

    def inactive_project(self) -> dict:
        project = project_record.default_project_record("proj:inactive", "repo_url", "https://github.com/example/inactive")
        project["tracking_state"] = "active"
        project["service_status"] = "unknown"
        project["source_code_urls"] = ["https://github.com/example/inactive"]
        project["source_repo_records"] = [
            {
                "url": "https://github.com/example/inactive",
                "provider": "github",
                "confidence": "confirmed",
                "relation": "source_repo",
                "source": "test",
                "updated_at": "2025-01-01T00:00:00+00:00",
            }
        ]
        return project

    def review(self, project: dict) -> dict | None:
        return registry.stalled_review_entry(
            project,
            now=self.NOW,
            inactivity_days=45,
            max_enrich_age_days=30,
        )

    def test_recent_enrich_allows_inactivity_downgrade(self) -> None:
        project = self.inactive_project()
        project["last_enriched_at"] = "2026-06-10T00:00:00+00:00"

        entry = self.review(project)

        self.assertIsNotNone(entry)
        self.assertEqual(entry["suggested_tracking_state"], "stalled")
        self.assertNotIn("not enriched", " ".join(entry["reasons"]))

    def test_stale_enrich_suppresses_inactivity_downgrade(self) -> None:
        project = self.inactive_project()
        project["last_enriched_at"] = "2026-01-01T00:00:00+00:00"

        self.assertIsNone(self.review(project))

    def test_never_enriched_suppresses_dead_service_downgrade(self) -> None:
        project = self.inactive_project()
        project["service_status"] = "dead"
        project["last_enriched_at"] = ""

        self.assertIsNone(self.review(project))

    def test_stale_enrich_does_not_block_activity_reactivation(self) -> None:
        project = self.inactive_project()
        project["tracking_state"] = "stalled"
        project["last_enriched_at"] = "2026-01-01T00:00:00+00:00"
        project["source_repo_records"][0]["updated_at"] = "2026-06-11T00:00:00+00:00"

        entry = self.review(project)

        self.assertIsNotNone(entry)
        self.assertEqual(entry["suggested_tracking_state"], "active")


class ProjectRegistryTechnicalInterestTest(unittest.TestCase):
    def project(self) -> dict:
        project = project_record.default_project_record("proj:technical", "repo_url", "https://github.com/example/protocol-lab")
        project["category"] = "infra_tool"
        return project

    def test_high_technical_interest_adds_showcase_signal_without_live_service(self) -> None:
        project = self.project()
        project.update(
            {
                "technical_interest_level": "high",
                "technical_interest_reasons": ["protocol_gap_exposed"],
                "technical_interest_summary": "相互運用時の不足を具体化している。",
            }
        )

        signals = registry.compute_showcase_signals(project)

        self.assertEqual([signal["kind"] for signal in signals], ["technical_insight_candidate"])
        self.assertEqual(signals[0]["details"]["technical_interest_score"], 70)

    def test_notable_technical_interest_is_saved_but_not_recommended(self) -> None:
        project = self.project()
        project.update(
            {
                "technical_interest_level": "notable",
                "technical_interest_reasons": ["implementation_reference"],
                "technical_interest_summary": "実装参照として有用。",
            }
        )

        shaped = project_record.ensure_project_shape(project)

        self.assertEqual(shaped["technical_interest_score"], 40)
        self.assertEqual(registry.compute_showcase_signals(shaped), [])

    def test_technical_interest_state_filters_unsupported_reason(self) -> None:
        project = self.project()
        project.update(
            {
                "technical_interest_level": "exceptional",
                "technical_interest_reasons": ["novel_design_pattern", "unsupported"],
            }
        )

        shaped = project_record.ensure_project_shape(project)

        self.assertEqual(shaped["technical_interest_score"], 90)
        self.assertEqual(shaped["technical_interest_reasons"], ["novel_design_pattern"])

    def test_llm_output_without_technical_fields_preserves_existing_assessment(self) -> None:
        project = self.project()
        project.update(
            {
                "technical_interest_level": "high",
                "technical_interest_reasons": ["implementation_reference"],
                "technical_interest_summary": "実装参照として有用。",
            }
        )

        registry.apply_llm_output(project, {}, {}, {})

        self.assertEqual(project["technical_interest_level"], "high")
        self.assertEqual(project["technical_interest_reasons"], ["implementation_reference"])

    def test_partial_llm_technical_output_preserves_omitted_fields(self) -> None:
        project = self.project()
        project.update(
            {
                "technical_interest_level": "high",
                "technical_interest_reasons": ["implementation_reference"],
                "technical_interest_summary": "実装参照として有用。",
            }
        )

        registry.apply_llm_output(project, {"technical_interest_summary": "不足を具体化している。"}, {}, {})

        self.assertEqual(project["technical_interest_level"], "high")
        self.assertEqual(project["technical_interest_reasons"], ["implementation_reference"])
        self.assertEqual(project["technical_interest_summary"], "不足を具体化している。")


class ProjectRegistryEditorialProfileTest(unittest.TestCase):
    SUPPORTED_EVIDENCE = {"supported_service_urls": ["https://service.example"]}

    def project(self) -> dict:
        project = project_record.default_project_record("proj:editorial", "nsid", "app.example.record")
        project["tagline"] = "プロフィールスキーマ"
        project["tagline_is_provisional"] = True
        project["summary"] = "レコードから推測した暫定説明。"
        return project

    def test_stronger_editorial_source_replaces_existing_summary(self) -> None:
        project = self.project()

        registry.apply_llm_output(
            project,
            {"summary": "公式サービス情報を反映した新しい説明。"},
            {},
            self.SUPPORTED_EVIDENCE,
        )

        self.assertEqual(project["summary"], "公式サービス情報を反映した新しい説明。")

    def test_same_editorial_sources_preserve_existing_summary(self) -> None:
        project = self.project()
        project["llm_source"] = {"evidence": self.SUPPORTED_EVIDENCE}

        registry.apply_llm_output(
            project,
            {"summary": "根拠は同じだが表現だけが異なる説明。"},
            {},
            self.SUPPORTED_EVIDENCE,
        )

        self.assertEqual(project["summary"], "レコードから推測した暫定説明。")

    def test_stronger_editorial_source_replaces_provisional_tagline(self) -> None:
        project = self.project()

        registry.apply_llm_output(
            project,
            {"tagline": "ウェアラブル連携", "tagline_is_provisional": True},
            {},
            self.SUPPORTED_EVIDENCE,
        )

        self.assertEqual(project["tagline"], "ウェアラブル連携")
        self.assertTrue(project["tagline_is_provisional"])

    def test_latest_llm_output_preserves_non_provisional_tagline(self) -> None:
        project = self.project()
        project["tagline"] = "確認済み紹介文"
        project["tagline_is_provisional"] = False

        registry.apply_llm_output(
            project,
            {"tagline": "新しい暫定案", "tagline_is_provisional": True},
            {},
            self.SUPPORTED_EVIDENCE,
        )

        self.assertEqual(project["tagline"], "確認済み紹介文")
        self.assertFalse(project["tagline_is_provisional"])

    def test_confirmed_docs_and_repository_are_editorial_sources(self) -> None:
        evidence = {
            "resource_url_records": [
                {"url": "https://service.example/docs/start", "role": "docs", "confidence": "confirmed"},
                {"url": "https://service.example/privacy", "role": "privacyPolicy", "confidence": "confirmed"},
            ],
            "source_code_urls": ["https://github.com/example/service"],
        }

        self.assertEqual(
            registry.profile_update_sources(evidence),
            {
                "resource:docs:https://service.example/docs/start",
                "repository:https://github.com/example/service",
            },
        )

    def test_new_repository_analysis_revision_is_an_editorial_source(self) -> None:
        previous_evidence = {
            "source_code_urls": ["https://github.com/example/service"],
            "repository_analysis_results": [],
        }
        current_evidence = {
            "source_code_urls": ["https://github.com/example/service"],
            "repository_analysis_results": [{
                "repo_url": "https://github.com/example/service",
                "revision": "abc123",
                "summary": "Repository implementation analysis.",
                "stale": False,
            }],
        }

        self.assertTrue(registry.profile_has_new_strong_source(previous_evidence, current_evidence))
        self.assertFalse(registry.profile_has_new_strong_source(current_evidence, current_evidence))

    def test_repository_analysis_saved_before_current_policy_refreshes_profile_once(self) -> None:
        project = self.project()
        evidence = {
            "source_code_urls": ["https://github.com/example/service"],
            "repository_analysis_results": [{
                "repo_url": "https://github.com/example/service",
                "revision": "abc123",
                "summary": "Repository implementation analysis.",
                "stale": False,
            }],
        }
        project["llm_source"] = {"evidence": evidence}

        registry.apply_llm_output(project, {"summary": "実装を反映した説明。"}, {}, evidence)
        self.assertEqual(project["summary"], "実装を反映した説明。")
        self.assertEqual(
            project["llm_source"]["editorial_source_policy_version"],
            registry.EDITORIAL_SOURCE_POLICY_VERSION,
        )

        registry.apply_llm_output(project, {"summary": "同じ根拠による別表現。"}, {}, evidence)
        self.assertEqual(project["summary"], "実装を反映した説明。")

    def test_repository_observations_do_not_directly_override_llm_category(self) -> None:
        evidence = {
            "repository_analysis_results": [{
                "repo_url": "https://github.com/example/plugin",
                "source_revision": "abc123",
                "summary": "Browser plugin implementation.",
                "observations": [{
                    "predicate": "contains_host_integration_code",
                    "basis": "inferred",
                    "evidence": [{"path": "src/webpackModules/plugin.ts"}],
                }],
            }],
        }
        project = self.project()

        registry.apply_llm_output(project, {"category": "library"}, {}, evidence)

        self.assertEqual(project["category"], "library")
        self.assertEqual(project["llm_output"]["category"], "library")

    def test_same_service_becoming_usable_is_an_editorial_source(self) -> None:
        project = self.project()
        previous_evidence = {
            "website_url": "https://service.example",
            "service_urls": ["https://service.example"],
            "supported_service_urls": ["https://service.example"],
            "service_status_hint": "unreleased",
            "service_status_evidence": ["coming_soon_detected"],
        }
        current_evidence = {
            "website_url": "https://service.example",
            "service_urls": ["https://service.example"],
            "supported_service_urls": ["https://service.example"],
            "service_status_hint": "live",
            "service_status_evidence": ["login_surface_detected"],
        }
        project["llm_source"] = {"evidence": previous_evidence}

        self.assertTrue(registry.profile_has_new_strong_source(previous_evidence, current_evidence))
        registry.apply_llm_output(
            project,
            {"summary": "利用可能になったサービスを表す説明。"},
            {},
            current_evidence,
        )
        self.assertEqual(project["summary"], "利用可能になったサービスを表す説明。")

    def test_repeated_usable_service_evidence_is_not_a_new_editorial_source(self) -> None:
        evidence = {
            "website_url": "https://service.example",
            "supported_service_urls": ["https://service.example"],
            "service_status_hint": "beta",
            "service_status_evidence": ["browser_public_app_surface_detected"],
        }

        self.assertFalse(registry.profile_has_new_strong_source(evidence, evidence))

    def test_launch_text_without_usable_surface_is_not_an_editorial_source(self) -> None:
        evidence = {
            "website_url": "https://service.example",
            "supported_service_urls": ["https://service.example"],
            "service_status_hint": "beta",
            "service_status_evidence": ["beta_text_detected"],
        }

        self.assertNotIn("usable_service:https://service.example", registry.profile_update_sources(evidence))

class ProjectRegistryManualReviewSignalTest(unittest.TestCase):
    def project(self) -> dict:
        project = project_record.default_project_record("proj:manualreview", "service_url", "https://example.com")
        project["manual_review_recommended"] = True
        project["manual_review_kind"] = "service_status_manual_check"
        project["manual_review_priority"] = "medium"
        project["manual_review_reason"] = "サービスを確認する"
        return project

    def llm_signal(self, project: dict) -> dict | None:
        return next(
            (signal for signal in registry.compute_review_signals(project) if signal["kind"] == "llm_manual_review_recommended"),
            None,
        )

    def test_low_confidence_published_infra_service_is_not_actionable(self) -> None:
        project = self.project()
        project.update(
            {
                "status": "published",
                "category": "infra_tool",
                "website_url": "https://example.com",
                "service_status": "live",
                "service_status_confidence": "low",
            }
        )

        self.assertIsNone(self.llm_signal(project))

    def test_draft_unknown_service_can_change_promotion_status(self) -> None:
        signal = self.llm_signal(self.project())

        self.assertIsNotNone(signal)
        self.assertIn("promotion_status_resolution", signal["details"]["action_basis"])

    def test_draft_unverified_service_without_conflict_is_not_actionable(self) -> None:
        project = self.project()
        project["service_status"] = "unverified"

        self.assertIsNone(self.llm_signal(project))

    def test_unintroduced_project_service_can_require_introduction_validation(self) -> None:
        project = self.project()
        project.update(
            {
                "status": "published",
                "category": "project",
                "website_url": "https://example.com",
                "service_status": "live",
                "service_status_confidence": "low",
            }
        )

        signal = self.llm_signal(project)

        self.assertIsNotNone(signal)
        self.assertIn("introduction_validation", signal["details"]["action_basis"])

    def test_conflicting_service_evidence_remains_actionable(self) -> None:
        project = self.project()
        project.update(
            {
                "status": "published",
                "category": "infra_tool",
                "website_url": "https://example.com",
                "service_status": "unverified",
                "service_status_confidence": "medium",
                "service_status_evidence": ["mixed_launch_signals_detected"],
            }
        )

        signal = self.llm_signal(project)

        self.assertIsNotNone(signal)
        self.assertIn("conflicting_service_status_evidence", signal["details"]["action_basis"])

    def test_non_service_manual_review_kind_is_unchanged(self) -> None:
        project = self.project()
        project["manual_review_kind"] = "identity_resolution"
        project["status"] = "published"

        self.assertIsNotNone(self.llm_signal(project))


if __name__ == "__main__":
    unittest.main()
