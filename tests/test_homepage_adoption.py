import copy
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record, relations, enrichment_pipeline
from project_registry_core.evidence import EvidenceBundle


class HomepageAdoptionTest(TempRegistryDbTestCase):
    repo = "https://github.com/example/app"
    url = "https://example.com/project"

    def project(self, key="proj:homepage-role", repo=None, url=None, confirmed_relation=True, confirmed_operation=True):
        repo, url = repo or self.repo, url or self.url
        project = self.make_project(key, "repo_url", repo)
        registry.add_manual_seed(project, "repo_url", repo, source="test")
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-09-08T00:00:00Z")
        bundle.add_record(relations.relation_record(
            source="github_repo_metadata", classification="confirmed", observed_at=bundle.collected_at,
            subject_type="repo", subject_value=repo, predicate="repository_homepage",
            object_type="website", object_value=url, locator=repo, method="github_homepage"))
        if confirmed_relation:
            bundle.add_record(relations.relation_record(
                source="operator_review:project_repository", classification="confirmed", observed_at=bundle.collected_at,
                subject_type="website", subject_value=url, predicate="project_repository",
                object_type="repo", object_value=repo, locator=url, method="operator_judgment"))
        if confirmed_operation:
            bundle.add_record(relations.relation_record(
                source="operator_review:operation", classification="confirmed", observed_at=bundle.collected_at,
                subject_type="project", subject_value=key, predicate="operates_service",
                object_type="service", object_value=url, locator=url, method="operator_judgment"))
        registry.apply_github_repo_evidence(project, bundle)
        project["website_url"] = "https://example.com"
        return project

    def result(self, final=None):
        return {"status_code": 200, "final_url": final or self.url,
                "html": f'<footer><a href="{self.repo}">Source code</a></footer>'}

    def browser_page(self, url=None, visible=True):
        return {"url": url or self.url, "visibility_observed": True, "links": [],
                "forms": [{"visible": visible, "inputs": [{"type": "password", "visible": visible}],
                           "submit_texts": ["Sign in"], "visible_submit_texts": ["Sign in"] if visible else []}]}

    def collect(self, project, result=None, page=None, allow_browser=True):
        conn = registry.open_db()
        try:
            with mock.patch.object(registry.service_core, "playwright_navigate", return_value=page or self.browser_page()) as browser:
                bundle = registry.collect_homepage_role_evidence(conn, project, self.url, result or self.result(), allow_browser=allow_browser)
            return bundle, browser.call_count
        finally:
            conn.close()

    def test_visible_exact_homepage_adopts_path_with_reason_and_replays(self):
        # Role-specific evidence can support adoption before this page is a component.
        project = self.project(confirmed_relation=False)
        original = copy.deepcopy(project)
        bundle, count = self.collect(project)
        self.assertEqual(count, 1)
        self.assertEqual(bundle.records[-1].value["action"], "CONFIRM_SERVICE")
        registry.apply_service_evidence(project, bundle)
        self.assertNotEqual(project["service_status"], "live")
        registry.apply_homepage_role_evidence(project, bundle)
        self.assertEqual(project["service_urls"], [self.url])
        self.assertEqual(project["website_url"], "https://example.com")
        self.assertTrue(registry.identifier_has_active_confirm_decision(project, "service_url", self.url))
        registry.apply_replay_evidence(original, bundle)
        self.assertEqual(original["service_urls"], project["service_urls"])
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            saved = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(saved["service_urls"], [self.url])
            self.assertIn({"kind": "service_url", "value": self.url}, registry.trusted_anchor_nodes(saved))
        finally:
            conn.close()

    def test_no_visible_surface_or_no_browser_budget_only_creates_site_candidate(self):
        for budget, visible in ((True, False), (False, True)):
            with self.subTest(budget=budget):
                project = self.project()
                bundle, count = self.collect(project, page=self.browser_page(visible=visible), allow_browser=budget)
                self.assertEqual(count, int(budget))
                registry.apply_homepage_role_evidence(project, bundle)
                self.assertEqual(project["service_urls"], [])
                self.assertEqual(project["project_site_urls"], [])
                self.assertEqual(project["project_site_url_candidates"], [self.url])

    def test_changed_browser_destination_does_not_adopt_static_destination(self):
        bundle, _ = self.collect(self.project(), page=self.browser_page(url="https://example.com/login"))
        self.assertEqual(bundle.records[-1].value["action"], "CANDIDATE_PROJECT_SITE")

    def test_failed_browser_attempt_is_saved_without_adoption(self):
        project = self.project()
        conn = registry.open_db()
        try:
            with mock.patch.object(registry.service_core, "playwright_navigate", side_effect=RuntimeError("timeout")):
                bundle = registry.collect_homepage_role_evidence(conn, project, self.url, self.result())
            registry.apply_service_evidence(project, bundle)
            registry.apply_homepage_role_evidence(project, bundle)
            observation = project["service_verification_results"][-1]
            self.assertEqual(observation["lane"], "repository_homepage_browser")
            self.assertEqual(observation["fetch_error"], "browser_error")
            self.assertEqual(project["service_urls"], [])
        finally:
            conn.close()

    def test_observed_redirect_reuses_existing_role_and_does_not_replace_website(self):
        project = self.project()
        final = "https://example.com/app"
        registry.add_manual_seed(project, "service_url", final, source="test")
        bundle, count = self.collect(project, result=self.result(final))
        self.assertEqual(count, 0)
        self.assertEqual(bundle.records[-1].value["action"], "REUSE_SERVICE")
        registry.apply_homepage_role_evidence(project, bundle)
        self.assertEqual(project["service_urls"], [final])
        self.assertEqual(project["website_url"], "https://example.com")

    def test_collision_guard_and_missing_return_reference_do_not_browse_or_adopt(self):
        for reason in ("guard", "reference", "fetch", "collision"):
            with self.subTest(reason=reason):
                project = self.project()
                result = self.result()
                if reason == "collision":
                    other = self.make_project("proj:other", "service_url", self.url)
                    registry.add_manual_seed(other, "service_url", self.url, source="test")
                    conn = registry.open_db()
                    project_record.save_project(conn, other)
                    conn.commit()
                    conn.close()
                elif reason == "guard":
                    project["blocked_identifiers"] = [{"type": "service_url", "value": self.url}]
                elif reason == "reference":
                    result["html"] = '<a href="https://github.com/example/dependency">Source code</a>'
                else:
                    result["status_code"] = 503
                bundle, count = self.collect(project, result=result)
                self.assertEqual(count, 0)
                self.assertEqual(bundle.records[-1].value["action"], {
                    "guard": "REVIEW_GUARD", "reference": "REVIEW_ATTRIBUTION",
                    "fetch": "REVIEW_FETCH", "collision": "REVIEW_COLLISION",
                }[reason])
                registry.apply_homepage_role_evidence(project, bundle)
                self.assertEqual(project["service_urls"], [])

    def test_normal_enrich_connects_observation_to_adoption(self):
        project = self.project()
        project["website_url"] = ""
        empty = EvidenceBundle(project_key=project["project_key"], collected_at="2026-09-08T00:00:00Z")
        conn = registry.open_db()
        try:
            with (
                mock.patch.object(enrichment_pipeline, "collect_github_repo_evidence", return_value=empty),
                mock.patch.object(enrichment_pipeline, "should_refresh_lexicon_surface", return_value=False),
                mock.patch.object(enrichment_pipeline, "should_refresh_recent_record_samples", return_value=False),
                mock.patch.object(enrichment_pipeline, "plan_investigation_queries", return_value={}),
                mock.patch.object(enrichment_pipeline, "collect_investigation_evidence", return_value=empty),
                mock.patch.object(enrichment_pipeline, "enrich_from_resource_pages"),
                mock.patch.object(enrichment_pipeline.llm_transport, "run_llm_with_metrics", side_effect=RuntimeError("offline test")),
                mock.patch.object(enrichment_pipeline, "probe_service_url", return_value=self.result()),
                mock.patch.object(registry.service_core, "playwright_navigate", return_value=self.browser_page()),
            ):
                enriched = enrichment_pipeline.enrich_project(conn, project)
                candidate = self.project(confirmed_relation=False, confirmed_operation=False)
                candidate["website_url"] = ""
                candidate = enrichment_pipeline.enrich_project(conn, candidate)
            self.assertIn(self.url, enriched["service_urls"])
            self.assertTrue(registry.identifier_has_active_confirm_decision(enriched, "service_url", self.url))
            self.assertEqual(enriched["website_url"], "")
            self.assertEqual(candidate["service_urls"], [])
            self.assertIn(self.url, candidate["project_site_url_candidates"])
            project_record.save_project(conn, candidate)
            conn.commit()
            saved = project_record.fetch_project(conn, candidate["project_key"])
            self.assertIn(self.url, saved["project_site_url_candidates"])
            self.assertNotIn({"kind": "site_url", "value": self.url}, registry.trusted_anchor_nodes(saved))
        finally:
            conn.close()

    def test_browser_budget_moves_to_unobserved_homepage_on_next_enrich(self):
        project = self.project()
        project["website_url"] = ""
        second = "https://example.com/second"
        relation = copy.deepcopy(project["relation_evidence"][0])
        relation["relation"]["object"]["value"] = second
        project["relation_evidence"].append(relation)
        confirmed = copy.deepcopy(project["relation_evidence"][1])
        confirmed["relation"]["subject"]["value"] = second
        project["relation_evidence"].append(confirmed)
        operation = copy.deepcopy(project["relation_evidence"][2])
        operation["relation"]["object"]["value"] = second
        project["relation_evidence"].append(operation)
        empty = EvidenceBundle(project_key=project["project_key"], collected_at="2026-09-08T00:00:00Z")
        conn = registry.open_db()
        try:
            with (
                mock.patch.object(enrichment_pipeline, "collect_github_repo_evidence", return_value=empty),
                mock.patch.object(enrichment_pipeline, "should_refresh_lexicon_surface", return_value=False),
                mock.patch.object(enrichment_pipeline, "should_refresh_recent_record_samples", return_value=False),
                mock.patch.object(enrichment_pipeline, "plan_investigation_queries", return_value={}),
                mock.patch.object(enrichment_pipeline, "collect_investigation_evidence", return_value=empty),
                mock.patch.object(enrichment_pipeline, "enrich_from_resource_pages"),
                mock.patch.object(enrichment_pipeline.llm_transport, "run_llm_with_metrics", side_effect=RuntimeError("offline test")),
                mock.patch.object(enrichment_pipeline, "probe_service_url", side_effect=lambda url, **kw: self.result(url)),
                mock.patch.object(registry.service_core, "playwright_navigate", side_effect=lambda url: self.browser_page(url, visible=False)) as browser,
            ):
                project = enrichment_pipeline.enrich_project(conn, project)
                self.assertEqual(browser.call_count, 1)
                enrichment_pipeline.enrich_project(conn, project)
                self.assertEqual([call.args[0] for call in browser.call_args_list], [self.url, second])
        finally:
            conn.close()

    def test_link_interpretation_only_supports_site_candidate_without_operation(self):
        for label in ("Source code", ""):
            project = self.project(confirmed_relation=False, confirmed_operation=False)
            result = {**self.result(), "html": f'<footer><a href="{self.repo}">{label}</a></footer>'}
            bundle, calls = self.collect(project, result=result)
            facts = bundle.records[-1].value["facts"]
            self.assertTrue(facts["observed_return_reference"])
            self.assertEqual(facts["role_assessments"]["service"]["reason"], "missing_operation_attribution")
            self.assertEqual(bundle.records[-1].value["action"], "CANDIDATE_PROJECT_SITE")
            self.assertEqual(calls, 0)
            registry.apply_homepage_role_evidence(project, bundle)
            self.assertEqual(project["service_urls"], [])
            self.assertEqual(project["project_site_url_candidates"], [self.url])
            self.assertNotIn({"kind": "site_url", "value": self.url}, registry.trusted_anchor_nodes(project))

    def test_downgraded_relation_blocks_previously_collected_adoption(self):
        project = self.project()
        bundle, _ = self.collect(project)
        self.assertEqual(bundle.records[-1].value["action"], "CONFIRM_SERVICE")
        project["relation_evidence"][2]["classification"] = "candidate"
        registry.apply_homepage_role_evidence(project, bundle)
        self.assertEqual(project["service_urls"], [])

    def test_replay_requires_return_reference_to_current_homepage_repository(self):
        project = self.project()
        bundle, _ = self.collect(project)
        second_repo = "https://github.com/example/second"
        registry.add_manual_seed(project, "repo_url", second_repo, source="test")
        second = self.project(repo=second_repo)
        project["relation_evidence"].extend(second["relation_evidence"])
        project["relation_evidence"][0]["classification"] = "candidate"
        registry.apply_homepage_role_evidence(project, bundle)
        self.assertEqual(project["service_urls"], [])
        self.assertEqual(project["project_site_url_candidates"], [])

    def test_old_role_policy_assessment_cannot_restore_automatic_adoption(self):
        for version in ("repository-homepage-role-v1", "repository-homepage-role-v2"):
            project = self.project()
            bundle, _ = self.collect(project)
            bundle.records[-1].value["policy_version"] = version
            registry.apply_replay_evidence(project, bundle)
            self.assertEqual(project["service_urls"], [])

    def test_confirmed_repository_placement_and_login_do_not_prove_operation(self):
        project = self.project(confirmed_operation=False)
        result = {**self.result(), "html": self.result()["html"] + '<form><input type="password"></form>'}
        bundle, calls = self.collect(project, result=result)
        self.assertEqual(calls, 0)
        self.assertEqual(bundle.records[-1].value["action"], "CANDIDATE_PROJECT_SITE")
        registry.apply_homepage_role_evidence(project, bundle)
        self.assertEqual(project["service_urls"], [])

    def test_probe_inferred_operation_does_not_bootstrap_homepage_adoption(self):
        for source, method in (("service_static:page", "static_probe"),
                               ("historical_import", "deep_probe"),
                               ("service_deep:page", "historical_method")):
            project = self.project()
            operation = project["relation_evidence"][2]
            operation["source"] = source
            operation["relation"]["method"] = method
            bundle, calls = self.collect(project)
            self.assertEqual(calls, 0)
            self.assertEqual(bundle.records[-1].value["facts"]["operation_relations"], [])
            self.assertEqual(bundle.records[-1].value["action"], "CANDIDATE_PROJECT_SITE")

    def test_operation_for_other_project_or_destination_does_not_support_role(self):
        for side, value in (("subject", "proj:other"), ("object", "https://example.com/elsewhere")):
            project = self.project()
            project["relation_evidence"][2]["relation"][side]["value"] = value
            bundle, calls = self.collect(project)
            self.assertEqual(calls, 0)
            self.assertEqual(bundle.records[-1].value["action"], "CANDIDATE_PROJECT_SITE")

    def test_barazo_docs_footer_can_propose_site_without_confirmed_page(self):
        # Reduced observed Barazo docs footer: a provider label, not an operation claim.
        project = self.project(confirmed_relation=False, confirmed_operation=False)
        result = {**self.result(), "html": f'<footer><nav><ul><li><a href="{self.repo}">GitHub</a></li></ul></nav></footer>'}
        bundle, calls = self.collect(project, result=result)
        self.assertEqual(calls, 0)
        registry.apply_homepage_role_evidence(project, bundle)
        self.assertEqual(project["project_site_url_candidates"], [self.url])
        self.assertEqual(project["project_site_urls"], [])
        self.assertEqual(project["service_urls"], [])

    def test_pending_homepages_in_different_projects_conflict_in_both_orders(self):
        projects = [self.project(), self.project(key="proj:other", repo="https://github.com/example/other")]
        conn = registry.open_db()
        try:
            for project in projects:
                project_record.save_project(conn, project)
            conn.commit()
            for ordered in (projects, list(reversed(projects))):
                for project in ordered:
                    repo = project["project_repository_urls"][0]
                    result = {**self.result(), "html": f'<footer><a href="{repo}">Source code</a></footer>'}
                    bundle, calls = self.collect(project, result=result)
                    facts = bundle.records[-1].value["facts"]
                    self.assertTrue(facts["observed_return_reference"])
                    self.assertTrue(facts["operation_relations"])
                    self.assertEqual(facts["collision_projects"], [p["project_key"] for p in projects if p is not project])
                    self.assertEqual(bundle.records[-1].value["action"], "REVIEW_COLLISION")
                    self.assertEqual(calls, 0)
                    registry.apply_homepage_role_evidence(project, bundle)
                    self.assertEqual(project["service_urls"], [])
        finally:
            conn.close()

    def test_multiple_repositories_of_same_project_do_not_conflict(self):
        project = self.project()
        second_repo = "https://github.com/example/second"
        registry.add_manual_seed(project, "repo_url", second_repo, source="test")
        second = self.project(repo=second_repo)
        project["relation_evidence"].extend(second["relation_evidence"])
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            bundle, _ = self.collect(project)
            facts = bundle.records[-1].value["facts"]
            self.assertEqual(facts["repositories"], [self.repo, second_repo])
            self.assertEqual(facts["collision_projects"], [])
            registry.apply_homepage_role_evidence(project, bundle)
            self.assertEqual(project["service_urls"], [self.url])
        finally:
            conn.close()

    def test_pending_homepage_observed_redirect_conflicts(self):
        other_url = "https://other.example.com"
        other = self.project(key="proj:other", repo="https://github.com/example/other", url=other_url)
        redirect = relations.relation_record(
            source="service_repository_homepage", classification="confirmed", observed_at="2026-09-08T00:00:00Z",
            subject_type="website", subject_value=other_url, predicate="redirects_to",
            object_type="website", object_value=self.url, locator=other_url, method="http_redirect")
        other["relation_evidence"].append({"relation": redirect.value, "source": redirect.source,
                                          "classification": redirect.classification, "observed_at": redirect.observed_at})
        conn = registry.open_db()
        try:
            project_record.save_project(conn, other)
            conn.commit()
            bundle, calls = self.collect(self.project())
            self.assertEqual(bundle.records[-1].value["action"], "REVIEW_COLLISION")
            self.assertEqual(calls, 0)
        finally:
            conn.close()

    def test_unadopted_repository_homepage_does_not_create_collision(self):
        other = self.project(key="proj:other", repo="https://github.com/example/other")
        conn = registry.open_db()
        try:
            project_record.save_project(conn, other)
            conn.execute("UPDATE project_identifier_adoptions SET cleared_at = ? WHERE project_key = ?",
                         ("2026-09-08T00:00:00Z", other["project_key"]))
            conn.commit()
            bundle, _ = self.collect(self.project())
            self.assertEqual(bundle.records[-1].value["facts"]["collision_projects"], [])
            self.assertEqual(bundle.records[-1].value["action"], "CONFIRM_SERVICE")
        finally:
            conn.close()

    def publication(self, project, *, applicability="current_project", kind="web"):
        analysis = registry.repository_analysis_core
        project['repository_analysis_results'] = [{
            'repo_url': self.repo, 'version': analysis.ANALYSIS_VERSION, 'policy_version': analysis.POLICY_VERSION,
            'summary': '公開アプリ', 'last_attempt': {'status': 'success'}, 'source_revision': 'revision-a',
            'observations': [{'predicate': 'declares_service_url', 'value': self.url,
                              'applicability': applicability, 'service_kind': kind,
                              'evidence': [{'path': 'README', 'line': 1}]}],
        }]
        return project

    def test_uncertain_publication_candidate_needs_neither_fetch_nor_reverse_link(self):
        project = self.publication(self.project(confirmed_operation=False), applicability='uncertain')
        registry.apply_publication_candidates(project)
        self.assertIn(self.url, project['service_url_candidates'])
        bundle, count = self.collect(project, result={'status_code': 404, 'html': ''})
        registry.apply_homepage_role_evidence(project, bundle)
        self.assertEqual(count, 0)
        self.assertNotIn(self.url, project['service_urls'])
        bundle, count = self.collect(project)
        self.assertEqual(count, 0)
        self.assertNotEqual(bundle.records[-1].value['action'], 'CONFIRM_SERVICE')

    def test_publication_can_confirm_existing_site_but_never_from_fork_return_link(self):
        project = self.publication(self.project(confirmed_operation=False))
        registry.add_manual_seed(project, 'site_url', self.url, source='test')
        original = copy.deepcopy(project)
        bundle, count = self.collect(project)
        self.assertEqual(count, 1)
        registry.apply_homepage_role_evidence(project, bundle)
        self.assertIn(self.url, project['service_urls'])
        original['repository_analysis_results'][0]['observations'][0]['applicability'] = 'uncertain'
        registry.apply_homepage_role_evidence(original, bundle)
        self.assertNotIn(self.url, original['service_urls'])
        fork = self.publication(self.project(confirmed_operation=False))
        bundle, count = self.collect(fork, result={'status_code': 200, 'html':
            '<a href="https://github.com/upstream/app">Source code</a>'})
        self.assertEqual(count, 0)
        self.assertNotEqual(bundle.records[-1].value['action'], 'CONFIRM_SERVICE')

    def test_publication_without_homepage_metadata_and_competing_publication(self):
        project = self.publication(self.project(confirmed_operation=False))
        project['relation_evidence'] = []
        self.assertIn(self.url, registry.repository_homepage_probe_urls(project))
        bundle, _ = self.collect(project)
        self.assertEqual(bundle.records[-1].value['action'], 'CONFIRM_SERVICE')
        other = self.publication(self.project(key='proj:publication-other', confirmed_operation=False))
        other['relation_evidence'] = []
        conn = registry.open_db()
        try:
            project_record.save_project(conn, other)
            conn.commit()
            bundle, count = self.collect(project)
            self.assertEqual(count, 0)
            self.assertEqual(bundle.records[-1].value['action'], 'REVIEW_COLLISION')
        finally:
            conn.close()

    def test_protocol_response_confirms_without_browser_but_generic_json_does_not(self):
        self.url = 'https://example.com'
        project = self.publication(self.project(confirmed_operation=False), kind='pds')
        response = {'status_code': 200, 'html': '{"did":"did:web:example.com","availableUserDomains":[".example.com"]}'}
        with mock.patch.object(registry, 'probe_service_url', return_value=response):
            bundle, count = self.collect(project, result={'status_code': 200, 'html': 'Code: ' + self.repo})
        self.assertEqual(count, 0)
        self.assertEqual(bundle.records[-1].value['action'], 'CONFIRM_SERVICE')
        adopted = copy.deepcopy(project)
        registry.apply_homepage_role_evidence(adopted, bundle)
        self.assertIn(self.url, adopted['service_urls'])
        for value in ('{}', '{"status":"ok"}', '{"did":"fake","availableUserDomains":[]}'):
            with mock.patch.object(registry, 'probe_service_url', return_value={'status_code': 200, 'html': value}):
                bundle, _ = self.collect(project)
            self.assertNotEqual(bundle.records[-1].value['action'], 'CONFIRM_SERVICE')

    def test_protocol_path_candidate_survives_without_probe_or_saved_confirmation(self):
        project = self.publication(self.project(confirmed_operation=False), kind='api')
        registry.apply_publication_candidates(project)
        with mock.patch.object(registry, 'probe_service_url') as probe:
            bundle, count = self.collect(project)
        probe.assert_not_called()
        self.assertEqual(count, 0)
        facts = bundle.records[-1].value['facts']
        self.assertEqual(facts['protocol_evidence']['reason'], 'unsupported_service_url')
        self.assertEqual(bundle.collector_results[-1].request_count, 0)
        # Reproduce the old saved observation claiming a nested XRPC endpoint worked.
        facts['protocol_surface'] = True
        facts['protocol_evidence'] = {'verified': True, 'endpoint': self.url + '/xrpc/com.atproto.server.describeServer'}
        registry.apply_homepage_role_evidence(project, bundle)
        self.assertIn(self.url, project['service_url_candidates'])
        self.assertNotIn(self.url, project['service_urls'])
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            saved = project_record.fetch_project(conn, project['project_key'])
            self.assertIn(self.url, saved['service_url_candidates'])
            self.assertNotIn(self.url, saved['service_urls'])
        finally:
            conn.close()

    def test_protocol_checker_does_not_strip_url_scope_or_accept_redirect(self):
        for url in ('https://example.com/api', 'https://example.com/?tenant=one',
                    'https://example.com/#api', 'https://user@example.com'):
            with self.subTest(url=url), mock.patch.object(registry, 'probe_service_url') as probe:
                result = registry.service_core.collect_protocol_service_evidence(url, probe=probe)
                self.assertFalse(result['verified'])
                probe.assert_not_called()
        response = {'status_code': 200, 'final_url': 'https://other.example/xrpc/com.atproto.server.describeServer',
                    'html': '{"did":"did:web:other.example","availableUserDomains":[]}'}
        probe = mock.Mock(return_value=response)
        result = registry.service_core.collect_protocol_service_evidence('https://example.com/', probe=probe)
        self.assertFalse(result['verified'])
        probe.assert_called_once_with('https://example.com/xrpc/com.atproto.server.describeServer', timeout=15.0)

    def test_publication_path_survives_save_and_replay_without_confirm(self):
        project = self.publication(self.project(confirmed_operation=False), applicability='uncertain')
        registry.apply_publication_candidates(project)
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            saved = project_record.fetch_project(conn, project['project_key'])
            self.assertIn(self.url, saved['service_url_candidates'])
            registry.prune_project_urls(saved)
            self.assertIn(self.url, saved['service_url_candidates'])
            bundle = EvidenceBundle(project_key=project['project_key'], collected_at='2026-09-10T00:00:00Z')
            result = project['repository_analysis_results'][0]
            bundle.add_record(registry.repository_analysis_core.evidence_record(
                {'repo_url': self.repo}, result, bundle.collected_at, cache_hit=False))
            target = self.project(confirmed_operation=False)
            registry.apply_replay_evidence(target, bundle)
            self.assertIn(self.url, target['service_url_candidates'])
            self.assertNotIn(self.url, target['service_urls'])
        finally:
            conn.close()

    def test_new_publication_is_probed_in_same_enrich(self):
        project = self.project(confirmed_operation=False)
        project['relation_evidence'] = []
        project['website_url'] = ''
        empty = EvidenceBundle(project_key=project['project_key'], collected_at='2026-09-10T00:00:00Z')
        repo_bundle = EvidenceBundle(project_key=project['project_key'], collected_at=empty.collected_at)
        repo_bundle.add_record(registry.EvidenceRecord(
            kind='github_repo_snapshot', source='github_repo:' + self.repo, classification='confirmed',
            observed_at=empty.collected_at, value={'repo_url': self.repo, 'repo': {},
            'readme_sections': [{'path': 'README', 'line': 1, 'excerpt': 'Try this app at ' + self.url}]}))
        def response(prompt, **kwargs):
            if kwargs.get('purpose') != 'repository_analysis':
                raise RuntimeError('offline triage')
            material = registry.repository_analysis_core.build_material(self.repo, repo_bundle)
            candidate = next(x for x in registry.repository_analysis_core.observation_candidate_catalog(material).values()
                             if x['predicate'] == 'declares_service_url')
            return {'summary': 'アプリ', 'summary_evidence': candidate['evidence_ids'],
                    'observations': [{'candidate_id': candidate['candidate_id'], 'applicability': 'current_project',
                                      'service_kind': 'web'}], 'collections': [], 'capabilities': []}, {}
        conn = registry.open_db()
        try:
            with (
                mock.patch.object(enrichment_pipeline, 'collect_github_repo_evidence', return_value=repo_bundle),
                mock.patch.object(enrichment_pipeline, 'should_refresh_lexicon_surface', return_value=False),
                mock.patch.object(enrichment_pipeline, 'should_refresh_recent_record_samples', return_value=False),
                mock.patch.object(enrichment_pipeline, 'plan_investigation_queries', return_value={}),
                mock.patch.object(enrichment_pipeline, 'collect_investigation_evidence', return_value=empty),
                mock.patch.object(enrichment_pipeline, 'enrich_from_resource_pages'),
                mock.patch.object(enrichment_pipeline.llm_transport, 'run_llm_with_metrics', side_effect=response),
                mock.patch.object(enrichment_pipeline, 'probe_service_url', return_value=self.result()) as probe,
                mock.patch.object(registry.service_core, 'playwright_navigate', return_value=self.browser_page()),
            ):
                result = enrichment_pipeline.enrich_project(conn, project)
            self.assertIn(self.url, [x.args[0] for x in probe.call_args_list])
            self.assertIn(self.url, result['service_urls'])
        finally:
            conn.close()
