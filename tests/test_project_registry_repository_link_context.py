from __future__ import annotations

import unittest

from helpers import ROOT  # noqa: F401 - initializes the local scripts import path
from project_registry_core import collector_relation_shadow, repository_link_context


class RepositoryLinkContextTest(unittest.TestCase):
    def evaluate(self, html: str) -> list[dict[str, object]]:
        return collector_relation_shadow.evaluate_project_repository_html(
            html, "https://project.example/docs"
        )["rows"]

    def test_extracts_bounded_structural_context(self) -> None:
        rows = repository_link_context.extract_repository_link_contexts(
            """
            <main><h2>Related projects</h2><ul>
              <li>Alpha <a href="https://github.com/example/alpha" aria-label="GitHub"></a></li>
              <li>Beta <a href="https://github.com/example/beta">Repository</a></li>
            </ul></main>
            """,
            "https://project.example",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["nearest_heading"], "Related projects")
        self.assertEqual(rows[0]["container_text"], "Alpha")
        self.assertEqual(rows[0]["group_repository_count"], 2)
        self.assertEqual(rows[0]["page_repository_count"], 2)
        self.assertIn("li", rows[0]["ancestor_tags"])
        self.assertIn("main", rows[0]["landmarks"])

    def test_non_repository_links_are_not_counted_as_repository_context(self) -> None:
        rows = repository_link_context.extract_repository_link_contexts(
            '<a href="https://example.net/docs">Docs</a><a href="https://github.com/example/project">Code</a>',
            "https://project.example",
        )
        self.assertEqual([row["repository_url"] for row in rows], ["https://github.com/example/project"])

    def test_unique_provider_icon_in_global_navigation_is_project_repository(self) -> None:
        rows = self.evaluate(
            '<header><nav><a href="https://github.com/example/project" aria-label="GitHub"></a></nav></header>'
        )
        self.assertEqual(rows[0]["disposition"], "CONFIRM_PROJECT_REPOSITORY")
        self.assertEqual(rows[0]["reason"], "visually_unlabeled_single_repository_link")

    def test_visually_unlabeled_unique_repository_link_is_positive_without_landmark(self) -> None:
        for anchor in (
            '<a href="https://github.com/example/project"></a>',
            '<a href="https://github.com/example/project" aria-label="GitHub"></a>',
        ):
            with self.subTest(anchor=anchor):
                rows = self.evaluate(f'<div class="social-link">{anchor}</div>')
                self.assertEqual(rows[0]["disposition"], "CONFIRM_PROJECT_REPOSITORY")
                self.assertEqual(rows[0]["reason"], "visually_unlabeled_single_repository_link")

    def test_visually_unlabeled_link_in_list_or_multi_repository_page_is_not_auto_confirmed(self) -> None:
        listed = self.evaluate(
            '<ul><li><a href="https://github.com/other/project"></a></li></ul>'
        )
        self.assertEqual(listed[0]["disposition"], "REVIEW_PROJECT_REPOSITORY")
        multiple = self.evaluate(
            """
            <div><a href="https://github.com/example/project"></a></div>
            <footer><a href="https://github.com/platform/site" aria-label="bug report"></a></footer>
            """
        )
        self.assertEqual({row["disposition"] for row in multiple}, {"REVIEW_PROJECT_REPOSITORY"})
        platform = next(row for row in multiple if row["repository_url"].endswith("platform/site"))
        self.assertEqual(platform["purpose"], "issue_or_community")
        self.assertEqual(platform["reason"], "visually_unlabeled_link_requires_context")

    def test_descendant_image_alt_is_kept_as_icon_presentation(self) -> None:
        rows = self.evaluate(
            '<header><a href="https://github.com/example/project"><img alt="GitHub repository"></a></header>'
        )
        self.assertEqual(rows[0]["descendant_label"], "GitHub repository")
        self.assertEqual(rows[0]["purpose"], "explicit_source")
        self.assertEqual(rows[0]["disposition"], "CONFIRM_PROJECT_REPOSITORY")

    def test_awesome_list_icons_are_external_references(self) -> None:
        rows = self.evaluate(
            """
            <main><h2>Awesome tools</h2><ul>
              <li>Alpha <a href="https://github.com/other/alpha" aria-label="GitHub"></a></li>
              <li>Beta <a href="https://github.com/other/beta" aria-label="GitHub"></a></li>
            </ul></main>
            """
        )
        self.assertEqual(
            {row["disposition"] for row in rows},
            {"DO_NOT_PROMOTE"},
        )

    def test_related_project_source_label_does_not_override_external_context(self) -> None:
        rows = self.evaluate(
            """
            <section><h2>Related projects</h2>
              <p>Other app <a href="https://github.com/other/app">Source code</a></p>
            </section>
            """
        )
        self.assertEqual(rows[0]["disposition"], "DO_NOT_PROMOTE")

    def test_issue_release_schema_and_docs_source_links_are_project_repositories(self) -> None:
        cases = {
            "Report a bug": "issue_or_community",
            "Download latest release": "release_or_download",
            "Lexicon definitions": "schema_or_specification",
            "Edit this page": "documentation_source",
        }
        for label, purpose in cases.items():
            with self.subTest(label=label):
                rows = self.evaluate(
                    f'<main><a href="https://github.com/example/project/issues">{label}</a></main>'
                )
                self.assertEqual(rows[0]["disposition"], "CONFIRM_PROJECT_REPOSITORY")
                self.assertEqual(rows[0]["purpose"], purpose)

    def test_dependency_repository_is_not_promoted(self) -> None:
        rows = self.evaluate(
            """
            <main><h2>Dependencies</h2>
              <p>Built with <a href="https://github.com/other/library">GitHub</a></p>
            </main>
            """
        )
        self.assertEqual(rows[0]["disposition"], "DO_NOT_PROMOTE")

    def test_generic_body_icon_is_positive_when_it_is_the_only_repository(self) -> None:
        rows = self.evaluate(
            '<main><p><a href="https://github.com/example/project" aria-label="GitHub"></a></p></main>'
        )
        self.assertEqual(rows[0]["disposition"], "CONFIRM_PROJECT_REPOSITORY")

    def test_same_repository_presentations_are_aggregated_without_discarding_evidence(self) -> None:
        result = collector_relation_shadow.evaluate_project_repository_html(
            """
            <footer>
              <a href="https://github.com/example/project" aria-label="GitHub"></a>
              <a href="https://github.com/example/project/issues">Submit an Issue</a>
            </footer>
            """,
            "https://project.example",
        )
        self.assertEqual(result["repository_link_count"], 2)
        self.assertEqual(result["repository_pair_count"], 1)
        self.assertEqual(result["pairs"][0]["observation_count"], 2)
        self.assertEqual(result["pairs"][0]["disposition"], "CONFIRM_PROJECT_REPOSITORY")

    def test_github_case_variants_share_one_repository_identity(self) -> None:
        result = collector_relation_shadow.evaluate_project_repository_html(
            """
            <a href="https://github.com/Example/Project">Repository</a>
            <a href="https://github.com/example/project/releases">Releases</a>
            """,
            "https://project.example",
        )
        self.assertEqual(result["repository_pair_count"], 1)
        self.assertEqual(result["pairs"][0]["observation_count"], 2)

    def test_unrelated_release_locator_is_not_enough_without_release_presentation(self) -> None:
        rows = self.evaluate(
            '<section><a href="https://github.com/other/plugin/releases/tag/v1">Zotero plugin</a></section>'
        )
        self.assertEqual(rows[0]["purpose"], "other_ambiguous")
        self.assertEqual(rows[0]["disposition"], "REVIEW_PROJECT_REPOSITORY")

    def test_version_link_to_release_is_accepted_for_project_repository(self) -> None:
        rows = self.evaluate(
            '<header>built today · <a href="https://github.com/example/project/releases/tag/v1.2">v1.2</a></header>'
        )
        self.assertEqual(rows[0]["purpose"], "release_or_download")
        self.assertEqual(rows[0]["disposition"], "CONFIRM_PROJECT_REPOSITORY")

    def test_open_source_block_can_directly_identify_project_repository(self) -> None:
        rows = self.evaluate(
            """
            <section><h2>Built in the open</h2>
              <p>This project is 100% open source, from the code that runs the site.
                <a href="https://github.com/example/project">View on GitHub</a>
              </p>
            </section>
            """
        )
        self.assertEqual(rows[0]["disposition"], "CONFIRM_PROJECT_REPOSITORY")
        self.assertEqual(
            rows[0]["reason"],
            "surrounding_context_directly_identifies_project_repository",
        )

    def test_current_page_comparison_matches_recollected_pair_without_writes(self) -> None:
        calls: list[str] = []

        def fetch(url: str) -> dict[str, object]:
            calls.append(url)
            return {
                "status_code": 200,
                "html": '<footer><a href="https://github.com/example/project/issues">Issues</a></footer>',
            }

        report = collector_relation_shadow.compare_current_project_repository_pages(
            [{
                "project_key": "proj:test",
                "project_name": "Test",
                "service_url": "https://project.example",
                "repo_url": "https://github.com/example/project",
                "promotion_disposition": "REVIEW_SOURCE_REPOSITORY",
            }],
            fetch_page=fetch,
            page_limit=10,
        )
        self.assertEqual(calls, ["https://project.example"])
        self.assertEqual(
            report["proposal_match_counts"],
            {"CONFIRM_PROJECT_REPOSITORY": 1},
        )

    def test_current_page_repository_redirect_is_direct_support(self) -> None:
        report = collector_relation_shadow.compare_current_project_repository_pages(
            [{
                "project_key": "proj:test",
                "service_url": "https://old.example",
                "repo_url": "https://github.com/example/project",
                "promotion_disposition": "RECOLLECT_PRESENTATION",
            }],
            fetch_page=lambda _url: {
                "status_code": 200,
                "final_url": "https://github.com/example/project",
                "html": "<html>provider navigation is irrelevant</html>",
            },
            page_limit=10,
        )
        match = report["pages"][0]["matches"][0]
        self.assertEqual(match["current_disposition"], "CONFIRM_PROJECT_REPOSITORY")
        self.assertEqual(match["reasons"], ["subject_redirects_to_repository"])

    def test_recollection_accepts_page_url_and_separates_fetch_failure(self) -> None:
        report = collector_relation_shadow.compare_current_project_repository_pages(
            [{
                "project_key": "proj:test",
                "page_url": "https://docs.example",
                "repository_url": "https://github.com/example/project",
                "page_standing": "outside_current_component",
            }],
            fetch_page=lambda _url: {"fetch_error": "timeout"},
            page_limit=10,
            concurrency=2,
        )
        self.assertEqual(report["proposal_match_counts"], {"FETCH_FAILED": 1})
        self.assertEqual(report["pages"][0]["page_url"], "https://docs.example")
        self.assertEqual(report["pages"][0]["proposal_match_counts"], {"FETCH_FAILED": 1})

    def test_strong_link_requires_the_exact_current_component(self) -> None:
        for standing in ("below_current_component", "outside_current_component"):
            with self.subTest(standing=standing):
                report = collector_relation_shadow.compare_current_project_repository_pages(
                    [{
                        "project_key": "proj:test",
                        "page_url": "https://shared-host.example/item",
                        "repository_url": "https://github.com/platform/core",
                        "page_standing": standing,
                    }],
                    fetch_page=lambda _url: {
                        "html": '<a href="https://github.com/platform/core">Source code</a>',
                    },
                    page_limit=10,
                )
                match = report["pages"][0]["matches"][0]
                self.assertEqual(match["current_disposition"], "CONFIRM_PROJECT_REPOSITORY")
                self.assertEqual(match["policy_disposition"], "REVIEW_PROJECT_REPOSITORY")
                self.assertEqual(
                    match["policy_reasons"],
                    ["page_is_not_the_exact_current_project_component"],
                )
                self.assertEqual(report["proposal_match_counts"], {"REVIEW_PROJECT_REPOSITORY": 1})


if __name__ == "__main__":
    unittest.main()
