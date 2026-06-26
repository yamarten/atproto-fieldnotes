from __future__ import annotations

import unittest

from helpers import registry, TempRegistryDbTestCase
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord
from project_registry_core import trust_graph


class TrustGraphCoreTest(unittest.TestCase):
    def test_normalizes_and_deduplicates_edges(self) -> None:
        project: dict = {}

        trust_graph.upsert_edge(
            project,
            subject={"kind": "service_url", "value": "https://app.example.com"},
            predicate="service_links_repo",
            object={"kind": "repo_url", "value": "https://github.com/example/app"},
            source="service_probe:https://app.example.com",
            locator="https://app.example.com",
            method="html_link",
            classification="candidate",
            observed_at="2026-01-01T00:00:00+00:00",
        )
        trust_graph.upsert_edge(
            project,
            subject={"kind": "service_url", "value": "https://app.example.com"},
            predicate="service_links_repo",
            object={"kind": "repo_url", "value": "https://github.com/example/app"},
            source="service_probe:https://app.example.com",
            locator="https://app.example.com",
            method="html_link",
            classification="confirmed",
            eligible_for_auto_confirm=True,
            observed_at="2026-01-02T00:00:00+00:00",
        )

        edges = trust_graph.graph(project)["edges"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["classification"], "confirmed")
        self.assertTrue(edges[0]["eligible_for_auto_confirm"])
        self.assertEqual(edges[0]["observed_at"], "2026-01-02T00:00:00+00:00")

    def test_one_way_edge_does_not_auto_confirm(self) -> None:
        project: dict = {}
        trust_graph.upsert_edge(
            project,
            subject={"kind": "service_url", "value": "https://app.example.com"},
            predicate="service_links_repo",
            object={"kind": "repo_url", "value": "https://github.com/example/app"},
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        evaluation = trust_graph.evaluate_auto_confirm(
            project,
            candidate={"kind": "service_url", "value": "https://app.example.com"},
            anchors=[{"kind": "repo_url", "value": "https://github.com/example/app"}],
        )

        self.assertFalse(evaluation["auto_confirm"])
        self.assertEqual(evaluation["reason"], "no_mutual_trust_path")

    def test_mutual_edges_auto_confirm_url_candidate(self) -> None:
        project: dict = {}
        trust_graph.upsert_edge(
            project,
            subject={"kind": "service_url", "value": "https://app.example.com"},
            predicate="service_links_repo",
            object={"kind": "repo_url", "value": "https://github.com/example/app"},
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        trust_graph.upsert_edge(
            project,
            subject={"kind": "repo_url", "value": "https://github.com/example/app"},
            predicate="repo_links_service",
            object={"kind": "service_url", "value": "https://app.example.com"},
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        evaluation = trust_graph.evaluate_auto_confirm(
            project,
            candidate={"kind": "service_url", "value": "https://app.example.com"},
            anchors=[{"kind": "repo_url", "value": "https://github.com/example/app"}],
        )

        self.assertTrue(evaluation["auto_confirm"])
        self.assertEqual(evaluation["reason"], "mutual_trust_path")

    def test_non_url_identifier_is_not_in_v1_auto_confirm_scope(self) -> None:
        project: dict = {}

        evaluation = trust_graph.evaluate_auto_confirm(
            project,
            candidate={"kind": "collection", "value": "com.example.post"},
            anchors=[{"kind": "did", "value": "did:plc:example"}],
        )

        self.assertFalse(evaluation["auto_confirm"])
        self.assertEqual(evaluation["reason"], "kind_not_in_v1_scope")


class RegistryTrustGraphIntegrationTest(unittest.TestCase):
    def test_blocked_service_candidate_does_not_auto_confirm(self) -> None:
        project = registry.default_project_record("proj:blockedtrust", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
        project["blocked_identifiers"] = [{"type": "service_url", "value": "https://app.example.com"}]
        registry.record_trust_edge(
            project,
            "repo_url",
            "https://github.com/example/app",
            "repo_links_service",
            "service_url",
            "https://app.example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        registry.record_trust_edge(
            project,
            "service_url",
            "https://app.example.com",
            "service_links_repo",
            "repo_url",
            "https://github.com/example/app",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        evaluation = registry.trust_auto_confirm_identifier(project, "service_url", "https://app.example.com")

        self.assertFalse(evaluation["auto_confirm"])
        self.assertEqual(evaluation["reason"], "guarded_identifier")

    def test_manual_repo_seed_and_mutual_edges_confirm_service(self) -> None:
        project = registry.default_project_record("proj:mutualservice", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
        registry.record_trust_edge(
            project,
            "repo_url",
            "https://github.com/example/app",
            "repo_links_service",
            "service_url",
            "https://app.example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        registry.record_trust_edge(
            project,
            "service_url",
            "https://app.example.com",
            "service_links_repo",
            "repo_url",
            "https://github.com/example/app",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        self.assertTrue(registry.confirm_service_url_with_trust(project, "https://app.example.com"))
        self.assertEqual(project["service_urls"], ["https://app.example.com"])

    def test_tangled_record_adds_candidate_and_did_edge_without_confirming_repo(self) -> None:
        project = registry.default_project_record("proj:tangledtrust", "service_url", "https://example.com")
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-25T00:00:00+00:00")
        bundle.add_record(EvidenceRecord(
            kind="tangled_repo_snapshot",
            source="tangled_account:dev.example",
            classification="confirmed",
            observed_at=bundle.collected_at,
            value={
                "owner_handle": "dev.example",
                "owner_did": "did:plc:dev",
                "repo_url": "https://tangled.org/dev.example/app",
                "score": 5,
            },
        ))

        registry.apply_tangled_evidence(project, bundle)

        self.assertEqual(project["source_code_urls"], [])
        self.assertEqual(project["candidate_source_code_urls"], ["https://tangled.org/dev.example/app"])
        edges = trust_graph.graph(project)["edges"]
        self.assertTrue(any(edge["predicate"] == "handle_resolves_did" for edge in edges))
        self.assertTrue(any(edge["predicate"] == "did_links_repo" for edge in edges))

    def test_collection_owned_when_schema_did_and_exact_authority_handle_are_mutual(self) -> None:
        project = registry.default_project_record("proj:collectiontrust", "service_url", "https://example.com")
        project["developer_accounts"] = [{"handle": "example.com", "did": "did:plc:dev", "source": "manual"}]
        project["lexicon_resolution_entries"] = [
            {
                "input_nsid": "app.example.post",
                "resolved_nsid": "app.example.post",
                "repo_did": "did:plc:dev",
                "authority_domain": "example.com",
                "record_uri": "at://did:plc:dev/com.atproto.lexicon.schema/app.example.post",
                "resolved_at": "2026-06-25T00:00:00+00:00",
            }
        ]

        self.assertTrue(registry.project_owned_lexicon_nsid(project, "app.example.post"))

        edges = trust_graph.graph(project)["edges"]
        self.assertTrue(any(edge["predicate"] == "did_publishes_schema" for edge in edges))
        self.assertTrue(any(edge["predicate"] == "collection_authority_domain" for edge in edges))
        self.assertTrue(any(edge["predicate"] == "domain_exact_handle" for edge in edges))

    def test_collection_not_owned_by_schema_record_without_exact_authority_handle(self) -> None:
        project = registry.default_project_record("proj:hostedcollection", "service_url", "https://sprk.so")
        project["developer_accounts"] = [{"handle": "sprk.so", "did": "did:plc:spark", "source": "manual"}]
        project["lexicon_resolution_entries"] = [
            {
                "input_nsid": "so.sprk.alice.post",
                "resolved_nsid": "so.sprk.alice.post",
                "repo_did": "did:plc:spark",
                "authority_domain": "alice.sprk.so",
                "record_uri": "at://did:plc:spark/com.atproto.lexicon.schema/so.sprk.alice.post",
                "resolved_at": "2026-06-25T00:00:00+00:00",
            }
        ]

        self.assertFalse(registry.project_owned_lexicon_nsid(project, "so.sprk.alice.post"))

    def test_collection_not_owned_by_did_schema_record_one_way_only(self) -> None:
        project = registry.default_project_record("proj:onewaycollection", "service_url", "https://example.com")
        project["developer_accounts"] = [{"did": "did:plc:dev", "source": "manual"}]
        project["lexicon_resolution_entries"] = [
            {
                "input_nsid": "app.example.post",
                "resolved_nsid": "app.example.post",
                "repo_did": "did:plc:dev",
                "authority_domain": "example.com",
                "record_uri": "at://did:plc:dev/com.atproto.lexicon.schema/app.example.post",
                "resolved_at": "2026-06-25T00:00:00+00:00",
            }
        ]

        self.assertFalse(registry.project_owned_lexicon_nsid(project, "app.example.post"))

    def test_handle_requires_did_document_reverse_claim_for_auto_confirm(self) -> None:
        project = registry.default_project_record("proj:handletrust", "did", "did:plc:dev")
        registry.add_manual_seed(project, "did", "did:plc:dev", source="test")
        registry.record_trust_edge(
            project,
            "handle",
            "dev.example.com",
            "handle_resolves_did",
            "did",
            "did:plc:dev",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        one_way = registry.trust_auto_confirm_identifier(project, "handle", "dev.example.com")
        self.assertFalse(one_way["auto_confirm"])

        registry.record_trust_edge(
            project,
            "did",
            "did:plc:dev",
            "did_document_claims_handle",
            "handle",
            "dev.example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        mutual = registry.trust_auto_confirm_identifier(project, "handle", "dev.example.com")
        self.assertTrue(mutual["auto_confirm"])
        self.assertEqual(mutual["reason"], "mutual_trust_path")

    def test_exact_domain_can_auto_confirm_only_through_mutual_identity_path(self) -> None:
        project = registry.default_project_record("proj:domaintrust", "did", "did:plc:dev")
        registry.add_manual_seed(project, "did", "did:plc:dev", source="test")
        registry.record_trust_edge(
            project,
            "handle",
            "example.com",
            "handle_resolves_did",
            "did",
            "did:plc:dev",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        registry.record_trust_edge(
            project,
            "did",
            "did:plc:dev",
            "did_document_claims_handle",
            "handle",
            "example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        registry.record_trust_edge(
            project,
            "domain",
            "example.com",
            "domain_exact_handle",
            "handle",
            "example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        missing_reverse = registry.trust_auto_confirm_identifier(project, "domain", "example.com")
        self.assertFalse(missing_reverse["auto_confirm"])

        registry.record_trust_edge(
            project,
            "handle",
            "example.com",
            "handle_exact_domain",
            "domain",
            "example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        mutual = registry.trust_auto_confirm_identifier(project, "domain", "example.com")
        self.assertTrue(mutual["auto_confirm"])

    def test_runtime_view_exposes_handle_identifier_separately_from_account(self) -> None:
        project = registry.default_project_record("proj:runtimehandle", "service_url", "https://example.com")
        project["developer_accounts"] = [{"handle": "dev.example.com", "did": "did:plc:dev", "source": "manual"}]

        identifiers = registry.runtime_view.project_identifiers(project)
        self.assertTrue(any(item.kind == "account" and item.role == "developer_account" for item in identifiers))
        self.assertTrue(any(item.kind == "handle" and item.value == "dev.example.com" for item in identifiers))
        self.assertTrue(any(item.kind == "did" and item.value == "did:plc:dev" for item in identifiers))


class TrustGraphAuditRepairTest(TempRegistryDbTestCase):
    def _source_repository_relation(
        self,
        subject_type: str,
        subject_value: str,
        object_type: str,
        object_value: str,
        *,
        method: str,
        classification: str = "confirmed",
    ) -> dict:
        return {
            "relation": {
                "subject": {"type": subject_type, "value": subject_value},
                "predicate": "source_repository",
                "object": {"type": object_type, "value": object_value},
                "locator": "test",
                "method": method,
            },
            "source": f"test:{method}",
            "classification": classification,
            "observed_at": "2026-06-25T00:00:00+00:00",
            "context": {},
        }

    def test_audit_classifies_confirmed_url_issues(self) -> None:
        conn = registry.open_db()
        try:
            missing = self.make_project("proj:missingedge", "repo_url", "https://github.com/example/app")
            registry.add_manual_seed(missing, "repo_url", "https://github.com/example/app", source="test")
            missing["service_urls"] = ["https://app.example.com"]
            missing["relation_evidence"] = [
                self._source_repository_relation(
                    "repo", "https://github.com/example/app", "website", "https://app.example.com",
                    method="github_homepage",
                ),
                self._source_repository_relation(
                    "service", "https://app.example.com", "repo", "https://github.com/example/app",
                    method="html_link",
                ),
            ]
            registry.save_project(conn, missing)

            reprobe = self.make_project("proj:reprobe", "repo_url", "https://github.com/example/app")
            registry.add_manual_seed(reprobe, "repo_url", "https://github.com/example/app", source="test")
            reprobe["service_urls"] = ["https://docs.example.com"]
            reprobe["relation_evidence"] = [
                self._source_repository_relation(
                    "repo", "https://github.com/example/app", "website", "https://docs.example.com",
                    method="github_homepage",
                ),
            ]
            registry.save_project(conn, reprobe)

            downgrade = self.make_project("proj:downgrade", "nsid", "app.example.post")
            downgrade["service_urls"] = ["https://unrelated.example.com"]
            registry.save_project(conn, downgrade)

            audit = registry.build_trust_graph_audit(conn, example_limit=10)
        finally:
            conn.close()

        self.assertEqual(audit["issue_counts"]["missing_recomputed_edge"], 1)
        self.assertEqual(audit["issue_counts"]["needs_reprobe"], 1)
        self.assertEqual(audit["issue_counts"]["should_downgrade"], 1)

    def test_repair_trust_graph_persists_recomputed_edges_without_downgrade_by_default(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:repairtrust", "repo_url", "https://github.com/example/app")
            registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
            project["service_urls"] = ["https://app.example.com"]
            project["relation_evidence"] = [
                self._source_repository_relation(
                    "repo", "https://github.com/example/app", "website", "https://app.example.com",
                    method="github_homepage",
                ),
                self._source_repository_relation(
                    "service", "https://app.example.com", "repo", "https://github.com/example/app",
                    method="html_link",
                ),
            ]
            registry.save_project(conn, project)

            dry_run = registry.repair_trust_graph(conn, execute=False)
            self.assertEqual(dry_run["changed_project_count"], 1)

            executed = registry.repair_trust_graph(conn, execute=True)
            fetched = registry.fetch_project(conn, "proj:repairtrust")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(executed["updated_project_count"], 1)
        graph = trust_graph.graph(fetched)
        self.assertTrue(any(edge["predicate"] == "repo_links_service" for edge in graph["edges"]))
        self.assertTrue(any(item.get("auto_confirm") for item in graph["evaluations"]))
        self.assertEqual(fetched["service_urls"], ["https://app.example.com"])

    def test_repair_trust_graph_can_downgrade_unsupported_when_explicit(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:trustdowngrade", "nsid", "app.example.post")
            project["service_urls"] = ["https://unrelated.example.com"]
            registry.save_project(conn, project)

            with self.assertRaises(ValueError):
                registry.repair_trust_graph(conn, execute=False, downgrade_unsupported=True)

            executed = registry.repair_trust_graph(conn, execute=True, downgrade_unsupported=True)
            fetched = registry.fetch_project(conn, "proj:trustdowngrade")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(executed["downgraded_identifier_count"], 1)
        self.assertEqual(fetched["service_urls"], [])
        self.assertEqual(fetched["service_url_candidates"], ["https://unrelated.example.com"])

    def test_repair_trust_graph_can_downgrade_unsupported_nsid(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:trustnsiddowngrade", "nsid", "app.example.post")
            project["resolved_nsids"] = ["app.example.post"]
            project["lexicon_resolution_entries"] = [{
                "input_nsid": "app.example.post",
                "resolved_nsid": "app.example.post",
                "repo_did": "did:plc:other",
                "authority_domain": "example.com",
                "record_uri": "at://did:plc:other/com.atproto.lexicon.schema/app.example.post",
                "resolved_at": "2026-06-25T00:00:00+00:00",
            }]
            registry.save_project(conn, project)

            executed = registry.repair_trust_graph(conn, execute=True, downgrade_unsupported=True)
            fetched = registry.fetch_project(conn, "proj:trustnsiddowngrade")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(executed["downgraded_identifier_count"], 1)
        self.assertEqual(fetched["primary_nsid"], "")
        self.assertEqual(fetched["resolved_nsids"], [])
        self.assertEqual(fetched["input_nsids"], ["app.example.post"])
        self.assertEqual(fetched["lexicon_resolution_entries"][0]["resolved_nsid"], "app.example.post")

    def test_repair_trust_graph_can_downgrade_did_without_dropping_handle(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:trustdiddowngrade", "repo_url", "https://github.com/example/app")
            registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
            project["developer_accounts"] = [{"handle": "dev.example.com", "did": "did:plc:dev", "source": "manual"}]
            registry.save_project(conn, project)

            executed = registry.repair_trust_graph(conn, execute=True, downgrade_unsupported=True)
            fetched = registry.fetch_project(conn, "proj:trustdiddowngrade")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(executed["downgraded_identifier_count"], 2)
        self.assertEqual(fetched["developer_accounts"], [])
        self.assertEqual(
            sorted(fetched["candidate_developer_accounts"], key=lambda item: sorted(item)),
            [
                {"did": "did:plc:dev", "source": "manual"},
                {"handle": "dev.example.com", "source": "manual"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
