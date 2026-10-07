from __future__ import annotations

import copy
import contextlib

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry
from project_registry_core import identifier_storage, project_record, runtime_view
from project_registry_core.application import merge


class _CandidateConfirmedMergeFixtures:
    def pair(self, kind="repo_url", role="project_repository", value="https://github.com/example/shared"):
        projects = [self.make_project("proj:left", "repo_url", "https://github.com/a/left"),
                    self.make_project("proj:right", "repo_url", "https://github.com/b/right")]
        for project, classification in zip(projects, ("candidate", "confirmed")):
            project[runtime_view.RUNTIME_IDENTIFIER_ROWS] = [dict(
                kind=kind, role=role, value=value, classification=classification,
                is_primary=False, provider="", metadata={}, observed_at="",
            )]
            project[runtime_view.RUNTIME_IDENTIFIER_BASELINE] = []
        return projects


class CandidateConfirmedMergeUnitTest(_CandidateConfirmedMergeFixtures, ProjectRecordTestCase):
    def test_shared_resource_counts_and_alias_do_not_make_auto_ready(self):
        left, right = self.pair()
        for project in (left, right):
            project["name"] = "Shared"
            row = project[runtime_view.RUNTIME_IDENTIFIER_ROWS][0]
            project[runtime_view.RUNTIME_IDENTIFIER_ROWS] = [dict(row, value=f"https://github.com/example/shared{i}") for i in range(10)]
        candidate = registry.duplicate_candidate_for_pair(left, right)
        self.assertEqual(candidate["score"], 8)
        self.assertEqual(candidate["merge_confidence"], "medium")
        self.assertFalse(candidate["auto_merge_ready"])
        self.assertIn("candidate_confirmed_identifiers", registry.batch_moderate_overlap(left, right))
        self.assertEqual(registry.batch_strict_overlap(left, right), {})
        # An independent existing auto-ready signal retains its meaning.
        left["service_urls"] = right["service_urls"] = ["https://independent.test"]
        self.assertTrue(registry.duplicate_candidate_for_pair(left, right)["auto_merge_ready"])

    def test_developer_roles_different_roles_and_nonclaims_do_not_match(self):
        for kind, role, value in [("account", "developer_account", "example.test"),
                                  ("handle", "developer_handle", "example.test"),
                                  ("did", "developer_did", "did:plc:example"),
                                  ("nsid", "referenced_nsid", "app.example.post")]:
            self.assertIsNone(registry.duplicate_candidate_for_pair(*self.pair(kind, role, value)))
        left, right = self.pair("handle", "project_handle", "example.test")
        right[runtime_view.RUNTIME_IDENTIFIER_ROWS][0]["role"] = "developer_handle"
        self.assertIsNone(registry.duplicate_candidate_for_pair(left, right))
        left, right = self.pair()
        right[runtime_view.RUNTIME_IDENTIFIER_ROWS][0]["classification"] = "derived"
        self.assertIsNone(registry.duplicate_candidate_for_pair(left, right))

    def test_guards_and_separate_in_either_direction(self):
        for guarded in (0, 1):
            for decision in ("blocked_identifiers", "deferred_identifiers", "separate_project_keys"):
                pair = self.pair("service_url", "service", "https://example.test/app")
                if decision == "separate_project_keys":
                    pair[guarded][decision] = [pair[1 - guarded]["project_key"]]
                else:
                    pair[guarded][decision] = [{"type": "service_url", "value": "https://example.test",
                                               "expires_at": "2999-01-01T00:00:00+00:00"}]
                with self.subTest(guarded=guarded, decision=decision):
                    self.assertEqual(registry.compute_duplicate_candidates(None, pair[0], projects=pair), [])
                    self.assertEqual(registry.batch_moderate_overlap(*pair), {})
        left, right = self.pair()
        left["deferred_identifiers"] = [{"type": "repo_url", "value": "https://github.com/example/shared", "expires_at": "2000-01-01T00:00:00+00:00"}]
        self.assertIsNotNone(registry.duplicate_candidate_for_pair(left, right))

    def test_candidate_candidate_and_case_distinct_nsid_are_not_new_matches(self):
        left, right = self.pair("nsid", "owned_nsid", "app.example.Post")
        right[runtime_view.RUNTIME_IDENTIFIER_ROWS][0]["value"] = "app.example.post"
        self.assertIsNone(registry.duplicate_candidate_for_pair(left, right))
        right[runtime_view.RUNTIME_IDENTIFIER_ROWS][0].update(value="app.example.Post", classification="candidate")
        self.assertIsNone(registry.duplicate_candidate_for_pair(left, right))


