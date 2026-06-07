from __future__ import annotations

import socket
import unittest
import urllib.error
import urllib.request
from io import BytesIO

from helpers import registry


class ProjectRegistryUrlCompatibilityTest(unittest.TestCase):
    def test_legacy_website_candidates_are_folded_into_service_candidates(self) -> None:
        project = registry.default_project_record("proj:urlcompat", "service_url", "https://example.com")
        project["website_url_candidates"] = ["https://app.example.com"]
        project["service_url_candidates"] = []

        shaped = registry.ensure_project_shape(project)

        self.assertEqual(shaped["service_url_candidates"], ["https://app.example.com"])
        self.assertEqual(shaped["website_url_candidates"], ["https://app.example.com"])
        self.assertEqual(registry.service_url_candidate_values(shaped), ["https://app.example.com"])

    def test_provider_repo_views_are_derived_from_confirmed_source_records(self) -> None:
        project = registry.default_project_record("proj:repoview", "nsid", "app.example.post")
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

        shaped = registry.ensure_project_shape(project)

        self.assertEqual(shaped["github_repo_urls"], ["https://github.com/example/app"])
        self.assertEqual(shaped["tangled_repo_urls"], ["https://tangled.org/example/lexicons"])
        self.assertIn("https://tangled.org/example/lexicons", shaped["source_code_urls"])

    def test_provider_repo_views_ignore_candidate_source_records(self) -> None:
        project = registry.default_project_record("proj:repocandidateview", "nsid", "app.example.post")
        registry.add_candidate_source_code_url(
            project,
            "https://github.com/example/candidate",
            "test",
            reason="candidate only",
        )
        project["candidate_source_code_urls"] = ["https://github.com/example/candidate"]

        shaped = registry.ensure_project_shape(project)

        self.assertEqual(shaped["source_code_urls"], [])
        self.assertEqual(shaped["github_repo_urls"], [])
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
                    registry.validate_outbound_fetch_url(url)

    def test_validate_outbound_fetch_url_preserves_query_string(self) -> None:
        url = "https://api.github.com/search/repositories?q=Skyrdle+OR+farm.smol.games.skyrdle.score&per_page=5"

        self.assertEqual(registry.validate_outbound_fetch_url(url), url)

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

        original_opener = registry.NO_REDIRECT_OPENER
        original_blocker = registry.outbound_host_is_blocked
        registry.NO_REDIRECT_OPENER = RedirectOpener()
        registry.outbound_host_is_blocked = lambda host: host == "127.0.0.1"
        try:
            with self.assertRaises(ValueError):
                registry.request_bytes_with_final_url("https://example.com/redirect")
        finally:
            registry.NO_REDIRECT_OPENER = original_opener
            registry.outbound_host_is_blocked = original_blocker

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

        original_opener = registry.NO_REDIRECT_OPENER
        registry.NO_REDIRECT_OPENER = LocalOpener()
        try:
            with self.assertRaises(ValueError):
                registry.request_bytes_with_final_url("http://192.168.1.2:1234/v1/responses")
            status, body, _headers, final_url = registry.request_bytes_with_final_url(
                "http://192.168.1.2:1234/v1/responses",
                allow_blocked_hosts=True,
            )
        finally:
            registry.NO_REDIRECT_OPENER = original_opener

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

        original_opener = registry.NO_REDIRECT_OPENER
        registry.NO_REDIRECT_OPENER = RecordingOpener()
        try:
            registry.request_bytes_with_final_url(
                "https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors?q=Skyrdle+%7C+skyrdle.com&limit=5",
            )
        finally:
            registry.NO_REDIRECT_OPENER = original_opener

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

        original_request_json = registry.request_json
        original_tracked_collections = registry.SEMBLE_TRACKED_COLLECTIONS
        registry.request_json = fake_request_json
        registry.SEMBLE_TRACKED_COLLECTIONS = (
            {"handle": "byarielm.fyi", "rkey": "rkey123", "label": "stale label"},
        )
        try:
            results = registry.fetch_semble_collection_sync_results(pages=1, per_page=20)
        finally:
            registry.request_json = original_request_json
            registry.SEMBLE_TRACKED_COLLECTIONS = original_tracked_collections

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


