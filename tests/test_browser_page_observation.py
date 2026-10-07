from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry
from project_registry_core import project_record, service


class _BrowserPageObservationFixtures:
    def page(self, url, *, visible=True):
        return {
            "url": url, "title": "Sign in", "visibility_observed": True,
            "forms": [{"visible": visible, "inputs": [{"type": "password", "visible": visible}],
                       "submit_texts": ["Sign in"], "visible_submit_texts": ["Sign in"] if visible else []}],
            "links": [],
        }


class BrowserPageObservationUnitTest(_BrowserPageObservationFixtures, ProjectRecordTestCase):
    def test_hidden_and_unobserved_controls_do_not_establish_visible_surface(self):
        hidden = self.page("https://example.com", visible=False)
        self.assertTrue(service.browser_page_auth_features(hidden)["password_form_detected"])
        self.assertFalse(service.browser_page_visible_surface_features(hidden)["positive_surface"])
        old = {"url": "https://example.com", "forms": [{"inputs": [{"type": "password"}], "submit_texts": ["Sign in"]}]}
        features = service.browser_page_visible_surface_features(old)
        self.assertFalse(features["visibility_observed"])
        self.assertFalse(features["positive_surface"])
        self.assertTrue(service.browser_page_visible_surface_features(self.page("https://example.com"))["positive_surface"])

    def test_hidden_password_or_submit_is_not_visible_login(self):
        for field in ("input", "submit"):
            page = self.page("https://example.com")
            if field == "input":
                page["forms"][0]["inputs"][0]["visible"] = False
            else:
                page["forms"][0]["visible_submit_texts"] = []
            self.assertFalse(service.browser_page_visible_surface_features(page)["positive_surface"])

    def test_public_surface_requires_visible_action_and_content_links(self):
        page = {"url": "https://example.com", "visibility_observed": True,
                "service_actions": [{"text": "Post new entry", "visible": True}],
                "links": [{"url": f"https://example.com/user/{i}", "visible": True} for i in range(3)]}
        self.assertTrue(service.browser_page_visible_surface_features(page)["public_app_surface_detected"])
        page["links"][0]["visible"] = False
        self.assertFalse(service.browser_page_visible_surface_features(page)["public_app_surface_detected"])

    def test_display_contents_form_can_have_visible_controls(self):
        page = self.page("https://example.com")
        page["forms"][0]["visible"] = False
        self.assertTrue(service.browser_page_visible_surface_features(page)["positive_surface"])

    def test_hidden_duplicate_link_does_not_erase_visible_link(self):
        links = [{"href": "https://example.com/user/post", "text": "Post", "visible": visible}
                 for visible in (False, True)]
        saved = service.bounded_service_links("https://example.com", links)
        self.assertEqual([item["visible"] for item in saved], [False, True])


class BrowserPageObservationTest(_BrowserPageObservationFixtures, TempRegistryDbTestCase):
    def test_followed_login_does_not_become_positive_evidence_for_landing(self):
        root, login = "https://example.com", "https://example.com/login"
        pages = {root: {"url": root, "visibility_observed": True,
                        "links": [{"href": login, "text": "Sign in", "visible": True}]},
                 login: self.page(login)}
        project = self.make_project("proj:browser-pages", "repo_url", "https://github.com/example/app")
        bundle = service.collect_browser_service_evidence(project, root, service.BrowserDependencies(
            navigate=lambda url: pages[url], now=lambda: "2026-09-08T00:00:00Z"))
        value = bundle.records[0].value
        self.assertTrue(service.browser_service_observation(value)["positive_surface"])
        self.assertEqual([p["visible_surface_features"]["positive_surface"] for p in value["pages"]], [False, True])
        self.assertEqual([p["requested_url"] for p in value["pages"]], [root, login])
        registry.apply_replay_evidence(project, bundle)
        self.assertEqual(project["service_urls"], [])
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            saved = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(saved["service_verification_results"][0]["pages"], value["pages"])
            for page in value["pages"]:
                self.assertEqual(service.browser_page_visible_surface_features(page), page["visible_surface_features"])
        finally:
            conn.close()
