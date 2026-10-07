"""Review signals, promotion policy, and tracking recommendations without database I/O."""

from __future__ import annotations

from project_registry_core import atproto_usage
from project_registry_core import identifiers as identifier_core
from project_registry_core import promotion_attention
from project_registry_core import repo_urls as repo_url_core
from project_registry_core import service as service_core
import datetime as dt
from typing import Any

from project_registry_core import project_record


DEFAULT_PROMOTION_DEFER_DAYS = 30


def active_promotion_deferral(project: dict[str, Any]) -> dict[str, Any] | None:
    return promotion_attention.active_deferral(project)


def latest_promotion_deferral_reason(project: dict[str, Any]) -> str:
    # Converted reviews had no reason input. Do not let them hide an existing
    # defer reason from search or the next hold's editable initial value.
    deferrals = [item for item in project_record.normalize_promotion_deferrals(list(project.get("promotion_deferrals", [])))
                 if not item.get("legacy_review")]
    latest = max(reversed(deferrals), key=lambda item: project_record.parse_iso(item.get("added_at"))
                 or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) if deferrals else {}
    return str(latest.get("reason") or "")


def defer_promotion_on_project(project: dict[str, Any], *, reason: str = "", days: int | None = None) -> dict[str, str]:
    effective_reason = str(reason or "").strip()
    effective_days = max(1, min(365, int(days) if days is not None else DEFAULT_PROMOTION_DEFER_DAYS))
    expires_at = (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=effective_days)
    ).replace(microsecond=0).isoformat()
    entry = {
        "reason": effective_reason,
        "added_at": project_record.iso_now(),
        "expires_at": expires_at,
        "fingerprint": promotion_attention.fingerprint(project),
    }
    clear_active_promotion_deferrals(project)
    project["promotion_deferrals"] = project_record.normalize_promotion_deferrals(
        list(project.get("promotion_deferrals", [])) + [entry]
    )
    promotion_attention.record(project, "defer")
    return entry


def clear_active_promotion_deferrals(project: dict[str, Any]) -> None:
    cleared_at = project_record.iso_now()
    deferrals = project_record.normalize_promotion_deferrals(list(project.get("promotion_deferrals", [])))
    now = dt.datetime.now(dt.timezone.utc)
    for item in deferrals:
        if not item.get("cleared_at") and (project_record.parse_iso(item.get("expires_at")) or now) > now:
            item["cleared_at"] = cleared_at
    project["promotion_deferrals"] = deferrals


def active_promotion_priority_adjustment(project: dict[str, Any]) -> dict[str, str] | None:
    active = [
        item
        for item in project_record.normalize_promotion_priority_adjustments(
            list(project.get("promotion_priority_adjustments", []))
        )
        if not item.get("cleared_at")
    ]
    return active[-1] if active else None


def set_promotion_priority_on_project(
    project: dict[str, Any],
    *,
    lowered: bool,
    reason: str = "",
) -> dict[str, str] | None:
    adjustments = project_record.normalize_promotion_priority_adjustments(
        list(project.get("promotion_priority_adjustments", []))
    )
    cleared_at = project_record.iso_now()
    for item in adjustments:
        if not item.get("cleared_at"):
            item["cleared_at"] = cleared_at
    entry = None
    if lowered:
        normalized_reason = str(reason or "").strip()
        entry = {
            "reason": normalized_reason,
            "added_at": project_record.iso_now(),
        }
        adjustments.append(entry)
    project["promotion_priority_adjustments"] = adjustments
    return entry


def determine_promoted_status(project: dict[str, Any]) -> str:
    category = str(project.get("category") or "")
    if category == "reject":
        return "rejected"
    service_status = str(project.get("service_status") or "unknown")
    return {
        "live": "published",
        "invite_only": "invite_only",
        "beta": "beta",
        "unreleased": "tracked",
        "dead": "inactive",
        "private": "tracked",
        "none": "tracked",
        "unknown": "tracked",
    }.get(service_status, "tracked")


