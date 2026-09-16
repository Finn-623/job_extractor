"""Read-only schema audit for STEP 53B observed job-semantic endpoints."""
from __future__ import annotations
import json,sys
from pathlib import Path
from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.network_analyzer import list_observation,safe_business_data
ROOT=Path(__file__).resolve().parents[1]/"artifacts"/"step53b_recognition"/"per_site"
SITES=[("NAURA","https://career.naura.com/campus/jobs"),("Sinomach","https://zhaopin.sinomach.com.cn/SU64b4cfe82f9d24760ae8b80c/pb/school.html"),("CXMT","https://cxmt.zhiye.com/campus/jobs"),("Guangzhou Metro","https://gzmetro.zhiye.com/campus/jobs"),("Hisense","https://jobs.hisense.com/campus/jobs")]
def slug(x):return x.lower().replace(" ","_")
def run(site):
 company,url=site;seen=[]
 with BrowserRuntime(timeout_ms=20000) as b:
  def response(r):
   q=r.request
   if q.resource_type not in ("xhr","fetch"):return
   try: body=r.json()
   except Exception:return
   if not isinstance(body,(dict,list)):return
   path,n,sample=list_observation(body)
   if any(x in q.url.lower() for x in ("job","position","post","recruit")):
    seen.append({"endpoint":q.url,"method":q.method,"content_type":r.headers.get("content-type"),"request_body":q.post_data[:1000] if q.post_data else None,"top_level_keys":list(body)[:50] if isinstance(body,dict) else [],"candidate_list_path":path,"list_length":n,"sample":safe_business_data(sample[:5])})
  b.page.on("response",response);b.page.goto(url,wait_until="domcontentloaded",timeout=20000);b.page.wait_for_timeout(5000)
 path=ROOT/slug(company);path.mkdir(parents=True,exist_ok=True);(path/"schema.json").write_text(json.dumps({"company":company,"url":url,"responses":seen},ensure_ascii=False,indent=2),encoding="utf-8");print(company,len(seen),flush=True)
if __name__=="__main__":
 if "--site" in sys.argv:run(next(x for x in SITES if x[0].lower()==sys.argv[sys.argv.index("--site")+1].lower()))
 else:
  for x in SITES:run(x)
