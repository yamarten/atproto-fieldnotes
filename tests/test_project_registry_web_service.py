from __future__ import annotations

import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from project_registry_web import service


class ProjectRegistryWebServiceTest(unittest.TestCase):
    def test_launch_agent_uses_direnv_lan_bind_and_port_8888(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            home = Path(directory) / "home"
            payload = service.launch_agent_payload(
                workspace_root=root,
                home=home,
                direnv_path=Path("/opt/homebrew/bin/direnv"),
            )

        self.assertEqual(payload["Label"], service.SERVICE_LABEL)
        self.assertEqual(payload["ProgramArguments"][:3], [
            "/opt/homebrew/bin/direnv",
            "exec",
            str(root.resolve()),
        ])
        self.assertIn("0.0.0.0", payload["ProgramArguments"])
        self.assertIn("8888", payload["ProgramArguments"])
        self.assertEqual(payload["StandardOutPath"], "/dev/null")
        self.assertEqual(payload["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(payload["EnvironmentVariables"]["HOME"], str(home.resolve()))
        self.assertIn("/opt/homebrew/bin", payload["EnvironmentVariables"]["PATH"])
        self.assertNotIn("ATPROTO_SURVEY_WEB_TOKEN", repr(payload))

    def test_preflight_rejects_accessible_envrc_before_reading_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".envrc").write_text("export ATPROTO_SURVEY_WEB_TOKEN=test\n", encoding="utf-8")
            (root / ".envrc").chmod(0o644)

            with (
                mock.patch.object(service, "_read_web_token") as read_token,
                self.assertRaisesRegex(RuntimeError, "chmod 600"),
            ):
                service.preflight(workspace_root=root, direnv_path=Path("/usr/bin/direnv"))

        read_token.assert_not_called()

    def test_preflight_validates_runtime_in_direnv_before_opening_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".envrc").touch(mode=0o600)
            with (
                mock.patch.object(service, "_read_web_token", return_value="test"),
                mock.patch.object(service, "_run", side_effect=RuntimeError("invalid runtime")) as run,
                mock.patch.object(service.database, "open_database") as open_db,
                self.assertRaisesRegex(RuntimeError, "invalid runtime"),
            ):
                service.preflight(workspace_root=root, direnv_path=Path("/usr/bin/direnv"))
            self.assertEqual(run.call_args.args[0], [
                "/usr/bin/direnv", "exec", str(root), str(root / "bin/project-registry-python-env"), "status",
            ])
            open_db.assert_not_called()

    def test_write_plist_is_parseable_and_replaces_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.plist"
            path.write_text("old", encoding="utf-8")
            service._write_plist(path, {"Label": "test", "RunAtLoad": True})

            with path.open("rb") as handle:
                payload = plistlib.load(handle)

        self.assertEqual(payload, {"Label": "test", "RunAtLoad": True})

    def test_install_replaces_loaded_agent_and_bootstraps_generated_plist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            home = Path(directory) / "home"
            root.mkdir()
            completed = mock.Mock(returncode=0, stdout="", stderr="")
            with (
                mock.patch.object(service.sys, "platform", "darwin"),
                mock.patch.object(service, "_find_executable", side_effect=[
                    Path("/opt/homebrew/bin/direnv"),
                    Path("/bin/launchctl"),
                ]),
                mock.patch.object(service, "preflight"),
                mock.patch.object(service, "_run", return_value=completed) as run,
            ):
                payload = service.install(workspace_root=root, home=home, uid=501)

            plist_path = service.launch_agent_path(home)
            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)

        self.assertEqual(payload["bind"], "0.0.0.0:8888")
        self.assertEqual(payload["url"], "http://127.0.0.1:8888/")
        self.assertEqual(plist["ProgramArguments"][0], "/opt/homebrew/bin/direnv")
        self.assertEqual(run.call_args_list[0].args[0], [
            "/bin/launchctl",
            "print",
            "gui/501/local.atproto-survey.project-registry-web",
        ])
        self.assertEqual(run.call_args_list[1].args[0][:2], ["/bin/launchctl", "bootout"])
        self.assertEqual(run.call_args_list[2].args[0][:2], ["/bin/launchctl", "bootstrap"])

    def test_bootstrap_retries_transient_launchctl_failure(self) -> None:
        failed = mock.Mock(returncode=5, stdout="", stderr="Input/output error")
        succeeded = mock.Mock(returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(service, "_run", side_effect=[failed, succeeded]) as run,
            mock.patch.object(service.time, "sleep") as sleep,
        ):
            service._bootstrap_launch_agent(
                Path("/bin/launchctl"),
                501,
                Path("/Users/test/Library/LaunchAgents/service.plist"),
            )

        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_status_reports_launchctl_state_and_http_health(self) -> None:
        launchctl_output = "state = running\npid = 1234\n"
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(service.sys, "platform", "darwin"),
            mock.patch.object(service, "_find_executable", return_value=Path("/bin/launchctl")),
            mock.patch.object(
                service,
                "_launchctl_loaded",
                return_value=mock.Mock(returncode=0, stdout=launchctl_output),
            ),
            mock.patch.object(service, "_health_check", return_value=True),
        ):
            payload = service.status(home=Path(directory), uid=os.getuid())

        self.assertEqual(payload["state"], "running")
        self.assertEqual(payload["pid"], 1234)
        self.assertTrue(payload["healthy"])


if __name__ == "__main__":
    unittest.main()
