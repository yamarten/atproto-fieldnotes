from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

from .evidence import EvidenceBundle
from .persistence.storage import evaluation_snapshot


@dataclass(frozen=True)
class ReplayDependencies:
    apply_evidence: Callable[[dict[str, Any], EvidenceBundle], None]
    finalize: Callable[[dict[str, Any]], dict[str, Any]]


def replay_bundle(
    input_project: dict[str, Any],
    bundle: EvidenceBundle,
    dependencies: ReplayDependencies,
) -> dict[str, Any]:
    project = copy.deepcopy(input_project)
    dependencies.apply_evidence(project, bundle)
    return dependencies.finalize(project)


def semantic_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(set(before) | set(after))
    changes = [
        {"field": key, "before": before.get(key), "after": after.get(key)}
        for key in keys
        if before.get(key) != after.get(key)
    ]
    return {
        "changed": bool(changes),
        "change_count": len(changes),
        "changes": changes,
    }


def replay_and_diff(
    input_project: dict[str, Any],
    bundle: EvidenceBundle,
    saved_evaluation: dict[str, Any],
    dependencies: ReplayDependencies,
) -> dict[str, Any]:
    output = replay_bundle(input_project, bundle, dependencies)
    evaluation = evaluation_snapshot(output)
    return {
        "project_key": str(input_project.get("project_key") or bundle.project_key),
        "evaluation": evaluation,
        "diff": semantic_diff(saved_evaluation, evaluation),
        "output_project": output,
    }


def benchmark_cases(
    cases: list[dict[str, Any]],
    dependencies: ReplayDependencies,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    regression_count = 0
    for case in cases:
        bundle = EvidenceBundle.from_dict(dict(case["bundle"]))
        replayed = replay_bundle(dict(case["input_project"]), bundle, dependencies)
        actual = evaluation_snapshot(replayed)
        expected = dict(case.get("expected_evaluation") or {})
        diff = semantic_diff(expected, {key: actual.get(key) for key in expected})
        if diff["changed"]:
            regression_count += 1
        results.append({"case_id": str(case.get("case_id") or bundle.project_key), "actual": actual, "diff": diff})
    return {
        "case_count": len(results),
        "passed_count": len(results) - regression_count,
        "regression_count": regression_count,
        "results": results,
    }
