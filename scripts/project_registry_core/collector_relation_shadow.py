from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import re
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from . import collector_relation_policy
from . import domain_policy
from . import identifiers
from . import repo_urls


REPORT_SCHEMA_VERSION = 3
POLICY_VERSION = "collector-relation-shadow-v3"
VERDICTS = {"SUPPORTED", "NOT_SUPPORTED", "NEEDS_ASSESSMENT", "NOT_RECONSTRUCTIBLE"}
TRACEABLE_REPOSITORY_ADOPTION_SOURCES = {
    "operator_add_seed",
    "trust_policy",
}

EXPLICIT_SOURCE_LABELS = {
    "code",
    "code repository",
    "github repository",
    "repo",
    "repository",
    "source",
    "source code",
    "source repo",
    "source repository",
    "tangled repository",
    "view code",
    "view source",
    "view source code",
}
NON_SOURCE_LABEL_WORDS = {
    "backer",
    "backers",
    "donate",
    "donation",
    "fund",
    "funding",
    "patreon",
    "sponsor",
    "sponsors",
    "sponsorship",
}
SERVICE_SURFACE_MARKERS = {
    "app_shell_detected",
    "browser_identity_form_detected",
    "browser_oauth_entry_detected",
    "browser_password_form_detected",
    "browser_public_app_surface_detected",
    "login_surface_detected",
}


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _counter(values: Any) -> dict[str, int]:
    counter = values if isinstance(values, collections.Counter) else collections.Counter(values)
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda item: str(item[0]))}


def _normalized_label(value: str) -> str:
    lowered = str(value or "").lower()
    lowered = re.sub(r"[↗→›»]+", " ", lowered)
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def classify_service_link_label(value: str) -> str:
    label = _normalized_label(value)
    if not label:
        return "unlabeled"
    words = set(label.split())
    if words.intersection(NON_SOURCE_LABEL_WORDS) or re.search(
        r"(?:backer|donat|fund|patreon|sponsor)", label
    ):
        return "non_source"
    if label == "open source" or label.startswith("open source "):
        return "ambiguous"
    if label in EXPLICIT_SOURCE_LABELS:
        return "explicit_source"
    if label in {"github", "tangled", "codeberg", "gitlab"}:
        return "provider_only"
    return "ambiguous"


def classify_url_role(value: str) -> str:
    return collector_relation_policy.classify_url_role(value)


def _subject_adoption_sources(observation: dict[str, Any]) -> set[str]:
    return {
        str(adoption.get("source") or "")
        for entry in observation.get("subject_identifiers", [])
        for adoption in entry.get("adoptions", [])
        if str(adoption.get("source") or "")
    }


def evaluate_repository_project_attribution(observation: dict[str, Any]) -> dict[str, str]:
    if observation.get("direction") != "repo_to_url":
        return {"verdict": "NOT_EVALUATED", "evidence": "not_repository_subject"}
    if not _subject_confirmed(observation):
        return {"verdict": "NOT_SUPPORTED", "evidence": "repository_is_not_current_confirmed_component"}
    sources = _subject_adoption_sources(observation)
    if any(
        source in TRACEABLE_REPOSITORY_ADOPTION_SOURCES or source.startswith("operator_review:")
        for source in sources
    ):
        return {"verdict": "SUPPORTED", "evidence": "traceable_adoption"}
    if sources == {"legacy_manual_seed_migration"}:
        return {"verdict": "NEEDS_ASSESSMENT", "evidence": "legacy_bootstrap_only"}
    if not sources:
        return {"verdict": "NOT_RECONSTRUCTIBLE", "evidence": "active_adoption_context_missing"}
    return {"verdict": "NEEDS_ASSESSMENT", "evidence": "other_active_adoption"}


def evaluate_repository_url_reference(observation: dict[str, Any]) -> dict[str, str]:
    if observation.get("direction") != "repo_to_url":
        return {"verdict": "NOT_EVALUATED", "evidence": "not_repository_to_url"}
    target_role = classify_url_role(str(observation.get("object_value") or ""))
    if target_role in {"invalid", "non_public"}:
        return {"verdict": "NOT_SUPPORTED", "evidence": f"target_role_is_{target_role}"}
    if observation.get("method") == "github_homepage":
        return {"verdict": "SUPPORTED", "evidence": "github_repository_homepage_metadata"}
    if not bool(observation.get("context_reconstructible")):
        return {"verdict": "NOT_RECONSTRUCTIBLE", "evidence": "saved_repository_link_context_is_missing"}
    return {"verdict": "NEEDS_ASSESSMENT", "evidence": "repository_link_context_requires_assessment"}


def evaluate_homepage_target_role(observation: dict[str, Any]) -> dict[str, Any]:
    static_role = classify_url_role(str(observation.get("object_value") or ""))
    surface = str(observation.get("target_surface") or "none")
    primary_website = bool(observation.get("target_is_primary_website"))
    if static_role != "unknown":
        role = static_role
        verdict = "NOT_SUPPORTED"
    elif surface in {"confirmed", "observed"}:
        role = "service_surface"
        verdict = "SUPPORTED"
    elif surface == "candidate":
        role = "candidate_service"
        verdict = "NEEDS_ASSESSMENT"
    elif primary_website:
        role = "primary_website"
        verdict = "NEEDS_ASSESSMENT"
    else:
        role = "unknown"
        verdict = "NEEDS_ASSESSMENT"
    return {
        "verdict": verdict,
        "role": role,
        "static_role": static_role,
        "target_surface": surface,
        "target_is_primary_website": primary_website,
    }


