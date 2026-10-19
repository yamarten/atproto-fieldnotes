#!/usr/bin/env python3

from __future__ import annotations

from project_registry_core.application import review as application_review
from project_registry_core import project_review

import argparse
import json
import sqlite3
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from project_registry_core.persistence import database
from project_registry_core import project_record


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "survey.db"
SERVICE_STATUSES = {"live", "beta", "invite_only", "unverified"}
SERVICE_CATEGORIES = {"project", "infra_tool"}
TECHNICAL_CATEGORIES_EXCLUDED = {"reject", "name_only"}


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _score_summary(values: list[int]) -> dict[str, int | float | None]:
    return {
        "min": min(values) if values else None,
        "median": statistics.median(values) if values else None,
        "max": max(values) if values else None,
    }


def _counterfactual_project(project: dict[str, Any]) -> dict[str, Any]:
    counterfactual = dict(project)
    counterfactual["introduced_url"] = ""
    return project_record.ensure_project_shape(counterfactual)


def _service_route(project: dict[str, Any], *, include_personal_tools: bool) -> dict[str, Any]:
    category = str(project.get("category") or "")
    allowed_categories = set(SERVICE_CATEGORIES)
    if include_personal_tools:
        allowed_categories.add("personal_tool")
    applicable = category in SERVICE_CATEGORIES | {"personal_tool"}
    blockers: list[str] = []
    if applicable and category not in allowed_categories:
        blockers.append("personal_tool_disabled")
    if applicable and category in allowed_categories:
        service_status = project_record.normalize_service_status(project.get("service_status"))
        if service_status not in SERVICE_STATUSES:
            blockers.append("service_status_not_candidate")
        if not project.get("service_urls"):
            blockers.append("confirmed_service_url_missing")
    return {
        "applicable": applicable,
        "enabled": applicable and category in allowed_categories,
        "blockers": blockers,
        "website_url": str(project.get("website_url") or ""),
        "confirmed_service_urls": [str(item) for item in project.get("service_urls", [])],
        "candidate_service_urls": project_review.project_service_hint_urls(project),
    }


def _lexicon_route(project: dict[str, Any]) -> dict[str, Any]:
    applicable = str(project.get("category") or "") == "lexicon"
    blockers: list[str] = []
    if applicable:
        if str(project.get("tracking_state") or "active") in {"out_of_scope", "ignore"}:
            blockers.append("tracking_state_ineligible")
        nsid_hints = project_record.unique_list(
            [str(item or "") for item in project.get("resolved_nsids", []) if str(item or "").strip()]
            + [str(item or "") for item in project.get("input_nsids", []) if str(item or "").strip()]
            + [str(item or "") for item in project.get("recorded_lexicon_namespaces", []) if str(item or "").strip()]
        )
        if not nsid_hints:
            blockers.append("lexicon_nsid_missing")
        has_reference = bool(
            project.get("website_url")
            or project.get("project_repository_urls")
            or project_review.project_repo_hint_urls(project)
            or project_record.resource_url_values(project)
        )
        if not has_reference:
            blockers.append("lexicon_reference_missing")
    return {
        "applicable": applicable,
        "blockers": blockers,
    }


def _technical_route(project: dict[str, Any]) -> dict[str, Any]:
    category = str(project.get("category") or "")
    applicable = category not in TECHNICAL_CATEGORIES_EXCLUDED
    blockers: list[str] = []
    if not applicable:
        blockers.append("technical_category_excluded")
    else:
        level = str(project.get("technical_interest_level") or "none")
        if level not in {"high", "exceptional"}:
            blockers.append("technical_interest_below_candidate_threshold")
        if not project.get("technical_interest_reasons"):
            blockers.append("technical_interest_reasons_missing")
        if not str(project.get("technical_interest_summary") or "").strip():
            blockers.append("technical_interest_summary_missing")
    return {
        "applicable": applicable,
        "blockers": blockers,
    }


def _primary_no_signal_reason(
    project: dict[str, Any],
    routes: dict[str, dict[str, Any]],
) -> str:
    category = str(project.get("category") or "")
    service_blockers = list(routes["service"]["blockers"])
    if category == "personal_tool" and "personal_tool_disabled" in service_blockers:
        return "personal_tool_disabled"
    if category in SERVICE_CATEGORIES | {"personal_tool"} and routes["service"]["enabled"]:
        if "service_status_not_candidate" in service_blockers:
            return "service_status_not_candidate"
        if "confirmed_service_url_missing" in service_blockers:
            return "confirmed_service_url_missing"
        return "service_signal_absence_unexplained"
    if category == "lexicon":
        lexicon_blockers = list(routes["lexicon"]["blockers"])
        return lexicon_blockers[0] if lexicon_blockers else "lexicon_signal_absence_unexplained"
    if category in TECHNICAL_CATEGORIES_EXCLUDED:
        return "category_excluded_from_promotion_signals"
    technical_blockers = list(routes["technical"]["blockers"])
    return technical_blockers[0] if technical_blockers else "technical_signal_absence_unexplained"