class ProjectRegistryNameCandidateTest(unittest.TestCase):
    def project_with_candidates(self, candidates: list[dict[str, str]], *, name: str = "") -> dict:
        project = registry.default_project_record("proj:aaaaaaaaaaaa", "nsid", "app.example.post")
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
        project["primary_nsid"] = "com.hectorsector.book.status"
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
        project["primary_nsid"] = "zip.meowing.isfox"
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
        project["primary_nsid"] = "com.hectorsector.book.status"
        project["input_nsids"] = ["com.hectorsector.book.status"]
        project["authority_domains"] = ["book.hectorsector.com"]

        self.assertEqual(registry.choose_best_project_name(project), "Book Status")

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
        project["primary_nsid"] = "zip.meowing.isfox"
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
        project["primary_nsid"] = "app.atprotomkt.marketplace.listing"
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
        self.assertEqual(registry.classify_service_probe_error(urllib.error.URLError("TLS certificate verify failed")), "tls_failed")
        self.assertEqual(registry.classify_service_probe_error(socket.timeout("timed out")), "timeout")
        self.assertEqual(registry.classify_service_probe_error(urllib.error.URLError("Connection refused")), "connection_refused")


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

    def test_same_host_http_to_https_redirect_canonicalizes_service_url(self) -> None:
        project = registry.default_project_record("proj:redirecttest", "service_url", "http://example.com")
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
        self.assertEqual(project["website_url"], "https://example.com")
        redirect_logs = [item for item in project["investigation_log"] if item.get("kind") == "service_redirect"]
        self.assertEqual(redirect_logs[0]["final_url"], "https://example.com")

    def test_cross_host_redirect_does_not_canonicalize_service_url(self) -> None:
        project = registry.default_project_record("proj:redirectcross", "service_url", "https://example.com")
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
        self.assertNotIn("https://other.example.net", project["website_url_candidates"])

    def test_supported_cross_host_redirect_adds_final_url_as_candidate(self) -> None:
        project = registry.default_project_record("proj:redirectcandidate", "service_url", "https://example.com")
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
        self.assertIn("https://other.example.net", project["service_url_candidates"])
        self.assertIn("https://other.example.net", project["website_url_candidates"])
        self.assertIn("https://other.example.net", project["evidence_urls"])

    def test_supported_same_site_redirect_sets_empty_primary_website_url(self) -> None:
        project = registry.default_project_record("proj:redirectsamefamily", "service_url", "https://example.com")
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

        self.assertEqual(project["website_url"], "https://www.example.com")
        self.assertIn("https://www.example.com", project["service_urls"])

    def test_supported_same_site_redirect_keeps_existing_primary_as_candidate(self) -> None:
        project = registry.default_project_record("proj:redirectsamefamilyexisting", "service_url", "https://example.com")
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
        self.assertNotIn("https://www.example.com", project["service_urls"])
        self.assertIn("https://www.example.com", project["service_url_candidates"])
        self.assertIn("https://www.example.com", project["website_url_candidates"])


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

    def test_supported_authority_root_survives_unverified_guess_prune(self) -> None:
        project = registry.default_project_record("proj:authorityroot", "nsid", "xyz.tz2at.raw.observation")
        project["primary_nsid"] = "xyz.tz2at.raw.observation"
        project["input_nsids"] = ["xyz.tz2at.raw.observation"]
        project["authority_domains"] = ["raw.tz2at.xyz"]

        registry.apply_service_probe_result(
            project,
            "https://tz2at.xyz",
            self.probe_result("TZ2AT app for xyz.tz2at.raw.observation"),
        )
        project = registry.finalize_project_shape(project)

        retained_urls = set(project["service_urls"]) | set(project["service_url_candidates"]) | set(project["website_url_candidates"])
        self.assertIn("https://tz2at.xyz", project["supported_service_urls"])
        self.assertIn("https://tz2at.xyz", retained_urls)

    def test_unsupported_authority_root_is_still_pruned(self) -> None:
        project = registry.default_project_record("proj:authorityroot", "nsid", "xyz.tz2at.raw.observation")
        project["primary_nsid"] = "xyz.tz2at.raw.observation"
        project["input_nsids"] = ["xyz.tz2at.raw.observation"]
        project["authority_domains"] = ["raw.tz2at.xyz"]
        project["service_url_candidates"] = ["https://tz2at.xyz"]
        project["website_url_candidates"] = ["https://tz2at.xyz"]

        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://tz2at.xyz", project["service_url_candidates"])
        self.assertNotIn("https://tz2at.xyz", project["website_url_candidates"])

    def test_confirmed_service_url_is_removed_from_candidate_views(self) -> None:
        project = registry.default_project_record("proj:serviceoverlap", "service_url", "https://example.com")
        project["service_urls"] = ["https://example.com", "https://app.example.com"]
        project["service_url_candidates"] = ["https://app.example.com"]
        project["website_url_candidates"] = ["https://app.example.com"]

        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://app.example.com", project["service_url_candidates"])
        self.assertNotIn("https://app.example.com", project["website_url_candidates"])


