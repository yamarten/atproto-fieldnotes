import datetime as dt
import json
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record, promotion_attention
from project_registry_core.application import presentation


class ProjectQueryDecisionTest(TempRegistryDbTestCase):
    def test_search_uses_legacy_review_hold_without_writing_migration(self):
        now = dt.datetime(2026, 10, 7, 12, tzinfo=dt.timezone.utc)
        conn = registry.open_db()
        self.addCleanup(conn.close)
        project = self.make_project("proj:legacy")
        project_record.save_project(conn, project)
        project = project_record.fetch_project(conn, project["project_key"])
        state = json.loads(conn.execute("SELECT state_json FROM projects WHERE project_key=?", (project["project_key"],)).fetchone()[0])
        state.setdefault("ops", {})["promotion_attention"] = {
            "reviewed_until": (now + dt.timedelta(days=1)).isoformat(),
            "reviewed_fingerprint": promotion_attention.fingerprint(project),
        }
        conn.execute("UPDATE projects SET state_json=? WHERE project_key=?", (json.dumps(state), project["project_key"]))
        conn.commit()
        before = list(conn.iterdump())
        with mock.patch("project_registry_core.application.query.dt.datetime", wraps=dt.datetime) as clock:
            clock.now.return_value = now
            result = presentation.search_projects(conn, promotion_hold="yes")
        self.assertEqual([item["project_key"] for item in result["items"]], [project["project_key"]])
        self.assertEqual(list(conn.iterdump()), before)
        self.assertFalse(conn.in_transaction)

    def test_search_hold_and_decision_filters_respect_expiry_and_changed_evidence(self):
        now = dt.datetime(2026, 10, 7, 12, tzinfo=dt.timezone.utc)
        conn = registry.open_db()
        self.addCleanup(conn.close)
        # Keep both old SQL CURRENT_TIMESTAMP and Python policy at the same instant.
        conn.create_function("current_timestamp", 0, lambda: now.strftime("%Y-%m-%d %H:%M:%S"))
        with mock.patch("project_registry_core.application.query.dt.datetime", wraps=dt.datetime) as clock:
            clock.now.return_value = now
            for label, hours, offset, changed, cleared in (
                ("future", 1, 0, False, False),
                ("expired", -1, 0, False, False),
                ("offset-expired", -1, 9, False, False),
                ("changed", 1, 0, True, False),
                ("cleared", 1, 0, False, True),
                ("no-hold", None, 0, False, False),
            ):
                project = self.make_project("proj:" + label)
                project_record.save_project(conn, project)
                project = project_record.fetch_project(conn, project["project_key"])
                if hours is not None:
                    registry.defer_promotion_on_project(project)
                    project["promotion_deferrals"][0]["expires_at"] = (
                        now + dt.timedelta(hours=hours)
                    ).astimezone(dt.timezone(dt.timedelta(hours=offset))).isoformat()
                    if cleared:
                        project["promotion_deferrals"][0]["cleared_at"] = now.isoformat()
                    if changed:
                        project["features"] = ["new capability"]
                project_record.save_project(conn, project)

            expected = {
                key: registry.active_promotion_deferral(project_record.fetch_project(conn, key)) is not None
                for key in ["proj:" + label for label in ("future", "expired", "offset-expired", "changed", "cleared", "no-hold")]
            }
            self.assertEqual({key for key, held in expected.items() if held}, {"proj:future"})
            with mock.patch.object(project_record, "fetch_project", wraps=project_record.fetch_project) as fetch:
                result = presentation.search_projects(conn)
            self.assertEqual({item["project_key"]: item["promotion_hold"] for item in result["items"]}, expected)
            # Only potentially active holds need the full policy input.
            self.assertEqual({call.args[1] for call in fetch.call_args_list}, {"proj:future", "proj:changed"})
            self.assertEqual(len(fetch.call_args_list), 2)
            held = presentation.search_projects(conn, promotion_hold="yes")
            self.assertEqual([item["project_key"] for item in held["items"]], ["proj:future"])
            for status, labels in (("active", {"future", "changed"}), ("expired", {"expired", "offset-expired"}), ("cleared", {"cleared"})):
                with self.subTest(status=status):
                    result = presentation.search_projects(conn, decision_kind="defer_promotion", decision_status=status)
                    self.assertEqual({item["project_key"] for item in result["items"]}, {"proj:" + label for label in labels})
