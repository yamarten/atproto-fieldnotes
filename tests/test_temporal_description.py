from __future__ import annotations

from unittest import mock

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry

from project_registry_core.application import review as application_review
from project_registry_core import editorial, project_record, service, storage
from project_registry_core.application import investigation, presentation, projects
from project_registry_core import worker
from project_registry_core.jobs import CancellationToken, OperationRequest
from project_registry_web.jobs import SCHEDULE_PRESETS, preset_steps
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord


class _TemporalDescriptionFixtures:
    URL = "https://reading.example"

    OLD = "2026-07-01T00:00:00+00:00"

    NEW = "2026-09-14T00:00:00+00:00"

    def project(self, state="active"):
        project = self.make_project("proj:temporal", "service_url", self.URL)
        project.update(summary="読書記録を共有する。独自Lexiconで書籍を表す。", tagline="読書記録共有",
                       features=["読書記録共有", "独自Lexicon"], service_urls=[self.URL],
                       tracking_state=state, editorial={"mode": "automatic"})
        return project

    def browser(self, project, *, at=OLD, positive=True, error=False):
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at=at)
        bundle.add_record(EvidenceRecord(kind="service_verification", source=f"service_browser:{self.URL}",
            classification="candidate" if error else "confirmed", observed_at=at,
            value={"lane": "browser", "requested_url": self.URL, "observed_at": at,
                   "public_app_surface_detected": positive, "fetch_error": "browser_error" if error else "", "pages": []}))
        registry.apply_service_evidence(project, bundle)

    def propose(self, project, summary, *, kind="changed_facts", content="2026年に提供終了。", sources=True, features=None):
        material = editorial.source("service_page", self.URL, content, self.NEW)
        output = {"summary": summary, "description_change": {"kind": kind, "reason": content,
                  "sources": [material["id"] if sources else "invented"]}}
        if features is not None:
            output["features"] = features
        registry.apply_llm_output(project, output, {"generated_at": self.NEW}, {"description_sources": [material]})


