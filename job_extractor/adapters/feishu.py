from __future__ import annotations
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from time import perf_counter
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
    def __init__(self,page_size=10,max_pages=1000,browser_factory=BrowserRuntime):
        self.page_size=page_size; self.max_pages=max_pages; self.browser_factory=browser_factory
        self.scope=None; self.recorder=MetricsRecorder(); self.detail_strategy="LIST_SUFFICIENT"
        self.list_requests=self.page_count=self.details_attempted=self.details_succeeded=self.details_failed=0
        self.elapsed_seconds=0.0; self.browser_pages_opened=self.browser_requests_observed=0
        self.initial_load_seconds=self.list_pagination_seconds=self.detail_fallback_seconds=self.normalize_seconds=0.0
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
    def collect(self,url):
        started=datetime.now(); errors=[]; raws=[]; expected=None; runtime=None
        self.recorder=MetricsRecorder(); self.recorder.set_page_size(self.page_size); self.recorder.set_jd_strategy("LIST_SUFFICIENT")
        try:
            with self.browser_factory() as runtime:
                if hasattr(runtime,"observe_only"): runtime.observe_only(self.LIST_PATH,self.DETAIL_PREFIX)
                phase=perf_counter()
                first=runtime.open_and_capture(url,self.LIST_PATH,"POST"); self.recorder.record_list_request(); self.recorder.record_page()
                self.scope=self.parse_scope(runtime.page.content()); batch,expected=self.parse_list(first.json); raws.extend(batch)
                self.initial_load_seconds=perf_counter()-phase; phase=perf_counter()
                prior_ids=None
                while len(raws)<expected and self.recorder.metrics.list_pages<self.max_pages:
                    ids=tuple(str(x.get("id")) for x in batch)
                    if not batch: break
                    if ids==prior_ids: errors.append(make_error("PAGINATION_LOOP","repeated page")); break
                    prior_ids=ids
                    nxt=runtime.page.locator(".atsx-pagination-next")
                    if nxt.count()!=1 or nxt.get_attribute("aria-disabled")=="true": break
                    capture=runtime.capture_after(self.LIST_PATH,"POST",lambda:nxt.click()); self.recorder.record_list_request(); self.recorder.record_page()
                    batch,total=self.parse_list(capture.json)
                    if total!=expected: errors.append(make_error("COUNT_CHANGED","page total changed")); break
                    raws.extend(batch)
                if len(raws)<expected and self.recorder.metrics.list_pages>=self.max_pages:
                    errors.append(make_error("PAGINATION_LIMIT","max pages reached",max_pages=self.max_pages))
                self.list_pagination_seconds=perf_counter()-phase; phase=perf_counter()
                fallback=[i for i,raw in enumerate(raws) if not raw.get("description") or not raw.get("requirement")]
                if fallback:
                    self.detail_strategy="DETAIL_FALLBACK"; self.recorder.set_jd_strategy("DETAIL_FALLBACK")
                for index in fallback:
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
                self.detail_fallback_seconds=perf_counter()-phase
        except BrowserRuntimeError as exc: errors.append(make_error(str(exc),"browser collection failed"))
        except FeishuResponseError as exc: errors.append(make_error(str(exc),"response validation failed"))
        finally:
            if runtime:
                self.browser_pages_opened=runtime.pages_opened; self.browser_requests_observed=runtime.requests_observed
            self._sync_metrics()
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
            errors=errors,started_at=started,finished_at=datetime.now())
