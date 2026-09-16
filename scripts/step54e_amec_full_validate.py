"""STEP 54E: AMEC full 182-job live validation of the detail-contract fix.

Rebuilds the plan from the fresh benchmark's discovery evidence (STEP 54E
production fix applies: trusted detected_scope + evidence-derived result path).
No detail budget — full detail enrichment. Evidence only, no production change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator
from job_extractor.reporting import ReportManager

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "artifacts" / "step54_v1_benchmark" / "per_site" / "amec"
OUT = ROOT / "artifacts" / "step54e_amec_fix"


def _discovery() -> DiscoveryResult:
    raw = json.loads((SRC / "discovery.json").read_text("utf-8"))
    try:
        return DiscoveryResult.model_validate(raw)
    except Exception:
        return DiscoveryResult(
            source_url=raw.get("source_url") or "https://app.mokahr.com/campus_apply/amec/4362",
            status=raw.get("status") or "DISCOVERED",
            runtime_source=raw.get("runtime_source") or {},
            detected_scope=raw.get("detected_scope") or {},
        )


def main() -> int:
    plan = CollectionPlanBuilder().build(_discovery())
    print("detail_body_template:", plan.detail_body_template)
    print("detail_result_path:", plan.detail_result_path)
    v = CollectionPlanValidator().validate(plan)
    print("validator:", "valid" if v.valid else f"INVALID {v.errors}")
    if not v.valid:
        return 1

    collector = GenericRuntimeDataCollector(plan)  # no detail budget: full enrichment
    collected = collector.collect()

    jobs = list(collected.jobs.values()) if isinstance(collected.jobs, dict) else list(collected.jobs)
    dc = collected.data_completeness
    stats = collector.detail_stats or {}
    failure_codes: dict[str, int] = {}
    for code in (stats.get("failures") or {}).values():
        failure_codes[code] = failure_codes.get(code, 0) + 1

    report_dir = OUT / "full_reports"
    artifacts = ReportManager().generate_reports(collected, report_dir)
    jd_states = {}
    for j in jobs:
        state = (j.raw_data or {}).get("_jd_state") or "ABSENT"
        jd_states[state] = jd_states.get(state, 0) + 1

    row = {
        "expected_jobs": collected.total_expected,
        "collected_jobs": collected.total_unique,
        "collection_status": collected.status,
        "termination_reason": collected.metrics.termination_reason,
        "detail_required": dc.detail_required,
        "detail_attempted": dc.detail_attempted,
        "detail_succeeded": dc.detail_succeeded,
        "detail_failed": dc.detail_failed,
        "failure_codes": failure_codes,
        "jd_complete": dc.jd_complete,
        "jd_incomplete": dc.jd_incomplete,
        "jd_state_counts": jd_states,
        "excel_success": artifacts.excel_path.exists() and artifacts.excel_path.stat().st_size > 0,
        "identity_mismatch": stats.get("identity_mismatch", 0),
        "decode_errors": stats.get("decode_errors", 0),
        "false_complete": sum(1 for j in jobs if (j.raw_data or {}).get("_jd_state") in ("FULL_TEXT", "SPLIT") and not (j.full_jd or "").strip()),
    }
    print(json.dumps(row, ensure_ascii=False, indent=2))
    (OUT / "full_validation.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
