from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, captured_stdout, registry
from project_registry_core import cli
from project_registry_core.application import enrichment, merge, projects
from project_registry_core.commands import enrichment as enrichment_commands
from project_registry_core.evidence import EvidenceBundle
from project_registry_core.jobs import CancellationToken, OperationRequest
from project_registry_core import project_record
from project_registry_core.persistence import storage


class _ProjectRegistryEnrichRunFixtures:
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
                "llm_max_output_tokens": 2048,
                "llm_api_key": "secret-value",
            },
        )


class ProjectRegistryEnrichRunUnitTest(_ProjectRegistryEnrichRunFixtures, ProjectRecordTestCase):
    def test_cli_fail_fast_option_sets_bulk_policy(self) -> None:
        args = cli.build_parser().parse_args(["enrich", "--all", "--fail-fast"])
        request = enrichment_commands.request_from_args(args)
        self.assertFalse(request.parameters["continue_on_error"])

    def test_cli_triage_prompt_suffix_file_is_forwarded(self) -> None:
        args = cli.build_parser().parse_args([
            "enrich", "-p", "proj:111111111111",
            "--llm-triage-prompt-suffix-file", "model-rules.txt",
        ])
        request = enrichment_commands.request_from_args(args)

        self.assertEqual(request.parameters["llm_triage_prompt_suffix_file"], "model-rules.txt")

    def test_cli_llm_max_output_tokens_is_forwarded(self) -> None:
        args = cli.build_parser().parse_args([
            "enrich", "-p", "proj:111111111111",
            "--llm-max-output-tokens", "2048",
        ])
        request = enrichment_commands.request_from_args(args)

        self.assertEqual(request.parameters["llm_max_output_tokens"], 2048)

    def test_unknown_model_has_no_automatic_triage_rules(self) -> None:
        options = enrichment.EnrichmentOptions(llm_model="example/other-model")

        with mock.patch.dict(
            "os.environ", {"ATPROTO_SURVEY_LLM_TRIAGE_PROMPT_SUFFIX_FILE": ""}
        ):
            suffix_path = enrichment.configured_triage_prompt_suffix_file(options)

        self.assertEqual(suffix_path, "")


