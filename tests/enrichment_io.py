"""Fixed external responses for tests that exercise the real enrichment stages."""
import base64
from contextlib import ExitStack
import copy
import json
import os
from urllib.parse import urlsplit
from unittest import mock

from helpers import registry
from project_registry_core.transport import atproto
from project_registry_core.transport import http_transport, llm_transport, reliability
from project_registry_core.repositories import repository_sources


class EnrichmentIO:
    def __init__(self, *, browser=None, llm=None, dns=None):
        self.responses = {}
        self.unexpected = []
        self.http = mock.Mock(side_effect=self.request)
        self.browser = mock.Mock(side_effect=browser or self.unconfigured_browser)
        self.llm = mock.Mock(side_effect=llm or RuntimeError("fixture: LLM unavailable"))
        self.dns = mock.Mock(side_effect=dns or self.unconfigured_dns)

    def json(self, url, payload):
        self.responses[url] = (200, json.dumps(payload).encode(), {"content-type": "application/json"}, url)

    def page(self, url, *, html="", status_code=200, final_url=None):
        self.responses[url] = (status_code, html.encode(), {"content-type": "text/html"}, final_url or url)

    def github_repo(self, url, *, readme="", **metadata):
        slug = url.removeprefix("https://github.com/")
        api = "https://api.github.com/repos/" + slug
        self.json(api, {"name": slug.split('/')[-1], "full_name": slug, "html_url": url, **metadata})
        self.json(api + "/readme", {"content": base64.b64encode(readme.encode()).decode()})

    def request(self, url, **kwargs):
        if url in self.responses:
            return copy.deepcopy(self.responses[url])
        parsed = urlsplit(url)
        empty_searches = {
            ("api.github.com", "/search/repositories"): {"items": []},
            ("public.api.bsky.app", "/xrpc/app.bsky.actor.searchActors"): {"actors": []},
            ("bsky.social", "/xrpc/app.bsky.feed.searchPosts"): {"posts": []},
            ("bsky.social", "/xrpc/com.atproto.server.createSession"): {"accessJwt": "fixture-token"},
        }
        payload = empty_searches.get((parsed.netloc, parsed.path))
        if payload is not None:
            return 200, json.dumps(payload).encode(), {"content-type": "application/json"}, url
        self.unexpected.append(url)
        raise AssertionError(f"unconfigured HTTP response: {url}")

    def unconfigured_browser(self, url, **kwargs):
        self.unexpected.append(f"browser:{url}")
        raise AssertionError(f"unconfigured browser response: {url}")

    def unconfigured_dns(self, name, **kwargs):
        self.unexpected.append(f"dns:{name}")
        raise AssertionError(f"unconfigured DNS response: {name}")

    def __enter__(self):
        self.stack = ExitStack()
        self.stack.enter_context(mock.patch.dict(os.environ, {
            "BSKY_HANDLE": "fixture.test", "BSKY_APP_PASSWORD": "fixture-password",
            "BSKY_AUTH_HOST": "https://bsky.social", "GITHUB_TOKEN": "",
            "ATPROTO_SURVEY_TRIAGE_REUSE": "0",
        }))
        # Isolate caches rather than allowing another test's response to hide an I/O call.
        for module, names in (
            (registry, ("_SERVICE_PROBE_CACHE", "_GITHUB_REPO_SEARCH_CACHE")),
            (repository_sources, ("_GITHUB_REPO_FETCH_CACHE", "_CODEBERG_REPO_FETCH_CACHE", "_TANGLED_REPO_FETCH_CACHE")),
        ):
            for name in names:
                self.stack.enter_context(mock.patch.object(module, name, reliability.TTLCache()))
        self.stack.enter_context(mock.patch.object(atproto, "_BSKY_SESSION_CACHE", {}))
        self.stack.enter_context(mock.patch.object(http_transport, "request_bytes_with_final_url", self.http))
        self.stack.enter_context(mock.patch.object(atproto, "resolve_dns_txt", self.dns))
        self.stack.enter_context(mock.patch.object(registry.service_core, "playwright_navigate", self.browser))
        self.stack.enter_context(mock.patch.object(llm_transport, "run_local_llm", self.llm))
        return self

    def __exit__(self, *exc):
        self.stack.__exit__(*exc)
        # The pipeline records many acquisition errors instead of raising them.
        # An unconfigured fake must still fail its test, even when caught there.
        if self.unexpected:
            raise AssertionError(f"unconfigured external calls: {self.unexpected}")