def _repository_name_alignment(observation: dict[str, Any]) -> str:
    project_name = re.sub(r"[^a-z0-9]+", "", str(observation.get("project_name") or "").lower())
    path = urllib.parse.urlparse(str(observation.get("subject_value") or "")).path.strip("/").split("/")
    repo_name = re.sub(r"[^a-z0-9]+", "", (path[1] if len(path) > 1 else "").lower())
    if not project_name or not repo_name:
        return "unavailable"
    return "aligned" if project_name in repo_name or repo_name in project_name else "different"


def evaluate_homepage_candidate_profiles(observation: dict[str, Any]) -> dict[str, Any]:
    attribution = evaluate_repository_project_attribution(observation)
    reference = evaluate_repository_url_reference(observation)
    target_role = evaluate_homepage_target_role(observation)
    if reference["verdict"] == "NOT_SUPPORTED" or target_role["verdict"] == "NOT_SUPPORTED":
        current_membership = traceable_attribution = "NOT_SUPPORTED"
    elif reference["verdict"] != "SUPPORTED":
        current_membership = traceable_attribution = str(reference["verdict"])
    elif not _subject_confirmed(observation):
        current_membership = traceable_attribution = "NOT_SUPPORTED"
    else:
        current_membership = "SUPPORTED"
        traceable_attribution = (
            "SUPPORTED" if attribution["verdict"] == "SUPPORTED" else "NEEDS_ASSESSMENT"
        )
    return {
        "repository_project_attribution": attribution,
        "repository_url_reference": reference,
        "target_role": target_role,
        "candidate_projection": {
            "current_membership": current_membership,
            "traceable_attribution": traceable_attribution,
        },
        "name_alignment_review_signal": _repository_name_alignment(observation),
    }


def homepage_is_website_candidate(observation: dict[str, Any]) -> bool:
    return collector_relation_policy.repository_homepage_is_website_candidate(
        str(observation.get("object_value") or "")
    )


def evaluate_component_attachment_projection(observation: dict[str, Any]) -> dict[str, str]:
    """Project a matched identifier and directional relations to an attach action."""
    match_count = int(observation.get("best_match_count") or 0)
    match_standing = str(observation.get("best_match_standing") or "")
    outward_relation = bool(observation.get("outward_relation_supported"))
    return_relation = bool(observation.get("return_relation_supported"))
    if match_count == 0:
        return {"disposition": "UNMATCHED", "reason": "no_identifier_match"}
    if match_count > 1:
        return {
            "disposition": "REVIEW",
            "reason": "multiple_projects_share_best_identifier_match",
        }
    if match_standing != "confirmed":
        return {
            "disposition": "REVIEW",
            "reason": f"matched_identifier_is_{match_standing or 'unknown'}",
        }
    if outward_relation and return_relation:
        return {
            "disposition": "AUTO_ATTACH",
            "reason": "unique_confirmed_anchor_and_bidirectional_component_relations",
        }
    return {
        "disposition": "REVIEW",
        "reason": "component_relation_is_one_way",
    }


def endpoint_status(value: str) -> dict[str, str]:
    normalized = identifiers.normalize_url(str(value or ""))
    canonical = repo_urls.canonical_repo_url(normalized)
    if not normalized or not repo_urls.is_supported_repo_url(normalized):
        return {"status": "invalid", "canonical_value": canonical or normalized, "locator": normalized}
    return {
        "status": "repository_root" if canonical == normalized.rstrip("/") else "repository_locator",
        "canonical_value": canonical,
        "locator": normalized,
    }


def evaluate_observation(observation: dict[str, Any]) -> dict[str, Any]:
    direction = str(observation.get("direction") or "")
    if direction == "service_to_repo":
        endpoint = endpoint_status(str(observation.get("object_value") or ""))
        if endpoint["status"] == "invalid":
            return {"verdict": "NOT_SUPPORTED", "reason": "target_is_not_repository_endpoint", "endpoint": endpoint}
        label_status = classify_service_link_label(str(observation.get("link_label") or ""))
        if label_status == "explicit_source":
            return {"verdict": "SUPPORTED", "reason": "explicit_source_link", "endpoint": endpoint}
        if label_status == "non_source":
            return {"verdict": "NOT_SUPPORTED", "reason": "link_label_has_non_source_purpose", "endpoint": endpoint}
        if not bool(observation.get("context_reconstructible")):
            return {"verdict": "NOT_RECONSTRUCTIBLE", "reason": "saved_link_context_is_missing", "endpoint": endpoint}
        return {
            "verdict": "NEEDS_ASSESSMENT",
            "reason": f"{label_status}_repository_link",
            "endpoint": endpoint,
        }
    if direction == "repo_to_url":
        target_role = classify_url_role(str(observation.get("object_value") or ""))
        if target_role != "unknown":
            return {
                "verdict": "NOT_SUPPORTED",
                "reason": f"target_role_is_{target_role}",
                "target_role": target_role,
            }
        if not bool(observation.get("context_reconstructible")):
            return {
                "verdict": "NOT_RECONSTRUCTIBLE",
                "reason": "saved_repository_link_context_is_missing",
                "target_role": target_role,
            }
        return {
            "verdict": "NEEDS_ASSESSMENT",
            "reason": "same_project_relationship_requires_endpoint_comparison",
            "target_role": target_role,
        }
    return {"verdict": "NOT_SUPPORTED", "reason": "unsupported_direction"}


def evaluate_strict_projection(
    observation: dict[str, Any],
    deterministic: dict[str, Any],
    *,
    reciprocal_deterministic_support: bool = False,
) -> dict[str, Any]:
    if deterministic["verdict"] in {"SUPPORTED", "NOT_SUPPORTED"}:
        return dict(deterministic)
    if (
        observation.get("direction") == "repo_to_url"
        and str(observation.get("target_surface") or "none") in {"confirmed", "observed"}
        and reciprocal_deterministic_support
    ):
        return {
            "verdict": "SUPPORTED",
            "reason": "service_surface_and_reciprocal_explicit_source_link",
            "target_role": "service",
        }
    if deterministic["verdict"] == "NOT_RECONSTRUCTIBLE":
        return dict(deterministic)
    return dict(deterministic)


