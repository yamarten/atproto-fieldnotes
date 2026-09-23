"""Immutable, private snapshots of individual LLM evaluation cases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import registry

SCHEMA_VERSION = 1
CASE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\Z")
URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
INLINE_SECRET = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;&]+")
SECRET_KEYS = {"authorization", "cookie", "cookies", "set_cookie", "password", "secret", "api_key", "access_token", "refresh_token", "private_key"}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _redact(value: Any, at: str, changed: list[str]) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string JSON key at {at}")
            path = f"{at}.{key}"
            lowered = key.lower().replace("-", "_")
            if lowered in SECRET_KEYS or lowered.endswith(("_api_key", "_password", "_secret", "_token")):
                result[key] = "[redacted]"
                if item != "[redacted]":
                    changed.append(path)
            else:
                result[key] = _redact(item, path, changed)
        return result
    if isinstance(value, list):
        return [_redact(item, f"{at}[{index}]", changed) for index, item in enumerate(value)]
    if not isinstance(value, str):
        return value

    def clean_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        trailing = raw[len(raw.rstrip(".,;!?)")):]
        raw = raw.rstrip(".,;!?)")
        try:
            parsed = urlsplit(raw)
            if not parsed.hostname:
                return match.group(0)
            host = parsed.hostname
            if ":" in host:
                host = f"[{host}]"
            if parsed.port:
                host = f"{host}:{parsed.port}"
            cleaned = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
            return cleaned + trailing
        except ValueError:
            return match.group(0)

    result = URL.sub(clean_url, value)
    result = BEARER.sub("Bearer [redacted]", result)
    result = INLINE_SECRET.sub(lambda match: match.group(1) + "=[redacted]", result)
    if result != value:
        changed.append(at)
    return result


def _nonempty_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _validate_snapshot(value: Any, name: str) -> None:
    if not isinstance(value, dict) or value.get("status") not in {"captured", "reconstructed", "missing"}:
        raise ValueError(f"{name}.status must be captured, reconstructed, or missing")
    if value["status"] == "missing":
        if "payload" in value:
            raise ValueError(f"{name}.payload must be absent when status is missing")
    elif "payload" not in value:
        raise ValueError(f"{name}.payload is required")


def _validate_case(case: Any) -> None:
    if not isinstance(case, dict) or case.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported case schema_version")
    allowed = {"schema_version", "case_id", "task", "origin", "input", "output", "execution",
               "used_for_adjustment", "assessments", "sanitization"}
    if set(case) != allowed:
        raise ValueError(f"case fields must be exactly {sorted(allowed)}")
    if not isinstance(case.get("case_id"), str) or not CASE_ID.fullmatch(case["case_id"]):
        raise ValueError("case_id must be 1-80 ASCII letters, digits, _ or -")
    if case.get("task") not in {"triage", "repository_analysis"}:
        raise ValueError("task must be triage or repository_analysis")
    origin = case.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") not in {"saved_run", "evaluation", "manual", "reconstructed"}:
        raise ValueError("origin.kind must identify the source type")
    _nonempty_text(origin.get("source"), "origin.source")
    for name in ("input", "output", "execution"):
        _validate_snapshot(case.get(name), name)
    if not isinstance(case.get("used_for_adjustment"), bool):
        raise ValueError("used_for_adjustment must be boolean")
    assessments = case.get("assessments")
    if not isinstance(assessments, list):
        raise ValueError("assessments must be a list; an empty list means unassessed")
    for index, assessment in enumerate(assessments):
        name = f"assessments[{index}]"
        if not isinstance(assessment, dict) or assessment.get("verdict") not in {"supported", "unsupported", "uncertain"}:
            raise ValueError(f"{name}.verdict is invalid")
        if assessment.get("authority") not in {"human", "codex", "model", "unreviewed"}:
            raise ValueError(f"{name}.authority is invalid")
        for field in ("claim", "reason"):
            _nonempty_text(assessment.get(field), f"{name}.{field}")
    sanitation = case.get("sanitization")
    if not isinstance(sanitation, dict) or not isinstance(sanitation.get("human_reviewed"), bool):
        raise ValueError("sanitization.human_reviewed must be boolean")
    if "auto_redacted_paths" in sanitation and (
        not isinstance(sanitation["auto_redacted_paths"], list)
        or any(not isinstance(item, str) for item in sanitation["auto_redacted_paths"])
    ):
        raise ValueError("sanitization.auto_redacted_paths must be a list of paths")


def _store_path(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("case store must be an absolute path outside the repository")
    result = path.expanduser().resolve()
    if result.is_relative_to(registry.ROOT.resolve()) or any(
        (parent / ".git").exists() for parent in (result, *result.parents)
    ):
        raise ValueError("case store must be outside every Git repository and worktree")
    return result


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"private case directory has group/other access: {path}")


def _write_immutable(directory: Path, name: str, payload: dict[str, Any]) -> Path:
    _private_directory(directory)
    target = directory / name
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".case-", dir=directory, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)  # no replacement, including concurrent writers
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def register(store: Path, source: Path) -> dict[str, Any]:
    store = _store_path(store)
    case = json.loads(source.read_text(encoding="utf-8"))
    _validate_case(case)
    previous_redactions = case["sanitization"].get("auto_redacted_paths", [])
    redacted: list[str] = []
    case = _redact(case, "$", redacted)
    case["sanitization"]["auto_redacted_paths"] = sorted(set(previous_redactions) | set(redacted))
    # Automatic changes invalidate a prior assertion that the final snapshot was reviewed.
    if redacted:
        case["sanitization"]["human_reviewed"] = False
    _validate_case(case)
    digest = hashlib.sha256(_json_bytes(case)).hexdigest()
    envelope = {"case": case, "sha256": digest}
    target = _write_immutable(store, f"{case['case_id']}.json", envelope)
    return {"case_id": case["case_id"], "path": str(target), "sha256": digest,
            "auto_redacted_paths": case["sanitization"]["auto_redacted_paths"],
            "human_reviewed": case["sanitization"]["human_reviewed"]}


def _read_base(store: Path, case_id: str) -> dict[str, Any]:
    store = _store_path(store)
    if not CASE_ID.fullmatch(case_id):
        raise ValueError("invalid case_id")
    path = store / f"{case_id}.json"
    if path.is_symlink():
        raise ValueError("case file must not be a symlink")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or not isinstance(envelope.get("case"), dict):
        raise ValueError(f"invalid case envelope: {case_id}")
    case = envelope["case"]
    _validate_case(case)
    if case["case_id"] != case_id or hashlib.sha256(_json_bytes(case)).hexdigest() != envelope.get("sha256"):
        raise ValueError(f"case integrity check failed: {case_id}")
    return envelope


def _validate_event(event: Any) -> None:
    if not isinstance(event, dict) or event.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported case event schema_version")
    if not isinstance(event.get("case_id"), str) or not CASE_ID.fullmatch(event["case_id"]):
        raise ValueError("invalid event case_id")
    if not isinstance(event.get("event_id"), str) or not CASE_ID.fullmatch(event["event_id"]):
        raise ValueError("invalid event_id")
    if event.get("kind") not in {"assessment", "adjustment_used", "snapshot_reviewed"}:
        raise ValueError("invalid case event kind")
    if event.get("authority") not in {"human", "codex", "model", "unreviewed"}:
        raise ValueError("invalid case event authority")
    _nonempty_text(event.get("source"), "event.source")
    _nonempty_text(event.get("reason"), "event.reason")
    if event["kind"] == "assessment":
        assessment = event.get("assessment")
        if not isinstance(assessment, dict) or assessment.get("verdict") not in {"supported", "unsupported", "uncertain"}:
            raise ValueError("event assessment.verdict is invalid")
        _nonempty_text(assessment.get("claim"), "event.assessment.claim")
        if "supersedes" in event and not isinstance(event["supersedes"], str):
            raise ValueError("event.supersedes must be an assessment ID")
        required = {"schema_version", "case_id", "event_id", "kind", "authority", "source", "reason", "assessment"}
        if set(event) not in (required, required | {"supersedes"}):
            raise ValueError("assessment event fields are invalid")
    elif "assessment" in event:
        raise ValueError("only assessment events may include an assessment")
    elif set(event) != {"schema_version", "case_id", "event_id", "kind", "authority", "source", "reason"}:
        raise ValueError("case event fields are invalid")
    if event["kind"] == "snapshot_reviewed" and event["authority"] != "human":
        raise ValueError("snapshot_reviewed requires human authority")


def append_event(store: Path, source: Path) -> dict[str, Any]:
    store = _store_path(store)
    event = json.loads(source.read_text(encoding="utf-8"))
    _validate_event(event)
    current = read(store, event["case_id"])
    if "supersedes" in event and event["supersedes"] not in {
        item["assessment_id"] for item in current["effective"]["assessments"]
    }:
        raise ValueError("supersedes must name a current assessment")
    _private_directory(store)
    redacted: list[str] = []
    event = _redact(event, "$", redacted)
    if redacted:
        raise ValueError("event contains secrets; redact and review the draft before appending")
    _validate_event(event)
    event["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    digest = hashlib.sha256(_json_bytes(event)).hexdigest()
    if (store / "events").is_symlink() or (store / "events" / event["case_id"]).is_symlink():
        raise ValueError("case event directories must not be symlinks")
    directory = store / "events" / event["case_id"]
    target = _write_immutable(directory, f"{event['event_id']}.json", {"event": event, "sha256": digest})
    return {"case_id": event["case_id"], "event_id": event["event_id"], "path": str(target), "sha256": digest}


def _read_events(store: Path, case_id: str) -> list[dict[str, Any]]:
    directory = store / "events" / case_id
    if (store / "events").is_symlink() or directory.is_symlink():
        raise ValueError("case event directory must not be a symlink")
    if not directory.exists():
        return []
    events = []
    for path in directory.glob("*.json"):
        if path.is_symlink() or not CASE_ID.fullmatch(path.stem):
            raise ValueError(f"invalid case event path: {path}")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or not isinstance(envelope.get("event"), dict):
            raise ValueError(f"invalid case event envelope: {path}")
        event = envelope["event"]
        recorded_at = event.get("recorded_at")
        if not isinstance(recorded_at, str):
            raise ValueError(f"case event timestamp missing: {path}")
        unsigned = {key: value for key, value in event.items() if key != "recorded_at"}
        _validate_event(unsigned)
        if event["case_id"] != case_id or event["event_id"] != path.stem or hashlib.sha256(_json_bytes(event)).hexdigest() != envelope.get("sha256"):
            raise ValueError(f"case event integrity check failed: {path}")
        events.append(event)
    return sorted(events, key=lambda item: (item["recorded_at"], item["event_id"]))


def read(store: Path, case_id: str) -> dict[str, Any]:
    store = _store_path(store)
    envelope = _read_base(store, case_id)
    case = envelope["case"]
    events = _read_events(store, case_id)
    envelope["events"] = events
    assessments = [
        {**assessment, "assessment_id": f"base:{index}"}
        for index, assessment in enumerate(case["assessments"])
    ]
    assessments.extend(
        {**event["assessment"], "authority": event["authority"], "reason": event["reason"],
         "assessment_id": event["event_id"]}
        for event in events if event["kind"] == "assessment"
    )
    assessment_ids = {item["assessment_id"] for item in assessments}
    superseded = [event["supersedes"] for event in events if event["kind"] == "assessment" and "supersedes" in event]
    if len(set(superseded)) != len(superseded) or any(item not in assessment_ids for item in superseded):
        raise ValueError(f"invalid assessment revision history: {case_id}")
    replacements = {event["supersedes"]: event["event_id"] for event in events
                    if event["kind"] == "assessment" and "supersedes" in event}
    for start in replacements:
        seen = set()
        cursor = start
        while cursor in replacements:
            if cursor in seen:
                raise ValueError(f"cyclic assessment revision history: {case_id}")
            seen.add(cursor)
            cursor = replacements[cursor]
    envelope["effective"] = {
        "used_for_adjustment": case["used_for_adjustment"] or any(event["kind"] == "adjustment_used" for event in events),
        "human_reviewed": case["sanitization"]["human_reviewed"] or any(event["kind"] == "snapshot_reviewed" for event in events),
        "assessments": [item for item in assessments if item["assessment_id"] not in replacements],
    }
    return envelope


def list_cases(store: Path) -> list[dict[str, Any]]:
    store = _store_path(store)
    if not store.exists():
        return []
    rows = []
    for path in sorted(store.glob("*.json")):
        envelope = read(store, path.stem)
        case = envelope["case"]
        rows.append({"case_id": case["case_id"], "task": case["task"],
                     "input_status": case["input"]["status"],
                     "assessment_count": len(envelope["effective"]["assessments"]),
                     "event_count": len(envelope["events"]),
                     "human_reviewed": envelope["effective"]["human_reviewed"],
                     "used_for_adjustment": envelope["effective"]["used_for_adjustment"]})
    return rows