class ProjectRegistryProfileUrlTest(unittest.TestCase):
    def test_bluesky_profile_subject_distinguishes_handle_and_did(self) -> None:
        self.assertEqual(
            registry.extract_bluesky_profile_subject("https://bsky.app/profile/alice.example.com"),
            {"handle": "alice.example.com"},
        )
        self.assertEqual(
            registry.extract_bluesky_profile_subject("https://bsky.app/profile/did:plc:abcdefghijklmnopqrstuvwx"),
            {"did": "did:plc:abcdefghijklmnopqrstuvwx"},
        )
        self.assertEqual(
            registry.extract_bluesky_profile_subject("https://bsky.app/profile/did:web:alice.example.com"),
            {"did": "did:web:alice.example.com"},
        )
        self.assertEqual(registry.extract_bluesky_profile_subject("https://bsky.app/profile/not-a-handle"), {})

    def test_did_profile_url_becomes_candidate_account_not_service(self) -> None:
        profile_url = "https://bsky.app/profile/did:plc:abcdefghijklmnopqrstuvwx"
        project = registry.default_project_record("proj:didprofile", "repo_url", "https://github.com/example/app")
        project["website_url"] = profile_url
        project["service_urls"] = [profile_url]

        project = registry.finalize_project_shape(project)

        self.assertEqual(project["website_url"], "")
        self.assertNotIn(profile_url, project["service_urls"])
        self.assertIn(
            {"did": "did:plc:abcdefghijklmnopqrstuvwx", "source": "service_urls:profile_url"},
            project["candidate_developer_accounts"],
        )


