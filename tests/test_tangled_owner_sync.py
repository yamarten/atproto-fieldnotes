from __future__ import annotations

import io
import json
import urllib.error
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import source_sync, tangled_identity, worker
from project_registry_core.jobs import CancellationToken, OperationRequest


def http_error(status, error="Unknown", message="remote response"):
    return urllib.error.HTTPError("https://identity.example", status, "error", {},
                                  io.BytesIO(json.dumps({"error": error, "message": message}).encode()))


class TangledOwnerSyncTest(TempRegistryDbTestCase):
    scope = {"source": "jetstream_v2", "service": "https://jetstream.us-west.bsky.network",
             "collection": "sh.tangled.repo"}

    def entry(self, owner="one", seq=101, rkey="repo", **changes):
        return {"owner_did": f"did:plc:{owner}", "seq": seq, "rkey": rkey,
                "operation": "create", "time": "2026-09-19T00:00:00Z",
                "record": {"name": rkey, "description": "atproto project", "repoDid": "did:plc:repository"},
                "atproto_relevant": True, **changes}

    def snapshot(self, entries, *, truncated=False):
        return {"events": entries, "event_count": len(entries),
                "sealed_tip_seq": max(200, max((item["seq"] for item in entries), default=200)),
                "last_event_seq": max((item["seq"] for item in entries), default=200),
                "truncated": truncated, "cancelled": False}

    def seed_state(self, conn, pending=None):
        registry.write_sync_state(conn, "tangled-repos", self.scope,
                                  {"seq": 100, "pending_owner_records": pending or []})
        conn.commit()

    def state(self, conn):
        return registry.read_sync_state(conn, "tangled-repos", self.scope)["watermark"]

    def resolve(self, did):
        if did == "did:plc:one":
            raise tangled_identity.OwnerUnavailable("no_valid_claimed_handle")
        return "valid.example"

    def test_profiles_are_not_required_for_identity(self):
        for error, message in [("AccountTakedown", "Account has been suspended"),
                               ("InvalidRequest", "Profile not found")]:
            with self.subTest(error=error):
                def request(url, **kwargs):
                    if "getProfile" in url:
                        raise http_error(400, error, message)
                    self.assertIn("com.atproto.identity.resolveHandle?handle=valid.example", url)
                    return {"did": "did:plc:one"}
                with mock.patch.object(tangled_identity, "resolve_did_document", return_value={
                    "id": "did:plc:one", "alsoKnownAs": ["at://valid.example"],
                }), mock.patch.object(tangled_identity.http_transport, "request_json", side_effect=request) as fetch:
                    self.assertEqual(tangled_identity.resolve_owner_handle("did:plc:one"), "valid.example")
                self.assertEqual(fetch.call_count, 1)

    def test_identity_conditions_are_deferred_without_fabricating_handles(self):
        cases = [
            ({"id": "did:plc:other"}, {}, "did_document_id_mismatch"),
            ({"id": "did:plc:one"}, {}, "no_valid_claimed_handle"),
            ({"id": "did:plc:one", "alsoKnownAs": ["at://handle.invalid"]}, {}, "no_valid_claimed_handle"),
            ({"id": "did:plc:one", "alsoKnownAs": ["at://bad.example/path", "at://bad.123"]}, {}, "no_valid_claimed_handle"),
            ({"id": "did:plc:one", "alsoKnownAs": ["at://old.example", "at://valid.example"]},
             {"did": "did:plc:other"}, "handle_did_mismatch"),
        ]
        for document, identity, reason in cases:
            with self.subTest(reason=reason), mock.patch.object(tangled_identity, "resolve_did_document", return_value=document), mock.patch.object(tangled_identity.http_transport, "request_json", return_value=identity):
                with self.assertRaisesRegex(tangled_identity.OwnerUnavailable, reason):
                    tangled_identity.resolve_owner_handle("did:plc:one")

    def test_http_target_conditions_and_global_failures_are_distinct(self):
        for stage in ("did_document", "resolve_handle"):
            cases = [(404, "Unknown", stage == "did_document"), (410, "Unknown", stage == "did_document"),
                     (400, "HandleNotFound", stage == "resolve_handle"), (400, "InvalidRequest", False),
                     (401, "AuthRequired", False), (403, "Forbidden", False), (429, "RateLimitExceeded", False),
                     (503, "Unavailable", False)]
            for status, error, unavailable in cases:
                with self.subTest(stage=stage, status=status), mock.patch.object(tangled_identity.time, "sleep") as sleep:
                    operation = mock.Mock(side_effect=lambda: (_ for _ in ()).throw(http_error(status, error)))
                    expected = tangled_identity.OwnerUnavailable if unavailable else RuntimeError
                    with self.assertRaises(expected) as caught:
                        tangled_identity._request("did:plc:one", stage, operation)
                    self.assertEqual(operation.call_count, 3 if status in {429, 503} else 1)
                    if not unavailable:
                        self.assertIn("did:plc:one", str(caught.exception))
                        self.assertIn(stage, str(caught.exception))
                    self.assertTrue(all(call.args[0] <= 2 for call in sleep.call_args_list))

    def test_network_failure_and_malformed_identity_abort(self):
        for failure in (urllib.error.URLError("network down"), TimeoutError("timeout")):
            with self.subTest(failure=failure), mock.patch.object(tangled_identity.time, "sleep"):
                operation = mock.Mock(side_effect=failure)
                with self.assertRaisesRegex(RuntimeError, "did:plc:one.*did_document"):
                    tangled_identity._request("did:plc:one", "did_document", operation)
                self.assertEqual(operation.call_count, 3)
        with mock.patch.object(tangled_identity, "resolve_did_document", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "malformed DID document"):
                tangled_identity.resolve_owner_handle("did:plc:one")

    def test_public_resolver_unresolved_response_is_retained_not_a_global_400(self):
        with self.assertRaisesRegex(tangled_identity.OwnerUnavailable, "handle_unresolved"):
            tangled_identity._request("did:plc:one", "resolve_handle", mock.Mock(
                side_effect=http_error(400, "InvalidRequest", "Unable to resolve handle")))
        with self.assertRaisesRegex(RuntimeError, "HTTP 400"):
            tangled_identity._request("did:plc:one", "resolve_handle", mock.Mock(
                side_effect=http_error(400, "InvalidRequest", "Unknown query parameter")))

    def test_rate_limit_retry_wait_is_bounded_and_can_recover(self):
        error = http_error(429)
        error.headers = {"Retry-After": "120"}
        operation = mock.Mock(side_effect=[error, {"did": "did:plc:one"}])
        with mock.patch.object(tangled_identity.time, "sleep") as sleep:
            result = tangled_identity._request("did:plc:one", "resolve_handle", operation)
        self.assertEqual(result, {"did": "did:plc:one"})
        sleep.assert_called_once_with(2.0)

    def test_worker_failure_preserves_owner_stage_and_http_status(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        events = []
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot([self.entry()])), mock.patch.object(tangled_identity, "resolve_did_document", side_effect=http_error(401)):
            result = worker._run_simple(conn, OperationRequest(
                operation="sync", operation_id="owner-error", execute=True, parameters={"source": "tangled"},
            ), CancellationToken(), events.append)
        self.assertEqual(result.status, "failed")
        self.assertIn("did:plc:one: did_document HTTP 401", result.error["message"])
        self.assertEqual(events[-1].data["error"], result.error)

    def test_mixed_owners_are_saved_with_catalog_and_recovered_on_next_run(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        self.seed_state(conn)
        entries = [self.entry(), self.entry(seq=102, rkey="second"), self.entry("two", 103)]
        progress = []
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot(entries, truncated=True)), mock.patch.object(tangled_identity, "resolve_owner_handle", side_effect=self.resolve) as resolve:
            summary = source_sync.sync_tangled_repos(conn, execute=True, progress=lambda *args: progress.append(args))
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual((summary["created"], summary["skipped_owner"], summary["unresolved_owners"]), (1, 2, 1))
        self.assertEqual(summary["owner_errors"], [{"owner_did": "did:plc:one", "reason": "no_valid_claimed_handle"}])
        self.assertEqual(self.state(conn)["seq"], 103)
        self.assertEqual(len(self.state(conn)["pending_owner_records"]), 2)
        self.assertTrue(any(item[2].get("outcome") == "deferred_owner" for item in progress))
        conn.close()
        conn = registry.open_db()
        self.addCleanup(conn.close)
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot([])), mock.patch.object(tangled_identity, "resolve_owner_handle", return_value="recovered.example"):
            recovered = source_sync.sync_tangled_repos(conn, execute=True)
        self.assertEqual(recovered["created"], 2)
        self.assertEqual(recovered["retried_owner_records"], 2)
        self.assertEqual(self.state(conn), {"seq": 200, "pending_owner_records": []})
        self.assertEqual(len(registry.all_projects(conn)), 3)

    def test_pending_replay_is_bounded_rotates_and_folds_delete(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        self.seed_state(conn, [self.entry(seq=90, rkey="first"), self.entry(seq=91, rkey="second")])
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot([])), mock.patch.object(tangled_identity, "resolve_owner_handle", side_effect=self.resolve):
            summary = source_sync.sync_tangled_repos(conn, execute=True, max_items=1)
        self.assertEqual(summary["retried_owner_records"], 1)
        self.assertEqual([item["rkey"] for item in self.state(conn)["pending_owner_records"]], ["second", "first"])
        deletion = self.entry(seq=201, rkey="first", operation="delete", record={}, atproto_relevant=False)
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot([deletion])), mock.patch.object(tangled_identity, "resolve_owner_handle", side_effect=self.resolve):
            source_sync.sync_tangled_repos(conn, execute=True, max_items=1)
        self.assertEqual([item["rkey"] for item in self.state(conn)["pending_owner_records"]], ["second"])

    def test_display_limit_never_truncates_persisted_pending_records(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        self.seed_state(conn)
        entries = [self.entry(seq=101 + i, rkey=f"repo-{i}") for i in range(101)]
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot(entries)), mock.patch.object(tangled_identity, "resolve_owner_handle", side_effect=self.resolve):
            summary = source_sync.sync_tangled_repos(conn, execute=True, create_unmatched=False)
        self.assertEqual(len(summary["results"]), 100)
        self.assertEqual(len(self.state(conn)["pending_owner_records"]), 101)
        self.assertTrue(summary["state_updated"])

    def test_dry_run_and_unmatched_disabled_do_not_persist_catalog_or_watermark(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        self.seed_state(conn)
        for options in ({"execute": False}, {"execute": True, "create_unmatched": False}):
            with self.subTest(options=options), mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot([self.entry(), self.entry("two", 102)])), mock.patch.object(tangled_identity, "resolve_owner_handle", side_effect=self.resolve):
                summary = source_sync.sync_tangled_repos(conn, **options)
            self.assertFalse(summary["state_updated"])
            self.assertEqual(self.state(conn), {"seq": 100, "pending_owner_records": []})
            self.assertEqual(registry.all_projects(conn), [])

    def test_transient_after_valid_owner_leaves_all_writes_and_state_untouched(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        self.seed_state(conn)
        for failure in (http_error(503), http_error(429), urllib.error.URLError("offline")):
            with self.subTest(failure=failure), mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot([self.entry("two"), self.entry(seq=102)])), mock.patch.object(tangled_identity, "resolve_owner_handle", side_effect=["valid.example", failure]):
                with self.assertRaises(type(failure)):
                    source_sync.sync_tangled_repos(conn, execute=True)
            self.assertEqual(registry.all_projects(conn), [])
            self.assertEqual(self.state(conn)["seq"], 100)

    def test_write_failure_rolls_back_created_projects_and_catalog(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        self.seed_state(conn)
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot([self.entry("two"), self.entry(seq=102)])), mock.patch.object(tangled_identity, "resolve_owner_handle", side_effect=self.resolve), mock.patch.object(source_sync, "write_sync_state", side_effect=RuntimeError("state write failed")):
            with self.assertRaisesRegex(RuntimeError, "state write failed"):
                source_sync.sync_tangled_repos(conn, execute=True)
        self.assertEqual(registry.all_projects(conn), [])
        self.assertEqual(self.state(conn)["seq"], 100)
        conn.commit()
        self.assertEqual(registry.all_projects(conn), [])

    def test_cancellation_during_resolution_never_starts_writing(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        self.seed_state(conn)
        cancelled = False
        def resolve(did):
            nonlocal cancelled
            cancelled = True
            return "valid.example"
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot([self.entry(), self.entry("two", 102)])), mock.patch.object(tangled_identity, "resolve_owner_handle", side_effect=resolve) as fetch:
            summary = source_sync.sync_tangled_repos(conn, execute=True, cancel_requested=lambda: cancelled)
        self.assertTrue(summary["cancelled"])
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(summary["state_updated"])
        self.assertEqual(registry.all_projects(conn), [])

    def test_cancellation_after_a_write_preserves_project_but_not_checkpoint(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        self.seed_state(conn)
        cancelled = False
        def progress(current, total, item):
            nonlocal cancelled
            if item["kind"] == "source_item":
                cancelled = True
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value=self.snapshot([self.entry("two"), self.entry(seq=102)])), mock.patch.object(tangled_identity, "resolve_owner_handle", return_value="valid.example"):
            summary = source_sync.sync_tangled_repos(conn, execute=True, progress=progress, cancel_requested=lambda: cancelled)
        self.assertTrue(summary["cancelled"])
        self.assertFalse(summary["state_updated"])
        self.assertEqual(len(registry.all_projects(conn)), 1)
        self.assertEqual(self.state(conn)["seq"], 100)
