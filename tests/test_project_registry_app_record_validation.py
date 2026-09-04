from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import cli, project_record
from project_registry_core.application import app_record_validation
from project_registry_core.commands import app_record_validation as validation_command


class AppRecordValidationTest(TempRegistryDbTestCase):
    def _save(self, project: dict) -> None:
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
        finally:
            conn.close()

    def test_ready_content_traces_profile_and_confirmed_link_evidence(self) -> None:
        project = self.make_project(
            "proj:entryready01", "repo_url", "https://github.com/example/reader"
        )
        project.update({
            "name": "Reader",
            "category": "project",
            "summary": "Reads long-form AT Protocol posts.",
            "website_url": "https://reader.example",
            "project_repository_urls": ["https://github.com/example/reader"],
            "tags": ["reading"],
            "platforms": ["web", "iOS"],
            "resource_url_records": [{
                "url": "https://reader.example/docs",
                "role": "docs",
                "confidence": "confirmed",
                "source": "service_page:https://reader.example",
                "observed_at": "2026-01-01T00:00:00+00:00",
            }],
        })
        self._save(project)

        conn = app_record_validation.open_registry_readonly(self.db_path)
        try:
            report = app_record_validation.build_report(conn)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE projects SET name='changed'")
        finally:
            conn.close()

        item = report["projects"][0]
        self.assertEqual(item["readiness"], "needs_publication_context")
        self.assertTrue(item["content_ready"])
        self.assertFalse(item["record_ready"])
        self.assertNotIn("createdAt", item["candidate_record"])
        self.assertEqual(
            item["candidate_record"]["platforms"],
            [
                "community.lexicon.app.defs#platformWeb",
                "community.lexicon.app.defs#platformIOS",
            ],
        )
        links_field = next(field for field in item["fields"] if field["field"] == "links")
        sources = {entry["source"] for entry in links_field["evidence"]}
        self.assertIn("projects.website_url", sources)
        self.assertIn("project_identifiers", sources)
        self.assertIn("state_json.evidence.resource_url_records", sources)
        self.assertEqual(report["target"]["nsid"], "community.lexicon.app.entry")
        self.assertEqual(report["target"]["excluded_by_policy"], ["profileDid"])

    def test_missing_fields_exclude_candidate_and_noncurrent_identifiers(self) -> None:
        project = self.make_project("proj:entrymissing", "nsid", "com.example.reader")
        project["name"] = ""
        project["category"] = "project"
        project["service_url_candidates"] = ["https://candidate.example"]
        self._save(project)
        conn = registry.open_db()
        try:
            conn.execute(
                "INSERT INTO project_noncurrent_identifiers("
                "project_key, kind, value, role, standing, reason, recorded_at, origin_project_key, metadata_json"
                ") VALUES (?, 'repo_url', 'https://github.com/example/old', "
                "'project_repository', 'former', 'replaced', '', ?, '{}')",
                (project["project_key"], project["project_key"]),
            )
            conn.commit()
        finally:
            conn.close()

        report = app_record_validation.build_report_from_path(self.db_path)
        item = report["projects"][0]
        self.assertEqual(item["readiness"], "missing_required")
        self.assertEqual(item["issues"]["missing_required"], ["name", "links"])
        self.assertEqual(item["excluded_inputs"]["noncurrent_identifiers"], 1)
        excluded = item["excluded_inputs"]["non_confirmed_current_identifiers"]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["value"], "https://candidate.example")
        self.assertEqual(excluded[0]["classification"], "candidate")
        self.assertEqual(
            item["issues"]["insufficient_evidence"][0]["field"], "links"
        )
        self.assertEqual(
            item["issues"]["insufficient_evidence"][0]["evidence_ref"],
            "excluded_inputs.non_confirmed_link_sources",
        )
        self.assertNotIn("links", item["candidate_record"])

    def test_unconvertible_values_and_unsettled_qualifiers_are_distinct(self) -> None:
        project = self.make_project("proj:entryinvalid", "nsid", "com.example.invalid")
        project["name"] = "x" * 201
        project["category"] = "project"
        project["website_url"] = "https://invalid.example"
        project["tags"] = [f"tag-{index}" for index in range(11)]
        project["platforms"] = [f"platform-{index}" for index in range(13)]
        self._save(project)

        report = app_record_validation.build_report_from_path(self.db_path)
        item = report["projects"][0]
        self.assertEqual(item["readiness"], "unconvertible")
        self.assertEqual(item["issues"]["unconvertible"], ["name", "tags", "platforms"])
        self.assertIn("lexicons", item["issues"]["specification_uncertain"])
        self.assertIn("accountIndicators", item["issues"]["specification_uncertain"])
        self.assertIn("description", item["issues"]["missing_optional"])

    def test_candidate_only_link_is_insufficient_evidence(self) -> None:
        project = self.make_project("proj:entrycandidate", "nsid", "com.example.candidate")
        project["name"] = "Candidate-only Link"
        project["category"] = "project"
        project["service_url_candidates"] = ["https://candidate.example"]
        self._save(project)

        item = app_record_validation.build_report_from_path(self.db_path)["projects"][0]
        self.assertEqual(item["readiness"], "insufficient_evidence")
        self.assertEqual(item["issues"]["missing_required"], ["links"])
        self.assertEqual(
            item["issues"]["insufficient_evidence"][0]["field"], "links"
        )

    def test_non_link_candidate_does_not_explain_missing_links(self) -> None:
        project = self.make_project("proj:entrynsidonly", "nsid", "com.example.current")
        project["name"] = "NSID-only Evidence"
        project["category"] = "project"
        self._save(project)
        conn = registry.open_db()
        try:
            conn.execute(
                "INSERT INTO project_identifiers("
                "project_key, ordinal, kind, value, role, classification"
                ") VALUES (?, 1, 'nsid', 'com.example.candidate', 'lexicon', 'candidate')",
                (project["project_key"],),
            )
            conn.commit()
        finally:
            conn.close()

        item = app_record_validation.build_report_from_path(self.db_path)["projects"][0]
        self.assertEqual(item["readiness"], "missing_required")
        self.assertEqual(item["issues"]["insufficient_evidence"], [])
        self.assertEqual(item["excluded_inputs"]["non_confirmed_link_sources"], [])

    def test_cli_writes_reproducible_json_without_changing_database(self) -> None:
        project = self.make_project("proj:entrycli000", "service_url", "https://cli.example")
        project["name"] = "CLI Example"
        project["category"] = "project"
        project["website_url"] = "https://cli.example"
        self._save(project)
        database_before = self.db_path.read_bytes()
        output = Path(self.tempdir.name) / "report.json"

        args = cli.build_parser().parse_args([
            "validate-app-record-export",
            "--db-path", str(self.db_path),
            "--output", str(output),
        ])
        self.assertEqual(validation_command.cmd_validate_app_record_export(args), 0)
        first = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(database_before, self.db_path.read_bytes())

        second = app_record_validation.build_report_from_path(self.db_path)
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(first, second)

    def test_export_uses_explicit_manifest_and_stable_project_order(self) -> None:
        for project_key, name, url in (
            ("proj:export000002", "Second", "https://second.example"),
            ("proj:export000001", "First", "https://first.example"),
        ):
            project = self.make_project(project_key, "service_url", url)
            project.update({
                "name": name,
                "category": "project",
                "website_url": url,
                "first_seen_at": "2020-01-01T00:00:00+00:00",
            })
            self._save(project)
        manifest = {
            "manifest_schema_version": 1,
            "project_keys": ["proj:export000002", "proj:export000001"],
            "publication_context": {"createdAt": "2026-09-04T10:20:30Z"},
        }

        first = app_record_validation.build_export_from_path(self.db_path, manifest)
        second = app_record_validation.build_export_from_path(self.db_path, manifest)

        self.assertEqual(first, second)
        self.assertEqual(
            first["publication_manifest"]["project_keys"],
            ["proj:export000001", "proj:export000002"],
        )
        self.assertEqual(
            first["records"][0],
            {
                "$type": "community.lexicon.app.entry",
                "createdAt": "2026-09-04T10:20:30Z",
                "links": [{
                    "role": "community.lexicon.app.defs#linkRoleWebsite",
                    "uri": "https://first.example",
                }],
                "name": "First",
            },
        )
        self.assertNotEqual(
            first["records"][0]["createdAt"],
            "2020-01-01T00:00:00+00:00",
        )
        self.assertNotIn("project_key", first["records"][0])
        self.assertNotIn("collection", first["records"][0])
        self.assertNotIn("target", first)

    def test_export_requires_separate_scope_uncertain_allowlist(self) -> None:
        project = self.make_project(
            "proj:uncertain001", "service_url", "https://uncertain.example"
        )
        project.update({
            "name": "Uncertain",
            "category": "name_only",
            "website_url": "https://uncertain.example",
        })
        self._save(project)
        manifest = {
            "manifest_schema_version": 1,
            "project_keys": [project["project_key"]],
            "publication_context": {"createdAt": "2026-09-04T00:00:00Z"},
        }

        with self.assertRaisesRegex(ValueError, "scope_uncertain"):
            app_record_validation.build_export_from_path(self.db_path, manifest)

        manifest["allow_scope_uncertain_project_keys"] = [project["project_key"]]
        export = app_record_validation.build_export_from_path(self.db_path, manifest)
        self.assertEqual(len(export["records"]), 1)

        manifest["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            app_record_validation.build_export_from_path(self.db_path, manifest)

    def test_export_rejects_invalid_context_and_unexportable_selection(self) -> None:
        project = self.make_project("proj:exportbad001", "nsid", "com.example.missing")
        project.update({"name": "Missing Link", "category": "project"})
        self._save(project)
        base_manifest = {
            "manifest_schema_version": 1,
            "project_keys": [project["project_key"]],
            "publication_context": {"createdAt": "2026-09-04T00:00:00+09:00"},
        }
        with self.assertRaisesRegex(ValueError, "RFC 3339 UTC"):
            app_record_validation.build_export_from_path(self.db_path, base_manifest)

        base_manifest["publication_context"]["createdAt"] = "2026-09-04T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "missing_required"):
            app_record_validation.build_export_from_path(self.db_path, base_manifest)

        base_manifest["publication_context"]["createdAt"] = (
            "2026-09-04T00:00:00." + "1" * 50 + "Z"
        )
        with self.assertRaisesRegex(ValueError, "64 characters"):
            app_record_validation.build_export_from_path(self.db_path, base_manifest)

        base_manifest["publication_context"]["createdAt"] = "2026-02-30T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "valid calendar datetime"):
            app_record_validation.build_export_from_path(self.db_path, base_manifest)

    def test_export_cli_is_read_only_and_does_not_write_partial_output(self) -> None:
        project = self.make_project(
            "proj:exportcli001", "service_url", "https://export-cli.example"
        )
        project.update({
            "name": "Export CLI",
            "category": "project",
            "website_url": "https://export-cli.example",
        })
        self._save(project)
        database_before = self.db_path.read_bytes()
        manifest_path = Path(self.tempdir.name) / "manifest.json"
        output_path = Path(self.tempdir.name) / "records.json"
        manifest_path.write_text(json.dumps({
            "manifest_schema_version": 1,
            "project_keys": [project["project_key"]],
            "publication_context": {"createdAt": "2026-09-04T00:00:00Z"},
        }), encoding="utf-8")
        args = cli.build_parser().parse_args([
            "export-app-records",
            "--db-path", str(self.db_path),
            "--manifest", str(manifest_path),
            "--output", str(output_path),
        ])

        self.assertEqual(validation_command.cmd_export_app_records(args), 0)
        self.assertEqual(database_before, self.db_path.read_bytes())
        self.assertEqual(len(json.loads(output_path.read_text())["records"]), 1)

        bad_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bad_manifest["project_keys"] = ["proj:not-found"]
        manifest_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
        output_path.unlink()
        with self.assertRaisesRegex(ValueError, "unknown project keys"):
            validation_command.cmd_export_app_records(args)
        self.assertFalse(output_path.exists())

        args.output = str(self.db_path)
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            validation_command.cmd_export_app_records(args)

    def test_scope_gate_prevents_field_complete_reject_from_becoming_ready(self) -> None:
        project = self.make_project("proj:entryreject0", "repo_url", "https://github.com/example/noise")
        project.update({
            "name": "Unrelated Specification",
            "category": "reject",
            "website_url": "https://unrelated.example",
        })
        self._save(project)

        item = app_record_validation.build_report_from_path(self.db_path)["projects"][0]
        self.assertEqual(item["readiness"], "not_applicable")
        self.assertEqual(item["publication_scope"]["reason"], "category_reject")
        self.assertFalse(item["content_ready"])