class TemporalDescriptionUnitTest(_TemporalDescriptionFixtures, ProjectRecordTestCase):
    def test_closure_keeps_purpose_and_technical_value_in_single_description(self):
        project = self.project()
        summary = "読書記録を共有していたサービス。独自Lexiconで書籍を表現していた。2026年に提供終了。"
        self.propose(project, summary, features=["独自Lexiconによる書籍表現"])
        self.assertEqual(project["summary"], summary)
        self.assertEqual(project["features"], ["独自Lexiconによる書籍表現"])
        self.assertNotIn("proposal", project["editorial"])
        self.assertNotIn("history", project["editorial"])

    def test_feature_change_replaces_obsolete_feature_and_preserves_useful_former_role(self):
        project = self.project()
        summary = "現在は書籍検索を提供する。当初の読書記録共有で用いた独自Lexiconも公開している。"
        self.propose(project, summary, content="読書共有を終了し、現在は書籍検索に特化。", features=["書籍検索", "旧読書共有のLexicon公開"])
        self.assertEqual(project["summary"], summary)
        self.assertNotIn("読書記録共有", project["features"])

    def test_original_error_is_corrected_without_inventing_historical_feature(self):
        project = self.project()
        self.propose(project, "書籍を表すLexicon。共有サービスという従来の記述は誤り。", kind="correction", content="公式仕様にはLexicon定義のみ。", features=["書籍Lexicon定義"])
        self.assertEqual(project["editorial"]["last_update"]["kind"], "correction")
        self.assertEqual(project["features"], ["書籍Lexicon定義"])

    def test_invalid_reference_or_wording_only_cannot_replace_description(self):
        for kind, refs in [("wording_only", True), ("changed_facts", False), ("insufficient_evidence", True)]:
            with self.subTest(kind=kind, refs=refs):
                project = self.project()
                before = editorial.profile(project)
                self.propose(project, "取得できないため不明。" * 30, kind=kind, sources=refs)
                self.assertEqual(editorial.profile(project), before)

    def test_old_browser_success_does_not_override_new_http_failure(self):
        for status in [403, 410, 503]:
            with self.subTest(status=status):
                project = self.project()
                original = editorial.profile(project)
                self.browser(project)
                bundle = service.collect_service_probe_evidence(project["project_key"], self.URL,
                    {"status_code": status, "title": "", "description": "", "final_url": self.URL,
                     "service_status": "private" if status == 403 else "dead" if status == 410 else "unverified",
                     "service_status_confidence": "medium", "service_status_evidence": [f"http_{status}"],
                     "links": [], "urls": [], "html": ""}, observed_at=self.NEW, supports_project=False)
                registry.apply_service_evidence(project, bundle)
                self.assertEqual(project["service_status"], "unverified")
                self.assertEqual(editorial.profile(project), original)
                browser = next(item for item in project["service_verification_results"] if item["lane"] == "browser")
                self.assertEqual(browser["last_success"]["observed_at"], self.OLD)

    def test_failed_browser_attempt_keeps_success_and_reactivation_does_not_change_tracking(self):
        project = self.project("stable")
        original = editorial.profile(project)
        self.browser(project)
        self.browser(project, at=self.NEW, positive=False, error=True)
        self.assertEqual(project["service_status"], "unverified")
        result = project["service_verification_results"][0]
        self.assertEqual(result["last_success"]["observed_at"], self.OLD)
        self.assertEqual(result["surface_change"], "surface_not_confirmed")
        self.browser(project, at="2026-09-15T00:00:00+00:00")
        self.assertEqual(project["service_status"], "live")
        self.assertEqual(project["tracking_state"], "stable")
        self.assertEqual(editorial.profile(project), original)
        self.assertEqual(project["service_verification_results"][0]["surface_change"], "surface_reached")

    def test_replayed_older_observation_does_not_replace_latest(self):
        project = self.project()
        self.browser(project, at=self.NEW, positive=False, error=True)
        self.browser(project, at=self.OLD)
        self.assertEqual(project["service_verification_results"][0]["observed_at"], self.NEW)
        self.assertEqual(project["service_status"], "unverified")

    def test_browser_exception_is_saved_as_attempt_not_silent_absence(self):
        project = self.project()
        self.browser(project)
        bundle = service.collect_browser_service_evidence(project, self.URL, service.BrowserDependencies(
            navigate=mock.Mock(side_effect=TimeoutError("test timeout")), now=lambda: self.NEW))
        registry.apply_service_evidence(project, bundle)
        self.assertEqual(project["service_verification_results"][0]["observed_at"], self.NEW)
        self.assertEqual(project["service_verification_results"][0]["last_success"]["observed_at"], self.OLD)

    def test_real_source_material_changes_on_same_url_and_excludes_failures(self):
        project = self.project()
        good = {"lane": "static", "requested_url": self.URL, "supports_project": True,
                "observed_at": self.OLD, "status_code": 200, "html_extract": "読書を共有するサービス。"}
        project["service_verification_results"] = [good]
        old_sources = registry.build_llm_evidence_bundle(project)["description_sources"]
        self.assertEqual(len(old_sources), 1)
        project["service_verification_results"] = [{**good, "html_extract": "書籍検索へ変更しました。", "observed_at": self.NEW}]
        new_sources = registry.build_llm_evidence_bundle(project)["description_sources"]
        self.assertNotEqual(old_sources[0]["id"], new_sources[0]["id"])
        self.assertEqual(old_sources[0]["locator"], new_sources[0]["locator"])
        for record in [{**good, "fetch_error": "timeout"}, {**good, "status_code": 503}, {**good, "supports_project": False}]:
            project["service_verification_results"] = [record]
            self.assertFalse(registry.build_llm_evidence_bundle(project)["description_sources"])

    def test_dismissal_stops_same_proposal_and_new_evidence_can_reopen_it(self):
        project = self.project()
        project["editorial"]["mode"] = "review"
        self.propose(project, "提供終了した読書共有サービス。")
        editorial.dismiss_proposal(project)
        self.propose(project, "提供終了した読書共有サービス。")
        self.assertNotIn("proposal", project["editorial"])
        self.propose(project, "再公開された読書共有サービス。", content="提供を再開しました。")
        self.assertIn("proposal", project["editorial"])


