"""The accepted resources a registry project represents, and their adoption reasons."""
from __future__ import annotations

from typing import Any, Mapping

from . import relations
from .persistence import identifier_storage, runtime_view


def confirmed_components(project: Mapping[str, Any]) -> list[Any]:
    return [item for item in runtime_view.project_identifiers(project)
            if item.classification == "confirmed" and item.role in identifier_storage.COMPONENT_ROLES
            and not relations.identifier_is_guarded(project, item.kind, item.value)]


def projection(project: Mapping[str, Any]) -> dict[str, Any]:
    rows = project.get(runtime_view.RUNTIME_ADOPTION_ROWS, project.get("identifier_adoptions", []))
    components = []
    for item in confirmed_components(project):
        adoptions = [{key: row.get(key, "") for key in (
            "adoption_kind", "source", "created_at", "registration_origin", "review_required", "review_reason",
        )} for row in rows if row.get("kind") == item.kind and row.get("value") == item.value
                     and row.get("role") == item.role and not row.get("cleared_at")]
        components.append({"kind": item.kind, "value": item.value, "role": item.role,
                           "adoptions": adoptions,
                           "registration_origin": any(row["registration_origin"] for row in adoptions)})
    return {"components": components, "has_identity": bool(components),
            "discovery_sources": list(project.get("discovery_sources", []))}
