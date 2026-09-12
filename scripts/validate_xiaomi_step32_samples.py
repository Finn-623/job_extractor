"""Validate five observed public detail routes and their exact list bindings."""
from __future__ import annotations
import argparse,json,re,time
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

def main():
    p=argparse.ArgumentParser();p.add_argument("--evidence",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    evidence=json.loads(a.evidence.read_text(encoding="utf-8"));source=next(x for x in evidence["responses"] if "/search/job/posts" in x["url"]);records=source["sample_records"]
    links=evidence["relevant_controls"];selected=[];seen=set()
    for record in records:
        city=(record.get("city_info") or {}).get("name");function=(record.get("job_function") or {}).get("name");key=(city,function)
        if key in seen:continue
        match=next((x for x in links if str(record["id"]) in x.get("href","") and record["title"] in x.get("text","")),None)
        if match:selected.append((record,urljoin(evidence["url"],match["href"])));seen.add(key)
        if len(selected)==5:break
    rows=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True);context=browser.new_context();page=context.new_page();page.set_default_timeout(20000)
        for record,url in selected:
            captured=[]
            def observe(response):
                if response.request.resource_type in ("xhr","fetch") and f"/job/posts/{record['id']}" in response.url:
                    try:captured.append((response.status,response.json()))
                    except Exception:pass
            page.on("response",observe);started=time.perf_counter();response=page.goto(url,wait_until="domcontentloaded",timeout=20000);page.wait_for_timeout(3000)
            body=page.locator("body").inner_text();payload=captured[-1][1] if captured else {};detail=(payload.get("data") or {}).get("job_post_detail") or {}
            detail_title=detail.get("title");detail_id=str(detail.get("id") or record["id"]);description=detail.get("description") or "";requirement=detail.get("requirement") or ""
            dom_has=record["title"] in body and (description[:50] in body if description else False)
            rows.append({"id":record["id"],"title":record["title"],"city":(record.get("city_info") or {}).get("name"),"category":(record.get("job_function") or {}).get("name"),"url":url,"http_status":response.status if response else None,"xhr_status":captured[-1][0] if captured else None,"detail_id":detail_id,"id_match":detail_id==str(record["id"]),"detail_title":detail_title,"title_match":detail_title==record["title"],"source_type":"HYBRID" if captured and dom_has else "XHR_DETAIL" if captured else "DOM_DETAIL" if dom_has else "NO_DETAIL_CONTENT","body_length":len(body),"full_jd_length":len(description)+len(requirement),"responsibilities":bool(description),"requirements":bool(requirement),"elapsed":time.perf_counter()-started})
            page.remove_listener("response",observe)
        browser.close()
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(rows,ensure_ascii=False))
if __name__=="__main__":main()
