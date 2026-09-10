from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from helpers import registry, project_record, TempRegistryDbTestCase
from project_registry_core import repository_trust, repository_trust_migration, trust_graph


SERVICE = "https://app.example.com"
REPO = "https://github.com/example/app"


def edge(*, classification="confirmed", source="service_page", method="bounded_first_party_link", reverse=False):
    subject = {"kind": "service_url", "value": SERVICE}
    obj = {"kind": "repo_url", "value": REPO}
    return {"subject": obj if reverse else subject, "object": subject if reverse else obj,
            "predicate": "repo_links_service" if reverse else "service_links_repo",
            "classification": classification, "eligible_for_auto_confirm": classification == "confirmed",
            "source": source, "method": method, "locator": SERVICE,
            "observed_at": "2026-09-03T09:46:39+00:00", "context": {"reason": "fixture"}}


class RepositoryTrustTest(unittest.TestCase):
    def test_known_lexrpc_candidate_cannot_admit_repository_probe(self):
        service = "https://lexrpc.readthedocs.io"
        repo = "https://github.com/bluesky-social/atproto"
        stale = {**edge(), "subject": {"kind": "service_url", "value": service},
                 "object": {"kind": "repo_url", "value": repo},
                 "source": f"service_page:{service}", "locator": service}
        project = project_record.default_project_record("proj:229e919decf3", "service_url", service)
        registry.add_manual_seed(project, "service_url", service, source="test")
        project["candidate_project_repository_urls"] = [repo]
        project["trust_graph"] = {"edges": [stale]}
        repository_trust.record_observation(project, {**stale, "classification": "candidate",
            "context": {"disposition": "REVIEW_PROJECT_REPOSITORY", "reason": "external schema references"}})
        self.assertEqual(registry.candidate_repo_trust_probe_urls(project), [])

    def test_current_candidate_overrides_stale_confirmed_mirror(self):
        # lexrpc failure shape: same source/method/time, current candidate, old confirmed mirror.
        project = {"trust_graph": {"edges": [edge()]}}
        repository_trust.record_observation(project, edge(classification="candidate"))
        current = trust_graph.graph(project)["edges"]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["classification"], "candidate")
        self.assertEqual(trust_graph.auto_confirm_edges(current), [])
        self.assertEqual(len(trust_graph.stored_graph(project)["edges"]), 1)

    def test_new_observation_replaces_same_source_but_preserves_independent_source(self):
        project = {}
        repository_trust.record_observation(project, edge())
        repository_trust.record_observation(project, edge(source="independent"))
        later = {**edge(classification="candidate"), "observed_at": "2026-09-04T00:00:00+00:00"}
        repository_trust.record_observation(project, later)
        repository_trust.record_observation(project, edge())  # late arrival of older data
        self.assertEqual([item["classification"] for item in trust_graph.graph(project)["edges"]], ["candidate", "confirmed"])

    def test_observation_writer_does_not_store_mirror_and_retains_context(self):
        project = {}
        trust_graph.upsert_edge(project, **edge())
        self.assertEqual(trust_graph.stored_graph(project)["edges"], [])
        self.assertEqual(trust_graph.graph(project)["edges"][0]["context"]["reason"], "fixture")

    def test_homepage_pair_tracks_current_forward_relation(self):
        project = {}
        repository_trust.record_observation(project, edge(reverse=True, method="github_homepage"))
        project["relation_evidence"][0]["relation"]["predicate"] = "repository_homepage"
        self.assertEqual(trust_graph.graph(project)["edges"], [])
        repository_trust.record_observation(project, edge())
        self.assertEqual(len(trust_graph.auto_confirm_edges(trust_graph.graph(project)["edges"])), 2)
        repository_trust.record_observation(project, edge(classification="candidate"))
        self.assertEqual(len(trust_graph.graph(project)["edges"]), 1)

    def test_llm_omission_preserves_judgment_and_explicit_related_replaces_it(self):
        project = {}
        pair = {"service_url": SERVICE, "repo_url": REPO}
        evidence = {"repository_link_candidates": [pair]}
        registry.apply_repository_link_assessments(project, {"repository_link_assessments": [{**pair, "relationship": "same_project"}]}, evidence)
        registry.apply_repository_link_assessments(project, {"repository_link_assessments": []}, evidence)
        self.assertEqual(trust_graph.graph(project)["edges"][0]["classification"], "confirmed")
        registry.apply_repository_link_assessments(project, {"repository_link_assessments": [{**pair, "relationship": "related"}]}, evidence)
        self.assertEqual(trust_graph.graph(project)["edges"][0]["classification"], "candidate")

    def test_failed_page_fetch_keeps_previous_observation(self):
        project = {}
        repository_trust.record_observation(project, edge(source=f"service_probe:{SERVICE}", method="html_link"))
        before = copy.deepcopy(project)
        registry.service_probe_supports_project(project, SERVICE, {"fetch_error": "timeout", "status_code": 0})
        self.assertEqual(project, before)

    def test_saved_verdict_cannot_support_removed_relation(self):
        project = project_record.default_project_record("proj:current", "service_url", SERVICE)
        registry.add_manual_seed(project, "service_url", SERVICE, source="test")
        repository_trust.record_observation(project, edge())
        repository_trust.record_observation(project, edge(reverse=True))
        self.assertTrue(registry.trust_auto_confirm_identifier(project, "repo_url", REPO)["auto_confirm"])
        project["relation_evidence"] = []
        self.assertFalse(registry.url_has_current_trust(project, "repo_url", REPO))
        self.assertFalse(trust_graph.confirmed_by_trust_policy(project, "repo_url", REPO))

    def test_legacy_unbacked_edge_never_enters_current_graph(self):
        project = {"trust_graph": {"edges": [edge(method="first_party_link")]}}
        self.assertEqual(trust_graph.graph(project)["edges"], [])


