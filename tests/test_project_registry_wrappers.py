from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class ProjectRegistryWrapperTest(unittest.TestCase):
    def test_public_wrappers_reject_unsupported_python_before_import(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tempdir:
            for wrapper_name in ("project-registry", "project-registry-web"):
                with self.subTest(wrapper=wrapper_name):
                    fake_root = Path(tempdir) / wrapper_name
                    wrapper_path = fake_root / "bin" / wrapper_name
                    helper_path = fake_root / "bin" / "_project-registry-python"
                    python_path = fake_root / ".venv" / "bin" / "python"
                    wrapper_path.parent.mkdir(parents=True)
                    python_path.parent.mkdir(parents=True)
                    shutil.copy2(source_root / "bin" / wrapper_name, wrapper_path)
                    shutil.copy2(source_root / "bin" / "_project-registry-python", helper_path)
                    python_path.write_text(
                        """#!/bin/sh
case "$2" in
  *sys.version_info*) exit 1 ;;
  *platform.python_version*) printf '3.9.18\\n'; exit 0 ;;
esac
exit 99
""",
                        encoding="utf-8",
                    )
                    python_path.chmod(0o755)

                    result = subprocess.run(
                        [str(wrapper_path), "--help"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertIn("requires Python 3.10 or newer (found 3.9.18)", result.stderr)
                    self.assertNotIn("SyntaxError", result.stderr)

    def test_test_wrapper_uses_git_common_checkout_venv(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tempdir:
            fake_root = Path(tempdir) / "worktree"
            common_root = Path(tempdir) / "main"
            tool_root = Path(tempdir) / "tools"
            wrapper_path = fake_root / "bin" / "project-registry-test"
            helper_path = fake_root / "bin" / "_project-registry-python"
            python_path = common_root / ".venv" / "bin" / "python"
            git_path = tool_root / "git"
            wrapper_path.parent.mkdir(parents=True)
            python_path.parent.mkdir(parents=True)
            tool_root.mkdir(parents=True)
            shutil.copy2(source_root / "bin" / "project-registry-test", wrapper_path)
            shutil.copy2(source_root / "bin" / "_project-registry-python", helper_path)
            python_path.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
                "printf '%s\\n' \"$@\"\n",
                encoding="utf-8",
            )
            python_path.chmod(0o755)
            git_path.write_text(
                f"#!/bin/sh\nprintf '%s\\n' '{common_root / '.git'}'\n",
                encoding="utf-8",
            )
            git_path.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join([str(tool_root), env.get("PATH", "")])

            result = subprocess.run(
                [str(wrapper_path), "-p", "test_example.py"],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            args = result.stdout.splitlines()
            self.assertEqual(args[:3], ["-m", "unittest", "discover"])
            self.assertIn(str(fake_root / "tests"), args)
            self.assertEqual(args[-2:], ["-p", "test_example.py"])

    def test_test_wrapper_warns_when_workspace_temp_exceeds_budget(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tempdir:
            fake_root = Path(tempdir) / "worktree"
            wrapper_path = fake_root / "bin" / "project-registry-test"
            helper_path = fake_root / "bin" / "_project-registry-python"
            python_path = fake_root / ".venv" / "bin" / "python"
            temp_path = fake_root / ".tmp"
            wrapper_path.parent.mkdir(parents=True)
            python_path.parent.mkdir(parents=True)
            temp_path.mkdir(parents=True)
            shutil.copy2(source_root / "bin" / "project-registry-test", wrapper_path)
            shutil.copy2(source_root / "bin" / "_project-registry-python", helper_path)
            python_path.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            python_path.chmod(0o755)
            (temp_path / "large.db").write_bytes(b"x" * 4096)
            env = os.environ.copy()
            env["PROJECT_REGISTRY_TMP_WARN_KIB"] = "1"

            result = subprocess.run(
                [str(wrapper_path)],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(".tmp uses", result.stderr)
            self.assertIn("cleanup-workspace-temp", result.stderr)


if __name__ == "__main__":
    unittest.main()
