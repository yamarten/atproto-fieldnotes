from __future__ import annotations

import datetime as dt
import json
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import storage
from project_registry_core.evidence import EvidenceBundle
from project_registry_core.application import decisions, explain, maintenance, merge, presentation, projects, review


class ProjectRegistryApplicationTest(TempRegistryDbTestCase):
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

    def test_prune_migration_provenance_preserves_identifier_semantics(self) -> None:
        conn = registry.open_db()
        created = projects.add_seed(conn, seed="https://example.com", seed_type="service_url")
        row = conn.execute(
            """
            SELECT * FROM project_identifiers
            WHERE project_key = ? AND role = 'manual_seed'
            """,
            (created["project_key"],),
        ).fetchone()
        metadata = {
            "migration_source": "schema_v1",
            "legacy_field": "manual_seeds",
            "legacy_classification": "confirmed",
            "trust_policy": "v1-quarantine-1",
            "trust_basis": "operator_input",
            "legacy_record": {
                "type": "service_url",
                "value": "https://example.com",
                "added_at": "2025-01-01T00:00:00+00:00",
            },
            "retained_note": "keep",
        }
        conn.execute(
            "UPDATE project_identifiers SET metadata_json = ? WHERE project_key = ? AND ordinal = ?",
            (json.dumps(metadata), created["project_key"], int(row["ordinal"])),
        )
        repo_ordinal = int(conn.execute(
            "SELECT MAX(ordinal) + 1 FROM project_identifiers WHERE project_key = ?",
            (created["project_key"],),
        ).fetchone()[0])
        conn.execute(
            """
            INSERT INTO project_identifiers VALUES (?, ?, 'repo_url', ?, 'source_repository_record',
                'candidate', 0, 'github', ?, '')
            """,
            (
                created["project_key"],
                repo_ordinal,
                "https://github.com/example/app",
                json.dumps({
                    "migration_source": "schema_v1",
                    "legacy_classification": "confirmed",
                    "trust_policy": "v1-quarantine-1",
                    "downgrade_reason": "no current corroboration",
                    "legacy_record": {
                        "url": "https://github.com/example/app",
                        "provider": "github",
                        "confidence": "confirmed",
                        "source": "legacy",
                    },
                }),
            ),
        )
        conn.commit()
        semantic_before = tuple(row[key] for key in (
            "project_key", "kind", "value", "role", "classification", "is_primary", "provider", "observed_at",
        ))

        dry_run = maintenance.prune_migration_provenance(conn)
        unchanged = json.loads(conn.execute(
            "SELECT metadata_json FROM project_identifiers WHERE project_key = ? AND ordinal = ?",
            (created["project_key"], int(row["ordinal"])),
        ).fetchone()[0])
        executed = maintenance.prune_migration_provenance(conn, execute=True)
        after = conn.execute(
            "SELECT * FROM project_identifiers WHERE project_key = ? AND ordinal = ?",
            (created["project_key"], int(row["ordinal"])),
        ).fetchone()
        repo_after = conn.execute(
            "SELECT * FROM project_identifiers WHERE project_key = ? AND ordinal = ?",
            (created["project_key"], repo_ordinal),
        ).fetchone()
        fetched = registry.fetch_project(conn, created["project_key"])
        conn.close()

        semantic_after = tuple(after[key] for key in (
            "project_key", "kind", "value", "role", "classification", "is_primary", "provider", "observed_at",
        ))
        compacted = json.loads(after["metadata_json"])
        self.assertEqual(dry_run["candidate_identifier_count"], 2)
        self.assertEqual(unchanged, metadata)
        self.assertEqual(executed["updated_identifier_count"], 2)
        self.assertEqual(semantic_after, semantic_before)
        self.assertEqual(compacted["retained_note"], "keep")
        self.assertEqual(compacted["type"], "service_url")
        self.assertEqual(compacted["value"], "https://example.com")
        self.assertNotIn("migration_source", compacted)
        self.assertNotIn("legacy_record", compacted)
        repo_metadata = json.loads(repo_after["metadata_json"])
        self.assertEqual(repo_after["classification"], "candidate")
        self.assertEqual(repo_metadata["confidence"], "candidate")
        self.assertEqual(fetched["source_code_urls"], [])
        self.assertIn("https://github.com/example/app", fetched["candidate_source_code_urls"])

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
                tracking_state="watchlist",
                note="application service test",
            )
            after = registry.fetch_project(conn, created["project_key"])
            assert after is not None
        finally:
            conn.close()

        self.assertEqual(len(selected), 1)
        self.assertEqual(updated["tracking_state"], "watchlist")
        self.assertEqual(updated["tracking_note"], "application service test")
        self.assertEqual(after["last_seen_at"], before["last_seen_at"])
        self.assertEqual(after["last_enriched_at"], before["last_enriched_at"])

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

    def test_merge_alias_reference_snapshot_can_be_restored_for_interactive_undo(self) -> None:
        conn = registry.open_db()
        try:
            source = projects.add_seed(conn, seed="https://source.example", seed_type="service_url")
            target = projects.add_seed(conn, seed="https://target.example", seed_type="service_url")
            other = projects.add_seed(conn, seed="https://other.example", seed_type="service_url")
            decisions.set_separate(
                conn,
                project_key=source["project_key"],
                other_project_key=other["project_key"],
            )
            reference_rows = registry.project_merge_reference_rows(conn, source["project_key"])
            with conn:
                registry.register_project_merge_alias(
                    conn,
                    source_project_key=source["project_key"],
                    target_project_key=target["project_key"],
                )
            with conn:
                registry.restore_project_merge_aliases(
                    conn,
                    source_project_key=source["project_key"],
                    source_aliases=[],
                )
                registry.restore_project_merge_references(
                    conn,
                    source_project_key=source["project_key"],
                    reference_rows=reference_rows,
                )
            alias_count = conn.execute("SELECT COUNT(*) FROM project_key_aliases").fetchone()[0]
            restored_targets = [
                row["target_value"]
                for row in conn.execute(
                    "SELECT target_value FROM project_decisions WHERE project_key = ? AND kind = 'separate_project'",
                    (other["project_key"],),
                )
            ]
        finally:
            conn.close()

        self.assertEqual(alias_count, 0)
        self.assertIn(source["project_key"], restored_targets)
        self.assertNotIn(target["project_key"], restored_targets)

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
