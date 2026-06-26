from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from project_registry_core import database
from project_registry_core import registry as core
from project_registry_core.application import decisions, merge, presentation as services, projects
from .jobs import JobConflictError, JobManager


STATIC_ROOT = Path(__file__).resolve().parent / "static"
DEFAULT_REVIEW_STATES = {"active", "watchlist", "stable", "stalled"}
DEFAULT_PROMOTION_STATES = {"active", "watchlist"}


def restart_argv(argv: list[str] | None = None) -> list[str]:
    current = list(sys.argv if argv is None else argv)
    return [sys.executable, "-m", "project_registry_web", *current[1:]]


def build_information(root: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    info: dict[str, Any] = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "commit": "",
        "dirty": False,
    }
    if not (root / ".git").exists():
        return info
    try:
        info["commit"] = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        info["dirty"] = bool(subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        info["commit"] = ""
        info["dirty"] = False
    return info


def _int(value: Any, default: int, *, minimum: int = 1, maximum: int = 500) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _boolean(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError("boolean value required")
    return value


def _set(values: list[str]) -> set[str]:
    output: set[str] = set()
    for value in values:
        output.update(part for part in value.replace(",", " ").split() if part)
    return output


def _choice(value: str, allowed: set[str], *, default: str = "any") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in allowed:
        raise ValueError(f"invalid value: {normalized}")
    return normalized


def _date(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    import datetime as dt

    return dt.date.fromisoformat(normalized).isoformat()


class RegistryWebServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], *, token: str, db_path: Path, read_only: bool = False):
        super().__init__(address, RegistryWebHandler)
        self.token = token
        self.db_path = Path(db_path).expanduser().resolve()
        self.read_only = bool(read_only)
        self.instance_id = uuid.uuid4().hex
        self.build_info = build_information()
        self.reload_lock = threading.Lock()
        self.reload_requested = False
        self.mutation_lock = threading.Lock()
        self.jobs = JobManager(self.db_path)

    def open_db(self) -> sqlite3.Connection:
        return database.open_database(self.db_path)

    def server_close(self) -> None:
        jobs = getattr(self, "jobs", None)
        if jobs is not None:
            jobs.close()
        super().server_close()

    def request_process_reload(self, reason: str) -> str:
        with self.reload_lock:
            if self.reload_requested:
                raise ValueError("server reload is already in progress")
            self.reload_requested = True
        reload_id = uuid.uuid4().hex

        def reload_soon() -> None:
            time.sleep(0.25)
            print(f"Reloading project registry web server ({reload_id}): {reason}", flush=True)
            self.jobs.close()
            os.execv(sys.executable, restart_argv())

        threading.Thread(target=reload_soon, name="registry-web-manual-reload", daemon=True).start()
        return reload_id


class RegistryWebHandler(BaseHTTPRequestHandler):
    server: RegistryWebServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self.authorized():
                self.send_error_json("unauthorized", "API token is required", HTTPStatus.UNAUTHORIZED)
                return
            self.dispatch(lambda: self.handle_api_get(parsed))
            return
        self.dispatch(lambda: self.serve_static(parsed.path))

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error_json("not_found", "Resource not found", HTTPStatus.NOT_FOUND)
            return
        if not self.authorized():
            self.send_error_json("unauthorized", "API token is required", HTTPStatus.UNAUTHORIZED)
            return
        if bool(getattr(self.server, "read_only", False)):
            self.send_error_json("read_only", "Web server is running in read-only mode", HTTPStatus.FORBIDDEN)
            return
        lock = getattr(self.server, "mutation_lock", None)
        if lock is None:
            self.dispatch(lambda: self.handle_api_post(parsed, self.read_json()))
        else:
            with lock:
                self.dispatch(lambda: self.handle_api_post(parsed, self.read_json()))

    def dispatch(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except KeyError as exc:
            self.send_error_json("not_found", str(exc).strip("'"), HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            message = str(exc)
            if message.startswith("project not found:"):
                self.send_error_json("not_found", message, HTTPStatus.NOT_FOUND)
            else:
                self.send_error_json("validation_error", message, HTTPStatus.BAD_REQUEST)
        except JobConflictError as exc:
            self.send_error_json("job_conflict", str(exc), HTTPStatus.CONFLICT)
        except services.StaleProjectError as exc:
            self.send_error_json("stale_project", str(exc), HTTPStatus.CONFLICT)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                self.send_error_json("database_busy", str(exc), HTTPStatus.SERVICE_UNAVAILABLE)
            else:
                self.send_error_json("database_error", str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)
        except Exception as exc:  # pragma: no cover - defensive HTTP boundary
            self.send_error_json("internal_error", str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def authorized(self) -> bool:
        auth = str(self.headers.get("Authorization") or "")
        header = str(self.headers.get("X-Project-Registry-Token") or "")
        supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else header.strip()
        return bool(supplied) and supplied == self.server.token

    def serve_static(self, path: str) -> None:
        requested = "index.html" if path in {"", "/", "/index.html"} else path.lstrip("/")
        if requested not in {"index.html", "styles.css", "app.js"}:
            self.send_error_json("not_found", "Resource not found", HTTPStatus.NOT_FOUND)
            return
        file_path = STATIC_ROOT / requested
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_bytes(file_path.read_bytes(), content_type=f"{content_type}; charset=utf-8")

    def handle_api_get(self, parsed: urllib.parse.ParseResult) -> None:
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/api/settings":
            self.send_json({
                "schema_version": database.SCHEMA_VERSION,
                "server_instance_id": str(getattr(self.server, "instance_id", "")),
                "db_path": str(self.server.db_path),
                "token_required": True,
                "read_only": bool(getattr(self.server, "read_only", False)),
                "tracking_states": sorted(core.TRACKING_STATES),
                "operations_connected": True,
                "build": dict(getattr(self.server, "build_info", {}) or {}),
                "operation_capabilities": {
                    "enrich": True,
                    "sync": True,
                    "review_ops": True,
                    "subprocess_worker": True,
                },
            })
            return
        if path == "/api/jobs":
            self.send_json(self.server.jobs.list_jobs(limit=_int(query.get("limit", [50])[0], 50)))
            return
        if path == "/api/checkpoints":
            self.send_json(self.server.jobs.list_checkpoints(limit=_int(query.get("limit", [100])[0], 100)))
            return
        if path.startswith("/api/jobs/"):
            job_id = urllib.parse.unquote(path.removeprefix("/api/jobs/")).strip("/")
            if not job_id or "/" in job_id:
                raise KeyError("job not found")
            self.send_json(self.server.jobs.get_job(job_id))
            return
        if path == "/api/projects":
            with contextlib.closing(self.server.open_db()) as conn:
                self.send_json(services.search_projects(
                    conn,
                    query=str(query.get("q", [""])[0]),
                    tracking_states=_set(query.get("tracking_states", [])) or None,
                    service_statuses=_set(query.get("service_statuses", [])) or None,
                    contribution_levels=_set(query.get("contribution_levels", [])) or None,
                    technical_interest_levels=_set(query.get("technical_interest_levels", [])) or None,
                    introduced=_choice(query.get("introduced", ["any"])[0], {"yes", "no", "any"}),
                    has_candidates=_choice(query.get("has_candidates", ["any"])[0], {"yes", "no", "any"}),
                    has_errors=_choice(query.get("has_errors", ["any"])[0], {"yes", "no", "any"}),
                    promotion_hold=_choice(query.get("promotion_hold", ["any"])[0], {"yes", "no", "any"}),
                    seen_since=_date(query.get("seen_since", [""])[0]),
                    enriched_before=_date(query.get("enriched_before", [""])[0]),
                    sort=str(query.get("sort", [""])[0]),
                    limit=_int(query.get("limit", [50])[0], 50),
                    identifier_kind=str(query.get("identifier_kind", [""])[0]),
                    identifier_value=str(query.get("identifier_value", [""])[0]),
                    identifier_classification=str(query.get("identifier_classification", [""])[0]),
                    relation_predicate=str(query.get("relation_predicate", [""])[0]),
                    relation_subject_type=str(query.get("relation_subject_type", [""])[0]),
                    relation_subject_value=str(query.get("relation_subject_value", [""])[0]),
                    relation_object_type=str(query.get("relation_object_type", [""])[0]),
                    relation_object_value=str(query.get("relation_object_value", [""])[0]),
                    relation_source=str(query.get("relation_source", [""])[0]),
                    decision_kind=str(query.get("decision_kind", [""])[0]),
                    decision_target_type=str(query.get("decision_target_type", [""])[0]),
                    decision_target_value=str(query.get("decision_target_value", [""])[0]),
                    decision_status=_choice(
                        query.get("decision_status", [""])[0],
                        {"", "active", "expired", "cleared"},
                        default="",
                    ),
                ))
            return
        if path == "/api/review-queue":
            states = _set(query.get("tracking_states", [])) or DEFAULT_REVIEW_STATES
            with contextlib.closing(self.server.open_db()) as conn:
                self.send_json(services.queue_payload(
                    conn,
                    queue="review",
                    tracking_states=states,
                    kinds=_set(query.get("kinds", [])) or None,
                    limit=_int(query.get("limit", [50])[0], 50),
                ))
            return
        if path == "/api/promotion-queue":
            states = _set(query.get("tracking_states", [])) or DEFAULT_PROMOTION_STATES
            with contextlib.closing(self.server.open_db()) as conn:
                self.send_json(services.queue_payload(
                    conn,
                    queue="promotion",
                    tracking_states=states,
                    kinds=_set(query.get("kinds", [])) or None,
                    limit=_int(query.get("limit", [50])[0], 50),
                    include_personal_tools=str(query.get("include_personal_tools", [""])[0]).lower() in {"1", "true", "yes"},
                ))
            return
        if path == "/api/merge-batch":
            with contextlib.closing(self.server.open_db()) as conn:
                self.send_json(services.merge_batch_plan(
                    conn,
                    mode=str(query.get("mode", ["moderate"])[0]),
                    limit=_int(query.get("limit", [50])[0], 50),
                ))
            return
        if path == "/api/merge-pair":
            source = str(query.get("source_project_key", [""])[0]).strip()
            target = str(query.get("target_project_key", [""])[0]).strip()
            if not source or not target:
                raise ValueError("source_project_key and target_project_key are required")
            include_bootstrap_details = str(query.get("include_bootstrap_details", [""])[0]).lower() in {"1", "true", "yes"}
            with contextlib.closing(self.server.open_db()) as conn:
                self.send_json(services.merge_pair(
                    conn,
                    source_project_key=source,
                    target_project_key=target,
                    include_bootstrap_details=include_bootstrap_details,
                ))
            return
        if path == "/api/seeds/preview":
            seed = str(query.get("seed", [""])[0]).strip()
            if not seed:
                raise ValueError("seed is required")
            with contextlib.closing(self.server.open_db()) as conn:
                self.send_json(services.seed_preview(
                    conn,
                    seed=seed,
                    seed_type=str(query.get("type", [""])[0]).strip() or None,
                ))
            return
        if path.startswith("/api/investigation-runs/"):
            parts = [urllib.parse.unquote(part) for part in path.removeprefix("/api/investigation-runs/").split("/") if part]
            if len(parts) not in {1, 2}:
                raise KeyError("investigation run not found")
            with contextlib.closing(self.server.open_db()) as conn:
                if len(parts) == 2 and parts[1] == "replay":
                    self.send_json(services.investigation_replay(conn, run_id=parts[0]))
                elif len(parts) == 1:
                    self.send_json(services.investigation_run_detail(
                        conn,
                        run_id=parts[0],
                        evidence_limit=_int(query.get("evidence_limit", [100])[0], 100),
                    ))
                else:
                    raise KeyError("investigation run not found")
            return
        if path.startswith("/api/projects/") and path.endswith("/investigation-runs"):
            key = urllib.parse.unquote(path.removeprefix("/api/projects/").removesuffix("/investigation-runs")).strip("/")
            if not key or "/" in key:
                raise KeyError("project not found")
            with contextlib.closing(self.server.open_db()) as conn:
                self.send_json(services.investigation_run_list(
                    conn,
                    project_key=key,
                    limit=_int(query.get("limit", [50])[0], 50),
                ))
            return
        if path.startswith("/api/projects/") and path.endswith("/merge-analysis"):
            key = urllib.parse.unquote(path.removeprefix("/api/projects/").removesuffix("/merge-analysis")).strip("/")
            if not key or "/" in key:
                raise KeyError("project not found")
            with contextlib.closing(self.server.open_db()) as conn:
                self.send_json(services.project_merge_analysis(conn, project_key=key))
            return
        if path.startswith("/api/projects/"):
            key = urllib.parse.unquote(path.removeprefix("/api/projects/")).strip("/")
            if not key or "/" in key:
                raise KeyError("project not found")
            with contextlib.closing(self.server.open_db()) as conn:
                self.send_json(services.project_detail(conn, project_key=key))
            return
        self.send_error_json("not_found", "Resource not found", HTTPStatus.NOT_FOUND)

    def handle_api_post(self, parsed: urllib.parse.ParseResult, payload: dict[str, Any]) -> None:
        path = parsed.path
        if path == "/api/jobs/enrich":
            result = self.server.jobs.start_enrich(self.enrich_job_parameters(payload))
            self.send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path == "/api/jobs/sync":
            parameters = self.sync_job_parameters(payload)
            result = self.server.jobs.start_sync(parameters, execute=_boolean(payload.get("execute")))
            self.send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path == "/api/jobs/review":
            parameters = self.review_job_parameters(payload)
            result = self.server.jobs.start_review(parameters, execute=_boolean(payload.get("execute")))
            self.send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/jobs/"):
            parts = [urllib.parse.unquote(part) for part in path.removeprefix("/api/jobs/").split("/") if part]
            if len(parts) != 2:
                self.send_error_json("not_found", "Resource not found", HTTPStatus.NOT_FOUND)
                return
            job_id, action = parts
            if action == "cancel":
                self.send_json(self.server.jobs.cancel(job_id), status=HTTPStatus.ACCEPTED)
                return
            if action == "resume":
                self.send_json(self.server.jobs.resume(job_id), status=HTTPStatus.ACCEPTED)
                return
            raise ValueError(f"unknown job action: {action}")
        if path.startswith("/api/checkpoints/"):
            parts = [urllib.parse.unquote(part) for part in path.removeprefix("/api/checkpoints/").split("/") if part]
            if len(parts) == 2 and parts[1] == "resume":
                self.send_json(self.server.jobs.resume_checkpoint(parts[0]), status=HTTPStatus.ACCEPTED)
                return
            raise ValueError("unknown checkpoint action")
        if path == "/api/seeds":
            seed = str(payload.get("seed") or "").strip()
            if not seed:
                raise ValueError("seed is required")
            with contextlib.closing(self.server.open_db()) as conn:
                requested_project = str(payload.get("project_key") or "").strip()
                if requested_project:
                    services.require_project_revision(conn, requested_project, str(payload.get("expected_revision") or ""))
                result = projects.add_seed(
                    conn,
                    seed=seed,
                    seed_type=str(payload.get("type") or "").strip() or None,
                    project_key=str(payload.get("project_key") or "").strip() or None,
                )
            self.send_json(result, status=HTTPStatus.CREATED)
            return
        if path == "/api/reload":
            if payload.get("confirm") is not True:
                raise ValueError("confirm=true is required")
            reload_id = self.server.request_process_reload("manual API request")
            self.send_json({"reload_id": reload_id, "status": "reloading"}, status=HTTPStatus.ACCEPTED)
            return
        if path == "/api/merge-batch/action":
            action = str(payload.get("action") or "").strip()
            source = str(payload.get("source_project_key") or "").strip()
            target = str(payload.get("target_project_key") or "").strip()
            with contextlib.closing(self.server.open_db()) as conn:
                services.require_project_revision(conn, source, str(payload.get("expected_source_revision") or ""))
                services.require_project_revision(conn, target, str(payload.get("expected_target_revision") or ""))
                if action == "merge":
                    result = merge.merge_projects(
                        conn,
                        project_key=source,
                        target_project_key=target,
                        execute=True,
                    )
                elif action == "separate":
                    result = decisions.set_separate(conn, project_key=source, other_project_key=target)
                else:
                    raise ValueError("action must be merge or separate")
            self.send_json(result)
            return
        if not path.startswith("/api/projects/"):
            self.send_error_json("not_found", "Resource not found", HTTPStatus.NOT_FOUND)
            return
        parts = [urllib.parse.unquote(part) for part in path.removeprefix("/api/projects/").split("/") if part]
        if len(parts) != 2:
            self.send_error_json("not_found", "Resource not found", HTTPStatus.NOT_FOUND)
            return
        project_key, action = parts
        with contextlib.closing(self.server.open_db()) as conn:
            services.require_project_revision(conn, project_key, str(payload.get("expected_revision") or ""))
            if action == "merge-recommendation":
                target_key = str(payload.get("other_project_key") or "").strip()
                if not target_key:
                    raise ValueError("merge recommendation target is required")
                services.require_project_revision(
                    conn, target_key, str(payload.get("expected_target_revision") or "")
                )
            result = self.run_project_action(conn, project_key, action, payload)
            detail_key = str(result.get("target_project_key") or result.get("project_key") or project_key)
            detail = services.project_detail(conn, project_key=detail_key)
        self.send_json({"result": result, "detail": detail})

    def enrich_job_parameters(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_key = str(payload.get("project_key") or "").strip()
        all_projects = bool(payload.get("all_projects"))
        if bool(project_key) == all_projects:
            raise ValueError("choose exactly one of project_key or all_projects")
        tracking_values = payload.get("tracking_states") or []
        if not isinstance(tracking_values, list):
            raise ValueError("tracking_states must be an array")
        tracking_states = tuple(str(item).strip() for item in tracking_values if str(item).strip())
        invalid_states = set(tracking_states) - core.TRACKING_STATES
        if invalid_states:
            raise ValueError(f"invalid tracking_states: {', '.join(sorted(invalid_states))}")
        return {
            "project_key": project_key,
            "all_projects": all_projects,
            "all_tracking_states": bool(payload.get("all_tracking_states")),
            "tracking_states": tracking_states,
            "include_stable": bool(payload.get("include_stable")),
            "include_stalled": bool(payload.get("include_stalled")),
            "include_out_of_scope": bool(payload.get("include_out_of_scope")),
            "include_archived": bool(payload.get("include_archived")),
            "include_ignore": bool(payload.get("include_ignore")),
            "new_only": bool(payload.get("new_only")),
            "retry_failures": bool(payload.get("retry_failures")),
            "deep_investigation": bool(payload.get("deep_investigation")),
        }

    def sync_job_parameters(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = _choice(
            str(payload.get("source") or ""),
            {"introduced", "github", "collections", "semble", "tangled"},
            default="",
        )
        all_history = _boolean(payload.get("all_history"))
        return {
            "source": source,
            "create_unmatched": _boolean(payload.get("create_unmatched")),
            "incremental": not all_history,
            "all_history": all_history,
            "screen_unmatched": _boolean(payload.get("screen_unmatched")),
            "lookback_days": _int(payload.get("lookback_days"), 10, maximum=3650),
        }

    def review_job_parameters(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = _choice(
            str(payload.get("kind") or ""),
            {"stalled", "ignore", "out_of_scope"},
            default="",
        )
        tracking_values = payload.get("tracking_states") or []
        if not isinstance(tracking_values, list):
            raise ValueError("tracking_states must be an array")
        tracking_states = tuple(str(item).strip() for item in tracking_values if str(item).strip())
        invalid_states = set(tracking_states) - core.TRACKING_STATES
        if invalid_states:
            raise ValueError(f"invalid tracking_states: {', '.join(sorted(invalid_states))}")
        note_reason = str(payload.get("note_reason") or "").strip()
        allowed_reasons = (
            set(core.IGNORE_REVIEW_NOTE_REASONS)
            if kind == "ignore"
            else set(core.OUT_OF_SCOPE_REVIEW_NOTE_REASONS)
            if kind == "out_of_scope"
            else set()
        )
        if note_reason and note_reason not in allowed_reasons:
            raise ValueError(f"invalid note_reason: {note_reason}")
        selected_values = payload.get("selected_candidate_ids") or []
        if not isinstance(selected_values, list):
            raise ValueError("selected_candidate_ids must be an array")
        return {
            "kind": kind,
            "tracking_states": tracking_states,
            "limit": _int(payload.get("limit"), 100),
            "inactivity_days": _int(payload.get("inactivity_days"), 45, maximum=3650),
            "max_enrich_age_days": _int(payload.get("max_enrich_age_days"), 30, maximum=3650),
            "note": str(payload.get("note") or "").strip(),
            "note_reason": note_reason,
            "selected_candidate_ids": tuple(str(item) for item in selected_values if str(item)),
            "plan_fingerprint": str(payload.get("plan_fingerprint") or ""),
        }

    def run_project_action(
        self,
        conn: sqlite3.Connection,
        project_key: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        identifier_type = str(payload.get("identifier_type") or "").strip()
        value = str(payload.get("value") or "").strip()
        note = str(payload.get("note") or "").strip()
        if action == "tracking-state":
            return projects.set_tracking_state(
                conn,
                project_key=project_key,
                tracking_state=str(payload.get("tracking_state") or ""),
                note=note,
            )
        if action == "profile-preview":
            return projects.preview_profile_edit(
                conn,
                project_key=project_key,
                name=str(payload.get("name") or ""),
                notes=str(payload.get("notes") or ""),
            )
        if action == "profile":
            return projects.apply_profile_edit(
                conn,
                project_key=project_key,
                name=str(payload.get("name") or ""),
                notes=str(payload.get("notes") or ""),
            )
        if action == "confirm-candidate":
            return services.confirm_candidate(
                conn, project_key=project_key, identifier_type=identifier_type, value=value
            )
        if action == "reject-candidate":
            return services.reject_candidate(
                conn, project_key=project_key, identifier_type=identifier_type, value=value, note=note
            )
        if action in {"block-identifier", "clear-block-identifier"}:
            return decisions.set_identifier_block(
                conn,
                project_key=project_key,
                identifier_type=identifier_type,
                value=value,
                note=note,
                clear=action.startswith("clear-"),
            )
        if action in {"defer-identifier", "clear-defer-identifier"}:
            return decisions.set_identifier_defer(
                conn,
                project_key=project_key,
                identifier_type=identifier_type,
                value=value,
                note=note,
                days=_int(payload.get("days"), 30, maximum=365),
                clear=action.startswith("clear-"),
            )
        if action in {"defer-promotion", "clear-promotion-deferral"}:
            return decisions.set_promotion_defer(
                conn,
                project_key=project_key,
                reason=str(payload.get("reason") or note),
                days=_int(payload.get("days"), 7, maximum=365),
                clear=action.startswith("clear-"),
            )
        if action == "dismiss-review-kind":
            return decisions.dismiss_review_kind(
                conn,
                project_key=project_key,
                kind=str(payload.get("kind") or "").strip(),
            )
        if action in {"dismiss-review-target", "defer-review-target", "clear-review-target"}:
            return decisions.set_review_target(
                conn,
                project_key=project_key,
                target_type=str(payload.get("target_type") or "").strip(),
                target_value=str(payload.get("target_value") or "").strip(),
                action=str(payload.get("review_action") or ("defer" if action == "defer-review-target" else "dismiss")),
                note=note,
                days=_int(payload.get("days"), 30, maximum=365),
                clear=action == "clear-review-target",
            )
        if action == "split-project":
            return merge.split_identifier(
                conn,
                project_key=project_key,
                identifier_type=identifier_type,
                value=value,
                note=note,
                execute=True,
            )
        if action == "clear-separate":
            return decisions.set_separate(
                conn,
                project_key=project_key,
                other_project_key=str(payload.get("other_project_key") or "").strip(),
                clear=True,
            )
        if action == "merge-recommendation":
            return merge.merge_projects(
                conn,
                project_key=project_key,
                use_recommendation=True,
                expected_recommendation_target=str(payload.get("other_project_key") or "").strip(),
                execute=True,
            )
        raise ValueError(f"unknown project action: {action}")

    def read_json(self) -> dict[str, Any]:
        length = _int(self.headers.get("Content-Length"), 0, minimum=0, maximum=1_000_000)
        if not length:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def send_error_json(self, code: str, message: str, status: HTTPStatus) -> None:
        self.send_json({"error": {"code": code, "message": message}}, status=status)

    def send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps({"api_version": services.WEB_API_VERSION, **payload}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_bytes(body, content_type="application/json; charset=utf-8", status=status)

    def send_bytes(
        self,
        body: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-registry-web",
        description="Serve the schema v2 project registry Web UI.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Use 0.0.0.0 for LAN access.")
    parser.add_argument("--port", type=int, default=8765, help="Bind port. Default: 8765.")
    parser.add_argument("--db-path", help="SQLite DB path. Defaults to data/survey.db.")
    parser.add_argument("--token", help="Shared API token. ATPROTO_SURVEY_WEB_TOKEN is also supported.")
    parser.add_argument("--reload", action="store_true", help="Restart when Web implementation files change.")
    parser.add_argument("--reload-interval", type=float, default=1.0, help="Reload polling interval in seconds.")
    parser.add_argument("--read-only", action="store_true", help="Disable all Web API POST operations.")
    return parser


def start_reload_watcher(server: RegistryWebServer, paths: list[Path], interval: float) -> None:
    watched = {path: path.stat().st_mtime_ns for path in paths if path.exists()}

    def watch() -> None:
        while True:
            time.sleep(max(0.2, interval))
            for path, previous in list(watched.items()):
                try:
                    current = path.stat().st_mtime_ns
                except OSError:
                    continue
                if current != previous:
                    server.jobs.close()
                    os.execv(sys.executable, restart_argv())

    threading.Thread(target=watch, name="registry-web-reload", daemon=True).start()


def main() -> int:
    args = build_parser().parse_args()
    token = str(args.token or os.environ.get("ATPROTO_SURVEY_WEB_TOKEN") or "").strip()
    if not token:
        print("error: --token or ATPROTO_SURVEY_WEB_TOKEN is required", file=sys.stderr)
        return 2
    db_path = Path(args.db_path).expanduser() if args.db_path else core.DB_PATH
    server = RegistryWebServer(
        (str(args.host), int(args.port)), token=token, db_path=db_path, read_only=bool(args.read_only)
    )
    if args.reload:
        scripts_root = Path(__file__).resolve().parents[1]
        watch_roots = [scripts_root / "project_registry_web", scripts_root / "project_registry_core"]
        start_reload_watcher(
            server,
            [
                path
                for root in watch_roots
                for path in root.rglob("*")
                if path.suffix in {".py", ".html", ".css", ".js"}
            ],
            float(args.reload_interval or 1.0),
        )
    url = f"http://{args.host}:{args.port}/?token={urllib.parse.quote(token)}"
    print(f"Serving project registry Web UI at {url}", flush=True)
    print(f"Using DB: {server.db_path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
