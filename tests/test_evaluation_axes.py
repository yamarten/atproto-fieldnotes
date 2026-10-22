import contextlib
import datetime as dt

from helpers import TempRegistryDbTestCase, ProjectRecordTestCase, registry
from project_registry_core import atproto_usage, collection_trust, project_record, project_review, service_assessment, promotion_attention
from project_registry_core.application import review, decisions
from project_registry_core.repositories import repository_analysis


def with_source(project, text, *, path="repository metadata", line=0, stale=False):
    repo="https://github.com/example/"+project['project_key'].split(':')[-1]
    evidence={"path":path,"excerpt":text}
    evidence.update({"line":line} if line else {"json_pointer":"/description"})
    project.update(project_repository_urls=[repo], repository_analysis_results=[{
        "repo_url":repo,"summary":"Source summary", "summary_evidence":[evidence],
        "version":repository_analysis.ANALYSIS_VERSION,"policy_version":repository_analysis.POLICY_VERSION,
        "stale":stale,"source_revision":"fixed-revision",
    }])
    return project


class EvaluationAxesTest(ProjectRecordTestCase):
    def project(self, key, text=""):
        p=self.make_project("proj:"+key,"service_url","https://"+key+".example")
        p.update(category="project",service_urls=["https://"+key+".example"],service_status="live",
                 service_status_evidence=["login_surface_detected"],service_status_confidence="medium",
                 technical_interest_level="high",technical_interest_summary="Reusable protocol mechanism",
                 technical_interest_reasons=["implementation_reference"])
        return with_source(p,text) if text else p

    def test_purpose_does_not_create_a_ranking_group(self):
        regular=self.project("regular")
        # Positive examples reduced from Fortransky, Corgi and SkySweeper README/metadata.
        for text in ["A terminal-only Bluesky / AT Protocol client written in Fortran.",
                     "A community-governed Bluesky feed generator where subscribers vote on algorithm parameters",
                     "SkySweeper is a service which automatically deletes your old skeets from Bluesky."]:
            with self.subTest(text=text):
                client=self.project("client",text)
                client["resolved_nsids"]=["app.client.profile"]
                queue=review.build_showcase_queue_from_projects([client,regular],limit=None)
                self.assertEqual([q['project_key'] for q in queue],[client['project_key'],regular['project_key']])
                self.assertNotIn('relevance',queue[0])
                self.assertEqual(len(review.build_showcase_queue_from_projects([client],view='technical')),1)

    def test_technical_selection_is_independent_of_normal_candidate_conditions(self):
        p=self.project('technical')
        p.update(service_urls=[],resolved_nsids=[],service_status='none')
        self.assertEqual(review.build_showcase_queue_from_projects([p]),[])
        self.assertEqual(len(review.build_showcase_queue_from_projects([p],view='technical')),1)

    def test_freshness_can_cross_old_signal_priorities_and_manual_lowering_wins(self):
        old=self.project('old')
        old.update(resolved_nsids=['app.example.old'],source_repo_records=[])
        recent=self.project('recent')
        recent.update(service_status='unverified',collection_observations=[{
            'nsid':'app.example.activity','max':dt.datetime.now(dt.timezone.utc).isoformat()}])
        self.assertEqual([r['project_key'] for r in review.build_showcase_queue_from_projects([old,recent])],
                         [recent['project_key'],old['project_key']])
        project_review.set_promotion_priority_on_project(recent,lowered=True,reason='Special topic')
        self.assertEqual([r['project_key'] for r in review.build_showcase_queue_from_projects([old,recent])],
                         [old['project_key'],recent['project_key']])

    def test_lexicon_resolution_counts_more_than_twelve_and_invalidates_latest_failure(self):
        p=self.project('schemas')
        p['resolved_nsids']=[f'app.example.schema{i}' for i in range(25)]
        p['lexicon_schema_samples']=[{'nsid':n,'id':n} for n in p['resolved_nsids'][:12]]
        p['lexicon_resolution_entries']=[{
            'input_nsid':n,'resolved_nsid':n,'repo_did':'did:plc:test','authority_domain':'example.app',
            'record_uri':f'at://did:plc:test/com.atproto.lexicon.schema/{n}',
            'resolved_at':'2026-10-01T00:00:00Z','publication_validation':{
                'version':collection_trust.VALIDATION_VERSION,'schema_id':n,'schema_type':'record','schema_hash':'fixture-hash'}}
            for n in p['resolved_nsids']]
        self.assertEqual(project_review.promotion_lexicon_evidence(p),{'owned_count':25,'resolved_count':25})
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[])['components']['lexicon'],40)
        p['lexicon_resolution_entries'].append({**p['lexicon_resolution_entries'][0],
            'resolved_at':'2026-10-02T00:00:00Z','publication_validation':{}})
        self.assertEqual(project_review.promotion_lexicon_evidence(p)['resolved_count'],24)
        p['blocked_identifiers']=[{'type':'nsid','value':p['resolved_nsids'][1]}]
        self.assertEqual(project_review.promotion_lexicon_evidence(p),{'owned_count':24,'resolved_count':23})
        p['lexicon_resolution_entries'][2]['publication_validation']['type_changed']=True
        self.assertEqual(project_review.promotion_lexicon_evidence(p)['resolved_count'],22)
        p['publication_schemas']=[{'schema_id':p['resolved_nsids'][3],'status':'deleted'}]
        self.assertEqual(project_review.promotion_lexicon_evidence(p)['resolved_count'],21)

    def test_small_lexicon_slope_and_no_confidence_review_or_role_bonus(self):
        p=self.project('score')
        p['resolved_nsids']=['app.example.one']
        before=project_review.promotion_queue_score_breakdown(p,[{'kind':'service_surface_confirmed'}])
        self.assertEqual(before['components'],{'lexicon':1,'service_evidence':16,'recent_activity':0,
                                               'description':0,'repository':0})
        p.update(manual_review_recommended=True,
                 service_status_confidence='high',input_nsids=['app.example.hint'])
        atproto_usage.set_override(p,role='main_data',focus='independent',reason='Main data',assessed_at='now')
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[{'kind':'service_surface_confirmed'}]),before)
        p['resolved_nsids'].append('app.example.two')
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[])['components']['lexicon'],2)

    def test_activity_is_scored_once_and_missing_or_old_dates_are_neutral(self):
        p=self.project('activity')
        now=dt.datetime(2026,10,23,tzinfo=dt.timezone.utc)
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[],now=now)['components']['recent_activity'],0)
        for days,expected in [(0,30),(3,30),(4,24),(8,18),(15,12),(31,6),(61,0),(200,0)]:
            stamp=(now-dt.timedelta(days=days)).isoformat()
            p.update(source_repo_records=[{'updated_at':stamp}],collection_observations=[{'max':stamp}])
            score=project_review.promotion_queue_score_breakdown(p,[],now=now)
            self.assertEqual(score['components']['recent_activity'],expected)
            self.assertEqual(score['total'],expected)

    def test_description_is_a_single_small_bonus_not_a_completeness_checklist(self):
        p=self.project('description')
        for summary,tagline in [('Details',''),('', 'Short explanation'),('Details','Short explanation')]:
            p.update(summary=summary,tagline=tagline)
            self.assertEqual(project_review.promotion_queue_score_breakdown(p,[])['total'],3)
        p.update(tagline_is_provisional=False,manual_review_recommended=False)
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[])['total'],3)
        p.update(summary='  ',tagline='\n')
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[])['total'],0)

    def test_repository_bonus_requires_adoption_and_respects_guards(self):
        p=self.project('repository')
        repo='https://github.com/example/implementation'
        p.update(candidate_project_repository_urls=[repo],manual_seeds=[{'type':'repo_url','value':repo}],
                 source_repo_records=[{'repo_url':repo}])
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[])['total'],0)
        p['project_repository_urls']=[repo]
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[])['total'],3)
        for field in ('blocked_identifiers','deferred_identifiers'):
            p[field]=[{'type':'repo_url','value':repo}]
            self.assertEqual(project_review.promotion_queue_score_breakdown(p,[])['total'],0)
            p[field]=[]
        p['project_repository_urls'].append('https://github.com/example/second')
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[])['total'],3)

    def test_service_scores_observed_access_without_penalizing_beta(self):
        p=self.project('access')
        for status,evidence,expected in [
            ('live',['browser_public_app_surface_detected'],20),
            ('beta',['browser_public_app_surface_detected'],20),
            ('live',['login_surface_detected'],16),
            ('beta',['login_surface_detected'],16),
            ('invite_only',['login_surface_detected'],12),
            ('invite_only',['browser_public_app_surface_detected'],12),
            ('invite_only',['invite_text_detected'],8),
            ('unverified',['login_surface_detected'],8),
            ('live',['app_shell_detected'],8),
        ]:
            with self.subTest(status=status,evidence=evidence):
                p.update(service_status=status,service_status_evidence=evidence)
                signals=project_review._showcase_signals_from_shaped(
                    p,include_personal_tools=False,include_ineligible=False)
                score=project_review.promotion_queue_score_breakdown(p,signals)
                self.assertEqual(score['components']['service_evidence'],expected)
                self.assertFalse(service_assessment.projection(p)['usage_tested'])

    def test_activity_and_supporting_evidence_have_bounded_tradeoffs(self):
        now=dt.datetime(2026,10,23,tzinfo=dt.timezone.utc)
        established=self.project('established')
        established['resolved_nsids']=[f'app.example.schema{i}' for i in range(40)]
        recent=self.project('new')
        recent.update(resolved_nsids=['app.example.one'],summary='Description',
                      project_repository_urls=['https://github.com/example/new'],
                      collection_observations=[{'max':now.isoformat()}])
        score=lambda p: project_review.promotion_queue_score_breakdown(p,[],now=now)['total']
        # Recent activity plus all investigation aids cannot erase a 39-point
        # definition difference, but can overtake a modest ten-definition lead.
        self.assertGreater(score(established),score(recent))
        established['resolved_nsids']=established['resolved_nsids'][:10]
        self.assertGreater(score(recent),score(established))

    def test_conflicting_operator_purposes_block_merge(self):
        left=self.project('left'); right=self.project('right')
        for p,focus in [(left,'bluesky'),(right,'infrastructure')]:
            atproto_usage.set_override(p,role='main_data',focus=focus,reason='Explicit purpose',assessed_at='now')
        with self.assertRaisesRegex(ValueError,'conflicting'):
            atproto_usage.merge_decisions(left['atproto_usage_decisions'],right['atproto_usage_decisions'],cleared_at='later')

    def test_no_schema_or_record_count_bonus_for_review_and_no_confidence_bonus(self):
        from project_registry_core import project_review
        p=self.project('facts')
        first=project_review.promotion_queue_score_breakdown(p,[])
        p['service_status_confidence']='high'
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[]),first)
        self.assertEqual(project_review.review_queue_sort_score({'atproto_contribution_score':90},[{'priority':4}]),40)

    def test_existing_ordinary_confirmation_fingerprint_is_preserved(self):
        # Recorded with the pre-change policy and this fixed minimal project.
        p=self.make_project('proj:held')
        self.assertEqual(promotion_attention.fingerprint(p),
                         'promotion-attention-v1:57846fbc59e2e7edb96a1e4b1cce17ba9305ad461cc4cc05b9523188eb996c24')

    def test_surface_scope_and_latest_failure(self):
        p=self.project('service')
        for evidence,expected in [(['login_surface_detected'],'login'),(['app_shell_detected'],'app_shell'),
                                  (['verified_authority_service_surface'],'unconfirmed'),
                                  (['docs_only_surface_detected'],'documentation'),
                                  (['beta_text_detected'],'announcement')]:
            p['service_status_evidence']=evidence
            result=service_assessment.projection(p)
            self.assertEqual(result['surface'],expected)
            self.assertFalse(result['usage_tested'])
        p['service_verification_results']=[
            {'requested_url':p['service_urls'][0],'lane':'static','observed_at':'2026-10-01T00:00:00Z',
             'status_code':200,'supports_project':True,'service_status_evidence':['login_surface_detected']},
            {'requested_url':p['service_urls'][0],'lane':'static','observed_at':'2026-10-02T00:00:00Z',
             'status_code':503,'supports_project':True,'service_status_evidence':[]}]
        self.assertEqual(service_assessment.projection(p)['surface'],'failed')
        self.assertFalse(service_assessment.projection(p)['entrypoint_observed'])
        p['service_verification_results'][-1].update(status_code=200,supports_project=False,
                                                    service_status_evidence=['login_surface_detected'])
        self.assertEqual(service_assessment.projection(p)['surface'],'unconfirmed')
        p['service_verification_results'].append({'requested_url':p['service_urls'][0], 'lane':'browser',
            'observed_at':'2026-10-03T00:00:00Z','public_app_surface_detected':True})
        self.assertEqual(service_assessment.projection(p)['surface'],'public')
        p['service_verification_results'][-1]['rendered_error_detected']=True
        self.assertEqual(service_assessment.projection(p)['surface'],'failed')

    def test_docs_only_none_does_not_mean_service_absence(self):
        from project_registry_core import project_review
        p=self.project('docs')
        p.update(service_status='none',service_status_evidence=['docs_only_surface_detected'],
                 technical_interest_level='none',technical_interest_summary='')
        signals=project_review.compute_showcase_signals(p)
        self.assertEqual([s['kind'] for s in signals],['service_candidate_needs_validation'])


