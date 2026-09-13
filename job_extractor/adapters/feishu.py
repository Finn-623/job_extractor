from __future__ import annotations
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from time import perf_counter,sleep
from urllib.parse import urlparse
from job_extractor.adapters.base import BaseAdapter
from job_extractor.adapters.feishu_config import FeishuScope
from job_extractor.browser import BrowserRuntime, BrowserRuntimeError
from job_extractor.models import CollectionResult, Job
from job_extractor.runtime import MetricsRecorder, make_error

class FeishuResponseError(RuntimeError): pass

class _MetadataParser(HTMLParser):
    def __init__(self): super().__init__(); self.capture=False; self.parts=[]
    def handle_starttag(self,tag,attrs):
        self.capture=tag=="script" and dict(attrs).get("id")=="js-websiteInfo"
    def handle_endtag(self,tag):
        if tag=="script": self.capture=False
    def handle_data(self,data):
        if self.capture: self.parts.append(data)

def _i18n(value: Any) -> str | None:
    if isinstance(value,str): return value
    if isinstance(value,dict):
        for key in ("i18n_name","i18n","zh_cn","name","en_us"):
            if isinstance(value.get(key),str) and value[key]: return value[key]
    return None

def _safe_business_data(value: Any) -> Any:
    forbidden=("token","cookie","authorization","signature","session","secret","csrf")
    if isinstance(value,dict): return {k:_safe_business_data(v) for k,v in value.items() if not any(x in k.lower() for x in forbidden)}
    if isinstance(value,list): return [_safe_business_data(v) for v in value]
    return value

