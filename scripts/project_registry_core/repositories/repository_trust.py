"""Current page/repository graph projection. Stored graph mirrors are not inputs."""
from __future__ import annotations

from typing import Any, Mapping

from .. import collector_relation_policy
from ..identity import identifiers
from .. import relations
from . import repo_urls


PREDICATES = {"service_links_repo", "repo_links_service"}


def is_repository_edge(edge: Mapping[str, Any]) -> bool:
    return edge.get("predicate") in PREDICATES and {
        (edge.get("subject") or {}).get("kind"),
        (edge.get("object") or {}).get("kind"),
    } == {"service_url", "repo_url"}


def _latest_observations(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, ...], dict[str, Any]] = {}
    for edge in edges:
        key = (edge["subject"]["kind"], edge["subject"]["value"], edge["predicate"],
               edge["object"]["value"], edge["source"], edge["locator"], edge["method"])
        if key not in latest or edge["observed_at"] >= latest[key]["observed_at"]:
            latest[key] = edge
    return list(latest.values())


def project_edges(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = relations.normalize_relation_records(project.get("relation_evidence", []))
    output = []
    homepages = []
    for item in records:
        relation = item["relation"]
        subject, obj = relation["subject"], relation["object"]
        forward = subject["type"] in {"website", "service"} and obj["type"] == "repo"
        reverse = subject["type"] == "repo" and obj["type"] in {"website", "service"}
        if not (forward or reverse):
            continue
        homepage = relation["predicate"] == "repository_homepage" and reverse
        if relation["predicate"] != "project_repository" and not homepage:
            continue
        service = identifiers.normalize_url((subject if forward else obj)["value"])
        repo = repo_urls.canonical_repo_url((obj if forward else subject)["value"])
        if not service or not repo:
            continue
        edge = {
            "subject": {"kind": "service_url" if forward else "repo_url", "value": service if forward else repo},
            "predicate": "service_links_repo" if forward else "repo_links_service",
            "object": {"kind": "repo_url" if forward else "service_url", "value": repo if forward else service},
            "source": item["source"], "locator": relation.get("locator", ""),
            "method": relation.get("method", ""), "classification": item["classification"],
            "eligible_for_auto_confirm": item["classification"] == "confirmed",
            "observed_at": item["observed_at"],
            "context": {**item["context"], "from_relation": True},
        }
        if homepage:
            homepages.append(edge)
        else:
            output.append(edge)
    # Entity aliases and repository URL canonicalization can collapse raw relation keys.
    # They are still the same observation, so an older confirmed result must not win.
    output = _latest_observations(output)
    for edge in _latest_observations(homepages):
        if edge["classification"] != "confirmed" or not collector_relation_policy.repository_homepage_service_candidate(edge["object"]["value"]):
            continue
        forward = next((item for item in output if item["predicate"] == "service_links_repo"
                        and item["eligible_for_auto_confirm"]
                        and item["subject"] == edge["object"] and item["object"] == edge["subject"]), None)
        if forward:
            edge["method"] = "repository_homepage_pair"
            edge["context"]["paired_service_relation_source"] = forward["source"]
            output.append(edge)
    return output


def record_observation(project: dict[str, Any], edge: Mapping[str, Any]) -> None:
    """Store one interpreted observation, replacing the same source/locator/method."""
    def entity(value: Mapping[str, Any]) -> dict[str, str]:
        return {"type": "repo" if value["kind"] == "repo_url" else "website", "value": value["value"]}
    record = {
        "relation": {"subject": entity(edge["subject"]), "predicate": "project_repository",
                     "object": entity(edge["object"]), "locator": edge.get("locator", ""),
                     "method": edge.get("method", "")},
        "classification": edge.get("classification", "candidate"),
        "source": edge.get("source", ""), "observed_at": edge.get("observed_at", ""),
        "context": dict(edge.get("context") or {}),
    }
    project["relation_evidence"] = relations.normalize_relation_records([
        *project.get("relation_evidence", []), record,
    ])
