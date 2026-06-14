from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .. import database
from .. import registry as core


def migrate_database(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    return database.migrate_database(path, dry_run=dry_run)


def repair_database(conn: sqlite3.Connection, *, execute: bool = False) -> dict[str, Any]:
    return core.audit_and_repair_identifiers(conn, execute=execute)


def audit_urls(conn: sqlite3.Connection, *, example_limit: int = 20) -> dict[str, Any]:
    return core.build_url_field_audit(conn, example_limit=max(0, example_limit))


def cleanup_app_links(conn: sqlite3.Connection, *, execute: bool = False) -> dict[str, Any]:
    return core.cleanup_project_app_links(conn, execute=execute)
