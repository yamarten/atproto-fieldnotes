from __future__ import annotations

from project_registry_core import project_review

import argparse
import json

from .. import registry as core
from ..persistence import runtime_view
from ..application import projects as services
from project_registry_core import project_record


def cmd_add_seed(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.add_seed(
            conn,
            seed=args.seed,
            seed_type=args.type,
            project_key=args.project_key,
        )
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False))
    return 0

def _tracking_states(args: argparse.Namespace) -> set[str] | None:
    values = getattr(args, "tracking_states", None)
    return {str(item) for item in values} if values else None


def cmd_show(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        projects = services.select_projects(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.type,
            all_projects=bool(args.all),
            tracking_states=_tracking_states(args),
        )
        projects = services.prepare_project_views(conn, projects)
        if args.format == "json":
            for project in projects:
                print(json.dumps(runtime_view.sanitize_runtime_snapshot(project), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        for project in projects:
            _print_project(project)
        return 0
    finally:
        conn.close()


def _print_project(project: dict[str, object]) -> None:
    print(f"[{project['project_key']}] {project.get('name') or '(unnamed)'}")
    print(f"  tracking_state={project.get('tracking_state') or 'active'} category={project.get('category') or '-'} app_category={project.get('app_category') or '-'}")
    print(f"  tracking_note={project.get('tracking_note') or '-'}")
    print(f"  input_nsids={', '.join(project.get('input_nsids', [])) or '-'}")
    print(f"  resolved_nsids={', '.join(project.get('resolved_nsids', [])) or '-'}")
    print(f"  namespaces={', '.join(project.get('recorded_lexicon_namespaces', [])) or '-'}")
    print(f"  website={project.get('website_url') or '-'}")
    print(f"  website_candidates={', '.join(project.get('service_url_candidates', [])) or '-'}")
    print(f"  service_status={project.get('service_status') or '-'}")
    print(f"  atproto_evidence={project.get('atproto_contribution_level') or 'unknown'} (observation group, not usage depth)")
    print(f"  introduced={bool(project.get('introduced_url'))} introduced_url={project.get('introduced_url') or '-'}")
    print(f"  github={', '.join(project_record.provider_repo_url_values(project, 'github')) or '-'}")
    print(f"  source_code={', '.join(project.get('project_repository_urls', [])) or '-'}")
    print(f"  candidate_source_code={', '.join(project.get('candidate_project_repository_urls', [])) or '-'}")
    print(f"  features={'; '.join(project.get('features', [])) or '-'}")
    print(f"  tagline={project.get('tagline') or '-'} provisional={project.get('tagline_is_provisional', False)}")
    print(f"  summary={project.get('summary') or '-'}")
    print(f"  last_run_errors={'; '.join(project.get('last_run_errors', [])) or '-'}")
    if project.get("blocked_identifiers"):
        blocked = [f"{item.get('type')}:{item.get('value')}" for item in project.get("blocked_identifiers", []) if item.get("type") and item.get("value")]
        print(f"  blocked_identifiers={', '.join(blocked) or '-'}")
    if project.get("deferred_identifiers"):
        deferred = [f"{item.get('type')}:{item.get('value')} until {item.get('expires_at')}" for item in project.get("deferred_identifiers", []) if item.get("type") and item.get("value")]
        print(f"  deferred_identifiers={', '.join(deferred) or '-'}")
    if project.get("promotion_deferrals"):
        active = project_review.active_promotion_deferral(project)
        detail = f"{active.get('reason')} until {active.get('expires_at')}" if active else f"inactive; last reason={project_review.latest_promotion_deferral_reason(project) or '-'}"
        print(f"  promotion_deferral={detail}")
    if project.get("separate_project_keys"):
        print(f"  separate_project_keys={', '.join(project.get('separate_project_keys', []))}")
    duplicate_candidates = project.get("duplicate_candidates", [])
    if duplicate_candidates:
        print(f"  duplicate_candidates={', '.join(item['project_key'] for item in duplicate_candidates)}")
    recommendation = project.get("merge_recommendation", {})
    if recommendation:
        print(
            "  merge_recommendation="
            f"{recommendation.get('target_project_key') or '-'} "
            f"score={recommendation.get('score', 0)} "
            f"confidence={recommendation.get('merge_confidence') or '-'} "
            f"auto_ready={recommendation.get('auto_merge_ready', False)}"
        )


def cmd_set_tracking_state(args: argparse.Namespace) -> int:
    conn = core.open_db()
    try:
        payload = services.set_tracking_state(
            conn,
            project_key=args.project_key,
            seed=args.seed,
            seed_type=args.type,
            tracking_state=args.tracking_state,
            note=args.note,
        )
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False))
    return 0
