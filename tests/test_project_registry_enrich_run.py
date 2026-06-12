from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest import mock

from helpers import TempRegistryDbTestCase, captured_stdout, registry


class ProjectRegistryEnrichRunTest(TempRegistryDbTestCase):
    def test_enrich_run_checkpoint_roundtrip_omits_api_key_value(self) -> None:
        checkpoint_path = Path(self.tempdir.name) / "run.json"
        args = argparse.Namespace(
            run_id="test-run",
            all_tracking_states=False,
            tracking_states=None,
            include_stable=True,
            include_stalled=False,
            include_out_of_scope=False,
            include_archived=False,
            include_ignore=False,
            new_only=True,
            retry_failures=False,
            deep_investigation=True,
            llm_endpoint="https://llm.example",
            llm_model="example-model",
            llm_api_style="chat",
            llm_reasoning_effort="low",
            llm_api_key="secret-value",
        )
        projects = [
            {"project_key": "proj:111111111111"},
            {"project_key": "proj:222222222222"},
        ]

        checkpoint = registry.new_enrich_run_checkpoint(projects, args)
        registry.mark_enrich_run_project_started(checkpoint, "proj:111111111111")
        registry.mark_enrich_run_project_completed(checkpoint, "proj:111111111111")
        written = registry.write_enrich_run_checkpoint(checkpoint, str(checkpoint_path))
        loaded = registry.load_enrich_run_checkpoint("test-run", str(checkpoint_path))

        self.assertEqual(written, checkpoint_path)
        self.assertEqual(loaded["run_id"], "test-run")
        self.assertEqual(loaded["completed_project_keys"], ["proj:111111111111"])
        self.assertEqual(loaded["options"]["llm_endpoint"], "https://llm.example")
        self.assertTrue(loaded["options"]["llm_api_key_configured"])
        self.assertNotIn("secret-value", checkpoint_path.read_text())

    def test_resume_remaining_projects_skips_completed_keys(self) -> None:
        conn = registry.open_db()
        try:
            for key in ("proj:111111111111", "proj:222222222222", "proj:333333333333"):
                project = self.make_project(key, "nsid", f"app.example.{key[-1]}")
                registry.save_project(conn, project)
            conn.commit()

            checkpoint = {
                "run_id": "resume-test",
                "selected_project_keys": [
                    "proj:111111111111",
                    "proj:222222222222",
                    "proj:333333333333",
                    "proj:missing0000",
                ],
                "completed_project_keys": ["proj:111111111111"],
            }

            projects = registry.load_enrich_run_projects(conn, checkpoint)
            remaining = registry.enrich_run_remaining_projects(projects, checkpoint)

            self.assertEqual(checkpoint["missing_project_keys"], ["proj:missing0000"])
            self.assertEqual([project["project_key"] for project in remaining], ["proj:222222222222", "proj:333333333333"])
        finally:
            conn.close()

    def test_cmd_enrich_all_interrupt_writes_resume_checkpoint_without_traceback(self) -> None:
        conn = registry.open_db()
        try:
            for key in ("proj:111111111111", "proj:222222222222"):
                project = self.make_project(key, "nsid", f"app.example.{key[-1]}")
                registry.save_project(conn, project)
            conn.commit()
        finally:
            conn.close()

        checkpoint_path = Path(self.tempdir.name) / "interrupt.json"
        args = registry.build_parser().parse_args(
            [
                "enrich",
                "--all",
                "--run-id",
                "interrupt-test",
                "--checkpoint-path",
                str(checkpoint_path),
            ]
        )
        calls: list[str] = []

        def fake_enrich(conn, project, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(project["project_key"])
            if project["project_key"] == "proj:222222222222":
                raise KeyboardInterrupt()
            enriched = dict(project)
            enriched["name"] = "Interrupted Test"
            return enriched

        with mock.patch.object(registry, "enrich_project", side_effect=fake_enrich):
            with captured_stdout() as stdout:
                result = registry.cmd_enrich(args)

        self.assertEqual(result, 130)
        self.assertEqual(calls, ["proj:111111111111", "proj:222222222222"])
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertTrue(lines[-1]["interrupted"])
        self.assertIn("--resume-run interrupt-test", lines[-1]["resume_command"])

        checkpoint = json.loads(checkpoint_path.read_text())
        self.assertEqual(checkpoint["status"], "interrupted")
        self.assertEqual(checkpoint["completed_project_keys"], ["proj:111111111111"])
        self.assertEqual(checkpoint["current_project_key"], "proj:222222222222")
