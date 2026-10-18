from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT, SCRIPTS

from project_registry_core.application import review as application_review
from project_registry_core import cli
from project_registry_core.persistence import database
from project_registry_core import dev_database, project_record, registry
from project_registry_core.persistence import storage
from project_registry_core.application import presentation


class DevDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "data" / "dev" / "survey.db"

    def prepare(self, *, reset: bool = False) -> dict:
        return dev_database.prepare_database(self.root, reset=reset)

    def snapshot(self) -> list[str]:
        with contextlib.closing(sqlite3.connect(self.path)) as conn:
            return list(conn.iterdump())

    def make_legacy_database(self) -> None:
        self.prepare()
        with contextlib.closing(sqlite3.connect(self.path)) as conn:
            # Schema 4 predates publication storage; also exercise obsolete DDL.
            for table in ("publication_verifications", "publication_catalog", "catalog_metadata", "pending_publication_events", "collection_inventory_members"):
                conn.execute(f"DROP TABLE {table}")
            conn.execute("ALTER TABLE projects ADD COLUMN obsolete_fixture_column TEXT")
            conn.execute("CREATE TABLE obsolete_fixture_rows(value TEXT)")
            conn.execute("INSERT INTO obsolete_fixture_rows VALUES ('keep on failure')")
            conn.execute("CREATE VIEW obsolete_fixture_view AS SELECT * FROM obsolete_fixture_rows")
            conn.execute("CREATE TRIGGER obsolete_fixture_trigger AFTER INSERT ON obsolete_fixture_rows BEGIN UPDATE projects SET name='Old'; END")
            conn.execute("UPDATE registry_meta SET value='4' WHERE key='schema_version'")
            conn.commit()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "project_registry_core.cli", *args],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(SCRIPTS)},
            capture_output=True, text=True, timeout=30,
        )

    def test_fixture_uses_current_storage_and_representative_queries(self) -> None:
        result = self.prepare()
        self.assertEqual(result["projects"], 5)
        self.assertFalse((self.root / "data" / "survey.db").exists())
        with contextlib.closing(database.open_database(self.path)) as conn:
            self.assertEqual(database.database_schema_version(conn), database.SCHEMA_VERSION)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            promotion = application_review.promotion_queue(conn)
            self.assertEqual([p["project_key"] for p in promotion], ["proj:dddd00000001"])
            review = application_review.review_queue(conn)
            sketch = next(p for p in review if p["project_key"] == "proj:dddd00000002")
            self.assertIn("service_candidate_needs_confirmation", [s["kind"] for s in sketch["signals"]])
            for item in promotion + review:
                for command in item["suggested_commands"]:
                    self.assertEqual(shlex.split(command)[1:3], ["--db-path", str(self.path)])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_decisions").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_relations").fetchone()[0], 1)
            success = storage.load_investigation_run(conn, "inv:dev-fieldnotes-success")
            partial = storage.load_investigation_run(conn, "inv:dev-sketch-partial")
            self.assertIsNotNone(success)
            self.assertIsNotNone(partial)
            collectors = storage.list_collector_run_rows(conn, "inv:dev-sketch-partial")
            self.assertEqual(collectors[0]["status"], "partial")
            self.assertEqual(storage.list_investigation_runs(conn, project_key="proj:dddd00000005"), [])
            detail = presentation.project_detail(conn, project_key="proj:dddd00000002")
            self.assertTrue(detail["identifiers"])
            self.assertTrue(detail["decisions"])
            history = presentation.investigation_run_detail(conn, run_id="inv:dev-sketch-partial")
            self.assertTrue(history)

    def test_reset_restores_data_and_clears_added_rows(self) -> None:
        self.prepare()
        before = self.snapshot()
        with contextlib.closing(database.open_database(self.path)) as conn:
            conn.execute("UPDATE projects SET name='Changed'")
            conn.execute("INSERT INTO sync_state VALUES ('demo', 'fixture', '{}', '{}', 'demo')")
            conn.commit()
        self.prepare(reset=True)
        self.assertEqual(self.snapshot(), before)
        self.prepare(reset=True)
        self.assertEqual(self.snapshot(), before)

    def test_existing_database_requires_explicit_reset(self) -> None:
        self.prepare()
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(self.path.read_bytes(), before)

    def test_unmarked_database_is_never_reset(self) -> None:
        registry.initialize_db(self.path).close()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "not created by dev-db"):
            self.prepare(reset=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_schema_is_recreated_by_cli_with_current_fictional_data(self) -> None:
        self.prepare()
        current = self.snapshot()
        self.path.unlink()
        self.make_legacy_database()
        inode = self.path.stat().st_ino
        with self.assertRaises(database.SchemaMismatchError):
            database.open_database(self.path)
        before = self.snapshot()
        stderr = io.StringIO()
        with mock.patch.object(registry, "ROOT", self.root), contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["--db-path", str(self.path), "show", "--all"]), 1)
        self.assertIn("dev-db --reset", stderr.getvalue())
        self.assertIn("discard existing development data", stderr.getvalue())
        self.assertEqual(self.snapshot(), before)
        stdout = io.StringIO()
        with mock.patch.object(registry, "ROOT", self.root), contextlib.redirect_stdout(stdout):
            self.assertEqual(cli.main(["dev-db", "--reset"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["action"], "reset")
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(self.snapshot(), current)
        with contextlib.closing(database.open_database(self.path)) as conn:
            self.assertEqual(database.database_schema_version(conn), database.SCHEMA_VERSION)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertTrue(presentation.project_detail(conn, project_key="proj:dddd00000002")["identifiers"])
            self.assertTrue(storage.load_investigation_run(conn, "inv:dev-sketch-partial"))
        result = self.run_cli("--db-path", str(self.path), "show", "--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Demo Sprout", result.stdout)

    def test_invalid_or_future_schema_is_never_reset(self) -> None:
        self.prepare()
        for version in ("0", "-1", "invalid", str(database.SCHEMA_VERSION + 1), None):
            with self.subTest(version=version):
                with contextlib.closing(sqlite3.connect(self.path)) as conn:
                    conn.execute("DELETE FROM registry_meta WHERE key='schema_version'")
                    if version is not None:
                        conn.execute("INSERT INTO registry_meta VALUES ('schema_version', ?)", (version,))
                    conn.commit()
                before = self.path.read_bytes()
                with self.assertRaisesRegex(ValueError, "schema version"):
                    self.prepare(reset=True)
                self.assertEqual(self.path.read_bytes(), before)

    def test_old_unmarked_wrong_marker_and_empty_databases_are_never_reset(self) -> None:
        self.make_legacy_database()
        for marker in (None, "some-other-fixture"):
            with self.subTest(marker=marker):
                with contextlib.closing(sqlite3.connect(self.path)) as conn:
                    conn.execute("DELETE FROM registry_meta WHERE key='dev_fixture'")
                    if marker:
                        conn.execute("INSERT INTO registry_meta VALUES ('dev_fixture', ?)", (marker,))
                    conn.commit()
                before = self.path.read_bytes()
                with self.assertRaisesRegex(ValueError, "not created by dev-db"):
                    self.prepare(reset=True)
                self.assertEqual(self.path.read_bytes(), before)
        self.path.unlink()
        self.path.touch()
        with self.assertRaisesRegex(ValueError, "not created by dev-db"):
            self.prepare(reset=True)
        self.assertEqual(self.path.read_bytes(), b"")

    def test_symbolic_and_hard_link_targets_are_refused(self) -> None:
        self.path.parent.mkdir(parents=True)
        target = self.root / "user.db"
        registry.initialize_db(target).close()
        before = target.read_bytes()
        for hard in (False, True):
            with self.subTest(hard=hard):
                if hard:
                    os.link(target, self.path)
                else:
                    self.path.symlink_to(target)
                for reset in (False, True):
                    with self.assertRaisesRegex(ValueError, "links"):
                        self.prepare(reset=reset)
                self.path.unlink()
                self.assertEqual(target.read_bytes(), before)

    def test_linked_parent_and_orphan_sidecars_are_refused(self) -> None:
        (self.root / "data").mkdir()
        other = self.root / "other"
        other.mkdir()
        self.path.parent.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.prepare()
        self.path.parent.unlink()
        self.path.parent.mkdir()
        sidecar = Path(str(self.path) + "-wal")
        sidecar.write_text("do not touch")
        with self.assertRaisesRegex(ValueError, "orphan"):
            self.prepare()
        self.assertEqual(sidecar.read_text(), "do not touch")

    def test_linked_sidecars_are_never_used_during_reset(self) -> None:
        self.make_legacy_database()
        before = self.path.read_bytes()
        target = self.root / "other-file"
        target.write_text("do not touch")
        for suffix in ("-wal", "-shm", "-journal"):
            for hard in (False, True):
                with self.subTest(suffix=suffix, hard=hard):
                    sidecar = Path(str(self.path) + suffix)
                    if hard:
                        os.link(target, sidecar)
                    else:
                        sidecar.symlink_to(target)
                    with self.assertRaisesRegex(ValueError, "links"):
                        self.prepare(reset=True)
                    sidecar.unlink()
                    self.assertEqual(self.path.read_bytes(), before)
                    self.assertEqual(target.read_text(), "do not touch")

    def test_reset_refuses_open_reader_and_writer(self) -> None:
        self.prepare()
        before = self.snapshot()
        for write in (False, True):
            with self.subTest(write=write), contextlib.closing(database.open_database(self.path)) as conn:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                conn.execute("SELECT * FROM projects").fetchall()
                with self.assertRaisesRegex(ValueError, "in use"):
                    self.prepare(reset=True)
                conn.rollback()
        self.assertEqual(self.snapshot(), before)

    def test_old_schema_reset_refuses_readers_and_writers_in_both_journal_modes(self) -> None:
        self.make_legacy_database()
        before = self.snapshot()
        for mode in ("WAL", "DELETE"):
            for write in (False, True):
                with self.subTest(mode=mode, write=write), contextlib.closing(sqlite3.connect(self.path)) as conn:
                    conn.execute(f"PRAGMA journal_mode={mode}")
                    conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                    conn.execute("SELECT * FROM projects").fetchall()
                    with self.assertRaisesRegex(ValueError, "in use"):
                        self.prepare(reset=True)
                    conn.rollback()
                self.assertEqual(self.snapshot(), before)

    def test_old_schema_and_data_survive_failed_schema_install_or_seeding(self) -> None:
        self.make_legacy_database()
        before = self.snapshot()
        inode = self.path.stat().st_ino
        original_create = database.create_schema
        def fail_create(conn, **kwargs):
            original_create(conn, **kwargs)
            raise RuntimeError("schema failed")
        def fail_seed(conn):
            project_record.save_project(conn, project_record.default_project_record("proj:partial", "nsid", "org.example.test"), commit=False)
            raise RuntimeError("fixture failed")
        for owner, name, failure in ((database, "create_schema", fail_create), (dev_database, "seed_database", fail_seed)):
            with self.subTest(stage=name), mock.patch.object(owner, name, side_effect=failure):
                with self.assertRaises(RuntimeError):
                    self.prepare(reset=True)
            self.assertEqual(self.path.stat().st_ino, inode)
            self.assertEqual(self.snapshot(), before)

    def test_invalid_fixture_foreign_keys_roll_back_old_schema(self) -> None:
        self.make_legacy_database()
        before = self.snapshot()
        original_seed = dev_database.seed_database
        def invalid_seed(conn):
            counts = original_seed(conn)
            conn.execute("INSERT INTO project_key_aliases VALUES ('proj:orphan', 'proj:missing', '', '')")
            return counts
        with mock.patch.object(dev_database, "seed_database", side_effect=invalid_seed):
            with self.assertRaisesRegex(ValueError, "foreign key validation"):
                self.prepare(reset=True)
        self.assertEqual(self.snapshot(), before)

    def test_failed_reset_rolls_back_all_deletions_and_partial_seeding(self) -> None:
        self.prepare()
        before = self.snapshot()
        def fail(conn):
            project_record.save_project(conn, project_record.default_project_record("proj:partial", "nsid", "org.example.test"), commit=False)
            raise RuntimeError("fixture failed")
        with mock.patch.object(dev_database, "seed_database", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "fixture failed"):
                self.prepare(reset=True)
        self.assertEqual(self.snapshot(), before)

    def test_failed_creation_leaves_no_database(self) -> None:
        with mock.patch.object(dev_database, "seed_database", side_effect=RuntimeError("fixture failed")):
            with self.assertRaises(RuntimeError):
                self.prepare()
        self.assertEqual(list(self.path.parent.glob("survey.db*")), [])

    def test_real_cli_reads_and_mutates_selected_database(self) -> None:
        self.prepare()
        result = self.run_cli("--db-path", str(self.path), "show", "--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Demo Sprout", result.stdout)
        result = self.run_cli("--db-path", str(self.path), "set-tracking-state", "-p", "proj:dddd00000005", "--tracking-state", "stalled")
        self.assertEqual(result.returncode, 0, result.stderr)
        with contextlib.closing(database.open_database(self.path)) as conn:
            self.assertEqual(project_record.fetch_project(conn, "proj:dddd00000005")["tracking_state"], "stalled")

    def test_cli_selection_restored_and_conflicting_selectors_rejected(self) -> None:
        self.prepare()
        original = registry.DB_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--db-path", str(self.path), "show", "--all"]), 0)
        self.assertEqual(registry.DB_PATH, original)
        for command in (["dev-db"], ["init-db", "--db-path", str(self.path)]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                cli.main(["--db-path", str(self.path), *command])
            self.assertEqual(exc.exception.code, 2)
        missing = self.root / "missing.db"
        result = self.run_cli("--db-path", str(missing), "show", "--all")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(missing.exists())