class CandidateConfirmedMergeTest(_CandidateConfirmedMergeFixtures, TempRegistryDbTestCase):
    def test_all_component_roles_both_directions_and_index(self):
        claims = [("repo_url", "project_repository", "https://github.com/example/shared"),
                  ("service_url", "service", "https://example.test/app"),
                  ("site_url", "project_site", "https://example.test"),
                  ("nsid", "owned_nsid", "app.example.Test"),
                  ("account", "project_account", "example.test"),
                  ("handle", "project_handle", "example.test"),
                  ("did", "brand_did", "did:plc:example")]
        self.assertEqual({role for _, role, _ in claims}, identifier_storage.COMPONENT_ROLES)
        for kind, role, value in claims:
            left, right = self.pair(kind, role, value)
            before = copy.deepcopy([left, right])
            for project, other, classification in ((left, right, "candidate"), (right, left, "confirmed")):
                with self.subTest(kind=kind, classification=classification):
                    candidates = registry.compute_duplicate_candidates(None, project, projects=[left, right])
                    self.assertEqual(len(candidates), 1)
                    self.assertEqual(candidates[0], registry.duplicate_candidate_for_pair(project, other))
                    self.assertEqual(candidates[0]["identifier_matches"], [dict(
                        kind=kind, role=role, value=value, project_classification=classification,
                        other_classification="confirmed" if classification == "candidate" else "candidate",
                    )])
                    self.assertFalse(candidates[0]["auto_merge_ready"])
            self.assertEqual([left, right], before)

    def test_persisted_pair_modes_mutual_condition_and_no_writes(self):
        with contextlib.closing(registry.open_db()) as conn:
            for project in self.pair():
                project_record.save_project(conn, project)
            conn.commit()
            before = list(conn.iterdump())
            self.assertEqual(registry.build_batch_merge_plan(conn, "strict")[0], [])
            for mode in ("moderate", "broad"):
                plans, _ = registry.build_batch_merge_plan(conn, mode)
                self.assertEqual(len(plans), 1)
                self.assertFalse(plans[0]["recommendation"]["auto_merge_ready"])
                source = project_record.fetch_project(conn, plans[0]["source_project_key"])
                classification = runtime_view.project_identifiers(source)[0].classification
                self.assertEqual(plans[0]["identifier_matches"][0]["project_classification"], classification)
            with self.assertRaisesRegex(ValueError, "not auto-merge-ready"):
                merge.merge_projects(conn, project_key="proj:left", use_recommendation=True, execute=True)
            self.assertEqual(list(conn.iterdump()), before)
            # A third project with a stronger existing signal takes precedence.
            right = project_record.fetch_project(conn, "proj:right")
            third = self.make_project("proj:third", "repo_url", "https://github.com/c/third")
            right["service_urls"] = third["service_urls"] = ["https://strong.test"]
            project_record.save_project(conn, right)
            project_record.save_project(conn, third)
            conn.commit()
            plans, skipped = registry.build_batch_merge_plan(conn, "moderate")
            self.assertFalse(any("proj:left" in (p["source_project_key"], p["target_project_key"]) for p in plans))
            self.assertTrue(any(p["reason"] == "not_mutual_recommendation" and p["project_key"] == "proj:left" for p in skipped))

    def test_noncurrent_row_does_not_create_candidate(self):
        for standing in ("former", "non_production"):
            with self.subTest(standing=standing), contextlib.closing(registry.open_db()) as conn:
                for project in self.pair():
                    project_record.save_project(conn, project)
                conn.commit()
                identifier_storage.move_current_to_noncurrent(
                    conn, project_key="proj:right", kind="repo_url", value="https://github.com/example/shared",
                    role="project_repository", standing=standing, reason="retired", recorded_at="2026-09-27",
                )
                conn.commit()
                left = project_record.fetch_project(conn, "proj:left")
                self.assertEqual(registry.compute_duplicate_candidates(conn, left), [])
                conn.execute("DELETE FROM projects")
                conn.execute("DELETE FROM project_revisions")
                conn.commit()
