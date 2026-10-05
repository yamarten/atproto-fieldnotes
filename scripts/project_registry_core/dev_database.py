"""Small fictional registry for manual CLI/Web checks, never the unit-test shared DB."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from . import database, project_record, registry, storage
from .evidence import EVIDENCE_SCHEMA_VERSION, EvidenceBundle


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "dev-registry.json"
MARKER_KEY = "dev_fixture"
MARKER_VALUE = "project-registry-fictional-v1"


def _check_path(root: Path) -> Path:
    path = root / "data" / "dev" / "survey.db"
    for entry in (root / "data", path.parent, path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
        if entry.is_symlink():
            raise ValueError(f"dev DB refuses symbolic links: {entry}")
        if entry.exists() and entry.is_file() and entry.stat().st_nlink != 1:
            raise ValueError(f"dev DB refuses hard links: {entry}")
    return path


def seed_database(conn: sqlite3.Connection) -> dict[str, int]:
    """Use current codecs; caller owns the transaction and a private test/dev DB."""
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    projects = {}
    for definition in fixture["projects"]:
        project = project_record.default_project_record(
            definition["project_key"], definition["seed_type"], definition["seed_value"],
        )
        project.update(definition["fields"])
        project.update(first_seen_at=fixture["observed_at"], last_seen_at=fixture["observed_at"])
        project_record.save_project(conn, project, preserve_timestamps=True, commit=False)
        projects[project["project_key"]] = project_record.fetch_project(conn, project["project_key"])
    for definition in fixture["investigations"]:
        project = projects[definition["project_key"]]
        bundle = EvidenceBundle.from_dict({
            **definition["bundle"], "schema_version": EVIDENCE_SCHEMA_VERSION,
            "project_key": project["project_key"], "collected_at": fixture["observed_at"],
        })
        storage.save_investigation_run(
            conn, project_key=project["project_key"], run_id=definition["run_id"],
            started_at=fixture["observed_at"], completed_at=fixture["observed_at"],
            input_project=project, output_project=project, bundle=bundle,
            evaluation_version="fictional-display-fixture-v1",
        )
    conn.execute("INSERT INTO registry_meta(key, value) VALUES (?, ?)", (MARKER_KEY, MARKER_VALUE))
    return {"projects": len(projects), "investigations": len(fixture["investigations"])}


def _reset_database(path: Path) -> dict[str, int]:
    # Do not run schema bootstrap (executescript commits) inside this transaction.
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0)) as conn:
        try:
            conn.row_factory = sqlite3.Row
            if database.database_schema_version(conn) != database.SCHEMA_VERSION:
                raise ValueError("dev DB reset requires the current schema")
            marker = conn.execute("SELECT value FROM registry_meta WHERE key=?", (MARKER_KEY,)).fetchone()
            if marker is None or marker[0] != MARKER_VALUE:
                raise ValueError("refusing to reset a DB not created by dev-db")
            # Leaving WAL requires other SQLite connections to release the DB.
            # Exclusive rollback-journal mode then excludes readers and writers.
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN EXCLUSIVE")
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )]
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                conn.execute(f"DELETE FROM {quoted}" + (" WHERE key != 'schema_version'" if table == "registry_meta" else ""))
            from .publication_registry import install_schema
            install_schema(conn)
            counts = seed_database(conn)
            conn.commit()
            return counts
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc) or "busy" in str(exc):
                raise ValueError("dev DB is in use; stop its CLI/Web/workers before --reset") from exc
            raise
        finally:
            conn.rollback()


def prepare_database(workspace_root: Path, *, reset: bool = False) -> dict[str, object]:
    """The public command supplies its own checkout root, never a user DB path."""
    path = _check_path(Path(workspace_root).resolve())
    if path.exists():
        if not reset:
            raise FileExistsError(f"dev DB already exists: {path}; reuse it or stop its processes and use dev-db --reset")
        counts = _reset_database(path)
    else:
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("orphan SQLite sidecars exist; inspect them before creating the dev DB")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents an existing arbitrary DB from being opened.
        with path.open("xb"):
            pass
        try:
            conn = registry.initialize_db(path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                counts = seed_database(conn)
                conn.commit()
            finally:
                conn.close()
        except Exception:
            path.unlink()
            raise
    return {"db_path": str(path), "action": "reset" if reset else "created", **counts}