def evaluate_promotion(project: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    has_confirmed_identifier = bool(
        project.get("service_urls")
        or project_record.project_repository_url_values(project)
        or project.get("resolved_nsids")
    )
    if not has_confirmed_identifier:
        blockers.append("no confirmed service/repository/lexicon")
    if str(project.get("service_status") or "unknown") in {"", "unknown"}:
        blockers.append("service_status is still unknown")
    if not str(project.get("summary") or "").strip():
        blockers.append("summary missing")
    if not str(project.get("tagline") or "").strip():
        blockers.append("tagline missing")
    if len(project.get("features", [])) < 3:
        blockers.append("fewer than 3 features")
    if not str(project.get("category") or "").strip():
        blockers.append("category missing")
    if not project.get("llm_source"):
        blockers.append("LLM review missing")
    return blockers


def apply_promotion_state(project: dict[str, Any]) -> dict[str, Any]:
    blockers = evaluate_promotion(project)
    project["promotion_blockers"] = blockers
    project["promotion_ready"] = not blockers
    if blockers:
        project["status"] = "draft"
        if not project.get("watch_reason"):
            project["watch_reason"] = "; ".join(blockers)
    else:
        project["status"] = determine_promoted_status(project)
    return project


def record_promotion_transition(project: dict[str, Any], previous_status: str) -> dict[str, Any]:
    current_status = str(project.get("status") or "draft")
    project["promoted_now"] = previous_status == "draft" and current_status != "draft"
    if project["promoted_now"]:
        event = {
            "at": project_record.iso_now(),
            "from_status": previous_status,
            "to_status": current_status,
            "service_status": str(project.get("service_status") or ""),
            "website_url": str(project.get("website_url") or ""),
            "name": str(project.get("name") or ""),
        }
        project["promotion_events"] = [event] + [dict(item) for item in project.get("promotion_events", []) if isinstance(item, dict)]
        project["last_promoted_at"] = event["at"]
    else:
        project.setdefault("promotion_events", [])
        project.setdefault("last_promoted_at", str(project.get("last_promoted_at") or ""))
    return project


def project_confirmed_identifier_count(project: dict[str, Any]) -> int:
    count = 0
    if project.get("service_urls"):
        count += 1
    if project.get("project_repository_urls"):
        count += 1
    if project.get("resolved_nsids"):
        count += 1
    return count


def project_repo_hint_urls(project: dict[str, Any]) -> list[str]:
    hints = repo_url_core.normalize_repo_collection(
        project_record.unique_list(
            [str(item or "") for item in project_record.project_repository_url_values(project)]
            + [str(item or "") for item in project.get("candidate_project_repository_urls", [])]
        )
    )
    manual_repo_hints: list[str] = []
    for item in project.get("manual_seeds", []):
        if not isinstance(item, dict) or str(item.get("type") or "") != "repo_url":
            continue
        raw_value = str(item.get("value") or "").strip()
        if not raw_value:
            continue
        normalized_value = repo_url_core.canonical_repo_url(raw_value) or identifier_core.normalize_url(raw_value)
        if normalized_value:
            manual_repo_hints.append(normalized_value)
    return project_record.unique_list(hints + manual_repo_hints)


def project_service_hint_urls(project: dict[str, Any]) -> list[str]:
    confirmed = {
        identifier_core.normalize_url(str(item or ""))
        for item in list(project.get("service_urls", []))
        if identifier_core.normalize_url(str(item or ""))
    }
    failed = {
        identifier_core.normalize_url(str(item or ""))
        for item in project.get("failed_service_urls", [])
        if identifier_core.normalize_url(str(item or ""))
    }
    hints = project_record.unique_list(
        [str(item or "") for item in project_record.service_url_candidate_values(project)]
    )
    normalized_hints = [
        identifier_core.normalize_url(str(item or ""))
        for item in hints
        if identifier_core.normalize_url(str(item or ""))
        and identifier_core.normalize_url(str(item or "")) not in confirmed
        and identifier_core.normalize_url(str(item or "")) not in failed
    ]
    manual_service_hints: list[str] = []
    for item in project.get("manual_seeds", []):
        if not isinstance(item, dict) or str(item.get("type") or "") != "service_url":
            continue
        normalized_value = identifier_core.normalize_url(str(item.get("value") or "").strip())
        if normalized_value and normalized_value not in confirmed and normalized_value not in failed:
            manual_service_hints.append(normalized_value)
    return project_record.unique_list(normalized_hints + manual_service_hints)


def sanitize_manual_review_reason(project: dict[str, Any], reason: str) -> str:
    text = project_record.normalize_ja_en_mixed_spacing(str(reason or "").strip())
    if not text:
        return text
    repo_hints = project_repo_hint_urls(project)
    if repo_hints and "公開されていないため" in text:
        text = text.replace("ソースコードが公開されていないため", "候補リポジトリはあるが未確認のため")
        text = text.replace("公開されていないため", "未確認のため")
    if repo_hints and "ソースコードが公開されていない" in text:
        text = text.replace("ソースコードが公開されていない", "候補リポジトリはあるが未確認")
    return text


def service_status_manual_review_action_basis(project: dict[str, Any]) -> list[str]:
    service_status = project_record.normalize_service_status(str(project.get("service_status") or "unknown"))
    service_confidence = project_record.normalize_service_status_confidence(str(project.get("service_status_confidence") or "none"))
    service_evidence = {str(item) for item in project.get("service_status_evidence", []) if str(item).strip()}
    blockers = set(str(item) for item in project.get("promotion_blockers", []))
    basis: list[str] = []

    if str(project.get("status") or "draft") == "draft" and (
        service_status == "unknown"
        or "service_status is still unknown" in blockers
    ):
        basis.append("promotion_status_resolution")

    if (
        not str(project.get("introduced_url") or "").strip()
        and str(project.get("category") or "") in {"project", "personal_tool"}
        and service_status in {"live", "beta", "invite_only"}
        and service_confidence in {"none", "low"}
    ):
        basis.append("introduction_validation")

    if "mixed_launch_signals_detected" in service_evidence:
        basis.append("conflicting_service_status_evidence")

    return basis


def compute_review_signals(
    project: dict[str, Any],
    *,
    assume_shaped: bool = False,
    apply_promotion: bool = True,
) -> list[dict[str, Any]]:
    shaped = dict(project) if assume_shaped else project_record.ensure_project_shape(dict(project))
    if apply_promotion:
        shaped = apply_promotion_state(shaped)
    signals: list[dict[str, Any]] = []
    dismissed_kinds = {str(item).strip() for item in shaped.get("dismissed_review_kinds", []) if str(item).strip()}
    proposal = (shaped.get("editorial") or {}).get("proposal")
    if proposal:
        signals.append({"kind": "editorial_refresh", "priority": 7,
                        "reason": str(proposal.get("reason") or "説明の更新案があります"),
                        "details": {"action_basis": ["description_proposal"],
                                    "change_kind": proposal.get("kind")}})
    changed_services = [service_core.observation_summary(item) for item in shaped.get("service_verification_results", [])
                        if item.get("requested_url") in shaped.get("service_urls", [])
                        and (item.get("surface_change") or item.get("content_changed"))]
    if changed_services:
        signals.append({"kind": "service_observation_changed", "priority": 5,
                        "details": {"observations": changed_services,
                                    "action_basis": ["review_description_and_tracking_separately"]}})
    if shaped.get("publication_review"):
        signals.append({"kind": "lexicon_publication_changed", "priority": 5,
                        "details": {"reasons": list(shaped["publication_review"]), "action_basis": ["review_lexicon_publication"]}})
    if shaped.get("tracking_state") == "out_of_scope":
        return [signal for signal in signals if signal["kind"] not in dismissed_kinds]
    blockers = set(str(item) for item in shaped.get("promotion_blockers", []))
    service_status = str(shaped.get("service_status") or "unknown")
    service_confidence = str(shaped.get("service_status_confidence") or "none")
    service_evidence = {str(item) for item in shaped.get("service_status_evidence", []) if str(item).strip()}
    confirmed_identifier_count = project_confirmed_identifier_count(shaped)
    service_candidates = project_record.unique_list(
        ([str(shaped.get("website_url") or "")] if shaped.get("website_url") else [])
        + [str(item or "") for item in shaped.get("service_urls", [])]
        + [str(item or "") for item in project_record.service_url_candidate_values(shaped)]
    )
    repo_candidates = project_repo_hint_urls(shaped)
    standalone_service_candidates = project_service_hint_urls(shaped)
    nsid_count = len(project_record.unique_list(
        [str(item or "") for item in shaped.get("input_nsids", [])]
        + [str(item or "") for item in shaped.get("resolved_nsids", [])]
    ))
    manual_review_kind = str(shaped.get("manual_review_kind") or "other")
    manual_review_action_basis = (
        service_status_manual_review_action_basis(shaped)
        if manual_review_kind == "service_status_manual_check"
        else []
    )
    if (
        shaped.get("manual_review_recommended")
        and manual_review_kind != "other"
        and (manual_review_kind != "service_status_manual_check" or manual_review_action_basis)
    ):
        llm_priority = {"none": 0, "low": 4, "medium": 7, "high": 9}.get(str(shaped.get("manual_review_priority") or "none"), 4)
        signals.append(
            {
                "kind": "llm_manual_review_recommended",
                "priority": llm_priority,
                "details": {
                    "manual_review_kind": manual_review_kind,
                    "manual_review_priority": str(shaped.get("manual_review_priority") or "none"),
                    "manual_review_reason": str(shaped.get("manual_review_reason") or ""),
                    "action_basis": manual_review_action_basis,
                },
            }
        )

    if not shaped.get("service_urls") and standalone_service_candidates:
        signals.append(
            {
                "kind": "service_candidate_needs_confirmation",
                "priority": 8,
                "details": {
                    "service_candidate_count": len(standalone_service_candidates),
                    "service_candidates": standalone_service_candidates[:3],
                },
            }
        )

    if not shaped.get("project_repository_urls") and repo_candidates:
        signals.append(
            {
                "kind": "candidate_repo_needs_confirmation",
                "priority": 8,
                "details": {
                    "repo_candidate_count": len(repo_candidates),
                    "repo_candidates": repo_candidates[:3],
                },
            }
        )

    if service_status in {"live", "beta", "invite_only"} and shaped.get("status") == "draft":
        missing_editorial = sorted(blockers & {"summary missing", "tagline missing", "category missing", "LLM review missing"})
        if missing_editorial:
            signals.append(
                {
                    "kind": "service_now_usable_but_needs_editorial_review",
                    "priority": 9 if service_status == "live" else 8,
                    "details": {
                        "service_status": service_status,
                        "blockers": missing_editorial,
                        "website_url": str(shaped.get("website_url") or ""),
                    },
                }
            )

    if confirmed_identifier_count <= 1 and service_status == "unknown" and (
        len(service_candidates) >= 2 or len(repo_candidates) >= 2 or nsid_count >= 2
    ):
        signals.append(
            {
                "kind": "more_evidence_than_current_classification",
                "priority": 7,
                "details": {
                    "service_candidate_count": len(service_candidates),
                    "repo_candidate_count": len(repo_candidates),
                    "nsid_count": nsid_count,
                    "confirmed_identifier_count": confirmed_identifier_count,
                },
            }
        )

    latest_activity = project_latest_activity_at(shaped)
    now = dt.datetime.now(dt.timezone.utc)
    activity_days = activity_age_days(latest_activity, now)
    editorial_missing = not str(shaped.get("tagline") or "").strip() or not str(shaped.get("summary") or "").strip()
    if activity_days is not None and activity_days <= 14 and (editorial_missing or service_status in {"unknown", "unverified"}):
        signals.append(
            {
                "kind": "stale_metadata_with_recent_activity",
                "priority": 7,
                "details": {
                    "activity_age_days": activity_days,
                    "latest_activity_at": latest_activity.isoformat() if latest_activity else "",
                    "missing_editorial": editorial_missing,
                    "service_status": service_status,
                },
            }
        )

    if service_status == "unverified" and service_confidence in {"medium", "high"} and service_evidence & {
        "login_surface_detected",
        "app_shell_detected",
        "invite_text_detected",
        "beta_text_detected",
        "coming_soon_detected",
    }:
        signals.append(
            {
                "kind": "service_status_unverified_but_promising",
                "priority": 8,
                "details": {
                    "service_status_confidence": service_confidence,
                    "service_status_evidence": sorted(service_evidence),
                },
            }
        )

    if str(shaped.get("category") or "") == "lexicon" and shaped.get("service_urls"):
        signals.append(
            {
                "kind": "category_recheck_needed",
                "priority": 7,
                "details": {
                    "category": str(shaped.get("category") or ""),
                    "website_url": str(shaped.get("website_url") or ""),
                    "service_url_count": len(shaped.get("service_urls", [])),
                    "repo_url_count": len(project_repo_hint_urls(shaped)),
                },
            }
        )

    if shaped.get("external_lexicons") and (shaped.get("resolved_nsids") or shaped.get("input_nsids")):
        signals.append(
            {
                "kind": "external_lexicon_dependency_needs_review",
                "priority": 6,
                "details": {
                    "external_lexicons": list(shaped.get("external_lexicons", []))[:5],
                    "owned_nsid_count": len(project_record.unique_list(list(shaped.get("resolved_nsids", [])) + list(shaped.get("input_nsids", [])))),
                },
            }
        )

    if nsid_count >= 3 or len(shaped.get("lexicon_schema_samples", [])) >= 3 or len(shaped.get("recent_record_samples", [])) >= 6:
        signals.append(
            {
                "kind": "high_information_density",
                "priority": 6,
                "details": {
                    "nsid_count": nsid_count,
                    "lexicon_schema_sample_count": len(shaped.get("lexicon_schema_samples", [])),
                    "recent_record_sample_count": len(shaped.get("recent_record_samples", [])),
                },
            }
        )

    if len(service_candidates) >= 3 or len(repo_candidates) >= 3 or len(shaped.get("manual_seeds", [])) >= 4:
        signals.append(
            {
                "kind": "many_identity_candidates",
                "priority": 5,
                "details": {
                    "service_candidate_count": len(service_candidates),
                    "repo_candidate_count": len(repo_candidates),
                    "manual_seed_count": len(shaped.get("manual_seeds", [])),
                },
            }
        )

    if dismissed_kinds:
        signals = [signal for signal in signals if str(signal.get("kind") or "") not in dismissed_kinds]
    return signals


def confirmed_dns_lexicon_publications(project: dict[str, Any]) -> list[dict[str, str]]:
    confirmed_nsids = {
        identifier_core.normalize_nsid(str(item or ""))
        for item in project.get("resolved_nsids", [])
        if identifier_core.normalize_nsid(str(item or ""))
    }
    sampled_nsids = {
        identifier_core.normalize_nsid(str(item.get("nsid") or ""))
        for item in project.get("lexicon_schema_samples", [])
        if isinstance(item, dict) and identifier_core.normalize_nsid(str(item.get("nsid") or ""))
    }
    publications: list[dict[str, str]] = []
    for entry in project_record.normalize_lexicon_resolution_entries(
        project.get("lexicon_resolution_entries", [])
    ):
        nsid = identifier_core.normalize_nsid(str(entry.get("resolved_nsid") or ""))
        authority_domain = str(entry.get("authority_domain") or "").strip().lower().rstrip(".")
        repo_did = str(entry.get("repo_did") or "").strip()
        record_uri = str(entry.get("record_uri") or "").strip()
        expected_record_uri = f"at://{repo_did}/com.atproto.lexicon.schema/{nsid}"
        if (
            not nsid
            or nsid not in confirmed_nsids
            or nsid not in sampled_nsids
            or authority_domain != identifier_core.derive_authority_domain(nsid)
            or identifier_core.normalize_nsid(str(entry.get("input_nsid") or "")) != nsid
            or not repo_did
            or record_uri != expected_record_uri
        ):
            continue
        publications.append(
            {
                "nsid": nsid,
                "authority_domain": authority_domain,
                "repo_did": repo_did,
                "record_uri": record_uri,
                "resolved_at": str(entry.get("resolved_at") or "").strip(),
            }
        )
    return publications


def service_is_derivative_bluesky_tool(project: dict[str, Any]) -> bool:
    inspired_by = {str(item or "").strip().lower() for item in project.get("inspired_by", []) if str(item or "").strip()}
    if inspired_by and inspired_by != {"bluesky"}:
        return False
    if project.get("resolved_nsids") or project.get("external_lexicons"):
        return False
    text_blob = " ".join(
        [
            str(project.get("summary") or ""),
            str(project.get("tagline") or ""),
            str(project.get("classification_reason") or ""),
            str(project.get("watch_reason") or ""),
            " ".join(str(item or "") for item in project.get("features", [])),
        ]
    ).lower()
    utility_markers = [
        "viewer",
        "profile viewer",
        "dashboard",
        "analytics",
        "bookmark",
        "bookmarks",
        "likes",
        "followers",
        "history",
        "catalog",
        "search",
        "explore public bluesky",
        "プロフィール閲覧",
        "閲覧",
        "検索",
        "分析",
        "ダッシュボード",
        "ブックマーク",
        "いいね",
        "フォロワー",
        "履歴",
    ]
    novelty_markers = [
        "community note",
        "community notes",
        "コミュニティノート",
        "fork",
        "clone",
        "フォーク",
        "拡張",
        "独自機能",
        "annotation",
        "moderation",
    ]
    utility_hits = sum(1 for marker in utility_markers if marker in text_blob)
    novelty_hits = sum(1 for marker in novelty_markers if marker in text_blob)
    return utility_hits >= 2 and novelty_hits == 0


def project_has_owned_protocol_surface(project: dict[str, Any]) -> bool:
    return bool(
        project.get("resolved_nsids")
        or project.get("input_nsids")
        or project.get("recorded_lexicon_namespaces")
        or project.get("lexicon_namespaces")
        or project.get("collection_observations")
    )


def project_has_independent_service_surface(project: dict[str, Any]) -> bool:
    return bool(
        str(project.get("website_url") or "").strip()
        or project.get("service_urls")
        or str(project.get("introduced_url") or "").strip()
    )


def confirmed_github_fork_records(project: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(record)
        for record in project_record.normalize_source_repo_records(project.get("source_repo_records", []))
        if str(record.get("provider") or "") == "github"
        and str(record.get("confidence") or "") == "confirmed"
        and str(record.get("relation") or "") == "fork_repo"
    ]


def fork_without_independent_identity_reasons(project: dict[str, Any]) -> list[str]:
    fork_records = confirmed_github_fork_records(project)
    if not fork_records:
        return []
    if project_has_independent_service_surface(project):
        return []
    if project_has_owned_protocol_surface(project):
        return []
    service_status = project_record.normalize_service_status(str(project.get("service_status") or "unknown"))
    if service_status in {"live", "beta", "invite_only"}:
        return []
    reasons = [f"confirmed project repository is a GitHub fork: {fork_records[0].get('url') or ''}"]
    reasons.append("no independent website or service surface")
    reasons.append("no owned lexicon or collection surface")
    if service_status:
        reasons.append(f"service_status={service_status}")
    return reasons


def derivative_bluesky_tool_out_of_scope_reasons(project: dict[str, Any]) -> list[str]:
    if str(project.get("introduced_url") or "").strip():
        return []
    if str(project.get("category") or "") == "lexicon":
        return []
    if project_has_owned_protocol_surface(project):
        return []
    if not service_is_derivative_bluesky_tool(project):
        return []
    reasons = ["derivative Bluesky utility without owned protocol surface"]
    service_status = project_record.normalize_service_status(str(project.get("service_status") or "unknown"))
    if service_status:
        reasons.append(f"service_status={service_status}")
    inspired_by = [str(item or "").strip() for item in project.get("inspired_by", []) if str(item or "").strip()]
    if inspired_by:
        reasons.append(f"inspired_by={', '.join(inspired_by[:3])}")
    return reasons


def ignore_review_entry(project: dict[str, Any]) -> dict[str, Any] | None:
    tracking_state = str(project.get("tracking_state") or "active")
    if tracking_state in {"out_of_scope", "ignore", "archived"}:
        return None
    if str(project.get("introduced_url") or "").strip():
        return None

    fork_reasons = fork_without_independent_identity_reasons(project)
    if not fork_reasons:
        return None

    nsids = project_record.unique_list(
        [str(item or "") for item in project.get("resolved_nsids", []) if str(item or "").strip()]
        + [str(item or "") for item in project.get("input_nsids", []) if str(item or "").strip()]
        + [str(item or "") for item in project.get("recorded_lexicon_namespaces", []) if str(item or "").strip()]
    )
    repo_urls = project_record.unique_list(
        [str(item or "") for item in project_record.project_repository_url_values(project) if str(item or "").strip()]
    )
    return {
        "project_key": project.get("project_key", ""),
        "name": project.get("name", ""),
        "tracking_state": tracking_state,
        "service_status": project_record.normalize_service_status(str(project.get("service_status") or "unknown")),
        "category": str(project.get("category") or ""),
        "kind": "fork_without_independent_identity",
        "seed_type": project.get("seed_type", ""),
        "seed_value": project.get("seed_value", ""),
        "website_url": str(project.get("website_url") or ""),
        "repo_urls": repo_urls,
        "nsids": nsids,
        "reasons": fork_reasons,
        "suggested_tracking_state": "ignore",
    }


def auth_only_out_of_scope_reasons(project: dict[str, Any]) -> list[str]:
    category = str(project.get("category") or "")
    has_current_signal = bool(project.get("atproto_auth_only")) and category in {"project", "personal_tool"}
    if not has_current_signal:
        return []
    if project_has_owned_protocol_surface(project):
        return []
    return ["authentication-only integration without owned protocol surface"]


def out_of_scope_review_entry(project: dict[str, Any]) -> dict[str, Any] | None:
    tracking_state = str(project.get("tracking_state") or "active")
    if tracking_state == "out_of_scope":
        if not project_has_owned_protocol_surface(project):
            return None
        return {
            "project_key": project.get("project_key", ""),
            "name": project.get("name", ""),
            "tracking_state": tracking_state,
            "service_status": project_record.normalize_service_status(str(project.get("service_status") or "unknown")),
            "category": str(project.get("category") or ""),
            "kind": "owned_protocol_surface_detected",
            "seed_type": project.get("seed_type", ""),
            "seed_value": project.get("seed_value", ""),
            "website_url": str(project.get("website_url") or ""),
            "repo_urls": project_record.unique_list(
                [str(item or "") for item in project_record.project_repository_url_values(project) if str(item or "").strip()]
            ),
            "nsids": project_record.unique_list(
                [str(item or "") for item in project.get("resolved_nsids", []) if str(item or "").strip()]
                + [str(item or "") for item in project.get("input_nsids", []) if str(item or "").strip()]
                + [str(item or "") for item in project.get("recorded_lexicon_namespaces", []) if str(item or "").strip()]
            ),
            "reasons": ["owned protocol surface detected after out-of-scope classification"],
            "suggested_tracking_state": "active",
        }
    if tracking_state in {"ignore", "archived"}:
        return None
    if str(project.get("introduced_url") or "").strip():
        return None

    auth_reasons = auth_only_out_of_scope_reasons(project)
    derivative_reasons = derivative_bluesky_tool_out_of_scope_reasons(project)
    if not auth_reasons and not derivative_reasons:
        return None
    if auth_reasons:
        kind = "auth_only_without_owned_protocol_surface"
        reasons = auth_reasons
    else:
        kind = "derivative_bluesky_utility"
        reasons = derivative_reasons

    nsids = project_record.unique_list(
        [str(item or "") for item in project.get("resolved_nsids", []) if str(item or "").strip()]
        + [str(item or "") for item in project.get("input_nsids", []) if str(item or "").strip()]
        + [str(item or "") for item in project.get("recorded_lexicon_namespaces", []) if str(item or "").strip()]
    )
    return {
        "project_key": project.get("project_key", ""),
        "name": project.get("name", ""),
        "tracking_state": tracking_state,
        "service_status": project_record.normalize_service_status(str(project.get("service_status") or "unknown")),
        "category": str(project.get("category") or ""),
        "kind": kind,
        "seed_type": project.get("seed_type", ""),
        "seed_value": project.get("seed_value", ""),
        "website_url": str(project.get("website_url") or ""),
        "repo_urls": project_record.unique_list(
            [str(item or "") for item in project_record.project_repository_url_values(project) if str(item or "").strip()]
        ),
        "nsids": nsids,
        "reasons": reasons,
        "suggested_tracking_state": "out_of_scope",
    }


IGNORE_REVIEW_NOTE_REASONS: dict[str, str] = {
    "atproto_unrelated": "atproto無関係",
    "personal_experiment": "個人の実験",
    "fork_without_identity": "独自性の薄いfork",
    "other": "その他",
}


OUT_OF_SCOPE_REVIEW_NOTE_REASONS: dict[str, str] = {
    "auth_only": "認証用途のみ",
    "derivative_utility": "派生Blueskyツール",
    "client_only": "Blueskyクライアント",
    "other": "現在の調査スコープ外",
}


def default_ignore_review_note_reason(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "")
    if kind == "fork_without_independent_identity":
        return "fork_without_identity"
    return "other"


def build_ignore_review_note(item: dict[str, Any], reason_key: str, extra_note: str = "") -> str:
    label = IGNORE_REVIEW_NOTE_REASONS.get(reason_key, IGNORE_REVIEW_NOTE_REASONS["other"])
    parts = [f"review-ignore: {label}"]
    if extra_note:
        parts.append(str(extra_note).strip())
    return " / ".join([part for part in parts if part])


def default_out_of_scope_review_note_reason(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "")
    if kind == "owned_protocol_surface_detected":
        return "other"
    if kind == "auth_only_without_owned_protocol_surface":
        return "auth_only"
    if kind == "derivative_bluesky_utility":
        return "derivative_utility"
    return "other"


def build_out_of_scope_review_note(item: dict[str, Any], reason_key: str, extra_note: str = "") -> str:
    label = OUT_OF_SCOPE_REVIEW_NOTE_REASONS.get(reason_key, OUT_OF_SCOPE_REVIEW_NOTE_REASONS["other"])
    parts = [f"review-out-of-scope: {label}"]
    if extra_note:
        parts.append(str(extra_note).strip())
    return " / ".join([part for part in parts if part])


def promotion_candidate_visibility(project: dict[str, Any]) -> dict[str, Any]:
    if str(project.get("introduced_url") or "").strip():
        return {"group": "excluded", "reasons": ["already_introduced"]}
    tracking_state = str(project.get("tracking_state") or "active")
    if tracking_state in {"out_of_scope", "ignore"}:
        return {
            "group": "excluded",
            "reasons": [f"tracking_state_{tracking_state}"],
        }
    if active_promotion_deferral(project):
        return {"group": "deferred", "reasons": ["active_promotion_deferral"]}
    return {"group": "eligible", "reasons": []}


def promotion_service_evidence_group(signals: list[dict[str, Any]]) -> str:
    signal_kinds = {str(item.get("kind") or "") for item in signals}
    if "service_surface_confirmed" in signal_kinds:
        return "confirmed"
    if "service_candidate_needs_validation" in signal_kinds:
        return "needs_validation"
    return "not_applicable"


def promotion_queue_score_breakdown(
    project: dict[str, Any],
    signals: list[dict[str, Any]],
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current_time = now or dt.datetime.now(dt.timezone.utc)
    components = {
        "service_evidence": 0,
        "lexicon_publication": 0,
        "technical_interest": 0,
        "service_status": 0,
        "service_confidence": 0,
        "profile_completeness": 0,
        "repository_evidence": 0,
        "lexicon_evidence": 0,
        "review_readiness": 0,
        "atproto_usage": 0,
        "promotion_blockers": 0,
        "recent_activity": 0,
        "recent_collection_activity": 0,
        "recent_repository_activity": 0,
    }
    signal_kinds = {str(item.get("kind") or "") for item in signals}
    if "service_surface_confirmed" in signal_kinds:
        components["service_evidence"] = 200
    elif "service_candidate_needs_validation" in signal_kinds:
        components["service_evidence"] = 100
    if "lexicon_publisher_confirmed" in signal_kinds:
        components["lexicon_publication"] = 160
    if "technical_insight_candidate" in signal_kinds:
        components["technical_interest"] = int(project.get("technical_interest_score", 0) or 0)

    service_status = project_record.normalize_service_status(project.get("service_status"))
    components["service_status"] = {
        "live": 30,
        "beta": 20,
        "invite_only": 15,
        "unreleased": 10,
        "unverified": 8,
        "none": 6,
        "unknown": 4,
        "private": 2,
        "dead": 0,
    }.get(service_status, 0)
    components["service_confidence"] = {
        "high": 12,
        "medium": 8,
        "low": 4,
        "none": 0,
    }.get(project_record.normalize_service_status_confidence(project.get("service_status_confidence")), 0)
    if str(project.get("summary") or "").strip():
        components["profile_completeness"] += 8
    if str(project.get("tagline") or "").strip():
        components["profile_completeness"] += 6
    if str(project.get("tagline") or "").strip() and not bool(project.get("tagline_is_provisional")):
        components["profile_completeness"] += 3
    if project_repo_hint_urls(project):
        components["repository_evidence"] = 6
    nsid_hints = project_record.unique_list(
        [str(item or "") for item in project.get("resolved_nsids", []) if str(item or "").strip()]
        + [str(item or "") for item in project.get("input_nsids", []) if str(item or "").strip()]
    )
    if nsid_hints:
        components["lexicon_evidence"] = min(4, len(nsid_hints))
    if not bool(project.get("manual_review_recommended")):
        components["review_readiness"] = 4
    # Ownership/observation strength is not depth of protocol use.
    usage_role = atproto_usage.projection(project)["role"]
    if signal_kinds & {"service_surface_confirmed", "service_candidate_needs_validation", "lexicon_publisher_confirmed"}:
        components["atproto_usage"] = 60 if usage_role == "main_data" else -120 if usage_role in atproto_usage.LIMITED_ROLES else 0
    blockers = set(str(item) for item in project.get("promotion_blockers", []))
    components["promotion_blockers"] = -len(
        blockers & {"summary missing", "tagline missing", "fewer than 3 features", "LLM review missing"}
    )
    latest_activity = project_latest_activity_at(project)
    activity_days = activity_age_days(latest_activity, current_time)
    if activity_days is not None:
        if activity_days <= 3:
            components["recent_activity"] = 18
        elif activity_days <= 7:
            components["recent_activity"] = 14
        elif activity_days <= 14:
            components["recent_activity"] = 10
        elif activity_days <= 30:
            components["recent_activity"] = 6
        elif activity_days <= 60:
            components["recent_activity"] = 2
        elif activity_days >= 180:
            components["recent_activity"] = -4
    latest_collection_activity = project_latest_collection_activity_at(project)
    latest_repo_activity = project_latest_repo_activity_at(project)
    collection_days = activity_age_days(latest_collection_activity, current_time)
    repo_days = activity_age_days(latest_repo_activity, current_time)
    if collection_days is not None and collection_days <= 14:
        components["recent_collection_activity"] = 3
    if repo_days is not None and repo_days <= 14:
        components["recent_repository_activity"] = 3
    return {"total": sum(components.values()), "components": components}


def promotion_queue_sort_score(project: dict[str, Any], signals: list[dict[str, Any]]) -> int:
    return int(promotion_queue_score_breakdown(project, signals)["total"])


def promotion_policy_projection(
    project: dict[str, Any],
    signals: list[dict[str, Any]],
) -> dict[str, Any]:
    priority_adjustment = active_promotion_priority_adjustment(project)
    return {
        "candidate_visibility": promotion_candidate_visibility(project),
        "attention": promotion_attention.projection(project),
        "atproto_usage": atproto_usage.projection(project),
        "active_deferral": active_promotion_deferral(project),
        "service_evidence_group": promotion_service_evidence_group(signals),
        "priority_group": "lowered" if priority_adjustment else "normal",
        "priority_reason": str((priority_adjustment or {}).get("reason") or ""),
        "score_breakdown": promotion_queue_score_breakdown(project, signals),
    }


def review_queue_sort_score(project: dict[str, Any], signals: list[dict[str, Any]]) -> int:
    top_priority = max([int(item.get("priority", 0) or 0) for item in signals] or [0])
    return top_priority * 10 + int(project.get("atproto_contribution_score", 0) or 0)


def compute_showcase_signals(
    project: dict[str, Any],
    *,
    include_personal_tools: bool = False,
    include_ineligible: bool = False,
) -> list[dict[str, Any]]:
    shaped = apply_promotion_state(project_record.ensure_project_shape(dict(project)))
    return _showcase_signals_from_shaped(
        shaped, include_personal_tools=include_personal_tools, include_ineligible=include_ineligible,
    )


def _showcase_signals_from_shaped(
    shaped: dict[str, Any], *, include_personal_tools: bool, include_ineligible: bool,
) -> list[dict[str, Any]]:
    """Read a normalized project with promotion state already applied."""
    if not include_ineligible and promotion_candidate_visibility(shaped)["group"] != "eligible":
        return []

    signals: list[dict[str, Any]] = []
    blockers = set(str(item) for item in shaped.get("promotion_blockers", []))
    service_status = str(shaped.get("service_status") or "unknown")
    service_status_confidence = project_record.normalize_service_status_confidence(
        shaped.get("service_status_confidence")
    )
    service_status_evidence = {
        str(item).strip()
        for item in shaped.get("service_status_evidence", [])
        if str(item).strip()
    }
    category = str(shaped.get("category") or "")

    usage_role = atproto_usage.projection(shaped)["role"]

    allowed_service_categories = {"project", "infra_tool"}
    if include_personal_tools:
        allowed_service_categories.add("personal_tool")
    if (
        category in allowed_service_categories
        and service_status in {"live", "beta", "invite_only", "unverified"}
    ):
        if shaped.get("service_urls"):
            usable_surface_evidence = {
                "login_surface_detected",
                "app_shell_detected",
                "browser_login_surface_reached",
                "browser_password_form_detected",
                "browser_identity_form_detected",
                "browser_oauth_entry_detected",
                "browser_public_app_surface_detected",
                "verified_authority_service_surface",
                "invite_text_detected",
                "beta_text_detected",
            }
            surface_confirmed = (
                service_status != "unverified"
                and service_status_confidence in {"medium", "high"}
                and bool(service_status_evidence & usable_surface_evidence)
            )
            validation_reasons: list[str] = []
            if service_status == "unverified":
                validation_reasons.append("service_status_unverified")
            if service_status_confidence not in {"medium", "high"}:
                validation_reasons.append("low_service_status_confidence")
            if not service_status_evidence & usable_surface_evidence:
                validation_reasons.append("usable_surface_unconfirmed")
            signals.append(
                {
                    "kind": (
                        "service_surface_confirmed"
                        if surface_confirmed
                        else "service_candidate_needs_validation"
                    ),
                    "priority": (
                        ((9 if service_status == "live" else 8) if surface_confirmed else 6)
                        if usage_role == "main_data"
                        else (5 if surface_confirmed else 3) if usage_role in atproto_usage.LIMITED_ROLES
                        else (7 if surface_confirmed else 4)
                    ),
                    "details": {
                        "category": category,
                        "service_status": service_status,
                        "service_status_confidence": service_status_confidence,
                        "service_status_evidence": sorted(service_status_evidence),
                        "validation_reasons": validation_reasons,
                        "website_url": str(shaped.get("website_url") or ""),
                        "blockers": sorted(blockers & {"summary missing", "tagline missing", "fewer than 3 features", "LLM review missing"}),
                    },
                }
            )

    allowed_lexicon_categories = set(project_record.PROJECT_CATEGORY_VALUES) - {"reject", "name_only", "personal_tool"}
    if include_personal_tools:
        allowed_lexicon_categories.add("personal_tool")
    dns_publications = confirmed_dns_lexicon_publications(shaped)
    if category in allowed_lexicon_categories and dns_publications:
        signals.append(
            {
                "kind": "lexicon_publisher_confirmed",
                "priority": 5 if usage_role in atproto_usage.LIMITED_ROLES else 7,
                "details": {
                    "publication_count": len(dns_publications),
                    "publications": dns_publications[:5],
                    "website_url": str(shaped.get("website_url") or ""),
                    "repo_url": project_repo_hint_urls(shaped)[:1],
                    "blockers": sorted(blockers),
                },
            }
        )

    technical_interest_level = str(shaped.get("technical_interest_level") or "none")
    technical_interest_reasons = list(shaped.get("technical_interest_reasons", []))
    technical_interest_summary = str(shaped.get("technical_interest_summary") or "").strip()
    if (
        technical_interest_level in {"high", "exceptional"}
        and technical_interest_reasons
        and technical_interest_summary
        and category not in {"reject", "name_only"}
    ):
        signals.append(
            {
                "kind": "technical_insight_candidate",
                "priority": 9 if technical_interest_level == "exceptional" else 8,
                "details": {
                    "technical_interest_level": technical_interest_level,
                    "technical_interest_score": int(shaped.get("technical_interest_score", 0) or 0),
                    "technical_interest_reasons": technical_interest_reasons,
                    "technical_interest_summary": technical_interest_summary,
                },
            }
        )

    return signals


def project_latest_collection_activity_at(project: dict[str, Any]) -> dt.datetime | None:
    latest: dt.datetime | None = None
    for observation in project.get("collection_observations", []):
        if not isinstance(observation, dict):
            continue
        recent_count = int(observation.get("recent_count") or 0)
        if recent_count > 0:
            observed_at = project_record.parse_iso_datetime(str(observation.get("observed_at") or ""))
            if observed_at and (latest is None or observed_at > latest):
                latest = observed_at
        max_seen = project_record.parse_iso_datetime(str(observation.get("max") or ""))
        if max_seen and (latest is None or max_seen > latest):
            latest = max_seen
    return latest


def project_latest_repo_activity_at(project: dict[str, Any]) -> dt.datetime | None:
    latest: dt.datetime | None = None
    for record in project.get("source_repo_records", []):
        if not isinstance(record, dict):
            continue
        updated_at = project_record.parse_iso_datetime(str(record.get("updated_at") or ""))
        if updated_at and (latest is None or updated_at > latest):
            latest = updated_at
    return latest


def project_latest_activity_at(project: dict[str, Any]) -> dt.datetime | None:
    latest_collection = project_latest_collection_activity_at(project)
    latest_repo = project_latest_repo_activity_at(project)
    if latest_collection is None:
        return latest_repo
    if latest_repo is None:
        return latest_collection
    return max(latest_collection, latest_repo)


def activity_age_days(latest: dt.datetime | None, now: dt.datetime) -> int | None:
    if latest is None:
        return None
    return max(0, (now - latest).days)


def activity_is_recent(latest: dt.datetime | None, now: dt.datetime, threshold_days: int) -> bool:
    age = activity_age_days(latest, now)
    return age is not None and age < max(1, int(threshold_days))


def stalled_review_entry(
    project: dict[str, Any],
    *,
    now: dt.datetime,
    inactivity_days: int,
    max_enrich_age_days: int,
) -> dict[str, Any] | None:
    tracking_state = str(project.get("tracking_state") or "active")
    latest_collection_activity = project_latest_collection_activity_at(project)
    latest_repo_activity = project_latest_repo_activity_at(project)
    recent_collection_activity = activity_is_recent(latest_collection_activity, now, inactivity_days)
    recent_repo_activity = activity_is_recent(latest_repo_activity, now, inactivity_days)
    collection_age_days = activity_age_days(latest_collection_activity, now)
    repo_age_days = activity_age_days(latest_repo_activity, now)

    if tracking_state == "stalled":
        reasons: list[str] = []
        if recent_repo_activity:
            reasons.append(f"recent repo activity {repo_age_days} days ago")
        if recent_collection_activity:
            reasons.append(f"recent collection activity {collection_age_days} days ago")
        if reasons:
            return {
                "project_key": project.get("project_key", ""),
                "name": project.get("name", ""),
                "tracking_state": tracking_state,
                "service_status": str(project.get("service_status") or "unknown"),
                "seed_type": project.get("seed_type", ""),
                "seed_value": project.get("seed_value", ""),
                "last_enriched_at": project.get("last_enriched_at", ""),
                "latest_collection_activity_at": latest_collection_activity.isoformat() if latest_collection_activity else "",
                "latest_repo_activity_at": latest_repo_activity.isoformat() if latest_repo_activity else "",
                "reasons": reasons,
                "suggested_tracking_state": "active",
            }
        return None

    if tracking_state != "active":
        return None

    if recent_repo_activity:
        return None

    reasons: list[str] = []
    hard_signal = False
    if recent_collection_activity:
        if tracking_state == "active" and repo_age_days is not None and repo_age_days >= inactivity_days:
            return {
                "project_key": project.get("project_key", ""),
                "name": project.get("name", ""),
                "tracking_state": tracking_state,
                "service_status": str(project.get("service_status") or "unknown"),
                "seed_type": project.get("seed_type", ""),
                "seed_value": project.get("seed_value", ""),
                "last_enriched_at": project.get("last_enriched_at", ""),
                "latest_collection_activity_at": latest_collection_activity.isoformat() if latest_collection_activity else "",
                "latest_repo_activity_at": latest_repo_activity.isoformat() if latest_repo_activity else "",
                "reasons": [
                    f"recent collection activity {collection_age_days} days ago",
                    f"no recent repo activity for {repo_age_days} days",
                ],
                "suggested_tracking_state": "stable",
            }
        return None

    last_enriched_at = project_record.parse_iso_datetime(str(project.get("last_enriched_at") or ""))
    if last_enriched_at is None:
        return None
    enrich_age = max(0, (now - last_enriched_at).days)
    if enrich_age >= max(1, int(max_enrich_age_days)):
        return None

    if latest_collection_activity is not None:
        has_recent_counts = any(
            isinstance(item, dict) and int(item.get("recent_count") or 0) > 0
            for item in project.get("collection_observations", [])
        )
        if collection_age_days is not None and collection_age_days >= inactivity_days and not has_recent_counts:
            reasons.append(f"no collection activity for {collection_age_days} days")
            hard_signal = True

    if latest_repo_activity is not None and repo_age_days is not None and repo_age_days >= inactivity_days:
        reasons.append(f"no repo activity for {repo_age_days} days")
        hard_signal = True

    service_status = str(project.get("service_status") or "unknown")
    if service_status in {"dead", "private"}:
        reasons.append(f"service_status={service_status}")
        hard_signal = True
    elif service_status in {"unknown", "none"}:
        reasons.append(f"service_status={service_status}")

    confirmed_identity_count = int(bool(project.get("service_urls"))) + int(bool(project.get("project_repository_urls"))) + int(bool(project.get("resolved_nsids")))
    if confirmed_identity_count <= 1:
        reasons.append("weak confirmed identity")

    if not hard_signal:
        return None

    if len(reasons) < 2:
        return None

    return {
        "project_key": project.get("project_key", ""),
        "name": project.get("name", ""),
        "tracking_state": tracking_state,
        "service_status": service_status,
        "seed_type": project.get("seed_type", ""),
        "seed_value": project.get("seed_value", ""),
        "last_enriched_at": project.get("last_enriched_at", ""),
        "latest_collection_activity_at": latest_collection_activity.isoformat() if latest_collection_activity else "",
        "latest_repo_activity_at": latest_repo_activity.isoformat() if latest_repo_activity else "",
        "reasons": reasons,
        "suggested_tracking_state": "stalled",
    }