def analyze_projects(
    projects: list[dict[str, Any]],
    *,
    include_personal_tools: bool = False,
    details: str = "no-signal",
    detail_limit: int | None = None,
) -> dict[str, Any]:
    if details not in {"none", "no-signal", "all"}:
        raise ValueError(f"unsupported detail mode: {details}")

    introduced_projects = [project for project in projects if str(project.get("introduced_url") or "").strip()]
    introduction_groups: dict[str, list[dict[str, Any]]] = {}
    for project in introduced_projects:
        introduction_groups.setdefault(str(project.get("introduced_url") or ""), []).append(project)
    current_queue = application_review.build_showcase_queue_from_projects(
        projects,
        limit=None,
        include_personal_tools=include_personal_tools,
    )
    current_queue_scores = [int(item.get("sort_score", 0) or 0) for item in current_queue]
    signal_counts: Counter[str] = Counter()
    introduced_categories: Counter[str] = Counter()
    signaled_categories: Counter[str] = Counter()
    visibility_groups: list[str] = []
    no_signal_primary_reasons: list[str] = []
    no_signal_categories: list[str] = []
    no_signal_service_statuses: list[str] = []
    no_signal_technical_levels: list[str] = []
    no_signal_route_blockers: list[str] = []
    introduced_signal_scores: list[int] = []
    detail_rows: list[dict[str, Any]] = []
    signal_project_count = 0

    for original in introduced_projects:
        project = _counterfactual_project(original)
        category = str(project.get("category") or "")
        introduced_categories[category] += 1
        visibility = project_review.promotion_candidate_visibility(project)
        visibility_groups.append(str(visibility.get("group") or ""))
        signals = project_review.compute_showcase_signals(
            project,
            include_personal_tools=include_personal_tools,
            include_ineligible=True,
        )
        signal_kinds = sorted(str(item.get("kind") or "") for item in signals)
        signal_counts.update(signal_kinds)
        score_breakdown = project_review.promotion_queue_score_breakdown(project, signals) if signals else None
        if signals:
            signal_project_count += 1
            signaled_categories[category] += 1
            introduced_signal_scores.append(int((score_breakdown or {}).get("total", 0) or 0))

        routes = {
            "service": _service_route(project, include_personal_tools=include_personal_tools),
            "lexicon": _lexicon_route(project),
            "technical": _technical_route(project),
        }
        primary_reason = ""
        if not signals:
            primary_reason = _primary_no_signal_reason(project, routes)
            no_signal_primary_reasons.append(primary_reason)
            no_signal_categories.append(str(project.get("category") or ""))
            no_signal_service_statuses.append(str(project.get("service_status") or ""))
            no_signal_technical_levels.append(str(project.get("technical_interest_level") or "none"))
            for route in routes.values():
                no_signal_route_blockers.extend(str(item) for item in route["blockers"])

        include_detail = details == "all" or (details == "no-signal" and not signals)
        if include_detail:
            introduced_url = str(original.get("introduced_url") or "")
            group = introduction_groups.get(introduced_url, [])
            detail_rows.append(
                {
                    "project_key": str(project.get("project_key") or ""),
                    "name": str(project.get("name") or ""),
                    "introduced_url": introduced_url,
                    "introduction_group_project_count": len(group),
                    "introduction_group_project_keys": sorted(
                        str(item.get("project_key") or "") for item in group
                    ),
                    "category": str(project.get("category") or ""),
                    "tracking_state": str(project.get("tracking_state") or ""),
                    "service_status": str(project.get("service_status") or ""),
                    "service_status_confidence": str(project.get("service_status_confidence") or ""),
                    "service_status_evidence": [
                        str(item) for item in project.get("service_status_evidence", [])
                    ],
                    "has_service_url": bool(
                        str(project.get("website_url") or "").strip() or project.get("service_urls")
                    ),
                    "website_url": str(project.get("website_url") or ""),
                    "confirmed_service_urls": [str(item) for item in project.get("service_urls", [])],
                    "candidate_service_urls": project_review.project_service_hint_urls(project),
                    "stored_service_url_candidates": [
                        str(item) for item in project.get("service_url_candidates", [])
                    ][:5],
                    "supported_service_urls": [
                        str(item) for item in project.get("supported_service_urls", [])
                    ][:5],
                    "failed_service_urls": [
                        str(item) for item in project.get("failed_service_urls", [])
                    ][:5],
                    "manual_service_seed_urls": project_record.manual_seed_values(
                        project,
                        "service_url",
                    )[:5],
                    "repository_urls": project_review.project_repo_hint_urls(project)[:3],
                    "nsids": project_record.unique_list(
                        [str(item or "") for item in project.get("resolved_nsids", []) if str(item or "").strip()]
                        + [str(item or "") for item in project.get("input_nsids", []) if str(item or "").strip()]
                    )[:5],
                    "seed_type": str(project.get("seed_type") or ""),
                    "seed_value": str(project.get("seed_value") or ""),
                    "tagline": str(project.get("tagline") or ""),
                    "summary": str(project.get("summary") or ""),
                    "notes": str(project.get("notes") or ""),
                    "classification_reason": str(project.get("classification_reason") or ""),
                    "description_missing": project_review.missing_description_fields(project),
                    "atproto_contribution_level": str(project.get("atproto_contribution_level") or ""),
                    "atproto_contribution_score": int(project.get("atproto_contribution_score", 0) or 0),
                    "technical_interest_level": str(project.get("technical_interest_level") or "none"),
                    "technical_interest_reasons": [
                        str(item) for item in project.get("technical_interest_reasons", [])
                    ],
                    "technical_interest_summary": str(project.get("technical_interest_summary") or ""),
                    "last_enriched_at": str(project.get("last_enriched_at") or ""),
                    "counterfactual_visibility": visibility,
                    "signal_kinds": signal_kinds,
                    "score_breakdown": score_breakdown,
                    "primary_no_signal_reason": primary_reason,
                    "routes": routes,
                }
            )

    detail_rows.sort(
        key=lambda item: (
            str(item.get("primary_no_signal_reason") or ""),
            str(item.get("category") or ""),
            str(item.get("project_key") or ""),
        )
    )
    total_detail_count = len(detail_rows)
    if detail_limit is not None:
        detail_rows = detail_rows[: max(0, int(detail_limit))]

    category_signal_coverage = {
        category: {
            "introduced": total,
            "with_signal": signaled_categories.get(category, 0),
            "without_signal": total - signaled_categories.get(category, 0),
            "coverage_ratio": round(signaled_categories.get(category, 0) / total, 4) if total else 0.0,
        }
        for category, total in sorted(introduced_categories.items())
    }

    return {
        "analysis_schema_version": 1,
        "policy": {
            "introduced_counterfactual": "introduced_url cleared; all other current project fields retained",
            "include_personal_tools": include_personal_tools,
            "unintroduced_projects_are_negative_examples": False,
        },
        "summary": {
            "project_count": len(projects),
            "introduced_count": len(introduced_projects),
            "introduction_url_count": len(introduction_groups),
            "introduction_group_size_counts": _counts(
                str(len(group)) for group in introduction_groups.values()
            ),
            "category_signal_coverage": category_signal_coverage,
            "introduced_with_signal_count": signal_project_count,
            "introduced_without_signal_count": len(introduced_projects) - signal_project_count,
            "counterfactual_visibility_counts": _counts(visibility_groups),
            "signal_counts": dict(sorted(signal_counts.items())),
            "introduced_signal_score": _score_summary(introduced_signal_scores),
            "current_queue_count": len(current_queue),
            "current_queue_score": _score_summary(current_queue_scores),
            "no_signal_primary_reason_counts": _counts(no_signal_primary_reasons),
            "no_signal_route_blocker_counts": _counts(no_signal_route_blockers),
            "no_signal_category_counts": _counts(no_signal_categories),
            "no_signal_service_status_counts": _counts(no_signal_service_statuses),
            "no_signal_technical_interest_counts": _counts(no_signal_technical_levels),
        },
        "detail_mode": details,
        "detail_count": total_detail_count,
        "details_truncated": len(detail_rows) < total_detail_count,
        "projects": detail_rows,
    }


