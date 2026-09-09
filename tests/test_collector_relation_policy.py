import unittest

from project_registry_core import collector_relation_policy as policy


class DocumentationHostTest(unittest.TestCase):
    def test_hosted_project_documentation_keeps_website_role_without_service_candidate(self):
        for url in ("https://arroba.readthedocs.io", "https://lexrpc.readthedocs.io/en/latest",
                    "https://docs.rs/atrium-api", "https://hexdocs.pm/atproto"):
            with self.subTest(url=url):
                self.assertEqual(policy.classify_url_role(url), "documentation")
                self.assertEqual(policy.repository_homepage_service_candidate(url), "")
                self.assertTrue(policy.repository_homepage_is_website_candidate(url))
                self.assertEqual(policy.normalize_repository_homepage(url), url)

    def test_documentation_host_match_requires_domain_boundary(self):
        for url in ("https://notreadthedocs.io", "https://readthedocs.io.example.com"):
            self.assertEqual(policy.classify_url_role(url), "unknown")
