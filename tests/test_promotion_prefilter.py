from __future__ import annotations

from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_record, project_review
from project_registry_core.application import decisions, presentation, review


class PromotionPrefilterTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)

    def put(self, n, *, category='project', status='live', service=False,
            owned=False, technical=False, introduced=False, tracking='active', guard=False):
        p = self.make_project(f'proj:{n:012x}', 'unknown', '')
        p.update(name=f'Project {n}', category=category, service_status=status, tracking_state=tracking)
        url = f'https://promotion{n}.example'
        if service:
            registry.confirm_service_seed(p, url, source='fixture')
        else:
            project_record.set_service_url_candidate_values(p, [url])
        if owned:
            p['resolved_nsids'] = ['app.example.record']
        else:
            p['input_nsids'] = ['app.example.record']
        if guard:
            p['blocked_identifiers'] = [{'type': 'nsid', 'value': 'app.example.record', 'note': 'unrelated'}]
        if technical:
            p.update(technical_interest_level='high', technical_interest_reasons=['novel_design_pattern'],
                     technical_interest_summary='Distinct protocol design')
        if introduced:
            p['introduced_url'] = 'https://example.com/introduced'
        project_record.save_project(self.conn, p)
        return p['project_key']

    def full_reader(self, conn, *, tracking_states=None, **kwargs):
        return project_record.list_projects(conn, tracking_states=tracking_states)

    def test_sources_and_saved_decisions_match_full_response_for_every_view(self):
        for n, options in enumerate((
            {}, {'service': True}, {'owned': True, 'category': 'lexicon'},
            {'owned': True, 'category': 'library'}, {'owned': True, 'guard': True},
            {'service': True, 'owned': True, 'category': 'personal_tool'},
            {'service': True, 'owned': True, 'category': 'reject'},
            {'service': True, 'status': 'unknown'}, {'service': True, 'status': 'none'},
            {'service': True, 'status': 'dead'}, {'technical': True},
            {'technical': True, 'category': 'personal_tool'}, {'service': True, 'introduced': True},
            {'service': True, 'tracking': 'ignore'}, {'owned': True, 'tracking': 'archived'},
        )):
            self.put(n, **options)
        # Saved decisions with no current recommendation signal must survive all.
        for n, lowered in ((20, True), (21, False)):
            key = self.put(n, introduced=True, tracking='out_of_scope')
            if lowered:
                decisions.set_promotion_priority(self.conn, project_key=key, lowered=True, reason='テーマ')
            else:
                decisions.set_promotion_defer(self.conn, project_key=key, reason='待機', days=30)
        for view in ('inbox', 'technical', 'all', 'lowered', 'waiting'):
            for personal in (False, True):
                for kinds in (None, {'lexicon_definition_confirmed'}, {'technical_insight_candidate'}):
                    for reason, offset in (('', 0), ('テーマ', 0), ('', 2)):
                        args = dict(queue='promotion', tracking_states={'active', 'stable'},
                                    promotion_view=view, include_personal_tools=personal, kinds=kinds,
                                    reason=reason, offset=offset, limit=2)
                        with self.subTest(view=view, personal=personal, kinds=kinds, reason=reason, offset=offset):
                            with mock.patch.object(project_record, 'list_promotion_candidate_projects', side_effect=self.full_reader):
                                expected = presentation.queue_payload(self.conn, **args)
                            self.assertEqual(presentation.queue_payload(self.conn, **args), expected)
        for states in (None, set(), {'active'}, {'archived'}):
            with mock.patch.object(project_record, 'list_promotion_candidate_projects', side_effect=self.full_reader):
                expected = review.promotion_queue(self.conn, tracking_states=states)
            self.assertEqual(review.promotion_queue(self.conn, tracking_states=states), expected)
        technical_keys = {p['project_key'] for p in review.promotion_queue(self.conn, view='technical')}
        self.assertEqual(technical_keys, {f'proj:{n:012x}' for n in (10, 11)})
        all_keys = {p['project_key'] for p in review.promotion_queue(self.conn, view='all')}
        self.assertTrue({f'proj:{n:012x}' for n in (20, 21)} <= all_keys)

    def test_noise_and_introduced_projects_do_not_increase_hydration(self):
        self.put(1, service=True)
        self.put(2, technical=True)
        counts = []
        previous = 3
        for end in (28, 103):
            for n in range(previous, end):
                key = self.put(n, service=n % 2 == 0, introduced=n % 2 == 0)
                self.conn.execute("UPDATE projects SET state_json=json_set(state_json,'$.evidence.unrelated',?) WHERE project_key=?",
                                  ('x' * 32768, key))
            previous = end
            for view in ('inbox', 'technical'):
                with mock.patch.object(project_record, '_project_from_snapshot_row',
                                       wraps=project_record._project_from_snapshot_row) as hydrate:
                    result = review.promotion_queue(self.conn, view=view)
                self.assertEqual(len(result), 1)
                counts.append(hydrate.call_count)
        self.assertEqual(counts, [1, 1, 1, 1])

    def test_snapshot_keeps_selection_and_children_consistent_and_next_read_refreshes(self):
        key = self.put(1, service=True)
        self.conn.commit()
        expected = review.promotion_queue(self.conn, view='inbox')
        writer = registry.open_db()
        self.addCleanup(writer.close)
        original = project_record._project_from_snapshot_row
        def update_after_selection(conn, row):
            if conn is self.conn:
                changed = project_record.fetch_project(writer, key)
                changed['name'] = 'Updated name'
                project_review.set_promotion_priority_on_project(changed, lowered=True, reason='Later')
                project_record.save_project(writer, changed)
            return original(conn, row)
        with mock.patch.object(project_record, '_project_from_snapshot_row', side_effect=update_after_selection):
            self.assertEqual(review.promotion_queue(self.conn, view='inbox'), expected)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(review.promotion_queue(self.conn, view='inbox'), [])
        self.assertEqual(review.promotion_queue(self.conn, view='all')[0]['name'], 'Updated name')
        decisions.set_promotion_priority(self.conn, project_key=key, lowered=False)
        self.assertEqual(len(review.promotion_queue(self.conn, view='inbox')), 1)
        self.conn.execute('BEGIN')
        self.conn.execute("UPDATE projects SET introduced_url='https://example.com/post' WHERE project_key=?", (key,))
        self.assertEqual(review.promotion_queue(self.conn, view='inbox'), [])
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(len(review.promotion_queue(self.conn, view='inbox')), 1)
