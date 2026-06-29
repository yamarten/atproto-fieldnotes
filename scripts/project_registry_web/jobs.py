from __future__ import annotations

import collections
import calendar
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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from project_registry_core.jobs import OperationRequest, generate_operation_id, utc_now


ACTIVE_STATUSES = {"queued", "running", "cancelling"}
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}
WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


SCHEDULE_PRESETS: dict[str, dict[str, Any]] = {
    "enrich_all": {
        "label": "Enrich all",
        "description": "Run normal enrich --all for active/watchlist projects.",
        "default_schedule": {"enabled": False, "week_of_month": 0, "weekday": 4, "time": "03:00", "timezone": "Asia/Tokyo"},
    },
    "sync_introduced": {
        "label": "Sync introduced",
        "description": "Run introduced-post sync with SQLite updates.",
        "default_schedule": {"enabled": False, "week_of_month": 0, "weekday": 1, "time": "03:00", "timezone": "Asia/Tokyo"},
    },
    "sync_sources_then_enrich": {
        "label": "Sync sources + enrich",
        "description": "Run non-introduced sync sources, then normal enrich --all.",
        "default_schedule": {"enabled": False, "week_of_month": 0, "weekday": 2, "time": "03:00", "timezone": "Asia/Tokyo"},
    },
    "enrich_all_states": {
        "label": "All-state maintenance",
        "description": "Run enrich --all across every tracking state, then apply stalled review updates.",
        "default_schedule": {"enabled": False, "week_of_month": 1, "weekday": 6, "time": "04:00", "timezone": "Asia/Tokyo"},
    },
}


class JobConflictError(RuntimeError):
    pass


def _step(operation: str, label: str, parameters: dict[str, Any], *, execute: bool = True) -> dict[str, Any]:
    return {
        "operation": operation,
        "label": label,
        "parameters": dict(parameters),
        "execute": bool(execute),
    }


def preset_steps(preset_id: str) -> list[dict[str, Any]]:
    if preset_id == "enrich_all":
        return [_step("enrich", "enrich --all", {"all_projects": True})]
    if preset_id == "enrich_all_states":
        return [
            _step("enrich", "enrich --all --all-tracking-states", {"all_projects": True, "all_tracking_states": True}),
            _step("review", "review stalled --execute", {
                "kind": "stalled",
                "inactivity_days": 45,
                "max_enrich_age_days": 30,
                "limit": 500,
                "note": "auto-updated by scheduled all-state maintenance",
            }),
        ]
    if preset_id == "sync_introduced":
        return [_step("sync", "sync introduced", {
            "source": "introduced",
            "incremental": True,
            "all_history": False,
            "lookback_days": 7,
        })]
    if preset_id == "sync_sources_then_enrich":
        return [
            _step("sync", "sync github", {
                "source": "github",
                "create_unmatched": True,
                "screen_unmatched": True,
                "incremental": True,
                "all_history": False,
                "lookback_days": 10,
            }),
            _step("sync", "sync collections", {
                "source": "collections",
                "create_unmatched": True,
                "incremental": True,
                "all_history": False,
                "lookback_days": 10,
            }),
            _step("sync", "sync semble", {
                "source": "semble",
                "create_unmatched": True,
                "incremental": True,
                "all_history": False,
                "lookback_days": 14,
            }),
            _step("sync", "sync tangled", {
                "source": "tangled",
                "create_unmatched": False,
                "incremental": True,
                "all_history": False,
            }),
            _step("enrich", "enrich --all", {"all_projects": True}),
        ]
    raise ValueError(f"unknown schedule preset: {preset_id}")


def _parse_schedule_time(value: str) -> tuple[int, int]:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        raise ValueError("schedule time must be HH:MM")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("schedule time must be HH:MM")
    return hour, minute


def _timezone(value: str) -> dt.tzinfo:
    name = str(value or "Asia/Tokyo").strip() or "Asia/Tokyo"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown schedule timezone: {name}") from exc


