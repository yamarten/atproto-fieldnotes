from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import enrichment_pipeline, source_sync
from project_registry_core.evidence import CollectorFailure, CollectorResult, EvidenceBundle, EvidenceRecord
from project_registry_core import evaluation as evaluation_core
from project_registry_core import integration as integration_core
from project_registry_core import investigation as investigation_core
from project_registry_core import llm_transport as llm_transport_core
from project_registry_core import relations as relation_core
from project_registry_core import repository_analysis
from project_registry_core import repository_material
from project_registry_core import repository_sources
from project_registry_core import runtime_view
from project_registry_core import service as service_core
from project_registry_core import storage as storage_core
from project_registry_core import tangled as tangled_core
from project_registry_core import project_record


class ProjectRegistryResearchPipelineTest(TempRegistryDbTestCase):
    def test_triage_prompt_suffix_is_bounded_and_hashed(self) -> None:
        prompt, suffix_hash = enrichment_pipeline.append_triage_prompt_suffix(
            "base prompt", "  category gate\n"
        )
        expected_hash = hashlib.sha256(b"category gate").hexdigest()
        self.assertEqual(prompt, "base prompt\n\nEvaluation prompt variant:\ncategory gate\n")
        self.assertEqual(suffix_hash, expected_hash)

        unchanged, empty_hash = enrichment_pipeline.append_triage_prompt_suffix("base prompt", "")
        self.assertEqual(unchanged, "base prompt")
        self.assertEqual(empty_hash, "")

    def test_project_patch_applies_resource_url_budget_immediately(self) -> None:
        project = project_record.default_project_record("proj:resourcepatch", "service_url", "https://example.com")
        records = [
            {
                "url": f"https://example.com/docs/page-{index}",
                "role": "docs",
                "source": "resource_page:https://example.com",
                "confidence": "confirmed",
            }
            for index in range(20)
        ]
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-07-11T00:00:00+00:00")
        bundle.add_record(EvidenceRecord(
            kind="project_patch",
            source="resource_page",
            classification="derived",
            observed_at=bundle.collected_at,
            value={"fields": {"resource_url_records": records}},
            context={"allowed_fields": ["resource_url_records"]},
        ))

        registry.apply_collector_snapshot_evidence(project, bundle)

        self.assertEqual(len(project["resource_url_records"]), 4)

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

    def test_llm_metrics_record_sizes_without_prompt_or_endpoint(self) -> None:
        requests: list[dict] = []
        with mock.patch.object(
            llm_transport_core,
            "run_local_llm",
            return_value=(
                {"name": "例"},
                {
                    "transport": "openai_compatible_endpoint",
                    "endpoint": "https://secret.example/v1",
                    "model": "fixture",
                    "api_style": "chat",
                    "prompt_version": "fixture-v1",
                },
            ),
        ):
            payload, _ = llm_transport_core.run_llm_with_metrics("調査", purpose="triage", requests=requests)

        self.assertEqual(payload, {"name": "例"})
        self.assertEqual(requests[0]["status"], "success")
        self.assertEqual(requests[0]["prompt_chars"], 2)
        self.assertEqual(requests[0]["prompt_bytes"], 6)
        self.assertGreater(requests[0]["response_bytes"], 0)
        self.assertNotIn("prompt", requests[0])
        self.assertNotIn("endpoint", requests[0])

    def test_llm_metrics_separate_timeout_failure(self) -> None:
        requests: list[dict] = []
        with mock.patch.object(llm_transport_core, "run_local_llm", side_effect=TimeoutError("timed out")):
            with self.assertRaises(TimeoutError):
                llm_transport_core.run_llm_with_metrics("prompt", purpose="planning", requests=requests)
        self.assertEqual(requests[0]["status"], "failed")
        self.assertEqual(requests[0]["error_category"], "timeout")

    def test_llm_metric_budget_preserves_triage_and_aggregates_repository_calls(self) -> None:
        requests = [
            {"purpose": "repository_analysis", "status": "success", "duration_ms": 10, "prompt_bytes": 100}
            for _index in range(20)
        ] + [
            {"purpose": "planning", "status": "success", "duration_ms": 20, "prompt_bytes": 200},
            {"purpose": "triage", "status": "success", "duration_ms": 30, "prompt_bytes": 300},
        ]

        metrics = storage_core.normalize_run_metrics({"llm_requests": requests})

        self.assertEqual(len(metrics["llm_requests"]), 16)
        self.assertEqual(metrics["llm_requests"][-2]["purpose"], "planning")
        self.assertEqual(metrics["llm_requests"][-1]["purpose"], "triage")
        self.assertEqual(metrics["llm_request_summary"]["repository_analysis"]["count"], 20)
        self.assertEqual(metrics["llm_request_summary"]["repository_analysis"]["prompt_bytes"], 2000)

    def test_command_llm_transport_invokes_configured_command(self) -> None:
        completed = subprocess.CompletedProcess(
            args="fixture-llm",
            returncode=0,
            stdout='{"name":"Fixture"}',
            stderr="",
        )
        with (
            mock.patch.dict(llm_transport_core.os.environ, {
                "ATPROTO_SURVEY_LLM_COMMAND": "fixture-llm",
                "ATPROTO_SURVEY_LLM_TIMEOUT": "12",
            }),
            mock.patch.object(llm_transport_core.subprocess, "run", return_value=completed) as run,
        ):
            payload, meta = llm_transport_core.run_local_llm("prompt")

        self.assertEqual(payload, {"name": "Fixture"})
        self.assertEqual(meta["transport"], "command")
        self.assertEqual(run.call_args.kwargs["input"], "prompt")
        self.assertTrue(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 12.0)

    def test_endpoint_retries_only_rejected_compatible_payload(self) -> None:
        rejected = urllib.error.HTTPError(
            "https://llm.example/v1/chat/completions", 400, "unsupported response_format", {}, None
        )
        accepted = ({"choices": [{"message": {"content": '{"name":"Fixture"}'}}]}, "")
        with mock.patch.object(
            llm_transport_core,
            "request_json_post_raw",
            side_effect=[rejected, accepted],
        ) as request:
            payload, meta = llm_transport_core.run_local_llm_via_endpoint(
                "prompt", "https://llm.example/v1", "fixture", "", api_style="chat"
            )

        self.assertEqual(payload, {"name": "Fixture"})
        self.assertEqual(meta["api_style"], "chat")
        self.assertEqual(request.call_count, 2)
        first_payload = request.call_args_list[0].args[1]
        second_payload = request.call_args_list[1].args[1]
        self.assertIn("response_format", first_payload)
        self.assertNotIn("response_format", second_payload)

    def test_endpoint_timeout_is_not_retried(self) -> None:
        with mock.patch.object(
            llm_transport_core,
            "request_json_post_raw",
            side_effect=TimeoutError("model timed out"),
        ) as request:
            with self.assertRaises(TimeoutError):
                llm_transport_core.run_local_llm_via_endpoint(
                    "prompt", "https://llm.example/v1", "fixture", "", api_style="auto"
                )
        self.assertEqual(request.call_count, 1)

    def test_endpoint_invalid_model_content_is_not_retried(self) -> None:
        response = ({"choices": [{"message": {"content": "not-json"}}]}, "")
        with mock.patch.object(
            llm_transport_core,
            "request_json_post_raw",
            return_value=response,
        ) as request:
            with self.assertRaises(llm_transport_core.LLMResponseError) as raised:
                llm_transport_core.run_local_llm_via_endpoint(
                    "prompt", "https://llm.example/v1", "fixture", "", api_style="auto"
                )
        self.assertEqual(request.call_count, 1)
        self.assertEqual(llm_transport_core.classify_llm_request_error(raised.exception), "invalid_response")

    def test_endpoint_transport_failure_and_rate_limit_are_not_retried(self) -> None:
        with mock.patch.object(
            llm_transport_core,
            "request_json_post_raw",
            side_effect=OSError("connection refused"),
        ) as request:
            with self.assertRaises(llm_transport_core.LLMTransportError) as transport_error:
                llm_transport_core.run_local_llm_via_endpoint(
                    "prompt", "https://llm.example/v1", "fixture", "", api_style="auto"
                )
        self.assertEqual(request.call_count, 1)
        self.assertEqual(llm_transport_core.classify_llm_request_error(transport_error.exception), "transport_failure")

        limited = urllib.error.HTTPError(
            "https://llm.example/v1/chat/completions", 429, "rate limited", {}, None
        )
        with mock.patch.object(
            llm_transport_core,
            "request_json_post_raw",
            side_effect=limited,
        ) as request:
            with self.assertRaisesRegex(RuntimeError, "HTTP 429") as rate_error:
                llm_transport_core.run_local_llm_via_endpoint(
                    "prompt", "https://llm.example/v1", "fixture", "", api_style="auto"
                )
        self.assertEqual(request.call_count, 1)
        self.assertEqual(llm_transport_core.classify_llm_request_error(rate_error.exception), "rate_limited")

    def test_search_collector_returns_evidence_without_mutating_project(self) -> None:
        project = self.make_project("proj:researchbundle")
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
            self.make_project("proj:researchratelimit"),
            {"repo_search_queries": ["Example App"]},
            dependencies,
        )

        self.assertEqual(bundle.failures[0].category, "rate_limit")
        self.assertTrue(bundle.failures[0].retryable)
        self.assertEqual(bundle.collector_results[0].status, "rate_limited")

    def test_github_repo_collector_returns_snapshot_without_mutating_project(self) -> None:
        project = self.make_project("proj:researchreposnapshot", "repo_url", "https://github.com/example/app")
        original = copy.deepcopy(project)
        dependencies = investigation_core.RepositoryCollectorDependencies(
            now=lambda: "2026-06-13T00:00:00+00:00",
            fetch_repo=lambda repo_url: {
                "repo": {
                    "full_name": "example/app",
                    "description": "Example App",
                    "unused_api_payload": "x" * 1000,
                },
                "readme_text": "https://app.example.com",
                "tree": [{"path": "lexicons/app.example.post.json"}],
                "default_branch": "main",
            },
            repo_lexicon_hints=lambda repo_url, **kwargs: [
                {"nsid": "app.example.post", "path": "lexicons/app.example.post.json", "schema": {"lexicon": 1}}
            ],
            extract_urls=lambda text: ["https://app.example.com"],
            repo_semantic_hints=lambda repo_url, **kwargs: [{
                "nsid": "app.example.post",
                "path": "src/client.ts",
                "predicate": "uses_collection",
                "operation": "write",
            }],
        )

        bundle = investigation_core.collect_repository_evidence(
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
        self.assertEqual(snapshot["semantic_hint_count"], 1)
        self.assertNotIn("semantic_hints", snapshot)
        self.assertNotIn("unused_api_payload", snapshot["repo"])

    def test_github_candidate_trust_collection_excludes_repo_content(self) -> None:
        project = self.make_project("proj:githubtrustonly", "service_url", "https://app.example.com")
        lexicon_hints = mock.Mock(return_value=[{"nsid": "app.example.post"}])
        semantic_hints = mock.Mock(return_value=[{"nsid": "app.example.post"}])
        dependencies = investigation_core.RepositoryCollectorDependencies(
            now=lambda: "2026-07-18T00:00:00+00:00",
            fetch_repo=lambda _repo_url: {
                "repo": {
                    "name": "app",
                    "full_name": "example/app",
                    "description": "Unconfirmed description",
                    "homepage": "https://app.example.com",
                },
                "readme_text": "See https://app.example.com",
                "tree": [{"path": "lexicons/app.example.post.json", "type": "blob"}],
                "default_branch": "main",
            },
            repo_lexicon_hints=lexicon_hints,
            extract_urls=registry.extract_urls,
            repo_semantic_hints=semantic_hints,
        )

        bundle = investigation_core.collect_repository_evidence(
            project,
            "https://github.com/example/app",
            dependencies,
            trust_only=True,
        )

        self.assertFalse(any(record.kind == "github_repo_snapshot" for record in bundle.records))
        self.assertTrue(any(record.kind == "relation" for record in bundle.records))
        lexicon_hints.assert_not_called()
        semantic_hints.assert_not_called()

    def test_github_readme_url_snapshot_has_count_and_byte_budget(self) -> None:
        values = [f"https://example.com/path-{index}" for index in range(100)] + [
            "https://example.com/" + "x" * 3000
        ]
        bounded = investigation_core.bounded_github_readme_urls(values)
        self.assertLessEqual(len(bounded), investigation_core.GITHUB_README_URL_MAX_COUNT)
        self.assertLessEqual(
            len(json.dumps(bounded, ensure_ascii=False).encode("utf-8")),
            investigation_core.GITHUB_README_URL_MAX_BYTES,
        )
        self.assertEqual(bounded[0], values[0])
        self.assertNotIn(values[-1], bounded)

    def test_github_readme_relation_applies_candidate_beyond_snapshot_budget(self) -> None:
        project = self.make_project("proj:readme-budget", "repo_url", "https://github.com/example/app")
        project["name"] = "Example App"
        urls = [f"https://noise-{index}.invalid/path" for index in range(100)] + [
            "https://exampleapp.example.com"
        ]
        dependencies = investigation_core.RepositoryCollectorDependencies(
            now=lambda: "2026-07-10T00:00:00+00:00",
            fetch_repo=lambda repo_url: {
                "repo": {"name": "app", "full_name": "example/app", "description": "Example App"},
                "readme_text": "fixture",
                "tree": [],
                "default_branch": "main",
            },
            repo_lexicon_hints=lambda repo_url, **kwargs: [],
            extract_urls=lambda text: urls,
        )
        bundle = investigation_core.collect_repository_evidence(
            project, "https://github.com/example/app", dependencies
        )
        snapshot = bundle.records[0].value
        assert isinstance(snapshot, dict)
        self.assertNotIn("https://exampleapp.example.com", snapshot["readme_urls"])

        registry.apply_github_repo_evidence(project, bundle)

        self.assertIn("https://exampleapp.example.com", project["service_url_candidates"])

    def test_confirmed_candidate_github_content_does_not_add_manual_seed_provenance(self) -> None:
        project = self.make_project("proj:confirmed-candidate", "service_url", "https://app.example.com")
        repo_url = "https://github.com/example/app"
        registry.confirm_source_code_url(
            project,
            repo_url,
            "candidate_repo_trust_inspection",
            relation="mutual_first_party_link",
            evidence_kind="candidate_repo_trust_inspection",
        )
        dependencies = investigation_core.RepositoryCollectorDependencies(
            now=lambda: "2026-07-18T00:00:00+00:00",
            fetch_repo=lambda _repo_url: {
                "repo": {"name": "app", "full_name": "example/app", "description": "Confirmed app"},
                "readme_text": "",
                "tree": [],
                "default_branch": "main",
            },
            repo_lexicon_hints=lambda _repo_url, **_kwargs: [],
            extract_urls=lambda _text: [],
        )
        bundle = investigation_core.collect_repository_evidence(project, repo_url, dependencies)

        registry.apply_github_repo_evidence(project, bundle, confirm_repo_snapshot=False)

        confirmed_records = [
            item
            for item in project["source_repo_records"]
            if item.get("url") == repo_url and item.get("confidence") == "confirmed"
        ]
        self.assertEqual(len(confirmed_records), 1)
        self.assertEqual(confirmed_records[0]["source"], "candidate_repo_trust_inspection")
        self.assertNotIn("github_repo_seed", {item.get("source") for item in confirmed_records})
        self.assertTrue(
            any(
                item.get("value") == "Confirmed app" and item.get("source") == "github:example/app"
                for item in project["summary_candidates"]
            )
        )

    def test_tangled_repo_collector_returns_readme_snapshot_without_mutating_project(self) -> None:
        project = self.make_project("proj:tangledreposnapshot", "repo_url", "https://tangled.org/dev.example/app")
        original = copy.deepcopy(project)
        dependencies = investigation_core.TangledRepoCollectorDependencies(
            now=lambda: "2026-06-13T00:00:00+00:00",
            fetch_tangled_repo=lambda repo_url: {
                "repo": {"full_name": "dev.example/app", "name": "app", "description": "Example Tangled app"},
                "readme_text": "See https://app.example.com for docs.",
                "tree": [{"path": "README.md", "type": "blob"}],
                "default_branch": "main",
            },
            extract_urls=lambda text: registry.extract_urls(text),
        )

        bundle = investigation_core.collect_tangled_repo_evidence(
            project,
            "https://tangled.org/dev.example/app",
            dependencies,
        )

        self.assertEqual(project, original)
        self.assertEqual(bundle.records[0].kind, "tangled_repo_inspection")
        snapshot = bundle.records[0].value
        assert isinstance(snapshot, dict)
        self.assertEqual(snapshot["readme_urls"], ["https://app.example.com"])
        self.assertIn("See https://app.example.com", snapshot["readme_excerpt"])
        self.assertTrue(any(record.source.startswith("tangled_readme:") for record in bundle.records))

    def test_extract_urls_trims_markdown_image_link_boundaries(self) -> None:
        text = "[![Crate](https://img.shields.io/crates/v/app.svg)](https://crates.io/crates/app)"

        self.assertEqual(
            registry.extract_urls(text),
            ["https://img.shields.io/crates/v/app.svg", "https://crates.io/crates/app"],
        )

    def test_evidence_fixture_roundtrip_preserves_ordered_records(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "project_registry" / "evidence-v2-synthetic-reference.json"
        bundle = EvidenceBundle.load(fixture_path)

        self.assertEqual(bundle.schema_version, 2)
        self.assertEqual(bundle.project_key, "proj:synthetic-reference")
        self.assertEqual(bundle.records[0].kind, "github_repo_snapshot")
        self.assertEqual(EvidenceBundle.from_json(bundle.to_json()).to_dict(), bundle.to_dict())

    def test_saved_evidence_fixture_can_be_re_evaluated_without_collection(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "project_registry" / "evidence-v2-synthetic-reference.json"
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
        project = self.make_project("proj:researchqueries")
        project["name"] = "Example App"
        project["input_nsids"] = ["app.example.post"]
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
        project = self.make_project("proj:researchcollect")
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
                "description": "Example App for atproto",
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
        project = self.make_project("proj:researchrepo", "repo_url", "https://github.com/example/app")
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
            mock.patch.object(repository_sources, "fetch_github_repo", return_value=repo_payload),
            mock.patch.object(repository_material, "repo_lexicon_hints", return_value=lexicons),
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
            project = self.make_project("proj:researchenrich")
            with (
                mock.patch.object(enrichment_pipeline, "should_refresh_lexicon_surface", return_value=False),
                mock.patch.object(enrichment_pipeline, "should_refresh_recent_record_samples", return_value=False),
                mock.patch.object(enrichment_pipeline, "plan_investigation_queries", return_value={}),
                mock.patch.object(
                    enrichment_pipeline,
                    "collect_investigation_evidence",
                    return_value=EvidenceBundle(
                        project_key=project["project_key"],
                        collected_at="2026-06-13T00:00:00+00:00",
                    ),
                ),
                mock.patch.object(llm_transport_core, "run_local_llm", side_effect=RuntimeError("LLM unavailable")),
            ):
                enriched = enrichment_pipeline.enrich_project(conn, project)
            project_record.save_project(conn, enriched, enriched=True)
            conn.commit()
            fetched = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["project_key"], "proj:researchenrich")
        self.assertNotIn(runtime_view.RUNTIME_INVESTIGATION_RUN_ID, fetched)
        self.assertIn("LLM unavailable", "\n".join(fetched["last_run_errors"]))
        self.assertTrue(fetched["last_enriched_at"])
        conn = registry.open_db()
        try:
            run = storage_core.latest_investigation_run(conn, "proj:researchenrich")
        finally:
            conn.close()
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run["metrics"]["version"], 2)
        self.assertEqual(
            set(run["metrics"]["phases"]),
            {
                "initial_collection_ms",
                "planning_ms",
                "investigation_collection_ms",
                "triage_ms",
                "evaluation_ms",
                "run_history_save_ms",
                "project_save_ms",
            },
        )
        self.assertEqual(run["metrics"]["llm_requests"][0]["purpose"], "triage")
        self.assertEqual(run["metrics"]["llm_requests"][0]["status"], "failed")
        self.assertGreater(run["metrics"]["llm_requests"][0]["prompt_bytes"], 0)

    def test_enrich_collects_known_github_repo_once_per_run(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:single-repo-collection", "repo_url", "https://github.com/example/app")
        project["source_code_urls"] = ["https://github.com/example/app"]
        repo_bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-07-10T00:00:00+00:00")
        repo_bundle.add_collector_result(CollectorResult(
            source="github_repo", operation="inspect", status="success",
            observed_at=repo_bundle.collected_at, request_count=1, result_count=1,
        ))
        empty_bundle = EvidenceBundle(project_key=project["project_key"], collected_at=repo_bundle.collected_at)
        try:
            with (
                mock.patch.object(enrichment_pipeline, "should_refresh_lexicon_surface", return_value=False),
                mock.patch.object(enrichment_pipeline, "should_refresh_recent_record_samples", return_value=False),
                mock.patch.object(enrichment_pipeline, "collect_github_repo_evidence", return_value=repo_bundle) as collect_repo,
                mock.patch.object(enrichment_pipeline, "apply_github_repo_evidence"),
                mock.patch.object(enrichment_pipeline, "plan_investigation_queries", return_value={}),
                mock.patch.object(enrichment_pipeline, "collect_investigation_evidence", return_value=empty_bundle),
                mock.patch.object(llm_transport_core, "run_local_llm", side_effect=RuntimeError("LLM unavailable")),
            ):
                enrichment_pipeline.enrich_project(conn, project)
        finally:
            conn.close()

        self.assertEqual(collect_repo.call_count, 1)
        self.assertEqual(collect_repo.call_args.args[1], "https://github.com/example/app")

    def test_repository_analysis_adds_declared_docs_site_as_candidate_resource(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:repo-docs", "repo_url", "https://github.com/example/app")
        project["source_code_urls"] = ["https://github.com/example/app"]
        repo_bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-07-26T00:00:00+00:00")
        repo_bundle.add_record(EvidenceRecord(
            kind="github_repo_snapshot",
            source="github_repo:https://github.com/example/app",
            classification="confirmed",
            observed_at=repo_bundle.collected_at,
            value={
                "provider": "github",
                "repo": {"name": "app", "description": "Example application"},
                "revision": "abc123",
                "readme_excerpt": "Example application.",
                "configuration_hints": [{
                    "path": "mkdocs.yml",
                    "kind": "documentation_config",
                    "content_excerpt": "site_url: https://docs.example.com",
                }],
                "tree_paths": ["mkdocs.yml", "docs/index.md", "docs/setup.md"],
            },
        ))
        repo_bundle.add_collector_result(CollectorResult(
            source="github_repo", operation="inspect", status="success",
            observed_at=repo_bundle.collected_at, request_count=1, result_count=1,
        ))
        empty_bundle = EvidenceBundle(project_key=project["project_key"], collected_at=repo_bundle.collected_at)

        def llm_result(prompt: str, **_kwargs: object) -> tuple[dict, dict]:
            if "Analyze a confirmed source repository" in prompt:
                return ({
                    "summary": "ドキュメントサイトを持つAT Protocolアプリケーション。",
                    "summary_evidence": ["E02"],
                    "observations": [{
                        "predicate": "declares_documentation_site_url",
                        "basis": "declared",
                        "value": "https://docs.example.com",
                        "evidence": ["E02"],
                    }],
                }, {"model": "fixture", "transport": "fixture"})
            return ({}, {"model": "fixture", "transport": "fixture"})

        try:
            with (
                mock.patch.object(enrichment_pipeline, "should_refresh_lexicon_surface", return_value=False),
                mock.patch.object(enrichment_pipeline, "should_refresh_recent_record_samples", return_value=False),
                mock.patch.object(enrichment_pipeline, "collect_github_repo_evidence", return_value=repo_bundle),
                mock.patch.object(enrichment_pipeline, "apply_github_repo_evidence"),
                mock.patch.object(enrichment_pipeline, "plan_investigation_queries", return_value={}),
                mock.patch.object(enrichment_pipeline, "collect_investigation_evidence", return_value=empty_bundle),
                mock.patch.object(llm_transport_core, "run_local_llm", side_effect=llm_result) as run_llm,
            ):
                enriched = enrichment_pipeline.enrich_project(
                    conn, project, llm_max_output_tokens=2048
                )
        finally:
            conn.close()

        docs_records = [
            item for item in enriched["resource_url_records"]
            if item.get("role") == "docs" and item.get("url") == "https://docs.example.com"
        ]
        self.assertEqual(
            len(docs_records), 1,
            {"resources": enriched["resource_url_records"], "analysis": enriched["repository_analysis_results"]},
        )
        self.assertEqual(docs_records[0]["confidence"], "candidate")
        self.assertEqual(
            enriched["repository_analysis_results"][0]["prompt_version"],
            "repository-analysis-v6",
        )
        self.assertGreaterEqual(run_llm.call_count, 2)
        self.assertTrue(all(
            call.kwargs["llm_max_output_tokens"] == 2048
            for call in run_llm.call_args_list
        ))

    def test_repository_collection_failure_preserves_previous_success(self) -> None:
        project = self.make_project(
            "proj:repo-collection-failure", "repo_url", "https://github.com/example/app"
        )
        project["repository_analysis_results"] = [{
            "version": repository_analysis.ANALYSIS_VERSION,
            "policy_version": repository_analysis.POLICY_VERSION,
            "repo_url": "https://github.com/example/app",
            "provider": "github",
            "source_revision": "old-revision",
            "summary": "previous success",
        }]

        enrichment_pipeline.record_repository_collection_failure(
            project, "https://github.com/example/app"
        )

        result = project["repository_analysis_results"][0]
        self.assertEqual(result["summary"], "previous success")
        self.assertEqual(result["source_revision"], "old-revision")
        self.assertEqual(result["last_attempt"]["status"], "failed")
        self.assertEqual(result["last_attempt"]["error_category"], "provider_fetch_failed")
        self.assertTrue(repository_analysis.result_is_stale(result))

    def test_enrich_candidate_repo_collects_trust_before_full_content(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:candidate-repo-trust")
        repo_url = "https://github.com/example/app"
        project["candidate_source_code_urls"] = [repo_url]
        trust_bundle = EvidenceBundle(
            project_key=project["project_key"],
            collected_at="2026-07-18T00:00:00+00:00",
        )
        repo_bundle = EvidenceBundle(
            project_key=project["project_key"],
            collected_at="2026-07-18T00:00:01+00:00",
        )
        empty_bundle = EvidenceBundle(
            project_key=project["project_key"],
            collected_at="2026-07-18T00:00:02+00:00",
        )
        try:
            with (
                mock.patch.object(enrichment_pipeline, "should_refresh_lexicon_surface", return_value=False),
                mock.patch.object(enrichment_pipeline, "should_refresh_recent_record_samples", return_value=False),
                mock.patch.object(enrichment_pipeline, "candidate_repo_trust_probe_urls", return_value=[repo_url]),
                mock.patch.object(
                    enrichment_pipeline,
                    "collect_github_repo_evidence",
                    side_effect=[trust_bundle, repo_bundle],
                ) as collect_repo,
                mock.patch.object(enrichment_pipeline, "apply_candidate_repo_trust_evidence"),
                mock.patch.object(enrichment_pipeline, "confirm_source_code_url_with_trust", return_value=True),
                mock.patch.object(enrichment_pipeline, "apply_github_repo_evidence") as apply_repo,
                mock.patch.object(enrichment_pipeline, "plan_investigation_queries", return_value={}),
                mock.patch.object(enrichment_pipeline, "collect_investigation_evidence", return_value=empty_bundle),
                mock.patch.object(llm_transport_core, "run_local_llm", side_effect=RuntimeError("LLM unavailable")),
            ):
                enrichment_pipeline.enrich_project(conn, project)
        finally:
            conn.close()

        self.assertEqual(collect_repo.call_count, 2)
        self.assertEqual(collect_repo.call_args_list[0].kwargs, {"trust_only": True})
        self.assertEqual(collect_repo.call_args_list[1].kwargs, {})
        self.assertEqual(apply_repo.call_count, 1)
        self.assertEqual(apply_repo.call_args.kwargs, {"confirm_repo_snapshot": False})

    def test_evidence_v2_rejects_v1_and_keeps_source_status(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceBundle.from_dict({
                "schema_version": 1,
                "project_key": "proj:v1",
                "collected_at": "2026-06-13T00:00:00+00:00",
                "records": [],
                "failures": [],
            })
        bundle = EvidenceBundle(project_key="proj:v2", collected_at="2026-06-13T00:00:00+00:00")
        bundle.add_collector_result(CollectorResult(
            source="github", operation="search", status="partial", observed_at=bundle.collected_at,
            request_count=2, result_count=1, stop_reason="rate_limit",
        ))
        decoded = EvidenceBundle.from_json(bundle.to_json())
        self.assertEqual(decoded.schema_version, 2)
        self.assertEqual(decoded.collector_results[0].status, "partial")

    def test_investigation_storage_and_replay_are_network_free(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:storedrun")
        project["service_urls"] = ["https://example.com"]
        project[runtime_view.RUNTIME_VIEW_MARKER] = True
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-13T00:00:00+00:00")
        bundle.add_record(relation_core.relation_record(
            source="fixture", classification="derived", observed_at=bundle.collected_at,
            subject_type="repo", subject_value="https://github.com/example/app",
            predicate="uses_collection", object_type="nsid", object_value="app.example.post",
        ))
        bundle.add_collector_result(CollectorResult(
            source="fixture", operation="load", status="success", observed_at=bundle.collected_at,
            request_count=0, result_count=1,
        ))
        try:
            run_id = storage_core.save_investigation_run(
                conn, project_key=project["project_key"], started_at=bundle.collected_at,
                completed_at=bundle.collected_at, input_project=project, output_project=project, bundle=bundle,
            )
            conn.commit()
            run = storage_core.load_investigation_run(conn, run_id)
        finally:
            conn.close()
        assert run is not None
        self.assertNotIn(runtime_view.RUNTIME_VIEW_MARKER, run["input_project"])
        self.assertEqual(run["input_project"]["service_urls"], ["https://example.com"])
        replayed = registry.replay_core.replay_and_diff(
            run["input_project"], run["bundle"], run["evaluation"], registry.replay_dependencies()
        )
        self.assertEqual(replayed["evaluation"]["atproto_contribution_level"], "substantial")

    def test_investigation_storage_keeps_run_metrics_out_of_replay_evaluation(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:runmetrics")
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-07-10T00:00:00+00:00")
        try:
            run_id = storage_core.save_investigation_run(
                conn,
                project_key=project["project_key"],
                started_at=bundle.collected_at,
                completed_at=bundle.collected_at,
                input_project=project,
                output_project=project,
                bundle=bundle,
                run_metrics={
                    "total_ms": 120,
                    "phases": {"initial_collection_ms": 80, "unknown_ms": 999},
                    "llm_requests": [{
                        "purpose": "triage",
                        "status": "timeout",
                        "duration_ms": 40,
                        "prompt_bytes": 1234,
                        "secret": "must not persist",
                    }],
                },
            )
            conn.commit()
            run = storage_core.load_investigation_run(conn, run_id)
            summary = storage_core.list_investigation_run_summaries(conn, project_key=project["project_key"])[0]
            raw = json.loads(conn.execute(
                "SELECT evaluation_json FROM investigation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()[0])
        finally:
            conn.close()

        assert run is not None
        self.assertNotIn(storage_core.RUN_METRICS_KEY, run["evaluation"])
        self.assertGreaterEqual(run["metrics"]["total_ms"], 120)
        self.assertIn("run_history_save_ms", run["metrics"]["phases"])
        self.assertNotIn("unknown_ms", run["metrics"]["phases"])
        self.assertNotIn("secret", run["metrics"]["llm_requests"][0])
        self.assertEqual(summary["total_ms"], run["metrics"]["total_ms"])
        self.assertIn(storage_core.RUN_METRICS_KEY, raw)
        replayed = registry.replay_core.replay_and_diff(
            run["input_project"], run["bundle"], run["evaluation"], registry.replay_dependencies()
        )
        self.assertNotIn(
            storage_core.RUN_METRICS_KEY,
            {item["field"] for item in replayed["diff"]["changes"]},
        )

    def test_collector_result_projection_keeps_worst_source_status(self) -> None:
        results = storage_core.aggregate_collector_results([
            CollectorResult(
                source="github_repo", operation="inspect", status="success",
                observed_at="2026-07-10T00:00:00+00:00", request_count=1, result_count=1,
            ),
            CollectorResult(
                source="github_repo", operation="inspect", status="failed",
                observed_at="2026-07-10T00:00:01+00:00", request_count=1, stop_reason="timeout",
            ),
        ])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "failed")
        self.assertEqual(results[0].request_count, 2)
        self.assertEqual(results[0].result_count, 1)

    def test_collector_run_projects_counts_without_copying_bundle_records(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:collector-family")
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-13T00:00:00+00:00")
        bundle.add_record(relation_core.relation_record(
            source="github_readme:https://github.com/example/app", classification="candidate",
            observed_at=bundle.collected_at, subject_type="repo", subject_value="https://github.com/example/app",
            predicate="source_repository", object_type="website", object_value="https://example.com",
        ))
        bundle.add_collector_result(CollectorResult(
            source="github_repo", operation="inspect", status="success", observed_at=bundle.collected_at,
            request_count=3, result_count=1,
        ))
        bundle.add_collector_result(CollectorResult(
            source="github_repo", operation="inspect", status="success", observed_at=bundle.collected_at,
            request_count=2, result_count=1,
        ))
        try:
            run_id = storage_core.save_investigation_run(
                conn, project_key=project["project_key"], started_at=bundle.collected_at,
                completed_at=bundle.collected_at, input_project=project, output_project=project, bundle=bundle,
            )
            rows = conn.execute(
                "SELECT result_json, records_json, failures_json FROM collector_runs WHERE run_id = ?", (run_id,)
            ).fetchall()
            projected = storage_core.list_collector_run_rows(conn, run_id)
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        result = json.loads(rows[0]["result_json"])
        self.assertEqual(result["evidence_storage"], "bundle_json")
        self.assertEqual(result["request_count"], 5)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(rows[0]["records_json"], "[]")
        self.assertEqual(rows[0]["failures_json"], "[]")
        self.assertEqual(projected[0]["record_count"], 1)
        self.assertEqual(projected[0]["failure_count"], 0)

    def test_service_replay_uses_saved_probe_without_follow_up_network(self) -> None:
        project = self.make_project("proj:service-replay", "service_url", "https://example.com")
        project["manual_seeds"] = [{"type": "service_url", "value": "https://example.com"}]
        bundle = service_core.collect_service_probe_evidence(
            project["project_key"], "https://example.com",
            {"status_code": 200, "title": "Example", "description": "Open app",
             "service_status": "live", "service_status_confidence": "medium",
             "service_status_evidence": ["app_shell_detected"], "links": [], "urls": []},
            observed_at="2026-06-13T00:00:00+00:00",
        )
        with mock.patch.object(registry, "probe_service_url", side_effect=AssertionError("network called")):
            replayed = registry.replay_core.replay_bundle(project, bundle, registry.replay_dependencies())
        self.assertEqual(replayed["service_status"], "live")
        self.assertIn("app_shell_detected", replayed["service_status_evidence"])

    def test_service_probe_evidence_applies_count_and_byte_budgets(self) -> None:
        html = "調査" * 10000
        links = [
            {"url": f"https://noise-{index}.example/path", "text": "noise" * 200}
            for index in range(500)
        ] + [
            {"url": "https://github.com/example/app", "text": "Source code"},
            {"url": "https://example.com/docs", "text": "Documentation"},
            {"url": "https://bsky.app/profile/example.com", "text": "Bluesky"},
            {"url": "https://example.com/" + "x" * 3000, "text": "oversized"},
        ]
        urls = [f"https://noise-{index}.example/path" for index in range(500)] + [
            "https://bsky.app/profile/example.com"
        ]
        bundle = service_core.collect_service_probe_evidence(
            "proj:service-budget",
            "https://example.com",
            {
                "status_code": 200,
                "title": "Example",
                "description": "Example service",
                "html": html,
                "links": links,
                "urls": urls,
            },
            observed_at="2026-07-10T00:00:00+00:00",
            supports_project=True,
        )
        result = next(record.value for record in bundle.records if record.kind == "service_verification")

        self.assertLessEqual(len(result["html_extract"].encode("utf-8")), service_core.SERVICE_HTML_EXTRACT_MAX_BYTES)
        self.assertEqual(result["content_sha256"], hashlib.sha256(html.encode("utf-8")).hexdigest())
        self.assertGreater(result["observed_html_bytes"], len(result["html_extract"].encode("utf-8")))
        self.assertEqual(result["observed_link_count"], len(links))
        self.assertLessEqual(len(result["links"]), service_core.SERVICE_LINK_MAX_COUNT)
        self.assertLessEqual(
            len(json.dumps(result["links"], ensure_ascii=False).encode("utf-8")),
            service_core.SERVICE_LINK_MAX_BYTES,
        )
        self.assertLess(len(result["links"]), result["observed_link_count"])
        self.assertIn("https://github.com/example/app", {item["url"] for item in result["links"]})
        self.assertIn("https://example.com/docs", {item["url"] for item in result["links"]})
        self.assertFalse(any(
            len(item["url"].encode("utf-8")) > service_core.SERVICE_URL_ITEM_MAX_BYTES
            for item in result["links"]
        ))
        self.assertIn("https://bsky.app/profile/example.com", result["urls"])
        self.assertEqual(result["observed_url_count"], len(urls))
        self.assertLessEqual(
            len(json.dumps(result["urls"], ensure_ascii=False).encode("utf-8")),
            service_core.SERVICE_URL_MAX_BYTES,
        )
        self.assertTrue(any(
            record.kind == "relation"
            and record.value.get("predicate") == "source_repository"
            for record in bundle.records
        ))

    def test_service_verification_current_state_keeps_latest_surface(self) -> None:
        results: list[dict] = []
        results = service_core.merge_service_verification_results(results, {
            "lane": "static", "requested_url": "https://example.com/", "title": "old"
        })
        results = service_core.merge_service_verification_results(results, {
            "lane": "static", "requested_url": "https://example.com", "title": "new"
        })
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "new")

        for index in range(40):
            results = service_core.merge_service_verification_results(results, {
                "lane": "follow_up",
                "requested_url": f"https://example.com/page-{index}",
            })
        self.assertEqual(len(results), service_core.SERVICE_VERIFICATION_RESULT_MAX_COUNT)
        self.assertEqual(results[-1]["requested_url"], "https://example.com/page-39")

    def test_saved_service_evidence_recomputes_policy_derived_same_site_flag(self) -> None:
        project = self.make_project("proj:saved-service-policy", "service_url", "https://foo.pages.dev")
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-13T00:00:00+00:00")
        bundle.add_record(EvidenceRecord(
            kind="service_verification",
            source="service_static:https://foo.pages.dev",
            classification="derived",
            observed_at=bundle.collected_at,
            value={
                "lane": "static",
                "requested_url": "https://foo.pages.dev",
                "final_url": "https://bar.pages.dev",
                "title": "",
                "description": "",
                "status_code": 200,
                "service_status": "unverified",
                "service_status_confidence": "none",
                "redirected": True,
                "redirect_same_host": False,
                "redirect_same_site_family": True,
                "redirect_http_to_https": False,
                "redirect_cross_host": True,
                "service_status_evidence": ["http_200", "redirect_same_site_family"],
                "links": [],
                "urls": [],
            },
        ))

        registry.apply_saved_service_evidence(project, bundle)

        result = project["service_verification_results"][0]
        self.assertFalse(result["redirect_same_site_family"])
        self.assertEqual(result["service_status_evidence"], ["http_200"])

    def test_service_integration_does_not_collect_follow_up_pages(self) -> None:
        project = self.make_project("proj:service-apply", "service_url", "https://example.com")
        project["website_url"] = "https://example.com"
        project["manual_seeds"] = [{"type": "service_url", "value": "https://example.com"}]
        bundle = service_core.collect_service_probe_evidence(
            project["project_key"], "https://example.com",
            {"status_code": 200, "title": "Example", "description": "Example service",
             "service_status": "live", "service_status_confidence": "medium",
             "service_status_evidence": ["app_shell_detected"], "urls": [],
             "links": [{"url": "https://example.com/docs", "text": "Docs"}]},
            observed_at="2026-06-13T00:00:00+00:00",
        )
        with mock.patch.object(registry, "probe_service_url", side_effect=AssertionError("network called")):
            registry.apply_service_evidence(project, bundle)
        self.assertEqual(len(project["service_verification_results"]), 1)

    def test_current_service_observation_replaces_stale_same_lane_scalar(self) -> None:
        service_url = "https://example.com"
        project = self.make_project("proj:current-service", "service_url", service_url)
        project["manual_seeds"] = [{"type": "service_url", "value": service_url, "source": "manual"}]
        project["website_url"] = service_url
        project["service_urls"] = [service_url]
        old_bundle = service_core.collect_service_probe_evidence(
            project["project_key"],
            service_url,
            {
                "status_code": 200, "final_url": service_url, "title": "Example", "description": "App",
                "service_status": "live", "service_status_confidence": "medium",
                "service_status_evidence": ["http_200", "login_surface_detected"],
                "links": [], "urls": [], "html": "<form>Sign in</form>",
            },
            observed_at="2026-07-15T00:00:00+00:00",
            supports_project=True,
        )
        registry.apply_service_evidence(project, old_bundle)
        self.assertEqual(project["service_status"], "live")

        new_bundle = service_core.collect_service_probe_evidence(
            project["project_key"],
            service_url,
            {
                "status_code": 200, "final_url": service_url, "title": "Example", "description": "Information",
                "service_status": "unverified", "service_status_confidence": "low",
                "service_status_evidence": ["http_200"], "links": [], "urls": [], "html": "Information",
            },
            observed_at="2026-07-16T00:00:00+00:00",
            supports_project=True,
        )
        registry.apply_service_evidence(project, new_bundle)

        self.assertEqual(project["service_status"], "unverified")
        self.assertEqual(project["service_status_confidence"], "low")
        self.assertEqual(project["service_status_evidence"], ["http_200"])
        self.assertEqual(len(project["service_verification_results"]), 1)

    def test_current_service_observation_ignores_follow_up_status(self) -> None:
        service_url = "https://example.com"
        project = self.make_project("proj:current-follow-up", "service_url", service_url)
        project["manual_seeds"] = [{"type": "service_url", "value": service_url, "source": "manual"}]
        project["website_url"] = service_url
        project["service_urls"] = [service_url]
        static_bundle = service_core.collect_service_probe_evidence(
            project["project_key"], service_url,
            {
                "status_code": 200, "final_url": service_url, "title": "Example", "description": "Information",
                "service_status": "unverified", "service_status_confidence": "low",
                "service_status_evidence": ["http_200"], "links": [], "urls": [], "html": "Information",
            },
            observed_at="2026-07-16T00:00:00+00:00", supports_project=True,
        )
        follow_up_bundle = service_core.collect_service_probe_evidence(
            project["project_key"], f"{service_url}/login",
            {
                "status_code": 200, "final_url": f"{service_url}/login", "title": "Sign in", "description": "",
                "service_status": "live", "service_status_confidence": "medium",
                "service_status_evidence": ["http_200", "login_surface_detected"],
                "links": [], "urls": [], "html": "<form>Sign in</form>",
            },
            observed_at="2026-07-16T00:01:00+00:00", lane="follow_up", supports_project=True,
        )
        static_bundle.extend(follow_up_bundle)

        registry.apply_service_evidence(project, static_bundle)

        self.assertEqual(project["service_status"], "unverified")
        self.assertEqual(project["service_status_confidence"], "low")
        self.assertEqual(project["service_status_evidence"], ["http_200"])

    def test_current_service_observation_prefers_whitewind_browser_surface(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "project_registry" / "browser-service-calibration-v1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        case = next(item for item in fixture["cases"] if item["case_id"] == "whitewind-rendered-public-app-surface")
        service_url = case["service_url"]
        project = self.make_project("proj:whitewind-current", "service_url", service_url)
        project["manual_seeds"] = [{"type": "service_url", "value": service_url, "source": "manual"}]
        project["website_url"] = service_url
        project["service_urls"] = [service_url]
        static_bundle = service_core.collect_service_probe_evidence(
            project["project_key"], service_url,
            {
                "status_code": 200, "final_url": service_url, "title": "WhiteWind", "description": "Docs",
                "service_status": "none", "service_status_confidence": "medium",
                "service_status_evidence": ["http_200", "docs_only_surface_detected"],
                "links": [], "urls": [], "html": "Documentation",
            },
            observed_at="2026-07-15T00:00:00+00:00", supports_project=True,
        )
        registry.apply_service_evidence(project, static_bundle)
        pages = list(case["navigation_results"])
        browser_bundle = service_core.collect_browser_service_evidence(
            project,
            service_url,
            service_core.BrowserDependencies(
                navigate=lambda _url: copy.deepcopy(pages.pop(0)),
                now=lambda: "2026-07-16T00:00:00+00:00",
            ),
        )

        before_projection = copy.deepcopy(project)
        registry.apply_service_evidence(project, browser_bundle)
        projection_input = copy.deepcopy(project)
        projection = registry.project_current_service_observation(project)

        self.assertEqual(before_projection["service_status"], "none")
        self.assertEqual(project["service_status"], "live")
        self.assertEqual(project["service_status_confidence"], "high")
        self.assertIn("browser_public_app_surface_detected", project["service_status_evidence"])
        self.assertNotIn("docs_only_surface_detected", project["service_status_evidence"])
        self.assertEqual(projection["service_status"], "live")
        self.assertEqual(project, projection_input)

    def test_current_service_projection_preserves_saved_investigation_history(self) -> None:
        service_url = "https://example.com"
        project = self.make_project("proj:current-history", "service_url", service_url)
        project["manual_seeds"] = [{"type": "service_url", "value": service_url, "source": "manual"}]
        project["website_url"] = service_url
        project["service_urls"] = [service_url]
        old_bundle = service_core.collect_service_probe_evidence(
            project["project_key"], service_url,
            {
                "status_code": 200, "final_url": service_url, "title": "Example", "description": "App",
                "service_status": "live", "service_status_confidence": "medium",
                "service_status_evidence": ["http_200", "login_surface_detected"],
                "links": [], "urls": [], "html": "<form>Sign in</form>",
            },
            observed_at="2026-07-15T00:00:00+00:00", supports_project=True,
        )
        old_input = copy.deepcopy(project)
        registry.apply_service_evidence(project, old_bundle)
        old_output = registry.finalize_project_shape(project)
        conn = registry.open_db()
        try:
            project_record.save_project(conn, old_output, enriched=True)
            run_id = storage_core.save_investigation_run(
                conn,
                project_key=project["project_key"],
                started_at=old_bundle.collected_at,
                completed_at=old_bundle.collected_at,
                input_project=old_input,
                output_project=old_output,
                bundle=old_bundle,
            )
            conn.commit()

            current = project_record.fetch_project(conn, project["project_key"])
            assert current is not None
            new_bundle = service_core.collect_service_probe_evidence(
                project["project_key"], service_url,
                {
                    "status_code": 200, "final_url": service_url, "title": "Example", "description": "Information",
                    "service_status": "unverified", "service_status_confidence": "low",
                    "service_status_evidence": ["http_200"], "links": [], "urls": [], "html": "Information",
                },
                observed_at="2026-07-16T00:00:00+00:00", supports_project=True,
            )
            registry.apply_service_evidence(current, new_bundle)
            project_record.save_project(conn, registry.finalize_project_shape(current), enriched=True)
            conn.commit()
            stored_run = storage_core.load_investigation_run(conn, run_id)
            stored_current = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        assert stored_run is not None
        assert stored_current is not None
        self.assertEqual(stored_run["evaluation"]["service_status"], "live")
        self.assertEqual(stored_run["bundle"].records[0].value["service_status"], "live")
        self.assertEqual(stored_current["service_status"], "unverified")
        self.assertEqual(stored_current["service_status_confidence"], "low")

    def test_repair_db_reports_and_applies_current_service_projection(self) -> None:
        service_url = "https://example.com"
        project = self.make_project("proj:repair-current-service", "service_url", service_url)
        project["manual_seeds"] = [{"type": "service_url", "value": service_url, "source": "manual"}]
        project["website_url"] = service_url
        project["service_urls"] = [service_url]
        bundle = service_core.collect_service_probe_evidence(
            project["project_key"], service_url,
            {
                "status_code": 200, "final_url": service_url, "title": "Example", "description": "Information",
                "service_status": "unverified", "service_status_confidence": "low",
                "service_status_evidence": ["http_200"], "links": [], "urls": [], "html": "Information",
            },
            observed_at="2026-07-16T00:00:00+00:00", supports_project=True,
        )
        project["service_verification_results"] = [dict(bundle.records[0].value)]
        project["service_status"] = "live"
        project["service_status_confidence"] = "low"
        project["service_status_evidence"] = ["http_200"]
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project, enriched=True)
            conn.commit()
            dry_run = registry.audit_and_repair_identifiers(conn, execute=False)
            executed = registry.audit_and_repair_identifiers(conn, execute=True)
            repaired = project_record.fetch_project(conn, project["project_key"])
            after_repair = registry.audit_and_repair_identifiers(conn, execute=False)
        finally:
            conn.close()

        assert repaired is not None
        self.assertEqual(dry_run["current_service_projection_change_count"], 1)
        self.assertEqual(dry_run["current_service_projection_examples"][0]["project_key"], project["project_key"])
        self.assertEqual(executed["current_service_projection_change_count"], 1)
        self.assertEqual(repaired["service_status"], "unverified")
        self.assertEqual(repaired["service_status_confidence"], "low")
        self.assertEqual(repaired["service_status_evidence"], ["http_200"])
        self.assertEqual(after_repair["current_service_projection_change_count"], 0)

    def test_repair_db_detects_relation_only_normalized_child_change(self) -> None:
        project = self.make_project("proj:relation-only-repair", "repo_url", "https://github.com/example/app")
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
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            dry_run = registry.audit_and_repair_identifiers(conn, execute=False)
            registry.audit_and_repair_identifiers(conn, execute=True)
            row = conn.execute(
                "SELECT predicate, classification FROM project_relations WHERE project_key = ?",
                (project["project_key"],),
            ).fetchone()
            after = registry.audit_and_repair_identifiers(conn, execute=False)
        finally:
            conn.close()

        self.assertEqual(dry_run["normalized_child_change_count"], 1)
        self.assertEqual((row["predicate"], row["classification"]), ("mentions_collection", "candidate"))
        self.assertEqual(after["normalized_child_change_count"], 0)

    def test_follow_up_service_evidence_does_not_emit_operates_service_relation(self) -> None:
        bundle = service_core.collect_service_probe_evidence(
            "proj:service-followup",
            "https://example.com/docs",
            {"status_code": 200, "title": "Docs", "description": "Docs", "links": [], "urls": []},
            observed_at="2026-06-13T00:00:00+00:00",
            lane="follow_up",
        )

        self.assertFalse(
            any(
                record.kind == "relation"
                and isinstance(record.value, dict)
                and record.value.get("predicate") == "operates_service"
                for record in bundle.records
            )
        )

    def test_service_probe_evidence_requires_project_support_for_operates_service_relation(self) -> None:
        unsupported = service_core.collect_service_probe_evidence(
            "proj:service-static",
            "https://example.net",
            {"status_code": 200, "title": "Other", "description": "Other", "links": [], "urls": []},
            observed_at="2026-06-13T00:00:00+00:00",
        )
        supported = service_core.collect_service_probe_evidence(
            "proj:service-static",
            "https://example.com",
            {"status_code": 200, "title": "Example", "description": "Example", "links": [], "urls": []},
            observed_at="2026-06-13T00:00:00+00:00",
            supports_project=True,
        )

        self.assertFalse(any(record.kind == "relation" for record in unsupported.records))
        self.assertTrue(any(
            record.kind == "relation"
            and isinstance(record.value, dict)
            and record.value.get("predicate") == "operates_service"
            for record in supported.records
        ))

    def test_service_integration_ignores_guarded_operates_service_relation(self) -> None:
        project = self.make_project("proj:guarded-service", "service_url", "https://example.com")
        registry.defer_identifier_on_project(project, "service_url", "https://other.example.com")
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-13T00:00:00+00:00")
        bundle.add_record(relation_core.relation_record(
            source="service_static:https://other.example.com",
            classification="confirmed",
            observed_at=bundle.collected_at,
            subject_type="project",
            subject_value=project["project_key"],
            predicate="operates_service",
            object_type="service",
            object_value="https://other.example.com",
            locator="https://other.example.com",
            method="static_probe",
        ))

        registry.apply_service_evidence(project, bundle)

        self.assertEqual(project.get("relation_evidence", []), [])

    def test_oauth_repo_scope_is_usage_evidence_not_ownership(self) -> None:
        records = relation_core.oauth_metadata_relation_records(
            "https://example.com/client-metadata.json",
            {"client_uri": "https://example.com", "scope": "atproto repo:org.other.post include:org.other.permissions"},
            "2026-06-13T00:00:00+00:00",
        )
        project = self.make_project("proj:oauth", "service_url", "https://example.com")
        project["website_url"] = "https://example.com"
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-13T00:00:00+00:00", records=records)
        registry.apply_investigation_evidence(project, bundle)
        self.assertFalse(registry.project_owned_lexicon_nsid(project, "org.other.post"))
        self.assertEqual(project_record.compute_atproto_contribution(project)["atproto_contribution_level"], "optional")

    def test_github_semantics_distinguish_schema_and_fixture(self) -> None:
        schema = {"lexicon": 1, "defs": {"main": {"type": "record"}}}
        self.assertEqual(relation_core.classify_lexicon_path("lexicons/app.example.post.json", schema), ("defines_schema", "candidate"))
        self.assertEqual(relation_core.classify_lexicon_path("tests/fixtures/app.example.post.json", schema), ("mentions_collection", "candidate"))

    def test_repo_schema_relation_without_accepted_nsid_does_not_prove_nsid_ownership(self) -> None:
        project = self.make_project("proj:repodefines", "repo_url", "https://github.com/example/app")
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
            }
        ]

        self.assertFalse(registry.project_owned_lexicon_nsid(project, "org.other.post"))

    def test_manual_nsid_seed_supports_same_collection_authority_only(self) -> None:
        project = self.make_project("proj:manualnsidauthority", "nsid", "dev.idiolect.recommendation")
        project["manual_seeds"] = [{"type": "nsid", "value": "dev.idiolect.recommendation"}]

        self.assertTrue(registry.project_owned_lexicon_nsid(project, "dev.idiolect.adapter"))
        self.assertFalse(registry.project_owned_lexicon_nsid(project, "dev.idiolect.recommendation.comment"))
        self.assertFalse(registry.project_owned_lexicon_nsid(project, "dev.idiolect"))

    def test_manual_nsid_seed_does_not_support_parent_or_child_namespace(self) -> None:
        project = self.make_project("proj:manualnsidnamespace", "nsid", "network.comind.memory")
        project["manual_seeds"] = [{"type": "nsid", "value": "network.comind.memory"}]

        self.assertFalse(registry.project_owned_lexicon_nsid(project, "network.comind.void.memory"))

    def test_github_integration_downgrades_unowned_repo_lexicon_relation(self) -> None:
        project = self.make_project("proj:reporelation", "repo_url", "https://github.com/example/app")
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["manual_seeds"] = [{"type": "repo_url", "value": "https://github.com/example/app"}]
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-13T00:00:00+00:00")
        bundle.add_record(relation_core.relation_record(
            source="github_repo:https://github.com/example/app",
            classification="candidate",
            observed_at=bundle.collected_at,
            subject_type="repo",
            subject_value="https://github.com/example/app",
            predicate="defines_schema",
            object_type="nsid",
            object_value="org.other.post",
            locator="lexicons/org/other/post.json",
            method="lexicon_json",
        ))

        registry.apply_github_repo_evidence(project, bundle)

        relation = project["relation_evidence"][0]["relation"]
        self.assertEqual(relation["predicate"], "mentions_collection")
        self.assertEqual(project["relation_evidence"][0]["classification"], "candidate")

    def test_github_integration_accepts_repo_schema_relation_for_owned_nsid(self) -> None:
        project = self.make_project("proj:acceptedrepodefine", "repo_url", "https://github.com/example/app")
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["manual_seeds"] = [
            {"type": "repo_url", "value": "https://github.com/example/app"},
            {"type": "nsid", "value": "app.example.post"},
        ]
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-13T00:00:00+00:00")
        bundle.add_record(relation_core.relation_record(
            source="github_repo:https://github.com/example/app",
            classification="candidate",
            observed_at=bundle.collected_at,
            subject_type="repo",
            subject_value="https://github.com/example/app",
            predicate="defines_schema",
            object_type="nsid",
            object_value="app.example.post",
            locator="lexicons/app/example/post.json",
            method="lexicon_json",
        ))

        registry.apply_github_repo_evidence(project, bundle)

        relation = project["relation_evidence"][0]["relation"]
        self.assertEqual(relation["predicate"], "defines_schema")
        self.assertEqual(project["relation_evidence"][0]["classification"], "confirmed")

    def test_relation_dedupe_keeps_strongest_latest_record(self) -> None:
        base = {
            "relation": {
                "subject": {"type": "repo", "value": "https://github.com/example/app"},
                "predicate": "defines_schema",
                "object": {"type": "nsid", "value": "app.example.post"},
                "locator": "lexicons/app.example.post.json",
                "method": "lexicon_json",
            },
            "source": "github_repo:https://github.com/example/app",
        }
        normalized = relation_core.normalize_relation_records([
            {**base, "classification": "candidate", "observed_at": "2026-06-01T00:00:00+00:00", "context": {"path": "old"}},
            {**base, "classification": "confirmed", "observed_at": "2026-06-02T00:00:00+00:00", "context": {"owner": "confirmed"}},
        ])
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["classification"], "confirmed")
        self.assertEqual(normalized[0]["observed_at"], "2026-06-02T00:00:00+00:00")
        self.assertEqual(normalized[0]["context"], {"path": "old", "owner": "confirmed"})

    def test_collector_failure_rejects_unknown_category(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid collector failure category"):
            CollectorFailure(
                source="fixture", operation="fetch", target="fixture", category="mystery",
                message="unknown failure", retryable=False,
            )

    def test_browser_collector_does_not_treat_followed_link_as_confirmed_login_surface(self) -> None:
        project = self.make_project("proj:browser", "service_url", "https://example.com")
        original = copy.deepcopy(project)
        pages = {
            "https://example.com": {"url": "https://example.com", "title": "App", "text": "Welcome", "links": [{"href": "https://example.com/login", "text": "Log in"}], "js_rendered": True},
            "https://example.com/login": {"url": "https://example.com/login", "title": "Sign in", "text": "Sign in", "links": [], "js_rendered": True},
        }
        bundle = service_core.collect_browser_service_evidence(
            project, "https://example.com",
            service_core.BrowserDependencies(navigate=lambda url: pages[url], now=lambda: "2026-06-13T00:00:00+00:00"),
        )
        self.assertEqual(project, original)
        registry.apply_service_evidence(project, bundle)
        value = bundle.records[0].value
        self.assertTrue(value["auth_link_followed"])
        self.assertFalse(value["login_surface_reached"])
        self.assertNotIn("browser_login_surface_reached", project["service_status_evidence"])
        self.assertIn("browser_rendered_surface", project["service_status_evidence"])

    def test_browser_collector_confirms_password_form_and_preserves_summary(self) -> None:
        project = self.make_project("proj:browserform", "service_url", "https://example.com")
        pages = {
            "https://example.com": {
                "url": "https://example.com", "title": "App", "text": "Welcome",
                "links": [{"href": "https://example.com/login", "text": "Log in"}], "js_rendered": True,
            },
            "https://example.com/login": {
                "url": "https://example.com/login", "title": "Sign in", "text": "Sign in",
                "links": [], "js_rendered": True,
                "forms": [{
                    "action": "https://example.com/session", "method": "post",
                    "inputs": [
                        {"type": "email", "name": "email", "autocomplete": "email"},
                        {"type": "password", "name": "password", "autocomplete": "current-password"},
                    ],
                    "submit_texts": ["Sign in"],
                }],
            },
        }
        bundle = service_core.collect_browser_service_evidence(
            project, "https://example.com",
            service_core.BrowserDependencies(navigate=lambda url: pages[url], now=lambda: "2026-07-16T00:00:00+00:00"),
        )
        value = bundle.records[0].value
        self.assertTrue(value["password_form_detected"])
        self.assertTrue(value["identity_form_detected"])
        self.assertTrue(value["login_surface_reached"])
        self.assertEqual(value["pages"][1]["forms"][0]["inputs"][1]["type"], "password")
        registry.apply_service_evidence(project, bundle)
        self.assertIn("browser_password_form_detected", project["service_status_evidence"])
        self.assertIn("browser_identity_form_detected", project["service_status_evidence"])

    def test_browser_collector_separates_oauth_entry_and_rendered_errors(self) -> None:
        project = self.make_project("proj:browseroauth", "service_url", "https://example.com")
        page = {
            "url": "https://example.com", "title": "App login", "text": "Continue",
            "links": [], "js_rendered": True,
            "auth_actions": [{
                "kind": "a", "href": "https://auth.example.com/oauth/authorize", "text": "Continue with Bluesky",
            }],
            "network_errors": ["GET https://api.example.com/session"],
            "console_errors": ["backend unavailable"],
        }
        bundle = service_core.collect_browser_service_evidence(
            project, "https://example.com",
            service_core.BrowserDependencies(navigate=lambda _url: page, now=lambda: "2026-07-16T00:00:00+00:00"),
        )
        value = bundle.records[0].value
        self.assertTrue(value["oauth_entry_detected"])
        self.assertFalse(value["password_form_detected"])
        self.assertTrue(value["rendered_error_detected"])
        registry.apply_service_evidence(project, bundle)
        self.assertIn("browser_oauth_entry_detected", project["service_status_evidence"])
        self.assertIn("browser_rendered_error_detected", project["service_status_evidence"])

    def test_browser_public_app_surface_requires_action_and_content_items(self) -> None:
        project = self.make_project("proj:browserlandingcta", "service_url", "https://example.com")
        page = {
            "url": "https://example.com", "title": "Coming soon", "text": "Create post",
            "links": [
                {"href": "https://example.com/about", "text": "About"},
                {"href": "https://example.com/docs/start", "text": "Docs"},
            ],
            "service_actions": [{"kind": "button", "href": "", "text": "Create post"}],
            "forms": [], "auth_actions": [], "network_errors": [], "console_errors": [],
            "js_rendered": True,
        }
        bundle = service_core.collect_browser_service_evidence(
            project, "https://example.com",
            service_core.BrowserDependencies(navigate=lambda _url: page, now=lambda: "2026-07-16T00:00:00+00:00"),
        )
        value = bundle.records[0].value
        self.assertTrue(value["service_action_detected"])
        self.assertEqual(value["public_content_item_count"], 0)
        self.assertFalse(value["public_app_surface_detected"])

    def test_browser_service_calibration_fixture_is_network_free_and_reproducible(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "browser-service-calibration-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["provenance"]["page_summaries_manually_minimized"])
        self.assertTrue(payload["provenance"]["live_urls_are_smoke_references_only"])
        self.assertEqual(len(payload["cases"]), 5)
        self.assertEqual(len({item["case_id"] for item in payload["cases"]}), 5)

        for case in payload["cases"]:
            pages = list(case["navigation_results"])
            calls: list[str] = []

            def navigate(url: str) -> dict:
                calls.append(url)
                if len(calls) > len(pages):
                    self.fail(f"unexpected navigation for {case['case_id']}: {url}")
                return copy.deepcopy(pages[len(calls) - 1])

            project = self.make_project(f"proj:{case['case_id']}", "service_url", case["service_url"])
            bundle = service_core.collect_browser_service_evidence(
                project,
                case["service_url"],
                service_core.BrowserDependencies(navigate=navigate, now=lambda: "2026-07-16T00:00:00+00:00"),
            )
            self.assertFalse(bundle.failures)
            value = bundle.records[0].value
            for key, expected in case["expected"].items():
                self.assertEqual(value[key], expected, f"{case['case_id']}:{key}")
            self.assertEqual(len(calls), len(pages))
            if value["public_app_surface_detected"]:
                registry.apply_service_evidence(project, bundle)
                self.assertIn("browser_public_app_surface_detected", project["service_status_evidence"])
                project["category"] = "project"
                project["website_url"] = case["service_url"]
                signal_kinds = {item["kind"] for item in registry.compute_showcase_signals(project)}
                self.assertIn("service_surface_confirmed", signal_kinds)

    def test_tangled_account_collector_paginates_pds_records(self) -> None:
        project = self.make_project("proj:tangled", "service_url", "https://example.com")
        project["name"] = "Example"
        project["developer_accounts"] = [{"handle": "dev.example", "source": "manual"}]
        cursors: list[str] = []
        def list_records(_pds: str, _did: str, cursor: str) -> dict:
            cursors.append(cursor)
            if not cursor:
                return {"records": [{"uri": "at://did:plc:dev/sh.tangled.repo/one", "value": {"name": "Example", "repoDid": "did:plc:repo"}}], "cursor": "next"}
            return {"records": []}
        bundle = tangled_core.collect_account_repos(
            project, "dev.example",
            tangled_core.TangledDependencies(
                now=lambda: "2026-06-13T00:00:00+00:00",
                resolve_handle=lambda handle: {"did": "did:plc:dev"},
                resolve_did_document=lambda did: {"service": [{"id": "#atproto_pds", "type": "AtprotoPersonalDataServer", "serviceEndpoint": "https://pds.example"}]},
                extract_pds_url=lambda doc: "https://pds.example",
                list_records=list_records,
            ),
        )
        self.assertEqual(cursors, ["", "next"])
        self.assertEqual(bundle.records[0].kind, "tangled_repo_snapshot")

    def test_tangled_evidence_applies_record_and_readme_text_as_candidates(self) -> None:
        project = self.make_project("proj:tangledtext", "repo_url", "https://tangled.org/dev.example/app")
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-13T00:00:00+00:00")
        bundle.add_record(EvidenceRecord(
            kind="tangled_repo_snapshot",
            source="tangled_pds:dev.example",
            classification="candidate",
            observed_at="2026-06-13T00:00:00+00:00",
            value={
                "owner_handle": "dev.example",
                "owner_did": "did:plc:dev",
                "repo_url": "https://tangled.org/dev.example/app",
                "record": {"name": "app", "description": "Protocol-aware local app"},
                "score": 1,
            },
        ))
        bundle.add_record(EvidenceRecord(
            kind="tangled_repo_inspection",
            source="tangled_repo:https://tangled.org/dev.example/app",
            classification="candidate",
            observed_at="2026-06-13T00:00:00+00:00",
            value={
                "repo_url": "https://tangled.org/dev.example/app",
                "repo": {"full_name": "dev.example/app", "name": "app", "description": "README-backed app"},
                "readme_urls": ["https://app.example.com"],
                "readme_excerpt": "README says this is the official app.",
            },
        ))

        registry.apply_tangled_evidence(project, bundle)

        self.assertIn("Protocol-aware local app", [item["value"] for item in project["summary_candidates"]])
        self.assertIn("README says this is the official app.", [item["value"] for item in project["summary_candidates"]])
        self.assertIn("https://app.example.com", project_record.service_url_candidate_values(project))

    def test_tangled_optional_adapters_remain_weak_candidates(self) -> None:
        timeline = tangled_core.normalize_optional_adapter_candidates(
            '<a href="https://tangled.org/dev.example/app">repo</a>', adapter="timeline",
        )
        bot = tangled_core.normalize_optional_adapter_candidates(
            [{"url": "https://tangled.org/dev.example/app", "post_uri": "at://did/post/1"}], adapter="bot",
        )
        self.assertEqual(timeline[0]["classification"], "candidate")
        self.assertEqual(bot[0]["classification"], "heuristic")

    def test_tangled_ufos_entries_accept_null_topics(self) -> None:
        entries = tangled_core.normalize_ufos_entries([{
            "collection": tangled_core.TANGLED_COLLECTION,
            "did": "did:plc:example",
            "rkey": "repo",
            "time_us": 1,
            "record": {
                "name": "Example",
                "description": "An atproto repository",
                "topics": None,
            },
        }])

        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["atproto_relevant"])

    def test_tangled_gap_does_not_advance_watermark(self) -> None:
        conn = registry.open_db()
        scope = {"source": "ufos", "collection": tangled_core.TANGLED_COLLECTION}
        registry.write_sync_state(conn, "tangled-repos", scope, {"time_us": 100})
        conn.commit()
        try:
            with mock.patch.object(source_sync, "fetch_tangled_ufos_window", return_value=[{
                "owner_did": "", "rkey": "one", "time_us": 200,
                "record": {"name": "Example", "description": "atproto repo"},
                "atproto_relevant": True,
            }]):
                summary = source_sync.sync_tangled_repos(conn, execute=True, create_unmatched=False)
            state = registry.read_sync_state(conn, "tangled-repos", scope)
        finally:
            conn.close()
        self.assertTrue(summary["gap_possible"])
        self.assertFalse(summary["state_updated"])
        self.assertEqual(state["watermark"]["time_us"], 100)

    def test_tangled_create_unmatched_can_update_new_project_in_same_run(self) -> None:
        conn = registry.open_db()
        try:
            with (
                mock.patch.object(source_sync, "fetch_tangled_ufos_window", return_value=[{
                    "owner_did": "did:plc:example",
                    "rkey": "example-repo",
                    "time_us": 200,
                    "record": {"name": "example-repo", "description": "atproto repo"},
                    "atproto_relevant": True,
                }]),
                mock.patch.object(registry, "fetch_bsky_profile", return_value={"handle": "dev.example"}),
            ):
                summary = source_sync.sync_tangled_repos(conn, execute=True, create_unmatched=True)
            projects = registry.all_projects(conn)
        finally:
            conn.close()

        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["results"][0]["outcome"], "created")
        self.assertEqual(summary["results"][0]["project_key"], projects[0]["project_key"])
        self.assertEqual(len(projects), 1)
        self.assertTrue(registry.runtime_view.is_complete(projects[0]))

    def test_confirmed_root_www_redirect_removes_only_alias_candidate(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" / "project_registry" / "investigation-benchmark-v2.json").read_text())[0]
        bundle = EvidenceBundle.from_dict(fixture["bundle"])
        project = registry.replay_core.replay_bundle(fixture["input_project"], bundle, registry.replay_dependencies())
        self.assertEqual(project["service_url_candidates"], ["https://graph.at.fund"])
        self.assertEqual(project["url_aliases"][0]["canonical"], "https://www.at.fund")

    def test_repair_db_folds_only_confirmed_root_www_alias(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:url-repair", "service_url", "https://www.at.fund")
        project["website_url"] = "https://www.at.fund"
        project["service_urls"] = ["https://www.at.fund"]
        project["manual_seeds"] = [{"type": "service_url", "value": "https://www.at.fund"}]
        project["service_url_candidates"] = ["https://at.fund", "https://graph.at.fund"]
        project["relation_evidence"] = [{
            "relation": {
                "subject": {"type": "service", "value": "https://at.fund"},
                "predicate": "redirects_to",
                "object": {"type": "service", "value": "https://www.at.fund"},
                "locator": "https://at.fund", "method": "http_redirect",
            },
            "source": "service_static:https://at.fund", "classification": "confirmed",
            "observed_at": "2026-06-13T00:00:00+00:00", "context": {},
        }]
        try:
            project_record.save_project(conn, project)
            registry.audit_and_repair_identifiers(conn, execute=True)
            repaired = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()
        assert repaired is not None
        self.assertEqual(repaired["service_url_candidates"], ["https://graph.at.fund"])

    def test_benchmark_corpus_has_six_passing_cases(self) -> None:
        path = Path(__file__).parent / "fixtures" / "project_registry" / "investigation-benchmark-v2.json"
        summary = registry.replay_core.benchmark_cases(registry.load_benchmark_cases(path), registry.replay_dependencies())
        self.assertEqual(summary["case_count"], 6)
        self.assertEqual(summary["regression_count"], 0)

    def test_cli_and_core_api_remain_available(self) -> None:
        from project_registry_core import cli

        parser = cli.build_parser()
        args = parser.parse_args(["enrich", "--seed", "app.example.post"])

        self.assertEqual(args.command, "enrich")
        self.assertEqual(parser.parse_args(["sync-tangled-repos"]).command, "sync-tangled-repos")
        self.assertEqual(parser.parse_args(["verify-service", "-p", "proj:test", "--lane", "browser"]).lane, "browser")
        self.assertEqual(parser.parse_args(["replay-investigation", "--run-id", "inv:test"]).run_id, "inv:test")
        self.assertTrue(callable(enrichment_pipeline.enrich_project))
        for name in (
            "collect_investigation_evidence",
            "apply_investigation_evidence",
            "collect_github_repo_evidence",
            "apply_github_repo_evidence",
            "collect_tangled_repo_evidence",
            "apply_tangled_evidence",
            "plan_investigation_queries",
            "run_investigation_collectors",
            "enrich_from_github_repo",
            "project_owned_lexicon_nsid",
            "classify_service_probe_result",
        ):
            self.assertTrue(callable(getattr(registry, name)))
        self.assertTrue(callable(project_record.compute_atproto_contribution))
        self.assertFalse((registry.ROOT / "scripts" / "project_registry.py").exists())

    def test_public_cli_wrapper_is_the_only_shell_entrypoint(self) -> None:
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
        result = subprocess.run(
            [str(registry.ROOT / "bin" / "project-registry"), "--help"],
            cwd=registry.ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertIn("usage: project-registry", result.stdout)
        self.assertFalse((registry.ROOT / "scripts" / "project-registry").exists())


if __name__ == "__main__":
    unittest.main()
