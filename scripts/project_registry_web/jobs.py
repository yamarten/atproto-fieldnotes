from __future__ import annotations

import collections
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from project_registry_core.jobs import OperationRequest, generate_operation_id, utc_now


ACTIVE_STATUSES = {"queued", "running", "cancelling"}
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}


class JobConflictError(RuntimeError):
    pass


@dataclass
class JobRecord:
    job_id: str
    request: OperationRequest
    status: str = "queued"
    created_at: str = field(default_factory=utc_now)
    started_at: str = ""
    completed_at: str = ""
    pid: int | None = None
    returncode: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    stderr: collections.deque[str] = field(default_factory=lambda: collections.deque(maxlen=50))
    process: subprocess.Popen[str] | None = field(default=None, repr=False)

    def snapshot(self, *, include_events: bool = False) -> dict[str, Any]:
        parameters = self.request.parameters
        checkpoint = Path(str(parameters.get("checkpoint_path") or ""))
        progress = next(
            (event.get("data", {}).get("progress") for event in reversed(self.events) if event.get("kind") == "progress"),
            None,
        )
        last_item = next(
            (event.get("data") for event in reversed(self.events) if event.get("kind") == "item_completed"),
            None,
        )
        elapsed_seconds: int | None = None
        eta_seconds: int | None = None
        if self.started_at and self.status in ACTIVE_STATUSES:
            try:
                started = dt.datetime.fromisoformat(self.started_at)
                elapsed_seconds = max(0, int((dt.datetime.now(dt.timezone.utc) - started).total_seconds()))
                current = int((progress or {}).get("current") or 0)
                total = int((progress or {}).get("total") or 0)
                if current > 0 and total > current and elapsed_seconds > 0:
                    eta_seconds = max(1, int(elapsed_seconds * (total - current) / current))
            except (TypeError, ValueError):
                pass
        output = {
            "job_id": self.job_id,
            "operation_id": self.request.operation_id,
            "operation": self.request.operation,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "pid": self.pid,
            "returncode": self.returncode,
            "request": self.request.to_dict(),
            "result": self.result,
            "error": self.error,
            "latest_event": self.events[-1] if self.events else None,
            "progress": progress,
            "elapsed_seconds": elapsed_seconds,
            "eta_seconds": eta_seconds,
            "last_item": last_item,
            "event_count": len(self.events),
            "can_cancel": self.status in {"queued", "running"},
            "can_resume": (
                self.request.operation == "enrich"
                and self.status in {"cancelled", "failed"}
                and bool(parameters.get("all_projects"))
                and bool(str(parameters.get("checkpoint_path") or ""))
                and checkpoint.exists()
            ),
        }
        if include_events:
            output["events"] = list(self.events)
            output["stderr"] = list(self.stderr)
        return output