class ProjectRegistryResourceUrlRecordTest(unittest.TestCase):
    def test_legacy_resource_url_becomes_unknown_legacy_record(self) -> None:
        project = registry.default_project_record("proj:resourcelegacy", "service_url", "https://example.com")
        project["resource_urls"] = ["https://example.com/docs"]

        shaped = registry.ensure_project_shape(project)

        self.assertEqual(shaped["resource_urls"], ["https://example.com/docs"])
        self.assertEqual(
            shaped["resource_url_records"],
            [
                {
                    "url": "https://example.com/docs",
                    "role": "unknown",
                    "source": "legacy_resource_urls",
                    "observed_at": "",
                    "confidence": "legacy",
                }
            ],
        )

    def test_resource_url_values_use_records_without_compat_view(self) -> None:
        project = registry.default_project_record("proj:resourcecanonical", "service_url", "https://example.com")
        project["resource_urls"] = []
        project["resource_url_records"] = [
            {
                "url": "https://example.com/docs",
                "role": "docs",
                "source": "service_page:https://example.com",
                "observed_at": "",
                "confidence": "confirmed",
            }
        ]

        self.assertEqual(registry.resource_url_values(project), ["https://example.com/docs"])
        self.assertEqual(
            registry.resource_url_record_values(project),
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
        project = registry.default_project_record("proj:deepurls", "service_url", "https://example.com")
        project["website_url"] = "https://example.com/app"
        project["service_urls"] = ["https://example.com/app"]
        project["resource_urls"] = []
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
        project = registry.default_project_record("proj:deepcandidate", "nsid", "app.example.post")
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
        project = registry.default_project_record("proj:deepprofile", "repo_url", "https://github.com/example/app")
        project["service_url_candidates"] = ["https://bsky.app/profile/did:plc:abcdefghijklmnopqrstuvwx"]

        self.assertEqual(registry.deep_investigation_page_urls(project), [])

    def test_apply_resource_page_probe_status_records_page_kind_without_confirming_url(self) -> None:
        project = registry.default_project_record("proj:deeplogin", "service_url", "https://example.com")
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
        project = registry.default_project_record("proj:resourceconfirmed", "service_url", "https://example.com")
        registry.add_resource_url(
            project,
            "https://example.com/docs",
            role="docs",
            source="service_page:https://example.com",
            confidence="confirmed",
        )

        links = registry.build_project_app_links(project)

        self.assertIn({"uri": "https://example.com/docs", "role": "docs", "official": True}, links)

    def test_candidate_resource_record_does_not_add_app_link(self) -> None:
        project = registry.default_project_record("proj:resourcecandidate", "service_url", "https://example.com")
        registry.add_resource_url(
            project,
            "https://external.example.net/docs",
            role="docs",
            source="service_page:https://example.com",
            confidence="candidate",
        )

        self.assertEqual(registry.build_project_app_links(project), [])

    def test_confirmed_resource_record_replaces_legacy_record_for_same_url(self) -> None:
        project = registry.default_project_record("proj:resourcepromote", "service_url", "https://example.com")
        project["resource_urls"] = ["https://example.com/docs"]
        project = registry.ensure_project_shape(project)

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

    def test_first_party_link_collection_records_same_site_docs_as_confirmed(self) -> None:
        project = registry.default_project_record("proj:resourcefirstparty", "service_url", "https://example.com")

        registry.collect_first_party_links(
            project,
            "https://example.com",
            [
                {"url": "https://example.com/docs", "text": "Docs"},
                {"url": "https://external.example.net/docs", "text": "External docs"},
            ],
            "service_page:https://example.com",
        )

        records = {record["url"]: record for record in project["resource_url_records"]}
        self.assertEqual(records["https://example.com/docs"]["confidence"], "confirmed")
        self.assertEqual(records["https://example.com/docs"]["role"], "docs")
        self.assertEqual(records["https://external.example.net/docs"]["confidence"], "candidate")


class ProjectRegistryLexiconScopeTest(unittest.TestCase):
    def nsid_project(self) -> dict:
        project = registry.default_project_record("proj:lexiconscope", "nsid", "xyz.tz2at.raw.observation")
        project["primary_nsid"] = "xyz.tz2at.raw.observation"
        project["input_nsids"] = ["xyz.tz2at.raw.observation"]
        project["authority_domains"] = ["raw.tz2at.xyz"]
        project["category"] = "lexicon"
        return project

    def test_shared_lexicon_positioning_preserves_lexicon_category(self) -> None:
        project = self.nsid_project()
        project["summary"] = "第三者の相互運用を目的とする共有レキシコン。"
        project["website_url"] = "https://standard.site"

        registry.reclassify_service_specific_lexicon(project)

        self.assertEqual(project["lexicon_scope"], "shared")
        self.assertEqual(project["category"], "lexicon")

    def test_matching_service_surface_reclassifies_service_specific_lexicon(self) -> None:
        project = self.nsid_project()
        project["supported_service_urls"] = ["https://tz2at.xyz"]
        project["service_url_candidates"] = ["https://tz2at.xyz"]

        registry.reclassify_service_specific_lexicon(project)

        self.assertEqual(project["lexicon_scope"], "service_specific")
        self.assertEqual(project["category"], "project")

    def test_resource_url_records_count_as_service_surface_without_compat_view(self) -> None:
        project = self.nsid_project()
        project["resource_urls"] = []
        project["resource_url_records"] = [
            {
                "url": "https://tz2at.xyz/docs",
                "role": "docs",
                "source": "service_page:https://tz2at.xyz",
                "observed_at": "",
                "confidence": "confirmed",
            }
        ]

        registry.reclassify_service_specific_lexicon(project)

        self.assertEqual(project["lexicon_scope"], "service_specific")
        self.assertEqual(project["category"], "project")

    def test_nsid_only_candidate_keeps_unknown_scope(self) -> None:
        project = self.nsid_project()

        registry.reclassify_service_specific_lexicon(project)

        self.assertEqual(project["lexicon_scope"], "unknown")
        self.assertEqual(project["category"], "lexicon")
        self.assertIn("shared_lexicon_positioning_not_found", project["lexicon_scope_evidence"])

    def test_llm_evidence_exposes_lexicon_scope(self) -> None:
        project = self.nsid_project()
        project["resource_urls"] = []
        project["resource_url_records"] = [
            {
                "url": "https://tz2at.xyz/docs",
                "role": "docs",
                "source": "service_page:https://tz2at.xyz",
                "observed_at": "",
                "confidence": "confirmed",
            }
        ]
        registry.apply_lexicon_scope_state(project)

        evidence = registry.build_llm_evidence_bundle(project)

        self.assertEqual(evidence["resource_urls"], ["https://tz2at.xyz/docs"])
        self.assertEqual(evidence["resource_url_records"][0]["confidence"], "confirmed")
        self.assertEqual(evidence["lexicon_scope"], "service_specific")
        self.assertEqual(
            evidence["lexicon_scope_evidence"],
            ["namespace_matches_project_identity", "service_or_collection_surface_detected"],
        )


class ProjectRegistryLlmParserTest(unittest.TestCase):
    def test_parse_chat_completion_message_content(self) -> None:
        payload = {"choices": [{"message": {"content": "chat text"}}]}

        self.assertEqual(registry.parse_openai_compatible_response(payload), "chat text")

    def test_parse_responses_output_text(self) -> None:
        payload = {"output_text": "responses text"}

        self.assertEqual(registry.parse_openai_compatible_response(payload), "responses text")

    def test_parse_responses_output_content_text(self) -> None:
        payload = {"output": [{"content": [{"type": "output_text", "text": "nested text"}]}]}

        self.assertEqual(registry.parse_openai_compatible_response(payload), "nested text")

    def test_parse_completion_text(self) -> None:
        payload = {"choices": [{"text": "completion text"}]}

        self.assertEqual(registry.parse_openai_compatible_response(payload), "completion text")

    def test_parse_empty_response_raises(self) -> None:
        with self.assertRaises(ValueError):
            registry.parse_openai_compatible_response({"output": [{"content": []}]})


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

    def test_record_creator_with_namespace_lineage_is_high_confidence_auto_merge(self) -> None:
        self.assertEqual(
            self.confidence_for({"record_creators_and_namespace_lineage": ["app.example"]}),
            ("high", True),
        )


class ProjectRegistryAtprotoContributionTest(unittest.TestCase):
    def project(self) -> dict:
        return registry.default_project_record("proj:contribution", "service_url", "https://example.com")

    def test_resolved_nsid_is_core(self) -> None:
        result = registry.compute_atproto_contribution(dict(self.project(), resolved_nsids=["app.example.post"]))

        self.assertEqual(result["atproto_contribution_level"], "core")
        self.assertEqual(result["atproto_contribution_score"], 90)

    def test_record_observation_is_substantial(self) -> None:
        result = registry.compute_atproto_contribution(
            dict(self.project(), collection_observations=[{"collection": "app.example.post", "count": 12, "recent_count": 3}])
        )

        self.assertEqual(result["atproto_contribution_level"], "substantial")
        self.assertEqual(result["atproto_contribution_score"], 70)

    def test_nsid_hint_only_is_optional(self) -> None:
        result = registry.compute_atproto_contribution(dict(self.project(), input_nsids=["app.example.post"]))

        self.assertEqual(result["atproto_contribution_level"], "optional")
        self.assertEqual(result["atproto_contribution_score"], 40)

    def test_auth_only_category_is_kept_without_protocol_surface(self) -> None:
        result = registry.compute_atproto_contribution(dict(self.project(), category="auth_only"))

        self.assertEqual(result["atproto_contribution_level"], "auth_only")
        self.assertEqual(result["atproto_contribution_score"], 20)

    def test_strong_evidence_overrides_auth_only_category(self) -> None:
        result = registry.compute_atproto_contribution(
            dict(self.project(), category="auth_only", recent_record_samples=[{"collection": "app.example.post"}])
        )

        self.assertEqual(result["atproto_contribution_level"], "substantial")

    def test_review_sort_score_prioritizes_protocol_contribution(self) -> None:
        signals = [{"kind": "llm_manual_review_recommended", "priority": 7}]

        self.assertGreater(
            registry.review_queue_sort_score({"atproto_contribution_score": 90}, signals),
            registry.review_queue_sort_score({"atproto_contribution_score": 20}, signals),
        )


class ProjectRegistryTechnicalInterestTest(unittest.TestCase):
    def project(self) -> dict:
        project = registry.default_project_record("proj:technical", "repo_url", "https://github.com/example/protocol-lab")
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

        shaped = registry.ensure_project_shape(project)

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

        shaped = registry.ensure_project_shape(project)

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


class ProjectRegistryManualReviewSignalTest(unittest.TestCase):
    def project(self) -> dict:
        project = registry.default_project_record("proj:manualreview", "service_url", "https://example.com")
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
