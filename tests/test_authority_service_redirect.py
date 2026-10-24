from __future__ import annotations

import copy
import datetime as dt
from urllib.parse import urlencode

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry
from enrichment_io import EnrichmentIO
from project_registry_core import enrichment_pipeline, project_record
from project_registry_core.maintenance import registry_audit


class AuthorityServiceRedirectTest(ProjectRecordTestCase):
    def project_and_result(self, final_url="https://example.app/login"):
        project = self.make_project("proj:authority-login")
        project["authority_domains"] = ["example.app"]
        result = {
            **registry.service_probe_redirect_metadata("https://example.app", final_url),
            "status_code": 200,
            "title": "Example",
            "description": "Sign in with your AT Protocol account",
            "html": '<title>Example</title><form><input name="handle"></form>',
            "links": [],
            "urls": [],
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200"],
            "fetch_error": "",
            "supports_project": False,
            "lane": "authority",
        }
        return project, result

    def test_same_host_entry_survives_unsupported_probe_and_finalize(self):
        for path in ("/login", "/signin", "/sign-in", "/signup", "/register", "/app", "/feed"):
            with self.subTest(path=path):
                project, result = self.project_and_result("https://example.app" + path)
                registry.apply_service_probe_result(project, "https://example.app", result, collect_follow_ups=False)
                self.assertEqual(project["service_url_candidates"], [])
                registry.apply_authority_service_probe_result(project, "https://example.app", result)
                project["service_verification_results"] = [{**result, "html_extract": result["html"]}]
                finalized = registry.finalize_project_shape(project)
                self.assertEqual(finalized["service_url_candidates"], ["https://example.app"])
                self.assertEqual(finalized["service_urls"], [])
                self.assertEqual(finalized["supported_service_urls"], [])

    def test_redirect_does_not_bypass_authority_surface_or_status_checks(self):
        for changes in (
            {"status_code": 404},
            {"service_status": "dead"},
            {"service_status": "unreleased"},
            {"service_status": "unknown"},
            {"title": "Domain for sale", "description": "Buy this domain", "html": ""},
            {"description": "Sign in", "html": ""},
        ):
            with self.subTest(changes=changes):
                project, result = self.project_and_result()
                self.assertFalse(registry.authority_service_probe_candidate_allowed(
                    project, "https://example.app", {**result, **changes}
                ))
        project, result = self.project_and_result()
        project["authority_domains"] = ["other.app"]
        self.assertFalse(registry.authority_service_probe_candidate_allowed(project, "https://example.app", result))

    def test_redirect_to_other_host_downgrade_or_supporting_page_is_not_authority_entry(self):
        for final_url in (
            "https://auth.example.app/login",
            "http://example.app/login", "https://example.app/docs",
            "https://example.app/about", "https://example.app/help",
            "https://example.app/logo.png",
        ):
            with self.subTest(final_url=final_url):
                project, result = self.project_and_result(final_url)
                self.assertFalse(registry.authority_service_probe_candidate_allowed(project, "https://example.app", result))

    def test_guards_apply_to_entry_and_redirect_target(self):
        for field in ("blocked_identifiers", "deferred_identifiers"):
            for url in ("https://example.app", "https://example.app/login"):
                with self.subTest(field=field, url=url):
                    project, result = self.project_and_result()
                    project[field] = [{
                        "type": "service_url", "value": url,
                        "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat(),
                    }]
                    self.assertFalse(registry.authority_service_probe_candidate_allowed(project, "https://example.app", result))

    def test_parent_fallback_redirect_needs_service_surface_or_concrete_evidence(self):
        project, result = self.project_and_result()
        project["authority_domains"] = ["graph.example.app"]
        result.update({
            "authority_fallback_from": "https://graph.example.app",
            "authority_fallback_status_code": 0,
            "authority_fallback_fetch_error": "dns_failed",
            "description": "Sign in", "html": "<form>Sign in</form>",
        })
        self.assertTrue(registry.authority_service_probe_candidate_allowed(project, "https://example.app", result))
        result.update({"service_status": "unverified", "title": "Account Suspended", "html": ""})
        self.assertFalse(registry.authority_service_probe_candidate_allowed(project, "https://example.app", result))
        result.update({"title": "Example", "html": "An AT Protocol application"})
        self.assertTrue(registry.authority_service_probe_candidate_allowed(project, "https://example.app", result))
        result.update({"service_status": "none"})
        self.assertFalse(registry.authority_service_probe_candidate_allowed(project, "https://example.app", result))


class AuthorityServiceRedirectStorageTest(TempRegistryDbTestCase):
    def test_enrich_save_reload_keeps_origin_candidate_without_confirming_login(self):
        project = self.make_project("proj:enrich-authority-login")
        did = "did:plc:authorityfixture"
        io = EnrichmentIO(dns=lambda *args, **kwargs: ["did=" + did])
        io.json("https://plc.directory/" + did, {
            "id": did, "service": [{"type": "AtprotoPersonalDataServer", "serviceEndpoint": "https://pds.example.test"}],
        })
        params = urlencode({"repo": did, "collection": "com.atproto.lexicon.schema", "rkey": "app.example.post"})
        io.json("https://pds.example.test/xrpc/com.atproto.repo.getRecord?" + params, {
            "uri": f"at://{did}/com.atproto.lexicon.schema/app.example.post",
            "value": {"$type": "com.atproto.lexicon.schema", "lexicon": 1, "id": "app.example.post",
                      "defs": {"main": {"type": "space", "collections": []}}},
        })
        io.page("https://example.app", final_url="https://example.app/login", html=(
            '<title>Example</title><meta name="description" content="An AT Protocol app">'
            '<form><input name="handle"><button>Sign in</button></form>'
        ))
        conn = registry.open_db()
        try:
            with io:
                enriched = enrichment_pipeline.enrich_project(conn, project)
                project_record.save_project(conn, enriched, enriched=True)
                conn.commit()
            fetched = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()
        self.assertEqual(fetched["service_url_candidates"], ["https://example.app"])
        self.assertEqual(fetched["service_urls"], [])
        self.assertEqual(fetched["supported_service_urls"], [])
        probe = fetched["service_verification_results"][0]
        self.assertEqual(probe["final_url"], "https://example.app/login")
        self.assertFalse(probe["supports_project"])

    def test_repair_recovers_missing_candidate_from_saved_probe_without_fetching(self):
        project, result = AuthorityServiceRedirectTest().project_and_result()
        project["service_verification_results"] = [{
            **result, "html_extract": result["html"], "html": "", "lane": "authority",
        }]
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            original = copy.deepcopy(project_record.fetch_project(conn, project["project_key"]))
            self.assertEqual(original["service_url_candidates"], [])
            registry_audit.audit_and_repair_identifiers(conn, execute=False)
            self.assertEqual(project_record.fetch_project(conn, project["project_key"])["service_url_candidates"], [])
            registry_audit.audit_and_repair_identifiers(conn, execute=True)
            repaired = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(repaired["service_url_candidates"], ["https://example.app"])
            self.assertEqual(repaired["service_urls"], [])
            self.assertEqual(repaired["service_verification_results"], original["service_verification_results"])
            registry_audit.audit_and_repair_identifiers(conn, execute=True)
            self.assertEqual(project_record.fetch_project(conn, project["project_key"])["service_url_candidates"], ["https://example.app"])
        finally:
            conn.close()
