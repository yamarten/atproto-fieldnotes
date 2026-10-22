from __future__ import annotations

import contextlib
import copy
import json

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry
import promotion_outcome_analysis
from project_registry_core import project_record, project_review
from project_registry_core.application import presentation, review


class _DescriptionFixtures:
    def profile(self):
        project = self.make_project("proj:description", "service_url", "https://example.com")
        project.update(name="Example", summary="Bookmarks stored as records.", tagline="Bookmark service",
                       features=[], llm_source={}, category="project", service_status="live",
                       service_status_confidence="high", service_status_evidence=["http_200", "login_surface_detected"])
        registry.confirm_service_seed(project, "https://example.com", source="test")
        return project


class DescriptionAvailabilityTest(_DescriptionFixtures, ProjectRecordTestCase):
    def test_missing_description_only_loses_the_single_availability_bonus(self):
        baseline = review.build_showcase_queue_from_projects([self.profile()], limit=None)[0]
        for missing, expected_loss in (([], 0), (["summary"], 0), (["tagline"], 0), (["summary", "tagline"], 3)):
            with self.subTest(missing=missing):
                project = self.profile()
                project.update({field: "" for field in missing})
                before = copy.deepcopy(project)
                queue = review.build_showcase_queue_from_projects([project], limit=None)
                self.assertEqual(len(queue), 1)
                self.assertEqual(queue[0]["description_missing"], missing)
                self.assertEqual(queue[0]["signals"][0]["kind"], "service_surface_confirmed")
                self.assertEqual(baseline["sort_score"] - queue[0]["sort_score"], expected_loss)
                self.assertNotIn("promotion_blockers", queue[0]["score_breakdown"]["components"])
                self.assertEqual(project_review.compute_review_signals(project), [])
                self.assertEqual(project, before)
                self.assertNotIn("status", queue[0])

    def test_manual_description_needs_neither_llm_nor_feature_quota(self):
        scores = []
        for count in (0, 1, 3):
            for llm_source in ({}, {"model": "fixture"}):
                project = self.profile()
                project.update(features=[f"Capability {i}" for i in range(count)], llm_source=llm_source)
                scores.append(review.build_showcase_queue_from_projects([project], limit=None)[0]["sort_score"])
        self.assertEqual(len(set(scores)), 1)

    def test_service_category_and_operator_decisions_still_control_candidates(self):
        for field, value in (("category", "reject"), ("category", ""), ("service_status", "dead"),
                             ("service_status", "unknown"), ("tracking_state", "ignore"),
                             ("introduced_url", "https://example.org/post")):
            with self.subTest(field=field, value=value):
                project = self.profile()
                project[field] = value
                self.assertEqual(review.build_showcase_queue_from_projects([project], limit=None), [])
        project = self.profile()
        project["summary"] = ""
        project_review.defer_promotion_on_project(project, reason="Waiting for release")
        self.assertEqual(review.build_showcase_queue_from_projects([project], limit=None), [])
        project_review.clear_active_promotion_deferrals(project)
        project_review.set_promotion_priority_on_project(project, lowered=True, reason="Bookmark roundup")
        queue = review.build_showcase_queue_from_projects([project], limit=None)
        self.assertEqual(queue[0]["priority_group"], "lowered")
        self.assertEqual(queue[0]["description_missing"], ["summary"])

    def test_old_readiness_cannot_change_scores_or_outcome_analysis(self):
        project = self.profile()
        baseline = review.build_showcase_queue_from_projects([project], limit=None)[0]["sort_score"]
        project.update(status="draft", promotion_ready=False, promotion_blockers=["summary missing", "LLM review missing"],
                       introduced_url="https://example.org/introduction")
        before = copy.deepcopy(project)
        analysis = promotion_outcome_analysis.analyze_projects([project], details="all")
        item = analysis["projects"][0]
        self.assertNotIn("promotion_blockers", item)
        self.assertEqual(item["description_missing"], [])
        project["introduced_url"] = ""
        self.assertEqual(review.build_showcase_queue_from_projects([project], limit=None)[0]["sort_score"], baseline)
        project["introduced_url"] = before["introduced_url"]
        self.assertEqual(project, before)


class DescriptionStorageTest(_DescriptionFixtures, TempRegistryDbTestCase):
    def test_legacy_state_is_ignored_on_read_and_dropped_on_save_without_erasing_notes(self):
        project = self.profile()
        project.update(summary="", watch_reason="summary missing; wait for release")
        legacy = {"status": "draft", "promotion_ready": False, "promotion_blockers": ["summary missing"],
                  "last_promoted_at": "2026-01-01", "promotion_events": [{"at": "2026-01-01"}]}
        with contextlib.closing(registry.open_db()) as conn:
            project_record.save_project(conn, project)
            conn.commit()
            row = conn.execute("SELECT state_json FROM projects WHERE project_key=?", (project["project_key"],)).fetchone()
            state = json.loads(row["state_json"])
            state.setdefault("ops", {}).update(legacy)
            state["evidence"]["test_observation"] = {"status": "success"}
            encoded = json.dumps(state)
            conn.execute("UPDATE projects SET state_json=? WHERE project_key=?", (encoded, project["project_key"]))
            conn.commit()
            detail = presentation.project_detail(conn, project_key=project["project_key"])
            self.assertEqual(detail["evaluation"]["description_missing"], ["summary"])
            self.assertNotIn("promotion_ready", detail["evaluation"])
            self.assertTrue(set(legacy).isdisjoint(detail["project"]["state"]["ops"]))
            self.assertEqual(detail["project"]["state"]["evidence"]["test_observation"]["status"], "success")
            stored = project_record.fetch_project(conn, project["project_key"])
            self.assertTrue(set(legacy).isdisjoint(stored))
            self.assertEqual(stored["watch_reason"], project["watch_reason"])
            # Viewing does not rewrite saved state or historical records.
            self.assertEqual(conn.execute("SELECT state_json FROM projects WHERE project_key=?", (project["project_key"],)).fetchone()[0], encoded)
            stored["summary"] = "New description"
            project_record.save_project(conn, stored)
            conn.commit()
            updated = json.loads(conn.execute("SELECT state_json FROM projects WHERE project_key=?", (project["project_key"],)).fetchone()[0])
            self.assertTrue(set(legacy).isdisjoint(updated["ops"]))
            restored = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(restored["watch_reason"], project["watch_reason"])
            self.assertEqual(restored["tracking_state"], "active")
            self.assertEqual(restored["service_status"], "live")
            self.assertEqual(presentation.project_detail(conn, project_key=project["project_key"])["evaluation"]["description_missing"], [])
