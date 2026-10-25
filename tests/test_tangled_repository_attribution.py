from __future__ import annotations

import urllib.parse
from unittest import mock

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase
from enrichment_io import EnrichmentIO
from project_registry_core import project_record, registry, source_sync, tangled, tangled_identity
from project_registry_core.application import enrichment
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord
from project_registry_core.jobs import OperationRequest
from project_registry_core.relations import relation_record


ROOT = "https://tangled.org/byjp.me/atproto-deeplink"
OWNER = "did:plc:ephkzpinhaqcabtkugtbzrwu"


class TangledRepositoryAttributionTest(ProjectRecordTestCase):
    def test_official_account_ownership_and_exact_name_do_not_confirm_repository(self):
        github = "https://github.com/example/atproto-deeplink"
        site = "https://byjp.me/atproto-deeplink"
        project = self.make_project("proj:official-account", "repo_url", github)
        registry.add_manual_seed(project, "repo_url", github, source="test")
        registry.add_manual_seed(project, "service_url", site, source="test")
        project["name"] = "atproto-deeplink"
        project["developer_accounts"] = [{"handle": "byjp.me", "did": OWNER, "source": "test"}]
        official = EvidenceBundle(project_key=project["project_key"], collected_at="2026-10-25T00:00:00Z")
        official.add_record(relation_record(
            source=f"service_page:{site}", classification="confirmed", observed_at=official.collected_at,
            subject_type="website", subject_value=site, predicate="official_account",
            object_type="handle", object_value="byjp.me", locator=site,
            method="bounded_first_party_link", context={"policy_version": "official-account-v2"},
        ))
        registry.apply_tangled_evidence(project, official)
        registry.apply_tangled_evidence(project, self.collect(project, [{"name": "atproto-deeplink"}]))
        self.assertIn(ROOT, project["candidate_project_repository_urls"])
        project = registry.finalize_project_shape(project)
        registry.reconcile_trust_supported_url_candidates(project)
        self.assertEqual(project["project_repository_urls"], [github])

    def project(self):
        project = self.make_project("proj:attribution", "repo_url", ROOT)
        project.update(name="atproto-deeplink", project_repository_urls=[ROOT],
                       developer_accounts=[{"handle": "byjp.me", "did": OWNER, "source": "manual"}])
        return project

    def collect(self, project, records, observed_at="2026-10-16T00:00:00+00:00"):
        return tangled.collect_account_repos(project, "byjp.me", tangled.TangledDependencies(
            now=lambda: observed_at,
            resolve_handle=lambda _: {"did": OWNER},
            resolve_did_document=lambda _: {},
            extract_pds_url=lambda _: "https://pds.invalid",
            list_records=lambda *_: {"records": [
                {"uri": f"at://{OWNER}/sh.tangled.repo/{index}", "value": value}
                for index, value in enumerate(records)
            ]},
        ))

    def test_owner_portfolio_does_not_supply_project_candidates(self):
        # Observed candidate names from proj:0cdbc43b85cf; descriptions below
        # isolate the shared-word trigger rather than quote unavailable records.
        names = ["backup.ist", "webtiles.byjp.me", "atproto-gaming-dotnet", "where-its-AT",
                 "slay-the-spire-ii-atproto", "slices", "every-day-calendar", "atplay"]
        project = self.project()
        records = [{"name": name, "description": "An atproto utility", "website": "https://other.invalid"}
                   for name in names]
        bundle = self.collect(project, records)
        registry.apply_tangled_evidence(project, bundle)
        self.assertFalse(bundle.failures)
        self.assertEqual(project["candidate_project_repository_urls"], [])
        for field in ("name_candidates", "summary_candidates", "tagline_candidates"):
            self.assertEqual(project[field], [])
        self.assertEqual(project_record.service_url_candidate_values(project), [])
        self.assertEqual(len([x for x in project["relation_evidence"] if x["relation"]["predicate"] == "owns_repository"]), 8)
        self.assertTrue(all(not x["eligible_for_auto_confirm"] for x in project["trust_graph"]["edges"]))
        self.assertEqual(project["project_repository_urls"], [ROOT])

    def test_specific_name_and_exact_website_admit_candidates_without_content(self):
        project = self.project()
        project["project_repository_urls"] = []
        project["service_urls"] = ["https://app.invalid/demo?mode=one"]
        registry.add_confirmed_identifier_decision(project, "service_url", project["service_urls"][0], source="test")
        bundle = self.collect(project, [
            {"name": "atproto-deeplink", "description": "Project library"},
            {"name": "web-client", "description": "Project client", "website": "https://APP.invalid:443/demo?mode=one"},
            {"name": "other-client", "website": "https://app.invalid/demo?mode=two"},
        ])
        registry.apply_tangled_evidence(project, bundle)
        self.assertEqual(project["candidate_project_repository_urls"], [ROOT, "https://tangled.org/byjp.me/web-client"])
        self.assertEqual(project["summary_candidates"], [])
        self.assertEqual(project["name_candidates"], [])
        finalized = registry.finalize_project_shape(project)
        self.assertEqual(finalized["candidate_project_repository_urls"], [ROOT, "https://tangled.org/byjp.me/web-client"])

    def test_website_candidate_is_rechecked_after_observation_or_anchor_changes(self):
        project = self.project()
        registry.add_manual_seed(project, "service_url", "https://app.invalid/demo", source="test")
        registry.apply_tangled_evidence(project, self.collect(project, [
            {"name": "web-client", "website": "https://app.invalid/demo"}]))
        self.assertIn("https://tangled.org/byjp.me/web-client", registry.finalize_project_shape(project)["candidate_project_repository_urls"])
        update = self.collect(project, [{"name": "web-client", "website": "https://other.invalid"}], "2026-10-17T00:00:00+00:00")
        registry.apply_tangled_evidence(project, update)
        self.assertNotIn("https://tangled.org/byjp.me/web-client", registry.finalize_project_shape(project)["candidate_project_repository_urls"])
        registry.apply_tangled_evidence(project, self.collect(project, [{"name": "web-client", "website": "https://app.invalid/demo"}]))
        self.assertNotIn("https://tangled.org/byjp.me/web-client", registry.finalize_project_shape(project)["candidate_project_repository_urls"])
        registry.apply_tangled_evidence(project, self.collect(project, [{"name": "web-client"}], "2026-10-18T00:00:00+00:00"))
        registry.apply_tangled_evidence(project, update)
        self.assertFalse(any(item["relation"]["method"] == "pds_repo_website" for item in project["relation_evidence"]))

        fresh = self.project()
        registry.add_manual_seed(fresh, "service_url", "https://app.invalid/demo", source="test")
        registry.apply_tangled_evidence(fresh, self.collect(fresh, [{"name": "web-client", "website": "https://app.invalid/demo"}]))
        fresh["blocked_identifiers"] = [{"type": "service_url", "value": "https://app.invalid/demo"}]
        self.assertEqual(registry.finalize_project_shape(fresh)["candidate_project_repository_urls"], [])

    def test_accepted_repository_supplies_content_and_owner(self):
        project = self.project()
        project["name"] = "Different display name"
        project["developer_accounts"] = []
        registry.apply_tangled_evidence(project, self.collect(project, [
            {"name": "atproto-deeplink", "description": "Project library", "website": "https://app.invalid/demo"},
            {"name": "unrelated", "description": "Other library", "website": "https://other.invalid"},
        ]))
        self.assertEqual([x["value"] for x in project["summary_candidates"]], ["Project library"])
        self.assertEqual(project["summary_candidates"][0]["source"], f"tangled_pds:byjp.me:{ROOT}")
        self.assertEqual([x["value"] for x in project["name_candidates"]], ["atproto-deeplink"])
        self.assertEqual(project_record.service_url_candidate_values(project), ["https://app.invalid/demo"])
        self.assertEqual(project["developer_accounts"][0]["did"], OWNER)

    def test_legacy_score_and_relation_do_not_readmit_owner_portfolio(self):
        project = self.project()
        bundle = self.collect(project, [{"name": "unrelated", "description": "atproto tools"}])
        for record in bundle.records:
            if record.kind == "tangled_repo_snapshot":
                record.value["score"] = 999
            elif record.kind == "relation":
                record.value["predicate"] = "project_repository"
        registry.apply_tangled_evidence(project, bundle)
        self.assertEqual(project["candidate_project_repository_urls"], [])
        self.assertEqual(project["relation_evidence"][0]["relation"]["predicate"], "owns_repository")

    def test_refresh_replaces_legacy_ownership_shape_without_duplicate_claim(self):
        project = self.project()
        bundle = self.collect(project, [{"name": "unrelated"}])
        old = bundle.records[1].to_dict()
        old["value"]["predicate"] = "project_repository"
        project["relation_evidence"] = [{"relation": old["value"], "source": old["source"],
                                        "classification": "confirmed", "observed_at": old["observed_at"]}]
        registry.apply_tangled_evidence(project, bundle)
        self.assertEqual(len(project["relation_evidence"]), 1)
        self.assertEqual(project["relation_evidence"][0]["relation"]["predicate"], "owns_repository")
        self.assertEqual(project["relation_evidence"][0]["context"]["legacy_predicate"], "project_repository")

    def test_rejected_repository_does_not_become_developer_or_supply_inspection_text(self):
        project = self.project()
        project["developer_accounts"] = []
        bundle = self.collect(project, [{"name": "unrelated", "description": "atproto tools"}])
        bundle.add_record(EvidenceRecord(kind="tangled_repo_inspection", source="tangled_repo:byjp.me/unrelated",
            classification="candidate", observed_at=bundle.collected_at,
            value={"repo_url": "https://tangled.org/byjp.me/unrelated",
                   "repo": {"name": "unrelated", "description": "Other library"},
                   "readme_excerpt": "Other project", "readme_urls": ["https://other.invalid"]}))
        registry.apply_tangled_evidence(project, bundle)
        self.assertEqual(project["developer_accounts"], [])
        self.assertEqual(project["name_candidates"], [])
        self.assertEqual(project["summary_candidates"], [])
        self.assertEqual(project_record.service_url_candidate_values(project), [])

    def test_owner_alone_cannot_admit_repository_through_other_paths(self):
        project = self.project()
        for url in ("https://tangled.org/byjp.me/unrelated", "https://github.com/byjp.me/unrelated"):
            with self.subTest(url=url):
                registry.add_candidate_project_repository_url(project, url, "search")
                self.assertNotIn(url, project["candidate_project_repository_urls"])
        registry.apply_llm_output(project, {"project_repository_urls": ["https://tangled.org/byjp.me/unrelated"]}, {}, {})
        self.assertEqual(project["candidate_project_repository_urls"], [])

    def test_guards_override_metadata_and_accepted_content(self):
        for field in ("blocked_identifiers", "deferred_identifiers"):
            with self.subTest(field=field):
                project = self.project()
                project[field] = [{"type": "repo_url", "value": ROOT, "expires_at": "2099-01-01T00:00:00+00:00"}]
                registry.apply_tangled_evidence(project, self.collect(project, [{"name": "atproto-deeplink", "description": "Blocked content"}]))
                self.assertEqual(project["summary_candidates"], [])
                self.assertEqual(project["candidate_project_repository_urls"], [])

    def test_exact_component_relation_still_admits_differently_named_repository(self):
        project = self.project()
        url = "https://github.com/other-owner/client"
        project["relation_evidence"] = [{"relation": {
            "subject": {"type": "website", "value": "https://app.invalid"},
            "predicate": "project_repository", "object": {"type": "repo", "value": url},
            "locator": "https://app.invalid", "method": "bounded_first_party_link"},
            "source": "service_page:https://app.invalid", "classification": "confirmed",
            "context": {"subject_is_exact_component": True}}]
        registry.add_candidate_project_repository_url(project, url, "service_page:https://app.invalid")
        self.assertIn(url, project["candidate_project_repository_urls"])


