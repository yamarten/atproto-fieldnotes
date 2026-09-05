from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from project_registry_core import lexicon_publication_catalog as catalog
from project_registry_core.application import lexicon_observation
from project_registry_core.lexicon_publication_comparison import LEXICON_COLLECTION


def publication(seq: int, nsid: str, *, did: str = "did:plc:new", main_type: str = "record") -> dict:
    return {
        "type": "event", "seq": seq, "time": f"2026-09-05T00:00:{seq:02d}Z",
        "did": did, "operation": "create", "rev": f"rev-{seq}",
        "collection": LEXICON_COLLECTION, "rkey": nsid,
        "record": {
            "$type": LEXICON_COLLECTION, "id": nsid, "lexicon": 1,
            "defs": {"main": {"type": main_type}},
        },
    }


class LexiconPublicationObservationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.registry_path = self.root / "survey.db"
        conn = sqlite3.connect(self.registry_path)
        conn.executescript("""
          CREATE TABLE projects(project_key TEXT, state_json TEXT);
          CREATE TABLE project_identifiers(project_key TEXT, kind TEXT, value TEXT);
          INSERT INTO projects VALUES('proj:existing','{}');
        """)
        conn.commit()
        conn.close()

    def write_sync_state(self, *, status: str = "completed", completed_at: str | None = None) -> None:
        completed_at = completed_at or catalog.iso_now()
        (self.root / lexicon_observation.SYNC_STATE_FILENAME).write_text(json.dumps({
            "version": 1,
            "schedules": {
                lexicon_observation.SYNC_PRESET_ID: {
                    "last_status": status,
                    "last_completed_at": completed_at,
                    "last_period": "2026-W36-5",
                }
            },
            "queue": [],
        }))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def success_verifier(self, nsid: str, did: str, **_kwargs: object) -> dict:
        sidecar = sqlite3.connect(lexicon_observation.sidecar_path_for_registry(self.registry_path))
        try:
            record_hash = sidecar.execute(
                "SELECT record_hash FROM publication_catalog WHERE did=? AND rkey=?", (did, nsid),
            ).fetchone()[0]
        finally:
            sidecar.close()
        return {
            "dns_same_did": True, "current_record_refetched": True, "reason": "verified",
            "retryable": False, "authority_did": did, "record_uri": f"at://{did}/{LEXICON_COLLECTION}/{nsid}",
            "record_hash": record_hash,
        }

    def test_fixed_observation_updates_sidecar_with_read_only_registry_and_early_signals(self) -> None:
        self.write_sync_state()
        before = self.registry_path.read_bytes()
        item = publication(1, "net.new.space", main_type="space")
        result = lexicon_observation.run(
            self.registry_path,
            run_id="observe:one",
            snapshot_fetcher=lambda **_kwargs: {
                "events": [item], "after_seq": 0, "sealed_tip_seq": 1,
                "last_event_seq": 1, "truncated": False,
            },
            inventory_fetcher=lambda _url, **_kwargs: [{"collection": "net.new.record"}],
            verifier=self.success_verifier,
        )
        self.assertEqual(result["operation_status"], "completed")
        self.assertEqual(result["ingestion"]["new"], 1)
        self.assertEqual(result["inventory"]["status"], "completed")
        self.assertEqual(result["verification"]["verified"], 1)
        self.assertFalse(result["review_due"])
        self.assertEqual(result["review_reasons"], [])
        self.assertEqual(result["history"]["valid_period_count"], 1)
        self.assertEqual(result["categories"]["non_record"], 0)
        self.assertEqual(result["categories"]["space_without_declared_activity"], 1)
        self.assertEqual(self.registry_path.read_bytes(), before)
        self.assertTrue((self.root / lexicon_observation.SIDECAR_FILENAME).is_file())

    def test_inventory_timeout_is_durable_partial_and_not_success(self) -> None:
        def timeout(_url: str, **_kwargs: object) -> object:
            raise TimeoutError("inventory deadline")

        result = lexicon_observation.run(
            self.registry_path,
            run_id="observe:timeout",
            snapshot_fetcher=lambda **_kwargs: {
                "events": [], "after_seq": 0, "sealed_tip_seq": 0,
                "last_event_seq": 0, "truncated": False,
            },
            inventory_fetcher=timeout,
        )
        self.assertEqual(result["operation_status"], "partial")
        self.assertEqual(result["inventory"]["status"], "failed")
        self.assertIn("TimeoutError", result["inventory"]["error"])
        self.assertEqual(result["history"]["successful_run_count"], 0)
        self.assertEqual(result["history"]["items"][0]["status"], "partial")

    def test_missing_failed_and_stale_sync_are_not_comparable(self) -> None:
        missing = lexicon_observation.sync_comparison(
            self.registry_path, observation_started_at="2026-09-06T20:00:00Z",
        )
        self.assertEqual(missing["reason"], "sync_state_missing")
        self.write_sync_state(status="failed", completed_at="2026-09-05T14:00:00Z")
        failed = lexicon_observation.sync_comparison(
            self.registry_path, observation_started_at="2026-09-05T20:00:00Z",
        )
        self.assertEqual(failed["reason"], "sync_not_completed")
        self.write_sync_state(completed_at="2026-09-01T00:00:00Z")
        stale = lexicon_observation.sync_comparison(
            self.registry_path, observation_started_at="2026-09-06T20:00:00Z",
        )
        self.assertEqual(stale["reason"], "sync_too_old")
        self.assertEqual(stale["status"], "unavailable")

    def test_publication_first_seen_before_baseline_is_not_a_new_discovery_when_verified_later(self) -> None:
        sidecar = catalog.initialize_catalog(self.root / lexicon_observation.SIDECAR_FILENAME)
        try:
            catalog.ingest_segment(
                sidecar, [publication(1, "net.old.space", main_type="space")],
                {"after_seq": 0, "sealed_tip_seq": 1, "last_event_seq": 1, "truncated": False},
            )
            catalog.rebaseline_evaluation(sidecar, reason="test baseline", baseline_at="2026-09-05T00:00:00Z")
        finally:
            sidecar.close()
        self.write_sync_state()
        result = lexicon_observation.run(
            self.registry_path,
            run_id="observe:post-baseline-verify",
            snapshot_fetcher=lambda **_kwargs: {
                "events": [], "after_seq": 1, "sealed_tip_seq": 1,
                "last_event_seq": 1, "truncated": False,
            },
            inventory_fetcher=lambda _url, **_kwargs: [],
            verifier=self.success_verifier,
        )
        signals = result["history"]["items"][0]["signals"]
        self.assertEqual(signals["new_verified_spaces"], [])
        self.assertEqual(signals["new_verified_unregistered_authorities"], [])
        self.assertEqual(result["categories"]["verified_unregistered"], 0)

    def test_weekly_categories_separate_activity_type_and_registry_association(self) -> None:
        report = {
            "changes": {"new_schemas": [
                {"did": "did:one", "rkey": "app.one.record", "schema_id": "app.one.record", "definition_types": ["record"], "first_seen_revision": 2},
                {"did": "did:two", "rkey": "app.two.query", "schema_id": "app.two.query", "definition_types": ["query"], "first_seen_revision": 2},
                {"did": "did:three", "rkey": "app.three.space", "schema_id": "app.three.space", "definition_types": ["space"], "first_seen_revision": 2},
                {"did": "did:four", "rkey": "app.four.record", "schema_id": "app.four.record", "definition_types": ["record"], "first_seen_revision": 2},
            ]},
            "verified_publisher_candidates": [
                {"did": "did:one", "rkey": "app.one.record", "exact_project_keys": ["proj:one"], "authority_project_keys": []},
                {"did": "did:two", "rkey": "app.two.query", "exact_project_keys": [], "authority_project_keys": ["proj:a", "proj:b"]},
                {"did": "did:three", "rkey": "app.three.space", "exact_project_keys": [], "authority_project_keys": []},
            ],
            "space_declared_collections": [
                {"schema_id": "app.three.space", "publisher_did": "did:three", "declared_collection": "app.one.record", "activity_observed": True},
            ],
            "mirror_or_catalog_only": [
                {"did": "did:four", "rkey": "app.four.record", "verification": {
                    "checked_at": "2026-09-06T00:00:00Z", "dns_same_did": False,
                    "current_record_refetched": False,
                }},
            ],
        }
        categories = lexicon_observation._weekly_categories(
            report, baseline_revision=1, observed_collections={"app.one.record"},
        )
        self.assertEqual(categories["record_with_fresh_activity"], 1)
        self.assertEqual(categories["record_without_observed_activity"], 1)
        self.assertEqual(categories["non_record"], 1)
        self.assertEqual(categories["space_with_declared_activity"], 1)
        self.assertEqual(categories["mirror_or_verification_failure"], 1)
        self.assertEqual(categories["verified_exact_unique"], 1)
        self.assertEqual(categories["verified_authority_ambiguous"], 1)
        self.assertEqual(categories["verified_unregistered"], 1)

    def test_bounded_verification_separates_unchecked_backlog_from_attempted_failures(self) -> None:
        self.write_sync_state()
        items = [publication(index, f"net.new.schema{index}") for index in range(1, 14)]
        result = lexicon_observation.run(
            self.registry_path,
            run_id="observe:bounded-backlog",
            snapshot_fetcher=lambda **_kwargs: {
                "events": items, "after_seq": 0, "sealed_tip_seq": 13,
                "last_event_seq": 13, "truncated": False,
            },
            inventory_fetcher=lambda _url, **_kwargs: [],
            verifier=lambda *_args, **_kwargs: {
                "dns_same_did": False, "current_record_refetched": False,
                "reason": "dns_did_mismatch", "retryable": True,
                "authority_did": "did:plc:other", "record_uri": "", "record_hash": "",
            },
        )
        self.assertEqual(result["verification"]["eligible_checked"], lexicon_observation.VERIFY_LIMIT)
        self.assertEqual(result["categories"]["mirror_or_verification_failure"], 12)
        self.assertEqual(result["categories"]["verification_pending"], 1)

    def test_partial_sealed_tip_resumes_after_reopen_without_mixing_tip(self) -> None:
        first_event = publication(5, "net.new.one")
        first = lexicon_observation.run(
            self.registry_path,
            run_id="observe:first",
            snapshot_fetcher=lambda **_kwargs: {
                "events": [first_event], "after_seq": 0, "sealed_tip_seq": 10,
                "last_event_seq": 5, "truncated": True,
            },
            inventory_fetcher=lambda _url, **_kwargs: [],
        )
        self.assertEqual(first["operation_status"], "partial")
        self.assertEqual(first["ingestion"]["resume_after_seq"], 5)

        def resumed_snapshot(**kwargs: object) -> dict:
            self.assertEqual(kwargs["after_seq"], 5)
            self.assertEqual(kwargs["sealed_tip_seq"], 10)
            return {
                "events": [publication(8, "net.new.two")], "after_seq": 5,
                "sealed_tip_seq": 10, "last_event_seq": 8, "truncated": False,
            }

        second = lexicon_observation.run(
            self.registry_path,
            run_id="observe:second",
            snapshot_fetcher=resumed_snapshot,
            inventory_fetcher=lambda _url, **_kwargs: [],
            verifier=lambda *_args, **_kwargs: {
                "dns_same_did": False, "current_record_refetched": False, "reason": "dns_did_mismatch",
                "retryable": True, "authority_did": "did:plc:other", "record_uri": "", "record_hash": "",
            },
        )
        self.assertEqual(second["operation_status"], "completed")
        self.assertEqual(second["ingestion"]["watermark_seq"], 10)
        self.assertEqual(second["verification"]["failed"], 2)
        sidecar = catalog.initialize_catalog(self.root / lexicon_observation.SIDECAR_FILENAME)
        try:
            self.assertIsNone(catalog.catalog_state(sidecar)["pending_sealed_tip_seq"])
            self.assertEqual(sidecar.execute("SELECT COUNT(*) FROM publication_catalog").fetchone()[0], 2)
        finally:
            sidecar.close()


if __name__ == "__main__":
    unittest.main()
