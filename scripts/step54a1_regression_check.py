"""STEP 54A.1 — regression controls: AECC / Sinomach / CATL / Geely.

Verifies the invalid-total selection logic introduced for the zero-total
cluster did not regress sites whose total was already healthy:
- AECC      data.total=202   (tier1 'total')
- Sinomach  data.pageForm.dataCount=943 (tier1 'datacount')
- CATL      588 via MOKA runtime (BROWSER_RUNTIME_PAGINATION_BRIDGE, unchanged)
- Geely     data.totalSize=7 (tier1 'totalsize', fixed in 53H)
No production code changes; read-only verification runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from job_extractor.collectors.generic_ats import GenericATSCollector
from job_extractor.discovery.detector import GenericApiDetector
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator

ROOT = Path("artifacts/step54a1_regression")
SITES = [
    ("AECC", "https://aecc.iguopin.com/job"),
    ("Sinomach", "https://zhaopin.sinomach.com.cn/SU64b4cfe82f9d24760ae8b80c/pb/school.html"),
    ("CATL", "https://app.mokahr.com/campus-recruitment/catlhr/148948#/jobs"),
    ("Geely", "https://careers.geelytech.com/campus"),
]
EXPECT_TOTAL_FIELD = {"AECC": "data.total", "Sinomach": "data.pageForm.dataCount", "Geely": "data.totalSize"}


def slug(value: str) -> str:
    return value.lower().replace(" ", "_")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, url in SITES:
        folder = ROOT / slug(name)
        folder.mkdir(parents=True, exist_ok=True)
        started = perf_counter()
        error = None
        collection_row = None
        try:
            discovery = GenericApiDetector(timeout_ms=15000, source_budget_seconds=60).discover(url)
            plan = CollectionPlanBuilder().build(discovery)
            validation = CollectionPlanValidator().validate(plan)
            dispatchable = GenericATSCollector.can_dispatch(plan)
            candidate = discovery.probable_list_api
            row = {
                "site": name,
                "detected_total_path": candidate.response_shape.get("total_field") if candidate else None,
                "reported_total": candidate.observed_total if candidate else None,
                "plan_mode": plan.mode,
                "plan_executable": plan.executable,
                "plan_dispatchable": dispatchable,
                "warnings": plan.warnings,
            }
            if plan.executable and validation.valid and dispatchable:
                collection = GenericATSCollector(plan).collect()
                m = collection.metrics
                row.update({
                    "collection_status": collection.status,
                    "termination": m.termination_reason,
                    "total_expected": collection.total_expected,
                    "records_unique": collection.total_unique,
                    "pages_requested": m.pages_requested,
                    "pages_succeeded": m.pages_succeeded,
                })
                (folder / "collection.json").write_text(collection.model_dump_json(indent=2), encoding="utf-8")
            else:
                row["collection_status"] = "NOT_STARTED"
            (folder / "discovery.json").write_text(discovery.model_dump_json(indent=2), encoding="utf-8")
            (folder / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — record and continue
            error = f"{type(exc).__name__}: {exc}"
            row = {"site": name, "collection_status": "ERROR", "error": error}
        row["expected_total_field"] = EXPECT_TOTAL_FIELD.get(name)
        row["elapsed_seconds"] = round(perf_counter() - started, 3)
        if row.get("expected_total_field") and row.get("detected_total_path") != row["expected_total_field"]:
            row["regression_flag"] = f"TOTAL_FIELD_CHANGED {row.get('detected_total_path')}"
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (ROOT / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("summary:", ROOT / "summary.json", flush=True)


if __name__ == "__main__":
    main()
