from __future__ import annotations

import json
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from project_registry_core import collection_trust, database, identifier_storage, project_record
from project_registry_core.application import decisions, merge, presentation


class MergeIdentifierPreservationTest(TempRegistryDbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = registry.open_db()

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def seed(self, key, url="https://example.com"):
        project = self.make_project(key, "nsid", "com.example.post")
        project["service_url_candidates"] = [url] if url else []
        project_record.save_project(self.conn, project)

    def confirm(self, key, url):
        presentation.confirm_candidate(self.conn, project_key=key, identifier_type="service_url", value=url)

    def merge(self, source, target):
        return merge.merge_projects(self.conn, project_key=source, target_project_key=target, execute=True, force=True)

    def adoptions(self, key):
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM project_identifier_adoptions WHERE project_key=? ORDER BY adoption_id", (key,),
        )]

    def identifiers(self, key):
        return [dict(row) for row in self.conn.execute(
            "SELECT kind,value,role,classification,is_primary,provider,metadata_json,observed_at "
            "FROM project_identifiers WHERE project_key=? ORDER BY kind,value,role", (key,),
        )]

    def test_confirm_merge_fetch_preserves_both_directions_and_candidate_collisions(self):
        for index, (url, owner, candidate) in enumerate(
            (url, owner, candidate)
            for url in ("https://example.com", "https://example.com/docs")
            for owner in ("source", "target")
            for candidate in ("", "same", "https://app.example.com")
        ):
            with self.subTest(url=url, owner=owner, candidate=candidate):
                source, target = f"proj:s{index}", f"proj:t{index}"
                owned_key = source if owner == "source" else target
                other_key = target if owner == "source" else source
                self.seed(owned_key, url)
                self.seed(other_key, url if candidate == "same" else candidate)
                self.confirm(owned_key, url)
                before = self.adoptions(owned_key)
                self.merge(source, target)
                fetched = project_record.fetch_project(self.conn, target)
                self.assertIn(url, fetched["service_urls"])
                self.assertTrue(registry.identifier_has_active_confirm_decision(fetched, "service_url", url))
                self.assertEqual(self.adoptions(target), [dict(row, project_key=target) for row in before])
                self.assertIn({"kind": "service_url", "value": url}, registry.trusted_anchor_nodes(fetched))
                rows = self.identifiers(target)
                project_record.save_project(self.conn, fetched)
                self.assertEqual(self.identifiers(target), rows)
                self.assertEqual(self.adoptions(target), [dict(row, project_key=target) for row in before])

    def test_repeated_merges_keep_both_original_adoption_events_without_new_origin(self):
        url = "https://example.com/docs"
        for key in ("proj:a", "proj:b", "proj:c"):
            self.seed(key, url)
        with mock.patch.object(project_record, "iso_now", return_value="2026-09-01T00:00:00+00:00"):
            self.confirm("proj:a", url)
            self.confirm("proj:b", url)
        before = self.adoptions("proj:a") + self.adoptions("proj:b")
        self.merge("proj:a", "proj:b")
        self.merge("proj:b", "proj:c")
        project_record.save_project(self.conn, project_record.fetch_project(self.conn, "proj:c"))
        self.assertEqual(self.adoptions("proj:c"), sorted(
            [dict(row, project_key="proj:c") for row in before], key=lambda row: row["adoption_id"],
        ))
        self.assertEqual(project_record.resolve_project_key_input(self.conn, "proj:a"), "proj:c")

    def test_source_exact_rows_preserve_role_metadata_provider_and_observation(self):
        url = "https://example.com/docs"
        self.seed("proj:s", url)
        self.seed("proj:t", url)
        self.confirm("proj:s", url)
        self.conn.execute(
            "UPDATE project_identifiers SET metadata_json=?,provider=?,observed_at=? "
            "WHERE project_key='proj:s' AND kind='service_url'",
            ('{"evidence":{"original":"source"}}', "custom-provider", "2026-08-01"),
        )
        self.conn.execute(
            "INSERT INTO project_identifiers VALUES ('proj:s',99,'service_url',?,'documentation','candidate',0,'docs-provider',?,?)",
            (url, '{"purpose":"documentation"}', "2026-08-02"),
        )
        metadata = json.loads(self.conn.execute(
            "SELECT metadata_json FROM project_decisions WHERE project_key='proj:s' AND kind='confirm_identifier'",
        ).fetchone()[0])
        metadata["audit"] = {"reference": "original confirmation"}
        metadata["role"] = ""  # SQL role is authoritative, including older rows.
        self.conn.execute(
            "UPDATE project_decisions SET metadata_json=? WHERE project_key='proj:s' AND kind='confirm_identifier'",
            (json.dumps(metadata),),
        )
        self.conn.commit()
        before = [row for row in self.identifiers("proj:s") if row["kind"] == "service_url"]
        self.merge("proj:s", "proj:t")
        self.assertEqual([row for row in self.identifiers("proj:t") if row["kind"] == "service_url"], before)
        fetched = project_record.fetch_project(self.conn, "proj:t")
        confirmation = fetched["confirmed_identifiers"][0]
        self.assertEqual(confirmation["role"], "service")
        self.assertEqual(confirmation["audit"], metadata["audit"])
        self.assertEqual(confirmation["source"], "web_ui")
        project_record.save_project(self.conn, fetched)
        self.assertEqual([row for row in self.identifiers("proj:t") if row["kind"] == "service_url"], before)
        self.assertEqual(len(self.adoptions("proj:t")), 1)

    def test_source_confirmed_nsid_wins_over_target_candidate(self):
        self.seed("proj:s", "")
        self.seed("proj:t", "")
        self.conn.execute("UPDATE project_identifiers SET classification='candidate' WHERE project_key IN ('proj:s','proj:t')")
        self.conn.commit()
        presentation.confirm_candidate(self.conn, project_key="proj:s", identifier_type="nsid", value="com.example.post")
        source = project_record.fetch_project(self.conn, "proj:s")
        target = project_record.fetch_project(self.conn, "proj:t")
        preview = registry.merge_project_records(target, source)
        self.assertEqual(collection_trust.independent_adoptions(preview, "com.example.post"), [
            dict(row, project_key="proj:t") for row in self.adoptions("proj:s")
        ])
        self.assertEqual(collection_trust.independent_adoptions(source, "com.example.post"), self.adoptions("proj:s"))
        self.merge("proj:s", "proj:t")
        self.assertEqual(self.identifiers("proj:t")[0]["classification"], "confirmed")
        self.assertEqual(project_record.fetch_project(self.conn, "proj:t")["confirmed_identifiers"][0]["role"], "owned_nsid")

    def test_identifier_guards_require_resolution_even_with_force(self):
        for index, guard in enumerate((decisions.set_identifier_block, decisions.set_identifier_defer)):
            for owner in ("source", "target"):
                with self.subTest(guard=guard.__name__, owner=owner):
                    source, target = f"proj:s{index}{owner}", f"proj:t{index}{owner}"
                    owned, guarded = (source, target) if owner == "source" else (target, source)
                    self.seed(owned)
                    self.seed(guarded)
                    self.confirm(owned, "https://example.com")
                    guard(self.conn, project_key=guarded, identifier_type="service_url", value="https://example.com")
                    before = database.normalized_rows_snapshot(self.conn)
                    with self.assertRaisesRegex(ValueError, "identifier block/defer"):
                        self.merge(source, target)
                    self.assertEqual(database.normalized_rows_snapshot(self.conn), before)

    def test_noncurrent_and_cleared_adoptions_are_not_reactivated(self):
        repo = "https://github.com/example/former"
        self.seed("proj:s", "")
        self.seed("proj:t", "")
        presentation.confirm_candidate(self.conn, project_key="proj:s", identifier_type="repo_url", value=repo)
        identifier_storage.move_current_to_noncurrent(
            self.conn, project_key="proj:s", kind="repo_url", value=repo, role="project_repository",
            standing="former", reason="operator retired repository", recorded_at="2026-09-01",
        )
        self.conn.commit()
        before = self.adoptions("proj:s")
        self.merge("proj:s", "proj:t")
        self.assertFalse(any(row["value"] == repo for row in self.identifiers("proj:t")))
        self.assertEqual(self.adoptions("proj:t"), [dict(row, project_key="proj:t") for row in before])
        self.assertEqual(self.conn.execute(
            "SELECT project_key,origin_project_key,standing FROM project_noncurrent_identifiers WHERE value=?", (repo,),
        ).fetchone()[:], ("proj:t", "proj:s", "former"))
        project_record.save_project(self.conn, project_record.fetch_project(self.conn, "proj:t"))
        self.assertEqual(self.adoptions("proj:t"), [dict(row, project_key="proj:t") for row in before])

    def test_current_noncurrent_conflict_is_rejected_without_reactivation(self):
        self.seed("proj:s")
        self.seed("proj:t")
        self.confirm("proj:s", "https://example.com")
        self.confirm("proj:t", "https://example.com")
        identifier_storage.move_current_to_noncurrent(
            self.conn, project_key="proj:s", kind="service_url", value="https://example.com", role="service",
            standing="non_production", reason="test deployment", recorded_at="2026-09-01",
        )
        self.conn.commit()
        before = database.normalized_rows_snapshot(self.conn)
        with self.assertRaisesRegex(ValueError, "noncurrent identifier"):
            self.merge("proj:s", "proj:t")
        self.assertEqual(database.normalized_rows_snapshot(self.conn), before)

    def test_cleared_source_confirmation_stays_cleared_when_target_has_current_claim(self):
        url = "https://example.com"
        for key in ("proj:s", "proj:t"):
            self.seed(key, url)
            self.confirm(key, url)
        source = project_record.fetch_project(self.conn, "proj:s")
        registry.clear_confirmed_identifier_decision(source, "service_url", url)
        registry.downgrade_identifier_to_candidate(source, "service_url", url, keep_candidate=True)
        project_record.save_project(self.conn, source)
        before = self.adoptions("proj:s") + self.adoptions("proj:t")
        self.assertTrue(self.adoptions("proj:s")[0]["cleared_at"])
        self.merge("proj:s", "proj:t")
        project_record.save_project(self.conn, project_record.fetch_project(self.conn, "proj:t"))
        self.assertEqual(self.adoptions("proj:t"), sorted(
            [dict(row, project_key="proj:t") for row in before], key=lambda row: row["adoption_id"],
        ))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM project_decisions WHERE project_key='proj:t' AND kind='confirm_identifier' AND cleared_at<>''",
        ).fetchone()[0], 1)

    def test_failure_after_adoption_transfer_rolls_back_ownership_and_revision(self):
        self.seed("proj:s")
        self.seed("proj:t")
        self.confirm("proj:s", "https://example.com")
        before = database.normalized_rows_snapshot(self.conn)
        source_before = project_record.fetch_project(self.conn, "proj:s")
        target_before = project_record.fetch_project(self.conn, "proj:t")
        with mock.patch.object(database, "replace_project_children", side_effect=RuntimeError("save failed")):
            with self.assertRaisesRegex(RuntimeError, "save failed"):
                self.merge("proj:s", "proj:t")
        self.assertEqual(database.normalized_rows_snapshot(self.conn), before)
        self.assertEqual(project_record.fetch_project(self.conn, "proj:s"), source_before)
        self.assertEqual(project_record.fetch_project(self.conn, "proj:t"), target_before)

    def test_cleared_collection_confirmation_does_not_revoke_other_origin_at_same_time(self):
        nsid = "com.example.post"
        for key in ("proj:s", "proj:t"):
            self.seed(key, "")
        self.conn.execute("UPDATE project_identifiers SET classification='candidate'")
        self.conn.commit()
        with mock.patch.object(project_record, "iso_now", return_value="2026-09-01T00:00:00+00:00"):
            for key in ("proj:s", "proj:t"):
                presentation.confirm_candidate(self.conn, project_key=key, identifier_type="nsid", value=nsid)
        source = project_record.fetch_project(self.conn, "proj:s")
        registry.clear_confirmed_identifier_decision(source, "nsid", nsid)
        registry.downgrade_identifier_to_candidate(source, "nsid", nsid, keep_candidate=False)
        project_record.save_project(self.conn, source)
        adopted_target = self.adoptions("proj:t")
        self.merge("proj:s", "proj:t")
        current = project_record.fetch_project(self.conn, "proj:t")
        self.assertEqual(collection_trust.independent_adoptions(current, nsid), adopted_target)
        confirmations = current["confirmed_identifiers"]
        self.assertEqual({entry["origin_project_key"] for entry in confirmations}, {"proj:s", "proj:t"})
