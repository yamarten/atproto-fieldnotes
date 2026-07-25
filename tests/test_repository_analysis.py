from __future__ import annotations

import unittest
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record
from project_registry_core import repository_analysis
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord


class RepositoryAnalysisTest(unittest.TestCase):
    def github_bundle(self) -> EvidenceBundle:
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
                "lexicon_hints": [{"nsid": "dev.example.Record", "path": "lexicons/dev.example.Record.json"}],
                "source_evidence": [{
                    "nsid": "com.atproto.repo.getRecord",
                    "path": "src/client.ts",
                    "operation": "read",
                    "excerpt": "client.call('com.atproto.repo.getRecord')",
                }],
            },
        ))
        return bundle

    def test_material_is_bounded_and_keeps_revision_and_source_locator(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        self.assertEqual(material["revision"], "abc123")
        self.assertEqual(material["provider"], "github")
        self.assertTrue(material["source_inspected"])
        self.assertEqual(material["source_evidence"][0]["path"], "src/client.ts")
        self.assertIn("untrusted", repository_analysis.build_prompt({}, material))

    def test_matching_revision_reuses_current_result(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        project = {"repository_analysis_results": [{
            "version": repository_analysis.ANALYSIS_VERSION,
            "repo_url": material["repo_url"],
            "revision": material["revision"],
            "summary": "cached",
            "stale": False,
        }]}
        self.assertEqual(repository_analysis.cached_result(project, material)["summary"], "cached")

    def test_failure_keeps_previous_analysis_and_defers_same_revision(self) -> None:
        material = repository_analysis.build_material(
            "https://github.com/example/app", self.github_bundle()
        )
        project = {"repository_analysis_results": [{
            "repo_url": material["repo_url"],
            "revision": "old",
            "summary": "previous analysis",
        }]}
        now = "2026-07-25T00:00:00+00:00"
        repository_analysis.mark_failure(project, material, now, "timeout")
        result = project["repository_analysis_results"][0]
        self.assertEqual(result["summary"], "previous analysis")
        self.assertTrue(result["stale"])
        self.assertTrue(repository_analysis.retry_deferred(project, material, now))

    def test_triage_evidence_includes_only_confirmed_repository_analysis(self) -> None:
        project = project_record.default_project_record(
            "proj:repo-analysis", "repo_url", "https://github.com/example/app"
        )
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["repository_analysis_results"] = [{
            "repo_url": "https://github.com/example/app",
            "revision": "abc123",
            "summary": "confirmed repository analysis",
            "stale": False,
        }]
        evidence = registry.build_llm_evidence_bundle(project)
        self.assertEqual(
            evidence["repository_analysis_results"][0]["summary"],
            "confirmed repository analysis",
        )

    def test_package_manifest_extracts_only_relevant_structured_fields(self) -> None:
        tree = [{"path": "package.json", "type": "blob", "size": 100}]
        with mock.patch.object(registry, "github_repo_file_text", return_value='''{
          "name": "example-app",
          "scripts": {"start": "node index.js"},
          "dependencies": {"@atproto/api": "1.0.0", "unrelated": "2.0.0"}
        }'''):
            hints = registry.repo_manifest_hints(
                "https://github.com/example/app", ref="main", tree=tree
            )
        self.assertEqual(hints[0]["scripts"], ["start"])
        self.assertEqual(hints[0]["protocol_dependencies"], {"@atproto/api": "1.0.0"})

    def test_plugin_source_layout_is_not_classified_as_library(self) -> None:
        tree = [
            {"path": "package.json", "type": "blob"},
            {"path": "src/example/webpackModules/presence.tsx", "type": "blob"},
        ]
        self.assertEqual(registry.github_repo_implementation_role(tree), "plugin")


class RepositoryAnalysisStorageTest(TempRegistryDbTestCase):
    def test_current_analysis_roundtrips_through_project_state(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project(
                "proj:repo-analysis-roundtrip", "repo_url", "https://github.com/example/app"
            )
            project["source_code_urls"] = ["https://github.com/example/app"]
            project["repository_analysis_results"] = [{
                "repo_url": "https://github.com/example/app",
                "revision": "abc123",
                "summary": "saved analysis",
                "stale": False,
            }]
            project_record.save_project(conn, project)
            conn.commit()
            loaded = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()
        assert loaded is not None
        self.assertEqual(loaded["repository_analysis_results"][0]["summary"], "saved analysis")


if __name__ == "__main__":
    unittest.main()
