from __future__ import annotations

import json
from unittest import mock
from helpers import TempRegistryDbTestCase, captured_stdout, registry
from project_registry_core import cli, dev_database, editorial, project_record, project_review
from project_registry_core.persistence import runtime_view
from project_registry_core.application import presentation, review


class ReviewRuntimeTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.addCleanup(self.conn.close)

    def test_cli_filters_editorial_service_and_publication_signals(self):
        kinds = ("editorial_refresh",
                 "service_observation_changed", "lexicon_publication_changed")
        expected = {}
        for index, kind in enumerate(kinds):
            key = f"proj:{index + 1:012x}"
            url = f"https://review{index}.example"
            project = self.make_project(key, "service_url", url)
            project.update(name=kind, summary="Stored description", tracking_state="active")
            if kind != "missing_project_identity":
                registry.confirm_service_seed(project, url, source="fixture")
            if kind == "editorial_refresh":
                material = editorial.source("service_page", url, "New feature", "2026-10-01T00:00:00+00:00")
                editorial.apply_proposal(project, {"summary": "Updated description"}, {
                    "kind": "changed_facts", "reason": "New feature", "sources": [material["id"]],
                }, [material], generated_at="2026-10-01T00:00:00+00:00")
            elif kind == "service_observation_changed":
                project["service_verification_results"] = [{
                    "requested_url": url, "lane": "browser", "surface_change": "surface_not_confirmed",
                    "observed_at": "2026-10-01T00:00:00+00:00",
                }]
            elif kind == "lexicon_publication_changed":
                project["publication_review"] = ["publisher_changed"]
            project_record.save_project(self.conn, project)
            expected[kind] = key
        self.conn.commit()

        for kind, key in expected.items():
            with self.subTest(kind=kind), captured_stdout() as output:
                self.assertEqual(cli.main(["--db-path", str(self.db_path), "review-queue", "--kinds", kind]), 0)
                result = json.loads(output.getvalue())
                self.assertEqual(result["review_count"], 1)
                self.assertEqual([item["project_key"] for item in result["items"]], [key])
                self.assertEqual([signal["kind"] for signal in result["items"][0]["signals"]], [kind])

    def test_demo_queue_batch_and_detail_keep_full_contribution_and_signals(self):
        dev_database.seed_database(self.conn)
        for key in ('proj:dddd00000002', 'proj:dddd00000004', 'proj:dddd00000005'):
            project = project_record.fetch_project(self.conn, key)
            project['editorial'] = {'proposal': {'kind': 'changed_facts', 'reason': 'New facts'}}
            project_record.save_project(self.conn, project)
        self.conn.commit()
        with mock.patch.object(project_record, 'ensure_project_shape', wraps=project_record.ensure_project_shape) as shape, \
             mock.patch.object(runtime_view, 'refresh_baselines', wraps=runtime_view.refresh_baselines) as baseline:
            queue = review.review_queue(self.conn, limit=100)
        self.assertEqual(shape.call_count, 5)
        self.assertEqual(baseline.call_count, 5)
        batch, end = review.build_review_queue_batch(self.conn, batch_size=100)
        self.assertEqual(end, 5)
        self.assertEqual(batch, queue)
        for key in ('proj:dddd00000002', 'proj:dddd00000004', 'proj:dddd00000005'):
            with self.subTest(key=key):
                full = project_record.fetch_project(self.conn, key)
                self.assertEqual(full['atproto_contribution_level'], 'optional')
                item = next(row for row in queue if row['project_key'] == key)
                self.assertEqual(item['atproto_contribution_level'], full['atproto_contribution_level'])
                self.assertEqual(item['atproto_contribution_score'], full['atproto_contribution_score'])
                self.assertEqual(item['seed_type'], full['seed_type'])
                self.assertEqual(item['seed_value'], full['seed_value'])
                expected = sorted(project_review.compute_review_signals(full, assume_shaped=True),
                                  key=lambda s: (-s['priority'], s['kind']))
                self.assertEqual(item['signals'], expected)
                self.assertEqual(item['sort_score'], project_review.review_queue_sort_score(full, expected))
                self.assertEqual(review.build_review_queue_item(self.conn, key), item)
                detail = presentation.project_detail(self.conn, project_key=key)
                self.assertEqual(detail['review'], item)
                self.assertEqual(detail['evaluation']['atproto_contribution_level'], item['atproto_contribution_level'])

    def test_relation_evidence_and_guards_control_contribution(self):
        for number, predicate, guarded, level, score in (
            (1, 'defines_schema', False, 'core', 90),
            (2, 'uses_collection', False, 'substantial', 70),
            (3, 'defines_schema', True, 'optional', 40),
        ):
            with self.subTest(predicate=predicate, guarded=guarded):
                key = f'proj:{number:012x}'
                url = f'https://relation{number}.example'
                nsid = 'org.example.notes.entry'
                project = self.make_project(key, 'service_url', url)
                registry.confirm_service_seed(project, url, source='fixture')
                project.update(editorial={'proposal': {'kind': 'changed_facts'}}, manual_review_recommended=True, manual_review_kind='identity_resolution',
                               manual_review_priority='medium', relation_evidence=[{
                    'relation': {'subject': {'type': 'service_url', 'value': url},
                                 'predicate': predicate, 'object': {'type': 'nsid', 'value': nsid},
                                 'locator': url + '/docs', 'method': 'fixture'},
                    'source': 'fixture', 'classification': 'candidate',
                    'observed_at': '2026-09-01T00:00:00+00:00',
                }])
                if guarded:
                    project['blocked_identifiers'] = [{'type': 'nsid', 'value': nsid, 'note': 'Unrelated schema'}]
                project_record.save_project(self.conn, project)
                full = project_record.fetch_project(self.conn, key)
                self.assertEqual((full['atproto_contribution_level'], full['atproto_contribution_score']), (level, score))
                item = review.build_review_queue_item(self.conn, key)
                self.assertEqual((item['atproto_contribution_level'], item['atproto_contribution_score']), (level, score))
                self.assertEqual(presentation.project_detail(self.conn, project_key=key)['review'], item)
        queue = review.review_queue(self.conn)
        self.assertEqual([row['atproto_contribution_score'] for row in queue], [90, 70, 40])

    def test_auth_only_observation_does_not_change_equal_priority_order(self):
        for key, auth in (('proj:aaaaaaaaaaaa', True), ('proj:bbbbbbbbbbbb', False)):
            project = self.make_project(key, 'service_url', f'https://{key[-12:]}.example')
            project.update(editorial={'proposal': {'kind': 'changed_facts'}}, name='Login tool' if auth else 'Collection hint', category='project',
                           atproto_auth_only=auth, manual_review_recommended=True,
                           manual_review_kind='identity_resolution', manual_review_priority='medium')
            if not auth:
                project['input_nsids'] = ['org.example.hint.entry']
            project_record.save_project(self.conn, project)
        queue = review.review_queue(self.conn)
        self.assertEqual([row['project_key'] for row in queue], ['proj:aaaaaaaaaaaa', 'proj:bbbbbbbbbbbb'])
        self.assertEqual([(row['atproto_contribution_level'], row['atproto_contribution_score']) for row in queue],
                         [('auth_only', 20), ('optional', 40)])
        self.assertEqual(queue[0]['top_priority'], queue[1]['top_priority'])

    def test_publication_change_is_visible_and_dismissal_is_respected_on_next_read(self):
        project = self.make_project('proj:cccccccccccc', 'service_url', 'https://publication.example')
        registry.confirm_service_seed(project, 'https://publication.example', source='fixture')
        project.update(name='Publication tool', category='project', service_status='live',
                       tagline='A publication tool', summary='Fictional description',
                       publication_review=['publisher_changed'])
        project_record.save_project(self.conn, project)
        item = review.build_review_queue_item(self.conn, project['project_key'])
        self.assertIsNotNone(item)
        self.assertEqual([s['kind'] for s in item['signals']], ['lexicon_publication_changed'])
        self.assertEqual(item['signals'][0]['details']['reasons'], ['publisher_changed'])
        self.assertEqual(review.review_queue(self.conn), [item])
        self.assertEqual(review.review_queue(self.conn, kinds={'candidate_repo_needs_confirmation'}), [])
        self.assertEqual(review.review_queue(self.conn, tracking_states={'stalled'}), [])
        saved = project_record.fetch_project(self.conn, project['project_key'])
        saved['dismissed_review_kinds'] = ['lexicon_publication_changed']
        project_record.save_project(self.conn, saved)
        self.assertEqual(review.review_queue(self.conn), [])
        self.assertIsNone(review.build_review_queue_item(self.conn, project['project_key']))
