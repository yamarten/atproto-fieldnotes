from __future__ import annotations

import io
import contextlib
import json
import signal
import sqlite3
import sys
import threading
import types
from unittest import mock
from http import HTTPStatus
from io import BytesIO

from helpers import ROOT, ProjectRecordTestCase, TempRegistryDbTestCase, registry

from project_registry_core import project_merge
from project_registry_core.identity import domain_policy
from project_registry_core import introduced_review
from project_registry_core.persistence import storage
from project_registry_core.evidence import CollectorFailure, CollectorResult, EvidenceBundle
from project_registry_web.server import (
    RegistryWebHandler,
    RegistryWebServer,
    build_information,
    build_parser,
    install_sigterm_shutdown,
    main as web_main,
    restart_argv,
)
from project_registry_core.application import presentation, projects
from project_registry_core import project_record


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

    def start_verify_service(self, parameters: dict) -> dict:
        self.parameters = parameters
        self.execute = True
        return {**self.job, "operation": "verify_service", "request": {"parameters": parameters}}

    def list_jobs(self, *, limit: int = 50) -> dict:
        return {"count": 1, "items": [dict(self.job)]}

    def get_job(self, job_id: str) -> dict:
        if job_id != self.job["job_id"]:
            raise KeyError(f"job not found: {job_id}")
        return dict(self.job)

    def list_checkpoints(self, *, limit: int = 100) -> dict:
        return {"count": 0, "items": []}

    def list_schedules(self) -> dict:
        return {"version": 1, "items": [{"preset_id": "enrich_all", "schedule": {"enabled": False}}], "queue": []}

    def update_schedules(self, payload) -> dict:
        self.parameters = payload
        return {"version": 1, "items": payload if isinstance(payload, list) else payload.get("items", []), "queue": []}

    def run_scheduled_preset(self, preset_id: str) -> dict:
        if preset_id != "enrich_all":
            raise ValueError(f"unknown schedule preset: {preset_id}")
        self.parameters = {"preset_id": preset_id}
        return {"version": 1, "items": [], "queue": [{"preset_id": preset_id}]}

    def resume_checkpoint(self, run_id: str) -> dict:
        job = dict(self.job)
        job["operation_id"] = run_id
        return job

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


