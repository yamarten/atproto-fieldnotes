from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
from pathlib import Path
from unittest import mock

from helpers import ROOT, TempRegistryDbTestCase, registry
from enrich_overlap_support import KEYS, NOW, Responses, seed, semantic
from project_registry_core import enrichment_pipeline as pipeline, project_record, storage, github_api, runtime_view
from project_registry_core import llm_transport
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord, CollectorResult
from project_registry_core.application import enrichment, merge
from project_registry_core.jobs import CancellationToken, OperationRequest


class EnrichPrefetchTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)
        self.responses = Responses()
        self.stack = self.responses.patches()
        self.addCleanup(self.stack.close)
        seed(self.conn)
        self.path = Path(self.tempdir.name) / "checkpoint.json"
        self.events = []

    def run_batch(self, **parameters):
        token = parameters.pop("cancellation", None)
        return enrichment.run(self.conn, OperationRequest.create("enrich", operation_id="overlap-test", execute=True,
            parameters={"all_projects": True, "checkpoint_path": str(self.path), **parameters}),
            cancellation=token, emit=self.events.append)

    def run_rows(self):
        return [storage.load_investigation_run(self.conn, row[0]) for row in self.conn.execute(
            "SELECT run_id FROM investigation_runs ORDER BY rowid")]

    def edit(self, key, **changes):
        other = registry.open_db()
        try:
            project = project_record.fetch_project(other, key)
            project.update(changes)
            project_record.save_project(other, project)
            other.commit()
        finally:
            other.close()

    def test_overlap_one_llm_and_no_cross_project_io_concurrency(self):
        self.responses.collection_delay = .015
        self.responses.llm_delay = .025
        result = self.run_batch()
        self.assertEqual(result.status, "completed")
        execution = result.data["execution"]
        self.assertEqual((execution["started"], execution["consumed"], execution["discarded"]), (2, 2, 0))
        self.assertEqual(self.responses.summary(), {"calls": {"probe": 3, "planning": 3, "search": 3, "triage": 3},
            "max_llm_active": 1, "max_probe_active": 1, "probe_llm_overlap": True})
        self.assertTrue(all(row["observations"]["planning"]["complete"] for row in self.run_rows()))
        self.assertTrue(all(row["observations"]["search_outcomes"]["data"] for row in self.run_rows()))
        self.assertEqual(json.loads(self.path.read_text())["completed_project_keys"], KEYS[:3])
        self.assertEqual([event.sequence for event in self.events], list(range(1, len(self.events) + 1)))

    def test_cancel_preserves_collection_then_resume_refetches_only_unfinished(self):
        token = CancellationToken()
        def cancel_on_next(url):
            if "overlap-2" in url:
                token.request_cancel("fixture")
        self.responses.on_probe = cancel_on_next
        result = self.run_batch(cancellation=token)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.data["completed_project_count"], 1)
        self.assertEqual(self.responses.calls["probe"], 2)
        self.assertEqual(self.responses.calls["planning"], 1)
        unapplied = next(row for row in self.run_rows() if row["application_status"] == "unapplied")
        self.assertEqual(unapplied["project_key"], KEYS[1])
        self.assertEqual(unapplied["evaluation"], {"incomplete": True})
        self.assertEqual(unapplied["conflict_reason"], "prefetch_incomplete:cancelled")
        self.assertTrue(unapplied["bundle"].records)
        self.assertFalse(project_record.fetch_project(self.conn, KEYS[1])["last_enriched_at"])
        self.assertEqual(json.loads(self.path.read_text())["completed_project_keys"], [KEYS[0]])
        self.responses.on_probe = None
        self.responses.reset()
        resumed = self.run_batch(resume_run="overlap-test")
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(self.responses.calls["probe"], 2)
        self.assertTrue(storage.load_investigation_run(self.conn, unapplied["run_id"])["resolved_at"])

    def test_manual_edit_of_prefetched_snapshot_refetches_and_preserves_decision(self):
        changed = False
        def edit_during_llm(purpose):
            nonlocal changed
            if not changed:
                changed = True
                self.edit(KEYS[1], name="Operator edit", notes="Keep this manual note")
        self.responses.on_llm = edit_during_llm
        result = self.run_batch()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.data["execution"]["discarded"], 1)
        self.assertEqual(self.responses.calls["probe"], 4)
        self.assertEqual(project_record.fetch_project(self.conn, KEYS[1])["notes"], "Keep this manual note")
        rows = self.run_rows()
        self.assertEqual(len(rows), 4)
        self.assertTrue(next(row for row in rows if row["application_status"] == "unapplied")["resolved_at"])
        self.assertIn("Operator edit", next(row for row in rows if row["project_key"] == KEYS[1] and row["application_status"] == "applied")["input_project"]["name"])

    def test_tracking_change_skips_prefetched_and_resolves_unused_history(self):
        self.responses.on_llm = lambda purpose: self.edit(KEYS[1], tracking_state="ignore") if purpose == "planning" else None
        result = self.run_batch()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.data["skipped_projects"], [{"project_key": KEYS[1], "reason": "tracking_state_changed"}])
        unused = next(row for row in self.run_rows() if row["application_status"] == "unapplied")
        self.assertTrue(unused["resolved_at"])
        self.assertFalse(project_record.fetch_project(self.conn, KEYS[1])["last_enriched_at"])

    def test_merge_prefetched_into_processed_alias_does_not_duplicate_or_recreate(self):
        merged = False
        def after_first(purpose):
            nonlocal merged
            if merged or purpose != "triage":
                return
            merged = True
            other = registry.open_db()
            try:
                merge.merge_projects(other, project_key=KEYS[1], target_project_key=KEYS[0], execute=True, force=True)
            finally:
                other.close()
        # The current project also becomes stale, so it must be retried; the alias
        # then runs once on its new snapshot, and the third project still completes.
        self.responses.on_llm = after_first
        result = self.run_batch()
        self.assertEqual(result.status, "retry_wait")
        self.assertIsNone(project_record.fetch_project(self.conn, KEYS[1]))
        self.assertEqual(result.data["conflicted_project_count"], 1)
        self.assertEqual(result.data["execution"]["discarded"], 1)
        self.assertEqual(result.data["completed_project_count"], 2)
        self.assertTrue(project_record.fetch_project(self.conn, KEYS[2])["last_enriched_at"])

    def test_merge_prefetched_into_later_target_runs_canonical_only_once(self):
        merged = False
        def merge_next(purpose):
            nonlocal merged
            if merged:
                return
            merged = True
            other = registry.open_db()
            try:
                merge.merge_projects(other, project_key=KEYS[1], target_project_key=KEYS[2], execute=True, force=True)
            finally:
                other.close()
        self.responses.on_llm = merge_next
        result = self.run_batch()
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.responses.calls["planning"], 2)
        self.assertEqual(result.data["skipped_projects"], [{"project_key": KEYS[2], "reason": "canonical_project_already_processed"}])
        self.assertIsNone(project_record.fetch_project(self.conn, KEYS[1]))
        self.assertTrue(next(row for row in self.run_rows() if row["application_status"] == "unapplied")["resolved_at"])

    def test_deleted_prefetched_project_is_not_recreated(self):
        deleted = False
        def delete_next(purpose):
            nonlocal deleted
            if not deleted:
                deleted = True
                other = registry.open_db()
                try:
                    other.execute("DELETE FROM projects WHERE project_key=?", (KEYS[1],))
                    other.commit()
                finally:
                    other.close()
        self.responses.on_llm = delete_next
        result = self.run_batch()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.data["skipped_projects"], [{"project_key": KEYS[1], "reason": "project_missing"}])
        self.assertIsNone(project_record.fetch_project(self.conn, KEYS[1]))
        self.assertTrue(next(row for row in self.run_rows() if row["application_status"] == "unapplied")["resolved_at"])

    def test_repository_analysis_also_opens_prefetch_window_without_moving_llm_to_thread(self):
        main_thread = threading.get_ident()
        for key in KEYS[:3]:
            project = project_record.fetch_project(self.conn, key)
            registry.confirm_repo_seed(project, f"https://github.com/demo/{key[-1]}", source="overlap_fixture")
            project_record.save_project(self.conn, project)
        self.conn.commit()
        def repo(project, url, **kwargs):
            bundle = EvidenceBundle(project_key=project["project_key"], collected_at=NOW)
            bundle.add_record(EvidenceRecord(kind="github_repo_snapshot", source=f"github_repo:{url}",
                classification="confirmed", observed_at=NOW,
                value={"repo_url": url, "repo": {"name": "demo", "description": "AT Protocol demo"},
                       "revision": "abc123", "readme_excerpt": "An AT Protocol demo.", "tree_paths": []}))
            bundle.add_collector_result(CollectorResult(source="github_repo", operation="inspect", status="success",
                observed_at=NOW, request_count=1, result_count=1))
            return bundle
        purposes = []
        original = llm_transport.run_llm_with_metrics
        def record(prompt, **kwargs):
            self.assertEqual(threading.get_ident(), main_thread)
            purposes.append(kwargs["purpose"])
            return original(prompt, **kwargs)
        with mock.patch.object(pipeline, "collect_github_repo_evidence", side_effect=repo), \
             mock.patch.object(llm_transport, "run_llm_with_metrics", side_effect=record):
            result = self.run_batch()
        self.assertEqual(result.status, "completed")
        self.assertEqual(purposes[0], "repository_analysis")
        self.assertEqual(purposes.count("repository_analysis"), 3)
        self.assertEqual(result.data["execution"]["consumed"], 2)
        for row in self.run_rows():
            self.assertEqual(row["metrics"]["llm_request_summary"]["repository_analysis"]["success_count"], 1)
            analyses = [item for item in row["bundle"].collector_results if item.source == "repository_analysis"]
            self.assertEqual([item.status for item in analyses], ["success"])

    def test_partial_failure_and_rate_limit_remain_project_local(self):
        def rate_limit(url):
            if "overlap-2" in url:
                error = urllib.error.HTTPError(url, 429, "rate limit", {"Retry-After": "1"}, None)
                converted = github_api.GitHubRateLimitError(error)
                error.close()
                raise converted
        self.responses.on_probe = rate_limit
        result = self.run_batch()
        self.assertEqual(result.status, "completed")
        rows = self.run_rows()
        self.assertTrue(rows[1]["bundle"].failures)
        self.assertEqual(rows[1]["bundle"].failures[0].category, "rate_limit")
        self.assertFalse(rows[0]["bundle"].failures)
        self.assertFalse(rows[2]["bundle"].failures)

    def test_prefetch_exception_is_charged_to_next_project_and_resume_retries_it(self):
        original = pipeline.collect_initial
        def fail_one(project):
            if project["project_key"] == KEYS[1]:
                raise ValueError("initial failed")
            return original(project)
        with mock.patch.object(pipeline, "collect_initial", side_effect=fail_one):
            result = self.run_batch()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["failed_projects"][0]["project_key"], KEYS[1])
        self.assertEqual(result.data["completed_project_count"], 2)
        self.responses.reset()
        self.assertEqual(self.run_batch(resume_run="overlap-test").status, "completed")
        self.assertEqual(self.responses.calls["planning"], 1)

    def test_foreground_fail_fast_keeps_unprocessed_prefetch(self):
        original = pipeline.enrich_project
        def fail_after_collection(*args, **kwargs):
            original(*args, **kwargs)
            raise ValueError("save boundary fixture")
        with mock.patch.object(pipeline, "enrich_project", side_effect=fail_after_collection):
            result = self.run_batch(continue_on_error=False)
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.responses.calls["planning"], 1)
        self.assertEqual(result.data["execution"]["discarded"], 1)
        self.assertEqual(self.run_rows()[0]["conflict_reason"], "prefetch_incomplete:fail_fast")
        self.assertEqual(json.loads(self.path.read_text())["completed_project_keys"], [])

    def test_no_prefetch_and_no_llm_do_not_start_speculation(self):
        result = self.run_batch(prefetch=False)
        self.assertEqual(result.data["execution"]["started"], 0)
        self.assertFalse(self.responses.summary()["probe_llm_overlap"])
        with mock.patch.object(pipeline, "enrich_project", side_effect=lambda conn, project, **kw: project):
            result = self.run_batch()
        self.assertEqual(result.data["execution"]["started"], 0)

    def test_slow_prefetch_joins_before_foreground_network_and_does_not_overlap_llms(self):
        self.responses.collection_delay = .025
        self.responses.llm_delay = .001
        result = self.run_batch()
        self.assertGreater(result.data["execution"]["join_wait_ms"], 10)
        for probe in [row for row in self.responses.timeline if row[0] == "probe"]:
            for search in [row for row in self.responses.timeline if row[0] == "search"]:
                self.assertFalse(probe[2] < search[3] and search[2] < probe[3])
        self.assertEqual(self.responses.summary()["max_llm_active"], 1)

    def test_checkpoint_has_only_committed_items_while_next_collection_runs(self):
        checked = threading.Event()
        def check(url):
            if "overlap-2" in url:
                checkpoint = json.loads(self.path.read_text())
                self.assertEqual(checkpoint["completed_project_keys"], [])
                self.assertEqual(checkpoint["current_project_key"], KEYS[0])
                checked.set()
        self.responses.on_probe = check
        self.run_batch()
        self.assertTrue(checked.is_set())

    def test_fixed_responses_match_projects_candidates_and_history_with_reuse_hit_and_miss(self):
        for warmups in (0, 3):
            with self.subTest(warmups=warmups):
                for _ in range(warmups):
                    self.run_batch(prefetch=False)
                baseline = sqlite3.connect(":memory:")
                self.conn.backup(baseline)
                snapshots = []
                counts = []
                try:
                    for enabled in (False, True):
                        baseline.backup(self.conn)
                        self.responses.reset()
                        result = self.run_batch(prefetch=enabled)
                        self.assertEqual(result.status, "completed")
                        current = []
                        for key in KEYS[:3]:
                            row = storage.latest_investigation_run(self.conn, key)
                            current.append({"project": runtime_view.sanitize_runtime_snapshot(project_record.fetch_project(self.conn, key)),
                                "input": row["input_project"], "output": row["output_project"],
                                "bundle": row["bundle"].to_dict(), "evaluation": row["evaluation"],
                                "planning": row["observations"]["planning"]["data"],
                                "search_outcomes": row["observations"]["search_outcomes"]["data"]})
                            self.assertEqual(row["metrics"]["triage_reuse"]["status"], "hit" if warmups else "miss")
                        snapshots.append(semantic(current))
                        counts.append(dict(self.responses.calls))
                    self.assertEqual(snapshots[0], snapshots[1])
                    self.assertEqual(counts[0], counts[1])
                finally:
                    baseline.close()

    def start_entry(self, mode, *, scenario="normal", parameters=None):
        marker = Path(self.tempdir.name) / "second-started"
        marker.unlink(missing_ok=True)
        env = {**os.environ, "ENRICH_TEST_SCENARIO": scenario, "ENRICH_TEST_MARKER": str(marker)}
        command = [sys.executable, str(ROOT / "tests/enrich_overlap_entry.py"), mode]
        payload = None
        if mode == "cli":
            command += ["--db-path", str(self.db_path), "enrich", "--all", "--checkpoint-path", str(self.path)]
        else:
            request = OperationRequest.create("enrich", operation_id="worker-overlap", execute=True,
                parameters={"all_projects": True, "checkpoint_path": str(self.path), **(parameters or {})})
            payload = json.dumps({"db_path": str(self.db_path), "request": request.to_dict()})
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        if payload:
            process.stdin.write(payload)
        process.stdin.close()
        process.stdin = None
        return process, marker

    def test_cli_and_worker_execute_the_same_prefetch_application(self):
        for mode in ("cli", "worker"):
            with self.subTest(mode=mode):
                process, _ = self.start_entry(mode)
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stderr + stdout)
                rows = [json.loads(line) for line in stdout.splitlines()]
                data = rows[-1]["payload"]["data"] if mode == "worker" else rows[-1]
                self.assertEqual(data["execution"]["consumed"], 2)
                self.assertEqual(data["completed_project_count"], 3)

    def test_real_worker_sigint_preserves_prefetch_and_checkpoint_then_resumes(self):
        process, marker = self.start_entry("worker", scenario="slow_second")
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists())
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr + stdout)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        result = json.loads(stdout.splitlines()[-1])["payload"]
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["data"]["execution"]["discarded"], 1)
        self.assertEqual(json.loads(self.path.read_text())["completed_project_keys"], [KEYS[0]])
        process, _ = self.start_entry("worker", parameters={"resume_run": "worker-overlap"})
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, stderr + stdout)
        self.assertEqual(json.loads(self.path.read_text())["completed_project_keys"], KEYS[:3])

    def test_real_worker_manual_change_while_prefetch_is_in_flight(self):
        process, marker = self.start_entry("worker", scenario="slow_second")
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists())
            self.edit(KEYS[1], notes="Changed while worker was fetching")
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr + stdout)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        data = json.loads(stdout.splitlines()[-1])["payload"]["data"]
        self.assertEqual(data["execution"]["discarded"], 1)
        self.assertEqual(project_record.fetch_project(self.conn, KEYS[1])["notes"], "Changed while worker was fetching")
