from __future__ import annotations

import io
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core.commands import queue_interaction
from project_registry_core import project_record, project_review, project_merge
from project_registry_core.application import decisions, presentation, review
from project_registry_core.publications import publication_registry


class ReviewChangesTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)
        self.key = 'proj:111111111111'
        self.url = 'https://review.example'
        self.p = self.make_project(self.key, 'service_url', self.url)
        registry.confirm_service_seed(self.p, self.url, source='fixture')
        self.p['service_verification_results'] = [{
            'requested_url': self.url, 'lane': 'browser', 'surface_change': 'surface_not_confirmed',
            'observed_at': '2026-10-01T00:00:00+00:00',
        }]
        project_record.save_project(self.conn, self.p)

    def load(self):
        return project_record.fetch_project(self.conn, self.key)

    def acknowledge(self):
        signal = project_review.compute_review_signals(self.load())[0]
        decisions.acknowledge_review(self.conn, project_key=self.key, fingerprint=signal['fingerprint'])
        return signal['fingerprint']

    def interact(self, answers):
        stdout, stderr = io.StringIO(), io.StringIO()
        stdout.isatty = lambda: True
        with (mock.patch.object(queue_interaction.sys, 'stdout', stdout),
              mock.patch.object(queue_interaction.sys, 'stderr', stderr),
              mock.patch.object(queue_interaction.sys.stdin, 'isatty', return_value=True),
              mock.patch('builtins.input', side_effect=answers)):
            queue_interaction.output_project_queue(self.conn, queue_name='review',
                queue=review.review_queue(self.conn), tracking_states={'active'}, kinds=None, interactive=True)
        return stdout.getvalue(), stderr.getvalue()

    def test_cli_acknowledges_change_without_dismissing_kind(self):
        self.interact(['x'])
        self.assertEqual(review.review_queue(self.conn), [])
        self.assertEqual(self.load()['dismissed_review_kinds'], [])
        self.assertEqual(self.load()['review_target_decisions'][0]['kind'], 'acknowledge_review')

    def test_cli_editorial_is_not_acknowledgeable(self):
        p = self.load()
        p['service_verification_results'] = []
        p['editorial'] = {'proposal': {'kind': 'changed_facts', 'reason': 'Updated feature'}}
        project_record.save_project(self.conn, p)
        _, menu = self.interact(['x', 'q'])
        self.assertNotIn('x=acknowledge', menu)
        self.assertEqual(self.load()['dismissed_review_kinds'], [])
        self.assertIsNotNone(review.build_review_queue_item(self.conn, self.key))

    def test_cli_uppercase_rejects_candidate_instead_of_confirming(self):
        candidate = 'https://candidate.example'
        p = self.load()
        project_record.set_service_url_candidate_values(p, [candidate])
        project_record.save_project(self.conn, p)
        self.interact(['C', 'q'])
        p = self.load()
        self.assertTrue(project_record.identifier_is_blocked(p, 'service_url', candidate))
        self.assertNotIn(candidate, p['service_urls'])

    def test_cli_deep_enrich_dispatch_is_distinct(self):
        with mock.patch.object(queue_interaction, 'enrich_project', return_value=self.load()) as enrich:
            self.interact(['E', 'q'])
        self.assertTrue(enrich.call_args.kwargs['deep_investigation'])

    def test_acknowledgement_roundtrip_clear_and_new_observation(self):
        fingerprint = self.acknowledge()
        self.assertEqual(review.review_queue(self.conn), [])
        saved = self.load()
        self.assertEqual(saved['service_urls'], [self.url])
        self.assertEqual(saved['service_verification_results'], self.p['service_verification_results'])
        project_record.save_project(self.conn, saved)
        self.assertEqual(review.review_queue(self.conn), [])
        decisions.acknowledge_review(self.conn, project_key=self.key, fingerprint=fingerprint, clear=True)
        self.assertIsNotNone(review.build_review_queue_item(self.conn, self.key))
        self.acknowledge()
        saved = self.load()
        saved['service_verification_results'][0]['observed_at'] = '2026-10-02T00:00:00+00:00'
        project_record.save_project(self.conn, saved)
        self.assertNotEqual(project_review.compute_review_signals(self.load())[0]['fingerprint'], fingerprint)
        with self.assertRaises(ValueError):
            decisions.acknowledge_review(self.conn, project_key=self.key, fingerprint=fingerprint)
        history = presentation.project_detail(self.conn, project_key=self.key)['decisions']['history']
        self.assertEqual(len([d for d in history if d['kind'] == 'acknowledge_review']), 2)

    def test_content_change_and_unadopted_service_do_not_create_task(self):
        p = self.load()
        p['service_verification_results'][0].pop('surface_change')
        p['service_verification_results'][0]['content_changed'] = True
        self.assertEqual(project_review.compute_review_signals(p), [])
        p['service_verification_results'][0]['surface_change'] = 'surface_not_confirmed'
        decisions.set_identifier_block(self.conn, project_key=self.key, identifier_type='service_url', value=self.url)
        p = self.load()
        self.assertEqual(project_review.compute_review_signals(p), [])

    def test_publication_recheck_uses_facts_not_verification_time(self):
        p = self.load()
        p['service_verification_results'] = []
        p['publication_review'] = ['発行根拠の変化: org.example.post']
        p['publication_schemas'] = [{'schema_id': 'org.example.post', 'schema_type': 'record',
                                     'status': 'verified', 'record_hash': 'first', 'publisher_did': 'did:plc:one'}]
        project_record.save_project(self.conn, p)
        fingerprint = self.acknowledge()
        p = self.load()
        p['publication_schemas'][0]['verified_at'] = '2026-10-02T00:00:00+00:00'
        project_record.save_project(self.conn, p)
        self.assertEqual(project_review.compute_review_signals(self.load()), [])
        p = self.load()
        p['publication_schemas'][0]['record_hash'] = 'second'
        project_record.save_project(self.conn, p)
        self.assertNotEqual(project_review.compute_review_signals(self.load())[0]['fingerprint'], fingerprint)

    def test_editorial_proposal_must_be_resolved_through_editor(self):
        p = self.load()
        p['service_verification_results'] = []
        p['editorial'] = {'proposal': {'kind': 'changed_facts', 'reason': 'Updated feature'}}
        project_record.save_project(self.conn, p)
        signal = project_review.compute_review_signals(self.load())[0]
        self.assertEqual(signal['kind'], 'editorial_refresh')
        self.assertNotIn('fingerprint', signal)
        with self.assertRaises(ValueError):
            decisions.acknowledge_review(self.conn, project_key=self.key, fingerprint='')

    def test_publication_import_repeated_facts_and_recurrence(self):
        p = self.load()
        p['service_verification_results'] = []
        p['input_nsids'] = ['org.example.post']
        original = {'schema_id': 'org.example.post', 'schema_type': 'record', 'status': 'verified',
                    'publisher_did': 'did:plc:one', 'record_hash': 'original'}
        p['publication_schemas'] = [original]
        project_record.save_project(self.conn, p)
        changed = {**original, 'record_hash': 'changed'}
        publication_registry.import_projects(self.conn, {'org.example.post': changed}, execute=True)
        first = self.acknowledge()
        publication_registry.import_projects(self.conn, {'org.example.post': {**changed, 'checked_at': 'later'}}, execute=True)
        self.assertEqual(project_review.compute_review_signals(self.load()), [])
        publication_registry.import_projects(self.conn, {'org.example.post': original}, execute=True)
        self.acknowledge()
        publication_registry.import_projects(self.conn, {'org.example.post': changed}, execute=True)
        self.assertNotEqual(project_review.compute_review_signals(self.load())[0]['fingerprint'], first)

    def test_merge_keeps_acknowledgements_but_new_combined_change_reappears(self):
        self.acknowledge()
        source = self.load()
        target = self.make_project('proj:222222222222', 'service_url', 'https://other.example')
        registry.confirm_service_seed(target, 'https://other.example', source='fixture')
        merged = project_merge.combine_records(target, source)
        self.assertEqual(project_review.compute_review_signals(merged), [])
        merged['service_verification_results'].append({
            'requested_url': 'https://other.example', 'lane': 'browser',
            'surface_change': 'surface_reached', 'observed_at': '2026-10-03T00:00:00+00:00'})
        self.assertEqual(len(project_review.compute_review_signals(merged)), 1)

    def test_identifier_latest_exact_guard_replaces_opposite_guard(self):
        decisions.set_identifier_block(self.conn, project_key=self.key, identifier_type='service_url', value=self.url)
        decisions.set_identifier_defer(self.conn, project_key=self.key, identifier_type='service_url', value=self.url)
        p = self.load()
        self.assertFalse(project_record.identifier_is_blocked(p, 'service_url', self.url))
        self.assertTrue(project_record.identifier_is_deferred(p, 'service_url', self.url))
        decisions.set_identifier_block(self.conn, project_key=self.key, identifier_type='service_url', value=self.url)
        p = self.load()
        self.assertTrue(project_record.identifier_is_blocked(p, 'service_url', self.url))
        self.assertFalse(project_record.identifier_is_deferred(p, 'service_url', self.url))
