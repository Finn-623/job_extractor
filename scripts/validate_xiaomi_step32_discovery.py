"""Single bounded downstream discovery/plan diagnostic."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from job_extractor.adapters.generic import GenericAdapter
from job_extractor.discovery.detector import GenericApiDetector
from job_extractor.planning import CollectionPlanValidator

def main():
    p=argparse.ArgumentParser();p.add_argument("url");p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    result=GenericApiDetector().discover(a.url);plan=GenericAdapter().build_plan(result);validation=CollectionPlanValidator().validate(plan)
    data={"discovery":result.model_dump(mode="json"),"plan":plan.model_dump(mode="json"),"validation":validation.model_dump(mode="json")}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"status":result.status,"elapsed":result.elapsed_seconds,"candidate":result.probable_list_api.url if result.probable_list_api else None,"plan_mode":plan.mode,"browser_trigger":plan.browser_trigger,"valid":validation.valid,"errors":validation.errors},ensure_ascii=False))
if __name__=="__main__":main()
