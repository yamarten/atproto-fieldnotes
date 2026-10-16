from __future__ import annotations

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase
from project_registry_core import project_record, registry, tangled
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord
from project_registry_core.relations import relation_record


ROOT = "https://tangled.org/byjp.me/atproto-deeplink"
OWNER = "did:plc:ephkzpinhaqcabtkugtbzrwu"


class TangledRepositoryAttributionTest(ProjectRecordTestCase):
    def project(self):
        project = self.make_project("proj:attribution", "repo_url", ROOT)
        project.update(name="atproto-deeplink", project_repository_urls=[ROOT],
                       developer_accounts=[{"handle": "byjp.me", "did": OWNER, "source": "manual"}])
        return project

    def collect(self, project, records):
        return tangled.collect_account_repos(project, "byjp.me", tangled.TangledDependencies(
            now=lambda: "2026-10-16T00:00:00+00:00",
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
        self.assertEqual(len(project["relation_evidence"]), 8)
        self.assertEqual({x["relation"]["predicate"] for x in project["relation_evidence"]}, {"owns_repository"})
        self.assertTrue(all(not x["eligible_for_auto_confirm"] for x in project["trust_graph"]["edges"]))
        self.assertEqual(project["project_repository_urls"], [ROOT])

    def test_specific_name_and_exact_website_admit_candidates_without_content(self):
        project = self.project()
        project["project_repository_urls"] = []
        project["service_urls"] = ["https://app.invalid/demo?mode=one"]
        bundle = self.collect(project, [
            {"name": "atproto-deeplink", "description": "Project library"},
            {"name": "web-client", "description": "Project client", "website": "https://APP.invalid:443/demo?mode=one"},
            {"name": "other-client", "website": "https://app.invalid/demo?mode=two"},
        ])
        registry.apply_tangled_evidence(project, bundle)
        self.assertEqual(project["candidate_project_repository_urls"], [ROOT, "https://tangled.org/byjp.me/web-client"])
        self.assertEqual(project["summary_candidates"], [])
        self.assertEqual(project["name_candidates"], [])

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
