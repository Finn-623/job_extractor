"""Read-only lineage evidence collector for STEP 53C."""
from __future__ import annotations
import hashlib,json,re,sys
from pathlib import Path
from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.network_analyzer import list_observation,safe_business_data
from job_extractor.discovery.runtime_data import observe_runtime_job_source
ROOT=Path(__file__).resolve().parents[1]/"artifacts"/"step53c_decode_boundary"
SITES=[("NAURA","https://career.naura.com/campus/jobs"),("CXMT","https://cxmt.zhiye.com/campus/jobs"),("Guangzhou Metro","https://gzmetro.zhiye.com/campus/jobs"),("Hisense","https://jobs.hisense.com/campus/jobs")]
def slug(x):return x.lower().replace(" ","_")
def run(site):
 company,url=site;requests=[]
 with BrowserRuntime(timeout_ms=20000) as b:
  def on_response(r):
   q=r.request
   if q.resource_type not in ("xhr","fetch") or not any(x in q.url.lower() for x in ("job","position","post","recruit")):return
   text=""
   try:text=r.text()
   except Exception:pass
   entry={"endpoint":q.url,"method":q.method,"content_type":r.headers.get("content-type"),"request_body":q.post_data[:500] if q.post_data else None,"raw_length":len(text),"raw_sha256":hashlib.sha256(text.encode()).hexdigest() if text else None,"raw_kind":"UNKNOWN"}
   try:
    data=json.loads(text);path,n,sample=list_observation(data);entry.update({"raw_kind":"JSON","top_level_keys":list(data)[:50] if isinstance(data,dict) else [],"candidate_list_path":path,"list_length":n,"sample":safe_business_data(sample[:5])})
   except Exception:
    entry["preview"]=re.sub(r"(?i)(token|cookie|authorization|session|signature)\s*[:=]\s*[^,\s]+",r"\1=[REDACTED]",text[:300])
   requests.append(entry)
  b.page.on("response",on_response);b.page.goto(url,wait_until="domcontentloaded",timeout=20000);b.page.wait_for_timeout(6000)
  body=b.page.locator("body").inner_text()[:20000]
  # Job-like lines are DOM ground truth, not inferred source records.
  lines=[x.strip() for x in body.splitlines() if x.strip() and re.search(r"(?:\(J\d{3,}\)|工程师|经理|专员|研究员|职位|岗位)",x)][:20]
  state=observe_runtime_job_source(b.page,provider="UNKNOWN")
  runtime={"confidence":state.confidence,"records":state.record_count,"path":state.source_path,"id_field":state.job_id_field,"title_field":state.job_title_field,"evidence":state.evidence}
  dom={"visible_job_samples":lines[:5],"body_length":len(body),"title":b.page.title(),"final_url":b.page.url}
 p=ROOT/"per_site"/slug(company);p.mkdir(parents=True,exist_ok=True)
 (p/"requests.json").write_text(json.dumps(requests,ensure_ascii=False,indent=2),encoding="utf-8")
 (p/"response_schema.json").write_text(json.dumps(requests,ensure_ascii=False,indent=2),encoding="utf-8")
 (p/"runtime_evidence.json").write_text(json.dumps(runtime,ensure_ascii=False,indent=2),encoding="utf-8")
 (p/"dom_evidence.json").write_text(json.dumps(dom,ensure_ascii=False,indent=2),encoding="utf-8")
 print(company,len(requests),runtime["records"],flush=True)
if __name__=="__main__":
 if "--site" in sys.argv:run(next(x for x in SITES if x[0].lower()==sys.argv[sys.argv.index("--site")+1].lower()))
 else:
  for x in SITES:run(x)
