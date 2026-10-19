from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import sqlite3
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from ..persistence import database as registry_database
from .. import enrichment_pipeline, registry as core
from ..persistence import storage, concurrency
from ..jobs import (
    CancellationToken,
    EventKind,
    EventSink,
    OperationEvent,
    OperationRequest,
    OperationResult,
)
from project_registry_core import project_record
from .enrich_prefetch import InitialPrefetch


DEFAULT_TRIAGE_RULES_BY_MODEL = {
    "qwen/qwen3.6-35b-a3b": core.ROOT / "prompts/model-rules/qwen-3.6-35b-a3b.txt",
    "qwen3.6-35b-a3b": core.ROOT / "prompts/model-rules/qwen-3.6-35b-a3b.txt",
}

_CHECKPOINT_SOURCE_KEYS = "_enrich_checkpoint_source_keys"


def configured_triage_prompt_suffix_file(options: EnrichmentOptions) -> str:
    if options.llm_triage_prompt_suffix_file:
        return options.llm_triage_prompt_suffix_file
    configured = os.getenv("ATPROTO_SURVEY_LLM_TRIAGE_PROMPT_SUFFIX_FILE", "").strip()
    if configured:
        return configured
    model = str(options.llm_model or os.getenv("ATPROTO_SURVEY_LLM_MODEL", "")).strip().lower()
    path = DEFAULT_TRIAGE_RULES_BY_MODEL.get(model)
    return str(path) if path is not None else ""


@dataclass(frozen=True)
class EnrichmentOptions:
    project_key: str = ""
    seed: str = ""
    seed_type: str = ""
    all_projects: bool = False
    all_tracking_states: bool = False
    tracking_states: tuple[str, ...] = ()
    include_stable: bool = False
    include_stalled: bool = False
    include_out_of_scope: bool = False
    include_archived: bool = False
    include_ignore: bool = False
    new_only: bool = False
    retry_failures: bool = False
    continue_on_error: bool = True
    checkpoint_path: str = ""
    no_checkpoint: bool = False
    resume_run: str = ""
    deep_investigation: bool = False
    prefetch: bool = True
    llm_endpoint: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_api_style: str = ""
    llm_reasoning_effort: str = ""
    llm_max_output_tokens: int = 0
    llm_triage_prompt_suffix_file: str = ""

    @classmethod
    def from_request(cls, request: OperationRequest) -> EnrichmentOptions:
        if request.operation != "enrich":
            raise ValueError(f"unsupported operation: {request.operation}")
        allowed = {item.name for item in fields(cls)}
        values = {key: value for key, value in request.parameters.items() if key in allowed}
        values["tracking_states"] = tuple(str(item) for item in values.get("tracking_states") or ())
        values["llm_max_output_tokens"] = int(values.get("llm_max_output_tokens") or 0)
        return cls(**values)

    def checkpoint_options(self) -> dict[str, Any]:
        names = (
            "all_tracking_states",
            "tracking_states",
            "include_stable",
            "include_stalled",
            "include_out_of_scope",
            "include_archived",
            "include_ignore",
            "new_only",
            "retry_failures",
            "continue_on_error",
            "deep_investigation",
            "prefetch",
            "llm_endpoint",
            "llm_model",
            "llm_api_style",
            "llm_reasoning_effort",
            "llm_max_output_tokens",
            "llm_triage_prompt_suffix_file",
        )
        values = {name: getattr(self, name) for name in names}
        values["tracking_states"] = list(self.tracking_states)
        values["llm_api_key_configured"] = bool(self.llm_api_key)
        return values


def _validate_run_id(run_id: str) -> str:
    value = str(run_id or "").strip()
    if not value:
        raise ValueError("enrich run id is required")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ValueError(f"invalid enrich run id: {run_id}")
    return value


def checkpoint_path(run_id: str, override: str = "") -> Path:
    return Path(override).expanduser() if override else core.ENRICH_RUNS_DIR / f"{_validate_run_id(run_id)}.json"


