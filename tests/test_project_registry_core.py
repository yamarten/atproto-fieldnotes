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
                {"value": "Example App", "source": "service_title:https://example.com"},
            ],
            name="Error",
        )

        values = [item["value"] for item in registry.current_name_candidates(project)]

        self.assertEqual(values, ["Example App"])
        self.assertEqual(registry.choose_best_project_name(project), "Example App")

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


if __name__ == "__main__":
    unittest.main()
