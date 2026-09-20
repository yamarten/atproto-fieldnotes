from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record, source_sync, tangled, tangled_identity


PARENT = "did:plc:o32gsn3jolddqna6w4kdzuv2"


class TangledForkSyncTest(TempRegistryDbTestCase):
    scope = {"source": "jetstream_v2", "service": "https://jetstream.us-west.bsky.network",
             "collection": "sh.tangled.repo"}

    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)
        registry.write_sync_state(self.conn, "tangled-repos", self.scope,
                                  {"seq": 100, "pending_owner_records": []})
        self.conn.commit()

    def entry(self, seq=101, rkey="fork", **record):
        value = {"name": rkey, "description": "atproto project", **record}
        return {"owner_did": "did:plc:fixtureowner", "rkey": rkey, "seq": seq,
                "operation": "create", "record": value,
                "atproto_relevant": tangled.atproto_relevant(value)}

    def sync(self, entries, *, truncated=False, owner_handle="fork.example", **options):
        snapshot = {"events": entries, "event_count": len(entries),
                    "sealed_tip_seq": 500, "last_event_seq": max((e["seq"] for e in entries), default=100),
                    "truncated": truncated}
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=snapshot), \
                mock.patch.object(tangled_identity, "resolve_owner_handle", return_value=owner_handle):
            return source_sync.sync_tangled_repos(self.conn, **options)

    def state(self):
        return registry.read_sync_state(self.conn, "tangled-repos", self.scope)["watermark"]

    def test_source_contract_distinguishes_unknown_from_declared_parent(self):
        cases = [
            ({}, None, "missing"),
            ({"source": None}, None, "missing"),
            ({"source": ""}, None, "missing"),
            ({"source": PARENT}, True, "repo_did"),
            ({"source": "at://did:web:parent.example/sh.tangled.repo/3m66yykkd2a22"}, True, "repo_at_uri"),
            ({"source": "https://github.com/owner/repo.git"}, None, "unrecognized"),
            ({"source": "git@example.com:owner/repo.git"}, None, "unrecognized"),
            ({"source": "https://tangled.org/owner.example/repo"}, None, "unrecognized"),
            ({"source": {}}, None, "invalid"),
            ({"source": False}, None, "invalid"),
            ({"source": "did:plc:"}, None, "invalid"),
            ({"source": "did:web:broken%"}, None, "invalid"),
            ({"source": " " + PARENT}, None, "invalid"),
            ({"source": "at://did:plc:parent/app.bsky.feed.post/key"}, None, "invalid"),
            ({"source": "at://did:plc:parent/sh.tangled.repo/.."}, None, "invalid"),
            ({"source": "at://did:plc:parent/sh.tangled.repo/key?x=1"}, None, "invalid"),
            ({"source": "at://did:plc:parent/sh.tangled.repo/key?"}, None, "invalid"),
            ({"source": "at://did:plc:parent/sh.tangled.repo/key/more"}, None, "invalid"),
        ]
        for record, fork, status in cases:
            with self.subTest(record=record):
                metadata = source_sync.tangled_fork_metadata(record)
                self.assertIs(metadata["fork"], fork)
                self.assertEqual(metadata["fork_source_status"], status)
                self.assertEqual(metadata["fork_source"], record["source"] if fork else "")

    def test_real_fork_records_are_excluded_in_normal_sync(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/project_registry/tangled-fork-records.json").read_text())
        for case in fixture["cases"]:
            with self.subTest(url=case["repo_url"]):
                record = case["record"]
                handle, name = case["repo_url"].removeprefix("https://tangled.org/").split("/")
                entry = self.entry(rkey=name, **record)
                entry["owner_did"] = case["owner_did"]
                result = self.sync([entry], execute=True, truncated=True, owner_handle=handle)
                self.assertEqual((result["created"], result["skipped_forks"]), (0, 1))
                self.assertEqual(result["results"][0]["repo_url"], case["repo_url"])
                self.assertEqual(result["results"][0]["fork_source"], record["source"])
                self.assertEqual(result["results"][0]["reason"], "tangled_fork_excluded_from_discovery")
        self.assertEqual(registry.all_projects(self.conn), [])
        self.assertEqual(self.state()["seq"], 101)

    def test_mixed_discovery_retains_unknowns_and_reports_exclusions(self):
        entries = [self.entry(source=PARENT), self.entry(102, "original"),
                   self.entry(103, "external", source="https://github.com/example/repo"),
                   self.entry(104, "malformed", source={"unexpected": PARENT})]
        events = []
        result = self.sync(entries, execute=True, truncated=True, progress=lambda *args: events.append(args[-1]))
        self.assertEqual((result["created"], result["skipped_forks"], result["unmatched"]), (3, 1, 0))
        self.assertEqual(len(registry.all_projects(self.conn)), 3)
        self.assertEqual(self.state()["seq"], 104)
        self.assertTrue(any(e.get("outcome") == "skipped_fork" and e.get("reason") for e in events))
        self.assertTrue(all(r["fork"] is None for r in result["results"][1:]))

    def test_fork_only_advances_sealed_tip_even_when_creation_disabled(self):
        result = self.sync([self.entry(source=PARENT)], execute=True, create_unmatched=False)
        self.assertEqual(result["skipped_forks"], 1)
        self.assertTrue(result["state_updated"])
        self.assertEqual(self.state()["seq"], 500)

    def test_unmatched_unknown_still_blocks_checkpoint_without_creation(self):
        result = self.sync([self.entry(source=PARENT), self.entry(102, "unknown")],
                           execute=True, create_unmatched=False)
        self.assertEqual((result["unmatched"], result["skipped_forks"]), (1, 1))
        self.assertFalse(result["state_updated"])
        self.assertEqual(self.state()["seq"], 100)

    def test_dry_run_never_persists_and_all_history_still_excludes(self):
        for options in ({}, {"execute": True, "all_history": True}, {"execute": True, "incremental": False}):
            with self.subTest(options=options):
                result = self.sync([self.entry(source=PARENT)], **options)
                self.assertEqual(result["skipped_forks"], 1)
                self.assertEqual(registry.all_projects(self.conn), [])
                if not options.get("execute") or options.get("incremental") is False:
                    self.assertFalse(result["state_updated"])

    def test_independent_fork_uses_explicit_registration_then_exact_updates(self):
        # Website/keywords do not override normal discovery's fork:false policy.
        entry = self.entry(source=PARENT, website="https://independent.example.com")
        self.assertEqual(self.sync([entry], execute=True)["skipped_forks"], 1)
        project = registry.ensure_project_for_seed(self.conn, "repo_url", "https://tangled.org/fork.example/fork", source="manual")
        registry.add_manual_seed(project, "service_url", "https://independent.example.com", source="manual")
        project_record.save_project(self.conn, project)
        key = project["project_key"]
        result = self.sync([entry], execute=True)
        self.assertEqual((result["matched"], result["created"], result["skipped_forks"]), (1, 0, 0))
        updated = project_record.fetch_project(self.conn, key)
        self.assertIn("tangled_jetstream", updated["discovery_sources"])
        self.assertIn("https://independent.example.com", updated["service_urls"])
        self.assertEqual(len(registry.all_projects(self.conn)), 1)

    def test_unknown_owner_fork_remains_pending_until_exact_match_is_possible(self):
        entry = self.entry(source=PARENT)
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value={
            "events": [entry], "sealed_tip_seq": 200, "last_event_seq": 101,
        }), mock.patch.object(tangled_identity, "resolve_owner_handle", side_effect=tangled_identity.OwnerUnavailable("no_claim")):
            result = source_sync.sync_tangled_repos(self.conn, execute=True)
        self.assertEqual((result["skipped_owner"], result["skipped_forks"]), (1, 0))
        self.assertEqual(len(self.state()["pending_owner_records"]), 1)
        recovered = self.sync([], execute=True)
        self.assertEqual((recovered["retried_owner_records"], recovered["skipped_forks"]), (1, 1))
        self.assertEqual(self.state()["pending_owner_records"], [])

    def test_delete_and_latest_update_are_folded_before_fork_selection(self):
        fork = self.entry(source=PARENT)
        deletion = {**fork, "operation": "delete", "seq": 102, "record": {}}
        result = self.sync([fork, deletion], execute=True)
        self.assertEqual((result["selected"], result["skipped_forks"]), (0, 0))
        result = self.sync([fork, self.entry(103)], execute=True)
        self.assertEqual((result["created"], result["skipped_forks"]), (1, 0))

    def test_cancel_on_fork_result_does_not_advance_checkpoint(self):
        cancelled = False
        def progress(*args):
            nonlocal cancelled
            cancelled = args[-1].get("outcome") == "skipped_fork"
        result = self.sync([self.entry(source=PARENT), self.entry(102, "later")], execute=True,
                           progress=progress, cancel_requested=lambda: cancelled)
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["state_updated"])
        self.assertEqual(self.state()["seq"], 100)
        self.assertEqual(registry.all_projects(self.conn), [])

    def test_failure_rolls_back_catalog_and_checkpoint_after_fork(self):
        with mock.patch.object(source_sync, "write_sync_state", side_effect=RuntimeError("write failed")):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                self.sync([self.entry(source=PARENT), self.entry(102, "normal")], execute=True)
        self.assertEqual(self.state()["seq"], 100)
        self.assertEqual(registry.all_projects(self.conn), [])

    def test_github_default_excludes_forks_and_explicit_query_is_preserved(self):
        self.assertIn("fork:false", source_sync.github_search_query_with_defaults("atproto"))
        self.assertNotIn("fork:false", source_sync.github_search_query_with_defaults("atproto fork:only"))
