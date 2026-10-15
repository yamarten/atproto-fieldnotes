from __future__ import annotations

from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record
from project_registry_core.application import merge


class MergeTimestampTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def seed(self, key, first_seen, last_seen="2026-09-10T00:00:00+00:00", last_enriched=""):
        project = self.make_project(key)
        project.update(first_seen_at=first_seen, last_seen_at=last_seen, last_enriched_at=last_enriched)
        project_record.save_project(self.conn, project, preserve_timestamps=True)
        # Empty timestamps represent existing rows with missing observation data.
        self.conn.execute("UPDATE projects SET first_seen_at=? WHERE project_key=?", (first_seen, key))
        self.conn.commit()

    def merge(self, source, target, *, execute=True):
        return merge.merge_projects(
            self.conn, project_key=source, target_project_key=target, execute=execute, force=True,
        )

    def reload(self, key):
        # A fresh connection verifies the committed scalar, not the in-memory merge.
        self.conn.close()
        self.conn = registry.open_db()
        return project_record.fetch_project(self.conn, key)

    def assert_first_seen(self, key, expected):
        project = self.reload(key)
        self.assertEqual(project["first_seen_at"], expected)
        self.assertEqual(self.conn.execute(
            "SELECT first_seen_at FROM projects WHERE project_key=?", (key,),
        ).fetchone()[0], expected)
        return project

    def test_merge_persists_oldest_first_seen_in_either_direction(self):
        older = "2026-08-01T00:00:00+00:00"
        newer = "2026-09-01T00:00:00+00:00"
        for index, (source_first, target_first) in enumerate(((older, newer), (newer, older))):
            with self.subTest(source_first=source_first):
                source, target = f"proj:source{index}", f"proj:target{index}"
                self.seed(source, source_first, "2026-09-15T00:00:00+00:00", "2026-09-14T00:00:00+00:00")
                self.seed(target, target_first, "2026-09-10T00:00:00+00:00", "2026-09-09T00:00:00+00:00")
                source_record = project_record.fetch_project(self.conn, source)
                target_record = project_record.fetch_project(self.conn, target)
                preview = merge.merge_project_records(target_record, source_record)
                self.assertEqual(preview["first_seen_at"], older)

                self.merge(source, target)

                merged = self.assert_first_seen(target, older)
                self.assertEqual(merged["last_seen_at"], "2026-09-15T00:00:00+00:00")
                self.assertEqual(merged["last_enriched_at"], "2026-09-14T00:00:00+00:00")
                self.assertIsNone(project_record.fetch_project(self.conn, source))
                self.assertEqual(project_record.resolve_project_key_input(self.conn, source), target)
                project_record.save_project(self.conn, merged, preserve_timestamps=True)
                self.assert_first_seen(target, older)

    def test_repeated_merges_keep_original_oldest_timestamp(self):
        oldest = "2026-07-01T00:00:00+00:00"
        for key, timestamp in (
            ("proj:a", oldest),
            ("proj:b", "2026-09-01T00:00:00+00:00"),
            ("proj:c", "2026-08-01T00:00:00+00:00"),
        ):
            self.seed(key, timestamp)
        self.merge("proj:a", "proj:b")
        self.assert_first_seen("proj:b", oldest)
        self.merge("proj:b", "proj:c")
        self.assert_first_seen("proj:c", oldest)
        self.assertEqual(project_record.resolve_project_key_input(self.conn, "proj:a"), "proj:c")

    def test_missing_first_seen_uses_available_value_or_save_time(self):
        known = "2026-08-01T00:00:00+00:00"
        now = "2026-10-15T00:00:00+00:00"
        for index, (source_first, target_first, expected) in enumerate((
            ("", known, known), (known, "", known), ("", "", now),
        )):
            with self.subTest(source_first=source_first, target_first=target_first):
                source, target = f"proj:missing-source{index}", f"proj:missing-target{index}"
                self.seed(source, source_first)
                self.seed(target, target_first)
                with mock.patch.object(project_record, "iso_now", return_value=now):
                    self.merge(source, target)
                self.assert_first_seen(target, expected)

    def test_merge_preview_does_not_change_saved_timestamps(self):
        older, newer = "2026-08-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"
        self.seed("proj:source", older)
        self.seed("proj:target", newer)
        self.merge("proj:source", "proj:target", execute=False)
        self.assert_first_seen("proj:source", older)
        self.assert_first_seen("proj:target", newer)

    def test_normal_save_keeps_first_seen_despite_incoming_changes(self):
        original = "2026-08-01T00:00:00+00:00"
        now = "2026-10-15T00:00:00+00:00"
        self.seed("proj:normal", original)
        for incoming in ("2026-07-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00", ""):
            with self.subTest(incoming=incoming):
                project = project_record.fetch_project(self.conn, "proj:normal")
                project["first_seen_at"] = incoming
                with mock.patch.object(project_record, "iso_now", return_value=now):
                    project_record.save_project(self.conn, project, enriched=True)
                saved = self.assert_first_seen("proj:normal", original)
                self.assertEqual(saved["last_seen_at"], now)
                self.assertEqual(saved["last_enriched_at"], now)

    def test_edit_with_preserved_timestamps_keeps_original_observation_times(self):
        first = "2026-08-01T00:00:00+00:00"
        last = "2026-09-01T00:00:00+00:00"
        enriched = "2026-08-31T00:00:00+00:00"
        self.seed("proj:edit", first, last, enriched)
        project = project_record.fetch_project(self.conn, "proj:edit")
        project["name"] = "Edited project"
        project_record.save_project(self.conn, project, preserve_timestamps=True)
        saved = self.assert_first_seen("proj:edit", first)
        self.assertEqual(saved["name"], "Edited project")
        self.assertEqual(saved["last_seen_at"], last)
        self.assertEqual(saved["last_enriched_at"], enriched)
