"""STEP 54B Stage1: run the validated AMEC plan on 10 real jobs.

Uses the persisted plan.json from the Plan Gate (no re-discovery). The plan's
detail contract carries the evidence-derived detail_result_path=["data"], so
the production collector now resolves the business wrapper organically.

Job selection: front 3 / middle 4 / tail 3 across the job list to span pages.
Everything comes from the real site; no synthetic data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.planning import CollectionPlanValidator
from job_extractor.planning.models import CollectionPlan

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "step54b_amec"


def main() -> int:
    plan = CollectionPlan.model_validate_json((ART / "plan.json").read_text("utf-8"))
    validation = CollectionPlanValidator().validate(plan)
    print("validator:", "valid" if validation.valid else f"INVALID {validation.errors}")
    if not validation.valid:
        return 1

    collector = GenericRuntimeDataCollector(plan, enrich_details=False)
    result = collector.collect()

    jobs = list(result.jobs.values()) if isinstance(result.jobs, dict) else list(result.jobs)
    n = len(jobs)
    print(f"collected jobs: {n}")

    picks = [jobs[i] for i in (0, 1, 2, n // 4, n // 4 + 1, n // 2, n // 2 + 1, n - 3, n - 2, n - 1)] if n >= 10 else jobs[:10]

    spot_checks = []
    for j in picks:
        raw = j.raw_data or {}
        spot_checks.append({
            "job_id": j.job_id,
            "title": j.job_title,
            "detail_merged": raw.get("_generic_detail_source") == "detail",
            "jd_state": raw.get("_jd_state"),
            "full_jd_present": bool(j.full_jd),
            "jd_len": len(j.full_jd or ""),
            "responsibilities": len(j.responsibilities or []),
            "requirements": len(j.requirements or []),
        })

    stats = collector.detail_stats or {}
    failures = stats.get("failures", {})
    fail_codes = {}
    for code in failures.values():
        fail_codes[code] = fail_codes.get(code, 0) + 1

    dc = result.data_completeness
    row = {
        "total_collected": n,
        "selection": [j.job_id for j in picks],
        # whole-run detail accounting (the collector fetches every job needing detail)
        "detail_required": dc.detail_required,
        "detail_attempted": dc.detail_attempted,
        "detail_succeeded": dc.detail_succeeded,
        "detail_failed": dc.detail_failed,
        # collector detail_stats failure taxonomy
        "decode_errors": stats.get("decode_errors", 0),
        "identity_mismatch": stats.get("identity_mismatch", 0),
        "timeouts": stats.get("timeouts", 0),
        "budget_exhausted": stats.get("budget_exhausted", 0),
        "failure_codes": fail_codes,
        # JD quality on the 10-job spread subset
        "subset_jd_complete": sum(1 for j in picks if (j.raw_data or {}).get("_jd_state") == "COMPLETE"),
        "subset_jd_partial": sum(1 for j in picks if j.full_jd and (j.raw_data or {}).get("_jd_state") != "COMPLETE"),
        "subset_jd_missing": sum(1 for j in picks if not j.full_jd),
        "spot_checks": spot_checks,
    }
    print(json.dumps(row, ensure_ascii=False, indent=2))
    (ART / "stage1.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
