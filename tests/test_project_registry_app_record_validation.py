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
        self._save(project)

        report = app_record_validation.build_report_from_path(self.db_path)
        item = report["projects"][0]
        self.assertEqual(item["readiness"], "unconvertible")
        self.assertEqual(item["issues"]["unconvertible"], ["name", "tags"])
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