def _normalize_schedule(raw: dict[str, Any], *, defaults: dict[str, Any]) -> dict[str, Any]:
    merged = {**defaults, **{key: value for key, value in raw.items() if key in {"enabled", "week_of_month", "weekday", "time", "timezone"}}}
    week_of_month = int(merged.get("week_of_month") or 0)
    if week_of_month < 0 or week_of_month > 5:
        raise ValueError("schedule week_of_month must be between 0 and 5")
    weekday = int(merged.get("weekday") or 0)
    if weekday < 0 or weekday > 6:
        raise ValueError("schedule weekday must be between 0 and 6")
    hour, minute = _parse_schedule_time(str(merged.get("time") or "03:00"))
    timezone = str(merged.get("timezone") or "Asia/Tokyo").strip() or "Asia/Tokyo"
    _timezone(timezone)
    return {
        "enabled": bool(merged.get("enabled")),
        "week_of_month": week_of_month,
        "weekday": weekday,
        "time": f"{hour:02d}:{minute:02d}",
        "timezone": timezone,
    }


def _nth_weekday_date(year: int, month: int, weekday: int, week_of_month: int) -> dt.date | None:
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    day = 1 + offset + 7 * (week_of_month - 1)
    if day > calendar.monthrange(year, month)[1]:
        return None
    return dt.date(year, month, day)


def _schedule_due(schedule: dict[str, Any], now_utc: dt.datetime) -> tuple[bool, str, str]:
    timezone = _timezone(str(schedule.get("timezone") or "Asia/Tokyo"))
    local = now_utc.astimezone(timezone)
    hour, minute = _parse_schedule_time(str(schedule.get("time") or "03:00"))
    weekday = max(0, min(6, int(schedule.get("weekday") or 0)))
    week_of_month = max(0, min(5, int(schedule.get("week_of_month") or 0)))
    if week_of_month == 0:
        target_date = (local - dt.timedelta(days=local.weekday() - weekday)).date()
        target = dt.datetime.combine(target_date, dt.time(hour, minute), tzinfo=timezone)
        iso = target_date.isocalendar()
        period = f"{iso.year}-W{iso.week:02d}-{weekday}"
    else:
        target_date = _nth_weekday_date(local.year, local.month, weekday, week_of_month)
        if target_date is None:
            period = f"{local.year}-{local.month:02d}-W{week_of_month}-{weekday}"
            return False, period, ""
        target = dt.datetime.combine(target_date, dt.time(hour, minute), tzinfo=timezone)
        period = f"{local.year}-{local.month:02d}-W{week_of_month}-{weekday}"
    return local >= target, period, target.astimezone(dt.timezone.utc).isoformat()


