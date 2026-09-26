"""One speculative collection, joined before the foreground resumes its I/O."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .. import enrichment_pipeline as pipeline, enrich_observations, llm_transport
from .. import concurrency, project_record, runtime_view, storage
from ..jobs import CancellationToken


class InitialPrefetch:
    def __init__(self, conn, cancellation: CancellationToken) -> None:
        self.conn = conn  # Used on the caller thread only.
        self.cancellation = cancellation
        self.load_next: Callable[[], dict[str, Any] | None] | None = None
        self.initial: pipeline.InitialCollection | None = None
        self.error: Exception | None = None
        self.identity: tuple[str, Any] | None = None
        self.on_unapplied: Callable[[dict[str, str]], None] | None = None
        self.started = time.perf_counter()
        self.metrics: dict[str, Any] = {
            "mode": "sequential", "started": 0, "consumed": 0,
            "discarded": 0, "failed": 0, "collection_ms": 0,
            "join_wait_ms": 0, "unapplied_runs": [], "collection_errors": [],
        }

    @staticmethod
    def snapshot_identity(project: dict[str, Any]) -> tuple[str, Any]:
        return str(project["project_key"]), project.get(runtime_view.RUNTIME_PROJECT_REVISION)

    def run_llm(self, *args, **kwargs):
        load_next, self.load_next = self.load_next, None
        if load_next is None or self.identity is not None or self.cancellation.cancel_requested:
            return llm_transport.run_llm_with_metrics(*args, **kwargs)
        # Speculation cannot turn a foreground LLM request into a failure.
        try:
            project = load_next()
        except Exception:
            project = None  # The ordinary next-item read reports any persistent failure.
        if project is None or self.cancellation.cancel_requested:
            return llm_transport.run_llm_with_metrics(*args, **kwargs)
        self.identity = self.snapshot_identity(project)
        self.metrics["started"] += 1
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="enrich-prefetch") as executor:
            future = executor.submit(pipeline.collect_initial, project)
            try:
                return llm_transport.run_llm_with_metrics(*args, **kwargs)
            finally:
                started = time.perf_counter()
                try:
                    self.initial = future.result()
                    self.metrics["collection_ms"] += round(self.initial.elapsed_seconds * 1000)
                except Exception as exc:
                    self.error = exc
                    self.metrics["failed"] += 1
                    self.metrics["collection_errors"].append({"project_key": self.identity[0], "error": f"{type(exc).__name__}: {exc}"})
                self.metrics["join_wait_ms"] += round((time.perf_counter() - started) * 1000)

    def take(self, project: dict[str, Any]) -> pipeline.InitialCollection | None:
        if self.identity is None:
            return None
        if self.identity != self.snapshot_identity(project):
            self.discard("snapshot_changed")
            return None
        initial, error = self.initial, self.error
        self.initial = self.error = self.identity = None
        if error is not None:
            raise error
        self.metrics["consumed"] += 1
        return initial

    def discard(self, reason: str) -> None:
        self.load_next = None
        if self.identity is None:
            return
        if self.initial is not None:
            initial = self.initial
            # Preserve collected evidence without applying the speculative snapshot.
            # This is an incomplete collection, not a completed investigation.
            with concurrency.write_transaction(self.conn):
                run_id = storage.save_investigation_run(
                    self.conn, project_key=self.identity[0],
                    started_at=initial.started_at, completed_at=project_record.iso_now(),
                    input_project=initial.input_project, output_project=initial.project,
                    bundle=initial.evidence, evaluation={"incomplete": True},
                    run_metrics={"total_ms": round(initial.elapsed_seconds * 1000),
                                 "phases": {"initial_collection_ms": round(initial.elapsed_seconds * 1000)}},
                    observations=enrich_observations.pack({"operations": initial.operation_timer.rows}),
                    application_status="unapplied", conflict_reason=f"prefetch_incomplete:{reason}",
                )
            item = {"project_key": self.identity[0], "run_id": run_id, "reason": reason}
            self.metrics["unapplied_runs"].append(item)
            # Clear ownership before checkpoint I/O, which may itself fail.
            self.initial = self.error = self.identity = None
            self.metrics["discarded"] += 1
            if self.on_unapplied:
                self.on_unapplied(item)
            return
        self.metrics["discarded"] += 1
        self.initial = self.error = self.identity = None

    def summary(self) -> dict[str, Any]:
        return {**self.metrics, "batch_elapsed_ms": round((time.perf_counter() - self.started) * 1000)}
