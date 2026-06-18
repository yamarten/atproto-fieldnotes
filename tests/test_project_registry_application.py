from __future__ import annotations

import datetime as dt

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import storage
from project_registry_core.application import decisions, maintenance, merge, presentation, projects, review


class ProjectRegistryApplicationTest(TempRegistryDbTestCase):
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
