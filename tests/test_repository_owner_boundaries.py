from __future__ import annotations

import urllib.parse
from unittest import mock

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry
from enrichment_io import EnrichmentIO
from project_registry_core import project_record, tangled
from project_registry_core.application import enrichment
from project_registry_core.jobs import OperationRequest
from project_registry_core.repositories import repo_urls


class RepositoryOwnerBoundariesTest(ProjectRecordTestCase):
    def test_owner_identity_includes_the_forge_and_normalizes_its_url_aliases(self):
        urls = ["https://github.com/alice/app", "https://tangled.org/alice/app",
                "https://codeberg.org/alice/app", "https://gitlab.com/alice/app",
                "https://git.sr.ht/~alice/app"]
        self.assertEqual(len({repo_urls.repo_owner_identity(url) for url in urls}), len(urls))
        self.assertEqual(repo_urls.repo_owner_identity("https://github.com/Alice/Other"), ("github.com", "alice"))
        self.assertEqual(repo_urls.repo_owner_identity("https://tangled.sh/@alice/app"), ("tangled.org", "alice"))
        self.assertEqual(repo_urls.repo_owner_identity("https://tangled.org/did:plc:AbC/app"), ("did", "did:plc:AbC"))
        self.assertIsNone(repo_urls.repo_owner_identity("https://tangled.org/did:plc:/app"))
        self.assertIsNone(repo_urls.repo_owner_identity("https://example.com/alice/app"))
        self.assertIsNone(repo_urls.repo_owner_identity("https://github.com/alice"))

    def test_global_did_identity_does_not_depend_on_the_hosting_service(self):
        first = "https://tangled.org/did:plc:AbC/app"
        second = "https://another-forge.example/did:plc:AbC/app"
        self.assertIsNone(repo_urls.repo_owner_identity(second))
        # Simulate a future URL adapter accepting a second ATProto forge. This
        # does not add that forge to production or change its URL normalizer.
        with mock.patch.object(repo_urls, "is_supported_repo_url", return_value=True):
            self.assertEqual(repo_urls.repo_owner_identity(first), repo_urls.repo_owner_identity(second))
            self.assertNotEqual(repo_urls.repo_identity_key(first), repo_urls.repo_identity_key(second))
            self.assertNotEqual(repo_urls.repo_owner_identity(second),
                                repo_urls.repo_owner_identity(second.replace("AbC", "abc")))

    def test_global_did_owner_matching_still_requires_project_evidence(self):
        project = self.project("https://tangled.org/did:plc:AbC/old-implementation")
        same = "https://another-forge.example/did:plc:AbC/field-notes"
        with mock.patch.object(repo_urls, "is_supported_repo_url", return_value=True):
            self.assertTrue(registry.repo_candidate_allowed_for_project(project, same))
            self.assertFalse(registry.repo_candidate_allowed_for_project(project, same.replace("AbC", "other")))
            self.assertFalse(registry.repo_candidate_allowed_for_project(project, same.replace("field-notes", "unrelated")))
            self.assertFalse(registry.confirm_project_repository_url_with_trust(project, same, "test"))

    def test_unresolved_handle_and_did_are_not_comparable_owner_identifiers(self):
        for anchor, candidate in [
            ("https://tangled.org/alice.example/old-implementation", "https://tangled.org/did:plc:AbC/fieldnotes"),
            ("https://tangled.org/did:plc:AbC/old-implementation", "https://tangled.org/alice.example/fieldnotes"),
        ]:
            with self.subTest(anchor=anchor, candidate=candidate):
                project = self.project(anchor)
                # The independently matching project name permits a candidate;
                # unresolved handle/DID values neither support nor contradict it.
                self.assertTrue(registry.repo_candidate_allowed_for_project(project, candidate))
                self.assertNotEqual(repo_urls.repo_owner_identity(anchor), repo_urls.repo_owner_identity(candidate))

    def project(self, repo="https://github.com/original/old-implementation"):
        project = self.make_project("proj:owner", "repo_url", repo)
        registry.add_manual_seed(project, "repo_url", repo, source="test")
        project["name"] = "Field Notes"
        return project

    def test_other_forges_do_not_supply_owner_agreement_or_disagreement(self):
        for anchor, candidate in [
            ("https://github.com/original/old-implementation", "https://codeberg.org/different/field-notes"),
            ("https://tangled.org/original.example/old-implementation", "https://github.com/different/field-notes"),
            ("https://codeberg.org/alice/old-implementation", "https://gitlab.com/alice/field-notes"),
            ("https://gitlab.com/alice/old-implementation", "https://git.sr.ht/~different/field-notes"),
        ]:
            with self.subTest(anchor=anchor, candidate=candidate):
                project = self.project(anchor)
                self.assertNotEqual(repo_urls.repo_owner_identity(anchor), repo_urls.repo_owner_identity(candidate))
                self.assertTrue(registry.repo_candidate_allowed_for_project(project, candidate))
                unrelated = candidate.rsplit("/", 1)[0] + "/unrelated"
                self.assertFalse(registry.repo_candidate_allowed_for_project(project, unrelated))

    def test_same_forge_owner_filter_and_direct_relation_keep_their_meaning(self):
        project = self.project()
        allowed = "https://github.com/original/field-notes"
        other = "https://github.com/different/field-notes"
        self.assertTrue(registry.repo_candidate_allowed_for_project(project, allowed))
        self.assertFalse(registry.repo_candidate_allowed_for_project(project, other))
        # An owner on another forge with this label does not rescue that mismatch.
        registry.add_manual_seed(project, "repo_url", "https://codeberg.org/different/other", source="test")
        self.assertFalse(registry.repo_candidate_allowed_for_project(project, other))
        project["relation_evidence"] = [{"relation": {
            "subject": {"type": "website", "value": "https://app.example"},
            "predicate": "project_repository", "object": {"type": "repo", "value": other},
            "locator": "https://app.example", "method": "bounded_first_party_link"},
            "source": "service_page:https://app.example", "classification": "confirmed",
            "context": {"subject_is_exact_component": True}}]
        self.assertTrue(registry.repo_candidate_allowed_for_project(project, other))

    def test_tangled_name_basis_is_the_same_during_admission_and_cleanup(self):
        project = self.project("https://tangled.org/original.example/old-implementation")
        repo = "https://tangled.org/different.example/field-notes"
        self.assertEqual(tangled.repository_match_reason(project, repo, {"name": "field-notes"}), "project_name_exact")
        self.assertTrue(registry.repo_candidate_allowed_for_project(project, repo, repository_metadata={"name": "field-notes"}))
        self.assertTrue(registry.repo_candidate_allowed_for_project(project, repo))
        project["name"] = "Renamed project"
        self.assertFalse(registry.repo_candidate_allowed_for_project(project, repo))

    def test_inactive_repository_does_not_supply_a_trusted_owner(self):
        anchor = "https://github.com/original/old-implementation"
        for field in ("blocked_identifiers", "deferred_identifiers"):
            with self.subTest(field=field):
                project = self.project(anchor)
                project[field] = [{"type": "repo_url", "value": anchor, "expires_at": "2099-01-01T00:00:00Z"}]
                self.assertEqual(registry.trusted_repo_owner_identities(project), set())
                self.assertTrue(registry.repo_candidate_allowed_for_project(project, "https://github.com/different/field-notes"))
                self.assertFalse(registry.repo_candidate_allowed_for_project(project, "https://github.com/original/unrelated"))

    def test_owner_label_is_not_a_domain_handle_or_project_identity_bonus(self):
        project = self.project("https://tangled.org/alice.example/old-implementation")
        project.update(name="Unrelated", authority_domains=["alice.example"], bluesky_handles=["alice.example"])
        # No text or URL metadata matches the project; only the owner's label does.
        self.assertEqual(registry.github_repo_relevance(project, {"url": "https://github.com/alice/elsewhere"}), 0)
        registry.add_manual_seed(project, "repo_url", "https://github.com/alice/known", source="test")
        self.assertEqual(registry.github_repo_relevance(project, {"url": "https://github.com/alice/elsewhere"}), 6)

    def test_owner_label_does_not_reclassify_a_lexicon_as_service_specific(self):
        project = self.make_project("proj:lexicon-owner", "nsid", "org.alice.note")
        project.update(name="Vocabulary", category="lexicon",
                       project_repository_urls=["https://github.com/alice/vocabulary"],
                       service_url_candidates=["https://independent.example"])
        project_record.reclassify_service_specific_lexicon(project)
        self.assertEqual((project["lexicon_scope"], project["category"]), ("unknown", "lexicon"))