class EvaluationAxesPersistenceTest(TempRegistryDbTestCase):
    def test_legacy_decision_new_focus_save_clear_and_reconfirmation(self):
        p=self.make_project('proj:legacy')
        p['category']='project'
        atproto_usage.set_override(p,role='bluesky_client',reason='Old explicit purpose',assessed_at='2026-10-01')
        with contextlib.closing(registry.open_db()) as conn:
            project_record.save_project(conn,p)
            # Existing schema-5 rows have no focus metadata before this change.
            conn.execute("UPDATE project_decisions SET metadata_json='{}' WHERE kind='assess_atproto_usage'")
            conn.commit()
            restored=project_record.fetch_project(conn,p['project_key'])
            self.assertEqual(atproto_usage.projection(restored)['role'],'unknown')
            self.assertEqual(atproto_usage.projection(restored)['focus'],'bluesky')
            baseline=promotion_attention.fingerprint(restored)
            decisions.set_atproto_usage(conn,project_key=p['project_key'],role='main_data',focus='infrastructure',reason='Independent AppView implementation')
            restored=project_record.fetch_project(conn,p['project_key'])
            self.assertEqual(atproto_usage.projection(restored)['focus'],'infrastructure')
            self.assertNotEqual(promotion_attention.fingerprint(restored),baseline)
            self.assertEqual(len(restored['atproto_usage_decisions']),2)
            decisions.set_atproto_usage(conn,project_key=p['project_key'],clear=True)
            self.assertEqual(atproto_usage.projection(project_record.fetch_project(conn,p['project_key']))['source'],'none')

    def test_legacy_focus_annotation_has_no_ranking_effect(self):
        p=with_source(self.make_project('proj:override'),'A Bluesky client')
        p['category']='project'
        atproto_usage.set_override(p,role='unknown',focus='unknown',reason='Quoted another app',assessed_at='now')
        before=project_review.promotion_queue_score_breakdown(p,[])
        atproto_usage.set_override(p,role='unknown',focus='auto',reason='Use original declaration',assessed_at='later')
        self.assertEqual(project_review.promotion_queue_score_breakdown(p,[]),before)
