"""NSID identity is shared; publication validation and consumers remain typed."""
from __future__ import annotations

from typing import Any, Mapping

PRIMARY_TYPES = frozenset({"record", "space", "query", "procedure", "subscription", "permission-set"})
DEFINITION_TYPES = frozenset({"object", "string", "token", "array", "integer", "boolean", "bytes", "blob", "cid-link", "ref", "union", "unknown"})


def schema_type(schema: Mapping[str, Any]) -> str:
    definitions = schema.get("defs")
    if not isinstance(definitions, Mapping) or not definitions:
        return "unknown"
    if any(not isinstance(value, Mapping) or str(value.get("type") or "") not in PRIMARY_TYPES | DEFINITION_TYPES
           for value in definitions.values()):
        return "unknown"
    main = definitions.get("main")
    if main is None:
        return "definitions"
    if not isinstance(main, Mapping):
        return "unknown"
    kind = str(main.get("type") or "unknown")
    return kind if kind in PRIMARY_TYPES else "definitions" if kind in DEFINITION_TYPES else "unknown"


def record_sample_nsids(project: Mapping[str, Any]) -> list[str]:
    """Preserve legacy collection inputs, but never sample known non-records."""
    kinds = {str(item.get("schema_id")): str(item.get("schema_type"))
             for item in project.get("publication_schemas", []) if isinstance(item, dict)}
    for item in project.get("lexicon_schema_samples", []):
        if isinstance(item, dict):
            sid = str(item.get("id") or item.get("nsid") or "")
            schema = item.get("schema") if isinstance(item.get("schema"), dict) else item
            if sid and isinstance(schema.get("defs"), dict):
                sample_kind = schema_type(schema)
                if sample_kind != "record" or sid not in kinds:
                    kinds[sid] = sample_kind
    latest: dict[str, Mapping[str, Any]] = {}
    for entry in project.get("lexicon_resolution_entries", []):
        sid = str(entry.get("input_nsid") or "")
        if sid and str(entry.get("resolved_at") or "") >= str(latest.get(sid, {}).get("resolved_at") or ""):
            latest[sid] = entry
    for sid, entry in latest.items():
        validation = entry.get("publication_validation")
        if isinstance(validation, Mapping) and (not validation.get("version") or validation.get("schema_type") != "record" or validation.get("type_changed")):
            kinds[sid] = "unknown"
    return list(dict.fromkeys(str(n) for n in list(project.get("resolved_nsids", [])) + list(project.get("input_nsids", []))
                             if kinds.get(str(n), "record") == "record"))