def load_case_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("version") or 0) != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError(f"invalid collector relation case catalog: {path}")
    return payload


def evaluate_case_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in catalog.get("cases", []):
        observation = dict(case.get("observation") or {})
        deterministic = evaluate_observation(observation)
        strict = evaluate_strict_projection(
            observation,
            deterministic,
            reciprocal_deterministic_support=bool(case.get("reciprocal_deterministic_support")),
        )
        actual = {
            "deterministic": deterministic["verdict"],
            "strict": strict["verdict"],
            "endpoint_status": str((deterministic.get("endpoint") or {}).get("status") or ""),
        }
        attachment = case.get("github_search_attachment")
        if isinstance(attachment, dict):
            attachment_result = evaluate_component_attachment_projection(attachment)
            actual["github_search_attachment"] = attachment_result["disposition"]
            actual["github_search_attachment_reason"] = attachment_result["reason"]
        expected = dict(case.get("expected") or {})
        passed = all(actual.get(key) == value for key, value in expected.items())
        results.append({
            "case_id": str(case.get("case_id") or ""),
            "description": str(case.get("description") or ""),
            "expected": expected,
            "actual": actual,
            "passed": passed,
        })
    return {
        "case_count": len(results),
        "passed_count": sum(bool(item["passed"]) for item in results),
        "failed_count": sum(not bool(item["passed"]) for item in results),
        "cases": results,
    }


