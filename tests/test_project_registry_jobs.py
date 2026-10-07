from __future__ import annotations

import json
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from helpers import ProjectRecordTestCase, SCRIPTS, TempRegistryDbTestCase, registry
from project_registry_core import source_sync, worker, runtime_view
from project_registry_core.jobs import CancellationToken, OperationRequest
from project_registry_core.application import review as review_services
from project_registry_web.jobs import JobConflictError, JobManager, JobRecord, _schedule_due


FAKE_WORKER = r'''
import datetime
import json
import signal
import sys
import time
from pathlib import Path

cancelled = False

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def stop(_signum, _frame):
    global cancelled
    cancelled = True

def write(value):
    print(json.dumps(value), flush=True)

signal.signal(signal.SIGINT, stop)
payload = json.load(sys.stdin)
request = payload["request"]
operation_id = request["operation_id"]
operation = request["operation"]
parameters = request.get("parameters", {})
checkpoint = parameters.get("checkpoint_path")
if checkpoint:
    path = Path(checkpoint)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
write({"type":"event","payload":{"schema_version":1,"operation_id":operation_id,"operation":operation,"kind":"started","sequence":1,"at":now(),"data":{"enrich_run":{"status":"running"}}}})
if parameters.get("wait_on_first") and not parameters.get("resume_run"):
    while not cancelled:
        time.sleep(0.02)
failed = bool(parameters.get("fail"))
status = "cancelled" if cancelled else ("failed" if failed else "completed")
source = str(parameters.get("source") or "")
summary = {
    "created": 1,
    "matched_existing": 2,
    "unmatched": 1,
    "state_updated": True,
    "imported_project_keys": [f"proj:{source}"],
    "unmatched_entries": [{"repo_url": f"https://example.test/{source}", "reason": "unmatched"}],
} if operation == "sync" else "enrich_all"
error = {"type":"SyntheticError","message":"synthetic sync failure"} if failed else None
write({"type":"event","payload":{"schema_version":1,"operation_id":operation_id,"operation":operation,"kind":status,"sequence":2,"at":now(),"data":{"summary":summary}}})
write({"type":"result","payload":{"schema_version":1,"operation_id":operation_id,"operation":operation,"status":status,"started_at":now(),"completed_at":now(),"data":{"summary":summary},"error":error}})
'''


class _ProjectRegistryJobFixtures:
    def wait_for(self, job_id: str, statuses: set[str], timeout: float = 3.0, manager: JobManager | None = None) -> dict:
        manager = manager or self.manager
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = manager.get_job(job_id)
            if job["status"] in statuses:
                return job
            time.sleep(0.02)
        self.fail(f"job did not reach {statuses}: {manager.get_job(job_id)}")


