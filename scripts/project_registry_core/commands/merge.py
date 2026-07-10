from __future__ import annotations

import argparse
import copy
import json
import sys

from .. import registry as core
from ..application import merge as services


def cmd_inspect_merge_pair(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.inspect_pair(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.type,
            target_project_key=args.target_project_key,
            target_seed=args.target_seed,
            target_seed_type=args.target_type,
        )
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.merge_projects(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.type,
            target_project_key=args.target_project_key,
            target_seed=args.target_seed,
            target_seed_type=args.target_type,
            use_recommendation=bool(args.use_recommendation),
            execute=bool(args.execute),
            force=bool(args.force),
        )
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0


def cmd_split_project(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.split_identifier(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.project_type,
            identifier_type=args.identifier_type,
            value=args.value,
            note=args.note or "",
            execute=bool(args.execute),
        )
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0


def cmd_merge_batch(args: argparse.Namespace) -> int:
    conn = core.open_db()
    if args.interactive:
        args.execute = True
    effective_mode = str(args.mode or ("moderate" if args.interactive else "strict"))
    plans, skipped = core.build_batch_merge_plan(
        conn,
        mode=effective_mode,
    )
    if args.max_merges is not None:
        plans = plans[: max(0, int(args.max_merges))]

    executed: list[dict[str, Any]] = []
    blocked_pairs: list[dict[str, Any]] = []
    ignored_projects: list[dict[str, Any]] = []
    undo_stack: list[dict[str, Any]] = []
    interactive = bool(args.interactive)
    if interactive and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise ValueError("--interactive requires a TTY")

    def undo_last_interactive_action() -> bool:
        if not undo_stack:
            print(json.dumps({"undo": "noop", "reason": "no_previous_action"}, ensure_ascii=False))
            return False
        entry = undo_stack.pop()
        kind = str(entry.get("kind") or "")
        with conn:
            core.save_project(
                conn,
                entry["source_before"],
                enriched=bool(entry["source_before"].get("last_enriched_at")),
                commit=False,
            )
            core.save_project(
                conn,
                entry["target_before"],
                enriched=bool(entry["target_before"].get("last_enriched_at")),
                commit=False,
            )
            if kind == "merge":
                core.restore_project_merge_aliases(
                    conn,
                    source_project_key=str(entry["source_before"]["project_key"]),
                    source_aliases=list(entry.get("source_aliases_before") or []),
                )
                core.restore_project_merge_references(
                    conn,
                    source_project_key=str(entry["source_before"]["project_key"]),
                    reference_rows=list(entry.get("source_reference_rows_before") or []),
                )
        if kind == "merge" and executed:
            executed.pop()
        elif kind == "block" and blocked_pairs:
            blocked_pairs.pop()
        elif kind in {"ignore_source", "ignore_target"} and ignored_projects:
            ignored_projects.pop()
        print(
            json.dumps(
                {
                    "undo": "ok",
                    "kind": kind,
                    "source_project_key": entry["source_before"].get("project_key", ""),
                    "target_project_key": entry["target_before"].get("project_key", ""),
                },
                ensure_ascii=False,
            )
        )
        return True

    for index, plan in enumerate(plans, start=1):
        source = core.fetch_project(conn, plan["source_project_key"])
        target = core.fetch_project(conn, plan["target_project_key"])
        if source is None or target is None:
            skipped.append(
                core.attach_batch_entry_seed_fields(
                    {
                    "project_key": plan["source_project_key"],
                    "target_project_key": plan["target_project_key"],
                    "reason": "project_missing_during_execution",
                    },
                    source,
                    target,
                )
            )
            continue
        source = core.ensure_project_shape(source)
        target = core.ensure_project_shape(target)
        merged = core.merge_project_records(target, source)
        merged["duplicate_candidates"] = core.compute_duplicate_candidates(conn, merged)
        merged["merge_recommendation"] = core.compute_merge_recommendation(merged)
        merged = core.apply_promotion_state(merged)

        summary = {
            "source_project_key": source["project_key"],
            "target_project_key": target["project_key"],
            "source_name": source.get("name", ""),
            "target_name": target.get("name", ""),
            "source_seed_type": source.get("seed_type", ""),
            "source_seed_value": source.get("seed_value", ""),
            "target_seed_type": target.get("seed_type", ""),
            "target_seed_value": target.get("seed_value", ""),
            "merged_name": merged.get("name", ""),
            "merged_website_url": merged.get("website_url", ""),
            "merged_source_code_urls": merged.get("source_code_urls", []),
            "merged_service_status": merged.get("service_status", ""),
            "reasons": plan["reasons"],
        }

        if interactive:
            prompt_payload = {
                "index": index,
                "total": len(plans),
                "source_project_key": source["project_key"],
                "target_project_key": target["project_key"],
                "source_name": source.get("name", ""),
                "target_name": target.get("name", ""),
                "source_seed_type": source.get("seed_type", ""),
                "source_seed_value": source.get("seed_value", ""),
                "target_seed_type": target.get("seed_type", ""),
                "target_seed_value": target.get("seed_value", ""),
                "reasons": plan["reasons"],
            }
            print(json.dumps(prompt_payload, ensure_ascii=False, indent=2, sort_keys=True))
            details_payload = {
                "projects": core.build_project_comparison_payload(source, target),
            }
            while True:
                response = input("merge this plan? [y]es/[n]o/[d]etails/[b]lock/[i]gnore-source/[t]ignore-target/[u]ndo/[q]uit: ").strip().lower()
                if response in {"", "y", "yes"}:
                    break
                if response in {"n", "no"}:
                    summary["skipped_interactively"] = True
                    break
                if response in {"d", "detail", "details"}:
                    print(json.dumps(details_payload, ensure_ascii=False, indent=2, sort_keys=True))
                    continue
                if response in {"b", "block"}:
                    source_before = copy.deepcopy(source)
                    target_before = copy.deepcopy(target)
                    source["separate_project_keys"] = core.unique_list(list(source.get("separate_project_keys", [])) + [target["project_key"]])
                    target["separate_project_keys"] = core.unique_list(list(target.get("separate_project_keys", [])) + [source["project_key"]])
                    source["duplicate_candidates"] = core.compute_duplicate_candidates(conn, source)
                    source["merge_recommendation"] = core.compute_merge_recommendation(source)
                    target["duplicate_candidates"] = core.compute_duplicate_candidates(conn, target)
                    target["merge_recommendation"] = core.compute_merge_recommendation(target)
                    with conn:
                        core.save_project(conn, source, enriched=bool(source.get("last_enriched_at")), commit=False)
                        core.save_project(conn, target, enriched=bool(target.get("last_enriched_at")), commit=False)
                    undo_stack.append(
                        {
                            "kind": "block",
                            "source_before": source_before,
                            "target_before": target_before,
                        }
                    )
                    blocked_pairs.append(
                        {
                            "source_project_key": source["project_key"],
                            "target_project_key": target["project_key"],
                            "source_seed_type": source.get("seed_type", ""),
                            "source_seed_value": source.get("seed_value", ""),
                            "target_seed_type": target.get("seed_type", ""),
                            "target_seed_value": target.get("seed_value", ""),
                        }
                    )
                    summary["blocked_interactively"] = True
                    break
                if response in {"i", "ignore-source"}:
                    source_before = copy.deepcopy(source)
                    target_before = copy.deepcopy(target)
                    source["tracking_state"] = "ignore"
                    if not str(source.get("tracking_note") or "").strip():
                        source["tracking_note"] = f"ignored during interactive merge-batch against {target['project_key']}"
                    core.save_project(conn, source, enriched=bool(source.get("last_enriched_at")))
                    conn.commit()
                    undo_stack.append(
                        {
                            "kind": "ignore_source",
                            "source_before": source_before,
                            "target_before": target_before,
                        }
                    )
                    ignored_projects.append(
                        {
                            "project_key": source["project_key"],
                            "seed_type": source.get("seed_type", ""),
                            "seed_value": source.get("seed_value", ""),
                            "tracking_state": "ignore",
                        }
                    )
                    summary["ignored_interactively"] = "source"
                    break
                if response in {"t", "ignore-target"}:
                    source_before = copy.deepcopy(source)
                    target_before = copy.deepcopy(target)
                    target["tracking_state"] = "ignore"
                    if not str(target.get("tracking_note") or "").strip():
                        target["tracking_note"] = f"ignored during interactive merge-batch against {source['project_key']}"
                    core.save_project(conn, target, enriched=bool(target.get("last_enriched_at")))
                    conn.commit()
                    undo_stack.append(
                        {
                            "kind": "ignore_target",
                            "source_before": source_before,
                            "target_before": target_before,
                        }
                    )
                    ignored_projects.append(
                        {
                            "project_key": target["project_key"],
                            "seed_type": target.get("seed_type", ""),
                            "seed_value": target.get("seed_value", ""),
                            "tracking_state": "ignore",
                        }
                    )
                    summary["ignored_interactively"] = "target"
                    break
                if response in {"u", "undo"}:
                    undo_last_interactive_action()
                    continue
                if response in {"q", "quit"}:
                    summary["quit_interactively"] = True
                    response = "q"
                    break
            if summary.get("quit_interactively"):
                break
            if summary.get("blocked_interactively") or summary.get("skipped_interactively") or summary.get("ignored_interactively"):
                continue

        executed.append(summary)

        if args.execute:
            source_before = copy.deepcopy(source)
            target_before = copy.deepcopy(target)
            source_aliases_before = core.registry_database.project_key_aliases(conn, source["project_key"])
            source_reference_rows_before = core.project_merge_reference_rows(conn, source["project_key"])
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
            undo_stack.append(
                {
                    "kind": "merge",
                    "source_before": source_before,
                    "target_before": target_before,
                    "source_aliases_before": source_aliases_before,
                    "source_reference_rows_before": source_reference_rows_before,
                }
            )

    print(
        json.dumps(
            ({
                "mode": "execute" if args.execute else "dry_run",
                "merge_mode": effective_mode,
                "planned_merges": len(plans),
                "executed_merges": len(executed),
                "executed": executed,
                "blocked_pairs": blocked_pairs,
                "ignored_projects": ignored_projects,
            } | (
                {
                    "skipped_count": len(skipped),
                    "skipped": skipped[:100],
                }
                if args.verbose else {}
            )),
            ensure_ascii=False,
            indent=2 if not args.execute else None,
            sort_keys=not args.execute,
        )
    )
    return 0
