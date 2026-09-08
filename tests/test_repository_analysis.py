from __future__ import annotations

import json
import unittest
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import identifiers
from project_registry_core import investigation
from project_registry_core import project_record
from project_registry_core import repository_analysis
from project_registry_core import repository_material
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord


class RepositoryAnalysisTest(unittest.TestCase):
    def evidence_id(
        self, material: dict, path: str, *, line: int = 0, json_pointer: str = ""
    ) -> str:
        return next(
            evidence_id
            for evidence_id, locator in repository_analysis.evidence_locator_catalog(material).items()
            if locator.get("path") == path
            and (not line or locator.get("line") == line)
            and (not json_pointer or locator.get("json_pointer") == json_pointer)
        )

    def observation_candidate_id(self, material: dict, predicate: str, *, value: str = "") -> str:
        return next(
            candidate_id
            for candidate_id, candidate in repository_analysis.observation_candidate_catalog(material).items()
            if candidate.get("predicate") == predicate
            and (not value or candidate.get("value") == value)
        )

    def github_bundle(self, *, manifest_hints: list[dict] | None = None) -> EvidenceBundle:
        bundle = EvidenceBundle(project_key="proj:repo-analysis", collected_at="2026-07-25T00:00:00+00:00")
        bundle.add_record(EvidenceRecord(
            kind="github_repo_snapshot",
            source="github_repo:https://github.com/example/app",
            classification="confirmed",
            observed_at=bundle.collected_at,
            value={
                "repo_url": "https://github.com/example/app",
                "repo": {"name": "app", "description": "AT Protocol app"},
                "default_branch": "main",
                "revision": "abc123",
                "readme_excerpt": "An app using com.atproto.repo.getRecord.",
                "tree_paths": ["package.json", "docs/index.md", "docs/setup.md", "src/client.ts"],
                "lexicon_hints": [{
                    "nsid": "dev.example.Record",
                    "path": "lexicons/dev.example.Record.json",
                    "schema": {"description": "Record", "defs": {"main": {"type": "record"}}},
                }],
                "manifest_hints": manifest_hints if manifest_hints is not None else [{
                    "path": "package.json",
                    "name": "example-app",
                    "private": False,
                    "bin": ["example-app"],
                    "exports": True,
                }],
                "configuration_hints": [{
                    "path": "mkdocs.yml",
                    "kind": "documentation_config",
                    "content_excerpt": "site_url: https://docs.example.com",
                }],
                "source_selection_performed": True,
                "source_evidence": [{
                    "nsid": "com.atproto.repo.getRecord",
                    "path": "src/client.ts",
                    "line": 12,
                    "operation": "read",
                    "excerpt": "client.call('com.atproto.repo.getRecord')",
                }],
            },
        ))
        return bundle

    def test_material_is_bounded_and_keeps_revision_coverage_and_locators(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        self.assertEqual(material["source_revision"], "abc123")
        self.assertEqual(material["provider"], "github")
        self.assertEqual(material["coverage"]["source"], "limited")
        self.assertEqual(material["source_evidence"][0]["line"], 12)
        self.assertEqual(material["lexicon_hints"][0]["def_types"], ["record"])
        self.assertLessEqual(len(material["source_evidence"]), 8)
        prompt = repository_analysis.build_prompt({}, material)
        self.assertIn("untrusted", prompt)
        self.assertIn('"evidence_blocks"', prompt)
        self.assertNotIn('"tree_paths"', prompt)
        self.assertNotIn(
            {"path": "docs/index.md"},
            repository_analysis.evidence_locator_catalog(material).values(),
        )

    def test_readme_sections_keep_head_and_tail_with_bounded_line_locators(self) -> None:
        text = "\n\n".join(
            f"## Section {index}\n" + (f"content-{index} " * 120)
            for index in range(8)
        )

        sections, metrics = investigation.bounded_readme_sections(text)

        self.assertEqual(len(sections), 6)
        self.assertIn("Section 0", sections[0]["excerpt"])
        self.assertIn("content-7", sections[-1]["excerpt"])
        self.assertTrue(all(item["line"] > 0 for item in sections))
        self.assertLessEqual(metrics["selected_char_count"], 6000)
        self.assertTrue(metrics["truncated"])

    def test_readme_section_lines_survive_multiline_html_comments(self) -> None:
        text = "\n<!-- hidden\ncomment -->\n# Visible\ncontent"

        sections, _metrics = investigation.bounded_readme_sections(text)

        self.assertEqual(sections[0]["line"], 4)
        self.assertTrue(sections[0]["excerpt"].startswith("# Visible"))

    def test_prompt_uses_closed_candidates_and_excludes_uncitable_tree_context(self) -> None:
        bundle = self.github_bundle(manifest_hints=[])
        snapshot = bundle.records[0].value
        snapshot["readme_excerpt"] = (
            "A command-line tool that reads collection app.example.Record records. "
            "The illustrative collection is example.nsid.record."
        )
        snapshot["tree_paths"] = ["lexicon-templates/example.json", "main.go"]
        material = repository_analysis.build_material("https://github.com/example/app", bundle)

        prompt = repository_analysis.build_prompt({}, material)
        observations = repository_analysis.observation_candidate_catalog(material)
        collections = repository_analysis.collection_candidate_catalog(material)

        self.assertNotIn("lexicon-templates/example.json", prompt)
        self.assertNotIn("main.go", prompt)
        self.assertIn("candidate_id", prompt)
        self.assertIn("declares_cli_entrypoint", {
            item["predicate"] for item in observations.values()
        })
        self.assertIn("app.example.Record", {
            item["nsid"] for item in collections.values()
        })
        self.assertTrue(all(item.get("operation") in {"mention", "read", "write", "call"} for item in collections.values()))
        self.assertTrue(all("allowed_operations" not in item for item in collections.values()))
        self.assertIn('"collections":[{"candidate_id":"C01"}]', prompt)
        self.assertNotIn("example.nsid.record", {
            item["nsid"] for item in collections.values()
        })
        self.assertLessEqual(
            sum(len(item["text"].encode("utf-8")) for item in repository_analysis.evidence_block_catalog(material)),
            repository_analysis.MAX_EVIDENCE_TEXT_BYTES,
        )

    def test_prompt_requires_empty_arrays_when_candidate_catalogs_are_empty(self) -> None:
        bundle = self.github_bundle(manifest_hints=[])
        bundle.records[0].value["readme_excerpt"] = "A small AT Protocol project."
        bundle.records[0].value["source_evidence"] = []
        bundle.records[0].value["lexicon_hints"] = []
        material = repository_analysis.build_material(
            "https://github.com/example/app",
            bundle,
        )

        prompt = repository_analysis.build_prompt({}, material)

        self.assertEqual(repository_analysis.collection_candidate_catalog(material), {})
        self.assertIn("collection_candidates is empty, so collections MUST be [] exactly", prompt)
        self.assertIn('"collections":[]', prompt)

    def test_repository_name_is_context_but_not_claimable_evidence(self) -> None:
        bundle = self.github_bundle(manifest_hints=[])
        snapshot = bundle.records[0].value
        snapshot["repo"] = {"name": "official-looking-name"}
        snapshot["readme_excerpt"] = ""
        snapshot["source_evidence"] = []
        snapshot["lexicon_hints"] = []
        material = repository_analysis.build_material("https://github.com/example/app", bundle)

        prompt = repository_analysis.build_prompt({"name": "official-looking-name"}, material)

        self.assertIn('"name": "official-looking-name"', prompt)
        self.assertNotIn(
            {"path": "repository metadata", "json_pointer": "/name"},
            repository_analysis.evidence_locator_catalog(material).values(),
        )
        self.assertNotIn(
            {"path": "repository metadata", "json_pointer": "/is_template"},
            repository_analysis.evidence_locator_catalog(material).values(),
        )

    def test_readme_collection_candidates_require_reverse_domain_authority(self) -> None:
        bundle = self.github_bundle(manifest_hints=[])
        snapshot = bundle.records[0].value
        snapshot["readme_excerpt"] = (
            "Collection dev.example.record is read with getRecord.\n"
            "Collection Records.Client.Record is read with getRecord.\n"
            "Collection records-api.example.dev is read with getRecord."
        )
        snapshot["source_evidence"] = []
        snapshot["lexicon_hints"] = []
        material = repository_analysis.build_material("https://github.com/example/app", bundle)

        nsids = {
            item["nsid"]
            for item in repository_analysis.collection_candidate_catalog(material).values()
        }

        self.assertIn("dev.example.record", nsids)
        self.assertNotIn("Records.Client.Record", nsids)
        self.assertNotIn("records-api.example.dev", nsids)

    def test_deterministic_observations_are_non_exclusive(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        predicates = {item["predicate"] for item in material["observations"]}
        self.assertTrue({
            "declares_cli_entrypoint",
            "declares_importable_package",
            "defines_lexicons",
            "contains_documentation_source",
            "uses_documentation_generator",
        }.issubset(predicates))

    def test_configuration_paths_do_not_treat_documentation_pages_as_deployment(self) -> None:
        tree = [
            {"type": "blob", "path": "website/docusaurus.config.js", "size": 1000},
            {"type": "blob", "path": "website/docs/intro.md", "size": 1000},
            {"type": "blob", "path": "website/docs/getting_started/_category_.json", "size": 1000},
            {"type": "blob", "path": "Dockerfile", "size": 1000},
        ]

        paths = repository_material.github_repo_configuration_paths(tree)

        self.assertIn("website/docusaurus.config.js", paths)
        self.assertIn("Dockerfile", paths)
        self.assertNotIn("website/docs/intro.md", paths)
        self.assertNotIn("website/docs/getting_started/_category_.json", paths)

    def test_normalized_result_accepts_only_supplied_locators(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        result = repository_analysis.normalize_result(material, {
            "summary": "AT Protocolレコードを読むCLI。",
            "summary_evidence": [self.evidence_id(material, "README")],
            "observations": [{"candidate_id": "O99"}],
            "capabilities": [{
                "text": "レコードを読む",
                "evidence": [self.evidence_id(material, "src/client.ts", line=12)],
            }],
            "collections": [{"candidate_id": "C99"}],
        }, {"model": "test", "transport": "test"}, "2026-07-25T01:00:00+00:00")
        predicates = {item["predicate"] for item in result["observations"]}
        self.assertNotIn("contains_service_entrypoint", predicates)
        self.assertNotIn("declares_browser_extension", predicates)
        self.assertNotIn("excerpt", result["capabilities"][0]["evidence"][0])
        self.assertIn(
            "client.call('com.atproto.repo.getRecord')",
            {item["excerpt"] for item in result["evidence_excerpts"]},
        )
        collection_nsids = {item["nsid"] for item in result["collections"]}
        self.assertIn("com.atproto.repo.getRecord", collection_nsids)
        self.assertNotIn("com.example.hallucinated", collection_nsids)
        self.assertNotIn("artifact_kind", result)

    def test_readme_documentation_candidates_preserve_exact_url_and_require_selection(self) -> None:
        bundle = self.github_bundle()
        snapshot = bundle.records[0].value
        snapshot["configuration_hints"] = []
        snapshot["readme_sections"] = [{"path": "README", "line": 20, "excerpt":
            "Developer and admin documentation for [Barazo](https://github.com/singi-labs), "
            "hosted at [docs.barazo.forum](https://docs.barazo.forum/guide). "
            "Built with [Fumadocs](https://fumadocs.vercel.app/)."}]
        material = repository_analysis.build_material("https://github.com/example/app", bundle)
        candidate = self.observation_candidate_id(material, "declares_documentation_site_url", value="https://docs.barazo.forum/guide")
        payload = {"summary": "ドキュメントサイト。", "summary_evidence": [self.evidence_id(material, "README")],
                   "observations": [], "capabilities": [], "collections": []}
        unselected = repository_analysis.normalize_result(material, payload, {}, "2026-09-08T00:00:00Z")
        self.assertEqual(repository_analysis.documentation_urls(unselected), [])
        for applicability in (None, "negated", "hypothetical", "third_party", "obsolete", "uncertain"):
            payload["observations"] = [{"candidate_id": candidate, "applicability": applicability}]
            rejected = repository_analysis.normalize_result(material, payload, {}, "2026-09-08T00:00:00Z")
            self.assertEqual(repository_analysis.documentation_urls(rejected), [])
        payload["observations"] = [{"candidate_id": candidate, "applicability": "current_project"}]
        selected = repository_analysis.normalize_result(material, payload, {}, "2026-09-08T00:00:00Z")
        self.assertEqual(repository_analysis.documentation_urls(selected), ["https://docs.barazo.forum/guide"])
        self.assertIn("site generators", repository_analysis.build_prompt({}, material))

    def test_homepage_metadata_and_plain_readme_links_do_not_declare_documentation(self) -> None:
        bundle = self.github_bundle()
        snapshot = bundle.records[0].value
        snapshot["configuration_hints"] = []
        snapshot["repo"]["homepage"] = "https://docs.example.com"
        snapshot["readme_sections"] = [{"path": "README", "line": 1, "excerpt": "See https://docs.example.com"}]
        material = repository_analysis.build_material("https://github.com/example/app", bundle)
        self.assertFalse(any(c["predicate"] == "declares_documentation_site_url"
                             for c in repository_analysis.observation_candidate_catalog(material).values()))

    def test_documentation_url_requires_the_url_in_supplied_evidence(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        result = repository_analysis.normalize_result(material, {
            "summary": "ドキュメントを持つAT Protocolプロジェクト。",
            "summary_evidence": [self.evidence_id(material, "mkdocs.yml")],
            "observations": [{"candidate_id": self.observation_candidate_id(
                material, "declares_documentation_site_url", value="https://docs.example.com"
            ), "applicability": "current_project"}, {"candidate_id": "O99"}],
            "capabilities": [],
            "collections": [],
        }, {}, "2026-07-25T01:00:00+00:00")
        self.assertEqual(repository_analysis.documentation_urls(result), ["https://docs.example.com"])
        self.assertEqual(
            [
                item for item in result["observations"]
                if item["predicate"] == "declares_documentation_site_url"
            ],
            [{
                "predicate": "declares_documentation_site_url",
                "basis": "declared",
                "evidence": [{"path": "mkdocs.yml"}],
                "value": "https://docs.example.com",
            }],
        )
        self.assertIn(
            "site_url: https://docs.example.com",
            {item["excerpt"] for item in result["evidence_excerpts"]},
        )

    def test_matching_revision_and_policy_reuses_current_result(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        project = {"repository_analysis_results": [{
            "version": repository_analysis.ANALYSIS_VERSION,
            "policy_version": repository_analysis.POLICY_VERSION,
            "repo_url": material["repo_url"],
            "source_revision": material["source_revision"],
            "material_hash": material["material_hash"],
            "summary": "cached",
            "summary_evidence": [{"path": "README"}],
        }]}
        self.assertEqual(repository_analysis.cached_result(project, material)["summary"], "cached")

    def test_failure_keeps_success_separate_and_defers_same_revision(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        project = {"repository_analysis_results": [{
            "repo_url": material["repo_url"],
            "source_revision": "old",
            "summary": "previous analysis",
        }]}
        now = "2026-07-25T00:00:00+00:00"
        repository_analysis.mark_failure(project, material, now, "timeout")
        result = project["repository_analysis_results"][0]
        self.assertEqual(result["summary"], "previous analysis")
        self.assertEqual(result["source_revision"], "old")
        self.assertEqual(result["last_attempt"]["status"], "failed")
        self.assertEqual(result["last_attempt"]["source_revision"], "abc123")
        self.assertTrue(repository_analysis.result_is_stale(result))
        self.assertTrue(repository_analysis.retry_deferred(project, material, now))

    def test_failed_new_policy_does_not_relabel_or_cache_old_success(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        project = {"repository_analysis_results": [{
            "version": 3,
            "policy_version": "repository-analysis-old",
            "repo_url": material["repo_url"],
            "provider": "github",
            "source_revision": material["source_revision"],
            "summary": "old success",
            "observed_at": "2026-07-24T00:00:00+00:00",
        }]}

        repository_analysis.mark_failure(
            project, material, "2026-07-25T00:00:00+00:00", "timeout"
        )

        result = project["repository_analysis_results"][0]
        self.assertEqual(result["version"], 3)
        self.assertEqual(result["policy_version"], "repository-analysis-old")
        self.assertEqual(result["summary"], "old success")
        self.assertEqual(
            result["last_attempt"]["policy_version"], repository_analysis.POLICY_VERSION
        )
        self.assertIsNone(repository_analysis.cached_result(project, material))
        self.assertTrue(repository_analysis.result_is_stale(result))

    def test_previous_policy_success_is_stale(self) -> None:
        self.assertTrue(repository_analysis.result_is_stale({
            "version": repository_analysis.ANALYSIS_VERSION,
            "policy_version": "repository-analysis-old",
            "summary": "previous policy result",
            "last_attempt": {"status": "success"},
        }))

    def test_configuration_failure_does_not_defer_retry(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        project = {"repository_analysis_results": []}
        now = "2026-07-25T00:00:00+00:00"

        repository_analysis.mark_failure(project, material, now, "unconfigured")

        attempt = project["repository_analysis_results"][0]["last_attempt"]
        self.assertNotIn("next_retry_at", attempt)
        self.assertFalse(repository_analysis.retry_deferred(project, material, now))

    def test_legacy_configuration_failure_with_retry_time_is_not_deferred(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        project = {"repository_analysis_results": [{
            "repo_url": material["repo_url"],
            "last_attempt": {
                "status": "failed",
                "version": repository_analysis.ANALYSIS_VERSION,
                "policy_version": repository_analysis.POLICY_VERSION,
                "source_revision": material["source_revision"],
                "material_hash": material["material_hash"],
                "error_category": "unconfigured",
                "next_retry_at": "2026-07-26T00:00:00+00:00",
            },
        }]}

        self.assertFalse(repository_analysis.retry_deferred(
            project, material, "2026-07-25T00:00:00+00:00"
        ))

    def test_tree_only_cargo_path_does_not_declare_importable_package(self) -> None:
        material = repository_analysis.build_material(
            "https://tangled.org/example/repo",
            self._bundle_with_tree(["Cargo.toml", "src/main.rs"]),
        )

        result = repository_analysis.normalize_result(material, {
            "summary": "Rustアプリケーション。",
            "summary_evidence": [self.evidence_id(material, "README")],
            "observations": [{"candidate_id": "O99"}],
            "capabilities": [],
            "collections": [],
        }, {}, "2026-07-25T00:00:00+00:00")

        self.assertNotIn("declares_importable_package", {
            item["predicate"] for item in result["observations"]
        })
        self.assertNotIn(
            {"path": "Cargo.toml"},
            repository_analysis.evidence_locator_catalog(material).values(),
        )

    def test_summary_requires_a_supported_locator(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )

        with self.assertRaisesRegex(ValueError, "summary evidence is required"):
            repository_analysis.normalize_result(material, {
                "summary": "根拠のない要約。",
                "summary_evidence": ["E99"],
                "observations": [],
                "capabilities": [],
                "collections": [],
            }, {}, "2026-07-25T00:00:00+00:00")

    def test_web_framework_does_not_establish_host_integration(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )

        result = repository_analysis.normalize_result(material, {
            "summary": "AT Protocolアプリケーション。",
            "summary_evidence": [self.evidence_id(material, "README")],
            "observations": [{"candidate_id": "O99"}],
            "capabilities": [],
            "collections": [],
        }, {}, "2026-07-25T00:00:00+00:00")

        self.assertNotIn("contains_host_integration_code", {
            item["predicate"] for item in result["observations"]
        })

    def test_triage_evidence_includes_only_confirmed_repository_analysis(self) -> None:
        project = project_record.default_project_record(
            "proj:repo-analysis", "repo_url", "https://github.com/example/app"
        )
        project["project_repository_urls"] = ["https://github.com/example/app"]
        project["repository_analysis_results"] = [{
            "repo_url": "https://github.com/example/candidate",
            "source_revision": "candidate",
            "summary": "candidate repository analysis",
            "summary_evidence": [{"path": "README"}],
        }, {
            "repo_url": "https://github.com/example/app",
            "source_revision": "abc123",
            "summary": "confirmed repository analysis",
            "summary_evidence": [{"path": "README"}],
        }]
        evidence = registry.build_llm_evidence_bundle(project)
        self.assertEqual(len(evidence["repository_analysis_results"]), 1)
        self.assertEqual(
            evidence["repository_analysis_results"][0]["summary"],
            "confirmed repository analysis",
        )

    def test_triage_view_deduplicates_repeated_evidence_excerpts(self) -> None:
        excerpt = "general purpose AT Protocol CLI " * 80
        result = {
            "version": repository_analysis.ANALYSIS_VERSION,
            "policy_version": repository_analysis.POLICY_VERSION,
            "repo_url": "https://github.com/example/goat",
            "source_revision": "abc123",
            "summary": "AT Protocol CLI.",
            "summary_evidence": [{"path": "README", "excerpt": excerpt}],
            "capabilities": [
                {"text": f"capability {index}", "evidence": [{"path": "README", "excerpt": excerpt}]}
                for index in range(8)
            ],
            "observations": [],
            "collections": [],
            "last_attempt": {"status": "success"},
        }

        view = repository_analysis.triage_view(result)

        self.assertEqual(len(view["evidence_excerpts"]), 1)
        self.assertLessEqual(len(view["evidence_excerpts"][0]["excerpt"]), 600)
        self.assertTrue(all(
            "excerpt" not in item["evidence"][0]
            for item in view["capabilities"]
        ))
        self.assertNotIn("last_attempt", view)
        self.assertLess(len(json.dumps(view, ensure_ascii=False)), 4000)

    def test_triage_evidence_excludes_legacy_summary_without_evidence(self) -> None:
        project = project_record.default_project_record(
            "proj:repo-analysis", "repo_url", "https://github.com/example/app"
        )
        project["project_repository_urls"] = ["https://github.com/example/app"]
        project["repository_analysis_results"] = [{
            "repo_url": "https://github.com/example/app",
            "source_revision": "abc123",
            "summary": "legacy repository analysis",
        }]

        self.assertEqual(repository_analysis.select_for_triage(project), [])

    def test_package_manifest_extracts_distribution_fields(self) -> None:
        tree = [{"path": "package.json", "type": "blob", "size": 100}]
        with mock.patch.object(repository_material, "github_repo_file_text", return_value='''{
          "name": "example-app",
          "bin": {"example": "cli.js"},
          "exports": {".": "index.js"},
          "scripts": {"start": "node index.js"},
          "dependencies": {"@atproto/api": "1.0.0", "unrelated": "2.0.0"}
        }'''):
            hints = repository_material.repo_manifest_hints(
                "https://github.com/example/app", ref="main", tree=tree
            )
        self.assertEqual(hints[0]["scripts"], ["start"])
        self.assertEqual(hints[0]["bin"], ["example"])
        self.assertTrue(hints[0]["exports"])
        self.assertEqual(hints[0]["protocol_dependencies"], {"@atproto/api": "1.0.0"})

    def test_pyproject_manifest_extracts_declared_cli_with_original_pointer(self) -> None:
        tree = [{"path": "pyproject.toml", "type": "blob", "size": 100}]
        text = '''
[project]
name = "example-cli"
description = "AT Protocol CLI"
dependencies = ["atproto>=0.0.1"]

[project.scripts]
example = "example.cli:main"
'''
        hints = repository_material.repo_manifest_hints(
            "https://github.com/example/cli",
            ref="main",
            tree=tree,
            file_text=lambda *_args, **_kwargs: text,
        )

        self.assertEqual(hints[0]["manifest_kind"], "python")
        self.assertEqual(hints[0]["bin"], ["example"])
        self.assertEqual(hints[0]["bin_pointer"], "/project/scripts")
        material = repository_analysis.build_material(
            "https://github.com/example/cli",
            self.github_bundle(manifest_hints=hints),
        )
        cli_observation = next(
            item for item in material["observations"]
            if item["predicate"] == "declares_cli_entrypoint"
        )
        self.assertEqual(
            cli_observation["evidence"][0]["json_pointer"], "/project/scripts"
        )

    def test_host_layout_is_only_an_inferred_observation(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/plugin",
            self._bundle_with_tree(["package.json", "src/example/webpackModules/presence.tsx"]),
        )
        observation = next(
            item for item in material["observations"]
            if item["predicate"] == "contains_host_integration_code"
        )
        self.assertEqual(observation["basis"], "inferred")
        self.assertNotIn("declares_browser_extension", {
            item["predicate"] for item in material["observations"]
        })

    def test_semantic_hints_classify_each_call_line_and_preserve_nsid_case(self) -> None:
        tree = [{"path": "src/client.ts", "type": "blob", "size": 300}]
        source = "\n".join([
            "logger.info(module.exports)",
            "client.getRecord({ collection: 'dev.example.Record' })",
            "client.putRecord({ collection: 'dev.example.OtherRecord' })",
        ])
        with mock.patch.object(repository_material, "github_repo_file_text", return_value=source):
            hints = repository_material.repo_semantic_hints(
                "https://github.com/example/app", ref="main", tree=tree
            )
        self.assertEqual(
            [(item["nsid"], item["operation"], item["line"]) for item in hints],
            [("dev.example.Record", "read", 2), ("dev.example.OtherRecord", "write", 3)],
        )

    def test_nsid_validation_preserves_case_sensitive_name(self) -> None:
        self.assertTrue(identifiers.is_valid_nsid("dev.example.Record"))
        self.assertTrue(identifiers.is_valid_nsid("dev.example.record"))
        self.assertTrue(identifiers.is_valid_nsid("com.bartenderSky.preferences.profileMusic"))
        self.assertFalse(identifiers.is_valid_nsid("logger.info"))

    def test_codeberg_snapshot_uses_provider_neutral_analysis_contract(self) -> None:
        project = project_record.default_project_record(
            "proj:codeberg-analysis", "repo_url", "https://codeberg.org/cartes/web"
        )
        dependencies = registry.investigation_core.RepositoryCollectorDependencies(
            now=lambda: "2026-07-25T00:00:00+00:00",
            fetch_repo=lambda _url: {
                "repo": {"name": "web", "full_name": "cartes/web", "description": "Cartes"},
                "readme_text": "Cartes AT Protocol application.",
                "tree": [{"path": "package.json", "type": "blob", "size": 100}],
                "default_branch": "main",
                "revision": "codeberg123",
            },
            repo_lexicon_hints=lambda *_args, **_kwargs: [],
            extract_urls=lambda _text: [],
            repo_manifest_hints=lambda *_args, **_kwargs: [{
                "path": "package.json", "name": "cartes", "private": True,
            }],
            provider="codeberg",
        )
        bundle = registry.investigation_core.collect_repository_evidence(
            project, "https://codeberg.org/cartes/web", dependencies
        )
        material = repository_analysis.build_material("https://codeberg.org/cartes/web", bundle)
        self.assertEqual(bundle.records[0].kind, "codeberg_repo_snapshot")
        self.assertEqual(material["provider"], "codeberg")
        self.assertEqual(material["source_revision"], "codeberg123")
        self.assertEqual(material["coverage"]["manifests"], "complete")

    def _bundle_with_tree(self, paths: list[str]) -> EvidenceBundle:
        bundle = EvidenceBundle(project_key="proj:tree", collected_at="2026-07-25T00:00:00+00:00")
        bundle.add_record(EvidenceRecord(
            kind="github_repo_snapshot",
            source="github_repo:https://github.com/example/plugin",
            classification="confirmed",
            observed_at=bundle.collected_at,
            value={
                "repo": {"name": "plugin"},
                "revision": "tree123",
                "tree_paths": paths,
                "readme_excerpt": "Plugin project.",
            },
        ))
        return bundle


class RepositoryAnalysisStorageTest(TempRegistryDbTestCase):
    def test_current_analysis_roundtrips_through_project_state(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project(
                "proj:repo-analysis-roundtrip", "repo_url", "https://github.com/example/app"
            )
            project["project_repository_urls"] = ["https://github.com/example/app"]
            project["repository_analysis_results"] = [{
                "version": repository_analysis.ANALYSIS_VERSION,
                "policy_version": repository_analysis.POLICY_VERSION,
                "repo_url": "https://github.com/example/app",
                "source_revision": "abc123",
                "summary": "saved analysis",
                "last_attempt": {"status": "success", "attempted_at": "2026-07-25T00:00:00+00:00"},
            }]
            project_record.save_project(conn, project)
            conn.commit()
            loaded = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()
        assert loaded is not None
        self.assertEqual(loaded["repository_analysis_results"][0]["summary"], "saved analysis")
        self.assertEqual(loaded["repository_analysis_results"][0]["source_revision"], "abc123")


if __name__ == "__main__":
    unittest.main()
