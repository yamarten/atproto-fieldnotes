from __future__ import annotations

import datetime as dt
import contextlib
import json
from unittest import mock

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, registry

from project_registry_core.application import review as application_review
from project_registry_core import project_review
from project_registry_core import project_record, promotion_attention, runtime_view
from project_registry_core.application import decisions, merge, presentation


class _PromotionAttentionFixtures:
    def candidate(self, key):
        project = self.make_project(key, "service_url", "https://app.example")
        project.update(category="project", service_status="live", service_status_confidence="medium",
                       service_status_evidence=["http_200", "login_surface_detected"])
        registry.confirm_service_seed(project, "https://app.example", source="test")
        return project_record.ensure_project_shape(project)

    def assert_lowered_matches_full_scan(self, conn, **options):
        arguments = dict(queue="promotion", tracking_states={"active"}, promotion_view="lowered")
        arguments.update(options)
        actual = presentation.queue_payload(conn, **arguments)
        # Retain the previous all-project input path as the semantic oracle;
        # the unchanged builder and presentation layer perform final selection.
        with mock.patch.object(project_record, "list_projects_with_decision",
                               side_effect=lambda conn, kind, **kw: project_record.list_projects(conn, **kw)):
            expected = presentation.queue_payload(conn, **arguments)
        self.assertEqual(actual, expected)
        return actual

    def legacy_project(self, conn, key, *, expired=False, cleared=False, defer_days=None, legacy_defer=False):
        project = self.candidate(key)
        project_record.save_project(conn, project)
        if defer_days is not None:
            decisions.set_promotion_defer(conn, project_key=key, reason="release", days=defer_days)
            if legacy_defer:
                saved = project_record.fetch_project(conn, key)
                saved["promotion_deferrals"][0].pop("fingerprint")
                project_record.save_project(conn, saved)
        saved = project_record.fetch_project(conn, key)
        now = dt.datetime.now(dt.timezone.utc)
        original = {"fingerprint": promotion_attention.fingerprint(saved),
                    "snapshot": promotion_attention.snapshot(saved),
                    "outcome_counts": {"reviewed": 3, "defer": 1},
                    "last_outcome": "restore" if cleared else "reviewed",
                    "last_action_at": now.isoformat()}
        if not cleared:
            original.update(reviewed_until=(now + dt.timedelta(days=-1 if expired else 20)).isoformat(),
                            reviewed_fingerprint=original["fingerprint"])
        state = json.loads(conn.execute("SELECT state_json FROM projects WHERE project_key=?", (key,)).fetchone()[0])
        state.setdefault("ops", {})["promotion_attention"] = original
        conn.execute("UPDATE projects SET state_json=? WHERE project_key=?", (json.dumps(state), key))
        conn.commit()
        return original


