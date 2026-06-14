from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectRegistryWebUnavailableTest(unittest.TestCase):
    def test_web_entrypoint_reports_schema_v2_disconnect(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "project_registry_web.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("not connected to registry schema v2", result.stderr)


if __name__ == "__main__":
    unittest.main()
