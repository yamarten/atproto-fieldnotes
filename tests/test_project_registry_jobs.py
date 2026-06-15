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
from project_registry_web.jobs import JobConflictError, JobManager


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
        )

    def tearDown(self) -> None:
        self.manager.close(timeout=1.0)
        super().tearDown()

    def wait_for(self, job_id: str, statuses: set[str], timeout: float = 3.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.manager.get_job(job_id)
            if job["status"] in statuses:
                return job
            time.sleep(0.02)
        self.fail(f"job did not reach {statuses}: {self.manager.get_job(job_id)}")

    def test_subprocess_job_completes_and_keeps_events(self) -> None:
        job = self.manager.start_enrich({"all_projects": True})
        completed = self.wait_for(job["job_id"], {"completed"})

        self.assertEqual(completed["returncode"], 0)
        self.assertEqual(completed["result"]["status"], "completed")
        self.assertEqual([event["kind"] for event in completed["events"]], ["started", "completed"])
        self.assertFalse(completed["can_cancel"])
        self.assertFalse(completed["can_resume"])
        self.assertTrue(completed["request"]["parameters"]["checkpoint_path"].endswith(".json"))

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


if __name__ == "__main__":
    import unittest

    unittest.main()
