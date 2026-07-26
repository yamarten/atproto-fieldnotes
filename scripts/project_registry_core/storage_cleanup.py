from __future__ import annotations

import json
import sqlite3
import urllib.parse
from collections import Counter
from collections.abc import Callable
from typing import Any

from . import identifiers, investigation, llm_evidence, service, storage
from .evidence import EvidenceBundle


GITHUB_RECORD_ROOTS = {"github_repo", "github_repo_metadata", "github_readme", "github_source"}


def json_text(value: Any, *, sort_keys: bool = True) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=sort_keys)


def utf8_bytes(value: str) -> int:
    return len(value.encode("utf-8"))


def source_root(value: str) -> str:
    return str(value or "").split(":", 1)[0]


def compact_saved_service_verification(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    requested_url = str(result.get("requested_url") or result.get("final_url") or "")
    if "links" in result:
        raw_links = result.get("links") if isinstance(result.get("links"), list) else []
        result["observed_link_count"] = max(
            int(result.get("observed_link_count") or 0),
            len(raw_links),
        )
        result["links"] = service.bounded_service_links(requested_url, raw_links)
    if "urls" in result:
        raw_urls = result.get("urls") if isinstance(result.get("urls"), list) else []
        result["observed_url_count"] = max(
            int(result.get("observed_url_count") or 0),
            len(raw_urls),
        )
        result["urls"] = service.bounded_service_urls(requested_url, raw_urls)
    if "html_extract" in result:
        result["html_extract"] = service.bounded_utf8_text(
            str(result.get("html_extract") or ""),
            service.SERVICE_HTML_EXTRACT_MAX_BYTES,
        )
    return result


def compact_service_verification_results(value: Any) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        selected = service.merge_service_verification_results(
            selected,
            compact_saved_service_verification(item),
        )
    return selected


def compact_llm_evidence(value: dict[str, Any]) -> dict[str, Any]:
    current = dict(value)
    for _attempt in range(8):
        compacted = llm_evidence.bounded_llm_evidence(current)
        if compacted == current:
            return compacted
        current = compacted
    raise ValueError("LLM evidence cleanup did not converge")


def compact_flat_project_snapshot(
    value: dict[str, Any],
    normalize_resource_url_records: Callable[[list[Any]], list[dict[str, str]]],
) -> dict[str, Any]:
    result = dict(value)
    if "service_verification_results" in result:
        result["service_verification_results"] = compact_service_verification_results(
            result.get("service_verification_results")
        )
    if "resource_url_records" in result:
        result["resource_url_records"] = normalize_resource_url_records(
            list(result.get("resource_url_records") or [])
        )
    llm_source = result.get("llm_source")
    if isinstance(llm_source, dict) and isinstance(llm_source.get("evidence"), dict):
        result["llm_source"] = dict(
            llm_source,
            evidence=compact_llm_evidence(dict(llm_source["evidence"])),
        )
    return result


def compact_grouped_project_state(
    value: dict[str, Any],
    normalize_resource_url_records: Callable[[list[Any]], list[dict[str, str]]],
) -> dict[str, Any]:
    result = dict(value)
    for group_name, raw_group in value.items():
        if not isinstance(raw_group, dict):
            continue
        result[group_name] = compact_flat_project_snapshot(
            raw_group,
            normalize_resource_url_records,
        )
    return result


def _empty_category_stats() -> dict[str, int]:
    return {
        "container_count": 0,
        "changed_container_count": 0,
        "item_count_before": 0,
        "item_count_after": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "bytes_saved": 0,
    }


def _category_values(value: dict[str, Any], category: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    containers = [("root", value)] + [
        (str(key), item)
        for key, item in value.items()
        if isinstance(item, dict)
    ]
    for location, container in containers:
        if category in {"resource_url_records", "service_verification_results"}:
            if category in container:
                values[location] = container.get(category)
            continue
        if category == "llm_evidence":
            llm_source = container.get("llm_source")
            if isinstance(llm_source, dict) and isinstance(llm_source.get("evidence"), dict):
                values[location] = llm_source["evidence"]
    return values


def _record_category_change(
    stats: dict[str, int],
    before: Any,
    after: Any,
) -> int:
    before_text = json_text(before)
    after_text = json_text(after)
    before_bytes = utf8_bytes(before_text)
    after_bytes = utf8_bytes(after_text)
    stats["container_count"] += 1
    stats["changed_container_count"] += int(before_text != after_text)
    stats["item_count_before"] += len(before) if isinstance(before, list) else int(bool(before))
    stats["item_count_after"] += len(after) if isinstance(after, list) else int(bool(after))
    stats["bytes_before"] += before_bytes
    stats["bytes_after"] += after_bytes
    stats["bytes_saved"] += before_bytes - after_bytes
    return before_bytes - after_bytes


def _resource_record_key(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        return ("", "unknown")
    return (
        identifiers.normalize_url(str(value.get("url") or "")),
        str(value.get("role") or "unknown"),
    )


def _resource_path_pattern(url: str) -> str:
    path = urllib.parse.urlparse(identifiers.normalize_url(url)).path.strip("/").lower()
    return f"/{path.split('/', 1)[0]}" if path else "/"


def _record_removed_resource_dimensions(
    before: Any,
    after: Any,
    *,
    by_role: Counter[str],
    by_host: Counter[str],
    by_path_pattern: Counter[str],
) -> None:
    before_items = [item for item in before if isinstance(item, dict)] if isinstance(before, list) else []
    after_counts = Counter(_resource_record_key(item) for item in after if isinstance(item, dict)) if isinstance(after, list) else Counter()
    for item in before_items:
        key = _resource_record_key(item)
        if after_counts[key] > 0:
            after_counts[key] -= 1
            continue
        url, role = key
        host = urllib.parse.urlparse(url).netloc.lower() or "(invalid)"
        by_role[role] += 1
        by_host[host] += 1
        by_path_pattern[_resource_path_pattern(url)] += 1


def compact_evidence_bundle(bundle: EvidenceBundle) -> tuple[EvidenceBundle, dict[str, int]]:
    payload = bundle.to_dict()
    records: list[dict[str, Any]] = []
    github_record_indexes: dict[str, int] = {}
    duplicate_github_records = 0
    service_records_changed = 0
    github_snapshots_changed = 0
    for raw_record in payload.get("records", []):
        record = dict(raw_record)
        raw_value = record.get("value")
        if record.get("kind") == "service_verification" and isinstance(raw_value, dict):
            compact_value = compact_saved_service_verification(raw_value)
            if compact_value != raw_value:
                service_records_changed += 1
            record["value"] = compact_value
        elif record.get("kind") == "github_repo_snapshot" and isinstance(raw_value, dict):
            compact_value = dict(raw_value)
            semantic_hints = [
                dict(item) for item in compact_value.get("semantic_hints", [])
                if isinstance(item, dict)
            ]
            readme_urls = [str(item or "") for item in compact_value.get("readme_urls", [])]
            compact_value["repo"] = investigation.compact_github_repo_snapshot(
                dict(compact_value.get("repo") or {})
            )
            compact_value.pop("semantic_hints", None)
            compact_value["semantic_hint_count"] = max(
                int(compact_value.get("semantic_hint_count") or 0),
                len(semantic_hints),
            )
            compact_value["readme_urls"] = investigation.bounded_github_readme_urls(readme_urls)
            compact_value["readme_url_count"] = max(
                int(compact_value.get("readme_url_count") or 0),
                len(readme_urls),
            )
            if compact_value != raw_value:
                github_snapshots_changed += 1
            record["value"] = compact_value

        if source_root(str(record.get("source") or "")) in GITHUB_RECORD_ROOTS:
            key = json_text({
                field: field_value
                for field, field_value in record.items()
                if field != "observed_at"
            })
            existing_index = github_record_indexes.get(key)
            if existing_index is not None:
                existing = records[existing_index]
                if str(record.get("observed_at") or "") > str(existing.get("observed_at") or ""):
                    existing["observed_at"] = record.get("observed_at")
                duplicate_github_records += 1
                continue
            github_record_indexes[key] = len(records)
        records.append(record)
    payload["records"] = records
    return EvidenceBundle.from_dict(payload), {
        "duplicate_github_records_removed": duplicate_github_records,
        "service_records_changed": service_records_changed,
        "github_snapshots_changed": github_snapshots_changed,
    }


def _empty_size_stats() -> dict[str, int]:
    return {
        "row_count": 0,
        "changed_row_count": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "bytes_saved": 0,
    }


def _record_size(stats: dict[str, int], before: str, after: str) -> int:
    before_bytes = utf8_bytes(before)
    after_bytes = utf8_bytes(after)
    stats["row_count"] += 1
    stats["bytes_before"] += before_bytes
    stats["bytes_after"] += after_bytes
    if before != after:
        stats["changed_row_count"] += 1
    saved = before_bytes - after_bytes
    stats["bytes_saved"] += saved
    return saved


def _add_example(
    examples: list[dict[str, Any]],
    example: dict[str, Any],
    *,
    limit: int,
) -> None:
    if limit <= 0 or int(example.get("bytes_saved") or 0) <= 0:
        return
    examples.append(example)
    examples.sort(key=lambda item: int(item.get("bytes_saved") or 0), reverse=True)
    del examples[limit:]


def compact_registry_storage(
    conn: sqlite3.Connection,
    *,
    normalize_resource_url_records: Callable[[list[Any]], list[dict[str, str]]],
    execute: bool = False,
    example_limit: int = 20,
    vacuum: bool = False,
) -> dict[str, Any]:
    if example_limit < 0:
        raise ValueError("example_limit must be zero or greater")
    if vacuum and not execute:
        raise ValueError("--vacuum requires --execute")
    required_tables = {"projects", "investigation_runs", "collector_runs"}
    existing_tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?, ?)",
            tuple(sorted(required_tables)),
        )
    }
    missing_tables = sorted(required_tables - existing_tables)
    if missing_tables:
        raise ValueError(f"storage cleanup requires tables: {', '.join(missing_tables)}")

    project_stats = _empty_size_stats()
    input_stats = _empty_size_stats()
    output_stats = _empty_size_stats()
    bundle_stats = _empty_size_stats()
    collector_stats = {
        "run_count": 0,
        "changed_run_count": 0,
        "row_count_before": 0,
        "row_count_after": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "bytes_saved": 0,
    }
    snapshot_evaluation_diff_count = 0
    bundle_metrics = {
        "duplicate_github_records_removed": 0,
        "service_records_changed": 0,
        "github_snapshots_changed": 0,
    }
    category_stats = {
        "resource_urls": _empty_category_stats(),
        "service_verification": _empty_category_stats(),
        "llm_evidence": _empty_category_stats(),
    }
    removed_resource_by_role: Counter[str] = Counter()
    removed_resource_by_host: Counter[str] = Counter()
    removed_resource_by_path_pattern: Counter[str] = Counter()
    resource_examples: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    collector_before = {
        str(row["run_id"]): {
            "row_count": int(row["row_count"] or 0),
            "bytes": int(row["payload_bytes"] or 0),
        }
        for row in conn.execute(
            """
            SELECT run_id, COUNT(*) AS row_count,
                   COALESCE(SUM(
                       LENGTH(CAST(result_json AS BLOB)) +
                       LENGTH(CAST(records_json AS BLOB)) +
                       LENGTH(CAST(failures_json AS BLOB))
                   ), 0) AS payload_bytes
            FROM collector_runs
            GROUP BY run_id
            """
        )
    }
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count_before = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_before = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    integrity_before = "not_run"
    integrity_after = "not_run"
    foreign_key_violation_count_before: int | None = None
    foreign_key_violation_count_after: int | None = None

    if execute:
        if conn.in_transaction:
            raise ValueError("storage cleanup requires a connection without an active transaction")
        integrity_before_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        integrity_before = "ok" if integrity_before_rows == ["ok"] else "; ".join(integrity_before_rows)
        if integrity_before != "ok":
            raise ValueError(f"database integrity check failed before cleanup: {integrity_before}")
        foreign_key_violation_count_before = sum(
            1 for _row in conn.execute("PRAGMA foreign_key_check")
        )
        if foreign_key_violation_count_before:
            raise ValueError(
                f"database has {foreign_key_violation_count_before} foreign key violations before cleanup"
            )
        conn.execute("BEGIN IMMEDIATE")
    try:
        project_cursor = conn.execute("SELECT project_key, state_json FROM projects ORDER BY project_key")
        while rows := project_cursor.fetchmany(100):
            for row in rows:
                project_key = str(row["project_key"])
                before = str(row["state_json"])
                state = json.loads(before)
                compact_state = compact_grouped_project_state(state, normalize_resource_url_records)
                for category, stats_key in (
                    ("resource_url_records", "resource_urls"),
                    ("service_verification_results", "service_verification"),
                    ("llm_evidence", "llm_evidence"),
                ):
                    before_values = _category_values(state, category)
                    after_values = _category_values(compact_state, category)
                    for location in sorted(set(before_values) | set(after_values)):
                        before_value = before_values.get(location, [] if category != "llm_evidence" else {})
                        after_value = after_values.get(location, [] if category != "llm_evidence" else {})
                        category_saved = _record_category_change(
                            category_stats[stats_key], before_value, after_value
                        )
                        if category == "resource_url_records":
                            _record_removed_resource_dimensions(
                                before_value,
                                after_value,
                                by_role=removed_resource_by_role,
                                by_host=removed_resource_by_host,
                                by_path_pattern=removed_resource_by_path_pattern,
                            )
                            _add_example(
                                resource_examples,
                                {
                                    "scope": "project_current",
                                    "key": project_key,
                                    "location": location,
                                    "bytes_saved": category_saved,
                                },
                                limit=example_limit,
                            )
                after = json_text(compact_state, sort_keys=False)
                saved = _record_size(project_stats, before, after)
                _add_example(
                    examples,
                    {"scope": "project_current", "key": project_key, "bytes_saved": saved},
                    limit=example_limit,
                )
                if execute and before != after:
                    conn.execute(
                        "UPDATE projects SET state_json = ? WHERE project_key = ?",
                        (after, project_key),
                    )

        run_cursor = conn.execute(
            """
            SELECT run_id, input_project_json, output_project_json, bundle_json
            FROM investigation_runs
            ORDER BY rowid
            """
        )
        while rows := run_cursor.fetchmany(50):
            for row in rows:
                run_id = str(row["run_id"])
                input_before = storage.decode_history_json_text(
                    row["input_project_json"], context=f"investigation run {run_id} input_project_json"
                )
                output_before = storage.decode_history_json_text(
                    row["output_project_json"], context=f"investigation run {run_id} output_project_json"
                )
                bundle_before = storage.decode_history_json_text(
                    row["bundle_json"], context=f"investigation run {run_id} bundle_json"
                )
                input_value = json.loads(input_before)
                output_value = json.loads(output_before)
                input_compact = compact_flat_project_snapshot(input_value, normalize_resource_url_records)
                output_compact = compact_flat_project_snapshot(output_value, normalize_resource_url_records)
                for snapshot_name, before_value, after_value in (
                    ("input", input_value, input_compact),
                    ("output", output_value, output_compact),
                ):
                    for category, stats_key in (
                        ("resource_url_records", "resource_urls"),
                        ("service_verification_results", "service_verification"),
                        ("llm_evidence", "llm_evidence"),
                    ):
                        before_values = _category_values(before_value, category)
                        after_values = _category_values(after_value, category)
                        for location in sorted(set(before_values) | set(after_values)):
                            raw_category = before_values.get(location, [] if category != "llm_evidence" else {})
                            compact_category = after_values.get(location, [] if category != "llm_evidence" else {})
                            category_saved = _record_category_change(
                                category_stats[stats_key], raw_category, compact_category
                            )
                            if category == "resource_url_records":
                                _record_removed_resource_dimensions(
                                    raw_category,
                                    compact_category,
                                    by_role=removed_resource_by_role,
                                    by_host=removed_resource_by_host,
                                    by_path_pattern=removed_resource_by_path_pattern,
                                )
                                _add_example(
                                    resource_examples,
                                    {
                                        "scope": f"investigation_{snapshot_name}",
                                        "key": run_id,
                                        "location": location,
                                        "bytes_saved": category_saved,
                                    },
                                    limit=example_limit,
                                )
                input_after = json_text(input_compact)
                output_after = json_text(output_compact)
                input_saved = _record_size(input_stats, input_before, input_after)
                output_saved = _record_size(output_stats, output_before, output_after)
                if (
                    storage.evaluation_snapshot(input_value) != storage.evaluation_snapshot(input_compact)
                    or storage.evaluation_snapshot(output_value) != storage.evaluation_snapshot(output_compact)
                ):
                    snapshot_evaluation_diff_count += 1

                compact_bundle, compact_metrics = compact_evidence_bundle(
                    EvidenceBundle.from_json(bundle_before)
                )
                for key in bundle_metrics:
                    bundle_metrics[key] += compact_metrics[key]
                bundle_after = json_text(compact_bundle.to_dict())
                bundle_saved = _record_size(bundle_stats, bundle_before, bundle_after)

                projection_rows = storage.collector_run_projection_rows(compact_bundle)
                projection_bytes = sum(
                    utf8_bytes(str(item[field]))
                    for item in projection_rows
                    for field in ("result_json", "records_json", "failures_json")
                )
                previous_projection = collector_before.get(
                    run_id,
                    {"row_count": 0, "bytes": 0},
                )
                collector_stats["run_count"] += 1
                collector_stats["row_count_before"] += int(previous_projection["row_count"])
                collector_stats["row_count_after"] += len(projection_rows)
                collector_stats["bytes_before"] += int(previous_projection["bytes"])
                collector_stats["bytes_after"] += projection_bytes
                collector_stats["bytes_saved"] += int(previous_projection["bytes"]) - projection_bytes
                if (
                    int(previous_projection["row_count"]) != len(projection_rows)
                    or int(previous_projection["bytes"]) != projection_bytes
                ):
                    collector_stats["changed_run_count"] += 1

                total_saved = input_saved + output_saved + bundle_saved + (
                    int(previous_projection["bytes"]) - projection_bytes
                )
                _add_example(
                    examples,
                    {
                        "scope": "investigation_run",
                        "key": run_id,
                        "bytes_saved": total_saved,
                        "input_bytes_saved": input_saved,
                        "output_bytes_saved": output_saved,
                        "bundle_bytes_saved": bundle_saved,
                        "collector_bytes_saved": int(previous_projection["bytes"]) - projection_bytes,
                    },
                    limit=example_limit,
                )
                if execute:
                    if (
                        input_before != input_after
                        or output_before != output_after
                        or bundle_before != bundle_after
                    ):
                        conn.execute(
                            """
                            UPDATE investigation_runs
                            SET input_project_json = ?, output_project_json = ?, bundle_json = ?
                            WHERE run_id = ?
                            """,
                            (
                                storage.encode_history_json_text(input_after),
                                storage.encode_history_json_text(output_after),
                                storage.encode_history_json_text(bundle_after),
                                run_id,
                            ),
                        )
                    storage.replace_collector_run_projection(conn, run_id, compact_bundle)
        if execute:
            foreign_key_violation_count_after = sum(
                1 for _row in conn.execute("PRAGMA foreign_key_check")
            )
            if foreign_key_violation_count_after:
                raise ValueError(
                    f"database has {foreign_key_violation_count_after} foreign key violations after cleanup"
                )
            integrity_after_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
            integrity_after = "ok" if integrity_after_rows == ["ok"] else "; ".join(integrity_after_rows)
            if integrity_after != "ok":
                raise ValueError(f"database integrity check failed after cleanup: {integrity_after}")
            conn.commit()
    except Exception:
        if execute:
            conn.rollback()
        raise

    if vacuum:
        conn.execute("VACUUM")

    page_count_after = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_after = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    input_output_stats = {
        key: input_stats[key] + output_stats[key]
        for key in input_stats
    }
    total_before = sum(
        item["bytes_before"]
        for item in (project_stats, input_output_stats, bundle_stats, collector_stats)
    )
    total_after = sum(
        item["bytes_after"]
        for item in (project_stats, input_output_stats, bundle_stats, collector_stats)
    )
    return {
        "mode": "execute" if execute else "dry_run",
        "project_current": project_stats,
        "investigation_snapshots": {
            **input_output_stats,
            "input": input_stats,
            "output": output_stats,
            "evaluation_diff_run_count": snapshot_evaluation_diff_count,
        },
        "investigation_bundles": {**bundle_stats, **bundle_metrics},
        "collector_projection": collector_stats,
        "payload_categories": {
            "resource_urls": {
                **category_stats["resource_urls"],
                "removed_by_role": dict(removed_resource_by_role.most_common()),
                "removed_by_host": dict(removed_resource_by_host.most_common(50)),
                "removed_by_path_pattern": dict(removed_resource_by_path_pattern.most_common(50)),
                "largest_savings": resource_examples,
            },
            "service_verification": category_stats["service_verification"],
            "llm_evidence": category_stats["llm_evidence"],
        },
        "total": {
            "bytes_before": total_before,
            "bytes_after": total_after,
            "bytes_saved": total_before - total_after,
        },
        "storage": {
            "page_size": page_size,
            "page_count_before": page_count_before,
            "page_count_after": page_count_after,
            "freelist_count_before": freelist_before,
            "freelist_count_after": freelist_after,
            "vacuum_required_for_file_shrink": not vacuum,
            "vacuumed": vacuum,
            "integrity_check_before": integrity_before,
            "integrity_check_after": integrity_after,
            "foreign_key_violation_count_before": foreign_key_violation_count_before,
            "foreign_key_violation_count_after": foreign_key_violation_count_after,
        },
        "largest_savings": examples,
    }
