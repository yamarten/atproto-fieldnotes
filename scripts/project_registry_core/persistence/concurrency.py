"""Short transactions and project-local optimistic concurrency tokens."""
from __future__ import annotations

import contextlib
import sqlite3
import uuid
from functools import wraps
from collections.abc import Iterator, Iterable
from typing import Any

from . import runtime_view


class RegistryConnection(sqlite3.Connection):
    """Transport preconditions retained until the atomic save boundary."""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.expected_project_revisions: dict[str, int] = {}


def consistent_read(function):
    @wraps(function)
    def wrapped(conn, *args, **kwargs):
        with read_snapshot(conn):
            return function(conn, *args, **kwargs)
    return wrapped


class StaleProjectWriteError(RuntimeError):
    def __init__(self, message: str, *, project_key: str = "") -> None:
        super().__init__(message)
        self.project_key = project_key


@contextlib.contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """End our read snapshot before returning; never acquire a writer lock."""
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN")
    try:
        yield
    finally:
        if own:
            conn.rollback()


@contextlib.contextmanager
def write_transaction(conn: sqlite3.Connection, *, commit: bool = True) -> Iterator[None]:
    own = not conn.in_transaction
    name = "write_" + uuid.uuid4().hex
    if own:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        if own:
            conn.rollback()
        raise
    else:
        conn.execute(f"RELEASE {name}")
        if own and commit:
            try:
                conn.commit()
            except BaseException:
                conn.rollback()
                raise


def revision(conn: sqlite3.Connection, project_key: str) -> int:
    row = conn.execute("SELECT revision FROM project_revisions WHERE project_key=?", (project_key,)).fetchone()
    return int(row[0]) if row else 0


def check_projects(conn: sqlite3.Connection, projects: Iterable[dict[str, Any]], *, existing_aliases: dict[str, str] | None = None) -> None:
    for key, expected in getattr(conn, "expected_project_revisions", {}).items():
        if revision(conn, key) != expected:
            raise StaleProjectWriteError(f"project changed since it was loaded: {key}", project_key=key)
    if isinstance(conn, RegistryConnection):
        conn.expected_project_revisions.clear()
    for project in projects:
        key = str(project["project_key"])
        alias = conn.execute("SELECT canonical_project_key FROM project_key_aliases WHERE alias_project_key=?", (key,)).fetchone()
        expected = project.get(runtime_view.RUNTIME_PROJECT_REVISION)
        actual = revision(conn, key)
        alias_allowed = alias and (existing_aliases or {}).get(key) == alias[0]
        if (alias and not alias_allowed) or expected is None or int(expected) != actual:
            reason = f"merged into {alias[0]}" if alias else f"revision {expected} -> {actual}"
            raise StaleProjectWriteError(f"project changed while this write was in progress: {key} ({reason}); retry against current project state", project_key=key)


def install_revision_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE project_revisions (project_key TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
    conn.execute("INSERT INTO project_revisions SELECT project_key, 1 FROM projects")
    tables = {
        "projects": ("project_key",),
        "project_identifiers": ("project_key",),
        "project_relations": ("project_key",),
        "project_decisions": ("project_key",),
        "project_noncurrent_identifiers": ("project_key",),
        "project_identifier_adoptions": ("project_key",),
        "project_identifier_transitions": ("project_key",),
        "project_key_aliases": ("alias_project_key", "canonical_project_key"),
    }
    for table, columns in tables.items():
        for operation in ("INSERT", "UPDATE", "DELETE"):
            versions = ("NEW",) if operation == "INSERT" else ("OLD",) if operation == "DELETE" else ("OLD", "NEW")
            statements = " ".join(
                f"INSERT INTO project_revisions(project_key, revision) VALUES ({version}.{column}, 1) "
                "ON CONFLICT(project_key) DO UPDATE SET revision=revision+1;"
                for version in versions for column in columns
            )
            conn.execute(f"CREATE TRIGGER revision_{table}_{operation.lower()} AFTER {operation} ON {table} BEGIN {statements} END")
