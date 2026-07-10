from __future__ import annotations

import io
import json
import sqlite3
import sys
import threading
import types
from unittest import mock
from http import HTTPStatus
from io import BytesIO

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import storage
from project_registry_core.evidence import CollectorFailure, CollectorResult, EvidenceBundle
from project_registry_web.server import RegistryWebHandler, RegistryWebServer, build_information, build_parser, main as web_main, restart_argv
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
        self.assertIn("Reload server", script)

        self.assertIn("startProjectEnrich", script)
        self.assertIn("openJobDetails", script)
        self.assertIn("Start sync job", script)
        self.assertIn("Build review plan", script)
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
        self.assertNotIn("read_only", settings)
        self.assertNotIn("--read-only", build_parser().format_help())
        self.assertNotIn("read_only", script)

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

    def test_restart_argv_preserves_arguments_but_uses_package_entrypoint(self) -> None:
        self.assertEqual(
            restart_argv(["/tmp/project_registry_web/__main__.py", "--host", "0.0.0.0", "--reload"]),
            [sys.executable, "-m", "project_registry_web", "--host", "0.0.0.0", "--reload"],
        )

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
        self.assertEqual(detail["overview"]["manual_seed"]["value"], "app.example.post")
        manual_seed_nsid = next(
            item for item in detail["identifiers"]
            if item["value"] == "app.example.post" and "manual_seed" in item["roles"]
        )
        manual_seed_candidate_roles = {
            entry["role"]
            for entry in manual_seed_nsid["entries"]
            if entry["classification"] == "candidate"
        }
        self.assertNotIn("input_nsid", manual_seed_candidate_roles)
        self.assertNotIn("primary_nsid", manual_seed_candidate_roles)
        candidate_values = {
            item["value"] for item in detail["identifiers"] if item["classification"] == "candidate"
        }
        self.assertIn("https://app.example.com", candidate_values)

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
        candidate = next(entry for entry in repository["entries"] if entry["classification"] == "candidate")
        self.assertTrue(candidate["actions"]["confirm"])
        self.assertEqual(repository["actions"]["scope"], "identifier")

    def test_website_role_displays_as_primary_service_marker(self) -> None:
        conn = registry.open_db()
        try:
            project = registry.fetch_project(conn, "proj:111111111111")
            assert project is not None
            project["website_url"] = "https://app.example.com"
            project["service_urls"] = ["https://app.example.com"]
            registry.save_project(conn, project, preserve_timestamps=True)
            conn.commit()
        finally:
            conn.close()

        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        service = next(item for item in detail["identifiers"] if item["value"] == "https://app.example.com")
        self.assertTrue(service["is_primary"])
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

    def test_merge_pair_includes_both_projects_and_result_preview(self) -> None:
        status, payload, _ = self.request(
            "/api/merge-pair?source_project_key=proj:111111111111&target_project_key=proj:222222222222"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["source"]["name"], "Example App")
        self.assertEqual(payload["target"]["name"], "Example Duplicate")
        self.assertTrue(payload["source"]["identifiers"])
        self.assertFalse(payload["first_creator_details_included"])
        self.assertEqual(payload["merged_preview"]["target_project_key"], "proj:222222222222")
        self.assertEqual(payload["merged_preview"]["merged_name"], "Example Duplicate")

    def test_merge_pair_includes_multi_tenant_suffix_suggestion(self) -> None:
        conn = registry.open_db()
        try:
            source = registry.default_project_record("proj:foofoofoofoo", "service_url", "https://foo.example.com")
            registry.add_manual_seed(source, "service_url", "https://foo.example.com", source="test")
            target = registry.default_project_record("proj:barbarbarbar", "service_url", "https://bar.example.com")
            registry.add_manual_seed(target, "service_url", "https://bar.example.com", source="test")
            registry.save_project(conn, source)
            registry.save_project(conn, target)
            conn.commit()
        finally:
            conn.close()

        status, payload, _ = self.request(
            "/api/merge-pair?source_project_key=proj:foofoofoofoo&target_project_key=proj:barbarbarbar"
        )

        self.assertEqual(status, 200)
        self.assertEqual([item["suffix"] for item in payload["suggested_multi_tenant_suffixes"]], ["example.com"])

    def test_can_add_multi_tenant_suffix_from_web_api(self) -> None:
        original_domain_overrides_path = registry.DOMAIN_OVERRIDES_PATH
        original_suffixes = registry._CONFIGURED_MULTI_TENANT_SUFFIXES
        domain_overrides_path = self.db_path.with_name("domain-overrides.json")
        domain_overrides_path.write_text('{"custom_multi_tenant_suffixes": []}\n')
        try:
            registry.DOMAIN_OVERRIDES_PATH = domain_overrides_path
            registry.reset_domain_config_cache()

            status, payload, _ = self.request(
                "/api/domain-overrides/multi-tenant-suffixes",
                method="POST",
                payload={"suffix": "Example.COM."},
                include_revision=False,
            )

            self.assertEqual(status, 200)
            self.assertTrue(payload["added"])
            self.assertEqual(payload["suffix"], "example.com")
            self.assertEqual(json.loads(domain_overrides_path.read_text())["custom_multi_tenant_suffixes"], ["example.com"])
        finally:
            registry.DOMAIN_OVERRIDES_PATH = original_domain_overrides_path
            registry._CONFIGURED_MULTI_TENANT_SUFFIXES = original_suffixes
            registry.reset_domain_extractors()

    def test_merge_pair_can_include_first_creator_details_on_request(self) -> None:
        status, payload, _ = self.request(
            "/api/merge-pair?source_project_key=proj:111111111111&target_project_key=proj:222222222222&include_first_creator_details=1"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["first_creator_details_included"])
        self.assertIn("source_collection_first_creator_dids", payload)
        self.assertIn("target_collection_first_creator_dids", payload)

    def test_investigation_history_detail_and_replay(self) -> None:
        status, runs, _ = self.request("/api/projects/proj:111111111111/investigation-runs")
        self.assertEqual(status, 200)
        self.assertEqual(runs["count"], 1)
        self.assertNotIn("bundle", runs["items"][0])
        run_id = runs["items"][0]["run_id"]

        status, detail, _ = self.request(f"/api/investigation-runs/{run_id}?evidence_limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(detail["collector_count"] if "collector_count" in detail else len(detail["collectors"]), 2)
        self.assertEqual(detail["failure_count"], 1)
        self.assertLessEqual(len(detail["records"]), 1)

        status, replay, _ = self.request(f"/api/investigation-runs/{run_id}/replay")
        self.assertEqual(status, 200)
        self.assertEqual(replay["run_id"], run_id)
        self.assertIn("diff", replay)

    def test_merge_analysis_is_loaded_separately_from_project_detail(self) -> None:
        original = registry.compute_duplicate_candidates
        registry.compute_duplicate_candidates = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("detail computed duplicate candidates")
        )
        try:
            status, detail, _ = self.request("/api/projects/proj:111111111111")
        finally:
            registry.compute_duplicate_candidates = original
        self.assertEqual(status, 200)
        self.assertTrue(detail["merge_analysis_pending"])
        self.assertEqual(detail["explanations"]["topics"]["merge_candidate"]["conclusion"], "pending")

        status, analysis, _ = self.request("/api/projects/proj:111111111111/merge-analysis")
        self.assertEqual(status, 200)
        self.assertEqual(analysis["project_key"], "proj:111111111111")
        self.assertTrue(analysis["revision"])
        self.assertIn("merge_recommendation", analysis)
        self.assertNotEqual(analysis["explanation"]["conclusion"], "pending")

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
        original = registry.list_projects
        registry.list_projects = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("hydrated all projects"))
        try:
            status, payload, _ = self.request("/api/projects?q=app.example.post")
        finally:
            registry.list_projects = original
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"][0]["project_key"], "proj:111111111111")

    def test_stale_revision_rejects_write(self) -> None:
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
        self.assertEqual(payload["detail"]["overview"]["promotion_defer"]["suggested_days"], 45)

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
            project = registry.fetch_project(conn, "proj:111111111111")
        finally:
            conn.close()
        self.assertIn("https://github.com/example/second", [item["value"] for item in project["manual_seeds"]])

    def test_profile_preview_apply_and_stale_guard(self) -> None:
        status, detail, _ = self.request("/api/projects/proj:111111111111")
        self.assertEqual(status, 200)
        revision = detail["revision"]
        status, preview, _ = self.request(
            "/api/projects/proj:111111111111/profile-preview",
            method="POST",
            payload={"expected_revision": revision, "name": "Edited App", "notes": "operator note"},
        )
        self.assertEqual(status, 200)
        self.assertEqual([item["field"] for item in preview["result"]["changes"]], ["name", "notes"])

        status, applied, _ = self.request(
            "/api/projects/proj:111111111111/profile",
            method="POST",
            payload={"expected_revision": revision, "name": "Edited App", "notes": "operator note"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(applied["detail"]["project"]["name"], "Edited App")
        self.assertEqual(applied["detail"]["project"]["profile"]["core"]["notes"], "operator note")

        status, stale, _ = self.request(
            "/api/projects/proj:111111111111/profile",
            method="POST",
            payload={"expected_revision": revision, "name": "Stale", "notes": ""},
            include_revision=False,
        )
        self.assertEqual(status, 409)
        self.assertEqual(stale["error"]["code"], "stale_project")

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
