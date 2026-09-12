"""Validate generic public-HTML detail extraction from prior verified list evidence."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import httpx
from job_extractor.collectors.generic_detail import GenericHtmlDetailCollector
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.planning import CollectionPlan,CollectionPlanValidator
from job_extractor.reporting.manager import ReportManager

SAMPLE_IDS=("246","744","777","783","46")

def make_plan(evidence:dict)->CollectionPlan:
    values=dict(evidence["plan"]);values["initial_values"]=evidence["browser"]["body"]
    values.update(body_encoding="FORM",observed_list_length=evidence["browser_record_count"],detail_mode="DETAIL_HTTP_HTML",detail_url_field="click_url",detail_id_field="id")
    return CollectionPlan.model_validate(values)

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--evidence",type=Path,required=True);parser.add_argument("--jobs",type=Path,required=True);parser.add_argument("--output",type=Path,required=True);parser.add_argument("--full",action="store_true");args=parser.parse_args()
    evidence=json.loads(args.evidence.read_text(encoding="utf-8"));prior=json.loads(args.jobs.read_text(encoding="utf-8"));plan=make_plan(evidence)
    args.output.mkdir(parents=True,exist_ok=True);summary={"plan_valid":CollectionPlanValidator().validate(plan).valid,"detail_mode":plan.detail_mode,"detail_url_field":plan.detail_url_field}
    if args.full:
        (args.output/"checkpoint.json").write_text(json.dumps({"phase":"COLLECTING"}),encoding="utf-8")
        result=GenericHttpCollector(plan).collect()
        (args.output/"checkpoint.json").write_text(json.dumps({"phase":"COLLECTED","expected":result.total_expected,"fetched":result.total_fetched,"unique":result.total_unique,"details_attempted":result.metrics.details_attempted}),encoding="utf-8")
        reports=ReportManager().generate_reports(result,args.output)
        summary.update({"expected":result.total_expected,"fetched":result.total_fetched,"unique":result.total_unique,"status":result.status,"metrics":result.metrics.model_dump(),"completeness":result.data_completeness.model_dump(),"errors":result.errors,"warnings":result.warnings,"excel":str(reports.excel_path) if reports.excel_path else None})
        (args.output/"result.json").write_text(result.model_dump_json(indent=2),encoding="utf-8")
    else:
        by_id={str(x["job_id"]):x for x in prior["jobs"]};raws=[dict(by_id[jid]["raw_data"]) for jid in SAMPLE_IDS]
        with httpx.Client(timeout=30,follow_redirects=True) as client:
            enriched,success,failures=GenericHtmlDetailCollector(plan,client,max_workers=4).enrich(raws,("id",),("name",))
        samples=[]
        for raw in enriched:
            samples.append({"id":raw.get("id"),"title":raw.get("name"),"department":raw.get("department"),"category":raw.get("fun_name"),"location":raw.get("addr"),"url":raw.get("_generic_detail_url") or raw.get("click_url"),"bound_id":raw.get("_generic_bound_id"),"title_binding":bool(raw.get("_generic_description")),"source":raw.get("_generic_detail_source"),"full_jd_length":len(raw.get("_generic_description") or ""),"responsibilities":bool(raw.get("_generic_responsibilities")),"requirements":bool(raw.get("_generic_requirements"))})
        summary.update({"attempted":len(raws),"succeeded":success,"failed":len(failures),"failures":failures,"samples":samples})
    (args.output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":main()