class TemporalDescriptionTest(_TemporalDescriptionFixtures, TempRegistryDbTestCase):
    def test_unknown_provenance_is_reviewed_and_proposal_survives_roundtrip(self):
        project = self.project()
        project.pop("editorial")
        before = editorial.profile(project)
        self.propose(project, "読書記録を共有していたサービス。2026年に提供終了。")
        self.assertEqual(editorial.profile(project), before)
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            loaded = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(loaded["editorial"], project["editorial"])
            queue = application_review.review_queue(conn, kinds={"editorial_refresh"})
            self.assertEqual(queue[0]["project_key"], project["project_key"])
            detail = presentation.project_detail(conn, project_key=project["project_key"])
            self.assertFalse(detail["project"]["editorial"]["automatic"])
            self.assertEqual(detail["project"]["editorial"]["proposal"]["kind"], "changed_facts")
            accepted = project["editorial"]["proposal"]["profile"]
            projects.apply_profile_edit(conn, project_key=project["project_key"], name="Reading", notes="", introduced_url="", description=accepted)
            loaded = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(editorial.profile(loaded), accepted)
            self.assertEqual(loaded["editorial"]["mode"], "review")
            self.assertNotIn("proposal", loaded["editorial"])
            self.assertEqual(loaded["editorial"]["last_update"]["kind"], "operator_accepted_proposal")
            self.propose(loaded, "さらに変更された説明。")
            self.assertEqual(editorial.profile(loaded), accepted)
        finally:
            conn.close()

    def test_monitor_is_bounded_dry_run_and_includes_agreed_states(self):
        conn = registry.open_db()
        try:
            for index, state in enumerate(["active", "stable", "stalled", "out_of_scope", "ignore", "archived"]):
                project = self.project(state)
                project["project_key"] = f"proj:monitor{index}"
                project_record.save_project(conn, project)
            conn.commit()
            with mock.patch.object(registry, "collect_service_verification", side_effect=AssertionError("dry-run fetched")):
                preview = investigation.monitor_services(conn, now=self.NEW, limit=2)
            self.assertEqual(preview["due_count"], 4)
            self.assertEqual(len(preview["selected"]), 2)
            self.assertEqual({p["tracking_state"] for p in investigation.monitor_services(conn, now=self.NEW)["selected"]},
                             {"active", "stable", "stalled", "out_of_scope"})
            def collect(project, url, lane):
                return service.collect_browser_service_evidence(project, url, service.BrowserDependencies(
                    navigate=mock.Mock(side_effect=TimeoutError("offline fixture")), now=lambda: self.NEW))
            with mock.patch.object(registry, "collect_service_verification", side_effect=collect) as collector:
                result = investigation.monitor_services(conn, execute=True, now=self.NEW, limit=2)
                self.assertEqual(collector.call_count, 2)
            self.assertEqual(result["operation_status"], "partial")
            self.assertEqual(investigation.monitor_services(conn, now=self.NEW)["due_count"], 2)
            for item in result["results"]:
                run = storage.load_investigation_run(conn, item["run_id"])
                self.assertEqual(run["output_project"]["summary"], self.project()["summary"])
        finally:
            conn.close()

    def test_service_only_verification_does_not_refresh_enrichment_age(self):
        project = self.project()
        project["last_enriched_at"] = self.OLD
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project, preserve_timestamps=True)
            conn.commit()
            project = project_record.fetch_project(conn, project["project_key"])
            with mock.patch.object(registry, "collect_service_verification", return_value=EvidenceBundle(project_key=project["project_key"], collected_at=self.NEW)):
                registry.verify_service_project(conn, project, lane="browser", execute=True)
            self.assertEqual(project_record.fetch_project(conn, project["project_key"])["last_enriched_at"], self.OLD)
        finally:
            conn.close()

    def test_disabled_preset_dispatches_shared_monitor_and_handles_cancellation(self):
        self.assertFalse(SCHEDULE_PRESETS["service_observation"]["default_schedule"]["enabled"])
        step = preset_steps("service_observation")[0]
        conn = registry.open_db()
        try:
            project_record.save_project(conn, self.project())
            conn.commit()
            request = OperationRequest.create(step["operation"], parameters=step["parameters"], execute=False)
            events = []
            result = worker._run_simple(conn, request, CancellationToken(), events.append)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.data["summary"]["due_count"], 1)
            with mock.patch.object(registry, "collect_service_verification", side_effect=AssertionError("cancelled run fetched")):
                self.assertEqual(investigation.monitor_services(conn, execute=True, cancel_requested=lambda: True)["operation_status"], "cancelled")
        finally:
            conn.close()

    def test_out_of_scope_observation_change_reaches_normal_review_without_reactivation(self):
        project = self.project("out_of_scope")
        self.browser(project, positive=False)
        self.browser(project, at=self.NEW)
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            queue = application_review.review_queue(conn)
            self.assertEqual(queue[0]["project_key"], project["project_key"])
            self.assertEqual([signal["kind"] for signal in queue[0]["signals"]], ["service_observation_changed"])
            self.assertEqual(project_record.fetch_project(conn, project["project_key"])["tracking_state"], "out_of_scope")
        finally:
            conn.close()
