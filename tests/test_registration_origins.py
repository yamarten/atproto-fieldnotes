from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record, project_merge, project_review, registration, source_sync
from project_registry_core.application import decisions, presentation, projects
from project_registry_core.persistence import runtime_view
from project_registry_core.publications import publication_registry, lexicon_publication_catalog as catalog
from test_lexicon_publication_catalog import event, summary


class RegistrationOriginsTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)

    def publication(self, sid='org.example.project.post'):
        state = catalog.catalog_state(self.conn)
        seq = state['watermark_seq'] + 1
        catalog.ingest_segment(self.conn, [event(seq, 'did:plc:author', sid)],
                               summary(state['watermark_seq'], seq, seq, truncated=False))
        return publication_registry.schemas(self.conn, now=project_record.iso_now())

    def collection(self):
        result = registry.import_collection_inventory_payload(self.conn, [{
            'collection': 'org.example.project.post', 'count': 2, 'recent_count': 1,
            'min': '2026-10-01T00:00:00Z', 'max': '2026-10-10T00:00:00Z',
        }], execute=True, create_unmatched=True, source='test')
        return result['imported_project_keys'][0]

    def test_pending_publication_has_identity_and_durable_reason_without_authenticating_publisher(self):
        result = publication_registry.import_projects(self.conn, self.publication(), execute=True)
        key = result['imported_project_keys'][0]
        detail = presentation.project_detail(self.conn, project_key=key)
        identity = detail['identity']
        self.assertTrue(identity['has_identity'])
        component = identity['components'][0]
        self.assertEqual((component['value'], component['registration_origin']), ('org.example.project.post', True))
        self.assertEqual(component['adoptions'][0]['adoption_kind'], 'bootstrap')
        self.assertEqual(detail['project']['publication_schemas'][0]['status'], 'pending')
        p = project_record.fetch_project(self.conn, key)
        self.assertEqual(p['manual_seeds'], [])
        self.assertEqual(project_merge.trusted_collection_nsids_for_merge(p), set())
        count = self.conn.execute('SELECT count(*) FROM project_identifier_adoptions').fetchone()[0]
        publication_registry.import_projects(self.conn, self.publication(), execute=True)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM project_identifier_adoptions').fetchone()[0], count)

    def test_existing_project_does_not_bootstrap_a_new_candidate(self):
        p = project_record.default_project_record('proj:aaaaaaaaaaaa', 'nsid', 'org.example.project.first')
        registry.add_manual_seed(p, 'nsid', 'org.example.project.first', source='operator_add_seed')
        project_record.save_project(self.conn, p)
        result = publication_registry.import_projects(self.conn, self.publication(), execute=True)
        self.assertEqual(result['created'], 0)
        p = project_record.fetch_project(self.conn, p['project_key'])
        self.assertEqual(p['input_nsids'], ['org.example.project.post'])
        self.assertEqual(len(p[runtime_view.RUNTIME_ADOPTION_ROWS]), 1)

    def test_collection_representative_gets_adoption_and_sync_is_idempotent(self):
        key = self.collection()
        before = list(self.conn.execute('SELECT adoption_id FROM project_identifier_adoptions'))
        p = project_record.fetch_project(self.conn, key)
        self.assertEqual(len(before), 1)
        self.assertTrue(registration.projection(p)['components'][0]['registration_origin'])
        self.assertIn({'kind': 'collection', 'value': 'org.example.project.post'}, registry.trusted_anchor_nodes(p))
        self.collection()
        self.assertEqual(list(self.conn.execute('SELECT adoption_id FROM project_identifier_adoptions')), before)

    def test_manual_entry_for_each_seed_kind_has_identity_and_operator_origin(self):
        for seed_type, value in [('nsid', 'org.example.project.post'),
                                 ('repo_url', 'https://github.com/example/project'),
                                 ('service_url', 'https://example.org')]:
            result = projects.add_seed(self.conn, seed=value, seed_type=seed_type)
            identity = presentation.project_detail(self.conn, project_key=result['project_key'])['identity']
            matching = next(item for item in identity['components'] if item['value'] == value)
            self.assertTrue(matching['registration_origin'])
            self.assertEqual(matching['adoptions'][0]['adoption_kind'], 'operator')

    def test_legacy_current_seed_is_not_displayed_as_historical_registration_origin(self):
        p = project_record.default_project_record('proj:aaaaaaaaaaaa', 'nsid', 'org.example.project.post')
        project_record.save_project(self.conn, p)
        identity = presentation.project_detail(self.conn, project_key=p['project_key'])['identity']
        self.assertTrue(identity['has_identity'])
        self.assertFalse(identity['components'][0]['registration_origin'])
        self.assertEqual(identity['components'][0]['adoptions'], [])

    def test_semble_new_targets_have_confirmed_identity_and_bootstrap_reason(self):
        values = [('nsid', 'org.example.semble.post'), ('repo_url', 'https://github.com/example/semble'),
                  ('service_url', 'https://semble.example.org')]
        scan = {'results':[{'seed_type':kind, 'seed_value':value} for kind, value in values],
                'scan_complete':True, 'pages':1, 'fetched_cards':3, 'skipped_cards':0}
        with mock.patch.object(source_sync, 'fetch_semble_collection_source_sync_results', return_value=scan):
            result = source_sync.sync_semble_collections(self.conn, execute=True, create_unmatched=True,
                pages=1, per_page=10, lookback_days=None, incremental=False,
                tracked_collections=[{'handle':'example.test', 'rkey':'collection'}])
        self.assertEqual(result['created'], 3)
        for p in registry.all_projects(self.conn):
            identity = registration.projection(p)
            self.assertTrue(identity['has_identity'])
            origins = [item for item in p[runtime_view.RUNTIME_ADOPTION_ROWS] if item['registration_origin']]
            self.assertEqual(len(origins), 1)
            self.assertEqual((origins[0]['source'], origins[0]['adoption_kind']), ('semble_collection_sync', 'bootstrap'))

    def test_rejecting_last_identity_requires_project_review_without_promoting_candidate(self):
        key = publication_registry.import_projects(self.conn, self.publication(), execute=True)['imported_project_keys'][0]
        publication_registry.import_projects(self.conn, self.publication('org.example.project.other'), execute=True)
        decisions.set_identifier_block(self.conn, project_key=key, identifier_type='nsid',
                                       value='org.example.project.post', note='false target')
        detail = presentation.project_detail(self.conn, project_key=key)
        self.assertFalse(detail['identity']['has_identity'])
        p = project_record.fetch_project(self.conn, key)
        self.assertEqual(p['resolved_nsids'], [])
        self.assertIn('org.example.project.other', p['input_nsids'])
        self.assertIn('missing_project_identity', {item['kind'] for item in project_review.compute_review_signals(p)})
        projects.set_tracking_state(self.conn, project_key=key, tracking_state='ignore')
        p = project_record.fetch_project(self.conn, key)
        self.assertNotIn('missing_project_identity', {item['kind'] for item in project_review.compute_review_signals(p)})

    def test_publication_merge_requires_current_verification_or_independent_adoption(self):
        key = publication_registry.import_projects(self.conn, self.publication(), execute=True)['imported_project_keys'][0]
        p = project_record.fetch_project(self.conn, key)
        item = p['publication_schemas'][0]
        item.update(status='verified', next_verification_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
        self.assertEqual(project_merge.trusted_collection_nsids_for_merge(p), {'org.example.project.post'})
        p[runtime_view.RUNTIME_ADOPTION_ROWS][0]['review_required'] = True
        self.assertEqual(project_merge.trusted_collection_nsids_for_merge(p), set())
        p[runtime_view.RUNTIME_ADOPTION_ROWS][0]['review_required'] = False
        item['attribution_conflict'] = True
        self.assertEqual(project_merge.trusted_collection_nsids_for_merge(p), set())
        item.pop('attribution_conflict')
        item['next_verification_at'] = '2020-01-01T00:00:00Z'
        self.assertEqual(project_merge.trusted_collection_nsids_for_merge(p), set())
        registry.add_confirmed_identifier_decision(p, 'nsid', 'org.example.project.post', source='web_ui', role='owned_nsid')
        self.assertEqual(project_merge.trusted_collection_nsids_for_merge(p), {'org.example.project.post'})
