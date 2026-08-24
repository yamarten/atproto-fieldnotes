from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import registry as core
from .. import collector_relation_shadow
from .. import database
from .. import domain_policy
from .. import identifier_storage_shadow
from .. import identifier_storage_materialization
from .. import merge_policy_shadow
from .. import trust_policy_shadow
from ..application import maintenance as services


def _with_db(call):
    conn = core.open_db()
    try:
        return call(conn)
    finally:
        conn.close()


def cmd_init_db(args: argparse.Namespace) -> int:
    path = Path(args.db_path).expanduser() if args.db_path else core.DB_PATH
    conn = core.initialize_db(path)
    try:
        payload = {
            "db_path": str(path.resolve()),
            "schema_version": database.database_schema_version(conn),
        }
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_repair_db(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.repair_database(conn, execute=bool(args.execute)))
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0


def cmd_audit_url_fields(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.audit_urls(conn, example_limit=int(args.example_limit)))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_audit_trust_graph(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.audit_trust_graph(conn, example_limit=int(args.example_limit)))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_compare_trust_policy(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser()
    report = trust_policy_shadow.compare_database_path(
        Path(args.db_path),
        case_catalog_path=core.ROOT / "tests" / "fixtures" / "project_registry" / "trust-policy-cases-v1.json",
        example_limit=int(args.example_limit),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        "output": str(output),
        "content_hash": report["content_hash"],
        "input": {
            key: report["input"][key]
            for key in ("project_count", "claim_count", "deduplicated_edge_count", "allowed_edge_count", "unmapped_relation_count")
        },
        "case_catalog": {
            key: report["case_catalog"][key]
            for key in ("case_count", "passed_count", "failed_count")
        },
        "review_gate": report["review_gate"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["review_gate"]["case_catalog_passed"] and not report["review_gate"]["strategy_divergence_count"] else 1


def cmd_compare_collector_relation_policy(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser()
    db_path = Path(args.db_path).expanduser()
    if output.resolve() == db_path.resolve():
        raise ValueError("--output must not overwrite --db-path")
    report = collector_relation_shadow.compare_database_path(
        db_path,
        case_catalog_path=(
            core.ROOT
            / "tests"
            / "fixtures"
            / "project_registry"
            / "collector-relation-policy-cases-v1.json"
        ),
        example_limit=int(args.example_limit),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        "output": str(output),
        "content_hash": report["content_hash"],
        "input": report["input"],
        "case_catalog": {
            key: report["case_catalog"][key]
            for key in ("case_count", "passed_count", "failed_count")
        },
        "deterministic_policy": report["deterministic_policy"],
        "strict_projection": report["strict_projection"],
        "candidate_admission": report["candidate_admission"],
        "github_homepage_profiles": report["github_homepage_profiles"],
        "github_homepage_three_axis": report["github_homepage_three_axis"],
        "github_homepage_storage_projection": report["github_homepage_storage_projection"],
        "github_homepage_website_projection": report["github_homepage_website_projection"],
        "github_search_homepage_attachment": {
            key: report["github_search_homepage_attachment"][key]
            for key in (
                "reconstructed_homepage_claim_count",
                "previous_policy_auto_attach_count",
                "shadow_disposition_counts",
                "shadow_reason_counts",
                "best_match_standing_counts",
                "current_placement_counts",
                "family_disposition_counts",
                "historical_match_kind_reconstructible",
                "comparison_is_read_only",
                "production_policy_connected",
            )
        },
        "relation_assessor_inventory": {
            key: report["relation_assessor_inventory"][key]
            for key in (
                "current_proposal_count",
                "saved_assessment_pair_count",
                "saved_assessment_without_current_proposal_count",
                "route_counts",
                "route_reason_counts",
                "link_presentation_counts",
                "current_assessment_counts",
                "current_edge_counts",
                "same_project_without_bounded_target_material_count",
                "same_project_on_unconfirmed_subject_count",
                "same_project_without_component_role_count",
                "llm_only_forward_pair_with_trust_adoption_count",
                "comparison_is_read_only",
                "production_behavior_changed",
            )
        },
        "review_gate": report["review_gate"],
    }
    promotion = report["relation_assessor_inventory"]["candidate_relation_promotion"]
    summary["relation_assessor_inventory"]["candidate_relation_promotion"] = {
        key: promotion[key]
        for key in (
            "disposition_counts",
            "reason_counts",
            "purpose_counts",
            "presentation_channel_counts",
            "presentation_count_counts",
            "disposition_by_reciprocal_presence",
            "reciprocal_relation_is_promotion_input",
        )
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["review_gate"]["ready_for_user_review"] else 1


def cmd_compare_project_repository_context(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser()
    db_path = Path(args.db_path).expanduser()
    if output.resolve() == db_path.resolve():
        raise ValueError("--output must not overwrite --db-path")
    source_report = collector_relation_shadow.compare_database_path(
        db_path,
        case_catalog_path=(
            core.ROOT / "tests" / "fixtures" / "project_registry"
            / "collector-relation-policy-cases-v1.json"
        ),
        example_limit=100000,
    )
    promotion = source_report["relation_assessor_inventory"]["candidate_relation_promotion"]
    proposals: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for rows in promotion.get("examples", {}).values():
        for row in rows:
            key = (
                str(row.get("project_key") or ""),
                str(row.get("service_url") or ""),
                str(row.get("repo_url") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            proposals.append(dict(row))
    comparison = collector_relation_shadow.compare_current_project_repository_pages(
        proposals,
        fetch_page=lambda url: core.probe_service_url(url, timeout=float(args.timeout)),
        page_limit=int(args.page_limit),
    )
    report = {
        "schema_version": 1,
        "policy": "project-repository-context-shadow-v1",
        "source_database": str(db_path.resolve()),
        "source_report_hash": str(source_report.get("content_hash") or ""),
        "database_read_only": True,
        "collector_or_database_writes": False,
        **comparison,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "selected_page_count": comparison["selected_page_count"],
        "proposal_match_counts": comparison["proposal_match_counts"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_compare_merge_policy(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser()
    db_path = Path(args.db_path).expanduser()
    if output.resolve() == db_path.resolve():
        raise ValueError("--output must not overwrite --db-path")
    report = merge_policy_shadow.compare_database_path(
        db_path,
        case_catalog_path=core.ROOT / "tests" / "fixtures" / "project_registry" / "merge-policy-cases-v1.json",
        domain_overrides_path=Path(args.domain_overrides).expanduser() if args.domain_overrides else None,
        example_limit=int(args.example_limit),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    comparison = report["current_and_shadow"]
    summary = {
        "output": str(output),
        "content_hash": report["content_hash"],
        "input": report["input"],
        "case_catalog": {
            key: report["case_catalog"][key]
            for key in ("case_count", "passed_count", "failed_count")
        },
        "comparison": {
            key: comparison[key]
            for key in (
                "pair_count",
                "current_candidate_count",
                "current_auto_ready_count",
                "current_strict_pair_count",
                "current_moderate_pair_count",
                "shadow_verdict_counts",
            )
        },
        "historical_merges": {
            "alias_count": report["historical_merges"]["alias_count"],
            "verdict_counts": report["historical_merges"]["verdict_counts"],
        },
        "review_gate": report["review_gate"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["review_gate"]["case_catalog_passed"] else 1


def cmd_compare_identifier_storage(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser()
    db_path = Path(args.db_path).expanduser()
    if output.resolve() == db_path.resolve():
        raise ValueError("--output must not overwrite --db-path")
    report = identifier_storage_shadow.compare_database_path(
        db_path,
        example_limit=int(args.example_limit),
    )
    materialized_value = getattr(args, "materialized_db", None)
    materialized_db = Path(materialized_value).expanduser() if materialized_value else None
    if materialized_db is not None:
        if materialized_db.resolve() in {db_path.resolve(), output.resolve()}:
            raise ValueError("--materialized-db must differ from --db-path and --output")
        manifest = (
            Path(getattr(args, "review_manifest", None)).expanduser()
            if getattr(args, "review_manifest", None)
            else core.ROOT / "tests" / "fixtures" / "project_registry" / "identifier-adoption-review-v1.json"
        )
        report["materialization"] = identifier_storage_materialization.materialize_reviewed_copy(
            db_path,
            materialized_db,
            manifest,
        )
        report["content_hash"] = identifier_storage_shadow._content_hash(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        "output": str(output),
        "content_hash": report["content_hash"],
        "input": report["input"],
        "decision_role_mapping": {
            "decision_count": report["decision_role_mapping"]["decision_count"],
            "status_counts": report["decision_role_mapping"]["status_counts"],
        },
        "adoption_projection": {
            key: report["adoption_projection"][key]
            for key in (
                "confirmed_component_claim_count",
                "covered_claim_count",
                "coverage_percent",
                "relation_recovered_claim_count",
                "legacy_context_recovered_claim_count",
                "guarded_confirmed_claim_count",
                "uncovered_claim_count",
            )
        },
        "prototype_cases": {
            key: report["prototype_cases"][key]
            for key in ("case_count", "passed_count", "failed_count")
        },
        "review_gate": report["review_gate"],
    }
    if "materialization" in report:
        summary["materialization"] = {
            "target": report["materialization"]["target"],
            "after": report["materialization"]["after"],
            "review": report["materialization"]["review"],
            "validation": report["materialization"]["validation"],
            "review_gate": report["materialization"]["review_gate"],
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    materialization_ready = bool(report.get("materialization", {}).get("review_gate", {}).get("ready", True))
    return 0 if report["review_gate"]["ready_for_user_review"] and materialization_ready else 1


def cmd_repair_trust_graph(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.repair_trust_graph(
        conn,
        execute=bool(args.execute),
        downgrade_unsupported=bool(args.downgrade_unsupported),
        example_limit=int(args.example_limit),
    ))
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.execute else None, sort_keys=not args.execute))
    return 0


def cmd_cleanup_storage_payloads(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.cleanup_storage_payloads(
        conn,
        execute=bool(args.execute),
        example_limit=int(args.example_limit),
        vacuum=bool(args.vacuum),
    ))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_cleanup_workspace_temp(args: argparse.Namespace) -> int:
    workspace_root = Path(__file__).resolve().parents[3]
    payload = services.cleanup_workspace_temp(
        workspace_root,
        older_than_hours=int(args.older_than_hours),
        execute=bool(args.execute),
        example_limit=int(args.example_limit),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_compress_investigation_history(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.compress_investigation_history(
        conn,
        execute=bool(args.execute),
        example_limit=int(args.example_limit),
        vacuum=bool(args.vacuum),
    ))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_prune_investigation_history(args: argparse.Namespace) -> int:
    payload = _with_db(lambda conn: services.prune_investigation_history(
        conn,
        retention_days=int(args.retention_days),
        keep_latest_per_project=int(args.keep_latest_per_project),
        execute=bool(args.execute),
        vacuum=bool(args.vacuum),
    ))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_audit_source_config(args: argparse.Namespace) -> int:
    interactive = bool(args.interactive)
    if interactive and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise ValueError("default interactive mode requires a TTY; use --no-interactive for JSON dry-run output")
    requested_suffix = domain_policy.normalize_multi_tenant_suffix(args.domain) if args.domain else ""
    added_suffix = False
    should_configure = bool(requested_suffix and args.execute)
    if requested_suffix and requested_suffix not in domain_policy.configured_multi_tenant_suffixes():
        if interactive and not should_configure:
            should_configure = core.prompt_for_confirmation(f"Add {requested_suffix} to config/domain-overrides.json? [y/N]")
    suffixes = [requested_suffix] if requested_suffix else sorted(domain_policy.configured_multi_tenant_suffixes())
    conn = core.open_db()
    try:
        suffix_update = services.configure_multi_tenant_suffix(conn, requested_suffix) if should_configure else None
        added_suffix = bool(suffix_update and suffix_update["added"])
        same_site_refresh = suffix_update["same_site_refresh"] if suffix_update else None
        suggestions = core.build_source_config_split_suggestions(conn, suffixes=suffixes)
        payload = {
            "mode": "interactive" if interactive else "dry_run",
            "requested_suffix": requested_suffix,
            "suffix_added": added_suffix,
            "audited_suffixes": suffixes,
            "same_site_refresh": same_site_refresh,
            "split_suggestion_count": len(suggestions),
            "split_suggestions": suggestions,
        }
        if not interactive:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        review_result = core.review_source_config_split_suggestions(conn, suffixes, initial_suggestions=suggestions)
    finally:
        conn.close()
    print(json.dumps({**{key: value for key, value in payload.items() if key != "split_suggestions"}, **review_result}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
