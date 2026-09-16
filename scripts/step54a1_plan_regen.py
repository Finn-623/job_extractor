"""STEP 54A.1 — regenerate discovery+plan for the invalid-total cluster only.

Reuses the exact production pipeline from step54a_live_validation.py
(GenericApiDetector → CollectionPlanBuilder → validator → dispatch check) but
scopes to YMTC / CXMT / Guangzhou Metro per instruction 九.  No collection is
performed here; live collection runs separately.  No production code changes.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from job_extractor.collectors.generic_ats import GenericATSCollector
from job_extractor.discovery.detector import GenericApiDetector
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator
from job_extractor.planning.execution_contract import missing_fields

ROOT = Path("artifacts/step54a1_plans")
SITES = [
    ("YMTC", "https://ymtc.zhiye.com/campus/jobs"),
    ("CXMT", "https://cxmt.zhiye.com/campus/jobs"),
    ("Guangzhou Metro", "https://gzmetro.zhiye.com/campus/jobs"),
]


def slug(value: str) -> str:
    return value.lower().replace(" ", "_")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, url in SITES:
        started = perf_counter()
        folder = ROOT / "per_site" / slug(name)
        folder.mkdir(parents=True, exist_ok=True)
        discovery = GenericApiDetector(timeout_ms=15000, source_budget_seconds=60).discover(url)
        plan = CollectionPlanBuilder().build(discovery)
        validation = CollectionPlanValidator().validate(plan)
        dispatchable = GenericATSCollector.can_dispatch(plan)
        candidate = discovery.probable_list_api
        pagination = discovery.detected_pagination
        row = {
            "site": name,
            "source_found": candidate is not None,
            "source_url_endpoint": candidate.url if candidate else None,
            "source_method": candidate.method if candidate else None,
            "detected_total_path": candidate.response_shape.get("total_field") if candidate else None,
            "selected_total_field": plan.total_field,
            "reported_total": candidate.observed_total if candidate else None,
            "observed_list_length": candidate.observed_list_length if candidate else None,
            "pagination_mode": plan.pagination_type,
            "page_param": plan.page_param,
            "page_size": pagination.page_size,
            "plan_valid": validation.valid,
            "plan_executable": plan.executable,
            "plan_dispatchable": dispatchable,
            "missing_fields": missing_fields(plan),
            "warnings": plan.warnings,
            "elapsed_seconds": round(perf_counter() - started, 3),
        }
        (folder / "discovery.json").write_text(discovery.model_dump_json(indent=2), encoding="utf-8")
        (folder / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        (folder / "row.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (ROOT / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("summary:", ROOT / "summary.json", flush=True)


if __name__ == "__main__":
    main()