class _ProjectRegistryWebFixtures:
    token = "web-test-token"

    def seed_fixture(self) -> None:
        conn = registry.open_db()
        project = project_record.default_project_record("proj:111111111111", "nsid", "app.example.post")
        project["name"] = "Example App"
        project["summary"] = "Example summary"
        project["tagline"] = "Example tagline"
        project["tracking_state"] = "active"
        project["service_status"] = "live"
        registry.add_manual_seed(project, "nsid", "app.example.post", source="fixture")
        project["service_url_candidates"] = ["https://app.example.com"]
        project["project_repository_urls"] = ["https://github.com/example/app"]
        project["candidate_project_repository_urls"] = ["https://github.com/example/candidate"]
        project["candidate_developer_accounts"] = [{"handle": "dev.example.com", "did": "did:plc:developer", "source": "fixture"}]
        project["url_aliases"] = [{"alias": "https://example.com", "canonical": "https://app.example.com", "reason": "redirect", "confidence": "candidate"}]
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
        project_record.save_project(conn, project)

        duplicate = project_record.default_project_record("proj:222222222222", "service_url", "https://duplicate.example")
        duplicate["name"] = "Example Duplicate"
        duplicate["website_url"] = "https://duplicate.example"
        duplicate["service_urls"] = ["https://duplicate.example"]
        project_record.save_project(conn, duplicate)

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
        bundle.add_collector_result(CollectorResult(
            source="service_static",
            operation="probe",
            status="success",
            observed_at="2026-06-14T00:00:00+00:00",
            request_count=1,
            result_count=1,
        ))
        storage.save_investigation_run(
            conn,
            project_key=project["project_key"],
            started_at="2026-06-14T00:00:00+00:00",
            completed_at="2026-06-14T00:00:01+00:00",
            input_project=project,
            output_project=project,
            bundle=bundle,
            run_metrics={
                "total_ms": 1200,
                "phases": {"initial_collection_ms": 800, "evaluation_ms": 400},
                "llm_requests": [{
                    "purpose": "triage",
                    "status": "success",
                    "duration_ms": 300,
                    "prompt_bytes": 2048,
                    "response_bytes": 512,
                    "timeout_seconds": 300,
                }],
            },
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


class ProjectRegistryWebUnitTest(_ProjectRegistryWebFixtures, ProjectRecordTestCase):
    def test_http_request_log_excludes_query_and_payload(self) -> None:
        handler = RegistryWebHandler.__new__(RegistryWebHandler)
        handler.command = "POST"
        handler.client_address = ("127.0.0.1", 12345)
        stderr = io.StringIO()

        def respond() -> None:
            handler._response_status = HTTPStatus.CREATED

        with mock.patch("project_registry_web.server.sys.stderr", stderr):
            handler.handle_request("/api/seeds", respond)

        line = stderr.getvalue()
        self.assertIn("method=POST path='/api/seeds' status=201 duration_ms=", line)
        self.assertNotIn("token", line)
        self.assertNotIn("seed", line.removeprefix("http method=POST path='/api/seeds'"))

    def test_restart_argv_preserves_arguments_but_uses_package_entrypoint(self) -> None:
        self.assertEqual(
            restart_argv(["/tmp/project_registry_web/__main__.py", "--host", "0.0.0.0", "--reload"]),
            [sys.executable, "-m", "project_registry_web", "--host", "0.0.0.0", "--reload"],
        )


class ProjectRegistryWebTest(_ProjectRegistryWebFixtures, TempRegistryDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed_fixture()
        self.server = types.SimpleNamespace(
            token=self.token,
            db_path=self.db_path,
            open_db=lambda: registry.open_db(),
            jobs=FakeJobManager(),
            mutation_lock=threading.Lock(),
        )

    def test_main_rejects_missing_database_before_starting_server(self) -> None:
        missing_path = self.db_path.parent / "missing" / "survey.db"
        stderr = io.StringIO()
        with (
            mock.patch("project_registry_web.server.sys.argv", [
                "project-registry-web",
                "--token",
                self.token,
                "--db-path",
                str(missing_path),
            ]),
            mock.patch("project_registry_web.server.RegistryWebServer") as server_class,
            mock.patch("project_registry_web.server.sys.stderr", stderr),
        ):
            result = web_main()

        self.assertEqual(result, 2)
        self.assertIn("registry database not found", stderr.getvalue())
        self.assertFalse(missing_path.exists())
        server_class.assert_not_called()

    def test_introduced_review_api_lists_and_resolves_post(self) -> None:
        source = {"uri": "at://did:plc:example/app.bsky.feed.post/review", "author": {"handle": "example.test"},
                  "indexedAt": "2026-09-23T00:00:00Z", "record": {"text": "A post", "facets": []}}
        conn = registry.open_db()
        try:
            item = introduced_review.observe_posts(conn, [source])[0]
            registry.write_sync_state(conn, "introduced", {"query": "test"}, {"review_items": [item], "last_scan_complete": False})
            conn.commit()
        finally:
            conn.close()
        status, listed, _ = self.request("/api/introduced-review")
        self.assertEqual(status, 200)
        self.assertEqual(listed["open_count"], 1)
        self.assertEqual(listed["items"][0]["reason"], "no_url")
        self.assertEqual(listed["items"][0]["post_line"], "")
        self.assertEqual(listed["items"][0]["matched_projects"], [])
        status, result, _ = self.request("/api/introduced-review/action", method="POST", payload={
            "scope_key": listed["items"][0]["scope_key"], "item_id": item["id"], "action": "link",
            "project_key": "proj:111111111111",
        })
        self.assertEqual(status, 200)
        self.assertEqual(result["item"]["status"], "linked")
        status, listed, _ = self.request("/api/introduced-review?status=all")
        self.assertEqual(listed["open_count"], 0)
        self.assertEqual(listed["items"][0]["status"], "linked")
        self.assertEqual(listed["items"][0]["linked_project"]["name"], "Example App")
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(detail["project"]["introduced_url"], "https://bsky.app/profile/example.test/post/review")

    def test_introduced_review_api_projects_named_candidates_without_applying(self) -> None:
        url = "https://duplicate.example"
        text = f"別の紹介\nCandidate: {url}\n別の行"
        source = {"uri": "at://did:plc:example/app.bsky.feed.post/candidate", "author": {"handle": "example.test"},
                  "record": {"text": text, "facets": []}}
        conn = registry.open_db()
        try:
            item = introduced_review.observe_posts(conn, [source])[0]
            registry.write_sync_state(conn, "introduced", {"query": "named"}, {"review_items": [item]})
            conn.commit()
        finally:
            conn.close()
        status, listed, _ = self.request("/api/introduced-review")
        self.assertEqual(status, 200)
        self.assertEqual(listed["items"][0]["post_line"], f"Candidate: {url}")
        self.assertEqual(listed["items"][0]["matched_projects"], [
            {"project_key": "proj:222222222222", "name": "Example Duplicate", "introduced_url": ""}])
        status, detail, _ = self.request("/api/projects/proj:222222222222")
        self.assertEqual(status, 200)
        self.assertEqual(detail["project"]["introduced_url"], "")

    def test_static_assets_and_api_authentication(self) -> None:
        status, html, content_type = self.request_text("/")
        self.assertEqual(status, 200)
        self.assertIn("レビュー", html)
        self.assertIn("type=\"module\"", html)
        self.assertIn("text/html", content_type)

        status, script, content_type = self.request_text("/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(script, (ROOT / "scripts/project_registry_web/static/app.js").read_text())
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
        self.assertEqual(
            set(settings["filter_options"]["tracking_states"]),
            {"active", "stable", "stalled", "out_of_scope", "archived", "ignore"},
        )
        self.assertIn("live", settings["filter_options"]["service_statuses"])
        self.assertIn("core", settings["filter_options"]["contribution_levels"])
        self.assertIn("nsid", settings["filter_options"]["identifier_kinds"])
        self.assertIn("defines_schema", settings["filter_options"]["relation_predicates"])
        self.assertIn("repo", settings["filter_options"]["relation_subject_types"])
        self.assertIn("service", settings["filter_options"]["relation_object_types"])
        self.assertIn("confirm_identifier", settings["filter_options"]["decision_kinds"])
        self.assertIn("review_kind", settings["filter_options"]["decision_target_types"])
        self.assertNotIn("read_only", settings)
        self.assertNotIn("--read-only", build_parser().format_help())

    def test_authorization_uses_constant_time_comparison(self) -> None:
        with mock.patch("project_registry_web.server.hmac.compare_digest", return_value=True) as compare:
            status, _settings, _ = self.request("/api/settings")

        self.assertEqual(status, 200)
        compare.assert_called_once_with(self.token, self.token)

    def test_unexpected_api_exception_keeps_server_traceback(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(RegistryWebHandler, "handle_api_get", side_effect=RuntimeError("diagnostic sentinel")),
            mock.patch("project_registry_web.server.sys.stderr", stderr),
        ):
            status, payload, _ = self.request("/api/settings")

        self.assertEqual(status, 500)
        self.assertEqual(payload["error"]["code"], "internal_error")
        self.assertIn("Traceback (most recent call last)", stderr.getvalue())
        self.assertIn("RuntimeError: diagnostic sentinel", stderr.getvalue())

    def test_post_body_is_read_before_mutation_lock(self) -> None:
        lock = TrackingLock()
        self.server.mutation_lock = lock
        original = RegistryWebHandler.read_json

        def assert_outside_lock(handler):
            self.assertEqual(lock.entries, 0)
            return original(handler)

        with mock.patch.object(RegistryWebHandler, "read_json", assert_outside_lock):
            status, _payload, _ = self.request(
                "/api/projects/proj:111111111111/tracking-state",
                method="POST",
                payload={"tracking_state": "stable"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(lock.entries, 1)

    def test_build_information_only_reads_git_checkout(self) -> None:
        root = self.db_path.parent / "source"
        root.mkdir()
        self.assertEqual(build_information(root)["commit"], "")
        (root / ".git").mkdir()
        completed = [
            mock.Mock(stdout="abc123def456\n"),
            mock.Mock(stdout=" M scripts/example.py\n"),
        ]
        with mock.patch("project_registry_web.server.subprocess.run", side_effect=completed) as run:
            info = build_information(root)
        self.assertEqual(info["commit"], "abc123def456")
        self.assertEqual(info["python_executable"], sys.executable)
        self.assertEqual(info["python_base_prefix"], sys.base_prefix)
        self.assertTrue(info["dirty"])
        self.assertEqual(run.call_count, 2)

    def test_manual_server_reload_requires_confirmation(self) -> None:
        requested: list[str] = []
        self.server.request_process_reload = lambda reason: requested.append(reason) or "reload:test"

        status, payload, _ = self.request("/api/reload", method="POST", payload={"confirm": False})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "validation_error")
        self.assertEqual(requested, [])

        status, payload, _ = self.request("/api/reload", method="POST", payload={"confirm": True})
        self.assertEqual(status, 202)
        self.assertEqual(payload["reload_id"], "reload:test")
        self.assertEqual(requested, ["manual API request"])

    def test_manual_server_reload_stops_jobs_and_execs_same_argv(self) -> None:
        server = RegistryWebServer.__new__(RegistryWebServer)
        server.reload_lock = threading.Lock()
        server.reload_requested = False
        server.jobs = mock.Mock()
        with mock.patch("project_registry_web.server.threading.Thread") as thread:
            reload_id = server.request_process_reload("test")
            target = thread.call_args.kwargs["target"]
            thread.return_value.start.assert_called_once_with()
        with (
            mock.patch("project_registry_web.server.time.sleep"),
            mock.patch("project_registry_web.server.os.execv") as execv,
            mock.patch("project_registry_web.server.sys.argv", ["/path/project_registry_web/__main__.py", "--port", "8888"]),
            mock.patch("builtins.print"),
        ):
            target()

        self.assertTrue(reload_id)
        server.jobs.close.assert_called_once_with()
        execv.assert_called_once_with(
            sys.executable,
            [sys.executable, "-m", "project_registry_web", "--port", "8888"],
        )
        with self.assertRaisesRegex(ValueError, "already in progress"):
            server.request_process_reload("again")

    def test_sigterm_requests_server_shutdown_once(self) -> None:
        server = mock.Mock()
        with (
            mock.patch("project_registry_web.server.signal.getsignal", return_value="previous"),
            mock.patch("project_registry_web.server.signal.signal") as set_signal,
            mock.patch("project_registry_web.server.threading.Thread") as thread,
        ):
            previous = install_sigterm_shutdown(server)
            handler = set_signal.call_args.args[1]
            handler(signal.SIGTERM, None)
            handler(signal.SIGTERM, None)

        self.assertEqual(previous, "previous")
        thread.assert_called_once_with(
            target=server.shutdown,
            name="registry-web-shutdown",
            daemon=True,
        )
        thread.return_value.start.assert_called_once_with()

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
        status, _, _ = self.request("/api/jobs/sync", method="POST", payload={"source": "lexicons", "execute": False})
        self.assertEqual(status, 202)
        self.assertEqual(self.server.jobs.parameters["source"], "lexicons")
        self.assertFalse(self.server.jobs.execute)
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

    def test_browser_service_verification_job_requires_saved_project_url(self) -> None:
        status, job, _ = self.request(
            "/api/jobs/verify-service",
            method="POST",
            payload={
                "project_key": "proj:111111111111",
                "service_url": "https://app.example.com",
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(job["operation"], "verify_service")
        self.assertEqual(self.server.jobs.parameters, {
            "project_key": "proj:111111111111",
            "service_url": "https://app.example.com",
        })
        self.assertTrue(self.server.jobs.execute)

        status, error, _ = self.request(
            "/api/jobs/verify-service",
            method="POST",
            payload={
                "project_key": "proj:111111111111",
                "service_url": "https://arbitrary.example",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "validation_error")

    def test_job_schedule_endpoints_validate_preset_allowlist(self) -> None:
        status, schedules, _ = self.request("/api/job-schedules")
        self.assertEqual(status, 200)
        self.assertEqual(schedules["items"][0]["preset_id"], "enrich_all")

        status, updated, _ = self.request(
            "/api/job-schedules",
            method="POST",
            payload={"items": [{"preset_id": "enrich_all", "enabled": True, "week_of_month": 0, "weekday": 4, "time": "03:00", "timezone": "Asia/Tokyo"}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.server.jobs.parameters["items"][0]["preset_id"], "enrich_all")

        status, queued, _ = self.request("/api/job-schedules/enrich_all/run", method="POST", payload={})
        self.assertEqual(status, 202)
        self.assertEqual(queued["queue"][0]["preset_id"], "enrich_all")

        status, error, _ = self.request("/api/job-schedules/delete_everything/run", method="POST", payload={})
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
        self.assertNotIn("manual_seed", detail["overview"])
        owned_nsid = next(
            item for item in detail["identifiers"]
            if item["value"] == "app.example.post" and "owned_nsid" in item["roles"]
        )
        self.assertEqual(owned_nsid["classification"], "confirmed")
        candidate_values = {
            item["value"] for item in detail["identifiers"] if item["classification"] == "candidate"
        }
        self.assertIn("https://app.example.com", candidate_values)

    def test_search_matches_saved_description_fields_only(self) -> None:
        conn = registry.open_db()
        try:
            for suffix, field, value, tracking_state in (
                ("tagline", "tagline", "TaglineOnly SharedDescription", "active"),
                ("summary", "summary", "SummaryOnly SharedDescription", "active"),
                ("features", "features", ["FeatureOnly SharedDescription", "Second SharedDescription"], "stalled"),
                ("noise", "system_notes", ["HiddenOnly SharedDescription"], "active"),
            ):
                project = project_record.default_project_record(
                    f"proj:description{suffix}", "nsid", f"app.fixture.{suffix}"
                )
                project["name"] = f"Fixture {suffix}"
                project["tracking_state"] = tracking_state
                project[field] = value
                if suffix == "noise":
                    project["notes"] = "HiddenOnly"
                project_record.save_project(conn, project)
            conn.commit()
        finally:
            conn.close()

        for query, suffix in (
            ("taglineonly", "tagline"),
            ("SUMMARYONLY", "summary"),
            ("featureonly", "features"),
        ):
            status, payload, _ = self.request(f"/api/projects?q={query}")
            self.assertEqual(status, 200)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["project_key"], f"proj:description{suffix}")

        status, payload, _ = self.request("/api/projects?q=HiddenOnly")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)

        status, payload, _ = self.request("/api/projects?q=app.fixture.tagline")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["project_key"], "proj:descriptiontagline")

        status, payload, _ = self.request("/api/projects?q=shareddescription&limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["returned_count"], 2)
        self.assertEqual(len(payload["items"]), 2)

        status, payload, _ = self.request("/api/projects?q=shareddescription&tracking_states=active")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(
            {item["project_key"] for item in payload["items"]},
            {"proj:descriptiontagline", "proj:descriptionsummary"},
        )

    def test_search_candidate_badge_ignores_confirmed_duplicate_rows(self) -> None:
        conn = registry.open_db()
        try:
            project = project_record.default_project_record("proj:duplicatecandidate", "nsid", "app.example.post")
            registry.add_manual_seed(project, "nsid", "app.example.post", source="fixture")
            project["website_url"] = "https://app.example.com"
            project["service_urls"] = ["https://app.example.com"]
            project_record.save_project(conn, project)
        finally:
            conn.close()

        status, payload, _ = self.request("/api/projects?q=duplicatecandidate")
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"][0]["project_key"], "proj:duplicatecandidate")
        self.assertFalse(payload["items"][0]["has_candidates"])

        status, payload, _ = self.request("/api/projects?q=duplicatecandidate&has_candidates=yes")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)

    def test_project_detail_separates_operator_and_system_notes(self) -> None:
        conn = registry.open_db()
        try:
            project = project_record.fetch_project(conn, "proj:111111111111")
            assert project is not None
            project["notes"] = "operator note"
            project["system_notes"] = [
                "merged from proj:123456789abc at 2026-07-12T05:41:55+00:00"
            ]
            project_record.save_project(conn, project)
        finally:
            conn.close()

        status, detail, _ = self.request("/api/projects/proj:111111111111")

        self.assertEqual(status, 200)
        self.assertEqual(detail["project"]["profile"]["core"]["notes"], "operator note")
        self.assertIn(
            "merged from proj:123456789abc at 2026-07-12T05:41:55+00:00",
            detail["project"]["system_notes"],
        )

    def test_identifier_entries_keep_role_classification_association(self) -> None:
        conn = registry.open_db()
        ordinal = conn.execute(
            "SELECT MAX(ordinal) + 1 FROM project_identifiers WHERE project_key = ?",
            ("proj:111111111111",),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO project_identifiers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "proj:111111111111", ordinal, "repo_url", "https://github.com/example/app",
                "source_repository_record", "candidate", 0, "fixture", "{}",
                "2026-06-14T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()

        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        repository = next(
            item for item in detail["identifiers"]
            if item["value"] == "https://github.com/example/app"
        )
        self.assertEqual(
            {entry["classification"] for entry in repository["entries"]},
            {"confirmed", "candidate"},
        )
        self.assertEqual(
            {entry["role"] for entry in repository["entries"]},
            {"project_repository"},
        )
        self.assertEqual(
            {entry["role_label"] for entry in repository["entries"]},
            {"repository", "source"},
        )
        candidate = next(entry for entry in repository["entries"] if entry["classification"] == "candidate")
        self.assertIn("source_repository_record", candidate["source_roles"])
        self.assertFalse(candidate["actions"]["confirm"])
        self.assertFalse(candidate["actions"]["reject"])
        self.assertEqual(repository["actions"]["scope"], "identifier")

    def test_website_scalar_does_not_mark_matching_service_as_primary(self) -> None:
        conn = registry.open_db()
        try:
            project = project_record.fetch_project(conn, "proj:111111111111")
            assert project is not None
            project["website_url"] = "https://app.example.com"
            project["service_urls"] = ["https://app.example.com"]
            project_record.save_project(conn, project, preserve_timestamps=True)
            conn.commit()
        finally:
            conn.close()

        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        self.assertEqual(detail["overview"]["confirmed_services"], ["https://app.example.com"])
        service = next(item for item in detail["identifiers"] if item["value"] == "https://app.example.com")
        self.assertFalse(service["is_primary"])
        self.assertEqual([entry["role"] for entry in service["entries"]], ["service"])

    def test_identity_identifier_groups_handle_with_account_did(self) -> None:
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        identity = next(item for item in detail["identifiers"] if item["value"] == "did:plc:developer")

        self.assertEqual(identity["family"], "identity")
        self.assertIn("account", identity["kinds"])
        self.assertIn("handle", identity["kinds"])
        self.assertIn("did", identity["kinds"])
        self.assertFalse(identity["actions"]["confirm"])
        self.assertFalse(any(item["value"] == "dev.example.com" for item in detail["identifiers"]))

    def test_project_detail_hides_malformed_developer_identifier_rows(self) -> None:
        conn = registry.open_db()
        try:
            conn.execute(
                "INSERT INTO project_identifiers(project_key, ordinal, kind, value, role, classification, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "proj:111111111111",
                    98,
                    "account",
                    "${encodeURIComponent(profileData.handle)}`",
                    "developer_account",
                    "candidate",
                    '{"handle":"${encodeURIComponent(profileData.handle)}`","source":"service_raw_url:https://skyspace.me"}',
                ),
            )
            conn.execute(
                "INSERT INTO project_identifiers(project_key, ordinal, kind, value, role, classification, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "proj:111111111111",
                    99,
                    "handle",
                    "${encodeuricomponent(profiledata.handle)}`",
                    "developer_handle",
                    "candidate",
                    '{"account":"${encodeURIComponent(profileData.handle)}`"}',
                ),
            )
            conn.commit()
        finally:
            conn.close()

        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        self.assertFalse(any("encodeuricomponent" in item["value"].lower() for item in detail["identifiers"]))

    def test_authority_domain_observation_is_not_a_splitable_component_identifier(self) -> None:
        conn = registry.open_db()
        try:
            project = project_record.fetch_project(conn, "proj:111111111111")
            assert project is not None
            registry.add_manual_seed(project, "domain", "app.example.com", source="fixture")
            project_record.save_project(conn, project, preserve_timestamps=True)
            conn.commit()
        finally:
            conn.close()

        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        self.assertFalse(any(item["value"] == "app.example.com" for item in detail["identifiers"]))

    def test_merge_pair_includes_both_projects_and_result_preview(self) -> None:
        status, payload, _ = self.request(
            "/api/merge-pair?source_project_key=proj:111111111111&target_project_key=proj:222222222222"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["source"]["name"], "Example App")
        self.assertEqual(payload["target"]["name"], "Example Duplicate")
        self.assertTrue(payload["source"]["identifiers"])
        identity = next(item for item in payload["source"]["identifiers"] if item["value"] == "did:plc:developer")
        self.assertEqual(identity["family"], "identity")
        self.assertIn("account", identity["kinds"])
        self.assertIn("handle", identity["kinds"])
        self.assertIn("did", identity["kinds"])
        self.assertFalse(any(item["value"] == "dev.example.com" for item in payload["source"]["identifiers"]))
        self.assertFalse(payload["first_creator_details_included"])
        self.assertEqual(payload["merged_preview"]["target_project_key"], "proj:222222222222")
        self.assertEqual(payload["merged_preview"]["merged_name"], "Example Duplicate")

        reverse_status, reverse_payload, _ = self.request(
            "/api/merge-pair?source_project_key=proj:222222222222&target_project_key=proj:111111111111"
        )
        self.assertEqual(reverse_status, 200)
        self.assertEqual(reverse_payload["source"]["project_key"], "proj:222222222222")
        self.assertEqual(reverse_payload["target"]["project_key"], "proj:111111111111")
        self.assertEqual(reverse_payload["merged_preview"]["target_project_key"], "proj:111111111111")

    def test_merge_pair_includes_multi_tenant_suffix_suggestion(self) -> None:
        conn = registry.open_db()
        try:
            source = project_record.default_project_record("proj:foofoofoofoo", "service_url", "https://foo.example.com")
            registry.add_manual_seed(source, "service_url", "https://foo.example.com", source="test")
            target = project_record.default_project_record("proj:barbarbarbar", "service_url", "https://bar.example.com")
            registry.add_manual_seed(target, "service_url", "https://bar.example.com", source="test")
            project_record.save_project(conn, source)
            project_record.save_project(conn, target)
            conn.commit()
        finally:
            conn.close()

        status, payload, _ = self.request(
            "/api/merge-pair?source_project_key=proj:foofoofoofoo&target_project_key=proj:barbarbarbar"
        )

        self.assertEqual(status, 200)
        self.assertEqual([item["suffix"] for item in payload["suggested_multi_tenant_suffixes"]], ["example.com"])

    def test_can_add_multi_tenant_suffix_from_web_api(self) -> None:
        original_domain_overrides_path = domain_policy.DOMAIN_OVERRIDES_PATH
        original_suffixes = domain_policy._CONFIGURED_MULTI_TENANT_SUFFIXES
        domain_overrides_path = self.db_path.with_name("domain-overrides.json")
        domain_overrides_path.write_text('{"custom_multi_tenant_suffixes": []}\n')
        try:
            domain_policy.DOMAIN_OVERRIDES_PATH = domain_overrides_path
            domain_policy.reset_domain_config_cache()
            conn = registry.open_db()
            project = self.make_project("proj:suffixrefresh", "service_url", "https://foo.example.com")
            project["service_status_evidence"] = ["http_200", "redirect_same_site_family"]
            project["service_verification_results"] = [{
                "requested_url": "https://foo.example.com",
                "final_url": "https://bar.example.com",
                "redirected": True,
                "redirect_same_site_family": True,
                "service_status_evidence": ["http_200", "redirect_same_site_family"],
            }]
            project_record.save_project(conn, project)
            conn.close()

            status, payload, _ = self.request(
                "/api/domain-overrides/multi-tenant-suffixes",
                method="POST",
                payload={"suffix": "Example.COM."},
                include_revision=False,
            )

            self.assertEqual(status, 200)
            self.assertTrue(payload["added"])
            self.assertEqual(payload["suffix"], "example.com")
            self.assertEqual(payload["same_site_refresh"]["changed_project_count"], 1)
            self.assertEqual(json.loads(domain_overrides_path.read_text())["custom_multi_tenant_suffixes"], ["example.com"])
            conn = registry.open_db()
            refreshed = project_record.fetch_project(conn, project["project_key"])
            conn.close()
            assert refreshed is not None
            self.assertFalse(refreshed["service_verification_results"][0]["redirect_same_site_family"])
            self.assertNotIn("redirect_same_site_family", refreshed["service_status_evidence"])

            status, retry_payload, _ = self.request(
                "/api/domain-overrides/multi-tenant-suffixes",
                method="POST",
                payload={"suffix": "example.com"},
                include_revision=False,
            )
            self.assertEqual(status, 200)
            self.assertFalse(retry_payload["added"])
            self.assertEqual(retry_payload["same_site_refresh"]["changed_project_count"], 0)
        finally:
            domain_policy.DOMAIN_OVERRIDES_PATH = original_domain_overrides_path
            domain_policy._CONFIGURED_MULTI_TENANT_SUFFIXES = original_suffixes
            domain_policy.reset_domain_extractors()

    def test_merge_pair_can_include_first_creator_details_on_request(self) -> None:
        status, payload, _ = self.request(
            "/api/merge-pair?source_project_key=proj:111111111111&target_project_key=proj:222222222222&include_first_creator_details=1"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["first_creator_details_included"])
        self.assertIn("source_collection_first_creator_dids", payload)
        self.assertIn("target_collection_first_creator_dids", payload)
        self.assertIn("source_collection_first_creator_details", payload)
        self.assertIn("target_collection_first_creator_details", payload)

    def test_investigation_history_detail_and_replay(self) -> None:
        status, runs, _ = self.request("/api/projects/proj:111111111111/investigation-runs")
        self.assertEqual(status, 200)
        self.assertEqual(runs["count"], 1)
        self.assertNotIn("bundle", runs["items"][0])
        self.assertGreaterEqual(runs["items"][0]["total_ms"], 1200)
        run_id = runs["items"][0]["run_id"]

        status, detail, _ = self.request(f"/api/investigation-runs/{run_id}?evidence_limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(detail["collector_count"] if "collector_count" in detail else len(detail["collectors"]), 2)
        self.assertEqual(detail["failure_count"], 1)
        self.assertLessEqual(len(detail["records"]), 1)
        self.assertGreaterEqual(detail["metrics"]["total_ms"], 1200)
        self.assertEqual(detail["metrics"]["llm_requests"][0]["prompt_bytes"], 2048)

        status, replay, _ = self.request(f"/api/investigation-runs/{run_id}/replay")
        self.assertEqual(status, 200)
        self.assertEqual(replay["run_id"], run_id)
        self.assertIn("diff", replay)

    def test_merge_analysis_is_loaded_separately_from_project_detail(self) -> None:
        original = project_merge.compute_duplicate_candidates
        project_merge.compute_duplicate_candidates = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("detail computed duplicate candidates")
        )
        try:
            status, detail, _ = self.request("/api/projects/proj:111111111111")
        finally:
            project_merge.compute_duplicate_candidates = original
        self.assertEqual(status, 200)
        self.assertTrue(detail["merge_analysis_pending"])
        self.assertEqual(detail["explanations"]["topics"]["merge_candidate"]["conclusion"], "pending")

        status, analysis, _ = self.request("/api/projects/proj:111111111111/merge-analysis")
        self.assertEqual(status, 200)
        self.assertEqual(analysis["project_key"], "proj:111111111111")
        self.assertTrue(analysis["revision"])
        self.assertIn("merge_recommendation", analysis)
        self.assertNotEqual(analysis["explanation"]["conclusion"], "pending")

    def test_candidate_confirmed_merge_analysis_pair_and_moderate_are_read_only(self) -> None:
        source, target = "proj:crosssource", "proj:crosstarget"
        value = "https://github.com/cross/one-resource"
        with contextlib.closing(registry.open_db()) as conn:
            left = self.make_project(source, "repo_url", "https://github.com/left/alpha")
            right = self.make_project(target, "repo_url", "https://github.com/right/beta")
            left["candidate_project_repository_urls"] = [value]
            right["project_repository_urls"] = [value]
            project_record.save_project(conn, left)
            project_record.save_project(conn, right)
            conn.commit()
            before = list(conn.iterdump())
        for key, other, classification in ((source, target, "candidate"), (target, source, "confirmed")):
            status, analysis, _ = self.request(f"/api/projects/{key}/merge-analysis")
            self.assertEqual(status, 200)
            candidate = next(item for item in analysis["duplicates"] if item["project_key"] == other)
            self.assertFalse(candidate["auto_merge_ready"])
            self.assertEqual(candidate["identifier_matches"][0]["value"], value)
            self.assertEqual(candidate["identifier_matches"][0]["project_classification"], classification)
            status, pair, _ = self.request(f"/api/merge-pair?source_project_key={key}&target_project_key={other}")
            self.assertEqual(status, 200)
            self.assertEqual(pair["source_pair_candidate"]["identifier_matches"], candidate["identifier_matches"])
        for mode, expected in (("strict", False), ("moderate", True), ("broad", True)):
            status, plans, _ = self.request(f"/api/merge-batch?mode={mode}")
            self.assertEqual(status, 200)
            matches = [item for item in plans["items"] if {item["source_project_key"], item["target_project_key"]} == {source, target}]
            self.assertEqual(bool(matches), expected)
            if matches:
                self.assertEqual(matches[0]["identifier_matches"][0]["value"], value)
        with contextlib.closing(registry.open_db()) as conn:
            self.assertEqual(list(conn.iterdump()), before)
            left = project_record.fetch_project(conn, source)
            left["separate_project_keys"] = [target]
            project_record.save_project(conn, left)
            conn.commit()
        status, analysis, _ = self.request(f"/api/projects/{source}/merge-analysis")
        self.assertEqual(status, 200)
        self.assertNotIn(target, [item["project_key"] for item in analysis["duplicates"]])

    def test_service_and_ownership_review_uses_saved_evidence(self) -> None:
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        review = detail["service_ownership_review"]
        self.assertEqual(review["service_verification_status"], "observed")
        self.assertEqual(review["oauth_status"], "not_observed")
        owned = next(item for item in review["nsid_ownership"] if item["nsid"] == "app.example.post")
        self.assertEqual(owned["evidence_status"], "observed")
        self.assertTrue(any(item["source"] == "service_static" for item in review["service_verifications"]))
        self.assertFalse(detail["explanations"]["network_used"])
        self.assertIn("merge_candidate", detail["explanations"]["topics"])

    def test_oauth_base_only_declaration_is_observed_in_detail(self) -> None:
        from project_registry_core import oauth_metadata
        with contextlib.closing(registry.open_db()) as conn:
            project = project_record.fetch_project(conn, "proj:111111111111")
            project["oauth_client_metadata"] = [oauth_metadata.observe(
                "https://example.com/client.json", {"scope": "atproto"}, "2026-09-22T01:00:00+00:00",
            )]
            project_record.save_project(conn, project)
        with mock.patch.object(oauth_metadata, "_parse_json", side_effect=AssertionError("API must use saved facts")):
            status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        review = detail["service_ownership_review"]
        self.assertEqual(review["oauth_status"], "observed")
        self.assertEqual(review["oauth_permissions"], [])
        self.assertTrue(review["oauth_clients"][0]["analysis"]["atproto_only"])

    def test_usage_passages_are_served_from_saved_observations(self) -> None:
        from project_registry_core import usage_material
        with contextlib.closing(registry.open_db()) as conn:
            project = project_record.fetch_project(conn, "proj:111111111111")
            project["service_verification_results"] = [{"requested_url": "https://example.com",
                "supports_project": True, "status_code": 200, "lane": "static", "observed_at": "2026-09-22",
                "atproto_text_candidates": usage_material.from_html("<p>Plans to store notes on a PDS.</p>")}]
            project["lexicon_schema_samples"] = [{"nsid": "app.example.note", "defs": {"main": {
                "type": "record", "description": "Reading notes", "record": {"properties": "... 2 keys"}}}}]
            project["recent_record_samples"] = [{"collection": "app.example.note", "did": "did:plc:example", "rkey": "one",
                                                  "record": {"$type": "app.example.note", "text": "note"}}]
            project_record.save_project(conn, project)
        with mock.patch.object(usage_material, "page_text", side_effect=AssertionError("should use saved extraction")):
            status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        material = detail["service_ownership_review"]["atproto_material"]
        self.assertEqual(material["status"], "matches")
        self.assertEqual(material["sources"][0]["passages"][0]["text"], "Plans to store notes on a PDS.")
        self.assertEqual(material["collections"]["rows"][0]["collection"], "app.example.note")
        self.assertEqual(material["collections"]["rows"][0]["schemas"][0]["record_description"], "Reading notes")
        self.assertEqual(material["collections"]["rows"][0]["record_samples"][0]["fields"], ["text"])

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

    def test_search_filters_by_operator_notes_presence(self) -> None:
        conn = registry.open_db()
        try:
            project = project_record.fetch_project(conn, "proj:111111111111")
            assert project is not None
            project["notes"] = "follow up with maintainer"
            project_record.save_project(conn, project)
        finally:
            conn.close()

        status, payload, _ = self.request("/api/projects?has_notes=yes")
        self.assertEqual(status, 200)
        self.assertEqual(
            [item["project_key"] for item in payload["items"]],
            ["proj:111111111111"],
        )

        status, payload, _ = self.request("/api/projects?has_notes=no")
        self.assertEqual(status, 200)
        self.assertNotIn("proj:111111111111", {item["project_key"] for item in payload["items"]})

    def test_project_search_supports_first_seen_sort(self) -> None:
        conn = registry.open_db()
        conn.execute(
            "UPDATE projects SET first_seen_at = ?, last_seen_at = ? WHERE project_key = ?",
            ("2026-06-01T00:00:00+00:00", "2026-06-20T00:00:00+00:00", "proj:111111111111"),
        )
        conn.execute(
            "UPDATE projects SET first_seen_at = ?, last_seen_at = ? WHERE project_key = ?",
            ("2026-06-10T00:00:00+00:00", "2026-06-19T00:00:00+00:00", "proj:222222222222"),
        )
        conn.commit()
        conn.close()

        status, payload, _ = self.request("/api/projects?sort=first_seen_desc")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sort"], "first_seen_desc")
        self.assertEqual(payload["items"][0]["project_key"], "proj:222222222222")
        self.assertEqual(payload["items"][0]["first_seen_at"], "2026-06-10T00:00:00+00:00")

        status, payload, _ = self.request("/api/projects?sort=first_seen_asc")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sort"], "first_seen_asc")
        self.assertEqual(payload["items"][0]["project_key"], "proj:111111111111")

    def test_search_does_not_hydrate_all_projects(self) -> None:
        original = project_record.list_projects
        project_record.list_projects = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("hydrated all projects"))
        try:
            status, payload, _ = self.request("/api/projects?q=app.example.post")
        finally:
            project_record.list_projects = original
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"][0]["project_key"], "proj:111111111111")

    def test_stale_revision_rejects_write(self) -> None:
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        stale_revision = detail["revision"]

        conn = registry.open_db()
        project = project_record.fetch_project(conn, "proj:111111111111")
        assert project is not None
        project["name"] = "Changed elsewhere"
        project_record.save_project(conn, project, preserve_timestamps=True)
        conn.close()

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/tracking-state",
            method="POST",
            payload={"tracking_state": "stable", "expected_revision": stale_revision},
            include_revision=False,
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "stale_project")

    def test_project_mutations_pass_through_server_lock(self) -> None:
        lock = TrackingLock()
        self.server.mutation_lock = lock
        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/tracking-state",
            method="POST",
            payload={"tracking_state": "stable"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["detail"]["project"]["tracking_state"], "stable")
        self.assertEqual(lock.entries, 1)

    def test_tracking_confirm_reject_and_decision_roundtrip(self) -> None:
        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/tracking-state",
            method="POST",
            payload={"tracking_state": "stable", "note": "web review"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["detail"]["project"]["tracking_state"], "stable")

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
        self.assertEqual(payload["detail"]["overview"]["promotion_defer"]["last_reason"], "wait for editorial review")
        self.assertEqual(payload["detail"]["overview"]["promotion_defer"]["default_days"], 30)

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/lower-promotion-priority",
            method="POST",
            payload={"note": "best in a grouped feature"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["detail"]["promotion_policy"]["priority_group"], "lowered")
        self.assertEqual(payload["detail"]["promotion_policy"]["priority_reason"], "best in a grouped feature")
        self.assertTrue(any(
            item["kind"] == "lower_promotion_priority"
            for item in payload["detail"]["decisions"]["active"]
        ))

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/clear-promotion-priority",
            method="POST",
            payload={},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["detail"]["promotion_policy"]["priority_group"], "normal")

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
        self.assertIsNone(project_record.fetch_project(conn, split_key))
        conn.close()

    def test_confirm_then_merge_preserves_source_identifier_and_adoption(self) -> None:
        url = "https://example.com/docs"
        status, _, _ = self.request(
            "/api/projects/proj:111111111111/confirm-candidate",
            method="POST",
            payload={"identifier_type": "service_url", "value": url},
        )
        self.assertEqual(status, 200)
        conn = registry.open_db()
        before = dict(conn.execute(
            "SELECT * FROM project_identifier_adoptions WHERE project_key=? AND value=?",
            ("proj:111111111111", url),
        ).fetchone())
        conn.close()
        status, _, _ = self.request(
            "/api/merge-batch/action", method="POST",
            payload={"action": "merge", "source_project_key": "proj:111111111111", "target_project_key": "proj:222222222222"},
        )
        self.assertEqual(status, 200)
        status, detail, _ = self.request("/api/projects/proj:222222222222")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["value"] == url and item["classification"] == "confirmed" for item in detail["identifiers"]))
        self.assertTrue(any(item["kind"] == "confirm_identifier" and item["target_value"] == url for item in detail["decisions"]["active"]))
        conn = registry.open_db()
        after = [dict(row) for row in conn.execute(
            "SELECT * FROM project_identifier_adoptions WHERE project_key=? AND value=?",
            ("proj:222222222222", url),
        )]
        conn.close()
        self.assertEqual(after, [dict(before, project_key="proj:222222222222")])

    def test_merge_action_returns_next_review_for_merged_project(self) -> None:
        conn = registry.open_db()
        source = self.make_project("proj:mergechain01", "service_url", "https://merge-source.example")
        target = self.make_project("proj:mergechain02", "service_url", "https://merge-target.example")
        remaining = self.make_project("proj:mergechain03", "service_url", "https://merge-remaining.example")
        shared_repo = "https://github.com/example/merge-chain"
        for project in (source, target, remaining):
            registry.confirm_project_repository_url(project, shared_repo, "test")
            project_record.save_project(conn, project)
        conn.commit()
        conn.close()

        status, payload, _ = self.request(
            "/api/merge-batch/action",
            method="POST",
            payload={
                "action": "merge",
                "source_project_key": source["project_key"],
                "target_project_key": target["project_key"],
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["remaining_merge_candidate_count"], 1)
        self.assertEqual(
            {
                payload["next_merge_review"]["source_project_key"],
                payload["next_merge_review"]["target_project_key"],
            },
            {target["project_key"], remaining["project_key"]},
        )

    def test_targeted_review_decisions_and_developer_confirmation(self) -> None:
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        relation_target = detail["relations"][0]["review_target"]
        failure_target = detail["investigation"]["failures"][0]["review_target"]

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/dismiss-review-target",
            method="POST",
            payload={
                "expected_revision": detail["revision"],
                "target_type": relation_target["target_type"],
                "target_value": relation_target["target_value"],
                "note": "reviewed relation",
            },
        )
        self.assertEqual(status, 200)
        active = payload["detail"]["decisions"]["active"]
        self.assertTrue(any(item["kind"] == "dismiss_review_target" for item in active))

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/defer-review-target",
            method="POST",
            payload={
                "expected_revision": payload["detail"]["revision"],
                "target_type": failure_target["target_type"],
                "target_value": failure_target["target_value"],
                "note": "retry later",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(item["kind"] == "defer_review_target" for item in payload["detail"]["decisions"]["active"]))

        account = next(item for item in payload["detail"]["identifiers"] if item["value"] == "did:plc:developer")
        self.assertFalse(account["actions"]["confirm"])
        developer_entry = next(
            entry for entry in account["entries"]
            if entry["classification"] == "candidate" and entry["role"] == "developer_account"
        )
        self.assertTrue(developer_entry["actions"]["confirm"])
        self.assertEqual(developer_entry["actions"]["identifier_type"], "account")
        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/confirm-candidate",
            method="POST",
            payload={
                "expected_revision": payload["detail"]["revision"],
                "identifier_type": "account",
                "value": "did:plc:developer",
            },
        )
        self.assertEqual(status, 200)
        confirmed = next(item for item in payload["detail"]["identifiers"] if item["value"] == "did:plc:developer")
        self.assertEqual(confirmed["classification"], "confirmed")

    def test_identity_identifier_defer_accepts_account_and_handle_types(self) -> None:
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)

        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/defer-identifier",
            method="POST",
            payload={
                "expected_revision": detail["revision"],
                "identifier_type": "account",
                "value": "did:plc:developer",
                "days": 30,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(
            item["kind"] == "defer_identifier"
            and item["target_type"] == "account"
            and item["target_value"] == "did:plc:developer"
            for item in payload["detail"]["decisions"]["active"]
        ))

        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        status, payload, _ = self.request(
            "/api/projects/proj:111111111111/defer-identifier",
            method="POST",
            payload={
                "expected_revision": detail["revision"],
                "identifier_type": "handle",
                "value": "dev.example.com",
                "days": 30,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(
            item["kind"] == "defer_identifier"
            and item["target_type"] == "handle"
            and item["target_value"] == "dev.example.com"
            for item in payload["detail"]["decisions"]["active"]
        ))

    def test_add_manual_seed_to_existing_project(self) -> None:
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        status, payload, _ = self.request(
            "/api/seeds",
            method="POST",
            payload={
                "seed": "https://github.com/example/second",
                "project_key": "proj:111111111111",
                "expected_revision": detail["revision"],
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["project_key"], "proj:111111111111")
        conn = registry.open_db()
        try:
            project = project_record.fetch_project(conn, "proj:111111111111")
        finally:
            conn.close()
        self.assertIn("https://github.com/example/second", project["project_repository_urls"])
        self.assertTrue(registry.identifier_has_active_confirm_decision(
            project, "repo_url", "https://github.com/example/second"
        ))

    def test_description_edit_preview_protection_and_stale_guard(self) -> None:
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        revision = detail["revision"]
        fields = {"tagline": "読書記録共有", "summary": "読書記録を共有していたサービス。独自Lexiconを公開している。",
                  "features": ["独自Lexicon"]}
        payload = {"expected_revision": revision, "name": "Example App", "notes": "", "introduced_url": "",
                   "description": fields, "automatic_description": False}
        status, preview, _ = self.request("/api/projects/proj:111111111111/profile-preview", method="POST", payload=payload)
        self.assertEqual(status, 200)
        self.assertIn("summary", [item["field"] for item in preview["result"]["changes"]])
        status, saved, _ = self.request("/api/projects/proj:111111111111/profile", method="POST", payload=payload)
        self.assertEqual(status, 200)
        self.assertEqual(saved["detail"]["project"]["profile"]["core"]["summary"], fields["summary"])
        self.assertEqual(saved["detail"]["project"]["features"], fields["features"])
        self.assertFalse(saved["detail"]["project"]["editorial"]["automatic"])
        status, rejected, _ = self.request("/api/projects/proj:111111111111/profile", method="POST",
                                           payload={**payload, "description": {"summary": "古い編集"}}, include_revision=False)
        self.assertEqual(status, 409)
        status, current, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(current["project"]["profile"]["core"]["summary"], fields["summary"])

    def test_profile_preview_apply_and_stale_guard(self) -> None:
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        revision = detail["revision"]
        introduced_url = "https://bsky.app/profile/yamarten.bsky.social/post/3ltestpost"
        status, preview, _ = self.request(
            "/api/projects/proj:111111111111/profile-preview",
            method="POST",
            payload={
                "expected_revision": revision,
                "name": "Edited App",
                "introduced_url": introduced_url,
                "notes": "operator note",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [item["field"] for item in preview["result"]["changes"]],
            ["name", "notes", "introduced_url"],
        )

        status, applied, _ = self.request(
            "/api/projects/proj:111111111111/profile",
            method="POST",
            payload={
                "expected_revision": revision,
                "name": "Edited App",
                "introduced_url": introduced_url,
                "notes": "operator note",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(applied["detail"]["project"]["name"], "Edited App")
        self.assertEqual(applied["detail"]["project"]["introduced_url"], introduced_url)
        self.assertEqual(applied["detail"]["project"]["profile"]["core"]["notes"], "operator note")
        conn = registry.open_db()
        try:
            edited_project = project_record.fetch_project(conn, "proj:111111111111")
        finally:
            conn.close()
        self.assertIn(
            {"value": "Edited App", "source": "operator_edit"},
            [
                {"value": item.get("value"), "source": item.get("source")}
                for item in edited_project["name_candidates"]
            ],
        )

        status, stale, _ = self.request(
            "/api/projects/proj:111111111111/profile",
            method="POST",
            payload={"expected_revision": revision, "name": "Stale", "notes": ""},
            include_revision=False,
        )
        self.assertEqual(status, 409)
        self.assertEqual(stale["error"]["code"], "stale_project")

    def test_profile_change_between_request_precondition_and_save_returns_conflict(self) -> None:
        _, detail, _ = self.request("/api/projects/proj:111111111111")
        apply_edit = projects.apply_profile_edit
        def competing_edit(conn, **kwargs):
            other = registry.open_db()
            try:
                project = project_record.fetch_project(other, "proj:111111111111")
                project["notes"] = "Concurrent correction"
                project_record.save_project(other, project)
            finally:
                other.close()
            return apply_edit(conn, **kwargs)
        with mock.patch.object(projects, "apply_profile_edit", side_effect=competing_edit):
            status, result, _ = self.request("/api/projects/proj:111111111111/profile", method="POST", payload={"expected_revision": detail["revision"], "name": "Obsolete", "notes": "Obsolete"}, include_revision=False)
        self.assertEqual(status, 409)
        self.assertEqual(result["error"]["code"], "stale_project")
        _, current, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(current["project"]["profile"]["core"]["notes"], "Concurrent correction")

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

        status, payload, _ = self.request("/api/review-queue?limit=10&kinds=not_a_signal")
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"], [])

    def test_promotion_queue_api_exposes_policy_projection(self) -> None:
        conn = registry.open_db()
        project = project_record.fetch_project(conn, "proj:111111111111")
        assert project is not None
        project["category"] = "project"
        project["website_url"] = "https://app.example.com"
        project["service_status_confidence"] = "medium"
        project["service_status_evidence"] = ["http_200", "login_surface_detected"]
        registry.confirm_service_seed(project, "https://app.example.com", source="fixture")
        project_record.save_project(conn, project)
        conn.commit()
        conn.close()

        status, payload, _ = self.request("/api/promotion-queue?limit=10")

        self.assertEqual(status, 200)
        item = next(item for item in payload["items"] if item["project_key"] == project["project_key"])
        self.assertEqual(item["candidate_visibility"], {"group": "eligible", "reasons": []})
        self.assertEqual(item["service_evidence_group"], "confirmed")
        self.assertEqual(item["priority_group"], "normal")
        self.assertEqual(item["sort_score"], item["score_breakdown"]["total"])

    def test_promotion_queue_api_defaults_include_stable_but_not_stalled(self) -> None:
        with contextlib.closing(registry.open_db()) as conn:
            for state in ("stable", "stalled"):
                project = self.make_project(f"proj:{state}")
                project.update(
                    name=f"{state} technical project",
                    tracking_state=state,
                    category="project",
                    technical_interest_level="high",
                    technical_interest_reasons=["implementation_reference"],
                    technical_interest_summary="Reusable protocol implementation",
                )
                project_record.save_project(conn, project)

        _, default, _ = self.request("/api/promotion-queue?view=technical")
        default_keys = {item["project_key"] for item in default["items"]}
        self.assertIn("proj:stable", default_keys)
        self.assertNotIn("proj:stalled", default_keys)

        _, explicit, _ = self.request("/api/promotion-queue?view=technical&tracking_states=stalled")
        explicit_keys = {item["project_key"] for item in explicit["items"]}
        self.assertIn("proj:stalled", explicit_keys)
        self.assertNotIn("proj:stable", explicit_keys)

    def test_atproto_usage_api_and_technical_view(self) -> None:
        from promotion_selection import cases, apply_fixed_assessment
        with contextlib.closing(registry.open_db()) as conn:
            for case in cases():
                project_record.save_project(conn, apply_fixed_assessment(case))
        status, first, _ = self.request("/api/promotion-queue?view=technical&limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(first["total"], 3)
        _, second, _ = self.request("/api/promotion-queue?view=technical&limit=1&offset=1")
        self.assertNotEqual(first["items"][0]["project_key"], second["items"][0]["project_key"])
        key = "fixture:squire"
        _, detail, _ = self.request(f"/api/projects/{key}")
        revision = detail["revision"]
        self.assertEqual(detail["promotion_policy"]["atproto_usage"]["role"], "supporting")
        status, _, _ = self.request(f"/api/projects/{key}/atproto-usage", method="POST",
                                    payload={"role": "main_data", "reason": ""})
        self.assertEqual(status, 400)
        status, result, _ = self.request(f"/api/projects/{key}/atproto-usage", method="POST",
                                        payload={"role": "main_data", "reason": "主機能も移行した根拠"})
        self.assertEqual(status, 200)
        self.assertEqual(result["detail"]["promotion_policy"]["atproto_usage"]["source"], "operator")
        status, _, _ = self.request(f"/api/projects/{key}/atproto-usage", method="POST",
                                    payload={"clear": True, "expected_revision": revision}, include_revision=False)
        self.assertEqual(status, 409)
        status, result, _ = self.request(f"/api/projects/{key}/atproto-usage", method="POST", payload={"clear": True})
        self.assertEqual(status, 200)
        self.assertEqual(result["detail"]["promotion_policy"]["atproto_usage"]["role"], "unknown")

    def test_promotion_attention_api_views_search_and_stale_mutations(self) -> None:
        key = "proj:111111111111"
        with contextlib.closing(registry.open_db()) as conn:
            project = project_record.fetch_project(conn, key)
            project.update(category="project", service_status="live")
            registry.confirm_service_seed(project, "https://app.example.com", source="fixture")
            project_record.save_project(conn, project)
        _, detail, _ = self.request(f"/api/projects/{key}")
        revision = detail["revision"]
        status, result, _ = self.request(f"/api/projects/{key}/defer-promotion", method="POST", payload={"expected_revision": revision})
        self.assertEqual(status, 200)
        self.assertEqual(result["detail"]["promotion_policy"]["attention"]["group"], "held")
        status, _, _ = self.request(f"/api/projects/{key}/clear-promotion-deferral", method="POST", payload={"expected_revision": revision})
        self.assertEqual(status, 409)
        _, payload, _ = self.request("/api/promotion-queue?view=waiting&limit=1")
        self.assertEqual(payload["items"][0]["project_key"], key)
        _, payload, _ = self.request("/api/promotion-queue")
        self.assertNotIn(key, [item["project_key"] for item in payload["items"]])
        status, lowered, _ = self.request(f"/api/projects/{key}/lower-promotion-priority", method="POST",
            payload={"expected_revision":result["detail"]["revision"], "note":"OAuth feature"})
        self.assertEqual(status, 200)
        _, payload, _ = self.request("/api/promotion-queue?view=lowered&reason=oauth")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["reasons"], [{"reason":"OAuth feature", "count":1}])
        _, payload, _ = self.request("/api/promotion-queue?view=lowered&reason=oauth&offset=1")
        self.assertEqual(payload["count"], 0)
        status, result, _ = self.request(f"/api/projects/{key}/defer-promotion", method="POST",
            payload={"expected_revision":lowered["detail"]["revision"], "note":"waiting for release"})
        self.assertEqual(status, 200)
        _, payload, _ = self.request("/api/promotion-queue?view=waiting&reason=release")
        self.assertEqual(payload["items"][0]["project_key"], key)
        status, _, _ = self.request("/api/promotion-queue?view=unknown")
        self.assertEqual(status, 400)
        status, _, _ = self.request("/api/promotion-queue?view=reviewed")
        self.assertEqual(status, 400)
        for action in ("review-promotion", "clear-promotion-review"):
            status, _, _ = self.request(f"/api/projects/{key}/{action}", method="POST", payload={})
            self.assertEqual(status, 400)

    def test_lowered_api_matches_full_scan_for_saved_candidates_and_pages(self) -> None:
        keys = ("proj:111111111111", "proj:222222222222")
        with contextlib.closing(registry.open_db()) as conn:
            for key in keys:
                project = project_record.fetch_project(conn, key)
                project.update(tracking_state="out_of_scope", service_status="unknown",
                               introduced_url="https://example.com/introduced")
                project_record.save_project(conn, project)
        for key in keys:
            status, _, _ = self.request(f"/api/projects/{key}/lower-promotion-priority",
                method="POST", payload={"note": "Straße"})
            self.assertEqual(status, 200)
        recovered = []
        for offset in (0, 1, 2):
            path = f"/api/promotion-queue?view=lowered&reason=STRASSE&limit=1&offset={offset}"
            actual = self.request(path)
            with mock.patch.object(project_record, "list_projects_with_decision",
                    side_effect=lambda conn, kind, **kw: project_record.list_projects(conn, **kw)):
                self.assertEqual(actual, self.request(path))
            status, payload, _ = actual
            self.assertEqual(status, 200)
            self.assertEqual(payload["total"], 2)
            self.assertEqual(payload["reasons"], [{"reason": "Straße", "count": 2}])
            self.assertEqual(payload["has_more"], offset == 0)
            recovered.extend(item["project_key"] for item in payload["items"])
        self.assertCountEqual(recovered, keys)

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