def analyze_database(
    db_path: Path,
    *,
    include_personal_tools: bool = False,
    details: str = "no-signal",
    detail_limit: int | None = None,
) -> dict[str, Any]:
    target = Path(db_path).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"database does not exist: {target}")
    conn = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        database.configure_connection(conn)
        schema_version = database.database_schema_version(conn)
        if schema_version != database.SCHEMA_VERSION:
            raise database.SchemaMismatchError(
                f"expected schema version {database.SCHEMA_VERSION}, found {schema_version}"
            )
        projects = project_record.list_projects(conn)
    finally:
        conn.close()
    return analyze_projects(
        projects,
        include_personal_tools=include_personal_tools,
        details=details,
        detail_limit=detail_limit,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze introduced projects against the current promotion policy without writing SQLite."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Schema v2 SQLite database path.")
    parser.add_argument(
        "--include-personal-tools",
        action="store_true",
        help="Match promotion queue mode that includes personal tools.",
    )
    parser.add_argument(
        "--details",
        choices=("none", "no-signal", "all"),
        default="no-signal",
        help="Project detail rows to include in JSON.",
    )
    parser.add_argument(
        "--detail-limit",
        type=int,
        help="Limit detail rows after deterministic sorting; omit for all rows.",
    )
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = analyze_database(
        args.db,
        include_personal_tools=bool(args.include_personal_tools),
        details=str(args.details),
        detail_limit=args.detail_limit,
    )
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
