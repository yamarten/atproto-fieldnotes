from __future__ import annotations

import copy
import json
from unittest import mock

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry
from project_registry_core import oauth_metadata, oauth_service, project_record, service, service_discovery, project_review, relations
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord
from test_project_registry_collection_trust import publication
from enrichment_io import EnrichmentIO
from project_registry_core import enrichment_pipeline

URL = "https://atmosynth.at"
METADATA = URL + "/client-metadata.json"
NSID = "at.atmosynth.patch"


def metadata(scope="atproto repo:at.atmosynth.patch", **changes):
    return {"client_id": METADATA, "client_uri": URL, "application_type": "web",
            "dpop_bound_access_tokens": True, "grant_types": ["authorization_code", "refresh_token"],
            "redirect_uris": [URL + "/auth/callback"], "response_types": ["code"], "scope": scope, **changes}


def page(url=URL, **changes):
    return {"url": url, "status_code": 200, "visibility_observed": True,
            "forms": [{"context": "Sign in with your Atmosphere account to create patches.",
                       "visible": True, "visible_submit_texts": ["CONTINUE"],
                       "inputs": [{"name": "handle", "label": "Your Atmosphere handle", "type": "text",
                                   "visible": True, "placeholder": "alice.bsky.social"}]}], **changes}


def supported_project():
    p = project_record.default_project_record("proj:oauthservice", "nsid", NSID)
    p["resolved_nsids"] = [NSID]
    registry.add_confirmed_identifier_decision(p, "nsid", NSID, source="collection_inventory_import", role="owned_nsid")
    registry.apply_lexicon_record_result(p, NSID, publication(NSID, when=project_record.iso_now()))
    p["oauth_client_metadata"] = [oauth_metadata.observe(METADATA, metadata(), project_record.iso_now(),
        response={"status_code": 200, "content_type": "application/json", "redirected": False},
        discovery={"service_url": URL, "method": "conventional_path"})]
    p["service_verification_results"] = [{"requested_url": URL, "lane": "browser_discovery",
        "observed_at": project_record.iso_now(), "pages": [page()]}]
    return p


