"""Tracking transitions caused by explicit choices and introduction events."""
from __future__ import annotations

from typing import Any

from .identity import identifiers


def set_state(project: dict[str, Any], state: str) -> None:
    project["tracking_state"] = state
    # A later choice, including choosing stable again, supersedes the automatic
    # introduction transition. Removing the introduction must respect it.
    project["tracking_from_introduction"] = False


def set_introduced_url(project: dict[str, Any], value: str) -> None:
    previous = str(project.get("introduced_url") or "")
    current = identifiers.normalize_url(value) if value else ""
    project["introduced_url"] = current
    if not previous and current and project.get("tracking_state", "active") == "active":
        project["tracking_state"] = "stable"
        project["tracking_from_introduction"] = True
    elif previous and not current:
        if project.get("tracking_from_introduction") is True and project.get("tracking_state") == "stable":
            project["tracking_state"] = "active"
        project["tracking_from_introduction"] = False


def reactivation_state(project: dict[str, Any]) -> str:
    return "stable" if project.get("introduced_url") else "active"


def reactivate(project: dict[str, Any]) -> None:
    set_state(project, reactivation_state(project))
    project["tracking_from_introduction"] = bool(project.get("introduced_url"))
