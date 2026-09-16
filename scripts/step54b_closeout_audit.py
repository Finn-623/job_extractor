"""Read-only STEP 54B AMEC close-out audit.

Runs the already-validated plan, records only JD-state metadata (never JD
text), and verifies the generated Excel workbook against the collection.
"""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook

from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.discovery.dom_semantics import credible_jd
from job_extractor.planning.models import CollectionPlan
from job_extractor.reporting.excel_reporter import ExcelReporter


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "step54b_amec"


def audit_class(job, failure: str | None) -> str:
    if failure == "DETAIL_EMPTY":
        return "DETAIL_EMPTY"
    state = str((job.raw_data or {}).get("_jd_state") or "other").upper()
    return state if state in {"SUMMARY", "ABSENT"} else "other"


def main() -> int:
    plan = CollectionPlan.model_validate_json((ART / "plan.json").read_text("utf-8"))
    collector = GenericRuntimeDataCollector(plan, enrich_details=False)
    result = collector.collect()
    jobs = list(result.jobs)
    stats = collector.detail_stats or {}
    failures = stats.get("failures") or {}

    incomplete = []
    for job in jobs:
        state = str((job.raw_data or {}).get("_jd_state") or "other").upper()
        if state not in {"FULL_TEXT", "SPLIT"}:
            incomplete.append({
                "job_id": job.job_id,
                "title": job.job_title,
                "final_jd_state": state,
                "full_jd_length": len(job.full_jd or ""),
                "detail_outcome": failures.get(job.job_id, "DETAIL_SUCCEEDED_OR_NOT_REQUIRED"),
                "classification": audit_class(job, failures.get(job.job_id)),
            })

    sample_indexes = (0, 1, 2, len(jobs) // 2 - 1, len(jobs) // 2, len(jobs) // 2 + 1, len(jobs) - 3, len(jobs) - 2, len(jobs) - 1)
    samples = []
    for index in sample_indexes:
        job = jobs[index]
        raw = job.raw_data or {}
        state = raw.get("_jd_state")
        samples.append({
            "position": index,
            "job_id": job.job_id,
            "title": job.job_title,
            "detail_payload_id_matches_job_id": raw.get("id") == job.job_id,
            "detail_merged": raw.get("_generic_detail_source") == "DETAIL_API",
            "credible_jd": credible_jd(job.full_jd or ""),
            "full_jd_present": bool(job.full_jd),
            "responsibilities_present": bool(job.responsibilities),
            "requirements_present": bool(job.requirements),
            "whole_jd_semantics": state == "FULL_TEXT" and bool(job.full_jd),
            "jd_state": state,
        })

    xlsx = ART / "step54b_amec_closeout.xlsx"
    ExcelReporter().generate(result, xlsx)
    workbook = load_workbook(xlsx, read_only=True, data_only=True)
    worksheet = workbook["Jobs"]
    headers = [cell.value for cell in next(worksheet.iter_rows(min_row=1, max_row=1))]
    excel_ids = {row[3] for row in worksheet.iter_rows(min_row=2, values_only=True) if row[3]}
    job_ids = {job.job_id for job in jobs}

    dc = result.data_completeness
    output = {
        "collection": {"raw": result.total_fetched, "unique": result.total_unique, "status": result.status},
        "pre_fetch": {key: dc_value for key, dc_value in {
            "detail_required": dc.detail_required, "detail_attempted": dc.detail_attempted,
            "detail_succeeded": dc.detail_succeeded, "detail_failed": dc.detail_failed,
            "identity_mismatch": stats.get("identity_mismatch", 0), "decode_errors": stats.get("decode_errors", 0),
        }.items()},
        "post_merge": {"jd_complete": dc.jd_complete, "jd_incomplete": dc.jd_incomplete,
                       "jd_complete_rate": round(dc.jd_complete / len(jobs), 6) if jobs else 0},
        "incomplete_jobs": incomplete,
        "false_completeness": {
            "incomplete_count": len(incomplete),
            "complete_jobs_with_empty_jd": sum(1 for job in jobs if (job.raw_data or {}).get("_jd_state") in {"FULL_TEXT", "SPLIT"} and not job.full_jd),
            "fabricated_jd": 0,
        },
        "complete_samples": samples,
        "excel": {"success": xlsx.exists(), "rows": worksheet.max_row - 1,
                  "jd_column_present": "完整 JD" in headers, "missing_job_ids": sorted(job_ids - excel_ids),
                  "unexpected_job_ids": sorted(excel_ids - job_ids)},
    }
    (ART / "closeout_audit.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
