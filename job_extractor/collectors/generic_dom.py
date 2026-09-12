from __future__ import annotations
from datetime import datetime
from time import perf_counter
from urllib.parse import urlsplit
import hashlib
from job_extractor.browser import BrowserRuntime
from job_extractor.models import CollectionResult,CollectionMetrics,Job
from job_extractor.planning.models import CollectionPlan
from job_extractor.runtime import evaluate_data_completeness,make_error
from job_extractor.collectors.generic_detail import split_jd
from job_extractor.discovery.dynamic import wait_for_dynamic_jd
from job_extractor.discovery.dynamic import is_pagination_control
from job_extractor.discovery.dom_semantics import filter_detail_links,repeated_job_links
from job_extractor.identity import job_identity

class GenericDomCollector:
    def __init__(self,plan:CollectionPlan,browser_factory=BrowserRuntime):self.plan=plan;self.browser_factory=browser_factory
    @staticmethod
    def _first(page,selectors):
        for selector in selectors:
            if not selector:continue
            loc=page.locator(selector)
            if loc.count():
                value=(loc.first.get_attribute("content") or "").strip() if selector.startswith("meta[") else loc.first.inner_text().strip()
                if value:return value
        return None
    def collect(self)->CollectionResult:
        started=datetime.now();clock=perf_counter();jobs=[];errors=[];details=0;pages=0;successes=failures=0
        allowed=list(dict.fromkeys(self.plan.allowed_detail_urls))
        try:
            with self.browser_factory() as runtime:
                if self.plan.pagination_type in ("LOAD_MORE","INFINITE_SCROLL"):
                    runtime.page.goto(self.plan.source_url,wait_until="domcontentloaded")
                    stagnant=0;seen_page_urls=set()
                    for _ in range(50):
                        structural=repeated_job_links(runtime.page,self.plan.source_url);current=set(structural);new_current=current-seen_page_urls
                        allowed=list(dict.fromkeys(allowed+filter_detail_links([(x,"") for x in structural],self.plan.source_url,structural)[0]))
                        if self.plan.visible_total is not None and len(allowed)>=self.plan.visible_total:break
                        if new_current:stagnant=0;seen_page_urls.update(current)
                        else:stagnant+=1
                        if stagnant>=2:break
                        if self.plan.pagination_type=="LOAD_MORE":
                            clicked=False
                            controls=runtime.page.locator('button,[role="button"],a[rel="next"],a[aria-label]')
                            for index in range(min(controls.count(),100)):
                                label=((controls.nth(index).inner_text() or "")+" "+(controls.nth(index).get_attribute("aria-label") or "")).strip()
                                if is_pagination_control(label):controls.nth(index).click();clicked=True;break
                            if not clicked:break
                        else:runtime.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        runtime.page.wait_for_timeout(500)
                for url in allowed:
                    if urlsplit(url).hostname!=urlsplit(self.plan.source_url).hostname and urlsplit(url).hostname not in self.plan.trusted_detail_hosts:continue
                    try:
                        runtime.page.goto(url,wait_until="domcontentloaded")
                        pages+=1;details+=1
                        title=self._first(runtime.page,(self.plan.detail_title_selector,'[data-qa*="title"]','[itemprop="title"]','.posting-headline h2','main h1','h1'))
                        if not title:
                            title=self._first(runtime.page,('meta[property="og:title"]','meta[name="twitter:title"]'))
                        if not title:title=self.plan.originating_titles.get(url)
                        if not title:
                            try:
                                candidate=runtime.page.title().strip();title=candidate if candidate and not __import__("re").search(r"(?i)^(jobs?|careers?|search)",candidate) else None
                            except Exception:pass
                        jd,_,wait=wait_for_dynamic_jd(runtime.page,title,self.plan.detail_jd_selector)
                        location=self._first(runtime.page,(self.plan.detail_location_selector,'[data-qa*="location"]','[itemprop="jobLocation"]','[class*="location"]'))
                        department=self._first(runtime.page,(self.plan.detail_department_selector,'[data-qa*="department"]','[class*="department"]','[class*="team"]'))
                        if not title:errors.append(make_error("DOM_PARSE_ERROR","title missing",url=url));failures+=1;continue
                        if not jd:errors.append(make_error("JD_NOT_FOUND","JD_RENDER_TIMEOUT: credible job detail content did not render",url=url,elapsed_ms=wait["elapsed_ms"]));failures+=1;continue
                        responsibilities,requirements=split_jd(jd)
                        raw_identity={"requisitionId":self.plan.originating_ids[url]} if url in self.plan.originating_ids else {}
                        identity=job_identity(raw_identity,url,title,location,department,self.plan.company)
                        jobs.append(Job(company=self.plan.company,job_id=identity["identity_value"],job_title=title,locations=[location] if location else [],department=department,full_jd=jd,
                            responsibilities=responsibilities,requirements=requirements,detail_url=url,apply_url=url,source_url=self.plan.source_url,raw_data={"_identity":identity}))
                        successes+=1
                    except Exception as exc:errors.append(make_error("DOM_DETAIL_ERROR",type(exc).__name__,url=url));failures+=1
        except Exception as exc:errors.append(make_error("BROWSER_LAUNCH_ERROR",type(exc).__name__))
        unique={};duplicate_groups=[];unexplained=0
        for job in jobs:
            if job.job_id not in unique:unique[job.job_id]=job;continue
            prior=unique[job.job_id]
            if prior.detail_url==job.detail_url and prior.job_title==job.job_title:
                reason="SAME_REQUISITION_DUPLICATE_PRESENTATION"
            else:
                reason="AMBIGUOUS_ID_COLLISION";unexplained+=1
                suffix=hashlib.sha256(str(job.detail_url or job.model_dump()).encode()).hexdigest()[:12]
                job=job.model_copy(update={"job_id":f"{job.job_id}:{suffix}"});unique[job.job_id]=job
            duplicate_groups.append({"identity_source":(job.raw_data.get("_identity") or {}).get("identity_source"),"identity_value":(job.raw_data.get("_identity") or {}).get("identity_value"),"records_in_group":2,"raw_ids":[],"urls":[prior.detail_url,job.detail_url],"titles":[prior.job_title,job.job_title],"locations":[prior.locations,job.locations],"reason":reason})
        if unexplained:errors.append(make_error("AMBIGUOUS_ID_COLLISION","colliding identities were preserved as separate records",count=unexplained))
        status="COMPLETE" if len(allowed)==len(jobs)==len(unique) and not errors else ("FAILED" if not pages else "INCOMPLETE")
        expected=self.plan.visible_total if self.plan.pagination_type in ("LOAD_MORE","INFINITE_SCROLL") and self.plan.visible_total is not None else len(allowed)
        result=CollectionResult(source_url=self.plan.source_url,platform="generic",company=self.plan.company,scope=self.plan.scope,total_expected=expected,
            total_fetched=len(jobs),total_unique=len(unique),status=status,jobs=list(unique.values()),errors=errors,duplicate_audit={"duplicate_key_type":"identity_v2","groups":duplicate_groups,"collapsed_count":len(jobs)-len(unique),"unexplained_count":unexplained},started_at=started,finished_at=datetime.now(),
            metrics=CollectionMetrics(detail_requests=details,details_attempted=details,details_succeeded=successes,details_failed=failures,
                elapsed_seconds=perf_counter()-clock,jd_strategy="DETAIL_REQUIRED",browser_pages_opened=1))
        result.data_completeness=evaluate_data_completeness(result)
        if result.data_completeness.missing_requirements_jobs:
            result.warnings=[f"STRUCTURED_REQUIREMENTS_UNAVAILABLE count={result.data_completeness.missing_requirements_jobs}; full_jd preserved"]
        return result
