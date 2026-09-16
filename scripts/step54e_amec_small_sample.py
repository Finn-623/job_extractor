"""STEP 54E: AMEC small-sample live validation of the detail-contract fix.

Rebuilds the plan from the fresh benchmark's discovery evidence, so the STEP 54E
production fix applies (runtime scope consistency + business result-path
derivation). The detail stage is bounded to 12 jobs (small sample). Evidence
only — no production changes here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "artifacts" / "step54_v1_benchmark" / "per_site" / "amec"
OUT = ROOT / "artifacts" / "step54e_amec_fix"


def _discovery() -> DiscoveryResult:
    raw = json.loads((SRC / "discovery.json").read_text("utf-8"))
    try:
        return DiscoveryResult.model_validate(raw)
    except Exception:
        # Minimal reconstruction from the persisted evidence fields.
        return DiscoveryResult(
            source_url=raw.get("source_url") or "https://app.mokahr.com/campus_apply/amec/4362",
            status=raw.get("status") or "DISCOVERED",
            runtime_source=raw.get("runtime_source") or {},
            detected_scope=raw.get("detected_scope") or {},
        )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    result_discovery = _discovery()
    plan = CollectionPlanBuilder().build(result_discovery)
    print("rebuilt detail_body_template:", plan.detail_body_template)
    print("rebuilt detail_result_path:", plan.detail_result_path)
    print("scope siteId (trusted):", (result_discovery.detected_scope or {}).get("siteId"))
    v = CollectionPlanValidator().validate(plan)
    print("validator:", "valid" if v.valid else f"INVALID {v.errors}")
    if not v.valid:
        return 1

    collector = GenericRuntimeDataCollector(plan, detail_budget=12)
    collected = collector.collect()

    jobs = list(collected.jobs.values()) if isinstance(collected.jobs, dict) else list(collected.jobs)
    dc = collected.data_completeness
    stats = collector.detail_stats or {}
    detail_jobs = [j for j in jobs if (j.raw_data or {}).get("_generic_detail_source") == "detail"]
    row = {
        "detail_body_template": plan.detail_body_template,
        "detail_result_path": plan.detail_result_path,
        "collected_jobs": len(jobs),
        "collection_status": collected.status,
        "termination_reason": collected.metrics.termination_reason,
        "detail_required": dc.detail_required,
        "detail_attempted": dc.detail_attempted,
        "detail_succeeded": dc.detail_succeeded,
        "detail_failed": dc.detail_failed,
        "decode_errors": stats.get("decode_errors", 0),
        "identity_mismatch": stats.get("identity_mismatch", 0),
        "budget_exhausted": stats.get("budget_exhausted", 0),
        "failure_codes": {},
        "sample": [
            {
                "job_id": j.job_id,
                "title": j.job_title,
                "jd_state": (j.raw_data or {}).get("_jd_state"),
                "jd_len": len(j.full_jd or ""),
                "jd_head": (j.full_jd or "")[:60],
            }
            for j in detail_jobs[:12]
        ],
    }
    row["failure_codes"] = {}
    for code in (stats.get("failures") or {}).values():
        row["failure_codes"][code] = row["failure_codes"].get(code, 0) + 1
    print(json.dumps(row, ensure_ascii=False, indent=2))
    (OUT / "small_sample.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
