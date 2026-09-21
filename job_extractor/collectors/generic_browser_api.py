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
from job_extractor.discovery.dynamic import wait_for_hydration,wait_for_readiness_consensus
from job_extractor.discovery.sources import trigger_job_page_search
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
    def __init__(self,page,headers=None):self.page=page;self.headers=dict(headers or {})
    def request(self,method:str,url:str,**kwargs):
        params=kwargs.get("params") or {}
        if params:url+=("&" if "?" in url else "?")+urlencode(params)
        argument={"url":url,"method":method,"body":kwargs.get("json") or kwargs.get("data"),"headers":self.headers}
        if kwargs.get("data") is not None:argument["form"]=True
        result=self.page.evaluate("""async ({url,method,body,form,headers}) => {
          const options={method,credentials:'same-origin',headers:{...(headers||{})}};
          if(method!=='GET'){
            options.headers['content-type']=form?'application/x-www-form-urlencoded':'application/json';
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
            headers=runtime.session_headers(self.plan.list_endpoint) if hasattr(runtime,"session_headers") else {}
            result=GenericHttpCollector(self.plan,client=_BrowserClient(runtime.page,headers=headers)).collect()
            result.metrics.browser_pages_opened=runtime.pages_opened
            result.metrics.browser_requests_observed=runtime.requests_observed
            result.metrics.collection_mode="BROWSER_SESSION_REPLAY"
        # A native browser observation is stronger evidence than a request we
        # reconstructed.  Some public portals apply request interceptors that
        # cannot be faithfully recreated with page.evaluate(fetch).  Fall
        # back only when the safe, observed total proves the replay divergent.
        if self._native_pagination_required(result):
            return self._collect_observed_pages()
        return result

    def _native_pagination_required(self,result:CollectionResult)->bool:
        observed=self.plan.observed_total
        return bool(
            self.plan.browser_trigger=="AUTO_PAGINATION"
            and isinstance(observed,int) and observed>=0
            and result.total_expected != observed
        )
    def _collect_observed_pages(self):
        started=datetime.now();clock=perf_counter();payloads=[];capture_error=None
        endpoint=safe_url(self.plan.list_endpoint or "")[0];method=self.plan.list_method or "GET"
        predicate=lambda response:safe_url(response.url)[0]==endpoint and response.request.method==method and "json" in (response.headers.get("content-type") or "").lower()
        try:
            with self.browser_factory() as runtime:
                matched=[]
                def capture(response):
                    if predicate(response):matched.append(response)
                # Listen before navigation because a portal can issue its first
                # list response during hydration or activation.
                runtime.page.on("response",capture)
                runtime.page.goto(self.plan.source_url,wait_until="domcontentloaded")
                wait_for_hydration(runtime.page,lambda:len(matched))
                if not matched:
                    trigger_job_page_search(runtime.page)
                    wait_for_readiness_consensus(runtime.page,lambda:len(matched),timeout_ms=min(runtime.timeout_ms,10000))
                remaining=max(0,int(runtime.timeout_ms-(perf_counter()-clock)*1000))
                while not matched and remaining>0:
                    runtime.page.wait_for_timeout(min(250,remaining))
                    remaining=max(0,int(runtime.timeout_ms-(perf_counter()-clock)*1000))
                if not matched:
                    return self._native_capture_failure("NATIVE_FIRST_RESPONSE_TIMEOUT",started,perf_counter()-clock)
                try:payloads.append(matched[0].json())
                except Exception:
                    return self._native_capture_failure("NATIVE_FIRST_RESPONSE_JSON_READ_FAILED",started,perf_counter()-clock)
                runtime.page.wait_for_timeout(300);self._bind_visible_links(runtime.page,payloads[-1]);total=extract_path(payloads[0],self.plan.total_field)
                records=extract_path(payloads[0],self.plan.list_path);observed=len(records) if isinstance(records,list) else 0
                for _ in range(1,1000):
                    if isinstance(total,int) and observed>=total:break
                    next_page=runtime.page.locator('li[class*="pagination-next"]:not([aria-disabled="true"]):not([class*="disabled"]) a,li[class*="pagination-next"]:not([aria-disabled="true"]):not([class*="disabled"]) button,a[rel="next"],button[aria-label*="Next" i],button[aria-label*="下一页"],button:has-text("Load more"),button:has-text("加载更多")').first
                    click_next=next_page.count()>0
                    if not click_next and not self._can_scroll_for_more(runtime.page):capture_error="PAGINATION_CONTROL_NOT_FOUND";break
                    try:
                        with runtime.page.expect_response(predicate,timeout=runtime.timeout_ms) as info:
                            if click_next:next_page.click()
                            else:self._scroll_for_more(runtime.page)
                        payload=info.value.json();runtime.page.wait_for_timeout(300);self._bind_visible_links(runtime.page,payload);batch=extract_path(payload,self.plan.list_path);payloads.append(payload);observed+=len(batch) if isinstance(batch,list) else 0
                    except Exception:capture_error="PAGINATION_RESPONSE_NOT_OBSERVED";break
        except Exception as exc:
            return CollectionResult(source_url=self.plan.source_url,platform="generic",company=self.plan.company,scope=self.plan.scope,status="FAILED",errors=[f"BROWSER_API_CAPTURE_FAILED reason={type(exc).__name__}"],started_at=started,finished_at=datetime.now())
        replay_plan=self.plan.model_copy(update={"mode":"HTTP_API","browser_trigger":None,"query_values":{}})
        result=GenericHttpCollector(replay_plan,client=_SequenceClient(payloads)).collect()
        result.metrics.browser_pages_opened=1;result.metrics.browser_requests_observed=len(payloads);result.metrics.elapsed_seconds=perf_counter()-clock
        result.metrics.collection_mode="NATIVE_BROWSER_LIST"
        if capture_error:
            result.errors.append(f"{capture_error} reason=official pagination could not be completed");result.status="INCOMPLETE"
        return result
    def _native_capture_failure(self,reason,start,elapsed):
        result=CollectionResult(source_url=self.plan.source_url,platform="generic",company=self.plan.company,scope=self.plan.scope,status="FAILED",errors=[reason],started_at=start,finished_at=datetime.now())
        result.metrics.collection_mode="NATIVE_BROWSER_LIST";result.metrics.elapsed_seconds=elapsed
        return result
    def _can_scroll_for_more(self,page)->bool:
        try:
            return bool(page.evaluate("""() => {
              const nodes=[document.scrollingElement,...document.querySelectorAll('*')];
              return nodes.some(n => n && n.scrollHeight > n.clientHeight + 8 && n.scrollTop < n.scrollHeight - n.clientHeight - 2);
            }"""))
        except Exception:return False
    def _scroll_for_more(self,page)->None:
        page.evaluate("""() => {
          const nodes=[document.scrollingElement,...document.querySelectorAll('*')]
            .filter(n => n && n.scrollHeight > n.clientHeight + 8 && n.scrollTop < n.scrollHeight - n.clientHeight - 2);
          const target=nodes.sort((a,b)=>(b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight))[0];
          if(target) target.scrollTop=Math.min(target.scrollTop+Math.max(target.clientHeight*.85, 400),target.scrollHeight);
        }""")
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
