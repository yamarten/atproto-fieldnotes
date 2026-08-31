from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..application import app_record_validation


def cmd_validate_app_record_export(args: argparse.Namespace) -> int:
    database_path = Path(args.db_path).resolve()
    output_path = Path(args.output).resolve()
    if database_path == output_path:
        raise ValueError("--output must not overwrite --db-path")
    report = app_record_validation.build_report_from_path(database_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output_path),
        "target_nsid": report["target"]["nsid"],
        "project_count": report["snapshot"]["project_count"],
        "readiness_counts": report["summary"]["readiness_counts"],
        "content_hash": report["content_hash"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
