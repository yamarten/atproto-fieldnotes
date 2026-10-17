from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core.transport import atproto
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


class TargetPostLineTest(unittest.TestCase):
    def test_text_url_line_is_exact_and_unambiguous(self) -> None:
        url = "https://example.test/app"
        text = f"別の紹介 https://other.test\n対象はこちら {url}。\n次の紹介"
        self.assertEqual(introduced_review.target_post_line(text, url), f"対象はこちら {url}。")
        self.assertEqual(introduced_review.target_post_line(text, "https://example.test"), "")
        self.assertEqual(introduced_review.target_post_line(text + f"\n再掲 {url}", url), "")
        self.assertEqual(introduced_review.target_post_line(text, ""), "")

    def test_facet_uses_utf8_position_and_rejects_unusable_or_ambiguous_ranges(self) -> None:
        url = "https://example.test/app"
        text = "ほかの紹介\n対象アプリ: example.test/a…\n同じ表示 example.test/a…"
        label = "example.test/a…"
        start = len(text[:text.index(label)].encode())
        end = start + len(label.encode())
        def facet(begin, finish):
            return {"index": {"byteStart": begin, "byteEnd": finish},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]}
        valid = facet(start, end)
        self.assertEqual(introduced_review.target_post_line(text, url, [valid]), "対象アプリ: example.test/a…")
        for invalid in [facet(-1, end), facet(start, 999), facet(1, end), facet(0, end), facet(start, start)]:
            with self.subTest(invalid=invalid):
                self.assertEqual(introduced_review.target_post_line(text, url, [invalid]), "")
        second_start = len(text[:text.rindex(label)].encode())
        self.assertEqual(introduced_review.target_post_line(text, url, [valid, facet(second_start, second_start + len(label.encode()))]), "")
        self.assertEqual(introduced_review.target_post_line(text, url), "")


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

    def test_payload_has_current_named_candidates_and_safe_legacy_line_without_writes(self) -> None:
        conn = registry.open_db()
        try:
            project = self.add_project(conn, "proj:choice", service="https://choice.example")
            project.update(name="Named Choice", introduced_url="https://example.test/older")
            project_record.save_project(conn, project)
            source = post("choice", urls=["https://choice.example"], text="Before\nChoice\nAfter")
            source["record"]["facets"][0]["index"] = {"byteStart": 7, "byteEnd": 13}
            item = introduced_review.observe_posts(conn, [source])[0]
            legacy = {**item, "id": "old", "post_text": "Before\nhttps://choice.example\nAfter"}
            legacy.pop("post_line")
            item["matched_project_keys"].append("proj:deleted")
            registry.write_sync_state(conn, "introduced", {"query": self.query}, {"review_items": [item, legacy]})
            conn.commit()
            before = conn.total_changes
            items = {entry["id"]: entry for entry in introduced_review.review_payload(conn)["items"]}
            self.assertEqual(items[item["id"]]["post_line"], "Choice")
            self.assertEqual(items["old"]["post_line"], "https://choice.example")
            choice = items[item["id"]]["matched_projects"][0]
            self.assertEqual((choice["project_key"], choice["name"], choice["introduced_url"]),
                             ("proj:choice", "Named Choice", "https://example.test/older"))
            self.assertIn("exact_identifier", choice["match_reasons"])
            self.assertEqual(conn.total_changes, before)
            self.assertEqual(project_record.fetch_project(conn, "proj:choice")["introduced_url"], "https://example.test/older")
        finally:
            conn.close()

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
            self.assertEqual(saved["introduced_url"], atproto.bsky_post_web_url(posts[0]))
            self.assertEqual(by_key[("mixed", "https://auto.example")]["status"], "auto_applied")
            self.assertEqual(by_key[("mixed", "https://auto.example/about")]["reason"], "unmatched")
            self.assertEqual(by_key[("plain", "https://plain.example/path")]["reason"], "unmatched")
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
            item = introduced_review.review_payload(conn, include_resolved=True,
                                                     scope_key=item["scope_key"], target_id=item["id"])["items"][0]
            self.assertEqual(item["reason"], "candidate_match")
            self.assertEqual(item["matched_projects"][0]["project_key"], "proj:candidate")
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"],
                                           action="link", project_key="proj:candidate")
            conn.close()
            conn = registry.open_db()
            saved = project_record.fetch_project(conn, "proj:candidate")
            self.assertEqual(saved["introduced_url"], atproto.bsky_post_web_url(source))
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

    def test_another_unique_url_in_same_post_settles_ambiguous_match(self) -> None:
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
            self.assertEqual(ambiguous["status"], "auto_applied")
            self.assertEqual(ambiguous["resolved_project_key"], "proj:first")
            self.assertEqual(next(item for item in payload["items"] if item["url"] == "https://also.example")["status"], "auto_applied")
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 0)
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"],
                             atproto.bsky_post_web_url(source))
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
                                 atproto.bsky_post_web_url(source))
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
                             atproto.bsky_post_web_url(source))
            introduced_review.resolve_item(conn, scope_key=manual["scope_key"], target_id=manual["id"],
                                           action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:auto")["introduced_url"],
                             atproto.bsky_post_web_url(source))
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
                             atproto.bsky_post_web_url(source))
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
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], "")
            self.assertEqual(project_record.fetch_project(conn, "proj:second")["introduced_url"], "")
            self.sync(conn, [source], cursor="next", max_pages=1)
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 1)
            item = introduced_review.review_payload(conn)["items"][0]
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"],
                                           action="link", project_key="proj:second")
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], "")
            self.assertEqual(project_record.fetch_project(conn, "proj:second")["introduced_url"],
                             atproto.bsky_post_web_url(source))
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], "")
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

    def test_reference_resources_are_retained_without_project_review(self) -> None:
        conn = registry.open_db()
        try:
            urls = ["https://github.com/example/app/pull/42", "https://github.com/example/app/issues/7",
                    "https://discourse.atmosphere.community/t/plans/1278/5",
                    "https://forum.example/t/launch/123", "https://docs.bsky.app/docs/advanced-guides/oauth"]
            source = post("references", urls=urls + ["https://www.discourse.org"])
            self.sync(conn, [source])
            items = {i["url"]: i for i in introduced_review.review_payload(conn, include_resolved=True)["items"]}
            self.assertTrue(all(items[url]["status"] == "reference" for url in urls))
            self.assertEqual(items["https://www.discourse.org"]["status"], "open")
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 1)
            ref = items[urls[0]]
            introduced_review.resolve_item(conn, scope_key=ref["scope_key"], target_id=ref["id"], action="review_target")
            self.sync(conn, [source])
            ref = next(i for i in introduced_review.review_payload(conn)["items"] if i["url"] == urls[0])
            self.assertTrue(ref["review_as_target"])
            with self.assertRaises(ValueError):
                introduced_review.resolve_item(conn, scope_key=ref["scope_key"], target_id=ref["id"], action="create",
                                               seed_type="service_url", seed_value=urls[0])
        finally:
            conn.close()

    def test_reference_repository_hint_is_only_shared_with_same_target_line(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:hint", repo="https://github.com/example/hint")
            target, reference = "https://new.example/app", "https://github.com/example/hint/pull/7"
            source = post("hint", text=f"App: {target} ({reference})\nOther: https://unrelated.example")
            items = introduced_review.observe_posts(conn, [source], explore=True)
            target_item = next(i for i in items if i["url"] == target)
            self.assertIn("proj:hint", target_item["matched_project_keys"])
            self.assertEqual(target_item["status"], "open")
            self.assertEqual(next(i for i in items if i["url"] == reference)["status"], "reference")
            self.assertEqual(next(i for i in items if i["url"] == "https://unrelated.example")["matched_project_keys"], [])
        finally:
            conn.close()

    def test_broad_candidates_have_reasons_but_do_not_auto_assign(self) -> None:
        conn = registry.open_db()
        try:
            project = self.add_project(conn, "proj:family", service="https://app.family.example")
            project.update(name="Named App")
            project_record.save_project(conn, project)
            source = post("broad", urls=["https://docs.family.example/start"], text="Named App")
            self.sync(conn, [source])
            item = introduced_review.review_payload(conn)["items"][0]
            detail = introduced_review.review_payload(conn, include_resolved=True, scope_key=item["scope_key"], target_id=item["id"])["items"][0]
            reasons = detail["matched_projects"][0]["match_reasons"]
            self.assertIn("merge_aliases", reasons)
            self.assertIn("authority_service_families", reasons)
            self.assertEqual(project_record.fetch_project(conn, "proj:family")["introduced_url"], "")
        finally:
            conn.close()

    def test_candidate_service_is_not_an_automatic_match_and_text_can_be_rechecked(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:weak", "service_url", "https://weak.example")
            project["service_url_candidates"] = ["https://weak.example"]
            project_record.save_project(conn, project)
            self.sync(conn, [post("weak", urls=["https://weak.example"])])
            self.assertEqual(project_record.fetch_project(conn, "proj:weak")["introduced_url"], "")
            self.add_project(conn, "proj:text", service="https://text.example")
            self.sync(conn, [post("text", text="See https://text.example")])
            item = next(i for i in introduced_review.review_payload(conn)["items"] if i["url"] == "https://text.example")
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="recheck")
            self.assertTrue(project_record.fetch_project(conn, "proj:text")["introduced_url"])
        finally:
            conn.close()

    def test_legacy_ambiguous_assignment_is_not_treated_as_resolved(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:first", service="https://shared.example")
            self.add_project(conn, "proj:second", service="https://shared.example")
            source = post("legacy-auto", urls=["https://shared.example"])
            self.sync(conn, [source])
            state = registry.read_sync_state(conn, "introduced", {"query": self.query})
            item = state["watermark"]["review_items"][0]
            item["auto_set_project_key"] = "proj:first"
            registry.write_sync_state(conn, "introduced", {"query": self.query}, state["watermark"])
            project = project_record.fetch_project(conn, "proj:first")
            project["introduced_url"] = atproto.bsky_post_web_url(source)
            project_record.save_project(conn, project)
            conn.commit()
            self.sync(conn, [source])
            item = introduced_review.review_payload(conn)["items"][0]
            self.assertEqual(item["reason"], "multiple_projects")
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="ignore")
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], "")
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], atproto.bsky_post_web_url(source))
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 1)
        finally:
            conn.close()

    def test_create_link_repeat_sync_and_undo_keep_new_project(self) -> None:
        conn = registry.open_db()
        try:
            source = post("new", urls=["https://old.example/landing"])
            self.sync(conn, [source])
            item = introduced_review.review_payload(conn)["items"][0]
            result = introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="create",
                                                     seed_type="service_url", seed_value="https://new.example/app", name="New App")
            key = result["item"]["created_project_key"]
            saved = project_record.fetch_project(conn, key)
            self.assertEqual(saved["name"], "New App")
            self.assertTrue(any(candidate["source"] == "operator_edit" for candidate in saved["name_candidates"]))
            self.assertIn("https://new.example/app", saved["service_urls"])
            self.assertEqual(saved["introduced_url"], atproto.bsky_post_web_url(source))
            with self.assertRaises(ValueError):
                introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="create",
                                               seed_type="service_url", seed_value="https://new.example/app")
            self.sync(conn, [source])
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 0)
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="reopen")
            self.assertEqual(project_record.fetch_project(conn, key)["introduced_url"], "")
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 1)
        finally:
            conn.close()

    def test_creation_rolls_back_if_review_save_fails_and_rejects_existing_seed(self) -> None:
        conn = registry.open_db()
        try:
            self.sync(conn, [post("atomic", text="No URL")])
            item = introduced_review.review_payload(conn)["items"][0]
            with mock.patch.object(introduced_review, "save_state", side_effect=RuntimeError("save failed")):
                with self.assertRaisesRegex(RuntimeError, "save failed"):
                    introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="create",
                                                   seed_type="repo_url", seed_value="https://github.com/example/new")
            self.assertEqual(conn.execute("SELECT count(*) FROM projects").fetchone()[0], 0)
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 1)
            self.add_project(conn, "proj:existing", repo="https://github.com/example/new")
            with self.assertRaisesRegex(ValueError, "already belongs"):
                introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="create",
                                               seed_type="repo_url", seed_value="https://github.com/example/new")
        finally:
            conn.close()

    def test_manual_link_follows_project_merge_and_can_be_undone(self) -> None:
        from project_registry_core.application import merge
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:source", candidate_repo="https://github.com/example/merge")
            self.add_project(conn, "proj:target", service="https://merged.example")
            self.sync(conn, [post("merge", urls=["https://github.com/example/merge"])])
            item = introduced_review.review_payload(conn)["items"][0]
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="link",
                                           project_key="proj:source")
            merge.merge_projects(conn, project_key="proj:source", target_project_key="proj:target", execute=True, force=True)
            linked = introduced_review.review_payload(conn, include_resolved=True)["items"][0]
            self.assertEqual(linked["linked_project"]["project_key"], "proj:target")
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:target")["introduced_url"], "")
        finally:
            conn.close()

    def test_comparison_context_reuses_prefilter_and_bounds_large_project_reads(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:match", repo="https://github.com/example/match")
            for number in range(100):
                project = self.make_project(f"proj:noise{number}", "service_url", f"https://noise{number}.invalid")
                project["notes"] = "irrelevant state " * 1000
                project_record.save_project(conn, project)
            search = introduced_review.CandidateSearch(conn)
            with mock.patch.object(project_record, "list_duplicate_candidate_inputs", wraps=project_record.list_duplicate_candidate_inputs) as inputs, \
                 mock.patch.object(project_record, "list_duplicate_projects", wraps=project_record.list_duplicate_projects) as reads:
                for label in ("Match", "Another linked name"):
                    matches = introduced_review.candidate_matches(conn, {
                        "seed_type": "repo_url", "seed_value": "https://github.com/example/match", "label": label}, search)
                    self.assertEqual([match["project_key"] for match in matches], ["proj:match"])
                self.assertEqual(inputs.call_count, 1)
                self.assertEqual(reads.call_count, 1)
                self.assertEqual(reads.call_args.kwargs["project_keys"], {"proj:match"})
        finally:
            conn.close()

    def test_suppressed_ambiguity_does_not_prevent_undo_of_its_manual_support(self) -> None:
        conn = registry.open_db()
        try:
            self.add_project(conn, "proj:first", service="https://shared.example")
            self.add_project(conn, "proj:second", service="https://shared.example")
            source = post("same-in-two-queries", urls=["https://shared.example"])
            self.sync(conn, [source])
            item = introduced_review.review_payload(conn)["items"][0]
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="link", project_key="proj:first")
            with mock.patch.object(source_sync, "search_bsky_posts_payload", return_value={"posts": [source]}):
                source_sync.sync_introduced_entries(conn, query="another query", execute=True, all_history=True)
            items = introduced_review.review_payload(conn, include_resolved=True)["items"]
            other = next(i for i in items if i["query"] == "another query")
            self.assertEqual(other["status"], "auto_applied")
            self.assertTrue(other["resolution_only"])
            introduced_review.resolve_item(conn, scope_key=item["scope_key"], target_id=item["id"], action="reopen")
            self.assertEqual(project_record.fetch_project(conn, "proj:first")["introduced_url"], "")
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 2)
        finally:
            conn.close()

    def test_existing_unambiguous_introduction_survives_candidate_adoption(self) -> None:
        conn = registry.open_db()
        try:
            source = post("old-weak-auto", urls=["https://weak.example"])
            project = self.make_project("proj:weak", "service_url", "https://weak.example")
            project["service_url_candidates"] = ["https://weak.example"]
            project["introduced_url"] = atproto.bsky_post_web_url(source)
            project_record.save_project(conn, project)
            item = introduced_review.observe_posts(conn, [source])[0]
            item.update(status="auto_applied", auto_set_project_key="proj:weak", matched_project_keys=["proj:weak"])
            registry.write_sync_state(conn, "introduced", {"query": self.query}, {"review_items": [item]})
            conn.commit()
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 0)
            self.sync(conn, [source])
            self.assertEqual(introduced_review.review_payload(conn)["open_count"], 0)
            self.assertEqual(project_record.fetch_project(conn, "proj:weak")["introduced_url"], atproto.bsky_post_web_url(source))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
