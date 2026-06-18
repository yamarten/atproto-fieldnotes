from __future__ import annotations

import json
import sqlite3
import threading
import types
from http import HTTPStatus
from io import BytesIO

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import storage
from project_registry_core.evidence import CollectorFailure, CollectorResult, EvidenceBundle
from project_registry_web.server import RegistryWebHandler
from project_registry_core.application import presentation


class FakeJobManager:
    def __init__(self) -> None:
        self.parameters: dict = {}
        self.execute = False
        self.job = {
            "job_id": "job:test",
            "operation_id": "enrich-test",
            "operation": "enrich",
            "status": "running",
            "request": {"parameters": {"all_projects": True}},
            "events": [],
            "can_cancel": True,
            "can_resume": False,
        }

    def start_enrich(self, parameters: dict) -> dict:
        self.parameters = parameters
        return dict(self.job)

    def start_sync(self, parameters: dict, *, execute: bool = False) -> dict:
        self.parameters = parameters
        self.execute = execute
        return {**self.job, "operation": "sync", "request": {"parameters": parameters}}

    def start_review(self, parameters: dict, *, execute: bool = False) -> dict:
        self.parameters = parameters
        self.execute = execute
        return {**self.job, "operation": "review", "request": {"parameters": parameters}}

    def list_jobs(self, *, limit: int = 50) -> dict:
        return {"count": 1, "items": [dict(self.job)]}

    def get_job(self, job_id: str) -> dict:
        if job_id != self.job["job_id"]:
            raise KeyError(f"job not found: {job_id}")
        return dict(self.job)

    def cancel(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        job["status"] = "cancelling"
        return job

    def resume(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        job["job_id"] = "job:resumed"
        return job


class TrackingLock:
    def __init__(self) -> None:
        self.entries = 0

    def __enter__(self) -> None:
        self.entries += 1

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class ProjectRegistryWebTest(TempRegistryDbTestCase):
    token = "web-test-token"

    def setUp(self) -> None:
        super().setUp()
        self.seed_fixture()
        self.server = types.SimpleNamespace(
            token=self.token,
            db_path=self.db_path,
            open_db=lambda: registry.open_db(),
            jobs=FakeJobManager(),
            read_only=False,
            mutation_lock=threading.Lock(),
        )

    def seed_fixture(self) -> None:
        conn = registry.open_db()
        project = registry.default_project_record("proj:111111111111", "nsid", "app.example.post")
        project["name"] = "Example App"
        project["summary"] = "Example summary"
        project["tagline"] = "Example tagline"
        project["tracking_state"] = "active"
        project["service_status"] = "live"
        registry.add_manual_seed(project, "nsid", "app.example.post", source="fixture")
        project["service_url_candidates"] = ["https://app.example.com"]
        project["source_code_urls"] = ["https://github.com/example/app"]
        project["candidate_source_code_urls"] = ["https://github.com/example/candidate"]
        project["relation_evidence"] = [{
            "relation": {
                "subject": {"type": "repo", "value": "https://github.com/example/app"},
                "predicate": "defines_schema",
                "object": {"type": "nsid", "value": "app.example.post"},
                "locator": "lexicons/app.example.post.json",
                "method": "lexicon_json",
            },
            "source": "github_source",
            "classification": "confirmed",
            "observed_at": "2026-06-14T00:00:00+00:00",
            "context": {},
        }]
        registry.save_project(conn, project)

        duplicate = registry.default_project_record("proj:222222222222", "service_url", "https://duplicate.example")
        duplicate["name"] = "Example Duplicate"
        duplicate["website_url"] = "https://duplicate.example"
        duplicate["service_urls"] = ["https://duplicate.example"]
        registry.save_project(conn, duplicate)

        bundle = EvidenceBundle(project_key=project["project_key"], collected_at="2026-06-14T00:00:00+00:00")
        bundle.add_collector_result(CollectorResult(
            source="github_repo",
            operation="inspect",
            status="partial",
            observed_at="2026-06-14T00:00:00+00:00",
            request_count=2,
            result_count=1,
            stop_reason="timeout",
        ))
        bundle.add_failure(CollectorFailure(
            source="github_repo",
            operation="readme",
            target="https://github.com/example/app",
            category="timeout",
            message="fixture timeout",
            retryable=True,
        ))
        storage.save_investigation_run(
            conn,
            project_key=project["project_key"],
            started_at="2026-06-14T00:00:00+00:00",
            completed_at="2026-06-14T00:00:01+00:00",
            input_project=project,
            output_project=project,
            bundle=bundle,
        )
        conn.commit()
        conn.close()

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        token: str | None = None,
        include_revision: bool = True,
    ) -> tuple[int, dict, str]:
        payload = dict(payload or {}) if payload is not None else None
        if method == "POST" and include_revision and payload is not None:
            if path.startswith("/api/projects/"):
                project_key = path.removeprefix("/api/projects/").split("/", 1)[0]
                conn = registry.open_db()
                payload.setdefault("expected_revision", presentation.project_revision(conn, project_key))
                conn.close()
            elif path == "/api/merge-batch/action":
                conn = registry.open_db()
                payload.setdefault("expected_source_revision", presentation.project_revision(conn, payload["source_project_key"]))
                payload.setdefault("expected_target_revision", presentation.project_revision(conn, payload["target_project_key"]))
                conn.close()
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {}
        if path.startswith("/api/"):
            headers["X-Project-Registry-Token"] = self.token if token is None else token
        if data is not None:
            headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(data or b""))
        captured: dict = {}
        handler = RegistryWebHandler.__new__(RegistryWebHandler)
        handler.server = self.server
        handler.path = path
        handler.headers = headers
        handler.rfile = BytesIO(data or b"")

        def capture_json(_self, body: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            captured.update(status=int(status), payload={"api_version": 2, **body}, content_type="application/json; charset=utf-8")

        def capture_bytes(_self, body: bytes, *, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            captured.update(status=int(status), payload=json.loads(body.decode("utf-8")), content_type=content_type)

        handler.send_json = types.MethodType(capture_json, handler)
        handler.send_bytes = types.MethodType(capture_bytes, handler)
        if method == "POST":
            handler.do_POST()
        else:
            handler.do_GET()
        return captured["status"], captured["payload"], captured["content_type"]

    def request_text(self, path: str) -> tuple[int, str, str]:
        captured: dict = {}
        handler = RegistryWebHandler.__new__(RegistryWebHandler)
        handler.server = self.server
        handler.path = path
        handler.headers = {}
        handler.rfile = BytesIO()

        def capture_json(_self, body: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            captured.update(status=int(status), body=json.dumps(body), content_type="application/json; charset=utf-8")

        def capture_bytes(_self, body: bytes, *, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            captured.update(status=int(status), body=body.decode("utf-8"), content_type=content_type)

        handler.send_json = types.MethodType(capture_json, handler)
        handler.send_bytes = types.MethodType(capture_bytes, handler)
        handler.do_GET()
        return captured["status"], captured["body"], captured["content_type"]

    def test_static_assets_and_api_authentication(self) -> None:
        status, html, content_type = self.request_text("/")
        self.assertEqual(status, 200)
        self.assertIn("Review", html)
        self.assertIn("type=\"module\"", html)
        self.assertIn("text/html", content_type)

        status, script, content_type = self.request_text("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("Latest Investigation", script)
        self.assertIn("Start enrich job", script)
        self.assertIn("startProjectEnrich", script)
        self.assertIn("openJobDetails", script)
        self.assertIn("Start sync job", script)
        self.assertIn("Start review job", script)
        self.assertIn("javascript", content_type)

        status, payload, _ = self.request("/api/projects", token="wrong")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")
        self.assertEqual(payload["api_version"], 2)

        status, payload, _ = self.request("/missing")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

        status, settings, _ = self.request("/api/settings")
        self.assertEqual(status, 200)
        self.assertTrue(settings["operations_connected"])
        self.assertTrue(settings["operation_capabilities"]["enrich"])
        self.assertTrue(settings["operation_capabilities"]["sync"])
        self.assertTrue(settings["operation_capabilities"]["review_ops"])
        self.assertFalse(settings["read_only"])

    def test_enrich_job_endpoints_validate_start_list_cancel_and_resume(self) -> None:
        status, payload, _ = self.request(
            "/api/jobs/enrich",
            method="POST",
            payload={"all_projects": True, "deep_investigation": True, "include_stable": True},
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["job_id"], "job:test")
        self.assertTrue(self.server.jobs.parameters["all_projects"])
        self.assertTrue(self.server.jobs.parameters["deep_investigation"])
        self.assertTrue(self.server.jobs.parameters["include_stable"])

        status, payload, _ = self.request(
            "/api/jobs/enrich",
            method="POST",
            payload={"project_key": "proj:111111111111", "all_projects": False},
        )
        self.assertEqual(status, 202)
        self.assertEqual(self.server.jobs.parameters["project_key"], "proj:111111111111")
        self.assertFalse(self.server.jobs.parameters["all_projects"])

        status, jobs, _ = self.request("/api/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(jobs["count"], 1)

        status, detail, _ = self.request("/api/jobs/job%3Atest")
        self.assertEqual(status, 200)
        self.assertEqual(detail["operation"], "enrich")

        status, stopping, _ = self.request("/api/jobs/job%3Atest/cancel", method="POST", payload={})
        self.assertEqual(status, 202)
        self.assertEqual(stopping["status"], "cancelling")

        status, resumed, _ = self.request("/api/jobs/job%3Atest/resume", method="POST", payload={})
        self.assertEqual(status, 202)
        self.assertEqual(resumed["job_id"], "job:resumed")

        status, error, _ = self.request("/api/jobs/enrich", method="POST", payload={"all_projects": False})
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "validation_error")

    def test_sync_and_review_job_endpoints_validate_allowlists(self) -> None:
        status, sync_job, _ = self.request(
            "/api/jobs/sync",
            method="POST",
            payload={
                "source": "github",
                "execute": True,
                "create_unmatched": True,
                "screen_unmatched": True,
                "all_history": True,
                "lookback_days": 20,
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(sync_job["operation"], "sync")
        self.assertEqual(self.server.jobs.parameters["source"], "github")
        self.assertTrue(self.server.jobs.parameters["all_history"])
        self.assertTrue(self.server.jobs.execute)

        status, review_job, _ = self.request(
            "/api/jobs/review",
            method="POST",
            payload={
                "kind": "stalled",
                "execute": False,
                "inactivity_days": 60,
                "max_enrich_age_days": 45,
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(review_job["operation"], "review")
        self.assertEqual(self.server.jobs.parameters["kind"], "stalled")
        self.assertEqual(self.server.jobs.parameters["inactivity_days"], 60)
        self.assertFalse(self.server.jobs.execute)

        status, error, _ = self.request(
            "/api/jobs/sync", method="POST", payload={"source": "arbitrary_url"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "validation_error")

        status, error, _ = self.request(
            "/api/jobs/review", method="POST", payload={"kind": "delete_everything"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "validation_error")

        status, error, _ = self.request(
            "/api/jobs/review",
            method="POST",
            payload={"kind": "stalled", "execute": "false"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "validation_error")

        status, error, _ = self.request(
            "/api/jobs/enrich",
            method="POST",
            payload={"all_projects": True, "tracking_states": ["invalid"]},
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "validation_error")

    def test_search_and_v2_project_detail(self) -> None:
        status, payload, _ = self.request("/api/projects?q=Example&has_candidates=yes")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["project_key"], "proj:111111111111")

        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        self.assertEqual(detail["project"]["profile"]["core"]["summary"], "Example summary")
        self.assertEqual(detail["relations"][0]["predicate"], "defines_schema")
        self.assertEqual(detail["investigation"]["collectors"][0]["status"], "partial")
        self.assertEqual(detail["investigation"]["failures"][0]["category"], "timeout")
        self.assertTrue(detail["revision"])
        self.assertEqual(detail["overview"]["manual_seed"]["value"], "app.example.post")
        self.assertNotIn("website_url_candidates", detail["project"])
        self.assertNotIn("github_repo_urls", detail["project"])
        candidate_values = {
            item["value"] for item in detail["identifiers"] if item["classification"] == "candidate"
        }
        self.assertIn("https://app.example.com", candidate_values)

    def test_search_uses_normalized_table_filters(self) -> None:
        cases = (
            ("identifier_kind=repo_url&identifier_value=example/candidate&identifier_classification=candidate", 1),
            ("relation_predicate=defines_schema&relation_object_type=nsid&relation_object_value=app.example", 1),
            ("relation_source=github_source&relation_subject_type=repo", 1),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                status, payload, _ = self.request(f"/api/projects?{query}")
                self.assertEqual(status, 200)
                self.assertEqual(payload["count"], expected)
                self.assertEqual(payload["items"][0]["project_key"], "proj:111111111111")

    def test_search_does_not_hydrate_all_projects(self) -> None:
        original = registry.list_projects
        registry.list_projects = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("hydrated all projects"))
        try:
            status, payload, _ = self.request("/api/projects?q=app.example.post")
        finally:
            registry.list_projects = original
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"][0]["project_key"], "proj:111111111111")

    def test_stale_revision_and_read_only_reject_writes(self) -> None:
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        stale_revision = detail["revision"]

        conn = registry.open_db()
        project = registry.fetch_project(conn, "proj:111111111111")
        assert project is not None
        project["name"] = "Changed elsewhere"
        registry.save_project(conn, project, preserve_timestamps=True)
        conn.close()

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/tracking-state",
            method="POST",
            payload={"tracking_state": "watchlist", "expected_revision": stale_revision},
            include_revision=False,
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "stale_project")

        self.server.read_only = True
        status, payload, _ = self.request(
            "/api/jobs/enrich", method="POST", payload={"all_projects": True}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "read_only")

    def test_project_mutations_pass_through_server_lock(self) -> None:
        lock = TrackingLock()
        self.server.mutation_lock = lock
        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/tracking-state",
            method="POST",
            payload={"tracking_state": "watchlist"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["detail"]["project"]["tracking_state"], "watchlist")
        self.assertEqual(lock.entries, 1)

    def test_tracking_confirm_reject_and_decision_roundtrip(self) -> None:
        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/tracking-state",
            method="POST",
            payload={"tracking_state": "watchlist", "note": "web review"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["detail"]["project"]["tracking_state"], "watchlist")

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/confirm-candidate",
            method="POST",
            payload={"identifier_type": "service_url", "value": "https://app.example.com"},
        )
        self.assertEqual(status, 200)
        confirmed = {
            item["value"] for item in payload["detail"]["identifiers"]
            if item["classification"] == "confirmed"
        }
        self.assertIn("https://app.example.com", confirmed)

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/reject-candidate",
            method="POST",
            payload={
                "identifier_type": "repo_url",
                "value": "https://github.com/example/candidate",
                "note": "not maintained here",
            },
        )
        self.assertEqual(status, 200)
        self.assertIn(
            "https://github.com/example/candidate",
            {item["target_value"] for item in payload["detail"]["decisions"]["active"]},
        )
        self.assertNotIn(
            "https://github.com/example/candidate",
            {item["value"] for item in payload["detail"]["identifiers"]},
        )

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/defer-promotion",
            method="POST",
            payload={"note": "wait for editorial review", "days": 7},
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(item["kind"] == "defer_promotion" for item in payload["detail"]["decisions"]["active"]))

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/clear-promotion-deferral",
            method="POST",
            payload={},
        )
        self.assertEqual(status, 200)
        self.assertFalse(any(item["kind"] == "defer_promotion" for item in payload["detail"]["decisions"]["active"]))

        conn = registry.open_db()
        rows = conn.execute(
            "SELECT kind, target_value FROM project_decisions WHERE project_key = ?",
            ("proj:111111111111",),
        ).fetchall()
        conn.close()
        self.assertIn(("block_identifier", "https://github.com/example/candidate"), [tuple(row) for row in rows])

    def test_defer_clear_split_and_merge(self) -> None:
        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/defer-identifier",
            method="POST",
            payload={"identifier_type": "repo_url", "value": "https://github.com/example/app", "days": 30},
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(item["kind"] == "defer_identifier" for item in payload["detail"]["decisions"]["active"]))

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/clear-defer-identifier",
            method="POST",
            payload={"identifier_type": "repo_url", "value": "https://github.com/example/app"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(any(item["kind"] == "defer_identifier" for item in payload["detail"]["decisions"]["active"]))

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/split-project",
            method="POST",
            payload={"identifier_type": "repo_url", "value": "https://github.com/example/app", "note": "separate package"},
        )
        self.assertEqual(status, 200)
        split_key = payload["result"]["target_project_key"]
        self.assertEqual(payload["detail"]["project"]["project_key"], split_key)

        status, merged, _ = self.request(
            "/api/merge-batch/action",
            method="POST",
            payload={
                "action": "merge",
                "source_project_key": split_key,
                "target_project_key": "proj:222222222222",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(merged["mode"], "execute")
        conn = registry.open_db()
        self.assertIsNone(registry.fetch_project(conn, split_key))
        conn.close()

    def test_seed_preview_create_and_queue_endpoints(self) -> None:
        status, preview, _ = self.request("/api/seeds/preview?seed=https%3A%2F%2Fnew.example")
        self.assertEqual(status, 200)
        self.assertTrue(preview["can_create"])

        status, created, _ = self.request(
            "/api/seeds", method="POST", payload={"seed": "https://new.example"}
        )
        self.assertEqual(status, 201)
        self.assertTrue(created["project_key"].startswith("proj:"))

        for path in ("/api/review-queue?limit=10", "/api/promotion-queue?limit=10", "/api/merge-batch?limit=10"):
            status, payload, _ = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn("items", payload)

    def test_validation_not_found_and_database_busy_error_shapes(self) -> None:
        status, payload, _ = self.request("/api/projects?introduced=maybe")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "validation_error")

        status, payload, _ = self.request("/api/projects/proj:does-not-exist")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

        original = self.server.open_db
        self.server.open_db = lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked"))
        try:
            status, payload, _ = self.request("/api/projects")
        finally:
            self.server.open_db = original
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "database_busy")


if __name__ == "__main__":
    import unittest

    unittest.main()