class TangledOwnershipStorageTest(TempRegistryDbTestCase):
    def official_account_project(self):
        github, site = "https://github.com/example/atproto-deeplink", "https://byjp.me/atproto-deeplink"
        project = self.make_project("proj:official-account", "repo_url", github)
        registry.add_manual_seed(project, "repo_url", github, source="test")
        registry.add_manual_seed(project, "service_url", site, source="test")
        project["name"] = "atproto-deeplink"
        record = relation_record(
            source=f"service_page:{site}", classification="confirmed", observed_at="2026-10-25T00:00:00Z",
            subject_type="website", subject_value=site, predicate="official_account",
            object_type="handle", object_value="byjp.me", locator=site,
            method="bounded_first_party_link", context={"policy_version": "official-account-v2"},
        )
        registry.apply_tangled_evidence(project, EvidenceBundle(
            project_key=project["project_key"], collected_at=record.observed_at, records=[record],
        ))
        return project

    def test_normal_sync_does_not_attach_by_official_owner_and_name(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        target = self.official_account_project()
        project_record.save_project(conn, target)
        conn.commit()
        entry = {"owner_did": OWNER, "rkey": "source", "seq": 101, "operation": "create",
                 "record": {"name": "atproto-deeplink", "description": "atproto application"},
                 "atproto_relevant": True}
        with mock.patch.object(source_sync, "fetch_tangled_jetstream_snapshot", return_value={
            "events": [entry], "sealed_tip_seq": 101, "last_event_seq": 101,
        }), mock.patch.object(tangled_identity, "resolve_owner_handle", return_value="byjp.me"):
            result = source_sync.sync_tangled_repos(conn, execute=True)
        self.assertEqual((result["created"], result["matched"]), (1, 0))
        saved = project_record.fetch_project(conn, target["project_key"])
        self.assertNotIn(ROOT, saved["project_repository_urls"])
        self.assertNotEqual(result["results"][0]["project_key"], target["project_key"])

    def test_normal_enrich_does_not_merge_by_official_owner_and_name(self):
        conn = registry.open_db()
        self.addCleanup(conn.close)
        project_record.save_project(conn, self.official_account_project())
        source = self.make_project("proj:fragment", "repo_url", ROOT)
        registry.add_manual_seed(source, "repo_url", ROOT, source="tangled_jetstream")
        project_record.add_developer_account(source, "byjp.me", "tangled_jetstream", did=OWNER)
        project_record.save_project(conn, source)
        conn.commit()
        io = EnrichmentIO()
        io.page(ROOT, html='<title>byjp.me/atproto-deeplink at main &middot; Tangled</title>')
        io.page(ROOT + "/raw/main/README.md", html="# atproto-deeplink\nAn application.")
        io.json("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor=byjp.me", {"did": OWNER, "handle": "byjp.me"})
        io.json("https://plc.directory/" + OWNER, {"id": OWNER, "alsoKnownAs": ["at://byjp.me"],
            "service": [{"id": "#atproto_pds", "type": "AtprotoPersonalDataServer", "serviceEndpoint": "https://pds.example"}]})
        io.json("https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=byjp.me", {"did": OWNER})
        io.json("https://pds.example/xrpc/com.atproto.repo.listRecords?" + urllib.parse.urlencode({"repo": OWNER, "collection": "sh.tangled.repo", "limit": 100}),
            {"records": [{"uri": f"at://{OWNER}/sh.tangled.repo/source", "value": {"name": "atproto-deeplink"}}]})
        with io:
            result = enrichment.run(conn, OperationRequest.create("enrich", execute=True,
                parameters={"project_key": "proj:fragment"}))
        self.assertEqual(result.status, "completed")
        self.assertIsNotNone(project_record.fetch_project(conn, "proj:fragment"))
        self.assertEqual(conn.execute("SELECT count(*) FROM project_key_aliases").fetchone()[0], 0)
        self.assertNotIn(ROOT, project_record.fetch_project(conn, "proj:official-account")["project_repository_urls"])

    def test_exact_website_candidate_survives_save_fetch_without_becoming_confirmed(self):
        project = self.make_project("proj:homepage", "service_url", "https://app.invalid/demo")
        registry.add_manual_seed(project, "service_url", "https://app.invalid/demo", source="test")
        project["name"] = "Different project name"
        repo = "https://tangled.org/byjp.me/web-client"
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-10-16T00:00:00+00:00")
        bundle.add_record(EvidenceRecord(kind="tangled_repo_snapshot", source="tangled_pds:byjp.me",
            classification="candidate", observed_at=bundle.collected_at,
            value={"owner_handle": "byjp.me", "owner_did": OWNER, "repo_url": repo,
                   "record": {"name": "web-client", "website": "https://app.invalid/demo", "description": "Candidate content"}}))
        registry.apply_tangled_evidence(project, bundle)
        conn = registry.open_db()
        try:
            project_record.save_project(conn, registry.finalize_project_shape(project))
            fetched = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(fetched["candidate_project_repository_urls"], [repo])
            self.assertEqual(fetched["project_repository_urls"], [])
            self.assertEqual(fetched["summary_candidates"], [])
            self.assertTrue(registry.repo_candidate_allowed_for_project(fetched, repo))
            self.assertEqual(conn.execute("SELECT classification FROM project_identifiers WHERE project_key=? AND kind='repo_url' AND value=?", (project["project_key"], repo)).fetchone()[0], "candidate")
            project_record.save_project(conn, registry.finalize_project_shape(fetched))
            self.assertEqual(project_record.fetch_project(conn, project["project_key"])["candidate_project_repository_urls"], [repo])
        finally:
            conn.close()

    def test_ownership_roundtrip_does_not_become_project_repository_identifier(self):
        project = self.make_project("proj:ownership", "repo_url", ROOT)
        project["project_repository_urls"] = [ROOT]
        project["name"] = "atproto-deeplink"
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-10-16T00:00:00+00:00")
        other = "https://tangled.org/byjp.me/other"
        bundle.add_record(relation_record(source="tangled_pds:byjp.me", classification="confirmed",
            observed_at=bundle.collected_at, subject_type="did", subject_value=OWNER,
            predicate="owns_repository", object_type="repo", object_value=other,
            locator=f"at://{OWNER}/sh.tangled.repo/other", method="pds_list_records"))
        registry.apply_tangled_evidence(project, bundle)
        conn = registry.open_db()
        try:
            project_record.save_project(conn, registry.finalize_project_shape(project))
            fetched = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(fetched["project_repository_urls"], [ROOT])
            self.assertEqual(fetched["candidate_project_repository_urls"], [])
            row = conn.execute("SELECT predicate,classification,locator FROM project_relations WHERE project_key=?", (project["project_key"],)).fetchone()
            self.assertEqual(tuple(row), ("owns_repository", "confirmed", f"at://{OWNER}/sh.tangled.repo/other"))
            self.assertIsNone(conn.execute("SELECT 1 FROM project_identifiers WHERE project_key=? AND kind='repo_url' AND value=?", (project["project_key"], other)).fetchone())
        finally:
            conn.close()
