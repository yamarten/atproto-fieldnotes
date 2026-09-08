"""Evaluate homepage attribution separately for each proposed project role."""
from typing import Any

POLICY_VERSION = "repository-homepage-role-v3"


def role_assessments(facts: dict[str, Any]) -> dict[str, dict[str, str]]:
    """A repository placement is not a claim about who operates a service."""
    if facts.get("guarded") or facts.get("collision_projects"):
        reason = "active_guard" if facts.get("guarded") else "shared_homepage_requires_review"
        return {role: {"status": "CONFLICTING", "reason": reason} for role in ("project_site", "service")}
    if not facts.get("successful_page") or not facts.get("page_role_allowed"):
        return {role: {"status": "NOT_EVALUATED", "reason": "page_not_available_or_out_of_scope"}
                for role in ("project_site", "service")}
    if facts.get("existing_role"):
        return {role: {"status": "NOT_EVALUATED", "reason": "existing_component_reused"}
                for role in ("project_site", "service")}
    reference = facts.get("adopted_repository") and facts.get("observed_return_reference")
    site = {"status": "INSUFFICIENT", "reason": "missing_homepage_return_reference"}
    service = {"status": "INSUFFICIENT", "reason": "missing_operation_attribution"}
    if reference:
        site = {"status": "INSUFFICIENT", "reason": "homepage_reference_supports_candidate_not_primary_site"}
    if reference and facts.get("operation_relations"):
        service = ({"status": "SUPPORTED", "reason": "confirmed_operation_and_visible_surface"}
                   if facts.get("visible_surface") else
                   {"status": "INSUFFICIENT", "reason": "operation_confirmed_surface_unverified"})
    return {"project_site": site, "service": service}


def evaluate(facts: dict[str, Any]) -> str:
    if facts.get("guarded"):
        return "REVIEW_GUARD"
    if facts.get("collision_projects"):
        return "REVIEW_COLLISION"
    if not facts.get("adopted_repository"):
        return "REVIEW_ATTRIBUTION"
    if not facts.get("successful_page"):
        return "REVIEW_FETCH"
    if not facts.get("page_role_allowed"):
        return "REVIEW_RESOURCE"
    if facts.get("existing_role") == "service":
        return "REUSE_SERVICE"
    if facts.get("existing_role") == "project_site":
        return "REUSE_PROJECT_SITE"
    if not facts.get("observed_return_reference"):
        return "REVIEW_ATTRIBUTION"
    if role_assessments(facts)["service"]["status"] == "SUPPORTED":
        return "CONFIRM_SERVICE"
    # A proposed role is not an adopted component or a trust anchor.
    return "CANDIDATE_PROJECT_SITE"
