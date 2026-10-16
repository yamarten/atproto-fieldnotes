from __future__ import annotations

import json
from pathlib import Path
import unittest

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import account_links, project_record, service
from project_registry_core.identity import identifiers
from project_registry_core.persistence import runtime_view
from project_registry_core.evidence import EvidenceBundle
from project_registry_core.relations import normalize_relation_records, relation_record


NOW = "2026-10-16T00:00:00+00:00"
SITE = "https://app.example"
ACCOUNT = "https://bsky.app/profile/app.example"


def collect(html: str, **result_fields) -> EvidenceBundle:
    return service.collect_service_probe_evidence(
        "proj:test", SITE, {"status_code": 200, "title": "Example", "description": "", "html": html, **result_fields}, observed_at=NOW,
    )


def accounts(bundle: EvidenceBundle) -> list:
    return [record for record in bundle.records if record.kind == "relation" and record.value["predicate"] == "official_account"]


class OfficialAccountLinksTest(unittest.TestCase):
    def test_observed_regression_examples(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures/project_registry/official-account-links.json").read_text())
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                records = account_links.official_account_relation_records(case["url"], case["html"], observed_at=NOW)
                self.assertEqual([record.value["object"] for record in records], case["expected"])

    def test_only_exact_profile_endpoints_are_accounts(self) -> None:
        for suffix in ("/post/key", "/feed/key", "/lists/key", "/follows", "/followers", "/starter-pack/key", "/x/profile/other"):
            with self.subTest(suffix=suffix):
                url = ACCOUNT + suffix
                self.assertEqual(accounts(collect(f'<footer><a href="{url}" rel="me">Follow us on Bluesky</a></footer>', links=[{"url": url}])), [])
        for url in ("https://bsky.app/x/profile/app.example", "https://bsky.app/profile/", "https://bsky.app/profile/not-a-handle", "https://bsky.app/profile/app.example%2Fpost", "https://bsky.app.evil/profile/app.example", "ftp://bsky.app/profile/app.example"):
            with self.subTest(url=url):
                self.assertEqual(account_links.bluesky_profile_url(url), "")

    def test_profile_url_can_use_did_staging_query_and_trailing_slash(self) -> None:
        for url, expected in (
            (ACCOUNT + "/?ref=site#about", {"type": "handle", "value": "app.example"}),
            ("https://staging.bsky.app/profile/did:plc:abc", {"type": "did", "value": "did:plc:abc"}),
            ("https://bsky.app/profile/did:web:app.example", {"type": "did", "value": "did:web:app.example"}),
            ("https://bsky.app/profile/did:web:app.example%253A443", {"type": "did", "value": "did:web:app.example%3A443"}),
            ("https://bsky.app/profile/did:key:z6MAbC.", {"type": "did", "value": "did:key:z6MAbC."}),
        ):
            with self.subTest(url=url):
                records = accounts(collect(f'<a href="{url}" rel="me">Profile</a>'))
                self.assertEqual(records[0].value["object"], expected)
                self.assertEqual(records[0].context["observations"][0]["locator"], identifiers.normalize_url(url))

    def test_profile_aliases_share_the_observed_handle_without_resolving_did(self) -> None:
        urls = (
            "https://bsky.app/profile/App.Example",
            "https://staging.bsky.app/profile/app.example",
            "http://bsky.app/profile/app.example./?source=footer",
        )
        records = accounts(collect('<footer>' + ''.join(f'<a href="{url}">Bluesky</a>' for url in urls) + '</footer>'))
        self.assertEqual([record.value["object"] for record in records], [{"type": "handle", "value": "app.example"}])
        self.assertEqual(len(records[0].context["observations"]), 3)

    def test_handle_and_did_are_separate_observations_even_for_a_known_developer(self) -> None:
        html = '<footer>' + ''.join(f'<a href="https://bsky.app/profile/{value}">Bluesky</a>' for value in
                                  ("app.example", "did:key:z6MAbC", "did:key:z6Mabc")) + '</footer>'
        self.assertEqual([record.value["object"] for record in accounts(collect(html))], [
            {"type": "handle", "value": "app.example"},
            {"type": "did", "value": "did:key:z6MAbC"},
            {"type": "did", "value": "did:key:z6Mabc"},
        ])

    def test_self_declaration_and_rel_me_are_kept_with_evidence(self) -> None:
        for attrs, label, reason in (
            ("", "Follow us on Bluesky", "explicit_self_account"),
            ("", "Our Bluesky account", "explicit_self_account"),
            ('aria-label="Our official Bluesky account"', "", "explicit_self_account"),
            ('rel="noopener me"', "Profile", "rel_me"),
        ):
            with self.subTest(label=label, attrs=attrs):
                records = accounts(collect(f'<main><a href="{ACCOUNT}" {attrs}>{label}</a></main>'))
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].classification, "confirmed")
                self.assertEqual(records[0].context["policy_version"], account_links.POLICY_VERSION)
                self.assertEqual(records[0].context["observations"][0]["reason"], reason)

    def test_generic_social_links_require_navigation_context(self) -> None:
        for wrapper in ("footer", "nav", "header"):
            with self.subTest(wrapper=wrapper):
                self.assertEqual(len(accounts(collect(f'<{wrapper}><a href="{ACCOUNT}" aria-label="Bluesky"></a></{wrapper}>'))), 1)
        for label in ("Bluesky", "@app.example", "View Sean Highkin on Bluesky", "Follow on Bluesky", ""):
            with self.subTest(label=label):
                self.assertEqual(accounts(collect(f'<main><a href="{ACCOUNT}">{label}</a></main>')), [])
        self.assertEqual(accounts(collect("", links=[{"url": ACCOUNT, "text": "Follow us on Bluesky"}])), [])

    def test_quotes_people_and_credits_override_positive_labels(self) -> None:
        for html in (
            '<blockquote><a href="{url}">Follow us on Bluesky</a></blockquote>',
            '<article><footer><a href="{url}">Bluesky</a></footer></article>',
            '<section class="testimonials"><nav><a href="{url}">Bluesky</a></nav></section>',
            '<footer><section><h3>Contributors</h3><a href="{url}" rel="me">Bluesky</a></section></footer>',
            '<footer><p>Built by <a href="{url}">Bluesky</a></p></footer>',
            '<footer><div class="team-member"><a href="{url}" aria-label="Bluesky"></a></div></footer>',
        ):
            with self.subTest(html=html):
                self.assertEqual(accounts(collect(html.format(url=ACCOUNT))), [])

    def test_failed_fetch_does_not_assert_officiality(self) -> None:
        for fields in ({"status_code": 0}, {"status_code": 404}, {"status_code": 200, "fetch_error": "timeout"}):
            with self.subTest(fields=fields):
                self.assertEqual(accounts(collect(f'<footer><a href="{ACCOUNT}">Bluesky</a></footer>', **fields)), [])

    def test_full_page_is_classified_before_html_save_budget(self) -> None:
        html = " " * (service.SERVICE_HTML_EXTRACT_MAX_BYTES + 100) + f'<footer><a href="{ACCOUNT}">Bluesky</a></footer>'
        bundle = collect(html)
        self.assertEqual(len(accounts(bundle)), 1)
        self.assertNotIn(ACCOUNT, bundle.records[0].value["html_extract"])
        self.assertEqual(accounts(EvidenceBundle.from_dict(bundle.to_dict())), accounts(bundle))


