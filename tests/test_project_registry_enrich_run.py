from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from helpers import TempRegistryDbTestCase, captured_stdout, registry
from project_registry_core import cli
from project_registry_core.application import enrichment
from project_registry_core.commands import enrichment as enrichment_commands
from project_registry_core.jobs import CancellationToken, OperationRequest
from project_registry_core import project_record


class ProjectRegistryEnrichRunTest(TempRegistryDbTestCase):
    def seed_projects(self) -> None:
        conn = registry.open_db()
        try:
            for key in ("proj:111111111111", "proj:222222222222"):
                project_record.save_project(conn, self.make_project(key, "nsid", f"app.example.{key[-1]}"))
            conn.commit()
        finally:
            conn.close()

    def request(self, checkpoint_path: Path, *, resume_run: str = "") -> OperationRequest:
        return OperationRequest.create(
            "enrich",
            operation_id=resume_run or "test-run",
            execute=True,
            parameters={
                "all_projects": True,
                "include_stable": True,
                "checkpoint_path": str(checkpoint_path),
                "resume_run": resume_run,
                "deep_investigation": True,
                "llm_endpoint": "https://llm.example",
                "llm_model": "example-model",
                "llm_api_style": "chat",
                "llm_reasoning_effort": "low",
                "llm_api_key": "secret-value",
            },
        )

    def test_operation_request_and_checkpoint_omit_api_key_value(self) -> None:
        checkpoint_path = Path(self.tempdir.name) / "run.json"
        request = self.request(checkpoint_path)
        options = enrichment.EnrichmentOptions.from_request(request)
        checkpoint = enrichment.new_checkpoint(
            [{"project_key": "proj:111111111111"}],
            request,
            options,
        )
        written = enrichment.write_checkpoint(checkpoint, str(checkpoint_path))
        loaded = enrichment.load_checkpoint("test-run", str(checkpoint_path))

        self.assertEqual(written, checkpoint_path)
        self.assertEqual(loaded["options"]["llm_endpoint"], "https://llm.example")
        self.assertTrue(loaded["options"]["llm_api_key_configured"])
        self.assertNotIn("secret-value", checkpoint_path.read_text())
        self.assertEqual(request.to_dict()["parameters"]["llm_api_key"], "<configured>")

    def test_cancel_after_current_project_and_resume_checkpoint(self) -> None:
        self.seed_projects()
        checkpoint_path = Path(self.tempdir.name) / "interrupt.json"
        token = CancellationToken()
        events = []
        calls: list[str] = []

        def cancel_after_first(conn, project, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(project["project_key"])
            enriched = dict(project)
            enriched["name"] = "Graceful Cancel Test"
            token.request_cancel("test_stop_after_current")
            return enriched

        conn = registry.open_db()
        try:
            with mock.patch.object(enrichment.enrichment_pipeline, "enrich_project", side_effect=cancel_after_first):
                result = enrichment.run(
                    conn,
                    self.request(checkpoint_path),
                    emit=events.append,
                    cancellation=token,
                )
        finally:
            conn.close()

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(calls, ["proj:111111111111"])
        self.assertEqual([event.kind for event in events], ["started", "progress", "item_completed", "cancelled"])
        self.assertEqual([event.sequence for event in events], [1, 2, 3, 4])
        checkpoint = json.loads(checkpoint_path.read_text())
        self.assertEqual(checkpoint["status"], "interrupted")
        self.assertEqual(checkpoint["completed_project_keys"], ["proj:111111111111"])
        self.assertEqual(checkpoint["current_project_key"], "")
        self.assertNotIn("current_project_key", result.data)

        resumed_calls: list[str] = []

        def finish_remaining(conn, project, **kwargs):  # type: ignore[no-untyped-def]
            resumed_calls.append(project["project_key"])
            return dict(project, name="Resumed Test")

        conn = registry.open_db()
        try:
            with mock.patch.object(enrichment.enrichment_pipeline, "enrich_project", side_effect=finish_remaining):
                resumed = enrichment.run(conn, self.request(checkpoint_path, resume_run="test-run"))
        finally:
            conn.close()

        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed_calls, ["proj:222222222222"])
        checkpoint = json.loads(checkpoint_path.read_text())
        self.assertEqual(checkpoint["status"], "completed")
        self.assertEqual(
            checkpoint["completed_project_keys"],
            ["proj:111111111111", "proj:222222222222"],
        )

    def test_cli_adapter_emits_application_events_as_json_lines(self) -> None:
        self.seed_projects()
        checkpoint_path = Path(self.tempdir.name) / "cli.json"
        args = cli.build_parser().parse_args([
            "enrich",
            "--all",
            "--run-id",
            "cli-test",
            "--checkpoint-path",
            str(checkpoint_path),
        ])

        def fake_enrich(conn, project, **kwargs):  # type: ignore[no-untyped-def]
            return dict(project, name="CLI Adapter Test")

        with mock.patch.object(enrichment.enrichment_pipeline, "enrich_project", side_effect=fake_enrich):
            with captured_stdout() as stdout:
                result = enrichment_commands.cmd_enrich(args)

        self.assertEqual(result, 0)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertIn("enrich_run", lines[0])
        self.assertEqual([line["project_key"] for line in lines if "project_key" in line], [
            "proj:111111111111",
            "proj:222222222222",
        ])
        self.assertEqual(lines[-1]["summary"], "enrich_all")
        self.assertEqual(lines[-1]["completed_project_count"], 2)

    def test_bulk_continues_after_failure_and_resume_retries_only_failed_project(self) -> None:
        self.seed_projects()
        checkpoint_path = Path(self.tempdir.name) / "failure.json"
        calls: list[str] = []

        def fail_one(conn, project, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(project["project_key"])
            if project["project_key"] == "proj:111111111111":
                raise RuntimeError("project-specific failure")
            return dict(project, name="Completed after peer failure")

        events = []
        conn = registry.open_db()
        try:
            with mock.patch.object(enrichment.enrichment_pipeline, "enrich_project", side_effect=fail_one):
                result = enrichment.run(conn, self.request(checkpoint_path), emit=events.append)
        finally:
            conn.close()

        self.assertEqual(result.status, "failed")
        self.assertEqual(set(calls), {"proj:111111111111", "proj:222222222222"})
        self.assertEqual(result.data["failed_project_count"], 1)
        self.assertEqual(result.data["failed_projects"][0]["project_key"], "proj:111111111111")
        self.assertEqual(result.error["type"], "BulkEnrichmentError")
        self.assertEqual(events[-1].kind, "failed")
        checkpoint = json.loads(checkpoint_path.read_text())
        self.assertEqual(checkpoint["status"], "failed")
        self.assertEqual(checkpoint["completed_project_keys"], ["proj:222222222222"])
        self.assertEqual(checkpoint["failed_projects"][0]["project_key"], "proj:111111111111")

        resumed_calls: list[str] = []

        def succeed(conn, project, **kwargs):  # type: ignore[no-untyped-def]
            resumed_calls.append(project["project_key"])
            return dict(project, name="Recovered")

        conn = registry.open_db()
        try:
            with mock.patch.object(enrichment.enrichment_pipeline, "enrich_project", side_effect=succeed):
                resumed = enrichment.run(conn, self.request(checkpoint_path, resume_run="test-run"))
        finally:
            conn.close()

        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed_calls, ["proj:111111111111"])
        checkpoint = json.loads(checkpoint_path.read_text())
        self.assertEqual(checkpoint["status"], "completed")
        self.assertEqual(checkpoint["failed_projects"], [])

    def test_bulk_fail_fast_stops_before_next_project(self) -> None:
        self.seed_projects()
        checkpoint_path = Path(self.tempdir.name) / "fail-fast.json"
        request = self.request(checkpoint_path)
        request = OperationRequest.create(
            "enrich",
            operation_id=request.operation_id,
            execute=True,
            parameters={**request.parameters, "continue_on_error": False},
        )
        calls: list[str] = []

        def fail(conn, project, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(project["project_key"])
            raise RuntimeError("stop now")

        conn = registry.open_db()
        try:
            with mock.patch.object(enrichment.enrichment_pipeline, "enrich_project", side_effect=fail):
                result = enrichment.run(conn, request)
        finally:
            conn.close()

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(calls), 1)
        self.assertIn("--resume-run", result.data["resume_command"])

    def test_cli_fail_fast_option_sets_bulk_policy(self) -> None:
        args = cli.build_parser().parse_args(["enrich", "--all", "--fail-fast"])
        request = enrichment_commands.request_from_args(args)
        self.assertFalse(request.parameters["continue_on_error"])


if __name__ == "__main__":
    import unittest

    unittest.main()
