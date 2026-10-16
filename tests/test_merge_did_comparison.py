from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

from helpers import ProjectRecordTestCase
from project_registry_core.application import merge
from project_registry_core import collection_trust
from project_registry_core.identity import identifiers


def resolution(sid, did, kind="definitions", at="2026-10-13T15:21:40+00:00"):
    return {"input_nsid": sid, "resolved_nsid": sid, "repo_did": did,
            "authority_domain": identifiers.derive_authority_domain(sid),
            "record_uri": f"at://{did}/com.atproto.lexicon.schema/{sid}", "resolved_at": at,
            "publication_validation": {"version": collection_trust.VALIDATION_VERSION,
                "schema_id": sid, "schema_type": kind, "schema_hash": "fixture-hash"}}


class MergeDidComparisonTest(ProjectRecordTestCase):
    def test_all_four_role_comparisons_and_reverse_leave_candidates_unchanged(self):
        source = self.make_project("proj:source", "nsid", "social.source.embed")
        target = self.make_project("proj:target", "nsid", "app.target.post")
        source["lexicon_resolution_entries"] = [resolution("social.source.embed", "did:web:shared.example")]
        target["lexicon_resolution_entries"] = [resolution("app.target.post", "did:web:shared.example", "record")]
        source["input_nsids"].append("app.source.post")
        with mock.patch.object(merge, "fetch_collection_first_creator_did", return_value="did:web:shared.example") as fetch:
            initial = merge.inspect_merge_pair_payload(source, target)
            fetch.assert_not_called()
            self.assertEqual(initial["did_comparison"]["publisher_publisher"], ["did:web:shared.example"])
            self.assertIsNone(initial["did_comparison"]["first_observer_first_observer"])
            result = merge.inspect_merge_pair_payload(source, target, include_first_creator_details=True)
            self.assertEqual(fetch.call_args_list, [mock.call("app.source.post"), mock.call("app.target.post")])
            for matches in result["did_comparison"].values():
                self.assertEqual(matches, ["did:web:shared.example"])
            self.assertEqual(result["source_pair_candidate"], initial["source_pair_candidate"])
            self.assertEqual(result["target_pair_candidate"], initial["target_pair_candidate"])
            self.assertEqual(result["moderate_overlap"], initial["moderate_overlap"])
            reverse = merge.inspect_merge_pair_payload(target, source, include_first_creator_details=True)
            self.assertEqual(reverse["source_lexicon_publisher_dids"], result["target_lexicon_publisher_dids"])
            self.assertEqual(reverse["did_comparison"], result["did_comparison"])
        witness = initial["source_lexicon_publisher_details"]["social.source.embed"][0]
        self.assertEqual(witness["observed_at"], "2026-10-13T15:21:40+00:00")
        self.assertEqual(witness["status"], "validated_observation")

    def test_cross_comparison_keeps_direction_and_errors(self):
        source = self.make_project("proj:source", "nsid", "social.source.embed")
        source["lexicon_resolution_entries"] = [resolution("social.source.embed", "did:plc:publisher")]
        target = self.make_project("proj:target", "nsid", "app.target.post")
        with mock.patch.object(merge, "fetch_collection_first_creator_did", return_value="did:plc:publisher") as fetch:
            result = merge.inspect_merge_pair_payload(source, target, include_first_creator_details=True)
            fetch.assert_called_once_with("app.target.post")
            self.assertEqual(result["did_comparison"]["source_publisher_target_first_observer"], ["did:plc:publisher"])
            self.assertEqual(result["did_comparison"]["source_first_observer_target_publisher"], [])
            reverse = merge.inspect_merge_pair_payload(target, source, include_first_creator_details=True)
            self.assertEqual(reverse["did_comparison"]["source_first_observer_target_publisher"], ["did:plc:publisher"])
        with mock.patch.object(merge, "fetch_collection_first_creator_did", side_effect=RuntimeError("offline")) as fetch:
            result = merge.inspect_merge_pair_payload(source, target, include_first_creator_details=True)
            fetch.assert_called_once()
            self.assertEqual(result["target_collection_first_creator_details"]["app.target.post"]["status"], "error")
            self.assertEqual(result["did_comparison"]["source_publisher_target_first_observer"], [])

    def test_known_nonrecords_are_excluded_from_every_input_and_unknown_keeps_case(self):
        project = self.make_project("proj:types", "nsid", "app.example.recordMixed")
        project["input_nsids"] = ["app.example.recordMixed", "app.example.query", "app.example.unknown"]
        project["lexicons"] = ["app.example.space"]
        project["resolved_nsids"] = ["app.example.token"]
        project["recent_record_samples"] = [{"collection": "app.example.definition"}]
        project["publication_schemas"] = [
            {"schema_id": "app.example.query", "schema_type": "query"},
            {"schema_id": "app.example.space", "schema_type": "space"},
            {"schema_id": "app.example.unknown", "schema_type": "unknown"}]
        project["lexicon_schema_samples"] = [{"nsid": "app.example.token", "defs": {"main": {"type": "token"}}}]
        project["lexicon_resolution_entries"] = [resolution("app.example.definition", "did:plc:a")]
        self.assertEqual(merge.project_collection_nsids(project), {"app.example.recordMixed", "app.example.unknown"})

    def test_unverified_newer_observation_and_catalog_changes_suppress_old_witness(self):
        project = self.make_project("proj:publisher", "nsid", "app.example.schema")
        sid = "app.example.schema"
        entry = resolution(sid, "did:plc:old")
        project["lexicon_resolution_entries"] = [entry]
        self.assertEqual(merge.project_lexicon_publishers(project)[0], {sid: "did:plc:old"})
        project["lexicon_resolution_entries"].append({**entry, "repo_did": "did:plc:new",
            "resolved_at": "2026-10-14T15:21:40+00:00", "publication_validation": {}})
        self.assertEqual(merge.project_lexicon_publishers(project)[0], {})
        project["lexicon_resolution_entries"] = [entry]
        for status in ("pending", "invalid", "deleted"):
            project["publication_schemas"] = [{"schema_id": sid, "status": status,
                "publishers": [{"did": "did:plc:mirror", "hash": "fixture-hash"}]}]
            mapping, details = merge.project_lexicon_publishers(project)
            self.assertEqual(mapping, {})
            self.assertFalse(any(item["included_in_comparison"] for item in details[sid]))

    def test_catalog_verified_publisher_requires_freshness_and_no_attribution_conflict(self):
        project = self.make_project("proj:catalog", "nsid", "app.example.schema")
        sid = "app.example.schema"
        publication = {"schema_id": sid, "status": "verified", "publisher_did": "did:plc:verified",
                       "next_verification_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
        project["publication_schemas"] = [publication]
        self.assertEqual(merge.project_lexicon_publishers(project)[0], {sid: "did:plc:verified"})
        publication["attribution_conflict"] = True
        self.assertEqual(merge.project_lexicon_publishers(project)[0], {})
        publication["attribution_conflict"] = False
        publication["next_verification_at"] = "2020-01-01T00:00:00+00:00"
        self.assertEqual(merge.project_lexicon_publishers(project)[0], {})

    def test_catalog_refresh_is_auxiliary_and_does_not_mutate_projects_or_candidates(self):
        import copy
        source = self.make_project("proj:source", "nsid", "app.example.schema")
        target = self.make_project("proj:target", "nsid", "app.example.schema")
        source["publication_schemas"] = [{"schema_id": "app.example.schema", "schema_type": "record", "status": "pending"}]
        before = copy.deepcopy(source)
        initial = merge.inspect_merge_pair_payload(source, target)
        current = {"app.example.schema": {"schema_id": "app.example.schema", "schema_type": "definitions",
            "status": "verified", "publisher_did": "did:plc:new",
            "next_verification_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}}
        with mock.patch.object(merge, "fetch_collection_first_creator_did") as fetch:
            result = merge.inspect_merge_pair_payload(source, target, current_publications=current,
                                                       include_first_creator_details=True)
            fetch.assert_not_called()
        self.assertEqual(result["source_pair_candidate"], initial["source_pair_candidate"])
        self.assertEqual(result["target_pair_candidate"], initial["target_pair_candidate"])
        self.assertEqual(result["strict_overlap"], initial["strict_overlap"])
        self.assertEqual(result["moderate_overlap"], initial["moderate_overlap"])
        self.assertEqual(result["source_lexicon_publisher_dids"], {"app.example.schema": "did:plc:new"})
        self.assertEqual(source, before)
