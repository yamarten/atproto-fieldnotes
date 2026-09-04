from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import sys
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
import lexicon_publication_catalog as catalog_cli
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
        self.conn = catalog.initialize_catalog(self.sidecar)

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def test_schema_initialization_has_explicit_version(self) -> None:
        state = catalog.catalog_state(self.conn)
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["watermark_seq"], 0)

    def test_init_refuses_an_existing_non_catalog_database(self) -> None:
        path = self.root / "survey.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE projects(project_key TEXT)")
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(RuntimeError, "non-empty SQLite"):
            catalog.initialize_catalog(path)
        check = sqlite3.connect(path)
        try:
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"projects"})
        finally:
            check.close()

    def test_unsupported_catalog_version_is_rejected_before_schema_changes(self) -> None:
        path = self.root / "future.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE catalog_metadata(singleton INTEGER PRIMARY KEY, schema_version INTEGER)")
        conn.execute("INSERT INTO catalog_metadata VALUES(1, 999)")
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(RuntimeError, "schema version: 999"):
            catalog.initialize_catalog(path)
        check = sqlite3.connect(path)
        try:
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"catalog_metadata"})
        finally:
            check.close()

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
        self.assertEqual(result, {"eligible_checked": 1, "verified": 0, "failed": 1, "catalog_revision": 2})
        row = self.conn.execute("SELECT * FROM publication_verifications").fetchone()
        self.assertEqual(row["dns_same_did"], 0)
        self.assertEqual(row["reason"], "dns_did_mismatch")
        self.assertEqual(row["next_verification_at"], "2026-09-06T01:00:00Z")

    def test_live_verifier_preserves_dns_success_when_record_fetch_fails(self) -> None:
        with (
            mock.patch.object(catalog, "resolve_lexicon_authority_did", return_value="did:plc:a"),
            mock.patch.object(catalog, "resolve_did_document", side_effect=TimeoutError("bounded timeout")),
        ):
            result = catalog.verify_publisher("com.example.one", "did:plc:a", dns_timeout=1, http_timeout=1)
        self.assertIs(result["dns_same_did"], True)
        self.assertIs(result["current_record_refetched"], False)
        self.assertTrue(result["reason"].startswith("current_record_fetch_failed:TimeoutError"))

        with mock.patch.object(catalog, "resolve_lexicon_authority_did", return_value="did:plc:other"):
            mismatch = catalog.verify_publisher("com.example.one", "did:plc:a", dns_timeout=1, http_timeout=1)
        self.assertIs(mismatch["dns_same_did"], False)
        self.assertEqual(mismatch["reason"], "dns_did_mismatch")

    def test_report_classifies_changes_mirrors_projects_authorities_and_space_activity(self) -> None:
        first = event(1, "did:plc:a", "com.example.space", main_type="space", collections=["com.example.active", "com.example.quiet"])
        mirror = event(2, "did:plc:mirror", "com.example.space", main_type="space", collections=["com.example.active", "com.example.quiet"])
        independent_one = event(3, "did:plc:new", "net.new.one")
        independent_two = event(4, "did:plc:new", "net.new.two")
        catalog.ingest_segment(self.conn, [first, mirror, independent_one, independent_two], summary(0, 4, 4, truncated=False))

        hashes = {(row["did"], row["schema_id"]): row["record_hash"] for row in self.conn.execute("SELECT * FROM publication_catalog")}
        def success(nsid: str, did: str, **_kwargs: object) -> dict:
            if did == "did:plc:mirror":
                return {
                    "dns_same_did": False, "current_record_refetched": False,
                    "reason": "dns_did_mismatch", "retryable": True,
                    "authority_did": "did:plc:a", "record_uri": "", "record_hash": "",
                }
            return {
                "dns_same_did": True, "current_record_refetched": True, "reason": "verified", "retryable": False,
                "authority_did": did, "record_uri": f"at://{did}/{LEXICON_COLLECTION}/{nsid}",
                "record_hash": hashes[(did, nsid)],
            }
        catalog.verify_due_publications(self.conn, limit=4, now="2026-09-05T01:00:00Z", resolver=success)
        index = {
            "known_schemas": {"com.example.space": ["proj:example"]},
            "known_nsids": {},
            "authority_projects": {"com.example": ["proj:example", "proj:other"]},
            "observed_collections": {"com.example.active"}, "project_count": 1, "query_only": True,
        }
        report = catalog.build_evaluation_report(self.conn, index, since_revision=0, now="2026-09-05T02:00:00Z")
        self.assertEqual(report["contract_version"], 1)
        self.assertEqual(report["counts"]["verified_publisher_candidates"], 3)
        self.assertEqual(len(report["changes"]["new_schemas"]), 4)
        self.assertEqual(len(report["verified_publisher_candidates"]), 3)
        self.assertEqual(len(report["existing_project_unique_candidates"]), 1)
        self.assertEqual(report["existing_project_unique_candidates"][0]["project_match_basis"], "exact_schema_or_nsid")
        self.assertEqual(report["unregistered_authority_groups"][0]["authority_group"], "net.new")
        self.assertTrue(report["unregistered_authority_groups"][0]["independent_candidate"])
        self.assertEqual(
            sorted({item["declared_collection"] for item in report["space_declared_collections_without_activity"]}),
            ["com.example.quiet"],
        )
        self.assertTrue(all("publisher_count_for_schema" in item for item in report["mirror_or_catalog_only"] if item["schema_id"] == "com.example.space"))
        verified_keys = {(item["did"], item["rkey"]) for item in report["verified_publisher_candidates"]}
        catalog_only_keys = {(item["did"], item["rkey"]) for item in report["mirror_or_catalog_only"]}
        self.assertFalse(verified_keys & catalog_only_keys)
        expired = catalog.build_evaluation_report(self.conn, index, since_revision=0, now="2026-09-20T02:00:00Z")
        self.assertEqual(expired["verified_publisher_candidates"], [])
        self.assertTrue(any(
            item["state"] == "verification_expired"
            for item in expired["deleted_or_verification_failed_or_expired"]
        ))

    def test_registry_is_read_only_and_same_path_is_rejected(self) -> None:
        registry_path = self.root / "survey.db"
        writable = sqlite3.connect(registry_path)
        writable.executescript("""
          CREATE TABLE projects(project_key TEXT, state_json TEXT);
          CREATE TABLE project_identifiers(project_key TEXT, kind TEXT, value TEXT);
          INSERT INTO projects VALUES('proj:a', '{"ops":{"collection_observations":[{"collection":"com.example.active"}]}}');
          INSERT INTO project_identifiers VALUES('proj:a','nsid','com.example.active');
        """)
        writable.commit()
        writable.close()
        readonly = open_read_only_database(registry_path)
        try:
            index = catalog.load_registry_index(readonly)
            self.assertTrue(index["query_only"])
            self.assertEqual(index["observed_collections"], {"com.example.active"})
            with self.assertRaises(sqlite3.OperationalError):
                readonly.execute("DELETE FROM projects")
        finally:
            readonly.close()
        with self.assertRaisesRegex(ValueError, "different SQLite files"):
            catalog.assert_distinct_database_paths(registry_path, registry_path)

        original_argv = sys.argv
        sys.argv = [
            "lexicon_publication_catalog.py", "report", "--sidecar-db", str(registry_path),
            "--registry-db", str(registry_path),
        ]
        try:
            with self.assertRaisesRegex(ValueError, "different SQLite files"):
                catalog_cli.main()
        finally:
            sys.argv = original_argv
        check = sqlite3.connect(registry_path)
        try:
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("catalog_metadata", tables)
        finally:
            check.close()

    def test_optional_collection_inventory_is_merged_without_registry_write(self) -> None:
        inventory = self.root / "inventory.json"
        inventory.write_text(json.dumps([{"collection": "net.new.active"}]))
        index = catalog_cli.add_collection_inventory(
            {"observed_collections": {"com.example.active"}}, inventory,
        )
        self.assertEqual(index["observed_collections"], {"com.example.active", "net.new.active"})


if __name__ == "__main__":
    unittest.main()
