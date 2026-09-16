"""STEP 54A.1 — live collection for the invalid-total cluster (YMTC/CXMT/GZMetro).

Executes the executable plans from artifacts/step54a1_plans via the production
GenericATSCollector.  Records pages requested/succeeded, raw/unique/duplicate
counts, termination reason and status.  Completion verdict follows the strict
COMPLETE rule (instruction 十一): unique == selected credible total AND all
required pages succeeded AND termination not in the blocking set AND no errors
AND no budget breach.  False COMPLETE = 0.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from job_extractor.collectors.generic_ats import GenericATSCollector
from job_extractor.planning.models import CollectionPlan

ROOT = Path("artifacts/step54a1_plans")
SITES = ["YMTC", "CXMT", "Guangzhou Metro"]
BLOCKING_TERMINATIONS = {"STAGNANT_PAGE", "PAGE_FAILED", "BUDGET_EXCEEDED", "DEADLINE", "RETRY_EXHAUSTED"}


def slug(value: str) -> str:
    return value.lower().replace(" ", "_")


def main() -> None:
    rows = []
    for name in SITES:
        folder = ROOT / "per_site" / slug(name)
        plan_model = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
        plan = CollectionPlan.model_validate(plan_model)
        started = perf_counter()
        error = None
        result = None
        try:
            result = GenericATSCollector(plan).collect()
        except Exception as exc:  # noqa: BLE001 — record and continue
            error = f"{type(exc).__name__}: {exc}"
        elapsed = round(perf_counter() - started, 3)

        m = result.metrics if result else None
        row = {
            "site": name,
            "selected_total_field": plan_model.get("total_field"),
            "total_expected": result.total_expected if result else None,
            "collection_started": result is not None or error is not None,
            "collection_status": result.status if result else "NOT_STARTED",
            "termination": m.termination_reason if m else None,
            "pages_requested": m.pages_requested if m else None,
            "pages_succeeded": m.pages_succeeded if m else None,
            "records_raw": result.total_fetched if result else None,
            "records_unique": result.total_unique if result else None,
            "records_duplicate": m.duplicate_jobs if m else None,
            "retry_count": m.retry_count if m else None,
            "collection_error": error,
            "elapsed_seconds": elapsed,
        }
        # COMPLETE rule (instruction 十一): false COMPLETE must stay 0.
        complete = False
        if result is not None:
            expected = result.total_expected
            complete = (
                expected is not None
                and result.total_unique == expected
                and result.status == "COMPLETE"
                and m.pages_succeeded == m.pages_requested
                and m.termination_reason not in BLOCKING_TERMINATIONS
                and not result.errors
            )
        row["verdict"] = "COMPLETE" if complete else ("PARTIAL" if result is not None else "INCOMPLETE")
        if result is not None:
            (folder / "collection.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        (folder / "collection_row.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (ROOT / "collection_summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("summary:", ROOT / "collection_summary.json", flush=True)


if __name__ == "__main__":
    main()
