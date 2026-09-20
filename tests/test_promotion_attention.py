from __future__ import annotations

import datetime as dt
import contextlib

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record, promotion_attention
from project_registry_core.application import decisions, presentation


class PromotionAttentionTest(TempRegistryDbTestCase):
    def candidate(self, key):
        project = self.make_project(key, "service_url", "https://app.example")
        project.update(category="project", service_status="live", service_status_confidence="medium",
                       service_status_evidence=["http_200", "login_surface_detected"])
        registry.confirm_service_seed(project, "https://app.example", source="test")
        return project_record.ensure_project_shape(project)

    def test_fifty_reviewed_candidates_make_room_without_excluding_them(self):
        projects = [self.candidate(f"proj:{n:012d}") for n in range(55)]
        first = registry.build_showcase_queue_from_projects(projects)
        self.assertEqual(len(first), 50)
        for project in projects[:50]:
            promotion_attention.record(project, "reviewed")
        next_queue = registry.build_showcase_queue_from_projects(projects)
        self.assertEqual([item["project_key"] for item in next_queue[:5]], [p["project_key"] for p in projects[50:]])
        inbox = registry.build_showcase_queue_from_projects(projects, view="inbox")
        self.assertEqual(len(inbox), 5)
        self.assertEqual(len(registry.build_showcase_queue_from_projects(projects, view="all", limit=None)), 55)
        promotion_attention.record(projects[0], "restore")
        self.assertEqual(len(registry.build_showcase_queue_from_projects(projects, view="inbox")), 6)

    def test_fingerprint_ignores_refresh_but_detects_material_change_and_expiry(self):
        project = self.candidate("proj:attention")
        now = dt.datetime.now(dt.timezone.utc)
        promotion_attention.record(project, "reviewed", now=now)
        original = promotion_attention.fingerprint(project)
        project.update(last_seen_at="2099-01-01", last_enriched_at="2099-01-01", summary="A wording revision",
                       collection_observations=[{"recent_count": 500}])
        # First material becoming available is a change; subsequent prose is not.
        self.assertNotEqual(original, promotion_attention.fingerprint(project))
        promotion_attention.record(project, "reviewed", now=now)
        project["summary"] = "Same purpose, different wording"
        self.assertEqual(promotion_attention.projection(project, now=now)["group"], "reviewed")
        self.assertEqual(promotion_attention.projection(project, now=now + dt.timedelta(days=31))["group"], "due")
        project["features"] = ["new record interoperability"]
        self.assertEqual(promotion_attention.projection(project, now=now)["group"], "changed")

    def test_defer_reappears_on_change_preserves_legacy_and_history(self):
        with contextlib.closing(registry.open_db()) as conn:
            project = self.candidate("proj:deferred")
            project_record.save_project(conn, project)
            decisions.set_promotion_defer(conn, project_key=project["project_key"], reason="公開待ち", days=30)
            saved = project_record.fetch_project(conn, project["project_key"])
            self.assertIsNotNone(registry.active_promotion_deferral(saved))
            saved["last_enriched_at"] = "2099-01-01"
            self.assertIsNotNone(registry.active_promotion_deferral(saved))
            saved["service_status"] = "beta"
            project_record.save_project(conn, saved)
            saved = project_record.fetch_project(conn, project["project_key"])
            self.assertIsNone(registry.active_promotion_deferral(saved))
            self.assertEqual(registry.build_showcase_queue_from_projects([saved], view="inbox")[0]["attention"]["group"], "changed")
            self.assertEqual(len(saved["promotion_deferrals"]), 1)
            # Existing decisions with no fingerprint retain their time-only contract.
            saved["promotion_deferrals"][0].pop("fingerprint")
            self.assertIsNotNone(registry.active_promotion_deferral(saved))
            decisions.set_promotion_defer(conn, project_key=project["project_key"], reason="再確認待ち", days=30)
            again = project_record.fetch_project(conn, project["project_key"])
            self.assertTrue(again["promotion_deferrals"][0]["cleared_at"])
            self.assertEqual(again["promotion_attention"]["outcome_counts"], {"defer": 2})

    def test_saved_state_survives_roundtrip_and_read_only_refresh(self):
        with contextlib.closing(registry.open_db()) as conn:
            project = self.candidate("proj:roundtrip")
            project_record.save_project(conn, project)
            decisions.set_promotion_reviewed(conn, project_key=project["project_key"])
            saved = project_record.fetch_project(conn, project["project_key"])
            attention = saved["promotion_attention"]
            revision = presentation.project_revision(conn, project["project_key"])
            for _ in range(3):
                payload = presentation.queue_payload(conn, queue="promotion", tracking_states={"active"})
                self.assertEqual(payload["total"], 0)
            self.assertEqual(presentation.project_revision(conn, project["project_key"]), revision)
            project_record.save_project(conn, saved)
            self.assertEqual(project_record.fetch_project(conn, project["project_key"])["promotion_attention"], attention)
            decisions.set_promotion_reviewed(conn, project_key=project["project_key"], clear=True)
            self.assertEqual(presentation.queue_payload(conn, queue="promotion", tracking_states={"active"})["total"], 1)

    def test_backlog_search_counts_and_pagination_include_candidates_without_signal(self):
        with contextlib.closing(registry.open_db()) as conn:
            for n in range(3):
                project = self.candidate(f"proj:backlog{n}")
                registry.set_promotion_priority_on_project(project, lowered=True, reason="認証のみ" if n < 2 else "共有機能")
                # A feature backlog must remain recoverable after recommendation disappears.
                project.update(service_status="unknown", tracking_state="out_of_scope")
                project_record.save_project(conn, project)
            payload = presentation.queue_payload(conn, queue="promotion", tracking_states={"active"},
                promotion_view="lowered", reason="認証", limit=1)
            self.assertEqual((payload["total"], payload["count"], payload["has_more"]), (2, 1, True))
            self.assertIn({"reason": "認証のみ", "count": 2}, payload["reasons"])
            next_page = presentation.queue_payload(conn, queue="promotion", tracking_states={"active"},
                promotion_view="lowered", reason="認証", limit=1, offset=1)
            self.assertNotEqual(payload["items"][0]["project_key"], next_page["items"][0]["project_key"])
            self.assertFalse(next_page["has_more"])

    def test_material_schema_and_identifier_changes_reopen_review(self):
        project = self.candidate("proj:schema")
        project["resolved_nsids"] = ["app.example.record"]
        project["lexicon_schema_samples"] = [{"nsid":"app.example.record", "defs":{"main":{"type":"record"}}}]
        promotion_attention.record(project, "reviewed")
        project["lexicon_schema_samples"][0]["observed_at"] = "2099-01-01"
        self.assertEqual(promotion_attention.projection(project)["group"], "reviewed")
        project["lexicon_schema_samples"][0]["defs"]["extra"] = {"type":"string"}
        self.assertEqual(promotion_attention.projection(project)["group"], "changed")

    def test_kind_filter_keeps_existing_score_scope(self):
        project = self.candidate("proj:technical")
        project.update(technical_interest_level="high", technical_interest_reasons=["novel_design_pattern"],
                       technical_interest_summary="Distinctive protocol design")
        item = registry.build_showcase_queue_from_projects([project], kinds={"technical_insight_candidate"})[0]
        self.assertEqual(item["service_evidence_group"], "not_applicable")
        self.assertEqual(item["score_breakdown"]["components"]["service_evidence"], 0)
