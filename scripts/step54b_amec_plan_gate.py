"""STEP 54B gate: regenerate the real AMEC plan via discovery → builder.

No collection. Prints the gate fields (instruction 十) and writes artifacts.
The contract must come from organic discovery evidence — nothing injected.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from job_extractor.discovery.detector import GenericApiDetector
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator

AMEC_URL = "https://app.mokahr.com/campus_apply/amec/4362#/jobs?page=1&anchorName=jobsList"
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "step54b_amec"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    d = GenericApiDetector(timeout_ms=15000, source_budget_seconds=60).discover(AMEC_URL)
    runtime = d.runtime_source
    contract = (runtime.detail_contract if runtime else None) or {}
    plan = CollectionPlanBuilder().build(d)
    validation = CollectionPlanValidator().validate(plan)

    gate = {
        "source_found": bool(runtime and runtime.records),
        "runtime_confidence": runtime.confidence if runtime else None,
        "runtime_record_count": runtime.record_count if runtime else 0,
        "runtime_detail_contract_present": bool(contract),
        "detail_endpoint_template": plan.detail_endpoint_template,
        "detail_method": plan.detail_method,
        "detail_id_field": plan.detail_id_field,
        "detail_body_template": plan.detail_body_template,
        "detail_decoder": plan.detail_decoder,
        "detail_jd_field": plan.detail_jd_field,
        "validator_valid": validation.valid,
        "plan_executable": plan.executable,
        "plan_mode": plan.mode,
        "discovery_status": d.status,
        "warnings": d.warnings,
        "validator_errors": validation.errors,
    }
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    (OUT / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    (OUT / "discovery.json").write_text(d.model_dump_json(indent=2), encoding="utf-8")
    (OUT / "gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = (
        bool(contract)
        and plan.detail_endpoint_template is not None
        and plan.detail_method is not None
        and plan.detail_id_field is not None
        and plan.detail_body_template is not None
        and plan.detail_jd_field is not None
    )
    print("GATE:", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
