from __future__ import annotations

import contextlib
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import project_merge, project_record
from project_registry_core.persistence import runtime_view


class DuplicateCandidatePrefilterTest(TempRegistryDbTestCase):
    def put(self, conn, key, *, name='', website='', claims=(), confirmations=()):
        project = self.make_project(key, 'unknown', '')
        project.update(name=name, website_url=website)
        project[runtime_view.RUNTIME_IDENTIFIER_ROWS] = [dict(
            kind=kind, value=value, role=role, classification=classification,
            is_primary=False, provider='', metadata={}, observed_at='',
        ) for kind, value, role, classification in claims]
        project[runtime_view.RUNTIME_IDENTIFIER_BASELINE] = []
        for kind, value in confirmations:
            registry.add_confirmed_identifier_decision(project, kind, value, source='web_ui')
        project_record.save_project(conn, project)
        return project_record.fetch_project(conn, key)

    def expected(self, conn, target):
        return project_merge.compute_duplicate_candidates(conn, target, projects=project_record.list_duplicate_projects(conn))

    def test_every_matching_signal_keeps_the_same_candidates_and_reasons(self):
        cases = [
            ('confirmed repo', ('repo_url','https://github.com/example/shared','project_repository','confirmed'), 'project_repository_urls'),
            ('candidate repo', ('repo_url','https://github.com/example/shared','project_repository','candidate'), 'candidate_project_repository_urls'),
            ('service', ('service_url','https://example.test/app','service','confirmed'), 'service_urls'),
        ]
        for number, (label, claim, reason) in enumerate(cases):
            with self.subTest(label=label), contextlib.closing(registry.open_db()) as conn:
                left = self.put(conn, f'proj:left{number}', claims=[claim])
                self.put(conn, f'proj:right{number}', claims=[claim])
                expected = self.expected(conn, left)
                right = next(item for item in expected if item['project_key'] == f'proj:right{number}')
                self.assertIn(reason, right['reasons'])
                self.assertEqual(expected, project_merge.compute_duplicate_candidates(conn, left))

    def test_candidate_confirmed_claims_preserve_kind_and_role(self):
        claims = [
            ('nsid','app.example.Record','owned_nsid'),
            ('site_url','https://component.example.test','project_site'),
            ('account','person.example.test','project_account'),
            ('handle','person.example.test','project_handle'),
            ('did','did:plc:component','brand_did'),
            ('repo_url','https://github.com/example/component','project_repository'),
            ('service_url','https://component.example.test','service'),
        ]
        with contextlib.closing(registry.open_db()) as conn:
            for number, (kind,value,role) in enumerate(claims):
                left = self.put(conn, f'proj:left{number}', claims=[(kind,value,role,'candidate')])
                self.put(conn, f'proj:right{number}', claims=[(kind,value,role,'confirmed')])
                expected = self.expected(conn, left)
                match = next(item for item in expected if item['project_key'] == f'proj:right{number}')
                self.assertIn('candidate_confirmed_identifiers', match['reasons'])
                self.assertEqual(expected, project_merge.compute_duplicate_candidates(conn, left))

    def test_aliases_websites_domains_and_confirmation_only_witnesses(self):
        cases = [
            (dict(name='Shared-Name'), dict(name='shared name'), 'merge_aliases'),
            (dict(website='https://same.example.test'), dict(website='https://same.example.test'), 'website_urls'),
            (dict(website='https://echo.test'), dict(website='https://echo.invalid'), 'merge_aliases'),
            (dict(claims=[('nsid','test.example.alpha.post','owned_nsid','confirmed')]),
             dict(claims=[('nsid','test.example.beta.post','owned_nsid','confirmed')]), 'authority_service_families'),
            (dict(confirmations=[('domain','abc.test')]), dict(confirmations=[('domain','ab.ctest')]), 'domain_identity_tokens'),
            (dict(confirmations=[('service_url','https://a.example.test')]),
             dict(confirmations=[('domain','b.example.test')]), 'authority_service_families'),
            (dict(claims=[('repo_url','https://github.com/one/Shared','project_repository','confirmed')]),
             dict(claims=[('repo_url','https://github.com/two/shared','project_repository','confirmed')]), 'merge_aliases'),
        ]
        with contextlib.closing(registry.open_db()) as conn:
            for number,(a,b,reason) in enumerate(cases):
                with self.subTest(reason=reason,number=number):
                    left=self.put(conn,f'proj:left{number}',**a)
                    self.put(conn,f'proj:right{number}',**b)
                    expected=self.expected(conn,left)
                    right=next(item for item in expected if item['project_key']==f'proj:right{number}')
                    self.assertIn(reason,right['reasons'])
                    self.assertEqual(expected,project_merge.compute_duplicate_candidates(conn,left))

    def test_namespace_lineage_retains_the_complete_comparison_path(self):
        with contextlib.closing(registry.open_db()) as conn:
            target=self.put(conn,'proj:target')
            self.put(conn,'proj:other',name='Other')
            target['recorded_lexicon_namespaces']=['app.example']
            target['record_creators']=['did:plc:creator']
            inputs=project_record.list_duplicate_candidate_inputs(conn)
            self.assertIsNone(project_merge.duplicate_candidate_keys(inputs,project_merge.duplicate_profile(target)))
            self.assertEqual(self.expected(conn,target),project_merge.compute_duplicate_candidates(conn,target))

    def test_unrelated_large_state_does_not_get_hydrated_as_project_count_grows(self):
        with contextlib.closing(registry.open_db()) as conn:
            target=self.put(conn,'proj:target',name='Target')
            self.put(conn,'proj:match',name='target')
            counts=[]
            for limit in (25,100):
                for number in range(len(counts)*25,limit):
                    key=f'proj:noise{number}'
                    if conn.execute('SELECT 1 FROM projects WHERE project_key=?',(key,)).fetchone():
                        continue
                    self.put(conn,key,name=f'Unrelated {number}',website=f'https://noise{number}.invalid')
                    conn.execute("UPDATE projects SET state_json=json_set(state_json,'$.evidence.unrelated',?) WHERE project_key=?",('x'*32768,key))
                conn.commit()
                expected=self.expected(conn,target)
                with mock.patch.object(project_record,'_duplicate_project_from_rows',wraps=project_record._duplicate_project_from_rows) as hydrate:
                    actual=project_merge.compute_duplicate_candidates(conn,target)
                self.assertEqual(actual,expected)
                counts.append(hydrate.call_count)
            self.assertEqual(counts,[1,1])

    def test_prefilter_and_selected_reads_share_a_snapshot_and_refresh_next_call(self):
        with contextlib.closing(registry.open_db()) as conn, contextlib.closing(registry.open_db()) as writer:
            target=self.put(conn,'proj:target',name='Shared')
            self.put(conn,'proj:other',name='Unrelated')
            conn.commit()
            expected=self.expected(conn,target)
            original=project_record.list_duplicate_candidate_inputs
            def change_after_prefilter(*args,**kwargs):
                result=original(*args,**kwargs)
                other=project_record.fetch_project(writer,'proj:other')
                other['name']='Shared'
                project_record.save_project(writer,other)
                return result
            with mock.patch.object(project_record,'list_duplicate_candidate_inputs',side_effect=change_after_prefilter):
                self.assertEqual(expected,project_merge.compute_duplicate_candidates(conn,target))
            self.assertFalse(conn.in_transaction)
            self.assertEqual([item['project_key'] for item in project_merge.compute_duplicate_candidates(conn,target)],['proj:other'])
            conn.execute('BEGIN')
            conn.execute("UPDATE projects SET name='Unrelated again' WHERE project_key='proj:other'")
            self.assertEqual(project_merge.compute_duplicate_candidates(conn,target),[])
            self.assertTrue(conn.in_transaction)
            conn.rollback()

    def test_selected_reader_preserves_order_empty_scope_and_unsavable_boundary(self):
        with contextlib.closing(registry.open_db()) as conn:
            for number in range(5):
                self.put(conn,f'proj:{number}',name='Shared')
            full=project_record.list_duplicate_projects(conn)
            keys={'proj:1','proj:3'}
            selected=project_record.list_duplicate_projects(conn,project_keys=keys)
            self.assertEqual(selected,[item for item in full if item['project_key'] in keys])
            self.assertEqual(project_record.list_duplicate_projects(conn,project_keys=set()),[])
            with self.assertRaisesRegex(ValueError,'complete runtime view'):
                project_record.save_project(conn,selected[0])

    def test_cancellation_during_prefilter_releases_owned_snapshot(self):
        with contextlib.closing(registry.open_db()) as conn:
            target=self.put(conn,'proj:target',name='Shared')
            conn.commit()
            changes=conn.total_changes
            calls=0
            def cancel():
                nonlocal calls
                calls+=1
                if calls==3:
                    raise RuntimeError('cancelled')
            with self.assertRaisesRegex(RuntimeError,'cancelled'):
                project_merge.compute_duplicate_candidates(conn,target,check_cancelled=cancel)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.total_changes,changes)