class ProjectRegistryEnrichRunTest(_ProjectRegistryEnrichRunFixtures, TempRegistryDbTestCase):
    def test_operation_request_and_checkpoint_omit_api_key_value(self) -> None:
        checkpoint_path = Path(self.tempdir.name) / "run.json"
        request = self.request(checkpoint_path)
        request = OperationRequest.create(
            "enrich",
            operation_id=request.operation_id,
            execute=True,
            parameters={**request.parameters, "llm_triage_prompt_suffix_file": "/tmp/model-rules.txt"},
        )
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
        self.assertEqual(loaded["options"]["llm_triage_prompt_suffix_file"], "/tmp/model-rules.txt")
        self.assertEqual(loaded["options"]["llm_max_output_tokens"], 2048)
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
        self.assertEqual(events[1].data["progress"]["eta_observed_count"], 0)
        self.assertEqual(events[1].data["progress"]["eta_remaining_count"], 2)
        checkpoint = json.loads(checkpoint_path.read_text())
        self.assertEqual(checkpoint["status"], "interrupted")
        self.assertEqual(checkpoint["completed_project_keys"], ["proj:111111111111"])
        self.assertEqual(checkpoint["current_project_key"], "")
        self.assertNotIn("current_project_key", result.data)

        resumed_calls: list[str] = []

        def finish_remaining(conn, project, **kwargs):  # type: ignore[no-untyped-def]
            resumed_calls.append(project["project_key"])
            return dict(project, name="Resumed Test")

        resumed_events = []
        conn = registry.open_db()
        try:
            with mock.patch.object(enrichment.enrichment_pipeline, "enrich_project", side_effect=finish_remaining):
                resumed = enrichment.run(
                    conn,
                    self.request(checkpoint_path, resume_run="test-run"),
                    emit=resumed_events.append,
                )
        finally:
            conn.close()

        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed_calls, ["proj:222222222222"])
        resumed_progress = next(event.data["progress"] for event in resumed_events if event.kind == "progress")
        self.assertEqual(resumed_progress["current"], 2)
        self.assertEqual(resumed_progress["total"], 2)
        self.assertEqual(resumed_progress["eta_observed_count"], 0)
        self.assertEqual(resumed_progress["eta_remaining_count"], 1)
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

    def test_enrich_started_before_merge_cannot_recreate_alias_source(self) -> None:
        setup = registry.open_db()
        try:
            source = projects.add_seed(setup, seed="https://source.example", seed_type="service_url")
            target = projects.add_seed(setup, seed="https://target.example", seed_type="service_url")
            source_snapshot = project_record.fetch_project(setup, source["project_key"])
            assert source_snapshot is not None
            prior_run_id = storage.save_investigation_run(
                setup,
                project_key=source["project_key"],
                started_at="2026-08-01T00:00:00+00:00",
                completed_at="2026-08-01T00:01:00+00:00",
                input_project=source_snapshot,
                output_project=source_snapshot,
                bundle=EvidenceBundle(
                    project_key=source["project_key"],
                    collected_at="2026-08-01T00:01:00+00:00",
                ),
            )
            setup.commit()
        finally:
            setup.close()

        stale_run_id = "inv:stale-after-merge"

        def finish_after_merge(conn, project, **kwargs):  # type: ignore[no-untyped-def]
            merger = registry.open_db()
            try:
                merge.merge_projects(
                    merger,
                    project_key=source["project_key"],
                    target_project_key=target["project_key"],
                    execute=True,
                    force=True,
                )
                current_target = project_record.fetch_project(merger, target["project_key"])
                assert current_target is not None
                current_target["summary"] = "target changed after merge"
                project_record.save_project(merger, current_target, preserve_timestamps=True)
                merger.commit()
            finally:
                merger.close()

            stale = dict(project)
            stale["summary"] = "stale source snapshot"
            storage.save_investigation_run(
                conn,
                run_id=stale_run_id,
                project_key=source["project_key"],
                started_at="2026-08-02T00:00:00+00:00",
                completed_at="2026-08-02T00:01:00+00:00",
                input_project=project,
                output_project=stale,
                bundle=EvidenceBundle(
                    project_key=source["project_key"],
                    collected_at="2026-08-02T00:01:00+00:00",
                ),
            )
            return stale

        request = OperationRequest.create(
            "enrich",
            operation_id="stale-enrich",
            execute=True,
            parameters={"project_key": source["project_key"]},
        )
        conn = registry.open_db()
        try:
            with mock.patch.object(
                enrichment.enrichment_pipeline,
                "enrich_project",
                side_effect=finish_after_merge,
            ):
                result = enrichment.run(conn, request)
        finally:
            conn.close()

        check = registry.open_db()
        try:
            alias_target = check.execute(
                "SELECT canonical_project_key FROM project_key_aliases WHERE alias_project_key = ?",
                (source["project_key"],),
            ).fetchone()
            revived_source = project_record.fetch_project(check, source["project_key"])
            current_target = project_record.fetch_project(check, target["project_key"])
            stale_run = storage.load_investigation_run(check, stale_run_id)
            run_ids = [
                item["run_id"]
                for item in storage.list_investigation_run_summaries(check, project_key=target["project_key"])
            ]
        finally:
            check.close()

        self.assertEqual(result.status, "retry_wait")
        self.assertEqual(result.data["conflicted_project_count"], 1)
        self.assertEqual(str(alias_target[0]), target["project_key"])
        self.assertIsNone(revived_source)
        assert current_target is not None
        self.assertEqual(current_target["summary"], "target changed after merge")
        self.assertIsNone(stale_run)
        self.assertEqual(run_ids, [prior_run_id])

    def test_resume_retries_merged_source_against_canonical_project_once(self) -> None:
        checkpoint_path = Path(self.tempdir.name) / "merged-source.json"
        setup = registry.open_db()
        try:
            source = projects.add_seed(setup, seed="https://source.example", seed_type="service_url")
            target = projects.add_seed(setup, seed="https://target.example", seed_type="service_url")
            selected = [
                project_record.fetch_project(setup, source["project_key"]),
                project_record.fetch_project(setup, target["project_key"]),
            ]
            assert all(selected)
            request = self.request(checkpoint_path)
            checkpoint = enrichment.new_checkpoint(
                selected,
                request,
                enrichment.EnrichmentOptions.from_request(request),
            )
            checkpoint["status"] = "failed"
            checkpoint["failed_projects"] = [{
                "project_key": source["project_key"],
                "error": "StaleProjectWriteError: merged during enrich",
                "at": "2026-08-02T00:01:00+00:00",
            }]
            enrichment.write_checkpoint(checkpoint, str(checkpoint_path))
            merge.merge_projects(
                setup,
                project_key=source["project_key"],
                target_project_key=target["project_key"],
                execute=True,
                force=True,
            )
        finally:
            setup.close()

        calls: list[str] = []

        def enrich_current(conn, project, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(project["project_key"])
            return dict(project, summary="retried canonical project")

        conn = registry.open_db()
        try:
            with mock.patch.object(
                enrichment.enrichment_pipeline,
                "enrich_project",
                side_effect=enrich_current,
            ):
                result = enrichment.run(
                    conn,
                    self.request(checkpoint_path, resume_run="test-run"),
                )
        finally:
            conn.close()

        saved_checkpoint = json.loads(checkpoint_path.read_text())
        self.assertEqual(result.status, "completed")
        self.assertEqual(calls, [target["project_key"]])
        self.assertEqual(
            set(saved_checkpoint["completed_project_keys"]),
            {source["project_key"], target["project_key"]},
        )
        self.assertEqual(saved_checkpoint["failed_projects"], [])
        self.assertEqual(saved_checkpoint["missing_project_keys"], [])

    def test_run_reads_triage_prompt_suffix_once_and_forwards_content(self) -> None:
        self.seed_projects()
        suffix_path = Path(self.tempdir.name) / "model-rules.txt"
        suffix_path.write_text("  category gate\n", encoding="utf-8")
        request = OperationRequest.create(
            "enrich",
            execute=True,
            parameters={
                "project_key": "proj:111111111111",
                "llm_triage_prompt_suffix_file": str(suffix_path),
            },
        )

        conn = registry.open_db()
        try:
            with mock.patch.dict(
                "os.environ",
                {"ATPROTO_SURVEY_LLM_TRIAGE_PROMPT_SUFFIX_FILE": "/ignored/model-rules.txt"},
            ):
                with mock.patch.object(
                    enrichment.enrichment_pipeline,
                    "enrich_project",
                    side_effect=lambda _conn, project, **_kwargs: project,
                ) as enrich_project:
                    result = enrichment.run(conn, request)
        finally:
            conn.close()

        self.assertEqual(result.status, "completed")
        self.assertEqual(enrich_project.call_args.kwargs["llm_triage_prompt_suffix"], "category gate")
        self.assertIsNone(enrich_project.call_args.kwargs["llm_max_output_tokens"])

    def test_run_uses_configured_triage_prompt_suffix_and_checkpoints_path(self) -> None:
        self.seed_projects()
        checkpoint_path = Path(self.tempdir.name) / "configured-suffix.json"
        suffix_path = Path(self.tempdir.name) / "qwen-rules.txt"
        suffix_path.write_text("  qwen rule\n", encoding="utf-8")
        request = self.request(checkpoint_path)

        conn = registry.open_db()
        try:
            with mock.patch.dict(
                "os.environ",
                {"ATPROTO_SURVEY_LLM_TRIAGE_PROMPT_SUFFIX_FILE": str(suffix_path)},
            ):
                with mock.patch.object(
                    enrichment.enrichment_pipeline,
                    "enrich_project",
                    side_effect=lambda _conn, project, **_kwargs: project,
                ) as enrich_project:
                    result = enrichment.run(conn, request)
        finally:
            conn.close()

        checkpoint = enrichment.load_checkpoint("test-run", str(checkpoint_path))
        self.assertEqual(result.status, "completed")
        self.assertEqual(enrich_project.call_args.kwargs["llm_triage_prompt_suffix"], "qwen rule")
        self.assertEqual(
            checkpoint["options"]["llm_triage_prompt_suffix_file"], str(suffix_path)
        )

    def test_run_automatically_uses_rules_for_current_qwen_model(self) -> None:
        self.seed_projects()
        request = OperationRequest.create(
            "enrich",
            execute=True,
            parameters={"project_key": "proj:111111111111"},
        )

        conn = registry.open_db()
        try:
            with mock.patch.dict(
                "os.environ",
                {
                    "ATPROTO_SURVEY_LLM_MODEL": "qwen/qwen3.6-35b-a3b",
                    "ATPROTO_SURVEY_LLM_TRIAGE_PROMPT_SUFFIX_FILE": "",
                },
            ):
                with mock.patch.object(
                    enrichment.enrichment_pipeline,
                    "enrich_project",
                    side_effect=lambda _conn, project, **_kwargs: project,
                ) as enrich_project:
                    result = enrichment.run(conn, request)
        finally:
            conn.close()

        self.assertEqual(result.status, "completed")
        suffix = enrich_project.call_args.kwargs["llm_triage_prompt_suffix"]
        self.assertIn("model-specific tie-breakers", suffix)

    def test_run_forwards_llm_max_output_tokens(self) -> None:
        self.seed_projects()
        request = OperationRequest.create(
            "enrich",
            execute=True,
            parameters={
                "project_key": "proj:111111111111",
                "llm_max_output_tokens": 2048,
            },
        )

        conn = registry.open_db()
        try:
            with mock.patch.object(
                enrichment.enrichment_pipeline,
                "enrich_project",
                side_effect=lambda _conn, project, **_kwargs: project,
            ) as enrich_project:
                result = enrichment.run(conn, request)
        finally:
            conn.close()

        self.assertEqual(result.status, "completed")
        self.assertEqual(enrich_project.call_args.kwargs["llm_max_output_tokens"], 2048)

    def test_run_rejects_negative_llm_max_output_tokens(self) -> None:
        request = OperationRequest.create(
            "enrich",
            execute=True,
            parameters={"project_key": "proj:111111111111", "llm_max_output_tokens": -1},
        )

        conn = registry.open_db()
        try:
            with self.assertRaisesRegex(ValueError, "llm_max_output_tokens"):
                enrichment.run(conn, request)
        finally:
            conn.close()


if __name__ == "__main__":
    import unittest

    unittest.main()
