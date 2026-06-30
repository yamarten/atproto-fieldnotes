from __future__ import annotations

import json
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from helpers import SCRIPTS, TempRegistryDbTestCase, registry
from project_registry_core.jobs import OperationRequest
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
status = "cancelled" if cancelled else "completed"
write({"type":"event","payload":{"schema_version":1,"operation_id":operation_id,"operation":operation,"kind":status,"sequence":2,"at":now(),"data":{"summary":"enrich_all"}}})
write({"type":"result","payload":{"schema_version":1,"operation_id":operation_id,"operation":operation,"status":status,"started_at":now(),"completed_at":now(),"data":{"summary":"enrich_all"},"error":None}})
'''


class ProjectRegistryJobTest(TempRegistryDbTestCase):
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

    def wait_for(self, job_id: str, statuses: set[str], timeout: float = 3.0, manager: JobManager | None = None) -> dict:
        manager = manager or self.manager
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = manager.get_job(job_id)
            if job["status"] in statuses:
                return job
            time.sleep(0.02)
        self.fail(f"job did not reach {statuses}: {manager.get_job(job_id)}")

    def test_subprocess_job_completes_and_keeps_events(self) -> None:
        job = self.manager.start_enrich({"all_projects": True})
        completed = self.wait_for(job["job_id"], {"completed"})

        self.assertEqual(completed["returncode"], 0)
        self.assertEqual(completed["result"]["status"], "completed")
        self.assertEqual([event["kind"] for event in completed["events"]], ["started", "completed"])
        self.assertFalse(completed["can_cancel"])
        self.assertFalse(completed["can_resume"])
        self.assertTrue(completed["request"]["parameters"]["checkpoint_path"].endswith(".json"))

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

    def test_generic_sync_and_review_jobs_use_same_process_boundary(self) -> None:
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

    def test_manual_scheduled_preset_runs_sequence_steps(self) -> None:
        queued = self.manager.run_scheduled_preset("sync_sources_then_enrich")
        self.assertEqual(queued["queue"][0]["preset_id"], "sync_sources_then_enrich")

        started = self.wait_for(queued["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(started["operation"], "sync")
        self.assertEqual(started["request"]["parameters"]["source"], "github")

        for expected_source in ("collections", "semble", "tangled"):
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

        state = self.manager.run_scheduler_once()
        self.assertEqual(state["queue"], [])
        schedule = next(item for item in state["items"] if item["preset_id"] == "sync_sources_then_enrich")
        self.assertEqual(schedule["last_status"], "completed")

    def test_all_state_maintenance_runs_enrich_then_stalled_review(self) -> None:
        queued = self.manager.run_scheduled_preset("enrich_all_states")
        enrich_job = self.wait_for(queued["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(enrich_job["operation"], "enrich")
        self.assertTrue(enrich_job["request"]["parameters"]["all_tracking_states"])

        state = self.manager.run_scheduler_once()
        review_job = self.wait_for(state["queue"][0]["current_job_id"], {"completed"})
        self.assertEqual(review_job["operation"], "review")
        self.assertEqual(review_job["request"]["parameters"]["kind"], "stalled")
        self.assertTrue(review_job["request"]["execute"])

        state = self.manager.run_scheduler_once()
        self.assertEqual(state["queue"], [])
        schedule = next(item for item in state["items"] if item["preset_id"] == "enrich_all_states")
        self.assertEqual(schedule["last_status"], "completed")

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

    def test_stalled_review_limit_bounds_returned_candidates(self) -> None:
        projects = [{"project_key": f"proj:{index}"} for index in range(3)]

        def entry(project: dict, **_kwargs: object) -> dict:
            return {
                "project_key": project["project_key"],
                "suggested_tracking_state": "stalled",
            }

        conn = registry.open_db()
        try:
            with mock.patch.object(review_services.core, "list_projects", return_value=projects), mock.patch.object(
                review_services.core, "ensure_project_shape", side_effect=lambda project: project
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

        with mock.patch.object(review_services.core, "list_projects", return_value=projects), mock.patch.object(
            review_services.core, "ensure_project_shape", side_effect=lambda project: project
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

    def test_sync_reports_item_progress_and_honors_boundary_cancellation(self) -> None:
        entries = [{
            "rkey": "repo-1",
            "time_us": 10,
            "record": {},
            "atproto_relevant": False,
            "owner_did": "",
        }]
        seen: list[tuple[int, int, dict]] = []
        conn = registry.open_db()
        try:
            with mock.patch.object(registry, "fetch_tangled_ufos_window", return_value=entries):
                result = registry.sync_tangled_repos(
                    conn,
                    progress=lambda current, total, item: seen.append((current, total, item)),
                    cancel_requested=lambda: False,
                )
                cancelled = registry.sync_tangled_repos(conn, cancel_requested=lambda: True)
        finally:
            conn.close()
        self.assertEqual((seen[0][0], seen[0][1]), (1, 1))
        self.assertEqual(seen[0][2]["source"], "tangled")
        self.assertTrue(cancelled["cancelled"])
        self.assertFalse(cancelled["state_updated"])
        self.assertFalse(result["cancelled"])


if __name__ == "__main__":
    import unittest

    unittest.main()