class FeishuAdapter(BaseAdapter):
    platform_name="feishu"; priority=30
    LIST_PATH="/api/v1/search/job/posts"; DETAIL_PREFIX="/api/v1/job/posts/"
    def __init__(self,page_size=10,max_pages=1000,browser_factory=BrowserRuntime,deadline_seconds=180.0,
                 replay_page_size=100,max_retries=2,clock=perf_counter,sleep_fn=sleep):
        self.page_size=page_size; self.max_pages=max_pages; self.browser_factory=browser_factory
        self.deadline_seconds=max(0.0,deadline_seconds);self.replay_page_size=max(page_size,replay_page_size)
        self.max_retries=max(0,max_retries);self.clock=clock;self.sleep_fn=sleep_fn
        self.scope=None; self.recorder=MetricsRecorder(); self.detail_strategy="LIST_SUFFICIENT"
        self.list_requests=self.page_count=self.details_attempted=self.details_succeeded=self.details_failed=0
        self.elapsed_seconds=0.0; self.browser_pages_opened=self.browser_requests_observed=0
        self.initial_load_seconds=self.list_pagination_seconds=self.detail_fallback_seconds=self.normalize_seconds=0.0
        self.retry_count=0;self.termination_reason=None;self.collection_mode="BROWSER_UI_CAPTURE";self.negotiated_page_size=page_size
        self.canonical_snapshot=True
    @classmethod
    def match(cls,url):
        h=(urlparse(url).hostname or "").lower(); return h=="jobs.feishu.cn" or h.endswith(".jobs.feishu.cn")
    @staticmethod
    def parse_scope(page_html):
        p=_MetadataParser(); p.feed(page_html)
        try: data=json.loads("".join(p.parts)); website=data["website_info"]; tenant=data.get("tenant_info") or {}
        except Exception as exc: raise FeishuResponseError("FEISHU_METADATA_INVALID") from exc
        process=website.get("process_type")
        if process not in (1,2): raise FeishuResponseError("FEISHU_SCOPE_UNKNOWN")
        return FeishuScope(str(website.get("id") or ""),str(website.get("path") or ""),process,
                           "social" if process==1 else "campus",tenant.get("tenant_name"))
    @staticmethod
    def parse_list(payload):
        if not isinstance(payload,dict) or payload.get("code") not in (None,0): raise FeishuResponseError("FEISHU_LIST_API_ERROR")
        data=payload.get("data")
        if not isinstance(data,dict) or not isinstance(data.get("job_post_list"),list) or not isinstance(data.get("count"),int):
            raise FeishuResponseError("FEISHU_LIST_SCHEMA_INVALID")
        return data["job_post_list"],data["count"]
    @staticmethod
    def parse_detail(payload):
        if not isinstance(payload,dict) or payload.get("code") not in (None,0): raise FeishuResponseError("FEISHU_DETAIL_API_ERROR")
        detail=(payload.get("data") or {}).get("job_post_detail")
        if not isinstance(detail,dict): raise FeishuResponseError("FEISHU_DETAIL_SCHEMA_INVALID")
        return detail
    @staticmethod
    def _lines(value): return [x.strip() for x in str(value or "").replace("\r","").split("\n") if x.strip()]
    @classmethod
    def normalize_job(cls,raw,url,scope):
        jid=raw.get("id"); title=raw.get("title")
        if not jid or not isinstance(title,str): return None
        description=str(raw.get("description") or ""); requirement=str(raw.get("requirement") or "")
        cities=[]
        for city in raw.get("city_list") or []:
            name=_i18n(city)
            if name and name not in cities: cities.append(name)
        recruit=raw.get("recruit_type") or {}; parent=recruit.get("parent") or {}
        observed=_i18n(parent) or _i18n(recruit)
        category=_i18n(raw.get("job_function")) or _i18n(raw.get("job_category"))
        post_info=raw.get("job_post_info") or {}; degree=_i18n(post_info.get("required_degree") or post_info.get("education"))
        detail=f"{urlparse(url).scheme}://{urlparse(url).netloc}/{scope.website_path}/position/detail/{jid}"
        stamp=raw.get("publish_time"); published=None
        if isinstance(stamp,(int,float)): published=datetime.fromtimestamp(stamp/1000,timezone.utc).isoformat()
        return Job(company=scope.company,job_id=str(jid),job_title=title,job_category=category,
            department=_i18n(raw.get("department_info") or post_info.get("department")),locations=cities,
            recruitment_type=observed,education=degree,headcount=raw.get("vacancies") if isinstance(raw.get("vacancies"),int) else None,
            responsibilities=cls._lines(description),requirements=cls._lines(requirement),
            full_jd="\n\n".join(x for x in (description,requirement) if x) or None,
            apply_url=detail,detail_url=detail,source_url=url,publish_date=published,raw_data=_safe_business_data(raw))
    def _sync_metrics(self):
        m=self.recorder.finish(); self.list_requests=m.list_requests; self.page_count=m.list_pages
        self.details_attempted=m.details_attempted; self.details_succeeded=m.details_succeeded; self.details_failed=m.details_failed
        self.elapsed_seconds=m.elapsed_seconds
        self.retry_count=m.retry_count;self.termination_reason=m.termination_reason;self.collection_mode=m.collection_mode or self.collection_mode
    @staticmethod
    def _active_replay(page,body,headers=None):
        return page.evaluate("""async ({body,headers}) => {
          const response=await fetch('/api/v1/search/job/posts',{method:'POST',credentials:'same-origin',headers:Object.assign({'content-type':'application/json'},headers||{}),body:JSON.stringify(body)});
          if(!response.ok)throw new Error(`HTTP_${response.status}`);return await response.json();
        }""",{"body":body,"headers":headers or {}})
    # Browser-managed or fingerprint-only headers are never forwarded on replay.
    NON_FORWARDABLE_HEADERS=("host","connection","content-length","content-type","accept",
                             "accept-encoding","accept-language","accept-charset","user-agent",
                             "cookie","cookie2","origin","referer","date","dnt","expect",
                             "keep-alive","te","trailer","transfer-encoding","upgrade","via",
                             "upgrade-insecure-requests","pragma","cache-control",
                             "sec-ch-ua","sec-ch-ua-mobile","sec-ch-ua-platform",
                             "sec-fetch-site","sec-fetch-mode","sec-fetch-dest","sec-fetch-user")
    @classmethod
    def _replay_headers(cls,captured_headers):
        """Generic capture->replay header forwarding: scope-bearing functional
        headers (e.g. website-path) must survive into the replay template;
        browser-managed and fingerprint-only headers must not be copied."""
        if not isinstance(captured_headers,dict): return {}
        return {str(key).lower():str(value) for key,value in captured_headers.items()
                if str(key).lower() not in cls.NON_FORWARDABLE_HEADERS}
    def _finish(self,url,started,raws,expected,errors):
        normalize_started=perf_counter(); unique={}
        for raw in raws:
            if raw.get("id") is not None: unique.setdefault(str(raw["id"]),raw)
        jobs=[]; leaks=0
        for raw in unique.values():
            recruit=raw.get("recruit_type") or {}; parent=recruit.get("parent") or {}
            parent_id=str(parent.get("id") or "")
            if self.scope and ((self.scope.process_type==1 and parent_id!="1") or (self.scope.process_type==2 and parent_id=="1")): leaks+=1
            job=self.normalize_job(raw,url,self.scope) if self.scope else None
            if job: jobs.append(job)
        if leaks: errors.append(make_error("SCOPE_LEAK","unexpected recruitment type",count=leaks))
        self.normalize_seconds=perf_counter()-normalize_started
        status="FAILED" if expected is None else ("COMPLETE" if len(raws)==expected==len(jobs) and not errors else "INCOMPLETE")
        return CollectionResult(source_url=url,platform="feishu",company=self.scope.company if self.scope else None,
            total_expected=expected,total_fetched=len(raws),total_unique=len(jobs),status=status,jobs=jobs,
            errors=errors,metrics=self.recorder.metrics,started_at=started,finished_at=datetime.now())
    def collect(self,url):
        started=datetime.now(); errors=[]; raws=[]; expected=None; runtime=None;clock=self.clock();deadline=clock+self.deadline_seconds;termination=None
        self.recorder=MetricsRecorder(); self.recorder.set_page_size(self.page_size); self.recorder.set_jd_strategy("LIST_SUFFICIENT")
        try:
            with self.browser_factory() as runtime:
                if hasattr(runtime,"observe_only"): runtime.observe_only(self.LIST_PATH,self.DETAIL_PREFIX)
                phase=self.clock()
                first=runtime.open_and_capture(url,self.LIST_PATH,"POST"); self.recorder.record_list_request(); self.recorder.record_page()
                self.scope=self.parse_scope(runtime.page.content()); batch,expected=self.parse_list(first.json)
                seen_ids={str(x.get("id")) for x in batch if x.get("id") is not None}
                self.initial_load_seconds=self.clock()-phase; phase=self.clock()
                prior_ids=None
                try:request_template=json.loads(first.post_data) if first.post_data else None
                except Exception:request_template=None
                replay_headers=self._replay_headers(first.request_headers)
                active_replay=isinstance(request_template,dict) and "offset" in request_template and "limit" in request_template
                self.collection_mode="BROWSER_SESSION_REPLAY" if active_replay else "BROWSER_UI_CAPTURE"
                self.recorder.set_collection_mode(self.collection_mode)
                # Canonical snapshot (STEP49): active replay of offset=0 with the
                # captured template becomes the collection baseline. The discovery
                # capture stays discovery-only evidence; NIO scope audit proved the
                # replay cohort is deterministic while the discovery total is not,
                # so all later page totals are reconciled against canonical_total.
                if active_replay and self.canonical_snapshot:
                    snapshot=None
                    for attempt in range(self.max_retries+1):
                        self.recorder.record_list_request()
                        try:snapshot=self._active_replay(runtime.page,dict(request_template),replay_headers);break
                        except Exception:
                            if attempt>=self.max_retries:break
                            delay=min(0.25*(2**attempt),max(0.0,deadline-self.clock()));self.recorder.record_retry(delay)
                            if delay:self.sleep_fn(delay)
                    if snapshot is not None:
                        try:snapshot_batch,snapshot_total=self.parse_list(snapshot)
                        except FeishuResponseError:snapshot_batch,snapshot_total=None,None
                        if snapshot_batch and snapshot_total is not None:
                            expected=snapshot_total;batch=snapshot_batch
                            seen_ids={str(x.get("id")) for x in snapshot_batch if x.get("id") is not None}
                            self.recorder.set_page_size(len(snapshot_batch))
                        else:snapshot=None
                    if snapshot is None:
                        # Scope uncertain -> keep discovery baseline; the error
                        # below forces INCOMPLETE regardless of final counts.
                        errors.append(make_error("CANONICAL_SNAPSHOT_FAILED","offset=0 replay unavailable; discovery baseline kept"))
                raws.extend(batch)
                self.recorder.record_rows(len(batch),len(seen_ids))
                next_offset=int(request_template.get("offset",0))+len(batch) if active_replay else 0
                while len(seen_ids)<expected and self.recorder.metrics.list_pages<self.max_pages:
                    if self.clock()>=deadline:
                        errors.append(make_error("COLLECTION_DEADLINE_EXCEEDED","global collection deadline reached"));termination="DEADLINE_EXCEEDED";break
                    ids=tuple(str(x.get("id")) for x in batch)
                    if not batch:termination="EMPTY_PAGE";break
                    if ids==prior_ids: errors.append(make_error("PAGINATION_NO_PROGRESS","repeated page"));termination="NO_PROGRESS";break
                    prior_ids=ids
                    if active_replay:
                        replay_body=dict(request_template);replay_body["offset"]=next_offset;replay_body["limit"]=self.replay_page_size
                        payload=None
                        for attempt in range(self.max_retries+1):
                            self.recorder.record_list_request()
                            try:payload=self._active_replay(runtime.page,replay_body,replay_headers);break
                            except Exception:
                                if attempt>=self.max_retries:break
                                delay=min(0.25*(2**attempt),max(0.0,deadline-self.clock()));self.recorder.record_retry(delay)
                                if delay:self.sleep_fn(delay)
                        if payload is None:
                            errors.append(make_error("PAGE_REQUEST_RETRIES_EXHAUSTED","browser-session replay failed"));termination="REQUEST_FAILED";break
                        batch,total=self.parse_list(payload);self.negotiated_page_size=self.replay_page_size
                        next_offset+=self.replay_page_size
                    else:
                        nxt=runtime.page.locator(".atsx-pagination-next")
                        if nxt.count()!=1 or nxt.get_attribute("aria-disabled")=="true":termination="UI_CONTROL_EXHAUSTED";break
                        capture=runtime.capture_after(self.LIST_PATH,"POST",lambda:nxt.click()); self.recorder.record_list_request()
                        batch,total=self.parse_list(capture.json)
                    self.recorder.record_page()
                    if total!=expected: errors.append(make_error("COUNT_CHANGED","page total changed")); break
                    previous=len(seen_ids);raws.extend(batch);seen_ids.update(str(x.get("id")) for x in batch if x.get("id") is not None)
                    self.recorder.record_rows(len(batch),len(seen_ids)-previous)
                    if batch and len(seen_ids)==previous:
                        errors.append(make_error("PAGINATION_NO_PROGRESS","page added no new stable job IDs"));termination="NO_PROGRESS";break
                if len(seen_ids)>=expected:termination="TOTAL_REACHED"
                if len(raws)<expected and self.recorder.metrics.list_pages>=self.max_pages:
                    errors.append(make_error("PAGINATION_LIMIT","max pages reached",max_pages=self.max_pages));termination="MAX_PAGES"
                self.recorder.set_termination(termination or "UNKNOWN")
                self.list_pagination_seconds=self.clock()-phase; phase=self.clock()
                fallback=[i for i,raw in enumerate(raws) if not raw.get("description") or not raw.get("requirement")]
                if fallback:
                    self.detail_strategy="DETAIL_FALLBACK"; self.recorder.set_jd_strategy("DETAIL_FALLBACK")
                for index in fallback:
                    if self.clock()>=deadline:
                        errors.append(make_error("COLLECTION_DEADLINE_EXCEEDED","detail enrichment budget exhausted"));self.recorder.set_termination("DEADLINE_EXCEEDED");break
                    jid=str(raws[index].get("id") or "")
                    if not jid:
                        errors.append(make_error("DETAIL_ERROR","missing job id",index=index)); continue
                    detail_url=f"{urlparse(url).scheme}://{urlparse(url).netloc}/{self.scope.website_path}/position/detail/{jid}"
                    try:
                        capture=runtime.open_and_capture(detail_url,self.DETAIL_PREFIX+jid,"GET")
                        detail=self.parse_detail(capture.json); raws[index]={**raws[index],**detail}
                        self.recorder.record_detail_request(True)
                    except (BrowserRuntimeError,FeishuResponseError) as exc:
                        self.recorder.record_detail_request(False)
                        errors.append(make_error("FEISHU_DETAIL_REQUEST_FAILED",str(exc),job_id=jid))
                self.detail_fallback_seconds=self.clock()-phase
        except BrowserRuntimeError as exc: errors.append(make_error(str(exc),"browser collection failed"))
        except FeishuResponseError as exc: errors.append(make_error(str(exc),"response validation failed"))
        finally:
            if runtime:
                self.browser_pages_opened=runtime.pages_opened; self.browser_requests_observed=runtime.requests_observed
            self._sync_metrics()
        return self._finish(url,started,raws,expected,errors)
