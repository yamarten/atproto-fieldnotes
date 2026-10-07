"""Tenant split proposals for explicit source configuration maintenance."""
from __future__ import annotations

import shlex
import sqlite3
import urllib.parse
from typing import Any

from .. import project_record, project_merge, registry as core
from ..identity import domain_policy as domain_policy_core, identifiers as identifier_core



def project_split_identifiers_by_tenant(project: dict[str, Any], suffix: str) -> dict[str, dict[str, list[str]]]:
    groups: dict[str, dict[str, list[str]]] = {}

    def add(tenant: str, identifier_type: str, value: str) -> None:
        if not tenant or not value:
            return
        group = groups.setdefault(
            tenant,
            {"service_urls": [], "nsids": [], "authority_domains": []},
        )
        field = {
            "service_url": "service_urls",
            "nsid": "nsids",
            "authority_domain": "authority_domains",
        }[identifier_type]
        group[field] = project_record.add_unique(group[field], value)

    service_urls = project_record.unique_list(
        list(project.get("service_urls", []))
        + project_record.manual_seed_values(project, "service_url")
    )
    for service_url in service_urls:
        normalized = identifier_core.normalize_url(str(service_url or ""))
        host = urllib.parse.urlparse(normalized).netloc.lower()
        add(domain_policy_core.tenant_identity_for_suffix(host, suffix), "service_url", normalized)

    nsids = project_record.unique_list(
        list(project.get("input_nsids", []))
        + list(project.get("resolved_nsids", []))
        + project_record.manual_seed_values(project, "nsid")
    )
    for nsid in nsids:
        normalized = identifier_core.normalize_nsid(str(nsid or ""))
        authority = identifier_core.derive_authority_domain_from_namespace(normalized)
        add(domain_policy_core.tenant_identity_for_suffix(authority, suffix), "nsid", normalized)

    for authority in project.get("authority_domains", []):
        normalized = str(authority or "").strip().lower().rstrip(".")
        add(domain_policy_core.tenant_identity_for_suffix(normalized, suffix), "authority_domain", normalized)
    return groups


def virtual_tenant_project(project: dict[str, Any], tenant: str, group: dict[str, list[str]]) -> dict[str, Any]:
    virtual = project_record.default_project_record(f"virtual:{project['project_key']}:{tenant}", "service_url", "")
    virtual["name"] = tenant
    virtual["service_urls"] = list(group.get("service_urls", []))
    virtual["input_nsids"] = list(group.get("nsids", []))
    virtual["resolved_nsids"] = list(group.get("nsids", []))
    virtual["authority_domains"] = list(group.get("authority_domains", []))
    virtual["recorded_lexicon_namespaces"] = project_record.unique_list(
        [
            project_record.namespace_from_collection_nsid(nsid)
            for nsid in group.get("nsids", [])
            if project_record.namespace_from_collection_nsid(nsid)
        ]
    )
    return core.finalize_project_shape(virtual)


def split_commands_for_tenant(project_key: str, group: dict[str, list[str]]) -> list[str]:
    commands: list[str] = []
    for identifier_type, values in (
        ("service_url", group.get("service_urls", [])),
        ("nsid", group.get("nsids", [])),
        ("domain", group.get("authority_domains", [])),
    ):
        for value in values:
            commands.append(
                "project-registry split-project "
                f"--project-key {shlex.quote(project_key)} "
                f"--identifier-type {identifier_type} "
                f"--value {shlex.quote(str(value))}"
            )
    return commands


def build_source_config_split_suggestions(conn: sqlite3.Connection, suffixes: list[str] | None = None) -> list[dict[str, Any]]:
    normalized_suffixes = sorted(
        {domain_policy_core.normalize_multi_tenant_suffix(item) for item in (suffixes or sorted(domain_policy_core.configured_multi_tenant_suffixes()))}
    )
    projects = [project_record.ensure_project_shape(project) for project in project_record.list_projects(conn)]
    suggestions: list[dict[str, Any]] = []
    for project in projects:
        for suffix in normalized_suffixes:
            groups = project_split_identifiers_by_tenant(project, suffix)
            if len(groups) < 2:
                continue
            tenant_groups: list[dict[str, Any]] = []
            for tenant_group_index, (tenant, group) in enumerate(sorted(groups.items()), start=1):
                virtual = virtual_tenant_project(project, tenant, group)
                comparison_projects = [
                    item for item in projects if item.get("project_key") != project.get("project_key")
                ] + [virtual]
                candidates = project_merge.compute_duplicate_candidates(conn, virtual, projects=comparison_projects)
                tenant_groups.append(
                    {
                        "tenant_group_index": tenant_group_index,
                        "tenant": tenant,
                        "identifiers": group,
                        "merge_candidates_after_split": candidates[:10],
                        "suggested_split_commands": split_commands_for_tenant(str(project["project_key"]), group),
                    }
                )
            suggestions.append(
                {
                    "source_project_key": project["project_key"],
                    "source_name": project.get("name", ""),
                    "trigger": f"custom_multi_tenant_suffix:{suffix}",
                    "tenant_groups": tenant_groups,
                    "shared_or_ambiguous_identifiers": {
                        "repo_urls": project_record.unique_list(
                            list(project.get("project_repository_urls", []))
                            + list(project.get("candidate_project_repository_urls", []))
                        ),
                    },
                    "requires_manual_confirmation": True,
                }
            )
    return suggestions
