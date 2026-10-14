from __future__ import annotations

import contextlib
import copy
from unittest import mock

from helpers import TempRegistryDbTestCase, registry

from project_registry_core import project_merge
from project_registry_core import project_record
from project_registry_core.persistence import database, runtime_view
from project_registry_core import trust_graph
from project_registry_core.publications import publication_registry
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

    def test_publication_verification_is_preserved_in_both_comparison_reads(self):
        with contextlib.closing(registry.open_db()) as conn:
            for number, verified in enumerate((True, False)):
                p = self.make_project(f'proj:publication{number}', 'nsid', 'app.example.post')
                p['confirmed_identifiers'] = [dict(type='nsid', value='app.example.post',
                    role='owned_nsid', source=publication_registry.SOURCE, added_at='2026-10-01')]
                p['publication_schemas'] = [dict(schema_id='app.example.post',
                    status='verified' if verified else 'pending', next_verification_at='2999-01-01T00:00:00+00:00')]
                project_record.save_project(conn, p)
            conn.commit()
            full = project_record.list_projects(conn)
            self.assertTrue(project_merge.trusted_collection_nsids_for_merge(full[0]))
            self.assertFalse(project_merge.trusted_collection_nsids_for_merge(full[1]))
            self.assert_equivalent(conn, full, project_record.list_duplicate_projects(conn))
            self.assert_equivalent(conn, full, project_record.list_batch_merge_projects(
                conn, tracking_states=project_record.TRACKING_STATES))

    def test_batch_projection_preserves_direction_modes_skips_and_unsavable_boundary(self):
        with contextlib.closing(registry.open_db()) as conn:
            self.seed(conn)
            p = project_record.fetch_project(conn, 'proj:000000000001')
            p.update(service_status='live', introduced_url='https://introduced.test/post',
                     resource_url_records=[dict(url='https://resource.test/docs', role='docs', confidence='confirmed')])
            project_record.save_project(conn, p)
            ignored = project_record.fetch_project(conn, 'proj:000000000005')
            ignored['tracking_state'] = 'ignore'
            project_record.save_project(conn, ignored)
            conn.commit()
            before = list(conn.iterdump())
            states = project_record.TRACKING_STATES - {'ignore'}
            full = [project_record.ensure_project_shape(p) for p in project_record.list_projects(conn, tracking_states=states)]
            slim = project_record.list_batch_merge_projects(conn, tracking_states=states)
            self.assertEqual([p['project_key'] for p in full], [p['project_key'] for p in slim])
            self.assertNotIn(ignored['project_key'], [p['project_key'] for p in slim])
            for a, b in zip(full, slim):
                self.assertEqual(project_merge.project_batch_merge_preference(a), project_merge.project_batch_merge_preference(b))
                self.assertEqual(project_record.project_alias_tokens(a), project_record.project_alias_tokens(b))
                with self.assertRaisesRegex(ValueError, 'complete runtime view'):
                    project_record.save_project(conn, copy.deepcopy(b))
            for mode in ('strict', 'moderate', 'broad'):
                with mock.patch.object(project_record, 'list_batch_merge_projects', return_value=full):
                    expected = project_merge.build_batch_merge_plan(conn, mode)
                # Result equivalence alone also passes if the batch path falls
                # back to expensive full records. Guard that read boundary too.
                with mock.patch.object(database, 'hydrate_project_children',
                                       side_effect=AssertionError('batch loaded complete records')):
                    self.assertEqual(expected, project_merge.build_batch_merge_plan(conn, mode))
            self.assertEqual(before, list(conn.iterdump()))
            self.assertEqual(project_record.list_batch_merge_projects(conn, tracking_states=set()), [])

    def test_batch_plan_keeps_confirmed_service_as_target_in_either_key_order(self):
        for service_number in (0, 1):
            with self.subTest(service_number=service_number), contextlib.closing(
                registry.initialize_db(self.db_path.with_name(f'direction-{service_number}.db'))
            ) as conn:
                repo = 'https://github.com/example/shared'
                for number in (0, 1):
                    project = self.make_project(f'proj:direction{number}', 'repo_url', repo)
                    project['name'] = 'Example'
                    registry.confirm_project_repository_url(project, repo, 'test')
                    if number == service_number:
                        registry.confirm_service_seed(project, 'https://service.example', source='test')
                    project_record.save_project(conn, project)
                # The independent expectation is the confirmed service, not a
                # second call to the same preference/direction implementation.
                result = presentation.merge_batch_plan(conn, mode='moderate')
                self.assertEqual(result['count'], 1)
                self.assertEqual([(p['source_project_key'], p['target_project_key']) for p in result['items']],
                                 [(f'proj:direction{1 - service_number}', f'proj:direction{service_number}')])

    def test_supplied_lookup_does_not_replace_unsaved_target_with_indexed_record(self):
        with contextlib.closing(registry.open_db()) as conn:
            self.seed(conn)
            projects = project_record.list_duplicate_projects(conn)
            profiles, index = project_merge.build_duplicate_candidate_index(projects)
            lookup = {p['project_key']: p for p in projects}
            target = copy.deepcopy(projects[0])
            target['name'] = 'Unsaved different name'
            target['website_url'] = 'https://unsaved.test'
            expected = project_merge.compute_duplicate_candidates(conn, target, projects=projects)
            self.assertEqual(expected, project_merge.compute_duplicate_candidates(conn, target,
                projects=projects, duplicate_profiles=profiles, duplicate_index=index, project_lookup=lookup))

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
