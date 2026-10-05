from __future__ import annotations

import copy
import sqlite3
import threading
import time
from unittest import mock

from helpers import TempRegistryDbTestCase
from project_registry_core import database, registry, project_record, publication_registry, lexicon_types
from project_registry_core.application import publications
from project_registry_core import lexicon_publication_catalog as catalog
from test_lexicon_publication_catalog import event, summary


class PublicationRegistryTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)

    def run_sync(self, events, *, execute=True, verify_limit=0, verifier=None):
        state = catalog.catalog_state(self.conn)
        after = state['watermark_seq']
        tip = max([e['seq'] for e in events], default=after)
        return publications.run(self.conn, execute=execute, service='https://example.com', verify_limit=verify_limit,
                                verifier=verifier, snapshot_fetcher=lambda **kw: {'events': events, **summary(after, tip, tip, truncated=False)})

    @staticmethod
    def payload(item):
        sid, did = item['rkey'], item['did']
        from project_registry_core import identifiers
        return {'did': did, 'did_doc': {'id': did}, 'authority_domain': identifiers.derive_authority_domain(sid),
                'pds_url': 'https://pds.example',
                'record': {'uri': f'at://{did}/com.atproto.lexicon.schema/{sid}', 'value': item['record']}}

    def test_default_verification_drains_more_than_twelve_and_reuses_results(self):
        items = [event(i + 1, 'did:plc:a', f'com.example.item{i}') for i in range(31)]
        self.run_sync(items)
        by_id = {i['rkey']: i for i in items}
        fetcher = mock.Mock(side_effect=lambda sid, **kw: self.payload(by_id[sid]))
        result = publications.verify(self.conn, fetcher=fetcher)
        self.assertEqual((result['attempted'], result['verified'], result['remaining']), (31, 31, 0))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(publications.verify(self.conn, fetcher=fetcher)['attempted'], 0)
        self.assertEqual(fetcher.call_count, 31)

    def test_parallel_fetch_is_bounded_and_serial_within_authority(self):
        items = [event(i + 1, 'did:plc:a', f'com.authority{i % 6}.item{i}') for i in range(24)]
        self.run_sync(items)
        by_id = {i['rkey']: i for i in items}
        lock = threading.Lock()
        active, peaks, overlap = set(), [], []
        def fetch(sid, **kw):
            authority = sid.rsplit('.', 1)[0]
            with lock:
                overlap.append(authority in active)
                active.add(authority)
                peaks.append(len(active))
            time.sleep(0.01)
            with lock:
                active.remove(authority)
            return self.payload(by_id[sid])
        result = publications.verify(self.conn, fetcher=fetch)
        self.assertEqual(result['verified'], 24)
        self.assertFalse(any(overlap))
        self.assertGreater(max(peaks), 1)
        self.assertLessEqual(max(peaks), 4)

    def test_time_budget_and_cancellation_keep_completed_work_for_resume(self):
        items = [event(i + 1, 'did:plc:a', f'com.example.item{i}') for i in range(3)]
        self.run_sync(items)
        by_id = {i['rkey']: i for i in items}
        clock = [0.0]
        def fetch(sid, **kw):
            clock[0] = 5.0
            return self.payload(by_id[sid])
        with mock.patch.object(publications.time, 'monotonic', side_effect=lambda: clock[0]):
            result = publications.verify(self.conn, max_seconds=1, fetcher=fetch)
        self.assertEqual((result['verified'], result['remaining'], result['stop_reason']), (1, 2, 'time_budget'))
        result = publications.verify(self.conn, cancel_requested=lambda: True)
        self.assertEqual((result['attempted'], result['remaining'], result['stop_reason']), (0, 2, 'cancelled'))
        result = publications.verify(self.conn, fetcher=lambda sid, **kw: self.payload(by_id[sid]))
        self.assertEqual((result['verified'], result['remaining']), (2, 0))

    def test_investigation_save_shares_verification_atomically_and_preserves_time(self):
        from project_registry_core import storage, collector_adapters
        item = event(1, 'did:plc:a', 'com.example.post')
        self.run_sync([item])
        project = project_record.list_projects(self.conn)[0]
        observed_at = catalog.iso_now()
        evidence = collector_adapters.collect_lexicon_result(project['project_key'], item['rkey'], self.payload(item), observed_at)
        storage.stage_investigation_run(project, project_key=project['project_key'], started_at=observed_at,
            completed_at=observed_at, input_project=copy.deepcopy(project), output_project=project, bundle=evidence)
        with mock.patch.object(project_record, '_save_project', side_effect=RuntimeError('fail project save')):
            with self.assertRaisesRegex(RuntimeError, 'fail project save'):
                project_record.save_project(self.conn, project, enriched=True)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publication_verifications').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0], 0)
        project_record.save_project(self.conn, project, enriched=True)
        row = self.conn.execute('SELECT * FROM publication_verifications').fetchone()
        self.assertEqual(row['checked_at'], observed_at)
        self.assertEqual(row['current_record_refetched'], 1)
        self.assertEqual(publications.verification_targets(self.conn, limit=None, now=observed_at), [])

    def test_shared_result_does_not_overwrite_newer_verification_or_changed_catalog(self):
        from project_registry_core import publication_verification as shared
        item = event(1, 'did:plc:a', 'com.example.post')
        self.run_sync([item])
        expected = shared.witnesses(self.conn, item['rkey'])
        shared.record_result(self.conn, item['rkey'], expected=expected, payload=self.payload(item), checked_at='2026-10-05T03:00:00Z')
        self.assertEqual(shared.record_result(self.conn, item['rkey'], expected=expected, error='same instant', checked_at='2026-10-05T03:00:00+00:00'), [])
        self.assertEqual(shared.record_result(self.conn, item['rkey'], expected=expected, error='old failure', checked_at='2026-10-05T02:00:00Z'), [])
        self.assertEqual(self.conn.execute('SELECT reason FROM publication_verifications').fetchone()[0], 'verified')
        self.assertEqual(publications.verification_targets(self.conn, limit=None, now='2026-10-10T03:00:00Z'), [])
        self.assertEqual(publications.verification_targets(self.conn, limit=None, now='2026-10-11T03:00:00Z'), [item['rkey']])
        changed = event(2, 'did:plc:a', item['rkey'], 'update', main_type='query')
        self.run_sync([changed])
        self.assertEqual(shared.record_result(self.conn, item['rkey'], expected=expected, payload=self.payload(item), checked_at='2026-10-05T04:00:00Z'), [])
        self.assertEqual(publications.verification_targets(self.conn, limit=None, now='2026-10-05T05:00:00Z'), [item['rkey']])

    def test_shared_investigation_failure_obeys_retry_deadline(self):
        from project_registry_core import storage
        from project_registry_core.evidence import EvidenceBundle, CollectorFailure, CollectorResult
        item = event(1, 'did:plc:a', 'com.example.post')
        self.run_sync([item])
        project = project_record.list_projects(self.conn)[0]
        observed_at = '2026-10-05T03:00:00Z'
        bundle = EvidenceBundle(project_key=project['project_key'], collected_at=observed_at)
        bundle.add_failure(CollectorFailure(source='lexicon_resolution', operation='resolve', target=item['rkey'],
            category='timeout', message='timed out', retryable=True))
        bundle.add_collector_result(CollectorResult(source='lexicon_resolution', operation='resolve', status='failed', observed_at=observed_at))
        storage.stage_investigation_run(project, project_key=project['project_key'], started_at=observed_at,
            completed_at=observed_at, input_project=copy.deepcopy(project), output_project=project, bundle=bundle)
        project_record.save_project(self.conn, project, enriched=True)
        self.assertEqual(publications.verification_targets(self.conn, limit=None, now='2026-10-05T04:00:00Z'), [])
        self.assertEqual(publications.verification_targets(self.conn, limit=None, now='2026-10-06T04:00:00Z'), [item['rkey']])

    def test_source_configuration_defaults_to_all_due_and_preserves_explicit_limit(self):
        from project_registry_core.application import sync
        for configured, expected in [({}, None), ({'verify_limit': None}, None), ({'verify_limit': 0}, 0), ({'verify_limit': 3}, 3)]:
            with self.subTest(configured=configured), mock.patch.object(sync, '_source_config', return_value=configured), mock.patch.object(publications, 'run', return_value={}) as run:
                sync.lexicons(self.conn)
                self.assertEqual(run.call_args.kwargs['verify_limit'], expected)
                self.assertEqual(run.call_args.kwargs['verification_seconds'], 3600)

    def test_space_and_query_share_namespace_without_owning_declared_collections(self):
        space = event(1, 'did:plc:author', 'com.example.space', main_type='space', collections=['net.third.post'])
        query = event(2, 'did:plc:author', 'com.example.getPosts', main_type='query')
        result = self.run_sync([space, query])
        self.assertEqual(result['created'], 1)
        p = project_record.list_projects(self.conn)[0]
        self.assertEqual(set(p['input_nsids']), {'com.example.space', 'com.example.getPosts'})
        self.assertEqual(p['resolved_nsids'], [])
        self.assertEqual(len(p['publication_schemas']), 2)
        self.assertEqual(lexicon_types.record_sample_nsids(p), [])
        self.assertNotIn('net.third.post', p['input_nsids'])
        self.assertFalse(p.get('last_enriched_at'))
        self.assertEqual(self.run_sync([])['created'], 0)

    def test_dry_run_leaves_registry_and_catalog_unchanged(self):
        before = '\n'.join(self.conn.iterdump())
        result = self.run_sync([event(1, 'did:plc:a', 'com.example.space', main_type='space', collections=[])], execute=False)
        self.assertEqual(result['created'], 1)
        self.assertEqual('\n'.join(self.conn.iterdump()), before)

    def test_conflict_does_not_create_third_project(self):
        for key in ['proj:aaaaaaaaaaaa', 'proj:bbbbbbbbbbbb']:
            p = project_record.default_project_record(key, 'nsid', 'com.example.post')
            project_record.save_project(self.conn, p)
        result = self.run_sync([event(1, 'did:plc:a', 'com.example.space', main_type='space', collections=[])])
        self.assertEqual(result['created'], 0)
        self.assertEqual(len(result['conflicts']), 1)
        self.assertEqual(len(project_record.list_projects(self.conn)), 2)

    def test_inventory_is_exact_and_unknown_before_complete_fetch(self):
        self.run_sync([event(1, 'did:plc:a', 'com.example.space', main_type='space', collections=['net.third.post'])])
        def observation():
            return publication_registry.schemas(self.conn, now=catalog.iso_now())['com.example.space']['declarations'][0]['public_activity']
        self.assertEqual(observation(), 'unknown')
        with self.conn:
            publication_registry.save_inventory(self.conn, [{'collection': 'net.third.other'}], observed_at=catalog.iso_now())
        self.assertEqual(observation(), 'not_observed')
        with self.conn:
            publication_registry.save_inventory(self.conn, [{'collection': 'net.third.post'}], observed_at=catalog.iso_now())
        self.assertEqual(observation(), 'observed')

    def test_verify_resolves_authority_once_per_schema_not_each_mirror(self):
        sid = 'com.example.space'
        original = event(1, 'did:plc:author', sid, main_type='space', collections=['net.third.post'])
        mirror = event(2, 'did:web:mirror.example', sid, main_type='space', collections=['net.third.post'])
        def fetch(nsid, **kwargs):
            self.assertFalse(self.conn.in_transaction)
            return {'did': original['did'], 'did_doc': {'id': original['did']}, 'authority_domain': 'example.com',
                    'pds_url': 'https://pds.example', 'record': {'uri': f"at://{original['did']}/com.atproto.lexicon.schema/{sid}", 'value': original['record']}}
        fetcher = mock.Mock(side_effect=fetch)
        self.run_sync([original, mirror], verify_limit=1, verifier=fetcher)
        fetcher.assert_called_once()
        self.assertEqual(publication_registry.schemas(self.conn, now=catalog.iso_now())[sid]['status'], 'verified')
        changed = event(3, original['did'], sid, 'update', main_type='space', collections=['net.third.other'])
        self.run_sync([changed])
        self.assertEqual(publication_registry.schemas(self.conn, now=catalog.iso_now())[sid]['status'], 'pending')
        self.assertTrue(project_record.list_projects(self.conn)[0]['publication_review'])

    def test_partial_snapshot_never_creates_projects(self):
        result = publications.run(self.conn, execute=True, service='https://example.com', verify_limit=0,
            snapshot_fetcher=lambda **kw: {'events': [event(1, 'did:plc:a', 'com.example.post')], **summary(0, 10, 1, truncated=True)})
        self.assertEqual(result['operation_status'], 'partial')
        self.assertEqual(project_record.list_projects(self.conn), [])
        self.assertEqual(catalog.catalog_state(self.conn)['watermark_seq'], 0)

    def test_old_schema_is_rejected_without_changes(self):
        for table in ['publication_verifications', 'publication_catalog', 'pending_publication_events', 'catalog_metadata', 'collection_inventory_members']:
            self.conn.execute(f'DROP TABLE {table}')
        self.conn.execute("UPDATE registry_meta SET value='4' WHERE key='schema_version'")
        self.conn.commit()
        before = list(self.conn.iterdump())
        with self.assertRaises(database.SchemaMismatchError):
            database.open_database(self.db_path)
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_authorities_rotate_across_runs_and_retry_after_failure(self):
        self.run_sync([event(1, 'did:plc:a', 'com.alpha.one'), event(2, 'did:plc:a', 'com.alpha.two'),
                       event(3, 'did:plc:b', 'com.beta.one'), event(4, 'did:plc:c', 'com.charlie.one')])
        calls = []
        def fail(sid, **kw):
            calls.append(sid)
            raise TimeoutError('test')
        for _ in range(3):
            publications.verify(self.conn, limit=1, max_seconds=10, fetcher=fail)
        self.assertEqual({sid.split('.')[1] for sid in calls}, {'alpha', 'beta', 'charlie'})

    def test_failed_inventory_is_unknown_without_deleting_last_complete_snapshot(self):
        from project_registry_core import source_sync
        self.run_sync([event(1, 'did:plc:a', 'com.example.space', main_type='space', collections=['net.third.post'])])
        with self.conn:
            publication_registry.save_inventory(self.conn, [{'collection': 'net.third.post'}], observed_at=catalog.iso_now())
        with mock.patch.object(source_sync, 'fetch_collection_inventory', side_effect=TimeoutError('offline')):
            with self.assertRaises(TimeoutError):
                source_sync.sync_collection_inventory(self.conn, source_url='https://example.com', execute=True, create_unmatched=True, lookback_days=7)
        item = publication_registry.schemas(self.conn, now=catalog.iso_now())['com.example.space']
        self.assertEqual(item['declarations'][0]['public_activity'], 'unknown')
        self.assertEqual(item['inventory_status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM collection_inventory_members').fetchone()[0], 1)

    def test_exact_match_wins_over_shared_namespace_and_guards_are_preserved(self):
        from project_registry_core.application import decisions
        for key, sid in [('proj:aaaaaaaaaaaa', 'com.example.post'), ('proj:bbbbbbbbbbbb', 'com.example.space')]:
            project_record.save_project(self.conn, project_record.default_project_record(key, 'nsid', sid))
        result = self.run_sync([event(1, 'did:plc:a', 'com.example.space', main_type='space', collections=[])])
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(result['imported_project_keys'], ['proj:bbbbbbbbbbbb'])
        decisions.set_identifier_block(self.conn, project_key='proj:bbbbbbbbbbbb', identifier_type='nsid', value='com.example.other')
        result = self.run_sync([event(2, 'did:plc:a', 'com.example.other')])
        self.assertEqual(result['created'], 0)
        self.assertTrue(result['rejected_entries'])

    def test_invalid_and_deleted_publication_preserve_manual_adoption_and_invalidate_support(self):
        sid = 'com.example.post'
        self.run_sync([event(1, 'did:plc:a', sid)])
        p = project_record.list_projects(self.conn)[0]
        registry.add_confirmed_identifier_decision(p, 'nsid', sid, source='operator', role='owned_nsid')
        p['resolved_nsids'] = [sid]
        p['input_nsids'] = []
        project_record.save_project(self.conn, p)
        changed = event(2, 'did:plc:a', sid, 'update')
        changed['record']['lexicon'] = True
        self.run_sync([changed])
        p = project_record.list_projects(self.conn)[0]
        self.assertEqual(p['publication_schemas'][0]['status'], 'invalid')
        self.assertIn(sid, p['resolved_nsids'])
        self.assertTrue(p['publication_review'])
        self.run_sync([event(3, 'did:plc:a', sid, 'delete')])
        p = project_record.list_projects(self.conn)[0]
        self.assertEqual(p['publication_schemas'][0]['status'], 'deleted')
        self.assertIn(sid, p['resolved_nsids'])
        self.assertTrue(p['confirmed_identifiers'])

    def test_typed_enrich_and_repeated_type_change_do_not_create_collection_paths(self):
        from project_registry_core import collection_trust, trust_graph
        sid = 'com.example.space'
        original = event(1, 'did:plc:a', sid, main_type='space', collections=['net.third.post'])
        self.run_sync([original])
        p = project_record.list_projects(self.conn)[0]
        payload = {'did': 'did:plc:a', 'did_doc': {'id': 'did:plc:a'}, 'authority_domain': 'example.com',
                   'pds_url': 'https://pds.example', 'record': {'uri': f'at://did:plc:a/com.atproto.lexicon.schema/{sid}', 'value': original['record']}}
        registry.apply_lexicon_record_result(p, sid, payload)
        self.assertEqual(lexicon_types.record_sample_nsids(p), [])
        self.assertEqual(p['record_creators'], [])
        self.assertFalse(collection_trust.evaluate_publisher_support(p, sid)['auto_confirm'])
        self.assertNotIn('net.third.post', p['input_nsids'])
        self.assertTrue(any(e['object'] == {'kind': 'nsid', 'value': sid} for e in trust_graph.stored_graph(p)['edges']))
        payload['record']['value']['defs']['main'] = {'type': 'query'}
        for _ in range(2):
            registry.apply_lexicon_record_result(p, sid, payload)
        self.assertTrue(p['lexicon_resolution_entries'][-1]['publication_validation']['type_changed'])
        self.assertNotIn(sid, collection_trust.verified_publications(p))

    def test_projection_api_and_type_search_show_space_and_inventory_absence(self):
        from project_registry_core.application import presentation
        with self.conn:
            publication_registry.save_inventory(self.conn, [{'collection': 'net.third.other'}], observed_at=catalog.iso_now())
        self.run_sync([event(1, 'did:plc:a', 'com.example.space', main_type='space', collections=['net.third.post'])])
        p = project_record.list_projects(self.conn)[0]
        detail = presentation.project_detail(self.conn, project_key=p['project_key'])
        self.assertEqual(detail['project']['publication_schemas'][0]['schema_type'], 'space')
        from project_registry_core.application import query
        self.assertEqual(len(query.search_project_rows(self.conn, lexicon_type='space')), 1)
        self.assertEqual(len(query.search_project_rows(self.conn, lexicon_type='non_record')), 1)
        self.assertEqual(len(query.search_project_rows(self.conn, lexicon_type='unobserved_declaration')), 1)
        self.assertEqual(query.search_project_rows(self.conn, lexicon_type='record'), [])

    def test_publisher_change_invalidates_siblings_and_ttl_never_extends_on_read(self):
        self.run_sync([event(1, 'did:plc:old', 'com.example.one'), event(2, 'did:plc:new', 'com.example.two')])
        def verify(sid, did, **kw):
            row = self.conn.execute('SELECT record_hash FROM publication_catalog WHERE rkey=?', (sid,)).fetchone()
            return {'dns_same_did': True, 'current_record_refetched': True, 'authority_did': did, 'record_hash': row[0]}
        catalog.verify_due_publications(self.conn, limit=1, schema_ids=['com.example.one'], now='2026-10-01T00:00:00Z', resolver=verify)
        self.assertEqual(publication_registry.schemas(self.conn, now='2026-10-01T01:00:00Z')['com.example.one']['status'], 'verified')
        self.assertEqual(publication_registry.schemas(self.conn, now='2026-10-09T00:00:00Z')['com.example.one']['status'], 'pending')
        catalog.verify_due_publications(self.conn, limit=1, schema_ids=['com.example.two'], now='2026-10-02T00:00:00Z', resolver=verify)
        rows = publication_registry.schemas(self.conn, now='2026-10-02T01:00:00Z')
        self.assertEqual(rows['com.example.one']['status'], 'pending')
        self.assertEqual(rows['com.example.two']['status'], 'verified')
        self.assertEqual(rows['com.example.one']['authority_dids'], ['did:plc:new'])

    def test_corrupt_id_does_not_leave_previous_identity_trusted(self):
        self.run_sync([event(1, 'did:plc:a', 'com.example.one')])
        changed = event(2, 'did:plc:a', 'com.example.one', 'update')
        changed['record']['id'] = 'net.unrelated.other'
        self.run_sync([changed])
        rows = publication_registry.schemas(self.conn, now=catalog.iso_now())
        self.assertEqual(rows['com.example.one']['status'], 'invalid')
        self.assertNotIn('net.unrelated.other', rows)
        self.assertEqual(len(project_record.list_projects(self.conn)), 1)

    def test_recent_nonrecord_resolution_overrides_old_catalog_for_sampling(self):
        sid = 'com.example.one'
        p = project_record.default_project_record('proj:aaaaaaaaaaaa', 'nsid', sid)
        p['publication_schemas'] = [{'schema_id': sid, 'schema_type': 'record'}]
        p['lexicon_schema_samples'] = [{'nsid': sid, 'defs': {'main': {'type': 'query'}}}]
        self.assertEqual(lexicon_types.record_sample_nsids(p), [])
        p['lexicon_schema_samples'] = []
        p['lexicon_resolution_entries'] = [{'input_nsid': sid, 'publication_validation': {}, 'resolved_at': catalog.iso_now()}]
        self.assertEqual(lexicon_types.record_sample_nsids(p), [])
