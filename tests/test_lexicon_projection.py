from __future__ import annotations

import json
from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record
from project_registry_core.persistence import database, runtime_view

A = 'org.example.notes.Entry'
B = 'org.example.notes.query'
C = 'org.example.notes.candidate'


class LexiconProjectionTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)
        self.key = 'proj:112233445566'
        project_record.save_project(self.conn, self.make_project(self.key, 'service_url', 'https://notes.example'))
        database.replace_identifier_rows(self.conn, self.key, [
            runtime_view.ProjectIdentifier('nsid', value, 'owned_nsid', classification,
                                           metadata={'source': 'fixture', 'custom': value}, observed_at='2026-09-01T00:00:00+00:00')
            for value, classification in ((A, 'confirmed'), (B, 'confirmed'), (C, 'candidate'))
        ])
        # Reproduce a stored count created by finalize/enrich before a later ordinary save.
        self.conn.execute("UPDATE projects SET state_json=json_set(state_json, '$.evidence.lexicon_count', 2) WHERE project_key=?", (self.key,))
        self.conn.commit()

    def stored_count(self):
        return json.loads(self.conn.execute('SELECT state_json FROM projects WHERE project_key=?', (self.key,)).fetchone()[0])['evidence']['lexicon_count']

    def test_read_restores_confirmed_lexicons_without_changing_rows_or_stored_count(self):
        before = database.normalized_rows_snapshot(self.conn)
        changes = self.conn.total_changes
        project = project_record.fetch_project(self.conn, self.key)
        self.assertEqual(project['lexicons'], [A, B])
        self.assertEqual(project['resolved_nsids'], [A, B])
        self.assertEqual(project['input_nsids'], [C])
        self.assertEqual(project['lexicon_count'], 2)
        self.assertEqual(project_record.list_projects(self.conn)[0]['lexicons'], [A, B])
        self.assertEqual(self.conn.total_changes, changes)
        self.assertEqual(database.normalized_rows_snapshot(self.conn), before)
        self.assertEqual(self.stored_count(), 2)

    def test_noop_and_profile_save_preserve_count_and_identifier_metadata(self):
        before = database.normalized_rows_snapshot(self.conn)
        for edit in (False, True):
            with self.subTest(edit=edit):
                project = project_record.fetch_project(self.conn, self.key)
                if edit:
                    project['notes'] = 'A profile correction'
                project_record.save_project(self.conn, project)
                self.assertEqual(self.stored_count(), 2)
                self.assertEqual(database.normalized_rows_snapshot(self.conn), before)

    def test_adoption_and_deferral_update_count_without_stale_compatibility_values(self):
        project = project_record.fetch_project(self.conn, self.key)
        registry.add_collection_owned_nsid(project, C, source='fixture')
        project_record.save_project(self.conn, project)
        self.assertEqual(self.stored_count(), 3)
        project = project_record.fetch_project(self.conn, self.key)
        registry.defer_identifier_on_project(project, 'nsid', A, note='Check attribution')
        project_record.save_project(self.conn, project)
        self.assertEqual(self.stored_count(), 2)
        project = project_record.fetch_project(self.conn, self.key)
        self.assertEqual(project['lexicons'], [B, C])
        self.assertNotIn(A, project['resolved_nsids'])

    def test_shape_uses_unique_confirmed_nsids_even_when_compatibility_values_are_stale(self):
        for resolved, expected in (([A, A, B], [A, B]), ([], [])):
            with self.subTest(resolved=resolved):
                project = self.make_project('proj:shape', 'service_url', 'https://shape.example')
                project.update(resolved_nsids=resolved, input_nsids=[C], lexicons=[C], lexicon_count=99)
                shaped = project_record.ensure_project_shape(project)
                self.assertEqual(shaped['lexicons'], expected)
                self.assertEqual(shaped['lexicon_count'], len(expected))
                self.assertEqual(shaped['input_nsids'], [C])
