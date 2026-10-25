"""Role-scoped OAuth declaration support. No network or implicit project roots."""
from __future__ import annotations

import urllib.parse
from typing import Any

from . import collection_trust, oauth_metadata, project_record, relations, service, service_discovery, trust_graph
from .persistence import runtime_view
from .identity import identifiers

POLICY_VERSION = "service-oauth-publication-v1"
ADOPTION_SOURCE = "trust_policy:service_oauth_publication"


def adopted_urls(project: dict[str, Any]) -> set[str]:
    return {item["value"] for item in project.get("confirmed_identifiers", [])
            if item.get("type") == "service_url" and item.get("source") == ADOPTION_SOURCE
            and not item.get("cleared_at")}


def browser_support(project: dict[str, Any], url: str) -> dict[str, Any]:
    observations = [value for value in project.get("service_verification_results", [])
                    if value.get("requested_url") == url and value.get("lane") in
                    {"browser_discovery", "browser", "repository_homepage_browser"}]
    if not observations:
        return {}
    latest = max(observations, key=lambda value: oauth_metadata._time(value.get("observed_at")))
    recorded_semantics = latest.get("hypothesis", {}).get("page_semantics", [])
    for probe in project.get("service_verification_results", []):
        if (probe.get("requested_url") == url and probe.get("lane") in {"static", "authority", "repository_homepage"}
                and oauth_metadata._time(probe.get("observed_at")) > oauth_metadata._time(latest.get("observed_at"))
                and (probe.get("fetch_error") or not 200 <= int(probe.get("status_code") or 0) < 300
                     or identifiers.normalize_url(str(probe.get("final_url") or url)) != url
                     or (recorded_semantics and probe.get("content_sha256")
                         and probe["content_sha256"] != recorded_semantics[0]))):
            return {}
    age = (oauth_metadata._time(project_record.iso_now()) - oauth_metadata._time(latest.get("observed_at"))).total_seconds()
    facts = service.browser_surface_assessment(latest)
    return facts if (0 <= age <= 30 * 86400 and identifiers.normalize_url(facts["url"]) == url
                     and facts["successful_page"] and facts["positive_surface"]
                     and not facts["rendered_errors"]) else {}


def evaluate(project: dict[str, Any], url: str, *, require_browser: bool = True) -> dict[str, Any]:
    candidate = trust_graph.node("service_url", url)
    unsupported = {"candidate": candidate, "auto_confirm": False, "policy_version": POLICY_VERSION,
                   "reason": "no_current_oauth_publication_support"}
    if not service_discovery.target_url(url) or relations.identifier_is_guarded(project, "service_url", url):
        return {**unsupported, "reason": "guarded_or_nonservice_url"}
    browser = browser_support(project, url)
    if require_browser and not browser:
        return {**unsupported, "reason": "no_visible_service_surface"}
    prepared = collection_trust.PublisherSupport(project)
    host = urllib.parse.urlsplit(url).hostname
    for metadata in oauth_metadata.usable_observations(project, fresh=True):
        if oauth_metadata.web_surface(metadata) != url:
            continue
        requested = {nsid for permission in metadata["analysis"]["permissions"] if permission.get("kind") == "repo"
                     for nsid in permission.get("collection", []) if "*" not in nsid}
        for nsid in sorted(requested):
            claim = prepared.claims.get(nsid)
            publication = prepared.publications.get(nsid, {})
            age = (oauth_metadata._time(project_record.iso_now()) - oauth_metadata._time(publication.get("resolved_at"))).total_seconds()
            if (not claim or claim.classification != "confirmed" or relations.identifier_is_guarded(project, "nsid", nsid)
                    or not 0 <= age <= 7 * 86400
                    or publication.get("authority_domain") != host
                    or publication.get("publication_validation", {}).get("schema_type") != "record"):
                continue
            roots = {root: rows for root, rows in prepared.roots.items()
                     if prepared.publications[root]["authority_domain"] == host
                     and prepared.publications[root]["publication_validation"]["schema_type"] == "record"
                     and prepared.publications[root]["repo_did"] == publication["repo_did"]
                     and 0 <= (oauth_metadata._time(project_record.iso_now()) - oauth_metadata._time(
                         prepared.publications[root].get("resolved_at"))).total_seconds() <= 7 * 86400}
            if not roots:
                continue
            domain = trust_graph.node("domain", host)
            client = trust_graph.node("oauth_client", metadata["metadata_url"])
            collection = trust_graph.node("collection", nsid)
            edges = []
            def edge(subject, predicate, obj, locator, observed_at):
                edges.append({"subject": subject, "predicate": predicate, "object": obj,
                              "source": POLICY_VERSION, "locator": locator, "observed_at": observed_at,
                              "method": POLICY_VERSION, "classification": "confirmed", "eligible_for_auto_confirm": True})
            anchors = []
            for root in roots:
                pub = prepared.publications[root]
                kind = "collection" if pub["publication_validation"]["schema_type"] == "record" else "nsid"
                anchor = trust_graph.node(kind, root)
                anchors.append(anchor)
                edge(anchor, "collection_authority_domain", domain, pub["record_uri"], pub["resolved_at"])
                edge(domain, "domain_publishes_lexicon_record", anchor, pub["record_uri"], pub["resolved_at"])
            edge(collection, "collection_authority_domain", domain, publication["record_uri"], publication["resolved_at"])
            edge(domain, "domain_exact_service", candidate, url, metadata["observed_at"])
            edge(candidate, "service_bound_oauth_client", client, metadata["metadata_url"], metadata["observed_at"])
            edge(client, "requests_permission", collection, metadata["metadata_url"], metadata["observed_at"])
            # There is deliberately no service -> domain shortcut in this scope.
            result = trust_graph.evaluate_auto_confirm(project, candidate=candidate, anchors=anchors,
                allowed_node_kinds={"service_url", "oauth_client", "domain", "collection", "nsid"},
                allowed_predicates={edge["predicate"] for edge in edges}, scoped_edges=edges)
            result.update(policy_version=POLICY_VERSION, metadata_url=metadata["metadata_url"],
                          observed_at=metadata["observed_at"], collection=nsid, browser=browser)
            if result.get("auto_confirm"):
                result.update(reason="service_oauth_publication", anchor_adoptions=roots[result["anchor"]["value"]])
                return result
    return unsupported


