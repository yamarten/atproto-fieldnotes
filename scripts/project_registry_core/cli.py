from __future__ import annotations

import argparse
import sys
import urllib.error
from pathlib import Path

from . import registry
from .commands import app_record_validation as app_record_validation_commands
from .commands import decisions as decision_commands
from .commands import enrichment as enrichment_commands
from .commands import explain as explain_commands
from .commands import investigation as investigation_commands
from .commands import llm_evaluation as llm_evaluation_commands
from .commands import maintenance as maintenance_commands
from .commands import merge as merge_commands
from .commands import projects as project_commands
from .commands import review as review_commands
from .commands import sync as sync_commands
from project_registry_core import project_record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-registry",
        description="Register manual atproto project seeds and enrich project records.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-db", help="Explicitly initialize a schema v3 registry database.")
    init_parser.add_argument("--db-path", type=Path, help="SQLite DB path. Defaults to data/survey.db.")
    init_parser.set_defaults(func=maintenance_commands.cmd_init_db)

    add_parser = subparsers.add_parser("add-seed", help="Register one manual seed.")
    add_parser.add_argument("seed", help="NSID, repository URL, or service URL.")
    add_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Override automatic seed type detection.")
    add_parser.add_argument("-p", "--project-key", help="Attach the seed to an existing project key.")
    add_parser.set_defaults(func=project_commands.cmd_add_seed)

    enrich_parser = subparsers.add_parser("enrich", help="Enrich one or more projects.")
    enrich_group = enrich_parser.add_mutually_exclusive_group(required=True)
    enrich_group.add_argument("-p", "--project-key", help="Target one project key.")
    enrich_group.add_argument("--seed", help="Target by known seed value.")
    enrich_group.add_argument("--all", action="store_true", help="Enrich all registered projects.")
    enrich_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    enrich_parser.add_argument(
        "-A",
        "--all-tracking-states",
        action="store_true",
        help="When used with --all, ignore tracking_state filtering and enrich every project.",
    )
    enrich_parser.add_argument(
        "--tracking-states",
        nargs="+",
        choices=sorted(project_record.TRACKING_STATES),
        help="When used with --all, override the default tracking_state set explicitly.",
    )
    enrich_parser.add_argument("--include-stable", action="store_true", help="When used with --all, include projects marked stable.")
    enrich_parser.add_argument("--include-stalled", action="store_true", help="When used with --all, include projects marked stalled.")
    enrich_parser.add_argument("--include-out-of-scope", action="store_true", help="When used with --all, include projects currently outside the survey scope.")
    enrich_parser.add_argument("--include-archived", action="store_true", help="When used with --all, include projects marked archived.")
    enrich_parser.add_argument("--include-ignore", action="store_true", help="When used with --all, include projects marked ignore.")
    enrich_parser.add_argument("--new-only", action="store_true", help="When used with --all, enrich only projects that have never been enriched yet.")
    enrich_parser.add_argument("--retry-failures", action="store_true", help="When used with --all, enrich only projects whose previous enrich left last_run_errors.")
    enrich_parser.add_argument("--fail-fast", action="store_true", help="When used with --all, stop after the first failed project instead of continuing the bulk run.")
    enrich_parser.add_argument("--run-id", help="When used with --all, use this id for the resumable checkpoint file.")
    enrich_parser.add_argument("--resume-run", help="Resume a previous --all enrich run from its checkpoint run id.")
    enrich_parser.add_argument("--checkpoint-path", help="Override the enrich run checkpoint JSON path.")
    enrich_parser.add_argument("--no-checkpoint", action="store_true", help="When used with --all, disable resumable checkpoint writing.")
    enrich_parser.add_argument("--deep-investigation", action="store_true", help="Probe discovered resource/docs pages as additional evidence before LLM review.")
    enrich_parser.add_argument("--llm-endpoint", help="OpenAI-compatible LLM endpoint or base URL.")
    enrich_parser.add_argument("--llm-model", help="Model name for the LLM endpoint.")
    enrich_parser.add_argument("--llm-api-key", help="Optional API key for the LLM endpoint.")
    enrich_parser.add_argument("--llm-api-style", choices=["auto", "chat", "responses", "completions"], help="LLM endpoint API style preference. Default: auto.")
    enrich_parser.add_argument("--llm-reasoning-effort", choices=["none", "minimal", "low", "medium", "high"], help="Optional reasoning/thinking effort for LLM calls.")
    enrich_parser.add_argument("--llm-max-output-tokens", type=int, default=0, help="Maximum output tokens for each LLM request. Default: endpoint setting.")
    enrich_parser.add_argument("--llm-triage-prompt-suffix-file", help="Append model/configuration-specific triage rules from this UTF-8 file; the content hash is saved with the LLM run.")
    enrich_parser.set_defaults(func=enrichment_commands.cmd_enrich)

    show_parser = subparsers.add_parser("show", help="Show registered projects.")
    show_group = show_parser.add_mutually_exclusive_group(required=True)
    show_group.add_argument("-p", "--project-key", help="Target one project key.")
    show_group.add_argument("--seed", help="Target by known seed value.")
    show_group.add_argument("--all", action="store_true", help="Show all projects.")
    show_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    show_parser.add_argument("--tracking-states", nargs="+", choices=sorted(project_record.TRACKING_STATES), help="When used with --all, restrict output to these tracking states.")
    show_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    show_parser.set_defaults(func=project_commands.cmd_show)

    validate_app_record_parser = subparsers.add_parser(
        "validate-app-record-export",
        help="Write a read-only community.lexicon.app.entry readiness report.",
    )
    validate_app_record_parser.add_argument(
        "--db-path", required=True, help="Source schema v3 SQLite database opened read-only."
    )
    validate_app_record_parser.add_argument(
        "--output", required=True, help="JSON report path. Use a path under .tmp for audits."
    )
    validate_app_record_parser.set_defaults(
        func=app_record_validation_commands.cmd_validate_app_record_export
    )

    export_app_record_parser = subparsers.add_parser(
        "export-app-records",
        help="Export explicitly selected community.lexicon.app.entry records.",
    )
    export_app_record_parser.add_argument(
        "--db-path", required=True, help="Source schema v3 SQLite database opened read-only."
    )
    export_app_record_parser.add_argument(
        "--manifest", required=True, help="Publication manifest JSON path."
    )
    export_app_record_parser.add_argument(
        "--output", required=True, help="Validated multi-record JSON output path."
    )
    export_app_record_parser.set_defaults(
        func=app_record_validation_commands.cmd_export_app_records
    )

    explain_parser = subparsers.add_parser("explain", help="Explain saved registry conclusions without network or LLM access.")
    explain_parser.add_argument("-p", "--project-key", required=True, help="Target one project key.")
    explain_parser.add_argument("--topic", choices=["all", *explain_commands.TOPICS], default="all", help="Explanation topic. Default: all.")
    explain_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    explain_parser.set_defaults(func=explain_commands.cmd_explain)

    review_queue_parser = subparsers.add_parser("review-queue", help="List projects that likely deserve human review next.")
    review_queue_parser.add_argument("--tracking-states", nargs="+", choices=sorted(project_record.TRACKING_STATES), help="Restrict review queue to these tracking states. Default: active stable stalled.")
    review_queue_parser.add_argument("--kinds", nargs="+", choices=[
        "service_candidate_needs_confirmation",
        "candidate_repo_needs_confirmation",
        "llm_manual_review_recommended",
        "service_now_usable_but_needs_editorial_review",
        "more_evidence_than_current_classification",
        "stale_metadata_with_recent_activity",
        "service_status_unverified_but_promising",
        "category_recheck_needed",
        "external_lexicon_dependency_needs_review",
        "high_information_density",
        "many_identity_candidates",
    ], help="Restrict to specific review signal kinds.")
    review_queue_parser.add_argument("--limit", type=int, default=None, help="Maximum number of projects to return. Default: 50, or unlimited when --interactive is set.")
    review_queue_parser.add_argument("--interactive", action="store_true", help="Step through review items interactively in a TTY.")
    review_queue_parser.set_defaults(func=review_commands.cmd_review_queue)

    promotion_queue_parser = subparsers.add_parser("promotion-queue", help="List unintroduced projects that look worth showcasing next.")
    promotion_queue_parser.add_argument("--tracking-states", nargs="+", choices=sorted(project_record.TRACKING_STATES), help="Restrict promotion queue to these tracking states. Default: active.")
    promotion_queue_parser.add_argument("--kinds", nargs="+", choices=[
        "service_surface_confirmed",
        "service_candidate_needs_validation",
        "lexicon_publisher_confirmed",
        "technical_insight_candidate",
    ], help="Restrict to specific promotion signal kinds.")
    promotion_queue_parser.add_argument("--limit", type=int, default=None, help="Maximum number of projects to return. Default: 50, or unlimited when --interactive is set.")
    promotion_queue_parser.add_argument("--include-personal-tools", action="store_true", help="Include category=personal_tool in service and Lexicon publication candidates.")
    promotion_queue_parser.add_argument("--interactive", action="store_true", help="Step through promotion items interactively in a TTY.")
    promotion_queue_parser.set_defaults(func=review_commands.cmd_promotion_queue)

    review_stalled_parser = subparsers.add_parser("review-stalled", help="Suggest activity-based tracking-state changes for active and stalled projects.")
    review_stalled_parser.add_argument("--inactivity-days", type=int, default=45, help="Use this many days as the inactivity threshold for repository and collection activity. Default: 45.")
    review_stalled_parser.add_argument(
        "--max-enrich-age-days",
        dest="max_enrich_age_days",
        type=int,
        default=30,
        help="Only consider stalled downgrades when the project was enriched within this freshness window. Default: 30.",
    )
    review_stalled_parser.add_argument("--execute", action="store_true", help="Apply the suggested tracking-state updates. Without this flag, show a dry-run summary only.")
    review_stalled_parser.add_argument("--interactive", action="store_true", help="Step through activity-review candidates interactively and apply confirmed updates.")
    review_stalled_parser.add_argument("--note", help="Optional tracking note to store when applying updates.")
    review_stalled_parser.set_defaults(func=review_commands.cmd_review_stalled)

    review_ignore_parser = subparsers.add_parser("review-ignore", help="Suggest unrelated or identity-less noise that likely deserves tracking_state=ignore.")
    review_ignore_parser.add_argument("--tracking-states", nargs="+", choices=sorted(project_record.TRACKING_STATES), help="Restrict review-ignore to these tracking states. Default: active stable stalled.")
    review_ignore_parser.add_argument("--limit", type=int, default=None, help="Maximum number of candidates to return or apply. Default: 100, or unlimited when --interactive is set.")
    review_ignore_parser.add_argument("--execute", action="store_true", help="Apply the suggested tracking_state=ignore updates. Without this flag, show a dry-run summary only.")
    review_ignore_parser.add_argument("--interactive", action="store_true", help="Step through ignore candidates interactively and apply confirmed ignores.")
    review_ignore_parser.add_argument("--note", help="Optional tracking note to store when applying ignore updates.")
    review_ignore_parser.add_argument("--note-reason", choices=sorted(registry.IGNORE_REVIEW_NOTE_REASONS), help="Preset review-ignore note category to store with ignored projects.")
    review_ignore_parser.set_defaults(func=review_commands.cmd_review_ignore)

    review_scope_parser = subparsers.add_parser(
        "review-out-of-scope",
        help="Suggest related projects that are currently outside the survey scope but should remain re-evaluable.",
    )
    review_scope_parser.add_argument("--tracking-states", nargs="+", choices=sorted(project_record.TRACKING_STATES), help="Restrict review-out-of-scope to these tracking states. Default: active stable stalled out_of_scope.")
    review_scope_parser.add_argument("--limit", type=int, default=None, help="Maximum number of candidates to return or apply. Default: 100, or unlimited when --interactive is set.")
    review_scope_parser.add_argument("--execute", action="store_true", help="Apply suggested tracking_state=out_of_scope updates. Without this flag, show a dry-run summary only.")
    review_scope_parser.add_argument("--interactive", action="store_true", help="Step through out-of-scope candidates interactively and apply confirmed updates.")
    review_scope_parser.add_argument("--note", help="Optional tracking note to store when applying out-of-scope updates.")
    review_scope_parser.add_argument("--note-reason", choices=sorted(registry.OUT_OF_SCOPE_REVIEW_NOTE_REASONS), help="Preset reason category to store with out-of-scope projects.")
    review_scope_parser.set_defaults(func=review_commands.cmd_review_out_of_scope)

    seed_list_parser = subparsers.add_parser("import-seed-list", help="Import a mixed text seed list with headings, notes, and URLs.")
    seed_list_parser.add_argument("path", help="Path to a plain-text seed list.")
    seed_list_parser.add_argument("--execute", action="store_true", help="Apply imports. Without this flag, show a dry-run summary only.")
    seed_list_parser.set_defaults(func=sync_commands.cmd_import_seed_list)

    sync_introduced_parser = subparsers.add_parser("sync-introduced", help="Search Bluesky posts and mark matching projects as introduced.")
    sync_introduced_parser.add_argument("--query", help="Override the configured Bluesky post search query.")
    sync_introduced_parser.add_argument("--limit", type=int, help="Override the configured posts-per-page budget.")
    sync_introduced_parser.add_argument("--max-pages", type=int, help="Override the configured pagination safety limit.")
    sync_introduced_parser.add_argument("--lookback-days", type=int, help="Override the configured initial lookback.")
    sync_introduced_parser.add_argument("--incremental", action=argparse.BooleanOptionalAction, default=True, help="Resume from the last successful sync. Default: enabled; use --no-incremental to ignore stored state.")
    sync_introduced_parser.add_argument("--all-history", action="store_true", help="Ignore the saved watermark and initial lookback for this run.")
    sync_introduced_parser.add_argument("--execute", action="store_true", help="Apply introduced flags. Without this flag, show a dry-run summary only.")
    sync_introduced_parser.set_defaults(func=sync_commands.cmd_sync_introduced)

    sync_github_parser = subparsers.add_parser("sync-github-search", help="Search GitHub repositories and import unknown repo seeds.")
    sync_github_parser.add_argument("--query", action="append", help="Override one GitHub repository search query. Can be passed multiple times.")
    sync_github_parser.add_argument("--pages", type=int, help="How many GitHub search result pages to scan.")
    sync_github_parser.add_argument("--per-page", type=int, help="How many results to fetch per page.")
    sync_github_parser.add_argument("--lookback-days", type=int, help="Override the configured repository update lookback.")
    sync_github_parser.add_argument("--all-history", action="store_true", help="Disable updated_at lookback and inspect every fetched page result once.")
    sync_github_parser.add_argument("--incremental", action=argparse.BooleanOptionalAction, default=True, help="Resume each query from its last successful sync. Default: enabled.")
    sync_github_parser.add_argument("--execute", action="store_true", help="Apply imports. Without this flag, show a dry-run summary only.")
    sync_github_parser.add_argument("--create-unmatched", action=argparse.BooleanOptionalAction, default=True, help="Create new projects for unknown GitHub repos. Default: enabled; use --no-create-unmatched to list only.")
    sync_github_parser.add_argument("--screen-unmatched", action="store_true", help="Before creating unmatched repos, fetch repo metadata/README/tree and keep only entries with atproto-related signals.")
    sync_github_parser.set_defaults(func=sync_commands.cmd_sync_github_search)

    sync_collections_parser = subparsers.add_parser("sync-collections", help="Fetch the collection inventory URL and import it.")
    sync_collections_parser.add_argument("--source-url", help="Override the collection inventory URL from config.")
    sync_collections_parser.add_argument("--execute", action="store_true", help="Apply imports. Without this flag, show a dry-run summary only.")
    sync_collections_parser.add_argument("--create-unmatched", action=argparse.BooleanOptionalAction, default=True, help="Create new projects for unmatched imported collections. Default: enabled; use --no-create-unmatched to list only.")
    sync_collections_parser.add_argument("--lookback-days", type=int, help="Override the configured collection activity lookback.")
    sync_collections_parser.add_argument("--incremental", action=argparse.BooleanOptionalAction, default=True, help="Use the last successful inventory watermark. Default: enabled.")
    sync_collections_parser.add_argument("--all-history", action="store_true", help="Ignore the saved watermark and initial lookback for unknown collections.")
    sync_collections_parser.add_argument("--save-path", type=Path, help="Optional path to save the fetched JSON payload.")
    sync_collections_parser.add_argument("--max-items", type=int, help="Bound the number of fetched entries processed; a truncated run does not advance sync state.")
    sync_collections_parser.set_defaults(func=sync_commands.cmd_sync_collections)

    sync_bluesky_lexicons_parser = subparsers.add_parser(
        "sync-bluesky-lexicons",
        help="Import official app.bsky and chat.bsky record lexicons into the confirmed Bluesky project.",
    )
    sync_bluesky_lexicons_parser.add_argument("--execute", action="store_true", help="Apply owned NSIDs. Without this flag, show a dry-run summary only.")
    sync_bluesky_lexicons_parser.set_defaults(func=sync_commands.cmd_sync_bluesky_lexicons)

    sync_semble_parser = subparsers.add_parser("sync-semble-collections", help="Sync fixed Semble collections into project seeds.")
    sync_semble_parser.add_argument("--pages", type=int, help="Override the configured Semble pages-per-collection budget.")
    sync_semble_parser.add_argument("--per-page", type=int, help="Override the configured cards-per-page budget.")
    sync_semble_parser.add_argument("--lookback-days", type=int, help="Override the configured card update lookback.")
    sync_semble_parser.add_argument("--all-history", action="store_true", help="Disable updated_at lookback and inspect every fetched page result once.")
    sync_semble_parser.add_argument("--incremental", action=argparse.BooleanOptionalAction, default=True, help="Resume each tracked collection from its last successful sync. Default: enabled.")
    sync_semble_parser.add_argument("--execute", action="store_true", help="Apply imports. Without this flag, show a dry-run summary only.")
    sync_semble_parser.add_argument("--create-unmatched", action=argparse.BooleanOptionalAction, default=True, help="Create new projects for unknown seeds discovered from Semble collection cards. Default: enabled; use --no-create-unmatched to list only.")
    sync_semble_parser.set_defaults(func=sync_commands.cmd_sync_semble_collections)

    sync_tangled_parser = subparsers.add_parser("sync-tangled-repos", help="Sync Tangled repository events from a Jetstream v2 snapshot.")
    sync_tangled_parser.add_argument("--execute", action="store_true", help="Apply imports. Without this flag, show a dry-run summary only.")
    sync_tangled_parser.add_argument("--create-unmatched", action=argparse.BooleanOptionalAction, default=True, help="Create projects for unmatched atproto-relevant repos. Default: enabled.")
    sync_tangled_parser.add_argument("--incremental", action=argparse.BooleanOptionalAction, default=True, help="Resume after the saved Jetstream v2 sequence. Default: enabled.")
    sync_tangled_parser.add_argument("--all-history", action="store_true", help="Ignore the saved sequence and replay the full filtered archive.")
    sync_tangled_parser.add_argument("--max-items", type=int, help="Bound archive events per run. A truncated execute run resumes after the last processed sequence.")
    sync_tangled_parser.set_defaults(func=sync_commands.cmd_sync_tangled_repos)

    replay_parser = subparsers.add_parser("replay-investigation", help="Replay a saved investigation without collectors or network access.")
    replay_target = replay_parser.add_mutually_exclusive_group(required=True)
    replay_target.add_argument("--run-id", help="Replay one saved investigation run.")
    replay_target.add_argument("-p", "--project-key", help="Replay the latest saved run for one project.")
    replay_parser.set_defaults(func=investigation_commands.cmd_replay_investigation)

    benchmark_parser = subparsers.add_parser("benchmark-investigation", help="Replay an investigation fixture corpus and report semantic regressions.")
    benchmark_parser.add_argument("--path", default=str(registry.ROOT / "tests" / "fixtures" / "project_registry"))
    benchmark_parser.set_defaults(func=investigation_commands.cmd_benchmark_investigation)

    export_fixture_parser = subparsers.add_parser("export-investigation-fixture", help="Export a sanitized fixture from a saved investigation run.")
    export_fixture_parser.add_argument("--run-id", required=True)
    export_fixture_parser.add_argument("--case-id")
    export_fixture_parser.add_argument("--output")
    export_fixture_parser.set_defaults(func=investigation_commands.cmd_export_investigation_fixture)

    export_llm_corpus_parser = subparsers.add_parser(
        "export-llm-evaluation-corpus",
        help="Export fixed triage evidence from saved runs without modifying the database.",
    )
    export_llm_corpus_parser.add_argument("--db-path", required=True, help="Source SQLite DB opened read-only.")
    export_llm_corpus_parser.add_argument("--output", required=True, help="Output corpus JSON path.")
    export_llm_corpus_parser.add_argument("--limit", type=int, default=40, help="Maximum cases. Default: 40.")
    export_llm_corpus_parser.add_argument(
        "--library-min", type=int, default=8,
        help="Minimum category=library cases to prioritize when available. Default: 8.",
    )
    export_llm_corpus_parser.add_argument(
        "--category-min", type=int, default=2,
        help="Minimum cases per available reference category. Default: 2.",
    )
    export_llm_corpus_parser.set_defaults(func=llm_evaluation_commands.cmd_export_llm_evaluation_corpus)

    evaluate_llm_parser = subparsers.add_parser(
        "evaluate-llm-corpus",
        help="Run one model configuration against a fixed LLM evaluation corpus.",
    )
    evaluate_llm_parser.add_argument("--corpus", required=True, help="Fixed corpus JSON path.")
    evaluate_llm_parser.add_argument("--labels", help="Optional reviewed expected-output labels JSON path.")
    evaluate_llm_parser.add_argument("--prompt-variant", help="Prompt variant label recorded in the report.")
    evaluate_llm_parser.add_argument("--prompt-suffix-file", help="Append an evaluation-only instruction suffix from this file.")
    evaluate_llm_parser.add_argument(
        "--repository-analysis-report",
        help="Overlay repeat-zero normalized repository results from this complete report onto matching triage cases.",
    )
    evaluate_llm_parser.add_argument("--output", required=True, help="Structured report JSON path.")
    evaluate_llm_parser.add_argument("--case-id", action="append", help="Run only this case ID. Repeatable.")
    evaluate_llm_parser.add_argument("--repeat", type=int, default=1, help="Runs per case. Default: 1.")
    evaluate_llm_parser.add_argument(
        "--current-profile-mode", choices=["without", "with"], default="without",
        help="Exclude or include the saved current profile in triage evidence. Default: without.",
    )
    evaluate_llm_parser.add_argument("--output-cap", type=int, default=2048, help="Maximum output tokens. Default: 2048.")
    evaluate_llm_parser.add_argument("--runtime", help="Runner name/version recorded in the report.")
    evaluate_llm_parser.add_argument("--quantization", help="Quantization label recorded in the report.")
    evaluate_llm_parser.add_argument("--llm-endpoint", help="OpenAI-compatible LLM endpoint or base URL.")
    evaluate_llm_parser.add_argument("--llm-model", help="Model name for the LLM endpoint.")
    evaluate_llm_parser.add_argument("--llm-api-key", help="Optional API key for the LLM endpoint.")
    evaluate_llm_parser.add_argument("--llm-api-style", choices=["auto", "chat", "responses", "completions"], help="LLM endpoint API style preference.")
    evaluate_llm_parser.add_argument("--llm-reasoning-effort", choices=["none", "minimal", "low", "medium", "high"], help="Reasoning/thinking effort.")
    evaluate_llm_parser.add_argument(
        "--reasoning-observation", choices=["applied", "ignored", "not_checked"],
        help="What the runtime log or model configuration showed for the requested reasoning setting.",
    )
    evaluate_llm_parser.add_argument(
        "--reasoning-observation-note",
        help="Short non-secret evidence for the reasoning observation, such as a runtime log message.",
    )
    evaluate_llm_parser.set_defaults(func=llm_evaluation_commands.cmd_evaluate_llm_corpus)

    compare_llm_reports_parser = subparsers.add_parser(
        "compare-llm-evaluation-reports",
        help="Compare two complete reports without selecting a production model.",
    )
    compare_llm_reports_parser.add_argument("--baseline", required=True, help="Baseline report JSON path.")
    compare_llm_reports_parser.add_argument("--candidate", required=True, help="Candidate report JSON path.")
    compare_llm_reports_parser.add_argument("--output", required=True, help="Comparison JSON path.")
    compare_llm_reports_parser.set_defaults(func=llm_evaluation_commands.cmd_compare_llm_evaluation_reports)

    export_repository_analysis_parser = subparsers.add_parser(
        "export-repository-analysis-corpus",
        help="Export fixed repository analysis material from saved runs without modifying the database.",
    )
    export_repository_analysis_parser.add_argument("--db-path", required=True, help="Source SQLite DB opened read-only.")
    export_repository_analysis_parser.add_argument("--output", required=True, help="Output corpus JSON path.")
    export_repository_analysis_parser.add_argument("--limit", type=int, default=12, help="Maximum cases. Default: 12.")
    export_repository_analysis_parser.set_defaults(
        func=llm_evaluation_commands.cmd_export_repository_analysis_corpus
    )

    evaluate_repository_analysis_parser = subparsers.add_parser(
        "evaluate-repository-analysis-corpus",
        help="Run one model configuration against fixed repository analysis material.",
    )
    evaluate_repository_analysis_parser.add_argument("--corpus", required=True, help="Fixed corpus JSON path.")
    evaluate_repository_analysis_parser.add_argument("--output", required=True, help="Structured report JSON path.")
    evaluate_repository_analysis_parser.add_argument("--case-id", action="append", help="Run only this case ID. Repeatable.")
    evaluate_repository_analysis_parser.add_argument("--repeat", type=int, default=1, help="Runs per case. Default: 1.")
    evaluate_repository_analysis_parser.add_argument("--output-cap", type=int, default=2048, help="Maximum output tokens. Default: 2048.")
    evaluate_repository_analysis_parser.add_argument("--runtime", help="Runner name/version recorded in the report.")
    evaluate_repository_analysis_parser.add_argument("--quantization", help="Quantization label recorded in the report.")
    evaluate_repository_analysis_parser.add_argument("--llm-endpoint", help="OpenAI-compatible LLM endpoint or base URL.")
    evaluate_repository_analysis_parser.add_argument("--llm-model", help="Model name for the LLM endpoint.")
    evaluate_repository_analysis_parser.add_argument("--llm-api-key", help="Optional API key for the LLM endpoint.")
    evaluate_repository_analysis_parser.add_argument("--llm-api-style", choices=["auto", "chat", "responses", "completions"], help="LLM endpoint API style preference.")
    evaluate_repository_analysis_parser.add_argument("--llm-reasoning-effort", choices=["none", "minimal", "low", "medium", "high"], help="Reasoning/thinking effort.")
    evaluate_repository_analysis_parser.add_argument(
        "--reasoning-observation", choices=["applied", "ignored", "not_checked"],
        help="What the runtime log or model configuration showed for the requested reasoning setting.",
    )
    evaluate_repository_analysis_parser.add_argument(
        "--reasoning-observation-note",
        help="Short non-secret evidence for the reasoning observation, such as a runtime log message.",
    )
    evaluate_repository_analysis_parser.set_defaults(
        func=llm_evaluation_commands.cmd_evaluate_repository_analysis_corpus
    )

    compare_repository_analysis_parser = subparsers.add_parser(
        "compare-repository-analysis-reports",
        help="Compare two complete repository analysis reports without selecting a production model.",
    )
    compare_repository_analysis_parser.add_argument("--baseline", required=True, help="Baseline report JSON path.")
    compare_repository_analysis_parser.add_argument("--candidate", required=True, help="Candidate report JSON path.")
    compare_repository_analysis_parser.add_argument("--output", required=True, help="Comparison JSON path.")
    compare_repository_analysis_parser.set_defaults(
        func=llm_evaluation_commands.cmd_compare_repository_analysis_reports
    )

    export_relation_assessment_parser = subparsers.add_parser(
        "export-relation-assessment-corpus",
        help="Export current ASSESS-routed relation material from a registry database opened read-only.",
    )
    export_relation_assessment_parser.add_argument("--db-path", required=True, help="Source SQLite DB opened read-only.")
    export_relation_assessment_parser.add_argument("--output", required=True, help="Output corpus JSON path.")
    export_relation_assessment_parser.set_defaults(
        func=llm_evaluation_commands.cmd_export_relation_assessment_corpus
    )

    export_recollection_relation_assessment_parser = subparsers.add_parser(
        "export-recollection-relation-assessment-corpus",
        help="Route a bounded recollection report and export exact-component relation cases.",
    )
    export_recollection_relation_assessment_parser.add_argument(
        "--report", required=True, help="Bounded project repository recollection JSON report."
    )
    export_recollection_relation_assessment_parser.add_argument(
        "--output", required=True, help="Output corpus JSON path."
    )
    export_recollection_relation_assessment_parser.set_defaults(
        func=llm_evaluation_commands.cmd_export_recollection_relation_assessment_corpus
    )

    evaluate_relation_assessment_parser = subparsers.add_parser(
        "evaluate-relation-assessment-corpus",
        help="Run the predicate-specific relation assessor against a fixed shadow corpus.",
    )
    evaluate_relation_assessment_parser.add_argument("--corpus", required=True, help="Fixed corpus JSON path.")
    evaluate_relation_assessment_parser.add_argument("--output", required=True, help="Structured report JSON path.")
    evaluate_relation_assessment_parser.add_argument("--case-id", action="append", help="Run only this case ID. Repeatable.")
    evaluate_relation_assessment_parser.add_argument("--repeat", type=int, default=1, help="Runs per case. Default: 1.")
    evaluate_relation_assessment_parser.add_argument("--output-cap", type=int, default=512, help="Maximum output tokens. Default: 512.")
    evaluate_relation_assessment_parser.add_argument(
        "--support-evidence-mode", choices=["both_endpoints", "direct_any"], default="both_endpoints",
        help="Require cited support from both endpoints, or allow any sufficient bounded evidence. Default: both_endpoints.",
    )
    evaluate_relation_assessment_parser.add_argument("--runtime", help="Runner name/version recorded in the report.")
    evaluate_relation_assessment_parser.add_argument("--quantization", help="Quantization label recorded in the report.")
    evaluate_relation_assessment_parser.add_argument("--llm-endpoint", help="OpenAI-compatible LLM endpoint or base URL.")
    evaluate_relation_assessment_parser.add_argument("--llm-model", help="Model name for the LLM endpoint.")
    evaluate_relation_assessment_parser.add_argument("--llm-api-key", help="Optional API key for the LLM endpoint.")
    evaluate_relation_assessment_parser.add_argument("--llm-api-style", choices=["auto", "chat", "responses", "completions"], help="LLM endpoint API style preference.")
    evaluate_relation_assessment_parser.add_argument("--llm-reasoning-effort", choices=["none", "minimal", "low", "medium", "high"], help="Reasoning/thinking effort.")
    evaluate_relation_assessment_parser.set_defaults(
        func=llm_evaluation_commands.cmd_evaluate_relation_assessment_corpus
    )

    verify_service_parser = subparsers.add_parser("verify-service", help="Run static, deep, or anonymous browser verification for one service.")
    verify_target = verify_service_parser.add_mutually_exclusive_group(required=True)
    verify_target.add_argument("-p", "--project-key")
    verify_target.add_argument("--seed")
    verify_service_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"])
    verify_service_parser.add_argument("--url", help="Override the project service URL.")
    verify_service_parser.add_argument("--lane", choices=["static", "deep", "browser"], default="static", help="Verification method: static page, deep same-site pages, or anonymous browser rendering.")
    verify_service_parser.add_argument("--execute", action="store_true", help="Apply evaluation and save a replayable investigation run.")
    verify_service_parser.set_defaults(func=investigation_commands.cmd_verify_service)

    repair_parser = subparsers.add_parser("repair-db", help="Normalize stored project JSON, refresh derived fields, and report collisions.")
    repair_parser.add_argument("--execute", action="store_true", help="Apply normalization and derived-field refreshes. Without this flag, show a dry-run summary only.")
    repair_parser.set_defaults(func=maintenance_commands.cmd_repair_db)

    audit_url_parser = subparsers.add_parser("audit-url-fields", help="Report URL field usage, aliases, and stored invariant violations without changing the DB.")
    audit_url_parser.add_argument("--example-limit", type=int, default=20, help="Maximum anomaly examples per kind. Default: 20.")
    audit_url_parser.set_defaults(func=maintenance_commands.cmd_audit_url_fields)

    audit_trust_parser = subparsers.add_parser("audit-trust-graph", help="Report trust-graph confirmation issues without changing the DB.")
    audit_trust_parser.add_argument("--example-limit", type=int, default=20, help="Maximum issue examples per kind. Default: 20.")
    audit_trust_parser.set_defaults(func=maintenance_commands.cmd_audit_trust_graph)

    migrate_repository_trust_parser = subparsers.add_parser(
        "migrate-repository-trust", help="Review or apply a page/repository relation migration plan."
    )
    migrate_repository_trust_parser.add_argument("--db-path", required=True)
    migrate_repository_trust_parser.add_argument("--output", required=True, help="Dry-run plan or execution result JSON.")
    migrate_repository_trust_parser.add_argument("--execute-plan", help="Archive legacy edges using this reviewed plan; requires --archive.")
    migrate_repository_trust_parser.add_argument(
        "--archive", metavar="ARCHIVE_JSON",
        help="With --execute-plan, archive all legacy edges and historical judgments without restoring relations. File must not exist.",
    )
    migrate_repository_trust_parser.set_defaults(func=maintenance_commands.cmd_migrate_repository_trust)

    compare_trust_policy_parser = subparsers.add_parser(
        "compare-trust-policy",
        help="Compare current and role-aware trust policies without changing the database.",
    )
    compare_trust_policy_parser.add_argument("--db-path", required=True, help="Existing registry database opened read-only.")
    compare_trust_policy_parser.add_argument("--output", required=True, help="Structured comparison report JSON path.")
    compare_trust_policy_parser.add_argument("--example-limit", type=int, default=20, help="Maximum v1/shadow delta examples per profile. Default: 20.")
    compare_trust_policy_parser.set_defaults(func=maintenance_commands.cmd_compare_trust_policy)

    compare_collector_relation_policy_parser = subparsers.add_parser(
        "compare-collector-relation-policy",
        help="Compare current and direction-aware service/repository collector policies without changing the database.",
    )
    compare_collector_relation_policy_parser.add_argument(
        "--db-path", required=True, help="Existing registry database opened read-only."
    )
    compare_collector_relation_policy_parser.add_argument(
        "--output", required=True, help="Structured comparison report JSON path."
    )
    compare_collector_relation_policy_parser.add_argument(
        "--example-limit", type=int, default=20, help="Maximum examples per verdict. Default: 20."
    )
    compare_collector_relation_policy_parser.set_defaults(
        func=maintenance_commands.cmd_compare_collector_relation_policy
    )

    compare_project_repository_context_parser = subparsers.add_parser(
        "compare-project-repository-context",
        help="Recollect bounded project/repository link context without changing the database.",
    )
    compare_project_repository_context_parser.add_argument(
        "--db-path", required=True, help="Existing registry database opened read-only."
    )
    compare_project_repository_context_parser.add_argument(
        "--output", required=True, help="Structured current-page comparison report JSON path."
    )
    compare_project_repository_context_parser.add_argument(
        "--page-limit", type=int, default=10, help="Maximum distinct current pages to fetch. Default: 10."
    )
    compare_project_repository_context_parser.add_argument(
        "--timeout", type=float, default=10.0, help="Per-page fetch timeout in seconds. Default: 10."
    )
    compare_project_repository_context_parser.set_defaults(
        func=maintenance_commands.cmd_compare_project_repository_context
    )

    compare_project_repository_recollection_parser = subparsers.add_parser(
        "compare-project-repository-recollection",
        help="Recollect pre-migration page/repository claims against current pages without database writes.",
    )
    compare_project_repository_recollection_parser.add_argument(
        "--legacy-db-path", required=True, help="Pre-migration registry database opened read-only."
    )
    compare_project_repository_recollection_parser.add_argument(
        "--db-path", required=True, help="Current registry database opened read-only."
    )
    compare_project_repository_recollection_parser.add_argument(
        "--output", required=True, help="Structured bounded recollection report JSON path."
    )
    compare_project_repository_recollection_parser.add_argument(
        "--page-limit", type=int, default=10, help="Maximum distinct current pages to fetch. Default: 10."
    )
    compare_project_repository_recollection_parser.add_argument(
        "--timeout", type=float, default=10.0, help="Per-page fetch timeout in seconds. Default: 10."
    )
    compare_project_repository_recollection_parser.add_argument(
        "--concurrency", type=int, default=6, help="Maximum concurrent page fetches. Default: 6."
    )
    compare_project_repository_recollection_parser.add_argument(
        "--retries", type=int, default=1, help="Retries after an empty fetch failure. Default: 1."
    )
    compare_project_repository_recollection_parser.set_defaults(
        func=maintenance_commands.cmd_compare_project_repository_recollection
    )

    materialize_project_repository_review_parser = subparsers.add_parser(
        "materialize-project-repository-review",
        help="Apply fixed operator-reviewed repository relations to a new shadow database.",
    )
    materialize_project_repository_review_parser.add_argument(
        "--db-path", required=True, help="Existing registry database opened read-only."
    )
    materialize_project_repository_review_parser.add_argument(
        "--materialized-db", required=True, help="New shadow database receiving reviewed decisions."
    )
    materialize_project_repository_review_parser.add_argument(
        "--review-manifest", required=True, help="Fixed operator review manifest JSON path."
    )
    materialize_project_repository_review_parser.add_argument(
        "--output", required=True, help="Structured shadow materialization report JSON path."
    )
    materialize_project_repository_review_parser.set_defaults(
        func=maintenance_commands.cmd_materialize_project_repository_review
    )

    compare_project_repository_migration_parser = subparsers.add_parser(
        "compare-project-repository-migration",
        help="Compare migration from the legacy source repository role to project_repository without changing the database.",
    )
    compare_project_repository_migration_parser.add_argument(
        "--db-path", required=True, help="Existing registry database opened read-only."
    )
    compare_project_repository_migration_parser.add_argument(
        "--output", required=True, help="Structured migration comparison report JSON path."
    )
    compare_project_repository_migration_parser.add_argument(
        "--example-limit", type=int, default=20, help="Maximum examples per disposition. Default: 20."
    )
    compare_project_repository_migration_parser.add_argument(
        "--materialized-db", help="New shadow database receiving the projected migration."
    )
    compare_project_repository_migration_parser.set_defaults(
        func=maintenance_commands.cmd_compare_project_repository_migration
    )

    compare_project_site_migration_parser = subparsers.add_parser(
        "compare-project-site-migration",
        help="Compare existing Website values and repository homepage leads for a project_site role without changing the database.",
    )
    compare_project_site_migration_parser.add_argument(
        "--db-path", required=True, help="Existing registry database opened read-only."
    )
    compare_project_site_migration_parser.add_argument(
        "--output", required=True, help="Structured migration comparison report JSON path."
    )
    compare_project_site_migration_parser.add_argument(
        "--example-limit", type=int, default=20, help="Maximum examples per disposition. Default: 20."
    )
    compare_project_site_migration_parser.add_argument(
        "--materialized-db", help="Optional new shadow database path receiving the reviewed decisions."
    )
    compare_project_site_migration_parser.add_argument(
        "--review-manifest", help="Optional reviewed decision manifest used with --materialized-db."
    )
    compare_project_site_migration_parser.set_defaults(
        func=maintenance_commands.cmd_compare_project_site_migration
    )

    compare_merge_policy_parser = subparsers.add_parser(
        "compare-merge-policy",
        help="Compare current merge candidates with the role-aware review policy without changing the database.",
    )
    compare_merge_policy_parser.add_argument("--db-path", required=True, help="Existing registry database opened read-only.")
    compare_merge_policy_parser.add_argument("--output", required=True, help="Structured comparison report JSON path.")
    compare_merge_policy_parser.add_argument("--domain-overrides", help="Optional domain-overrides.json used by the current-v2 baseline; defaults to the file beside the DB checkout when present.")
    compare_merge_policy_parser.add_argument("--example-limit", type=int, default=20, help="Maximum examples per verdict. Default: 20.")
    compare_merge_policy_parser.set_defaults(func=maintenance_commands.cmd_compare_merge_policy)

    compare_identifier_storage_parser = subparsers.add_parser(
        "compare-identifier-storage",
        help="Prototype noncurrent identifier and adoption storage without changing the database.",
    )
    compare_identifier_storage_parser.add_argument("--db-path", required=True, help="Existing registry database opened read-only.")
    compare_identifier_storage_parser.add_argument("--output", required=True, help="Structured prototype report JSON path.")
    compare_identifier_storage_parser.add_argument("--example-limit", type=int, default=20, help="Maximum migration audit examples per category. Default: 20.")
    compare_identifier_storage_parser.add_argument("--materialized-db", help="Optional new SQLite path for a reviewed shadow migration. Existing files are refused.")
    compare_identifier_storage_parser.add_argument("--review-manifest", help="Review manifest used with --materialized-db. Defaults to the fixed v1 fixture.")
    compare_identifier_storage_parser.set_defaults(func=maintenance_commands.cmd_compare_identifier_storage)

    repair_trust_parser = subparsers.add_parser("repair-trust-graph", help="Recompute stored trust graph details and optionally downgrade unsupported confirmations.")
    repair_trust_parser.add_argument("--execute", action="store_true", help="Apply trust graph recomputation. Without this flag, show a dry-run summary only.")
    repair_trust_parser.add_argument("--downgrade-unsupported", action="store_true", help="With --execute, downgrade confirmed identifiers that have no trust evidence to candidates.")
    repair_trust_parser.add_argument("--example-limit", type=int, default=20, help="Maximum examples in the summary. Default: 20.")
    repair_trust_parser.set_defaults(func=maintenance_commands.cmd_repair_trust_graph)

    cleanup_storage_parser = subparsers.add_parser(
        "cleanup-storage-payloads",
        help="Compact current service evidence and investigation payloads without deleting runs.",
    )
    cleanup_storage_parser.add_argument(
        "--execute",
        action="store_true",
        help="Rewrite payloads in one transaction. Without this flag, report the exact projection without writing.",
    )
    cleanup_storage_parser.add_argument(
        "--example-limit",
        type=int,
        default=20,
        help="Maximum largest-saving examples. Default: 20.",
    )
    cleanup_storage_parser.add_argument(
        "--vacuum",
        action="store_true",
        help="After a successful rewrite, VACUUM the database. Requires --execute and all other processes stopped.",
    )
    cleanup_storage_parser.set_defaults(func=maintenance_commands.cmd_cleanup_storage_payloads)

    cleanup_temp_parser = subparsers.add_parser(
        "cleanup-workspace-temp",
        help="Delete stale SQLite copies and sidecars under the workspace .tmp directory.",
    )
    cleanup_temp_parser.add_argument(
        "--older-than-hours",
        type=int,
        default=24,
        help="Delete SQLite artifact groups whose newest file is at least this old. Default: 24.",
    )
    cleanup_temp_parser.add_argument(
        "--example-limit",
        type=int,
        default=20,
        help="Maximum deletion candidates included in the report. Default: 20.",
    )
    cleanup_temp_parser.add_argument(
        "--execute",
        action="store_true",
        help="Delete matching artifacts. Without this flag, report candidates only.",
    )
    cleanup_temp_parser.set_defaults(func=maintenance_commands.cmd_cleanup_workspace_temp)

    compress_history_parser = subparsers.add_parser(
        "compress-investigation-history",
        help="Compress investigation history JSON while preserving decoded payloads.",
    )
    compress_history_parser.add_argument(
        "--execute",
        action="store_true",
        help="Rewrite legacy history JSON in one transaction. Without this flag, report the exact projection without writing.",
    )
    compress_history_parser.add_argument(
        "--example-limit",
        type=int,
        default=20,
        help="Maximum candidate or decode-failure examples. Default: 20.",
    )
    compress_history_parser.add_argument(
        "--vacuum",
        action="store_true",
        help="After a successful rewrite, VACUUM the database. Requires --execute and all other processes stopped.",
    )
    compress_history_parser.set_defaults(func=maintenance_commands.cmd_compress_investigation_history)

    prune_history_parser = subparsers.add_parser(
        "prune-investigation-history",
        help="Delete old investigation and collector runs while retaining recent and per-project history.",
    )
    prune_history_parser.add_argument(
        "--retention-days",
        type=int,
        default=90,
        help="Keep every run completed within this many days. Default: 90.",
    )
    prune_history_parser.add_argument(
        "--keep-latest-per-project",
        type=int,
        default=3,
        help="Always keep at least this many newest runs per project. Default: 3.",
    )
    prune_history_parser.add_argument("--execute", action="store_true", help="Delete matching runs. Without this flag, report candidates only.")
    prune_history_parser.add_argument(
        "--vacuum",
        action="store_true",
        help="After deletion, VACUUM the database to return free pages to the filesystem. Requires --execute.",
    )
    prune_history_parser.set_defaults(func=maintenance_commands.cmd_prune_investigation_history)

    tracking_parser = subparsers.add_parser("set-tracking-state", help="Set tracking state for one project.")
    tracking_target = tracking_parser.add_mutually_exclusive_group(required=True)
    tracking_target.add_argument("-p", "--project-key", help="Target one project key.")
    tracking_target.add_argument("-s", "--seed", help="Target by known seed value.")
    tracking_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    tracking_parser.add_argument("-S", "--tracking-state", required=True, choices=sorted(project_record.TRACKING_STATES), help="Tracking state to assign.")
    tracking_parser.add_argument("--note", help="Optional tracking note.")
    tracking_parser.set_defaults(func=project_commands.cmd_set_tracking_state)

    separate_parser = subparsers.add_parser("set-separate", help="Mark two projects as distinct so merge recommendations do not pair them.")
    separate_source = separate_parser.add_mutually_exclusive_group(required=True)
    separate_source.add_argument("-p", "--project-key", help="First project key.")
    separate_source.add_argument("-s", "--seed", help="First project by known seed value.")
    separate_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    separate_target = separate_parser.add_mutually_exclusive_group(required=True)
    separate_target.add_argument("-o", "--other-project-key", help="Second project key.")
    separate_target.add_argument("-O", "--other-seed", help="Second project by known seed value.")
    separate_parser.add_argument("--other-type", choices=["nsid", "repo_url", "service_url"], help="Required when --other-seed is ambiguous.")
    separate_parser.add_argument("--clear", action="store_true", help="Remove the separation block instead of adding it.")
    separate_parser.set_defaults(func=decision_commands.cmd_set_separate)

    block_parser = subparsers.add_parser("block-identifier", help="Block one identifier from being reattached to a project during normalize/enrich.")
    block_target = block_parser.add_mutually_exclusive_group(required=True)
    block_target.add_argument("-p", "--project-key", help="Target one project key.")
    block_target.add_argument("-s", "--seed", help="Target by known seed value.")
    block_parser.add_argument("--project-type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    block_parser.add_argument("-i", "--identifier-type", required=True, choices=["nsid", "repo_url", "service_url"], help="Identifier class to block.")
    block_parser.add_argument("-v", "--value", required=True, help="Identifier value to block on this project.")
    block_parser.add_argument("--note", help="Optional operator note recorded with the block.")
    block_parser.add_argument("--clear", action="store_true", help="Remove the block instead of adding it.")
    block_parser.set_defaults(func=decision_commands.cmd_block_identifier)

    defer_parser = subparsers.add_parser("defer-identifier", help="Temporarily downgrade one identifier while preventing automatic confirmation.")
    defer_target = defer_parser.add_mutually_exclusive_group(required=True)
    defer_target.add_argument("-p", "--project-key", help="Target one project key.")
    defer_target.add_argument("-s", "--seed", help="Target by known seed value.")
    defer_parser.add_argument("--project-type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    defer_parser.add_argument("-i", "--identifier-type", required=True, choices=["nsid", "repo_url", "service_url"], help="Identifier class to defer.")
    defer_parser.add_argument("-v", "--value", required=True, help="Identifier value to defer on this project.")
    defer_parser.add_argument("--days", type=int, default=registry.DEFAULT_DEFER_IDENTIFIER_DAYS, help="How many days to keep the temporary guard. Default: 30.")
    defer_parser.add_argument("--note", help="Optional operator note recorded with the defer guard.")
    defer_parser.add_argument("--clear", action="store_true", help="Remove the defer guard instead of adding it. This does not confirm the identifier.")
    defer_parser.set_defaults(func=decision_commands.cmd_defer_identifier)

    defer_promotion_parser = subparsers.add_parser("defer-promotion", help="Temporarily hide one project from promotion-queue while recording the reason.")
    defer_promotion_target = defer_promotion_parser.add_mutually_exclusive_group(required=True)
    defer_promotion_target.add_argument("-p", "--project-key", help="Target one project key.")
    defer_promotion_target.add_argument("-s", "--seed", help="Target by known seed value.")
    defer_promotion_parser.add_argument("--project-type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    defer_promotion_parser.add_argument("--days", type=int, default=None, help="How many days to hide the project from promotion-queue. Default: automatic based on project age and prior deferrals.")
    defer_promotion_parser.add_argument("--reason", help="Why promotion is deferred. Omit to reuse the most recent reason.")
    defer_promotion_parser.add_argument("--clear", action="store_true", help="Clear active promotion deferrals while retaining their history.")
    defer_promotion_parser.set_defaults(func=decision_commands.cmd_defer_promotion)

    audit_source_parser = subparsers.add_parser("audit-source-config", help="Audit registered projects after custom multi-tenant suffix changes.")
    audit_source_parser.add_argument("domain", nargs="?", help="Optional suffix to add to config/domain-overrides.json before auditing it.")
    audit_source_parser.add_argument("--interactive", action=argparse.BooleanOptionalAction, default=True, help="Review split suggestions interactively. Default: enabled; use --no-interactive for JSON dry-run output.")
    audit_source_parser.add_argument("--execute", action="store_true", help="Add the requested suffix without an interactive confirmation. Split suggestions are never auto-applied.")
    audit_source_parser.set_defaults(func=maintenance_commands.cmd_audit_source_config)

    split_parser = subparsers.add_parser("split-project", help="Split one identifier out of a merged project into a new project.")
    split_target = split_parser.add_mutually_exclusive_group(required=True)
    split_target.add_argument("-p", "--project-key", help="Source project key.")
    split_target.add_argument("-s", "--seed", help="Source project by known seed value.")
    split_parser.add_argument("--project-type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    split_parser.add_argument("-i", "--identifier-type", required=True, choices=["nsid", "repo_url", "service_url", "domain"], help="Identifier class to split out.")
    split_parser.add_argument("-v", "--value", required=True, help="Identifier value to split out into a new project.")
    split_parser.add_argument("--note", help="Optional operator note.")
    split_parser.add_argument("--execute", action="store_true", help="Apply the split. Without this flag, only show a dry-run summary.")
    split_parser.set_defaults(func=merge_commands.cmd_split_project)

    inspect_merge_parser = subparsers.add_parser("inspect-merge-pair", help="Inspect merge evidence for one specific project pair.")
    inspect_merge_source = inspect_merge_parser.add_mutually_exclusive_group(required=True)
    inspect_merge_source.add_argument("-p", "--project-key", help="Source project key.")
    inspect_merge_source.add_argument("-s", "--seed", help="Source project by known seed value.")
    inspect_merge_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    inspect_merge_target = inspect_merge_parser.add_mutually_exclusive_group(required=True)
    inspect_merge_target.add_argument("-t", "--target-project-key", help="Target project key.")
    inspect_merge_target.add_argument("-T", "--target-seed", help="Target project by known seed value.")
    inspect_merge_parser.add_argument("--target-type", choices=["nsid", "repo_url", "service_url"], help="Required when --target-seed is ambiguous.")
    inspect_merge_parser.set_defaults(func=merge_commands.cmd_inspect_merge_pair)

    merge_parser = subparsers.add_parser("merge", help="Merge one project into another.")
    merge_source = merge_parser.add_mutually_exclusive_group(required=True)
    merge_source.add_argument("-p", "--project-key", help="Source project key.")
    merge_source.add_argument("-s", "--seed", help="Source project by known seed value.")
    merge_parser.add_argument("--type", choices=["nsid", "repo_url", "service_url"], help="Required when --seed is ambiguous.")
    merge_target = merge_parser.add_mutually_exclusive_group(required=False)
    merge_target.add_argument("-t", "--target-project-key", help="Explicit target project key.")
    merge_target.add_argument("-T", "--target-seed", help="Explicit target project by known seed value.")
    merge_target.add_argument("--use-recommendation", action="store_true", help="Use the current merge recommendation for the source project.")
    merge_parser.add_argument("--target-type", choices=["nsid", "repo_url", "service_url"], help="Required when --target-seed is ambiguous.")
    merge_parser.add_argument("--execute", action="store_true", help="Apply the merge. Without this flag, only show a dry-run summary.")
    merge_parser.add_argument("--force", action="store_true", help="Allow executing a non-auto-ready recommendation merge.")
    merge_parser.set_defaults(func=merge_commands.cmd_merge)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except urllib.error.URLError as exc:
        print(f"[error] network request failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
