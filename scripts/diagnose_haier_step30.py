"""Step 30 five-sample public detail diagnostics."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urljoin,urlsplit,urlunsplit

import httpx
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE="https://maker.haier.net"


def safe_url(value:str)->str:
    parsed=urlsplit(value);return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,"",""))


def compact(value:str)->str:return re.sub(r"\s+"," ",value or "").strip()


def main()->None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--jobs",type=Path,required=True)
    parser.add_argument("--ids",nargs="+",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    source=json.loads(args.jobs.read_text(encoding="utf-8"))
    lookup={str(job["job_id"]):job for job in source["jobs"]}
    rows=[]
    with sync_playwright() as playwright,httpx.Client(timeout=30,follow_redirects=True) as client:
        browser=playwright.chromium.launch(headless=True)
        for jid in args.ids:
            job=lookup[jid];raw=job["raw_data"];detail=urljoin(BASE,raw["click_url"])
            http=client.get(detail);html=http.text
            page=browser.new_page();page.set_default_timeout(15_000);xhr=[]
            def observe(response):
                if response.request.resource_type not in ("xhr","fetch"):return
                item={"url":safe_url(response.url),"method":response.request.method,"status":response.status,
                      "content_type":response.headers.get("content-type")}
                if "json" in (item["content_type"] or "").lower():
                    try:
                        payload=response.json();item["top_level_type"]=type(payload).__name__
                        item["top_level_keys"]=sorted(payload) if isinstance(payload,dict) else []
                    except Exception:item["json_error"]=True
                xhr.append(item)
            page.on("response",observe);browser_status=None;timeout=False
            try:
                response=page.goto(detail,wait_until="domcontentloaded",timeout=20_000);browser_status=response.status if response else None
            except PlaywrightTimeoutError:timeout=True
            page.wait_for_timeout(3000)
            try:body=page.locator("body").inner_text(timeout=3000)
            except Exception:body=""
            try:
                regions=page.evaluate("""() => [...document.querySelectorAll('main,article,[class*="detail"],[class*="content"],[class*="job"],[id*="detail"],[id*="content"],[id*="job"]')].map(n=>({tag:n.tagName,id:n.id||'',className:String(n.className||'').slice(0,200),text:(n.innerText||'').trim().replace(/\\s+/g,' ').slice(0,5000),length:(n.innerText||'').trim().length})).filter(x=>x.length>20).sort((a,b)=>b.length-a.length).slice(0,20)""")
            except Exception:regions=[]
            headings=[]
            for pattern,label in ((r"岗位职责|工作职责|职位职责|职责描述","responsibility"),(r"任职要求|任职资格|职位要求|岗位要求|任职条件","requirement")):
                if re.search(pattern,body):headings.append(label)
            rid=re.search(r"/rid/(\d+)\.html",detail)
            rows.append({"id":jid,"source_title":job["job_title"],"department":job.get("department"),"category":raw.get("fun_name"),"location":raw.get("addr"),
                         "detail_url":detail,"rid":rid.group(1) if rid else None,"rid_matches_id":bool(rid and rid.group(1)==jid),
                         "http_status":http.status_code,"http_content_type":http.headers.get("content-type"),"http_length":len(html),
                         "browser_status":browser_status,"browser_timeout":timeout,"page_title":page.title(),"title_match":job["job_title"] in body,
                         "body_text_length":len(body),"headings":headings,"regions":regions,"xhr":xhr,
                         "serialized_json_scripts":page.locator('script[type="application/json"],script#__NEXT_DATA__').count(),
                         "main_text_prefix":compact(regions[0]["text"] if regions else body)[:500]})
            page.close()
        browser.close()
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(rows,ensure_ascii=True))

if __name__=="__main__":main()
