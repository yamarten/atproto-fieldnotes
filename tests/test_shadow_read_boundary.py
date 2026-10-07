import sqlite3
from unittest import mock

from helpers import ROOT, TempRegistryDbTestCase
from project_registry_core.maintenance import collector_relation_shadow, identifier_storage_shadow, merge_policy_shadow, project_repository_shadow, project_site_shadow, trust_policy_shadow


class ComparisonStopped(RuntimeError):
    pass


class ShadowReadBoundaryTest(TempRegistryDbTestCase):
    comparisons = (
        (trust_policy_shadow, "trust-policy-cases-v1.json"),
        (merge_policy_shadow, "merge-policy-cases-v1.json"),
        (collector_relation_shadow, "collector-relation-policy-cases-v1.json"),
        (identifier_storage_shadow, None),
        (project_repository_shadow, None),
        (project_site_shadow, None),
    )

    def options(self, fixture):
        return {"case_catalog_path": ROOT / "tests/fixtures/project_registry" / fixture} if fixture else {}

    def test_actual_comparison_connection_rejects_writes_and_closes_on_failure(self):
        for module, fixture in self.comparisons:
            with self.subTest(comparison=module.__name__):
                connections = []

                def inspect_connection(conn, **kwargs):
                    connections.append(conn)
                    self.assertTrue(conn.in_transaction)
                    # Exercise the connection supplied by the real path entry point.
                    with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                        conn.execute("CREATE TABLE forbidden(value TEXT)")
                    raise ComparisonStopped("comparison failed")

                with mock.patch.object(module, "compare_database", side_effect=inspect_connection):
                    with self.assertRaises(ComparisonStopped):
                        module.compare_database_path(self.db_path, **self.options(fixture))
                self.assertEqual(len(connections), 1)
                with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                    connections[0].execute("SELECT 1")

    def test_missing_source_is_not_created(self):
        missing = self.db_path.parent / "missing.db"
        for module, fixture in self.comparisons:
            with self.subTest(comparison=module.__name__):
                with self.assertRaises(FileNotFoundError):
                    module.compare_database_path(missing, **self.options(fixture))
                self.assertFalse(missing.exists())
