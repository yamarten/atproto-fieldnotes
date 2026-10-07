from __future__ import annotations

import copy
import json
from contextlib import closing
from unittest.mock import patch

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry

from project_registry_core import project_merge

from project_registry_core import enrichment_pipeline
from project_registry_core import collection_trust, identifiers, project_record, replay, runtime_view, storage
from project_registry_core.evidence import EvidenceBundle


A = "org.example.profile.account"
B = "org.example.profile.education"
C = "org.example.profile.course"
DID = "did:plc:publisher"
WHEN = "2026-09-14T06:00:00+00:00"


def publication(nsid: str, *, did: str = DID, when: str = WHEN) -> dict:
    return {
        "did": did, "did_doc": {"id": did}, "pds_url": "https://pds.example.net",
        "authority_domain": identifiers.derive_authority_domain(nsid), "resolved_at": when,
        "record": {"uri": f"at://{did}/com.atproto.lexicon.schema/{nsid}", "value": {
            "$type": "com.atproto.lexicon.schema", "lexicon": 1, "id": nsid,
            "defs": {"main": {"type": "record"}},
        }},
    }


def project() -> dict:
    p = project_record.default_project_record("proj:scopedpublisher", "service_url", "https://unverified.example.org")
    p["resolved_nsids"] = [A]
    p["input_nsids"] = [B, C]
    registry.add_confirmed_identifier_decision(p, "nsid", A, source="collection_inventory_import", role="owned_nsid")
    return p


