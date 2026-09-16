"""STEP 54A.1 — live collection for the invalid-total cluster (YMTC/CXMT/GZMetro).

Executes the executable plans from artifacts/step54a1_plans via the production
GenericATSCollector.  Records pages requested/succeeded, raw/unique/duplicate
counts, termination reason and status.  Completion verdict follows the strict
COMPLETE rule: unique == selected credible total AND all required pages
succeeded AND no stagnant/failed page AND no budget breach.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from job_extractor.collectors.generic_ats import GenericATSCollector

ROOT = Path("artifacts/step54a1_plans")
SITES = ["YMTC", "CXMT", "Guangzhou Metro"]


def slug(value: str) -> str:
    return value.lower().replace(" ", "_")


def main() -> None:
    rows = []
    for name in SITES:
        folder = ROOT / "per_site" / slug(name)
        plan_model = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
        # Rebuild the plan object from its serialized form via the builder's
        # stored model, keeping the exact production plan.
        from job_extractor.planning.models import CollectionPlan

        plan = CollectionPlan.model_validate(plan_model)
        started = perf_counter()
        error = None
        collection = None
        try:
            collection = GenericATSCollector(plan).collect()
        except Exception as exc:  # noqa: BLE001 — record and continue
            error = f"{type(exc).__name__}: {exc}"
        elapsed = round(perf_counter() - started, 3)

        row = {
            "site": name,
            "total_field": plan_model.get("total_field"),
            "total_expected": plan_model.get("total_expected"),
            "collection_started": collection is not None or error is not None,
            "collection_status": collection.status if collection else "NOT_STARTED",
            "termination": collection.metrics.termination_reason if collection else None,
            "pages_requested": collection.metrics.pages_requested if collection else None,
            "pages_succeeded": collection.metrics.pages_succeeded if collection else None,
            "records_raw": collection.metrics.records_raw if collection else None,
            "records_unique": collection.metrics.records_unique if collection else None,
            "records_duplicate": collection.metrics.records_duplicate if collection else None,
            "expected_pages": None if not collection else (collection.metrics.pages_requested),
            "collection_error": error,
            "elapsed_seconds": elapsed,
        }
        # COMPLETE rule (instruction 十一)
        complete = False
        if collection is not None:
            m = collection.metrics
            expected = row["total_expected"]
            complete = (
                expected is not None
                and m.records_unique == expected
                and m.pages_succeeded == m.pages_requested
                and m.termination_reason not in ("STAGNANT_PAGE", "PAGE_FAILED", "BUDGET_EXCEEDED")
                and not m.failed_pages
            )
        row["verdict"] = "COMPLETE" if complete else ("PARTIAL" if collection else "INCOMPLETE")
        if collection is not None:
            (folder / "collection.json").write_text(collection.model_dump_json(indent=2), encoding="utf-8")
        (folder / "collection_row.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (ROOT / "collection_summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("summary:", ROOT / "collection_summary.json", flush=True)


if __name__ == "__main__":
    main()
