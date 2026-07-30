from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from project_registry_core.application import maintenance


class WorkspaceTempCleanupTest(unittest.TestCase):
    def test_cleanup_is_dry_run_by_default_and_preserves_non_sqlite_files(self) -> None:
        now = dt.datetime(2026, 7, 30, 9, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temp_root = root / ".tmp"
            temp_root.mkdir()
            database = temp_root / "audit.db"
            report = temp_root / "report.json"
            database.write_bytes(b"db")
            report.write_text("{}", encoding="utf-8")
            old_timestamp = (now - dt.timedelta(hours=2)).timestamp()
            os.utime(database, (old_timestamp, old_timestamp))

            dry_run = maintenance.cleanup_workspace_temp(
                root,
                older_than_hours=1,
                now=now,
            )
            executed = maintenance.cleanup_workspace_temp(
                root,
                older_than_hours=1,
                execute=True,
                now=now,
            )

            self.assertEqual(dry_run["candidate_count"], 1)
            self.assertEqual(dry_run["deleted_count"], 0)
            self.assertEqual(executed["deleted_count"], 1)
            self.assertFalse(database.exists())
            self.assertTrue(report.exists())

    def test_cleanup_keeps_sqlite_family_when_one_sidecar_is_recent(self) -> None:
        now = dt.datetime(2026, 7, 30, 9, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temp_root = root / ".tmp"
            temp_root.mkdir()
            database = temp_root / "audit.db"
            wal = temp_root / "audit.db-wal"
            database.write_bytes(b"db")
            wal.write_bytes(b"wal")
            old_timestamp = (now - dt.timedelta(hours=2)).timestamp()
            recent_timestamp = (now - dt.timedelta(minutes=10)).timestamp()
            os.utime(database, (old_timestamp, old_timestamp))
            os.utime(wal, (recent_timestamp, recent_timestamp))

            result = maintenance.cleanup_workspace_temp(
                root,
                older_than_hours=1,
                execute=True,
                now=now,
            )

            self.assertEqual(result["candidate_count"], 0)
            self.assertEqual(result["retained_recent_count"], 2)
            self.assertTrue(database.exists())
            self.assertTrue(wal.exists())

    def test_cleanup_rejects_negative_policy_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "older_than_hours"):
                maintenance.cleanup_workspace_temp(root, older_than_hours=-1)
            with self.assertRaisesRegex(ValueError, "example_limit"):
                maintenance.cleanup_workspace_temp(root, example_limit=-1)


if __name__ == "__main__":
    unittest.main()