class PromotionAttentionUnitTest(_PromotionAttentionFixtures, ProjectRecordTestCase):
    def test_fifty_held_candidates_leave_normal_queue_but_remain_recoverable(self):
        projects = [self.candidate(f"proj:{n:012d}") for n in range(55)]
        first = application_review.build_showcase_queue_from_projects(projects)
        self.assertEqual(len(first), 50)
        for project in projects[:50]:
            project_review.defer_promotion_on_project(project)
        next_queue = application_review.build_showcase_queue_from_projects(projects)
        self.assertEqual(len(next_queue), 5)
        self.assertEqual([item["project_key"] for item in next_queue[:5]], [p["project_key"] for p in projects[50:]])
        inbox = application_review.build_showcase_queue_from_projects(projects, view="inbox")
        self.assertEqual(len(inbox), 5)
        self.assertEqual(len(application_review.build_showcase_queue_from_projects(projects, view="all", limit=None)), 55)
        project_review.clear_active_promotion_deferrals(projects[0])
        self.assertEqual(len(application_review.build_showcase_queue_from_projects(projects, view="inbox")), 6)

    def test_fingerprint_ignores_refresh_but_detects_material_change_and_expiry(self):
        project = self.candidate("proj:attention")
        now = dt.datetime.now(dt.timezone.utc)
        project_review.defer_promotion_on_project(project)
        original = promotion_attention.fingerprint(project)
        project.update(last_seen_at="2099-01-01", last_enriched_at="2099-01-01", summary="A wording revision",
                       collection_observations=[{"recent_count": 500}])
        # First material becoming available is a change; subsequent prose is not.
        self.assertNotEqual(original, promotion_attention.fingerprint(project))
        project_review.defer_promotion_on_project(project)
        project["summary"] = "Same purpose, different wording"
        self.assertEqual(promotion_attention.projection(project, now=now)["group"], "held")
        self.assertEqual(promotion_attention.projection(project, now=now + dt.timedelta(days=31))["group"], "due")
        project["features"] = ["new record interoperability"]
        self.assertEqual(promotion_attention.projection(project, now=now)["group"], "changed")

    def test_material_schema_and_identifier_changes_reopen_review(self):
        project = self.candidate("proj:schema")
        project["resolved_nsids"] = ["app.example.record"]
        project["lexicon_schema_samples"] = [{"nsid":"app.example.record", "defs":{"main":{"type":"record"}}}]
        project_review.defer_promotion_on_project(project)
        project["lexicon_schema_samples"][0]["observed_at"] = "2099-01-01"
        self.assertEqual(promotion_attention.projection(project)["group"], "held")
        project["lexicon_schema_samples"][0]["defs"]["extra"] = {"type":"string"}
        self.assertEqual(promotion_attention.projection(project)["group"], "changed")

    def test_kind_filter_keeps_existing_score_scope(self):
        project = self.candidate("proj:technical")
        project.update(technical_interest_level="high", technical_interest_reasons=["novel_design_pattern"],
                       technical_interest_summary="Distinctive protocol design")
        item = application_review.build_showcase_queue_from_projects([project], kinds={"technical_insight_candidate"})[0]
        self.assertEqual(item["service_evidence_group"], "not_applicable")
        self.assertEqual(item["score_breakdown"]["components"]["service_evidence"], 0)


