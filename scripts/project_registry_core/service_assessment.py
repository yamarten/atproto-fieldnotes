"""Describe the observed access surface without claiming successful service use."""
from __future__ import annotations

import datetime as dt
from typing import Any

from . import service


def surface(value: dict[str, Any]) -> str:
    if value.get("fetch_error") or value.get("rendered_error_detected") or int(value.get("status_code") or 0) >= 400:
        return "failed"
    evidence = set(value.get("service_status_evidence") or [])
    if value.get("lane") == "browser":
        observed = service.browser_service_observation(value)
        if not observed["positive_surface"]:
            return "unconfirmed"
        evidence.update(observed["service_status_evidence"])
    if evidence & {"browser_public_app_surface_detected"}:
        return "public"
    if evidence & {"login_surface_detected", "browser_login_surface_reached", "browser_password_form_detected",
                   "browser_identity_form_detected", "browser_oauth_entry_detected"}:
        return "login"
    if "app_shell_detected" in evidence:
        return "app_shell"
    if evidence & {"coming_soon_detected", "invite_text_detected", "beta_text_detected"}:
        return "announcement"
    if "docs_only_surface_detected" in evidence:
        return "documentation"
    return "unconfirmed"


def projection(project: dict[str, Any]) -> dict[str, Any]:
    urls = {str(url).rstrip("/") for url in project.get("service_urls", [])}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in project.get("service_verification_results", []):
        url = str(item.get("requested_url") or "").rstrip("/")
        lane = str(item.get("lane") or "static")
        if url not in urls or lane not in {"static", "deep", "authority", "browser"}:
            continue
        if lane != "browser" and not service.service_probe_represents_service_identity(url, lane):
            continue
        grouped.setdefault(url, []).append(item)
    observations = []
    for url, values in grouped.items():
        latest = max(values, key=lambda v: (
            service.observation_time(v) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            {"authority": 0, "static": 1, "deep": 2, "browser": 3}.get(v.get("lane"), 1),
        ))
        observed_surface = "unconfirmed" if latest.get("supports_project") is False else surface(latest)
        observations.append({"url": url, "surface": observed_surface,
                             "observed_at": str(latest.get("observed_at") or ""),
                             "method": str(latest.get("lane") or "static"),
                             "evidence": list(latest.get("service_status_evidence") or [])})
    if not observations and project.get("service_status_evidence"):
        observations.append({"url": "", "surface": surface(project), "observed_at": "",
                             "method": "saved_evaluation", "evidence": list(project["service_status_evidence"])})
    order = {"public": 8, "login": 7, "app_shell": 6, "announcement": 5,
             "documentation": 4, "unconfirmed": 3, "failed": 2, "not_observed": 1}
    selected = max(observations, key=lambda o: order[o["surface"]], default={"surface": "not_observed"})
    return {"surface": selected["surface"], "entrypoint_observed": selected["surface"] in {"login", "public"},
            "publication": str(project.get("service_status") or "unknown"),
            "usage_tested": False, "observations": observations}
