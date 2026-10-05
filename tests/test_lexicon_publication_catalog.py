from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import sys
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from project_registry_core import lexicon_publication_catalog as catalog
from project_registry_core.lexicon_publication_comparison import LEXICON_COLLECTION, open_read_only_database


def event(seq: int, did: str, nsid: str, operation: str = "create", *, main_type: str = "record", collections: list[str] | None = None) -> dict:
    record = None
    if operation != "delete":
        main = {"type": main_type}
        if collections is not None:
            main["collections"] = collections
        record = {"$type": LEXICON_COLLECTION, "id": nsid, "lexicon": 1, "defs": {"main": main}}
    return {
        "type": "event", "seq": seq, "time": f"2026-09-05T00:00:{seq:02d}Z",
        "did": did, "operation": operation, "rev": f"rev-{seq}",
        "collection": LEXICON_COLLECTION, "rkey": nsid, "record": record,
    }


def summary(after: int, tip: int, last: int, *, truncated: bool) -> dict:
    return {
        "type": "summary", "after_seq": after, "sealed_tip_seq": tip,
        "last_event_seq": last, "truncated": truncated,
    }


class LexiconPublicationCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.sidecar = self.root / "publication.db"
        self.conn = sqlite3.connect(self.sidecar)
        self.conn.row_factory = sqlite3.Row
        catalog.install_catalog_schema(self.conn)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def test_schema_initialization_has_explicit_version(self) -> None:
        state = catalog.catalog_state(self.conn)
        self.assertEqual(state["schema_version"], 3)
        self.assertEqual(state["watermark_seq"], 0)


    def test_incomplete_segments_stage_until_same_tip_completes(self) -> None:
        first = catalog.ingest_segment(
            self.conn, [event(5, "did:plc:a", "com.example.one")],
            summary(0, 10, 5, truncated=True),
        )
        self.assertEqual(first["status"], "staged")
        self.assertEqual(catalog.catalog_state(self.conn)["watermark_seq"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publication_catalog").fetchone()[0], 0)

        final = catalog.ingest_segment(
            self.conn, [event(8, "did:plc:a", "com.example.two")],
            summary(5, 10, 8, truncated=False),
        )
        self.assertEqual(final["status"], "applied")
        self.assertEqual(final["watermark_seq"], 10)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publication_catalog").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pending_publication_events").fetchone()[0], 0)

    def test_gap_and_mixed_tip_are_rejected_without_changing_staging(self) -> None:
        catalog.ingest_segment(self.conn, [event(5, "did:plc:a", "com.example.one")], summary(0, 10, 5, truncated=True))
        with self.assertRaisesRegex(ValueError, "gap or overlap"):
            catalog.ingest_segment(self.conn, [], summary(6, 10, 6, truncated=False))
        with self.assertRaisesRegex(ValueError, "mix sealed tips"):
            catalog.ingest_segment(self.conn, [], summary(5, 11, 5, truncated=False))
        state = catalog.catalog_state(self.conn)
        self.assertEqual(state["pending_after_seq"], 5)
        self.assertEqual(state["pending_sealed_tip_seq"], 10)

    def test_invalid_segment_rolls_back_and_does_not_advance_watermark(self) -> None:
        bad = event(2, "did:plc:a", "com.example.one")
        bad["collection"] = "wrong.collection"
        with self.assertRaisesRegex(ValueError, "invalid publication events"):
            catalog.ingest_segment(self.conn, [bad], summary(0, 2, 2, truncated=False))
        state = catalog.catalog_state(self.conn)
        self.assertEqual(state["watermark_seq"], 0)
        self.assertIsNone(state["pending_sealed_tip_seq"])

    def test_final_apply_failure_rolls_back_catalog_and_preserves_prior_resume_point(self) -> None:
        catalog.ingest_segment(
            self.conn, [event(1, "did:plc:a", "com.example.one")],
            summary(0, 2, 1, truncated=True),
        )
        original_apply = catalog._apply_pending_events

        def apply_then_fail(conn: sqlite3.Connection, revision: int) -> dict[str, int]:
            original_apply(conn, revision)
            raise RuntimeError("injected final apply failure")

        with mock.patch.object(catalog, "_apply_pending_events", side_effect=apply_then_fail):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                catalog.ingest_segment(
                    self.conn, [event(2, "did:plc:a", "com.example.two")],
                    summary(1, 2, 2, truncated=False),
                )
        state = catalog.catalog_state(self.conn)
        self.assertEqual(state["watermark_seq"], 0)
        self.assertEqual(state["pending_after_seq"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pending_publication_events").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publication_catalog").fetchone()[0], 0)

    def test_update_delete_and_stale_events_are_idempotent(self) -> None:
        create = event(3, "did:plc:a", "com.example.one")
        catalog.ingest_segment(self.conn, [create, create], summary(0, 3, 3, truncated=False))
        catalog.ingest_segment(
            self.conn,
            [event(4, "did:plc:a", "com.example.one", "update", main_type="query")],
            summary(3, 4, 4, truncated=False),
        )
        catalog.ingest_segment(
            self.conn,
            [event(5, "did:plc:a", "com.example.one", "delete")],
            summary(4, 5, 5, truncated=False),
        )
        row = self.conn.execute("SELECT * FROM publication_catalog").fetchone()
        self.assertEqual(row["seq"], 5)
        self.assertEqual(row["is_deleted"], 1)
        self.assertEqual(json.loads(row["definition_types_json"]), ["query"])

    def test_did_and_rkey_identity_preserves_mirror_publishers(self) -> None:
        catalog.ingest_segment(self.conn, [
            event(1, "did:plc:a", "com.example.one"),
            event(2, "did:plc:b", "com.example.one"),
        ], summary(0, 2, 2, truncated=False))
        rows = self.conn.execute("SELECT did, schema_id FROM publication_catalog ORDER BY did").fetchall()
        self.assertEqual([(row["did"], row["schema_id"]) for row in rows], [
            ("did:plc:a", "com.example.one"), ("did:plc:b", "com.example.one"),
        ])

    def test_verification_separates_dns_record_failure_and_rechecks_changed_content(self) -> None:
        item = event(1, "did:plc:a", "com.example.one")
        catalog.ingest_segment(self.conn, [item], summary(0, 1, 1, truncated=False))
        calls = []

        def success(nsid: str, did: str, **_kwargs: object) -> dict:
            calls.append((nsid, did))
            return {
                "dns_same_did": True, "current_record_refetched": True, "reason": "verified",
                "retryable": False, "authority_did": did,
                "record_uri": f"at://{did}/{LEXICON_COLLECTION}/{nsid}",
                "record_hash": catalog.json_hash(item["record"]),
            }

        result = catalog.verify_due_publications(self.conn, limit=1, now="2026-09-05T01:00:00Z", resolver=success)
        self.assertEqual(result["verified"], 1)
        self.assertEqual(calls, [("com.example.one", "did:plc:a")])
        self.assertEqual(catalog.verify_due_publications(
            self.conn, limit=1, now="2026-09-05T02:00:00Z", resolver=success,
        )["eligible_checked"], 0)

        changed = event(2, "did:plc:a", "com.example.one", "update", main_type="query")
        catalog.ingest_segment(self.conn, [changed], summary(1, 2, 2, truncated=False))
        mismatch = catalog.verify_due_publications(self.conn, limit=1, now="2026-09-05T03:00:00Z", resolver=success)
        self.assertEqual(mismatch["failed"], 1)
        verification = self.conn.execute("SELECT * FROM publication_verifications").fetchone()
        self.assertEqual(verification["dns_same_did"], 1)
        self.assertEqual(verification["current_record_refetched"], 0)
        self.assertEqual(verification["reason"], "current_record_hash_mismatch")
        self.assertEqual(verification["retryable"], 1)

    def test_verification_due_order_and_failure_reason_are_bounded(self) -> None:
        catalog.ingest_segment(self.conn, [
            event(1, "did:plc:a", "com.example.a"), event(2, "did:plc:b", "com.example.b"),
        ], summary(0, 2, 2, truncated=False))

        def fail(_nsid: str, _did: str, **_kwargs: object) -> dict:
            return {
                "dns_same_did": False, "current_record_refetched": False, "reason": "dns_did_mismatch",
                "retryable": True, "authority_did": "did:plc:other", "record_uri": "", "record_hash": "",
            }

        result = catalog.verify_due_publications(self.conn, limit=1, now="2026-09-05T01:00:00Z", resolver=fail)
        self.assertEqual(result["eligible_checked"], 1)
        self.assertEqual(result["verified"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["catalog_revision"], 2)
        self.assertEqual(result["targets"][0]["reason"], "dns_did_mismatch")
        row = self.conn.execute("SELECT * FROM publication_verifications").fetchone()
        self.assertEqual(row["dns_same_did"], 0)
        self.assertEqual(row["reason"], "dns_did_mismatch")
        self.assertEqual(row["next_verification_at"], "2026-09-06T01:00:00Z")

    def test_verification_can_be_limited_to_explicit_schema_and_did_targets(self) -> None:
        catalog.ingest_segment(self.conn, [
            event(1, "did:plc:a", "com.example.a"),
            event(2, "did:plc:b", "com.example.b"),
            event(3, "did:plc:c", "com.example.b"),
        ], summary(0, 3, 3, truncated=False))
        calls = []

        def success(nsid: str, did: str, **_kwargs: object) -> dict:
            calls.append((nsid, did))
            row = self.conn.execute(
                "SELECT record_hash FROM publication_catalog WHERE did=? AND schema_id=?", (did, nsid),
            ).fetchone()
            return {
                "dns_same_did": True, "current_record_refetched": True, "reason": "verified",
                "retryable": False, "authority_did": did, "record_uri": "at://record",
                "record_hash": row["record_hash"],
            }

        result = catalog.verify_due_publications(
            self.conn, limit=5, schema_ids=["com.example.b"], dids=["did:plc:c"],
            now="2026-09-05T01:00:00Z", resolver=success,
        )
        self.assertEqual(result["eligible_checked"], 1)
        self.assertEqual(calls, [("com.example.b", "did:plc:c")])
        remaining = self.conn.execute(
            "SELECT did,schema_id FROM publication_catalog WHERE (did,rkey) NOT IN "
            "(SELECT did,rkey FROM publication_verifications) ORDER BY did"
        ).fetchall()
        self.assertEqual([(row["did"], row["schema_id"]) for row in remaining], [
            ("did:plc:a", "com.example.a"), ("did:plc:b", "com.example.b"),
        ])

        with self.assertRaisesRegex(ValueError, "invalid target schema ID"):
            catalog.verify_due_publications(
                self.conn, limit=1, schema_ids=["not an nsid"], now="2026-09-05T01:00:00Z",
                resolver=success,
            )


if __name__ == "__main__":
    unittest.main()
