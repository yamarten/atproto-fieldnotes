"""Reduced operator-reviewed cases shared by queue tests and bounded LLM checks."""
import json
from pathlib import Path

from project_registry_core import atproto_usage, project_record, registry


def cases():
    root = Path(__file__).parent / "fixtures" / "project_registry"
    gold = json.loads((root / "manual-gold-v1.json").read_text())
    references = {c["subject"]["ref"]: c["subject"] for c in gold["showcase_reference_cases"]}
    corpus = json.loads((root / "promotion-selection-v1.json").read_text())
    for case in corpus["cases"]:
        subject = case.get("subject") or references[case["reference"]]
        p = project_record.default_project_record("fixture:" + subject["ref"], "service_url", subject["service_url"])
        p.update(name=subject["name"], category=subject["category"],
                 service_urls=[subject["service_url"]], website_url=subject["service_url"],
                 service_status="live" if subject["service_available"] else "none",
                 service_status_confidence="medium",
                 service_status_evidence=["login_surface_detected"] if subject["service_available"] else [],
                 summary=case["content"], tagline=subject["name"], features=["fixture purpose", "fixture data", "fixture use"])
        if case.get("source_type") == "lexicon":
            sample = json.loads(case["content"])
            p.update(resolved_nsids=[sample["id"]], lexicon_schema_samples=[sample])
        else:
            p["service_verification_results"] = [{
                "lane": "static", "requested_url": subject["service_url"], "status_code": 200,
                "supports_project": True, "html_extract": case["content"],
            }]
        evidence = registry.build_llm_evidence_bundle(p)
        yield {**case, "ref": subject["ref"], "project": p, "evidence": evidence}


def apply_fixed_assessment(case):
    p = case["project"]
    payload = {"atproto_usage": {
        "role": case["expected_role"], "reason": case["content"],
        "sources": [s["id"] for s in case["evidence"]["description_sources"]],
    }, "technical_interest_level": case["expected_technical"],
        "technical_interest_reasons": ["implementation_reference"],
        "technical_interest_summary": case["content"]}
    registry.apply_llm_output(p, payload, {"generated_at": "2026-09-22T00:00:00Z"}, case["evidence"])
    return project_record.ensure_project_shape(p)
