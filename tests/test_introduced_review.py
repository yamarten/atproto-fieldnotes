from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import introduced_review, project_record, source_sync


def post(rkey: str, *, urls: list[str] = (), text: str = "Example") -> dict:
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    return {
        "uri": f"at://did:plc:example/app.bsky.feed.post/{rkey}",
        "author": {"handle": "example.test"}, "indexedAt": timestamp,
        "record": {"createdAt": timestamp, "text": text,
                   "facets": [{"index": {"byteStart": 0, "byteEnd": len(text.encode())},
                               "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]}
                              for url in urls]},
    }


class IntroducedReviewTest(TempRegistryDbTestCase):
    query = "introduced review fixture"

    def sync(self, conn, posts: list[dict], *, execute: bool = True, cursor: str = "", max_pages: int = 100):
        with mock.patch.object(source_sync, "search_bsky_posts_payload", return_value={"posts": posts, "cursor": cursor}):
            return source_sync.sync_introduced_entries(conn, query=self.query, execute=execute,
                                                        all_history=True, max_pages=max_pages)

    def add_project(self, conn, key: str, *, service: str = "", repo: str = "", candidate_repo: str = ""):
        seed_type, seed_value = ("service_url", service) if service else ("repo_url", repo or candidate_repo)
        project = self.make_project(key, seed_type, seed_value)
        if service:
            registry.add_manual_seed(project, "service_url", service, source="test")
        if repo:
            registry.add_manual_seed(project, "repo_url", repo, source="test")
        if candidate_repo:
            project["candidate_project_repository_urls"] = [candidate_repo]
        project_record.save_project(conn, project)
        return project

    def test_records_unextractable_and_mixed_urls_without_changing_automatic_match(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:auto", service="https://auto.example")
            posts = [
                post("mixed", urls=["https://auto.example", "https://auto.example/about"]),
                post("plain", text="Look at https://plain.example/path"),
                post("empty", text="No URL here"),
            ]
            summary = self.sync(conn, posts)
            saved = project_record.fetch_project(conn, "proj:auto")
            payload = introduced_review.review_payload(conn, include_resolved=True)
            by_key = {(x["post_uri"].split("/")[-1], x["url"]): x for x in payload["items"]}
            self.assertEqual(summary["updated"], 1)
            self.assertEqual(saved["introduced_url"], registry.bsky_post_web_url(posts[0]))
            self.assertEqual(by_key[("mixed", "https://auto.example")]["status"], "auto_applied")
            self.assertEqual(by_key[("mixed", "https://auto.example/about")]["reason"], "no_seed")
            self.assertEqual(by_key[("plain", "https://plain.example/path")]["reason"], "text_url_not_extracted")
            self.assertEqual(by_key[("empty", "")]["reason"], "no_url")
            self.assertEqual(payload["open_count"], 3)
        finally:
            conn.close()

    def test_ignore_survives_repeat_sync_and_blocks_later_auto_match(self) -> None:
        conn = registry.open_db()
        try:
            source = post("ignored", urls=["https://github.com/example/later"])
            self.sync(conn, [source])
            item = introduced_review.review_payload(conn)["items"][0]
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="ignore")
            self.add_project(conn, "proj:later", repo="https://github.com/example/later")
            self.sync(conn, [source])
            saved = project_record.fetch_project(conn, "proj:later")
            payload = introduced_review.review_payload(conn, include_resolved=True)
            self.assertEqual(saved["introduced_url"], "")
            self.assertEqual(payload["open_count"], 0)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["status"], "ignored")
        finally:
            conn.close()

    def test_link_and_reopen_do_not_confirm_candidate_repo(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:candidate", candidate_repo="https://github.com/example/candidate")
            source = post("candidate", urls=["https://github.com/example/candidate"])
            self.sync(conn, [source])
            item = introduced_review.review_payload(conn)["items"][0]
            self.assertEqual(item["reason"], "unmatched")
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"],
                                           action="link", project_key="proj:candidate")
            conn.close()
            conn = registry.open_db()
            saved = project_record.fetch_project(conn, "proj:candidate")
            self.assertEqual(saved["introduced_url"], registry.bsky_post_web_url(source))
            self.assertIn("https://github.com/example/candidate", saved["candidate_project_repository_urls"])
            self.assertNotIn("https://github.com/example/candidate", saved["project_repository_urls"])
            self.sync(conn, [source])
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 0)
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:candidate")["introduced_url"], "")
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 1)
        finally:
            conn.close()

    def test_replaced_introduced_url_is_restored_after_repeat_sync(self) -> None:
        conn = registry.open_db()
        try:
            project = self.add_project(conn, "proj:replace", candidate_repo="https://github.com/example/replace")
            project = project_record.fetch_project(conn, project["project_key"])
            project["introduced_url"] = "https://bsky.app/profile/example.test/post/older"
            project_record.save_project(conn, project)
            source = post("newer", urls=["https://github.com/example/replace"])
            self.sync(conn, [source])
            item = introduced_review.review_payload(conn)["items"][0]
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"],
                                           action="link", project_key="proj:replace", replace=True)
            self.sync(conn, [source])
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:replace")["introduced_url"],
                             "https://bsky.app/profile/example.test/post/older")
        finally:
            conn.close()

    def test_matched_project_with_another_introduced_url_stays_open(self) -> None:
        conn = registry.open_db()
        try:
            project = self.add_project(conn, "proj:old", service="https://old.example")
            project = project_record.fetch_project(conn, project["project_key"])
            project["introduced_url"] = "https://bsky.app/profile/example.test/post/older"
            project_record.save_project(conn, project)
            self.sync(conn, [post("newer", urls=["https://old.example"])])
            item = introduced_review.review_payload(conn)["items"][0]
            self.assertEqual(item["reason"], "different_introduced_url")
            self.assertEqual(project_record.fetch_project(conn, "proj:old")["introduced_url"],
                             "https://bsky.app/profile/example.test/post/older")
        finally:
            conn.close()

    def test_missing_author_handle_keeps_post_open_with_did_link(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:nohandle", service="https://nohandle.example")
            source = post("nohandle", urls=["https://nohandle.example"])
            source["author"] = {}
            self.sync(conn, [source])
            item = introduced_review.review_payload(conn)["items"][0]
            self.assertEqual(item["reason"], "not_applied")
            self.assertEqual(item["post_web_url"], "https://bsky.app/profile/did:plc:example/post/nohandle")
        finally:
            conn.close()

    def test_ignoring_ambiguous_url_keeps_other_automatic_url_in_same_post(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:first", service="https://shared.example")
            first = project_record.fetch_project(conn, "proj:first")
            registry.add_manual_seed(first, "service_url", "https://also.example", source="test")
            project_record.save_project(conn, first)
            self.add_project(conn, "proj:second", service="https://shared.example")
            source = post("two-links", urls=["https://shared.example", "https://also.example"])
            self.sync(conn, [source])
            payload = introduced_review.review_payload(conn, include_resolved=True)
            ambiguous = next(item for item in payload["items"] if item["url"] == "https://shared.example")
            self.assertEqual(ambiguous["reason"], "multiple_projects")
            self.assertEqual(next(item for item in payload["items"] if item["url"] == "https://also.example")["status"], "auto_applied")
            introduced_review.resolve_item(conn, scope_key=ambiguous["scope_key"], target_id=ambiguous["id"], action="ignore")
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"],
                             registry.bsky_post_web_url(source))
        finally:
            conn.close()

    def test_two_manual_links_keep_url_until_last_decision_is_reopened(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:manual", service="https://manual.example")
            source = post("two-manual", urls=["https://manual.example/a", "https://manual.example/b"])
            self.sync(conn, [source])
            items = {item["url"]: item for item in introduced_review.review_payload(conn)["items"]}
            first = items["https://manual.example/a"]
            second = items["https://manual.example/b"]
            for first_reopen in (first, second):
                introduced_review.resolve_item(conn, scope_key=first["scope_key"], target_id=first["id"],
                                               action="link", project_key="proj:manual")
                introduced_review.resolve_item(conn, scope_key=second["scope_key"], target_id=second["id"],
                                               action="link", project_key="proj:manual")
                remaining = second if first_reopen is first else first
                introduced_review.resolve_item(conn, scope_key=first_reopen["scope_key"],
                                               target_id=first_reopen["id"], action="reopen")
                self.assertEqual(project_record.fetch_project(conn, "proj:manual")["introduced_url"],
                                 registry.bsky_post_web_url(source))
                self.assertEqual(introduced_review.review_payload(conn, include_resolved=True)["count"], 2)
                introduced_review.resolve_item(conn, scope_key=remaining["scope_key"],
                                               target_id=remaining["id"], action="reopen")
                self.assertEqual(project_record.fetch_project(conn, "proj:manual")["introduced_url"], "")
        finally:
            conn.close()

    def test_ignoring_auto_item_preserves_manual_link_to_same_post(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:auto", service="https://shared.example")
            self.add_project(conn, "proj:other", service="https://shared.example")
            source = post("auto-and-manual", urls=["https://shared.example", "https://shared.example/about",
                                                       "https://shared.example/other"])
            self.sync(conn, [source])
            items = {item["url"]: item for item in introduced_review.review_payload(conn)["items"]}
            automatic = items["https://shared.example"]
            manual = items["https://shared.example/about"]
            second_manual = items["https://shared.example/other"]
            introduced_review.resolve_item(conn, scope_key=manual["scope_key"], target_id=manual["id"],
                                           action="link", project_key="proj:auto")
            introduced_review.resolve_item(conn, scope_key=second_manual["scope_key"], target_id=second_manual["id"],
                                           action="link", project_key="proj:auto")
            introduced_review.resolve_item(conn, scope_key=automatic["scope_key"], target_id=automatic["id"],
                                           action="ignore")
            self.assertEqual(project_record.fetch_project(conn, "proj:auto")["introduced_url"],
                             registry.bsky_post_web_url(source))
            introduced_review.resolve_item(conn, scope_key=manual["scope_key"], target_id=manual["id"],
                                           action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:auto")["introduced_url"],
                             registry.bsky_post_web_url(source))
            introduced_review.resolve_item(conn, scope_key=second_manual["scope_key"], target_id=second_manual["id"],
                                           action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:auto")["introduced_url"], "")
        finally:
            conn.close()

    def test_links_from_two_queries_preserve_url_until_both_are_reopened(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:cross-query", service="https://cross.example")
            source = post("cross-query", urls=["https://cross.example/a", "https://cross.example/b"])
            self.sync(conn, [source])
            with mock.patch.object(source_sync, "search_bsky_posts_payload",
                                   return_value={"posts": [source], "cursor": ""}):
                source_sync.sync_introduced_entries(conn, query="second introduced query", execute=True,
                                                    all_history=True)
            items = introduced_review.review_payload(conn)["items"]
            first = next(item for item in items if item["query"] == self.query and item["url"].endswith("/a"))
            second = next(item for item in items if item["query"] == "second introduced query" and item["url"].endswith("/b"))
            introduced_review.resolve_item(conn, scope_key=first["scope_key"], target_id=first["id"],
                                           action="link", project_key="proj:cross-query")
            introduced_review.resolve_item(conn, scope_key=second["scope_key"], target_id=second["id"],
                                           action="link", project_key="proj:cross-query")
            introduced_review.resolve_item(conn, scope_key=first["scope_key"], target_id=first["id"], action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:cross-query")["introduced_url"],
                             registry.bsky_post_web_url(source))
            introduced_review.resolve_item(conn, scope_key=second["scope_key"], target_id=second["id"], action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:cross-query")["introduced_url"], "")
        finally:
            conn.close()

    def test_observation_failure_rolls_back_project_updates_and_review_state(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:rollback", service="https://rollback.example")
            source = post("rollback", urls=["https://rollback.example"])
            with mock.patch.object(source_sync.introduced_review, "observe_posts", side_effect=RuntimeError("observation failed")):
                with self.assertRaisesRegex(RuntimeError, "observation failed"):
                    self.sync(conn, [source])
            self.assertFalse(conn.in_transaction)
            conn.commit()  # A caller reusing the connection cannot commit partial project changes.
            self.assertEqual(project_record.fetch_project(conn, "proj:rollback")["introduced_url"], "")
            self.assertEqual(registry.read_sync_state(conn, "introduced", {"query": self.query})["watermark"], {})
        finally:
            conn.close()

    def test_second_project_save_failure_rolls_back_first_project(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:first", service="https://first.example")
            self.add_project(conn, "proj:second", service="https://second.example")
            source = post("two-projects", urls=["https://first.example", "https://second.example"])
            real_save = source_sync.save_project
            calls = 0

            def fail_second_save(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("second save failed")
                return real_save(*args, **kwargs)

            with mock.patch.object(source_sync, "save_project", side_effect=fail_second_save):
                with self.assertRaisesRegex(RuntimeError, "second save failed"):
                    self.sync(conn, [source])
            self.assertEqual(calls, 2)
            self.assertFalse(conn.in_transaction)
            conn.commit()
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], "")
            self.assertEqual(project_record.fetch_project(conn, "proj:second")["introduced_url"], "")
            self.assertEqual(registry.read_sync_state(conn, "introduced", {"query": self.query})["watermark"], {})
        finally:
            conn.close()

    def test_multiple_matches_remain_visible_and_partial_scan_does_not_advance_watermark(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:first", service="https://shared.example")
            self.add_project(conn, "proj:second", service="https://shared.example")
            source = post("shared", urls=["https://shared.example"])
            summary = self.sync(conn, [source], cursor="next", max_pages=1)
            state = registry.read_sync_state(conn, "introduced", {"query": self.query})
            payload = introduced_review.review_payload(conn)
            self.assertFalse(summary["scan_complete"])
            self.assertFalse(summary["state_updated"])
            self.assertNotIn("timestamp", state["watermark"])
            self.assertEqual(payload["open_count"], 1)
            self.assertEqual(payload["items"][0]["reason"], "multiple_projects")
            self.assertEqual(len(payload["items"][0]["matched_project_keys"]), 2)
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], registry.bsky_post_web_url(source))
            self.assertEqual(project_record.fetch_project(conn, "proj:second")["introduced_url"], "")
            self.sync(conn, [source], cursor="next", max_pages=1)
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 1)
            item = introduced_review.review_payload(conn)["items"][0]
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"],
                                           action="link", project_key="proj:second")
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], "")
            self.assertEqual(project_record.fetch_project(conn, "proj:second")["introduced_url"],
                             registry.bsky_post_web_url(source))
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"],
                             registry.bsky_post_web_url(source))
            self.assertEqual(project_record.fetch_project(conn, "proj:second")["introduced_url"], "")
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="ignore")
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], "")
            self.sync(conn, [source], cursor="next", max_pages=1)
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], "")
        finally:
            conn.close()

    def test_legacy_pending_is_shown_and_migrated_once(self) -> None:
        conn = registry.open_db()
        try:
            legacy = {"seed_type": "repo_url", "seed_value": "https://github.com/example/legacy",
                      "labels": ["Legacy"], "post_web_urls": ["https://bsky.app/profile/example.test/post/legacy"]}
            registry.write_sync_state(conn, "introduced", {"query": self.query},
                                      {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "pending_entries": [legacy]})
            conn.commit()
            payload = introduced_review.review_payload(conn)
            self.assertEqual(payload["open_count"], 1)
            self.assertEqual(payload["items"][0]["url_source"], "legacy")
            item = payload["items"][0]
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="ignore")
            self.sync(conn, [])
            state = registry.read_sync_state(conn, "introduced", {"query": self.query})
            self.assertNotIn("pending_entries", state["watermark"])
            self.assertEqual(len(state["watermark"]["review_items"]), 1)
            self.assertEqual(introduced_review.review_payload(conn, include_resolved=True)["items"][0]["status"], "ignored")
            self.add_project(conn, "proj:legacy", repo="https://github.com/example/legacy")
            self.sync(conn, [post("legacy", urls=["https://github.com/example/legacy"])])
            payload = introduced_review.review_payload(conn, include_resolved=True)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["status"], "ignored")
            self.assertEqual(payload["items"][0]["url_source"], "facet")
            self.assertEqual(project_record.fetch_project(conn, "proj:legacy")["introduced_url"], "")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
