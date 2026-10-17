from __future__ import annotations

import copy
from unittest import mock

from helpers import ProjectRecordTestCase, registry
from project_registry_core import collection_trust, project_record, trust_graph
from project_registry_core.application import explain


class TrustExplanationPreparationTest(ProjectRecordTestCase):
    def publication_project(self, count: int) -> dict:
        project = self.make_project("proj:many", "service_url", "https://example.com")
        registry.confirm_service_seed(project, "https://example.com", source="test")
        project["input_nsids"] = [f"com.example.record{i}" for i in range(count)]
        project["lexicon_resolution_entries"] = [
            {"input_nsid": nsid, "resolved_nsid": nsid, "repo_did": "did:plc:publisher",
             "authority_domain": "example.com", "record_uri": f"at://did:plc:publisher/com.atproto.lexicon.schema/{nsid}",
             "resolved_at": "2026-10-17T00:00:00+00:00"}
            for nsid in project["input_nsids"]
        ]
        return project

    def test_many_collections_match_independent_evaluation_and_paths(self) -> None:
        project = self.publication_project(8)
        actual = registry.trust_explanation(copy.deepcopy(project))
        reference = project_record.ensure_project_shape(copy.deepcopy(project))
        for row in actual["identifier_evaluations"]:
            if row["identifier"]["kind"] != "nsid":
                continue
            expected = registry.trust_collection_ownership_evaluation(reference, row["identifier"]["value"])
            paths = trust_graph.directional_paths(
                reference, candidate=row["node"], anchors=registry.collection_trusted_anchor_nodes(reference),
                allowed_node_kinds=registry.COLLECTION_TRUST_NODE_KINDS,
                allowed_predicates=registry.COLLECTION_TRUST_PREDICATES,
            )
            self.assertTrue(expected["auto_confirm"])
            self.assertEqual(row["evaluation"], expected)
            self.assertEqual(row["directional_paths"], paths)

    def test_edge_normalization_grows_linearly_with_publications(self) -> None:
        calls = []
        for count in (25, 100, 200):
            project = self.publication_project(count)
            with mock.patch.object(trust_graph, "normalize_edge", wraps=trust_graph.normalize_edge) as normalize:
                result = registry.trust_explanation(project)
            rows = [row for row in result["identifier_evaluations"] if row["identifier"]["kind"] == "nsid"]
            self.assertEqual(len(rows), count)
            self.assertTrue(all(row["evaluation"]["auto_confirm"] for row in rows))
            calls.append(normalize.call_count)
        self.assertLess(calls[1], calls[0] * 4.5)
        self.assertLess(calls[2], calls[1] * 2.2)

    def test_guarded_and_manual_collections_do_not_prepare_new_edges(self) -> None:
        for guarded in (False, True):
            project = self.make_project("proj:guard", "nsid", "com.example.record")
            project["manual_seeds"] = [{"type": "nsid", "value": "com.example.record"}]
            project["lexicon_resolution_entries"] = self.publication_project(1)["lexicon_resolution_entries"]
            if guarded:
                project["blocked_identifiers"] = [{"type": "nsid", "value": "com.example.record", "added_at": "2026-10-17T00:00:00+00:00"}]
            with mock.patch.object(registry, "sync_trust_edges_from_project_identity", wraps=registry.sync_trust_edges_from_project_identity) as sync:
                result = registry.trust_explanation(project)
            sync.assert_not_called()
            self.assertEqual(result["edge_count"], 0)
            self.assertEqual(result["identifier_evaluations"][0]["evaluation"]["reason"],
                             "guarded_identifier" if guarded else "manual_collection_seed")

    def test_publisher_explanation_shares_publication_and_adoption_facts(self) -> None:
        project = self.publication_project(25)
        project["service_urls"] = []
        project["resolved_nsids"] = project.pop("input_nsids")
        project["input_nsids"] = []
        project["confirmed_identifiers"] = []
        for nsid, entry in zip(project["resolved_nsids"], project["lexicon_resolution_entries"]):
            registry.add_confirmed_identifier_decision(project, "nsid", nsid, source="operator", role="owned_nsid")
            entry["publication_validation"] = {
                "version": collection_trust.VALIDATION_VERSION, "schema_id": nsid,
                "schema_type": "record", "schema_hash": "fixture",
            }
        reference = project_record.ensure_project_shape(copy.deepcopy(project))
        with mock.patch.object(collection_trust, "verified_publications", wraps=collection_trust.verified_publications) as publications, \
             mock.patch.object(collection_trust, "independent_adoptions", wraps=collection_trust.independent_adoptions) as adoptions:
            result = registry.trust_explanation(project)
        self.assertLessEqual(publications.call_count, 5)
        self.assertEqual(adoptions.call_count, 25)
        rows = [row for row in result["identifier_evaluations"] if row["identifier"]["kind"] == "nsid"]
        for row in rows:
            nsid = row["identifier"]["value"]
            self.assertEqual(row["evaluation"], collection_trust.evaluate_publisher_support(reference, nsid))
            self.assertEqual(row["evaluation"]["reason"], "accepted_lexicon_publisher")
            self.assertEqual(row["directional_paths"][0]["anchor_to_candidate"], row["evaluation"]["forward_path"])

    def test_later_explanation_observes_changed_publication(self) -> None:
        project = self.publication_project(1)
        before = registry.trust_explanation(project)
        project["lexicon_resolution_entries"][0]["publication_validation"] = {"schema_type": "query"}
        after = registry.trust_explanation(project)
        self.assertTrue(next(row for row in before["identifier_evaluations"] if row["identifier"]["kind"] == "nsid")["evaluation"]["auto_confirm"])
        self.assertFalse(next(row for row in after["identifier_evaluations"] if row["identifier"]["kind"] == "nsid")["evaluation"]["auto_confirm"])

    def test_requested_explanation_topic_does_not_compute_unreturned_trust(self) -> None:
        project = self.publication_project(8)
        with mock.patch.object(registry, "trust_explanation", side_effect=AssertionError("unrequested trust evaluation")):
            for topic in explain.TOPICS:
                if topic != "trust":
                    result = explain.explain_project_state(project, topic=topic)
                    self.assertEqual(list(result["topics"]), [topic])

    def test_batch_edges_match_sequential_merging(self) -> None:
        edges = [
            project_record.build_trust_edge("domain", "example.com", "domain_exact_handle", "handle", "example.com",
                                           source="test", classification=classification, eligible_for_auto_confirm=eligible,
                                           observed_at=date, context=context)
            for classification, eligible, date, context in (
                ("candidate", False, "2026-01-01", {"old": True, "shared": 1}),
                ("confirmed", True, "2026-02-01", {"shared": 2}),
                ("heuristic", False, "2025-01-01", {"new": True}),
            )
        ]
        sequential: dict = {}
        batch: dict = {}
        for edge in edges:
            trust_graph.upsert_edge(sequential, **edge, generated_at="fixed")
        trust_graph.upsert_edges(batch, edges, generated_at="fixed")
        self.assertEqual(batch, sequential)

    def test_prepared_paths_preserve_scope_and_path_order(self) -> None:
        project: dict = {}
        a = {"kind": "service_url", "value": "https://example.com"}
        b = {"kind": "repo_url", "value": "https://github.com/example/app"}
        for subject, predicate, obj in ((a, "service_links_repo", b), (b, "repo_links_service", a)):
            trust_graph.upsert_edge(project, subject=subject, predicate=predicate, object=obj,
                                    classification="confirmed", eligible_for_auto_confirm=True)
        prepared = trust_graph.PreparedGraph(project)
        for candidate, anchors in ((a, [b]), (b, [a])):
            for fn in (trust_graph.evaluate_auto_confirm, trust_graph.directional_paths):
                self.assertEqual(fn(project, candidate=candidate, anchors=anchors),
                                 fn(project, candidate=candidate, anchors=anchors, prepared_graph=prepared))

    def test_prepared_paths_preserve_cycles_ties_and_restricted_predicates(self) -> None:
        project: dict = {}
        domain = trust_graph.node("domain", "example.com")
        handles = [trust_graph.node("handle", value) for value in ("example.com", "other.example.com")]
        did = trust_graph.node("did", "did:plc:owner")
        for handle in handles:
            for subject, predicate, obj in (
                (domain, "domain_exact_handle", handle), (handle, "handle_resolves_did", did),
                (did, "did_document_claims_handle", handle), (handle, "handle_exact_domain", domain),
            ):
                trust_graph.upsert_edge(project, subject=subject, predicate=predicate, object=obj,
                                        classification="confirmed", eligible_for_auto_confirm=True)
        prepared = trust_graph.PreparedGraph(project)
        for predicates in (registry.IDENTITY_TRUST_PREDICATES, {"domain_exact_handle", "handle_resolves_did"}):
            for candidate in [domain, *handles, did]:
                arguments = dict(candidate=candidate, anchors=[domain, *handles, did],
                                 allowed_node_kinds=registry.IDENTITY_TRUST_NODE_KINDS, allowed_predicates=predicates)
                for fn in (trust_graph.evaluate_auto_confirm, trust_graph.directional_paths):
                    self.assertEqual(fn(project, **arguments), fn(project, **arguments, prepared_graph=prepared))
