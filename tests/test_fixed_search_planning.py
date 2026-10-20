from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit

from helpers import ProjectRecordTestCase, registry
from enrichment_io import EnrichmentIO
from project_registry_core import investigation


class FixedSearchPlanningTest(ProjectRecordTestCase):
    def test_service_host_is_not_bundled_with_broad_name_or_many_nsids(self):
        project = {"name": "Harbor Notes", "website_url": "https://notes.author.example.org/login",
                   "service_urls": ["https://photos.author.example.org"],
                   "authority_domains": ["feed.notes.author.example.org"],
                   "resolved_nsids": [f"org.example.author.notes.record{i}" for i in range(20)]}
        plan = registry.plan_investigation_queries(project)
        self.assertEqual(investigation.execution_queries(plan), {
            "github_search": ["notes.author.example.org", '"Harbor Notes"'],
            "bsky_actor_search": ["notes.author.example.org"],
            "bsky_post_search": ["notes.author.example.org"],
        })

    def test_input_availability_priority_and_empty_input(self):
        examples = [
            ({"service_urls": ["https://app.example.org"]}, "app.example.org"),
            ({"authority_domains": ["feed.example.org"]}, "feed.example.org"),
            ({"name": "Example"}, "Example"),
            ({"input_nsids": ["org.example.record"]}, "org.example.record"),
            ({}, None),
        ]
        for project, expected in examples:
            with self.subTest(project=project):
                plan = registry.plan_investigation_queries(project)
                self.assertEqual(plan["actor_search_queries"], [expected] if expected else [])
                self.assertEqual(plan["post_search_queries"], [expected] if expected else [])
                self.assertLessEqual(len(plan["repo_search_queries"]), 2)

    def test_url_name_has_a_short_repo_fallback_but_preserves_host_for_accounts(self):
        plan = registry.plan_investigation_queries({"name": "flight.author.example.org",
                                                   "service_urls": ["https://flight.author.example.org"]})
        self.assertEqual(plan["repo_search_queries"], ["flight.author.example.org", "flight"])
        self.assertEqual(plan["actor_search_queries"], ["flight.author.example.org"])

    def test_nsid_name_uses_collection_as_repo_fallback(self):
        plan = registry.plan_investigation_queries({"name": "org.example.trail", "authority_domains": ["trail.example.org"],
                                                   "resolved_nsids": ["org.example.trail.record", "org.example.trail.like"]})
        self.assertEqual(plan["repo_search_queries"], ["trail.example.org", "org.example.trail.record"])

    def test_known_github_repo_precedes_service_host_without_promoting_candidates(self):
        plan = registry.plan_investigation_queries({"name": "Notes", "service_urls": ["https://notes.example.org"],
                                                   "project_repository_urls": ["https://tangled.org/author.example/notes", "https://github.com/author/notes"],
                                                   "candidate_project_repository_urls": ["https://github.com/other/notes"]})
        self.assertEqual(plan["repo_search_queries"], ["author/notes", "Notes"])
        self.assertEqual(plan["post_search_queries"], ["notes.example.org"])

    def test_shared_host_is_preserved_without_guessing_an_owner(self):
        # A path-hosted service cannot be disambiguated by a hostname alone.
        # Keep its given name as a separate repo fallback rather than inventing an identity.
        plan = registry.plan_investigation_queries({"name": "Notes", "website_url": "https://shared.example.org/notes"})
        self.assertEqual(plan["repo_search_queries"], ["shared.example.org", "Notes"])

    def test_search_rendering_does_not_strip_platform_names_or_join_alternatives(self):
        plan = registry.plan_investigation_queries({"name": 'Bluesky "Notes"', "authority_domains": ["notes.example.org"]})
        self.assertEqual(investigation.execution_queries(plan)["github_search"],
                         ["notes.example.org", '"Bluesky \\"Notes\\""'])
        self.assertEqual(registry.plan_investigation_queries({"name": "Example"})["repo_search_queries"], ["Example"])

    def test_real_collector_uses_repo_fallback_only_after_no_accepted_candidate(self):
        for first_matches in (True, False):
            with self.subTest(first_matches=first_matches):
                project = self.make_project("proj:fixed-search")
                project.update(name="Harbor Notes", website_url="https://notes.example.org",
                               service_urls=["https://notes.example.org"])
                plan = registry.plan_investigation_queries(project)
                io = EnrichmentIO()
                target = {"html_url": "https://github.com/author/harbor-notes", "full_name": "author/harbor-notes",
                          "description": "Harbor Notes for atproto", "homepage": "https://notes.example.org"}
                irrelevant = {"html_url": "https://github.com/other/shipping", "full_name": "other/shipping",
                              "description": "Harbor shipping maps"}
                def url(query):
                    return 'https://api.github.com/search/repositories?' + urlencode(
                        {"q": query, "per_page": 5, "sort": "updated", "order": "desc"})
                io.json(url("notes.example.org"), {"items": [target if first_matches else irrelevant]})
                io.json(url('"Harbor Notes"'), {"items": [target]})
                with io:
                    bundle = registry.collect_investigation_evidence(project, plan)
                    registry.apply_investigation_evidence(project, bundle)
                github_queries = [parse_qs(urlsplit(call.args[0]).query)["q"][0]
                                  for call in io.http.call_args_list if urlsplit(call.args[0]).path == '/search/repositories']
                self.assertEqual(github_queries, ["notes.example.org"] + ([] if first_matches else ['"Harbor Notes"']))
                searches = [call for call in io.http.call_args_list if urlsplit(call.args[0]).path in (
                    '/search/repositories', '/xrpc/app.bsky.actor.searchActors', '/xrpc/app.bsky.feed.searchPosts')]
                self.assertEqual(len(searches), 3 if first_matches else 4)
                self.assertEqual(project['candidate_project_repository_urls'], ['https://github.com/author/harbor-notes'])
                io.llm.assert_not_called()
