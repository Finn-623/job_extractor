"""STEP54D.1 bounded live list-semantics validation; no discovery or detail fetch."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.planning import CollectionPlan
from job_extractor.reporting import ReportManager


FRESH = Path("artifacts/step54_fresh_v1_benchmark/per_site")
OUT = Path("artifacts/step54d1_live_validation")
SITES = ("naura", "ymtc", "cxmt", "guangzhou_metro", "hisense")


def plan_for(site: str) -> CollectionPlan:
    return CollectionPlan.model_validate(json.loads((FRESH / site / "plan.json").read_text(encoding="utf-8")))


def metrics(site: str, result, stage: str) -> dict[str, Any]:
    raws = [job.raw_data for job in result.jobs]
    return {
        "site": site, "stage": stage, "status": result.status,
        "jobs": len(result.jobs), "total_expected": result.total_expected,
        "duty_present": sum(bool(raw.get("Duty")) for raw in raws),
        "require_present": sum(bool(raw.get("Require")) for raw in raws),
        "responsibilities_populated": sum(bool(job.responsibilities) for job in result.jobs),
        "requirements_populated": sum(bool(job.requirements) for job in result.jobs),
        "jd_complete": result.data_completeness.jd_complete,
        "jd_incomplete": result.data_completeness.jd_incomplete,
        "jd_complete_rate": result.data_completeness.jd_complete / len(result.jobs) if result.jobs else 0.0,
        "list_sufficient": result.data_completeness.list_sufficient,
        "detail_attempted": result.data_completeness.detail_attempted,
        "detail_required": result.data_completeness.detail_required,
        "termination": result.metrics.termination_reason,
    }


def run(site: str, stage: str, max_pages: int | None = None) -> dict[str, Any]:
    result = GenericHttpCollector(plan_for(site), max_pages=max_pages or 1000, max_retries=1, deadline_seconds=120).collect()
    report_dir = OUT / stage / site / "reports"
    artifacts = ReportManager().generate_reports(result, report_dir)
    row = metrics(site, result, stage)
    row["excel_success"] = artifacts.excel_path.exists() and artifacts.excel_path.stat().st_size > 0
    (OUT / stage / site).mkdir(parents=True, exist_ok=True)
    (OUT / stage / site / "validation.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return row


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stage_a = [run(site, "stage_a_subset", max_pages=1) for site in ("naura", "hisense")]
    (OUT / "stage_a_summary.json").write_text(json.dumps(stage_a, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(row["jobs"] in range(10, 21) and row["jd_complete"] == row["jobs"] and row["detail_attempted"] == 0 for row in stage_a):
        raise SystemExit("STAGE_A_GATE_FAILED")
    stage_b = [run(site, "five_site_full") for site in SITES]
    (OUT / "five_site_summary.json").write_text(json.dumps(stage_b, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stage_a": stage_a, "five_site": stage_b}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
