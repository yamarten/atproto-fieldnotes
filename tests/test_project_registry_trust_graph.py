from __future__ import annotations

import unittest

from helpers import registry, TempRegistryDbTestCase
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord
from project_registry_core import trust_graph
from project_registry_core import project_record


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

    def test_mutual_references_support_repository_but_not_service_role(self) -> None:
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

        self.assertFalse(evaluation["auto_confirm"])
        reverse = trust_graph.evaluate_auto_confirm(
            project,
            candidate={"kind": "repo_url", "value": "https://github.com/example/app"},
            anchors=[{"kind": "service_url", "value": "https://app.example.com"}],
        )
        self.assertTrue(reverse["auto_confirm"])
        self.assertEqual(reverse["reason"], "mutual_trust_path")

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
    def test_full_evaluation_refresh_removes_stale_candidates_and_paths(self) -> None:
        project = project_record.default_project_record(
            "proj:refreshtrust", "repo_url", "https://github.com/example/app"
        )
        registry.add_manual_seed(
            project, "repo_url", "https://github.com/example/app", source="test"
        )
        trust_graph.upsert_evaluation(project, {
            "candidate": {"kind": "service_url", "value": "https://stale.example.com"},
            "auto_confirm": True,
            "reason": "mutual_trust_path",
            "forward_path": [{"method": "github_homepage"}],
            "backward_path": [],
            "policy_version": trust_graph.TRUST_POLICY_VERSION,
        })

        registry.refresh_trust_graph_evaluations(project)

        evaluations = trust_graph.graph(project)["evaluations"]
        self.assertFalse(any(
            (item.get("candidate") or {}).get("value") == "https://stale.example.com"
            for item in evaluations
        ))
        self.assertTrue(any(
            (item.get("candidate") or {}).get("value") == "https://github.com/example/app"
            for item in evaluations
        ))

    def test_blocked_service_candidate_does_not_auto_confirm(self) -> None:
        project = project_record.default_project_record("proj:blockedtrust", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
        registry.add_confirmed_identifier_decision(project, "repo_url", "https://github.com/example/app", source="test")
        project["blocked_identifiers"] = [{"type": "service_url", "value": "https://app.example.com"}]
        project_record.record_trust_edge(
            project,
            "repo_url",
            "https://github.com/example/app",
            "repo_links_service",
            "service_url",
            "https://app.example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        project_record.record_trust_edge(
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

    def test_manual_repo_seed_and_mutual_edges_do_not_establish_service_role(self) -> None:
        project = project_record.default_project_record("proj:mutualservice", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
        registry.add_confirmed_identifier_decision(project, "repo_url", "https://github.com/example/app", source="test")
        project_record.record_trust_edge(
            project,
            "repo_url",
            "https://github.com/example/app",
            "repo_links_service",
            "service_url",
            "https://app.example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        project_record.record_trust_edge(
            project,
            "service_url",
            "https://app.example.com",
            "service_links_repo",
            "repo_url",
            "https://github.com/example/app",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        self.assertFalse(registry.confirm_service_url_with_trust(project, "https://app.example.com"))
        self.assertEqual(project["service_urls"], [])

    def test_final_reconciliation_promotes_url_after_mutual_edges_arrive(self) -> None:
        project = project_record.default_project_record(
            "proj:lateurltrust", "service_url", "https://app.example.com"
        )
        registry.add_manual_seed(project, "service_url", "https://app.example.com", source="test")
        repo_url = "https://github.com/unrelated-owner/unrelated-repo"
        project["candidate_project_repository_urls"] = [repo_url]
        project["source_repo_records"] = [{
            "url": repo_url,
            "provider": "github",
            "confidence": "candidate",
            "relation": "candidate_repo",
            "source": "github_search",
            "reason": "weak discovery evidence",
            "evidence_kind": "github_search",
            "evidence_url": repo_url,
            "recorded_at": "2026-07-28T00:00:00+00:00",
            "updated_at": "",
        }]
        project_record.record_trust_edge(
            project,
            "service_url",
            "https://app.example.com",
            "service_links_repo",
            "repo_url",
            repo_url,
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        project_record.record_trust_edge(
            project,
            "repo_url",
            repo_url,
            "repo_links_service",
            "service_url",
            "https://app.example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        result = registry.reconcile_trust_supported_url_candidates(project)
        registry.finalize_project_shape(project)

        self.assertEqual(result["confirmed_repo_urls"], [repo_url])
        self.assertIn(repo_url, project["project_repository_urls"])
        self.assertNotIn(repo_url, project["candidate_project_repository_urls"])
        candidate_observations = [
            item
            for item in project["source_repo_records"]
            if item.get("url") == repo_url and item.get("confidence") == "candidate"
        ]
        self.assertEqual(len(candidate_observations), 1)

    def test_final_reconciliation_prunes_confirmed_http_redirect_source(self) -> None:
        project = project_record.default_project_record(
            "proj:redirectreconcile", "service_url", "https://example.com"
        )
        registry.add_manual_seed(project, "service_url", "https://example.com", source="test")
        project["service_url_candidates"] = ["http://example.com"]
        project["service_verification_results"] = [{
            "requested_url": "http://example.com",
            "final_url": "https://example.com",
            "status_code": 200,
            "redirected": True,
            "redirect_same_host": True,
            "redirect_same_site_family": True,
            "redirect_http_to_https": True,
        }]
        project_record.record_trust_edge(
            project,
            "service_url",
            "https://example.com",
            "service_canonical",
            "service_url",
            "http://example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        project_record.record_trust_edge(
            project,
            "service_url",
            "http://example.com",
            "service_canonical",
            "service_url",
            "https://example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )

        result = registry.reconcile_trust_supported_url_candidates(project)

        self.assertEqual(result["removed_redirect_service_urls"], ["http://example.com"])
        self.assertEqual(result["confirmed_service_urls"], [])
        self.assertNotIn("http://example.com", project["service_urls"])
        self.assertNotIn("http://example.com", project["service_url_candidates"])

    def test_tangled_record_adds_candidate_and_did_edge_without_confirming_repo(self) -> None:
        project = project_record.default_project_record("proj:tangledtrust", "service_url", "https://example.com")
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

        self.assertEqual(project["project_repository_urls"], [])
        self.assertEqual(project["candidate_project_repository_urls"], ["https://tangled.org/dev.example/app"])
        edges = trust_graph.graph(project)["edges"]
        self.assertTrue(any(edge["predicate"] == "handle_resolves_did" for edge in edges))
        self.assertTrue(any(edge["predicate"] == "did_links_repo" for edge in edges))

    def test_collection_owned_when_schema_did_and_exact_authority_handle_are_mutual(self) -> None:
        project = project_record.default_project_record("proj:collectiontrust", "service_url", "https://example.com")
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
        project = project_record.default_project_record("proj:hostedcollection", "service_url", "https://sprk.so")
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
        project = project_record.default_project_record("proj:onewaycollection", "service_url", "https://example.com")
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

    def test_collection_owned_through_confirmed_exact_root_service_and_lexicon_record(self) -> None:
        project = project_record.default_project_record(
            "proj:servicecollectiontrust", "service_url", "https://example.com"
        )
        registry.add_manual_seed(project, "service_url", "https://example.com", source="test")
        project["input_nsids"] = ["com.example.post"]
        project["lexicon_resolution_entries"] = [{
            "input_nsid": "com.example.post",
            "resolved_nsid": "com.example.post",
            "repo_did": "did:plc:schema",
            "authority_domain": "example.com",
            "record_uri": "at://did:plc:schema/com.atproto.lexicon.schema/com.example.post",
            "resolved_at": "2026-06-25T00:00:00+00:00",
        }]

        evaluation = registry.trust_collection_ownership_evaluation(project, "com.example.post")

        self.assertTrue(evaluation["auto_confirm"])
        self.assertEqual(evaluation["anchor"], {"kind": "service_url", "value": "https://example.com"})
        predicates = {
            edge["predicate"]
            for edge in evaluation["forward_path"] + evaluation["backward_path"]
        }
        self.assertEqual(
            predicates,
            {
                "service_exact_domain",
                "domain_exact_service",
                "domain_publishes_lexicon_record",
                "collection_authority_domain",
            },
        )

    def test_collection_not_owned_through_untrusted_or_non_root_service(self) -> None:
        entry = {
            "input_nsid": "com.example.post",
            "resolved_nsid": "com.example.post",
            "repo_did": "did:plc:schema",
            "authority_domain": "example.com",
            "record_uri": "at://did:plc:schema/com.atproto.lexicon.schema/com.example.post",
            "resolved_at": "2026-06-25T00:00:00+00:00",
        }
        untrusted = project_record.default_project_record(
            "proj:untrustedservicecollection", "service_url", "https://example.com"
        )
        untrusted["service_urls"] = ["https://example.com"]
        untrusted["input_nsids"] = ["com.example.post"]
        untrusted["lexicon_resolution_entries"] = [entry]

        non_root = project_record.default_project_record(
            "proj:nonrootservicecollection", "service_url", "https://example.com/app"
        )
        registry.add_manual_seed(non_root, "service_url", "https://example.com/app", source="test")
        non_root["input_nsids"] = ["com.example.post"]
        non_root["lexicon_resolution_entries"] = [entry]

        self.assertFalse(registry.trust_collection_ownership_evaluation(untrusted, "com.example.post")["auto_confirm"])
        self.assertFalse(registry.trust_collection_ownership_evaluation(non_root, "com.example.post")["auto_confirm"])

    def test_lexicon_result_promotes_candidate_only_when_collection_trust_is_mutual(self) -> None:
        def lexicon_result() -> dict:
            return {
                "did": "did:plc:schema",
                "did_doc": {"id": "did:plc:schema"},
                "authority_domain": "example.com",
                "pds_url": "https://pds.example.net",
                "record": {
                    "uri": "at://did:plc:schema/com.atproto.lexicon.schema/com.example.post",
                    "value": {"$type": "com.atproto.lexicon.schema", "lexicon": 1, "id": "com.example.post", "defs": {"main": {"type": "record"}}},
                },
            }

        trusted = project_record.default_project_record(
            "proj:trustedlexiconresult", "service_url", "https://example.com"
        )
        registry.add_manual_seed(trusted, "service_url", "https://example.com", source="test")
        trusted["input_nsids"] = ["com.example.post"]

        untrusted = project_record.default_project_record(
            "proj:untrustedlexiconresult", "service_url", "https://example.com"
        )
        untrusted["input_nsids"] = ["com.example.post"]

        registry.apply_lexicon_record_result(trusted, "com.example.post", lexicon_result())
        registry.apply_lexicon_record_result(untrusted, "com.example.post", lexicon_result())

        self.assertEqual(trusted["resolved_nsids"], ["com.example.post"])
        self.assertEqual(trusted["input_nsids"], [])
        self.assertEqual(untrusted["resolved_nsids"], [])
        self.assertEqual(untrusted["input_nsids"], ["com.example.post"])

    def test_handle_requires_did_document_reverse_claim_for_auto_confirm(self) -> None:
        project = project_record.default_project_record("proj:handletrust", "did", "did:plc:dev")
        registry.add_manual_seed(project, "did", "did:plc:dev", source="test")
        registry.add_confirmed_identifier_decision(project, "did", "did:plc:dev", source="test")
        project_record.record_trust_edge(
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

        project_record.record_trust_edge(
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
        project = project_record.default_project_record("proj:domaintrust", "did", "did:plc:dev")
        registry.add_manual_seed(project, "did", "did:plc:dev", source="test")
        registry.add_confirmed_identifier_decision(project, "did", "did:plc:dev", source="test")
        project_record.record_trust_edge(
            project,
            "handle",
            "example.com",
            "handle_resolves_did",
            "did",
            "did:plc:dev",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        project_record.record_trust_edge(
            project,
            "did",
            "did:plc:dev",
            "did_document_claims_handle",
            "handle",
            "example.com",
            classification="confirmed",
            eligible_for_auto_confirm=True,
        )
        project_record.record_trust_edge(
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

        project_record.record_trust_edge(
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
        project = project_record.default_project_record("proj:runtimehandle", "service_url", "https://example.com")
        project["developer_accounts"] = [{"handle": "dev.example.com", "did": "did:plc:dev", "source": "manual"}]

        identifiers = registry.runtime_view.project_identifiers(project)
        self.assertTrue(any(item.kind == "account" and item.role == "developer_account" for item in identifiers))
        self.assertTrue(any(item.kind == "handle" and item.value == "dev.example.com" for item in identifiers))
        self.assertTrue(any(item.kind == "did" and item.value == "did:plc:dev" for item in identifiers))


class TrustGraphAuditRepairTest(TempRegistryDbTestCase):
    def _project_repository_relation(
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
                "predicate": "project_repository",
                "object": {"type": object_type, "value": object_value},
                "locator": "test",
                "method": method,
            },
            "source": f"test:{method}",
            "classification": classification,
            "observed_at": "2026-06-25T00:00:00+00:00",
            "context": {},
        }

    def _repository_homepage_relation(self, repo_url: str, homepage: str) -> dict:
        return {
            "relation": {
                "subject": {"type": "repo", "value": repo_url},
                "predicate": "repository_homepage",
                "object": {"type": "website", "value": homepage},
                "locator": "test",
                "method": "github_homepage",
            },
            "source": "test:github_homepage",
            "classification": "confirmed",
            "observed_at": "2026-06-25T00:00:00+00:00",
            "context": {},
        }

    def test_audit_classifies_confirmed_url_issues(self) -> None:
        conn = registry.open_db()
        try:
            missing = self.make_project("proj:missingedge", "repo_url", "https://github.com/example/app")
            registry.add_manual_seed(missing, "repo_url", "https://github.com/example/app", source="test")
            registry.add_confirmed_identifier_decision(missing, "repo_url", "https://github.com/example/app", source="test")
            missing["service_urls"] = ["https://app.example.com"]
            missing["relation_evidence"] = [
                self._repository_homepage_relation(
                    "https://github.com/example/app", "https://app.example.com",
                ),
                self._project_repository_relation(
                    "service", "https://app.example.com", "repo", "https://github.com/example/app",
                    method="html_link",
                ),
            ]
            project_record.save_project(conn, missing)

            reprobe = self.make_project("proj:reprobe", "repo_url", "https://github.com/example/app")
            registry.add_manual_seed(reprobe, "repo_url", "https://github.com/example/app", source="test")
            registry.add_confirmed_identifier_decision(reprobe, "repo_url", "https://github.com/example/app", source="test")
            reprobe["service_urls"] = ["https://docs.example.com"]
            reprobe["relation_evidence"] = [
                self._repository_homepage_relation(
                    "https://github.com/example/app", "https://docs.example.com",
                ),
            ]
            project_record.save_project(conn, reprobe)

            downgrade = self.make_project("proj:downgrade", "nsid", "app.example.post")
            downgrade["service_urls"] = ["https://unrelated.example.com"]
            project_record.save_project(conn, downgrade)

            audit = registry.build_trust_graph_audit(conn, example_limit=10)
        finally:
            conn.close()

        self.assertNotIn("missing_recomputed_edge", audit["issue_counts"])
        self.assertEqual(audit["issue_counts"]["needs_reprobe"], 1)
        self.assertEqual(audit["issue_counts"]["should_downgrade"], 2)

    def test_repair_trust_graph_persists_evaluations_without_downgrade_by_default(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:repairtrust", "repo_url", "https://github.com/example/app")
            registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
            registry.add_confirmed_identifier_decision(project, "repo_url", "https://github.com/example/app", source="test")
            project["service_urls"] = ["https://app.example.com"]
            project["relation_evidence"] = [
                self._repository_homepage_relation(
                    "https://github.com/example/app", "https://app.example.com",
                ),
                self._project_repository_relation(
                    "service", "https://app.example.com", "repo", "https://github.com/example/app",
                    method="html_link",
                ),
            ]
            project_record.save_project(conn, project)

            dry_run = registry.repair_trust_graph(conn, execute=False)
            self.assertEqual(dry_run["changed_project_count"], 1)

            executed = registry.repair_trust_graph(conn, execute=True)
            fetched = project_record.fetch_project(conn, "proj:repairtrust")
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
            project_record.save_project(conn, project)

            with self.assertRaises(ValueError):
                registry.repair_trust_graph(conn, execute=False, downgrade_unsupported=True)

            executed = registry.repair_trust_graph(conn, execute=True, downgrade_unsupported=True)
            fetched = project_record.fetch_project(conn, "proj:trustdowngrade")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(executed["downgraded_identifier_count"], 1)
        self.assertEqual(fetched["service_urls"], [])
        self.assertEqual(fetched["service_url_candidates"], ["https://unrelated.example.com"])

    def test_repair_trust_graph_preserves_source_confirmed_owned_nsid(self) -> None:
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
            project_record.save_project(conn, project)

            executed = registry.repair_trust_graph(conn, execute=True, downgrade_unsupported=True)
            fetched = project_record.fetch_project(conn, "proj:trustnsiddowngrade")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(executed["downgraded_identifier_count"], 0)
        self.assertNotIn("primary_nsid", fetched)
        self.assertEqual(fetched["resolved_nsids"], ["app.example.post"])
        self.assertEqual(fetched["input_nsids"], [])
        self.assertEqual(fetched["lexicon_resolution_entries"][0]["resolved_nsid"], "app.example.post")

    def test_repair_trust_graph_can_downgrade_did_without_dropping_handle(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:trustdiddowngrade", "repo_url", "https://github.com/example/app")
            registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
            registry.add_confirmed_identifier_decision(project, "repo_url", "https://github.com/example/app", source="test")
            project["developer_accounts"] = [{"handle": "dev.example.com", "did": "did:plc:dev", "source": "manual"}]
            project_record.save_project(conn, project)

            executed = registry.repair_trust_graph(conn, execute=True, downgrade_unsupported=True)
            fetched = project_record.fetch_project(conn, "proj:trustdiddowngrade")
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