class RepositoryTrustMigrationTest(TempRegistryDbTestCase):
    def test_archive_preserves_raw_observations_and_current_graph_without_restoring(self):
        project = self.make_project('proj:archive', 'service_url', SERVICE)
        known = edge(classification='candidate')
        repository_trust.record_observation(project, known)
        llm = edge(source='llm', method='llm_same_project')
        unknown = edge(source='legacy', method='html_link')
        unrelated = {**edge(), 'object': {'kind': 'service_url', 'value': 'https://other.example.com'},
                     'predicate': 'same_site_link'}
        project['trust_graph'] = {'edges': [known, llm, unknown, unrelated]}
        pair = {'service_url': SERVICE, 'repo_url': REPO}
        project['llm_source'] = {'generated_at': llm['observed_at'], 'evidence': {'repository_link_candidates': [pair]}}
        project['llm_output'] = {'repository_link_assessments': [{**pair, 'relationship': 'same_project'}]}
        conn = registry.open_db()
        archive_path = Path(self.tempdir.name) / 'archive.json'
        try:
            project_record.save_project(conn, project)
            # Unknown fields and duplicate observations must survive archival verbatim.
            state = json.loads(conn.execute('SELECT state_json FROM projects').fetchone()[0])
            originals = [e for e in state['evidence']['trust_graph']['edges'] if repository_trust.is_repository_edge(e)]
            originals[-1]['legacy_extra'] = {'original': True}
            state['evidence']['trust_graph']['edges'].append(copy.deepcopy(originals[-1]))
            conn.execute('UPDATE projects SET state_json=?', (json.dumps(state),))
            conn.commit()
            before = project_record.fetch_project(conn, project['project_key'])
            relations_before = list(conn.execute('SELECT * FROM project_relations'))
            identifiers_before = list(conn.execute('SELECT * FROM project_identifiers'))
            plan = repository_trust_migration.build_plan(conn)
            self.assertEqual(plan['counts'], {'current_relation': 1, 'reconstructable_judgment': 1, 'needs_review': 1})
            conn.execute('BEGIN IMMEDIATE')
            result = repository_trust_migration.apply_plan(conn, plan, archive_path=archive_path)
            conn.commit()
            self.assertEqual(result['archived_legacy_edges'], 4)
            self.assertEqual(result['restored_relations'], 0)
            archive = json.loads(archive_path.read_text())
            self.assertEqual(archive['projects'], [{'project_key': project['project_key'], 'edges': originals + [originals[-1]]}])
            self.assertEqual(archive['reviewed_plan'], plan)
            restored = next(e['relation'] for e in archive['reviewed_plan']['entries'] if 'relation' in e)
            self.assertEqual(restored['context']['input_pair'], pair)
            digest = archive.pop('archive_digest')
            self.assertEqual(digest, repository_trust_migration._digest(archive))
            after = project_record.fetch_project(conn, project['project_key'])
            self.assertEqual(trust_graph.graph(before), trust_graph.graph(after))
            self.assertEqual(list(conn.execute('SELECT * FROM project_relations')), relations_before)
            self.assertEqual(list(conn.execute('SELECT * FROM project_identifiers')), identifiers_before)
            self.assertEqual(repository_trust_migration.build_plan(conn)['counts'], {})
            self.assertEqual(trust_graph.stored_graph(after)['edges'], [unrelated])
        finally:
            conn.close()

    def test_archive_write_failure_leaves_database_unchanged(self):
        project = self.make_project('proj:archive-error', 'service_url', SERVICE)
        project['trust_graph'] = {'edges': [edge(source='legacy')]}
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            plan = repository_trust_migration.build_plan(conn)
            before = conn.execute('SELECT state_json FROM projects').fetchone()[0]
            path = Path(self.tempdir.name) / 'archive.json'
            conn.execute('BEGIN IMMEDIATE')
            with mock.patch.object(repository_trust_migration.os, 'fsync', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    repository_trust_migration.apply_plan(conn, plan, archive_path=path)
            conn.rollback()
            self.assertEqual(conn.execute('SELECT state_json FROM projects').fetchone()[0], before)
            saved = path.read_bytes()
            conn.execute('BEGIN IMMEDIATE')
            with self.assertRaises(FileExistsError):
                repository_trust_migration.apply_plan(conn, plan, archive_path=path)
            conn.rollback()
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(conn.execute('SELECT state_json FROM projects').fetchone()[0], before)
        finally:
            conn.close()

    def test_archive_rejects_stale_plan_before_writing_file(self):
        project = self.make_project('proj:archive-stale', 'service_url', SERVICE)
        project['trust_graph'] = {'edges': [edge(source='legacy')]}
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            plan = repository_trust_migration.build_plan(conn)
            project = project_record.fetch_project(conn, project['project_key'])
            project['trust_graph']['edges'] = []
            project_record.save_project(conn, project)
            conn.commit()
            path = Path(self.tempdir.name) / 'archive.json'
            conn.execute('BEGIN IMMEDIATE')
            with self.assertRaises(ValueError):
                repository_trust_migration.apply_plan(conn, plan, archive_path=path)
            conn.rollback()
            self.assertFalse(path.exists())
        finally:
            conn.close()

    def test_readme_exact_source_still_wins_when_alias_has_another_locator(self):
        project = self.make_project('proj:readme-exact', 'service_url', SERVICE)
        slug = REPO.removeprefix('https://github.com/')
        old = edge(reverse=True, source=f'github_readme:{slug}', method='readme_link')
        repository_trust.record_observation(project, {**old, 'locator': 'README'})
        repository_trust.record_observation(project, {
            **old, 'source': f'github_readme:{REPO}', 'locator': 'README.md', 'classification': 'candidate'})
        project['trust_graph'] = {'edges': [old]}
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            plan = repository_trust_migration.build_plan(conn)
            self.assertEqual(plan['counts'], {'current_relation': 1})
            self.assertEqual(plan['entries'][0]['current_edge']['source'], old['source'])
        finally:
            conn.close()

    def test_readme_source_formats_match_without_merging_other_sources(self):
        project = self.make_project("proj:readme-migration", "service_url", SERVICE)
        slug = REPO.removeprefix("https://github.com/")
        old = edge(reverse=True, source=f"github_readme:{slug}", method="readme_link")
        current = {**old, "source": f"github_readme:{REPO}", "locator": "README", "classification": "candidate"}
        repository_trust.record_observation(project, current)
        other = {**old, "source": "github_readme:another/repository"}
        independent = {**old, "source": f"github_readme:{slug}", "method": "html_link"}
        project["trust_graph"] = {"edges": [old, other, independent]}
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            plan = repository_trust_migration.build_plan(conn)
            self.assertEqual(plan['counts'], {'current_relation': 1, 'needs_review': 2})
            matched = next(e for e in plan['entries'] if e['category'] == 'current_relation')
            self.assertEqual(matched['basis'], 'readme_source_format_equivalence')
            self.assertEqual(matched['current_edge']['classification'], 'candidate')
            before = list(conn.execute('SELECT * FROM project_relations'))
            conn.execute('BEGIN IMMEDIATE')
            result = repository_trust_migration.apply_plan(conn, plan, archive_path=Path(self.tempdir.name) / 'archive.json')
            conn.commit()
            self.assertEqual(result['archived_legacy_edges'], 3)
            self.assertEqual(list(conn.execute('SELECT * FROM project_relations')), before)
        finally:
            conn.close()

    def test_history_restores_omitted_pair_without_rerunning_llm(self):
        from project_registry_core import storage
        project = self.make_project("proj:history", "service_url", SERVICE)
        llm = edge(source="llm", method="llm_same_project")
        homepage = edge(source="github", reverse=True, method="github_homepage")
        repository_trust.record_observation(project, homepage)
        project["relation_evidence"][0]["relation"]["predicate"] = "repository_homepage"
        project["trust_graph"] = {"edges": [llm, {**homepage, "method": "repository_homepage_pair"}]}
        pair = {"service_url": SERVICE, "repo_url": REPO}
        snapshot = {
            "llm_source": {"generated_at": llm["observed_at"], "evidence": {"repository_link_candidates": [pair]}},
            "llm_output": {"repository_link_assessments": [{**pair, "relationship": "same_project"}]},
        }
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.execute("INSERT INTO investigation_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                "run:historical", project["project_key"], "2026-09-03T09:00:00+00:00", "2026-09-03T10:00:00+00:00",
                1, "test", "{}", storage.encode_history_json_text(json.dumps(snapshot)), "{}", "{}",
            ))
            plan = repository_trust_migration.build_plan(conn)
            self.assertEqual(plan["counts"], {"reconstructable_judgment": 2})
            self.assertEqual(plan["entries"][0]["relation"]["context"]["migration_run_id"], "run:historical")
            snapshot["llm_source"]["evidence"]["repository_link_candidates"] = []
            conn.execute("UPDATE investigation_runs SET output_project_json=?", (json.dumps(snapshot),))
            self.assertEqual(repository_trust_migration.build_plan(conn)["counts"], {"needs_review": 2})
        finally:
            conn.close()

    def test_dry_run_classifies_and_apply_requires_archive_and_preserves_identifiers(self):
        project = self.make_project("proj:migration", "service_url", SERVICE)
        repository_trust.record_observation(project, edge(classification="candidate"))
        llm = edge(source=f"llm_repository_relationship:{SERVICE}", method="llm_same_project")
        unknown = edge(source="legacy_unknown", method="first_party_link")
        project["trust_graph"] = {"edges": [edge(), llm, unknown]}
        pair = {"service_url": SERVICE, "repo_url": REPO}
        project["llm_source"] = {"generated_at": llm["observed_at"], "evidence": {"repository_link_candidates": [pair]}}
        project["llm_output"] = {"repository_link_assessments": [{**pair, "relationship": "same_project", "reason": "saved judgment"}]}
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            identifiers = list(conn.execute("SELECT * FROM project_identifiers"))
            before = conn.total_changes
            plan = repository_trust_migration.build_plan(conn)
            self.assertEqual(conn.total_changes, before)
            self.assertEqual(plan["counts"], {"current_relation": 1, "reconstructable_judgment": 1, "needs_review": 1})
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(ValueError):
                repository_trust_migration.apply_plan(conn, plan)
            self.assertEqual(conn.total_changes, before)
            path = Path(self.tempdir.name) / 'archive.json'
            result = repository_trust_migration.apply_plan(conn, plan, archive_path=path)
            conn.commit()
            self.assertEqual(result['archived_legacy_edges'], 3)
            self.assertEqual(result['restored_relations'], 0)
            self.assertEqual(list(conn.execute("SELECT * FROM project_identifiers")), identifiers)
            state = json.loads(conn.execute("SELECT state_json FROM projects").fetchone()[0])
            self.assertEqual(len(state["evidence"]["trust_graph"]["edges"]), 0)
            remaining = repository_trust_migration.build_plan(conn)
            self.assertEqual(remaining["counts"], {})
            with self.assertRaises(ValueError):
                repository_trust_migration.apply_plan(conn, plan, archive_path=path)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