class CollectionPublicationTrustUnitTest(ProjectRecordTestCase):
    def test_publisher_support_is_scoped_explainable_and_not_an_identity_or_merge_anchor(self) -> None:
        p = project()
        registry.apply_lexicon_record_result(p, A, publication(A))
        registry.apply_lexicon_record_result(p, B, publication(B))
        self.assertIn(B, p["resolved_nsids"])
        evaluation = registry.trust_collection_ownership_evaluation(p, B)
        self.assertEqual(evaluation["policy_version"], collection_trust.POLICY_VERSION)
        self.assertEqual(evaluation["anchor"]["value"], A)
        self.assertEqual(evaluation["scope"]["publisher_did"], DID)
        self.assertEqual(evaluation["anchor_adoptions"][0]["source"], "collection_inventory_import")
        self.assertTrue(evaluation["forward_path"] and evaluation["backward_path"])
        self.assertFalse(any(i.kind == "did" and i.classification == "confirmed" for i in runtime_view.project_identifiers(p)))
        self.assertNotIn(B, project_merge.trusted_collection_nsids_for_merge(p))
        self.assertIn(A, project_merge.trusted_collection_nsids_for_merge(p))
        decision = next(d for d in p["confirmed_identifiers"] if d["value"] == B)
        self.assertEqual(decision["source"], collection_trust.ADOPTION_SOURCE)
        self.assertEqual(json.loads(decision["source_ref"])["anchor"]["value"], A)
        explained = next(row for row in registry.trust_explanation(p)["identifier_evaluations"] if row["identifier"]["value"] == B)
        self.assertEqual(explained["evaluation"]["scope"]["authority_domain"], "profile.example.org")

    def test_collection_order_does_not_change_adoption(self) -> None:
        for order in ((A, B), (B, A)):
            with self.subTest(order=order):
                p = project()
                for nsid in order:
                    registry.apply_lexicon_record_result(p, nsid, publication(nsid))
                self.assertIn(B, p["resolved_nsids"])
                self.assertTrue(collection_trust.publisher_only_adoption(p, B))

    def test_candidate_and_independent_current_anchor_are_required(self) -> None:
        p = project()
        registry.apply_lexicon_record_result(p, A, publication(A))
        registry.apply_lexicon_record_result(p, B, publication(B))
        for label, change in (
            ("no candidate", lambda q: q.update(input_nsids=[], resolved_nsids=[A], lexicons=[A])),
            ("no adoption", lambda q: q.update(confirmed_identifiers=[])),
            ("candidate anchor", lambda q: q.update(resolved_nsids=[B], input_nsids=[A])),
            ("former anchor", lambda q: q.update(resolved_nsids=[B], input_nsids=[], lexicons=[B])),
            ("other project", lambda q: q.update(project_key="proj:other", resolved_nsids=[], confirmed_identifiers=[])),
        ):
            with self.subTest(label=label):
                q = copy.deepcopy(p)
                change(q)
                self.assertFalse(collection_trust.evaluate_publisher_support(q, B)["auto_confirm"])

    def test_guards_on_either_end_dominate_publication_support(self) -> None:
        for field in ("blocked_identifiers", "deferred_identifiers"):
            for target in (A, B):
                with self.subTest(field=field, target=target):
                    p = project()
                    p[field] = [{"type": "nsid", "value": target}]
                    registry.apply_lexicon_record_result(p, A, publication(A))
                    registry.apply_lexicon_record_result(p, B, publication(B))
                    self.assertNotIn(B, p["resolved_nsids"])

    def test_same_publisher_does_not_cross_authority_or_create_candidate(self) -> None:
        for target in ("org.example.other.education", "org.example.profile.v2.education", "org.example.education"):
            p = project()
            p["input_nsids"] = [target]
            registry.apply_lexicon_record_result(p, A, publication(A))
            registry.apply_lexicon_record_result(p, target, publication(target))
            self.assertNotIn(target, p["resolved_nsids"])
        p = project()
        p["input_nsids"] = []
        registry.apply_lexicon_record_result(p, A, publication(A))
        registry.apply_lexicon_record_result(p, B, publication(B))
        self.assertNotIn(B, p["resolved_nsids"])

    def test_different_publisher_and_invalid_publications_do_not_support_candidate(self) -> None:
        cases = []
        for key, value in (("authority_domain", "other.example.org"), ("did_doc", {"id": "did:plc:other"})):
            result = publication(B)
            result[key] = value
            cases.append(result)
        for key, value in (("$type", "app.bsky.feed.post"), ("lexicon", 2), ("id", C), ("defs", {"main": {"type": "unsupported-type"}})):
            result = publication(B)
            result["record"]["value"][key] = value
            cases.append(result)
        result = publication(B)
        result["record"]["uri"] = f"at://{DID}/app.bsky.feed.post/{B}"
        cases.append(result)
        legacy = publication(B)
        legacy.pop("authority_domain")
        cases.extend([legacy, publication(B, did="did:plc:another")])
        for result in cases:
            with self.subTest(result=result):
                p = project()
                registry.apply_lexicon_record_result(p, A, publication(A))
                registry.apply_lexicon_record_result(p, B, result)
                self.assertNotIn(B, p["resolved_nsids"])

    def test_derived_adoption_cannot_replace_revoked_root(self) -> None:
        p = project()
        for nsid in (A, B):
            registry.apply_lexicon_record_result(p, nsid, publication(nsid))
        registry.clear_confirmed_identifier_decision(p, "nsid", A)
        registry.apply_lexicon_record_result(p, C, publication(C))
        self.assertNotIn(C, p["resolved_nsids"])
        self.assertFalse(collection_trust.evaluate_publisher_support(p, B)["auto_confirm"])

    def test_legacy_entry_requires_new_observation_and_newer_failure_replaces_witness(self) -> None:
        p = project()
        registry.apply_lexicon_record_result(p, A, publication(A))
        p["lexicon_resolution_entries"][0].pop("publication_validation")
        registry.apply_lexicon_record_result(p, B, publication(B))
        self.assertNotIn(B, p["resolved_nsids"])
        registry.apply_lexicon_record_result(p, A, publication(A, when="2026-09-14T07:00:00+00:00"))
        self.assertIn(B, p["resolved_nsids"])
        invalid = publication(A, when="2026-09-14T08:00:00+00:00")
        invalid["record"]["value"]["defs"]["main"]["type"] = "query"
        registry.apply_lexicon_record_result(p, A, invalid)
        p = project_record.ensure_project_shape(p)
        self.assertFalse(collection_trust.evaluate_publisher_support(p, B)["auto_confirm"])
        registry.apply_lexicon_record_result(p, C, publication(C))
        self.assertNotIn(C, p["resolved_nsids"])

    def test_new_authority_delegation_invalidates_older_paths_even_for_other_schema(self) -> None:
        p = project()
        for nsid in (A, B):
            registry.apply_lexicon_record_result(p, nsid, publication(nsid))
        registry.apply_lexicon_record_result(p, C, publication(C, did="did:plc:newpublisher", when="2026-09-15T06:00:00+00:00"))
        for state in (p, project_record.ensure_project_shape(copy.deepcopy(p))):
            self.assertFalse(collection_trust.evaluate_publisher_support(state, B)["auto_confirm"])

    def test_response_for_wrong_nsid_invalidates_old_witness_and_codec_is_idempotent(self) -> None:
        p = project()
        for nsid in (A, B):
            registry.apply_lexicon_record_result(p, nsid, publication(nsid))
        wrong = publication(C, when="2026-09-15T06:00:00+00:00")
        registry.apply_lexicon_record_result(p, B, wrong)
        normalized = project_record.normalize_lexicon_resolution_entries(p["lexicon_resolution_entries"])
        self.assertEqual(normalized, project_record.normalize_lexicon_resolution_entries(normalized))
        p["lexicon_resolution_entries"] = normalized
        self.assertFalse(collection_trust.evaluate_publisher_support(p, B)["auto_confirm"])

    def test_fetch_records_exact_dns_authority_without_changing_endpoint_contract(self) -> None:
        result = publication(A)
        with patch.object(registry, "resolve_lexicon_authority_did", return_value=DID) as dns, \
             patch.object(registry, "resolve_did_document", return_value={"id": DID}) as did, \
             patch.object(registry, "extract_pds_url", return_value=result["pds_url"]), \
             patch.object(registry.http_transport, "request_json", return_value=result["record"]) as get:
            fetched = registry.fetch_lexicon_record(A)
        self.assertEqual(fetched["authority_domain"], "profile.example.org")
        self.assertTrue(collection_trust.publication_validation(A, fetched))
        self.assertEqual(dns.call_args.args[0], "profile.example.org")
        self.assertEqual(did.call_args.args[0], DID)
        self.assertIn("collection=com.atproto.lexicon.schema", get.call_args.args[0])