class RepositoryOwnerPersistenceTest(TempRegistryDbTestCase):
    def test_other_forge_candidate_survives_save_load_without_confirmation(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        project = self.make_project("proj:notes", "repo_url", "https://github.com/original/field-notes")
        registry.add_manual_seed(project, "repo_url", "https://github.com/original/field-notes", source="test")
        repo = "https://codeberg.org/different/field-notes"
        registry.add_candidate_project_repository_url(project, repo, "search")
        for _ in range(2):
            project = registry.finalize_project_shape(project)
            project_record.save_project(conn, project)
            conn.commit()
            project = project_record.fetch_project(conn, project["project_key"])
            self.assertIn(repo, project["candidate_project_repository_urls"])
            self.assertNotIn(repo, project["project_repository_urls"])
            self.assertFalse(registry.confirm_project_repository_url_with_trust(project, repo, "test"))

    def test_normal_enrich_preserves_linkat_candidate_and_does_not_inspect_or_adopt_it(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        github, site, repo = "https://github.com/mkizka/linkat", "https://linkat.blue", "https://tangled.org/linkat.blue/linkat"
        owner, pds = "did:plc:officialowner", "https://pds.example"
        project = self.make_project("proj:linkat", "repo_url", github)
        registry.add_manual_seed(project, "repo_url", github, source="test")
        registry.add_manual_seed(project, "service_url", site, source="test")
        project_record.add_developer_account(project, "linkat.blue", "test", did=owner)
        project["name"] = "Linkat"
        project_record.save_project(conn, project)
        fragment = self.make_project("proj:tangled-fragment", "repo_url", repo)
        registry.add_manual_seed(fragment, "repo_url", repo, source="tangled_jetstream")
        fragment["name"] = "Linkat"
        project_record.save_project(conn, fragment)
        conn.commit()
        io = EnrichmentIO()
        io.github_repo(github, readme="# Linkat", homepage=site)
        io.page(site, html=f'<title>Linkat</title><footer><a href="{github}">Source code</a></footer>')
        io.json("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor=linkat.blue", {"did": owner, "handle": "linkat.blue"})
        io.json("https://plc.directory/" + owner, {"id": owner, "service": [{"id": "#atproto_pds", "type": "AtprotoPersonalDataServer", "serviceEndpoint": pds}]})
        io.json(pds + "/xrpc/com.atproto.repo.listRecords?" + urllib.parse.urlencode({"repo": owner, "collection": "sh.tangled.repo", "limit": 100}),
                {"records": [{"uri": f"at://{owner}/sh.tangled.repo/source", "value": {"name": "linkat", "description": "Candidate content must not update the project"}}]})
        with io:
            result = enrichment.run(conn, OperationRequest.create("enrich", execute=True, parameters={"project_key": project["project_key"]}))
        self.assertEqual(result.status, "completed")
        saved = project_record.fetch_project(conn, project["project_key"])
        self.assertIn(repo, saved["candidate_project_repository_urls"])
        self.assertNotIn(repo, saved["project_repository_urls"])
        self.assertNotIn("Candidate content", saved["summary"])
        comparison = next(item for item in saved["duplicate_candidates"] if item["project_key"] == fragment["project_key"])
        self.assertIn("candidate_confirmed_identifiers", comparison["reasons"])
        self.assertFalse(comparison["auto_merge_ready"])
        self.assertIsNotNone(project_record.fetch_project(conn, fragment["project_key"]))
        self.assertEqual(conn.execute("SELECT count(*) FROM project_key_aliases").fetchone()[0], 0)
        self.assertFalse(any(call.args[0].startswith(repo) for call in io.http.call_args_list))
        saved = registry.finalize_project_shape(saved)
        project_record.save_project(conn, saved)
        self.assertIn(repo, project_record.fetch_project(conn, project["project_key"])["candidate_project_repository_urls"])
