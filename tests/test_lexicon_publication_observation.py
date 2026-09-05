from __future__ import annotations

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
        self.assertTrue(result["review_due"])
        self.assertEqual(result["review_reasons"], ["new_verified_space", "new_verified_unregistered_authority"])
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
