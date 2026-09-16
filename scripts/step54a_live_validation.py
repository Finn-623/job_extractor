"""Sequential live validation for STEP54A plan-executability closure."""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from job_extractor.collectors.generic_ats import GenericATSCollector
from job_extractor.discovery.detector import GenericApiDetector
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator
from job_extractor.planning.execution_contract import missing_fields

ROOT = Path("artifacts/step54a_plan_closure")
SITES = [
    ("AECC", "https://aecc.iguopin.com/job"),
    ("Sinomach", "https://zhaopin.sinomach.com.cn/SU64b4cfe82f9d24760ae8b80c/pb/school.html"),
    ("YMTC", "https://ymtc.zhiye.com/campus/jobs"),
    ("CXMT", "https://cxmt.zhiye.com/campus/jobs"),
    ("Guangzhou Metro", "https://gzmetro.zhiye.com/campus/jobs"),
    ("Hisense", "https://jobs.hisense.com/campus/jobs"),
]


def slug(value: str) -> str:
    return value.lower().replace(" ", "_")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, url in SITES:
        started = perf_counter(); folder = ROOT / "per_site" / slug(name); folder.mkdir(parents=True, exist_ok=True)
        discovery = GenericApiDetector(timeout_ms=15000, source_budget_seconds=60).discover(url)
        plan = CollectionPlanBuilder().build(discovery); validation = CollectionPlanValidator().validate(plan)
        dispatchable = GenericATSCollector.can_dispatch(plan)
        collection = None; collection_error = None
        if plan.executable and validation.valid and dispatchable:
            try:
                collection = GenericATSCollector(plan).collect()
                (folder / "collection.json").write_text(collection.model_dump_json(indent=2), encoding="utf-8")
            except Exception as exc:
                collection_error = f"{type(exc).__name__}: {exc}"
        candidate = discovery.probable_list_api
        row = {"site": name, "source_found": candidate is not None, "source_type": candidate.source_type if candidate else None,
               "source_confidence": candidate.confidence if candidate else None, "plan_mode": plan.mode,
               "plan_valid": validation.valid, "plan_executable": plan.executable, "plan_dispatchable": dispatchable,
               "missing_fields": missing_fields(plan), "warnings": plan.warnings, "reported_total": collection.total_expected if collection else (candidate.observed_total if candidate else None),
               "collection_started": collection is not None or collection_error is not None, "collection_status": collection.status if collection else "NOT_STARTED",
               "termination": collection.metrics.termination_reason if collection else None,
               "collection_error": collection_error, "elapsed_seconds": round(perf_counter()-started, 3)}
        (folder / "discovery.json").write_text(discovery.model_dump_json(indent=2), encoding="utf-8")
        (folder / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        (folder / "row.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(row); print(json.dumps(row, ensure_ascii=False), flush=True)
    (ROOT / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
