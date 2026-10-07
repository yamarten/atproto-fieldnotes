#!/usr/bin/env python3
"""Compare the real enrich application with fixed, network-free responses.

Runs each sample in a fresh process and isolated temporary DB. Artificial wait
lengths are explicit; this is not an estimate of production speedup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from enrich_overlap_support import KEYS, Responses, seed, semantic
from project_registry_core import registry, project_record
from project_registry_core.persistence import storage, runtime_view
from project_registry_core.application import enrichment
from project_registry_core.jobs import OperationRequest

SCENARIOS = {
    "triage_miss": {"collection_delay": .15, "llm_delay": .22, "warmups": 0},
    "triage_hit": {"collection_delay": .15, "llm_delay": .22, "warmups": 3},
    "slow_collection_hit": {"collection_delay": .25, "llm_delay": .03, "warmups": 3},
    "no_wait_hit": {"collection_delay": 0, "llm_delay": 0, "warmups": 3},
}


def sample(scenario, prefetch, count, payload_bytes):
    config = SCENARIOS[scenario]
    scratch = ROOT / ".tmp/enrich-overlap"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sample-", dir=scratch) as directory:
        directory = Path(directory)
        conn = registry.initialize_db(directory / "survey.db")
        try:
            responses = Responses(payload_bytes=payload_bytes)
            def run(enabled):
                return enrichment.run(conn, OperationRequest.create("enrich", execute=True,
                    parameters={"all_projects": True, "all_tracking_states": True,
                                "prefetch": enabled, "checkpoint_path": str(directory / "checkpoint.json")}))
            with responses.patches():
                seed(conn, count)
                for _ in range(config["warmups"]):
                    assert run(False).status == "completed"
                responses.reset()
                responses.collection_delay = config["collection_delay"]
                responses.llm_delay = config["llm_delay"]
                started = time.perf_counter()
                result = run(prefetch)
                elapsed = time.perf_counter() - started
                assert result.status == "completed", result.to_dict()
                records = []
                for key in KEYS[:count]:
                    project = project_record.fetch_project(conn, key)
                    row = storage.latest_investigation_run(conn, key)
                    records.append({
                        "project": runtime_view.sanitize_runtime_snapshot(project),
                        "input": row["input_project"], "output": row["output_project"],
                        "evaluation": row["evaluation"], "bundle": row["bundle"].to_dict(),
                        "observations": row["observations"],
                        "triage_reuse": row["metrics"]["triage_reuse"],
                    })
                # Encoded section size includes decimal precision of elapsed values;
                # ignore size metadata after retaining and comparing the actual data.
                def strip_sizes(value):
                    if isinstance(value, dict):
                        return {k: strip_sizes(v) for k, v in value.items() if k != "original_bytes"}
                    if isinstance(value, list):
                        return [strip_sizes(v) for v in value]
                    return value
                normalized = strip_sizes(semantic(records))
                assert responses.calls["probe"] == count, "fixture lost initial collection during warmup"
                expected_reuse = "hit" if config["warmups"] else "miss"
                assert all(r["triage_reuse"]["status"] == expected_reuse for r in records)
                encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()
                if os.getenv("ENRICH_COMPARE_SNAPSHOT"):
                    Path(os.environ["ENRICH_COMPARE_SNAPSHOT"]).write_bytes(encoded)
                rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                if sys.platform != "darwin":
                    rss *= 1024
                return {"scenario": scenario, "prefetch": prefetch, "project_count": count,
                        "delays_seconds": config, "payload_bytes_per_probe": payload_bytes,
                        "elapsed_seconds": round(elapsed, 6), "peak_rss_bytes": rss,
                        "semantic_sha256": hashlib.sha256(encoded).hexdigest(),
                        "execution": result.data["execution"], "response_trace": responses.summary(),
                        "triage_reuse": [r["triage_reuse"]["status"] for r in records]}
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", choices=SCENARIOS)
    parser.add_argument("--prefetch", action="store_true")
    parser.add_argument("--projects", type=int, default=6, choices=range(2, 9))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--payload-bytes", type=int, default=262144)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.sample:
        print(json.dumps(sample(args.sample, args.prefetch, args.projects, args.payload_bytes)))
        return
    rows = []
    for scenario in SCENARIOS:
        for repetition in range(args.repetitions):
            for prefetch in ([False, True] if repetition % 2 == 0 else [True, False]):
                command = [sys.executable, __file__, "--sample", scenario, "--projects", str(args.projects), "--payload-bytes", str(args.payload_bytes)]
                if prefetch:
                    command.append("--prefetch")
                row = json.loads(subprocess.check_output(command, text=True))
                row["repetition"] = repetition + 1
                rows.append(row)
    summaries = []
    for scenario in SCENARIOS:
        selected = [row for row in rows if row["scenario"] == scenario]
        seq = [row["elapsed_seconds"] for row in selected if not row["prefetch"]]
        overlap = [row["elapsed_seconds"] for row in selected if row["prefetch"]]
        summaries.append({"scenario": scenario, "sequential_median_seconds": statistics.median(seq),
                          "prefetch_median_seconds": statistics.median(overlap),
                          "reduction_percent": round(100 * (1 - statistics.median(overlap) / statistics.median(seq)), 2),
                          "semantic_equal": len({row["semantic_sha256"] for row in selected}) == 1,
                          "response_call_counts_equal": len({json.dumps(row["response_trace"]["calls"], sort_keys=True) for row in selected}) == 1})
    result = {"kind": "fixed_responses_simulated_waits",
              "environment": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
              "samples": rows, "summaries": summaries}
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(json.dumps(summaries, indent=2))
    if not all(row["semantic_equal"] and row["response_call_counts_equal"] for row in summaries):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