class ProjectRegistryJobUnitTest(_ProjectRegistryJobFixtures, ProjectRecordTestCase):
    def test_running_job_estimates_eta_from_latest_progress(self) -> None:
        started = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=100)).isoformat()
        record = JobRecord(
            job_id="job:eta",
            request=OperationRequest.create("enrich"),
            status="running",
            started_at=started,
            events=[{"kind": "progress", "data": {"progress": {"current": 2, "total": 10}}}],
        )
        snapshot = record.snapshot()
        self.assertGreaterEqual(snapshot["elapsed_seconds"], 99)
        self.assertGreaterEqual(snapshot["eta_seconds"], 396)
        self.assertLessEqual(snapshot["eta_seconds"], 404)

    def test_resumed_enrich_eta_uses_only_items_observed_after_resume(self) -> None:
        started = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=100)).isoformat()
        record = JobRecord(
            job_id="job:resumed-eta",
            request=OperationRequest.create("enrich", parameters={"resume_run": "run:original"}),
            status="running",
            started_at=started,
            events=[{"kind": "progress", "data": {"progress": {
                "current": 10,
                "total": 12,
                "eta_observed_count": 1,
                "eta_remaining_count": 3,
            }}}],
        )

        snapshot = record.snapshot()

        self.assertGreaterEqual(snapshot["eta_seconds"], 297)
        self.assertLessEqual(snapshot["eta_seconds"], 303)

    def test_resumed_enrich_eta_waits_for_one_observed_item(self) -> None:
        started = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=100)).isoformat()
        record = JobRecord(
            job_id="job:resumed-eta-first-item",
            request=OperationRequest.create("enrich", parameters={"resume_run": "run:original"}),
            status="running",
            started_at=started,
            events=[{"kind": "progress", "data": {"progress": {
                "current": 9,
                "total": 12,
                "eta_observed_count": 0,
                "eta_remaining_count": 4,
            }}}],
        )

        self.assertIsNone(record.snapshot()["eta_seconds"])

    def test_unknown_total_progress_is_preserved_without_eta(self) -> None:
        started = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=100)).isoformat()
        record = JobRecord(
            job_id="job:unknown-total",
            request=OperationRequest.create("sync", parameters={"source": "introduced"}),
            status="running",
            started_at=started,
            events=[{"kind": "progress", "data": {"progress": {
                "current": 1,
                "total": None,
                "unit": "page",
            }}}],
        )

        snapshot = record.snapshot()

        self.assertEqual(snapshot["progress"], {"current": 1, "total": None, "unit": "page"})
        self.assertIsNone(snapshot["eta_seconds"])

    def test_introduced_worker_emits_unknown_page_total(self) -> None:
        emitted = []

        def introduced(_conn, *, progress, **_kwargs):
            progress(1, None, {"kind": "source_page", "source": "introduced", "unit": "page"})
            return {"pages": 1}

        request = OperationRequest.create("sync", parameters={"source": "introduced"})
        with mock.patch.object(worker.sync, "introduced", side_effect=introduced):
            result = worker._run_simple(object(), request, CancellationToken(), emitted.append)

        progress_event = next(event for event in emitted if event.kind == "progress")
        self.assertEqual(progress_event.data["progress"], {"current": 1, "total": None, "unit": "page"})
        self.assertEqual(result.status, "completed")

    def test_stalled_worker_uses_no_limit_when_parameter_is_omitted(self) -> None:
        with mock.patch.object(worker.review, "stalled", return_value={}) as stalled:
            worker._run_review(
                object(),
                OperationRequest.create("review", parameters={"kind": "stalled"}),
            )
            worker._run_review(
                object(),
                OperationRequest.create("review", parameters={"kind": "stalled", "limit": 23}),
            )

        self.assertIsNone(stalled.call_args_list[0].kwargs["limit"])
        self.assertEqual(stalled.call_args_list[1].kwargs["limit"], 23)

    def test_browser_verification_worker_fixes_lane_and_requires_execute(self) -> None:
        request = OperationRequest.create(
            "verify_service",
            parameters={"project_key": "proj:example", "service_url": "https://example.test"},
            execute=True,
        )
        with mock.patch.object(worker.investigation, "verify_service", return_value={"mode": "execute"}) as verify:
            summary = worker._run_verify_service(object(), request)

        self.assertEqual(summary["mode"], "execute")
        verify.assert_called_once_with(
            mock.ANY,
            project_key="proj:example",
            lane="browser",
            service_url="https://example.test",
            execute=True,
        )
        with self.assertRaisesRegex(ValueError, "must save"):
            worker._run_verify_service(
                object(),
                OperationRequest.create("verify_service", parameters={"project_key": "proj:example"}),
            )

    def test_monthly_schedule_is_due_within_24_hours_only(self) -> None:
        schedule = {
            "enabled": True,
            "week_of_month": 2,
            "weekday": 1,
            "time": "01:00",
            "timezone": "Asia/Tokyo",
        }

        due, period, due_at = _schedule_due(schedule, dt.datetime(2026, 6, 8, 16, 0, tzinfo=dt.timezone.utc))
        self.assertTrue(due)
        self.assertEqual(period, "2026-06-W2-1")
        self.assertEqual(due_at, "2026-06-08T16:00:00+00:00")

        next_morning_due, next_morning_period, _ = _schedule_due(
            schedule,
            dt.datetime(2026, 6, 9, 14, 59, tzinfo=dt.timezone.utc),
        )
        self.assertTrue(next_morning_due)
        self.assertEqual(next_morning_period, "2026-06-W2-1")

        late_due, late_period, _ = _schedule_due(schedule, dt.datetime(2026, 6, 29, 14, 0, tzinfo=dt.timezone.utc))
        self.assertFalse(late_due)
        self.assertEqual(late_period, "2026-06-W2-1")

    def test_weekly_schedule_is_due_within_24_hours_only(self) -> None:
        schedule = {
            "enabled": True,
            "week_of_month": 0,
            "weekday": 0,
            "time": "08:00",
            "timezone": "Asia/Tokyo",
        }

        due, period, _ = _schedule_due(schedule, dt.datetime(2026, 6, 29, 0, 0, tzinfo=dt.timezone.utc))
        self.assertTrue(due)
        self.assertEqual(period, "2026-W27-0")

        next_day_due, next_day_period, _ = _schedule_due(schedule, dt.datetime(2026, 6, 29, 22, 59, tzinfo=dt.timezone.utc))
        self.assertTrue(next_day_due)
        self.assertEqual(next_day_period, "2026-W27-0")

        late_due, late_period, _ = _schedule_due(schedule, dt.datetime(2026, 6, 30, 23, 0, tzinfo=dt.timezone.utc))
        self.assertFalse(late_due)
        self.assertEqual(late_period, "2026-W27-0")