def database_guards(conn, project: dict[str, Any], url: str) -> dict[str, Any]:
    collisions = sorted({row[0] for row in conn.execute(
        "SELECT project_key FROM project_identifiers WHERE kind IN ('service_url','site_url') "
        "AND value=? AND classification='confirmed' AND project_key!=?", (url, project["project_key"]))})
    noncurrent = bool(conn.execute("SELECT 1 FROM project_noncurrent_identifiers WHERE project_key=? "
        "AND kind IN ('service_url','site_url') AND value=? LIMIT 1", (project["project_key"], url)).fetchone())
    support = evaluate(project, url, require_browser=False)
    for nsid in {support.get("collection"), support.get("anchor", {}).get("value")} - {None}:
        collisions.extend(row[0] for row in conn.execute(
            "SELECT project_key FROM project_identifiers WHERE kind='nsid' AND value=? "
            "AND role='owned_nsid' AND classification='confirmed' AND project_key!=?", (nsid, project["project_key"])))
    from .registry import competing_repository_homepages
    collisions.extend(item["project_key"] for item in competing_repository_homepages(conn, project["project_key"], [url]))
    return {"collision_projects": sorted(set(collisions)), "noncurrent": noncurrent}


def assert_new_adoptions_current(conn, project: dict[str, Any]) -> None:
    """Repeat current-state checks within the save transaction, including replay."""
    for url in adopted_urls(project):
        existing = conn.execute("SELECT 1 FROM project_identifier_adoptions WHERE project_key=? AND kind='service_url' "
            "AND value=? AND source=? AND cleared_at='' LIMIT 1", (project["project_key"], url, ADOPTION_SOURCE)).fetchone()
        guards = database_guards(conn, project, url)
        supported = not any(guards.values()) and evaluate(project, url)["auto_confirm"]
        if not existing and not supported:
            raise ValueError(f"OAuth service adoption no longer supported: {url}")
        if existing:
            reason = "" if supported else "service_oauth_support_requires_review"
            conn.execute("UPDATE project_identifier_adoptions SET review_required=?,review_reason=? "
                         "WHERE project_key=? AND kind='service_url' AND value=? AND source=? AND cleared_at=''",
                         (int(not supported), reason, project["project_key"], url, ADOPTION_SOURCE))
            for row in project.get(runtime_view.RUNTIME_ADOPTION_ROWS, []):
                if row.get("kind") == "service_url" and row.get("value") == url and row.get("source") == ADOPTION_SOURCE:
                    row.update(review_required=not supported, review_reason=reason)
