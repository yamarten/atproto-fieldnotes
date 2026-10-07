from __future__ import annotations

import datetime as dt
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import ProjectRecordTestCase, TempRegistryDbTestCase, captured_stdout, registry

from project_registry_core import project_review
from project_registry_core.application import review as application_review

from project_registry_core import project_merge
from project_registry_core import cli
from project_registry_core.persistence import database
from project_registry_core.identity import domain_policy
from project_registry_core import relations, source_sync
from project_registry_core.commands import review as review_commands
from project_registry_core.commands import queue_interaction
from project_registry_core.application import maintenance as maintenance_services
from project_registry_core.application import merge as merge_services
from project_registry_core.application import sync as sync_services
from project_registry_core import project_record


class ProjectRegistryRecordPolicyTest(ProjectRecordTestCase):
    def test_split_project_parser_has_no_explicit_new_key_option(self) -> None:
        args = cli.build_parser().parse_args([
            "split-project",
            "--project-key",
            "proj:source",
            "--identifier-type",
            "nsid",
            "--value",
            "app.example.post",
        ])

        self.assertFalse(hasattr(args, "new_project_key"))

        domain_args = cli.build_parser().parse_args([
            "split-project",
            "--project-key",
            "proj:source",
            "--identifier-type",
            "domain",
            "--value",
            "app.example.com",
        ])
        self.assertEqual(domain_args.identifier_type, "domain")

    def test_explain_parser_accepts_topic_and_format(self) -> None:
        args = cli.build_parser().parse_args([
            "explain", "--project-key", "proj:abc", "--topic", "ownership", "--format", "json"
        ])
        self.assertEqual(args.topic, "ownership")
        self.assertEqual(args.format, "json")

    def test_sync_parsers_create_unmatched_by_default_with_opt_out(self) -> None:
        parser = cli.build_parser()

        for command in ("sync-collections", "sync-github-search", "sync-semble-collections"):
            self.assertTrue(parser.parse_args([command]).create_unmatched)
            self.assertFalse(parser.parse_args([command, "--no-create-unmatched"]).create_unmatched)

    def test_sync_parsers_are_incremental_by_default(self) -> None:
        parser = cli.build_parser()

        for command in ("sync-introduced", "sync-collections", "sync-github-search", "sync-semble-collections"):
            self.assertTrue(parser.parse_args([command]).incremental)
            self.assertFalse(parser.parse_args([command, "--no-incremental"]).incremental)

        introduced = parser.parse_args(["sync-introduced"])
        self.assertIsNone(introduced.query)
        self.assertIsNone(introduced.lookback_days)
        self.assertIsNone(introduced.limit)
        self.assertIsNone(parser.parse_args(["sync-semble-collections"]).pages)

        config = registry.load_config()
        self.assertEqual(config["introduced_search"]["lookback_days"], 7)
        self.assertEqual(config["introduced_search"]["limit"], 10)
        self.assertEqual(config["semble_collections"]["pages"], 100)
        self.assertEqual(
            config["semble_collections"]["tracked_collections"],
            list(registry.SEMBLE_TRACKED_COLLECTIONS),
        )

    def test_builtin_lexicon_policy_does_not_come_from_source_config(self) -> None:
        with mock.patch.object(registry, "load_config", return_value={"collection_inventory": {"builtin_prefixes": ["evil."]}}):
            self.assertEqual(project_record.builtin_lexicon_prefixes(), ("app.bsky.", "chat.bsky.", "com.atproto."))

    def test_parser_accepts_defer_identifier_command(self) -> None:
        args = cli.build_parser().parse_args(
            [
                "defer-identifier",
                "--project-key",
                "proj:121212121212",
                "--identifier-type",
                "service_url",
                "--value",
                "https://example.com",
                "--days",
                "14",
            ]
        )

        self.assertEqual(args.func.__module__, "project_registry_core.commands.decisions")
        self.assertEqual(args.days, 14)

    def test_parser_accepts_defer_promotion_command(self) -> None:
        args = cli.build_parser().parse_args(
            [
                "defer-promotion",
                "--project-key",
                "proj:121212121212",
                "--reason",
                "中核機能が未実装",
            ]
        )

        self.assertEqual(args.func.__module__, "project_registry_core.commands.decisions")
        self.assertIsNone(args.days)

    def test_parser_accepts_audit_url_fields_command(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["audit-url-fields", "--example-limit", "3"])

        self.assertEqual(args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertEqual(args.example_limit, 3)

        storage_cleanup_args = parser.parse_args([
            "cleanup-storage-payloads",
            "--execute",
            "--example-limit",
            "3",
            "--vacuum",
        ])

        self.assertEqual(
            storage_cleanup_args.func.__module__,
            "project_registry_core.commands.maintenance",
        )
        self.assertTrue(storage_cleanup_args.execute)
        self.assertEqual(storage_cleanup_args.example_limit, 3)
        self.assertTrue(storage_cleanup_args.vacuum)

        workspace_temp_args = parser.parse_args([
            "cleanup-workspace-temp",
            "--older-than-hours",
            "6",
            "--example-limit",
            "3",
            "--execute",
        ])

        self.assertEqual(
            workspace_temp_args.func.__module__,
            "project_registry_core.commands.maintenance",
        )
        self.assertEqual(workspace_temp_args.older_than_hours, 6)
        self.assertEqual(workspace_temp_args.example_limit, 3)
        self.assertTrue(workspace_temp_args.execute)

        compression_args = parser.parse_args([
            "compress-investigation-history",
            "--execute",
            "--example-limit",
            "3",
            "--vacuum",
        ])

        self.assertEqual(
            compression_args.func.__module__,
            "project_registry_core.commands.maintenance",
        )
        self.assertTrue(compression_args.execute)
        self.assertEqual(compression_args.example_limit, 3)
        self.assertTrue(compression_args.vacuum)

        repair_args = parser.parse_args(["repair-db", "--execute"])

        self.assertEqual(repair_args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertTrue(repair_args.execute)

        prune_args = parser.parse_args([
            "prune-investigation-history",
            "--retention-days",
            "30",
            "--keep-latest-per-project",
            "2",
            "--execute",
            "--vacuum",
        ])

        self.assertEqual(prune_args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertEqual(prune_args.retention_days, 30)
        self.assertEqual(prune_args.keep_latest_per_project, 2)
        self.assertTrue(prune_args.execute)
        self.assertTrue(prune_args.vacuum)

    def test_audit_source_config_parser_is_interactive_by_default(self) -> None:
        parser = cli.build_parser()

        args = parser.parse_args(["audit-source-config", "example.com"])
        dry_run_args = parser.parse_args(["audit-source-config", "example.com", "--no-interactive"])

        self.assertEqual(args.func.__module__, "project_registry_core.commands.maintenance")
        self.assertTrue(args.interactive)
        self.assertFalse(dry_run_args.interactive)

    def test_out_of_scope_tracking_state_is_available_to_cli(self) -> None:
        parser = cli.build_parser()

        state_args = parser.parse_args(
            ["set-tracking-state", "--project-key", "proj:121212121212", "--tracking-state", "out_of_scope"]
        )
        review_args = parser.parse_args(["review-out-of-scope"])
        enrich_args = parser.parse_args(["enrich", "--all", "--include-out-of-scope"])

        self.assertEqual(state_args.tracking_state, "out_of_scope")
        self.assertIs(review_args.func, review_commands.cmd_review_out_of_scope)
        self.assertTrue(enrich_args.include_out_of_scope)

    def test_review_stalled_parser_accepts_freshness_option(self) -> None:
        parser = cli.build_parser()

        args = parser.parse_args(["review-stalled", "--max-enrich-age-days", "21"])

        self.assertEqual(args.max_enrich_age_days, 21)

    def test_promotion_queue_parser_accepts_supported_kinds(self) -> None:
        args = cli.build_parser().parse_args(
            [
                "promotion-queue",
                "--kinds",
                "technical_insight_candidate",
                "service_candidate_needs_validation",
            ]
        )

        self.assertEqual(
            args.kinds,
            ["technical_insight_candidate", "service_candidate_needs_validation"],
        )

    def test_merge_pair_suggests_unconfigured_multi_tenant_suffix(self) -> None:
        source = self.make_project("proj:foo", "service_url", "https://foo.example.com")
        registry.add_manual_seed(source, "service_url", "https://foo.example.com", source="test")
        target = self.make_project("proj:bar", "service_url", "https://bar.example.com")
        registry.add_manual_seed(target, "service_url", "https://bar.example.com", source="test")

        suggestions = project_merge.merge_pair_multi_tenant_suffix_suggestions(source, target)

        self.assertEqual([item["suffix"] for item in suggestions], ["example.com"])
        self.assertEqual(suggestions[0]["source_tenants"], ["foo.example.com"])
        self.assertEqual(suggestions[0]["target_tenants"], ["bar.example.com"])

    def test_defer_service_url_downgrades_to_candidate_and_blocks_confirmation(self) -> None:
        project = self.make_project("proj:121212121212", "service_url", "https://example.com")
        project["website_url"] = "https://example.com"
        project["service_urls"] = ["https://example.com"]

        registry.defer_identifier_on_project(project, "service_url", "https://example.com", days=30)
        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://example.com", project["service_urls"])
        self.assertEqual(project["website_url"], "https://example.com")
        self.assertIn("https://example.com", project["service_url_candidates"])
        registry.confirm_service_seed(project, "https://example.com", source="test")
        self.assertNotIn("https://example.com", project["service_urls"])

    def test_defer_repo_url_downgrades_to_candidate(self) -> None:
        project = self.make_project("proj:343434343434", "repo_url", "https://github.com/example/app")
        project["project_repository_urls"] = ["https://github.com/example/app"]

        registry.defer_identifier_on_project(project, "repo_url", "https://github.com/example/app", days=14)
        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://github.com/example/app", project["project_repository_urls"])
        self.assertIn("https://github.com/example/app", project["candidate_project_repository_urls"])
        self.assertEqual(project["deferred_identifiers"][0]["type"], "repo_url")

    def test_manual_repo_seed_stays_confirmed_project_repository(self) -> None:
        project = self.make_project("proj:manualrepo", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
        registry.add_confirmed_identifier_decision(project, "repo_url", "https://github.com/example/app", source="test")

        project = registry.finalize_project_shape(project)

        self.assertIn("https://github.com/example/app", project["project_repository_urls"])
        self.assertNotIn("https://github.com/example/app", project["candidate_project_repository_urls"])
        self.assertIn(
            {"kind": "repo_url", "value": "https://github.com/example/app"},
            registry.trusted_anchor_nodes(project),
        )

    def test_deferred_manual_repo_seed_is_not_trusted_anchor(self) -> None:
        project = self.make_project("proj:deferredrepo", "repo_url", "https://github.com/example/app")
        registry.add_manual_seed(project, "repo_url", "https://github.com/example/app", source="test")
        registry.defer_identifier_on_project(project, "repo_url", "https://github.com/example/app", days=14)

        project = registry.finalize_project_shape(project)

        self.assertNotIn("https://github.com/example/app", project["project_repository_urls"])
        self.assertIn("https://github.com/example/app", project["candidate_project_repository_urls"])
        self.assertNotIn(
            {"kind": "repo_url", "value": "https://github.com/example/app"},
            registry.trusted_anchor_nodes(project),
        )

    def test_defer_nsid_keeps_value_in_temporary_category(self) -> None:
        project = self.make_project("proj:565656565656", "nsid", "app.example.post")
        project["input_nsids"] = ["app.example.post"]

        registry.defer_identifier_on_project(project, "nsid", "app.example.post", days=30)
        project = registry.finalize_project_shape(project)

        self.assertNotIn("app.example.post", project["input_nsids"])
        self.assertEqual(project["deferred_identifiers"][0]["value"], "app.example.post")

    def test_expired_deferred_identifier_stops_blocking_confirmation(self) -> None:
        project = self.make_project("proj:defdefdefdef", "service_url", "https://example.com")
        project["deferred_identifiers"] = [
            {
                "type": "service_url",
                "value": "https://example.com",
                "added_at": "2026-01-01T00:00:00+00:00",
                "expires_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat(),
            }
        ]

        project = project_record.ensure_project_shape(project)
        registry.confirm_service_seed(project, "https://example.com", source="test")

        self.assertEqual(project["deferred_identifiers"], [])
        self.assertIn("https://example.com", project["service_urls"])

    def test_promotion_deferral_hides_queue_item_until_expiry(self) -> None:
        project = self.make_project("proj:promotionhold", "service_url", "https://example.com")
        project.update({
            "category": "project",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "login_surface_detected"],
        })
        registry.confirm_service_seed(project, "https://example.com", source="test")

        project_review.defer_promotion_on_project(project, reason="中核機能が未実装")

        self.assertEqual(project_review.compute_showcase_signals(project), [])
        project["promotion_deferrals"][0]["expires_at"] = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
        ).isoformat()
        self.assertEqual(
            [signal["kind"] for signal in project_review.compute_showcase_signals(project)],
            ["service_surface_confirmed"],
        )

    def test_out_of_scope_project_is_not_promoted(self) -> None:
        project = self.make_project("proj:outofscope", "service_url", "https://example.com")
        project.update({
            "category": "project",
            "service_status": "live",
            "service_status_confidence": "medium",
            "tracking_state": "out_of_scope",
        })
        registry.confirm_service_seed(project, "https://example.com", source="test")

        self.assertEqual(project_review.compute_showcase_signals(project), [])

    def test_dns_verified_lexicon_publisher_is_high_priority_promotion_candidate(self) -> None:
        project = self.make_project("proj:lexiconpublisher", "nsid", "app.example.post")
        project.update({
            "category": "project",
            "resolved_nsids": ["app.example.post"],
            "lexicon_schema_samples": [{"nsid": "app.example.post", "id": "app.example.post"}],
            "lexicon_resolution_entries": [{
                "input_nsid": "app.example.post",
                "resolved_nsid": "app.example.post",
                "repo_did": "did:plc:publisher",
                "authority_domain": "example.app",
                "pds_url": "https://pds.example",
                "record_uri": "at://did:plc:publisher/com.atproto.lexicon.schema/app.example.post",
                "resolved_at": "2026-08-28T00:00:00+00:00",
            }],
        })

        signals = project_review.compute_showcase_signals(project)

        self.assertEqual([signal["kind"] for signal in signals], ["lexicon_publisher_confirmed"])
        self.assertEqual(signals[0]["priority"], 7)
        self.assertEqual(signals[0]["details"]["publication_count"], 1)
        self.assertEqual(
            project_review.promotion_queue_score_breakdown(project, signals)["components"]["lexicon_publication"],
            160,
        )

    def test_lexicon_schema_without_dns_resolution_is_not_promotion_signal(self) -> None:
        project = self.make_project("proj:reposchema", "nsid", "app.example.post")
        project.update({
            "category": "project",
            "resolved_nsids": ["app.example.post"],
            "lexicon_schema_samples": [{"nsid": "app.example.post", "id": "app.example.post"}],
            "lexicon_resolution_entries": [],
        })

        self.assertEqual(project_review.compute_showcase_signals(project), [])

    def test_dns_resolved_candidate_nsid_is_not_confirmed_publisher_signal(self) -> None:
        project = self.make_project("proj:candidatelexicon", "nsid", "app.example.post")
        project.update({
            "category": "project",
            "resolved_nsids": [],
            "input_nsids": ["app.example.post"],
            "lexicon_schema_samples": [{"nsid": "app.example.post", "id": "app.example.post"}],
            "lexicon_resolution_entries": [{
                "input_nsid": "app.example.post",
                "resolved_nsid": "app.example.post",
                "repo_did": "did:plc:publisher",
                "authority_domain": "example.app",
                "record_uri": "at://did:plc:publisher/com.atproto.lexicon.schema/app.example.post",
            }],
        })

        self.assertEqual(project_review.compute_showcase_signals(project), [])

    def test_lexicon_publication_requires_matching_authority_and_record_uri(self) -> None:
        base = self.make_project("proj:mismatchedlexicon", "nsid", "app.example.post")
        base.update({
            "category": "project",
            "resolved_nsids": ["app.example.post"],
            "lexicon_schema_samples": [{"nsid": "app.example.post", "id": "app.example.post"}],
        })
        entry = {
            "input_nsid": "app.example.post",
            "resolved_nsid": "app.example.post",
            "repo_did": "did:plc:publisher",
            "authority_domain": "example.app",
            "record_uri": "at://did:plc:publisher/com.atproto.lexicon.schema/app.example.post",
        }

        for field, invalid_value in (
            ("authority_domain", "unrelated.example"),
            ("record_uri", "at://did:plc:other/com.atproto.lexicon.schema/app.example.post"),
        ):
            with self.subTest(field=field):
                project = dict(base)
                project["lexicon_resolution_entries"] = [{**entry, field: invalid_value}]
                self.assertEqual(project_review.compute_showcase_signals(project), [])

    def test_personal_tool_lexicon_publisher_requires_include_flag(self) -> None:
        project = self.make_project("proj:personallexicon", "nsid", "app.example.post")
        project.update({
            "category": "personal_tool",
            "resolved_nsids": ["app.example.post"],
            "lexicon_schema_samples": [{"nsid": "app.example.post", "id": "app.example.post"}],
            "lexicon_resolution_entries": [{
                "input_nsid": "app.example.post",
                "resolved_nsid": "app.example.post",
                "repo_did": "did:plc:publisher",
                "authority_domain": "example.app",
                "record_uri": "at://did:plc:publisher/com.atproto.lexicon.schema/app.example.post",
            }],
        })

        self.assertEqual(project_review.compute_showcase_signals(project), [])
        self.assertEqual(
            [signal["kind"] for signal in project_review.compute_showcase_signals(project, include_personal_tools=True)],
            ["lexicon_publisher_confirmed"],
        )

    def test_low_confidence_http_only_service_is_validation_candidate(self) -> None:
        project = self.make_project("proj:http200only", "service_url", "https://example.com")
        project.update({
            "category": "project",
            "service_status": "live",
            "service_status_confidence": "low",
            "service_status_evidence": ["http_200"],
        })
        registry.confirm_service_seed(project, "https://example.com", source="test")

        signals = project_review.compute_showcase_signals(project)

        self.assertEqual([signal["kind"] for signal in signals], ["service_candidate_needs_validation"])
        self.assertEqual(
            signals[0]["details"]["validation_reasons"],
            ["low_service_status_confidence", "usable_surface_unconfirmed"],
        )

    def test_unverified_service_remains_validation_candidate_even_with_usable_surface_evidence(self) -> None:
        project = self.make_project("proj:unverified", "service_url", "https://example.com")
        project.update({
            "category": "project",
            "service_status": "unverified",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "login_surface_detected"],
        })
        registry.confirm_service_seed(project, "https://example.com", source="test")

        signals = project_review.compute_showcase_signals(project)

        self.assertEqual([signal["kind"] for signal in signals], ["service_candidate_needs_validation"])
        self.assertEqual(
            signals[0]["details"]["validation_reasons"],
            ["service_status_unverified"],
        )

    def test_docs_only_service_is_validation_candidate(self) -> None:
        project = self.make_project("proj:docsonly", "service_url", "https://example.com")
        project.update({
            "category": "project",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "docs_only_surface_detected"],
        })
        registry.confirm_service_seed(project, "https://example.com", source="test")

        signals = project_review.compute_showcase_signals(project)

        self.assertEqual([signal["kind"] for signal in signals], ["service_candidate_needs_validation"])
        self.assertEqual(
            signals[0]["details"]["validation_reasons"],
            ["usable_surface_unconfirmed"],
        )

    def test_infra_tool_with_usable_surface_is_promoted_as_service(self) -> None:
        project = self.make_project("proj:infraservice", "service_url", "https://infra.example")
        project.update({
            "category": "infra_tool",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "app_shell_detected"],
        })
        registry.confirm_service_seed(project, "https://infra.example", source="test")

        signals = project_review.compute_showcase_signals(project)

        self.assertEqual([signal["kind"] for signal in signals], ["service_surface_confirmed"])
        self.assertEqual(signals[0]["details"]["category"], "infra_tool")

    def test_unverified_infra_tool_remains_validation_candidate(self) -> None:
        project = self.make_project("proj:infravalidation", "service_url", "https://infra.example")
        project.update({
            "category": "infra_tool",
            "service_status": "unverified",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "app_shell_detected"],
        })
        registry.confirm_service_seed(project, "https://infra.example", source="test")

        signals = project_review.compute_showcase_signals(project)

        self.assertEqual([signal["kind"] for signal in signals], ["service_candidate_needs_validation"])

    def test_library_with_service_fields_does_not_receive_service_signal(self) -> None:
        project = self.make_project("proj:librarydocs", "service_url", "https://library.example")
        project.update({
            "category": "library",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "app_shell_detected"],
        })
        registry.confirm_service_seed(project, "https://library.example", source="test")

        self.assertEqual(project_review.compute_showcase_signals(project), [])

    def test_validation_candidate_sorts_below_confirmed_service_surface(self) -> None:
        confirmed = self.make_project("proj:confirmed", "service_url", "https://confirmed.example")
        confirmed.update({
            "category": "project",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "login_surface_detected"],
        })
        registry.confirm_service_seed(confirmed, "https://confirmed.example", source="test")
        validation = dict(confirmed)
        validation["project_key"] = "proj:validation"
        validation["service_status_evidence"] = ["http_200"]

        confirmed_signals = project_review.compute_showcase_signals(confirmed)
        validation_signals = project_review.compute_showcase_signals(validation)

        self.assertEqual([item["kind"] for item in confirmed_signals], ["service_surface_confirmed"])
        self.assertEqual([item["kind"] for item in validation_signals], ["service_candidate_needs_validation"])
        self.assertEqual(
            project_review.promotion_queue_sort_score(confirmed, confirmed_signals)
            - project_review.promotion_queue_sort_score(validation, validation_signals),
            100,
        )

    def test_promotion_policy_separates_visibility_service_and_manual_priority(self) -> None:
        project = self.make_project("proj:policyaxes", "service_url", "https://example.com")
        project.update({
            "category": "project",
            "service_status": "live",
            "service_status_confidence": "medium",
            "service_status_evidence": ["http_200", "login_surface_detected"],
        })
        registry.confirm_service_seed(project, "https://example.com", source="test")
        signals = project_review.compute_showcase_signals(project)

        policy = project_review.promotion_policy_projection(project, signals)

        self.assertEqual(policy["candidate_visibility"], {"group": "eligible", "reasons": []})
        self.assertEqual(policy["service_evidence_group"], "confirmed")
        self.assertEqual(policy["priority_group"], "normal")
        self.assertEqual(
            policy["score_breakdown"]["total"],
            sum(policy["score_breakdown"]["components"].values()),
        )
        self.assertEqual(policy["score_breakdown"]["components"]["service_evidence"], 200)

        project_review.set_promotion_priority_on_project(project, lowered=True)
        policy = project_review.promotion_policy_projection(project, signals)
        self.assertEqual(policy["priority_group"], "lowered")
        self.assertEqual(policy["priority_reason"], "")

        project_review.defer_promotion_on_project(project, reason="editorial review later")
        self.assertEqual(
            project_review.promotion_candidate_visibility(project),
            {"group": "deferred", "reasons": ["active_promotion_deferral"]},
        )

    def test_merge_keeps_canonical_target_promotion_priority(self) -> None:
        target = self.make_project("proj:editorialtarget", "service_url", "https://target.example")
        source = self.make_project("proj:editorialsource", "service_url", "https://source.example")
        project_review.set_promotion_priority_on_project(
            target,
            lowered=True,
            reason="not ready for a standalone introduction",
        )
        project_review.set_promotion_priority_on_project(
            source,
            lowered=True,
            reason="best grouped with similar projects",
        )

        merged = merge_services.merge_project_records(target, source)

        self.assertEqual(
            project_review.active_promotion_priority_adjustment(merged)["reason"],
            "not ready for a standalone introduction",
        )
        adjustments = merged["promotion_priority_adjustments"]
        self.assertEqual(len(adjustments), 2)
        self.assertFalse(adjustments[0].get("cleared_at"))
        self.assertTrue(adjustments[1].get("cleared_at"))

    def test_promotion_deferral_allows_empty_reason_and_clear_keeps_history(self) -> None:
        project = self.make_project("proj:promotionhold", "service_url", "https://example.com")

        project_review.defer_promotion_on_project(project, reason="中核機能が未実装")
        project_review.clear_active_promotion_deferrals(project)
        entry = project_review.defer_promotion_on_project(project)

        self.assertEqual(entry["reason"], "")
        self.assertEqual(len(project["promotion_deferrals"]), 2)
        self.assertTrue(project["promotion_deferrals"][0]["cleared_at"])
        self.assertEqual(project_review.active_promotion_deferral(project)["reason"], "")

    def test_promotion_deferral_default_is_thirty_days_regardless_of_age_and_history(self) -> None:
        project = self.make_project("proj:promotionhold", "service_url", "https://example.com")
        project["first_seen_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat()
        for _ in range(5):
            entry = project_review.defer_promotion_on_project(project)
            duration = project_record.parse_iso(entry["expires_at"]) - project_record.parse_iso(entry["added_at"])
            self.assertAlmostEqual(duration.total_seconds(), 30 * 86400, delta=1)

    def test_promotion_deferral_uses_explicit_days_when_provided(self) -> None:
        project = self.make_project("proj:promotionhold", "service_url", "https://example.com")

        entry = project_review.defer_promotion_on_project(project, reason="explicit", days=7)
        expires = project_record.parse_iso(entry["expires_at"])
        added = project_record.parse_iso(entry["added_at"])

        self.assertIsNotNone(expires)
        self.assertIsNotNone(added)
        self.assertAlmostEqual((expires - added).total_seconds(), 7 * 24 * 60 * 60, delta=5)


class ProjectRegistryStorageTest(TempRegistryDbTestCase):
    def test_refresh_current_same_site_evidence_recomputes_stale_redirect_flags_only(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:samesiterepair", "service_url", "https://pages.dev")
            project["service_urls"] = ["https://pages.dev"]
            project["service_status"] = "live"
            project["service_status_evidence"] = ["http_200", "redirect_same_site_family"]
            project["service_verification_results"] = [{
                "requested_url": "https://pages.dev",
                "final_url": "https://pages.cloudflare.com",
                "redirected": True,
                "redirect_same_site_family": True,
                "service_status_evidence": ["http_200", "redirect_same_site_family"],
            }]
            project_record.save_project(conn, project)

            executed = registry.refresh_current_same_site_evidence(conn)
            self.assertEqual(executed["repaired_result_count"], 1)
            self.assertEqual(executed["aggregate_evidence_change_count"], 1)
            repaired = project_record.fetch_project(conn, project["project_key"])
            assert repaired is not None
            self.assertFalse(repaired["service_verification_results"][0]["redirect_same_site_family"])
            self.assertNotIn("redirect_same_site_family", repaired["service_verification_results"][0]["service_status_evidence"])
            self.assertEqual(repaired["service_status_evidence"], ["http_200"])
            self.assertEqual(repaired["service_urls"], ["https://pages.dev"])
            self.assertEqual(repaired["service_status"], "live")
            self.assertEqual(registry.refresh_current_same_site_evidence(conn)["changed_project_count"], 0)
        finally:
            conn.close()

    def test_repair_db_moves_did_out_of_developer_handle_field(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:didhandleaccount", "nsid", "app.example.didhandle")
            project_record.save_project(conn, project)
            state = json.loads(
                conn.execute(
                    "SELECT state_json FROM projects WHERE project_key = ?",
                    (project["project_key"],),
                ).fetchone()[0]
            )
            state.setdefault("evidence", {})["candidate_developer_accounts"] = [
                {"handle": "did:plc:abcdefghijklmnopqrstuvwx", "source": "fixture"}
            ]
            conn.execute(
                "UPDATE projects SET state_json = ? WHERE project_key = ?",
                (json.dumps(state), project["project_key"]),
            )
            conn.execute(
                "INSERT INTO project_identifiers(project_key, ordinal, kind, value, role, classification, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    project["project_key"],
                    98,
                    "account",
                    "did:plc:abcdefghijklmnopqrstuvwx",
                    "developer_account",
                    "candidate",
                    '{"handle":"did:plc:abcdefghijklmnopqrstuvwx","source":"fixture"}',
                ),
            )
            conn.execute(
                "INSERT INTO project_identifiers(project_key, ordinal, kind, value, role, classification, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    project["project_key"],
                    99,
                    "handle",
                    "did:plc:abcdefghijklmnopqrstuvwx",
                    "developer_handle",
                    "candidate",
                    '{"account":"did:plc:abcdefghijklmnopqrstuvwx"}',
                ),
            )
            conn.commit()

            dry_run = maintenance_services.repair_database(conn)
            self.assertEqual(dry_run["mode"], "dry_run")
            self.assertGreaterEqual(dry_run["pruned_project_count"], 1)
            self.assertEqual(dry_run["developer_account_repair_count"], 3)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM project_identifiers WHERE project_key = ? AND kind = 'handle' AND value LIKE 'did:%'",
                    (project["project_key"],),
                ).fetchone()[0],
                1,
            )

            executed = maintenance_services.repair_database(conn, execute=True)
            self.assertEqual(executed["mode"], "execute")
            repaired = project_record.fetch_project(conn, project["project_key"])
            assert repaired is not None
            self.assertEqual(
                repaired["candidate_developer_accounts"],
                [{"did": "did:plc:abcdefghijklmnopqrstuvwx", "source": "fixture"}],
            )
            rows = conn.execute(
                "SELECT kind, value, role FROM project_identifiers WHERE project_key = ? AND role LIKE 'developer_%' ORDER BY ordinal",
                (project["project_key"],),
            ).fetchall()
            self.assertNotIn(
                ("handle", "did:plc:abcdefghijklmnopqrstuvwx", "developer_handle"),
                [tuple(row) for row in rows],
            )
            self.assertIn(
                ("did", "did:plc:abcdefghijklmnopqrstuvwx", "developer_did"),
                [tuple(row) for row in rows],
            )
        finally:
            conn.close()

    def test_repair_db_drops_template_developer_identifier_rows(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:templatehandle", "service_url", "https://skyspace.me")
            project_record.save_project(conn, project)
            state = json.loads(
                conn.execute(
                    "SELECT state_json FROM projects WHERE project_key = ?",
                    (project["project_key"],),
                ).fetchone()[0]
            )
            state.setdefault("evidence", {})["candidate_developer_accounts"] = [
                {"handle": "${encodeURIComponent(profileData.handle)}`", "source": "service_raw_url:https://skyspace.me"}
            ]
            conn.execute(
                "UPDATE projects SET state_json = ? WHERE project_key = ?",
                (json.dumps(state), project["project_key"]),
            )
            conn.execute(
                "INSERT INTO project_identifiers(project_key, ordinal, kind, value, role, classification, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    project["project_key"],
                    98,
                    "account",
                    "${encodeURIComponent(profileData.handle)}`",
                    "developer_account",
                    "candidate",
                    '{"handle":"${encodeURIComponent(profileData.handle)}`","source":"service_raw_url:https://skyspace.me"}',
                ),
            )
            conn.execute(
                "INSERT INTO project_identifiers(project_key, ordinal, kind, value, role, classification, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    project["project_key"],
                    99,
                    "handle",
                    "${encodeuricomponent(profiledata.handle)}`",
                    "developer_handle",
                    "candidate",
                    '{"account":"${encodeURIComponent(profileData.handle)}`"}',
                ),
            )
            conn.commit()

            dry_run = maintenance_services.repair_database(conn)
            self.assertEqual(dry_run["mode"], "dry_run")
            self.assertGreaterEqual(dry_run["developer_account_repair_count"], 3)

            executed = maintenance_services.repair_database(conn, execute=True)
            self.assertEqual(executed["mode"], "execute")
            repaired = project_record.fetch_project(conn, project["project_key"])
            assert repaired is not None
            self.assertEqual(repaired["candidate_developer_accounts"], [])
            rows = conn.execute(
                "SELECT value FROM project_identifiers WHERE project_key = ? AND role LIKE 'developer_%'",
                (project["project_key"],),
            ).fetchall()
            self.assertFalse([row for row in rows if "encodeuricomponent" in str(row["value"]).lower()])
        finally:
            conn.close()

    def test_exact_identifier_lookup_uses_normalized_rows_without_project_hydration(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:indexlookup", "nsid", "app.example.post")
            registry.apply_seed_to_project(project, "nsid", "app.example.post")
            project["website_url"] = "https://example.com"
            project["service_urls"] = ["https://example.com"]
            project["service_url_candidates"] = ["https://candidate.example.com"]
            project["project_repository_urls"] = [
                "https://github.com/example/app",
                "https://tangled.sh/@example/app",
            ]
            project["developer_accounts"] = [{"handle": "dev.example.com", "did": "did:plc:developer"}]
            project["authority_domains"] = ["example.com"]
            project_record.save_project(conn, project)
            saved = project_record.fetch_project(conn, project["project_key"])
            assert saved is not None
            tangled_url = project_record.provider_repo_url_values(saved, "tangled")[0]
            seed_rows = conn.execute(
                "SELECT kind, value FROM project_identifiers WHERE project_key = ? AND role = 'owned_nsid' AND classification = 'confirmed'",
                (project["project_key"],),
            ).fetchall()
            self.assertIn(("nsid", "app.example.post"), [tuple(row) for row in seed_rows])

            with mock.patch.object(registry, "all_projects", side_effect=AssertionError("hydrated projects")):
                self.assertEqual(project_record.find_project_key_by_identifier(conn, "nsid", "app.example.post"), project["project_key"])
                self.assertEqual(project_record.find_project_key_by_identifier(conn, "service_url", "https://example.com/"), project["project_key"])
                self.assertEqual(project_record.find_project_key_by_identifier(conn, "service_url_candidate", "https://candidate.example.com"), project["project_key"])
                self.assertEqual(project_record.find_project_key_by_identifier(conn, "repo_url", "https://github.com/example/app"), project["project_key"])
                self.assertEqual(project_record.find_project_key_by_identifier(conn, "github_repo_url", "https://github.com/example/app"), project["project_key"])
                self.assertEqual(project_record.find_project_key_by_identifier(conn, "tangled_repo_url", tangled_url), project["project_key"])
                self.assertIsNone(project_record.find_project_key_by_identifier(conn, "domain", "EXAMPLE.COM."))

            authority_rows = conn.execute(
                "SELECT 1 FROM project_identifiers WHERE project_key=? AND role='authority_domain'",
                (project["project_key"],),
            ).fetchall()
            self.assertEqual(authority_rows, [])
            self.assertEqual(saved["authority_domains"], ["example.app"])

            with mock.patch.object(registry, "all_projects", side_effect=AssertionError("payload scan")):
                self.assertEqual(project_record.find_project_key_by_identifier(conn, "handle", "DEV.EXAMPLE.COM."), project["project_key"])
                self.assertEqual(project_record.find_project_key_by_identifier(conn, "did", "did:plc:developer"), project["project_key"])

            plan = conn.execute(
                "EXPLAIN QUERY PLAN SELECT project_key FROM project_identifiers INDEXED BY idx_project_identifiers_lookup WHERE kind = ? AND value = ?",
                ("nsid", "app.example.post"),
            ).fetchall()
            self.assertIn("idx_project_identifiers_lookup", " ".join(str(column) for row in plan for column in row))
        finally:
            conn.close()

    def test_exact_identifier_priority_and_semble_ambiguity_are_preserved(self) -> None:
        conn = registry.open_db()
        try:
            candidate = self.make_project("proj:aaaaaaaaaaaa", "nsid", "app.candidate.test")
            registry.apply_seed_to_project(candidate, "nsid", "app.candidate.test")
            candidate["service_url_candidates"] = ["https://shared.example.com"]
            confirmed = self.make_project("proj:zzzzzzzzzzzz", "service_url", "https://shared.example.com")
            registry.add_manual_seed(confirmed, "service_url", "https://shared.example.com", source="test")
            registry.confirm_service_seed(confirmed, "https://shared.example.com", source="test")
            project_record.save_project(conn, candidate)
            project_record.save_project(conn, confirmed)

            self.assertEqual(
                project_record.indexed_identifier_matches(conn, "service_url", "https://shared.example.com"),
                [(0, confirmed["project_key"]), (1, candidate["project_key"])],
            )
            self.assertEqual(
                project_record.find_project_key_by_identifier(conn, "service_url", "https://shared.example.com"),
                confirmed["project_key"],
            )
            with mock.patch.object(project_record, "project_matches_identifier", side_effect=AssertionError("payload scan")):
                resolved = registry.resolve_semble_project_key_for_seed_value(
                    conn,
                    "service_url",
                    "https://shared.example.com",
                    projects=[candidate, confirmed],
                )
            self.assertEqual(resolved, (confirmed["project_key"], "exact_identifier", None, ""))
        finally:
            conn.close()

    def test_indexed_lookup_ignores_removed_identifier_roles(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:legacyroles", "nsid", "app.current.post")
            project_record.save_project(conn, project)
            conn.executemany(
                "INSERT INTO project_identifiers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (project["project_key"], 99, "service_url", "https://legacy.example.com", "manual_seed", "candidate", 0, "", "{}", ""),
                    (project["project_key"], 100, "nsid", "app.legacy.post", "input_nsid", "candidate", 0, "", "{}", ""),
                ],
            )
            conn.commit()

            self.assertEqual(
                project_record.indexed_identifier_matches(conn, "service_url", "https://legacy.example.com"),
                [],
            )
            self.assertEqual(
                project_record.indexed_identifier_matches(conn, "service_url_candidate", "https://legacy.example.com"),
                [],
            )
            self.assertEqual(project_record.indexed_identifier_matches(conn, "nsid", "app.legacy.post"), [])
        finally:
            conn.close()

    def test_indexed_handle_lookup_prefers_developer_account_over_project_handle(self) -> None:
        conn = registry.open_db()
        try:
            project_handle = self.make_project("proj:aaaaaaaaaaaa", "nsid", "app.handle.project")
            project_handle["bluesky_handles"] = ["shared.example.com"]
            developer_handle = self.make_project("proj:zzzzzzzzzzzz", "nsid", "app.handle.developer")
            developer_handle["developer_accounts"] = [{"handle": "shared.example.com"}]
            project_record.save_project(conn, project_handle)
            project_record.save_project(conn, developer_handle)

            self.assertEqual(
                project_record.indexed_identifier_matches(conn, "handle", "SHARED.EXAMPLE.COM."),
                [(0, developer_handle["project_key"]), (1, project_handle["project_key"])],
            )
            self.assertEqual(
                project_record.find_project_key_by_identifier(conn, "handle", "shared.example.com"),
                developer_handle["project_key"],
            )
        finally:
            conn.close()

    def test_indexed_did_lookup_filters_normalized_rows_absent_from_current_payload(self) -> None:
        conn = registry.open_db()
        try:
            stale = self.make_project("proj:aaaaaaaaaaaa", "nsid", "app.did.stale")
            current = self.make_project("proj:zzzzzzzzzzzz", "nsid", "app.did.current")
            current["candidate_developer_accounts"] = [{"did": "did:plc:shared"}]
            project_record.save_project(conn, stale)
            project_record.save_project(conn, current)
            conn.execute(
                "INSERT INTO project_identifiers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (stale["project_key"], 99, "did", "did:plc:shared", "developer_did", "candidate", 0, "", "{}", ""),
            )
            conn.commit()

            with mock.patch.object(registry, "all_projects", side_effect=AssertionError("full payload scan")):
                self.assertEqual(
                    project_record.indexed_identifier_matches(conn, "did", "did:plc:shared"),
                    [(0, current["project_key"])],
                )
                self.assertEqual(
                    project_record.find_project_key_by_identifier(conn, "did", "did:plc:shared"),
                    current["project_key"],
                )
        finally:
            conn.close()

    def test_save_applies_resource_url_budget_to_raw_oversized_state(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:rawresourcebudget", "service_url", "https://example.com")
            project["resource_url_records"] = [
                {
                    "url": f"https://example.com/docs/page-{index}",
                    "role": "docs",
                    "source": "oversized_resource_urls",
                    "confidence": "candidate",
                }
                for index in range(20)
            ]
            project_record.save_project(conn, project)
            saved = project_record.fetch_project(conn, project["project_key"])
        finally:
            conn.close()

        assert saved is not None
        self.assertEqual(len(saved["resource_url_records"]), 4)

    def test_split_project_by_authority_domain_moves_domain_group(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:mixedauthority", "service_url", "https://foo.example.com")
            registry.confirm_service_seed(project, "https://foo.example.com", source="test")
            registry.confirm_service_seed(project, "https://bar.example.com", source="test")
            registry.add_manual_seed(project, "nsid", "com.example.foo.post", source="test")
            registry.add_manual_seed(project, "nsid", "com.example.bar.post", source="test")
            registry.add_manual_seed(project, "domain", "foo.example.com", source="test")
            registry.add_manual_seed(project, "repo_url", "https://github.com/example/mixed", source="test")
            project_record.save_project(conn, project)
            conn.commit()

            dry_run = merge_services.split_identifier(
                conn,
                project_key="proj:mixedauthority",
                identifier_type="domain",
                value="foo.example.com",
                execute=False,
            )
            unchanged = project_record.fetch_project(conn, "proj:mixedauthority")
            executed = merge_services.split_identifier(
                conn,
                project_key="proj:mixedauthority",
                identifier_type="domain",
                value="foo.example.com",
                note="wrong merge",
                execute=True,
            )
            source = project_record.fetch_project(conn, "proj:mixedauthority")
            target = project_record.fetch_project(conn, executed["target_project_key"])
        finally:
            conn.close()

        assert unchanged is not None
        assert source is not None
        assert target is not None
        self.assertEqual(dry_run["mode"], "dry_run")
        self.assertIn("foo.example.com", dry_run["target_authority_domains"])
        self.assertIn("foo.example.com", unchanged["authority_domains"])
        self.assertNotIn("https://foo.example.com", source["service_urls"])
        self.assertNotIn("com.example.foo.post", source["resolved_nsids"])
        self.assertNotIn("foo.example.com", source["authority_domains"])
        self.assertIn("https://bar.example.com", source["service_urls"])
        self.assertIn("com.example.bar.post", source["resolved_nsids"])
        self.assertIn("bar.example.com", source["authority_domains"])
        self.assertEqual(source["project_repository_urls"], ["https://github.com/example/mixed"])
        self.assertIn("https://foo.example.com", target["service_urls"])
        self.assertIn("com.example.foo.post", target["resolved_nsids"])
        self.assertIn("foo.example.com", target["authority_domains"])
        self.assertEqual(target["project_repository_urls"], [])
        blocked = {(item["type"], item["value"]) for item in source["blocked_identifiers"]}
        self.assertIn(("service_url", "https://foo.example.com"), blocked)
        self.assertIn(("nsid", "com.example.foo.post"), blocked)
        self.assertIn(("domain", "foo.example.com"), blocked)
        self.assertIn(target["project_key"], source["separate_project_keys"])
        self.assertIn(source["project_key"], target["separate_project_keys"])

    def test_init_db_command_explicitly_creates_custom_database(self) -> None:
        custom_path = Path(self.tempdir.name) / "custom" / "survey.db"
        args = cli.build_parser().parse_args(["init-db", "--db-path", str(custom_path)])

        with captured_stdout() as output:
            result = args.func(args)

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["db_path"], str(custom_path.resolve()))
        conn = database.open_database(custom_path, create=False)
        try:
            self.assertEqual(database.database_schema_version(conn), database.SCHEMA_VERSION)
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'investigation_runs'"
            ).fetchone())
        finally:
            conn.close()

    def test_open_db_uses_busy_timeout_and_wal(self) -> None:
        conn = registry.open_db()
        try:
            busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            conn.close()

        self.assertGreaterEqual(busy_timeout_ms, 30000)
        self.assertEqual(journal_mode, "wal")

    def test_sync_service_resolves_introduced_defaults_from_config(self) -> None:
        config = {
            "introduced_search": {
                "enabled": True,
                "query": "from:operator.example #topic",
                "limit": 8,
                "max_pages": 9,
                "lookback_days": 6,
            }
        }
        conn = registry.open_db()
        try:
            with (
                mock.patch.object(sync_services.core, "load_config", return_value=config),
                mock.patch.object(sync_services.core, "sync_introduced_entries", return_value={}) as sync,
            ):
                sync_services.introduced(conn)
        finally:
            conn.close()

        self.assertEqual(sync.call_args.kwargs["query"], "from:operator.example #topic")
        self.assertEqual(sync.call_args.kwargs["limit"], 8)
        self.assertEqual(sync.call_args.kwargs["max_pages"], 9)
        self.assertEqual(sync.call_args.kwargs["lookback_days"], 6)

    def test_disabled_source_is_rejected_before_sync(self) -> None:
        conn = registry.open_db()
        try:
            with mock.patch.object(
                sync_services.core,
                "load_config",
                return_value={"github_repo_search": {"enabled": False}},
            ):
                with self.assertRaisesRegex(ValueError, "source is disabled"):
                    sync_services.github(conn)
        finally:
            conn.close()

    def test_semble_scope_and_api_are_resolved_from_config(self) -> None:
        config = {
            "semble_collections": {
                "enabled": True,
                "api_url": "https://semble.example",
                "pages": 3,
                "per_page": 4,
                "lookback_days": 5,
                "tracked_collections": [{"handle": "operator.example", "rkey": "abc", "label": "test"}],
            }
        }
        conn = registry.open_db()
        try:
            with (
                mock.patch.object(sync_services.core, "load_config", return_value=config),
                mock.patch.object(sync_services.core, "sync_semble_collections", return_value={}) as sync,
            ):
                sync_services.semble(conn)
        finally:
            conn.close()

        self.assertEqual(sync.call_args.kwargs["pages"], 3)
        self.assertEqual(sync.call_args.kwargs["per_page"], 4)
        self.assertEqual(sync.call_args.kwargs["lookback_days"], 5)
        self.assertEqual(sync.call_args.kwargs["tracked_collections"][0]["api_url"], "https://semble.example")

    def test_bluesky_lexicon_sync_imports_only_app_and_chat_record_schemas(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:bluesky", "service_url", "https://bsky.app")
            registry.add_manual_seed(project, "service_url", "https://bsky.app", source="test")
            project_record.save_project(conn, project)
            repository = {
                "revision": "abc123",
                "tree": [
                    {"type": "blob", "path": "lexicons/app/bsky/feed/post.json"},
                    {"type": "blob", "path": "lexicons/chat/bsky/convo/defs.json"},
                    {"type": "blob", "path": "lexicons/app/bsky/actor/defs.json"},
                    {"type": "blob", "path": "lexicons/com/atproto/repo/strongRef.json"},
                ],
            }
            files = {
                "lexicons/app/bsky/feed/post.json": json.dumps({"id": "app.bsky.feed.post", "defs": {"main": {"type": "record"}}}),
                "lexicons/chat/bsky/convo/defs.json": json.dumps({"id": "chat.bsky.convo.defs", "defs": {"main": {"type": "record"}}}),
                "lexicons/app/bsky/actor/defs.json": json.dumps({"id": "app.bsky.actor.defs", "defs": {"main": {"type": "object"}}}),
            }
            with (
                mock.patch.object(source_sync.repository_source_core, "fetch_github_repo", return_value=repository),
                mock.patch.object(
                    source_sync.repository_source_core,
                    "github_repo_file_text",
                    side_effect=lambda _repo, path, **_kwargs: files[path],
                ),
            ):
                dry_run = source_sync.sync_bluesky_lexicons(
                    conn,
                    repo_url="https://github.com/bluesky-social/atproto",
                    target_service_url="https://bsky.app",
                )
                executed = source_sync.sync_bluesky_lexicons(
                    conn,
                    repo_url="https://github.com/bluesky-social/atproto",
                    target_service_url="https://bsky.app",
                    execute=True,
                )
            fetched = project_record.fetch_project(conn, "proj:bluesky")
        finally:
            conn.close()

        assert fetched is not None
        expected = ["app.bsky.feed.post", "chat.bsky.convo.defs"]
        self.assertEqual(dry_run["status"], "ready")
        self.assertEqual(dry_run["collections"], expected)
        self.assertEqual(executed["changed_count"], 2)
        self.assertTrue(set(expected).issubset(fetched["resolved_nsids"]))
        self.assertFalse(any(value.startswith("com.atproto.") for value in fetched["resolved_nsids"]))

    def test_collection_import_attaches_by_member_parent_namespace(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:homeport", "nsid", "social.homeport.activitypub.announce")
            registry.add_manual_seed(project, "nsid", "social.homeport.activitypub.announce", source="test")
            project_record.save_project(conn, project)

            result = registry.import_collection_inventory_payload(
                conn,
                [
                    {
                        "collection": "social.homeport.activitypub.note",
                        "count": 10,
                        "recent_count": 2,
                        "min": "2026-07-01T00:00:00+00:00",
                        "max": "2026-07-04T00:00:00+00:00",
                    }
                ],
                execute=True,
                create_unmatched=True,
                source="test_collection_inventory",
            )
            fetched = project_record.fetch_project(conn, "proj:homeport")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 1)
        self.assertIn("social.homeport.activitypub.note", fetched["resolved_nsids"])
        self.assertNotIn("social.homeport.activitypub.note", [item["value"] for item in fetched["manual_seeds"]])

    def test_collection_import_attaches_by_manual_seed_parent_namespace(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:comind", "nsid", "network.comind.memory")
            registry.add_manual_seed(project, "nsid", "network.comind.memory", source="test")
            registry.add_manual_seed(project, "nsid", "network.comind.void.action", source="test")
            project_record.save_project(conn, project)

            result = registry.import_collection_inventory_payload(
                conn,
                [
                    {
                        "collection": "network.comind.void.memory",
                        "count": 4,
                        "recent_count": 4,
                        "min": "2026-07-11T04:57:09+00:00",
                        "max": "2026-07-11T08:43:02+00:00",
                    }
                ],
                execute=True,
                create_unmatched=True,
                source="test_collection_inventory",
            )
            fetched = project_record.fetch_project(conn, "proj:comind")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 1)
        self.assertIn("network.comind.void.memory", fetched["resolved_nsids"])
        self.assertNotIn("network.comind.void.memory", [item["value"] for item in fetched["manual_seeds"]])

    def test_collection_import_attaches_all_aggregated_members(self) -> None:
        conn = registry.open_db()
        try:
            result = registry.import_collection_inventory_payload(
                conn,
                [
                    {
                        "collection": "link.latr.saved.external",
                        "count": 10,
                        "recent_count": 2,
                        "min": "2026-07-01T00:00:00+00:00",
                        "max": "2026-07-04T00:00:00+00:00",
                    },
                    {
                        "collection": "link.latr.saved.item",
                        "count": 3,
                        "recent_count": 1,
                        "min": "2026-07-02T00:00:00+00:00",
                        "max": "2026-07-05T00:00:00+00:00",
                    },
                ],
                execute=True,
                create_unmatched=True,
                source="test_collection_inventory",
            )
            fetched = project_record.fetch_project(conn, result["imported_project_keys"][0])
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(result["created"], 1)
        self.assertEqual(fetched["collection_observations"][0]["collection"], "link.latr.saved")
        self.assertNotIn("members", fetched["collection_observations"][0])
        self.assertIn("link.latr.saved.external", fetched["resolved_nsids"])
        self.assertIn("link.latr.saved.item", fetched["resolved_nsids"])
        self.assertEqual(fetched["manual_seeds"], [])

    def test_collection_import_partitions_safe_member_match_from_unmatched_siblings(self) -> None:
        conn = registry.open_db()
        try:
            shelf = self.make_project("proj:orbita-shelf", "nsid", "social.orbita.shelf.item")
            registry.add_manual_seed(shelf, "nsid", "social.orbita.shelf.item", source="test")
            project_record.save_project(conn, shelf)

            result = registry.import_collection_inventory_payload(
                conn,
                [
                    {"collection": "social.orbita.shelf.nook", "count": 10, "recent_count": 2},
                    {"collection": "social.orbita.event.message", "count": 4, "recent_count": 1},
                    {"collection": "social.orbita.event", "count": 3, "recent_count": 1},
                    {"collection": "social.orbita.forum.post", "count": 2, "recent_count": 0},
                ],
                execute=True,
                create_unmatched=True,
                source="test_collection_inventory",
            )
            fetched_shelf = project_record.fetch_project(conn, "proj:orbita-shelf")
            created_projects = [
                project_record.fetch_project(conn, key)
                for key in result["imported_project_keys"]
                if key != "proj:orbita-shelf"
            ]
        finally:
            conn.close()

        assert fetched_shelf is not None
        self.assertTrue(all(project is not None for project in created_projects))
        self.assertEqual(result["namespaces"], 4)
        self.assertEqual(result["created"], 3)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(len(result["imported_project_keys"]), 4)
        self.assertIn("social.orbita.shelf.nook", fetched_shelf["resolved_nsids"])
        self.assertNotIn("social.orbita.event.message", fetched_shelf["resolved_nsids"])
        self.assertEqual(fetched_shelf["collection_observations"][0]["count"], 10)
        self.assertEqual(
            {tuple(project["resolved_nsids"]) for project in created_projects if project is not None},
            {
                ("social.orbita.event.message",),
                ("social.orbita.event",),
                ("social.orbita.forum.post",),
            },
        )
        self.assertTrue(
            all("members" not in project["collection_observations"][0] for project in created_projects if project is not None)
        )

    def test_collection_all_history_rebuilds_observations_by_parent_namespace(self) -> None:
        payload = [
            {"collection": "site.filae.newsletter.edition", "count": 10, "max": "2026-05-01T00:00:00+00:00"},
            {"collection": "site.filae.chorus.note", "count": 4, "max": "2026-05-02T00:00:00+00:00"},
            {"collection": "site.filae.chorus.rating", "count": 3, "max": "2026-05-03T00:00:00+00:00"},
            {"collection": "site.filae.agora.post", "count": 2, "max": "2026-05-04T00:00:00+00:00"},
        ]
        conn = registry.open_db()
        try:
            filae = self.make_project("proj:filiae", "nsid", "site.filae.newsletter.edition")
            project_record.save_project(conn, filae)
            row = conn.execute("SELECT state_json FROM projects WHERE project_key = ?", ("proj:filiae",)).fetchone()
            state = json.loads(str(row["state_json"]))
            state.setdefault("ops", {})["collection_observations"] = [{
                "collection": "site.filae",
                "count": 19,
                "recent_count": 0,
                "min": "2026-04-01T00:00:00+00:00",
                "max": "2026-05-04T00:00:00+00:00",
                "source": "collection_inventory_url:https://collections.example/inventory.json",
            }]
            conn.execute(
                "UPDATE projects SET state_json = ? WHERE project_key = ?",
                (json.dumps(state, ensure_ascii=False, sort_keys=True), "proj:filiae"),
            )

            orphan = self.make_project("proj:orphan", "nsid", "social.orphan.old.item")
            project_record.save_project(conn, orphan)
            row = conn.execute("SELECT state_json FROM projects WHERE project_key = ?", ("proj:orphan",)).fetchone()
            state = json.loads(str(row["state_json"]))
            state.setdefault("ops", {})["collection_observations"] = [{
                "collection": "social.orphan.old",
                "count": 1,
                "recent_count": 0,
                "source": "collection_inventory_import",
            }]
            conn.execute(
                "UPDATE projects SET state_json = ? WHERE project_key = ?",
                (json.dumps(state, ensure_ascii=False, sort_keys=True), "proj:orphan"),
            )
            conn.commit()

            with mock.patch.object(source_sync, "fetch_collection_inventory", return_value=payload):
                dry_run = source_sync.sync_collection_inventory(
                    conn,
                    source_url="https://collections.example/inventory.json",
                    execute=False,
                    create_unmatched=True,
                    lookback_days=10,
                    all_history=True,
                )
                before = project_record.list_projects(conn)
                executed = source_sync.sync_collection_inventory(
                    conn,
                    source_url="https://collections.example/inventory.json",
                    execute=True,
                    create_unmatched=True,
                    lookback_days=10,
                    all_history=True,
                )
            after = project_record.list_projects(conn)
        finally:
            conn.close()

        self.assertEqual((dry_run["created"], dry_run["updated"]), (2, 1))
        self.assertEqual(len(before), 2)
        self.assertEqual((executed["created"], executed["updated"]), (2, 1))
        self.assertEqual(executed["collection_observation_rebuild_project_count"], 2)
        self.assertEqual(executed["collection_observation_cleanup_count"], 2)
        self.assertEqual(len(after), 4)
        by_nsids = {
            tuple(project["resolved_nsids"]): project
            for project in after
            if any(nsid.startswith("site.filae.") for nsid in project["resolved_nsids"])
        }
        self.assertEqual(
            set(by_nsids),
            {
                ("site.filae.newsletter.edition",),
                ("site.filae.chorus.note", "site.filae.chorus.rating"),
                ("site.filae.agora.post",),
            },
        )
        self.assertEqual(
            by_nsids[("site.filae.newsletter.edition",)]["collection_observations"][0]["collection"],
            "site.filae.newsletter",
        )
        for project in after:
            self.assertEqual(project["manual_seeds"], [])
            self.assertTrue(all("members" not in item for item in project["collection_observations"]))
        orphan_after = next(project for project in after if project["project_key"] == "proj:orphan")
        self.assertEqual(orphan_after["collection_observations"], [])

    def test_collection_import_does_not_attach_by_service_host_family(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:bakery", "service_url", "https://bakery.minomobi.com")
            registry.add_manual_seed(project, "service_url", "https://bakery.minomobi.com", source="test")
            project_record.save_project(conn, project)

            result = registry.import_collection_inventory_payload(
                conn,
                [
                    {
                        "collection": "com.minomobi.hoop.story.pulse",
                        "count": 10,
                        "recent_count": 2,
                        "min": "2026-07-01T00:00:00+00:00",
                        "max": "2026-07-04T00:00:00+00:00",
                    }
                ],
                execute=True,
                create_unmatched=True,
                source="test_collection_inventory",
            )
            fetched = project_record.fetch_project(conn, "proj:bakery")
        finally:
            conn.close()

        assert fetched is not None
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(fetched["input_nsids"], [])

    def test_semble_subpage_attaches_as_evidence_without_seed(self) -> None:
        conn = registry.open_db()
        original_tracked_collections = registry.SEMBLE_TRACKED_COLLECTIONS
        try:
            project = self.make_project("proj:cred", "service_url", "https://cred.blue")
            registry.add_manual_seed(project, "service_url", "https://cred.blue", source="test")
            project_record.save_project(conn, project)
            registry.SEMBLE_TRACKED_COLLECTIONS = (
                {"handle": "example.test", "rkey": "collection", "label": "apps"},
            )
            scan = {
                "source_key": "example.test/collection",
                "results": [],
                "evidence_only": [
                    {
                        "evidence_url": "https://cred.blue/gov.glados.computer",
                        "root_service_url": "https://cred.blue",
                        "reason": "semble_service_subpage",
                        "card_uri": "at://did:plc:example/network.cosmik.card/card123",
                        "card_updated_at": "2026-06-01T00:00:00Z",
                        "card_created_at": "2026-05-30T00:00:00Z",
                        "title": "cred.blue",
                        "description": "Score page",
                        "collection_label": "apps",
                        "collection_rkey": "collection",
                    }
                ],
                "pages": 1,
                "fetched_cards": 1,
                "skipped_cards": 0,
                "ordered_desc": True,
                "boundary_reached": False,
                "scan_complete": True,
                "newest_timestamp": "2026-06-01T00:00:00+00:00",
            }
            with mock.patch.object(source_sync, "fetch_semble_collection_source_sync_results", return_value=scan):
                result = source_sync.sync_semble_collections(
                    conn,
                    execute=True,
                    create_unmatched=True,
                    pages=1,
                    per_page=20,
                    lookback_days=None,
                    incremental=False,
                )
            fetched = project_record.fetch_project(conn, "proj:cred")
        finally:
            registry.SEMBLE_TRACKED_COLLECTIONS = original_tracked_collections
            conn.close()

        assert fetched is not None
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["evidence_only_attached"], 1)
        self.assertIn("https://cred.blue/gov.glados.computer", fetched["evidence_urls"])
        self.assertIn("at://did:plc:example/network.cosmik.card/card123", fetched["evidence_urls"])
        self.assertNotIn("https://cred.blue/gov.glados.computer", [item["value"] for item in fetched["manual_seeds"]])
        self.assertNotIn("https://cred.blue/gov.glados.computer", fetched["service_url_candidates"])

    def test_semble_candidate_service_match_is_suggestion_only(self) -> None:
        conn = registry.open_db()
        original_tracked_collections = registry.SEMBLE_TRACKED_COLLECTIONS
        try:
            project = self.make_project("proj:candidate", "repo_url", "https://github.com/example/candidate")
            project_record.set_service_url_candidate_values(project, ["https://candidate.example"])
            project_record.save_project(conn, project)
            registry.SEMBLE_TRACKED_COLLECTIONS = (
                {"handle": "example.test", "rkey": "collection", "label": "apps"},
            )
            scan = {
                "source_key": "example.test/collection",
                "results": [{
                    "seed_type": "service_url",
                    "seed_value": "https://candidate.example",
                    "raw_url": "https://candidate.example",
                    "card_uri": "at://did:plc:example/network.cosmik.card/root",
                    "card_updated_at": "2026-06-01T00:00:00Z",
                    "card_created_at": "2026-05-30T00:00:00Z",
                    "title": "Candidate",
                    "description": "Candidate root",
                    "collection_label": "apps",
                    "collection_rkey": "collection",
                }],
                "evidence_only": [{
                    "evidence_url": "https://candidate.example/detail",
                    "root_service_url": "https://candidate.example",
                    "reason": "semble_service_subpage",
                    "card_uri": "at://did:plc:example/network.cosmik.card/detail",
                    "card_updated_at": "2026-06-01T00:00:00Z",
                    "card_created_at": "2026-05-30T00:00:00Z",
                    "title": "Candidate detail",
                    "description": "Candidate detail page",
                    "collection_label": "apps",
                    "collection_rkey": "collection",
                }],
                "pages": 1,
                "fetched_cards": 2,
                "skipped_cards": 0,
                "ordered_desc": True,
                "boundary_reached": False,
                "scan_complete": True,
                "newest_timestamp": "2026-06-01T00:00:00+00:00",
            }
            with mock.patch.object(source_sync, "fetch_semble_collection_source_sync_results", return_value=scan):
                result = source_sync.sync_semble_collections(
                    conn,
                    execute=True,
                    create_unmatched=False,
                    pages=1,
                    per_page=20,
                    lookback_days=None,
                    incremental=False,
                )
            fetched = project_record.fetch_project(conn, "proj:candidate")
        finally:
            registry.SEMBLE_TRACKED_COLLECTIONS = original_tracked_collections
            conn.close()

        assert fetched is not None
        self.assertEqual(result["matched_existing"], 0)
        self.assertEqual(result["unmatched"], 1)
        self.assertEqual(result["evidence_only_attached"], 0)
        self.assertEqual(result["evidence_only_unmatched"], 1)
        self.assertEqual(result["suggested_existing_matches"], 2)
        self.assertTrue(all(item["suggested_project_key"] == "proj:candidate" for item in result["suggested_entries"]))
        self.assertNotIn("https://candidate.example/detail", fetched["evidence_urls"])
        self.assertNotIn(
            "https://candidate.example",
            [item["value"] for item in fetched["manual_seeds"]],
        )

    def test_sync_state_roundtrip_is_scoped(self) -> None:
        conn = registry.open_db()
        try:
            first_scope = {"query": "first"}
            second_scope = {"query": "second"}
            registry.write_sync_state(conn, "introduced", first_scope, {"timestamp": "2026-06-01T00:00:00+00:00"})
            conn.commit()

            first = registry.read_sync_state(conn, "introduced", first_scope)
            second = registry.read_sync_state(conn, "introduced", second_scope)
        finally:
            conn.close()

        self.assertEqual(first["watermark"]["timestamp"], "2026-06-01T00:00:00+00:00")
        self.assertEqual(second["watermark"], {})
        self.assertNotEqual(first["scope_key"], second["scope_key"])

    def test_introduced_sync_retains_unmatched_entry_for_later_project(self) -> None:
        conn = registry.open_db()
        query = "from:example.test #atproto"
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        post = {"uri": "at://did:plc:example/app.bsky.feed.post/abc", "author": {"handle": "example.test"},
                "indexedAt": timestamp, "record": {"createdAt": timestamp,
                "text": "Example", "facets": [{"index": {"byteStart": 0, "byteEnd": 7}, "features": [
                    {"$type": "app.bsky.richtext.facet#link", "uri": "https://github.com/example/post"}]}]}}
        seen_since: list[str] = []

        def fake_search(*args, **kwargs):
            seen_since.append(str(kwargs.get("since") or ""))
            return {"posts": [post]}

        try:
            with mock.patch.object(source_sync, "search_bsky_posts_payload", side_effect=fake_search):
                first = source_sync.sync_introduced_entries(conn, query=query, execute=True, lookback_days=7)

            state = registry.read_sync_state(conn, "introduced", {"query": query})
            self.assertEqual(first["unmatched"], 1)
            self.assertEqual(len(state["watermark"]["review_items"]), 1)
            self.assertEqual(state["watermark"]["review_items"][0]["reason"], "unmatched")
            self.assertTrue(seen_since[0])

            project = self.make_project("proj:introduced01", "repo_url", "https://github.com/example/post")
            registry.add_manual_seed(project, "repo_url", "https://github.com/example/post", source="test")
            project_record.save_project(conn, project)
            conn.commit()

            with mock.patch.object(source_sync, "search_bsky_posts_payload", return_value={"posts": []}):
                second = source_sync.sync_introduced_entries(conn, query=query, execute=True, lookback_days=7)
            saved = project_record.fetch_project(conn, "proj:introduced01")
            state = registry.read_sync_state(conn, "introduced", {"query": query})
        finally:
            conn.close()

        self.assertEqual(second["entries"], 1)
        self.assertEqual(second["pending_after"], 0)
        self.assertEqual(saved["introduced_url"], "https://bsky.app/profile/example.test/post/abc")
        self.assertEqual(state["watermark"]["review_items"][0]["status"], "auto_applied")

    def test_introduced_dry_run_does_not_write_sync_state(self) -> None:
        conn = registry.open_db()
        query = "dry-run"
        try:
            with mock.patch.object(source_sync, "search_bsky_posts_payload", return_value={"posts": []}):
                summary = source_sync.sync_introduced_entries(conn, query=query, execute=False)
            state = registry.read_sync_state(conn, "introduced", {"query": query})
        finally:
            conn.close()

        self.assertFalse(summary["state_updated"])
        self.assertEqual(state["watermark"], {})

    def test_introduced_progress_reports_pages_without_treating_safety_limit_as_total(self) -> None:
        conn = registry.open_db()
        seen: list[tuple[int, int | None, dict]] = []
        post = {"indexedAt": "2026-06-10T00:00:00Z", "record": {"createdAt": "2026-06-10T00:00:00Z"}}
        try:
            with mock.patch.object(
                source_sync,
                "search_bsky_posts_payload",
                return_value={"posts": [post]},
            ), mock.patch.object(source_sync, "collect_applog_entries", return_value={}):
                summary = source_sync.sync_introduced_entries(
                    conn,
                    query="progress-contract",
                    execute=False,
                    max_pages=100,
                    progress=lambda current, total, item: seen.append((current, total, item)),
                )
        finally:
            conn.close()

        self.assertEqual(summary["pages"], 1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][:2], (1, None))
        self.assertEqual(seen[0][2]["unit"], "page")

    def test_github_no_create_keeps_watermark_when_unmatched_remains(self) -> None:
        conn = registry.open_db()
        query = "atproto"
        scan = {
            "query": query,
            "results": [
                {
                    "url": "https://github.com/example/new-project",
                    "full_name": "example/new-project",
                    "description": "",
                    "homepage": "",
                    "updated_at": "2026-06-10T00:00:00Z",
                    "stargazers_count": 0,
                    "query": query,
                }
            ],
            "pages": 1,
            "boundary_reached": True,
            "scan_complete": True,
            "newest_timestamp": "2026-06-10T00:00:00+00:00",
        }
        try:
            with mock.patch.object(source_sync, "fetch_github_search_query_sync_results", return_value=scan):
                summary = source_sync.sync_github_search(
                    conn,
                    queries=[query],
                    execute=True,
                    create_unmatched=False,
                    pages=1,
                    per_page=10,
                    lookback_days=10,
                )
            state = registry.read_sync_state(conn, "github-search", {"queries": [query]})
        finally:
            conn.close()

        self.assertEqual(summary["unmatched"], 1)
        self.assertFalse(summary["state_updated"])
        self.assertEqual(state["watermark"], {})

    def test_github_homepage_exact_is_suggestion_without_reverse_source_relation(self) -> None:
        self.assertFalse(registry.sync_match_is_auto_attach_safe("homepage_exact"))
        conn = registry.open_db()
        query = "atproto"
        repo_url = "https://github.com/example/one-way"
        service_url = "https://one-way.example.com"
        project = self.make_project("proj:oneway", "service_url", service_url)
        registry.add_manual_seed(project, "service_url", service_url, source="test")
        project_record.save_project(conn, project)
        scan = {
            "query": query,
            "results": [{
                "url": repo_url,
                "full_name": "example/one-way",
                "description": "",
                "homepage": service_url,
                "updated_at": "2026-06-10T00:00:00Z",
                "stargazers_count": 0,
                "query": query,
            }],
            "pages": 1,
            "boundary_reached": True,
            "scan_complete": True,
            "newest_timestamp": "2026-06-10T00:00:00+00:00",
        }
        try:
            with mock.patch.object(source_sync, "fetch_github_search_query_sync_results", return_value=scan):
                summary = source_sync.sync_github_search(
                    conn,
                    queries=[query],
                    execute=True,
                    create_unmatched=False,
                    pages=1,
                    per_page=10,
                    lookback_days=10,
                )
            saved = project_record.fetch_project(conn, "proj:oneway")
        finally:
            conn.close()

        assert saved is not None
        self.assertEqual(summary["matched_existing"], 0)
        self.assertEqual(summary["suggested_existing_matches"], 1)
        self.assertEqual(summary["suggested_entries"][0]["suggested_match_kind"], "homepage_exact_confirmed")
        self.assertEqual(summary["suggested_entries"][0]["suggested_project_keys"], ["proj:oneway"])
        self.assertNotIn(repo_url, saved["project_repository_urls"])
        self.assertNotIn(repo_url, saved["candidate_project_repository_urls"])

    def test_github_homepage_exact_uses_shared_trust_policy_for_mutual_relation(self) -> None:
        conn = registry.open_db()
        query = "atproto"
        repo_url = "https://github.com/example/mutual"
        service_url = "https://mutual.example.com"
        project = self.make_project("proj:mutual", "service_url", service_url)
        registry.add_manual_seed(project, "service_url", service_url, source="test")
        project["relation_evidence"] = relations.normalize_relation_records([{
            "relation": {
                "subject": {"type": "service", "value": service_url},
                "predicate": "project_repository",
                "object": {"type": "repo", "value": repo_url},
                "locator": service_url,
                "method": "first_party_link",
            },
            "source": f"service_page:{service_url}",
            "classification": "confirmed",
            "observed_at": "2026-06-09T00:00:00+00:00",
            "context": {"link_text": "Source code"},
        }])
        project_record.save_project(conn, project)
        scan = {
            "query": query,
            "results": [{
                "url": repo_url,
                "full_name": "example/mutual",
                "description": "",
                "homepage": service_url,
                "updated_at": "2026-06-10T00:00:00Z",
                "stargazers_count": 0,
                "query": query,
            }],
            "pages": 1,
            "boundary_reached": True,
            "scan_complete": True,
            "newest_timestamp": "2026-06-10T00:00:00+00:00",
        }
        try:
            with mock.patch.object(source_sync, "fetch_github_search_query_sync_results", return_value=scan):
                summary = source_sync.sync_github_search(
                    conn,
                    queries=[query],
                    execute=True,
                    create_unmatched=False,
                    pages=1,
                    per_page=10,
                    lookback_days=10,
                )
            saved = project_record.fetch_project(conn, "proj:mutual")
            adoption = conn.execute(
                """
                SELECT source, registration_origin
                FROM project_identifier_adoptions
                WHERE project_key='proj:mutual' AND kind='repo_url' AND value=? AND cleared_at=''
                """,
                (repo_url,),
            ).fetchone()
        finally:
            conn.close()

        assert saved is not None
        assert adoption is not None
        self.assertEqual(summary["matched_existing"], 1)
        self.assertEqual(summary["match_reasons"], {"homepage_exact_trust_policy": 1})
        self.assertEqual(summary["suggested_existing_matches"], 0)
        self.assertIn(repo_url, saved["project_repository_urls"])
        self.assertNotIn(repo_url, [item["value"] for item in saved["manual_seeds"]])
        self.assertEqual((str(adoption["source"]), bool(adoption["registration_origin"])), ("trust_policy", False))

    def test_github_homepage_exact_does_not_choose_between_ambiguous_candidates(self) -> None:
        conn = registry.open_db()
        query = "atproto"
        repo_url = "https://github.com/example/ambiguous"
        service_url = "https://ambiguous.example.com"
        for project_key in ("proj:ambiguous1", "proj:ambiguous2"):
            project = self.make_project(project_key, "repo_url", f"https://github.com/example/{project_key}")
            project_record.set_service_url_candidate_values(project, [service_url])
            project_record.save_project(conn, project)
        scan = {
            "query": query,
            "results": [{
                "url": repo_url,
                "full_name": "example/ambiguous",
                "description": "",
                "homepage": service_url,
                "updated_at": "2026-06-10T00:00:00Z",
                "stargazers_count": 0,
                "query": query,
            }],
            "pages": 1,
            "boundary_reached": True,
            "scan_complete": True,
            "newest_timestamp": "2026-06-10T00:00:00+00:00",
        }
        try:
            with mock.patch.object(source_sync, "fetch_github_search_query_sync_results", return_value=scan):
                summary = source_sync.sync_github_search(
                    conn,
                    queries=[query],
                    execute=True,
                    create_unmatched=False,
                    pages=1,
                    per_page=10,
                    lookback_days=10,
                )
        finally:
            conn.close()

        self.assertEqual(summary["matched_existing"], 0)
        self.assertEqual(summary["suggested_existing_matches"], 1)
        suggestion = summary["suggested_entries"][0]
        self.assertIsNone(suggestion["suggested_project_key"])
        self.assertEqual(
            suggestion["suggested_project_keys"],
            ["proj:ambiguous1", "proj:ambiguous2"],
        )
        self.assertEqual(suggestion["suggested_match_kind"], "homepage_exact_candidate_ambiguous")

    def test_github_create_unmatched_keeps_operator_notes_empty(self) -> None:
        conn = registry.open_db()
        query = "atproto"
        repo_url = "https://github.com/example/new-project"
        scan = {
            "query": query,
            "results": [
                {
                    "url": repo_url,
                    "full_name": "example/new-project",
                    "description": "",
                    "homepage": "",
                    "updated_at": "2026-06-10T00:00:00Z",
                    "stargazers_count": 12,
                    "query": query,
                }
            ],
            "pages": 1,
            "boundary_reached": True,
            "scan_complete": True,
            "newest_timestamp": "2026-06-10T00:00:00+00:00",
        }
        try:
            with mock.patch.object(source_sync, "fetch_github_search_query_sync_results", return_value=scan):
                summary = source_sync.sync_github_search(
                    conn,
                    queries=[query],
                    execute=True,
                    create_unmatched=True,
                    pages=1,
                    per_page=10,
                    lookback_days=10,
                )
            projects = registry.all_projects(conn)
        finally:
            conn.close()

        self.assertEqual(summary["created"], 1)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["notes"], "")
        self.assertIn("github_repo_search_sync", projects[0]["discovery_sources"])
        self.assertEqual(projects[0]["manual_seeds"], [])
        self.assertIn(repo_url, projects[0]["project_repository_urls"])

    def test_github_screen_failure_does_not_advance_watermark(self) -> None:
        conn = registry.open_db()
        query = "atproto"
        scan = {
            "query": query,
            "results": [
                {
                    "url": "https://github.com/example/screen-failure",
                    "full_name": "example/screen-failure",
                    "description": "",
                    "homepage": "",
                    "updated_at": "2026-06-10T00:00:00Z",
                    "stargazers_count": 0,
                    "query": query,
                }
            ],
            "pages": 1,
            "boundary_reached": True,
            "scan_complete": True,
            "newest_timestamp": "2026-06-10T00:00:00+00:00",
        }
        try:
            with mock.patch.object(source_sync, "fetch_github_search_query_sync_results", return_value=scan), mock.patch.object(
                source_sync,
                "screen_github_repo_sync_candidate",
                side_effect=RuntimeError("temporary failure"),
            ):
                summary = source_sync.sync_github_search(
                    conn,
                    queries=[query],
                    execute=True,
                    create_unmatched=True,
                    pages=1,
                    per_page=10,
                    lookback_days=10,
                    screen_unmatched=True,
                )
            state = registry.read_sync_state(conn, "github-search", {"queries": [query]})
        finally:
            conn.close()

        self.assertEqual(summary["screen_reason_counts"]["screen_failed"], 1)
        self.assertFalse(summary["state_updated"])
        self.assertEqual(state["watermark"], {})

    def test_add_configured_multi_tenant_suffix_updates_domain_overrides_and_cache(self) -> None:
        original_config_path = registry.CONFIG_PATH
        original_domain_overrides_path = domain_policy.DOMAIN_OVERRIDES_PATH
        original_suffixes = domain_policy._CONFIGURED_MULTI_TENANT_SUFFIXES
        try:
            sources_path = Path(self.tempdir.name) / "sources.json"
            sources_path.write_text('{"collection_inventory": {}}\n')
            domain_overrides_path = Path(self.tempdir.name) / "domain-overrides.json"
            domain_overrides_path.write_text('{"custom_multi_tenant_suffixes": ["existing.example"]}\n')
            registry.CONFIG_PATH = sources_path
            domain_policy.DOMAIN_OVERRIDES_PATH = domain_overrides_path
            domain_policy.reset_domain_config_cache()

            self.assertTrue(domain_policy.add_configured_multi_tenant_suffix("Example.COM."))
            self.assertFalse(domain_policy.add_configured_multi_tenant_suffix("example.com"))

            config = json.loads(domain_overrides_path.read_text())
            self.assertEqual(config["custom_multi_tenant_suffixes"], ["existing.example", "example.com"])
            self.assertIn("example.com", domain_policy.configured_multi_tenant_suffixes())
            self.assertEqual(json.loads(sources_path.read_text()), {"collection_inventory": {}})
        finally:
            registry.CONFIG_PATH = original_config_path
            domain_policy.DOMAIN_OVERRIDES_PATH = original_domain_overrides_path
            domain_policy._CONFIGURED_MULTI_TENANT_SUFFIXES = original_suffixes
            domain_policy.reset_domain_extractors()

    def test_source_config_cli_only_configures_suffix_after_confirmation(self) -> None:
        config_path = Path(self.tempdir.name) / "domain-overrides.json"
        args = cli.build_parser().parse_args(["audit-source-config", "example.test"])
        for answer, accepted in (("", False), ("n", False), (EOFError, False), ("y", True), (" YES ", True)):
            with self.subTest(answer=answer):
                config_path.write_text('{"custom_multi_tenant_suffixes": []}')
                try:
                    with (
                        mock.patch.object(domain_policy, "DOMAIN_OVERRIDES_PATH", config_path),
                        mock.patch.object(domain_policy, "_CONFIGURED_MULTI_TENANT_SUFFIXES", None),
                        captured_stdout() as output,
                        mock.patch("sys.stdin.isatty", return_value=True),
                        mock.patch("sys.stdout.isatty", return_value=True),
                        mock.patch("sys.stderr", new=io.StringIO()) as error,
                        mock.patch("builtins.input", return_value=answer,
                                   side_effect=EOFError if answer is EOFError else None) as prompt,
                    ):
                        self.assertEqual(args.func(args), 0)
                        prompt.assert_called_once()
                        self.assertIn("[y/N]", error.getvalue())
                        self.assertEqual(json.loads(output.getvalue())["suffix_added"], accepted)
                        self.assertEqual(json.loads(config_path.read_text())["custom_multi_tenant_suffixes"],
                                         ["example.test"] if accepted else [])
                finally:
                    domain_policy.reset_domain_extractors()

    def test_source_config_audit_suggests_multi_tenant_project_split(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:tenanttenant", "service_url", "https://foo.example.com")
            project["name"] = "Mixed tenants"
            project["website_url"] = "https://foo.example.com"
            project["service_urls"] = ["https://foo.example.com", "https://bar.example.com"]
            project["input_nsids"] = ["com.example.foo.post", "com.example.bar.post"]
            project_record.save_project(conn, project)
            conn.commit()

            suggestions = registry.build_source_config_split_suggestions(conn, suffixes=["example.com"])

            self.assertEqual(len(suggestions), 1)
            self.assertEqual(suggestions[0]["source_project_key"], "proj:tenanttenant")
            tenant_groups = {item["tenant"]: item for item in suggestions[0]["tenant_groups"]}
            self.assertEqual(set(tenant_groups), {"foo.example.com", "bar.example.com"})
            self.assertIn("https://foo.example.com", tenant_groups["foo.example.com"]["identifiers"]["service_urls"])
            self.assertTrue(tenant_groups["foo.example.com"]["suggested_split_commands"])
        finally:
            conn.close()

    def test_source_config_tenant_split_moves_group_atomically(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:tenanttenant", "service_url", "https://foo.example.com")
            registry.add_manual_seed(project, "service_url", "https://foo.example.com", source="test")
            registry.add_manual_seed(project, "service_url", "https://bar.example.com", source="test")
            registry.add_manual_seed(project, "nsid", "com.example.foo.post", source="test")
            registry.add_manual_seed(project, "nsid", "com.example.bar.post", source="test")
            project_record.save_project(conn, project)
            conn.commit()

            result = registry.split_project_tenant_group(
                conn,
                "proj:tenanttenant",
                "bar.example.com",
                {
                    "service_urls": ["https://bar.example.com"],
                    "nsids": ["com.example.bar.post"],
                    "authority_domains": [],
                },
            )

            source = project_record.fetch_project(conn, "proj:tenanttenant")
            target = project_record.fetch_project(conn, result["target_project_key"])
            self.assertNotIn("https://bar.example.com", source["service_urls"])
            self.assertNotIn("com.example.bar.post", source["resolved_nsids"])
            self.assertIn("https://bar.example.com", target["service_urls"])
            self.assertIn("com.example.bar.post", target["resolved_nsids"])
            self.assertIn(target["project_key"], source["separate_project_keys"])
            self.assertIn(source["project_key"], target["separate_project_keys"])
            blocked = {(item["type"], item["value"]) for item in source["blocked_identifiers"]}
            self.assertIn(("service_url", "https://bar.example.com"), blocked)
            self.assertIn(("nsid", "com.example.bar.post"), blocked)
        finally:
            conn.close()

    def test_interactive_source_config_audit_rechecks_project_after_each_split(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:threetenants", "service_url", "https://foo.example.com")
            for url in (
                "https://foo.example.com",
                "https://bar.example.com",
                "https://baz.example.com",
            ):
                registry.add_manual_seed(project, "service_url", url, source="test")
            project_record.save_project(conn, project)
            conn.commit()

            initial_suggestions = registry.build_source_config_split_suggestions(conn, suffixes=["example.com"])
            with (
                mock.patch("builtins.input", side_effect=["1", "1"]),
                mock.patch("sys.stdout", new=io.StringIO()),
            ):
                result = registry.review_source_config_split_suggestions(
                    conn,
                    ["example.com"],
                    initial_suggestions=initial_suggestions,
                )

            source = project_record.fetch_project(conn, "proj:threetenants")
            remaining_groups = registry.project_split_identifiers_by_tenant(source, "example.com")
            self.assertEqual(result["split_count"], 2)
            self.assertEqual(
                [item["tenant"] for item in result["split_results"]],
                ["bar.example.com", "baz.example.com"],
            )
            self.assertEqual(set(remaining_groups), {"foo.example.com"})
            self.assertEqual(result["remaining_suggestion_count"], 0)
        finally:
            conn.close()

    def test_promotion_queue_item_exposes_policy_projection(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:policyitem", "service_url", "https://example.com")
            project.update({
                "category": "project",
                "service_status": "live",
                "service_status_confidence": "low",
                "service_status_evidence": ["http_200"],
            })
            registry.confirm_service_seed(project, "https://example.com", source="test")
            project_record.save_project(conn, project)
            conn.commit()

            item = application_review.build_showcase_queue_item(conn, project["project_key"])

            self.assertIsNotNone(item)
            assert item is not None
            self.assertEqual(item["candidate_visibility"], {"group": "eligible", "reasons": []})
            self.assertEqual(item["service_evidence_group"], "needs_validation")
            self.assertEqual(item["priority_group"], "normal")
            self.assertEqual(item["sort_score"], item["score_breakdown"]["total"])
        finally:
            conn.close()

    def test_lowered_promotion_priority_sorts_after_normal_candidates(self) -> None:
        conn = registry.open_db()
        try:
            normal = self.make_project("proj:normalpriority", "service_url", "https://normal.example")
            normal.update({
                "category": "project",
                "service_status": "live",
                "service_status_confidence": "medium",
                "service_status_evidence": ["http_200", "login_surface_detected"],
            })
            registry.confirm_service_seed(normal, "https://normal.example", source="test")
            lowered = dict(normal)
            lowered["project_key"] = "proj:loweredpriority"
            lowered["website_url"] = "https://lowered.example"
            lowered["service_urls"] = ["https://lowered.example"]
            project_review.set_promotion_priority_on_project(
                lowered,
                lowered=True,
                reason="weak candidate",
            )
            project_record.save_project(conn, normal)
            project_record.save_project(conn, lowered)
            conn.commit()

            queue = application_review.promotion_queue(conn, limit=None)

            keys = [item["project_key"] for item in queue]
            self.assertLess(keys.index(normal["project_key"]), keys.index(lowered["project_key"]))
            lowered_item = next(item for item in queue if item["project_key"] == lowered["project_key"])
            self.assertEqual(lowered_item["priority_group"], "lowered")
            self.assertEqual(lowered_item["priority_reason"], "weak candidate")
        finally:
            conn.close()

    def test_save_project_splits_scalars_profile_state_and_normalized_children(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:aaaaaaaaaaaa", "service_url", "https://example.com")
            project["name"] = "Example App"
            project["website_url"] = "https://example.com"
            project["service_urls"] = ["https://example.com"]
            project["service_status"] = "live"
            project["summary"] = "Example summary"
            project["tagline"] = "Example tagline"
            project["name_candidates"] = [{"value": "Example App", "source": "service_title:https://example.com"}]
            project["service_status_confidence"] = "medium"
            project["service_status_evidence"] = ["http_200", "login_surface_detected"]
            project["supported_service_urls"] = ["https://example.com"]
            project["resolved_nsids"] = ["app.example.post"]
            project["technical_interest_level"] = "high"
            project["technical_interest_reasons"] = ["novel_design_pattern"]
            project["technical_interest_summary"] = "新しい設計パターン。"
            registry.add_resource_url(
                project,
                "https://example.com/docs",
                role="docs",
                source="service_page:https://example.com",
                confidence="confirmed",
            )
            project_review.defer_promotion_on_project(project, reason="中核機能が未実装")

            project_record.save_project(conn, project, enriched=True)
            conn.commit()

            row = conn.execute("SELECT * FROM projects WHERE project_key = ?", ("proj:aaaaaaaaaaaa",)).fetchone()
            self.assertEqual(row["name"], "Example App")
            self.assertEqual(row["website_url"], "https://example.com")
            self.assertEqual(row["service_status"], "live")
            self.assertEqual(row["tracking_state"], "active")
            self.assertTrue(row["last_enriched_at"])

            profile_payload = json.loads(row["profile_json"])
            state_payload = json.loads(row["state_json"])
            self.assertEqual(profile_payload["core"]["summary"], "Example summary")
            self.assertEqual(profile_payload["core"]["tagline"], "Example tagline")
            self.assertEqual(state_payload["evidence"]["name_candidates"][0]["value"], "Example App")
            self.assertEqual(state_payload["ops"]["service_status_confidence"], "medium")
            self.assertEqual(state_payload["ops"]["atproto_contribution_level"], "core")
            self.assertEqual(state_payload["ops"]["atproto_contribution_score"], 90)
            self.assertEqual(state_payload["ops"]["lexicon_scope"], "service_specific")
            self.assertEqual(state_payload["ops"]["technical_interest_level"], "high")
            self.assertEqual(state_payload["ops"]["technical_interest_score"], 70)
            self.assertNotIn("promotion_deferrals", state_payload["ops"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_decisions WHERE project_key = ?", (project["project_key"],)).fetchone()[0], 1)
            self.assertEqual(state_payload["evidence"]["supported_service_urls"], ["https://example.com"])
            self.assertEqual(state_payload["evidence"]["resource_url_records"][0]["role"], "docs")

            fetched = project_record.fetch_project(conn, "proj:aaaaaaaaaaaa")
            self.assertEqual(fetched["summary"], "Example summary")
            self.assertEqual(fetched["name_candidates"][0]["source"], "service_title:https://example.com")
            self.assertEqual(fetched["atproto_contribution_level"], "core")
            self.assertEqual(fetched["lexicon_scope"], "service_specific")
            self.assertEqual(fetched["technical_interest_reasons"], ["novel_design_pattern"])
            self.assertEqual(project_record.resource_url_values(fetched), ["https://example.com/docs"])
        finally:
            conn.close()

    def test_save_project_rebuilds_app_links_only_from_canonical_sources(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:applinkcleanup", "service_url", "https://example.com")
            project["website_url"] = "https://example.com"
            project["service_urls"] = ["https://example.com"]
            project["links"] = [
                {"uri": "https://example.com", "role": "website", "official": True},
                {"uri": "https://example.com/old-docs", "role": "docs", "official": True},
            ]
            registry.add_resource_url(
                project,
                "https://example.com/docs",
                role="docs",
                source="service_page:https://example.com",
                confidence="confirmed",
            )
            project_record.save_project(conn, project)

            stored = project_record.fetch_project(conn, "proj:applinkcleanup")
            self.assertEqual(
                stored["links"],
                [
                    {"uri": "https://example.com", "role": "website", "official": True},
                    {"uri": "https://example.com/docs", "role": "docs", "official": True},
                ],
            )
        finally:
            conn.close()

    def test_url_field_audit_reports_only_canonical_fields(self) -> None:
        conn = registry.open_db()
        try:
            first = self.make_project("proj:urlfieldaudit1", "service_url", "https://example.com")
            first["website_url"] = "https://example.com"
            first["service_urls"] = ["https://example.com"]
            first["service_url_candidates"] = ["https://candidate.example.com"]
            first["project_repository_url"] = "https://github.com/example/app"
            first["project_repository_urls"] = ["https://github.com/example/app"]
            project_record.save_project(conn, first)

            second = self.make_project("proj:urlfieldaudit2", "service_url", "https://broken.example")
            second["website_url"] = "https://broken.example"
            second["service_url_candidates"] = ["https://two.example"]
            project_record.save_project(conn, second)

            summary = registry.build_url_field_audit(conn)
            aliases = {(item["left"], item["right"]): item for item in summary["alias_relationships"]}
            anomalies = {item["kind"]: item for item in summary["anomalies"]}
            website_only_services = conn.execute(
                "SELECT COUNT(*) FROM project_identifiers "
                "WHERE project_key = ? AND kind = 'service_url' AND classification = 'confirmed'",
                (second["project_key"],),
            ).fetchone()[0]

            self.assertEqual(summary["project_count"], 2)
            self.assertTrue(all("migration" not in item for item in summary["field_inventory"]))
            self.assertNotIn(("website_url", "service_urls"), aliases)
            self.assertNotIn("website_url_missing_from_service_urls", anomalies)
            self.assertEqual(website_only_services, 0)
            self.assertNotIn("cleanup_candidates", summary)
        finally:
            conn.close()

    def test_save_project_preserves_last_enriched_at_on_non_enriched_update(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:bbbbbbbbbbbb", "nsid", "app.example.post")
            project["name"] = "Example App"
            project_record.save_project(conn, project, enriched=True)
            conn.commit()
            first_last_enriched = conn.execute(
                "SELECT last_enriched_at FROM projects WHERE project_key = ?",
                ("proj:bbbbbbbbbbbb",),
            ).fetchone()["last_enriched_at"]

            fetched = project_record.fetch_project(conn, "proj:bbbbbbbbbbbb")
            fetched["summary"] = "Updated without enrich"
            project_record.save_project(conn, fetched, enriched=False)
            conn.commit()
            second_last_enriched = conn.execute(
                "SELECT last_enriched_at FROM projects WHERE project_key = ?",
                ("proj:bbbbbbbbbbbb",),
            ).fetchone()["last_enriched_at"]

            self.assertEqual(second_last_enriched, first_last_enriched)
        finally:
            conn.close()

    def test_audit_does_not_treat_shared_website_as_shared_service(self) -> None:
        conn = registry.open_db()
        try:
            source = self.make_project("proj:cccccccccccc", "service_url", "https://example.com")
            source["name"] = "Example Source"
            source["website_url"] = "https://example.com"
            source["duplicate_candidates"] = []
            source["merge_recommendation"] = {}
            project_record.save_project(conn, source)

            target = self.make_project("proj:dddddddddddd", "service_url", "https://example.com")
            target["name"] = "Example Target"
            target["website_url"] = "https://example.com"
            target["duplicate_candidates"] = []
            target["merge_recommendation"] = {}
            project_record.save_project(conn, target)
            conn.commit()

            summary = registry.audit_and_repair_identifiers(conn)
            self.assertGreaterEqual(summary["refreshed_derived_project_count"], 1)
            self.assertEqual(summary["mode"], "dry_run")

            repaired = project_record.fetch_project(conn, "proj:cccccccccccc")
            self.assertEqual(repaired["duplicate_candidates"], [])
            self.assertEqual(repaired["merge_recommendation"], {})

            executed = registry.audit_and_repair_identifiers(conn, execute=True)
            self.assertEqual(executed["mode"], "execute")

            repaired = project_record.fetch_project(conn, "proj:cccccccccccc")
            duplicate_keys = {item["project_key"] for item in repaired["duplicate_candidates"]}
            self.assertIn("proj:dddddddddddd", duplicate_keys)
            self.assertEqual(repaired["merge_recommendation"]["merge_confidence"], "medium")
            self.assertFalse(repaired["merge_recommendation"]["auto_merge_ready"])
        finally:
            conn.close()

    def test_audit_persists_missing_atproto_contribution_fields(self) -> None:
        conn = registry.open_db()
        try:
            now = project_record.iso_now()
            conn.execute(
                """
                INSERT INTO projects (
                    project_key, name, website_url, service_status, tracking_state,
                    introduced_url, profile_json, state_json, first_seen_at, last_seen_at, last_enriched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "proj:legacycontri",
                    "Legacy Contribution",
                    "",
                    "unknown",
                    "active",
                    "",
                    "{}",
                    json.dumps({"evidence": {"atproto_contribution_level": "unknown"}}),
                    now,
                    now,
                    None,
                ),
            )
            conn.execute(
                "INSERT INTO project_identifiers(project_key, ordinal, kind, value, role, classification) VALUES (?, ?, ?, ?, ?, ?)",
                ("proj:legacycontri", 0, "nsid", "app.example.post", "owned_nsid", "candidate"),
            )
            conn.commit()

            summary = registry.audit_and_repair_identifiers(conn, execute=True)
            self.assertEqual(summary["mode"], "execute")

            row = conn.execute("SELECT state_json FROM projects WHERE project_key = ?", ("proj:legacycontri",)).fetchone()
            state_payload = json.loads(row["state_json"])
            self.assertEqual(state_payload["ops"]["atproto_contribution_level"], "optional")
            self.assertEqual(state_payload["ops"]["atproto_contribution_score"], 40)
        finally:
            conn.close()

    def test_review_queue_does_not_refresh_duplicate_candidates(self) -> None:
        conn = registry.open_db()
        original_compute = project_merge.compute_duplicate_candidates
        try:
            project = self.make_project("proj:cafecafecafe", "nsid", "app.example.post")
            project["name"] = "Candidate Service"
            project["service_url_candidates"] = ["https://candidate.example"]
            project_record.save_project(conn, project)
            conn.commit()

            def fail_compute(*args, **kwargs):
                raise AssertionError("review queue should use stored derived merge fields")

            project_merge.compute_duplicate_candidates = fail_compute
            queue = application_review.review_queue(conn, tracking_states={"active"}, limit=5)
            batch, next_index = application_review.build_review_queue_batch(conn, tracking_states={"active"}, batch_size=5)
            item = application_review.build_review_queue_item(conn, "proj:cafecafecafe")

            self.assertEqual(queue[0]["project_key"], "proj:cafecafecafe")
            self.assertEqual(batch[0]["project_key"], "proj:cafecafecafe")
            self.assertGreaterEqual(next_index, 1)
            self.assertEqual(item["project_key"], "proj:cafecafecafe")
        finally:
            project_merge.compute_duplicate_candidates = original_compute
            conn.close()

    def test_lightweight_review_shape_matches_full_review_signals(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:abababababab", "nsid", "app.example.post")
            project["name"] = "Review Candidate"
            project["manual_review_recommended"] = True
            project["manual_review_kind"] = "identity_resolution"
            project["manual_review_priority"] = "high"
            project["service_url_candidates"] = ["https://candidate.example"]
            project["candidate_project_repository_urls"] = ["https://github.com/example/candidate"]
            project["collection_observations"] = [{"recent_count": 2, "observed_at": "2026-05-25T00:00:00+00:00"}]
            project_record.save_project(conn, project)
            conn.commit()

            row = conn.execute("SELECT * FROM projects WHERE project_key = ?", ("proj:abababababab",)).fetchone()
            payload = project_record.project_from_row_payload(row)
            full = project_review.apply_promotion_state(project_record.ensure_project_shape(dict(payload)))
            light = project_review.apply_promotion_state(project_record.lightweight_review_project_shape(dict(payload)))
            full_signals = project_review.compute_review_signals(full, assume_shaped=True, apply_promotion=False)
            light_signals = project_review.compute_review_signals(light, assume_shaped=True, apply_promotion=False)

            self.assertEqual(
                [item["kind"] for item in light_signals],
                [item["kind"] for item in full_signals],
            )
            self.assertEqual(application_review.review_queue(conn, tracking_states={"active"}, limit=1)[0]["project_key"], "proj:abababababab")
        finally:
            conn.close()

    def test_review_queue_prioritizes_atproto_contribution_for_equal_signals(self) -> None:
        conn = registry.open_db()
        try:
            optional = self.make_project("proj:111111111111", "service_url", "https://optional.example")
            optional["name"] = "Optional"
            optional["manual_review_recommended"] = True
            optional["manual_review_kind"] = "identity_resolution"
            optional["manual_review_priority"] = "medium"
            optional["input_nsids"] = ["app.example.optional"]
            project_record.save_project(conn, optional)

            substantial = self.make_project("proj:222222222222", "service_url", "https://substantial.example")
            substantial["name"] = "Substantial"
            substantial["manual_review_recommended"] = True
            substantial["manual_review_kind"] = "identity_resolution"
            substantial["manual_review_priority"] = "medium"
            substantial["collection_observations"] = [{"collection": "app.example.substantial", "count": 5}]
            project_record.save_project(conn, substantial)
            conn.commit()

            queue = application_review.review_queue(conn, tracking_states={"active"}, limit=2)

            self.assertEqual([item["project_key"] for item in queue], ["proj:222222222222", "proj:111111111111"])
            self.assertEqual(queue[0]["atproto_contribution_level"], "substantial")
            self.assertGreater(queue[0]["sort_score"], queue[1]["sort_score"])
        finally:
            conn.close()

    def test_interactive_promotion_queue_applies_limit_after_global_sort(self) -> None:
        conn = registry.open_db()
        try:
            low = self.make_project("proj:111111111111", "service_url", "https://low.example")
            low.update(
                {
                    "name": "Low",
                    "category": "project",
                    "service_status": "live",
                    "service_status_confidence": "medium",
                    "summary": "Low summary",
                    "tagline": "Low tagline",
                }
            )
            registry.confirm_service_seed(low, "https://low.example", source="test")
            project_record.save_project(conn, low)

            high = self.make_project("proj:zzzzzzzzzzzz", "service_url", "https://high.example")
            high.update(
                {
                    "name": "High",
                    "category": "project",
                    "service_status": "live",
                    "service_status_confidence": "medium",
                    "summary": "High summary",
                    "tagline": "High tagline",
                    "resolved_nsids": ["app.high.post"],
                }
            )
            registry.confirm_service_seed(high, "https://high.example", source="test")
            project_record.save_project(conn, high)
            conn.commit()

            args = cli.build_parser().parse_args(["promotion-queue", "--interactive", "--limit", "1"])
            with (
                mock.patch.object(registry, "open_db", return_value=conn),
                mock.patch.object(queue_interaction, "output_project_queue", return_value=0) as output,
            ):
                review_commands.cmd_promotion_queue(args)

            queue = output.call_args.kwargs["queue"]
            self.assertEqual([item["project_key"] for item in queue], ["proj:zzzzzzzzzzzz"])
            self.assertIsNone(output.call_args.kwargs["load_more"])
        finally:
            conn.close()

    def test_interactive_promotion_queue_can_hold_item_for_seven_days(self) -> None:
        conn = registry.open_db()
        try:
            project = self.make_project("proj:promotionhold", "service_url", "https://example.com")
            project.update({
                "category": "project",
                "service_status": "live",
                "service_status_confidence": "medium",
            })
            registry.confirm_service_seed(project, "https://example.com", source="test")
            project_record.save_project(conn, project)
            conn.commit()
            queue = application_review.promotion_queue(conn, tracking_states={"active"})
            stdout = io.StringIO()
            stderr = io.StringIO()
            stdout.isatty = lambda: True

            with (
                mock.patch.object(sys, "stdout", stdout),
                mock.patch.object(sys, "stderr", stderr),
                mock.patch.object(sys.stdin, "isatty", return_value=True),
                mock.patch("builtins.input", side_effect=["h", "中核機能が未実装"]),
            ):
                queue_interaction.output_project_queue(
                    conn,
                    queue_name="promotion",
                    queue=queue,
                    tracking_states={"active"},
                    kinds=None,
                    interactive=True,
                )

            fetched = project_record.fetch_project(conn, "proj:promotionhold")
            active = project_review.active_promotion_deferral(fetched)
            self.assertEqual(active["reason"], "中核機能が未実装")
            self.assertGreater(
                project_record.parse_iso(active["expires_at"]),
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=6),
            )
        finally:
            conn.close()

    def test_audit_reports_confirmed_identifier_collisions(self) -> None:
        conn = registry.open_db()
        try:
            first = self.make_project("proj:eeeeeeeeeeee", "service_url", "https://collision.example")
            first["name"] = "First"
            first["website_url"] = "https://collision.example"
            first["service_urls"] = ["https://collision.example"]
            project_record.set_service_url_candidate_values(first, ["https://shared-candidate.example"])
            project_record.save_project(conn, first)

            second = self.make_project("proj:ffffffffffff", "service_url", "https://collision.example")
            second["name"] = "Second"
            second["website_url"] = "https://collision.example"
            second["service_urls"] = ["https://collision.example"]
            project_record.set_service_url_candidate_values(second, ["https://shared-candidate.example"])
            project_record.save_project(conn, second)
            conn.commit()

            summary = registry.audit_and_repair_identifiers(conn)
            collisions = {
                (item["identifier_type"], item["identifier_value"]): set(item["project_keys"])
                for item in summary["collisions"]
            }

            self.assertIn(("service_url", "https://collision.example"), collisions)
            self.assertEqual(
                collisions[("service_url", "https://collision.example")],
                {"proj:eeeeeeeeeeee", "proj:ffffffffffff"},
            )
            self.assertNotIn(("service_url_candidate", "https://shared-candidate.example"), collisions)
        finally:
            conn.close()

    def test_audit_counts_provider_repo_collision_once(self) -> None:
        conn = registry.open_db()
        try:
            repo_url = "https://github.com/example/shared"
            first = self.make_project("proj:eeeeeeeeeeee", "repo_url", repo_url)
            first["project_repository_urls"] = [repo_url]
            project_record.save_project(conn, first)

            second = self.make_project("proj:ffffffffffff", "repo_url", repo_url)
            second["project_repository_urls"] = [repo_url]
            project_record.save_project(conn, second)
            conn.commit()

            summary = registry.audit_and_repair_identifiers(conn)
            matching = [
                item
                for item in summary["collisions"]
                if item["identifier_value"] == repo_url
            ]

            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["identifier_type"], "repo_url")
            self.assertEqual(
                set(matching[0]["project_keys"]),
                {"proj:eeeeeeeeeeee", "proj:ffffffffffff"},
            )
        finally:
            conn.close()

    def test_invalid_json_is_not_auto_repaired(self) -> None:
        conn = registry.open_db()
        try:
            now = project_record.iso_now()
            conn.execute(
                """
                INSERT INTO projects (
                    project_key, name, website_url, service_status, tracking_state,
                    introduced_url, profile_json, state_json, first_seen_at, last_seen_at, last_enriched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "proj:badbadbadbad",
                    "Broken",
                    "",
                    "unknown",
                    "active",
                    "",
                    "{bad json",
                    "{}",
                    now,
                    now,
                    None,
                ),
            )
            conn.commit()

            with self.assertRaises(json.JSONDecodeError):
                project_record.fetch_project(conn, "proj:badbadbadbad")
            with self.assertRaises(json.JSONDecodeError):
                registry.audit_and_repair_identifiers(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
