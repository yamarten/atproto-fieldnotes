from __future__ import annotations

import contextlib
import copy

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry
import promotion_outcome_analysis
from project_registry_core import project_record, project_review
from project_registry_core.application import presentation, review


class _PromotionReadinessFixtures:
    def profile(self):
        project = self.make_project("proj:readiness", "service_url", "https://example.com")
        project.update({
            "name": "Example",
            "summary": "A service described by its operator.",
            "tagline": "A concise description",
            "features": [],
            "llm_source": {},
            "category": "project",
            "service_status": "live",
            "service_status_confidence": "high",
            "service_status_evidence": ["http_200", "login_surface_detected"],
        })
        registry.confirm_service_seed(project, "https://example.com", source="test")
        return project


class PromotionReadinessTest(_PromotionReadinessFixtures, ProjectRecordTestCase):
    def test_manual_profile_needs_neither_llm_history_nor_feature_count(self):
        for count in (0, 1, 2, 3):
            for llm_source in ({}, {"model": "fixture"}):
                with self.subTest(features=count, llm_source=llm_source):
                    project = self.profile()
                    project.update(features=[f"Capability {i}" for i in range(count)], llm_source=llm_source)
                    evaluated = project_review.apply_promotion_state(project)
                    self.assertTrue(evaluated["promotion_ready"])
                    self.assertEqual(evaluated["promotion_blockers"], [])
                    self.assertEqual(evaluated["status"], "published")
                    kinds = {item["kind"] for item in project_review.compute_review_signals(project)}
                    self.assertNotIn("service_now_usable_but_needs_editorial_review", kinds)
                    queue = review.build_showcase_queue_from_projects([project], limit=None)
                    self.assertEqual(len(queue), 1)
                    self.assertEqual(queue[0]["score_breakdown"]["components"]["promotion_blockers"], 0)
                    service = next(item for item in queue[0]["signals"] if item["kind"] == "service_surface_confirmed")
                    self.assertEqual(service["details"]["blockers"], [])

    def test_missing_description_still_requires_editorial_review_and_lowers_score(self):
        project = self.profile()
        project.update(summary="", tagline="")
        project_review.apply_promotion_state(project)
        self.assertFalse(project["promotion_ready"])
        self.assertEqual(set(project["promotion_blockers"]), {"summary missing", "tagline missing"})
        signals = project_review.compute_review_signals(project)
        editorial = next(item for item in signals if item["kind"] == "service_now_usable_but_needs_editorial_review")
        self.assertEqual(set(editorial["details"]["blockers"]), {"summary missing", "tagline missing"})
        queue = review.build_showcase_queue_from_projects([project], limit=None)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["score_breakdown"]["components"]["promotion_blockers"], -2)

    def test_old_blockers_do_not_penalize_score_or_outcome_analysis(self):
        project = self.profile()
        project.update(introduced_url="https://example.org/introduction",
                       promotion_blockers=["LLM review missing", "fewer than 3 features"])
        before = copy.deepcopy(project)
        signals = project_review.compute_showcase_signals(project, include_ineligible=True)
        score = project_review.promotion_queue_score_breakdown(project, signals)
        self.assertEqual(score["components"]["promotion_blockers"], 0)
        analysis = promotion_outcome_analysis.analyze_projects([project], details="all")
        self.assertEqual(analysis["projects"][0]["promotion_blockers"], [])
        self.assertEqual(project, before)


class PromotionReadinessStorageTest(_PromotionReadinessFixtures, TempRegistryDbTestCase):
    def test_legacy_blockers_are_recomputed_in_detail_queue_and_saved_evaluation(self):
        project = self.profile()
        legacy = ["LLM review missing", "fewer than 3 features"]
        project.update(status="draft", promotion_ready=False, promotion_blockers=legacy)
        with contextlib.closing(registry.open_db()) as conn:
            project_record.save_project(conn, project)
            conn.commit()
            detail = presentation.project_detail(conn, project_key=project["project_key"])
            self.assertTrue(detail["evaluation"]["promotion_ready"])
            self.assertEqual(detail["evaluation"]["promotion_blockers"], [])
            queue = review.promotion_queue(conn, limit=None)
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0]["status"], "published")
            self.assertEqual(queue[0]["score_breakdown"]["components"]["promotion_blockers"], 0)
            stored = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(stored["promotion_blockers"], legacy)  # Viewing does not migrate the DB.
            project_review.apply_promotion_state(stored)
            project_record.save_project(conn, stored)
            conn.commit()
            restored = project_record.fetch_project(conn, project["project_key"])
            self.assertTrue(restored["promotion_ready"])
            self.assertEqual(restored["promotion_blockers"], [])
            self.assertEqual(restored["features"], [])
            self.assertFalse(restored["llm_source"])
