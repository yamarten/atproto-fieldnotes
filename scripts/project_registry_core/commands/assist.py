from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .. import registry as core


def should_interactively_confirm(args: argparse.Namespace) -> bool:
    return not bool(getattr(args, "execute", False)) and sys.stdin.isatty() and sys.stdout.isatty()


def prompt_for_confirmation(message: str) -> bool:
    print(message, file=sys.stderr)
    try:
        answer = input().strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def cmd_assist_db_edit(args: argparse.Namespace) -> int:
    conn = core.open_db()
    request_text = str(args.text or "").strip()
    if not request_text:
        raise ValueError("text is required")

    parsed, meta = core.parse_db_edit_request(
        request_text,
        llm_endpoint=getattr(args, "llm_endpoint", None),
        llm_model=getattr(args, "llm_model", None),
        llm_api_key=getattr(args, "llm_api_key", None),
        llm_reasoning_effort=getattr(args, "llm_reasoning_effort", None),
        llm_api_style=getattr(args, "llm_api_style", None),
    )
    action = str(parsed.get("action") or "unknown").strip()
    payload: dict[str, Any] = {
        "mode": "execute" if args.execute else "dry_run",
        "request": request_text,
        "parsed": parsed,
        "parser_meta": meta,
        "action": action,
    }
    confirmed_interactively = False

    if action in {"merge", "set_separate"}:
        source_query = str(parsed.get("source_query") or "").strip()
        target_query = str(parsed.get("target_query") or "").strip()
        source_candidates = core.project_search_candidates(conn, source_query, limit=5)
        target_candidates = core.project_search_candidates(conn, target_query, limit=5)
        payload["source_candidates"] = source_candidates
        payload["target_candidates"] = target_candidates
        if source_candidates:
            payload["suggested_source"] = source_candidates[0]
        if target_candidates:
            payload["suggested_target"] = target_candidates[0]
        if source_candidates and target_candidates:
            payload["suggested_command"] = (
                "project-registry "
                + ("merge" if action == "merge" else "set-separate")
                + f" -p {source_candidates[0]['project_key']} "
                + (f"-t {target_candidates[0]['project_key']}" if action == "merge" else f"-o {target_candidates[0]['project_key']}")
            )
        can_execute = (
            len(source_candidates) == 1
            and len(target_candidates) == 1
            and source_candidates[0]["project_key"] != target_candidates[0]["project_key"]
        )
        payload["can_execute"] = can_execute
        if can_execute and should_interactively_confirm(args):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            confirmed_interactively = prompt_for_confirmation("Apply this change now? [y/N]")
        if (args.execute or confirmed_interactively) and can_execute:
            if action == "merge":
                source = core.ensure_project_shape(core.fetch_project(conn, source_candidates[0]["project_key"]) or {})
                target = core.ensure_project_shape(core.fetch_project(conn, target_candidates[0]["project_key"]) or {})
                if core.merge_is_blocked(source, target):
                    raise ValueError("merge is manually blocked for this project pair")
                merged = core.merge_project_records(target, source)
                merged["duplicate_candidates"] = core.compute_duplicate_candidates(conn, merged)
                merged["merge_recommendation"] = core.compute_merge_recommendation(merged)
                merged = core.apply_promotion_state(merged)
                merged["separate_project_keys"] = [
                    key for key in merged.get("separate_project_keys", [])
                    if key not in {source["project_key"], target["project_key"]}
                ]
                with conn:
                    core.save_project(conn, merged, enriched=bool(merged.get("last_enriched_at")), commit=False)
                    core.register_project_merge_alias(
                        conn,
                        source_project_key=source["project_key"],
                        target_project_key=target["project_key"],
                    )
                    core.delete_project(conn, source["project_key"])
                payload["executed"] = {
                    "source_project_key": source["project_key"],
                    "target_project_key": target["project_key"],
                    "merged_name": merged.get("name", ""),
                }
            else:
                project = core.ensure_project_shape(core.fetch_project(conn, source_candidates[0]["project_key"]) or {})
                other = core.ensure_project_shape(core.fetch_project(conn, target_candidates[0]["project_key"]) or {})
                project_blocked = set(project.get("separate_project_keys", []))
                other_blocked = set(other.get("separate_project_keys", []))
                project_blocked.add(other["project_key"])
                other_blocked.add(project["project_key"])
                project["separate_project_keys"] = sorted(project_blocked)
                other["separate_project_keys"] = sorted(other_blocked)
                for item in (project, other):
                    item["duplicate_candidates"] = core.compute_duplicate_candidates(conn, item)
                    item["merge_recommendation"] = core.compute_merge_recommendation(item)
                with conn:
                    core.save_project(conn, project, enriched=bool(project.get("last_enriched_at")), commit=False)
                    core.save_project(conn, other, enriched=bool(other.get("last_enriched_at")), commit=False)
                payload["executed"] = {
                    "project_key": project["project_key"],
                    "other_project_key": other["project_key"],
                }
            payload["mode"] = "execute"
            payload["confirmed_interactively"] = confirmed_interactively
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if action == "block_identifier":
        project_query = str(parsed.get("project_query") or "").strip()
        identifier_value = str(parsed.get("identifier_value") or "").strip()
        identifier_type = str(parsed.get("identifier_type") or "").strip()
        project_candidates = core.project_search_candidates(conn, project_query, limit=5)
        payload["project_candidates"] = project_candidates
        payload["identifier_type"] = identifier_type
        payload["identifier_value"] = identifier_value
        if project_candidates:
            payload["suggested_project"] = project_candidates[0]
            if identifier_type and identifier_value:
                payload["suggested_command"] = (
                    "project-registry block-identifier "
                    f"-p {project_candidates[0]['project_key']} -i {identifier_type} -v {identifier_value}"
                )
        can_execute = len(project_candidates) == 1 and identifier_type in {"nsid", "repo_url", "service_url"} and bool(identifier_value)
        payload["can_execute"] = can_execute
        if can_execute and should_interactively_confirm(args):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            confirmed_interactively = prompt_for_confirmation("Apply this change now? [y/N]")
        if (args.execute or confirmed_interactively) and can_execute:
            project = core.ensure_project_shape(core.fetch_project(conn, project_candidates[0]["project_key"]) or {})
            normalized_value = core.normalize_blocked_identifier_value(identifier_type, identifier_value)
            blocked = core.normalize_blocked_identifiers(list(project.get("blocked_identifiers", [])))
            key = (identifier_type, normalized_value)
            if not any((str(item.get("type") or ""), str(item.get("value") or "")) == key for item in blocked):
                blocked.append({"type": identifier_type, "value": normalized_value, "note": str(parsed.get("note") or "").strip(), "added_at": core.iso_now()})
            project["blocked_identifiers"] = blocked
            project = core.finalize_project_shape(project)
            project["duplicate_candidates"] = core.compute_duplicate_candidates(conn, project)
            project["merge_recommendation"] = core.compute_merge_recommendation(project)
            core.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
            conn.commit()
            payload["executed"] = {"project_key": project["project_key"], "identifier_type": identifier_type, "value": normalized_value}
            payload["mode"] = "execute"
            payload["confirmed_interactively"] = confirmed_interactively
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if action == "set_tracking_state":
        project_query = str(parsed.get("project_query") or "").strip()
        tracking_state = core.normalize_tracking_state(str(parsed.get("tracking_state") or "").strip())
        project_candidates = core.project_search_candidates(conn, project_query, limit=5)
        payload["project_candidates"] = project_candidates
        payload["tracking_state"] = tracking_state
        if project_candidates:
            payload["suggested_project"] = project_candidates[0]
            payload["suggested_command"] = (
                "project-registry set-tracking-state "
                f"-p {project_candidates[0]['project_key']} -S {tracking_state}"
            )
        can_execute = len(project_candidates) == 1 and tracking_state in core.TRACKING_STATES
        payload["can_execute"] = can_execute
        if can_execute and should_interactively_confirm(args):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            confirmed_interactively = prompt_for_confirmation("Apply this change now? [y/N]")
        if (args.execute or confirmed_interactively) and can_execute:
            project = core.fetch_project(conn, project_candidates[0]["project_key"])
            if not project:
                raise ValueError("project not found")
            project["tracking_state"] = tracking_state
            note = str(parsed.get("note") or "").strip()
            if note:
                project["tracking_note"] = note
            core.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
            conn.commit()
            payload["executed"] = {"project_key": project["project_key"], "tracking_state": tracking_state}
            payload["mode"] = "execute"
            payload["confirmed_interactively"] = confirmed_interactively
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    payload["message"] = "supported actions: merge, set_separate, block_identifier, set_tracking_state"
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0
