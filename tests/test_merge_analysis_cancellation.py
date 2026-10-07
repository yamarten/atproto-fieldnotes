from __future__ import annotations

import contextlib
import http.client
import socket
import sqlite3
import subprocess
import threading
import time
from unittest import mock

from helpers import ProjectRecordTestCase, SCRIPTS, TempRegistryDbTestCase, registry
from project_registry_core import project_record, project_merge
from project_registry_core.application import presentation
from project_registry_web.request_cancellation import RequestCancellation, RequestCancelled
from project_registry_web.server import RegistryWebServer


class MergeAnalysisCancellationUnitTest(ProjectRecordTestCase):
    def test_browser_request_races_and_abort_errors(self):
        script = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(process.argv[1], 'utf8');
const part = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const nodes = new Map();
const state = {detailRequest:0, mergeAnalysisRequest:0, selected:'', detailRevision:'r'};
const pending = [], rendered = [], errors = [];
const context = vm.createContext({state, AbortController, token:'test',
  $: id => { if (!nodes.has(id)) nodes.set(id,{innerHTML:''}); return nodes.get(id); },
  document: {body:{classList:{add(){}}}}, syncUrl(){}, resetDetailScroll(){}, esc:x=>x,
  status: error=>errors.push(error), renderDetail:data=>rendered.push(data),
  apiErrorMessage: error=>error.message,
  fetch: (path, options) => new Promise((resolve,reject)=>pending.push({path,options,resolve,reject})),
});
vm.runInContext(part('async function api(', 'function restoreStateFromUrl(') +
  part('function clearDetailSelection()', 'function profileValue(') +
  part('async function loadMergeAnalysis(', 'async function loadInvestigationHistory('), context);
