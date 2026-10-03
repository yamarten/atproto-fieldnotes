from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import identifiers, investigation, project_record, relations, repository_material, storage
from project_registry_core.evidence import CollectorResult, EvidenceBundle


FIXTURE = json.loads((Path(__file__).parent / "fixtures/project_registry/nsid-extraction-regression-v1.json").read_text())


class NsidExtractionTest(unittest.TestCase):
    def test_known_false_extractions_and_valid_collections(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(identifiers.extract_nsids_from_text(case["text"]), case["expected"])

    def test_nsid_syntax_limits_and_authority_name_rules(self) -> None:
        valid = ["cn.8.lex.Record", "COM.123.my-app.RecordV2", "a.b.C"]
        valid.append(".".join(["a" * 63] * 3 + ["a" * 61, "R" * 63]))
        invalid = ["8.cn.Record", "cn.-app.Record", "cn.app-.Record", "cn.app.record-name", "cn.app.1Record"]
        invalid.extend(["a.b." + "R" * 64, ".".join(["a" * 63] * 4 + ["R"])])
        for value in valid:
            with self.subTest(value=value):
                self.assertTrue(identifiers.is_valid_nsid(value))
        for value in invalid:
            with self.subTest(value=value):
                self.assertFalse(identifiers.is_valid_nsid(value))

    def test_source_noise_does_not_consume_collection_hint_budget(self) -> None:
        source = "\n".join([
            *(case["text"] for case in FIXTURE["cases"] if not case["expected"]),
            "client.getRecord({collection: 'cn.8.lex.Record'})",
            "client.putRecord({collection: 'dev.my-app.OtherRecord'})",
        ])
        hints = repository_material.repo_semantic_hints(
            "https://github.com/example/app", ref="revision",
            tree=[{"path": "src/client.py", "type": "blob", "size": len(source)}],
            file_text=lambda *_args, **_kwargs: source,
        )
        self.assertEqual([(item["nsid"], item["operation"]) for item in hints], [
            ("cn.8.lex.Record", "read"), ("dev.my-app.OtherRecord", "write"),
        ])

    def test_collector_rejects_invalid_hints_before_snapshot_and_relations(self) -> None:
        valid_nsid = "com.atproto.repo.getRecord"
        hints = [{"nsid": token, "path": "src/client.py", "excerpt": f"session = '{token}'"} for token in FIXTURE["invalid_tokens"]]
        hints.append({"nsid": valid_nsid, "path": "src/client.py", "operation": "read", "predicate": "uses_collection", "excerpt": valid_nsid})
        project = project_record.default_project_record("proj:nsid-collector", "repo_url", "https://github.com/example/app")
        project["input_nsids"] = FIXTURE["invalid_tokens"]
        bundle = investigation.collect_repository_evidence(project, "https://github.com/example/app", investigation.RepositoryCollectorDependencies(
            now=lambda: "2026-10-03T00:00:00+00:00",
            fetch_repo=lambda _url: {"repo": {}, "readme_text": "", "tree": [], "revision": "revision"},
            repo_lexicon_hints=lambda *_args, **_kwargs: [],
            repo_semantic_hints=lambda *_args, **_kwargs: hints,
            extract_urls=lambda _text: [],
        ))
        self.assertEqual(bundle.records[0].value["semantic_hint_count"], 1)
        self.assertEqual([item["nsid"] for item in bundle.records[0].value["source_evidence"]], [valid_nsid])
        self.assertEqual([
            record.value["object"]["value"] for record in bundle.records if record.kind == "relation"
        ], [valid_nsid])


class NsidRelationRepairTest(TempRegistryDbTestCase):
    def test_repair_removes_legacy_invalid_relations_preserves_history_and_is_idempotent(self) -> None:
        project = self.make_project("proj:nsid-repair", "repo_url", "https://github.com/example/app")
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-07-14T00:00:00+00:00")
        for nsid in [*FIXTURE["invalid_tokens"], "cn.8.lex.Record", "dev.my-app.Record"]:
            bundle.add_record(relations.relation_record(
                source="github_source:https://github.com/example/app", classification="derived", observed_at=bundle.collected_at,
                subject_type="repo", subject_value="https://github.com/example/app", predicate="mentions_collection",
                object_type="nsid", object_value=nsid, locator="src/client.py", method="source_mention",
            ))
        bundle.add_collector_result(CollectorResult(
            source="fixture", operation="load", status="success", observed_at=bundle.collected_at,
            request_count=0, result_count=len(bundle.records),
        ))
        project["relation_evidence"] = [
            {"relation": record.value, "source": record.source, "classification": record.classification, "observed_at": record.observed_at}
            for record in bundle.records
        ]
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            run_id = storage.save_investigation_run(
                conn, project_key=project["project_key"], started_at=bundle.collected_at, completed_at=bundle.collected_at,
                input_project=project, output_project=project, bundle=bundle,
            )
            conn.commit()
            history_before = tuple(conn.execute("SELECT * FROM investigation_runs WHERE run_id=?", (run_id,)).fetchone())
            relations_before = [tuple(row) for row in conn.execute("SELECT * FROM project_relations")]
            report = registry.audit_and_repair_identifiers(conn, execute=False)
            self.assertEqual(report["normalized_child_change_count"], 1)
            self.assertEqual([tuple(row) for row in conn.execute("SELECT * FROM project_relations")], relations_before)
            registry.audit_and_repair_identifiers(conn, execute=True)
            current = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual([row[0] for row in conn.execute("SELECT object_value FROM project_relations ORDER BY ordinal")], [
                "cn.8.lex.Record", "dev.my-app.Record",
            ])
            self.assertEqual(tuple(conn.execute("SELECT * FROM investigation_runs WHERE run_id=?", (run_id,)).fetchone()), history_before)
            self.assertEqual(registry.audit_and_repair_identifiers(conn, execute=False)["normalized_child_change_count"], 0)
            self.assertEqual(registry.audit_and_repair_identifiers(conn, execute=False)["pruned_project_count"], 0)
            replayed = registry.replay_core.replay_bundle(copy.deepcopy(project), bundle, registry.replay_dependencies())
            self.assertEqual([item["relation"]["object"]["value"] for item in replayed["relation_evidence"]], [
                "cn.8.lex.Record", "dev.my-app.Record",
            ])
            self.assertEqual([item["relation"]["object"]["value"] for item in current["relation_evidence"]], [
                "cn.8.lex.Record", "dev.my-app.Record",
            ])
        finally:
            conn.close()
