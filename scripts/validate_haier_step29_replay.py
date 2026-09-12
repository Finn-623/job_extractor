"""Validate Step 29 collection from a previously browser-observed public request."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.planning import CollectionPlan,CollectionPlanValidator
from job_extractor.reporting.manager import ReportManager


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--evidence",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    evidence=json.loads(args.evidence.read_text(encoding="utf-8"))
    values=evidence["plan"]
    values["initial_values"]=evidence["browser"]["body"]
    values["body_encoding"]="FORM"
    values["observed_list_length"]=evidence["browser_record_count"]
    plan=CollectionPlan.model_validate(values)
    validation=CollectionPlanValidator().validate(plan)
    rows=[]
    for ordinal in range(1,6):
        collected=GenericHttpCollector(plan).collect() if validation.valid else None
        trace=collected.duplicate_audit.get("normalization_trace",[]) if collected else []
        normalized=sum(bool(item.get("normalized")) for item in trace)
        excel=None
        if collected:
            excel=ReportManager().generate_reports(collected,args.output.parent/f"replay_run_{ordinal}").excel_path
        total=collected.total_unique if collected else 0
        missing=collected.data_completeness.missing_jd_jobs if collected else 0
        row={"run":ordinal,"source_evidence":"REUSED_VERIFIED_BROWSER_OBSERVATION","plan_valid":validation.valid,
             "raw_records":collected.total_fetched if collected else 0,"normalized_jobs":normalized,
             "unique_jobs":collected.total_unique if collected else 0,"expected":collected.total_expected if collected else None,
             "detail_success":collected.metrics.details_succeeded if collected else 0,
             "jd_completeness_percent":round((total-missing)*100/total,2) if total else 0.0,
             "status":collected.status if collected else "NOT_EXECUTED","excel_generated":bool(excel and excel.exists()),
             "list_requests":collected.metrics.list_requests if collected else 0,"errors":collected.errors if collected else validation.errors}
        rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__":main()