(async()=>{
  const a=context.loadDetail('a'), b=context.loadDetail('b');
  assert.equal(pending[0].options.signal.aborted,true);
  pending[1].resolve({ok:true,json:async()=>({key:'b'})}); await b;
  // Even if a completed response races with abort, it cannot overwrite b.
  pending[0].resolve({ok:true,json:async()=>({key:'a'})}); await a;
  assert.deepEqual(rendered,[{key:'b'}]);
  const merge=context.loadMergeAnalysis('b','r',{});
  context.clearDetailSelection();
  assert.equal(pending[2].options.signal.aborted,true);
  pending[2].resolve({ok:true,json:async()=>({revision:'r'})}); await merge;
  assert.equal(nodes.has('merge-analysis-content'),false);
  const c=context.loadDetail('c');
  context.clearDetailSelection();
  pending[3].reject(Object.assign(new Error('aborted'),{name:'AbortError'})); await c;
  assert.equal(errors.length,0);
  assert.equal(state.selected,'');
  assert.match(nodes.get('detail').innerHTML,/登録プロジェクトを選択/);
  const body=context.api('/body');
  pending[4].resolve({ok:true,json:async()=>{throw Object.assign(new Error('body aborted'),{name:'AbortError'});}});
  await assert.rejects(body,{name:'AbortError'});
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', script, str(SCRIPTS / 'project_registry_web/static/app.js')],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_disconnect_is_per_socket_and_peek_does_not_consume(self):
        left, right = socket.socketpair()
        try:
            cancellation = RequestCancellation(left)
            right.sendall(b'x')
            cancellation.check()
            self.assertEqual(left.recv(1), b'x')
            right.close()
            cancellation.next_check = 0
            with self.assertRaises(RequestCancelled):
                cancellation.check()
        finally:
            left.close()
            right.close()


class MergeAnalysisCancellationTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        with contextlib.closing(registry.open_db()) as conn:
            for number in range(12):
                project = self.make_project(f'proj:{number:012x}', 'service_url', 'https://example.test')
                project['website_url'] = 'https://example.test'
                project_record.save_project(conn, project)
            conn.commit()

    def test_checkpoints_preserve_results_and_stale_protection(self):
        with contextlib.closing(registry.open_db()) as conn:
            before = list(conn.iterdump())
            expected = presentation.project_merge_analysis(conn, project_key='proj:000000000000')
            check = mock.Mock()
            self.assertEqual(expected, presentation.project_merge_analysis(
                conn, project_key='proj:000000000000', check_cancelled=check))
            self.assertGreater(check.call_count, 24)
            self.assertEqual(before, list(conn.iterdump()))
            revision = presentation.project_revision
            with mock.patch.object(presentation, 'project_revision', side_effect=['old', 'new']):
                with self.assertRaises(presentation.StaleProjectError):
                    presentation.project_merge_analysis(conn, project_key='proj:000000000000', check_cancelled=check)
            self.assertTrue(revision(conn, 'proj:000000000000'))

    def test_cancellation_stops_hydration_index_and_pair_comparison(self):
        for stage, module, name in (
            ('hydrate', project_record, '_duplicate_project_from_rows'),
            ('index', project_merge, 'duplicate_profile'),
            ('pairs', project_merge, 'duplicate_candidate_for_pair'),
        ):
            with self.subTest(stage=stage), contextlib.closing(registry.open_db()) as conn:
                count = 0
                original = getattr(module, name)
                def work(*args, **kwargs):
                    nonlocal count
                    count += 1
                    return original(*args, **kwargs)
                def check():
                    if count >= 3:
                        raise RequestCancelled()
                with mock.patch.object(module, name, side_effect=work):
                    with self.assertRaises(RequestCancelled):
                        presentation.project_merge_analysis(conn, project_key='proj:000000000000', check_cancelled=check)
                self.assertEqual(count, 3)
                self.assertFalse(conn.in_transaction)
                self.assertEqual(conn.total_changes, 0)

    def test_sql_interrupt_and_progress_handler_cleanup(self):
        with contextlib.closing(sqlite3.connect(':memory:')) as conn:
            cancellation = RequestCancellation(None)
            calls = 0
            def check():
                nonlocal calls
                calls += 1
                if calls > 2:
                    cancellation.cancelled = True
                    raise RequestCancelled()
            with mock.patch.object(cancellation, 'check', side_effect=check):
                with self.assertRaises(RequestCancelled), cancellation.during_queries(conn):
                    conn.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000) SELECT sum(x) FROM n').fetchone()
            self.assertEqual(conn.execute('SELECT 42').fetchone()[0], 42)

    def test_real_http_disconnect_stops_work_and_keeps_other_request(self):
        started = threading.Event()
        stopped = threading.Event()
        completed = threading.Event()
        visited = {}
        original_hydrate = project_record._duplicate_project_from_rows
        original_analysis = presentation.project_merge_analysis
        def hydrate(*args, **kwargs):
            ident = threading.get_ident()
            visited[ident] = visited.get(ident, 0) + 1
            started.set()
            time.sleep(.02)  # Controlled work; production has no artificial waits.
            return original_hydrate(*args, **kwargs)
        def analysis(*args, **kwargs):
            try:
                result = original_analysis(*args, **kwargs)
                completed.set()
                return result
            except RequestCancelled:
                stopped.set()
                raise
        with mock.patch('project_registry_web.server.JobManager'), mock.patch.object(project_record, '_duplicate_project_from_rows', side_effect=hydrate), mock.patch.object(presentation, 'project_merge_analysis', side_effect=analysis):
            server = RegistryWebServer(('127.0.0.1', 0), token='test', db_path=self.db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            old = http.client.HTTPConnection(*server.server_address, timeout=3)
            current = http.client.HTTPConnection(*server.server_address, timeout=3)
            path = '/api/projects/proj:000000000000/merge-analysis'
            try:
                old.request('GET', path, headers={'X-Project-Registry-Token': 'test'})
                self.assertTrue(started.wait(2))
                current.request('GET', path, headers={'X-Project-Registry-Token': 'test'})
                old.sock.shutdown(socket.SHUT_RDWR)
                old.close()
                self.assertTrue(stopped.wait(1))
                response = current.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(b'duplicates', response.read())
                self.assertTrue(completed.is_set())
                self.assertEqual(len(visited), 2)
                self.assertLess(min(visited.values()), max(visited.values()))
            finally:
                old.close()
                current.close()
                server.shutdown()
                server.server_close()
                thread.join(2)
