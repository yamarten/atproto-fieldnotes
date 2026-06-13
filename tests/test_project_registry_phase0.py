from __future__ import annotations

import unittest
from unittest import mock

from helpers import TempRegistryDbTestCase, registry


class ProjectRegistryPhase0CharacterizationTest(TempRegistryDbTestCase):
    def test_seed_investigation_queries_preserve_order_and_limits(self) -> None:
        project = self.make_project("proj:phase0queries")
        project["name"] = "Example App"
        project["primary_nsid"] = "app.example.post"
        project["authority_domains"] = ["example.com", "api.example.com"]
        project["bluesky_handles"] = ["example.com"]

        plan = registry.seed_investigation_queries(project)

        self.assertEqual(
            plan,
            {
                "repo_search_queries": ["Example App", "app.example.post", "example.com", "api.example.com"],
                "actor_search_queries": ["Example App", "app.example.post", "example.com", "api.example.com"],
                "post_search_queries": ["Example App", "app.example.post", "example.com", "api.example.com"],
                "focus_terms": ["Example App", "app.example.post", "example.com", "api.example.com"],
            },
        )

    def test_investigation_collectors_keep_threshold_limit_and_partial_failure(self) -> None:
        project = self.make_project("proj:phase0collect")
        project["name"] = "Example App"
        project["website_url"] = "https://example.com"
        plan = {
            "repo_search_queries": ["Example App", "Example App fallback"],
            "actor_search_queries": ["Example App"],
            "post_search_queries": ["Example App"],
        }
        repo_results = [
            {
                "url": "https://github.com/example/app",
                "full_name": "example/app",
                "description": "Example App for ATProto",
                "homepage": "https://example.com",
            },
            {
                "url": "https://github.com/example/other",
                "full_name": "example/other",
                "description": "Example App helper",
                "homepage": "https://example.com/docs",
            },
        ]

        with (
            mock.patch.object(registry, "search_github_repositories", return_value=repo_results) as github_search,
            mock.patch.object(registry, "github_repo_relevance", side_effect=[9, 7]),
            mock.patch.object(registry, "repo_candidate_allowed_for_project", return_value=True),
            mock.patch.object(registry, "search_bsky_actors", side_effect=TimeoutError("actor timeout")),
            mock.patch.object(registry, "search_bsky_posts", return_value=[]),
        ):
            registry.run_investigation_collectors(project, plan)

        self.assertEqual(github_search.call_count, 1)
        self.assertEqual(project["candidate_source_code_urls"], ["https://github.com/example/app"])
        self.assertIn("https://example.com", project["service_url_candidates"])
        self.assertTrue(any("actor timeout" in item for item in project["_run_errors"]))
        github_logs = [item for item in project["investigation_log"] if item.get("kind") == "github_repo_search"]
        self.assertEqual(github_logs[0]["accepted_count"], 1)

    def test_github_repo_enrichment_distinguishes_owned_and_reference_lexicons(self) -> None:
        project = self.make_project("proj:phase0repo", "repo_url", "https://github.com/example/app")
        repo_payload = {
            "repo": {
                "description": "Example App",
                "homepage": "",
                "full_name": "example/app",
                "updated_at": "2026-06-01T00:00:00Z",
                "fork": False,
            },
            "readme_text": "https://github.com/example/helper https://app.example.com",
            "tree": [],
            "default_branch": "main",
        }
        lexicons = [
            {"nsid": "app.example.post", "path": "lexicons/app.example.post.json", "schema": {"lexicon": 1}},
            {"nsid": "app.external.post", "path": "examples/app.external.post.json", "schema": {"lexicon": 1}},
        ]

        with (
            mock.patch.object(registry, "fetch_github_repo", return_value=repo_payload),
            mock.patch.object(registry, "repo_lexicon_hints", return_value=lexicons),
            mock.patch.object(
                registry,
                "project_owned_lexicon_nsid",
                side_effect=lambda _project, nsid: nsid == "app.example.post",
            ),
            mock.patch.object(registry, "repo_url_allowed_for_project", return_value=True),
            mock.patch.object(registry, "repo_context_service_candidate_allowed", return_value=True),
        ):
            registry.enrich_from_github_repo(project, "https://github.com/example/app")

        self.assertIn("app.example.post", project["input_nsids"])
        self.assertNotIn("app.external.post", project["input_nsids"])
        reference_logs = [
            item for item in project["investigation_log"]
            if item.get("kind") == "github_repo_lexicon_reference"
        ]
        self.assertEqual(reference_logs[0]["nsid"], "app.external.post")
        self.assertIn("https://github.com/example/helper", project["candidate_source_code_urls"])
        self.assertIn("https://app.example.com", project["service_url_candidates"])

    def test_enrich_save_fetch_preserves_top_level_contract(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:phase0enrich")
            with (
                mock.patch.object(registry, "should_refresh_lexicon_surface", return_value=False),
                mock.patch.object(registry, "should_refresh_recent_record_samples", return_value=False),
                mock.patch.object(registry, "plan_investigation_queries", return_value={}),
                mock.patch.object(registry, "run_investigation_collectors"),
                mock.patch.object(registry, "run_local_llm", side_effect=RuntimeError("LLM unavailable")),
            ):
                enriched = registry.enrich_project(conn, project)
            registry.save_project(conn, enriched, enriched=True)
            conn.commit()
            fetched = registry.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["project_key"], "proj:phase0enrich")
        self.assertIn("LLM unavailable", "\n".join(fetched["last_run_errors"]))
        self.assertTrue(fetched["last_enriched_at"])

    def test_cli_and_top_level_api_remain_available(self) -> None:
        parser = registry.build_parser()
        args = parser.parse_args(["enrich", "--seed", "app.example.post"])

        self.assertEqual(args.command, "enrich")
        for name in (
            "enrich_project",
            "plan_investigation_queries",
            "run_investigation_collectors",
            "enrich_from_github_repo",
            "project_owned_lexicon_nsid",
            "compute_atproto_contribution",
            "classify_service_probe_result",
        ):
            self.assertTrue(callable(getattr(registry, name)))


if __name__ == "__main__":
    unittest.main()
