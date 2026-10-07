from contextlib import closing
from pathlib import Path
import sqlite3
from unittest import mock

from helpers import TempRegistryDbTestCase
from project_registry_core.persistence import database


class DatabaseCopyTest(TempRegistryDbTestCase):
    def test_copy_commits_changes_and_source_rejects_writes(self):
        before = self.db_path.read_bytes()
        target_path = self.db_path.with_name("copy.db")
        connections = []
        schema_version = database.database_schema_version

        def check_source(source):
            connections.append(source)
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                source.execute("CREATE TABLE forbidden(value TEXT)")
            return schema_version(source)

        with mock.patch.object(database, "database_schema_version", side_effect=check_source):
            with database.materialized_database_copy(self.db_path, target_path) as (target, version):
                self.assertEqual(version, database.SCHEMA_VERSION)
                self.assertTrue(target.in_transaction)
                target.execute("CREATE TABLE copied(value TEXT)")
                target.execute("INSERT INTO copied VALUES ('committed')")
        self.assertEqual(self.db_path.read_bytes(), before)
        for connection in [target, *connections]:
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                connection.execute("SELECT 1")
        with closing(sqlite3.connect(target_path)) as result:
            self.assertEqual(result.execute("SELECT value FROM copied").fetchone()[0], "committed")

    def test_failed_or_interrupted_change_removes_only_the_copy(self):
        before = self.db_path.read_bytes()
        for error in (RuntimeError, KeyboardInterrupt):
            with self.subTest(error=error.__name__):
                target_path = self.db_path.with_name("failed.db")
                with self.assertRaises(error):
                    with database.materialized_database_copy(self.db_path, target_path) as (target, _):
                        target.execute("CREATE TABLE partial(value TEXT)")
                        raise error("stopped")
                self.assertFalse(target_path.exists())
                self.assertEqual(self.db_path.read_bytes(), before)
                with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                    target.execute("SELECT 1")

    def test_target_open_failure_closes_source_and_removes_reserved_file(self):
        target_path = self.db_path.with_name("failed-open.db")
        source = sqlite3.connect(f"{self.db_path.as_uri()}?mode=ro", uri=True)
        with mock.patch.object(database.sqlite3, "connect", side_effect=[source, sqlite3.OperationalError("target unavailable")]):
            with self.assertRaisesRegex(sqlite3.OperationalError, "target unavailable"):
                with database.materialized_database_copy(self.db_path, target_path):
                    self.fail("copy must not be supplied")
        self.assertFalse(target_path.exists())
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
            source.execute("SELECT 1")

    def test_preconditions_leave_existing_files_untouched(self):
        before = self.db_path.read_bytes()
        missing = self.db_path.with_name("missing.db")
        for source, target, options, error in (
            (self.db_path, self.db_path, {}, FileExistsError),
            (missing, missing.with_name("new.db"), {}, FileNotFoundError),
            (self.db_path, missing, {"required_schema_version": -1}, database.SchemaMismatchError),
        ):
            with self.subTest(error=error.__name__), self.assertRaises(error):
                with database.materialized_database_copy(source, target, **options):
                    self.fail("copy must not be supplied")
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertFalse(missing.exists())

    def test_concurrent_creator_is_not_overwritten_or_cleaned_up(self):
        target_path = self.db_path.with_name("raced.db")
        open_file = Path.open

        def create_first(path, mode, *args, **kwargs):
            self.assertEqual((path, mode), (target_path, "xb"))
            with open_file(path, "wb") as existing:
                existing.write(b"another creator's file")
            return open_file(path, mode, *args, **kwargs)

        with mock.patch.object(Path, "open", create_first), self.assertRaises(FileExistsError):
            with database.materialized_database_copy(self.db_path, target_path):
                self.fail("copy must not be supplied")
        self.assertEqual(target_path.read_bytes(), b"another creator's file")
