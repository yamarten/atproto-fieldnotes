from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from io import BytesIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import project_registry as registry  # noqa: E402
import project_registry_web as web  # noqa: E402


class ProjectRegistryWebTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = registry.DB_PATH
        self.db_path = Path(self.tempdir.name) / "survey.db"
        web.configure_registry_db(str(self.db_path))
        self._seed_project()
        self.server = self._fake_server()

    def tearDown(self) -> None:
        self.server.executor.shutdown(wait=False, cancel_futures=True)
        registry.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def _fake_server(self):
        server = types.SimpleNamespace()
        server.token = "test-token"
        server.jobs = {}
        server.jobs_lock = threading.Lock()
        server.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="registry-web-test")
        server.start_enrich_job = types.MethodType(web.ProjectRegistryWebServer.start_enrich_job, server)
        server._run_enrich_job = types.MethodType(web.ProjectRegistryWebServer._run_enrich_job, server)
        server.start_sync_job = types.MethodType(web.ProjectRegistryWebServer.start_sync_job, server)
        server._run_sync_job = types.MethodType(web.ProjectRegistryWebServer._run_sync_job, server)
        return server

    def _seed_project(self) -> None:
        conn = registry.open_db()
        try:
            project = registry.default_project_record("proj:111111111111", "nsid", "app.example.post")
            project["name"] = "Example App"
            project["summary"] = "Example summary"
            project["tagline"] = "Example tagline"
            project["tracking_state"] = "active"
            project["service_status"] = "live"
            project["service_url_candidates"] = ["https://example.com"]
            project["candidate_source_code_urls"] = ["https://github.com/example/app"]
            project["source_code_urls"] = ["https://github.com/example/app"]
            project["github_repo_urls"] = ["https://github.com/example/app"]
            registry.save_project(conn, project)
            duplicate = registry.default_project_record("proj:222222222222", "service_url", "https://example.com")
            duplicate["name"] = "Example Duplicate"
            duplicate["tracking_state"] = "active"
            duplicate["service_status"] = "live"
            duplicate["website_url"] = "https://example.com"
            registry.save_project(conn, duplicate)
            conn.commit()
        finally:
            conn.close()

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
        self.assertIn("Write the selected tracking_state to SQLite", web.INDEX_HTML)
        self.assertIn("confirmAction(`Set tracking_state", web.INDEX_HTML)
        self.assertIn("records a blocked identifier", web.INDEX_HTML)
        self.assertIn("Settings", web.INDEX_HTML)
        self.assertIn("Sync", web.INDEX_HTML)
        self.assertIn("tab-sync", web.INDEX_HTML)
        self.assertIn("/api/sync", web.INDEX_HTML)
        self.assertIn("sync-introduced", web.INDEX_HTML)
        self.assertIn("sync-collections", web.INDEX_HTML)
        self.assertIn("sync-github-search", web.INDEX_HTML)
        self.assertIn("Enrich Job", web.INDEX_HTML)
        self.assertIn("enrich-job-status", web.INDEX_HTML)
        self.assertIn("startEnrichJob", web.INDEX_HTML)
        self.assertIn("pollEnrichJob", web.INDEX_HTML)
        self.assertIn("setting-llm-endpoint", web.INDEX_HTML)
        self.assertIn("setting-llm-reasoning-custom", web.INDEX_HTML)
        self.assertIn("reasoningEffortPresets", web.INDEX_HTML)
        self.assertIn("custom...", web.INDEX_HTML)
        self.assertIn("uniqueValues", web.INDEX_HTML)
        self.assertIn("Open source", web.INDEX_HTML)
        self.assertIn("Open target", web.INDEX_HTML)
        self.assertIn("Project Edits", web.INDEX_HTML)
        self.assertIn("split-project", web.INDEX_HTML)
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

    def test_parser_accepts_reload_options(self) -> None:
        parser = web.build_parser()
        args = parser.parse_args(["--token", "x", "--reload", "--reload-interval", "0.5"])

        self.assertTrue(args.reload)
        self.assertEqual(args.reload_interval, 0.5)

    def test_settings_endpoint_exposes_safe_defaults(self) -> None:
        status, payload = self.request("GET", "/api/settings")

        self.assertEqual(status, 200)
        self.assertEqual(payload["db_path"], str(self.db_path.resolve()))
        self.assertTrue(payload["token_configured"])
        self.assertIn("ATPROTO_SURVEY_LLM_MODEL", payload["env"])
        self.assertNotIn("ATPROTO_SURVEY_WEB_TOKEN", payload["env"])

    def test_review_queue_shape(self) -> None:
        status, payload = self.request("GET", "/api/review-queue?limit=5")

        self.assertEqual(status, 200)
        self.assertEqual(payload["queue_name"], "review")
        self.assertIn("items", payload)
        self.assertIn("kind_counts", payload)

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
        self.assertEqual(captured["lookback_days"], 7)
        self.assertEqual(job_payload["status"], "done")
        self.assertEqual(job_payload["result"]["mode"], "dry_run")

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


if __name__ == "__main__":
    unittest.main()
