"""Five independent Step 29 collection validations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from job_extractor.adapters.generic import GenericAdapter
from job_extractor.discovery.detector import GenericApiDetector
from job_extractor.planning import CollectionPlanValidator
from job_extractor.reporting.manager import ReportManager


TARGET = "https://maker.haier.net/client/campus/activityindex.html"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    rows = []
    for ordinal in range(1, args.runs + 1):
        result = GenericApiDetector().discover(TARGET)
        adapter = GenericAdapter()
        plan = adapter.build_plan(result)
        validation = CollectionPlanValidator().validate(plan)
        candidate = result.probable_list_api
        collected = adapter.execute_plan(plan) if validation.valid and plan.confidence == "HIGH" else None
        trace = collected.duplicate_audit.get("normalization_trace", []) if collected else []
        normalized = sum(bool(item.get("normalized")) for item in trace)
        excel = None
        if collected:
            artifacts = ReportManager().generate_reports(collected, args.output.parent / f"run_{ordinal}")
            excel = artifacts.excel_path
        total = collected.total_unique if collected else 0
        missing = collected.data_completeness.missing_jd_jobs if collected else 0
        row = {
            "run": ordinal,
            "source_observed": bool(candidate),
            "plan_valid": validation.valid,
            "raw_records": collected.total_fetched if collected else 0,
            "normalized_jobs": normalized,
            "unique_jobs": collected.total_unique if collected else 0,
            "expected": collected.total_expected if collected else None,
            "detail_success": collected.metrics.details_succeeded if collected else 0,
            "jd_completeness_percent": round((total - missing) * 100 / total, 2) if total else 0.0,
            "status": collected.status if collected else "NOT_EXECUTED",
            "excel_generated": bool(excel and excel.exists()),
            "list_requests": collected.metrics.list_requests if collected else 0,
            "errors": collected.errors if collected else validation.errors,
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
