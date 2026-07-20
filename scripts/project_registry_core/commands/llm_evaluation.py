from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .. import llm_model_evaluation as evaluation
from .. import llm_transport
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

    model_spec = {
        "model": str(args.llm_model or os.getenv("ATPROTO_SURVEY_LLM_MODEL", "")),
        "transport": "configured",
        "runtime": str(args.runtime or ""),
        "quantization": str(args.quantization or ""),
        "prompt_variant": "baseline_current",
        "reasoning_effort": str(args.llm_reasoning_effort or os.getenv("ATPROTO_SURVEY_LLM_REASONING_EFFORT", "")),
        "api_style": str(args.llm_api_style or os.getenv("ATPROTO_SURVEY_LLM_API_STYLE", "auto")),
        "output_cap": output_cap,
    }
    output = Path(args.output)
    case_ids = set(args.case_id or [])
    _write_json(output, evaluation.checkpoint_report(corpus, model_spec, [], case_ids))
    report = evaluation.run_corpus(
        corpus,
        build_prompt=_prompt_from_evidence,
        run_llm=run_llm,
        model_spec=model_spec,
        repeat=int(args.repeat),
        case_ids=case_ids,
        root=core.ROOT,
        progress_callback=lambda results: _write_json(
            output, evaluation.checkpoint_report(corpus, model_spec, results, case_ids)
        ),
    )
    _write_json(output, report)
    print(json.dumps({"output": str(output), **report["summary"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["summary"]["failure_count"] == 0 else 1