def _schedule_next_due_at(schedule: dict[str, Any], now_utc: dt.datetime) -> str:
    timezone = _timezone(str(schedule.get("timezone") or "Asia/Tokyo"))
    local = now_utc.astimezone(timezone)
    hour, minute = _parse_schedule_time(str(schedule.get("time") or "03:00"))
    weekday = max(0, min(6, int(schedule.get("weekday") or 0)))
    week_of_month = max(0, min(5, int(schedule.get("week_of_month") or 0)))
    if week_of_month == 0:
        days_ahead = (weekday - local.weekday()) % 7
        target_date = (local + dt.timedelta(days=days_ahead)).date()
        target = dt.datetime.combine(target_date, dt.time(hour, minute), tzinfo=timezone)
        if target <= local:
            target += dt.timedelta(days=7)
    else:
        year = local.year
        month = local.month
        while True:
            target_date = _nth_weekday_date(year, month, weekday, week_of_month)
            if target_date is not None:
                target = dt.datetime.combine(target_date, dt.time(hour, minute), tzinfo=timezone)
                if target > local:
                    break
            else:
                target = None
            if target is not None and target > local:
                break
            month += 1
            if month > 12:
                year += 1
                month = 1
    return target.astimezone(dt.timezone.utc).isoformat()


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
        enable_scheduler: bool = True,
        scheduler_interval_seconds: float = 60.0,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.worker_command = list(worker_command or [sys.executable, "-m", "project_registry_core.worker"])
        self.max_completed = max(1, int(max_completed))
        self.max_events = max(10, int(max_events))
        self._jobs: collections.OrderedDict[str, JobRecord] = collections.OrderedDict()
        self._lock = threading.RLock()
        self._scripts_root = Path(__file__).resolve().parents[2]
        self._schedule_path = self.db_path.parent / "job-schedules.json"
        self._schedule_state = self._load_schedule_state()
        self._scheduler_interval_seconds = max(5.0, float(scheduler_interval_seconds))
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: threading.Thread | None = None
        if enable_scheduler:
            self._scheduler_thread = threading.Thread(target=self._scheduler_loop, name="project-registry-job-scheduler", daemon=True)
            self._scheduler_thread.start()

    def start_enrich(self, parameters: dict[str, Any], *, operation_id: str = "") -> dict[str, Any]:
        request = self._enrich_request(parameters, operation_id=operation_id)
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
            job = self._start_locked(request)
            return job.snapshot(include_events=True)

    def list_schedules(self) -> dict[str, Any]:
        with self._lock:
            return self._schedule_snapshot_locked()

    def update_schedules(self, items: Any) -> dict[str, Any]:
        if isinstance(items, dict):
            raw_items = items.get("items") or items.get("schedules") or []
        else:
            raw_items = items
        if isinstance(raw_items, dict):
            iterable = [{"preset_id": key, **dict(value)} for key, value in raw_items.items()]
        elif isinstance(raw_items, list):
            iterable = raw_items
        else:
            raise ValueError("schedules must be an array or object")
        with self._lock:
            schedules = self._schedule_state.setdefault("schedules", {})
            for raw in iterable:
                if not isinstance(raw, dict):
                    raise ValueError("schedule item must be an object")
                preset_id = str(raw.get("preset_id") or "").strip()
                if preset_id not in SCHEDULE_PRESETS:
                    raise ValueError(f"unknown schedule preset: {preset_id}")
                existing = dict(schedules.get(preset_id) or {})
                defaults = SCHEDULE_PRESETS[preset_id]["default_schedule"]
                normalized = _normalize_schedule({**existing, **raw}, defaults=defaults)
                schedules[preset_id] = {
                    **existing,
                    **normalized,
                    "preset_id": preset_id,
                    "updated_at": utc_now(),
                }
            self._save_schedule_state_locked()
            self.run_scheduler_once_locked()
            return self._schedule_snapshot_locked()

    def run_scheduled_preset(self, preset_id: str) -> dict[str, Any]:
        value = str(preset_id or "").strip()
        if value not in SCHEDULE_PRESETS:
            raise ValueError(f"unknown schedule preset: {value}")
        with self._lock:
            self._enqueue_schedule_locked(value, period=f"manual:{uuid.uuid4().hex[:8]}", reason="manual")
            self.run_scheduler_once_locked()
            return self._schedule_snapshot_locked()

    def _enrich_request(self, parameters: dict[str, Any], *, operation_id: str = "") -> OperationRequest:
        operation_id = str(operation_id or generate_operation_id("enrich"))
        parameters = dict(parameters)
        if parameters.get("all_projects") and not parameters.get("checkpoint_path"):
            parameters["checkpoint_path"] = str(
                self.db_path.parent / "enrich-runs" / f"{operation_id}.json"
            )
        return OperationRequest.create(
            "enrich",
            operation_id=operation_id,
            parameters=parameters,
            execute=True,
        )

    def _request_for_schedule_step(self, step: dict[str, Any], preset_id: str) -> OperationRequest:
        operation = str(step.get("operation") or "")
        label = str(step.get("label") or operation)
        parameters = {
            **dict(step.get("parameters") or {}),
            "_scheduled_preset_id": preset_id,
            "_scheduled_step": label,
        }
        if operation == "enrich":
            return self._enrich_request(parameters)
        if operation == "sync":
            return OperationRequest.create("sync", parameters=parameters, execute=bool(step.get("execute", True)))
        if operation == "review":
            return OperationRequest.create("review", parameters=parameters, execute=bool(step.get("execute", True)))
        raise ValueError(f"unsupported scheduled operation: {operation}")

    def _start_locked(self, request: OperationRequest) -> JobRecord:
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
        return job

    def _load_schedule_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self._schedule_path.exists():
            try:
                value = json.loads(self._schedule_path.read_text())
                if isinstance(value, dict):
                    payload = value
            except (OSError, ValueError, TypeError):
                payload = {}
        schedules = payload.get("schedules") if isinstance(payload.get("schedules"), dict) else {}
        normalized: dict[str, dict[str, Any]] = {}
        for preset_id, preset in SCHEDULE_PRESETS.items():
            existing = dict(schedules.get(preset_id) or {})
            defaults = dict(preset["default_schedule"])
            try:
                schedule = _normalize_schedule(existing, defaults=defaults)
            except ValueError:
                schedule = _normalize_schedule(defaults, defaults=defaults)
            normalized[preset_id] = {**existing, **schedule, "preset_id": preset_id}
        queue = payload.get("queue") if isinstance(payload.get("queue"), list) else []
        normalized_queue: list[dict[str, Any]] = []
        for item in queue:
            if not isinstance(item, dict):
                continue
            preset_id = str(item.get("preset_id") or "")
            if preset_id not in SCHEDULE_PRESETS:
                continue
            entry = dict(item)
            if str(entry.get("current_job_id") or ""):
                schedule = normalized.setdefault(preset_id, {"preset_id": preset_id})
                schedule.update({
                    "last_status": "deferred",
                    "last_message": "previous scheduled worker was interrupted; retrying step",
                })
                entry["current_job_id"] = ""
                entry["current_step"] = None
            normalized_queue.append(entry)
        return {
            "version": 1,
            "updated_at": str(payload.get("updated_at") or ""),
            "schedules": normalized,
            "queue": normalized_queue,
        }

    def _save_schedule_state_locked(self) -> None:
        self._schedule_state["updated_at"] = utc_now()
        self._schedule_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._schedule_path.with_suffix(self._schedule_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._schedule_state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        tmp.replace(self._schedule_path)

    def _schedule_snapshot_locked(self) -> dict[str, Any]:
        now = dt.datetime.now(dt.timezone.utc)
        schedules = self._schedule_state.setdefault("schedules", {})
        items: list[dict[str, Any]] = []
        for preset_id, preset in SCHEDULE_PRESETS.items():
            schedule = dict(schedules.get(preset_id) or {})
            due, period, _due_at = _schedule_due(schedule, now)
            items.append({
                "preset_id": preset_id,
                "label": preset["label"],
                "description": preset["description"],
                "steps": [{"operation": step["operation"], "label": step["label"]} for step in preset_steps(preset_id)],
                "schedule": {key: schedule.get(key) for key in ("enabled", "week_of_month", "weekday", "time", "timezone")},
                "weekday_label": WEEKDAY_LABELS[int(schedule.get("weekday") or 0)],
                "due_now": bool(due and schedule.get("enabled") and schedule.get("last_period") != period),
                "next_due_at": _schedule_next_due_at(schedule, now),
                "last_period": str(schedule.get("last_period") or ""),
                "last_queued_at": str(schedule.get("last_queued_at") or ""),
                "last_started_at": str(schedule.get("last_started_at") or ""),
                "last_completed_at": str(schedule.get("last_completed_at") or ""),
                "last_status": str(schedule.get("last_status") or ""),
                "last_message": str(schedule.get("last_message") or ""),
            })
        return {
            "version": 1,
            "updated_at": str(self._schedule_state.get("updated_at") or ""),
            "items": items,
            "queue": list(self._schedule_state.get("queue") or []),
        }

    def _enqueue_schedule_locked(self, preset_id: str, *, period: str, reason: str) -> None:
        queue = self._schedule_state.setdefault("queue", [])
        if any(str(item.get("preset_id") or "") == preset_id and str(item.get("period") or "") == period for item in queue if isinstance(item, dict)):
            return
        schedules = self._schedule_state.setdefault("schedules", {})
        schedule = schedules.setdefault(preset_id, {"preset_id": preset_id})
        status = "deferred" if self._active_job_locked() is not None else "queued"
        schedule.update({
            "last_period": period,
            "last_queued_at": utc_now(),
            "last_status": status,
            "last_message": "queued by scheduler" if reason == "schedule" else "queued manually",
        })
        queue.append({
            "preset_id": preset_id,
            "period": period,
            "reason": reason,
            "queued_at": utc_now(),
            "remaining_steps": preset_steps(preset_id),
            "current_step": None,
            "current_job_id": "",
            "completed_steps": [],
        })
        self._save_schedule_state_locked()

    def _active_job_locked(self) -> JobRecord | None:
        return next((job for job in self._jobs.values() if job.status in ACTIVE_STATUSES), None)

    def _advance_schedule_queue_locked(self) -> None:
        queue = self._schedule_state.setdefault("queue", [])
        if not queue:
            return
        current = queue[0]
        preset_id = str(current.get("preset_id") or "")
        schedules = self._schedule_state.setdefault("schedules", {})
        schedule = schedules.setdefault(preset_id, {"preset_id": preset_id})
        current_job_id = str(current.get("current_job_id") or "")
        if current_job_id:
            job = self._jobs.get(current_job_id)
            if job is None or job.status not in TERMINAL_STATUSES:
                return
            completed = list(current.get("completed_steps") or [])
            current_step = current.get("current_step") if isinstance(current.get("current_step"), dict) else {}
            completed.append({
                "label": str(current_step.get("label") or ""),
                "job_id": current_job_id,
                "status": job.status,
                "completed_at": job.completed_at,
            })
            current["completed_steps"] = completed
            current["current_job_id"] = ""
            current["current_step"] = None
            remaining = list(current.get("remaining_steps") or [])
            if remaining:
                current["remaining_steps"] = remaining[1:]
            if job.status != "completed":
                schedule.update({
                    "last_completed_at": job.completed_at or utc_now(),
                    "last_status": "failed",
                    "last_message": f"scheduled step failed: {current_step.get('label') or current_job_id}",
                })
                queue.pop(0)
                self._save_schedule_state_locked()
                return
        if self._active_job_locked() is not None:
            schedule.update({"last_status": "deferred", "last_message": "waiting for active background job"})
            self._save_schedule_state_locked()
            return
        remaining = list(current.get("remaining_steps") or [])
        if not remaining:
            schedule.update({
                "last_completed_at": utc_now(),
                "last_status": "completed",
                "last_message": "scheduled preset completed",
            })
            queue.pop(0)
            self._save_schedule_state_locked()
            return
        step = dict(remaining[0])
        request = self._request_for_schedule_step(step, preset_id)
        job = self._start_locked(request)
        current["current_job_id"] = job.job_id
        current["current_step"] = step
        schedule.update({
            "last_started_at": utc_now(),
            "last_status": "running",
            "last_message": f"running {step.get('label') or step.get('operation')}",
        })
        self._save_schedule_state_locked()

    def _enqueue_due_schedules_locked(self, now: dt.datetime) -> None:
        schedules = self._schedule_state.setdefault("schedules", {})
        queued_keys = {
            (str(item.get("preset_id") or ""), str(item.get("period") or ""))
            for item in self._schedule_state.get("queue", [])
            if isinstance(item, dict)
        }
        for preset_id in SCHEDULE_PRESETS:
            schedule = schedules.setdefault(preset_id, {"preset_id": preset_id})
            if not bool(schedule.get("enabled")):
                continue
            due, period, _due_at = _schedule_due(schedule, now)
            if not due or str(schedule.get("last_period") or "") == period or (preset_id, period) in queued_keys:
                continue
            self._enqueue_schedule_locked(preset_id, period=period, reason="schedule")

    def run_scheduler_once_locked(self, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now(dt.timezone.utc)
        self._advance_schedule_queue_locked()
        self._enqueue_due_schedules_locked(now)
        self._advance_schedule_queue_locked()

    def run_scheduler_once(self, now: dt.datetime | None = None) -> dict[str, Any]:
        with self._lock:
            self.run_scheduler_once_locked(now)
            return self._schedule_snapshot_locked()

    def _scheduler_loop(self) -> None:
        while not self._scheduler_stop.wait(self._scheduler_interval_seconds):
            try:
                self.run_scheduler_once()
            except Exception:
                # Keep the scheduler alive; failures remain visible through explicit API calls/logs.
                continue

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
        self._scheduler_stop.set()
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=min(1.0, timeout))
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