class ProjectRegistryJobTest(_ProjectRegistryJobFixtures, TempRegistryDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        conn = registry.open_db()
        conn.close()
        self.worker_path = Path(self.tempdir.name) / "fake_worker.py"
        self.worker_path.write_text(FAKE_WORKER)
        self.manager = JobManager(
            self.db_path,
            worker_command=[sys.executable, str(self.worker_path)],
            enable_scheduler=False,
        )

    def tearDown(self) -> None:
        self.manager.close(timeout=1.0)
        super().tearDown()

    def test_subprocess_job_completes_and_keeps_events(self) -> None:
        job = self.manager.start_enrich({"all_projects": True})
        completed = self.wait_for(job["job_id"], {"completed"})

        self.assertEqual(completed["returncode"], 0)
        self.assertEqual(completed["result"]["status"], "completed")
        self.assertEqual([event["kind"] for event in completed["events"]], ["started", "completed"])
        self.assertFalse(completed["can_cancel"])
        self.assertFalse(completed["can_resume"])
        self.assertTrue(completed["request"]["parameters"]["checkpoint_path"].endswith(".json"))

    def test_generic_sync_review_and_browser_jobs_use_same_process_boundary(self) -> None:
        sync_job = self.manager.start_sync({"source": "github"}, execute=False)
        sync_completed = self.wait_for(sync_job["job_id"], {"completed"})
        self.assertEqual(sync_completed["operation"], "sync")
        self.assertFalse(sync_completed["request"]["execute"])
        self.assertFalse(sync_completed["can_resume"])

        review_job = self.manager.start_review({"kind": "stalled"}, execute=True)
        review_completed = self.wait_for(review_job["job_id"], {"completed"})
        self.assertEqual(review_completed["operation"], "review")
        self.assertTrue(review_completed["request"]["execute"])
        self.assertFalse(review_completed["can_resume"])

        verify_job = self.manager.start_verify_service({
            "project_key": "proj:example",
            "service_url": "https://example.test",
        })
        verify_completed = self.wait_for(verify_job["job_id"], {"completed"})
        self.assertEqual(verify_completed["operation"], "verify_service")
        self.assertTrue(verify_completed["request"]["execute"])
        self.assertFalse(verify_completed["can_resume"])

    def test_partial_sync_is_a_failed_worker_result_with_summary(self) -> None:
        request = OperationRequest.create("sync", parameters={"source": "lexicons"}, execute=True)
        emitted = []
        with mock.patch.object(
            worker,
            "_run_sync",
            return_value={"operation_status": "partial", "inventory": {"status": "failed"}},
        ):
            result = worker._run_simple(str(self.db_path), request, CancellationToken(), emitted.append)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["summary"]["operation_status"], "partial")
        self.assertEqual(emitted[-1].kind, "failed")

    def test_service_observation_preset_dispatches_and_stays_disabled(self) -> None:
        queued = self.manager.run_scheduled_preset("service_observation")
        self.assertEqual(queued["queue"][0]["preset_id"], "service_observation")
        completed = self.wait_for(queued["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(completed["operation"], "monitor_services")
        self.assertEqual(completed["request"]["parameters"]["min_age_days"], 30)
        self.assertEqual(completed["request"]["parameters"]["limit"], 20)
        preset = next(item for item in self.manager.list_schedules()["items"] if item["preset_id"] == "service_observation")
        self.assertFalse(preset["schedule"]["enabled"])

    def test_manual_scheduled_preset_runs_sequence_steps(self) -> None:
        queued = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        self.assertEqual(queued["queue"][0]["preset_id"], "sync_sources_then_enrich")

        started = self.wait_for(queued["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(started["operation"], "sync")
        self.assertEqual(started["request"]["parameters"]["source"], "github")

        for expected_source in ("collections", "lexicons", "semble", "tangled"):
            state = self.manager.run_scheduler_once()
            current_job_id = state["queue"][0]["current_job_id"]
            completed = self.wait_for(current_job_id, {"completed"})
            self.assertEqual(completed["operation"], "sync")
            self.assertEqual(completed["request"]["parameters"]["source"], expected_source)

        state = self.manager.run_scheduler_once()
        enrich_job_id = state["queue"][0]["current_job_id"]
        completed = self.wait_for(enrich_job_id, {"completed"})
        self.assertEqual(completed["operation"], "enrich")
        self.assertTrue(completed["request"]["parameters"]["all_projects"])
        self.assertTrue(completed["request"]["parameters"]["new_only"])

        state = self.manager.run_scheduler_once()
        self.assertEqual(state["queue"], [])
        schedule = next(item for item in state["items"] if item["preset_id"] == "sync_sources_then_enrich")
        self.assertEqual(schedule["last_status"], "completed")
        self.assertNotIn("source_observations", state)

    def test_all_state_maintenance_runs_enrich_then_stalled_review(self) -> None:
        queued = self.manager.run_scheduled_preset("enrich_all_states")
        enrich_job = self.wait_for(queued["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(enrich_job["operation"], "enrich")
        self.assertTrue(enrich_job["request"]["parameters"]["all_tracking_states"])

        state = self.manager.run_scheduler_once()
        review_job = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(review_job["operation"], "review")
        self.assertEqual(review_job["request"]["parameters"]["kind"], "stalled")
        self.assertNotIn("limit", review_job["request"]["parameters"])
        self.assertTrue(review_job["request"]["execute"])

        state = self.manager.run_scheduler_once()
        self.assertEqual(state["queue"], [])
        schedule = next(item for item in state["items"] if item["preset_id"] == "enrich_all_states")
        self.assertEqual(schedule["last_status"], "completed")

    def test_conflicted_enrich_holds_dependent_review_and_resumes_after_restart(self) -> None:
        queued = self.manager.run_scheduled_preset("enrich_all_states")
        job_id = queued["queue"][0]["current_job_id"]
        completed = self.wait_for(job_id, {"completed"})
        with self.manager._lock:
            self.manager._jobs[job_id].status = "retry_wait"
        state = self.manager.run_scheduler_once()
        self.assertEqual(state["queue"], [])
        schedule = next(item for item in state["items"] if item["preset_id"] == "enrich_all_states")
        self.assertEqual(schedule["last_status"], "retry_wait")
        self.assertEqual([step["operation"] for step in schedule["pending_steps"]], ["enrich", "review"])
        self.assertEqual(schedule["last_steps"][-1]["status"], "deferred")
        self.manager.close(timeout=1)
        self.manager = JobManager(self.db_path, worker_command=[sys.executable, str(self.worker_path)], enable_scheduler=False)
        state = self.manager.run_scheduled_preset("enrich_all_states")
        resumed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(resumed["request"]["parameters"]["resume_run"], completed["operation_id"])
        state = self.manager.run_scheduler_once()
        reviewed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(reviewed["operation"], "review")
        state = self.manager.run_scheduler_once()
        schedule = next(item for item in state["items"] if item["preset_id"] == "enrich_all_states")
        self.assertEqual(schedule["last_status"], "completed")
        self.assertEqual(schedule["pending_steps"], [])

    def test_conflicted_source_continues_independent_steps_and_retries_only_pending(self) -> None:
        queued = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        job_id = queued["queue"][0]["current_job_id"]
        self.wait_for(job_id, {"completed"})
        with self.manager._lock:
            self.manager._jobs[job_id].status = "retry_wait"
        operations = []
        for _ in range(5):
            state = self.manager.run_scheduler_once()
            job = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
            operations.append(job["request"]["parameters"].get("source") or job["operation"])
        self.assertEqual(operations, ["collections", "lexicons", "semble", "tangled", "enrich"])
        state = self.manager.run_scheduler_once()
        schedule = next(item for item in state["items"] if item["preset_id"] == "sync_sources_then_enrich")
        self.assertEqual(schedule["last_status"], "retry_wait")
        self.assertEqual([step["operation"] for step in schedule["pending_steps"]], ["sync", "enrich"])
        state = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        job = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(job["request"]["parameters"]["source"], "github")
        state = self.manager.run_scheduler_once()
        job = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertTrue(job["request"]["parameters"]["new_only"])
        self.assertEqual(self.manager.run_scheduler_once()["queue"], [])

    def test_sync_and_enrich_conflicts_resume_checkpoint_then_select_new_projects(self) -> None:
        state = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        enrich_id = ""
        for index in range(6):
            job_id = state["queue"][0]["current_job_id"]
            job = self.wait_for(job_id, {"completed"})
            if index in {0, 5}:
                with self.manager._lock:
                    self.manager._jobs[job_id].status = "retry_wait"
            if index == 5:
                enrich_id = job["operation_id"]
            state = self.manager.run_scheduler_once()
        schedule = next(item for item in state["items"] if item["preset_id"] == "sync_sources_then_enrich")
        self.assertEqual([step["operation"] for step in schedule["pending_steps"]], ["sync", "enrich", "enrich"])
        state = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        state = self.manager.run_scheduler_once()
        resumed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(resumed["request"]["parameters"]["resume_run"], enrich_id)
        state = self.manager.run_scheduler_once()
        fresh = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertTrue(fresh["request"]["parameters"]["new_only"])
        self.assertNotIn("resume_run", fresh["request"]["parameters"])
        self.assertEqual(self.manager.run_scheduler_once()["queue"], [])

    def test_failed_sync_resumes_pending_after_restart_without_repeating_successes(self) -> None:
        preset_id = "sync_sources_then_enrich"
        state = self.manager.run_scheduled_preset(preset_id)
        for source in ("github", "collections", "lexicons", "semble"):
            job = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
            self.assertEqual(job["request"]["parameters"]["source"], source)
            if source != "semble":
                state = self.manager.run_scheduler_once()

        spawn = self.manager._spawn_locked

        def fail_sync(job):
            job.request.parameters["fail"] = True
            spawn(job)

        with mock.patch.object(self.manager, "_spawn_locked", side_effect=fail_sync):
            state = self.manager.run_scheduler_once()
        failed = self.wait_for(state["queue"][0]["current_job_id"], {"failed"})
        self.assertEqual(failed["request"]["parameters"]["source"], "tangled")
        state = self.manager.run_scheduler_once()
        self.assertEqual(state["queue"], [])
        schedule = next(item for item in state["items"] if item["preset_id"] == preset_id)
        self.assertEqual(schedule["last_status"], "retry_wait")
        self.assertIn("failed: sync tangled", schedule["last_message"])
        self.assertEqual([step["label"] for step in schedule["pending_steps"]], ["sync tangled", "enrich --all --new-only"])
        self.assertEqual([step["status"] for step in schedule["last_steps"]], ["completed"] * 4 + ["failed"])

        self.manager.close(timeout=1)
        self.manager = JobManager(self.db_path, worker_command=[sys.executable, str(self.worker_path)], enable_scheduler=False)
        persisted = next(item for item in self.manager.list_schedules()["items"] if item["preset_id"] == preset_id)
        self.assertEqual(persisted["pending_steps"], schedule["pending_steps"])
        self.assertEqual(persisted["last_steps"], schedule["last_steps"])
        self.manager.update_schedules([{
            "preset_id": preset_id, "enabled": True, "week_of_month": 0,
            "weekday": 0, "time": "08:00", "timezone": "Asia/Tokyo",
        }])
        state = self.manager.run_scheduler_once(dt.datetime(2026, 6, 29, 0, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(state["queue"], [])
        state = self.manager.run_scheduler_once(dt.datetime(2026, 7, 6, 0, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(state["queue"], [])
        self.assertEqual(self.manager.list_jobs()["count"], 0)
        # Keep the rest of this manual-resume test independent of the wall clock.
        self.manager.update_schedules([{"preset_id": preset_id, "enabled": False}])

        other = self.manager.run_scheduled_preset("sync_introduced")
        self.wait_for(other["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(self.manager.run_scheduler_once()["queue"], [])
        state = self.manager.run_scheduled_preset(preset_id)
        resumed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(resumed["request"]["parameters"]["source"], "tangled")
        self.assertNotIn("resume_run", resumed["request"]["parameters"])
        state = self.manager.run_scheduler_once()
        enriched = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertTrue(enriched["request"]["parameters"]["new_only"])
        state = self.manager.run_scheduler_once()
        schedule = next(item for item in state["items"] if item["preset_id"] == preset_id)
        self.assertEqual(schedule["last_status"], "completed")
        self.assertEqual(schedule["pending_steps"], [])
        self.assertEqual(self.manager.list_jobs()["count"], 3)

    def test_failed_and_cancelled_enrich_resume_checkpoint_before_dependent_review(self) -> None:
        for status in ("failed", "cancelled"):
            with self.subTest(status=status):
                spawn = self.manager._spawn_locked

                def interrupt_enrich(job):
                    job.request.parameters["fail" if status == "failed" else "wait_on_first"] = True
                    spawn(job)

                with mock.patch.object(self.manager, "_spawn_locked", side_effect=interrupt_enrich):
                    state = self.manager.run_scheduled_preset("enrich_all_states")
                job_id = state["queue"][0]["current_job_id"]
                if status == "cancelled":
                    self.wait_for(job_id, {"running"})
                    self.manager.cancel(job_id)
                stopped = self.wait_for(job_id, {status})
                state = self.manager.run_scheduler_once()
                self.assertEqual(state["queue"], [])
                schedule = next(item for item in state["items"] if item["preset_id"] == "enrich_all_states")
                self.assertEqual(schedule["last_status"], "retry_wait")
                self.assertEqual([step["operation"] for step in schedule["pending_steps"]], ["enrich", "review"])
                self.assertEqual(schedule["last_steps"][-1]["status"], status)
                parameters = schedule["pending_steps"][0]["parameters"]
                self.assertEqual(parameters["resume_run"], stopped["operation_id"])
                self.assertEqual(parameters["checkpoint_path"], stopped["request"]["parameters"]["checkpoint_path"])
                state = self.manager.run_scheduled_preset("enrich_all_states")
                resumed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
                self.assertEqual(resumed["operation_id"], stopped["operation_id"])
                self.assertEqual(resumed["request"]["parameters"]["checkpoint_path"], parameters["checkpoint_path"])
                state = self.manager.run_scheduler_once()
                reviewed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
                self.assertEqual(reviewed["operation"], "review")
                self.assertEqual(self.manager.run_scheduler_once()["queue"], [])

    def test_failed_review_retries_only_review(self) -> None:
        state = self.manager.run_scheduled_preset("enrich_all_states")
        self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        state = self.manager.run_scheduler_once()
        job_id = state["queue"][0]["current_job_id"]
        self.wait_for(job_id, {"completed"})
        with self.manager._lock:
            self.manager._jobs[job_id].status = "failed"
        state = self.manager.run_scheduler_once()
        schedule = next(item for item in state["items"] if item["preset_id"] == "enrich_all_states")
        self.assertEqual([step["operation"] for step in schedule["pending_steps"]], ["review"])
        state = self.manager.run_scheduled_preset("enrich_all_states")
        reviewed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(reviewed["operation"], "review")
        self.assertNotIn("resume_run", reviewed["request"]["parameters"])
        self.assertEqual(self.manager.run_scheduler_once()["queue"], [])

    def test_failure_keeps_earlier_conflicts_and_remaining_steps(self) -> None:
        state = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        for status in ("retry_wait", "failed"):
            job_id = state["queue"][0]["current_job_id"]
            self.wait_for(job_id, {"completed"})
            with self.manager._lock:
                self.manager._jobs[job_id].status = status
            state = self.manager.run_scheduler_once()
        schedule = next(item for item in state["items"] if item["preset_id"] == "sync_sources_then_enrich")
        expected = ["sync collections", "sync github", "sync lexicons", "sync semble", "sync tangled", "enrich --all --new-only"]
        self.assertEqual([step["label"] for step in schedule["pending_steps"]], expected)
        state = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        for label in expected:
            completed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
            self.assertEqual(completed["request"]["parameters"]["_scheduled_step"], label)
            state = self.manager.run_scheduler_once()
        self.assertEqual(state["queue"], [])

    def test_scheduled_worker_start_failure_is_held_until_manual_resume(self) -> None:
        with (
            mock.patch.object(self.manager, "_spawn_locked", side_effect=OSError("worker unavailable")),
            mock.patch("project_registry_web.jobs.traceback.print_exc"),
        ):
            state = self.manager.run_scheduled_preset("enrich_all")

        self.assertEqual(state["queue"], [])
        schedule = next(item for item in state["items"] if item["preset_id"] == "enrich_all")
        self.assertEqual(schedule["last_status"], "retry_wait")
        self.assertIn("could not start", schedule["last_message"])
        self.assertIn("worker unavailable", schedule["last_message"])
        self.assertEqual([step["operation"] for step in schedule["pending_steps"]], ["enrich"])
        self.assertNotIn("resume_run", schedule["pending_steps"][0]["parameters"])
        self.assertEqual(schedule["last_steps"][-1]["reason"], "worker_start_failed")

        second = self.manager.run_scheduler_once()
        self.assertEqual(second["queue"], [])
        self.assertEqual(
            next(item for item in second["items"] if item["preset_id"] == "enrich_all")["last_status"],
            "retry_wait",
        )
        state = self.manager.run_scheduled_preset("enrich_all")
        completed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertNotIn("resume_run", completed["request"]["parameters"])
        self.assertEqual(self.manager.run_scheduler_once()["queue"], [])

    def test_worker_start_failure_during_resume_preserves_only_unfinished_steps(self) -> None:
        state = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        job_id = state["queue"][0]["current_job_id"]
        self.wait_for(job_id, {"completed"})
        with self.manager._lock:
            self.manager._jobs[job_id].status = "retry_wait"
        for _ in range(5):
            state = self.manager.run_scheduler_once()
            self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.manager.run_scheduler_once()

        state = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        with (
            mock.patch.object(self.manager, "_spawn_locked", side_effect=OSError("worker unavailable")),
            mock.patch("project_registry_web.jobs.traceback.print_exc"),
        ):
            state = self.manager.run_scheduler_once()
        schedule = next(item for item in state["items"] if item["preset_id"] == "sync_sources_then_enrich")
        self.assertEqual([step["operation"] for step in schedule["pending_steps"]], ["enrich"])
        self.assertEqual(schedule["last_steps"][-2]["label"], "sync github")
        self.assertEqual(schedule["last_steps"][-2]["status"], "completed")
        state = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        completed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(completed["operation"], "enrich")
        self.assertTrue(completed["request"]["parameters"]["new_only"])
        self.assertEqual(self.manager.run_scheduler_once()["queue"], [])

    def test_enrich_worker_exit_before_checkpoint_restarts_step(self) -> None:
        with mock.patch.object(self.manager, "worker_command", [sys.executable, "-c", "raise SystemExit(1)"]):
            state = self.manager.run_scheduled_preset("enrich_all")
        stopped = self.wait_for(state["queue"][0]["current_job_id"], {"failed"})
        self.assertIsNone(stopped["result"])
        self.assertFalse(Path(stopped["request"]["parameters"]["checkpoint_path"]).exists())
        state = self.manager.run_scheduler_once()
        schedule = next(item for item in state["items"] if item["preset_id"] == "enrich_all")
        self.assertEqual(schedule["last_status"], "retry_wait")
        self.assertNotIn("resume_run", schedule["pending_steps"][0]["parameters"])
        state = self.manager.run_scheduled_preset("enrich_all")
        completed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertNotEqual(completed["operation_id"], stopped["operation_id"])
        self.assertNotIn("resume_run", completed["request"]["parameters"])
        self.assertEqual(self.manager.run_scheduler_once()["queue"], [])

    def test_schedule_queue_retries_running_step_after_manager_restart(self) -> None:
        state_path = self.db_path.parent / "job-schedules.json"
        state_path.write_text(json.dumps({
            "version": 1,
            "schedules": {
                "sync_sources_then_enrich": {
                    "preset_id": "sync_sources_then_enrich",
                    "enabled": False,
                    "week_of_month": 0,
                    "weekday": 2,
                    "time": "03:00",
                    "timezone": "Asia/Tokyo",
                }
            },
            "queue": [{
                "preset_id": "sync_sources_then_enrich",
                "period": "manual:restart",
                "reason": "manual",
                "queued_at": "2026-06-29T00:00:00+00:00",
                "remaining_steps": [{
                    "operation": "sync",
                    "label": "sync github",
                    "parameters": {"source": "github"},
                    "execute": True,
                }],
                "current_step": {
                    "operation": "sync",
                    "label": "sync github",
                    "parameters": {"source": "github"},
                    "execute": True,
                },
                "current_job_id": "job:lost",
                "completed_steps": [],
            }],
        }))
        restarted = JobManager(
            self.db_path,
            worker_command=[sys.executable, str(self.worker_path)],
            enable_scheduler=False,
        )
        try:
            state = restarted.list_schedules()
            self.assertEqual(state["queue"][0]["current_job_id"], "")
            schedule = next(item for item in state["items"] if item["preset_id"] == "sync_sources_then_enrich")
            self.assertEqual(schedule["last_status"], "deferred")

            state = restarted.run_scheduler_once()
            completed = self.wait_for(state["queue"][0]["current_job_id"], {"completed"}, manager=restarted)
            self.assertEqual(completed["request"]["parameters"]["source"], "github")
        finally:
            restarted.close(timeout=1.0)

    def test_cancel_conflict_and_resume_use_same_operation_checkpoint(self) -> None:
        first = self.manager.start_enrich({"all_projects": True, "wait_on_first": True})
        self.wait_for(first["job_id"], {"running"})
        with self.assertRaises(JobConflictError):
            self.manager.start_enrich({"all_projects": True})

        stopping = self.manager.cancel(first["job_id"])
        self.assertEqual(stopping["status"], "cancelling")
        cancelled = self.wait_for(first["job_id"], {"cancelled"})
        resumed = self.manager.resume(cancelled["job_id"])
        completed = self.wait_for(resumed["job_id"], {"completed"})

        self.assertEqual(completed["operation_id"], cancelled["operation_id"])
        self.assertEqual(
            completed["request"]["parameters"]["resume_run"],
            cancelled["operation_id"],
        )

    def test_discovers_and_resumes_orphaned_checkpoint_from_fixed_root(self) -> None:
        run_id = "enrich-discovered"
        root = self.db_path.parent / "enrich-runs"
        root.mkdir()
        checkpoint = root / f"{run_id}.json"
        checkpoint.write_text(json.dumps({
            "version": 1,
            "run_id": run_id,
            "status": "running",
            "updated_at": "2026-06-18T00:00:00+00:00",
            "options": {"include_stable": True},
            "selected_project_keys": ["proj:a", "proj:b"],
            "completed_project_keys": ["proj:a"],
            "failed_projects": [],
        }))

        discovered = self.manager.list_checkpoints()
        self.assertEqual(discovered["items"][0]["status"], "orphaned")
        self.assertTrue(discovered["items"][0]["can_resume"])
        resumed = self.manager.resume_checkpoint(run_id)
        completed = self.wait_for(resumed["job_id"], {"completed"})
        self.assertEqual(completed["operation_id"], run_id)
        self.assertEqual(completed["request"]["parameters"]["resume_run"], run_id)

        with self.assertRaisesRegex(ValueError, "invalid checkpoint"):
            self.manager.resume_checkpoint("../outside")

    def test_real_worker_protocol_completes_empty_bulk_enrich_without_network(self) -> None:
        checkpoint = Path(self.tempdir.name) / "empty-run.json"
        request = OperationRequest.create(
            "enrich",
            operation_id="empty-worker-test",
            parameters={
                "all_projects": True,
                "checkpoint_path": str(checkpoint),
            },
            execute=True,
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SCRIPTS) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        result = subprocess.run(
            [sys.executable, "-m", "project_registry_core.worker"],
            input=json.dumps({"db_path": str(self.db_path), "request": request.to_dict(include_sensitive=True)}),
            text=True,
            capture_output=True,
            env=env,
            cwd=str(SCRIPTS.parent),
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(messages[-1]["type"], "result")
        self.assertEqual(messages[-1]["payload"]["status"], "completed")
        self.assertTrue(checkpoint.exists())

    def test_real_worker_completes_review_dry_run_without_network(self) -> None:
        request = OperationRequest.create(
            "review",
            operation_id="empty-review-test",
            parameters={"kind": "stalled"},
            execute=False,
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SCRIPTS) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        result = subprocess.run(
            [sys.executable, "-m", "project_registry_core.worker"],
            input=json.dumps({"db_path": str(self.db_path), "request": request.to_dict(include_sensitive=True)}),
            text=True,
            capture_output=True,
            env=env,
            cwd=str(SCRIPTS.parent),
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([message["type"] for message in messages], ["event", "event", "result"])
        payload = messages[-1]["payload"]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["data"]["summary"]["mode"], "dry_run")
        self.assertEqual(payload["data"]["summary"]["candidate_count"], 0)

    def test_sync_reports_item_progress_and_honors_boundary_cancellation(self) -> None:
        snapshot = {
            "events": [{
                "rkey": "repo-1", "seq": 10, "record": {},
                "atproto_relevant": False, "owner_did": "did:plc:example",
                "operation": "create", "time": "2026-08-30T00:00:00Z",
            }],
            "event_count": 1,
            "sealed_tip_seq": 10,
            "last_event_seq": 10,
            "truncated": False,
            "cancelled": False,
        }
        seen: list[tuple[int, int, dict]] = []
        conn = registry.open_db()
        try:
            with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=snapshot):
                result = source_sync.sync_tangled_repos(
                    conn,
                    progress=lambda current, total, item: seen.append((current, total, item)),
                    cancel_requested=lambda: False,
                )
                cancelled = source_sync.sync_tangled_repos(conn, cancel_requested=lambda: True)
        finally:
            conn.close()
        self.assertEqual((seen[0][0], seen[0][1]), (1, 1))
        self.assertEqual(seen[0][2]["source"], "tangled")
        self.assertTrue(cancelled["cancelled"])
        self.assertFalse(cancelled["state_updated"])
        self.assertFalse(result["cancelled"])

    def test_collection_sync_bounds_processing_without_advancing_state(self) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        entries = [
            {"collection": "app.one.record", "min": now, "max": now, "count": 1},
            {"collection": "app.two.record", "min": now, "max": now, "count": 1},
        ]
        conn = registry.open_db()
        try:
            with mock.patch.object(source_sync, "fetch_collection_inventory", return_value=entries):
                summary = source_sync.sync_collection_inventory(
                    conn,
                    source_url="https://example.test/collections.json",
                    execute=True,
                    create_unmatched=True,
                    lookback_days=10,
                    max_items=1,
                )
            state = registry.read_sync_state(
                conn,
                "collections",
                {"source_url": "https://example.test/collections.json"},
            )
        finally:
            conn.close()

        self.assertEqual(summary["fetched_entries"], 2)
        self.assertEqual(summary["processed_entries"], 1)
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["created"], 1)
        self.assertFalse(summary["state_updated"])
        self.assertEqual(state["watermark"], {})
        self.assertEqual(state["last_success_at"], "")

    def test_tangled_truncated_sync_advances_to_last_processed_sequence(self) -> None:
        snapshot = {
            "events": [{
                "rkey": "repo-1", "seq": 10, "record": {},
                "atproto_relevant": False, "owner_did": "did:plc:example",
                "operation": "create", "time": "2026-08-30T00:00:00Z",
            }],
            "event_count": 1,
            "sealed_tip_seq": 20,
            "last_event_seq": 10,
            "truncated": True,
            "cancelled": False,
        }
        conn = registry.open_db()
        try:
            with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=snapshot):
                summary = source_sync.sync_tangled_repos(conn, execute=True, max_items=1)
            state = registry.read_sync_state(
                conn,
                "tangled-repos",
                {
                    "source": "jetstream_v2",
                    "service": "https://jetstream.us-west.bsky.network",
                    "collection": registry.tangled_core.TANGLED_COLLECTION,
                },
            )
        finally:
            conn.close()

        self.assertEqual(summary["observed"], 1)
        self.assertEqual(summary["fetched"], 1)
        self.assertTrue(summary["truncated"])
        self.assertTrue(summary["state_updated"])
        self.assertEqual(state["watermark"]["seq"], 10)

    def test_stalled_review_limit_bounds_returned_candidates(self) -> None:
        projects = [{"project_key": f"proj:{index}", runtime_view.RUNTIME_PROJECT_REVISION: 1} for index in range(3)]

        def entry(project: dict, **_kwargs: object) -> dict:
            return {
                "project_key": project["project_key"],
                "suggested_tracking_state": "stalled",
            }

        conn = registry.open_db()
        try:
            with mock.patch.object(review_services.project_record, "list_projects", return_value=projects), mock.patch.object(
                review_services.project_record, "ensure_project_shape", side_effect=lambda project: project
            ), mock.patch.object(review_services.core, "stalled_review_entry", side_effect=entry):
                payload = review_services.stalled(
                    conn,
                    limit=2,
                    now=dt.datetime(2026, 6, 15, tzinfo=dt.timezone.utc),
                )
        finally:
            conn.close()

        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["total_candidate_count"], 3)
        self.assertEqual(len(payload["candidates"]), 2)
        self.assertTrue(payload["plan_fingerprint"].startswith("plan:"))
        self.assertTrue(all(item["candidate_id"].startswith("candidate:") for item in payload["candidates"]))

        with mock.patch.object(review_services.project_record, "list_projects", return_value=projects), mock.patch.object(
            review_services.project_record, "ensure_project_shape", side_effect=lambda project: project
        ), mock.patch.object(review_services.core, "stalled_review_entry", side_effect=entry):
            with self.assertRaisesRegex(ValueError, "plan changed"):
                conn = registry.open_db()
                try:
                    review_services.stalled(
                        conn,
                        limit=2,
                        expected_plan_fingerprint="plan:stale",
                        now=dt.datetime(2026, 6, 15, tzinfo=dt.timezone.utc),
                    )
                finally:
                    conn.close()



if __name__ == "__main__":
    import unittest

    unittest.main()
