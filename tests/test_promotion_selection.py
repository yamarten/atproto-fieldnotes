import contextlib
import copy
import io
import json
from unittest import mock

from helpers import TempRegistryDbTestCase, registry
from promotion_selection import cases, apply_fixed_assessment
from project_registry_core import atproto_usage, project_record, promotion_attention
from project_registry_core.application import decisions, presentation, merge
from project_registry_core.cli import build_parser


class PromotionSelectionTest(TempRegistryDbTestCase):
    def test_positive_references_and_counterexamples_through_normal_queue(self):
        projects = [apply_fixed_assessment(c) for c in cases()]
        queue = registry.build_showcase_queue_from_projects(projects, limit=None)
        ranks = {p["project_key"]: i for i, p in enumerate(queue)}
        for good in ("bluesky", "rolld", "meyasuat", "standard-site", "idiolect"):
            for limited in ("squire", "skysweeper"):
                self.assertLess(ranks["fixture:" + good], ranks["fixture:" + limited])
        self.assertLess(ranks["fixture:schema-only"], ranks["fixture:squire"])
        self.assertEqual(len(queue), 8)  # Unknown and limited use remain selectable.
        technical = registry.build_showcase_queue_from_projects(projects, view="technical", limit=None)
        self.assertEqual({p["project_key"] for p in technical},
                         {"fixture:bluesky", "fixture:standard-site", "fixture:idiolect"})
        # Neither changing the name nor having a usable service drives technical ordering.
        modified = copy.deepcopy(projects)
        for p in modified:
            p.update(name="Renamed example", service_status="live", service_status_confidence="high")
        self.assertEqual([p["project_key"] for p in technical], [p["project_key"] for p in
            registry.build_showcase_queue_from_projects(modified, view="technical", limit=None)])

    def test_roles_need_context_and_current_sources_not_nsid_or_prior_prose(self):
        case = list(cases())[-1]
        p = case["project"]
        p["summary"] = "Main function definitely uses AT Protocol"
        for role in ("main_data", "supporting", "bluesky_client"):
            atproto_usage.apply_assessment(p, {"role": role, "reason": "schema exists",
                "sources": [s["id"] for s in case["evidence"]["description_sources"]]},
                case["evidence"]["description_sources"], assessed_at="")
            self.assertEqual(atproto_usage.projection(p)["role"], "unknown")
        case = next(c for c in cases() if c["ref"] == "squire")
        p = apply_fixed_assessment(case)
        self.assertEqual(atproto_usage.projection(p)["role"], "supporting")
        p["service_verification_results"][0]["html_extract"] = "New architecture"
        self.assertTrue(atproto_usage.projection(p)["stale"])
        atproto_usage.apply_assessment(p, {"role": "main_data", "reason": "bad ref", "sources": ["invented"]},
                                       case["evidence"]["description_sources"], assessed_at="")
        self.assertEqual(atproto_usage.projection(p)["role"], "unknown")

    def test_normal_save_override_reassessment_clear_and_rediscovery(self):
        case = next(c for c in cases() if c["ref"] == "rolld")
        p = apply_fixed_assessment(case)
        with contextlib.closing(registry.open_db()) as conn:
            project_record.save_project(conn, p)
            key = p["project_key"]
            restored = project_record.fetch_project(conn, key)
            self.assertEqual(atproto_usage.projection(restored)["role"], "main_data")
            decisions.set_promotion_reviewed(conn, project_key=key)
            decisions.set_atproto_usage(conn, project_key=key, role="supporting", reason="Main data is elsewhere")
            restored = project_record.fetch_project(conn, key)
            self.assertEqual(promotion_attention.projection(restored)["group"], "changed")
            registry.apply_llm_output(restored, {}, {}, {})
            self.assertEqual(atproto_usage.projection(restored)["role"], "supporting")
            project_record.save_project(conn, restored)
            decisions.set_atproto_usage(conn, project_key=key, clear=True)
            self.assertEqual(atproto_usage.projection(project_record.fetch_project(conn, key))["role"], "unknown")
            row = conn.execute("SELECT * FROM project_decisions WHERE project_key=? AND kind='assess_atproto_usage'", (key,)).fetchone()
            self.assertTrue(row["cleared_at"])
            state = json.loads(conn.execute("SELECT state_json FROM projects WHERE project_key=?", (key,)).fetchone()[0])
            self.assertNotIn("atproto_usage_decisions", state.get("ops", {}))
            self.assertNotIn("override", state.get("ops", {}).get("atproto_usage", {}))

    def test_technical_pagination_and_saved_dispositions(self):
        with contextlib.closing(registry.open_db()) as conn:
            for case in cases():
                p = apply_fixed_assessment(case)
                project_record.save_project(conn, p)
            query = dict(queue="promotion", tracking_states={"active"}, promotion_view="technical", limit=1)
            first = presentation.queue_payload(conn, **query)
            second = presentation.queue_payload(conn, **query, offset=1)
            self.assertEqual(first["total"], 3)
            self.assertNotEqual(first["items"][0]["project_key"], second["items"][0]["project_key"])
            decisions.set_promotion_priority(conn, project_key="fixture:idiolect", lowered=True, reason="暗号")
            decisions.set_promotion_defer(conn, project_key="fixture:standard-site", reason="材料待ち")
            decisions.set_promotion_reviewed(conn, project_key="fixture:bluesky")
            self.assertEqual(presentation.queue_payload(conn, **query)["total"], 0)
            lowered = presentation.queue_payload(conn, queue="promotion", tracking_states={"active"}, promotion_view="lowered", reason="暗号")
            self.assertEqual(lowered["items"][0]["project_key"], "fixture:idiolect")
            decisions.set_promotion_priority(conn, project_key="fixture:idiolect", lowered=False)
            self.assertEqual(presentation.queue_payload(conn, **query)["total"], 1)

    def test_bluesky_tools_can_have_independent_technical_interest(self):
        case = next(c for c in cases() if c["ref"] == "skysweeper")
        p = apply_fixed_assessment(case)
        p.update(technical_interest_level="high", technical_interest_summary="A concrete reusable protocol mechanism",
                 summary="Bluesky profile viewer analytics dashboard", inspired_by=["Bluesky"])
        self.assertEqual(len(registry.build_showcase_queue_from_projects([p], view="technical")), 1)
        self.assertEqual(atproto_usage.projection(p)["role"], "bluesky_client")

    def test_malformed_assessments_fall_back_to_unknown(self):
        case = next(c for c in cases() if c["ref"] == "squire")
        for value in (None, [], {"role": []}, {"role": {}, "reason": "bad"},
                      {"role": "main_data", "reason": "bad", "sources": {}}):
            p = apply_fixed_assessment(copy.deepcopy(case))
            atproto_usage.apply_assessment(p, value, case["evidence"]["description_sources"], assessed_at="")
            self.assertEqual(atproto_usage.projection(p)["role"], "unknown")
        self.assertEqual(atproto_usage.normalize({"automatic": {"role": "main_data", "reason": "bad", "sources": None}}), {})

    def test_merge_conflicting_operator_roles_requires_resolution(self):
        left, right = {}, {}
        atproto_usage.set_override(left, role="main_data", reason="a", assessed_at="")
        atproto_usage.set_override(right, role="supporting", reason="b", assessed_at="")
        with self.assertRaisesRegex(ValueError, "conflicting"):
            atproto_usage.merge_decisions(left["atproto_usage_decisions"], right["atproto_usage_decisions"], cleared_at="now")
        self.assertEqual(atproto_usage.active_override(atproto_usage.merge_decisions([], left["atproto_usage_decisions"], cleared_at="now"))["role"], "main_data")

    def test_merge_preserves_override_in_normal_save_path(self):
        with contextlib.closing(registry.open_db()) as conn:
            for case in cases():
                if case["ref"] in {"rolld", "squire"}:
                    project_record.save_project(conn, apply_fixed_assessment(case))
            decisions.set_atproto_usage(conn, project_key="fixture:squire", role="supporting", reason="outside backend")
            decisions.set_atproto_usage(conn, project_key="fixture:rolld", role="main_data", reason="PDS records")
            with self.assertRaisesRegex(ValueError, "conflicting"):
                merge.merge_projects(conn, project_key="fixture:squire", target_project_key="fixture:rolld", execute=True)
            self.assertIsNotNone(project_record.fetch_project(conn, "fixture:squire"))
            decisions.set_atproto_usage(conn, project_key="fixture:rolld", clear=True)
            merge.merge_projects(conn, project_key="fixture:squire", target_project_key="fixture:rolld", execute=True)
            restored = project_record.fetch_project(conn, "fixture:rolld")
            self.assertEqual(atproto_usage.projection(restored)["role"], "supporting")
            self.assertNotIn("automatic", restored["atproto_usage"])

    def test_cli_uses_same_technical_queue_and_operator_mutation(self):
        with contextlib.closing(registry.open_db()) as conn:
            p = apply_fixed_assessment(next(c for c in cases() if c["ref"] == "idiolect"))
            project_record.save_project(conn, p)
        parser = build_parser()
        for argv in (["set-atproto-usage", "-p", p["project_key"], "--role", "unknown", "--reason", "Review pending"],
                     ["promotion-queue", "--view", "technical"]):
            args = parser.parse_args(argv)
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(args.func(args), 0)
            self.assertIn(p["project_key"], stream.getvalue())
