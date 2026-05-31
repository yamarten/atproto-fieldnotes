from __future__ import annotations

import socket
import unittest
import urllib.error

from helpers import registry


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
