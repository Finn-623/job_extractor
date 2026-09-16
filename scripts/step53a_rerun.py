"""Read-only live recheck for STEP 53A's six source-observation sites."""
from __future__ import annotations
import json,sys
from pathlib import Path
from job_extractor.discovery.detector import GenericApiDetector
ROOT=Path(__file__).resolve().parents[1]/"artifacts"/"step53a_source_observation"
SITES=[("NAURA","https://career.naura.com/campus/jobs"),("Sinomach","https://zhaopin.sinomach.com.cn/SU64b4cfe82f9d24760ae8b80c/pb/school.html"),("AMEC","https://app.mokahr.com/campus_apply/amec/4362#/jobs?page=1&anchorName=jobsList"),("CXMT","https://cxmt.zhiye.com/campus/jobs"),("Guangzhou Metro","https://gzmetro.zhiye.com/campus/jobs"),("Hisense","https://jobs.hisense.com/campus/jobs")]
def slug(v):return v.lower().replace(" ","_")
def run(site):
 c,url=site; d=GenericApiDetector(timeout_ms=15000,source_budget_seconds=60).discover(url); runtime=d.runtime_source
 row={"company":c,"url":url,"source_found":bool(d.probable_list_api or (runtime and runtime.records)),"source_type":"NETWORK_JSON" if d.probable_list_api else ("RUNTIME_STATE" if runtime and runtime.records else "NONE"),"source_confidence":d.probable_list_api.confidence if d.probable_list_api else (runtime.confidence if runtime else "LOW"),"source_endpoint":d.probable_list_api.url if d.probable_list_api else None,"runtime_path":runtime.source_path if runtime else None,"runtime_records":runtime.record_count if runtime else 0,"warnings":d.warnings,"rejected": [{"url":x.url,"reasons":x.reasons} for x in d.rejected_candidates],"network":d.network_summary.model_dump(),"dom":d.dom_fallback,"elapsed_seconds":d.elapsed_seconds}
 p=ROOT/"per_site"/slug(c);p.mkdir(parents=True,exist_ok=True);(p/"rerun.json").write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding="utf-8");(p/"discovery.json").write_text(d.model_dump_json(indent=2),encoding="utf-8");print(c,row["source_found"],row["source_type"],flush=True)
if __name__=="__main__":
 ROOT.mkdir(parents=True,exist_ok=True)
 if "--site" in sys.argv:run(next(x for x in SITES if x[0].lower()==sys.argv[sys.argv.index("--site")+1].lower()))
 else:
  for x in SITES:run(x)
