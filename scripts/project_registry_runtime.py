"""Validate and select prepared Python generations; no downloads or service control."""
from __future__ import annotations

import argparse
import json
import os
import sys
import sysconfig
import tempfile
from pathlib import Path


def runtime_information(venv: Path) -> dict[str, str]:
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required")
    venv = venv.resolve(strict=True)
    python_root = (venv.parent / "python").resolve(strict=True)
    if python_root != venv.parent / "python":
        raise RuntimeError("generation python/ must not redirect to another directory")
    base = Path(sys.base_prefix).resolve(strict=True)
    executable = Path(sys._base_executable).resolve(strict=True)
    stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
    if Path(sys.prefix).resolve() != venv or sys.prefix == sys.base_prefix:
        raise RuntimeError("selected Python is not the requested virtual environment")
    if not all(path.is_relative_to(python_root) for path in (base, executable, stdlib)):
        raise RuntimeError("Python executable and standard library must belong to this generation's python/ directory")
    import tldextract

    return {
        "version": sys.version.split()[0],
        "venv": str(venv),
        "executable": sys.executable,
        "base_executable": str(executable),
        "base_prefix": str(base),
        "stdlib": str(stdlib),
        "tldextract": tldextract.__version__,
    }


def activate(venv: Path, root: Path) -> dict[str, str]:
    root = root.resolve(strict=True)
    venv = venv.resolve(strict=True)
    if venv.parent.parent != root / ".runtime" or venv.name != "venv":
        raise RuntimeError("activate requires a generation in this checkout's .runtime/")
    info = runtime_information(venv)
    if not (venv.parent / "requirements.txt").is_file():
        raise RuntimeError("generation preparation is incomplete")
    target = root / ".venv"
    if target.exists() and not target.is_symlink():
        raise RuntimeError(".venv is a directory; stop its processes and retain it as .venv-legacy before activation")
    # Atomic replacement of the pointer only. Do not move venvs or mutate old generations.
    with tempfile.TemporaryDirectory(prefix=".activate-", dir=root / ".runtime") as directory:
        link = Path(directory) / "venv"
        link.symlink_to(venv.relative_to(root), target_is_directory=True)
        os.replace(link, target)
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "status", "activate"))
    parser.add_argument("venv", type=Path, nargs="?")
    parser.add_argument("root", type=Path, nargs="?")
    args = parser.parse_args()
    try:
        venv = args.venv or Path(sys.prefix)
        if args.action == "activate":
            if args.root is None:
                parser.error("activate requires venv and root")
            info = activate(venv, args.root)
        else:
            info = runtime_information(venv)
        if args.action != "check":
            print(json.dumps(info, indent=2))
    except (RuntimeError, OSError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
