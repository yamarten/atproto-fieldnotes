from __future__ import annotations

import copy
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core.evidence import CollectorResult, EvidenceBundle, EvidenceRecord
from project_registry_core import evaluation as evaluation_core
from project_registry_core import investigation as investigation_core
from project_registry_core import relations as relation_core
from project_registry_core import service as service_core
from project_registry_core import storage as storage_core
from project_registry_core import tangled as tangled_core


class ProjectRegistryResearchPipelineTest(TempRegistryDbTestCase):
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
            project = self.make_project("proj:researchenrich")
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
        self.assertEqual(fetched["project_key"], "proj:researchenrich")
        self.assertIn("LLM unavailable", "\n".join(fetched["last_run_errors"]))
        self.assertTrue(fetched["last_enriched_at"])
        conn = registry.open_db()
        try:
            run = storage_core.latest_investigation_run(conn, "proj:researchenrich")
        finally:
            conn.close()
        self.assertIsNotNone(run)

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
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-13T00:00:00+00:00")
        bundle.add_record(relation_core.relation_record(
            source="fixture", classification="derived", observed_at=bundle.collected_at,
            subject_type="repo", subject_value="https://github.com/example/app",
            predicate="uses_collection", object_type="nsid", object_value="app.example.post",
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
        replayed = registry.replay_core.replay_and_diff(
            run["input_project"], run["bundle"], run["evaluation"], registry.replay_dependencies()
        )
        self.assertEqual(replayed["evaluation"]["atproto_contribution_level"], "substantial")

    def test_collector_run_keeps_github_repo_family_records(self) -> None:
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
        try:
            run_id = storage_core.save_investigation_run(
                conn, project_key=project["project_key"], started_at=bundle.collected_at,
                completed_at=bundle.collected_at, input_project=project, output_project=project, bundle=bundle,
            )
            row = conn.execute("SELECT records_json FROM collector_runs WHERE run_id = ?", (run_id,)).fetchone()
        finally:
            conn.close()
        assert row is not None
        self.assertEqual(json.loads(row["records_json"])[0]["source"].split(":", 1)[0], "github_readme")

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
        self.assertEqual(registry.compute_atproto_contribution(project)["atproto_contribution_level"], "optional")

    def test_github_semantics_distinguish_schema_and_fixture(self) -> None:
        schema = {"lexicon": 1, "defs": {"main": {"type": "record"}}}
        self.assertEqual(relation_core.classify_lexicon_path("lexicons/app.example.post.json", schema), ("defines_schema", "confirmed"))
        self.assertEqual(relation_core.classify_lexicon_path("tests/fixtures/app.example.post.json", schema), ("mentions_collection", "candidate"))

    def test_browser_collector_follows_only_anonymous_login_surface(self) -> None:
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
        self.assertEqual(project["service_status"], "live")
        self.assertIn("browser_login_surface_reached", project["service_status_evidence"])

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

    def test_tangled_optional_adapters_remain_weak_candidates(self) -> None:
        timeline = tangled_core.normalize_optional_adapter_candidates(
            '<a href="https://tangled.org/dev.example/app">repo</a>', adapter="timeline",
        )
        bot = tangled_core.normalize_optional_adapter_candidates(
            [{"url": "https://tangled.org/dev.example/app", "post_uri": "at://did/post/1"}], adapter="bot",
        )
        self.assertEqual(timeline[0]["classification"], "candidate")
        self.assertEqual(bot[0]["classification"], "heuristic")

    def test_tangled_gap_does_not_advance_watermark(self) -> None:
        conn = registry.open_db()
        scope = {"source": "ufos", "collection": tangled_core.TANGLED_COLLECTION}
        registry.write_sync_state(conn, "tangled-repos", scope, {"time_us": 100})
        conn.commit()
        try:
            with mock.patch.object(registry, "fetch_tangled_ufos_window", return_value=[{
                "owner_did": "", "rkey": "one", "time_us": 200,
                "record": {"name": "Example", "description": "atproto repo"},
                "atproto_relevant": True,
            }]):
                summary = registry.sync_tangled_repos(conn, execute=True, create_unmatched=False)
            state = registry.read_sync_state(conn, "tangled-repos", scope)
        finally:
            conn.close()
        self.assertTrue(summary["gap_possible"])
        self.assertFalse(summary["state_updated"])
        self.assertEqual(state["watermark"]["time_us"], 100)

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
            registry.save_project(conn, project)
            registry.audit_and_repair_identifiers(conn, execute=True)
            repaired = registry.fetch_project(conn, project["project_key"])
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
        self.assertFalse((registry.ROOT / "scripts" / "project_registry.py").exists())

    def test_public_cli_wrapper_is_the_only_shell_entrypoint(self) -> None:
        result = subprocess.run(
            [str(registry.ROOT / "bin" / "project-registry"), "--help"],
            cwd=registry.ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("usage: project-registry", result.stdout)
        self.assertFalse((registry.ROOT / "scripts" / "project-registry").exists())


if __name__ == "__main__":
    unittest.main()
