"""Bounded browser forensics for the public recruitment surface."""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
from urllib.parse import parse_qsl,urlsplit,urlunsplit
from playwright.sync_api import sync_playwright
from job_extractor.discovery.network_analyzer import get_path,list_observation,response_shape,sanitized_values,safe_url

def arrays(value,path="$",depth=0):
    found=[]
    if depth>8:return found
    if isinstance(value,list):
        sample=next((x for x in value if isinstance(x,dict)),None)
        found.append({"path":path,"count":len(value),"sample_keys":sorted(sample) if sample else [],"job_likeness":sum(any(token in str(k).lower() for token in ("job","position","title","location","department","requisition")) for k in (sample or {}))})
        for item in value[:3]:found.extend(arrays(item,path+"[]",depth+1))
    elif isinstance(value,dict):
        for key,item in value.items():found.extend(arrays(item,f"{path}.{key}",depth+1))
    return found

def main():
    parser=argparse.ArgumentParser();parser.add_argument("url");parser.add_argument("--output",type=Path,required=True);parser.add_argument("--interaction",choices=("none","scroll","click","click_scroll","paginate"),default="click_scroll");args=parser.parse_args()
    started=time.perf_counter();phase=["INITIAL"];events=[];responses=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);context=browser.new_context();page=context.new_page();page.set_default_timeout(10000)
        def observe(response):
            request=response.request
            if request.resource_type not in ("xhr","fetch"):return
            content=(response.headers.get("content-type") or "").lower()
            if "json" not in content:return
            try:payload=response.json()
            except Exception:return
            clean,query=safe_url(request.url)
            try:body=request.post_data_json if isinstance(request.post_data_json,dict) else {}
            except Exception:body={}
            shape=response_shape(payload);path,count,sample=list_observation(payload)
            responses.append({"ms":round((time.perf_counter()-started)*1000),"phase":phase[0],"url":clean,"method":request.method,"status":response.status,"request_content_type":request.headers.get("content-type"),"query":query,"body":sanitized_values(body),"top_level_type":type(payload).__name__,"top_level_keys":sorted(payload) if isinstance(payload,dict) else [],"arrays":arrays(payload),"inferred_list_path":path,"inferred_count":count,"inferred_sample_keys":sorted(sample[0]) if sample and isinstance(sample[0],dict) else [],"sample_records":sanitized_values({"records":sample[:20]}).get("records",[]),"inferred_total_field":shape.get("total_field"),"inferred_total":get_path(payload,shape.get("total_field")),"response_shape":shape})
        page.on("response",observe)
        phase[0]="NAVIGATION";page.goto(args.url,wait_until="domcontentloaded",timeout=20000);page.wait_for_timeout(5000)
        controls=page.evaluate("""() => [...document.querySelectorAll('a[href],button,[role=button],[role=link]')].map((n,i)=>({i,text:(n.innerText||n.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' '),href:n.getAttribute('href')||'',visible:!!(n.offsetWidth||n.offsetHeight)})).filter(x=>x.visible&&x.text).slice(0,500)""")
        relevant=[x for x in controls if any(word in (x["text"]+" "+x["href"]).lower() for word in ("职位","岗位","job","position","campus","校招","校园"))]
        target=next((x for x in relevant if x["text"].strip() in ("职位","岗位","全部职位","查看职位")),None)
        if target and args.interaction in ("click","click_scroll"):
            phase[0]="SEMANTIC_JOB_ENTRY";events.append({"action":"click","control":target,"before_url":page.url})
            try:page.locator('a[href],button,[role=button],[role=link]').nth(target["i"]).click(timeout=5000)
            except Exception as exc:events.append({"action":"click_failed","error":type(exc).__name__})
            page.wait_for_timeout(8000);events.append({"action":"after_click","url":page.url})
        if args.interaction in ("scroll","click_scroll"):
            phase[0]="CONTROLLED_SCROLL";page.evaluate("window.scrollTo(0,document.body.scrollHeight)");page.wait_for_timeout(5000)
        if args.interaction=="paginate":
            phase[0]="PAGINATION_READY";page.wait_for_timeout(10000);page.evaluate("window.scrollTo(0,document.body.scrollHeight)");page.wait_for_timeout(1000)
        pagination_controls=page.evaluate("""() => [...document.querySelectorAll('button,a,[role=button]')].map((n,i)=>({i,text:(n.innerText||'').trim(),aria:n.getAttribute('aria-label')||'',cls:n.className||'',disabled:!!n.disabled||n.getAttribute('aria-disabled')==='true'})).filter(x=>/next|pagination|page|\u4e0b\u4e00\u9875|\u4e0b\u9875/i.test(x.text+' '+x.aria+' '+x.cls)).slice(0,100)""")
        if args.interaction=="paginate":
            next_locator=page.locator('.atsx-pagination-next:not(.atsx-pagination-disabled) button,.ant-pagination-next:not(.ant-pagination-disabled) button,a[rel=next]').first
            if next_locator.count():
                phase[0]="PAGINATION_CLICK";events.append({"action":"pagination_click","selector":"semantic next-page control"})
                next_locator.click(timeout=5000);page.wait_for_timeout(5000)
        try:pagination_html=page.locator('[class*=pagination]').first.evaluate("n=>n.outerHTML")[:10000]
        except Exception:pagination_html=None
        result={"url":args.url,"final_url":page.url,"title":page.title(),"elapsed":time.perf_counter()-started,"relevant_controls":relevant[:100],"pagination_controls":pagination_controls,"pagination_html":pagination_html,"events":events,"responses":responses}
        browser.close()
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps({"responses":len(responses),"events":events,"final_url":result["final_url"],"elapsed":result["elapsed"]},ensure_ascii=False))

if __name__=="__main__":main()
