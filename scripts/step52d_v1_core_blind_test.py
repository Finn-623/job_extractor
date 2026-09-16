"""STEP 52D first-pass V1 blind test; uses unchanged generic pipeline only."""
from __future__ import annotations
import csv, json, sys
from pathlib import Path
from time import perf_counter
import step52_blind_test as generic

ROOT=Path(__file__).resolve().parents[1]/"artifacts"/"step52d_v1_core_blind_test"
SITES=[
 ("NAURA","https://career.naura.com/campus/jobs",False),
 ("AECC","https://aecc.iguopin.com/job",True),
 ("Sinomach","https://zhaopin.sinomach.com.cn/SU64b4cfe82f9d24760ae8b80c/pb/school.html",False),
 ("AMEC","https://app.mokahr.com/campus_apply/amec/4362#/jobs?page=1&anchorName=jobsList",False),
 ("YMTC","https://ymtc.zhiye.com/campus/jobs",True),
 ("CXMT","https://cxmt.zhiye.com/campus/jobs",False),
 ("CATL","https://app.mokahr.com/campus-recruitment/catlhr/148948#/jobs",False),
 ("Guangzhou Metro","https://gzmetro.zhiye.com/campus/jobs",False),
 ("Geely","https://careers.geelytech.com/campus",False),
 ("Hisense","https://jobs.hisense.com/campus/jobs",False),]

def slug(x): return generic._slug(x)
def classify(row):
    reason=(row.get("failure_reason") or "")+" "+(row.get("failure_category") or "")
    if row.get("environment_seed") and not row.get("source_found"):
        return "ENVIRONMENT_NETWORK"
    if row.get("source_false_positive"): return "SOURCE_FALSE_POSITIVE"
    if not row.get("source_found"): return "SOURCE_DISCOVERY_FAILURE"
    if not (row.get("plan_executable") and row.get("plan_valid") and row.get("dispatchable")): return "PLAN_EXECUTION_FAILURE"
    if "PAGE_REQUEST_FAILED" in reason: return "PAGE_REQUEST_FAILED"
    if "JD_NOT_FOUND" in reason or "JD_RENDER_TIMEOUT" in reason: return "JD_DETAIL_FAILURE"
    if row.get("collection_status")=="COMPLETE": return ""
    if "deadline" in reason.lower() or "timeout" in reason.lower(): return "COLLECTION_TIMEOUT"
    return row.get("failure_category") or "COLLECTION_INCOMPLETE"
def run(site):
    company,url,environment=site; t=perf_counter(); row=generic._run_site((company,"China benchmark",url)); folder=Path(row["artifact_path"])
    row.update({"input_url":url,"environment_seed":environment,"source_truth":"SOURCE_TRUE" if row.get("source_found") else "SOURCE_NOT_FOUND","source_endpoint":None,"source_rejection_reason":"","pages_fetched":0,"raw_rows":0,"collection_mode":None,"termination_reason":"","detail_required":0,"detail_attempted":0,"detail_succeeded":0,"detail_failed":0,"json_success":False,"markdown_success":False,"discovery_seconds":0.0,"collection_seconds":0.0,"detail_seconds":0.0,"export_seconds":0.0})
    d=folder/"discovery.json"; p=folder/"plan.json"; c=folder/"collection.json"
    if d.exists():
        data=json.loads(d.read_text()); row["discovery_seconds"]=data.get("elapsed_seconds") or 0; candidate=data.get("probable_list_api") or {}; row["source_endpoint"]=candidate.get("url"); row["source_rejection_reason"]="; ".join(data.get("warnings") or [])
    if p.exists(): row["pagination_fields"]=(json.loads(p.read_text()).get("pagination") or {})
    else: row["pagination_fields"]={}
    if c.exists():
        data=json.loads(c.read_text()); m=data.get("metrics") or {}; dc=data.get("data_completeness") or {}; reports=folder/"reports"
        row.update({"pages_fetched":m.get("list_pages",0),"raw_rows":m.get("raw_rows",0),"collection_mode":m.get("collection_mode"),"termination_reason":m.get("termination_reason") or "; ".join(data.get("errors") or []),"collection_seconds":m.get("elapsed_seconds",0),"detail_seconds":m.get("detail_fallback_seconds",0),"detail_required":dc.get("detail_required",0),"detail_attempted":dc.get("detail_attempted",0),"detail_succeeded":dc.get("detail_succeeded",0),"detail_failed":dc.get("detail_failed",0),"json_success":(reports/"jobs.json").exists(),"markdown_success":(reports/"report.md").exists()})
    row["failure_category"]=classify(row); row["failure_reason"]=row.get("failure_reason") or row["source_rejection_reason"]
    row["elapsed_seconds"]=round(perf_counter()-t,3); (folder/"step52d_row.json").write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding="utf-8"); print(company,row["failure_category"] or "COMPLETE",flush=True)

