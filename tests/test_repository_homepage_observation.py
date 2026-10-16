from __future__ import annotations

from unittest import mock

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry
from enrichment_io import EnrichmentIO
from project_registry_core import enrichment_pipeline, project_record, relations
from project_registry_core.persistence import runtime_view
from project_registry_core.evidence import EvidenceBundle


class _RepositoryHomepageObservationFixtures:
    def homepage_project(self, url="https://jbgruber.github.io/atrrr"):
        repo = "https://github.com/JBGruber/atrrr"
        project = self.make_project("proj:homepage", "repo_url", repo)
        registry.add_manual_seed(project, "repo_url", repo, source="test")
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-09-08T00:00:00Z")
        bundle.add_record(relations.relation_record(
            source=f"github_repo_metadata:{repo}", classification="confirmed",
            observed_at=bundle.collected_at, subject_type="repo", subject_value=repo,
            predicate="repository_homepage", object_type="website", object_value=url,
            locator=repo, method="github_homepage",
        ))
        registry.apply_github_repo_evidence(project, bundle)
        return project


class RepositoryHomepageObservationUnitTest(_RepositoryHomepageObservationFixtures, ProjectRecordTestCase):
    def test_exact_paths_and_docs_are_observed_without_service_candidates(self):
        for url in ("https://jbgruber.github.io/atrrr", "https://idiolect.dev/fieldwork",
                    "https://example.com/about", "https://docs.example.com/sdk",
                    "https://arroba.readthedocs.io/en/stable"):
            with self.subTest(url=url):
                project = self.homepage_project(url)
                self.assertEqual(registry.repository_homepage_probe_urls(project), [url])
                self.assertEqual(project["service_url_candidates"], [])

    def test_unadopted_repository_and_non_page_roles_are_excluded(self):
        project = self.homepage_project()
        # A confirmed-looking identifier without an active adoption is not an anchor.
        project[runtime_view.RUNTIME_ADOPTION_ROWS] = []
        self.assertEqual(registry.repository_homepage_probe_urls(project), [])
        for url in ("https://github.com/example/app", "https://pypi.org/project/atproto", "http://localhost/app"):
            self.assertEqual(registry.repository_homepage_probe_urls(self.homepage_project(url)), [])
        project = self.homepage_project()
        project["deferred_identifiers"] = [{"type": "service_url", "value": "https://jbgruber.github.io/atrrr", "expires_at": "2099-01-01T00:00:00Z"}]
        self.assertEqual(registry.repository_homepage_probe_urls(project), [])

    def test_failed_refresh_keeps_page_relations(self):
        project = self.homepage_project()
        url = registry.repository_homepage_probe_urls(project)[0]
        for result in ({"status_code": 200, "html": '<a href="https://github.com/JBGruber/atrrr">Source code</a>'},
                       {"status_code": 0, "fetch_error": "timeout"}):
            bundle = registry.collect_service_evidence_with_followups(project, url, result, lane="repository_homepage")
            registry.apply_service_evidence(project, bundle)
        self.assertTrue(any(item["relation"]["predicate"] == "project_repository" for item in project["relation_evidence"]))


class RepositoryHomepageObservationTest(_RepositoryHomepageObservationFixtures, TempRegistryDbTestCase):
    def test_observation_and_replay_preserve_roles_and_editorial_website(self):
        project = self.homepage_project()
        url = registry.repository_homepage_probe_urls(project)[0]
        project["website_url"] = "https://example.com"
        result = {
            "status_code": 200, "final_url": url + "/reference", "redirected": True,
            "html": '<a href="https://github.com/JBGruber/atrrr">Source code</a><pre>agent.Login()</pre>',
            "service_status": "live", "service_status_confidence": "high",
            "links": [], "urls": [],
        }
        with mock.patch.object(registry, "service_probe_supports_project", side_effect=AssertionError("premature attribution")):
            bundle = registry.collect_service_evidence_with_followups(project, url, result, lane="repository_homepage")
        for apply in (registry.apply_service_evidence, registry.apply_replay_evidence):
            apply(project, bundle)
            self.assertEqual(project["service_urls"], [])
            self.assertEqual(project["service_url_candidates"], [])
            self.assertEqual(project["website_url"], "https://example.com")
            self.assertIsNone(registry.project_current_service_observation(project))
        redirects = [r for r in bundle.records if r.kind == "relation" and r.value["predicate"] == "redirects_to"]
        self.assertEqual(redirects[0].value["subject"]["type"], "website")
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            saved = project_record.fetch_project(conn, project["project_key"])
            observation = saved["service_verification_results"][0]
            self.assertEqual(observation["requested_url"], url)
            self.assertEqual(observation["final_url"], url + "/reference")
            self.assertIn("agent.Login()", observation["html_extract"])
        finally:
            conn.close()

    def test_enrich_fetches_exact_homepage_once_and_uses_existing_service_lane(self):
        url = "https://jbgruber.github.io/atrrr"
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                project = self.homepage_project(url)
                if accepted:
                    registry.add_manual_seed(project, "service_url", url, source="test")
                project["website_url"] = url + "/"
                conn = registry.open_db()
                try:
                    io = EnrichmentIO()
                    for repo in project["project_repository_urls"]:
                        io.github_repo(repo, homepage=url)
                    io.page(url)
                    io.page(url + "/")
                    with io:
                        enriched = enrichment_pipeline.enrich_project(conn, project)
                    self.assertEqual([call.args[0] for call in io.http.call_args_list].count(url), 1)
                    self.assertEqual([call.args[0] for call in io.http.call_args_list].count(url + "/"), 1)
                    observations = [item for item in enriched["service_verification_results"]
                                    if item["requested_url"] == url]
                    self.assertEqual([item["lane"] for item in observations],
                                     ["static" if accepted else "repository_homepage"])
                finally:
                    conn.close()
