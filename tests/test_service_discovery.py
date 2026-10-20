from __future__ import annotations

import copy
import datetime as dt
import hashlib
import unittest
from unittest import mock

from helpers import TempRegistryDbTestCase, ProjectRecordTestCase, registry
from project_registry_core import project_record, service_discovery as policy, service, replay
from project_registry_core.application import service_discovery as application, investigation
from project_registry_core.persistence import storage
from enrichment_io import EnrichmentIO
from project_registry_core import enrichment_pipeline, triage_reuse
from project_registry_core import relations, project_merge
from project_registry_core.evidence import EvidenceBundle
from project_registry_core.persistence import runtime_view
from project_registry_core.transport import http_transport

NOW = "2026-10-20T00:00:00+00:00"


def browser_page(url, *, text="Sign in with your PDS", status=200, visible=True):
    return {"url": url, "status_code": status, "text": text, "title": "Sign in",
            "visibility_observed": True, "links": [],
            "forms": [{"visible": visible, "inputs": [{"type": "password", "visible": visible}],
                       "submit_texts": ["Sign in"], "visible_submit_texts": ["Sign in"] if visible else []}]}


class ServiceDiscoveryTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)

    def authority(self, key="proj:discovery", host="deception.town"):
        nsid = ".".join(reversed(host.split("."))) + ".game"
        p = self.make_project(key, "nsid", nsid)
        p["resolved_nsids"] = [nsid]
        p["authority_domains"] = [host]
        p["service_status"] = "none"
        p["last_enriched_at"] = "2026-01-01T00:00:00+00:00"
        p["service_verification_results"] = [{"requested_url": "https://" + host,
            "final_url": "https://" + host, "lane": "authority", "status_code": 200,
            "title": host, "html_extract": '<div id="root"></div>', "content_sha256": hashlib.sha256(b'<div id="root"></div>').hexdigest(),
            "observed_at": NOW, "supports_project": False}]
        self.save(p)
        return project_record.fetch_project(self.conn, key)

    def save(self, p):
        project_record.save_project(self.conn, p, preserve_timestamps=True)
        self.conn.commit()

    def investigate(self, **kwargs):
        return application.investigate(self.conn, execute=True, now=NOW,
            navigate=lambda url, **options: browser_page(url), **kwargs)

    def test_observe_candidate_save_replay_and_next_enrich_without_adopting(self):
        original = self.authority()
        self.assertEqual(original["service_url_candidates"], [])
        result = self.investigate()
        self.assertEqual(result["processed_count"], 1)
        p = project_record.fetch_project(self.conn, original["project_key"])
        self.assertEqual(p["service_url_candidates"], ["https://deception.town"])
        for field in ("service_urls", "website_url", "service_status", "tracking_state", "last_enriched_at"):
            self.assertEqual(p[field], original[field], field)
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 0)
        run = storage.load_investigation_run(self.conn, result["results"][0]["run_id"])
        again = replay.replay_bundle(run["input_project"], run["bundle"], registry.replay_dependencies())
        self.assertEqual(again["service_url_candidates"], p["service_url_candidates"])
        self.assertEqual(again[policy.STATE_FIELD], p[policy.STATE_FIELD])
        evidence = registry.build_llm_evidence_bundle(registry.finalize_project_shape(p))
        self.assertEqual(evidence["unadopted_url_observations"][0]["attribution"], "unconfirmed")
        self.assertFalse(any(v["type"] == "browser_page" for v in evidence["description_sources"]))

    def test_generic_login_hidden_controls_and_error_pages_do_not_add_candidate(self):
        for index, options in enumerate(({"text": "Sign in"}, {"visible": False}, {"status": 404})):
            with self.subTest(options=options):
                p = self.authority(f"proj:negative{index}", f"negative{index}.org")
                p[policy.STATE_FIELD] = []
                p["service_url_candidates"] = []
                self.save(p)
                result = application.investigate(self.conn, execute=True, now=NOW,
                    navigate=lambda url, **kw: browser_page(url, **options))
                saved = project_record.fetch_project(self.conn, p["project_key"])
                self.assertEqual(saved["service_url_candidates"], [])
                self.assertEqual(saved["service_status"], "none")

    def test_visible_continue_to_pds_entry_supports_candidate_without_authentication(self):
        self.authority()
        def navigate(url, **kwargs):
            return {"url": url, "status_code": 200, "visibility_observed": True,
                    "title": "deception.town", "text": "AN ATMOSPHERE MYSTERY HANDLE continue to pds",
                    "forms": [], "auth_actions": [{"kind": "button", "text": "continue to pds", "visible": True}],
                    "links": []}
        result = application.investigate(self.conn, execute=True, now=NOW, navigate=navigate)
        self.assertTrue(result["results"][0]["candidate_supported"])
        saved = project_record.fetch_project(self.conn, "proj:discovery")
        self.assertEqual(saved["service_status"], "none")

    def test_visible_pds_candidate_with_rendering_errors_is_not_confirmed_service(self):
        self.authority()
        def navigate(url, **kwargs):
            return {**browser_page(url), "console_errors": ["backend unavailable"]}
        result = application.investigate(self.conn, execute=True, now=NOW, navigate=navigate)
        self.assertTrue(result["results"][0]["candidate_supported"])
        p = project_record.fetch_project(self.conn, "proj:discovery")
        self.assertTrue(p[policy.STATE_FIELD][0]["assessment"]["rendered_errors"])
        self.assertEqual(p["service_status"], "none")

    def test_failure_retry_and_observation_eviction_do_not_reset_attempt(self):
        p = self.authority()
        result = application.investigate(self.conn, execute=True, now=NOW,
            navigate=mock.Mock(side_effect=TimeoutError("synthetic timeout")))
        self.assertEqual(result["operation_status"], "partial")
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 0)
        later = (dt.datetime.fromisoformat(NOW) + dt.timedelta(days=8)).isoformat()
        self.assertEqual(application.select(self.conn, now=later)["due_count"], 1)
        # Detailed browser observations may be evicted, but the durable attempt remains.
        saved = project_record.fetch_project(self.conn, p["project_key"])
        saved["service_verification_results"] = []
        self.save(saved)
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 0)

    def test_timestamp_update_does_not_reopen_but_content_change_does(self):
        p = self.authority()
        self.investigate()
        p = project_record.fetch_project(self.conn, p["project_key"])
        p["service_verification_results"][0]["observed_at"] = "2026-11-01T00:00:00Z"
        self.save(p)
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 0)
        p = project_record.fetch_project(self.conn, p["project_key"])
        p["service_verification_results"][0]["content_sha256"] = "new-page"
        self.save(p)
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 1)

    def test_non_authority_candidate_uses_content_change_in_retry_fingerprint(self):
        p = self.authority()
        p["resolved_nsids"] = []
        p["input_nsids"] = []
        url = "https://standalone-service.org"
        p["service_url_candidates"] = [url]
        p["service_verification_results"] = [{"requested_url": url, "final_url": url,
            "lane": "static", "status_code": 200, "content_sha256": "first", "observed_at": NOW}]
        self.save(p)
        self.investigate()
        p = project_record.fetch_project(self.conn, p["project_key"])
        p["service_verification_results"][0]["observed_at"] = "2026-12-01T00:00:00Z"
        self.save(p)
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 0)
        p = project_record.fetch_project(self.conn, p["project_key"])
        p["service_verification_results"][0]["content_sha256"] = "second"
        self.save(p)
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 1)

    def test_budget_cancellation_and_individual_failure_do_not_fetch_other_urls(self):
        self.authority("proj:a", "one.testsite.org")
        self.authority("proj:b", "two.testsite.org")
        navigate = mock.Mock(side_effect=TimeoutError("synthetic"))
        result = application.investigate(self.conn, execute=True, now=NOW, navigate=navigate, limit=1)
        self.assertEqual(result["processed_count"], 1)
        self.assertEqual(result["stop_reason"], "url_budget")
        self.assertEqual(result["remaining_due_count"], 1)
        navigate.reset_mock()
        result = application.investigate(self.conn, execute=True, now=NOW, navigate=navigate,
            cancel_requested=lambda: True)
        self.assertEqual(result["operation_status"], "cancelled")
        navigate.assert_not_called()
        times = iter([0, 2, 2])
        result = application.investigate(self.conn, execute=True, now=NOW, navigate=navigate,
            budget_seconds=1, clock=lambda: next(times))
        self.assertEqual(result["stop_reason"], "time_budget")
        navigate.assert_not_called()

    def test_public_role_filters_do_not_crawl_reference_urls_or_unprobed_authorities(self):
        p = self.authority()
        p["service_verification_results"] = []
        p["evidence_urls"] = ["https://reference.org"]
        p["service_url_candidates"] = ["http://127.0.0.1:3000", "https://docs.rs/package", "https://pypi.org/project/foo",
                                        "https://bsky.app/profile/foo", "https://real-service.org"]
        self.save(p)
        result = application.select(self.conn, now=NOW)
        self.assertEqual([v["url"] for v in result["selected"]], ["https://real-service.org"])

    def test_guard_and_other_project_confirmed_url_hold_research(self):
        p = self.authority()
        p["blocked_identifiers"] = [{"type": "service_url", "value": "https://deception.town", "created_at": NOW}]
        self.save(p)
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 0)
        p = project_record.fetch_project(self.conn, p["project_key"])
        p["blocked_identifiers"] = []
        self.save(p)
        other = self.make_project("proj:other", "service_url", "https://deception.town")
        other["service_urls"] = ["https://deception.town"]
        self.save(other)
        result = application.select(self.conn, now=NOW)
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["excluded_counts"]["ownership_conflict_or_noncurrent"], 1)

    def test_composition_continues_after_partial_monitor_and_keeps_budget_separate(self):
        with mock.patch.object(service, "ensure_browser_available"), \
             mock.patch.object(investigation, "monitor_services", return_value={"operation_status": "partial"}) as monitor, \
             mock.patch.object(application, "investigate", return_value={"operation_status": "completed"}) as discover:
            result = investigation.observe_services(self.conn, execute=True, discovery_limit=123, discovery_budget_seconds=600)
        self.assertEqual(result["operation_status"], "completed")
        self.assertEqual(result["outcome"], "partial")
        self.assertNotIn("limit", monitor.call_args.kwargs)
        self.assertEqual(discover.call_args.kwargs["limit"], 123)
        self.assertEqual(discover.call_args.kwargs["budget_seconds"], 600)

    def test_browser_candidate_does_not_project_live_but_confirmed_still_does(self):
        p = self.authority()
        p["service_verification_results"] = [{"requested_url": "https://deception.town", "lane": "browser",
            "observed_at": NOW, "supports_project": False, "login_surface_reached": True}]
        self.assertIsNone(registry.project_current_service_observation(p))
        p["service_urls"] = ["https://deception.town"]
        self.assertEqual(registry.project_current_service_observation(p)["service_status"], "live")

    def test_normal_enrich_consumes_saved_unadopted_observation_and_keeps_candidate(self):
        p = self.authority()
        self.investigate()
        p = project_record.fetch_project(self.conn, p["project_key"])
        io = EnrichmentIO(dns=lambda *args, **kwargs: [])
        io.page("https://deception.town", html='<div id="root"></div>')
        io.json(registry.UFOS_API + "/records?collection=town.deception.game", [])
        with io:
            result = enrichment_pipeline.enrich_project(self.conn, p)
        self.save(result)
        saved = project_record.fetch_project(self.conn, p["project_key"])
        self.assertIn("https://deception.town", saved["service_url_candidates"])
        self.assertEqual(saved["service_urls"], [])
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 0)
        self.assertEqual(registry.build_llm_evidence_bundle(saved)["unadopted_url_observations"][0]["url"], "https://deception.town")
        io.browser.assert_not_called()

    def test_source_removal_revokes_our_candidate_but_guard_keeps_attempt_history(self):
        p = self.authority()
        self.investigate()
        p = project_record.fetch_project(self.conn, p["project_key"])
        p["deferred_identifiers"] = [{"type": "service_url", "value": "https://deception.town",
                                      "expires_at": "2099-01-01T00:00:00Z"}]
        p = registry.finalize_project_shape(p)
        self.assertEqual(len(p[policy.STATE_FIELD]), 1)
        p["deferred_identifiers"] = []
        p["resolved_nsids"] = []
        p["input_nsids"] = []
        p = registry.finalize_project_shape(p)
        self.assertNotIn("https://deception.town", p["service_url_candidates"])
        self.assertEqual(p[policy.STATE_FIELD], [])

    def test_new_observation_changes_triage_input_without_becoming_description_evidence(self):
        p = self.authority()
        before = registry.build_llm_evidence_bundle(p)
        self.investigate()
        p = project_record.fetch_project(self.conn, p["project_key"])
        after = registry.build_llm_evidence_bundle(p)
        self.assertNotEqual(triage_reuse.semantic_evidence(before), triage_reuse.semantic_evidence(after))
        self.assertEqual(before["description_sources"], after["description_sources"])

    def test_repository_placement_requires_current_adoption_and_unknown_page_role(self):
        repo, url = "https://github.com/example/app", "https://demo-service.org"
        p = self.make_project("proj:repo", "repo_url", repo)
        registry.add_manual_seed(p, "repo_url", repo, source="test")
        bundle = EvidenceBundle(project_key=p["project_key"], collected_at=NOW)
        bundle.add_record(relations.relation_record(source="github_repo_metadata:" + repo,
            classification="confirmed", observed_at=NOW, subject_type="repo", subject_value=repo,
            predicate="repository_homepage", object_type="website", object_value=url,
            locator=repo, method="github_homepage"))
        registry.apply_github_repo_evidence(p, bundle)
        self.save(p)
        p = project_record.fetch_project(self.conn, p["project_key"])
        self.assertEqual(policy.hypotheses(p)[url]["group"], "repository")
        p[runtime_view.RUNTIME_ADOPTION_ROWS] = []
        self.assertEqual(policy.hypotheses(p), {})

    def test_cancellation_during_navigation_does_not_mark_attempt_complete(self):
        self.authority()
        cancelled = False
        def navigate(url, **kwargs):
            nonlocal cancelled
            cancelled = True
            return browser_page(url)
        result = application.investigate(self.conn, execute=True, now=NOW, navigate=navigate,
                                        cancel_requested=lambda: cancelled)
        self.assertEqual(result["operation_status"], "cancelled")
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM investigation_runs").fetchone()[0], 0)

    def test_concurrent_project_edit_keeps_unapplied_history_and_reselects(self):
        p = self.authority()
        def navigate(url, **kwargs):
            current = project_record.fetch_project(self.conn, p["project_key"])
            current["name"] = "Edited during browser observation"
            self.save(current)
            return browser_page(url)
        result = application.investigate(self.conn, execute=True, now=NOW, navigate=navigate)
        self.assertEqual(result["results"][0]["status"], "conflicted")
        self.assertEqual(application.select(self.conn, now=NOW)["due_count"], 1)
        self.assertEqual(self.conn.execute("SELECT application_status FROM investigation_runs").fetchone()[0], "unapplied")
        self.assertEqual(project_record.fetch_project(self.conn, p["project_key"])[policy.STATE_FIELD], [])

    def test_common_dependency_failure_is_not_saved_as_many_url_failures(self):
        self.authority()
        with mock.patch.object(service, "ensure_browser_available", side_effect=RuntimeError("Chromium missing")):
            with self.assertRaisesRegex(RuntimeError, "Chromium missing"):
                application.investigate(self.conn, execute=True, now=NOW)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM investigation_runs").fetchone()[0], 0)

    def test_repeated_unattributed_candidates_do_not_crawl_shared_reference_hosts(self):
        for key in ("proj:one", "proj:two"):
            p = self.make_project(key)
            p["service_url_candidates"] = ["https://shared-service.org"]
            self.save(p)
        result = application.select(self.conn, now=NOW)
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["excluded_counts"]["shared_unattributed_candidate"], 2)

    def test_new_owner_during_navigation_keeps_observation_unapplied(self):
        self.authority()
        def navigate(url, **kwargs):
            other = self.make_project("proj:new-owner", "service_url", url)
            other["service_urls"] = [url]
            self.save(other)
            return browser_page(url)
        result = application.investigate(self.conn, execute=True, now=NOW, navigate=navigate)
        self.assertEqual(result["results"][0]["status"], "conflicted")
        self.assertEqual(self.conn.execute("SELECT application_status FROM investigation_runs").fetchone()[0], "unapplied")
        self.assertEqual(project_record.fetch_project(self.conn, "proj:discovery")["service_url_candidates"], [])

    def test_merge_keeps_latest_attempt_and_last_success_independently(self):
        target, source = self.make_project("proj:target"), self.make_project("proj:source")
        success = {"observed_at": NOW, "assessment": {"positive_surface": True}}
        target[policy.STATE_FIELD] = [{"url": "https://service.org", "attempted_at": NOW,
                                       "status": "observed", "last_success": success}]
        source[policy.STATE_FIELD] = [{"url": "https://service.org", "attempted_at": "2026-11-01T00:00:00Z",
                                       "status": "failed", "last_success": {}}]
        merged = project_merge.combine_records(target, source)
        self.assertEqual(merged[policy.STATE_FIELD][0]["status"], "failed")
        self.assertEqual(merged[policy.STATE_FIELD][0]["last_success"], success)