class OAuthServiceTest(ProjectRecordTestCase):
    def test_scoped_path_requires_metadata_and_independent_root(self):
        p = supported_project()
        result = oauth_service.evaluate(p, URL)
        self.assertTrue(result["auto_confirm"], result)
        self.assertEqual(result["anchor"]["value"], NSID)
        self.assertIn("requests_permission", [edge["predicate"] for edge in result["backward_path"]])
        self.assertNotIn("service_exact_domain", [edge["predicate"] for edge in result["backward_path"]])
        registry.apply_oauth_service_adoptions(p)
        self.assertIn(URL, p["service_urls"])
        self.assertEqual(next(d["source"] for d in p["confirmed_identifiers"] if d["value"] == URL), oauth_service.ADOPTION_SOURCE)
        self.assertEqual(registry.trust_auto_confirm_identifier(p, "service_url", URL)["policy_version"], oauth_service.POLICY_VERSION)
        p["confirmed_identifiers"] = [d for d in p["confirmed_identifiers"] if d["value"] != NSID]
        self.assertFalse(oauth_service.evaluate(p, URL)["auto_confirm"])
        self.assertNotIn({"kind": "service_url", "value": URL}, registry.trusted_anchor_nodes(p))
        self.assertTrue(project_review.compute_review_signals(p))
        self.assertIn(URL, p["service_urls"])

    def test_candidate_only_and_negative_bindings(self):
        for changes in ({"scope": "atproto"}, {"scope": "atproto repo:*"},
                        {"scope": "atproto include:at.atmosynth.permissions"}, {"scope": "atproto repo:org.other.post"}):
            p = supported_project()
            p["oauth_client_metadata"][0]["analysis"] = oauth_metadata.analyze_scope(changes["scope"])
            registry.apply_oauth_service_adoptions(p)
            self.assertIn(URL, project_record.service_url_candidate_values(p))
            self.assertNotIn(URL, p["service_urls"])
        for changes in ({"application_type": "native"}, {"declared_client_uri": "https://other.example"},
                        {"declared_client_id": METADATA + "?wrong"}, {"grant_types": None},
                        {"redirect_uris": ["https://other.example/callback"]}, {"discovery": {}},
                        {"response": {"status_code": 200, "content_type": "text/html", "redirected": False}}):
            p = supported_project()
            p["oauth_client_metadata"][0].update(changes)
            self.assertFalse(oauth_metadata.web_surface(p["oauth_client_metadata"][0]), changes)
            self.assertFalse(oauth_service.evaluate(p, URL)["auto_confirm"])

    def test_login_local_context_and_originating_surface(self):
        linked = {"requested_url": URL, "pages": [page(forms=[], links=[]),
                   page(URL + "/login", auth_entry_from=URL)]}
        result = service.browser_surface_assessment(linked)
        self.assertEqual(result["url"], URL)
        self.assertTrue(result["atproto_evidence"] and result["positive_surface"])
        linked["pages"][1]["auth_entry_from"] = "https://elsewhere.example"
        self.assertFalse(service.browser_surface_assessment(linked)["atproto_evidence"])
        for label in ("Your handle", "Email"):
            value = page()
            value["forms"][0].update(context="Log in", inputs=[{"name": "handle", "label": label, "visible": True}])
            value["text"] = "Follow us on Bluesky"
            self.assertFalse(service.browser_surface_assessment({"pages": [value]})["atproto_evidence"])

    def test_derived_collection_returns_to_independent_root_and_guards_stop_it(self):
        p = supported_project()
        another = "at.atmosynth.module"
        p["input_nsids"].append(another)
        registry.apply_lexicon_record_result(p, another, publication(another, when=project_record.iso_now()))
        p["oauth_client_metadata"][0]["analysis"] = oauth_metadata.analyze_scope("atproto repo:" + another)
        result = oauth_service.evaluate(p, URL)
        self.assertTrue(result["auto_confirm"], result)
        self.assertEqual(result["anchor"]["value"], NSID)
        for blocked in (NSID, another):
            guarded = copy.deepcopy(p)
            guarded["blocked_identifiers"] = [{"type": "nsid", "value": blocked}]
            self.assertFalse(oauth_service.evaluate(guarded, URL)["auto_confirm"])

    def test_exact_client_uri_path_requires_its_own_observed_surface(self):
        p = supported_project()
        p["oauth_client_metadata"][0]["declared_client_uri"] = URL + "/studio"
        registry.reconcile_oauth_service_candidates(p)
        self.assertIn(URL + "/studio", service_discovery.hypotheses(p))
        self.assertFalse(oauth_service.evaluate(p, URL)["auto_confirm"])
        self.assertFalse(oauth_service.evaluate(p, URL + "/studio")["auto_confirm"])
        p["service_verification_results"] = [{"requested_url": URL + "/studio", "lane": "browser_discovery",
            "observed_at": project_record.iso_now(), "pages": [page(URL + "/studio")]}]
        self.assertTrue(oauth_service.evaluate(p, URL + "/studio")["auto_confirm"])

    def test_scheduled_browser_replay_is_candidate_only_even_with_metadata(self):
        p = supported_project()
        p["service_verification_results"] = []
        hypothesis = service_discovery.hypotheses(p)[URL]
        bundle = service.collect_browser_service_evidence(p, URL,
            service.BrowserDependencies(navigate=lambda url: page(url), now=project_record.iso_now),
            lane="browser_discovery", hypothesis=hypothesis)
        registry.apply_replay_evidence(p, bundle)
        self.assertIn(URL, project_record.service_url_candidate_values(p))
        self.assertNotIn(URL, p["service_urls"])
        self.assertTrue(oauth_service.evaluate(p, URL)["auto_confirm"])

    def test_html_metadata_references_and_late_browser_references(self):
        self.assertEqual(relations.oauth_metadata_candidates(URL, [{"url": METADATA}], ""), [METADATA])
        self.assertEqual(relations.oauth_metadata_candidates(URL, [], '<script>{"client_id":"/auth/client.json"}</script>'),
                         [URL + "/auth/client.json"])
        p = supported_project()
        p["oauth_client_metadata"] = []
        p["service_verification_results"][0]["pages"][0]["links"] = [{"url": URL + "/auth/oauth-client.json"}]
        plan = oauth_metadata.discovery_plan(p, URL, [], "", conventional=True, observed_at=project_record.iso_now())
        self.assertEqual(plan[0]["metadata_url"], URL + "/auth/oauth-client.json")

    def test_new_missing_attempt_revokes_support_without_erasing_declaration(self):
        p = supported_project()
        p["oauth_metadata_attempts"] = [{"metadata_url": METADATA, "observed_at": project_record.iso_now(), "status": "missing"}]
        self.assertFalse(oauth_service.evaluate(p, URL)["auto_confirm"])
        self.assertTrue(p["oauth_client_metadata"])

    def test_scope_removal_and_stale_replay_do_not_restore_support(self):
        p = supported_project()
        original = copy.deepcopy(p["oauth_client_metadata"][0])
        newer = {**original, "observed_at": "2099-01-01T00:00:00+00:00", "analysis": oauth_metadata.analyze_scope("atproto")}
        registry.apply_oauth_service_adoptions(p)
        bundle = EvidenceBundle(project_key=p["project_key"], collected_at=project_record.iso_now())
        for obs in (newer, original):
            bundle.add_record(EvidenceRecord(kind="oauth_client_metadata", source="fixture", classification="derived",
                                           observed_at=obs["observed_at"], value=obs))
        registry.apply_replay_evidence(p, bundle)
        self.assertEqual(p["oauth_client_metadata"][0]["analysis"]["permissions"], newer["analysis"]["permissions"])
        self.assertFalse(oauth_service.evaluate(p, URL)["auto_confirm"])
        self.assertTrue(project_review.compute_review_signals(p))

    def test_discovery_is_bounded_and_explicit_reference_bypasses_negative_cache(self):
        p = supported_project()
        p["oauth_client_metadata"] = []
        state = {}
        with mock.patch.object(registry.http_transport, "request_bytes_with_final_url", return_value=
                (404, b"", {}, METADATA)) as fetch:
            registry.collect_service_oauth_evidence(p, URL, {"status_code": 200, "html": "app"}, state)
            self.assertEqual(fetch.call_count, 3)
            registry.collect_service_oauth_evidence(p, "https://other.example", {"status_code": 200}, state)
            self.assertEqual(fetch.call_count, 3)
        plan = oauth_metadata.discovery_plan(p, URL, [{"url": METADATA}], "", conventional=True, observed_at=project_record.iso_now())
        self.assertEqual([i["metadata_url"] for i in plan], [METADATA])
        self.assertEqual(plan[0]["method"], "page_reference")
        with mock.patch.object(registry.http_transport, "request_bytes_with_final_url", return_value=
                (200, json.dumps(metadata()).encode(), {"content-type": "application/json"}, METADATA)) as fetch:
            registry.collect_service_oauth_evidence(p, URL, {"status_code": 200, "links": [{"url": METADATA}]}, {})
            self.assertEqual(fetch.call_count, 1)
            self.assertFalse(fetch.call_args.kwargs["follow_redirects"])
            self.assertEqual(fetch.call_args.kwargs["max_bytes"], 256 * 1024)
        self.assertIn(URL, project_record.service_url_candidate_values(p))


class OAuthServicePersistenceTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)

    def test_save_rechecks_collision_and_roundtrips_adoption(self):
        p = supported_project()
        registry.apply_oauth_service_adoptions(p, self.conn)
        project_record.save_project(self.conn, p)
        loaded = project_record.fetch_project(self.conn, p["project_key"])
        self.assertIn(URL, loaded["service_urls"])
        self.assertTrue(oauth_service.evaluate(loaded, URL)["auto_confirm"])

    def test_normal_enrich_discovers_adopts_and_replays_same_run(self):
        p = supported_project()
        p["oauth_client_metadata"] = []
        p["service_verification_results"] = []
        io = EnrichmentIO(browser=lambda url, **kw: page(url), dns=lambda *args, **kw: [])
        io.page(URL, html="<title>ATMOSYNTH</title><div>Explore patches</div>")
        io.json(METADATA, metadata())
        with io, mock.patch.object(enrichment_pipeline, "should_refresh_lexicon_surface", return_value=False), \
                mock.patch.object(enrichment_pipeline, "should_refresh_recent_record_samples", return_value=False):
            enriched = enrichment_pipeline.enrich_project(self.conn, p)
        self.assertIn(URL, enriched["service_urls"])
        self.assertEqual(io.browser.call_count, 1)
        self.assertEqual([c.args[0] for c in io.http.call_args_list if "metadata.json" in c.args[0]], [METADATA])
        project_record.save_project(self.conn, enriched)
        saved = project_record.fetch_project(self.conn, p["project_key"])
        self.assertTrue(oauth_service.evaluate(saved, URL)["auto_confirm"])
        self.assertTrue(any(source["type"] in {"service_page", "browser_page"}
                            for source in registry.build_llm_evidence_bundle(saved)["description_sources"]))

    def test_existing_adoption_loses_support_as_review_without_downgrade(self):
        p = supported_project()
        registry.apply_oauth_service_adoptions(p, self.conn)
        project_record.save_project(self.conn, p)
        p = project_record.fetch_project(self.conn, p["project_key"])
        p["oauth_client_metadata"][0]["analysis"] = oauth_metadata.analyze_scope("atproto")
        project_record.save_project(self.conn, p)
        saved = project_record.fetch_project(self.conn, p["project_key"])
        self.assertIn(URL, saved["service_urls"])
        self.assertTrue(self.conn.execute("SELECT review_required FROM project_identifier_adoptions "
            "WHERE kind='service_url' AND value=? AND source=?", (URL, oauth_service.ADOPTION_SOURCE)).fetchone()[0])
        self.assertTrue(project_review.compute_review_signals(saved))

    def test_competing_current_service_prevents_adoption(self):
        other = self.make_project("proj:other", "service_url", URL)
        registry.confirm_service_url(other, URL)
        registry.add_confirmed_identifier_decision(other, "service_url", URL, source="operator", role="service")
        project_record.save_project(self.conn, other)
        p = supported_project()
        registry.apply_oauth_service_adoptions(p, self.conn)
        self.assertNotIn(URL, p["service_urls"])
        registry.apply_oauth_service_adoptions(p)
        with self.assertRaisesRegex(ValueError, "no longer supported"):
            project_record.save_project(self.conn, p)
