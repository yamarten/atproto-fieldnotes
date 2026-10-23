from __future__ import annotations

import json
import io
from argparse import Namespace
from contextlib import redirect_stdout
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import introduced_review, operator_feedback, project_record, source_sync
from project_registry_core.application import browser_entry, feedback, merge, projects
from project_registry_core.maintenance import introduction_plans
from project_registry_core.commands import maintenance as maintenance_commands
from project_registry_core.persistence import concurrency
from test_introduced_review import post


class IntroductionPlanCompletionTest(TempRegistryDbTestCase):
    key = "proj:planned"
    url = "https://example.test/post/1"

    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        project = self.make_project(self.key, "service_url", "https://planned.example")
        registry.add_manual_seed(project, "service_url", "https://planned.example", source="test")
        project_record.save_project(self.conn, project)

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def fetch(self):
        return project_record.fetch_project(self.conn, self.key)

    def plan(self, reason="紹介する理由"):
        feedback.save_feedback(self.conn, project_key=self.key, kind="introduction-plan", reason=reason)
        return dict(operator_feedback.active_plan(self.fetch()))

    def introduce(self, url):
        projects.apply_profile_edit(self.conn, project_key=self.key, name="Example", notes="", introduced_url=url)

    def assert_completed(self, original):
        saved = self.fetch()
        self.assertIsNone(operator_feedback.active_plan(saved))
        entry = next(item for item in saved["introduction_plans"] if item["id"] == original["id"])
        for field in ("id", "reason", "added_at", "origin_project_key"):
            self.assertEqual(entry[field], original[field])
        self.assertTrue(entry["cleared_at"])
        self.assertEqual(entry["clear_reason"], "introduced")
        self.assertEqual(entry["cleared_by_introduced_url"], saved["introduced_url"])
        row = self.conn.execute("SELECT * FROM project_decisions WHERE project_key=? AND kind='plan_introduction' AND json_extract(metadata_json,'$.id')=?",
                                (self.key, original["id"])).fetchone()
        self.assertTrue(row["cleared_at"])
        self.assertEqual(json.loads(row["metadata_json"])["id"], original["id"])
        self.assertEqual(browser_entry.introduction_plans(self.conn)["total"], 0)
        return entry

    def test_normal_save_completes_plan_and_repeated_save_preserves_completion(self):
        original = self.plan()
        project = self.fetch()
        project["introduced_url"] = self.url
        project_record.save_project(self.conn, project, preserve_timestamps=True)
        completed = self.assert_completed(original)
        self.assertIsNone(operator_feedback.active_plan(project))
        project_record.save_project(self.conn, project, preserve_timestamps=True)
        self.assertEqual(self.assert_completed(original), completed)

    def test_reintroduction_plan_survives_same_url_save_but_completes_on_replacement(self):
        original = self.plan()
        self.introduce(self.url)
        self.assert_completed(original)
        again = self.plan("再紹介の理由")
        self.introduce(self.url)
        self.assertEqual(operator_feedback.active_plan(self.fetch())["id"], again["id"])
        self.assertEqual(introduction_plans.clear_introduced_plans(self.conn, execute=True)["cleared_plans"], 0)
        self.introduce("https://example.test/post/2")
        self.assert_completed(again)

    def test_removing_introduction_never_resurrects_a_completed_plan(self):
        original = self.plan()
        self.introduce(self.url)
        completed = self.assert_completed(original)
        self.introduce("")
        self.assertIsNone(operator_feedback.active_plan(self.fetch()))
        self.assertEqual(self.fetch()["introduction_plans"][0], completed)

    def sync(self, source, *, execute=True):
        with mock.patch.object(source_sync, "search_bsky_posts_payload", return_value={"posts": [source], "cursor": ""}):
            return source_sync.sync_introduced_entries(self.conn, query="plan test", execute=execute, all_history=True)

    def test_sync_dry_run_preserves_plan_and_execute_completes_it(self):
        original = self.plan()
        source = post("first", urls=["https://planned.example"])
        self.sync(source, execute=False)
        self.assertEqual(operator_feedback.active_plan(self.fetch())["id"], original["id"])
        self.sync(source)
        completed = self.assert_completed(original)
        self.sync(source)
        self.assertEqual(self.assert_completed(original), completed)
        again = self.plan("再紹介")
        self.sync(source)
        self.assertEqual(operator_feedback.active_plan(self.fetch())["id"], again["id"])

    def test_manual_link_completes_plan_and_reopen_keeps_history(self):
        original = self.plan()
        source = post("manual", urls=["https://unmatched.example"])
        self.sync(source)
        item = introduced_review.review_payload(self.conn)["items"][0]
        introduced_review.resolve_item(self.conn, scope_key=item["scope_key"], target_id=item["id"],
                                       action="link", project_key=self.key)
        completed = self.assert_completed(original)
        introduced_review.resolve_item(self.conn, scope_key=item["scope_key"], target_id=item["id"], action="reopen")
        self.assertEqual(self.fetch()["introduced_url"], "")
        self.assertEqual(self.fetch()["introduction_plans"][0], completed)

    def test_merge_introduced_source_completes_target_plan(self):
        other = "proj:introduced"
        project = self.make_project(other, "repo_url", "https://github.com/example/introduced")
        project["introduced_url"] = self.url
        project_record.save_project(self.conn, project)
        original = self.plan()
        merge.merge_projects(self.conn, project_key=other, target_project_key=self.key, execute=False)
        self.assertEqual(operator_feedback.active_plan(self.fetch())["id"], original["id"])
        merge.merge_projects(self.conn, project_key=other, target_project_key=self.key, execute=True)
        self.assert_completed(original)

    def test_merge_planned_source_into_introduced_target_completes_source_plan(self):
        original = self.plan()
        other = "proj:introduced"
        project = self.make_project(other, "repo_url", "https://github.com/example/introduced")
        project["introduced_url"] = self.url
        project_record.save_project(self.conn, project)
        merge.merge_projects(self.conn, project_key=self.key, target_project_key=other, execute=True)
        self.key = other
        self.assert_completed(original)

    def test_merge_preserves_source_reintroduction_after_completing_target_plan(self):
        original = self.plan()
        other = "proj:reintroduction"
        project = self.make_project(other, "repo_url", "https://github.com/example/reintroduction")
        project["introduced_url"] = self.url
        project_record.save_project(self.conn, project)
        feedback.save_feedback(self.conn, project_key=other, kind="introduction-plan", reason="再紹介")
        merge.merge_projects(self.conn, project_key=other, target_project_key=self.key, execute=True)
        saved = self.fetch()
        self.assertEqual(operator_feedback.active_plan(saved)["reason"], "再紹介")
        self.assertTrue(next(item for item in saved["introduction_plans"] if item["id"] == original["id"])["cleared_at"])

    def legacy_introduced_plan(self):
        original = self.plan()
        # Reproduce data saved by the previous version, before automatic completion.
        self.conn.execute("UPDATE projects SET introduced_url=? WHERE project_key=?", (self.url, self.key))
        self.conn.commit()
        return original

    def test_existing_completion_is_read_only_in_dry_run_narrow_and_idempotent_in_execute(self):
        original = self.legacy_introduced_plan()
        stale = self.fetch()
        before = dict(self.conn.execute("SELECT * FROM projects WHERE project_key=?", (self.key,)).fetchone())
        revisions = concurrency.revision(self.conn, self.key)
        changes = self.conn.total_changes
        with mock.patch.object(project_record, "iso_now", return_value="2026-10-24T00:00:00+00:00"):
            dry_run = introduction_plans.clear_introduced_plans(self.conn)
            self.assertEqual(self.conn.total_changes, changes)
            self.assertEqual(dry_run["cleared_plans"], 1)
            result = introduction_plans.clear_introduced_plans(self.conn, execute=True)
        self.assertEqual(result["changes"], dry_run["changes"])
        self.assert_completed(original)
        self.assertEqual(before, dict(self.conn.execute("SELECT * FROM projects WHERE project_key=?", (self.key,)).fetchone()))
        self.assertGreater(concurrency.revision(self.conn, self.key), revisions)
        with self.assertRaises(concurrency.StaleProjectWriteError):
            project_record.save_project(self.conn, stale)
        self.assertEqual(introduction_plans.clear_introduced_plans(self.conn, execute=True)["cleared_plans"], 0)

    def test_existing_completion_rolls_back_when_a_write_fails(self):
        original = self.legacy_introduced_plan()
        other = self.make_project("proj:z", "repo_url", "https://github.com/example/rollback")
        project_record.save_project(self.conn, other)
        feedback.save_feedback(self.conn, project_key="proj:z", kind="introduction-plan", reason="別の予定")
        self.conn.execute("UPDATE projects SET introduced_url=? WHERE project_key='proj:z'", (self.url,))
        self.conn.commit()
        revision = concurrency.revision(self.conn, self.key)
        self.conn.execute("CREATE TEMP TRIGGER fail_completion BEFORE UPDATE ON project_decisions WHEN OLD.project_key='proj:z' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaisesRegex(Exception, "test failure"):
            introduction_plans.clear_introduced_plans(self.conn, execute=True)
        self.assertEqual(operator_feedback.active_plan(self.fetch())["id"], original["id"])
        self.assertEqual(concurrency.revision(self.conn, self.key), revision)

    def test_cli_dry_run_uses_read_only_connection_and_reports_existing_plans(self):
        self.legacy_introduced_plan()
        output = io.StringIO()
        with mock.patch.object(registry, "open_db", side_effect=AssertionError("writable connection")):
            with redirect_stdout(output):
                maintenance_commands.cmd_clear_introduced_plans(Namespace(execute=False))
        result = json.loads(output.getvalue())
        self.assertEqual(result["cleared_plans"], 1)
        self.assertEqual(result["changes"][0]["project_key"], self.key)
        self.assertIsNotNone(operator_feedback.active_plan(self.fetch()))