class PromotionAttentionTest(_PromotionAttentionFixtures, TempRegistryDbTestCase):
    def test_all_views_match_full_hydration_across_pages_filters_and_updates(self):
        with contextlib.closing(registry.open_db()) as conn:
            for n in range(12):
                project = self.candidate(f"proj:equivalent{n:02d}")
                project.update(technical_interest_level="high" if n % 2 else "none",
                               technical_interest_reasons=["novel_design_pattern"],
                               technical_interest_summary="Protocol design")
                if n % 3 == 0:
                    project_review.set_promotion_priority_on_project(project, lowered=True, reason="Straße")
                if n % 4 == 0:
                    project_review.defer_promotion_on_project(project, reason="公開待ち")
                if n == 5:
                    project["category"] = "personal_tool"
                if n == 6:
                    project.update(tracking_state="ignore", service_status="unknown")
                if n == 7:
                    project["introduced_url"] = "https://example.com/post"
                project_record.save_project(conn, project)
            self.legacy_project(conn, "proj:legacyhold")
            self.legacy_project(conn, "proj:legacyexpired", expired=True)

            def check():
                # Reference path keeps the old extra normalization passes and
                # full scan, including projects excluded by decision selection.
                projects = project_record.list_projects(conn)
                for view in ("queue", "inbox", "technical", "waiting", "lowered", "all"):
                    for kinds in (None, {"technical_insight_candidate"}, {"service_surface_confirmed"}):
                        for personal in (False, True):
                            selected = [p for p in projects if view not in {"inbox", "technical"}
                                        or p["tracking_state"] == "active"]
                            expected = application_review.build_showcase_queue_from_projects(
                                [project_record.ensure_project_shape(p) for p in selected],
                                view=view, kinds=kinds, include_personal_tools=personal, limit=None,
                                command_prefix=application_review.registry_command_prefix(conn))
                            if view == "queue":
                                self.assertEqual(application_review.promotion_queue(conn, view=view, kinds=kinds,
                                    include_personal_tools=personal, limit=None), expected)
                                continue
                            reasons = {}
                            for item in expected:
                                for note in {item["priority_reason"], item["last_promotion_deferral_reason"]} - {""}:
                                    reasons[note] = reasons.get(note, 0) + 1
                            for reason in ("", "STRASSE", "公開", "absent"):
                                filtered = [i for i in expected if reason.casefold() in i["priority_reason"].casefold()
                                            or reason.casefold() in i["last_promotion_deferral_reason"].casefold()]
                                for offset in (0, 2, 50):
                                    actual = presentation.queue_payload(conn, queue="promotion",
                                        tracking_states={"active"}, promotion_view=view, kinds=kinds,
                                        include_personal_tools=personal, reason=reason, offset=offset, limit=2)
                                    self.assertEqual(actual, dict(queue="promotion", items=filtered[offset:offset+2],
                                        total=len(filtered), count=len(filtered[offset:offset+2]), offset=offset,
                                        has_more=offset+len(filtered[offset:offset+2]) < len(filtered),
                                        reasons=[dict(reason=k, count=v) for k, v in sorted(reasons.items())]))
            check()
            decisions.set_promotion_defer(conn, project_key="proj:equivalent01", reason="new")
            decisions.set_promotion_defer(conn, project_key="proj:equivalent00", clear=True)
            decisions.set_promotion_priority(conn, project_key="proj:equivalent03", lowered=False)
            changed = project_record.fetch_project(conn, "proj:equivalent04")
            changed["service_status"] = "beta"
            project_record.save_project(conn, changed)
            expired = project_record.fetch_project(conn, "proj:equivalent08")
            expired["promotion_deferrals"][0]["expires_at"] = "2000-01-01T00:00:00+00:00"
            project_record.save_project(conn, expired)
            check()

    def test_queue_normalizes_once_and_retains_saveable_complete_views(self):
        with contextlib.closing(registry.open_db()) as conn:
            project_record.save_project(conn, self.candidate("proj:once"))
            with mock.patch.object(project_record, "ensure_project_shape", wraps=project_record.ensure_project_shape) as shape, \
                 mock.patch.object(runtime_view, "refresh_baselines", wraps=runtime_view.refresh_baselines) as baseline:
                application_review.promotion_queue(conn, view="inbox")
            self.assertEqual(shape.call_count, 1)
            self.assertEqual(baseline.call_count, 1)
            fetched = project_record.fetch_project(conn, "proj:once")
            listed = project_record.list_projects(conn)[0]
            self.assertEqual(fetched, listed)
            self.assertTrue(runtime_view.is_complete(listed))
            self.assertTrue(runtime_view.normalized_children_unchanged(listed))
            project_record.save_project(conn, listed)

    def test_list_snapshot_keeps_scalar_children_and_revision_together(self):
        with contextlib.closing(registry.open_db()) as conn, contextlib.closing(registry.open_db()) as writer:
            key = "proj:snapshotall"
            project_record.save_project(conn, self.candidate(key))
            revision = presentation.project_revision(conn, key)
            original = project_record._project_from_snapshot_row
            def update_after_selection(reader, row):
                if reader is conn:
                    changed = project_record.fetch_project(writer, key)
                    changed["name"] = "updated"
                    project_review.set_promotion_priority_on_project(changed, lowered=True, reason="new")
                    project_record.save_project(writer, changed)
                return original(reader, row)
            with mock.patch.object(project_record, "_project_from_snapshot_row", side_effect=update_after_selection):
                selected = project_record.list_projects(conn)[0]
            self.assertNotEqual(selected["name"], "updated")
            self.assertIsNone(project_review.active_promotion_priority_adjustment(selected))
            self.assertEqual(str(selected[runtime_view.RUNTIME_PROJECT_REVISION]), revision)
            self.assertFalse(conn.in_transaction)
            current = project_record.fetch_project(conn, key)
            self.assertEqual(current["name"], "updated")
            self.assertEqual(project_review.active_promotion_priority_adjustment(current)["reason"], "new")

    def test_lowered_prefilter_preserves_full_response_across_filters_and_pages(self):
        with contextlib.closing(registry.open_db()) as conn:
            for n in range(8):
                project = self.candidate(f"proj:filtered{n}")
                if n:
                    project_review.set_promotion_priority_on_project(project, lowered=True,
                        reason="Straße" if n % 2 == 0 else "テーマ")
                if n == 1:
                    project_review.set_promotion_priority_on_project(project, lowered=False)
                elif n == 3:
                    project.update(tracking_state="out_of_scope", service_status="unknown")
                elif n == 4:
                    project["introduced_url"] = "https://example.com/introduced"
                elif n == 5:
                    project.update(tracking_state="ignore", category="personal_tool")
                elif n == 6:
                    project.update(tracking_state="stalled", technical_interest_level="high",
                        technical_interest_reasons=["novel_design_pattern"],
                        technical_interest_summary="Protocol design")
                    project_review.defer_promotion_on_project(project, reason="release", days=30)
                elif n == 7:
                    project_review.defer_promotion_on_project(project)
                project_record.save_project(conn, project)
            revisions = list(conn.execute("SELECT * FROM project_revisions ORDER BY project_key"))
            all_items = self.assert_lowered_matches_full_scan(conn)
            self.assertEqual(all_items["total"], 6)
            self.assertEqual({item["project_key"] for item in all_items["items"]},
                             {f"proj:filtered{n}" for n in range(2, 8)})
            self.assertTrue(any(not item["signals"] for item in all_items["items"]))
            for reason in ("", "STRASSE", "release", "missing"):
                for kinds in (None, {"service_surface_confirmed"}, {"technical_insight_candidate"}):
                    for personal in (False, True):
                        for offset in (0, 1, 20):
                            with self.subTest(reason=reason, kinds=kinds, personal=personal, offset=offset):
                                result = self.assert_lowered_matches_full_scan(conn, reason=reason,
                                    kinds=kinds, include_personal_tools=personal, limit=1, offset=offset)
                                if reason == "STRASSE" and kinds is None:
                                    self.assertEqual(result["total"], 3)
                                    self.assertIn({"reason": "テーマ", "count": 3}, result["reasons"])
            self.assertEqual(list(conn.execute("SELECT * FROM project_revisions ORDER BY project_key")), revisions)

    def test_lowered_hydrates_only_projects_with_decision_history_once(self):
        with contextlib.closing(registry.open_db()) as conn:
            for n in range(32):
                project = self.candidate(f"proj:bounded{n}")
                if n < 2:
                    project_review.set_promotion_priority_on_project(project, lowered=True, reason="first")
                    project_review.set_promotion_priority_on_project(project, lowered=True, reason="edited")
                if n == 1:
                    project_review.set_promotion_priority_on_project(project, lowered=False)
                project_record.save_project(conn, project)
            with mock.patch.object(project_record, "_project_from_snapshot_row", wraps=project_record._project_from_snapshot_row) as fetch:
                result = presentation.queue_payload(conn, queue="promotion", tracking_states={"active"},
                                                    promotion_view="lowered", limit=1)
            self.assertEqual(result["total"], 1)
            self.assertCountEqual([call.args[1]["project_key"] for call in fetch.call_args_list],
                                  ["proj:bounded0", "proj:bounded1"])
            for states in (None, {"active"}, {"stalled"}, set()):
                actual = application_review.promotion_queue(conn, view="lowered", tracking_states=states)
                expected = application_review.build_showcase_queue_from_projects(
                    project_record.list_projects(conn, tracking_states=states), view="lowered",
                    command_prefix=application_review.registry_command_prefix(conn))
                self.assertEqual(actual, expected)
            with mock.patch.object(project_record, "list_projects_with_decision") as selector:
                for view in ("queue", "inbox", "all"):
                    application_review.promotion_queue(conn, view=view)
                selector.assert_not_called()

    def test_lowered_prefilter_observes_edits_clear_and_merge_without_cache(self):
        with contextlib.closing(registry.open_db()) as conn:
            for key in ("proj:mergeloweredsource", "proj:mergeloweredtarget"):
                project_record.save_project(conn, self.candidate(key))
            source, target = "proj:mergeloweredsource", "proj:mergeloweredtarget"
            self.assertEqual(self.assert_lowered_matches_full_scan(conn)["total"], 0)
            for key, reason in ((source, "old"), (source, "edited"), (target, "target")):
                decisions.set_promotion_priority(conn, project_key=key, lowered=True, reason=reason)
                self.assertEqual(self.assert_lowered_matches_full_scan(conn, reason=reason)["total"], 1)
            self.assertEqual(self.assert_lowered_matches_full_scan(conn, reason="old")["total"], 0)
            merge.merge_projects(conn, project_key=source, target_project_key=target, execute=True, force=True)
            result = self.assert_lowered_matches_full_scan(conn)
            self.assertEqual([item["project_key"] for item in result["items"]], [target])
            self.assertEqual(result["items"][0]["priority_reason"], "target")
            decisions.set_promotion_priority(conn, project_key=target, lowered=False)
            self.assertEqual(self.assert_lowered_matches_full_scan(conn)["total"], 0)

    def test_lowered_selection_and_hydration_share_a_read_snapshot(self):
        with contextlib.closing(registry.open_db()) as conn, contextlib.closing(registry.open_db()) as writer:
            key = "proj:snapshotlowered"
            project = self.candidate(key)
            project_review.set_promotion_priority_on_project(project, lowered=True, reason="before")
            project_record.save_project(conn, project)
            original = project_record._project_from_snapshot_row

            def clear_after_selection(reader, row):
                if reader is conn:
                    self.assertTrue(reader.in_transaction)
                    decisions.set_promotion_priority(writer, project_key=key, lowered=False)
                return original(reader, row)

            with mock.patch.object(project_record, "_project_from_snapshot_row", side_effect=clear_after_selection):
                selected = project_record.list_projects_with_decision(conn, "lower_promotion_priority")
            self.assertEqual(project_review.active_promotion_priority_adjustment(selected[0])["reason"], "before")
            self.assertFalse(conn.in_transaction)
            self.assertEqual(self.assert_lowered_matches_full_scan(conn)["total"], 0)

    def test_defer_reappears_on_change_preserves_legacy_and_history(self):
        with contextlib.closing(registry.open_db()) as conn:
            project = self.candidate("proj:deferred")
            project_record.save_project(conn, project)
            decisions.set_promotion_defer(conn, project_key=project["project_key"], reason="公開待ち", days=30)
            saved = project_record.fetch_project(conn, project["project_key"])
            self.assertIsNotNone(project_review.active_promotion_deferral(saved))
            saved["last_enriched_at"] = "2099-01-01"
            self.assertIsNotNone(project_review.active_promotion_deferral(saved))
            saved["service_status"] = "beta"
            project_record.save_project(conn, saved)
            saved = project_record.fetch_project(conn, project["project_key"])
            self.assertIsNone(project_review.active_promotion_deferral(saved))
            self.assertEqual(application_review.build_showcase_queue_from_projects([saved], view="inbox")[0]["attention"]["group"], "changed")
            self.assertEqual(len(saved["promotion_deferrals"]), 1)
            # Existing decisions with no fingerprint retain their time-only contract.
            saved["promotion_deferrals"][0].pop("fingerprint")
            self.assertIsNotNone(project_review.active_promotion_deferral(saved))
            decisions.set_promotion_defer(conn, project_key=project["project_key"], reason="再確認待ち", days=30)
            again = project_record.fetch_project(conn, project["project_key"])
            self.assertTrue(again["promotion_deferrals"][0]["cleared_at"])
            self.assertEqual(again["promotion_attention"]["outcome_counts"], {"defer": 2})

    def test_saved_state_survives_roundtrip_and_read_only_refresh(self):
        with contextlib.closing(registry.open_db()) as conn:
            project = self.candidate("proj:roundtrip")
            project_record.save_project(conn, project)
            decisions.set_promotion_defer(conn, project_key=project["project_key"])
            saved = project_record.fetch_project(conn, project["project_key"])
            attention = saved["promotion_attention"]
            revision = presentation.project_revision(conn, project["project_key"])
            for _ in range(3):
                payload = presentation.queue_payload(conn, queue="promotion", tracking_states={"active"})
                self.assertEqual(payload["total"], 0)
            self.assertEqual(presentation.project_revision(conn, project["project_key"]), revision)
            project_record.save_project(conn, saved)
            self.assertEqual(project_record.fetch_project(conn, project["project_key"])["promotion_attention"], attention)
            decisions.set_promotion_defer(conn, project_key=project["project_key"], clear=True)
            self.assertEqual(presentation.queue_payload(conn, queue="promotion", tracking_states={"active"})["total"], 1)

    def test_backlog_search_counts_and_pagination_include_candidates_without_signal(self):
        with contextlib.closing(registry.open_db()) as conn:
            for n in range(3):
                project = self.candidate(f"proj:backlog{n}")
                project_review.set_promotion_priority_on_project(project, lowered=True, reason="認証のみ" if n < 2 else "共有機能")
                # A feature backlog must remain recoverable after recommendation disappears.
                project.update(service_status="unknown", tracking_state="out_of_scope")
                project_record.save_project(conn, project)
            payload = presentation.queue_payload(conn, queue="promotion", tracking_states={"active"},
                promotion_view="lowered", reason="認証", limit=1)
            self.assertEqual((payload["total"], payload["count"], payload["has_more"]), (2, 1, True))
            self.assertIn({"reason": "認証のみ", "count": 2}, payload["reasons"])
            next_page = presentation.queue_payload(conn, queue="promotion", tracking_states={"active"},
                promotion_view="lowered", reason="認証", limit=1, offset=1)
            self.assertNotEqual(payload["items"][0]["project_key"], next_page["items"][0]["project_key"])
            self.assertFalse(next_page["has_more"])

    def test_legacy_review_and_defer_read_conversion_and_roundtrip(self):
        with contextlib.closing(registry.open_db()) as conn:
            cases = [(False, False, None), (False, False, 7), (False, False, 40),
                     (True, False, None), (True, False, 7), (False, True, None)]
            for index, (expired, cleared, days) in enumerate(cases):
                with self.subTest(expired=expired, cleared=cleared, days=days):
                    key = f"proj:legacy{index}"
                    old = self.legacy_project(conn, key, expired=expired, cleared=cleared, defer_days=days)
                    raw_before = list(conn.execute("SELECT state_json FROM projects WHERE project_key=?", (key,)))
                    rows_before = list(conn.execute("SELECT * FROM project_decisions WHERE project_key=?", (key,)))
                    revision = presentation.project_revision(conn, key)
                    restored = project_record.fetch_project(conn, key)
                    holds = restored["promotion_deferrals"]
                    self.assertEqual(len(holds), int(days is not None) + int(not cleared))
                    if not cleared:
                        self.assertEqual(holds[-1]["expires_at"], old["reviewed_until"])
                        self.assertEqual(holds[-1]["fingerprint"], old["reviewed_fingerprint"])
                        self.assertEqual(holds[-1]["legacy_review"], old)
                    self.assertNotIn("reviewed_until", restored["promotion_attention"])
                    self.assertEqual(restored["promotion_attention"]["outcome_counts"], old["outcome_counts"])
                    detail = presentation.project_detail(conn, project_key=key)
                    history = [x for x in detail["decisions"]["history"] if x["kind"] == "defer_promotion"]
                    self.assertEqual(len(history), len(holds))
                    self.assertEqual(project_review.latest_promotion_deferral_reason(restored), "release" if days else "")
                    active = project_review.active_promotion_deferral(restored)
                    self.assertEqual(bool(active), days is not None or (not expired and not cleared))
                    if active:
                        deadlines = [project_record.parse_iso(x["expires_at"]) for x in holds]
                        self.assertEqual(project_record.parse_iso(active["expires_at"]), max(deadlines))
                    self.assertEqual(presentation.project_revision(conn, key), revision)
                    self.assertEqual(list(conn.execute("SELECT state_json FROM projects WHERE project_key=?", (key,))), raw_before)
                    self.assertEqual(list(conn.execute("SELECT * FROM project_decisions WHERE project_key=?", (key,))), rows_before)
                    for _ in range(2):
                        project_record.save_project(conn, restored, preserve_timestamps=True)
                        restored = project_record.fetch_project(conn, key)
                        self.assertEqual(restored["promotion_deferrals"], holds)

    def test_coexisting_legacy_holds_clear_and_lowering_are_independent(self):
        with contextlib.closing(registry.open_db()) as conn:
            key = "proj:coexisting"
            old = self.legacy_project(conn, key, defer_days=40, legacy_defer=True)
            decisions.set_promotion_priority(conn, project_key=key, lowered=True, reason="特集")
            decisions.set_promotion_priority(conn, project_key=key, lowered=False)
            saved = project_record.fetch_project(conn, key)
            self.assertEqual(len(saved["promotion_deferrals"]), 2)
            saved["features"] = ["changed"]
            # A legacy defer without a fingerprint still lasts until its deadline.
            self.assertIsNotNone(project_review.active_promotion_deferral(saved))
            decisions.set_promotion_defer(conn, project_key=key, clear=True)
            saved = project_record.fetch_project(conn, key)
            self.assertIsNone(project_review.active_promotion_deferral(saved))
            self.assertTrue(all(x.get("cleared_at") for x in saved["promotion_deferrals"]))
            self.assertEqual(saved["promotion_deferrals"][-1]["legacy_review"], old)
            decisions.set_promotion_defer(conn, project_key=key)
            saved = project_record.fetch_project(conn, key)
            self.assertEqual(len(saved["promotion_deferrals"]), 3)
            self.assertEqual(saved["promotion_deferrals"][-1]["reason"], "")

    def test_merge_retains_both_legacy_review_records(self):
        with contextlib.closing(registry.open_db()) as conn:
            old = [self.legacy_project(conn, key) for key in ("proj:holdsource", "proj:holdtarget")]
            merge.merge_projects(conn, project_key="proj:holdsource", target_project_key="proj:holdtarget", execute=True, force=True)
            saved = project_record.fetch_project(conn, "proj:holdtarget")
            self.assertCountEqual([x["legacy_review"] for x in saved["promotion_deferrals"]], old)

    def test_held_search_counts_and_pages_and_no_reason_cli(self):
        from project_registry_core.cli import build_parser
        import io
        with contextlib.closing(registry.open_db()) as conn:
            for n in range(3):
                key = f"proj:held{n}"
                project_record.save_project(conn, self.candidate(key))
                args = build_parser().parse_args(["defer-promotion", "-p", key])
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(args.func(args), 0)
            options = dict(queue="promotion", tracking_states={"active"}, promotion_view="waiting", limit=1)
            first = presentation.queue_payload(conn, **options)
            second = presentation.queue_payload(conn, offset=1, **options)
            self.assertEqual((first["total"], first["count"], first["has_more"]), (3, 1, True))
            self.assertNotEqual(first["items"][0]["project_key"], second["items"][0]["project_key"])
            decisions.set_promotion_defer(conn, project_key="proj:held0", reason="Release")
            filtered = presentation.queue_payload(conn, reason="release", **options)
            self.assertEqual(filtered["total"], 1)
            self.assertEqual(filtered["reasons"], [{"reason": "Release", "count": 1}])
            self.assertEqual(presentation.queue_payload(conn, queue="promotion", tracking_states={"active"})["total"], 0)
