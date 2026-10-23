from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import sqlite3
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import cli, project_record
from project_registry_core.application import review


class TrackingReviewTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()
        self.now = dt.datetime.now(dt.timezone.utc)
        self.fixture_number = 0

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def seed(self, kind):
        self.conn.close()
        self.fixture_number += 1
        self.db_path = self.db_path.with_name(f'review-{self.fixture_number}.db')
        registry.DB_PATH = self.db_path
        self.conn = registry.initialize_db(self.db_path)
        for suffix in ('a', 'b', 'c', 'd'):
            project = self.make_project(f'proj:{suffix}', 'repo_url', f'https://github.com/example/{suffix}')
            project['name'] = suffix
            project['tracking_state'] = 'active'
            project['tracking_note'] = 'operator note' if suffix == 'd' else ''
            if kind == 'stalled':
                project['source_repo_records'] = [{
                    'url': f'https://github.com/example/{suffix}', 'provider': 'github',
                    'confidence': 'confirmed', 'relation': 'source_repo', 'source': 'fixture',
                    'updated_at': (self.now if suffix == 'c' else self.now - dt.timedelta(days=100)).isoformat(),
                    'observed_at': self.now.isoformat(),
                }]
                project['last_enriched_at'] = self.now.isoformat()
                if suffix == 'b':
                    project['tracking_state'] = 'stable'
                if suffix == 'c':
                    project['tracking_state'] = 'stalled'
            elif kind == 'ignore':
                project['source_repo_records'] = [{
                    'url': f'https://github.com/example/{suffix}', 'provider': 'github',
                    'confidence': 'confirmed', 'relation': 'fork_repo', 'source': 'fixture',
                }]
                if suffix == 'b':
                    project['introduced_url'] = 'https://example.test/introduced'
            else:
                project['category'] = 'project'
                project['atproto_auth_only'] = True
                if suffix == 'b':
                    project['introduced_url'] = 'https://example.test/introduced'
                if suffix == 'c':
                    project['tracking_state'] = 'out_of_scope'
                    project['tracking_note'] = 'old exclusion'
                    project['input_nsids'] = ['app.example.post']
            project_record.save_project(self.conn, project, preserve_timestamps=True)
        self.conn.commit()

    def service(self, kind, **kwargs):
        if kind == 'stalled':
            return review.stalled(self.conn, now=self.now, **kwargs)
        return getattr(review, kind)(self.conn, **kwargs)

    def run_cli(self, kind, answers, *options, tty=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        stdout.isatty = lambda: tty
        args = cli.build_parser().parse_args([
            f'review-{kind.replace("_", "-")}', '--interactive', *options,
        ])
        with (
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr),
            mock.patch('sys.stdin.isatty', return_value=tty),
            mock.patch('builtins.input', side_effect=answers),
        ):
            status = args.func(args)
        values, remaining = [], stdout.getvalue().strip()
        while remaining:
            value, end = json.JSONDecoder().raw_decode(remaining)
            values.append(value)
            remaining = remaining[end:].lstrip()
        return status, values

    def saved(self):
        return {p['project_key']: (p['tracking_state'], p['tracking_note'])
                for p in project_record.list_projects(self.conn)}

    def test_cli_and_service_select_order_and_apply_the_same_real_candidates(self):
        for kind, expected in (
            ('stalled', ['proj:c', 'proj:a', 'proj:d']),
            ('ignore', ['proj:a', 'proj:c', 'proj:d']),
            ('out_of_scope', ['proj:a', 'proj:d', 'proj:c']),
        ):
            with self.subTest(kind=kind):
                self.seed(kind)
                initial = self.saved()
                preview = self.service(kind)
                self.assertEqual([c['project_key'] for c in preview['candidates']], expected)
                self.assertEqual(self.saved(), initial)
                options = {'note': 'review'}
                cli_options = ['--note', 'review']
                if kind != 'stalled':
                    options['note_reason'] = 'other'
                    cli_options += ['--note-reason', 'other']
                result = self.service(kind, execute=True, **options)
                service_saved = self.saved()
                self.assertEqual(result['updated_project_keys'], expected)
                self.assertEqual(service_saved['proj:d'][1], 'operator note')
                self.assertEqual(service_saved['proj:b'], initial['proj:b'])
                target = 'stalled' if kind == 'stalled' else kind
                self.assertEqual(service_saved['proj:a'][0], target)
                if kind == 'out_of_scope':
                    self.assertEqual(service_saved['proj:c'], ('active', 'reactivated after owned protocol surface detection'))
                self.seed(kind)
                status, output = self.run_cli(kind, ['y'] * 3, *cli_options)
                self.assertEqual(status, 0)
                self.assertEqual([item['project_key'] for item in output[:-1]], expected)
                self.assertEqual(output[-1]['updated_project_keys'], expected)
                self.assertEqual(self.saved(), service_saved)

    def test_service_limit_selection_and_stale_plan_use_saved_revisions(self):
        for kind in ('stalled', 'ignore', 'out_of_scope'):
            with self.subTest(kind=kind):
                self.seed(kind)
                preview = self.service(kind, limit=2)
                self.assertEqual((preview['candidate_count'], preview['total_candidate_count']), (2, 3))
                self.assertTrue(preview['plan_fingerprint'].startswith('plan:'))
                selected = preview['candidates'][1]
                self.assertTrue(selected['candidate_id'].startswith('candidate:'))
                result = self.service(kind, execute=True, limit=2,
                    selected_candidate_ids={selected['candidate_id']},
                    expected_plan_fingerprint=preview['plan_fingerprint'])
                self.assertEqual(result['updated_project_keys'], [selected['project_key']])
                before = self.saved()
                with self.assertRaisesRegex(ValueError, 'plan changed'):
                    self.service(kind, execute=True, limit=2, expected_plan_fingerprint=preview['plan_fingerprint'])
                with self.assertRaisesRegex(ValueError, 'stale candidate ids'):
                    self.service(kind, execute=True, selected_candidate_ids={'candidate:unknown'})
                self.assertEqual(self.saved(), before)

    def test_interactive_full_json_skip_quit_and_default_note(self):
        self.seed('ignore')
        status, output = self.run_cli('ignore', ['j', 'n', 'y', '', '', 'q'])
        self.assertEqual(status, 0)
        self.assertEqual(output[1]['project_key'], 'proj:a')  # Full project JSON.
        self.assertEqual(output[-1]['updated_project_keys'], ['proj:c'])
        self.assertEqual(self.saved()['proj:c'], ('ignore', 'review-ignore: 独自性の薄いfork'))
        self.assertEqual(self.saved()['proj:a'][0], 'active')
        self.assertEqual(self.saved()['proj:d'][0], 'active')

    def test_interactive_limit_counts_all_candidates_and_eof_skips_writes(self):
        self.seed('ignore')
        initial = self.saved()
        status, output = self.run_cli('ignore', [EOFError()], '--limit', '1')
        self.assertEqual(status, 0)
        self.assertEqual(output[0]['review_ignore_count'], 1)
        self.assertEqual(output[-1]['kind_counts'], {'fork_without_independent_identity': 3})
        self.assertEqual(output[-1]['updated_count'], 0)
        self.assertEqual(self.saved(), initial)

    def test_service_reports_progress_and_cancels_at_candidate_boundary(self):
        for kind in ('stalled', 'ignore', 'out_of_scope'):
            with self.subTest(kind=kind):
                self.seed(kind)
                events = []
                result = self.service(kind, execute=True, progress=lambda *event: events.append(event),
                                      cancel_requested=lambda: bool(events))
                self.assertTrue(result['cancelled'])
                self.assertEqual(result['updated_count'], 1)
                self.assertEqual(events[0][:2], (1, 3))

    def test_concurrent_edit_rejects_the_shown_snapshot_and_continues_other_candidates(self):
        for kind in ('stalled', 'ignore', 'out_of_scope'):
            for interactive in (False, True):
                with self.subTest(kind=kind, interactive=interactive):
                    self.seed(kind)
                    candidates = self.service(kind)['candidates']
                    changed_key = candidates[0 if interactive else 1]['project_key']
                    initial_state = self.saved()[changed_key][0]
                    changed = False

                    def edit(*_):
                        nonlocal changed
                        if not changed:
                            changed = True
                            with contextlib.closing(registry.open_db()) as other:
                                project = project_record.fetch_project(other, changed_key)
                                project['tracking_note'] = 'concurrent operator edit'
                                project_record.save_project(other, project)
                        return 'y'

                    if interactive:
                        options = ['--note', 'review']
                        if kind != 'stalled':
                            options += ['--note-reason', 'other']
                        status, output = self.run_cli(kind, edit, *options)
                        result = output[-1]
                        self.assertEqual(status, 2)
                    else:
                        result = self.service(kind, execute=True, progress=edit)
                    self.assertEqual(result['operation_status'], 'retry_wait')
                    self.assertEqual(result['updated_count'], 2)
                    self.assertEqual([item['project_key'] for item in result['conflicted_projects']], [changed_key])
                    self.assertEqual(self.saved()[changed_key], (initial_state, 'concurrent operator edit'))

    def test_cli_closes_its_connection_on_success_quit_and_errors(self):
        for kind in ('stalled', 'ignore', 'out_of_scope'):
            for outcome in ('success', 'quit', 'non_tty', 'input_error'):
                with self.subTest(kind=kind, outcome=outcome):
                    self.seed(kind)
                    opened = []
                    open_db = registry.open_db

                    def track_connection():
                        conn = open_db()
                        opened.append(conn)
                        return conn

                    options = ['--note', 'review']
                    if kind != 'stalled':
                        options += ['--note-reason', 'other']
                    with mock.patch.object(registry, 'open_db', side_effect=track_connection):
                        if outcome == 'input_error':
                            with self.assertRaisesRegex(RuntimeError, 'input failed'):
                                self.run_cli(kind, [RuntimeError('input failed')], *options)
                        elif outcome == 'non_tty':
                            with self.assertRaisesRegex(ValueError, 'requires a TTY'):
                                self.run_cli(kind, [], *options, tty=False)
                        else:
                            self.run_cli(kind, ['q'] if outcome == 'quit' else ['y'] * 3, *options)
                    self.assertEqual(len(opened), 1)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        opened[0].execute('SELECT 1')