class JobManager:
    def __init__(
        self,
        db_path: Path,
        *,
        worker_command: Iterable[str] | None = None,
        max_completed: int = 100,
        max_events: int = 200,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.worker_command = list(worker_command or [sys.executable, "-m", "project_registry_core.worker"])
        self.max_completed = max(1, int(max_completed))
        self.max_events = max(10, int(max_events))
        self._jobs: collections.OrderedDict[str, JobRecord] = collections.OrderedDict()
        self._lock = threading.RLock()
        self._scripts_root = Path(__file__).resolve().parents[2]

    def start_enrich(self, parameters: dict[str, Any], *, operation_id: str = "") -> dict[str, Any]:
        operation_id = str(operation_id or generate_operation_id("enrich"))
        parameters = dict(parameters)
        if parameters.get("all_projects") and not parameters.get("checkpoint_path"):
            parameters["checkpoint_path"] = str(
                self.db_path.parent / "enrich-runs" / f"{operation_id}.json"
            )
        request = OperationRequest.create(
            "enrich",
            operation_id=operation_id,
            parameters=parameters,
            execute=True,
        )
        return self.start(request)

    def start_sync(self, parameters: dict[str, Any], *, execute: bool = False) -> dict[str, Any]:
        request = OperationRequest.create(
            "sync",
            parameters=dict(parameters),
            execute=execute,
        )
        return self.start(request)

    def start_review(self, parameters: dict[str, Any], *, execute: bool = False) -> dict[str, Any]:
        request = OperationRequest.create(
            "review",
            parameters=dict(parameters),
            execute=execute,
        )
        return self.start(request)

    def start(self, request: OperationRequest) -> dict[str, Any]:
        with self._lock:
            active = next((job for job in self._jobs.values() if job.status in ACTIVE_STATUSES), None)
            if active is not None:
                raise JobConflictError(f"background job already active: {active.job_id}")
            job = JobRecord(job_id=f"job:{uuid.uuid4().hex[:12]}", request=request)
            self._jobs[job.job_id] = job
            self._trim_locked()
            try:
                self._spawn_locked(job)
            except Exception as exc:
                job.status = "failed"
                job.completed_at = utc_now()
                job.error = {"type": type(exc).__name__, "message": str(exc)}
                raise
            return job.snapshot(include_events=True)

    def list_jobs(self, *, limit: int = 50) -> dict[str, Any]:
        with self._lock:
            jobs = list(reversed(self._jobs.values()))[:max(1, min(500, int(limit)))]
            return {"count": len(jobs), "items": [job.snapshot() for job in jobs]}

    @property
    def checkpoint_root(self) -> Path:
        return self.db_path.parent / "enrich-runs"

    def list_checkpoints(self, *, limit: int = 100) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        root = self.checkpoint_root
        if not root.exists():
            return {"count": 0, "items": []}
        for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
            try:
                payload = json.loads(path.read_text())
                run_id = str(payload.get("run_id") or "")
                if not run_id or path.name != f"{run_id}.json":
                    continue
                selected = [str(item) for item in payload.get("selected_project_keys", []) if str(item)]
                completed = [str(item) for item in payload.get("completed_project_keys", []) if str(item)]
                status = str(payload.get("status") or "unknown")
                resumable = status != "completed" and len(completed) < len(selected)
                items.append({
                    "kind": "checkpoint",
                    "run_id": run_id,
                    "status": "orphaned" if status == "running" else status,
                    "checkpoint_status": status,
                    "updated_at": str(payload.get("updated_at") or ""),
                    "selected_project_count": len(selected),
                    "completed_project_count": len(completed),
                    "failed_project_count": len(payload.get("failed_projects", [])),
                    "can_resume": resumable,
                    "options": dict(payload.get("options") or {}),
                })
            except (OSError, ValueError, TypeError):
                continue
            if len(items) >= max(1, min(500, int(limit))):
                break
        return {"count": len(items), "items": items}

    def resume_checkpoint(self, run_id: str) -> dict[str, Any]:
        value = str(run_id or "").strip()
        if not value or any(part in value for part in ("/", "\\", "..")):
            raise ValueError("invalid checkpoint run id")
        path = self.checkpoint_root / f"{value}.json"
        if not path.is_file() or path.parent.resolve() != self.checkpoint_root.resolve():
            raise ValueError("enrich checkpoint is not available")
        payload = json.loads(path.read_text())
        if str(payload.get("run_id") or "") != value:
            raise ValueError("checkpoint run id mismatch")
        selected = [item for item in payload.get("selected_project_keys", []) if str(item)]
        completed = [item for item in payload.get("completed_project_keys", []) if str(item)]
        if str(payload.get("status") or "") == "completed" or len(completed) >= len(selected):
            raise ValueError("enrich checkpoint is already complete")
        parameters = dict(payload.get("options") or {})
        parameters.update({"all_projects": True, "resume_run": value, "checkpoint_path": str(path)})
        parameters.pop("llm_api_key_configured", None)
        return self.start_enrich(parameters, operation_id=value)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._require_locked(job_id)
            return job.snapshot(include_events=True)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._require_locked(job_id)
            if job.status not in ACTIVE_STATUSES:
                return job.snapshot(include_events=True)
            process = job.process
            if job.status == "queued":
                job.status = "cancelling"
            elif process is not None and process.poll() is None:
                job.status = "cancelling"
                process.send_signal(signal.SIGINT)
            return job.snapshot(include_events=True)

    def resume(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            previous = self._require_locked(job_id)
            if previous.status not in TERMINAL_STATUSES:
                raise JobConflictError(f"job is not resumable while {previous.status}: {job_id}")
            if not previous.request.parameters.get("all_projects"):
                raise ValueError("only bulk enrich jobs can be resumed")
            parameters = dict(previous.request.parameters)
            checkpoint = Path(str(parameters.get("checkpoint_path") or ""))
            if not str(parameters.get("checkpoint_path") or "") or not checkpoint.exists():
                raise ValueError("enrich checkpoint is not available")
            parameters["resume_run"] = previous.request.operation_id
            parameters["all_projects"] = True
        return self.start_enrich(parameters, operation_id=previous.request.operation_id)

    def close(self, *, timeout: float = 5.0) -> None:
        with self._lock:
            active = [job for job in self._jobs.values() if job.status in ACTIVE_STATUSES]
            for job in active:
                was_queued = job.status == "queued"
                job.status = "cancelling"
                if not was_queued and job.process is not None and job.process.poll() is None:
                    job.process.send_signal(signal.SIGINT)
            processes = [job.process for job in active if job.process is not None]
        for process in processes:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)

    def _spawn_locked(self, job: JobRecord) -> None:
        env = os.environ.copy()
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(self._scripts_root) + (os.pathsep + current_pythonpath if current_pythonpath else "")
        process = subprocess.Popen(
            self.worker_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(self._scripts_root.parent),
            env=env,
        )
        job.process = process
        job.pid = process.pid
        job.started_at = utc_now()
        payload = {
            "db_path": str(self.db_path),
            "request": job.request.to_dict(include_sensitive=True),
        }
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.close()
        except Exception:
            process.terminate()
            process.wait(timeout=1.0)
            raise
        threading.Thread(target=self._read_stderr, args=(job,), name=f"{job.job_id}-stderr", daemon=True).start()
        threading.Thread(target=self._monitor, args=(job,), name=f"{job.job_id}-monitor", daemon=True).start()

    def _read_stderr(self, job: JobRecord) -> None:
        process = job.process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                with self._lock:
                    job.stderr.append(line.rstrip())
        finally:
            process.stderr.close()

    def _monitor(self, job: JobRecord) -> None:
        process = job.process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    with self._lock:
                        job.stderr.append(f"invalid worker output: {line.rstrip()}")
                    continue
                with self._lock:
                    self._apply_message_locked(job, message)
        finally:
            process.stdout.close()
        returncode = process.wait()
        with self._lock:
            job.returncode = returncode
            job.completed_at = job.completed_at or utc_now()
            if job.status in ACTIVE_STATUSES:
                if job.result is not None:
                    job.status = str(job.result.get("status") or ("failed" if returncode else "completed"))
                else:
                    job.status = "failed" if returncode else "completed"
            if returncode and job.error is None:
                job.error = {
                    "type": "WorkerExit",
                    "message": f"worker exited with status {returncode}",
                }
            job.process = None
            self._trim_locked()

    def _apply_message_locked(self, job: JobRecord, message: Any) -> None:
        if not isinstance(message, dict):
            return
        kind = str(message.get("type") or "")
        if kind == "event":
            event = message.get("payload")
            if isinstance(event, dict):
                job.events.append(event)
                if len(job.events) > self.max_events:
                    del job.events[:len(job.events) - self.max_events]
                if event.get("kind") == "started":
                    if job.status == "cancelling":
                        if job.process is not None and job.process.poll() is None:
                            job.process.send_signal(signal.SIGINT)
                    else:
                        job.status = "running"
            return
        if kind == "result":
            result = message.get("payload")
            if isinstance(result, dict):
                job.result = result
                job.completed_at = str(result.get("completed_at") or utc_now())
                if isinstance(result.get("error"), dict):
                    job.error = result["error"]
            return
        if kind == "worker_error":
            error = message.get("error")
            job.error = error if isinstance(error, dict) else {"message": str(error)}
            job.completed_at = utc_now()

    def _require_locked(self, job_id: str) -> JobRecord:
        job = self._jobs.get(str(job_id or "").strip())
        if job is None:
            raise KeyError(f"job not found: {job_id}")
        return job

    def _trim_locked(self) -> None:
        completed = [job_id for job_id, job in self._jobs.items() if job.status in TERMINAL_STATUSES]
        for job_id in completed[:-self.max_completed]:
            self._jobs.pop(job_id, None)
