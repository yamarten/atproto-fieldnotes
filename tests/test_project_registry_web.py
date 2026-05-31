from __future__ import annotations

import json
import sqlite3
import threading
import time
import types
import urllib.parse
import unittest
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from io import BytesIO
from pathlib import Path

from helpers import TempRegistryDbTestCase, seed_web_fixture

import project_registry as registry  # noqa: E402
import project_registry_web as web  # noqa: E402


class ProjectRegistryWebTest(TempRegistryDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        web.configure_registry_db(str(self.db_path))
        seed_web_fixture()
        self.server = self._fake_server()

    def tearDown(self) -> None:
        self.server.executor.shutdown(wait=False, cancel_futures=True)
        super().tearDown()

    def _fake_server(self):
        server = types.SimpleNamespace()
        server.token = "test-token"
        server.jobs = {}
        server.jobs_lock = threading.Lock()
        server.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="registry-web-test")
        server.reload_requests = []
        server.shutdown_requests = []
        server.append_job_log = types.MethodType(web.ProjectRegistryWebServer.append_job_log, server)
        server.request_process_reload = lambda reason: server.reload_requests.append(reason) or "reload-test-id"
        server.request_process_shutdown = lambda reason: server.shutdown_requests.append(reason) or "shutdown-test-id"
        server.start_enrich_job = types.MethodType(web.ProjectRegistryWebServer.start_enrich_job, server)
        server._run_enrich_job = types.MethodType(web.ProjectRegistryWebServer._run_enrich_job, server)
        server.start_enrich_all_job = types.MethodType(web.ProjectRegistryWebServer.start_enrich_all_job, server)
        server._run_enrich_all_job = types.MethodType(web.ProjectRegistryWebServer._run_enrich_all_job, server)
        server.start_sync_job = types.MethodType(web.ProjectRegistryWebServer.start_sync_job, server)
        server._run_sync_job = types.MethodType(web.ProjectRegistryWebServer._run_sync_job, server)
        server.start_review_job = types.MethodType(web.ProjectRegistryWebServer.start_review_job, server)
        server._run_review_job = types.MethodType(web.ProjectRegistryWebServer._run_review_job, server)
        return server

    def request(self, method: str, path: str, payload: dict | None = None, token: str = "test-token") -> tuple[int, dict]:
        captured: dict = {}
        data = b""
        headers = {"X-Project-Registry-Token": token}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(data))

        handler = web.ProjectRegistryHandler.__new__(web.ProjectRegistryHandler)
        handler.server = self.server
        handler.path = path
        handler.headers = headers
        handler.rfile = BytesIO(data)

        def capture_json(_self, response_payload: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            captured["status"] = int(status)
            captured["payload"] = response_payload

        handler.send_json = types.MethodType(capture_json, handler)
        if method == "GET":
            handler.do_GET()
        elif method == "POST":
            handler.do_POST()
        else:
            raise AssertionError(f"unsupported method: {method}")
        return int(captured["status"]), captured["payload"]

    def test_api_rejects_wrong_token(self) -> None:
        status, payload = self.request("GET", "/api/projects", token="wrong")

        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

    def test_index_includes_button_hints_and_confirmations(self) -> None:
        self.assertIn("Open project details. This does not change the DB.", web.INDEX_HTML)
        self.assertIn("Review Ops", web.INDEX_HTML)
        self.assertIn("tab-review-ops", web.INDEX_HTML)
        self.assertIn("/api/review-stalled", web.INDEX_HTML)
        self.assertIn("/api/review-ignore", web.INDEX_HTML)
        self.assertIn("review-ops-result", web.INDEX_HTML)
        self.assertIn("data-review-kind", web.INDEX_HTML)
        self.assertIn("updateReviewOpsVisibility", web.INDEX_HTML)
        self.assertIn("renderReviewOpsJob", web.INDEX_HTML)
        self.assertIn("Apply this dry-run", web.INDEX_HTML)
        self.assertIn("apply-review-ops-dryrun", web.INDEX_HTML)
        self.assertIn("Write the selected tracking_state to SQLite", web.INDEX_HTML)
        self.assertIn("confirmAction(`Set tracking_state", web.INDEX_HTML)
        self.assertIn("confirm-modal", web.INDEX_HTML)
        self.assertIn("confirm-ok", web.INDEX_HTML)
        self.assertIn("Proceed", web.INDEX_HTML)
        self.assertIn("records a blocked identifier", web.INDEX_HTML)
        self.assertIn("Settings", web.INDEX_HTML)
        self.assertIn("Sync", web.INDEX_HTML)
        self.assertIn("tab-sync", web.INDEX_HTML)
        self.assertIn("Jobs", web.INDEX_HTML)
        self.assertIn("tab-jobs", web.INDEX_HTML)
        self.assertIn("loadJobsScreen", web.INDEX_HTML)
        self.assertIn("Recent Jobs", web.INDEX_HTML)
        self.assertIn("sync-${payload.kind}", web.INDEX_HTML)
        self.assertIn("review-${payload.kind}", web.INDEX_HTML)
        self.assertIn("job-list", web.INDEX_HTML)
        self.assertIn("New Seed", web.INDEX_HTML)
        self.assertIn("tab-new-seed", web.INDEX_HTML)
        self.assertIn("/api/seeds/preview", web.INDEX_HTML)
        self.assertIn("initialSeedParam", web.INDEX_HTML)
        self.assertIn("/api/sync", web.INDEX_HTML)
        self.assertIn("sync-introduced", web.INDEX_HTML)
        self.assertIn("sync-collections", web.INDEX_HTML)
        self.assertIn("sync-github-search", web.INDEX_HTML)
        self.assertIn("data-sync-kind", web.INDEX_HTML)
        self.assertIn("updateSyncVisibility", web.INDEX_HTML)
        self.assertIn("Enrich All", web.INDEX_HTML)
        self.assertIn("/api/enrich-all", web.INDEX_HTML)
        self.assertIn("run-enrich-all", web.INDEX_HTML)
        self.assertIn("Reload server", web.INDEX_HTML)
        self.assertIn("/api/reload", web.INDEX_HTML)
        self.assertIn("reload-server", web.INDEX_HTML)
        self.assertIn("Stop server", web.INDEX_HTML)
        self.assertIn("/api/shutdown", web.INDEX_HTML)
        self.assertIn("shutdown-server", web.INDEX_HTML)
        self.assertIn("PID:", web.INDEX_HTML)
        self.assertIn("Enrich Job", web.INDEX_HTML)
        self.assertIn("enrich-job-status", web.INDEX_HTML)
        self.assertIn("startEnrichJob", web.INDEX_HTML)
        self.assertIn("pollEnrichJob", web.INDEX_HTML)
        self.assertIn("action-card", web.INDEX_HTML)
        self.assertIn("setting-llm-endpoint", web.INDEX_HTML)
        self.assertIn("auto probe", web.INDEX_HTML)
        self.assertIn("Clear API key", web.INDEX_HTML)
        self.assertIn("clear-llm-api-key", web.INDEX_HTML)
        self.assertIn("Browser API key override", web.INDEX_HTML)
        self.assertIn('autocomplete="off"', web.INDEX_HTML)
        self.assertIn("setting-llm-reasoning-custom", web.INDEX_HTML)
        self.assertIn("reasoningEffortPresets", web.INDEX_HTML)
        self.assertIn("custom...", web.INDEX_HTML)
        self.assertIn("uniqueValues", web.INDEX_HTML)
        self.assertIn("Open source", web.INDEX_HTML)
        self.assertIn("Open target", web.INDEX_HTML)
        self.assertIn("Back to merge detail", web.INDEX_HTML)
        self.assertIn("mergeReturnIndex", web.INDEX_HTML)
        self.assertNotIn("Ignore source", web.INDEX_HTML)
        self.assertNotIn("Ignore target", web.INDEX_HTML)
        self.assertIn("Open project detail", web.INDEX_HTML)
        self.assertIn('state.tab === "review" ? "review" : "project"', web.INDEX_HTML)
        self.assertIn('state.detailView === "review"', web.INDEX_HTML)
        self.assertIn("Previous", web.INDEX_HTML)
        self.assertIn("Next", web.INDEX_HTML)
        self.assertIn("detailNavigation", web.INDEX_HTML)
        self.assertIn("projectNavigation", web.INDEX_HTML)
        self.assertIn("Project Edits", web.INDEX_HTML)
        self.assertIn("split-project", web.INDEX_HTML)
        self.assertIn("Merge Recommendation", web.INDEX_HTML)
        self.assertIn("merge-recommendation", web.INDEX_HTML)
        self.assertIn("Guards", web.INDEX_HTML)
        self.assertIn("clear-block-identifier", web.INDEX_HTML)
        self.assertIn("defer-identifier", web.INDEX_HTML)
        self.assertIn("clear-defer-identifier", web.INDEX_HTML)
        self.assertIn("Defer 30d", web.INDEX_HTML)
        self.assertIn("Clear defer", web.INDEX_HTML)
        self.assertIn("defer-promotion", web.INDEX_HTML)
        self.assertIn("clear-promotion-deferral", web.INDEX_HTML)
        self.assertIn("Hold 7d", web.INDEX_HTML)
        self.assertIn("Clear hold", web.INDEX_HTML)
        self.assertIn("requestTextAction", web.INDEX_HTML)
        self.assertIn("clear-separate", web.INDEX_HTML)
        self.assertIn("renderProjectGuards", web.INDEX_HTML)
        self.assertIn("renderEditableIdentifierRows", web.INDEX_HTML)
        self.assertIn("data-split-type", web.INDEX_HTML)
        self.assertIn("identifier-row", web.INDEX_HTML)
        self.assertIn("identifier-value", web.INDEX_HTML)
        self.assertIn("identifier-list", web.INDEX_HTML)
        self.assertIn("identifier-confirmed", web.INDEX_HTML)
        self.assertIn("identifier-candidate", web.INDEX_HTML)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto auto", web.INDEX_HTML)
        self.assertIn("flex-direction: column", web.INDEX_HTML)
        self.assertIn("flex: 1 1 auto", web.INDEX_HTML)
        self.assertIn("select, input", web.INDEX_HTML)
        self.assertIn("label.row", web.INDEX_HTML)
        self.assertIn("overflow-wrap: anywhere", web.INDEX_HTML)
        self.assertIn("grid-auto-rows: max-content", web.INDEX_HTML)
        self.assertIn("overscroll-behavior: contain", web.INDEX_HTML)
        self.assertIn(".card > a", web.INDEX_HTML)
        self.assertIn("justify-self: start", web.INDEX_HTML)
        self.assertIn("detail-open", web.INDEX_HTML)
        self.assertIn("detailBackButton", web.INDEX_HTML)
        self.assertIn("Back to list", web.INDEX_HTML)
        self.assertIn("list-count", web.INDEX_HTML)
        self.assertIn("function listCount", web.INDEX_HTML)
        self.assertIn("shown", web.INDEX_HTML)
        self.assertIn("listCache", web.INDEX_HTML)
        self.assertIn("clearListCache", web.INDEX_HTML)
        self.assertIn("Refreshing...", web.INDEX_HTML)
        self.assertIn("loadList({ force: true })", web.INDEX_HTML)
        self.assertIn("searchFilterPanel", web.INDEX_HTML)
        self.assertIn("parseSearchOperators", web.INDEX_HTML)
        self.assertIn("Clear filters", web.INDEX_HTML)
        self.assertIn("state:, service:, status:", web.INDEX_HTML)

    def test_parser_accepts_reload_options(self) -> None:
        parser = web.build_parser()
        args = parser.parse_args(["--token", "x", "--reload", "--reload-interval", "0.5"])

        self.assertTrue(args.reload)
        self.assertEqual(args.reload_interval, 0.5)

    def test_settings_endpoint_exposes_safe_defaults(self) -> None:
        status, payload = self.request("GET", "/api/settings")

        self.assertEqual(status, 200)
        self.assertEqual(payload["db_path"], str(self.db_path.resolve()))
        self.assertIsInstance(payload["pid"], int)
        self.assertTrue(payload["token_configured"])
        self.assertIn("ATPROTO_SURVEY_LLM_MODEL", payload["env"])
        self.assertNotIn("ATPROTO_SURVEY_WEB_TOKEN", payload["env"])

    def test_seed_preview_reports_exact_duplicate(self) -> None:
        seed = urllib.parse.quote("https://example.com", safe="")
        status, payload = self.request("GET", f"/api/seeds/preview?seed={seed}&type=service_url")

        self.assertEqual(status, 200)
        self.assertEqual(payload["seed_type"], "service_url")
        self.assertEqual(payload["seed_value"], "https://example.com")
        self.assertEqual(payload["existing_project"]["project_key"], "proj:222222222222")

    def test_seed_post_can_create_new_project_after_preview(self) -> None:
        seed = "https://github.com/example/new-repo"
        encoded = urllib.parse.quote(seed, safe="")
        status, payload = self.request("GET", f"/api/seeds/preview?seed={encoded}&type=repo_url")
        self.assertEqual(status, 200)
        self.assertIsNone(payload["existing_project"])

        status, payload = self.request("POST", "/api/seeds", {"seed": seed, "type": "repo_url"})

        self.assertEqual(status, 200)
        self.assertEqual(payload["seed_type"], "repo_url")
        conn = registry.open_db()
        try:
            project = registry.fetch_project(conn, payload["project_key"])
        finally:
            conn.close()
        self.assertIsNotNone(project)
        self.assertIn("https://github.com/example/new-repo", project["source_code_urls"])

    def test_reload_endpoint_requires_confirmation_and_requests_reload(self) -> None:
        status, payload = self.request("POST", "/api/reload", {})

        self.assertEqual(status, 400)
        self.assertIn("confirm=true", payload["error"])

        status, payload = self.request("POST", "/api/reload", {"confirm": True})

        self.assertEqual(status, 202)
        self.assertEqual(payload["reload_id"], "reload-test-id")
        self.assertEqual(payload["status"], "reloading")
        self.assertEqual(self.server.reload_requests, ["manual API request"])

    def test_shutdown_endpoint_requires_confirmation_and_requests_shutdown(self) -> None:
        status, payload = self.request("POST", "/api/shutdown", {})

        self.assertEqual(status, 400)
        self.assertIn("confirm=true", payload["error"])

        status, payload = self.request("POST", "/api/shutdown", {"confirm": True})

        self.assertEqual(status, 202)
        self.assertEqual(payload["shutdown_id"], "shutdown-test-id")
        self.assertEqual(payload["status"], "stopping")
        self.assertEqual(self.server.shutdown_requests, ["manual API request"])

    def test_review_queue_shape(self) -> None:
        status, payload = self.request("GET", "/api/review-queue?limit=5")

        self.assertEqual(status, 200)
        self.assertEqual(payload["queue_name"], "review")
        self.assertIn("items", payload)
        self.assertIn("kind_counts", payload)

    def test_project_search_combines_text_and_tracking_state_filter(self) -> None:
        conn = registry.open_db()
        try:
            duplicate = registry.fetch_project(conn, "proj:222222222222")
            duplicate["tracking_state"] = "archived"
            registry.save_project(conn, duplicate)
            conn.commit()
        finally:
            conn.close()

        status, payload = self.request("GET", "/api/projects?q=Example&tracking_states=active")

        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["project_key"], "proj:111111111111")

    def test_project_search_filters_service_status_and_candidates(self) -> None:
        status, payload = self.request("GET", "/api/projects?service_statuses=live&has_candidates=yes")

        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["project_key"], "proj:111111111111")

    def test_project_search_rejects_invalid_date(self) -> None:
        status, payload = self.request("GET", "/api/projects?seen_since=not-a-date")

        self.assertEqual(status, 400)
        self.assertIn("invalid ISO date", payload["error"])

    def test_merge_batch_plan_shape(self) -> None:
        original_build = web.registry.build_batch_merge_plan

        def fake_build(conn, mode="strict", *, bootstrap_collection_creator_heuristic=False):
            return (
                [
                    {
                        "source_project_key": "proj:222222222222",
                        "target_project_key": "proj:111111111111",
                        "source_name": "Example Duplicate",
                        "target_name": "Example App",
                        "reasons": {"same_service": ["https://example.com"]},
                        "recommendation": {"score": 10},
                    }
                ],
                [],
            )

        web.registry.build_batch_merge_plan = fake_build
        try:
            status, payload = self.request("GET", "/api/merge-batch?mode=moderate&limit=1")
        finally:
            web.registry.build_batch_merge_plan = original_build

        self.assertEqual(status, 200)
        self.assertEqual(payload["merge_mode"], "moderate")
        self.assertEqual(payload["planned_merges"], 1)
        self.assertEqual(payload["items"][0]["source_project_key"], "proj:222222222222")
        self.assertNotIn("comparison", payload["items"][0])

    def test_merge_pair_detail_includes_comparison(self) -> None:
        status, payload = self.request(
            "GET",
            "/api/merge-pair?source_project_key=proj:222222222222&target_project_key=proj:111111111111",
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["source_project_key"], "proj:222222222222")
        self.assertIn("comparison", payload)
        self.assertIn("merged_preview", payload)

    def test_merge_batch_merge_action_deletes_source(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/merge-batch/action",
            {
                "action": "merge",
                "source_project_key": "proj:222222222222",
                "target_project_key": "proj:111111111111",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "merged")
        conn = registry.open_db()
        try:
            source = registry.fetch_project(conn, "proj:222222222222")
            target = registry.fetch_project(conn, "proj:111111111111")
        finally:
            conn.close()
        self.assertIsNone(source)
        self.assertIsNotNone(target)

    def test_tracking_state_updates_temp_db(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/tracking-state",
            {"tracking_state": "watchlist", "note": "from test"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["project"]["tracking_state"], "watchlist")
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute("SELECT tracking_state FROM projects WHERE project_key = ?", ("proj:111111111111",)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], "watchlist")
        self.assertFalse(Path(self.tempdir.name, "other.db").exists())

    def test_confirm_and_reject_candidate(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/confirm-candidate",
            {"identifier_type": "service_url", "value": "https://example.com"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["confirmed"]["value"], "https://example.com")

        status, payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/reject-candidate",
            {"identifier_type": "repo_url", "value": "https://github.com/example/app"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["rejected"]["value"], "https://github.com/example/app")

        conn = registry.open_db()
        try:
            project = registry.fetch_project(conn, "proj:111111111111")
        finally:
            conn.close()
        self.assertIn("https://example.com", project["service_urls"])
        blocked = {(item["type"], item["value"]) for item in project["blocked_identifiers"]}
        self.assertIn(("repo_url", "https://github.com/example/app"), blocked)

    def test_project_detail_hides_confirmed_repo_candidates(self) -> None:
        status, payload = self.request("GET", "/api/projects/proj:111111111111")

        self.assertEqual(status, 200)
        self.assertEqual(payload["repo_candidates"], [])

    def test_block_identifier_action_removes_confirmed_repo(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/block-identifier",
            {"identifier_type": "repo_url", "value": "https://github.com/example/app", "note": "wrong repo"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["blocked"]["value"], "https://github.com/example/app")
        conn = registry.open_db()
        try:
            project = registry.fetch_project(conn, "proj:111111111111")
        finally:
            conn.close()
        self.assertNotIn("https://github.com/example/app", project["source_code_urls"])
        blocked = {(item["type"], item["value"]) for item in project["blocked_identifiers"]}
        self.assertIn(("repo_url", "https://github.com/example/app"), blocked)

    def test_clear_block_identifier_action_removes_guard_only(self) -> None:
        status, _payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/block-identifier",
            {"identifier_type": "repo_url", "value": "https://github.com/example/app", "note": "wrong repo"},
        )
        self.assertEqual(status, 200)

        status, payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/clear-block-identifier",
            {"identifier_type": "repo_url", "value": "https://github.com/example/app"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["cleared"]["value"], "https://github.com/example/app")
        conn = registry.open_db()
        try:
            project = registry.fetch_project(conn, "proj:111111111111")
        finally:
            conn.close()
        blocked = {(item["type"], item["value"]) for item in project["blocked_identifiers"]}
        self.assertNotIn(("repo_url", "https://github.com/example/app"), blocked)

    def test_defer_identifier_action_downgrades_repo_and_clear_removes_guard_only(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/defer-identifier",
            {"identifier_type": "repo_url", "value": "https://github.com/example/app", "note": "observe later", "days": 30},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["deferred"]["value"], "https://github.com/example/app")
        conn = registry.open_db()
        try:
            project = registry.fetch_project(conn, "proj:111111111111")
        finally:
            conn.close()
        self.assertNotIn("https://github.com/example/app", project["source_code_urls"])
        self.assertIn("https://github.com/example/app", project["candidate_source_code_urls"])
        deferred = {(item["type"], item["value"], item["note"]) for item in project["deferred_identifiers"]}
        self.assertIn(("repo_url", "https://github.com/example/app", "observe later"), deferred)

        status, payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/clear-defer-identifier",
            {"identifier_type": "repo_url", "value": "https://github.com/example/app"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["cleared_defer"]["value"], "https://github.com/example/app")
        conn = registry.open_db()
        try:
            project = registry.fetch_project(conn, "proj:111111111111")
        finally:
            conn.close()
        self.assertEqual(project["deferred_identifiers"], [])
        self.assertNotIn("https://github.com/example/app", project["source_code_urls"])
        self.assertIn("https://github.com/example/app", project["candidate_source_code_urls"])

    def test_promotion_hold_hides_queue_item_and_clear_preserves_history(self) -> None:
        conn = registry.open_db()
        try:
            project = registry.fetch_project(conn, "proj:222222222222")
            project["category"] = "project"
            registry.save_project(conn, project)
            conn.commit()
        finally:
            conn.close()

        status, payload = self.request("GET", "/api/promotion-queue")
        self.assertEqual(status, 200)
        self.assertIn("proj:222222222222", {item["project_key"] for item in payload["items"]})

        status, payload = self.request(
            "POST",
            "/api/projects/proj:222222222222/defer-promotion",
            {"reason": "wait for launch", "days": 7},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["promotion_deferral"]["reason"], "wait for launch")
        status, payload = self.request("GET", "/api/promotion-queue")
        self.assertEqual(status, 200)
        self.assertNotIn("proj:222222222222", {item["project_key"] for item in payload["items"]})

        status, payload = self.request("POST", "/api/projects/proj:222222222222/clear-promotion-deferral", {})

        self.assertEqual(status, 200)
        self.assertIsNone(payload["active_promotion_deferral"])
        conn = registry.open_db()
        try:
            project = registry.fetch_project(conn, "proj:222222222222")
        finally:
            conn.close()
        self.assertEqual(project["promotion_deferrals"][0]["reason"], "wait for launch")
        self.assertTrue(project["promotion_deferrals"][0]["cleared_at"])
        status, payload = self.request("GET", "/api/promotion-queue")
        self.assertEqual(status, 200)
        self.assertIn("proj:222222222222", {item["project_key"] for item in payload["items"]})

    def test_split_project_action_creates_new_project(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/split-project",
            {"identifier_type": "repo_url", "value": "https://github.com/example/app", "note": "separate library"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "split")
        self.assertEqual(payload["source_project_key"], "proj:111111111111")
        target_key = payload["target_project_key"]
        conn = registry.open_db()
        try:
            source = registry.fetch_project(conn, "proj:111111111111")
            target = registry.fetch_project(conn, target_key)
        finally:
            conn.close()
        self.assertIsNotNone(target)
        self.assertNotIn("https://github.com/example/app", source["source_code_urls"])
        self.assertIn("https://github.com/example/app", target["source_code_urls"])
        self.assertIn(target_key, source["separate_project_keys"])
        self.assertIn("proj:111111111111", target["separate_project_keys"])

    def test_clear_separate_action_updates_both_projects(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/split-project",
            {"identifier_type": "repo_url", "value": "https://github.com/example/app"},
        )
        self.assertEqual(status, 200)
        target_key = payload["target_project_key"]

        status, payload = self.request(
            "POST",
            "/api/projects/proj:111111111111/clear-separate",
            {"other_project_key": target_key},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "clear")
        conn = registry.open_db()
        try:
            source = registry.fetch_project(conn, "proj:111111111111")
            target = registry.fetch_project(conn, target_key)
        finally:
            conn.close()
        self.assertNotIn(target_key, source["separate_project_keys"])
        self.assertNotIn("proj:111111111111", target["separate_project_keys"])

    def test_merge_recommendation_action_merges_auto_ready_target(self) -> None:
        original_compute = web.registry.compute_duplicate_candidates

        def fake_compute(conn, project, *args, **kwargs):
            if project["project_key"] == "proj:111111111111":
                return [
                    {
                        "project_key": "proj:222222222222",
                        "score": 100,
                        "merge_confidence": "high",
                        "auto_merge_ready": True,
                        "reasons": {"same_service": ["https://example.com"]},
                    }
                ]
            return []

        web.registry.compute_duplicate_candidates = fake_compute
        try:
            status, payload = self.request("POST", "/api/projects/proj:111111111111/merge-recommendation", {})
        finally:
            web.registry.compute_duplicate_candidates = original_compute

        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "merged")
        self.assertEqual(payload["target_project_key"], "proj:222222222222")
        conn = registry.open_db()
        try:
            source = registry.fetch_project(conn, "proj:111111111111")
            target = registry.fetch_project(conn, "proj:222222222222")
        finally:
            conn.close()
        self.assertIsNone(source)
        self.assertIsNotNone(target)

    def test_failed_enrich_job_is_reported(self) -> None:
        original_enrich = web.registry.enrich_project

        def failing_enrich(*args, **kwargs):
            raise RuntimeError("planned failure")

        web.registry.enrich_project = failing_enrich
        try:
            status, payload = self.request(
                "POST",
                "/api/projects/proj:111111111111/enrich",
                {
                    "deep_investigation": True,
                    "llm_endpoint": "http://127.0.0.1:1234/v1",
                    "llm_model": "local-model",
                    "llm_api_key": "secret",
                    "llm_api_style": "responses",
                    "llm_reasoning_effort": "none",
                },
            )
            self.assertEqual(status, 202)
            job_id = payload["job_id"]
            self.assertEqual(self.server.jobs[job_id]["kind"], "enrich")
            self.assertEqual(self.server.jobs[job_id]["llm_options"]["llm_api_key"], "***")

            job_payload = {}
            for _ in range(50):
                status, job_payload = self.request("GET", f"/api/jobs/{job_id}")
                if job_payload.get("status") == "failed":
                    break
                time.sleep(0.05)

            self.assertEqual(status, 200)
            self.assertEqual(job_payload["status"], "failed")
            self.assertIn("planned failure", job_payload["error"])
        finally:
            web.registry.enrich_project = original_enrich

    def test_sync_job_runs_with_dry_run_default(self) -> None:
        original_run_sync = web.run_sync_payload
        captured: dict = {}

        def fake_run_sync(conn, payload):
            captured.update(payload)
            return {"mode": "dry_run", "kind": payload["kind"], "entries": 2}

        web.run_sync_payload = fake_run_sync
        try:
            status, payload = self.request("POST", "/api/sync", {"kind": "collections", "lookback_days": 7})
            self.assertEqual(status, 202)
            job_id = payload["job_id"]

            job_payload = {}
            for _ in range(50):
                status, job_payload = self.request("GET", f"/api/jobs/{job_id}")
                if job_payload.get("status") == "done":
                    break
                time.sleep(0.05)
        finally:
            web.run_sync_payload = original_run_sync

        self.assertEqual(status, 200)
        self.assertEqual(captured["kind"], "collections")
        self.assertFalse(captured["execute"])
        self.assertTrue(captured["create_unmatched"])
        self.assertEqual(captured["lookback_days"], 7)
        self.assertEqual(job_payload["status"], "done")
        self.assertEqual(job_payload["result"]["mode"], "dry_run")

    def test_sync_payload_can_disable_create_unmatched(self) -> None:
        payload = web.sync_payload_from_request({"kind": "collections", "create_unmatched": False})

        self.assertFalse(payload["create_unmatched"])

    def test_enrich_all_job_runs_limited_selection(self) -> None:
        original_enrich = web.registry.enrich_project
        seen: list[str] = []

        def fake_enrich(conn, project, **kwargs):
            seen.append(project["project_key"])
            project = dict(project)
            project["summary"] = "Bulk enriched"
            return registry.finalize_project_shape(project)

        web.registry.enrich_project = fake_enrich
        try:
            status, payload = self.request(
                "POST",
                "/api/enrich-all",
                {
                    "limit": 1,
                    "include_stable": True,
                    "deep_investigation": True,
                    "llm_reasoning_effort": "off",
                },
            )
            self.assertEqual(status, 202)
            job_id = payload["job_id"]

            job_payload = {}
            for _ in range(50):
                status, job_payload = self.request("GET", f"/api/jobs/{job_id}")
                if job_payload.get("status") == "done":
                    break
                time.sleep(0.05)
        finally:
            web.registry.enrich_project = original_enrich

        self.assertEqual(status, 200)
        self.assertEqual(len(seen), 1)
        self.assertEqual(job_payload["kind"], "enrich_all")
        self.assertEqual(job_payload["payload"]["tracking_states"], ["active", "stable", "watchlist"])
        self.assertEqual(job_payload["payload"]["llm_reasoning_effort"], "off")
        self.assertEqual(job_payload["result"]["completed_count"], 1)
        self.assertEqual(job_payload["result"]["failed_count"], 0)

    def test_review_stalled_job_reports_logs(self) -> None:
        original_run_review = web.run_review_stalled_payload

        def fake_run_review(conn, payload, *, log=None):
            if log:
                log("fake review stalled", limit=payload["limit"])
            return {
                "mode": "dry_run",
                "candidate_count": 1,
                "candidates": [{"project_key": "proj:111111111111", "suggested_tracking_state": "stalled"}],
            }

        web.run_review_stalled_payload = fake_run_review
        try:
            status, payload = self.request("POST", "/api/review-stalled", {"limit": 1})
            self.assertEqual(status, 202)
            job_id = payload["job_id"]

            job_payload = {}
            for _ in range(50):
                status, job_payload = self.request("GET", f"/api/jobs/{job_id}")
                if job_payload.get("status") == "done":
                    break
                time.sleep(0.05)
        finally:
            web.run_review_stalled_payload = original_run_review

        self.assertEqual(status, 200)
        self.assertEqual(job_payload["kind"], "review_stalled")
        self.assertEqual(job_payload["result"]["candidate_count"], 1)
        self.assertTrue(any(item.get("message") == "fake review stalled" for item in job_payload["logs"]))

    def test_review_ignore_execute_updates_temp_db(self) -> None:
        original_ignore_entry = web.registry.ignore_review_entry

        def fake_ignore_entry(project):
            if project["project_key"] != "proj:111111111111":
                return None
            return {
                "project_key": project["project_key"],
                "name": project["name"],
                "tracking_state": project["tracking_state"],
                "kind": "derivative_bluesky_utility",
                "reasons": ["test candidate"],
                "suggested_tracking_state": "ignore",
            }

        web.registry.ignore_review_entry = fake_ignore_entry
        try:
            status, payload = self.request("POST", "/api/review-ignore", {"execute": True, "limit": 1})
            self.assertEqual(status, 202)
            job_id = payload["job_id"]

            job_payload = {}
            for _ in range(50):
                status, job_payload = self.request("GET", f"/api/jobs/{job_id}")
                if job_payload.get("status") == "done":
                    break
                time.sleep(0.05)
        finally:
            web.registry.ignore_review_entry = original_ignore_entry

        self.assertEqual(status, 200)
        self.assertEqual(job_payload["kind"], "review_ignore")
        self.assertEqual(job_payload["result"]["updated_count"], 1)
        conn = registry.open_db()
        try:
            project = registry.fetch_project(conn, "proj:111111111111")
        finally:
            conn.close()
        self.assertEqual(project["tracking_state"], "ignore")

    def test_sync_rejects_unknown_kind(self) -> None:
        status, payload = self.request("POST", "/api/sync", {"kind": "repair-db"})

        self.assertEqual(status, 400)
        self.assertIn("kind must be one of", payload["error"])

    def test_should_refresh_lexicon_surface_handles_list_nsids(self) -> None:
        project = registry.default_project_record("proj:333333333333", "nsid", "app.example.post")
        project["resolved_nsids"] = ["app.example.post"]
        project["lexicon_schema_samples"] = [{"nsid": "app.example.post"}]
        project["summary"] = "Clear enough summary"
        project["tagline"] = "Clear tagline"
        project["features"] = ["feature one", "feature two"]
        project["website_url"] = "https://example.com"

        self.assertFalse(registry.should_refresh_lexicon_surface(project, ["app.example.post"]))

    def test_reasoning_effort_accepts_provider_values(self) -> None:
        self.assertEqual(registry.normalize_llm_reasoning_effort("on"), "on")
        self.assertEqual(registry.normalize_llm_reasoning_effort("off"), "off")
        self.assertEqual(registry.normalize_llm_reasoning_effort("provider_mode-1"), "provider_mode-1")
        self.assertEqual(registry.normalize_llm_reasoning_effort("bad value"), "")

    def test_enrich_job_reports_effective_llm_context(self) -> None:
        original_enrich = web.registry.enrich_project

        def fake_enrich(conn, project, **kwargs):
            project = dict(project)
            project["llm_source"] = {
                "transport": "openai_compatible_endpoint",
                "api_style": "responses",
                "resolved_endpoint": "http://127.0.0.1:1234/v1/responses",
            }
            project["last_run_errors"] = []
            return registry.finalize_project_shape(project)

        web.registry.enrich_project = fake_enrich
        try:
            status, payload = self.request("POST", "/api/projects/proj:111111111111/enrich", {"llm_api_style": "responses"})
            self.assertEqual(status, 202)
            job_id = payload["job_id"]

            job_payload = {}
            for _ in range(50):
                status, job_payload = self.request("GET", f"/api/jobs/{job_id}")
                if job_payload.get("status") == "done":
                    break
                time.sleep(0.05)
        finally:
            web.registry.enrich_project = original_enrich

        self.assertEqual(status, 200)
        self.assertEqual(job_payload["effective_llm"]["payload_api_style"], "responses")
        self.assertEqual(job_payload["result"]["llm_source"]["api_style"], "responses")
        self.assertTrue(any(item.get("llm_api_style") == "responses" for item in job_payload["logs"]))


if __name__ == "__main__":
    unittest.main()
