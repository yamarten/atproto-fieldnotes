from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord
from project_registry_core import evaluation as evaluation_core
from project_registry_core import investigation as investigation_core


class ProjectRegistryPhase0CharacterizationTest(TempRegistryDbTestCase):
    def search_dependencies(self, **overrides):  # type: ignore[no-untyped-def]
        values = {
            "now": lambda: "2026-06-13T00:00:00+00:00",
            "search_github_repositories": lambda *args, **kwargs: [],
            "github_repo_relevance": lambda project, repo: 0,
            "repo_candidate_allowed": lambda project, url: True,
            "search_bsky_actors": lambda *args, **kwargs: [],
            "bsky_actor_relevance": lambda project, actor: 0,
            "search_bsky_posts": lambda *args, **kwargs: [],
            "handle_matches_project": lambda project, handle: False,
            "fetch_bsky_author_feed": lambda *args, **kwargs: [],
            "timeout": 12.0,
        }
        values.update(overrides)
        return investigation_core.SearchCollectorDependencies(**values)

    def test_search_collector_returns_evidence_without_mutating_project(self) -> None:
        project = self.make_project("proj:phase0bundle")
        project["name"] = "Example App"
        original = copy.deepcopy(project)
        dependencies = self.search_dependencies(
            search_github_repositories=lambda *args, **kwargs: [
                {
                    "url": "https://github.com/example/app",
                    "full_name": "example/app",
                    "description": "Example App",
                    "homepage": "https://example.com",
                }
            ],
            github_repo_relevance=lambda project, repo: 9,
        )

        bundle = investigation_core.collect_investigation_evidence(
            project,
            {"repo_search_queries": ["Example App"]},
            dependencies,
        )

        self.assertEqual(project, original)
        self.assertEqual(
            [record.kind for record in bundle.records],
            ["investigation_query", "github_repo_search_candidate", "investigation_log"],
        )
        self.assertEqual(bundle.failures, [])

    def test_search_collector_classifies_rate_limit_failure(self) -> None:
        class RateLimitError(RuntimeError):
            code = 429

        dependencies = self.search_dependencies(
            search_github_repositories=lambda *args, **kwargs: (_ for _ in ()).throw(RateLimitError("limited")),
        )

        bundle = investigation_core.collect_investigation_evidence(
            self.make_project("proj:phase0ratelimit"),
            {"repo_search_queries": ["Example App"]},
            dependencies,
        )

        self.assertEqual(bundle.failures[0].category, "rate_limit")
        self.assertTrue(bundle.failures[0].retryable)

    def test_github_repo_collector_returns_snapshot_without_mutating_project(self) -> None:
        project = self.make_project("proj:phase0reposnapshot", "repo_url", "https://github.com/example/app")
        original = copy.deepcopy(project)
        dependencies = investigation_core.GitHubRepoCollectorDependencies(
            now=lambda: "2026-06-13T00:00:00+00:00",
            fetch_github_repo=lambda repo_url: {
                "repo": {"full_name": "example/app", "description": "Example App"},
                "readme_text": "https://app.example.com",
                "tree": [{"path": "lexicons/app.example.post.json"}],
                "default_branch": "main",
            },
            repo_lexicon_hints=lambda repo_url, **kwargs: [
                {"nsid": "app.example.post", "path": "lexicons/app.example.post.json", "schema": {"lexicon": 1}}
            ],
            extract_urls=lambda text: ["https://app.example.com"],
        )

        bundle = investigation_core.collect_github_repo_evidence(
            project,
            "https://github.com/example/app",
            dependencies,
        )

        self.assertEqual(project, original)
        self.assertEqual(bundle.records[0].kind, "github_repo_snapshot")
        snapshot = bundle.records[0].value
        assert isinstance(snapshot, dict)
        self.assertEqual(snapshot["readme_urls"], ["https://app.example.com"])
        self.assertEqual(snapshot["lexicon_hints"][0]["nsid"], "app.example.post")

    def test_evidence_fixture_roundtrip_preserves_ordered_records(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "project_registry" / "evidence-v1-synthetic-reference.json"
        bundle = EvidenceBundle.load(fixture_path)

        self.assertEqual(bundle.schema_version, 1)
        self.assertEqual(bundle.project_key, "proj:synthetic-reference")
        self.assertEqual(bundle.records[0].kind, "github_repo_snapshot")
        self.assertEqual(EvidenceBundle.from_json(bundle.to_json()).to_dict(), bundle.to_dict())

    def test_saved_evidence_fixture_can_be_re_evaluated_without_collection(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "project_registry" / "evidence-v1-synthetic-reference.json"
        bundle = EvidenceBundle.load(fixture_path)
        snapshot = bundle.records[0].value
        assert isinstance(snapshot, dict)
        hints = snapshot["lexicon_hints"]
        assert isinstance(hints, list) and isinstance(hints[0], dict)
        nsid = str(hints[0]["nsid"])
        project = self.make_project(
            "proj:synthetic-reference",
            "repo_url",
            "https://github.com/example/client",
        )
        project["name"] = "Example Client"

        self.assertFalse(registry.project_owned_lexicon_nsid(project, nsid))
        project["external_lexicons"] = [nsid]
        contribution = evaluation_core.compute_atproto_contribution(project)
        self.assertEqual(contribution["atproto_contribution_level"], "optional")

    def test_evidence_rejects_non_json_values(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceRecord(
                kind="invalid",
                source="test",
                classification="candidate",
                observed_at="2026-06-13T00:00:00+00:00",
                value={"invalid": object()},  # type: ignore[dict-item]
            )

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
                mock.patch.object(
                    registry,
                    "collect_investigation_evidence",
                    return_value=EvidenceBundle(
                        project_key=project["project_key"],
                        collected_at="2026-06-13T00:00:00+00:00",
                    ),
                ),
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
            "collect_investigation_evidence",
            "apply_investigation_evidence",
            "collect_github_repo_evidence",
            "apply_github_repo_evidence",
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
