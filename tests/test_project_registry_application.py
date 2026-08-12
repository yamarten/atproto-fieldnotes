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
from project_registry_core import project_record


class ProjectRegistryApplicationTest(TempRegistryDbTestCase):
    def test_promotion_priority_decision_roundtrips_and_is_independent_from_defer(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:editorial", "service_url", "https://example.com")
            project.update({
                "category": "project",
                "service_status": "live",
                "service_status_confidence": "medium",
                "service_status_evidence": ["http_200", "login_surface_detected"],
            })
            registry.confirm_service_seed(project, "https://example.com", source="test")
            project_record.save_project(conn, project)
            conn.commit()

            result = decisions.set_promotion_priority(
                conn,
                project_key=project["project_key"],
                lowered=True,
                reason="best presented with similar projects",
            )
            decisions.set_identifier_block(
                conn,
                project_key=project["project_key"],
                identifier_type="repo_url",
                value="https://github.com/example/unrelated",
                note="unrelated",
            )
            saved = project_record.fetch_project(conn, project["project_key"])
            assert saved is not None
            signals = registry.compute_showcase_signals(saved)
            policy = registry.promotion_policy_projection(saved, signals)

            self.assertEqual(result["priority_group"], "lowered")
            self.assertEqual(policy["candidate_visibility"]["group"], "eligible")
            self.assertEqual(policy["priority_group"], "lowered")
            self.assertEqual(policy["priority_reason"], "best presented with similar projects")
            self.assertIsNone(registry.active_promotion_deferral(saved))
            rows = conn.execute(
                "SELECT kind, target_type, target_value, cleared_at FROM project_decisions "
                "WHERE project_key = ? AND kind = 'lower_promotion_priority'",
                (project["project_key"],),
            ).fetchall()
            self.assertEqual([tuple(row) for row in rows], [
                ("lower_promotion_priority", "promotion_priority", "lowered", "")
            ])

            cleared = decisions.set_promotion_priority(
                conn,
                project_key=project["project_key"],
                lowered=False,
            )
            saved = project_record.fetch_project(conn, project["project_key"])
            assert saved is not None
            self.assertEqual(cleared["priority_group"], "normal")
            self.assertEqual(
                registry.promotion_policy_projection(saved, registry.compute_showcase_signals(saved))["priority_group"],
                "normal",
            )
        finally:
            conn.close()

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
            project_record.save_project(conn, source)
            project_record.save_project(conn, target)
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
            project = self.make_project("proj:eeeeeeeeeeee", "service_url", "https://example.com")
            project["service_status"] = "live"
            project["service_status_evidence"] = ["http_200"]
            project["collection_observations"] = [
                {"collection": "app.example.post", "count": 12, "recent_count": 3},
            ]
            project.update(project_record.compute_atproto_contribution(project))
            project_record.save_project(conn, project)
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

    def test_history_compression_dry_run_execute_and_decode_are_lossless(self) -> None:
        conn = registry.open_db()
        storage.ensure_investigation_tables(conn)
        payloads = (
            {"input": ["repeated-value"] * 100},
            {"output": ["repeated-value"] * 100},
            {"score": 4, "details": ["repeated-value"] * 100},
        )
        bundle = EvidenceBundle(project_key="proj:compressed", collected_at="2026-07-26T00:00:00+00:00")
        conn.execute(
            "INSERT INTO investigation_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "run:legacy",
                "proj:compressed",
                bundle.collected_at,
                bundle.collected_at,
                bundle.schema_version,
                "test",
                json.dumps(payloads[0]),
                json.dumps(payloads[1]),
                json.dumps(payloads[2]),
                bundle.to_json(),
            ),
        )
        conn.commit()
        before = tuple(conn.execute(
            "SELECT input_project_json, output_project_json, evaluation_json, bundle_json "
            "FROM investigation_runs WHERE run_id = 'run:legacy'"
        ).fetchone())

        dry_run = maintenance.compress_investigation_history(conn)
        self.assertEqual(dry_run["candidate_run_count"], 1)
        self.assertEqual(dry_run["candidate_value_count"], 4)
        self.assertEqual(dry_run["decode_failure_count"], 0)
        self.assertGreater(dry_run["estimated_bytes_saved"], 0)
        self.assertEqual(tuple(conn.execute(
            "SELECT input_project_json, output_project_json, evaluation_json, bundle_json "
            "FROM investigation_runs WHERE run_id = 'run:legacy'"
        ).fetchone()), before)

        executed = maintenance.compress_investigation_history(conn, execute=True)
        stored = tuple(conn.execute(
            "SELECT input_project_json, output_project_json, evaluation_json, bundle_json "
            "FROM investigation_runs WHERE run_id = 'run:legacy'"
        ).fetchone())
        loaded = storage.load_investigation_run(conn, "run:legacy")
        second_dry_run = maintenance.compress_investigation_history(conn)
        conn.close()

        self.assertEqual(executed["updated_run_count"], 1)
        self.assertEqual(executed["integrity_check_before"], "ok")
        self.assertEqual(executed["integrity_check_after"], "ok")
        self.assertTrue(all(storage.is_compressed_history_json(value) for value in stored))
        assert loaded is not None
        self.assertEqual(loaded["input_project"], payloads[0])
        self.assertEqual(loaded["output_project"], payloads[1])
        self.assertEqual(loaded["evaluation"], payloads[2])
        self.assertEqual(loaded["bundle"].to_dict(), bundle.to_dict())
        self.assertEqual(second_dry_run["candidate_run_count"], 0)

    def test_history_compression_reports_decode_failure_and_refuses_execute(self) -> None:
        conn = registry.open_db()
        storage.ensure_investigation_tables(conn)
        bad_payload = storage.HISTORY_JSON_COMPRESSION_HEADER + b"not-zlib"
        conn.execute(
            "INSERT INTO investigation_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "run:broken", "proj:broken", "2026-07-26", "2026-07-26", 2, "test",
                bad_payload, "{}", "{}", "{}",
            ),
        )
        conn.commit()

        dry_run = maintenance.compress_investigation_history(conn)
        with self.assertRaisesRegex(ValueError, "failed to decode"):
            maintenance.compress_investigation_history(conn, execute=True)
        with self.assertRaisesRegex(storage.HistoryPayloadDecodeError, "run:broken input_project_json"):
            storage.load_investigation_run(conn, "run:broken")
        conn.close()

        self.assertEqual(dry_run["decode_failure_count"], 1)
        self.assertEqual(dry_run["decode_failures"][0]["column"], "input_project_json")

    def test_history_compression_requires_execute_for_vacuum(self) -> None:
        conn = registry.open_db()
        try:
            with self.assertRaisesRegex(ValueError, "requires --execute"):
                maintenance.compress_investigation_history(conn, vacuum=True)
        finally:
            conn.close()

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
        project_record.save_project(conn, project)

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
        stored = project_record.fetch_project(conn, project["project_key"])
        assert stored is not None
        compact_bundle = EvidenceBundle.from_json(storage.decode_history_json_text(
            conn.execute(
                "SELECT bundle_json FROM investigation_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0],
            context=f"investigation run {run_id} bundle_json",
        ))
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
        project_record.save_project(conn, project)
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
        project_record.save_project(conn, project)
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
            before = project_record.fetch_project(conn, created["project_key"])
            assert before is not None
            updated = projects.set_tracking_state(
                conn,
                project_key=created["project_key"],
                tracking_state="stable",
                note="application service test",
            )
            after = project_record.fetch_project(conn, created["project_key"])
            assert after is not None
        finally:
            conn.close()

        self.assertEqual(len(selected), 1)
        self.assertEqual(updated["tracking_state"], "stable")
        self.assertEqual(updated["tracking_note"], "application service test")
        self.assertEqual(after["last_seen_at"], before["last_seen_at"])
        self.assertEqual(after["last_enriched_at"], before["last_enriched_at"])

    def test_add_seed_confirms_exact_service_path_and_removes_origin_candidate(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(
                conn,
                seed="https://tools.example.com/bluesky-list-manager",
                seed_type="service_url",
            )
            project = project_record.fetch_project(conn, created["project_key"])
            rows = conn.execute(
                "SELECT role, classification FROM project_identifiers "
                "WHERE project_key = ? AND kind = 'service_url' AND value = ?",
                (created["project_key"], "https://tools.example.com/bluesky-list-manager"),
            ).fetchall()
        finally:
            conn.close()

        assert project is not None
        self.assertEqual(created["confirmed_role"], "service")
        self.assertEqual(project["website_url"], "https://tools.example.com/bluesky-list-manager")
        self.assertIn("https://tools.example.com/bluesky-list-manager", project["service_urls"])
        self.assertNotIn("https://tools.example.com", project["service_url_candidates"])
        self.assertIn(("service", "confirmed"), [(row["role"], row["classification"]) for row in rows])
        self.assertTrue(
            registry.identifier_has_active_confirm_decision(
                project,
                "service_url",
                "https://tools.example.com/bluesky-list-manager",
            )
        )

    def test_add_seed_reapplies_existing_manual_service_as_confirmed_service(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:manualonly", "repo_url", "https://github.com/example/app")
            registry.add_manual_seed(
                project,
                "service_url",
                "https://tools.example.com/import/writing",
                source="legacy",
            )
            project_record.save_project(conn, project)
            created = projects.add_seed(
                conn,
                seed="https://tools.example.com/import/writing",
                seed_type="service_url",
                project_key=project["project_key"],
            )
            saved = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        assert saved is not None
        self.assertEqual(created["confirmed_role"], "service")
        self.assertIn("https://tools.example.com/import/writing", saved["service_urls"])

    def test_add_seed_does_not_replace_existing_website(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:websiteandservice", "repo_url", "https://github.com/example/app")
            project["website_url"] = "https://docs.example.com/app"
            project_record.save_project(conn, project)
            projects.add_seed(
                conn,
                seed="https://api.example.com",
                seed_type="service_url",
                project_key=project["project_key"],
            )
            saved = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        assert saved is not None
        self.assertEqual(saved["website_url"], "https://docs.example.com/app")
        self.assertIn("https://api.example.com", saved["service_urls"])

    def test_add_seed_treats_explicit_supporting_path_as_service(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(
                conn,
                seed="https://app.example.com/about",
                seed_type="service_url",
            )
            project = project_record.fetch_project(conn, created["project_key"])
        finally:
            conn.close()

        assert project is not None
        self.assertEqual(created["confirmed_role"], "service")
        self.assertIn("https://app.example.com/about", project["service_urls"])
        self.assertNotIn("https://app.example.com", project["service_url_candidates"])
        self.assertTrue(
            registry.identifier_has_active_confirm_decision(
                project,
                "service_url",
                "https://app.example.com/about",
            )
        )

    def test_add_seed_explicit_path_overrides_ancestor_guard(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:guardedpath", "repo_url", "https://github.com/example/app")
            project["blocked_identifiers"] = [
                {
                    "type": "service_url",
                    "value": "https://tools.example.com",
                    "note": "root is not the service",
                    "added_at": project_record.iso_now(),
                }
            ]
            project["deferred_identifiers"] = [
                {
                    "type": "service_url",
                    "value": "https://tools.example.com/import/writing",
                    "note": "waiting for review",
                    "added_at": project_record.iso_now(),
                    "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(),
                }
            ]
            project_record.save_project(conn, project)
            created = projects.add_seed(
                conn,
                seed="https://tools.example.com/import/writing",
                seed_type="service_url",
                project_key=project["project_key"],
            )
            saved = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        assert saved is not None
        self.assertEqual(created["confirmed_role"], "service")
        self.assertIn("https://tools.example.com/import/writing", saved["service_urls"])
        self.assertIn(
            "https://tools.example.com",
            registry.blocked_identifier_values(saved, "service_url"),
        )
        self.assertNotIn(
            "https://tools.example.com/import/writing",
            registry.deferred_identifier_values(saved, "service_url"),
        )

    def test_defer_after_add_seed_clears_operator_confirmation(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(
                conn,
                seed="https://tools.example.com/import/writing",
                seed_type="service_url",
            )
            decisions.set_identifier_defer(
                conn,
                project_key=created["project_key"],
                identifier_type="service_url",
                value="https://tools.example.com/import/writing",
            )
            saved = project_record.fetch_project(conn, created["project_key"])
        finally:
            conn.close()

        assert saved is not None
        self.assertNotIn("https://tools.example.com/import/writing", saved["service_urls"])
        self.assertFalse(
            registry.identifier_has_active_confirm_decision(
                saved,
                "service_url",
                "https://tools.example.com/import/writing",
            )
        )

    def test_add_seed_rejects_guarded_nsid_without_partial_seed_write(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:guardedseed", "repo_url", "https://github.com/example/app")
            project["blocked_identifiers"] = [
                {
                    "type": "nsid",
                    "value": "dev.example.wrong",
                    "note": "not this project",
                    "added_at": project_record.iso_now(),
                }
            ]
            project_record.save_project(conn, project)
            with self.assertRaisesRegex(ValueError, "guarded identifier"):
                projects.add_seed(
                    conn,
                    seed="dev.example.wrong",
                    seed_type="nsid",
                    project_key=project["project_key"],
                )
            saved = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        assert saved is not None
        self.assertNotIn(
            "dev.example.wrong",
            [item["value"] for item in saved["manual_seeds"]],
        )

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
            after = project_record.fetch_project(conn, created["project_key"])
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
                ("confirm_identifier", "service_url", "https://example.com"),
            ],
        )

    def test_confirm_candidate_records_operator_decision(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(conn, seed="https://example.com", seed_type="service_url")
            project = project_record.fetch_project(conn, created["project_key"])
            assert project is not None
            project["service_url_candidates"] = ["https://app.example.com"]
            project_record.save_project(conn, project, preserve_timestamps=True)
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

    def test_confirming_candidate_service_does_not_set_website(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:candidatewebsite", "repo_url", "https://github.com/example/app")
            project_record.save_project(conn, project)
            conn.execute(
                "INSERT INTO project_identifiers "
                "(project_key, ordinal, kind, value, role, classification, is_primary, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project["project_key"], 99, "service_url", "https://app.example.com", "service", "candidate", 0, "{}"),
            )
            conn.commit()

            presentation.confirm_candidate(
                conn,
                project_key=project["project_key"],
                identifier_type="service_url",
                value="https://app.example.com",
            )
            saved = project_record.fetch_project(conn, project["project_key"])
            row = conn.execute(
                "SELECT classification FROM project_identifiers "
                "WHERE project_key = ? AND kind = 'service_url' AND value = ? AND role = 'service'",
                (project["project_key"], "https://app.example.com"),
            ).fetchone()
        finally:
            conn.close()

        assert saved is not None
        self.assertEqual(saved["website_url"], "")
        self.assertEqual(row["classification"], "confirmed")

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
            self.assertIsNotNone(project_record.fetch_project(conn, source["project_key"]))
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
            self.assertIsNone(project_record.fetch_project(conn, source["project_key"]))
            self.assertIsNotNone(project_record.fetch_project(conn, target["project_key"]))
        finally:
            conn.close()

    def test_merge_returns_next_candidate_without_retaining_deleted_source(self) -> None:
        conn = registry.open_db()
        try:
            source = projects.add_seed(conn, seed="https://source.example", seed_type="service_url")
            target = projects.add_seed(conn, seed="https://target.example", seed_type="service_url")
            remaining = projects.add_seed(conn, seed="https://remaining.example", seed_type="service_url")
            shared_repo = "https://github.com/example/shared"
            for project_key in (source["project_key"], target["project_key"], remaining["project_key"]):
                project = project_record.fetch_project(conn, project_key)
                assert project is not None
                registry.confirm_source_code_url(project, shared_repo, "test")
                project_record.save_project(conn, project, preserve_timestamps=True)
            conn.commit()

            executed = merge.merge_projects(
                conn,
                project_key=source["project_key"],
                target_project_key=target["project_key"],
                execute=True,
                force=True,
            )
            saved = project_record.fetch_project(conn, target["project_key"])
        finally:
            conn.close()

        assert saved is not None
        candidate_keys = {item["project_key"] for item in saved["duplicate_candidates"]}
        self.assertNotIn(source["project_key"], candidate_keys)
        self.assertIn(remaining["project_key"], candidate_keys)
        self.assertEqual(executed["remaining_merge_candidate_count"], 1)
        self.assertEqual(
            {
                executed["next_merge_review"]["source_project_key"],
                executed["next_merge_review"]["target_project_key"],
            },
            {target["project_key"], remaining["project_key"]},
        )

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
            source = project_record.fetch_project(conn, created["project_key"])
            assert source is not None
            source["notes"] = "operator note"
            registry.confirm_source_code_url(source, "https://github.com/example/app", source="manual_seed")
            project_record.save_project(conn, source)
            conn.commit()

            result = merge.split_identifier(
                conn,
                project_key=source["project_key"],
                identifier_type="repo_url",
                value="https://github.com/example/app",
                note="separate package",
                execute=True,
            )
            saved = project_record.fetch_project(conn, source["project_key"])
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
            source_project = project_record.fetch_project(conn, source["project_key"])
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
            resolved_source_alias = project_record.resolve_project_key_input(conn, source["project_key"])
            resolved_target_alias = project_record.resolve_project_key_input(conn, target["project_key"])
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
            target_before = project_record.fetch_project(conn, target["project_key"])
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
            target_after = project_record.fetch_project(conn, target["project_key"])
            source_after = project_record.fetch_project(conn, source["project_key"])
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
            source_before = project_record.fetch_project(conn, source["project_key"])
            project_keys_before = {
                row["project_key"] for row in conn.execute("SELECT project_key FROM projects")
            }
            original_save = project_record.save_project
            calls = 0

            def fail_second_save(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("second save failed")
                return original_save(*args, **kwargs)

            with (
                mock.patch.object(merge.project_record, "save_project", side_effect=fail_second_save),
                self.assertRaisesRegex(RuntimeError, "second save failed"),
            ):
                merge.split_identifier(
                    conn,
                    project_key=source["project_key"],
                    identifier_type="service_url",
                    value="https://source.example",
                    execute=True,
                )
            source_after_split = project_record.fetch_project(conn, source["project_key"])
            project_keys_after = {
                row["project_key"] for row in conn.execute("SELECT project_key FROM projects")
            }
            self.assertEqual(project_keys_after, project_keys_before)
            self.assertEqual(source_after_split["manual_seeds"], source_before["manual_seeds"])

            calls = 0
            with (
                mock.patch.object(decisions.project_record, "save_project", side_effect=fail_second_save),
                self.assertRaisesRegex(RuntimeError, "second save failed"),
            ):
                decisions.set_separate(
                    conn,
                    project_key=source["project_key"],
                    other_project_key=other["project_key"],
                )
            source_after_separate = project_record.fetch_project(conn, source["project_key"])
            other_after_separate = project_record.fetch_project(conn, other["project_key"])
        finally:
            conn.close()

        self.assertNotIn(other["project_key"], source_after_separate["separate_project_keys"])
        self.assertNotIn(source["project_key"], other_after_separate["separate_project_keys"])

    def test_review_queries_are_available_as_services(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(conn, seed="https://example.com", seed_type="service_url")
            project = project_record.fetch_project(conn, created["project_key"])
            assert project is not None
            project["service_url_candidates"] = ["https://app.example.com"]
            project_record.save_project(conn, project)
            conn.commit()
            items = review.review_queue(conn, tracking_states={"active"}, limit=10)
        finally:
            conn.close()

        self.assertTrue(any(item["project_key"] == created["project_key"] for item in items))


if __name__ == "__main__":
    import unittest

    unittest.main()
