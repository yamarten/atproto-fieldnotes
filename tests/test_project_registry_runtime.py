from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
import project_registry_runtime as runtime


class RuntimeTest(unittest.TestCase):
    def test_linked_worktree_borrows_real_common_python_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main"
            worktree = root / "worktree"
            main.mkdir()
            env = os.environ.copy()
            env.pop("PROJECT_REGISTRY_PYTHON_ENV", None)

            def git(*args: str) -> None:
                subprocess.run(["git", "-C", str(main), *args], env=env, check=True, capture_output=True)

            git("init")
            git("-c", "user.name=Test", "-c", "user.email=test@example.test", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "test")
            git("worktree", "add", "--detach", str(worktree))
            (main / ".venv").symlink_to(Path(sys.prefix).resolve())
            (worktree / "bin").mkdir()
            (worktree / "scripts").mkdir()
            source = Path(runtime.__file__).parents[1]
            shutil.copy2(source / "bin/_project-registry-python", worktree / "bin")
            shutil.copy2(runtime.__file__, worktree / "scripts")
            result = subprocess.run(
                [str(worktree / "bin/_project-registry-python"), "probe", "-c", "import sys; print(sys.executable)"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(Path(sys.prefix).resolve() / "bin/python"))
            git("worktree", "remove", "--force", str(worktree))

    def test_runtime_rejects_an_ordinary_venv_using_external_python(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            venv = Path(directory) / "venv"
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
            (venv.parent / "python").mkdir()
            result = subprocess.run(
                [str(venv / "bin/python"), "-I", runtime.__file__, "check", str(venv)],
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must belong to this generation", result.stderr)

    def test_generation_cannot_disguise_external_python_as_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            venv = Path(directory) / "venv"
            venv.mkdir()
            (venv.parent / "python").symlink_to(Path(sys.base_prefix))
            with self.assertRaisesRegex(RuntimeError, "must not redirect"):
                runtime.runtime_information(venv)

    def test_activation_replaces_pointer_preserving_old_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / ".runtime/old/venv"
            new = root / ".runtime/new/venv"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            (new.parent / "requirements.txt").touch()
            (root / ".venv").symlink_to(old)
            with mock.patch.object(runtime, "runtime_information", return_value={"venv": str(new)}):
                runtime.activate(new, root)
                self.assertEqual((root / ".venv").resolve(), new.resolve())
                self.assertTrue(old.is_dir())
                (old.parent / "requirements.txt").touch()
                runtime.activate(old, root)
                self.assertEqual((root / ".venv").resolve(), old.resolve())
                self.assertTrue(new.is_dir())
            self.assertEqual(list((root / ".runtime").glob(".activate-*")), [])

    def test_activation_does_not_replace_legacy_directory_or_incomplete_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            new = root / ".runtime/new/venv"
            new.mkdir(parents=True)
            with mock.patch.object(runtime, "runtime_information", return_value={}):
                with self.assertRaisesRegex(RuntimeError, "incomplete"):
                    runtime.activate(new, root)
                (new.parent / "requirements.txt").touch()
                (root / ".venv").mkdir()
                with self.assertRaisesRegex(RuntimeError, "directory"):
                    runtime.activate(new, root)
            self.assertTrue((root / ".venv").is_dir())

    def test_prepare_refuses_reused_generation_without_invoking_uv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / ".runtime/existing").mkdir(parents=True)
            (root / "requirements.txt").touch()
            (root / ".python-version").write_text("3.14\n")
            source = Path(runtime.__file__).parents[1] / "bin/project-registry-python-env"
            script = root / "bin" / source.name
            script.write_bytes(source.read_bytes())
            script.chmod(0o755)
            uv = root / "bin/uv"
            uv.write_text("#!/bin/sh\ntouch \"$0.called\"\nexit 99\n")
            uv.chmod(0o755)
            env = dict(os.environ, PATH=str(root / "bin") + os.pathsep + os.environ["PATH"])
            result = subprocess.run([str(script), "prepare", "existing"], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "bin/uv.called").exists())


if __name__ == "__main__":
    unittest.main()
