from __future__ import annotations

import sqlite3

from helpers import TempRegistryDbTestCase
import promotion_outcome_analysis
from project_registry_core import project_record


class PromotionOutcomeAnalysisTest(TempRegistryDbTestCase):
    def introduced_project(
        self,
        project_key: str,
        *,
        category: str = "project",
        service_status: str = "live",
        website_url: str = "https://example.com",
    ) -> dict:
        project = self.make_project(project_key)
        project["introduced_url"] = "https://bsky.app/profile/example.test/post/3example"
        project["category"] = category
        project["service_status"] = service_status
        project["website_url"] = website_url
        project["service_status_confidence"] = "high"
        project["service_status_evidence"] = ["login_surface_detected"]
        project["technical_interest_level"] = "notable"
        project["technical_interest_reasons"] = ["implementation_reference"]
        project["technical_interest_summary"] = "Reference implementation"
        return project_record.ensure_project_shape(project)

    def test_analysis_uses_unintroduced_counterfactual_without_mutating_input(self) -> None:
        service = self.introduced_project("proj:111111111111")
        missing_url = self.introduced_project("proj:222222222222", website_url="")
        library = self.introduced_project(
            "proj:333333333333",
            category="library",
            website_url="",
        )

        payload = promotion_outcome_analysis.analyze_projects([service, missing_url, library])

        self.assertTrue(service["introduced_url"])
        self.assertEqual(payload["summary"]["introduced_count"], 3)
        self.assertEqual(payload["summary"]["introduction_url_count"], 1)
        self.assertEqual(payload["summary"]["introduction_group_size_counts"], {"3": 1})
        self.assertEqual(
            payload["summary"]["category_signal_coverage"],
            {
                "library": {
                    "introduced": 1,
                    "with_signal": 0,
                    "without_signal": 1,
                    "coverage_ratio": 0.0,
                },
                "project": {
                    "introduced": 2,
                    "with_signal": 1,
                    "without_signal": 1,
                    "coverage_ratio": 0.5,
                },
            },
        )
        self.assertEqual(payload["summary"]["introduced_with_signal_count"], 1)
        self.assertEqual(payload["summary"]["introduced_without_signal_count"], 2)
        self.assertEqual(
            payload["summary"]["no_signal_primary_reason_counts"],
            {
                "website_or_confirmed_service_url_missing": 1,
                "technical_interest_below_candidate_threshold": 1,
            },
        )
        details = {item["project_key"]: item for item in payload["projects"]}
        self.assertEqual(
            details["proj:222222222222"]["routes"]["service"]["blockers"],
            ["website_or_confirmed_service_url_missing"],
        )
        self.assertEqual(
            details["proj:333333333333"]["primary_no_signal_reason"],
            "technical_interest_below_candidate_threshold",
        )
        self.assertEqual(details["proj:333333333333"]["introduction_group_project_count"], 3)

    def test_personal_tool_mode_matches_queue_option(self) -> None:
        personal_tool = self.introduced_project(
            "proj:444444444444",
            category="personal_tool",
        )

        default_payload = promotion_outcome_analysis.analyze_projects([personal_tool])
        included_payload = promotion_outcome_analysis.analyze_projects(
            [personal_tool],
            include_personal_tools=True,
        )

        self.assertEqual(default_payload["summary"]["introduced_without_signal_count"], 1)
        self.assertEqual(
            default_payload["summary"]["no_signal_primary_reason_counts"],
            {"personal_tool_disabled": 1},
        )
        self.assertEqual(included_payload["summary"]["introduced_with_signal_count"], 1)
        self.assertEqual(
            included_payload["summary"]["signal_counts"],
            {"service_surface_confirmed": 1},
        )

        personal_tool["service_status"] = "none"
        no_service_payload = promotion_outcome_analysis.analyze_projects(
            [personal_tool],
            include_personal_tools=True,
        )
        self.assertEqual(
            no_service_payload["summary"]["no_signal_primary_reason_counts"],
            {"service_status_not_candidate": 1},
        )

    def test_infra_tool_uses_service_route(self) -> None:
        infra_tool = self.introduced_project(
            "proj:666666666666",
            category="infra_tool",
        )

        payload = promotion_outcome_analysis.analyze_projects([infra_tool])

        self.assertEqual(payload["summary"]["introduced_with_signal_count"], 1)
        self.assertEqual(
            payload["summary"]["signal_counts"],
            {"service_surface_confirmed": 1},
        )

    def test_database_analysis_opens_existing_schema_read_only(self) -> None:
        project = self.introduced_project("proj:555555555555")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            project_record.save_project(conn, project)
            conn.commit()
        finally:
            conn.close()
        payload = promotion_outcome_analysis.analyze_database(self.db_path, details="none")

        self.assertEqual(payload["summary"]["introduced_count"], 1)
        self.assertEqual(payload["detail_count"], 0)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT introduced_url FROM projects WHERE project_key = ?",
                    ("proj:555555555555",),
                ).fetchone()[0],
                project["introduced_url"],
            )
        finally:
            conn.close()
