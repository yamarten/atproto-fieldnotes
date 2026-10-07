from __future__ import annotations

from project_registry_core import project_review

from project_registry_core import project_merge

from project_registry_core import naming

from .. import editorial

import sqlite3
from typing import Any, Callable, Iterable

from .. import registry as core
from ..identity import identifiers
from project_registry_core import project_record


def _clear_exact_identifier_guards(project: dict[str, Any], identifier_type: str, value: str) -> None:
    normalized = project_record.normalize_blocked_identifier_value(identifier_type, value)
    project["blocked_identifiers"] = [
        item
        for item in project_record.normalize_blocked_identifiers(list(project.get("blocked_identifiers", [])))
        if not (
            str(item.get("type") or "") == identifier_type
            and str(item.get("value") or "") == normalized
        )
    ]
    project["deferred_identifiers"] = [
        item
        for item in project_record.normalize_deferred_identifiers(list(project.get("deferred_identifiers", [])))
        if not (
            str(item.get("type") or "") == identifier_type
            and str(item.get("value") or "") == normalized
        )
    ]


def add_seed(
    conn: sqlite3.Connection,
    *,
    seed: str,
    seed_type: str | None = None,
    project_key: str | None = None,
) -> dict[str, Any]:
    resolved_type = core.detect_seed_type(seed, seed_type)
    project = core.ensure_project_for_seed(
        conn,
        resolved_type,
        seed,
        project_key=project_key,
        commit=False,
    )
    normalized_value = project_record.canonical_seed_value(resolved_type, seed)
    if resolved_type in {"service_url", "repo_url"}:
        _clear_exact_identifier_guards(project, resolved_type, normalized_value)
    elif project_record.identifier_is_blocked(project, resolved_type, normalized_value) or project_record.identifier_is_deferred(
        project, resolved_type, normalized_value
    ):
        conn.rollback()
        raise ValueError("guarded identifier cannot be added as a seed")
    confirmed_role = ""
    if resolved_type == "service_url":
        core.confirm_service_url(project, normalized_value, operator_override=True)
        if not str(project.get("website_url") or "").strip():
            project["website_url"] = normalized_value
        origin = core.url_origin(normalized_value)
        project_record.set_service_url_candidate_values(
            project,
            [
                value
                for value in project_record.service_url_candidate_values(project)
                if not origin or core.url_origin(value) != origin
            ],
        )
        if normalized_value in project.get("service_urls", []):
            confirmed_role = "service"
    elif resolved_type == "repo_url":
        core.confirm_project_repository_url(
            project,
            normalized_value,
            source="operator_add_seed",
            relation="source_repo",
            reason="repository URL added by operator",
            evidence_kind="operator_seed",
            operator_override=True,
        )
        if normalized_value in project_record.project_repository_url_values(project):
            confirmed_role = "project_repository"
    elif resolved_type == "nsid":
        if normalized_value in project.get("resolved_nsids", []):
            confirmed_role = "owned_nsid"
    if confirmed_role:
        core.add_confirmed_identifier_decision(
            project,
            resolved_type,
            normalized_value,
            source="operator_add_seed",
            note="identifier added by operator",
            role=confirmed_role,
            registration_origin=True,
        )
    project = core.finalize_project_shape(project)
    project_record.save_project(
        conn,
        project,
        enriched=bool(project.get("last_enriched_at")),
        preserve_timestamps=True,
        commit=False,
    )
    conn.commit()
    return {
        "project_key": project["project_key"],
        "seed_type": resolved_type,
        "seed_value": normalized_value,
        "confirmed_role": confirmed_role,
        "name": project.get("name", ""),
    }


def select_projects(
    conn: sqlite3.Connection,
    *,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
    all_projects: bool = False,
    tracking_states: set[str] | None = None,
    create_seed: bool = False,
) -> list[dict[str, Any]]:
    if project_key:
        project = project_record.fetch_project(conn, project_record.resolve_project_key_input(conn, project_key))
        if project is None:
            raise ValueError(f"project not found: {project_key}")
        return [project]
    if seed:
        resolved_type = core.detect_seed_type(seed, seed_type)
        identifier_value = project_record.canonical_seed_value(resolved_type, seed)
        resolved_key = project_record.find_project_key_by_identifier(conn, resolved_type, identifier_value)
        if resolved_key is None:
            if not create_seed:
                raise ValueError(f"project not found for seed: {seed}")
            project = core.ensure_project_for_seed(conn, resolved_type, seed)
            conn.commit()
            return [project]
        project = project_record.fetch_project(conn, resolved_key)
        return [project] if project else []
    if all_projects:
        return project_record.list_projects(conn, tracking_states=tracking_states)
    raise ValueError("choose one of project_key, seed, or all_projects")