def resume_command(run_id: str, override: str = "") -> str:
    parts = ["project-registry", "enrich", "--all", "--resume-run", _validate_run_id(run_id)]
    if override:
        parts.extend(["--checkpoint-path", override])
    return " ".join(shlex.quote(part) for part in parts)


def new_checkpoint(projects: list[dict[str, Any]], request: OperationRequest, options: EnrichmentOptions) -> dict[str, Any]:
    now = project_record.iso_now()
    return {
        "version": 1,
        "run_id": _validate_run_id(request.operation_id),
        "status": "running",
        "started_at": now,
        "updated_at": now,
        "completed_at": "",
        "interrupted_at": "",
        "options": options.checkpoint_options(),
        "selected_project_keys": [str(project.get("project_key") or "") for project in projects if project.get("project_key")],
        "completed_project_keys": [],
        "failed_projects": [],
        "conflicted_projects": [],
        "skipped_projects": [],
        "missing_project_keys": [],
        "current_project_key": "",
    }


def load_checkpoint(run_id: str, override: str = "") -> dict[str, Any]:
    path = checkpoint_path(run_id, override)
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"invalid enrich checkpoint: {path}")
    checkpoint_run_id = _validate_run_id(str(payload.get("run_id") or run_id))
    requested_run_id = _validate_run_id(run_id)
    if checkpoint_run_id != requested_run_id:
        raise ValueError(f"checkpoint run_id mismatch: expected {requested_run_id}, got {checkpoint_run_id}")
    payload["run_id"] = checkpoint_run_id
    return payload


def write_checkpoint(checkpoint: dict[str, Any], override: str = "") -> Path:
    path = checkpoint_path(str(checkpoint.get("run_id") or ""), override)
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint["updated_at"] = project_record.iso_now()
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    return path


def _options_from_checkpoint(options: EnrichmentOptions, checkpoint: dict[str, Any]) -> EnrichmentOptions:
    stored = checkpoint.get("options")
    if not isinstance(stored, dict):
        return options
    allowed = {item.name for item in fields(EnrichmentOptions)} - {"llm_api_key", "checkpoint_path", "resume_run", "no_checkpoint"}
    updates = {key: value for key, value in stored.items() if key in allowed and key != "llm_api_key_configured"}
    if "tracking_states" in updates:
        updates["tracking_states"] = tuple(str(item) for item in updates["tracking_states"] or ())
    return replace(options, **updates)


def _tracking_states(options: EnrichmentOptions) -> set[str] | None:
    if options.all_tracking_states:
        return None
    if options.tracking_states:
        cleaned = {item.strip().lower() for item in options.tracking_states if item.strip()}
        return {item for item in cleaned if item in project_record.TRACKING_STATES} or None
    states = {"active"}
    for enabled, value in (
        (options.include_stable, "stable"),
        (options.include_stalled, "stalled"),
        (options.include_out_of_scope, "out_of_scope"),
        (options.include_archived, "archived"),
        (options.include_ignore, "ignore"),
    ):
        if enabled:
            states.add(value)
    return states


def _sort_key(project: dict[str, Any]) -> tuple[int, dt.datetime, float, str]:
    last_enriched = project_record.parse_iso_datetime(str(project.get("last_enriched_at") or ""))
    last_seen = project_record.parse_iso_datetime(str(project.get("last_seen_at") or ""))
    return (
        0 if last_enriched is None else 1,
        last_enriched or dt.datetime.fromtimestamp(0, tz=dt.timezone.utc),
        -(last_seen.timestamp()) if last_seen is not None else float("inf"),
        str(project.get("project_key") or ""),
    )


