from __future__ import annotations

import argparse
import contextlib
import json
import os
import plistlib
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence

from project_registry_core import database


SERVICE_LABEL = "local.atproto-survey.project-registry-web"
SERVICE_BIND_HOST = "0.0.0.0"
SERVICE_URL_HOST = "127.0.0.1"
SERVICE_PORT = 8888
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def launch_agent_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"


def error_log_path(home: Path) -> Path:
    return home / "Library" / "Logs" / "atproto-survey" / "project-registry-web.err.log"


def service_target(uid: int) -> str:
    return f"gui/{uid}/{SERVICE_LABEL}"


def service_domain(uid: int) -> str:
    return f"gui/{uid}"


def launch_agent_payload(
    *,
    workspace_root: Path,
    home: Path,
    direnv_path: Path,
) -> dict[str, Any]:
    root = workspace_root.resolve()
    return {
        "Label": SERVICE_LABEL,
        "ProgramArguments": [
            str(direnv_path.absolute()),
            "exec",
            str(root),
            str(root / "bin" / "project-registry-web"),
            "--host",
            SERVICE_BIND_HOST,
            "--port",
            str(SERVICE_PORT),
            "--db-path",
            str(root / "data" / "survey.db"),
        ],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "HOME": str(home.resolve()),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(error_log_path(home)),
    }


def _run(
    argv: Sequence[str],
    *,
    check: bool = True,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"command failed: {argv[0]}: {detail}")
    return result


def _find_executable(name: str) -> Path:
    value = shutil.which(name)
    if not value:
        raise RuntimeError(f"required executable was not found in PATH: {name}")
    return Path(value).absolute()