class CollectionPublicationTrustTest(TempRegistryDbTestCase):
    def test_review_required_or_cleared_adoption_and_stale_foreign_rows_are_not_roots(self) -> None:
        with closing(registry.open_db()) as conn:
            p = project()
            project_record.save_project(conn, p)
            p = project_record.fetch_project(conn, p["project_key"])
            for nsid in (A, B):
                registry.apply_lexicon_record_result(p, nsid, publication(nsid))
            for changes in ({"review_required": 1}, {"cleared_at": WHEN}, {"project_key": "proj:other"}):
                q = copy.deepcopy(p)
                for row in q[runtime_view.RUNTIME_ADOPTION_ROWS]:
                    row.update(changes)
                self.assertFalse(collection_trust.evaluate_publisher_support(q, B)["auto_confirm"])

    def test_save_fetch_and_saved_investigation_replay_preserve_provenance(self) -> None:
        with closing(registry.open_db()) as conn:
            p = project()
            project_record.save_project(conn, p)
            p = project_record.fetch_project(conn, p["project_key"])
            before = copy.deepcopy(p)
            bundle = EvidenceBundle(project_key=p["project_key"], collected_at=WHEN)
            for nsid in (B, A):
                bundle.extend(enrichment_pipeline.collect_lexicon_result(p["project_key"], nsid, publication(nsid), WHEN))
            registry.apply_collector_snapshot_evidence(p, bundle)
            project_record.save_project(conn, p)
            loaded = project_record.fetch_project(conn, p["project_key"])
            self.assertIn(B, loaded["resolved_nsids"])
            self.assertFalse(collection_trust.independent_adoptions(loaded, B))
            self.assertTrue(collection_trust.publisher_only_adoption(loaded, B))
            self.assertEqual(collection_trust.evaluate_publisher_support(loaded, B)["anchor"]["value"], A)
            run_id = storage.save_investigation_run(conn, project_key=p["project_key"], started_at=WHEN, completed_at=WHEN,
                                                  input_project=before, output_project=p, bundle=bundle)
            saved = storage.load_investigation_run(conn, run_id)
            self.assertIn("identifier_adoptions", saved["input_project"])
            with patch.object(registry.http_transport, "request_json", side_effect=AssertionError("replay must not fetch")):
                replayed = replay.replay_bundle(saved["input_project"], saved["bundle"], registry.replay_dependencies())
            self.assertIn(B, replayed["resolved_nsids"])
            self.assertEqual(collection_trust.evaluate_publisher_support(replayed, B)["anchor"]["value"], A)
            registry.clear_confirmed_identifier_decision(loaded, "nsid", A)
            self.assertFalse(collection_trust.evaluate_publisher_support(loaded, B)["auto_confirm"])
            project_record.save_project(conn, loaded)
            loaded = project_record.fetch_project(conn, p["project_key"])
            registry.apply_lexicon_record_result(loaded, C, publication(C))
            self.assertNotIn(C, loaded["resolved_nsids"])
