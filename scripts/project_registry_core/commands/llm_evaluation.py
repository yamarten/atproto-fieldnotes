from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .. import llm_model_evaluation as evaluation
from .. import llm_transport
from .. import repository_analysis
from .. import repository_analysis_evaluation
from .. import registry as core


def _prompt_from_evidence(evidence: dict) -> str:
    return core.build_triage_prompt_from_evidence(evidence)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cmd_export_llm_evaluation_corpus(args: argparse.Namespace) -> int:
    corpus = evaluation.export_corpus(
        Path(args.db_path),
        build_prompt=_prompt_from_evidence,
        limit=int(args.limit),
        library_min=int(args.library_min),
        category_min=int(args.category_min),
    )
    output = Path(args.output)
    _write_json(output, corpus)
    print(json.dumps({"output": str(output), **corpus["selection"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_evaluate_llm_corpus(args: argparse.Namespace) -> int:
    corpus_path = Path(args.corpus)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    if args.labels:
        label_set = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        corpus = evaluation.apply_labels(corpus, label_set)
    prompt_suffix = ""
    if args.prompt_suffix_file:
        prompt_suffix = Path(args.prompt_suffix_file).read_text(encoding="utf-8").strip()
    output_cap = int(args.output_cap)
    if output_cap < 1:
        raise ValueError("output-cap must be at least 1")

    def run_llm(prompt: str) -> tuple[dict, dict]:
        return llm_transport.run_local_llm(
            prompt,
            llm_endpoint=args.llm_endpoint,
            llm_model=args.llm_model,
            llm_api_key=args.llm_api_key,
            llm_reasoning_effort=args.llm_reasoning_effort,
            llm_api_style=args.llm_api_style,
            llm_max_output_tokens=output_cap,
        )

    def build_prompt(evidence: dict) -> str:
        prompt = _prompt_from_evidence(evidence)
        if prompt_suffix:
            prompt = f"{prompt}\n\nEvaluation prompt variant:\n{prompt_suffix}\n"
        return prompt

    requested_reasoning = str(
        args.llm_reasoning_effort or os.getenv("ATPROTO_SURVEY_LLM_REASONING_EFFORT", "")
    )
    reasoning_status = str(args.reasoning_observation or "not_checked")
    if not requested_reasoning:
        reasoning_status = "not_requested"
    reasoning_note = str(args.reasoning_observation_note or "").strip()
    if reasoning_status in {"applied", "ignored"} and not reasoning_note:
        raise ValueError(
            "--reasoning-observation-note is required when reasoning was observed as applied or ignored"
        )
    model_spec = {
        "model": str(args.llm_model or os.getenv("ATPROTO_SURVEY_LLM_MODEL", "")),
        "transport": "configured",
        "runtime": str(args.runtime or ""),
        "quantization": str(args.quantization or ""),
        "prompt_variant": str(args.prompt_variant or "baseline_current"),
        "current_profile_mode": str(args.current_profile_mode),
        "prompt_suffix_hash": evaluation.sha256_json({"text": prompt_suffix}) if prompt_suffix else "",
        "reasoning_effort_requested": requested_reasoning,
        "reasoning_observation": reasoning_status,
        "reasoning_observation_note": reasoning_note,
        "api_style": str(args.llm_api_style or os.getenv("ATPROTO_SURVEY_LLM_API_STYLE", "auto")),
        "output_cap": output_cap,
        "labels": str(args.labels or ""),
    }
    output = Path(args.output)
    case_ids = set(args.case_id or [])
    _write_json(output, evaluation.checkpoint_report(corpus, model_spec, [], case_ids))
    report = evaluation.run_corpus(
        corpus,
        build_prompt=build_prompt,
        run_llm=run_llm,
        model_spec=model_spec,
        repeat=int(args.repeat),
        current_profile_mode=str(args.current_profile_mode),
        case_ids=case_ids,
        root=core.ROOT,
        progress_callback=lambda results: _write_json(
            output, evaluation.checkpoint_report(corpus, model_spec, results, case_ids)
        ),
    )
    _write_json(output, report)
    print(json.dumps({"output": str(output), **report["summary"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["summary"]["failure_count"] == 0 else 1


def cmd_compare_llm_evaluation_reports(args: argparse.Namespace) -> int:
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    comparison = evaluation.compare_reports(baseline, candidate)
    output = Path(args.output)
    _write_json(output, comparison)
    print(json.dumps({
        "output": str(output),
        "automatic_checks_pass": comparison["automatic_checks_pass"],
        "comparison_issues": comparison["comparison_issues"],
        "automatic_regressions": comparison["automatic_regressions"],
        "remaining_checks": comparison["remaining_checks"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if comparison["automatic_checks_pass"] else 1


def cmd_export_repository_analysis_corpus(args: argparse.Namespace) -> int:
    corpus = repository_analysis_evaluation.export_corpus(
        Path(args.db_path), limit=int(args.limit),
    )
    output = Path(args.output)
    _write_json(output, corpus)
    print(json.dumps({"output": str(output), **corpus["selection"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_evaluate_repository_analysis_corpus(args: argparse.Namespace) -> int:
    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    output_cap = int(args.output_cap)
    if output_cap < 1:
        raise ValueError("output-cap must be at least 1")

    def run_llm(prompt: str) -> tuple[dict, dict]:
        return llm_transport.run_local_llm(
            prompt,
            llm_endpoint=args.llm_endpoint,
            llm_model=args.llm_model,
            llm_api_key=args.llm_api_key,
            llm_reasoning_effort=args.llm_reasoning_effort,
            llm_api_style=args.llm_api_style,
            llm_max_output_tokens=output_cap,
        )

    requested_reasoning = str(
        args.llm_reasoning_effort or os.getenv("ATPROTO_SURVEY_LLM_REASONING_EFFORT", "")
    )
    reasoning_status = str(args.reasoning_observation or "not_checked")
    if not requested_reasoning:
        reasoning_status = "not_requested"
    reasoning_note = str(args.reasoning_observation_note or "").strip()
    if reasoning_status in {"applied", "ignored"} and not reasoning_note:
        raise ValueError(
            "--reasoning-observation-note is required when reasoning was observed as applied or ignored"
        )
    model_spec = {
        "model": str(args.llm_model or os.getenv("ATPROTO_SURVEY_LLM_MODEL", "")),
        "transport": "configured",
        "runtime": str(args.runtime or ""),
        "quantization": str(args.quantization or ""),
        "prompt_version": repository_analysis.POLICY_VERSION,
        "reasoning_effort_requested": requested_reasoning,
        "reasoning_observation": reasoning_status,
        "reasoning_observation_note": reasoning_note,
        "api_style": str(args.llm_api_style or os.getenv("ATPROTO_SURVEY_LLM_API_STYLE", "auto")),
        "output_cap": output_cap,
    }
    output = Path(args.output)
    case_ids = set(args.case_id or [])
    _write_json(
        output,
        repository_analysis_evaluation.checkpoint_report(corpus, model_spec, [], case_ids),
    )
    report = repository_analysis_evaluation.run_corpus(
        corpus,
        run_llm=run_llm,
        model_spec=model_spec,
        repeat=int(args.repeat),
        case_ids=case_ids,
        root=core.ROOT,
        progress_callback=lambda results: _write_json(
            output,
            repository_analysis_evaluation.checkpoint_report(corpus, model_spec, results, case_ids),
        ),
    )
    _write_json(output, report)
    print(json.dumps({"output": str(output), **report["summary"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["summary"]["failure_count"] == 0 else 1


def cmd_compare_repository_analysis_reports(args: argparse.Namespace) -> int:
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    comparison = repository_analysis_evaluation.compare_reports(baseline, candidate)
    output = Path(args.output)
    _write_json(output, comparison)
    print(json.dumps({
        "output": str(output),
        "automatic_checks_pass": comparison["automatic_checks_pass"],
        "comparison_issues": comparison["comparison_issues"],
        "automatic_regressions": comparison["automatic_regressions"],
        "remaining_checks": comparison["remaining_checks"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if comparison["automatic_checks_pass"] else 1