def _read_web_token(*, direnv_path: Path, workspace_root: Path) -> str:
    result = _run(
        [
            str(direnv_path),
            "exec",
            str(workspace_root),
            sys.executable,
            "-c",
            "import os, sys; sys.stdout.write(os.environ.get('ATPROTO_SURVEY_WEB_TOKEN', ''))",
        ]
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("ATPROTO_SURVEY_WEB_TOKEN is not set by the workspace direnv environment")
    return token


def preflight(*, workspace_root: Path, direnv_path: Path) -> None:
    envrc = workspace_root / ".envrc"
    if not envrc.is_file():
        raise RuntimeError(f"workspace .envrc was not found: {envrc}")
    mode = stat.S_IMODE(envrc.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError(f"workspace .envrc must not be group/world accessible: chmod 600 {envrc}")
    _read_web_token(direnv_path=direnv_path, workspace_root=workspace_root)
    db_path = workspace_root / "data" / "survey.db"
    with contextlib.closing(database.open_database(db_path, create=False)):
        pass


def _write_plist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary_path = Path(handle.name)
        plistlib.dump(payload, handle, sort_keys=True)
    try:
        temporary_path.chmod(0o644)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _launchctl_loaded(launchctl: Path, uid: int) -> subprocess.CompletedProcess[str]:
    return _run([str(launchctl), "print", service_target(uid)], check=False)


def _bootstrap_launch_agent(launchctl: Path, uid: int, plist_path: Path) -> None:
    command = [str(launchctl), "bootstrap", service_domain(uid), str(plist_path)]
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(3):
        last_result = _run(command, check=False)
        if last_result.returncode == 0:
            return
        if attempt < 2:
            time.sleep(1.0)
    assert last_result is not None
    detail = last_result.stderr.strip() or last_result.stdout.strip() or f"exit status {last_result.returncode}"
    raise RuntimeError(f"command failed: {launchctl}: {detail}")


def install(*, workspace_root: Path, home: Path, uid: int) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent installation is supported only on macOS")
    direnv_path = _find_executable("direnv")
    launchctl = _find_executable("launchctl")
    preflight(workspace_root=workspace_root, direnv_path=direnv_path)

    plist_path = launch_agent_path(home)
    log_path = error_log_path(home)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if _launchctl_loaded(launchctl, uid).returncode == 0:
        _run([str(launchctl), "bootout", service_target(uid)])
    _write_plist(
        plist_path,
        launch_agent_payload(workspace_root=workspace_root, home=home, direnv_path=direnv_path),
    )
    _bootstrap_launch_agent(launchctl, uid, plist_path)
    return {
        "status": "installed",
        "label": SERVICE_LABEL,
        "bind": f"{SERVICE_BIND_HOST}:{SERVICE_PORT}",
        "url": f"http://{SERVICE_URL_HOST}:{SERVICE_PORT}/",
        "plist_path": str(plist_path),
        "error_log_path": str(log_path),
    }


def uninstall(*, home: Path, uid: int) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent installation is supported only on macOS")
    launchctl = _find_executable("launchctl")
    plist_path = launch_agent_path(home)
    was_loaded = _launchctl_loaded(launchctl, uid).returncode == 0
    if was_loaded:
        _run([str(launchctl), "bootout", service_target(uid)])
    removed = plist_path.exists()
    plist_path.unlink(missing_ok=True)
    return {
        "status": "uninstalled",
        "label": SERVICE_LABEL,
        "was_loaded": was_loaded,
        "removed_plist": removed,
        "retained_error_log": str(error_log_path(home)),
    }


def _health_check() -> bool:
    try:
        with urllib.request.urlopen(f"http://{SERVICE_URL_HOST}:{SERVICE_PORT}/", timeout=1.0) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def status(*, home: Path, uid: int) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent status is supported only on macOS")
    launchctl = _find_executable("launchctl")
    result = _launchctl_loaded(launchctl, uid)
    state_match = re.search(r"^\s*state = (.+)$", result.stdout, re.MULTILINE)
    pid_match = re.search(r"^\s*pid = (\d+)$", result.stdout, re.MULTILINE)
    loaded = result.returncode == 0
    return {
        "label": SERVICE_LABEL,
        "loaded": loaded,
        "state": state_match.group(1).strip() if state_match else ("not_loaded" if not loaded else "unknown"),
        "pid": int(pid_match.group(1)) if pid_match else None,
        "healthy": _health_check() if loaded else False,
        "bind": f"{SERVICE_BIND_HOST}:{SERVICE_PORT}",
        "url": f"http://{SERVICE_URL_HOST}:{SERVICE_PORT}/",
        "plist_path": str(launch_agent_path(home)),
        "error_log_path": str(error_log_path(home)),
    }


def restart(*, workspace_root: Path, uid: int) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent restart is supported only on macOS")
    launchctl = _find_executable("launchctl")
    if _launchctl_loaded(launchctl, uid).returncode != 0:
        raise RuntimeError("LaunchAgent is not loaded; run project-registry-web-service install")
    direnv_path = _find_executable("direnv")
    token = _read_web_token(direnv_path=direnv_path, workspace_root=workspace_root)
    request = urllib.request.Request(
        f"http://{SERVICE_URL_HOST}:{SERVICE_PORT}/api/reload",
        data=json.dumps({"confirm": True}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10.0) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Web server reload request failed: {exc}") from exc
    return {
        "status": "reloading",
        "label": SERVICE_LABEL,
        "bind": f"{SERVICE_BIND_HOST}:{SERVICE_PORT}",
        "url": f"http://{SERVICE_URL_HOST}:{SERVICE_PORT}/",
        "reload_id": payload.get("reload_id"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-registry-web-service",
        description="Manage the project registry Web UI as a macOS LaunchAgent.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("install", help="Install, replace, and start the LaunchAgent.")
    subparsers.add_parser("status", help="Show LaunchAgent and HTTP health status.")
    subparsers.add_parser("restart", help="Gracefully reload the running Web server.")
    subparsers.add_parser("uninstall", help="Stop the LaunchAgent and remove its plist.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = WORKSPACE_ROOT.resolve()
    home = Path.home().resolve()
    uid = os.getuid()
    try:
        if args.command == "install":
            payload = install(workspace_root=root, home=home, uid=uid)
        elif args.command == "status":
            payload = status(home=home, uid=uid)
        elif args.command == "restart":
            payload = restart(workspace_root=root, uid=uid)
        else:
            payload = uninstall(home=home, uid=uid)
    except (RuntimeError, database.SchemaMismatchError, sqlite3.Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
