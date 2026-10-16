from __future__ import annotations

import json
from pathlib import Path

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import developer_links, integration, project_record, service
from project_registry_core.application import presentation
from project_registry_core.evidence import EvidenceBundle, EvidenceRecord
from project_registry_core.identity import identifiers


SITE = "https://maker.example"
NOW = "2026-10-16T12:00:00Z"
CREDIT = '<p>Built by <a href="https://bsky.app/profile/author.example">Alice</a></p>'


def probe(html: str, **fields) -> dict:
    return {"status_code": 200, "final_url": SITE, "title": "Maker", "description": "AT Protocol app",
            "html": html, "urls": identifiers.extract_urls(html), "links": registry.extract_links(html, SITE),
            "service_status": "live", "service_status_confidence": "medium", "service_status_evidence": ["http_200"], **fields}


def apply(project, html, *, url=SITE, observed_at=NOW, **fields):
    bundle = service.collect_service_probe_evidence(project["project_key"], url, probe(html, **fields),
                                                   observed_at=observed_at, supports_project=True)
    registry.apply_service_evidence(project, bundle)
    return bundle


class DeveloperAccountLinksTest(TempRegistryDbTestCase):
    def test_role_boundary_fixtures(self):
        cases = json.loads((Path(__file__).parent / "fixtures/project_registry/developer-account-links.json").read_text())["cases"]
        for case in cases:
            with self.subTest(case=case["name"]):
                project = self.make_project("proj:roles", "service_url", SITE)
                project["service_urls"] = [SITE]
                apply(project, case["html"])
                expected = [{item["kind"]: item["value"], "source": f"developer_page:{SITE}"} for item in case["expected"]]
                self.assertEqual(project["candidate_developer_accounts"], expected)
                self.assertEqual(project["developer_accounts"], [])

    def test_graze_style_feed_and_json_remain_observations_without_developer_roles(self):
        project = self.make_project("proj:graze")
        html = '<blockquote><a href="https://bsky.app/profile/customer.bsky.social/feed/myfeed">Customer feed</a></blockquote>'
        html += '<script type="application/json">{"example":"https://bsky.app/profile/did:plc:placeholder/post/example"}</script>'
        bundle = apply(project, html)
        snapshot = next(record.value for record in bundle.records if record.kind == "service_verification")
        self.assertTrue(any("placeholder" in url for url in snapshot["urls"]))
        self.assertEqual(snapshot["developer_account_observations"]["accounts"], [])
        self.assertEqual(project["candidate_developer_accounts"], [])
        self.assertEqual(project["developer_accounts"], [])
        self.assertIn("https://bsky.app/profile/customer.bsky.social/feed/myfeed", project["evidence_urls"])

    def test_full_html_credit_survives_saved_html_budget_and_roundtrip(self):
        project = self.make_project("proj:latecredit")
        bundle = apply(project, "<div>" + "x" * 20000 + "</div>" + CREDIT)
        value = next(record.value for record in bundle.records if record.kind == "service_verification")
        self.assertNotIn("Built by", value["html_extract"])
        observation = value["developer_account_observations"]["accounts"][0]["observations"][0]
        self.assertEqual(observation["locator"], "https://bsky.app/profile/author.example")
        self.assertIn("Built by", observation["container_text"])
        with registry.open_db() as conn:
            project_record.save_project(conn, project)
            saved = project_record.fetch_project(conn, project["project_key"])
            self.assertEqual(saved["candidate_developer_accounts"], project["candidate_developer_accounts"])
            self.assertEqual(saved["service_verification_results"][0]["developer_account_observations"], value["developer_account_observations"])

    def test_successful_refetch_replaces_page_candidates_and_preserves_other_witnesses(self):
        project = self.make_project("proj:replace")
        project["developer_accounts"] = [{"handle": "confirmed.example", "source": "manual"}]
        project["candidate_developer_accounts"] = [
            {"handle": "customer.example", "source": f"service_raw_url:{SITE}"},
            {"did": "did:plc:placeholder", "source": f"service_page:{SITE}"},
            {"handle": "other.example", "source": f"service_raw_url:{SITE}/other"},
        ]
        apply(project, CREDIT)
        self.assertEqual({item.get("handle") for item in project["candidate_developer_accounts"]}, {"author.example", "other.example"})
        apply(project, "<p>No developer credits.</p>", observed_at="2026-10-16T13:00:00Z")
        self.assertEqual(project["candidate_developer_accounts"], [{"handle": "other.example", "source": f"service_raw_url:{SITE}/other"}])
        self.assertEqual(project["developer_accounts"], [{"handle": "confirmed.example", "source": "manual"}])

    def test_failed_incomplete_and_legacy_evidence_do_not_erase_candidates(self):
        project = self.make_project("proj:failure")
        apply(project, CREDIT)
        expected = list(project["candidate_developer_accounts"])
        apply(project, "", status_code=0, fetch_error="timeout")
        self.assertEqual(project["candidate_developer_accounts"], expected)
        value = probe("")
        value["html_extract"] = value.pop("html")
        legacy = EvidenceBundle(project_key=project["project_key"], collected_at=NOW, records=[EvidenceRecord(
            kind="service_verification", source=f"service_static:{SITE}", classification="confirmed", observed_at=NOW,
            value={**value, "requested_url": SITE, "lane": "static", "supports_project": True})])
        registry.apply_service_evidence(project, legacy)
        self.assertEqual(project["candidate_developer_accounts"], expected)
        missing_html = service.collect_service_probe_evidence(project["project_key"], SITE, value,
            observed_at=NOW, supports_project=True)
        self.assertNotIn("developer_account_observations", missing_html.records[0].value)
        registry.apply_service_evidence(project, missing_html)
        self.assertEqual(project["candidate_developer_accounts"], expected)
        many = "".join(f'<p>Built by <a href="https://bsky.app/profile/dev{i}.example">Dev {i}</a></p>' for i in range(40))
        bundle = apply(project, many, observed_at="2026-10-16T13:00:00Z")
        value = next(record.value for record in bundle.records if record.kind == "service_verification")
        self.assertFalse(value["developer_account_observations"]["complete"])
        self.assertIn(expected[0], project["candidate_developer_accounts"])
        self.assertLessEqual(len(json.dumps(value["developer_account_observations"]["accounts"], ensure_ascii=False).encode()), 32 * 1024)

    def test_older_observation_cannot_remove_newer_credit(self):
        project = self.make_project("proj:fresh")
        apply(project, CREDIT)
        apply(project, "", observed_at="2026-10-15T12:00:00Z")
        self.assertEqual(project["candidate_developer_accounts"], [{"handle": "author.example", "source": f"developer_page:{SITE}"}])

    def test_both_roles_need_their_own_context_without_automatic_adoption(self):
        project = self.make_project("proj:bothroles", "service_url", SITE)
        html = '<footer><p>Built by <a href="https://bsky.app/profile/maker.example">Creator</a></p>'
        html += '<p><a href="https://bsky.app/profile/maker.example">Follow us on Bluesky</a></p></footer>'
        apply(project, html)
        self.assertEqual(project["developer_accounts"], [])
        self.assertEqual(project["candidate_developer_accounts"], [{"handle": "maker.example", "source": f"developer_page:{SITE}"}])
        self.assertTrue(any(item["relation"]["predicate"] == "official_account" for item in project["relation_evidence"]))

    def test_candidate_accounts_are_not_exact_seed_matches(self):
        project = self.make_project("proj:noise")
        project["candidate_developer_accounts"] = [{"did": "did:plc:placeholder"}, {"handle": "customer.example"}]
        project["bluesky_handles"] = ["unadopted.example"]
        with registry.open_db() as conn:
            project_record.save_project(conn, project)
            for kind, value in (("did", "did:plc:placeholder"), ("handle", "customer.example"), ("handle", "unadopted.example")):
                self.assertEqual(project_record.indexed_identifier_matches(conn, kind, value), [])
                self.assertFalse(project_record.project_matches_identifier(project, kind, value)[1])
                self.assertIsNone(project_record.find_project_key_by_identifier(conn, kind, value))
            self.assertTrue(presentation.seed_preview(conn, seed="did:plc:placeholder")["can_create"])

    def test_operator_can_adopt_the_exact_case_sensitive_did(self):
        project = self.make_project("proj:adopt")
        project["candidate_developer_accounts"] = [{"did": "did:web:example.com:abc"}, {"did": "did:web:example.com:ABC"}]
        with registry.open_db() as conn:
            project_record.save_project(conn, project)
            presentation.confirm_candidate(conn, project_key=project["project_key"], identifier_type="account", value="did:web:example.com:ABC")
            saved = project_record.fetch_project(conn, project["project_key"])
            saved = registry.finalize_project_shape(saved)
            self.assertEqual(saved["developer_accounts"], [{"did": "did:web:example.com:ABC", "source": "web_ui"}])
            self.assertEqual(saved["candidate_developer_accounts"], [{"did": "did:web:example.com:abc"}])

    def test_profile_claim_requires_the_named_project_and_remains_candidate(self):
        for description, expected in (("Maintainer of Maker", True), ("Creator of Other", False),
                                      ("Not the developer of Maker", False), ("I use Maker", False), ("Makerを開発しています", True)):
            with self.subTest(description=description):
                self.assertEqual(bool(developer_links.profile_claim(description, ["Maker"])), expected)
        project = self.make_project("proj:search")
        project["candidate_developer_accounts"] = [{"handle": "old.example", "source": "bsky_actor_search:Maker"}]
        bundle = EvidenceBundle(project_key=project["project_key"], collected_at=NOW, records=[
            EvidenceRecord(kind="investigation_log", source="bsky_actor_search", classification="derived", observed_at=NOW,
                value={"kind": "bsky_actor_search", "query": "Maker"}, context={"policy_version": developer_links.POLICY_VERSION}),
            EvidenceRecord(kind="developer_handle_candidate", source="bsky_actor_search:Maker", classification="candidate", observed_at=NOW,
                value="author.example", context={"policy_version": developer_links.POLICY_VERSION, "reason": "profile_developer_claim", "description": "Maintainer of Maker"}),
            EvidenceRecord(kind="account_reference", source="bsky_author_feed:customer.example", classification="candidate", observed_at=NOW,
                value="customer.example"),
        ])
        registry.apply_investigation_evidence(project, bundle)
        self.assertEqual(project["candidate_developer_accounts"], [{"handle": "author.example", "source": "bsky_actor_search:Maker"}])
        self.assertEqual(project["developer_accounts"], [])
        self.assertIn("https://bsky.app/profile/customer.example", project["evidence_urls"])