class OfficialAccountPersistenceTest(TempRegistryDbTestCase):
    def test_relation_endpoints_match_developer_identifier_components_without_role_adoption(self) -> None:
        project = self.make_project("proj:test", "service_url", SITE)
        project["candidate_developer_accounts"] = [{"handle": "App.Example.", "did": "did:key:z6MAbC", "source": "test"}]
        bundle = collect('<footer><a href="https://bsky.app/profile/APP.EXAMPLE">Bluesky</a>'
                         '<a href="https://bsky.app/profile/did:key:z6MAbC">Bluesky</a></footer>')
        registry.apply_service_evidence(project, bundle)
        conn = registry.open_db()
        try:
            project_record.save_project(conn, project)
            conn.commit()
            saved = project_record.fetch_project(conn, project["project_key"])
            for relation in saved["relation_evidence"]:
                obj = relation["relation"]["object"]
                matches = conn.execute('SELECT role,classification FROM project_identifiers WHERE project_key=? AND kind=? AND value=?',
                                       (project["project_key"], obj["type"], obj["value"])).fetchall()
                self.assertEqual([(row["role"], row["classification"]) for row in matches], [(f'developer_{obj["type"]}', "candidate")])
            self.assertEqual(saved["developer_accounts"], [])
            self.assertEqual(saved["candidate_developer_accounts"], [{"handle": "app.example", "did": "did:key:z6MAbC", "source": "test"}])
            self.assertEqual(conn.execute("SELECT value FROM project_identifiers WHERE project_key=? AND kind='account'", (project["project_key"],)).fetchone()[0], "did:key:z6MAbC")
        finally:
            conn.close()

    def test_recollection_removes_ambiguous_legacy_profile_and_post(self) -> None:
        project = self.make_project("proj:test", "service_url", SITE)
        project["relation_evidence"] = normalize_relation_records([relation_record(
            source=f"service_page:{SITE}", classification=classification, observed_at=NOW,
            subject_type="website", subject_value=SITE, predicate="official_account",
            object_type="account", object_value=url, locator=SITE, method="first_party_link",
        ).to_dict() for url, classification in ((ACCOUNT, "candidate"), (ACCOUNT + "/post/key", "confirmed"))])
        registry.apply_service_evidence(project, collect(
            f'<article><a href="{ACCOUNT}">View user on Bluesky</a>'
            f'<a href="{ACCOUNT}/post/key">View on Bluesky</a></article>'
        ))
        self.assertEqual(project["relation_evidence"], [])

    def test_successful_recollection_replaces_legacy_account_relations_only_for_its_page(self) -> None:
        for lane in ("static", "deep", "follow_up", "repository_homepage", "authority"):
            with self.subTest(lane=lane):
                project = self.make_project(f"proj:{lane}", "service_url", SITE)
                legacy = relation_record(
                    source=f"service_page:{SITE}", classification="confirmed", observed_at=NOW,
                    subject_type="website", subject_value=SITE, predicate="official_account",
                    object_type="account", object_value=ACCOUNT + "/post/key", locator=SITE, method="first_party_link",
                ).to_dict()
                other = {**legacy, "source": "service_page:https://other.example"}
                manual = {**legacy, "source": "operator:test"}
                project["relation_evidence"] = normalize_relation_records([legacy, other, manual])
                bundle = service.collect_service_probe_evidence(
                    project["project_key"], SITE,
                    {"status_code": 200, "title": "Example", "description": "", "html": f'<footer><a href="{ACCOUNT}">Bluesky</a></footer>'},
                    observed_at=NOW, lane=lane,
                )
                registry.apply_service_evidence(project, bundle)
                conn = registry.open_db()
                try:
                    project_record.save_project(conn, project)
                    conn.commit()
                    saved = project_record.fetch_project(conn, project["project_key"])
                    relations = saved["relation_evidence"]
                    own = [item for item in relations if item["source"] == f"service_page:{SITE}"]
                    self.assertEqual(len(own), 1)
                    self.assertEqual(own[0]["relation"]["object"], {"type": "handle", "value": "app.example"})
                    self.assertEqual(own[0]["context"]["policy_version"], account_links.POLICY_VERSION)
                    self.assertEqual({item["source"] for item in relations}, {f"service_page:{SITE}", other["source"], manual["source"]})
                    self.assertEqual(conn.execute("SELECT count(*) FROM project_identifiers WHERE project_key=? AND kind='account'", (project["project_key"],)).fetchone()[0], 0)
                finally:
                    conn.close()

    def test_failed_recollection_preserves_existing_relation(self) -> None:
        project = self.make_project("proj:test", "service_url", SITE)
        good = collect(f'<footer><a href="{ACCOUNT}">Bluesky</a></footer>')
        registry.apply_service_evidence(project, good)
        before = project["relation_evidence"]
        registry.apply_service_evidence(project, collect("", status_code=0, fetch_error="timeout"))
        self.assertEqual(project["relation_evidence"], before)