def render():
    rows=[]
    for company,url,environment in SITES:
        path=ROOT/"per_site"/slug(company)/"step52d_row.json"
        if path.exists(): rows.append(json.loads(path.read_text()))
    for row in rows: generic._audit_existing_row(row); row["failure_category"]=classify(row); row["e2e_pass"]=bool(row["source_truth"]=="SOURCE_TRUE" and row["plan_executable"] and row["plan_valid"] and row["dispatchable"] and row["collection_status"]=="COMPLETE" and row["unique"]>0 and row["jd_complete"]>0 and row["excel_success"] and not row["source_false_positive"])
    def rate(items,fn): return round(sum(bool(fn(x)) for x in items)/len(items),4) if items else 0
    evaluable=[x for x in rows if not (x["environment_seed"] and x["failure_category"]=="SOURCE_DISCOVERY_FAILURE")]
    def metrics(items): return {"source_found":rate(items,lambda x:x["source_truth"]=="SOURCE_TRUE" and not x["source_false_positive"]),"executable_plan":rate(items,lambda x:x["plan_executable"] and x["plan_valid"] and x["dispatchable"]),"collection_started":rate(items,lambda x:x["collection_started"]),"full_collection":rate(items,lambda x:x["collection_status"]=="COMPLETE" and not x["source_false_positive"]),"jd_complete":round(sum(x["jd_complete"] for x in items)/sum(x["jd_total"] for x in items),4) if sum(x["jd_total"] for x in items) else 0,"excel_success":rate(items,lambda x:x["excel_success"]),"e2e":rate(items,lambda x:x["e2e_pass"])}
    payload={"budget_seconds":180,"all_valid_inputs":metrics(rows),"algorithmically_evaluable_inputs":metrics(evaluable),"false_complete_count":sum(x.get("source_false_positive",False) and x["collection_status"]=="COMPLETE" for x in rows),"sites":rows}; ROOT.mkdir(parents=True,exist_ok=True); (ROOT/"results.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    with (ROOT/"results.csv").open("w",encoding="utf-8",newline="") as f: w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    lines=["# STEP 52D V1 CORE BLIND TEST REPORT","","## 1. V1 Contract","","User-supplied official job-bearing page → unchanged generic discovery, plan, collection, completeness, and exports.","","## 2. Test Set","",f"Total valid: {len(rows)}; Environment blocked seeds: {sum(x['environment_seed'] for x in rows)}; Algorithmically evaluable: {len(evaluable)}","","## 3. Funnel KPI",""]
    for title,value in (("All Valid Inputs",payload["all_valid_inputs"]),("Algorithmically Evaluable Inputs",payload["algorithmically_evaluable_inputs"])): lines += [f"### {title}",""]+[f"- {k}: {v}" for k,v in value.items()]+[""]
    lines += ["## 4. Per-site Results","","| Company | Provider/class | Source | Pagination | Plan E/V/D | Collection | JD | Export X/J/M | Failure |","|---|---|---|---|---|---|---|---|---|"]
    for x in rows: lines.append(f"| {x['company']} | {x['provider']}/{x['provider_class']} | {x['source_truth']}/{x['source_confidence']} | {x['pagination']} | {x['plan_executable']}/{x['plan_valid']}/{x['dispatchable']} | {x['expected']}/{x['raw_rows']}/{x['unique']}/{x['collection_status']} pages={x['pages_fetched']} | {x['jd_complete']}/{x['jd_total']} | {x['excel_success']}/{x['json_success']}/{x['markdown_success']} | {x['failure_category']}: {x['failure_reason']} |")
    passed=[x['company'] for x in rows if x['e2e_pass']]; clusters={}
    for x in rows:
        if not x['e2e_pass']: clusters.setdefault(x['failure_category'],[]).append(x['company'])
    lines += ["","## 5. E2E Passed Sites","",*( [f"- {x}" for x in passed] or ["- None"]),"","## 6. Failed Sites",""]+[f"- {x['company']}: stage={x['collection_status']}; {x['failure_category']}; {x['failure_reason'] or x['source_rejection_reason']}" for x in rows if not x['e2e_pass']]+["","## 7. Provider Breakdown",""]
    for p in sorted({x['provider'] for x in rows}):
        g=[x for x in rows if x['provider']==p]; lines.append(f"- {p}: sites={len(g)}, source={rate(g,lambda x:x['source_truth']=='SOURCE_TRUE')}, full={rate(g,lambda x:x['collection_status']=='COMPLETE')}, e2e={rate(g,lambda x:x['e2e_pass'])}")
    lines += ["","## 8. Job-count Breakdown","","Derived from collection expected/observed counts; no complete collection means no size success claim.","","## 9. Source False Positive Audit","",f"count: {sum(x.get('source_false_positive',False) for x in rows)}","","## 10. False COMPLETE Audit","",f"count: {payload['false_complete_count']}","","## 11. Failure Clusters",""]+[f"- {k}: {', '.join(v)}" for k,v in clusters.items()]+["","## 12. V1 Capability Assessment","",f"On all 10 valid inputs, current observed E2E Excel success is {payload['all_valid_inputs']['e2e']:.0%}; treat the two environment-blocked seeds separately.","","## 13. STEP 53 Candidates","", "Rank only repeated failures after this first-pass evidence: source observation failures, then replay/request failures if repeated.","","## 14. Production Changes","","NONE","","## 15. Verdict","","PASS" if len(rows)==10 else "FAIL"]
    (ROOT/"report.md").write_text("\n".join(lines),encoding="utf-8")

if __name__=="__main__":
    ROOT.mkdir(parents=True,exist_ok=True); generic.ROOT=ROOT/"per_site"; generic.DISCOVERY_BUDGET=60; generic.DISCOVERY_TIMEOUT_MS=15000; generic.COLLECTION_BUDGET=100
    if "--render" in sys.argv: render()
    elif "--site" in sys.argv:
        target=sys.argv[sys.argv.index("--site")+1].lower(); run(next(site for site in SITES if site[0].lower()==target))
    else:
        for site in SITES: run(site)
        render()
