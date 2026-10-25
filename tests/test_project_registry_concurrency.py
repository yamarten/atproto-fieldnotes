from __future__ import annotations

import copy
import json
import multiprocessing
import sqlite3
import time
from pathlib import Path
from unittest import mock

from helpers import TempRegistryDbTestCase

from project_registry_core import project_review
from enrichment_io import EnrichmentIO
from project_registry_core.persistence import concurrency, database
from project_registry_core import project_record, registry
from project_registry_core.persistence import storage
from project_registry_core import source_sync
from project_registry_core.application import (
    decisions,
    enrichment,
    merge,
    presentation,
    projects,
    review,
    maintenance,
)
from project_registry_core.evidence import EvidenceBundle
from project_registry_core.jobs import OperationRequest


def update_from_process(path: str, key: str, ready, proceed, result) -> None:
    conn = database.open_database(Path(path))
    project = project_record.fetch_project(conn, key)
    ready.set()
    if not proceed.wait(10):
        result.put("timeout")
        return
    project["notes"] = "late process"
    try:
        project_record.save_project(conn, project)
        result.put("saved")
    except concurrency.StaleProjectWriteError:
        result.put("conflict")
    finally:
        conn.close()


class ProjectConcurrencyTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.other = registry.open_db()
        for key in ("proj:111111111111", "proj:222222222222", "proj:333333333333"):
            project = self.make_project(key, "nsid", f"app.example{key[-12:]}.post")
            project_record.save_project(self.conn, project)
        self.key = "proj:111111111111"

    def tearDown(self):
        self.conn.close()
        self.other.close()
        super().tearDown()

    def test_stale_snapshot_rejects_every_operator_mutation(self):
        mutations = [
            lambda: projects.apply_profile_edit(self.other, project_key=self.key, name="Human correction", notes="Keep", introduced_url=""),
            lambda: projects.set_tracking_state(self.other, project_key=self.key, tracking_state="ignore"),
            lambda: decisions.set_identifier_block(self.other, project_key=self.key, identifier_type="service_url", value="https://candidate.example.org"),
            lambda: decisions.set_identifier_defer(self.other, project_key=self.key, identifier_type="service_url", value="https://deferred.example.org"),
            lambda: presentation.confirm_candidate(self.other, project_key=self.key, identifier_type="service_url", value="https://confirmed.example.org"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                stale = project_record.fetch_project(self.conn, self.key)
                mutate()
                expected = project_record.fetch_project(self.other, self.key)
                stale["notes"] = "obsolete investigation"
                with self.assertRaises(concurrency.StaleProjectWriteError):
                    project_record.save_project(self.conn, stale)
                self.assertEqual(project_record.fetch_project(self.conn, self.key), expected)
                self.assertFalse(self.conn.in_transaction)

    def test_direct_children_and_aba_updates_are_detected(self):
        statements = [
            ("UPDATE projects SET name=name WHERE project_key=?", (self.key,)),
            ("UPDATE project_identifiers SET observed_at='changed' WHERE project_key=?", (self.key,)),
            ("INSERT INTO project_decisions VALUES (?, 0, 'separate_project', 'project_key', 'proj:222222222222', '', '', '', '', '', '{}')", (self.key,)),
            ("UPDATE project_decisions SET note='correction' WHERE project_key=?", (self.key,)),
            ("DELETE FROM project_decisions WHERE project_key=?", (self.key,)),
        ]
        for sql, args in statements:
            with self.subTest(sql=sql):
                old = project_record.fetch_project(self.conn, self.key)
                self.other.execute(sql, args)
                self.other.commit()
                with self.assertRaises(concurrency.StaleProjectWriteError):
                    project_record.save_project(self.conn, old)

    def test_delete_recreate_and_unrelated_project(self):
        stale = project_record.fetch_project(self.conn, self.key)
        other = project_record.fetch_project(self.other, "proj:222222222222")
        other["notes"] = "unrelated"
        project_record.save_project(self.other, other)
        project_record.save_project(self.conn, stale)
        old = copy.deepcopy(stale)
        self.other.execute("DELETE FROM projects WHERE project_key=?", (self.key,))
        self.other.commit()
        with self.assertRaises(concurrency.StaleProjectWriteError):
            project_record.save_project(self.conn, old)
        with self.assertRaises(concurrency.StaleProjectWriteError):
            project_record.save_project(self.conn, self.make_project(self.key))

    def test_web_precondition_survives_reload_before_save(self):
        revision = presentation.project_revision(self.conn, self.key)
        presentation.require_project_revision(self.conn, self.key, revision)
        projects.set_tracking_state(self.other, project_key=self.key, tracking_state="ignore")
        with self.assertRaises(concurrency.StaleProjectWriteError):
            projects.apply_profile_edit(self.conn, project_key=self.key, name="Stale form", notes="", introduced_url="")
        self.assertEqual(project_record.fetch_project(self.conn, self.key)["tracking_state"], "ignore")

    def test_merge_rechecks_input_and_rolls_back_all_outputs(self):
        real_merge = merge.merge_project_records
        def competing_update(target, source):
            result = real_merge(target, source)
            projects.set_tracking_state(self.other, project_key=source["project_key"], tracking_state="ignore")
            return result
        target_before = project_record.fetch_project(self.conn, "proj:222222222222")
        with mock.patch.object(merge, "merge_project_records", side_effect=competing_update):
            with self.assertRaises(concurrency.StaleProjectWriteError):
                merge.merge_projects(self.conn, project_key=self.key, target_project_key="proj:222222222222", execute=True, force=True)
        self.assertEqual(project_record.fetch_project(self.conn, "proj:222222222222"), target_before)
        self.assertIsNone(database.project_key_alias_target(self.conn, self.key))
        self.assertEqual(project_record.fetch_project(self.conn, self.key)["tracking_state"], "ignore")

    def test_child_failure_rolls_back_scalar_and_revision(self):
        old = project_record.fetch_project(self.conn, self.key)
        changed = copy.deepcopy(old)
        changed["name"] = "must rollback"
        with mock.patch.object(database, "replace_project_children", side_effect=RuntimeError("disk failure")):
            with self.assertRaises(RuntimeError):
                project_record.save_project(self.conn, changed)
        self.assertEqual(project_record.fetch_project(self.conn, self.key), old)
        self.assertFalse(self.conn.in_transaction)

    def test_read_does_not_wait_for_writer_and_does_not_hold_transaction(self):
        self.other.execute("BEGIN IMMEDIATE")
        self.other.execute("UPDATE projects SET name='pending' WHERE project_key=?", (self.key,))
        start = time.monotonic()
        reader = registry.open_db()
        try:
            self.assertNotEqual(project_record.fetch_project(reader, self.key)["name"], "pending")
            self.assertFalse(reader.in_transaction)
            self.assertLess(time.monotonic() - start, 1.0)
        finally:
            reader.close()
            self.other.rollback()

    def test_real_process_rejects_late_snapshot(self):
        ctx = multiprocessing.get_context("spawn")
        ready, proceed, result = ctx.Event(), ctx.Event(), ctx.Queue()
        process = ctx.Process(target=update_from_process, args=(str(self.db_path), self.key, ready, proceed, result))
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            projects.set_tracking_state(self.conn, project_key=self.key, tracking_state="ignore")
            proceed.set()
            self.assertEqual(result.get(timeout=10), "conflict")
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join()
            result.close()

    def test_bulk_conflict_continues_preserves_history_and_resumes(self):
        path = str(Path(self.tempdir.name) / "checkpoint.json")
        request = OperationRequest.create("enrich", operation_id="enrich-concurrency", parameters={"all_projects": True, "checkpoint_path": path})
        called = []
        def collect(conn, project, **kwargs):
            self.assertFalse(conn.in_transaction)
            called.append(project["project_key"])
            if len(called) == 1:
                edited = project_record.fetch_project(self.other, project["project_key"])
                edited["notes"] = "Human correction"
                project_record.save_project(self.other, edited)
            storage.stage_investigation_run(project, project_key=project["project_key"], started_at=project_record.iso_now(), completed_at=project_record.iso_now(), input_project=project, output_project=project, bundle=EvidenceBundle(project["project_key"], project_record.iso_now()))
            return project
        with mock.patch.object(enrichment.enrichment_pipeline, "enrich_project", side_effect=collect):
            result = enrichment.run(self.conn, request)
        self.assertEqual(result.status, "retry_wait")
        self.assertEqual(len(called), 3)
        checkpoint = json.loads(Path(path).read_text())
        self.assertEqual(len(checkpoint["completed_project_keys"]), 2)
        run_id = checkpoint["conflicted_projects"][0]["run_id"]
        self.assertEqual(storage.load_investigation_run(self.conn, run_id)["application_status"], "unapplied")
        self.assertIsNone(storage.latest_investigation_run(self.conn, called[0]))
        self.assertEqual(project_record.fetch_project(self.conn, called[0])["notes"], "Human correction")
        resume = OperationRequest.create("enrich", parameters={"all_projects": True, "checkpoint_path": path, "resume_run": request.operation_id})
        with mock.patch.object(enrichment.enrichment_pipeline, "enrich_project", side_effect=collect):
            result = enrichment.run(self.conn, resume)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(called), 4)
        self.assertTrue(storage.load_investigation_run(self.conn, run_id)["resolved_at"])

    def test_each_bulk_item_reads_latest_profile_and_tracking_state(self):
        called = []
        def collect(conn, project, **kwargs):
            called.append((project["project_key"], project["notes"]))
            if len(called) == 1:
                projects.apply_profile_edit(self.other, project_key="proj:222222222222", name="New name", notes="Edited while waiting", introduced_url="")
                projects.set_tracking_state(self.other, project_key="proj:333333333333", tracking_state="ignore")
            return project
        request = OperationRequest.create("enrich", parameters={"all_projects": True, "no_checkpoint": True})
        with mock.patch.object(enrichment.enrichment_pipeline, "enrich_project", side_effect=collect):
            result = enrichment.run(self.conn, request)
        self.assertEqual(result.status, "completed")
        self.assertEqual(called, [(self.key, ""), ("proj:222222222222", "Edited while waiting")])
        self.assertEqual(result.data["completed_project_count"], 2)
        self.assertEqual(result.data["skipped_projects"], [{"project_key": "proj:333333333333", "reason": "tracking_state_changed"}])

    def test_save_lock_covers_alias_check_and_child_replacement(self):
        original = database.project_key_alias_target
        def attempt_writer(conn, key):
            if conn is self.conn:
                self.other.execute("PRAGMA busy_timeout=5")
                with self.assertRaises(sqlite3.OperationalError):
                    self.other.execute("BEGIN IMMEDIATE")
            return original(conn, key)
        project = project_record.fetch_project(self.conn, self.key)
        with mock.patch.object(database, "project_key_alias_target", side_effect=attempt_writer):
            project_record.save_project(self.conn, project)
        self.other.execute("BEGIN IMMEDIATE")
        self.other.rollback()

    def test_merge_third_project_decision_invalidates_its_snapshot(self):
        third = "proj:333333333333"
        decisions.set_separate(self.conn, project_key=third, other_project_key=self.key)
        stale = project_record.fetch_project(self.conn, third)
        merge.merge_projects(self.other, project_key=self.key, target_project_key="proj:222222222222", execute=True, force=True)
        with self.assertRaises(concurrency.StaleProjectWriteError):
            project_record.save_project(self.conn, stale)
        self.assertIn("proj:222222222222", project_record.fetch_project(self.conn, third)["separate_project_keys"])

    def test_split_result_is_protected_from_old_source_snapshot(self):
        stale = project_record.fetch_project(self.conn, self.key)
        nsid = self.conn.execute("SELECT value FROM project_identifiers WHERE project_key=? AND kind='nsid'", (self.key,)).fetchone()[0]
        result = merge.split_identifier(self.other, project_key=self.key, identifier_type="nsid", value=nsid, execute=True)
        with self.assertRaises(concurrency.StaleProjectWriteError):
            project_record.save_project(self.conn, stale)
        self.assertIsNotNone(project_record.fetch_project(self.conn, result["target_project_key"]))
        source = project_record.fetch_project(self.conn, self.key)
        self.assertNotIn(nsid, source["input_nsids"] + source["resolved_nsids"])

    def test_real_pipeline_keeps_edit_and_unapplied_output(self):
        edited = False
        def edit_during_llm(*args, **kwargs):
            nonlocal edited
            self.assertFalse(self.conn.in_transaction)
            if not edited:
                projects.apply_profile_edit(self.other, project_key=self.key, name="Human name", notes="Human notes", introduced_url="")
                edited = True
            raise RuntimeError("fixture: LLM unavailable")
        request = OperationRequest.create("enrich", parameters={"project_key": self.key})
        io = EnrichmentIO(llm=edit_during_llm, dns=lambda *args, **kwargs: [],
                          browser=lambda url, **kw: {"url": url, "status_code": 200})
        io.json("https://ufos-api.microcosm.blue/records?collection=app.example111111111111.post", {"records": []})
        io.page("https://example111111111111.app")
        with io:
            result = enrichment.run(self.conn, request)
        self.assertTrue(edited)
        self.assertEqual(result.status, "retry_wait")
        self.assertEqual(project_record.fetch_project(self.conn, self.key)["notes"], "Human notes")
        runs = storage.list_investigation_runs(self.conn, project_key=self.key)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["application_status"], "unapplied")
        self.assertNotEqual(runs[0]["output_project"]["notes"], "Human notes")

    def test_service_verification_conflict_preserves_unapplied_history(self):
        current = project_record.fetch_project(self.conn, self.key)
        def collect(project, url, lane):
            projects.set_tracking_state(self.other, project_key=self.key, tracking_state="ignore")
            self.assertFalse(self.conn.in_transaction)
            return EvidenceBundle(self.key, project_record.iso_now())
        with mock.patch.object(registry, "collect_service_verification", side_effect=collect):
            with self.assertRaises(concurrency.StaleProjectWriteError):
                registry.verify_service_project(self.conn, current, lane="static", service_url="https://service.example.org", execute=True)
        runs = storage.list_investigation_runs(self.conn, project_key=self.key)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["application_status"], "unapplied")
        self.assertEqual(project_record.fetch_project(self.conn, self.key)["tracking_state"], "ignore")

    def test_review_does_not_apply_old_plan_to_new_snapshot(self):
        def entry(project):
            if project["project_key"] == self.key:
                projects.set_tracking_state(self.other, project_key=self.key, tracking_state="ignore")
            return {"project_key": project["project_key"], "kind": "test", "suggested_tracking_state": "out_of_scope"}
        with mock.patch.object(project_review, "out_of_scope_review_entry", side_effect=entry):
            result = review.out_of_scope(self.conn, execute=True)
        self.assertEqual(result["operation_status"], "retry_wait")
        self.assertEqual(len(result["conflicted_projects"]), 1)
        self.assertEqual(result["updated_count"], 2)
        self.assertEqual(project_record.fetch_project(self.conn, self.key)["tracking_state"], "ignore")

    def test_collection_sync_conflict_keeps_edit_and_does_not_advance_watermark(self):
        collection = "app.example111111111111.post"
        project = project_record.fetch_project(self.conn, self.key)
        project["resolved_nsids"] = [collection]
        project_record.save_project(self.conn, project)
        real_upsert = source_sync.upsert_collection_observation
        def update(project, *args, **kwargs):
            self.assertFalse(self.conn.in_transaction)
            projects.set_tracking_state(self.other, project_key=self.key, tracking_state="ignore")
            return real_upsert(project, *args, **kwargs)
        payload = [{"collection": collection, "count": 10, "recent_count": 2, "min": "2026-09-01T00:00:00+00:00", "max": "2026-09-14T00:00:00+00:00"}]
        before = registry.read_sync_state(self.conn, "collections", {"source_url": "https://inventory.example.org"})
        with (
            mock.patch.object(source_sync, "fetch_collection_inventory", return_value=payload),
            mock.patch.object(source_sync, "upsert_collection_observation", side_effect=update),
        ):
            with self.assertRaises(concurrency.StaleProjectWriteError):
                source_sync.sync_collection_inventory(self.conn, source_url="https://inventory.example.org", execute=True, create_unmatched=False, lookback_days=7)
        self.assertEqual(project_record.fetch_project(self.conn, self.key)["tracking_state"], "ignore")
        self.assertEqual(registry.read_sync_state(self.conn, "collections", {"source_url": "https://inventory.example.org"}), before)

    def test_unresolved_history_survives_pruning(self):
        project = project_record.fetch_project(self.conn, self.key)
        run_ids = [storage.save_investigation_run(self.conn, project_key=self.key, started_at="2000-01-01T00:00:00+00:00", completed_at="2000-01-01T00:00:00+00:00", input_project=project, output_project=project, bundle=EvidenceBundle(self.key, project_record.iso_now()), application_status="unapplied", conflict_reason="test") for _ in range(3)]
        self.conn.commit()
        result = maintenance.prune_investigation_history(self.conn, execute=True, retention_days=0, keep_latest_per_project=1)
        self.assertEqual(result["retained_unresolved_conflict_count"], 3)
        self.assertIsNone(storage.latest_investigation_run(self.conn, self.key))
        for run_id in run_ids:
            self.assertIsNotNone(storage.load_investigation_run(self.conn, run_id))
