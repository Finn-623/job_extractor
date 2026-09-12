"""Five independent post-fix validations for Step 28."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from job_extractor.adapters.generic import GenericAdapter
from job_extractor.discovery.detector import GenericApiDetector
from job_extractor.planning import CollectionPlanValidator


TARGET = "https://maker.haier.net/client/campus/activityindex.html"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for ordinal in range(1, 6):
        result = GenericApiDetector().discover(TARGET)
        plan = GenericAdapter().build_plan(result)
        validation = CollectionPlanValidator().validate(plan)
        candidate = result.probable_list_api
        collected = None
        if validation.valid and plan.confidence == "HIGH":
            collected = GenericAdapter().execute_plan(plan)
        row = {
            "run": ordinal,
            "job_source_observed": bool(candidate),
            "source_url": candidate.url if candidate else None,
            "observed_jobs": candidate.observed_list_length if candidate else 0,
            "discovery_status": result.status,
            "plan_mode": plan.mode,
            "plan_confidence": plan.confidence,
            "plan_valid": validation.valid,
            "plan_errors": validation.errors,
            "collection_executed": collected is not None,
            "collection_status": collected.status if collected else "NOT_EXECUTED",
            "collection_unique": collected.total_unique if collected else 0,
            "warnings": result.warnings,
            "elapsed_seconds": result.elapsed_seconds,
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
