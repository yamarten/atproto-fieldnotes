from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
import lexicon_publication_comparison as comparison_cli
from project_registry_core.publications import lexicon_publication_comparison as comparison


def event(seq: int, did: str, rkey: str, operation: str, record: dict | None = None) -> dict:
    if record is not None:
        record = {"$type": comparison.LEXICON_COLLECTION, **record}
    return {
        "type": "event",
        "seq": seq,
        "time": f"2026-09-04T00:00:0{seq}Z",
        "did": did,
        "operation": operation,
        "rev": f"rev-{seq}",
        "collection": comparison.LEXICON_COLLECTION,
        "rkey": rkey,
        "record": record,
    }


class LexiconPublicationComparisonTest(unittest.TestCase):
    def test_fold_uses_did_and_rkey_and_keeps_newest_event(self) -> None:
        values = [
            event(1, "did:plc:a", "com.example.one", "create", {"id": "com.example.one"}),
            event(2, "did:plc:b", "com.example.one", "create", {"id": "com.example.one"}),
            event(4, "did:plc:a", "com.example.one", "update", {"id": "com.example.one", "lexicon": 1}),
            event(3, "did:plc:a", "com.example.one", "delete"),
        ]

        result = comparison.fold_events(values)

        self.assertEqual(result["accepted_event_count"], 4)
        self.assertEqual(result["current_key_count"], 2)
        self.assertEqual(result["stale_or_duplicate_event_count"], 1)
        current = {(item["did"], item["rkey"]): item for item in result["current"]}
        self.assertEqual(current[("did:plc:a", "com.example.one")]["seq"], 4)
        self.assertEqual(current[("did:plc:b", "com.example.one")]["seq"], 2)

    def test_fold_delete_removes_only_matching_did_rkey(self) -> None:
        result = comparison.fold_events([
            event(1, "did:plc:a", "com.example.one", "create", {"id": "com.example.one"}),
            event(2, "did:plc:b", "com.example.one", "create", {"id": "com.example.one"}),
            event(3, "did:plc:a", "com.example.one", "delete"),
        ])

        self.assertEqual(result["current_key_count"], 1)
        self.assertEqual(result["deleted_key_count"], 1)
        self.assertEqual(result["current"][0]["did"], "did:plc:b")

    def test_inspection_separates_shape_version_id_and_space_uncertainty(self) -> None:
        inspected = comparison.inspect_schema(event(
            1,
            "did:plc:a",
            "com.example.permissions",
            "create",
            {
                "id": "com.example.wrong",
                "lexicon": 2,
                "defs": {
                    "main": {"type": "space", "collections": ["com.example.post", "com.example.draft"]},
                    "call": {"type": "procedure"},
                    "future": {"type": "future-type"},
                },
            },
        ))

        self.assertEqual(inspected["main_type"], "space")
        self.assertEqual(inspected["spaces"][0]["collections"], ["com.example.draft", "com.example.post"])
        self.assertEqual(
            inspected["problems"],
            ["rkey_id_mismatch", "unsupported_lexicon_version", "unknown_definition_type"],
        )

    def test_compare_distinguishes_schema_nsid_and_collection_activity(self) -> None:
        values = [
            event(1, "did:plc:a", "com.known.post", "create", {"id": "com.known.post", "lexicon": 1, "defs": {"main": {"type": "record"}}}),
            event(2, "did:plc:b", "org.unique.search", "create", {"id": "org.unique.search", "lexicon": 1, "defs": {"main": {"type": "query"}}}),
            event(3, "did:plc:c", "net.new.space", "create", {"id": "net.new.space", "lexicon": 1, "defs": {"main": {"type": "space", "collections": ["net.new.private"]}}}),
            event(4, "did:plc:c", "net.new.private", "create", {"id": "net.new.private", "lexicon": 1, "defs": {"main": {"type": "record"}}}),
        ]
        database_index = {
            "known_schemas": {"com.known.post": ["proj:known"]},
            "known_nsids": {"com.known.post": ["proj:known"], "org.unique.search": ["proj:unique"]},
            "authority_projects": {"com.known": ["proj:known"], "org.unique": ["proj:unique"]},
            "project_count": 2,
            "query_only": True,
        }

        result = comparison.compare(values, database_index, {"com.known.post"}, sample_limit=10)

        self.assertEqual(result["counts"]["known_schema_overlap"], 1)
        self.assertEqual(result["counts"]["non_record_query_procedure_subscription"], 1)
        self.assertEqual(result["counts"]["space_schemas"], 1)
        self.assertEqual(result["declared_record_collections_not_sync_observed"], ["net.new.private"])
        self.assertEqual(result["space_declared_collections_not_sync_observed"], ["net.new.private"])
        self.assertEqual(result["comparison_category_counts"]["existing_project_unique_candidate"], 1)
        self.assertEqual(result["comparison_category_counts"]["independent_project_candidate"], 2)

    def test_database_connection_is_query_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "survey.db"
            writable = sqlite3.connect(path)
            writable.executescript("""
                CREATE TABLE projects(project_key TEXT, state_json TEXT);
                CREATE TABLE project_identifiers(project_key TEXT, kind TEXT, value TEXT);
                INSERT INTO projects VALUES ('proj:test', '{"evidence":{"lexicon_schema_samples":[{"id":"com.example.post"}]}}');
                INSERT INTO project_identifiers VALUES ('proj:test', 'nsid', 'com.example.post');
            """)
            writable.commit()
            writable.close()

            conn = comparison.open_read_only_database(path)
            try:
                index = comparison.load_database_index(conn)
                self.assertTrue(index["query_only"])
                self.assertEqual(index["known_schemas"]["com.example.post"], ["proj:test"])
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("UPDATE projects SET project_key='changed'")
            finally:
                conn.close()

    def test_known_schema_is_not_reclassified_by_unknown_siblings(self) -> None:
        values = [
            event(1, "did:plc:a", "com.example.known", "create", {"id": "com.example.known", "lexicon": 1, "defs": {"main": {"type": "record"}}}),
            event(2, "did:plc:a", "com.example.newOne", "create", {"id": "com.example.newOne", "lexicon": 1, "defs": {"main": {"type": "record"}}}),
            event(3, "did:plc:a", "com.example.newTwo", "create", {"id": "com.example.newTwo", "lexicon": 1, "defs": {"main": {"type": "record"}}}),
        ]
        index = {
            "known_schemas": {"com.example.known": ["proj:known"]},
            "known_nsids": {},
            "authority_projects": {},
            "project_count": 1,
            "query_only": True,
        }

        result = comparison.compare(values, index, set())

        self.assertEqual(result["comparison_category_counts"]["known_schema_overlap"], 1)
        self.assertEqual(result["comparison_category_counts"]["independent_project_candidate"], 2)

    def test_collection_inventory_comparison_preserves_nsid_case(self) -> None:
        result = comparison.collection_inventory_nsids([
            {"collection": "com.example.camelCase"},
            {"collection": "not-an-nsid"},
        ])

        self.assertEqual(result, {"com.example.camelCase"})

    def test_offline_segments_require_contiguous_coverage_and_final_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.jsonl"
            tail = Path(directory) / "tail.jsonl"
            first.write_text(
                json.dumps(event(10, "did:plc:a", "com.example.one", "create", {"id": "com.example.one"}))
                + "\n"
                + json.dumps({
                    "type": "summary", "after_seq": 0, "last_event_seq": 10,
                    "sealed_tip_seq": 20, "truncated": True, "timed_out": True,
                })
                + "\n"
            )
            tail.write_text(
                json.dumps(event(18, "did:plc:a", "com.example.two", "create", {"id": "com.example.two"}))
                + "\n"
                + json.dumps({
                    "type": "summary", "after_seq": 10, "last_event_seq": 18,
                    "sealed_tip_seq": 20, "truncated": False, "timed_out": False,
                })
                + "\n"
            )

            events, complete = comparison_cli._events_from_jsonl([first, tail])
            tail.write_text(tail.read_text().replace('"after_seq": 10', '"after_seq": 11'))
            _, gap = comparison_cli._events_from_jsonl([first, tail])

            self.assertEqual(len(events), 2)
            self.assertFalse(complete["truncated"])
            self.assertTrue(complete["timed_out"])
            self.assertTrue(gap["truncated"])


if __name__ == "__main__":
    unittest.main()