def _select_projects(conn: sqlite3.Connection, options: EnrichmentOptions) -> list[dict[str, Any]]:
    if options.project_key:
        project = project_record.fetch_project(conn, project_record.resolve_project_key_input(conn, options.project_key))
        if project is None:
            raise ValueError(f"project not found: {options.project_key}")
        return [project]
    if options.seed:
        seed_type = core.detect_seed_type(options.seed, options.seed_type or None)
        value = project_record.canonical_seed_value(seed_type, options.seed)
        project_key = project_record.find_project_key_by_identifier(conn, seed_type, value)
        project = project_record.fetch_project(conn, project_key) if project_key else core.ensure_project_for_seed(conn, seed_type, options.seed)
        return [project] if project else []
    if options.all_projects:
        projects = project_record.list_projects(conn, tracking_states=_tracking_states(options))
        if options.new_only:
            projects = [project for project in projects if not str(project.get("last_enriched_at") or "").strip()]
        if options.retry_failures:
            projects = [project for project in projects if project.get("last_run_errors")]
        projects.sort(key=_sort_key)
        return projects
    raise ValueError("choose one of project_key, seed, or all_projects")


def _load_checkpoint_projects(conn: sqlite3.Connection, checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    projects_by_key: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    completed = {str(item) for item in checkpoint.get("completed_project_keys", []) if str(item)}
    completed.update(str(item["project_key"]) for item in checkpoint.get("skipped_projects", []))
    for value in checkpoint.get("selected_project_keys", []):
        project_key = str(value or "").strip()
        if not project_key or project_key in completed:
            continue
        project = project_record.fetch_project(conn, project_key)
        if project is None:
            alias_target = registry_database.project_key_alias_target(conn, project_key)
            project = project_record.fetch_project(conn, alias_target) if alias_target else None
        if project is None:
            missing.append(project_key)
            continue
        canonical_key = str(project.get("project_key") or "")
        selected = projects_by_key.setdefault(canonical_key, project)
        source_keys = selected.setdefault(_CHECKPOINT_SOURCE_KEYS, [])
        if project_key not in source_keys:
            source_keys.append(project_key)
    checkpoint["missing_project_keys"] = missing
    return list(projects_by_key.values()) + [{"project_key": key, _CHECKPOINT_SOURCE_KEYS: [key]} for key in missing]


def _mark_started(checkpoint: dict[str, Any], project_key: str) -> None:
    checkpoint["status"] = "running"
    checkpoint["current_project_key"] = project_key


def _mark_completed(checkpoint: dict[str, Any], project_key: str) -> None:
    completed = [str(item) for item in checkpoint.get("completed_project_keys", []) if str(item)]
    if project_key not in completed:
        completed.append(project_key)
    checkpoint["completed_project_keys"] = completed
    checkpoint["current_project_key"] = ""
    checkpoint["failed_projects"] = [
        item for item in checkpoint.get("failed_projects", [])
        if isinstance(item, dict) and item.get("project_key") != project_key
    ]
    checkpoint["conflicted_projects"] = [item for item in checkpoint.get("conflicted_projects", []) if item.get("project_key") != project_key]


def _resolve_conflicts(conn: sqlite3.Connection, checkpoint: dict[str, Any] | None, source_keys: list[str]) -> None:
    if not checkpoint:
        return
    run_ids = [str(item.get("run_id") or "") for item in checkpoint.get("conflicted_projects", []) + checkpoint.get("prefetched_runs", []) if item.get("project_key") in source_keys]
    if run_ids:
        conn.executemany("UPDATE investigation_runs SET resolved_at=? WHERE run_id=? AND application_status='unapplied'", [(project_record.iso_now(), key) for key in run_ids if key])
        conn.commit()


def _mark_failed(checkpoint: dict[str, Any], project_key: str, error: BaseException) -> None:
    failures = [
        item for item in checkpoint.get("failed_projects", [])
        if isinstance(item, dict) and item.get("project_key") != project_key
    ]
    failures.append({"project_key": project_key, "error": f"{type(error).__name__}: {error}", "at": project_record.iso_now()})
    checkpoint["failed_projects"] = failures
    checkpoint["current_project_key"] = ""


def _project_payload(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_key": project["project_key"],
        "name": project.get("name", ""),
        "tracking_state": project.get("tracking_state", "active"),
        "service_status": project.get("service_status", ""),
        "atproto_contribution_level": project.get("atproto_contribution_level", "unknown"),
        "atproto_contribution_score": int(project.get("atproto_contribution_score", 0) or 0),
        "category": project.get("category", ""),
        "app_category": project.get("app_category", ""),
        "tagline_is_provisional": project.get("tagline_is_provisional", False),
        "website_url": project.get("website_url", ""),
        "introduced": bool(project.get("introduced_url")),
        "introduced_url": project.get("introduced_url", ""),
        "last_run_errors": project.get("last_run_errors", []),
        "merge_recommendation": project.get("merge_recommendation", {}),
        "github_repo_urls": project_record.provider_repo_url_values(project, "github"),
        "resolved_nsids": project.get("resolved_nsids", []),
    }


def run(
    conn: sqlite3.Connection,
    request: OperationRequest,
    *,
    emit: EventSink | None = None,
    cancellation: CancellationToken | None = None,
) -> OperationResult:
    cancellation = cancellation or CancellationToken()
    prefetch = InitialPrefetch(conn, cancellation)
    try:
        result = _run(conn, request, emit=emit, cancellation=cancellation, prefetch=prefetch)
        result.data["execution"] = prefetch.summary()
        return result
    except BaseException:
        conn.rollback()
        raise
    finally:
        prefetch.discard("batch_stopped")


def _skip_reason(project, options: EnrichmentOptions, processed_keys: set[str]) -> str:
    if project is None:
        return "project_missing"
    if str(project["project_key"]) in processed_keys:
        return "canonical_project_already_processed"
    if options.all_projects:
        states = _tracking_states(options)
        if states is not None and project.get("tracking_state") not in states:
            return "tracking_state_changed"
        if options.new_only and project.get("last_enriched_at"):
            return "already_enriched"
        if options.retry_failures and not project.get("last_run_errors"):
            return "no_collector_failures"
    return ""


def _read_current(conn, project_key: str):
    with concurrency.read_snapshot(conn):
        alias_target = registry_database.project_key_alias_target(conn, project_key)
        return project_record.fetch_project(conn, alias_target or project_key)


def _run(
    conn: sqlite3.Connection,
    request: OperationRequest,
    *,
    emit: EventSink | None,
    cancellation: CancellationToken,
    prefetch: InitialPrefetch,
) -> OperationResult:
    options = EnrichmentOptions.from_request(request)
    if options.llm_max_output_tokens < 0:
        raise ValueError("llm_max_output_tokens must be zero or greater")
    if options.resume_run and not options.all_projects:
        raise ValueError("resume_run requires all_projects")
    if options.resume_run and options.no_checkpoint:
        raise ValueError("resume_run cannot be combined with no_checkpoint")
    if not options.resume_run:
        configured_suffix = configured_triage_prompt_suffix_file(options)
        if configured_suffix:
            options = replace(options, llm_triage_prompt_suffix_file=configured_suffix)
    cancellation = cancellation or CancellationToken()
    started_at = project_record.iso_now()
    sequence = 0

    def publish(kind: EventKind, data: dict[str, Any]) -> None:
        nonlocal sequence
        sequence += 1
        if emit is not None:
            emit(OperationEvent(
                operation_id=request.operation_id,
                operation=request.operation,
                kind=kind,
                sequence=sequence,
                data=data,
            ))

    checkpoint: dict[str, Any] | None = None
    written_path: Path | None = None
    resumed = False
    if options.all_projects and options.resume_run:
        checkpoint = load_checkpoint(options.resume_run, options.checkpoint_path)
        options = _options_from_checkpoint(options, checkpoint)
        projects = _load_checkpoint_projects(conn, checkpoint)
        written_path = write_checkpoint(checkpoint, options.checkpoint_path)
        resumed = True
    else:
        projects = _select_projects(conn, options)
        if options.all_projects and not options.no_checkpoint:
            checkpoint = new_checkpoint(projects, request, options)
            written_path = write_checkpoint(checkpoint, options.checkpoint_path)

    llm_triage_prompt_suffix = ""
    if checkpoint is not None:
        def record_prefetched_run(item):
            checkpoint.setdefault("prefetched_runs", []).append(item)
            write_checkpoint(checkpoint, options.checkpoint_path)
        prefetch.on_unapplied = record_prefetched_run
    if options.llm_triage_prompt_suffix_file:
        suffix_path = Path(options.llm_triage_prompt_suffix_file).expanduser()
        llm_triage_prompt_suffix = suffix_path.read_text(encoding="utf-8").strip()
        if not llm_triage_prompt_suffix:
            raise ValueError(f"LLM triage prompt suffix file is empty: {suffix_path}")

    selected_total = len(checkpoint.get("selected_project_keys", [])) if checkpoint else len(projects)
    prefetch.metrics["mode"] = "prefetch_one" if options.all_projects and options.prefetch else "sequential"
    completed_before = len({str(item) for item in checkpoint.get("completed_project_keys", []) if str(item)}) if checkpoint else 0
    run_started = dt.datetime.now(dt.timezone.utc)
    failed_projects: list[dict[str, str]] = []
    conflicted_projects: list[dict[str, str]] = []
    skipped_projects: list[dict[str, str]] = []
    processed_keys: set[str] = set()
    if checkpoint:
        publish("started", {"enrich_run": {
            "run_id": checkpoint["run_id"],
            "status": checkpoint.get("status", "running"),
            "resumed": resumed,
            "checkpoint_path": str(written_path or checkpoint_path(checkpoint["run_id"], options.checkpoint_path)),
            "resume_command": resume_command(checkpoint["run_id"], options.checkpoint_path),
            "selected_project_count": selected_total,
            "completed_project_count": completed_before,
            "remaining_project_count": len(projects),
        }})
    else:
        publish("started", {"enrich_run": {
            "run_id": request.operation_id,
            "status": "running",
            "resumed": False,
            "selected_project_count": selected_total,
            "completed_project_count": 0,
            "remaining_project_count": len(projects),
        }})

    for offset, project in enumerate(projects, start=1):
        if cancellation.cancel_requested:
            break
        project_key = str(project.get("project_key") or "")
        checkpoint_source_keys = [
            str(item)
            for item in project.pop(_CHECKPOINT_SOURCE_KEYS, [project_key])
            if str(item)
        ]
        index = completed_before + offset
        now = dt.datetime.now(dt.timezone.utc)
        elapsed_seconds = max(0.0, (now - run_started).total_seconds())
        average_seconds = elapsed_seconds / max(1, offset - 1) if offset > 1 else 0.0
        remaining_seconds = max(0.0, average_seconds * max(0, len(projects) - offset + 1)) if offset > 1 else 0.0
        publish("progress", {"progress": {
            "current": index,
            "total": selected_total,
            "eta_observed_count": max(0, offset - 1),
            "eta_remaining_count": max(0, len(projects) - offset + 1),
            "at": project_record.iso_now(),
            "phase": "start",
            "project_key": project_key,
            "elapsed": core.format_duration_hms(elapsed_seconds),
            "eta": core.format_duration_hms(remaining_seconds),
        }})
        if checkpoint:
            _mark_started(checkpoint, project_key)
            written_path = write_checkpoint(checkpoint, options.checkpoint_path)
        enriched = None
        try:
            # Selection fixes ordering, not the snapshot used by a later item.
            project = _read_current(conn, project_key)
            reason = _skip_reason(project, options, processed_keys)
            if reason:
                prefetch.discard(reason)
                _resolve_conflicts(conn, checkpoint, checkpoint_source_keys + ([str(project["project_key"])] if project else []))
                for key in checkpoint_source_keys:
                    skipped = {"project_key": key, "reason": reason}
                    skipped_projects.append(skipped)
                    if checkpoint:
                        _mark_completed(checkpoint, key)
                        checkpoint["completed_project_keys"].remove(key)
                        checkpoint.setdefault("skipped_projects", []).append(skipped)
                if checkpoint:
                    written_path = write_checkpoint(checkpoint, options.checkpoint_path)
                publish("item_skipped", {"project_key": project_key, "reason": reason})
                continue
            project_key = str(project["project_key"])
            initial = prefetch.take(project)

            def load_next():
                candidate = _read_current(conn, str(projects[offset]["project_key"]))
                if _skip_reason(candidate, options, processed_keys | {project_key}):
                    return None
                return candidate

            prefetch.load_next = load_next if options.all_projects and options.prefetch and offset < len(projects) else None
            enriched = enrichment_pipeline.enrich_project(
                conn,
                project,
                llm_endpoint=options.llm_endpoint or None,
                llm_model=options.llm_model or None,
                llm_api_key=options.llm_api_key or None,
                llm_reasoning_effort=options.llm_reasoning_effort or None,
                llm_api_style=options.llm_api_style or None,
                llm_max_output_tokens=options.llm_max_output_tokens or None,
                llm_triage_prompt_suffix=llm_triage_prompt_suffix or None,
                deep_investigation=options.deep_investigation,
                initial_collection=initial,
                llm_runner=prefetch.run_llm,
            )
            project_record.save_project(conn, enriched, enriched=True)
            conn.commit()
            processed_keys.add(project_key)
            _resolve_conflicts(conn, checkpoint, checkpoint_source_keys)
        except project_record.StaleProjectWriteError as exc:
            conn.rollback()
            run_id = storage.save_conflicted_investigation(conn, enriched or project, exc) if (enriched or project) else ""
            conflict = {"project_key": project_key, "error": str(exc), "run_id": run_id, "at": project_record.iso_now()}
            conflicted_projects.append(conflict)
            if checkpoint:
                previous = checkpoint.setdefault("conflicted_projects", [])
                # Keep earlier unresolved attempt IDs as well as the latest attempt.
                previous.extend({**conflict, "project_key": key} for key in checkpoint_source_keys)
                checkpoint["current_project_key"] = ""
                written_path = write_checkpoint(checkpoint, options.checkpoint_path)
            publish("item_conflicted", {**conflict, "continuing": options.all_projects})
            continue
        except Exception as exc:
            conn.rollback()
            failure = {
                "project_key": project_key,
                "error": f"{type(exc).__name__}: {exc}",
                "at": project_record.iso_now(),
            }
            failed_projects.append(failure)
            if checkpoint:
                for checkpoint_project_key in checkpoint_source_keys:
                    _mark_failed(checkpoint, checkpoint_project_key, exc)
                written_path = write_checkpoint(checkpoint, options.checkpoint_path)
            error = {"type": type(exc).__name__, "message": str(exc), "project_key": project_key}
            publish("failed", {"error": error, "continuing": bool(options.all_projects and options.continue_on_error)})
            if options.all_projects and options.continue_on_error:
                continue
            prefetch.discard("fail_fast")
            if checkpoint:
                checkpoint["execution"] = prefetch.summary()
                checkpoint["status"] = "failed"
                checkpoint["completed_at"] = project_record.iso_now()
                written_path = write_checkpoint(checkpoint, options.checkpoint_path)
            payload = {
                "summary": "enrich_all" if options.all_projects else "enrich",
                "run_id": checkpoint.get("run_id") if checkpoint else request.operation_id,
                "checkpoint_path": str(written_path or "") if checkpoint else "",
                "selected_project_count": selected_total,
                "completed_project_count": len(checkpoint.get("completed_project_keys", [])) if checkpoint else 0,
                "failed_project_count": 1,
                "failed_projects": failed_projects,
                "conflicted_projects": conflicted_projects,
                "conflicted_project_count": len(conflicted_projects),
                "skipped_projects": skipped_projects,
                "skipped_project_count": len(skipped_projects),
                "execution": prefetch.summary(),
                **(
                    {"resume_command": resume_command(checkpoint["run_id"], options.checkpoint_path)}
                    if checkpoint else {}
                ),
            }
            return OperationResult(
                request.operation_id,
                request.operation,
                "failed",
                started_at,
                project_record.iso_now(),
                payload,
                error,
            )
        if checkpoint:
            for checkpoint_project_key in checkpoint_source_keys:
                _mark_completed(checkpoint, checkpoint_project_key)
            written_path = write_checkpoint(checkpoint, options.checkpoint_path)
        publish("item_completed", _project_payload(enriched))
        if cancellation.cancel_requested:
            break

    prefetch.discard("cancelled" if cancellation.cancel_requested else "batch_finished")
    if checkpoint:
        checkpoint["execution"] = prefetch.summary()
    if cancellation.cancel_requested:
        if checkpoint:
            checkpoint["status"] = "interrupted"
            checkpoint["interrupted_at"] = project_record.iso_now()
            checkpoint["current_project_key"] = ""
            written_path = write_checkpoint(checkpoint, options.checkpoint_path)
            payload = {
                "summary": "enrich_all",
                "interrupted": True,
                "cancel_reason": cancellation.reason,
                "run_id": checkpoint["run_id"],
                "checkpoint_path": str(written_path),
                "resume_command": resume_command(checkpoint["run_id"], options.checkpoint_path),
                "selected_project_count": selected_total,
                "completed_project_count": len(checkpoint.get("completed_project_keys", [])),
            }
        else:
            payload = {"summary": "enrich", "interrupted": True, "cancel_reason": cancellation.reason}
        payload.update({"conflicted_projects": conflicted_projects, "conflicted_project_count": len(conflicted_projects),
                        "skipped_projects": skipped_projects, "skipped_project_count": len(skipped_projects),
                        "execution": prefetch.summary()})
        publish("cancelled", payload)
        return OperationResult(request.operation_id, request.operation, "cancelled", started_at, project_record.iso_now(), payload)

    if checkpoint:
        checkpoint["status"] = "failed" if failed_projects else "retry_wait" if conflicted_projects else "completed"
        checkpoint["completed_at"] = project_record.iso_now()
        checkpoint["current_project_key"] = ""
        written_path = write_checkpoint(checkpoint, options.checkpoint_path)
    summary = {
        "summary": "enrich_all" if options.all_projects else "enrich",
        "run_id": checkpoint.get("run_id") if checkpoint else request.operation_id,
        "checkpoint_path": str(written_path or "") if checkpoint else "",
        "project_count": len(projects),
        "selected_project_count": selected_total,
        "completed_project_count": len(checkpoint.get("completed_project_keys", [])) if checkpoint else len(processed_keys),
        "failed_project_count": len(failed_projects),
        "failed_projects": failed_projects,
        "conflicted_projects": conflicted_projects,
        "conflicted_project_count": len(conflicted_projects),
        "skipped_projects": skipped_projects,
        "execution": prefetch.summary(),
    }
    if failed_projects:
        if checkpoint:
            summary["resume_command"] = resume_command(checkpoint["run_id"], options.checkpoint_path)
        error = {
            "type": "BulkEnrichmentError",
            "message": f"{len(failed_projects)} project(s) failed during bulk enrich",
        }
        publish("failed", {**summary, "error": error})
        return OperationResult(request.operation_id, request.operation, "failed", started_at, project_record.iso_now(), summary, error)
    if conflicted_projects:
        if checkpoint:
            summary["resume_command"] = resume_command(checkpoint["run_id"], options.checkpoint_path)
        publish("retry_wait", summary)
        return OperationResult(request.operation_id, request.operation, "retry_wait", started_at, project_record.iso_now(), summary)
    publish("completed", summary)
    return OperationResult(request.operation_id, request.operation, "completed", started_at, project_record.iso_now(), summary)
