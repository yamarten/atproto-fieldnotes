from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from project_registry_core import registry  # noqa: E402


class TempRegistryDbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = registry.DB_PATH
        self.db_path = Path(self.tempdir.name) / "survey.db"
        registry.DB_PATH = self.db_path

    def tearDown(self) -> None:
        registry.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def make_project(self, project_key: str, seed_type: str = "nsid", seed_value: str = "app.example.post") -> dict:
        return registry.default_project_record(project_key, seed_type, seed_value)


def seed_web_fixture() -> None:
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


@contextlib.contextmanager
def captured_stdout() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        yield stream