class BrowserTransportTest(unittest.TestCase):
    def test_shared_transport_checks_navigation_redirect_subresource_and_websocket(self):
        playwright = mock.MagicMock()
        page = playwright.chromium.launch.return_value.new_page.return_value
        page.goto.return_value.status = 200
        page.url = "https://public-service.org"
        page.title.return_value = "Service"
        page.locator.return_value.evaluate_all.return_value = []
        page.locator.return_value.inner_text.return_value = "Hello"
        with mock.patch.object(http_transport, "outbound_host_is_blocked", side_effect=lambda h: h == "127.0.0.1"):
            result = service.playwright_page_payload(playwright, page.url, timeout_ms=100)
            self.assertEqual(result["status_code"], 200)
            http_route = page.context.route.call_args.args[1]
            socket_route = page.context.route_web_socket.call_args.args[1]
            private = mock.Mock()
            private.request.url = "http://127.0.0.1/private"
            http_route(private)
            private.abort.assert_called_once_with("blockedbyclient")
            private.continue_.assert_not_called()
            private.url = "ws://127.0.0.1/socket"
            socket_route(private)
            private.close.assert_called_once()
            private.connect_to_server.assert_not_called()
            public = mock.Mock()
            public.request.url = "https://public-service.org/asset.js"
            http_route(public)
            public.continue_.assert_called_once()
            playwright.chromium.launch.reset_mock()
            with self.assertRaises(ValueError):
                service.playwright_page_payload(playwright, "http://127.0.0.1", timeout_ms=100)
            playwright.chromium.launch.assert_not_called()
