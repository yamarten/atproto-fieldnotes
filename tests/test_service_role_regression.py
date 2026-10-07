import copy
import json
from pathlib import Path

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry
from enrichment_io import EnrichmentIO
from project_registry_core import enrichment_pipeline, project_record
from project_registry_core.repositories import repository_trust
from project_registry_core import trust_graph

FIXTURE = json.loads((Path(__file__).parent / "fixtures/project_registry/service-role-regression-v1.json").read_text())


class _ServiceRoleRegressionFixtures:
    def historical_arroba(self):
        observation = FIXTURE["historical_arroba_adoption"]
        self.assertEqual(observation["candidate"]["kind"], "service_url")
        self.assertEqual(observation["anchor"]["kind"], "repo_url")
        repo, url = observation["anchor"]["value"], observation["candidate"]["value"]
        p = self.make_project("proj:arroba-regression", "repo_url", repo)
        registry.add_manual_seed(p, "repo_url", repo, source="test")
        p["website_url"] = url  # The reviewed Website remains useful without a service claim.
        p["service_url_candidates"] = [url]
        for edge in observation["forward_path"] + observation["backward_path"]:
            repository_trust.record_observation(p, edge)
        self.assertFalse(trust_graph.evaluate_auto_confirm(
            p, candidate=observation["candidate"], anchors=[observation["anchor"]])["auto_confirm"])
        return p, url


class ServiceRoleRegressionUnitTest(_ServiceRoleRegressionFixtures, ProjectRecordTestCase):
    def test_current_three_cases_do_not_establish_service_from_repository_anchors(self):
        for case in FIXTURE["cases"]:
            with self.subTest(project=case["name"]):
                evaluation = trust_graph.evaluate_auto_confirm(
                    {"relation_evidence": copy.deepcopy(case["relation_evidence"])},
                    candidate={"kind": "service_url", "value": case["url"]}, anchors=case["anchors"])
                self.assertFalse(evaluation["auto_confirm"])

    def test_repository_cannot_bridge_two_services_but_canonical_service_path_still_works(self):
        p, url = self.historical_arroba()
        anchor = {"kind": "service_url", "value": "https://service.example.com"}
        repo = FIXTURE["historical_arroba_adoption"]["anchor"]
        for subject, obj, predicate in ((anchor, repo, "service_links_repo"), (repo, anchor, "repo_links_service")):
            trust_graph.upsert_edge(p, subject=subject, predicate=predicate, object=obj,
                                    classification="confirmed", eligible_for_auto_confirm=True)
        candidate = {"kind": "service_url", "value": url}
        self.assertFalse(trust_graph.evaluate_auto_confirm(p, candidate=candidate, anchors=[anchor])["auto_confirm"])
        for subject, obj in ((anchor, candidate), (candidate, anchor)):
            trust_graph.upsert_edge(p, subject=subject, predicate="service_canonical", object=obj,
                                    classification="confirmed", eligible_for_auto_confirm=True)
        self.assertTrue(trust_graph.evaluate_auto_confirm(p, candidate=candidate, anchors=[anchor])["auto_confirm"])


class ServiceRoleRegressionTest(_ServiceRoleRegressionFixtures, TempRegistryDbTestCase):
    def test_historical_same_project_adoption_does_not_return_after_enrich_save_and_recheck(self):
        p, url = self.historical_arroba()
        io = EnrichmentIO()
        for repo in p["project_repository_urls"]:
            io.github_repo(repo)
        io.page(url, final_url=url + "/en/stable", html="<h1>arroba documentation</h1>")
        conn = registry.open_db()
        try:
            with io:
                enriched = enrichment_pipeline.enrich_project(conn, p)
            self.assertEqual(enriched["service_urls"], [])
            project_record.save_project(conn, enriched)
            conn.commit()
            saved = project_record.fetch_project(conn, p["project_key"])
            registry.reconcile_trust_supported_url_candidates(saved)
            self.assertEqual(saved["service_urls"], [])
            self.assertEqual(saved["website_url"], url)
            self.assertFalse(registry.confirm_service_url_with_trust(saved, url))
            self.assertFalse(registry.identifier_has_active_confirm_decision(saved, "service_url", url))
        finally:
            conn.close()
