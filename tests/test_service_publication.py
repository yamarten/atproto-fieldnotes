import json
import unittest
from pathlib import Path

from project_registry_core import repository_analysis as analysis
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord


class ServicePublicationTest(unittest.TestCase):
    repo = 'https://github.com/example/app'

    def material(self, text):
        bundle = EvidenceBundle(project_key='proj:publication', collected_at='2026-09-10T00:00:00Z')
        bundle.add_record(EvidenceRecord(
            kind='github_repo_snapshot', source='github_repo:' + self.repo, classification='confirmed',
            observed_at=bundle.collected_at, value={'repo_url': self.repo, 'repo': {},
            'readme_sections': [{'path': 'README', 'line': 1, 'excerpt': text}]}))
        return analysis.build_material(self.repo, bundle)

    def test_reviewed_publication_urls_are_available_to_model_with_exact_paths(self):
        fixture = Path(__file__).parent / 'fixtures/project_registry/service-publication-v1.json'
        for case in json.loads(fixture.read_text())['cases']:
            material = self.material(case['text'])
            candidates = analysis.observation_candidate_catalog(material)
            urls = {c['value'] for c in candidates.values() if c['predicate'] == 'declares_service_url'}
            self.assertTrue(set(case['expected']) <= urls, case['id'])

    def test_applicability_gates_confirmation_not_uncertain_candidate_admission(self):
        material = self.material('Try this app at https://example.org/app')
        candidate = next(c for c in analysis.observation_candidate_catalog(material).values()
                         if c['predicate'] == 'declares_service_url')
        for applicability in (None, 'current_project', 'uncertain', 'third_party', 'negated', 'hypothetical', 'obsolete'):
            result = analysis.normalize_result(material, {
                'summary': 'アプリ', 'summary_evidence': candidate['evidence_ids'],
                'observations': [{'candidate_id': candidate['candidate_id'], 'applicability': applicability,
                                  'service_kind': 'web'}], 'collections': [], 'capabilities': [],
            }, {}, '2026-09-10T00:00:00Z')
            project = {'project_repository_urls': [self.repo], 'repository_analysis_results': [result]}
            self.assertEqual(bool(analysis.service_publications(project)), applicability in {'current_project', 'uncertain'})
            self.assertEqual(bool(analysis.service_publications(project, confirmed_only=True)), applicability == 'current_project')
            project['project_repository_urls'] = []
            self.assertEqual(analysis.service_publications(project), [])

    def test_mixed_documentation_and_service_declarations_are_distinct_candidates(self):
        material = self.material('Try our app at https://example.org/app. Documentation for this project is hosted at https://docs.example.org.')
        catalog = analysis.observation_candidate_catalog(material)
        self.assertEqual({x['predicate'] for x in catalog.values() if x.get('value') == 'https://example.org/app'},
                         {'declares_service_url', 'declares_documentation_site_url'})
