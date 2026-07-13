from __future__ import annotations

import datetime as dt
import json
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import cli
from project_registry_core import service, storage
from project_registry_core.commands import projects as project_commands
from project_registry_core.evidence import CollectorResult, EvidenceBundle, EvidenceRecord
from project_registry_core.application import decisions, explain, maintenance, merge, presentation, projects, review


class ProjectRegistryApplicationTest(TempRegistryDbTestCase):
    def test_show_by_unknown_seed_does_not_create_project(self) -> None:
        args = cli.build_parser().parse_args([
            "show",
            "--seed",
            "app.example.unknown",
            "--type",
            "nsid",
        ])

        with self.assertRaisesRegex(ValueError, "project not found for seed"):
            project_commands.cmd_show(args)

        conn = registry.open_db()
        try:
            project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(project_count, 0)

    def test_project_detail_defers_duplicate_analysis_and_analysis_runs_once(self) -> None:
        conn = registry.open_db()
        try:
            source = self.make_project("proj:aaaaaaaaaaaa", "service_url", "https://source.example.com")
            source["website_url"] = "https://shared.example.com"
            target = self.make_project("proj:bbbbbbbbbbbb", "service_url", "https://target.example.com")
            target["website_url"] = "https://shared.example.com"
            registry.save_project(conn, source)
            registry.save_project(conn, target)
            original = registry.compute_duplicate_candidates
            calls = 0

            def counted(*args, **kwargs):
                nonlocal calls
                calls += 1
                return original(*args, **kwargs)

            registry.compute_duplicate_candidates = counted
            try:
                detail = presentation.project_detail(conn, project_key=source["project_key"])
                self.assertEqual(calls, 0)
                analysis = presentation.project_merge_analysis(conn, project_key=source["project_key"])
            finally:
                registry.compute_duplicate_candidates = original
            with self.assertRaisesRegex(ValueError, "recommendation changed"):
                merge.merge_projects(
                    conn,
                    project_key=source["project_key"],
                    use_recommendation=True,
                    expected_recommendation_target="proj:cccccccccccc",
                )
        finally:
            conn.close()
        self.assertTrue(detail["merge_analysis_pending"])
        self.assertEqual(detail["explanations"]["topics"]["merge_candidate"]["conclusion"], "pending")
        self.assertEqual(calls, 1)
        self.assertEqual(analysis["merge_recommendation"]["target_project_key"], target["project_key"])
        self.assertEqual(analysis["explanation"]["conclusion"], "recommended")

    def test_explain_uses_saved_state_without_network_or_llm(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:eeeeeeeeeeee")
            project["service_status"] = "live"
            project["service_status_evidence"] = ["http_200"]
            project["collection_observations"] = [
                {"collection": "app.example.post", "count": 12, "recent_count": 3},
            ]
            project.update(registry.compute_atproto_contribution(project))
            registry.save_project(conn, project)
            conn.commit()
            payload = explain.explain_project(conn, project_key="proj:eeeeeeeeeeee")
        finally:
            conn.close()
        self.assertFalse(payload["network_used"])
        self.assertFalse(payload["llm_used"])
        self.assertEqual(payload["topics"]["service_status"]["conclusion"], "live")
        self.assertEqual(payload["topics"]["contribution"]["conclusion"], "substantial")

    def test_prune_investigation_history_keeps_recent_and_latest_runs(self) -> None:
        conn = registry.open_db()
        storage.ensure_investigation_tables(conn)

        def add_run(run_id: str, project_key: str, completed_at: str) -> None:
            conn.execute(
                """
                INSERT INTO investigation_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, project_key, completed_at, completed_at, 2, "test", "{}", "{}", "{}", "{}"),
            )
            conn.execute(
                """
                INSERT INTO collector_runs VALUES (?, 0, 'test', 'collect', 'success', ?, '{}', '{}', '{}')
                """,
                (run_id, completed_at),
            )

        add_run("run:a-oldest", "proj:a", "2025-01-01T00:00:00+00:00")
        add_run("run:a-old", "proj:a", "2025-02-01T00:00:00+00:00")
        add_run("run:a-recent-1", "proj:a", "2025-06-01T00:00:00+00:00")
        add_run("run:a-recent-2", "proj:a", "2025-06-10T00:00:00+00:00")
        add_run("run:b-only", "proj:b", "2025-01-01T00:00:00+00:00")
        conn.commit()
        now = dt.datetime(2025, 6, 18, tzinfo=dt.timezone.utc)

        dry_run = maintenance.prune_investigation_history(
            conn,
            retention_days=90,
            keep_latest_per_project=2,
            now=now,
        )
        self.assertEqual(dry_run["candidate_run_count"], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM investigation_runs").fetchone()[0], 5)

        executed = maintenance.prune_investigation_history(
            conn,
            retention_days=90,
            keep_latest_per_project=2,
            execute=True,
            now=now,
        )
        remaining = {
            str(row[0]) for row in conn.execute("SELECT run_id FROM investigation_runs")
        }
        collector_remaining = {
            str(row[0]) for row in conn.execute("SELECT run_id FROM collector_runs")
        }
        conn.close()

        self.assertEqual(executed["deleted_run_count"], 2)
        self.assertEqual(executed["deleted_collector_run_count"], 2)
        self.assertEqual(remaining, {"run:a-recent-1", "run:a-recent-2", "run:b-only"})
        self.assertEqual(collector_remaining, remaining)

    def test_prune_investigation_history_requires_execute_for_vacuum(self) -> None:
        conn = registry.open_db()
        try:
            with self.assertRaisesRegex(ValueError, "requires --execute"):
                maintenance.prune_investigation_history(conn, vacuum=True)
            result = maintenance.prune_investigation_history(conn, execute=True, vacuum=True)
        finally:
            conn.close()

        self.assertTrue(result["vacuumed"])

    def test_storage_payload_cleanup_is_dry_run_atomic_and_idempotent(self) -> None:
        conn = registry.open_db()
        project = self.make_project(
            "proj:storage-cleanup",
            "repo_url",
            "https://github.com/example/app",
        )
        service_result = {
            "requested_url": "https://example.com",
            "lane": "static",
            "links": [
                {"url": f"https://example.com/page-{index}", "text": "Page"}
                for index in range(300)
            ],
            "urls": [f"https://example.com/page-{index}" for index in range(400)],
            "html_extract": "x" * 20_000,
            "content_sha256": "saved-full-response-digest",
        }
        project["service_verification_results"] = [service_result, service_result]
        registry.save_project(conn, project)

        observed_at = "2026-07-10T00:00:00+00:00"
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at=observed_at)
        bundle.add_record(EvidenceRecord(
            kind="service_verification",
            source="service_static:https://example.com",
            classification="confirmed",
            observed_at=observed_at,
            value=service_result,
        ))
        github_record = EvidenceRecord(
            kind="github_repo_snapshot",
            source="github_repo:https://github.com/example/app",
            classification="confirmed",
            observed_at=observed_at,
            value={
                "repo_url": "https://github.com/example/app",
                "repo": {
                    "name": "app",
                    "full_name": "example/app",
                    "unused_api_payload": "z" * 10_000,
                },
                "semantic_hints": [{"nsid": "app.example.post", "path": "src/client.ts"}],
                "readme_urls": [f"https://example.com/readme-{index}" for index in range(100)],
                "lexicon_hints": [],
            },
        )
        bundle.add_record(github_record)
        bundle.add_record(EvidenceRecord(
            kind=github_record.kind,
            source=github_record.source,
            classification=github_record.classification,
            observed_at="2026-07-10T00:00:01+00:00",
            value=github_record.value,
        ))
        bundle.add_collector_result(CollectorResult(
            source="github_repo",
            operation="inspect",
            status="success",
            observed_at=observed_at,
            request_count=1,
            result_count=1,
        ))
        bundle.add_collector_result(CollectorResult(
            source="github_repo",
            operation="inspect",
            status="success",
            observed_at=observed_at,
            request_count=1,
            result_count=1,
        ))
        run_id = storage.save_investigation_run(
            conn,
            project_key=project["project_key"],
            started_at=observed_at,
            completed_at=observed_at,
            input_project=project,
            output_project=project,
            bundle=bundle,
        )
        collector_rows = conn.execute(
            "SELECT ordinal, result_json FROM collector_runs WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        for row in collector_rows:
            result = json.loads(str(row["result_json"]))
            result.pop("evidence_storage", None)
            result.pop("record_count", None)
            result.pop("failure_count", None)
            conn.execute(
                """
                UPDATE collector_runs
                SET result_json = ?, records_json = ?
                WHERE run_id = ? AND ordinal = ?
                """,
                (json.dumps(result), json.dumps([github_record.to_dict()] * 2), run_id, row["ordinal"]),
            )
        conn.commit()
        state_before = str(conn.execute(
            "SELECT state_json FROM projects WHERE project_key = ?",
            (project["project_key"],),
        ).fetchone()[0])
        run_before = tuple(conn.execute(
            """
            SELECT input_project_json, output_project_json, bundle_json
            FROM investigation_runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone())
        changes_before = conn.total_changes

        with self.assertRaisesRegex(ValueError, "requires --execute"):
            maintenance.cleanup_storage_payloads(conn, vacuum=True)
        dry_run = maintenance.cleanup_storage_payloads(conn)

        self.assertEqual(dry_run["mode"], "dry_run")
        self.assertGreater(dry_run["total"]["bytes_saved"], 0)
        self.assertEqual(dry_run["investigation_snapshots"]["evaluation_diff_run_count"], 0)
        self.assertEqual(conn.total_changes, changes_before)
        self.assertEqual(
            str(conn.execute(
                "SELECT state_json FROM projects WHERE project_key = ?",
                (project["project_key"],),
            ).fetchone()[0]),
            state_before,
        )
        self.assertEqual(
            tuple(conn.execute(
                """
                SELECT input_project_json, output_project_json, bundle_json
                FROM investigation_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()),
            run_before,
        )

        executed = maintenance.cleanup_storage_payloads(conn, execute=True, vacuum=True)
        stored = registry.fetch_project(conn, project["project_key"])
        assert stored is not None
        compact_bundle = EvidenceBundle.from_json(str(conn.execute(
            "SELECT bundle_json FROM investigation_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]))
        compact_service = next(
            record.value for record in compact_bundle.records
            if record.kind == "service_verification"
        )
        compact_github = next(
            record.value for record in compact_bundle.records
            if record.kind == "github_repo_snapshot"
        )
        assert isinstance(compact_service, dict)
        assert isinstance(compact_github, dict)
        projected = conn.execute(
            "SELECT result_json, records_json, failures_json FROM collector_runs WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        second_dry_run = maintenance.cleanup_storage_payloads(conn)
        conn.close()

        self.assertEqual(executed["mode"], "execute")
        self.assertTrue(executed["storage"]["vacuumed"])
        self.assertEqual(executed["storage"]["integrity_check_before"], "ok")
        self.assertEqual(executed["storage"]["integrity_check_after"], "ok")
        self.assertEqual(executed["storage"]["foreign_key_violation_count_before"], 0)
        self.assertEqual(executed["storage"]["foreign_key_violation_count_after"], 0)
        self.assertEqual(len(stored["service_verification_results"]), 1)
        self.assertLessEqual(len(compact_service["links"]), service.SERVICE_LINK_MAX_COUNT)
        self.assertLessEqual(len(compact_service["urls"]), service.SERVICE_URL_MAX_COUNT)
        self.assertLessEqual(
            len(str(compact_service["html_extract"]).encode("utf-8")),
            service.SERVICE_HTML_EXTRACT_MAX_BYTES,
        )
        self.assertEqual(compact_service["content_sha256"], "saved-full-response-digest")
        self.assertNotIn("semantic_hints", compact_github)
        self.assertNotIn("unused_api_payload", compact_github["repo"])
        self.assertEqual(compact_github["semantic_hint_count"], 1)
        self.assertEqual(
            sum(record.kind == "github_repo_snapshot" for record in compact_bundle.records),
            1,
        )
        self.assertEqual(
            next(
                record.observed_at for record in compact_bundle.records
                if record.kind == "github_repo_snapshot"
            ),
            "2026-07-10T00:00:01+00:00",
        )
        self.assertEqual(len(projected), 1)
        self.assertEqual(json.loads(projected[0]["result_json"])["evidence_storage"], "bundle_json")
        self.assertEqual(projected[0]["records_json"], "[]")
        self.assertEqual(projected[0]["failures_json"], "[]")
        self.assertEqual(second_dry_run["total"]["bytes_saved"], 0)
        self.assertEqual(second_dry_run["collector_projection"]["changed_run_count"], 0)

    def test_storage_payload_cleanup_reports_resource_and_llm_categories(self) -> None:
        conn = registry.open_db()
        project = self.make_project(
            "proj:storage-categories",
            "service_url",
            "https://example.com",
        )
        registry.save_project(conn, project)
        oversized_resources = [
            {
                "url": f"https://example.com/docs/page-{index}",
                "role": "docs",
                "source": "oversized_resource_urls",
                "confidence": "candidate",
            }
            for index in range(20)
        ]
        oversized_llm_evidence = {
            "resource_url_records": oversized_resources,
            "summary_candidates": [{"value": "x" * 10_000, "source": "historical_fixture"}],
        }
        state_row = conn.execute(
            "SELECT state_json FROM projects WHERE project_key = ?",
            (project["project_key"],),
        ).fetchone()
        state = json.loads(str(state_row[0]))
        state.setdefault("evidence", {})["resource_url_records"] = oversized_resources
        state["evidence"]["llm_source"] = {"evidence": oversized_llm_evidence}
        conn.execute(
            "UPDATE projects SET state_json = ? WHERE project_key = ?",
            (json.dumps(state), project["project_key"]),
        )
        raw_snapshot = dict(project)
        raw_snapshot["resource_url_records"] = oversized_resources
        raw_snapshot["llm_source"] = {"evidence": oversized_llm_evidence}
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-07-11T00:00:00+00:00")
        storage.save_investigation_run(
            conn,
            project_key=project["project_key"],
            started_at=bundle.collected_at,
            completed_at=bundle.collected_at,
            input_project=raw_snapshot,
            output_project=raw_snapshot,
            bundle=bundle,
        )
        conn.commit()
        try:
            dry_run = maintenance.cleanup_storage_payloads(conn)
            executed = maintenance.cleanup_storage_payloads(conn, execute=True)
            second = maintenance.cleanup_storage_payloads(conn)
            saved_state = json.loads(str(conn.execute(
                "SELECT state_json FROM projects WHERE project_key = ?",
                (project["project_key"],),
            ).fetchone()[0]))
        finally:
            conn.close()

        resource_stats = dry_run["payload_categories"]["resource_urls"]
        self.assertGreater(resource_stats["bytes_saved"], 0)
        self.assertEqual(resource_stats["removed_by_role"]["docs"], 48)
        self.assertEqual(resource_stats["removed_by_host"]["example.com"], 48)
        self.assertEqual(resource_stats["removed_by_path_pattern"]["/docs"], 48)
        self.assertGreater(dry_run["payload_categories"]["llm_evidence"]["bytes_saved"], 0)
        self.assertEqual(executed["investigation_snapshots"]["evaluation_diff_run_count"], 0)
        self.assertEqual(len(saved_state["evidence"]["resource_url_records"]), 4)
        self.assertEqual(second["payload_categories"]["resource_urls"]["bytes_saved"], 0)
        self.assertEqual(second["payload_categories"]["llm_evidence"]["bytes_saved"], 0)

    def test_storage_payload_cleanup_rolls_back_on_projection_failure(self) -> None:
        conn = registry.open_db()
        project = self.make_project("proj:storage-cleanup-rollback")
        project["service_verification_results"] = [{
            "requested_url": "https://example.com",
            "lane": "static",
            "html_extract": "x" * 20_000,
        }]
        registry.save_project(conn, project)
        observed_at = "2026-07-10T00:00:00+00:00"
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at=observed_at)
        storage.save_investigation_run(
            conn,
            project_key=project["project_key"],
            started_at=observed_at,
            completed_at=observed_at,
            input_project=project,
            output_project=project,
            bundle=bundle,
        )
        conn.commit()
        state_before = str(conn.execute(
            "SELECT state_json FROM projects WHERE project_key = ?",
            (project["project_key"],),
        ).fetchone()[0])
        with (
            mock.patch.object(
                storage,
                "replace_collector_run_projection",
                side_effect=RuntimeError("injected projection failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "injected projection failure"),
        ):
            maintenance.cleanup_storage_payloads(conn, execute=True)

        state_after = str(conn.execute(
            "SELECT state_json FROM projects WHERE project_key = ?",
            (project["project_key"],),
        ).fetchone()[0])
        conn.close()
        self.assertEqual(state_after, state_before)

    def test_project_services_do_not_require_cli_namespace(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(conn, seed="https://example.com", seed_type="service_url")
            selected = projects.select_projects(conn, project_key=created["project_key"])
            before = registry.fetch_project(conn, created["project_key"])
            assert before is not None
            updated = projects.set_tracking_state(
                conn,
                project_key=created["project_key"],
                tracking_state="stable",
                note="application service test",
            )
            after = registry.fetch_project(conn, created["project_key"])
            assert after is not None
        finally:
            conn.close()

        self.assertEqual(len(selected), 1)
        self.assertEqual(updated["tracking_state"], "stable")
        self.assertEqual(updated["tracking_note"], "application service test")
        self.assertEqual(after["last_seen_at"], before["last_seen_at"])
        self.assertEqual(after["last_enriched_at"], before["last_enriched_at"])

    def test_project_services_reject_removed_tracking_state(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(conn, seed="https://example.com", seed_type="service_url")
            with self.assertRaisesRegex(ValueError, "invalid tracking_state"):
                projects.set_tracking_state(
                    conn,
                    project_key=created["project_key"],
                    tracking_state="watchlist",
                )
            after = registry.fetch_project(conn, created["project_key"])
            assert after is not None
        finally:
            conn.close()

        self.assertEqual(after["tracking_state"], "active")

    def test_decision_services_persist_normalized_rows(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(conn, seed="https://example.com", seed_type="service_url")
            result = decisions.set_identifier_block(
                conn,
                project_key=created["project_key"],
                identifier_type="service_url",
                value="https://wrong.example",
                note="not this project",
            )
            decisions.dismiss_review_kind(conn, project_key=created["project_key"], kind="candidate_repo_needs_confirmation")
            rows = conn.execute(
                "SELECT kind, target_type, target_value FROM project_decisions WHERE project_key = ? ORDER BY ordinal",
                (created["project_key"],),
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(result["value"], "https://wrong.example")
        self.assertEqual(
            [(row["kind"], row["target_type"], row["target_value"]) for row in rows],
            [
                ("block_identifier", "service_url", "https://wrong.example"),
                ("dismiss_review", "review_kind", "candidate_repo_needs_confirmation"),
            ],
        )

    def test_confirm_candidate_records_operator_decision(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(conn, seed="https://example.com", seed_type="service_url")
            project = registry.fetch_project(conn, created["project_key"])
            assert project is not None
            project["service_url_candidates"] = ["https://app.example.com"]
            registry.save_project(conn, project, preserve_timestamps=True)
            result = presentation.confirm_candidate(
                conn,
                project_key=created["project_key"],
                identifier_type="service_url",
                value="https://app.example.com",
            )
            rows = conn.execute(
                "SELECT kind, target_type, target_value FROM project_decisions WHERE project_key = ? ORDER BY ordinal",
                (created["project_key"],),
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(result["value"], "https://app.example.com")
        self.assertIn(
            ("confirm_identifier", "service_url", "https://app.example.com"),
            [(row["kind"], row["target_type"], row["target_value"]) for row in rows],
        )

    def test_merge_service_supports_dry_run_and_execute(self) -> None:
        conn = registry.open_db()
        try:
            source = projects.add_seed(conn, seed="https://source.example", seed_type="service_url")
            target = projects.add_seed(conn, seed="https://target.example", seed_type="service_url")
            preview = merge.merge_projects(
                conn,
                project_key=source["project_key"],
                target_project_key=target["project_key"],
            )
            self.assertIsNotNone(registry.fetch_project(conn, source["project_key"]))
            executed = merge.merge_projects(
                conn,
                project_key=source["project_key"],
                target_project_key=target["project_key"],
                execute=True,
                force=True,
            )
        finally:
            conn.close()

        self.assertEqual(preview["mode"], "dry_run")
        self.assertEqual(executed["mode"], "execute")
        conn = registry.open_db()
        try:
            self.assertIsNone(registry.fetch_project(conn, source["project_key"]))
            self.assertIsNotNone(registry.fetch_project(conn, target["project_key"]))
        finally:
            conn.close()

    def test_explicit_merge_dry_run_does_not_compute_global_recommendations(self) -> None:
        conn = registry.open_db()
        try:
            source = projects.add_seed(conn, seed="https://source.example", seed_type="service_url")
            target = projects.add_seed(conn, seed="https://target.example", seed_type="service_url")
            original = registry.compute_duplicate_candidates

            def fail_compute(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
                raise AssertionError("explicit dry-run merge preview should not compute global duplicate candidates")

            registry.compute_duplicate_candidates = fail_compute
            try:
                preview = merge.merge_projects(
                    conn,
                    project_key=source["project_key"],
                    target_project_key=target["project_key"],
                    execute=False,
                    force=True,
                )
            finally:
                registry.compute_duplicate_candidates = original
        finally:
            conn.close()

        self.assertEqual(preview["mode"], "dry_run")
        self.assertEqual(preview["source_project_key"], source["project_key"])
        self.assertEqual(preview["target_project_key"], target["project_key"])

    def test_split_identifier_keeps_machine_history_out_of_operator_notes(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(conn, seed="https://source.example", seed_type="service_url")
            source = registry.fetch_project(conn, created["project_key"])
            assert source is not None
            source["notes"] = "operator note"
            registry.confirm_source_code_url(source, "https://github.com/example/app", source="manual_seed")
            registry.save_project(conn, source)
            conn.commit()

            result = merge.split_identifier(
                conn,
                project_key=source["project_key"],
                identifier_type="repo_url",
                value="https://github.com/example/app",
                note="separate package",
                execute=True,
            )
            saved = registry.fetch_project(conn, source["project_key"])
        finally:
            conn.close()

        self.assertEqual(result["mode"], "execute")
        assert saved is not None
        self.assertEqual(saved["notes"], "operator note")
        self.assertTrue(any(item.startswith("split out repo_url:https://github.com/example/app -> ") for item in saved["system_notes"]))
        self.assertFalse(any("split out" in line for line in saved["notes"].splitlines()))

    def test_merge_preserves_source_key_and_investigation_history_as_alias(self) -> None:
        conn = registry.open_db()
        try:
            source = projects.add_seed(conn, seed="https://source.example", seed_type="service_url")
            target = projects.add_seed(conn, seed="https://target.example", seed_type="service_url")
            final = projects.add_seed(conn, seed="https://final.example", seed_type="service_url")
            other = projects.add_seed(conn, seed="https://other.example", seed_type="service_url")
            decisions.set_separate(
                conn,
                project_key=source["project_key"],
                other_project_key=other["project_key"],
            )
            source_project = registry.fetch_project(conn, source["project_key"])
            assert source_project is not None
            run_id = storage.save_investigation_run(
                conn,
                project_key=source["project_key"],
                started_at="2026-06-19T00:00:00+00:00",
                completed_at="2026-06-19T00:01:00+00:00",
                input_project=source_project,
                output_project=source_project,
                bundle=EvidenceBundle(
                    project_key=source["project_key"],
                    collected_at="2026-06-19T00:01:00+00:00",
                ),
            )
            conn.commit()

            merge.merge_projects(
                conn,
                project_key=source["project_key"],
                target_project_key=target["project_key"],
                execute=True,
                force=True,
            )
            merge.merge_projects(
                conn,
                project_key=target["project_key"],
                target_project_key=final["project_key"],
                execute=True,
                force=True,
            )

            aliases = {
                row["alias_project_key"]: (row["canonical_project_key"], row["reason"])
                for row in conn.execute("SELECT * FROM project_key_aliases")
            }
            run = storage.load_investigation_run(conn, run_id)
            summaries = storage.list_investigation_run_summaries(conn, project_key=final["project_key"])
            summaries_via_alias = storage.list_investigation_run_summaries(conn, project_key=source["project_key"])
            resolved_source_alias = registry.resolve_project_key_input(conn, source["project_key"])
            resolved_target_alias = registry.resolve_project_key_input(conn, target["project_key"])
            other_separate = conn.execute(
                """
                SELECT target_value FROM project_decisions
                WHERE project_key = ? AND kind = 'separate_project'
                """,
                (other["project_key"],),
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(aliases[source["project_key"]], (final["project_key"], "merge"))
        self.assertEqual(aliases[target["project_key"]], (final["project_key"], "merge"))
        self.assertEqual(resolved_source_alias, final["project_key"])
        self.assertEqual(resolved_target_alias, final["project_key"])
        self.assertEqual(run["project_key"], source["project_key"])
        self.assertEqual([item["run_id"] for item in summaries], [run_id])
        self.assertEqual([item["run_id"] for item in summaries_via_alias], [run_id])
        self.assertIn(final["project_key"], [row["target_value"] for row in other_separate])
        self.assertNotIn(source["project_key"], [row["target_value"] for row in other_separate])
        self.assertNotIn(target["project_key"], [row["target_value"] for row in other_separate])

    def test_merge_rolls_back_target_and_alias_when_source_delete_fails(self) -> None:
        conn = registry.open_db()
        try:
            source = projects.add_seed(conn, seed="https://source.example", seed_type="service_url")
            target = projects.add_seed(conn, seed="https://target.example", seed_type="service_url")
            target_before = registry.fetch_project(conn, target["project_key"])
            with (
                mock.patch.object(merge.core, "delete_project", side_effect=RuntimeError("delete failed")),
                self.assertRaisesRegex(RuntimeError, "delete failed"),
            ):
                merge.merge_projects(
                    conn,
                    project_key=source["project_key"],
                    target_project_key=target["project_key"],
                    execute=True,
                    force=True,
                )
            target_after = registry.fetch_project(conn, target["project_key"])
            source_after = registry.fetch_project(conn, source["project_key"])
            alias_count = conn.execute("SELECT COUNT(*) FROM project_key_aliases").fetchone()[0]
        finally:
            conn.close()

        self.assertIsNotNone(source_after)
        self.assertEqual(target_after["manual_seeds"], target_before["manual_seeds"])
        self.assertEqual(alias_count, 0)

    def test_split_and_separate_roll_back_when_second_project_save_fails(self) -> None:
        conn = registry.open_db()
        try:
            source = projects.add_seed(conn, seed="https://source.example", seed_type="service_url")
            other = projects.add_seed(conn, seed="https://other.example", seed_type="service_url")
            source_before = registry.fetch_project(conn, source["project_key"])
            project_keys_before = {
                row["project_key"] for row in conn.execute("SELECT project_key FROM projects")
            }
            original_save = registry.save_project
            calls = 0

            def fail_second_save(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("second save failed")
                return original_save(*args, **kwargs)

            with (
                mock.patch.object(merge.core, "save_project", side_effect=fail_second_save),
                self.assertRaisesRegex(RuntimeError, "second save failed"),
            ):
                merge.split_identifier(
                    conn,
                    project_key=source["project_key"],
                    identifier_type="service_url",
                    value="https://source.example",
                    execute=True,
                )
            source_after_split = registry.fetch_project(conn, source["project_key"])
            project_keys_after = {
                row["project_key"] for row in conn.execute("SELECT project_key FROM projects")
            }
            self.assertEqual(project_keys_after, project_keys_before)
            self.assertEqual(source_after_split["manual_seeds"], source_before["manual_seeds"])

            calls = 0
            with (
                mock.patch.object(decisions.core, "save_project", side_effect=fail_second_save),
                self.assertRaisesRegex(RuntimeError, "second save failed"),
            ):
                decisions.set_separate(
                    conn,
                    project_key=source["project_key"],
                    other_project_key=other["project_key"],
                )
            source_after_separate = registry.fetch_project(conn, source["project_key"])
            other_after_separate = registry.fetch_project(conn, other["project_key"])
        finally:
            conn.close()

        self.assertNotIn(other["project_key"], source_after_separate["separate_project_keys"])
        self.assertNotIn(source["project_key"], other_after_separate["separate_project_keys"])

    def test_review_queries_are_available_as_services(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(conn, seed="https://example.com", seed_type="service_url")
            project = registry.fetch_project(conn, created["project_key"])
            assert project is not None
            project["service_url_candidates"] = ["https://app.example.com"]
            registry.save_project(conn, project)
            conn.commit()
            items = review.review_queue(conn, tracking_states={"active"}, limit=10)
        finally:
            conn.close()

        self.assertTrue(any(item["project_key"] == created["project_key"] for item in items))


if __name__ == "__main__":
    import unittest

    unittest.main()
