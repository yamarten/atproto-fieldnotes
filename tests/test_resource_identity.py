from __future__ import annotations

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record, project_merge, relations, trust_graph
from project_registry_core.identity import identifiers
from project_registry_core.identity import url_alias
from project_registry_core.application import browser_entry, feedback
from project_registry_core.persistence import runtime_view


class ResourceIdentityTest(TempRegistryDbTestCase):
    def test_common_keys_keep_roles_out_of_resource_identity(self):
        from project_registry_core.identity import resource_identity

        self.assertEqual(resource_identity.key("website", "https://EXAMPLE.com/"),
                         resource_identity.key("site_url", "https://example.com"))
        self.assertEqual(resource_identity.key("site_url", "https://example.com"),
                         resource_identity.key("service_url", "https://example.com"))
        self.assertEqual(resource_identity.key("account", "did:plc:abc"), resource_identity.key("did", "did:plc:abc"))
        self.assertNotEqual(resource_identity.key("handle", "example.com"), resource_identity.key("domain", "example.com"))
        self.assertNotEqual(resource_identity.key("did", "did:plc:abc"), resource_identity.key("handle", "example.com"))
        self.assertNotEqual(resource_identity.key("nsid", "com.example.post"), resource_identity.key("collection", "com.example.post"))

    def test_same_repository_has_one_identity_in_storage_graph_merge_and_guards(self):
        url = "https://github.com/Example/App/tree/main/src"
        project = self.make_project("proj:identity", "repo_url", url)
        project["project_repository_urls"] = [url]
        project["blocked_identifiers"] = [{"type": "repo_url", "value": url}]
        for guard in (project_record.identifier_is_blocked, relations.identifier_is_guarded):
            self.assertTrue(guard(project, "repo_url", "https://github.com/example/app"))
        self.assertEqual(trust_graph.node("repo_url", url), {"kind": "repo_url", "value": "https://github.com/example/app"})
        project["blocked_identifiers"] = []
        other = self.make_project("proj:other", "nsid", "com.other.post")
        other["candidate_project_repository_urls"] = ["https://github.com/example/app"]
        matches = project_merge.candidate_confirmed_identifier_matches(
            project_merge.component_identifier_profile(project), project_merge.component_identifier_profile(other))
        self.assertEqual([(m["kind"], m["value"], m["role"]) for m in matches],
                         [("repo_url", "https://github.com/example/app", "project_repository")])
        with registry.open_db() as conn:
            project_record.save_project(conn, project)
            self.assertEqual(project_record.find_project_key_by_identifier(conn, "repo_url", "https://github.com/EXAMPLE/APP"), "proj:identity")
            fetched = project_record.fetch_project(conn, "proj:identity")
            self.assertEqual(fetched["project_repository_urls"], ["https://github.com/example/app"])
            # An older DB row can still have original provider casing.
            conn.execute("UPDATE project_identifiers SET value='https://github.com/Example/App' WHERE kind='repo_url'")
            self.assertEqual(project_record.find_project_key_by_identifier(conn, "repo_url", "https://github.com/example/app"), "proj:identity")

    def test_nsid_did_and_non_github_paths_are_not_case_folded_by_guards(self):
        for kind, value, other in (
            ("nsid", "com.example.fooBar", "com.example.foobar"),
            ("did", "did:web:example.com:AbC", "did:web:example.com:abc"),
            ("repo_url", "https://gitlab.com/example/AbC", "https://gitlab.com/example/abc"),
        ):
            with self.subTest(kind=kind):
                project = {"blocked_identifiers": [{"type": kind, "value": value}]}
                for guard in (project_record.identifier_is_blocked, relations.identifier_is_guarded):
                    self.assertTrue(guard(project, kind, value))
                    self.assertFalse(guard(project, kind, other))
        self.assertEqual(identifiers.normalize_atproto_account({"handle": "did:web:example.com:ABC", "did": "did:web:example.com:abc"}),
                         {"did": "did:web:example.com:abc"})

    def test_url_resource_components_survive_candidate_storage_and_roundtrip(self):
        urls = ["https://example.com/app?id=1", "https://example.com/app?id=2",
                "https://example.com/app/", "https://example.com/app", "https://example.com/app#view"]
        project = self.make_project("proj:urls")
        project["service_url_candidates"] = urls
        with registry.open_db() as conn:
            project_record.save_project(conn, project)
            fetched = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(set(fetched["service_url_candidates"]), set(urls))
            project_record.save_project(conn, fetched)
            self.assertEqual({row[0] for row in conn.execute("SELECT value FROM project_identifiers WHERE kind='service_url'")}, set(urls))
        self.assertEqual(identifiers.normalize_url("https://EXAMPLE.com/"), "https://example.com")
        self.assertEqual(identifiers.normalize_url("https://EXAMPLE.com:443/"), "https://example.com")
        self.assertEqual(identifiers.normalize_url("http://example.com:80/"), "http://example.com")
        self.assertEqual(identifiers.normalize_url("https://example.com:8443/"), "https://example.com:8443")
        self.assertEqual(identifiers.normalize_url("at://did:web:example.com:ABC/com.example.post/self"),
                         "at://did:web:example.com:ABC/com.example.post/self")
        for guard in (project_record.identifier_is_blocked, relations.identifier_is_guarded):
            self.assertFalse(guard({"blocked_identifiers": [{"type": "service_url", "value": urls[0]}]}, "service_url", urls[1]))
            self.assertTrue(guard({"blocked_identifiers": [{"type": "service_url", "value": "https://example.com/app"}]}, "service_url", "https://example.com/app/child"))

    def test_relation_aliases_keep_all_original_reference_urls(self):
        urls = ["https://github.com/Example/App/blob/main/a.py", "https://github.com/example/app/tree/main/src"]
        records = [relations.relation_record(source="readme", classification="candidate", observed_at="2026-10-16",
            subject_type="repo", subject_value="https://github.com/reader/app", predicate="mentions_collection",
            object_type="repo", object_value=url, locator="README", method="readme_link").to_dict() for url in urls]
        normalized = relations.normalize_relation_records(records)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["relation"]["object"], {"type": "repo", "value": "https://github.com/example/app"})
        self.assertEqual({item["value"] for item in normalized[0]["context"]["original_endpoints"]}, set(urls))
        self.assertEqual(relations.normalize_relation_records(normalized), normalized)

    def test_latest_repository_observation_keeps_references_without_stale_context(self):
        records = [relations.relation_record(source="page", classification=classification, observed_at=date,
            subject_type="website", subject_value="https://example.com", predicate="project_repository",
            object_type="repo", object_value=url, locator="https://example.com", method="link", context=context).to_dict()
            for classification, date, url, context in (
                ("confirmed", "2026-10-15", "https://github.com/Example/App/tree/main", {"old_claim": True}),
                ("candidate", "2026-10-16", "https://github.com/example/app/blob/main/a.py", {"current_claim": True}),
            )]
        for values in (records, records[::-1]):
            current = relations.normalize_relation_records(values)
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]["classification"], "candidate")
            self.assertNotIn("old_claim", current[0]["context"])
            self.assertTrue(current[0]["context"]["current_claim"])
            self.assertEqual(len(current[0]["context"]["original_endpoints"]), 2)

    def test_observed_url_alias_does_not_assert_host_wide_equivalence(self):
        project = {"relation_evidence": relations.normalize_relation_records([relations.relation_record(source="page", classification="confirmed", observed_at="2026-10-16",
            subject_type="website", subject_value="https://example.com/old?id=1", predicate="redirects_to",
            object_type="website", object_value="https://www.example.com/new?id=2").to_dict()])}
        self.assertEqual(url_alias.canonical_alias_url(project, "https://example.com/old?id=1"), "https://www.example.com/new?id=2")
        self.assertEqual(url_alias.canonical_alias_url(project, "https://example.com/other?id=1"), "https://example.com/other?id=1")
        self.assertEqual(url_alias.canonical_alias_url(project, "https://example.com"), "https://example.com")

    def test_browser_entry_matches_query_surfaces_and_legacy_repository_case(self):
        with registry.open_db() as conn:
            for index in (1, 2):
                project = self.make_project(f"proj:query{index}")
                project["service_urls"] = [f"https://example.com/app?id={index}"]
                project_record.save_project(conn, project)
            result = browser_entry.resolve_page(conn, "https://EXAMPLE.com/app?id=1")
            self.assertEqual(result["project_key"], "proj:query1")
            self.assertEqual([item["project_key"] for item in result["items"]], ["proj:query1"])
            feedback.save_feedback(conn, project_key="proj:query1", kind="service-usage", service_url="https://example.com/app?id=1",
                nsid="com.example.post", reason="confirmed", source_url="https://example.com/proof?token=secret#part")
            usage = project_record.fetch_project(conn, "proj:query1")["service_usage_decisions"][-1]
            self.assertEqual(usage["service_url"], "https://example.com/app?id=1")
            self.assertEqual(usage["source_url"], "https://example.com/proof")
            project = self.make_project("proj:repo", "repo_url", "https://github.com/Example/App")
            project["project_repository_urls"] = ["https://github.com/Example/App"]
            project_record.save_project(conn, project)
            conn.execute("UPDATE project_identifiers SET value='https://github.com/Example/App' WHERE kind='repo_url'")
            result = browser_entry.resolve_page(conn, "https://github.com/example/app/tree/main")
            self.assertEqual(result["project_key"], "proj:repo")

    def test_graph_paths_connect_repository_case_aliases(self):
        project = {"trust_graph": {"edges": [{
            "subject": {"kind": "repo_url", "value": "https://github.com/Example/App"},
            "predicate": "service_canonical", "object": {"kind": "service_url", "value": "https://example.com/app?id=1"},
            "classification": "confirmed", "eligible_for_auto_confirm": True,
        }]}}
        paths = trust_graph.directional_paths(project,
            candidate={"kind": "service_url", "value": "https://example.com/app?id=1"},
            anchors=[{"kind": "repo_url", "value": "https://github.com/example/app"}])
        self.assertEqual(len(paths), 1)
        self.assertEqual(len(paths[0]["anchor_to_candidate"]), 1)
        self.assertEqual(trust_graph.directional_paths(project,
            candidate={"kind": "service_url", "value": "https://example.com/app?id=2"},
            anchors=[{"kind": "repo_url", "value": "https://github.com/example/app"}]), [])

    def test_same_did_keeps_distinct_roles_without_new_adoption(self):
        project = self.make_project("proj:roles")
        project.update(developer_accounts=[{"did": "did:plc:abc"}], brand_did="did:plc:abc")
        identifiers = [item for item in runtime_view.project_identifiers(project) if item.kind == "did"]
        self.assertEqual({(item.value, item.role) for item in identifiers}, {("did:plc:abc", "developer_did"), ("did:plc:abc", "brand_did")})

    def test_query_sync_replaces_wrong_collection_witness_and_keeps_record_type(self):
        project = self.make_project("proj:schemas")
        for nsid, schema_type in (("com.example.getThing", "query"), ("com.example.post", "record")):
            project.setdefault("lexicon_resolution_entries", []).append({
                "resolved_nsid": nsid, "repo_did": "did:plc:abc", "authority_domain": "example.com",
                "record_uri": f"at://did:plc:abc/com.atproto.lexicon.schema/{nsid}",
                "publication_validation": {"schema_type": schema_type},
            })
        project_record.record_trust_edge(project, "did", "did:plc:abc", "did_publishes_schema", "collection", "com.example.getThing",
            source="lexicon_resolution:com.example.getThing", method="repo_record")
        registry.sync_trust_edges_from_project_identity(project)
        edges = trust_graph.stored_graph(project)["edges"]
        for value, kind in (("com.example.getThing", "nsid"), ("com.example.post", "collection")):
            nodes = [edge[side] for edge in edges for side in ("subject", "object") if edge[side]["value"] == value]
            self.assertTrue(nodes)
            self.assertEqual({node["kind"] for node in nodes}, {kind})
        registry.sync_trust_edges_from_project_identity(project)
        self.assertEqual(trust_graph.stored_graph(project)["edges"], edges)
