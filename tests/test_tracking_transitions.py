from __future__ import annotations

import datetime as dt

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record, project_review, source_sync
from project_registry_core.application import projects, review
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord


class TrackingTransitionsTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.now = dt.datetime.now(dt.timezone.utc)
        self.repo = 'https://github.com/example/tracking'

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def put(self, state='active', introduced=''):
        p = self.make_project('proj:tracking', 'repo_url', self.repo)
        p.update(tracking_state=state, introduced_url=introduced, tracking_note='keep operator note')
        project_record.save_project(self.conn, p)
        return p

    def fetch(self):
        return project_record.fetch_project(self.conn, 'proj:tracking')

    def edit_introduction(self, url):
        return projects.apply_profile_edit(self.conn, project_key='proj:tracking',
                                          name='Example', notes='', introduced_url=url)

    def test_introduction_preview_save_replacement_and_removal(self):
        self.put()
        args = dict(project_key='proj:tracking', name='Example', notes='', introduced_url='https://example.com/post')
        preview = projects.preview_profile_edit(self.conn, **args)
        self.assertEqual(preview['after']['tracking_state'], 'stable')
        self.assertEqual(self.fetch()['tracking_state'], 'active')
        projects.apply_profile_edit(self.conn, **args)
        self.assertEqual(self.fetch()['tracking_state'], 'stable')
        self.assertTrue(self.fetch()['tracking_from_introduction'])
        self.edit_introduction('https://example.com/other-post')
        self.assertEqual(self.fetch()['tracking_state'], 'stable')
        self.edit_introduction('')
        self.assertEqual(self.fetch()['tracking_state'], 'active')
        self.assertFalse(self.fetch()['tracking_from_introduction'])
        self.assertEqual(self.fetch()['tracking_note'], 'keep operator note')

    def test_manual_choice_survives_repeated_introduction_and_undo(self):
        self.put()
        self.edit_introduction('https://example.com/post')
        for state in ('active', 'stable', 'stalled', 'out_of_scope', 'archived', 'ignore'):
            with self.subTest(state=state):
                projects.set_tracking_state(self.conn, project_key='proj:tracking', tracking_state=state)
                self.edit_introduction('https://example.com/replacement')
                self.assertEqual(self.fetch()['tracking_state'], state)
                self.edit_introduction('')
                self.assertEqual(self.fetch()['tracking_state'], state)
                self.edit_introduction('https://example.com/post')

    def test_old_introduced_active_and_manually_known_are_not_reclassified_on_read_or_save(self):
        p = self.put(introduced='https://example.com/post')
        project_record.save_project(self.conn, self.fetch())
        self.edit_introduction('https://example.com/post')
        self.assertEqual(self.fetch()['tracking_state'], 'active')
        projects.set_tracking_state(self.conn, project_key=p['project_key'], tracking_state='stable')
        self.edit_introduction('')
        self.assertEqual(self.fetch()['tracking_state'], 'stable')

    def test_first_introduction_keeps_all_nonactive_states(self):
        self.put()
        for state in ('stable', 'stalled', 'out_of_scope', 'archived', 'ignore'):
            with self.subTest(state=state):
                self.edit_introduction('')
                projects.set_tracking_state(self.conn, project_key='proj:tracking', tracking_state=state)
                self.edit_introduction('https://example.com/post')
                self.assertEqual(self.fetch()['tracking_state'], state)

    def test_activity_does_not_make_an_unintroduced_project_known(self):
        p = self.put()
        p['source_repo_records'] = [{'url': self.repo, 'updated_at': (self.now-dt.timedelta(days=100)).isoformat()}]
        p['collection_observations'] = [{'max': self.now.isoformat(), 'observed_at': self.now.isoformat()}]
        self.assertIsNone(project_review.stalled_review_entry(p, now=self.now, inactivity_days=45, max_enrich_age_days=30))

    def test_stalled_review_returns_introduced_project_to_known_and_preserves_note(self):
        p = self.put('stalled', 'https://example.com/post')
        p['source_repo_records'] = [{'url': self.repo, 'updated_at': self.now.isoformat()}]
        project_record.save_project(self.conn, p)
        result = review.stalled(self.conn, now=self.now, execute=True)
        self.assertEqual(result['updated_count'], 1)
        self.assertEqual(self.fetch()['tracking_state'], 'stable')
        self.assertEqual(self.fetch()['tracking_note'], 'keep operator note')
        self.edit_introduction('')
        self.assertEqual(self.fetch()['tracking_state'], 'active')

    def test_downgrade_requires_fresh_activity_observation_and_no_failed_investigation(self):
        p = self.put()
        old = (self.now-dt.timedelta(days=100)).isoformat()
        p.update(last_enriched_at=self.now.isoformat(), service_status='dead', source_repo_records=[])
        def proposal():
            return project_review.stalled_review_entry(p, now=self.now, inactivity_days=45, max_enrich_age_days=30)
        self.assertIsNone(proposal())
        p['source_repo_records'] = [{'url': self.repo, 'updated_at': old, 'observed_at': old}]
        self.assertIsNone(proposal())
        p['source_repo_records'][0]['observed_at'] = self.now.isoformat()
        self.assertEqual(proposal()['suggested_tracking_state'], 'stalled')
        p['last_run_errors'] = ['repository fetch failed']
        self.assertIsNone(proposal())

    def test_successful_repository_snapshot_refreshes_activity_without_changing_admission(self):
        p = self.put()
        old = (self.now-dt.timedelta(days=100)).isoformat()
        registry.confirm_project_repository_url(p, self.repo, 'github_repo_seed', updated_at=old)
        admission = p['source_repo_records'][0]['recorded_at']
        bundle = EvidenceBundle(project_key=p['project_key'], collected_at=self.now.isoformat())
        bundle.add_record(EvidenceRecord(kind='github_repo_snapshot', source='fixture', classification='confirmed',
                                        observed_at=bundle.collected_at,
                                        value={'repo_url': self.repo, 'repo': {'updated_at': old}}))
        registry.apply_github_repo_evidence(p, bundle)
        project_record.save_project(self.conn, p)
        records = self.fetch()['source_repo_records']
        self.assertEqual(records[0]['recorded_at'], admission)
        self.assertEqual(records[0]['observed_at'], bundle.collected_at)
        self.assertEqual(records[0]['updated_at'], old)

    def test_collection_sync_does_not_reactivate_for_a_newly_found_old_record(self):
        p = self.put('stalled', 'https://example.com/post')
        p['input_nsids'] = ['app.example.tracking.post']
        p['collection_observations'] = [{'collection':'app.example.tracking', 'count':1,
                                        'max': (self.now-dt.timedelta(days=100)).isoformat()}]
        project_record.save_project(self.conn, p)
        item = {'collection':'app.example.tracking.post', 'count':2, 'recent_count':0,
                'min':(self.now-dt.timedelta(days=100)).isoformat(),
                'max':(self.now-dt.timedelta(days=90)).isoformat()}
        source_sync.refresh_known_collection_activity(self.conn, [item], execute=True, source='fixture', inactivity_days=45)
        self.assertEqual(self.fetch()['tracking_state'], 'stalled')
        item.update(count=3, max=self.now.isoformat())
        source_sync.refresh_known_collection_activity(self.conn, [item], execute=True, source='fixture', inactivity_days=45)
        self.assertEqual(self.fetch()['tracking_state'], 'stable')
