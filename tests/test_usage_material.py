from __future__ import annotations

import contextlib
import copy
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import oauth_metadata, project_record, repository_analysis, service, usage_material
from project_registry_core.application import presentation
from project_registry_core.evidence import EvidenceBundle


class UsageMaterialTest(TempRegistryDbTestCase):
    def collect(self, html, *, date="2026-09-22T10:00:00+00:00"):
        project = self.make_project("proj:material", "service_url", "https://example.com")
        bundle = service.collect_service_probe_evidence(project["project_key"], "https://example.com", {
            "html": html, "status_code": 200, "service_status": "live", "title": "Example",
            "description": "", "final_url": "https://example.com", "links": [], "urls": [],
        }, observed_at=date, supports_project=True)
        registry.apply_service_evidence(project, bundle)
        return project, bundle

    def test_late_storage_description_survives_html_prefix_limit_and_replay(self):
        html = '<html><head><style>' + ('a { color: red; }' * 1000) + '</style></head><body><h1>Highlights</h1><p>Your highlights live in your personal data repository.</p></body></html>'
        project, bundle = self.collect(html)
        observation = project["service_verification_results"][0]
        self.assertNotIn("highlights live", observation["html_extract"])
        self.assertIn("highlights live", observation["atproto_text_candidates"]["passages"][0]["text"])
        replay = self.make_project(project["project_key"], "service_url", "https://example.com")
        registry.apply_replay_evidence(replay, EvidenceBundle.from_dict(bundle.to_dict()))
        with contextlib.closing(registry.open_db()) as conn:
            project_record.save_project(conn, replay)
            detail = presentation.project_detail(conn, project_key=project["project_key"])
        material = detail["service_ownership_review"]["atproto_material"]
        self.assertEqual(material["status"], "matches")
        self.assertIn("highlights live", material["sources"][0]["passages"][0]["text"])

    def test_negation_future_and_third_party_context_remain_exact(self):
        text = "A third-party example plans to store notes on a PDS. Our app does not store notes there."
        result = usage_material.passages(text)
        self.assertEqual(result["passages"][0]["text"], text)
        self.assertNotIn("role", result)
        self.assertNotIn("writes", result)
        for passage in result["passages"]:
            self.assertEqual(text[passage["start"]:passage["end"]], passage["text"])

    def test_scripts_and_markup_are_not_claimable_text(self):
        html = '<head>atproto</head><script>"PDS"</script><style>.Bluesky{}</style><template>PDS</template><svg><text>atproto</text></svg><p>Your <strong>personal data repository</strong> &amp; notes.</p>'
        text = usage_material.page_text(html)
        self.assertEqual(text, "Your personal data repository & notes.")

    def test_old_excerpt_and_new_failure_are_not_full_or_current_proof(self):
        project, _ = self.collect("<p>Uses PDS.</p>")
        project["service_verification_results"][0].pop("atproto_text_candidates")
        material = usage_material.review(project)
        self.assertEqual(material["sources"][0]["coverage"], "saved_html_excerpt")
        project["service_verification_results"][0]["fetch_error"] = "timeout"
        self.assertEqual(usage_material.review(project)["status"], "not_observed")
        project["service_verification_results"][0].pop("fetch_error")
        project["service_verification_results"][0]["supports_project"] = False
        self.assertEqual(usage_material.review(project)["status"], "not_observed")

    def test_no_match_and_unobserved_are_distinct(self):
        project, _ = self.collect("<p>Manage tasks.</p>")
        self.assertEqual(usage_material.review(project)["status"], "no_matches")
        self.assertEqual(usage_material.review({})["status"], "not_observed")
        project, _ = self.collect("<script>app()</script>")
        self.assertEqual(usage_material.review(project)["status"], "text_unavailable")
        project["service_verification_results"][0]["description"] = "A board game tracker on AT Protocol."
        self.assertEqual(usage_material.review(project)["sources"][0]["coverage"], "saved_description")

    def test_japanese_suffix_and_late_storage_statement_are_found(self):
        text = ("AT Protocol marketing. " + "padding " * 200) * 8 + "サインインしていれば、Bluesky PDSに質問や回答のデータが保存されます。"
        result = usage_material.passages(text)
        self.assertTrue(any("PDSに質問や回答" in p["text"] for p in result["passages"]))

    def test_literal_match_is_exact_and_does_not_change_evaluation(self):
        project, _ = self.collect("<p>PDS records include app.example.note and app.example.notebook.</p>")
        project["oauth_client_metadata"] = [oauth_metadata.observe("https://example.com/client.json", {
            "scope": "atproto repo:app.example.note repo:app.example.notebooks",
        }, "2026-09-22T10:00:00+00:00")]
        before = copy.deepcopy(project)
        material = usage_material.review(project)
        self.assertEqual(material["sources"][0]["literal_collections"], ["app.example.note"])
        self.assertEqual(project, before)
        self.assertEqual(usage_material.passages("app.example.note-other", ["app.example.note"])["passages"], [])
        self.assertTrue(usage_material.passages("app.example.noteに保存", ["app.example.note"])["passages"])
        self.assertTrue(usage_material.passages("Uses app.example.note.", ["app.example.note"])["passages"])
        self.assertFalse(usage_material.passages("app.example.note.child", ["app.example.note"])["passages"])

    def test_bounded_selection_keeps_offsets_and_reports_omission(self):
        text = ("PDS. " + "padding " * 200) * 10
        result = usage_material.passages(text)
        self.assertEqual(len(result["passages"]), usage_material.MAX_PASSAGES)
        self.assertTrue(result["matches_omitted"])
        for passage in result["passages"]:
            self.assertLessEqual(len(passage["text"]), usage_material.PASSAGE_CHARS)
            self.assertEqual(passage["text"], text[passage["start"]:passage["end"]])
        limited = usage_material.from_html("x" * (usage_material.MAX_HTML_CHARS + 1) + "PDS")
        self.assertTrue(limited["input_truncated"])
        self.assertFalse(limited["passages"])

    def test_browser_and_readme_use_raw_excerpt_not_generated_summary(self):
        project = self.make_project("proj:material", "service_url", "https://example.com")
        project["service_verification_results"] = [{"lane": "browser", "supports_project": True,
            "requested_url": "https://example.com", "observed_at": "2026-09-22", "pages": [
                {"url": "https://example.com/about", "text_extract": "Reads public PDS records."}]}]
        repository = {"repo_url": "https://github.com/example/app", "source_revision": "abc",
            "summary": "INVENTED summary: writes PDS data", "evidence_excerpts": [
                {"path": "README.md", "line": 15, "excerpt": "Plans to use AT Protocol."}]}
        with mock.patch.object(repository_analysis, "select_for_triage", return_value=[repository]):
            result = usage_material.review(project)
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(result["sources"][0]["locator"], "https://example.com/about")
        self.assertEqual(result["sources"][1]["passages"][0]["text"], "Plans to use AT Protocol.")

    def test_confirmed_current_repository_path_and_unconfirmed_exclusion(self):
        project = self.make_project("proj:repository", "service_url", "https://example.com")
        repo = "https://github.com/example/app"
        project["project_repository_urls"] = [repo]
        project["repository_analysis_results"] = [{
            "repo_url": repo, "source_revision": "abc123", "summary": "Generated description",
            "version": repository_analysis.ANALYSIS_VERSION, "policy_version": repository_analysis.POLICY_VERSION,
            "summary_evidence": [{"path": "README.md", "line": 12, "excerpt": "Reads public AT Protocol records."}],
        }]
        material = usage_material.review(project)
        self.assertEqual(material["sources"][0]["kind"], "repository")
        self.assertEqual(material["sources"][0]["passages"][0]["text"], "Reads public AT Protocol records.")
        project["project_repository_urls"] = []
        self.assertEqual(usage_material.review(project)["status"], "not_observed")
