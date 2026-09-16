"""STEP54D.2 — AECC full live validation (complete 205 collection, production path).

Full pagination through the existing AECC plan; no sampling, no truncation,
no production change. Writes per-job lineage without full JD text.
"""
from __future__ import annotations

import json
from pathlib import Path

from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.field_semantics import pick_jd_fields
from job_extractor.planning import CollectionPlan
from job_extractor.reporting import ReportManager

OUT = Path("artifacts/step54d2_contents_lineage/full_validation.json")
PLAN = json.loads(Path("artifacts/step54_fresh_v1_benchmark/per_site/aecc/plan.json").read_text(encoding="utf-8"))


def main() -> None:
    plan = CollectionPlan.model_validate(PLAN)
    result = GenericHttpCollector(plan, max_pages=1000, max_retries=1, deadline_seconds=180).collect()
    rows = []
    for job in result.jobs:
        raw = job.raw_data or {}
        contents = raw.get("contents")
        recognized = pick_jd_fields(raw)
        rows.append({
            "job_id": job.job_id,
            "contents_present": bool(contents),
            "contents_length": len(contents) if isinstance(contents, str) else None,
            "recognized_field": recognized["description_field"],
            "canonical_jd_present": bool(job.full_jd),
            "jd_state": raw.get("_jd_state"),
            "jd_complete": bool(job.full_jd) and raw.get("_jd_state") in ("FULL_TEXT", "SPLIT"),
        })
    dc = result.data_completeness
    metrics = result.metrics
    identity_anomalies = sum(1 for j in result.jobs if not j.job_id or not j.job_title)
    report_dir = OUT.parent / "full_reports"
    artifacts = ReportManager().generate_reports(result, report_dir)
    summary = {
        "expected_jobs": result.total_expected,
        "raw_count": result.total_fetched,
        "unique_count": result.total_unique,
        "collection_status": result.status,
        "termination_reason": metrics.termination_reason,
        "contents_present": sum(1 for r in rows if r["contents_present"]),
        "contents_recognized": sum(1 for r in rows if r["recognized_field"] == "contents"),
        "canonical_jd_present": sum(1 for r in rows if r["canonical_jd_present"]),
        "jd_complete": dc.jd_complete,
        "jd_incomplete": dc.jd_incomplete,
        "jd_state_counts": {
            state: sum(1 for r in rows if r["jd_state"] == state)
            for state in ("FULL_TEXT", "SPLIT", "SUMMARY", "ABSENT")
            if any(r["jd_state"] == state for r in rows)
        },
        "detail_attempted": dc.detail_attempted,
        "detail_succeeded": dc.detail_succeeded,
        "detail_failed": dc.detail_failed,
        "excel_success": artifacts.excel_path.exists() and artifacts.excel_path.stat().st_size > 0,
        "false_complete": sum(1 for r in rows if r["jd_complete"] and not r["contents_present"]),
        "identity_anomalies": identity_anomalies,
    }
    (OUT.parent / "full_reports").mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"summary": summary, "samples": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
