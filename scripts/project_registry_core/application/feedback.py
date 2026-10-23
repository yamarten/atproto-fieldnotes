"""Explicit operator feedback; no network, LLM, or inferred ownership."""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from .. import operator_feedback, project_record, registry as core
from ..identity import identifiers
from ..persistence import runtime_view
from .browser_entry import page_url


def save_feedback(conn: sqlite3.Connection, *, project_key: str, kind: str, reason: str = "",
                  clear: bool = False, service_url: str = "", nsid: str = "", source_url: str = "") -> dict[str, Any]:
    project = core.resolve_single_project(conn, project_key=project_key)
    now = project_record.iso_now()
    reason = reason.strip()
    if len(reason) > 4000:
        raise ValueError("reason must be at most 4000 characters")
    if kind == "introduction-plan":
        field = "introduction_plans"
        attributes = {"introduced_url_at_creation": str(project.get("introduced_url") or "")}
    elif kind == "service-usage":
        field = "service_usage_decisions"
        service_url = page_url(service_url, preserve_target=True)
        nsid = identifiers.normalize_nsid(nsid)
        if not identifiers.is_valid_nsid(nsid):
            raise ValueError("a valid collection NSID is required")
        services = {item.value for item in runtime_view.project_identifiers(project)
                    if item.kind == "service_url" and item.role == "service"}
        if not clear and service_url not in services:
            raise ValueError("service URL must belong to this project's saved services")
        if not clear and not reason:
            raise ValueError("reason is required for service usage")
        attributes = {"service_url": service_url, "nsid": nsid,
                      "source_url": page_url(source_url or service_url)}
    else:
        raise ValueError("unknown feedback kind")
    entries = operator_feedback.normalize_history(project.get(field))
    for entry in entries:
        if not entry.get("cleared_at") and (kind == "introduction-plan" or
                (entry.get("service_url") == service_url and entry.get("nsid") == nsid)):
            entry["cleared_at"] = now
    if not clear:
        entries.append({"id": uuid.uuid4().hex, "reason": reason, "added_at": now,
                        "origin_project_key": project["project_key"], **attributes})
    project[field] = entries
    operator_feedback.reconcile_usage(project)
    project_record.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), preserve_timestamps=True)
    return {"project_key": project["project_key"], "kind": kind, "cleared": clear}