def _service_results(state: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = state.get("evidence") if isinstance(state.get("evidence"), dict) else {}
    values = evidence.get("service_verification_results", state.get("service_verification_results", []))
    return [dict(item) for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _service_link_index(state: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for result in _service_results(state):
        bases = {
            identifiers.normalize_url(str(result.get(key) or ""))
            for key in ("requested_url", "final_url")
            if identifiers.normalize_url(str(result.get(key) or ""))
        }
        for link in result.get("links", []) if isinstance(result.get("links"), list) else []:
            if not isinstance(link, dict):
                continue
            raw_target = identifiers.normalize_url(str(link.get("url") or link.get("href") or ""))
            target = repo_urls.canonical_repo_url(raw_target) if repo_urls.is_supported_repo_url(raw_target) else raw_target
            if not target:
                continue
            context = {
                "link_text": str(link.get("text") or ""),
                "aria_label": str(link.get("aria_label") or ""),
                "link_title": str(link.get("title") or ""),
                "page_title": str(result.get("title") or ""),
                "page_description": str(result.get("description") or ""),
                "content_sha256": str(result.get("content_sha256") or ""),
            }
            for base in bases:
                output[(base, target)] = context
    return output


def _surface_statuses(
    state: dict[str, Any], identifiers_by_value: dict[tuple[str, str], list[dict[str, str]]],
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for (kind, value), entries in identifiers_by_value.items():
        if kind != "service_url":
            continue
        if any(item["role"] == "service" and item["classification"] == "confirmed" for item in entries):
            statuses[value] = "confirmed"
        elif any(item["role"] == "service" and item["classification"] == "candidate" for item in entries):
            statuses.setdefault(value, "candidate")
    for result in _service_results(state):
        markers = {str(item or "") for item in result.get("service_status_evidence", [])}
        if not markers.intersection(SERVICE_SURFACE_MARKERS):
            continue
        for key in ("requested_url", "final_url"):
            value = identifiers.normalize_url(str(result.get(key) or ""))
            if value and statuses.get(value) != "confirmed":
                statuses[value] = "observed"
    return statuses


def _identifier_kind(kind: str) -> str:
    return {"repo_url": "repo_url", "service_url": "service_url"}.get(kind, kind)


def _project_ui_url(project_key: str) -> str:
    return f"http://127.0.0.1:8888/?project={urllib.parse.quote(project_key, safe='')}"


def _load_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT project_key, name, website_url, state_json FROM projects ORDER BY project_key"
    ):
        key = str(row["project_key"])
        state = _json(row["state_json"], {})
        projects[key] = {
            "project_key": key,
            "name": str(row["name"]),
            "website_url": identifiers.normalize_url(str(row["website_url"] or "")),
            "state": state if isinstance(state, dict) else {},
            "identifiers": collections.defaultdict(list),
            "observations": [],
        }
    for row in conn.execute("SELECT * FROM project_identifiers ORDER BY project_key, ordinal"):
        project = projects.get(str(row["project_key"]))
        if project is None:
            continue
        kind = _identifier_kind(str(row["kind"]))
        raw_value = str(row["value"])
        value = repo_urls.canonical_repo_url(raw_value) if kind == "repo_url" else identifiers.normalize_url(raw_value)
        project["identifiers"][(kind, value)].append({
            "role": str(row["role"]),
            "classification": str(row["classification"]),
            "adoptions": [],
        })
    adoption_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_identifier_adoptions'"
    ).fetchone()
    if adoption_table is not None:
        for row in conn.execute(
            "SELECT * FROM project_identifier_adoptions WHERE cleared_at='' ORDER BY project_key, created_at"
        ):
            project = projects.get(str(row["project_key"]))
            if project is None:
                continue
            kind = _identifier_kind(str(row["kind"]))
            raw_value = str(row["value"])
            value = repo_urls.canonical_repo_url(raw_value) if kind == "repo_url" else identifiers.normalize_url(raw_value)
            for entry in project["identifiers"].get((kind, value), []):
                if entry["role"] != str(row["role"]):
                    continue
                entry["adoptions"].append({
                    "source": str(row["source"]),
                    "adoption_kind": str(row["adoption_kind"]),
                    "registration_origin": bool(row["registration_origin"]),
                    "review_required": bool(row["review_required"]),
                })
    for project in projects.values():
        project["link_index"] = _service_link_index(project["state"])
        project["surface_statuses"] = _surface_statuses(project["state"], project["identifiers"])
    for row in conn.execute(
        """
        SELECT * FROM project_relations
        WHERE predicate IN ('source_repository', 'repository_homepage')
        ORDER BY project_key, ordinal
        """
    ):
        project = projects.get(str(row["project_key"]))
        if project is None:
            continue
        subject_type = str(row["subject_type"])
        object_type = str(row["object_type"])
        if subject_type in {"service", "website"} and object_type == "repo":
            direction = "service_to_repo"
            subject_value = identifiers.normalize_url(str(row["subject_value"]))
            raw_object_value = identifiers.normalize_url(str(row["object_value"]))
            object_value = repo_urls.canonical_repo_url(raw_object_value)
            saved_context = _json(row["context_json"], {})
            matched_context = project["link_index"].get((subject_value, object_value), {})
            context = {**(saved_context if isinstance(saved_context, dict) else {}), **matched_context}
            link_label = " ".join(
                str(context.get(key) or "").strip()
                for key in ("link_text", "aria_label", "link_title")
                if str(context.get(key) or "").strip()
            )
            observation = {
                "direction": direction,
                "subject_kind": "service_url",
                "subject_value": subject_value,
                "object_kind": "repo_url",
                "object_value": raw_object_value,
                "canonical_object_value": object_value,
                "link_label": link_label,
                "context_reconstructible": bool(link_label),
            }
        elif subject_type == "repo" and object_type in {"service", "website"}:
            direction = "repo_to_url"
            subject_value = repo_urls.canonical_repo_url(str(row["subject_value"]))
            object_value = identifiers.normalize_url(str(row["object_value"]))
            observation = {
                "direction": direction,
                "subject_kind": "repo_url",
                "subject_value": subject_value,
                "object_kind": "service_url",
                "object_value": object_value,
                "target_surface": project["surface_statuses"].get(object_value, "none"),
                "project_website_url": project["website_url"],
                "target_is_primary_website": object_value == project["website_url"],
                "context_reconstructible": str(row["method"]) == "github_homepage",
            }
        else:
            continue
        target_key = (str(observation["object_kind"]), str(observation.get("canonical_object_value") or observation["object_value"]))
        subject_key = (str(observation["subject_kind"]), str(observation["subject_value"]))
        observation.update({
            "project_key": project["project_key"],
            "project_name": project["name"],
            "project_url": _project_ui_url(project["project_key"]),
            "source": str(row["source"]),
            "locator": str(row["locator"]),
            "method": str(row["method"]),
            "current_relation_predicate": str(row["predicate"]),
            "current_relation_classification": str(row["classification"]),
            "observed_at": str(row["observed_at"]),
            "subject_identifiers": list(project["identifiers"].get(subject_key, [])),
            "target_identifiers": list(project["identifiers"].get(target_key, [])),
        })
        project["observations"].append(observation)
    return list(projects.values())


def _github_search_attachment_audit(
    conn: sqlite3.Connection,
    projects: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    *,
    example_limit: int,
) -> dict[str, Any]:
    project_names = {str(item["project_key"]): str(item["name"]) for item in projects}
    exact_matches: dict[str, list[tuple[int, str, str]]] = collections.defaultdict(list)
    confirmed_family_matches: dict[str, set[str]] = collections.defaultdict(set)
    for row in conn.execute(
        """
        SELECT project_key, value, classification
        FROM project_identifiers
        WHERE kind='service_url' AND role='service'
          AND classification IN ('confirmed', 'candidate')
        """
    ):
        value = identifiers.normalize_url(str(row["value"]))
        standing = str(row["classification"])
        exact_matches[value].append(
            (0 if standing == "confirmed" else 1, str(row["project_key"]), standing)
        )
        if standing == "confirmed":
            family = domain_policy.root_domain(urllib.parse.urlparse(value).netloc.lower())
            if family:
                confirmed_family_matches[family].add(str(row["project_key"]))
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_noncurrent_identifiers'"
    ).fetchone():
        for row in conn.execute(
            """
            SELECT project_key, value, standing
            FROM project_noncurrent_identifiers
            WHERE kind='service_url' AND role='service'
            """
        ):
            standing = str(row["standing"])
            exact_matches[identifiers.normalize_url(str(row["value"]))].append(
                (2 if standing == "former" else 3, str(row["project_key"]), standing)
            )

    reciprocal_references = {
        (
            str(item["project_key"]),
            str(item["subject_value"]),
            str(item.get("canonical_object_value") or item["object_value"]),
        )
        for item in observations
        if item["direction"] == "service_to_repo"
        and item["current_relation_classification"] == "confirmed"
    }
    homepage_claims: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in observations:
        if item["direction"] != "repo_to_url" or item["method"] != "github_homepage":
            continue
        homepage_claims.setdefault(
            (str(item["project_key"]), str(item["subject_value"]), str(item["object_value"])),
            item,
        )

    rows: list[dict[str, Any]] = []
    family_counts: collections.Counter[str] = collections.Counter()
    for (relation_project_key, repo, homepage), item in homepage_claims.items():
        matches = sorted(exact_matches.get(homepage, []))
        if matches:
            best_priority = matches[0][0]
            best = [entry for entry in matches if entry[0] == best_priority]
        else:
            best = []
        best_project_keys = sorted({entry[1] for entry in best})
        best_standings = sorted({entry[2] for entry in best})
        chosen_project_key = best_project_keys[0] if best_project_keys else ""
        reciprocal = (
            chosen_project_key,
            homepage,
            repo,
        ) in reciprocal_references
        evaluation = evaluate_component_attachment_projection({
            "best_match_count": len(best_project_keys),
            "best_match_standing": best_standings[0] if len(best_standings) == 1 else "mixed",
            "outward_relation_supported": True,
            "return_relation_supported": reciprocal,
        })
        if chosen_project_key:
            placement = "same_project" if chosen_project_key == relation_project_key else "different_project"
            family_disposition = "exact_match_precedes_family"
        else:
            placement = "no_exact_match"
            family = domain_policy.root_domain(urllib.parse.urlparse(homepage).netloc.lower())
            family_project_keys = confirmed_family_matches.get(family, set()) if family else set()
            if len(family_project_keys) == 1:
                family_disposition = "unique_family_suggestion"
            elif len(family_project_keys) > 1:
                family_disposition = "ambiguous_family"
            else:
                family_disposition = "no_family_match"
        family_counts[family_disposition] += 1
        rows.append({
            "relation_project_key": relation_project_key,
            "relation_project_name": project_names.get(relation_project_key, ""),
            "relation_project_url": _project_ui_url(relation_project_key),
            "repo_url": repo,
            "homepage": homepage,
            "best_match_project_keys": best_project_keys,
            "best_match_project_names": [project_names.get(key, "") for key in best_project_keys],
            "best_match_standing": best_standings[0] if len(best_standings) == 1 else ("mixed" if best else ""),
            "previous_policy_disposition": "AUTO_ATTACH" if best else "UNMATCHED",
            "shadow_disposition": evaluation["disposition"],
            "shadow_reason": evaluation["reason"],
            "reciprocal_source_reference": reciprocal,
            "current_placement": placement,
            "family_disposition": family_disposition,
        })

    shadow_counts = collections.Counter(item["shadow_disposition"] for item in rows)
    reason_counts = collections.Counter(item["shadow_reason"] for item in rows)
    standing_counts = collections.Counter(
        item["best_match_standing"] or "none" for item in rows
    )
    placement_counts = collections.Counter(item["current_placement"] for item in rows)
    examples: dict[str, list[dict[str, Any]]] = {}
    for reason in sorted(reason_counts):
        examples[reason] = [item for item in rows if item["shadow_reason"] == reason][
            :example_limit
        ]
    return {
        "reconstructed_homepage_claim_count": len(rows),
        "historical_match_kind_event_count": None,
        "historical_match_kind_reconstructible": False,
        "historical_limit": (
            "sync result history does not preserve per-repository match_kind; this is a "
            "counterfactual replay over current identifiers and relations"
        ),
        "previous_policy_auto_attach_count": sum(
            item["previous_policy_disposition"] == "AUTO_ATTACH" for item in rows
        ),
        "shadow_disposition_counts": _counter(shadow_counts),
        "shadow_reason_counts": _counter(reason_counts),
        "best_match_standing_counts": _counter(standing_counts),
        "current_placement_counts": _counter(placement_counts),
        "family_disposition_counts": _counter(family_counts),
        "examples": examples,
        "comparison_is_read_only": True,
        "production_policy_connected": True,
    }


def _confirmed_component(entries: list[dict[str, str]], role: str) -> bool:
    return any(item.get("role") == role and item.get("classification") == "confirmed" for item in entries)


def _target_confirmed(observation: dict[str, Any]) -> bool:
    role = "source_repository" if observation["direction"] == "service_to_repo" else "service"
    return _confirmed_component(list(observation.get("target_identifiers", [])), role)


def _subject_confirmed(observation: dict[str, Any]) -> bool:
    role = "service" if observation["direction"] == "service_to_repo" else "source_repository"
    return _confirmed_component(list(observation.get("subject_identifiers", [])), role)


def _combined_claim_verdict(values: set[str]) -> str:
    for verdict in ("SUPPORTED", "NEEDS_ASSESSMENT", "NOT_RECONSTRUCTIBLE", "NOT_SUPPORTED"):
        if verdict in values:
            return verdict
    return "NOT_SUPPORTED"


def _claim_key(observation: dict[str, Any]) -> tuple[str, str, str, str]:
    value = str(observation.get("canonical_object_value") or observation.get("object_value") or "")
    role = "source_repository" if observation["direction"] == "service_to_repo" else "service"
    return str(observation["project_key"]), str(observation["object_kind"]), value, role


def _example(observation: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    output = {
        "project_key": observation["project_key"],
        "project_name": observation["project_name"],
        "project_url": observation["project_url"],
        "direction": observation["direction"],
        "subject": {"kind": observation["subject_kind"], "value": observation["subject_value"]},
        "object": {
            "kind": observation["object_kind"],
            "value": observation.get("canonical_object_value") or observation["object_value"],
            "locator": observation["object_value"],
        },
        "source": observation["source"],
        "method": observation["method"],
        "link_label": observation.get("link_label", ""),
        "target_surface": observation.get("target_surface", ""),
        "project_website_url": observation.get("project_website_url", ""),
        "target_is_primary_website": bool(observation.get("target_is_primary_website")),
        "current_relation_classification": observation["current_relation_classification"],
        "current_target_identifiers": observation.get("target_identifiers", []),
        "subject_adoptions": [
            adoption
            for entry in observation.get("subject_identifiers", [])
            for adoption in entry.get("adoptions", [])
        ],
        "verdict": evaluation["verdict"],
        "reason": evaluation["reason"],
    }
    if observation.get("direction") == "repo_to_url" and observation.get("method") == "github_homepage":
        output["homepage_axes"] = evaluate_homepage_candidate_profiles(observation)
    return output


def _content_hash(report: dict[str, Any]) -> str:
    payload = {key: value for key, value in report.items() if key != "content_hash"}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def compare_database(
    conn: sqlite3.Connection,
    *,
    case_catalog: dict[str, Any],
    example_limit: int = 20,
) -> dict[str, Any]:
    projects = _load_projects(conn)
    observations = [item for project in projects for item in project["observations"]]
    github_search_attachment = _github_search_attachment_audit(
        conn,
        projects,
        observations,
        example_limit=example_limit,
    )
    pair_index = {
        (
            item["project_key"],
            item["subject_value"] if item["direction"] == "service_to_repo" else item["object_value"],
            item.get("canonical_object_value") if item["direction"] == "service_to_repo" else item["subject_value"],
            item["direction"],
        ): item
        for item in observations
    }
    deterministic_results: list[dict[str, Any]] = []
    strict_results: list[dict[str, Any]] = []
    for observation in observations:
        deterministic = evaluate_observation(observation)
        deterministic_results.append(deterministic)
        service = observation["subject_value"] if observation["direction"] == "service_to_repo" else observation["object_value"]
        repo = observation.get("canonical_object_value") if observation["direction"] == "service_to_repo" else observation["subject_value"]
        reverse_direction = "repo_to_url" if observation["direction"] == "service_to_repo" else "service_to_repo"
        reverse = pair_index.get((observation["project_key"], service, repo, reverse_direction))
        reverse_supported = bool(reverse and evaluate_observation(reverse)["verdict"] == "SUPPORTED")
        strict_results.append(evaluate_strict_projection(
            observation,
            deterministic,
            reciprocal_deterministic_support=reverse_supported,
        ))

    method_counts = collections.Counter(f"{item['direction']}:{item['method']}" for item in observations)
    deterministic_counts = collections.Counter(item["verdict"] for item in deterministic_results)
    strict_counts = collections.Counter(item["verdict"] for item in strict_results)
    reason_counts = collections.Counter(item["reason"] for item in deterministic_results)

    current_candidates: set[tuple[str, str, str, str]] = set()
    current_policy_projection: set[tuple[str, str, str, str]] = set()
    current_candidate_verdicts: dict[tuple[str, str, str, str], set[str]] = collections.defaultdict(set)
    current_policy_verdicts: dict[tuple[str, str, str, str], set[str]] = collections.defaultdict(set)
    current_policy_subject_sources: dict[tuple[str, str, str, str], set[str]] = collections.defaultdict(set)
    current_policy_directions: dict[tuple[str, str, str, str], set[str]] = collections.defaultdict(set)
    deterministic_admissions: set[tuple[str, str, str, str]] = set()
    strict_admissions: set[tuple[str, str, str, str]] = set()
    for observation, deterministic, strict in zip(observations, deterministic_results, strict_results):
        if not _subject_confirmed(observation) or _target_confirmed(observation):
            continue
        key = _claim_key(observation)
        if (
            observation["direction"] == "service_to_repo"
            and observation["current_relation_classification"] == "confirmed"
        ) or (
            observation["direction"] == "repo_to_url"
            and observation["method"] == "github_homepage"
        ):
            current_policy_projection.add(key)
            current_policy_verdicts[key].add(str(strict["verdict"]))
            current_policy_directions[key].add(str(observation["direction"]))
            current_policy_subject_sources[key].update(
                str(adoption.get("source") or "(missing)")
                for entry in observation.get("subject_identifiers", [])
                for adoption in entry.get("adoptions", [])
            )
            if not current_policy_subject_sources[key]:
                current_policy_subject_sources[key].add("(missing)")
        if any(item.get("classification") == "candidate" for item in observation.get("target_identifiers", [])):
            current_candidates.add(key)
            current_candidate_verdicts[key].add(str(strict["verdict"]))
        if deterministic["verdict"] == "SUPPORTED":
            deterministic_admissions.add(key)
        if strict["verdict"] == "SUPPORTED":
            strict_admissions.add(key)

    homepage_rows = [
        (observation, strict)
        for observation, strict in zip(observations, strict_results)
        if observation["direction"] == "repo_to_url" and observation["method"] == "github_homepage"
    ]
    homepage_row_classifications = collections.Counter(
        item["current_relation_classification"] for item, _strict in homepage_rows
    )
    homepage_unique_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item, _strict in homepage_rows:
        homepage_unique_rows.setdefault(
            (
                str(item["project_key"]),
                str(item["subject_value"]),
                str(item["object_value"]),
            ),
            item,
        )
    homepage_current_relation_classifications = collections.Counter(
        item["current_relation_classification"] for item in homepage_unique_rows.values()
    )
    homepage_valid_direct_relations = {
        key: item
        for key, item in homepage_unique_rows.items()
        if evaluate_repository_url_reference(item)["verdict"] == "SUPPORTED"
    }
    homepage_confirmed_repository_rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in homepage_unique_rows.values():
        if _subject_confirmed(item):
            homepage_confirmed_repository_rows.setdefault(_claim_key(item), item)
    website_candidates_by_project: dict[str, set[str]] = collections.defaultdict(set)
    for item in homepage_confirmed_repository_rows.values():
        if homepage_is_website_candidate(item):
            website_candidates_by_project[str(item["project_key"])].add(str(item["object_value"]))
    website_projection_dispositions: dict[tuple[str, str, str, str], str] = {}
    for key, item in homepage_confirmed_repository_rows.items():
        target = str(item["object_value"])
        current_website = str(item.get("project_website_url") or "")
        if not homepage_is_website_candidate(item):
            disposition = "NOT_WEBSITE_CANDIDATE"
        elif current_website == target:
            disposition = "EXISTING_WEBSITE_MATCH"
        elif current_website:
            disposition = "EXISTING_WEBSITE_DIFFERS"
        elif len(website_candidates_by_project[str(item["project_key"])]) == 1:
            disposition = "EMPTY_WEBSITE_SINGLE_CANDIDATE"
        else:
            disposition = "EMPTY_WEBSITE_MULTIPLE_CANDIDATES"
        website_projection_dispositions[key] = disposition
    website_projection_counts = collections.Counter(website_projection_dispositions.values())
    website_projection_projects: dict[str, set[str]] = collections.defaultdict(set)
    website_projection_roles: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    for key, disposition in website_projection_dispositions.items():
        item = homepage_confirmed_repository_rows[key]
        website_projection_projects[disposition].add(str(item["project_key"]))
        website_projection_roles[disposition][
            evaluate_homepage_target_role(item)["role"]
        ] += 1
    homepage_current = {_claim_key(item) for item, _strict in homepage_rows if not _target_confirmed(item)}
    homepage_surface = {
        _claim_key(item)
        for item, _strict in homepage_rows
        if not _target_confirmed(item) and item.get("target_surface") in {"confirmed", "observed"}
    }
    homepage_strict = {
        _claim_key(item)
        for item, strict in homepage_rows
        if not _target_confirmed(item) and strict["verdict"] == "SUPPORTED"
    }
    homepage_admission_rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item, _strict in homepage_rows:
        if _subject_confirmed(item) and not _target_confirmed(item):
            homepage_admission_rows.setdefault(_claim_key(item), item)
    homepage_axis_results = {
        key: evaluate_homepage_candidate_profiles(item)
        for key, item in homepage_admission_rows.items()
    }
    homepage_attribution_verdicts = collections.Counter(
        result["repository_project_attribution"]["verdict"]
        for result in homepage_axis_results.values()
    )
    homepage_attribution_evidence = collections.Counter(
        result["repository_project_attribution"]["evidence"]
        for result in homepage_axis_results.values()
    )
    homepage_reference_verdicts = collections.Counter(
        result["repository_url_reference"]["verdict"]
        for result in homepage_axis_results.values()
    )
    homepage_reference_evidence = collections.Counter(
        result["repository_url_reference"]["evidence"]
        for result in homepage_axis_results.values()
    )
    homepage_target_roles = collections.Counter(
        result["target_role"]["role"] for result in homepage_axis_results.values()
    )
    homepage_current_membership_projection = collections.Counter(
        result["candidate_projection"]["current_membership"]
        for result in homepage_axis_results.values()
    )
    homepage_traceable_projection = collections.Counter(
        result["candidate_projection"]["traceable_attribution"]
        for result in homepage_axis_results.values()
    )
    homepage_name_alignment = collections.Counter(
        result["name_alignment_review_signal"] for result in homepage_axis_results.values()
    )

    examples: dict[str, list[dict[str, Any]]] = {}
    for verdict in sorted(VERDICTS):
        examples[f"deterministic_{verdict.lower()}"] = [
            _example(observation, result)
            for observation, result in zip(observations, deterministic_results)
            if result["verdict"] == verdict
        ][:example_limit]
    examples["github_homepage_surface_gated"] = [
        _example(observation, strict)
        for observation, strict in homepage_rows
        if observation.get("target_surface") in {"confirmed", "observed"}
    ][:example_limit]
    examples["github_homepage_three_axis"] = [
        _example(observation, {"verdict": "NEEDS_ASSESSMENT", "reason": "three_axis_review"})
        for observation in homepage_admission_rows.values()
    ][:example_limit]
    examples["github_homepage_website_projection"] = [
        {
            **_example(observation, {"verdict": "NEEDS_ASSESSMENT", "reason": "website_projection"}),
            "website_projection_disposition": website_projection_dispositions[key],
            "project_website_candidate_count": len(
                website_candidates_by_project[str(observation["project_key"])]
            ),
        }
        for key, observation in homepage_confirmed_repository_rows.items()
    ][:example_limit]
    examples["current_candidate_not_strictly_supported"] = [
        _example(observation, strict)
        for observation, strict in zip(observations, strict_results)
        if _claim_key(observation) in current_candidates and strict["verdict"] != "SUPPORTED"
    ][:example_limit]
    examples["strict_admission_supported"] = [
        _example(observation, strict)
        for observation, strict in zip(observations, strict_results)
        if _claim_key(observation) in strict_admissions
        and strict["verdict"] == "SUPPORTED"
        and _subject_confirmed(observation)
        and not _target_confirmed(observation)
    ][:example_limit]
    examples["current_policy_projection_not_strictly_supported"] = [
        _example(observation, strict)
        for observation, strict in zip(observations, strict_results)
        if _claim_key(observation) in current_policy_projection and strict["verdict"] != "SUPPORTED"
    ][:example_limit]

    case_results = evaluate_case_catalog(case_catalog)
    current_candidate_outcomes = collections.Counter(
        _combined_claim_verdict(values) for values in current_candidate_verdicts.values()
    )
    current_policy_outcomes = collections.Counter(
        _combined_claim_verdict(values) for values in current_policy_verdicts.values()
    )
    subject_source_outcomes: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for key, sources in current_policy_subject_sources.items():
        outcome = _combined_claim_verdict(current_policy_verdicts[key])
        for source in sources:
            subject_source_outcomes[source][outcome] += 1
    direction_outcomes: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for key, directions in current_policy_directions.items():
        outcome = _combined_claim_verdict(current_policy_verdicts[key])
        for direction in directions:
            direction_outcomes[direction][outcome] += 1
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": {"read_only": True},
        "input": {
            "project_count": len(projects),
            "relation_observation_count": len(observations),
            "source_repository_relation_count": sum(
                item["current_relation_predicate"] == "source_repository"
                for item in observations
            ),
            "relation_predicate_counts": _counter(
                item["current_relation_predicate"] for item in observations
            ),
            "direction_counts": _counter(item["direction"] for item in observations),
            "method_counts": _counter(method_counts),
            "current_relation_classification_counts": _counter(
                item["current_relation_classification"] for item in observations
            ),
        },
        "case_catalog": case_results,
        "deterministic_policy": {
            "verdict_counts": _counter(deterministic_counts),
            "reason_counts": _counter(reason_counts),
        },
        "strict_projection": {
            "verdict_counts": _counter(strict_counts),
            "relation_assessment_required_count": int(strict_counts["NEEDS_ASSESSMENT"]),
            "not_reconstructible_count": int(strict_counts["NOT_RECONSTRUCTIBLE"]),
            "llm_executed": False,
        },
        "candidate_admission": {
            "current_candidate_claim_count": len(current_candidates),
            "current_candidate_outcome_counts": _counter(current_candidate_outcomes),
            "current_policy_projected_claim_count": len(current_policy_projection),
            "current_policy_projection_outcome_counts": _counter(current_policy_outcomes),
            "current_policy_projection_by_subject_adoption_source": {
                source: _counter(outcomes)
                for source, outcomes in sorted(subject_source_outcomes.items())
            },
            "current_policy_projection_by_direction": {
                direction: _counter(outcomes)
                for direction, outcomes in sorted(direction_outcomes.items())
            },
            "deterministic_supported_claim_count": len(deterministic_admissions),
            "strict_supported_claim_count": len(strict_admissions),
            "current_candidate_retained_by_strict_count": len(current_candidates & strict_admissions),
            "current_candidate_requiring_assessment_count": int(
                current_candidate_outcomes["NEEDS_ASSESSMENT"]
            ),
            "current_candidate_not_reconstructible_count": int(
                current_candidate_outcomes["NOT_RECONSTRUCTIBLE"]
            ),
            "current_candidate_not_supported_count": int(current_candidate_outcomes["NOT_SUPPORTED"]),
            "current_policy_projection_retained_by_strict_count": len(
                current_policy_projection & strict_admissions
            ),
            "current_policy_projection_requiring_assessment_count": int(
                current_policy_outcomes["NEEDS_ASSESSMENT"]
            ),
            "current_policy_projection_not_reconstructible_count": int(
                current_policy_outcomes["NOT_RECONSTRUCTIBLE"]
            ),
            "current_policy_projection_not_supported_count": int(
                current_policy_outcomes["NOT_SUPPORTED"]
            ),
        },
        "github_homepage_profiles": {
            "observation_count": len(homepage_rows),
            "current_unconditional_claim_count": len(homepage_current),
            "service_surface_gated_claim_count": len(homepage_surface),
            "same_project_and_service_strict_claim_count": len(homepage_strict),
        },
        "github_homepage_three_axis": {
            "confirmed_repository_to_unconfirmed_url_claim_count": len(homepage_admission_rows),
            "repository_project_attribution": {
                "verdict_counts": _counter(homepage_attribution_verdicts),
                "evidence_counts": _counter(homepage_attribution_evidence),
            },
            "repository_url_reference": {
                "verdict_counts": _counter(homepage_reference_verdicts),
                "evidence_counts": _counter(homepage_reference_evidence),
            },
            "target_role": {
                "role_counts": _counter(homepage_target_roles),
            },
            "candidate_projection": {
                "current_membership": _counter(homepage_current_membership_projection),
                "traceable_attribution": _counter(homepage_traceable_projection),
            },
            "review_sampling_only": {
                "name_alignment_counts": _counter(homepage_name_alignment),
                "name_alignment_is_policy_input": False,
            },
        },
        "github_homepage_storage_projection": {
            "current_source_repository_row_count": sum(
                item["current_relation_predicate"] == "source_repository"
                for item, _strict in homepage_rows
            ),
            "current_repository_homepage_row_count": sum(
                item["current_relation_predicate"] == "repository_homepage"
                for item, _strict in homepage_rows
            ),
            "current_distinct_homepage_claim_count": len(homepage_unique_rows),
            "current_duplicate_homepage_row_count": len(homepage_rows) - len(homepage_unique_rows),
            "current_row_classification_counts": _counter(homepage_row_classifications),
            "current_distinct_claim_classification_counts": _counter(
                homepage_current_relation_classifications
            ),
            "proposed_repository_homepage_claim_count": len(
                homepage_valid_direct_relations
            ),
            "proposed_relation_classification": "confirmed",
            "invalid_homepage_observation_only_count": len(homepage_unique_rows)
            - len(homepage_valid_direct_relations),
            "projected_source_repository_claims_replaced_count": len(
                homepage_unique_rows
            ),
            "projected_trust_edges_deferred_count": len(homepage_unique_rows),
            "database_schema_change_required": False,
            "proposed_predicate": "repository_homepage",
        },
        "github_homepage_website_projection": {
            "confirmed_repository_claim_count": len(homepage_confirmed_repository_rows),
            "disposition_counts": _counter(website_projection_counts),
            "project_counts_by_disposition": {
                disposition: len(project_keys)
                for disposition, project_keys in sorted(website_projection_projects.items())
            },
            "target_role_counts_by_disposition": {
                disposition: _counter(role_counts)
                for disposition, role_counts in sorted(website_projection_roles.items())
            },
            "auto_update_executed": False,
        },
        "github_search_homepage_attachment": github_search_attachment,
        "examples": examples,
        "review_gate": {
            "case_catalog_passed": case_results["failed_count"] == 0,
            "ready_for_user_review": case_results["failed_count"] == 0,
            "comparison_is_read_only": True,
            "production_policy_connected": True,
            "database_schema_changed": False,
        },
    }
    report["content_hash"] = _content_hash(report)
    return report


def compare_database_path(
    db_path: Path,
    *,
    case_catalog_path: Path,
    example_limit: int = 20,
) -> dict[str, Any]:
    target = db_path.expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"database does not exist: {target}")
    conn = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        report = compare_database(
            conn,
            case_catalog=load_case_catalog(case_catalog_path),
            example_limit=max(0, int(example_limit)),
        )
        conn.rollback()
    finally:
        conn.close()
    report["source"]["db_path"] = str(target)
    report["content_hash"] = _content_hash(report)
    return report
