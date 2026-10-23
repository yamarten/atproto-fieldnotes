from __future__ import annotations

from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record
from project_registry_core.application import decisions, presentation, review


class ReviewPrefilterTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)

    def put(self, number, *, proposal=False, publication=False, surface=False,
            adopted=True, tracking='active'):
        key, url = f'proj:{number:012x}', f'https://review{number}.example'
        p = self.make_project(key, 'service_url', url)
        p.update(name=f'Project {number}', tracking_state=tracking)
        if adopted:
            registry.confirm_service_seed(p, url, source='fixture')
        if proposal:
            p['editorial'] = {'proposal': {'kind': 'changed_facts', 'reason': 'New facts'}}
        if publication:
            p['publication_review'] = ['publisher_changed']
        p['service_verification_results'] = [dict(
            requested_url=url, lane='browser', content_changed=True,
            **({'surface_change': 'surface_not_confirmed'} if surface else {}),
        )]
        project_record.save_project(self.conn, p)
        return key

    def full_queue(self, **kwargs):
        with mock.patch.object(project_record, 'list_review_candidate_projects',
                               side_effect=project_record.list_projects):
            return review.review_queue(self.conn, **kwargs)

    def test_results_filters_order_and_limit_match_full_scan(self):
        self.put(1, surface=True, adopted=False)  # Broad candidate, no signal.
        closed = self.put(2, surface=True)
        item = review.build_review_queue_item(self.conn, closed)
        decisions.acknowledge_review(self.conn, project_key=closed,
                                    fingerprint=item['signals'][0]['fingerprint'])
        self.put(3, publication=True)
        self.put(4, proposal=True, surface=True, publication=True)
        self.put(5, proposal=True, tracking='stalled')
        self.put(6, publication=True, tracking='ignore')
        self.put(7)  # Content hash change alone is not a notification.
        for states in (None, set(), {'active'}, {'active', 'stalled'}, {'ignore'}):
            for kinds in (None, set(), {'editorial_refresh'}, {'service_observation_changed'},
                          {'lexicon_publication_changed'}, {'retired_reason'}):
                for limit in (1, 50):
                    with self.subTest(states=states, kinds=kinds, limit=limit):
                        args = dict(tracking_states=states, kinds=kinds, limit=limit)
                        expected = self.full_queue(**args)
                        self.assertEqual(review.review_queue(self.conn, **args), expected)
                        self.assertEqual(presentation.queue_payload(self.conn, queue='review', **args)['items'], expected)
        self.assertEqual([i['project_key'] for i in review.review_queue(self.conn)],
                         [f'proj:{n:012x}' for n in (4, 5, 3, 6)])
        # An explicitly supplied empty CLI batch must not reload the database.
        with mock.patch.object(project_record, 'list_review_candidate_projects', side_effect=AssertionError):
            self.assertEqual(review.build_review_queue_batch(self.conn, project_list=[]), ([], 0))

    def test_unrelated_large_projects_do_not_increase_hydration(self):
        self.put(1, proposal=True)
        counts = []
        previous = 2
        for end in (27, 102):
            for n in range(previous, end):
                key = self.put(n)
                self.conn.execute("UPDATE projects SET state_json=json_set(state_json,'$.evidence.unrelated',?) WHERE project_key=?",
                                  ('x' * 32768, key))
            previous = end
            expected = self.full_queue()
            with mock.patch.object(project_record, '_project_from_snapshot_row',
                                   wraps=project_record._project_from_snapshot_row) as hydrate:
                self.assertEqual(review.review_queue(self.conn), expected)
            counts.append(hydrate.call_count)
        self.assertEqual(counts, [1, 1])

    def test_snapshot_and_next_read_reflect_source_and_child_changes(self):
        key = self.put(1, surface=True)
        other = self.put(2)
        self.conn.commit()
        expected = self.full_queue()
        writer = registry.open_db()
        self.addCleanup(writer.close)
        original = project_record._project_from_snapshot_row
        def update_before_children(conn, row):
            p = project_record.fetch_project(writer, other)
            p['editorial'] = {'proposal': {'kind': 'changed_facts'}}
            project_record.save_project(writer, p)
            decisions.acknowledge_review(writer, project_key=key,
                                        fingerprint=expected[0]['signals'][0]['fingerprint'])
            return original(conn, row)
        # The writer must use the original reader, not recurse into this hook.
        def hydrate(conn, row):
            if conn is self.conn:
                return update_before_children(conn, row)
            return original(conn, row)
        with mock.patch.object(project_record, '_project_from_snapshot_row', side_effect=hydrate):
            self.assertEqual(review.review_queue(self.conn), expected)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual([i['project_key'] for i in review.review_queue(self.conn)], [other])
        decisions.acknowledge_review(self.conn, project_key=key,
                                    fingerprint=expected[0]['signals'][0]['fingerprint'], clear=True)
        self.assertEqual(review.review_queue(self.conn), self.full_queue())
        self.conn.execute('BEGIN')
        self.conn.execute("UPDATE projects SET state_json=json_remove(state_json,'$.evidence.editorial') WHERE project_key=?", (other,))
        self.assertEqual([i['project_key'] for i in review.review_queue(self.conn)], [key])
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(review.review_queue(self.conn), self.full_queue())