class AtprotoAccountNormalizationTest(unittest.TestCase):
    def test_developer_normalization_and_persistence_use_the_same_identifier_rules(self) -> None:
        entries = [
            {"handle": " APP.Example. ", "did": "did:key:z6MAbC", "source": "test"},
            {"handle": "did:plc:abc", "source": "test"},
            {"handle": "not-a-handle", "did": "not-a-did"},
        ]
        expected = [{"handle": "app.example", "did": "did:key:z6MAbC", "source": "test"}, {"did": "did:plc:abc", "source": "test"}]
        self.assertEqual(project_record.normalize_developer_accounts(entries), expected)
        project = project_record.default_project_record("proj:test", "service_url", SITE)
        project["developer_accounts"] = entries
        self.assertEqual([row.value for row in runtime_view.project_identifiers(project) if row.role == "developer_account"],
                         ["did:key:z6MAbC", "did:plc:abc"])
        for account in expected:
            for kind, value in account.items():
                if kind in {"handle", "did"}:
                    self.assertEqual(identifiers.normalize_atproto_account_identifier(value), (kind, value))

    def test_developer_normalization_keeps_distinct_did_case(self) -> None:
        entries = [{"did": value, "source": "test"} for value in ("did:key:z6MAbC", "did:key:z6Mabc")]
        self.assertEqual(project_record.normalize_developer_accounts(entries), entries)

    def test_developer_profile_extraction_can_observe_an_author_without_claiming_officiality(self) -> None:
        url = ACCOUNT + "/post/key"
        self.assertEqual(project_record.extract_bluesky_profile_subject(url), {"handle": "app.example"})
        self.assertEqual(identifiers.extract_bluesky_profile_subject(url), {})
        self.assertEqual(accounts(collect(f'<a href="{url}">Follow us on Bluesky</a>')), [])


if __name__ == "__main__":
    unittest.main()