def prepare_project_view(
    conn: sqlite3.Connection, project: dict[str, Any], *, check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    duplicate_candidates = project_merge.compute_duplicate_candidates(conn, project, check_cancelled=check_cancelled)
    prepared = dict(project, duplicate_candidates=duplicate_candidates)
    prepared["merge_recommendation"] = project_merge.compute_merge_recommendation(prepared)
    return project_review.apply_promotion_state(prepared)


def prepare_project_views(
    conn: sqlite3.Connection,
    projects: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [prepare_project_view(conn, project) for project in projects]


def set_tracking_state(
    conn: sqlite3.Connection,
    *,
    project_key: str | None = None,
    seed: str | None = None,
    seed_type: str | None = None,
    tracking_state: str,
    note: str | None = None,
) -> dict[str, Any]:
    project = core.resolve_single_project(conn, project_key=project_key, seed=seed, seed_type=seed_type)
    target_state = str(tracking_state or "").strip().lower()
    if target_state not in project_record.TRACKING_STATES:
        raise ValueError(f"invalid tracking_state: {tracking_state}")
    project["tracking_state"] = target_state
    if note is not None:
        project["tracking_note"] = str(note).strip()
    project_record.save_project(
        conn,
        project,
        enriched=bool(project.get("last_enriched_at")),
        preserve_timestamps=True,
    )
    conn.commit()
    return {
        "project_key": project["project_key"],
        "tracking_state": project.get("tracking_state", "active"),
        "tracking_note": project.get("tracking_note", ""),
    }


def dismiss_description_proposal(conn: sqlite3.Connection, *, project_key: str) -> dict[str, Any]:
    project = core.resolve_single_project(conn, project_key=project_key)
    editorial.dismiss_proposal(project)
    project_record.save_project(conn, project, preserve_timestamps=True)
    conn.commit()
    return {"project_key": project["project_key"], "mode": "dismissed"}


def preview_profile_edit(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    name: str,
    notes: str,
    introduced_url: str,
    description: dict[str, Any] | None = None,
    automatic_description: bool | None = None,
) -> dict[str, Any]:
    project = core.resolve_single_project(conn, project_key=project_key)
    normalized_introduced_url = identifiers.normalize_url(str(introduced_url or "")) if introduced_url else ""
    if str(introduced_url or "").strip() and not normalized_introduced_url:
        raise ValueError("introduced_url must be an http(s) URL")
    before = {
        "name": str(project.get("name") or ""),
        "notes": str(project.get("notes") or ""),
        "introduced_url": str(project.get("introduced_url") or ""),
        **editorial.profile(project),
        "automatic_description": editorial.automatic(project),
    }
    after = {
        **before,
        "name": str(name or "").strip(),
        "notes": str(notes or "").strip(),
        "introduced_url": normalized_introduced_url,
    }
    if description is not None:
        if not isinstance(description, dict) or set(description) - set(editorial.FIELDS):
            raise ValueError("description must contain only tagline, summary and features")
        for field in ("tagline", "summary"):
            if field in description:
                value = description[field]
                if not isinstance(value, str) or len(value) > (30 if field == "tagline" else 8000):
                    raise ValueError(f"invalid {field}")
                after[field] = project_record.normalize_ja_en_mixed_spacing(value.strip())
        if "features" in description:
            values = description["features"]
            if not isinstance(values, list) or len(values) > 6 or any(not isinstance(v, str) or len(v) > 1000 for v in values):
                raise ValueError("features must contain at most 6 short strings")
            after["features"] = core.clean_human_string_list(values, limit=6)
        if any(before[field] != after[field] for field in editorial.FIELDS):
            after["automatic_description"] = False
    if automatic_description is not None:
        if not isinstance(automatic_description, bool):
            raise ValueError("automatic_description must be a boolean")
        after["automatic_description"] = automatic_description
    changes = [
        {"field": field, "before": before[field], "after": after[field]}
        for field in before
        if before[field] != after[field]
    ]
    return {
        "project_key": str(project["project_key"]),
        "before": before,
        "after": after,
        "changes": changes,
        "changed": bool(changes),
    }


def apply_profile_edit(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    name: str,
    notes: str,
    introduced_url: str,
    description: dict[str, Any] | None = None,
    automatic_description: bool | None = None,
) -> dict[str, Any]:
    preview = preview_profile_edit(
        conn,
        project_key=project_key,
        name=name,
        notes=notes,
        introduced_url=introduced_url,
        description=description,
        automatic_description=automatic_description,
    )
    if not preview["changed"]:
        return {**preview, "mode": "no_change"}
    project = core.resolve_single_project(conn, project_key=project_key)
    if preview["before"]["name"] != preview["after"]["name"]:
        project["name_candidates"] = [
            item
            for item in project.get("name_candidates", [])
            if isinstance(item, dict) and str(item.get("source") or "") != "operator_edit"
        ]
        naming.add_name_candidate(project, preview["after"]["name"], "operator_edit")
    project["name"] = preview["after"]["name"]
    project["notes"] = preview["after"]["notes"]
    project["introduced_url"] = preview["after"]["introduced_url"]
    if any(item["field"] in (*editorial.FIELDS, "automatic_description") for item in preview["changes"]):
        for field in editorial.FIELDS:
            project[field] = preview["after"][field]
        project["tagline_is_provisional"] = False
        editorial.record_edit(project, allow_automatic=preview["after"]["automatic_description"],
                              updated_at=project_record.iso_now())
    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True)
    conn.commit()
    return {**preview, "mode": "updated"}
