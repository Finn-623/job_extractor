"""Execute a previously fresh, validated Xiaomi generic plan for correctness validation."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from job_extractor.adapters.generic import GenericAdapter
from job_extractor.planning import CollectionPlan,CollectionPlanValidator
from job_extractor.reporting import ReportManager

def main():
    p=argparse.ArgumentParser();p.add_argument("--evidence",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    evidence=json.loads(a.evidence.read_text(encoding="utf-8"));plan=CollectionPlan.model_validate(evidence["plan"]);validation=CollectionPlanValidator().validate(plan)
    if not validation.valid:raise SystemExit("PLAN_INVALID "+str(validation.errors))
    result=GenericAdapter().execute_plan(plan);a.output.mkdir(parents=True,exist_ok=True);reports=ReportManager().generate_reports(result,a.output)
    (a.output/"result.json").write_text(result.model_dump_json(indent=2),encoding="utf-8")
    summary={"expected":result.total_expected,"fetched":result.total_fetched,"unique":result.total_unique,"status":result.status,"details_attempted":result.metrics.details_attempted,"details_succeeded":result.metrics.details_succeeded,"details_failed":result.metrics.details_failed,"full_jd":result.data_completeness.complete_jobs,"responsibilities":result.data_completeness.total_jobs-result.data_completeness.missing_responsibilities_jobs,"requirements":result.data_completeness.total_jobs-result.data_completeness.missing_requirements_jobs,"list_requests":result.metrics.list_requests,"browser_responses":result.metrics.browser_requests_observed,"elapsed":result.metrics.elapsed_seconds,"errors":result.errors,"excel":str(reports.excel_path) if reports.excel_path else None}
    (a.output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(summary,ensure_ascii=False))
if __name__=="__main__":main()
