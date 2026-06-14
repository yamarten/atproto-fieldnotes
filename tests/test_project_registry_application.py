from __future__ import annotations

from helpers import TempRegistryDbTestCase, registry
from project_registry_core.application import decisions, merge, projects, review


class ProjectRegistryApplicationTest(TempRegistryDbTestCase):
    def test_project_services_do_not_require_cli_namespace(self) -> None:
        conn = registry.open_db()
        try:
            created = projects.add_seed(conn, seed="https://example.com", seed_type="service_url")
            selected = projects.select_projects(conn, project_key=created["project_key"])
            updated = projects.set_tracking_state(
                conn,
                project_key=created["project_key"],
                tracking_state="watchlist",
                note="application service test",
            )
        finally:
            conn.close()

        self.assertEqual(len(selected), 1)
        self.assertEqual(updated["tracking_state"], "watchlist")
        self.assertEqual(updated["tracking_note"], "application service test")

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
