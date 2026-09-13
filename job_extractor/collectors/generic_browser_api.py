from __future__ import annotations
from datetime import datetime
import re
from time import perf_counter
from typing import Any
from urllib.parse import urlencode
from job_extractor.browser import BrowserRuntime
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.collectors.generic_detail import extract_path
from job_extractor.discovery.network_analyzer import safe_url
from job_extractor.models import CollectionResult
from job_extractor.planning.models import CollectionPlan

class _BrowserReplayHttpError(RuntimeError):
    def __init__(self,status_code:int):
        super().__init__(f"HTTP_{status_code}");self.response=type("ResponseMeta",(),{"status_code":status_code})()

class _Response:
    def __init__(self,payload:Any,status_code:int=200):self.payload=payload;self.status_code=status_code
    def raise_for_status(self):
        if not 200<=self.status_code<300:raise _BrowserReplayHttpError(self.status_code)
    def json(self):return self.payload

class _BrowserClient:
    """Replay only the observed request inside the official page context."""
    def __init__(self,page):self.page=page
    def request(self,method:str,url:str,**kwargs):
        params=kwargs.get("params") or {}
        if params:url+=("&" if "?" in url else "?")+urlencode(params)
        argument={"url":url,"method":method,"body":kwargs.get("json") or kwargs.get("data")}
        if kwargs.get("data") is not None:argument["form"]=True
        result=self.page.evaluate("""async ({url,method,body,form}) => {
          const options={method,credentials:'same-origin'};
          if(method!=='GET'){
            options.headers={'content-type':form?'application/x-www-form-urlencoded':'application/json'};
            options.body=form?new URLSearchParams(body||{}).toString():JSON.stringify(body||{});
          }
          const response=await fetch(url,options);let payload=null;
          try{payload=await response.json();}catch(_error){}
          return {status:response.status,payload};
        }""",argument)
        # Backward-compatible with lightweight/fake Page implementations and
        # older browser helpers that return the JSON payload directly.
        if not isinstance(result,dict) or "status" not in result:
            return _Response(result)
        return _Response(result.get("payload"),int(result.get("status") or 0))

class _SequenceClient:
    def __init__(self,payloads):self.payloads=iter(payloads)
    def request(self,*args,**kwargs):return _Response(next(self.payloads))

class GenericBrowserApiCollector:
    def __init__(self,plan:CollectionPlan,browser_factory=BrowserRuntime):self.plan=plan;self.browser_factory=browser_factory
    def collect(self):
        if self.plan.pagination_type not in ("PAGE","OFFSET") and self.plan.browser_trigger=="AUTO_PAGINATION":return self._collect_observed_pages()
        with self.browser_factory() as runtime:
            runtime.page.goto(self.plan.source_url,wait_until="domcontentloaded")
            result=GenericHttpCollector(self.plan,client=_BrowserClient(runtime.page)).collect()
            result.metrics.browser_pages_opened=runtime.pages_opened
            result.metrics.browser_requests_observed=runtime.requests_observed
            result.metrics.collection_mode="BROWSER_SESSION_REPLAY"
            return result
    def _collect_observed_pages(self):
        started=datetime.now();clock=perf_counter();payloads=[];capture_error=None
        endpoint=safe_url(self.plan.list_endpoint or "")[0];method=self.plan.list_method or "GET"
        predicate=lambda response:safe_url(response.url)[0]==endpoint and response.request.method==method and "json" in (response.headers.get("content-type") or "").lower()
        try:
            with self.browser_factory() as runtime:
                with runtime.page.expect_response(predicate,timeout=runtime.timeout_ms) as info:
                    runtime.page.goto(self.plan.source_url,wait_until="domcontentloaded")
                payloads.append(info.value.json());runtime.page.wait_for_timeout(300);self._bind_visible_links(runtime.page,payloads[-1]);total=extract_path(payloads[0],self.plan.total_field)
                records=extract_path(payloads[0],self.plan.list_path);observed=len(records) if isinstance(records,list) else 0
                for _ in range(1,1000):
                    if isinstance(total,int) and observed>=total:break
                    next_page=runtime.page.locator('li[class*="pagination-next"]:not([aria-disabled="true"]):not([class*="disabled"]) a,li[class*="pagination-next"]:not([aria-disabled="true"]):not([class*="disabled"]) button,a[rel="next"],button[aria-label*="Next" i],button[aria-label*="下一页"]').first
                    if not next_page.count():capture_error="PAGINATION_CONTROL_NOT_FOUND";break
                    try:
                        with runtime.page.expect_response(predicate,timeout=runtime.timeout_ms) as info:next_page.click()
                        payload=info.value.json();runtime.page.wait_for_timeout(300);self._bind_visible_links(runtime.page,payload);batch=extract_path(payload,self.plan.list_path);payloads.append(payload);observed+=len(batch) if isinstance(batch,list) else 0
                    except Exception:capture_error="PAGINATION_RESPONSE_NOT_OBSERVED";break
        except Exception as exc:
            return CollectionResult(source_url=self.plan.source_url,platform="generic",company=self.plan.company,scope=self.plan.scope,status="FAILED",errors=[f"BROWSER_API_CAPTURE_FAILED reason={type(exc).__name__}"],started_at=started,finished_at=datetime.now())
        replay_plan=self.plan.model_copy(update={"mode":"HTTP_API","browser_trigger":None,"query_values":{}})
        result=GenericHttpCollector(replay_plan,client=_SequenceClient(payloads)).collect()
        result.metrics.browser_pages_opened=1;result.metrics.browser_requests_observed=len(payloads);result.metrics.elapsed_seconds=perf_counter()-clock
        if capture_error:
            result.errors.append(f"{capture_error} reason=official pagination could not be completed");result.status="INCOMPLETE"
        return result
    def _bind_visible_links(self,page,payload):
        records=extract_path(payload,self.plan.list_path)
        if not isinstance(records,list):return
        try:links=page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a=>({url:a.href,text:(a.innerText||'').trim().replace(/\\s+/g,' ')}))")
        except Exception:return
        for record in records:
            if not isinstance(record,dict):continue
            jid=extract_path(record,self.plan.job_id_field);title=extract_path(record,self.plan.job_title_field)
            if jid is None or not isinstance(title,str):continue
            pattern=re.compile(rf"(?<![A-Za-z0-9]){re.escape(str(jid))}(?![A-Za-z0-9])")
            matches=[x["url"] for x in links if isinstance(x,dict) and isinstance(x.get("url"),str) and pattern.search(x["url"]) and title.strip().lower() in str(x.get("text") or "").lower()]
            if len(set(matches))==1:record["_generic_detail_url"]=matches[0]
