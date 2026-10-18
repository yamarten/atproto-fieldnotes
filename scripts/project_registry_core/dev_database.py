"""Small fictional registry for manual CLI/Web checks, never the unit-test shared DB."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .persistence import database
from . import project_record, registry
from .persistence import storage
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


def _check_generated_database(conn: sqlite3.Connection) -> None:
    has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='registry_meta'").fetchone()
    marker = conn.execute("SELECT value FROM registry_meta WHERE key=?", (MARKER_KEY,)).fetchone() if has_meta else None
    if marker is None or marker[0] != MARKER_VALUE:
        raise ValueError("refusing to reset a DB not created by dev-db")
    try:
        version = database.database_schema_version(conn)
    except ValueError as exc:
        raise ValueError("dev DB reset requires a valid schema version") from exc
    if version is None or not 1 <= version <= database.SCHEMA_VERSION:
        raise ValueError(f"dev DB reset requires a known schema version (1..{database.SCHEMA_VERSION}), got {version}")


def _reset_database(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0)) as conn:
        try:
            conn.row_factory = sqlite3.Row
            _check_generated_database(conn)
            # Leaving WAL requires other SQLite connections to release the DB.
            # Exclusive rollback-journal mode then excludes readers and writers.
            conn.execute("PRAGMA journal_mode=DELETE")
            # Dropping all tables must not run old FK cascades or revision triggers.
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN EXCLUSIVE")
            # Recheck after acquiring the lock: validation and replacement are atomic.
            _check_generated_database(conn)
            objects = conn.execute(
                "SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view', 'table') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'trigger' THEN 0 WHEN 'view' THEN 1 ELSE 2 END"
            ).fetchall()
            for kind, name in objects:
                quoted = '"' + name.replace('"', '""') + '"'
                conn.execute(f"DROP {kind.upper()} {quoted}")
            database.create_schema(conn, commit=False)
            counts = seed_database(conn)
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("regenerated dev DB failed foreign key validation")
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
            raise FileExistsError(f"dev DB already exists: {path}; reuse it or stop its processes and use dev-db --reset to discard existing development data and recreate the current schema")
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
    return {"db_path": str(path), "action": "reset" if reset else "created", "schema_version": database.SCHEMA_VERSION, **counts}
