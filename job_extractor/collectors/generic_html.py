from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
from time import perf_counter
from urllib.parse import urljoin, urlsplit

import httpx

from job_extractor.collectors.generic_detail import GenericHtmlDetailCollector
from job_extractor.collectors.generic_http import GenericHttpCollector, ID_FIELDS, TITLE_FIELDS
from job_extractor.discovery.dom_semantics import filter_detail_links
from job_extractor.models import CollectionResult
from job_extractor.planning.models import CollectionPlan
from job_extractor.runtime import MetricsRecorder, evaluate_data_completeness, make_error


class _Links(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.links=[]; self._href=None; self._text=[]
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href=dict(attrs).get("href"); self._text=[]
    def handle_data(self, data):
        if self._href: self._text.append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href," ".join("".join(self._text).split()))); self._href=None; self._text=[]


class GenericHtmlCollector:
    """HTTP-first execution for discovery-confirmed SSR job links."""
    def __init__(self, plan: CollectionPlan, client=None, max_pages:int=2):
        self.plan=plan; self.client=client or httpx.Client(timeout=30,follow_redirects=True)
        self.max_pages=max_pages; self.recorder=MetricsRecorder(); self.list_requests=self.page_count=0
        self.details_attempted=self.details_succeeded=self.details_failed=0; self.detail_strategy="DETAIL_HTTP_HTML"; self.elapsed_seconds=0.0
    @staticmethod
    def _parse(html:str, source_url:str):
        parser=_Links(); parser.feed(html); parser.close()
        return filter_detail_links(parser.links,source_url)[0], parser.links
    def collect(self)->CollectionResult:
        started=datetime.now(); clock=perf_counter(); errors=[]; raws=[]; page_url=self.plan.source_url; seen_pages=set(); termination="SINGLE_PAGE"
        try:
            for _ in range(self.max_pages):
                if page_url in seen_pages: errors.append(make_error("PAGINATION_NO_PROGRESS","repeated HTML page")); break
                seen_pages.add(page_url); self.recorder.record_list_request()
                response=self.client.get(page_url); response.raise_for_status(); self.recorder.record_page(); self.page_count+=1
                links, all_links=self._parse(response.text,page_url)
                if not links: errors.append(make_error("HTML_JOB_LINKS_MISSING","no discovery-confirmed job-like links")); break
                allowed=set(self.plan.allowed_detail_urls)
                selected=[link for link in links if not allowed or link in allowed or self.page_count>1]
                for link in selected:
                    label=next((text for href,text in all_links if urljoin(page_url,href)==link),"")
                    if not label: continue
                    raws.append({"id":link,"title":label,"detailUrl":link})
                next_pages=[urljoin(page_url,href) for href,label in all_links if "pageindex=" in href.lower() and urljoin(page_url,href) not in seen_pages]
                if not next_pages: break
                page_url=next_pages[0]; termination="PAGE_LIMIT" if self.page_count>=self.max_pages else "PAGINATION"
            if not raws: return CollectionResult(source_url=self.plan.source_url,platform="generic",status="FAILED",errors=errors,started_at=started,finished_at=datetime.now())
            fetched_count=len(raws)
            unique_raw={raw["detailUrl"]:raw for raw in raws}; raws=list(unique_raw.values())
            ids=tuple((self.plan.detail_id_field,*ID_FIELDS)); titles=tuple((self.plan.job_title_field,*TITLE_FIELDS))
            detail=GenericHtmlDetailCollector(self.plan,self.client); raws,success,failures=detail.enrich(raws,ids,titles)
            self.details_attempted=len(raws); self.details_succeeded=success; self.details_failed=len(failures)
            for code,jid in failures: errors.append(make_error(code,"HTML detail handoff failed",job_id=jid))
            normalizer=GenericHttpCollector(self.plan,self.client); jobs=[normalizer._job(raw) for raw in raws]; jobs=[job for job in jobs if job]
            expected=len(raws); complete=not errors and len(jobs)==expected and termination!="PAGE_LIMIT"
            result=CollectionResult(source_url=self.plan.source_url,platform="generic",company=self.plan.company,scope=self.plan.scope,total_expected=expected,total_fetched=len(raws),total_unique=len(jobs),status="COMPLETE" if complete else "INCOMPLETE",jobs=jobs,errors=errors,started_at=started,finished_at=datetime.now())
            result.metrics=self.recorder.finish(); result.metrics.details_attempted=self.details_attempted; result.metrics.details_succeeded=self.details_succeeded; result.metrics.details_failed=self.details_failed; result.metrics.collection_mode="HTML_SSR"; result.metrics.termination_reason=termination
            result.duplicate_audit={"collapsed_count":fetched_count-len(raws),"unexplained_count":0}
            result.data_completeness=evaluate_data_completeness(result); return result
        except Exception as exc:
            return CollectionResult(source_url=self.plan.source_url,platform="generic",status="FAILED",errors=[make_error("HTML_LIST_REQUEST_FAILED",type(exc).__name__)],started_at=started,finished_at=datetime.now())
        finally: self.elapsed_seconds=perf_counter()-clock
