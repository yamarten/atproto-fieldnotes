from __future__ import annotations

import contextlib
import copy
from unittest import mock

from helpers import TempRegistryDbTestCase, registry

from project_registry_core import project_merge
from project_registry_core import project_record, runtime_view, trust_graph
from project_registry_core.application import presentation


class DuplicateProjectReadTest(TempRegistryDbTestCase):
    def seed(self, conn):
        claims = [
            ('repo_url', 'project_repository', 'https://github.com/example/shared'),
            ('service_url', 'service', 'https://shared.test/app'),
            ('site_url', 'project_site', 'https://site.test'),
            ('nsid', 'owned_nsid', 'app.shared.Record'),
            ('account', 'project_account', 'shared.test'),
            ('handle', 'project_handle', 'shared.test'),
            ('did', 'brand_did', 'did:plc:shared'),
            ('handle', 'developer_handle', 'developer.test'),
            ('nsid', 'referenced_nsid', 'app.reference.record'),
        ]
        for number in range(6):
            p = self.make_project(f'proj:{number:012x}')
            p.update(name=f'Example {number}', website_url='https://shared.test',
                     summary='Description excluded from merge input',
                     record_creators=['did:plc:creator'], status='candidate' if number == 0 else 'draft')
            p[runtime_view.RUNTIME_IDENTIFIER_ROWS] = [dict(
                kind=kind, role=role, value=value, classification='candidate' if number % 2 == 0 else 'confirmed',
                is_primary=False, provider='', metadata={}, observed_at='',
            ) for kind, role, value in claims]
            p[runtime_view.RUNTIME_IDENTIFIER_BASELINE] = []
            if number == 2:
                p['blocked_identifiers'] = [{'type': 'service_url', 'value': 'https://shared.test'}]
                p['deferred_identifiers'] = [{'type': 'repo_url', 'value': claims[0][2],
                                              'expires_at': '2999-01-01T00:00:00+00:00'}]
                p['separate_project_keys'] = ['proj:000000000001']
            if number == 3:
                p['deferred_identifiers'] = [{'type': 'nsid', 'value': 'app.shared.Record',
                                              'expires_at': '2000-01-01T00:00:00+00:00'}]
            if number in (4, 5):
                p['confirmed_identifiers'] = [{'type': 'nsid', 'value': 'app.shared.Record',
                    'role': 'owned_nsid', 'source': 'trust_policy:accepted_lexicon_publisher',
                    'added_at': '2026-09-01T00:00:00+00:00'}]
                p['trust_graph'] = {'evaluations': [{'candidate': {'kind': 'collection', 'value': 'app.shared.Record'},
                    'auto_confirm': True, 'policy_version': trust_graph.TRUST_POLICY_VERSION}]}
            project_record.save_project(conn, p)
        conn.commit()

    def assert_equivalent(self, conn, complete, partial):
        self.assertEqual([p['project_key'] for p in complete], [p['project_key'] for p in partial])
        self.assertEqual([project_merge.duplicate_profile(p) for p in complete],
                         [project_merge.duplicate_profile(p) for p in partial])
        for target in complete:
            expected = project_merge.compute_duplicate_candidates(conn, target, projects=complete)
            self.assertEqual(expected, project_merge.compute_duplicate_candidates(conn, target, projects=partial))
            self.assertEqual(expected, project_merge.compute_duplicate_candidates(conn, target))

    def test_projection_preserves_profiles_pairs_guards_and_stored_claims(self):
        with contextlib.closing(registry.open_db()) as conn:
            self.seed(conn)
            before = list(conn.iterdump())
            complete = project_record.list_projects(conn)
            partial = project_record.list_duplicate_projects(conn)
            self.assert_equivalent(conn, complete, partial)
            for full, slim in zip(complete, partial):
                self.assertEqual(runtime_view.project_identifiers(full), runtime_view.project_identifiers(slim))
                self.assertFalse(runtime_view.is_complete(slim))
                self.assertFalse(slim.get('summary'))
                with self.assertRaisesRegex(ValueError, 'complete runtime view'):
                    project_record.save_project(conn, copy.deepcopy(slim))
            self.assertEqual(before, list(conn.iterdump()))
            self.assertFalse(conn.in_transaction)

    def test_bulk_reads_use_one_snapshot_across_concurrent_commit(self):
        with contextlib.closing(registry.open_db()) as conn, contextlib.closing(registry.open_db()) as writer:
            self.seed(conn)
            old = project_record.list_duplicate_projects(conn)
            calls = 0
            def update_after_first_child():
                nonlocal calls
                calls += 1
                if calls == 2:
                    p = project_record.fetch_project(writer, 'proj:000000000001')
                    p.update(name='Changed', website_url='https://changed.test')
                    p['separate_project_keys'] = ['proj:000000000000']
                    project_record.save_project(writer, p)
            actual = project_record.list_duplicate_projects(conn, check_cancelled=update_after_first_child)
            self.assertEqual(old, actual)
            self.assertFalse(conn.in_transaction)
            new = project_record.list_duplicate_projects(conn)
            self.assertNotEqual(old, new)
            self.assert_equivalent(conn, project_record.list_projects(conn), new)
            conn.execute('BEGIN')
            conn.execute("UPDATE projects SET name='Uncommitted' WHERE project_key='proj:000000000001'")
            own = project_record.list_duplicate_projects(conn)
            self.assertTrue(conn.in_transaction)
            self.assertEqual(next(p['name'] for p in own if p['project_key'] == 'proj:000000000001'), 'Uncommitted')
            conn.rollback()
            self.assertEqual(new, project_record.list_duplicate_projects(conn))

    def test_service_detects_source_revision_change_during_projection(self):
        with contextlib.closing(registry.open_db()) as conn, contextlib.closing(registry.open_db()) as writer:
            self.seed(conn)
            original = project_record._duplicate_project_from_rows
            changed = False
            def project_row(*args, **kwargs):
                nonlocal changed
                if not changed:
                    changed = True
                    p = project_record.fetch_project(writer, 'proj:000000000000')
                    p['name'] = 'Changed during analysis'
                    project_record.save_project(writer, p)
                return original(*args, **kwargs)
            with mock.patch.object(project_record, '_duplicate_project_from_rows', side_effect=project_row):
                with self.assertRaises(presentation.StaleProjectError):
                    presentation.project_merge_analysis(conn, project_key='proj:000000000000')
            self.assertFalse(conn.in_transaction)
            self.assertEqual(project_record.fetch_project(conn, 'proj:000000000000')['name'], 'Changed during analysis')

    def test_cancellation_while_reading_children_releases_snapshot(self):
        with contextlib.closing(registry.open_db()) as conn:
            self.seed(conn)
            changes = conn.total_changes
            calls = 0
            def cancel():
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise RuntimeError('cancelled')
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                project_record.list_duplicate_projects(conn, check_cancelled=cancel)
            self.assertEqual(calls, 4)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.total_changes, changes)
            self.assertEqual(len(project_record.list_duplicate_projects(conn)), 6)
