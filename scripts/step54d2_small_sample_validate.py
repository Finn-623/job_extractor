"""STEP54D.2 — AECC small-sample live validation (10-20 jobs, current production path).

Replays the existing AECC plan through GenericHttpCollector with max_pages=1
bounded pagination. No discovery re-run, no detail fetch, no production change.
"""
from __future__ import annotations

import json
from pathlib import Path

from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.field_semantics import pick_jd_fields
from job_extractor.planning import CollectionPlan
from job_extractor.reporting import ReportManager

OUT = Path("artifacts/step54d2_contents_lineage/small_sample_validation.json")
PLAN = json.loads(Path("artifacts/step54_fresh_v1_benchmark/per_site/aecc/plan.json").read_text(encoding="utf-8"))


def main() -> None:
    plan = CollectionPlan.model_validate(PLAN)
    result = GenericHttpCollector(plan, max_pages=1, max_retries=1, deadline_seconds=120).collect()
    rows = []
    for job in result.jobs:
        raw = job.raw_data or {}
        contents = raw.get("contents")
        recognized = pick_jd_fields(raw)
        rows.append({
            "job_id": job.job_id,
            "contents_present": bool(contents),
            "contents_length": len(contents) if isinstance(contents, str) else None,
            "recognized_field": recognized["description_field"],
            "canonical_jd_present": bool(job.full_jd),
            "jd_state": raw.get("_jd_state"),
            "jd_complete": bool(job.full_jd) and raw.get("_jd_state") in ("FULL_TEXT", "SPLIT"),
        })
    identity_anomalies = sum(1 for j in result.jobs if not j.job_id or not j.job_title)
    summary = {
        "sample_size": len(result.jobs),
        "total_expected": result.total_expected,
        "collection_status": result.status,
        "termination": result.metrics.termination_reason,
        "contents_recognized": sum(1 for r in rows if r["recognized_field"] == "contents"),
        "canonical_jd_present": sum(1 for r in rows if r["canonical_jd_present"]),
        "jd_complete": sum(1 for r in rows if r["jd_complete"]),
        "jd_state_counts": {
            state: sum(1 for r in rows if r["jd_state"] == state)
            for state in {r["jd_state"] for r in rows}
        },
        "detail_attempted": result.data_completeness.detail_attempted,
        "false_complete": sum(1 for r in rows if r["jd_complete"] and not r["contents_present"]),
        "identity_anomalies": identity_anomalies,
        "samples": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    ReportManager().generate_reports(result, OUT.parent / "reports")
    print(json.dumps({k: v for k, v in summary.items() if k != "samples"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
