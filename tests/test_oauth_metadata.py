from __future__ import annotations

import copy
import contextlib
import json
import subprocess
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import oauth_metadata, project_record, relations
from project_registry_core.application import presentation
from project_registry_core.evidence import EvidenceBundle


class OAuthMetadataTest(TempRegistryDbTestCase):
    url = "https://app.example/client-metadata.json"

    def collect(self, scope, *, date="2026-09-22T01:00:00+00:00", url=None):
        project = self.make_project("proj:oauth", "service_url", "https://app.example")
        with mock.patch.object(registry.http_transport, "request_json", return_value={
            "client_id": self.url, "client_uri": "https://untrusted.example", "scope": scope,
        }) as fetch, mock.patch.object(project_record, "iso_now", return_value=date):
            bundle = registry.collect_oauth_metadata_evidence(project, url or self.url)
        fetch.assert_called_once()
        return project, bundle

    def test_official_parser_actions_targets_and_audiences(self):
        result = oauth_metadata.analyze_scope(
            "atproto repo:app.example.post?action=delete "
            "repo?collection=app.example.one&collection=app.example.two&action=create&action=update "
            "repo:app.example.default rpc:app.bsky.actor.getProfile?aud=did:web:api.bsky.app%23bsky_appview "
            "include:app.example.permissions?aud=did:web:api.example%23appview blob:image/* account:email identity:handle"
        )
        self.assertEqual(result["status"], "parsed")
        permissions = result["permissions"]
        self.assertEqual(permissions[1]["collection"], ["app.example.post"])
        self.assertEqual(permissions[1]["action"], ["delete"])
        self.assertCountEqual(permissions[2]["collection"], ["app.example.one", "app.example.two"])
        self.assertCountEqual(permissions[2]["action"], ["create", "update"])
        self.assertCountEqual(permissions[3]["action"], ["create", "update", "delete"])
        self.assertEqual(permissions[4]["aud"], "did:web:api.bsky.app#bsky_appview")
        self.assertEqual(permissions[5]["nsid"], "app.example.permissions")
        self.assertEqual(permissions[5]["resolution"], "unresolved")
        self.assertEqual(permissions[6]["accept"], ["image/*"])
        self.assertEqual(permissions[7]["action"], ["read"])
        self.assertEqual(permissions[8]["attr"], "handle")

    def test_unknown_and_invalid_do_not_become_auth_only_or_nsids(self):
        for token in ("future:unknown", "repo:app.example.post?action=read", "repo:app.example.*", "rpc:app.example.get"):
            with self.subTest(token=token):
                result = oauth_metadata.analyze_scope(f"atproto {token}")
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["unrecognized"], [token])
                self.assertFalse(result["atproto_only"])
                self.assertEqual(relations.oauth_analysis_relation_records(self.url, result, "2026-09-22"), [])
        for value, status in ((None, "missing"), ([], "invalid_type"), ("", "empty"), ("a" * 16385, "too_large")):
            result = oauth_metadata.analyze_scope(value)
            self.assertEqual(result["status"], status)
            self.assertFalse(result["atproto_only"])

    def test_wildcards_transitions_and_include_are_not_actual_data_use(self):
        project, bundle = self.collect("atproto repo:* transition:generic transition:chat.bsky transition:email rpc:*?aud=* blob:*/*")
        registry.apply_investigation_evidence(project, bundle)
        self.assertEqual(project["relation_evidence"], [])
        self.assertEqual(project_record.compute_atproto_contribution(project)["atproto_contribution_level"], "unknown")
        self.assertFalse(project["oauth_client_metadata"][0]["analysis"]["atproto_only"])
        project, bundle = self.collect("atproto include:app.example.permissions?aud=did:web:api.example%23appview")
        registry.apply_investigation_evidence(project, bundle)
        relation = project["relation_evidence"][0]
        self.assertEqual(relation["relation"]["object"]["value"], "app.example.permissions")
        self.assertEqual(relation["relation"]["subject"]["value"], self.url)
        self.assertEqual(relation["context"]["scope_permissions"][0]["resolution"], "unresolved")
        self.assertFalse(registry.project_owned_lexicon_nsid(project, "app.example.permissions"))
        self.assertEqual(project_record.compute_atproto_contribution(project)["atproto_contribution_level"], "optional")

    def test_base_only_is_saved_and_displayed_without_auth_only_classification(self):
        project, bundle = self.collect("atproto")
        registry.apply_investigation_evidence(project, bundle)
        self.assertTrue(project["oauth_client_metadata"][0]["analysis"]["atproto_only"])
        self.assertFalse(project["atproto_auth_only"])
        self.assertEqual(project_record.compute_atproto_contribution(project)["atproto_contribution_level"], "unknown")
        with contextlib.closing(registry.open_db()) as conn:
            project_record.save_project(conn, project)
            with mock.patch.object(oauth_metadata, "_parse_json", side_effect=AssertionError("read starts parser")):
                saved = project_record.fetch_project(conn, project["project_key"])
                detail = presentation.project_detail(conn, project_key=project["project_key"])
            self.assertEqual(saved["oauth_client_metadata"], project["oauth_client_metadata"])
            review = detail["service_ownership_review"]
            self.assertEqual(review["oauth_status"], "observed")
            self.assertEqual(review["oauth_permissions"], [])
            self.assertTrue(review["oauth_clients"][0]["analysis"]["atproto_only"])
            state = json.loads(conn.execute("SELECT state_json FROM projects").fetchone()[0])
            self.assertIn("oauth_client_metadata", state["evidence"])

    def test_scope_reduction_replay_and_merge_keep_latest_snapshot(self):
        project, old = self.collect("atproto repo:app.example.post?action=delete")
        registry.apply_investigation_evidence(project, old)
        older_project = copy.deepcopy(project)
        self.assertEqual(len(project["relation_evidence"]), 1)
        _, latest = self.collect("atproto", date="2026-09-22T02:00:00+00:00")
        registry.apply_investigation_evidence(project, latest)
        self.assertEqual(project["relation_evidence"], [])
        legacy = EvidenceBundle(project_key=project["project_key"], collected_at=old.collected_at, records=
            relations.oauth_metadata_relation_records(self.url, {"scope": "atproto repo:app.example.post"}, old.collected_at))
        registry.apply_investigation_evidence(project, legacy)
        self.assertEqual(project["relation_evidence"], [])
        with mock.patch.object(oauth_metadata, "_parse_json", side_effect=AssertionError("replay starts parser")):
            registry.apply_replay_evidence(project, EvidenceBundle.from_dict(old.to_dict()))
        self.assertEqual(project["relation_evidence"], [])
        merged = registry.merge_project_records(project, {**older_project, "project_key": "proj:other"})
        self.assertEqual(merged["relation_evidence"], [])
        with contextlib.closing(registry.open_db()) as conn:
            project_record.save_project(conn, older_project)
            saved = project_record.fetch_project(conn, older_project["project_key"])
            registry.apply_investigation_evidence(saved, latest)
            project_record.save_project(conn, saved)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_relations WHERE predicate='requests_permission'").fetchone()[0], 0)

    def test_clients_stay_separate_and_failed_fetch_does_not_replace_observation(self):
        project, first = self.collect("atproto repo:app.example.post")
        registry.apply_investigation_evidence(project, first)
        _, second = self.collect("atproto", url="https://app.example/other-client.json")
        registry.apply_investigation_evidence(project, second)
        self.assertEqual(len(project["oauth_client_metadata"]), 2)
        self.assertEqual(len(project["relation_evidence"]), 1)
        with mock.patch.object(registry.http_transport, "request_json", side_effect=ValueError("failed")):
            failure = registry.collect_oauth_metadata_evidence(project, self.url)
        registry.apply_investigation_evidence(project, failure)
        self.assertEqual(len(project["relation_evidence"]), 1)
        self.assertTrue(failure.failures)

    def test_unavailable_parser_preserves_raw_scope_and_failure(self):
        for failure in (RuntimeError("not installed"), subprocess.TimeoutExpired("node", 5)):
            with self.subTest(failure=failure), mock.patch.object(oauth_metadata, "_parse_json", side_effect=failure):
                project, bundle = self.collect("atproto repo:app.example.post")
            registry.apply_investigation_evidence(project, bundle)
            observation = project["oauth_client_metadata"][0]
            self.assertEqual(observation["scope"], "atproto repo:app.example.post")
            self.assertEqual(observation["analysis"]["status"], "unavailable")
            self.assertEqual(bundle.failures[0].operation, "parse")
            self.assertEqual(bundle.collector_results[-1].status, "failed")

    def test_cached_results_are_independent(self):
        oauth_metadata._parse_json.cache_clear()
        with mock.patch.object(oauth_metadata.subprocess, "run", wraps=subprocess.run) as run:
            first = oauth_metadata.analyze_scope("atproto repo:app.example.cached")
            first["permissions"].clear()
            second = oauth_metadata.analyze_scope("atproto repo:app.example.cached")
        run.assert_called_once()
        self.assertEqual(len(second["permissions"]), 2)

    def test_separate_tokens_for_same_collection_keep_both_operations(self):
        project, bundle = self.collect("atproto repo:app.example.post?action=create repo:app.example.post?action=delete")
        registry.apply_investigation_evidence(project, bundle)
        self.assertEqual(len(project["relation_evidence"]), 1)
        permissions = project["relation_evidence"][0]["context"]["scope_permissions"]
        self.assertEqual([p["action"] for p in permissions], [["create"], ["delete"]])
