"""Safe, explicit fallback for user-supplied public recruitment cURLs."""
from __future__ import annotations

from dataclasses import dataclass, replace
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import threading
import json, os, re, shlex
import time
from typing import Any, Callable
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit
import httpx
from job_extractor.discovery.network_analyzer import get_path
from job_extractor.discovery.scorer import confidence, score_list
from job_extractor.field_semantics import canonical_jd, credible_body, infer_field, needs_detail_fetch, pick_jd_fields
from job_extractor.job_normalize import normalized_jd_halves, normalized_job_fields
from job_extractor.models import CollectionMetrics, CollectionResult, Job
from job_extractor.url_utils import normalize_url
from job_extractor.step94h_debug import append as append_step94h_debug, enabled as step94h_debug_enabled
from job_extractor.checkpoints import CheckpointWriter

_ID_NAMES={"id","jobid","job_id","positionid","position_id","postid","postingid","requisitionid","code"}
_PAGE_NAMES={"page","pageindex","pageno","page_number","currentpage"}

class ManualCurlResponseError(ValueError):
    """cURL-driven request failed at the transport/response contract layer."""
    """Expected public-response failures, safe to present in the CLI."""
    def __init__(self, code:str, detail:str=""):
        super().__init__(code if not detail else f"{code} {detail}")
        self.code=code

class PageReplayRejected(ManualCurlResponseError):
    """N6.2: the pasted List cURL itself is valid (jobs already landed)
    but automatic pagination via the same replayed request is rejected by
    the site (risk control / dynamic verification). Distinct from an
    invalid cURL: the caller should offer the browser-assisted HAR
    fallback instead of treating the cURL as failed."""
    def __init__(self, code: str = "CURL_PAGINATION_NOT_REPLAYABLE",
                 detail: str = "", jobs_count: int = 0):
        super().__init__(code, detail)
        self.code = code
        self.detail = detail
        self.jobs_count = jobs_count

class DetailCurlRequired(ValueError):
    """STEP94: raised when the hard priority chain exhausted its automatic
    methods (LIST JD / detail spec / rendered page) and the caller may ask the
    user for a Detail cURL — or fall back to a legal LIST_ONLY completion.
    Carries full list context so the CLI never re-fetches the list."""
    def __init__(self, resolution: Any, jobs: list[Job], context: dict[str, Any] | None = None,
                 task_states: dict[str, str] | None = None):
        super().__init__("DETAIL_CURL_REQUIRED")
        self.resolution=resolution
        self.jobs=jobs
        self.context=context or {}
        self.task_states=task_states or {}

@dataclass(frozen=True)
class RequestSpec:
    method:str; url:str; headers:dict[str,str]; body:Any=None
    @property
    def query_params(self): return dict(parse_qsl(urlsplit(self.url).query,keep_blank_values=True))
    def render_job_id(self,job_id:str):
        parts=urlsplit(self.url);query=parse_qsl(parts.query,keep_blank_values=True);changed=False;rendered=[]
        for key,value in query:
            if key.lower() in _ID_NAMES:rendered.append((key,job_id));changed=True
            else:rendered.append((key,value))
        if changed:return replace(self,url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(rendered),parts.fragment)))
        def substitute(value):
            if isinstance(value,dict):
                out={};found=False
                for key,item in value.items():
                    if key.lower() in _ID_NAMES:out[key]=job_id;found=True
                    else:out[key],nested=substitute(item);found=found or nested
                return out,found
            if isinstance(value,list):
                out=[];found=False
                for item in value:item,nested=substitute(item);out.append(item);found=found or nested
                return out,found
            return value,False
        body,changed=substitute(self.body)
        if not changed:raise ValueError("DETAIL_CURL_HAS_NO_JOB_ID_PARAMETER")
        return replace(self,body=body)

    def render_page(self,page:int):
        """Return the same request with its declared page parameter updated."""
        parts=urlsplit(self.url);query=parse_qsl(parts.query,keep_blank_values=True);changed=False;rendered=[]
        for key,value in query:
            if key.lower() in _PAGE_NAMES:
                rendered.append((key,str(page)));changed=True
            else:rendered.append((key,value))
        if changed:
            return replace(self,url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(rendered),parts.fragment)))
        if isinstance(self.body,str):
            pairs=parse_qsl(self.body,keep_blank_values=True);form_changed=False;form=[]
            for key,value in pairs:
                if key.lower() in _PAGE_NAMES:form.append((key,str(page)));form_changed=True
                else:form.append((key,value))
            if form_changed:return replace(self,body=urlencode(form))

        def substitute(value):
            if isinstance(value,dict):
                out={};found=False
                for key,item in value.items():
                    if key.lower() in _PAGE_NAMES:
                        out[key]=page if isinstance(item,int) else str(page);found=True
                    else:out[key],nested=substitute(item);found=found or nested
                return out,found
            if isinstance(value,list):
                out=[];found=False
                for item in value:
                    item,nested=substitute(item);out.append(item);found=found or nested
                return out,found
            return value,False
        body,changed=substitute(self.body)
        if not changed:raise ValueError("LIST_CURL_HAS_NO_PAGE_PARAMETER")
        return replace(self,body=body)

    def page_number(self):
        for key,value in parse_qsl(urlsplit(self.url).query,keep_blank_values=True):
            if key.lower() in _PAGE_NAMES:
                try:return int(value)
                except ValueError:return 1
        if isinstance(self.body,str):
            for key,value in parse_qsl(self.body,keep_blank_values=True):
                if key.lower() in _PAGE_NAMES:
                    try:return int(value)
                    except ValueError:return 1
        def find(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key.lower() in _PAGE_NAMES:
                        try:return int(item)
                        except (TypeError,ValueError):return 1
                    found=find(item)
                    if found is not None:return found
            if isinstance(value,list):
                for item in value:
                    found=find(item)
                    if found is not None:return found
            return None
        return find(self.body)

    def page_size(self):
        pairs=parse_qsl(urlsplit(self.url).query,keep_blank_values=True)
        if isinstance(self.body,str):pairs+=parse_qsl(self.body,keep_blank_values=True)
        for key,value in pairs:
            if key.lower() in {"pagesize","page_size","limit","pagesizes"}:
                try:return int(value)
                except ValueError:return None
        return None

def parse_curl(command:str)->RequestSpec:
    """Parse data only; no shell command is ever executed."""
    # Browser "Copy as cURL" uses a backslash followed by a physical newline.
    # This is command formatting, not request data, so collapse it before the
    # safe tokenizer sees an otherwise standalone ``\\`` token.
    command=re.sub(r"\\[ \t]*(?:\r?\n)"," ",command)
    tokens=shlex.split(command,posix=True)
    if not tokens or tokens[0]!="curl":raise ValueError("CURL_COMMAND_REQUIRED")
    method=None;headers={};data=None;url=None;index=1
    while index<len(tokens):
        token=tokens[index]
        if token in ("-X","--request"):
            index+=1
            if index>=len(tokens):raise ValueError("CURL_METHOD_REQUIRED")
            method=tokens[index].upper()
        elif token in ("-H","--header"):
            index+=1;key,sep,value=tokens[index].partition(":")
            if not sep or not key.strip():raise ValueError("INVALID_CURL_HEADER")
            headers[key.strip()]=value.strip()
        elif token in ("--data","--data-raw","--data-binary","-d"):
            index+=1;data=tokens[index]
        elif token=="--data-urlencode":
            index+=1;key,sep,value=tokens[index].partition("=");data=f"{quote_plus(key)}={quote_plus(value)}" if sep else quote_plus(tokens[index])
        elif token in ("-b","--cookie"):
            index+=1;headers["Cookie"]=tokens[index]
        elif token=="--url":index+=1;url=tokens[index]
        elif token.startswith(("http://","https://","[")):url=token
        elif token in ("-s","--silent","-L","--location","--compressed","--globoff","--insecure"):pass
        else:raise ValueError(f"UNSUPPORTED_CURL_OPTION {token}")
        index+=1
    if not url:raise ValueError("CURL_URL_REQUIRED")
    url=normalize_url(url)
    if method and method not in {"GET","POST","PUT","PATCH","DELETE"}:raise ValueError("UNSUPPORTED_CURL_METHOD")
    for key in list(headers):
        if key.lower()=="content-length":headers.pop(key);continue
        if key.lower()=="accept-encoding":headers.pop(key);continue
        if key.lower() in {"origin","referer"}:
            try:headers[key]=normalize_url(headers[key])
            except ValueError:pass
    body=data
    content_type=headers.get("Content-Type",headers.get("content-type","")).lower()
    if data is not None and ("json" in content_type or data.lstrip().startswith(("{","["))):
        try:body=json.loads(data)
        except json.JSONDecodeError:raise ValueError("INVALID_CURL_JSON_BODY")
    return RequestSpec(method or ("POST" if data is not None else "GET"),url,headers,body)

def _decode_json_response(response):
    """Decode public JSON without silently corrupting non-UTF-8 job data."""
    content_type=response.headers.get("content-type","")
    match=re.search(r"charset\s*=\s*([\w.-]+)",content_type,re.I)
    encodings=[match.group(1)] if match else []
    encodings.extend(["utf-8-sig","utf-8","gb18030"])
    tried=[]
    for encoding in dict.fromkeys(encodings):
        try:text=response.content.decode(encoding)
        except (UnicodeDecodeError,LookupError):
            tried.append(encoding);continue
        try:return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ManualCurlResponseError("RESPONSE_NOT_JSON",f"content_type={content_type or '-'} charset={encoding}") from exc
    raise ManualCurlResponseError("RESPONSE_DECODE_ERROR",f"content_type={content_type or '-'} tried={','.join(tried)}")

def _request(client,spec):
    kwargs={"headers":spec.headers}
    if spec.body is not None:kwargs["json" if isinstance(spec.body,(dict,list)) else "content"]=spec.body
    response=client.request(spec.method,spec.url,**kwargs);response.raise_for_status();return _decode_json_response(response)

def _request_with_retry(client,spec):
    """Retry only a single transient detail-request failure."""
    for attempt in range(2):
        try:return _request(client,spec)
        except httpx.HTTPStatusError as exc:
            transient=exc.response.status_code>=500
        except (httpx.TimeoutException,httpx.TransportError):
            transient=True
        if not transient or attempt:raise


class JDTimeBudget:
    """Stage-level, cooperative JD budget; running threads are never abandoned."""
    def __init__(self, seconds: float, on_timeout: Callable[[], str] | None = None):
        self.seconds=max(0.0, seconds); self.on_timeout=on_timeout; self.deadline=None; self.skipped=False
    def start(self) -> None:
        if self.deadline is None:self.deadline=time.monotonic()+self.seconds
    def remaining(self) -> float:
        return float("inf") if self.deadline is None else max(0.0,self.deadline-time.monotonic())
    def allow(self) -> bool:
        self.start()
        if self.remaining()>0:return True
        if self.on_timeout is not None and self.on_timeout()=="continue":
            self.deadline=time.monotonic()+self.seconds
            return True
        self.skipped=True
        return False


def _bounded_detail_futures(items, fn, workers: int, budget: JDTimeBudget, state_callback=None):
    """Submit only within budget; on skip, drain started work before returning."""
    iterator=iter(items); futures={}; results=[]; skipped=[]; stopping=False
    draining_reported=False
    def draining():
        nonlocal draining_reported
        if not draining_reported and state_callback is not None:
            draining_reported=True; state_callback(running=len(futures))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        while True:
            while not stopping and len(futures)<workers:
                if not budget.allow():stopping=True;draining();break
                try:item=next(iterator)
                except StopIteration:break
                futures[executor.submit(fn,item)]=item
            if not futures:break
            done,_=wait(futures,timeout=budget.remaining(),return_when=FIRST_COMPLETED)
            if not done:
                if not budget.allow():stopping=True;draining()
                continue
            for future in done:
                item=futures.pop(future);results.append((item,future))
        if stopping:skipped=list(iterator)
    return results,skipped

def _detail_record(value):
    if isinstance(value,dict):
        fields=pick_jd_fields(value)
        if fields["description"] or fields["responsibilities"] or fields["requirements"]:return value
        for child in value.values():
            found=_detail_record(child)
            if found:return found
    if isinstance(value,list):
        for child in value:
            found=_detail_record(child)
            if found:return found
    return None


def _auto_detail_api_spec(list_spec,campaign_prefix,job_id,recruit_type):
    """STEP94M: sibling Detail-API route of an evidenced listPosition API.

    Evidence-only construction (no hostname/endpoint guessing): the detail
    route mirrors the List cURL's own path prefix and campaign segment,
    swapping the ``listPosition`` segment for the platform-standard
    ``listPositionDetail`` segment, and posts exactly the form body the
    page's own XHR was captured sending (``postId`` + ``recruitType``).
    """
    parts=urlsplit(list_spec.url);path=parts.path
    marker=path.lower().rfind("listposition/")
    if marker<0 or not campaign_prefix or job_id in (None,""):return None
    base=path[:marker]
    url=urlunsplit((parts.scheme,parts.netloc,base+"listPositionDetail/"+campaign_prefix,"iSaJAx=isAjax&request_locale=zh_CN",""))
    # recruitType evidence chain: list record -> list query -> captured "1".
    # postType classification paths are deliberately never consulted.
    if recruit_type in (None,""):recruit_type=list_spec.query_params.get("recruitType") or "1"
    headers={"X-Requested-With":"XMLHttpRequest","Content-Type":"application/x-www-form-urlencoded",
             "Referer":f"{parts.scheme}://{parts.netloc}/{campaign_prefix.strip('/')}/pb/posDetail.html?postId={quote_plus(str(job_id))}",
             "Origin":f"{parts.scheme}://{parts.netloc}"}
    body="postId="+quote_plus(str(job_id))+"&recruitType="+quote_plus(str(recruit_type))
    return RequestSpec("POST",url,headers,body)

def _auto_detail_api_fill(client,list_spec,records,id_field,title_field,location_field,list_jobs,campaign_prefix,concurrency,progress_callback,budget=None,task_states=None,concurrency_stats=None):
    """Fill teaser Jobs through the direct Detail API (priority 2).

    Probes the evidenced sibling detail route on the first job that needs a
    JD.  A credible response locks the shape and fills every job (list
    record preserved in ``raw_data``); any probe failure returns ``None``
    immediately so the caller falls through to RENDERED_PAGE unchanged.
    Returns the success count on success.
    """
    needing=[(index,record) for index,record in enumerate(records) if list_jobs[index] is None]
    if not needing:return None
    latencies: list[float] = []; active=peak=0; active_lock=threading.Lock()
    def fill(index,record):
        nonlocal active,peak
        started_at=time.monotonic()
        with active_lock:
            active+=1;peak=max(peak,active)
        try:
            spec=_auto_detail_api_spec(list_spec,campaign_prefix,record.get(id_field),record.get("recruitType"))
            if spec is None:raise ValueError("AUTO_API_ROUTE_NOT_EVIDENCED")
            detail=_detail_record(_request_with_retry(client,spec))
            if not detail:raise ValueError("DETAIL_JD_NOT_FOUND")
            jd,_state,_resp,_req=canonical_jd(detail);picked=pick_jd_fields(detail)
            if not jd or not credible_body(jd) or not (picked["responsibilities"] or picked["requirements"]):raise ValueError("DETAIL_JD_NOT_FOUND")
        finally:
            latencies.append(time.monotonic()-started_at)
            with active_lock: active-=1
        resp_items,req_items=normalized_jd_halves(record,detail,picked)
        list_jobs[index]=Job(job_id=str(record[id_field]),job_title=str(record[title_field]),
                             **normalized_job_fields(record,detail),
                             responsibilities=resp_items,requirements=req_items,full_jd=jd,
                             source_url=list_spec.url,raw_data={"list":record,"detail":detail})
        if task_states is not None: task_states[str(record[id_field])]="SUCCESS"
    ok=fail=0;total=len(needing)
    def report(title):
        if progress_callback:progress_callback("detail",done=ok+fail,total=total,ok=ok,fail=fail,title=title)
    try:workers=max(1,min(8,int(os.getenv("DETAIL_API_CONCURRENCY","") or concurrency)))
    except ValueError:workers=concurrency
    if concurrency_stats is not None: concurrency_stats["configured"]=workers
    if progress_callback: progress_callback("detail_api_probe_start",total=total)
    if budget is not None and not budget.allow(): return 0,latencies,workers
    probe_index,probe_record=needing[0]
    try:fill(probe_index,probe_record);ok+=1;report(str(probe_record.get(title_field,"")))
    except Exception:
        if task_states is not None: task_states[str(probe_record.get(id_field,""))]="FAILED"
        if concurrency_stats is not None: concurrency_stats["peak"]=peak
        return None,[],workers  # stop immediately — no endpoint guessing
    if total>1:
        if progress_callback: progress_callback("detail_api_batch_start",total=total)
        active_budget=budget or JDTimeBudget(float("inf"))
        completed,skipped=_bounded_detail_futures(needing[1:],lambda item:fill(*item),workers,active_budget,
            state_callback=(lambda **data: progress_callback("detail_budget_draining",total=total,**data)) if progress_callback else None)
        for (_index,record),future in completed:
            try:future.result();ok+=1
            except Exception:
                fail+=1
                if task_states is not None: task_states[str(record.get(id_field,""))]="FAILED"
            report(str(record.get(title_field,"")))
        for _index,record in skipped:
            fail+=1;report(str(record.get(title_field,"")))
    if concurrency_stats is not None: concurrency_stats["peak"]=peak
    return ok,latencies,workers

@dataclass
class ManualCurlResult:
    records_path:str;total:int|None;score:int;confidence:str;jobs:list[Job];failed_details:int
    list_fetched:int=0
    unique_jobs:int=0
    failed_detail_reasons:list[tuple[str,str]]|None=None
    final_status:str="PARTIAL"
    list_termination:str=""
    runtime_seconds:float=0.0
    list_pages:int=0
    detail_strategy:str="DETAIL_REQUIRED"
    detail_resolution:Any=None  # DetailResolution.audit() dict (no cookies/cURL)
    # STEP94N: AUTO_API timing evidence (empty for rendered/user-curl runs).
    detail_api_latencies:list[float]|None=None
    detail_api_concurrency:int=1
    detail_api_peak_concurrency:int|None=None
    # N7A: resume activity accounting — None for fresh collections. The keys
    # preexisting/requested/new_success/failed/skipped/still_missing separate
    # this-run activity from the pre-existing credible-JD state.
    resume_stats:dict[str,int]|None=None
    checkpoint_path:str|None=None  # N7A: this run's own checkpoint (for the next resume)

def run_manual_curl(list_curl:str,detail_curl:str|None,*,max_jobs:int|None=5,max_pages:int=1000,concurrency:int=5,client:httpx.Client|None=None,progress_callback=None,detail_browser_factory=None,detail_page_url:str|None=None,campaign_context:str|None=None,jd_time_budget:float=300.0,jd_timeout_handler:Callable[[],str]|None=None,checkpoint_root:Path|None=None)->ManualCurlResult:
    list_spec=parse_curl(list_curl);detail_spec=parse_curl(detail_curl) if detail_curl else None;owned=client is None;client=client or httpx.Client(timeout=30,follow_redirects=True)
    # The public CLI URL can be a campaign host root.  A supplied List cURL's
    # Referer is direct, non-secret campaign-page evidence (e.g. /pb/school.html)
    # and augments—not replaces—the caller's public context.
    referer=next((str(value) for key,value in list_spec.headers.items() if key.casefold()=="referer"),"")
    effective_campaign_context=" ".join(value for value in (campaign_context,referer) if value)
    started=time.monotonic();concurrency=min(max(1,concurrency),8);checkpoint=None;snapshot_jobs=lambda:[]
    try:
        payload=_request(client,list_spec);score,_evidence,shape=score_list(list_spec.url,payload)
        if confidence(score)!="HIGH" or shape.get("rejection_reasons"):raise ValueError("LIST_CURL_NOT_CREDIBLE_JOB_SOURCE")
        path=shape.get("candidate_list_path");records=get_path(payload,path)
        if not isinstance(records,list):raise ValueError("LIST_RECORDS_PATH_INVALID")
        fields=shape.get("sample_field_names",[]);id_field,title_field=infer_field(fields,"id"),infer_field(fields,"title")
        if not id_field:id_field=shape.get("inferred_job_id_field")
        if not id_field or not title_field:raise ValueError("LIST_ID_OR_TITLE_FIELD_MISSING")
        total_path=shape.get("total_field");total=get_path(payload,total_path) if total_path else None
        total=total if isinstance(total,int) else None
        all_records=[];seen_ids=set();page_records=records;page=list_spec.page_number();pages=0;list_fetched=0;termination=""
        page_size=list_spec.page_size() or len(records) or None
        total_pages=(total+page_size-1)//page_size if total is not None and page_size else None
        while True:
            pages+=1
            if not page_records:
                termination="EMPTY_PAGE";break
            list_fetched+=len(page_records)
            if progress_callback:
                progress_callback("list", page=page or pages, pages=total_pages, fetched=list_fetched, total=total)
            new_count=0
            for record in page_records:
                if not isinstance(record,dict) or record.get(id_field) in (None,""):continue
                job_id=str(record[id_field])
                if job_id not in seen_ids:
                    seen_ids.add(job_id);all_records.append(record);new_count+=1
                    if max_jobs is not None and len(all_records)>=max_jobs:break
            if max_jobs is not None and len(all_records)>=max_jobs:
                termination="MAX_JOBS";break
            # ``total`` is a source raw-row count.  Cross-page duplicate rows
            # are legitimate source behaviour, so completion is based on raw
            # records fetched rather than only unique IDs.
            if total is not None and list_fetched>=total:
                termination="TOTAL_REACHED";break
            if page_size and len(page_records)<page_size:
                termination="SHORT_PAGE";break
            if new_count==0:
                termination="NO_NEW_UNIQUE_JOBS";break
            if page is None:
                termination="PAGINATION_UNSUPPORTED";break
            if pages>=max_pages:
                termination="MAX_PAGES";break
            page+=1
            try:
                page_payload=_request(client,list_spec.render_page(page))
            except (httpx.HTTPError, ManualCurlResponseError) as exc:
                if list_fetched>0:
                    raise PageReplayRejected(
                        jobs_count=len(all_records)) from exc
                raise
            page_records=get_path(page_payload,path)
            if not isinstance(page_records,list) and list_fetched>0 and (
                    isinstance(page_payload,dict) and page_payload.get("success") is False):
                # HTTP 200 but business-level rejection on the replayed page:
                # the cURL itself is valid; only deeper pagination is blocked.
                raise PageReplayRejected(jobs_count=len(all_records))
            if not isinstance(page_records,list):raise ValueError("LIST_RECORDS_PATH_INVALID")

        if progress_callback:
            progress_callback("list_complete",fetched=list_fetched,total=total,unique=len(all_records))
        location_field=infer_field(fields,"location");budget=JDTimeBudget(jd_time_budget,jd_timeout_handler)
        def list_job(record):
            if needs_detail_fetch(record,"DETAIL_FALLBACK"): return None
            jd,_state,_resp,_req=canonical_jd(record);picked=pick_jd_fields(record)
            if not jd:return None
            resp_items,req_items=normalized_jd_halves(record,None,picked)
            return Job(job_id=str(record[id_field]),job_title=str(record[title_field]),
                       **normalized_job_fields(record,None),
                       responsibilities=resp_items,requirements=req_items,full_jd=jd,
                       source_url=list_spec.url,raw_data={"list":record})
        list_jobs=[list_job(record) for record in all_records]
        def snapshot_jobs():
            return [job if job is not None else Job(job_id=str(record.get(id_field,"")),job_title=str(record.get(title_field,"")),
                    **normalized_job_fields(record,None),source_url=list_spec.url,raw_data={"list":record})
                    for record,job in zip(all_records,list_jobs)]
        if checkpoint_root is None:
            from job_extractor.config import OUTPUT_ROOT
            checkpoint_root=OUTPUT_ROOT
        checkpoint=CheckpointWriter(checkpoint_root,list_spec.url)
        task_states={str(record.get(id_field,"")): ("SUCCESS" if job is not None and job.full_jd else "PENDING")
                     for record,job in zip(all_records,list_jobs)}
        checkpoint.save(snapshot_jobs(),stage="JD_PENDING",task_states=task_states)
        if progress_callback:
            progress_callback("list_ready",fetched=list_fetched,total=total,unique=len(all_records))
        detail_events=0
        def checkpoint_progress(event,**data):
            nonlocal detail_events
            if progress_callback:progress_callback(event,**data)
            if event=="detail":
                detail_events+=1
                if detail_events%10==0 or data.get("done")==data.get("total"):
                    checkpoint.save(snapshot_jobs(),stage="JD_IN_PROGRESS",task_states=task_states)
        # STEP94 hard detail priority: LIST JD (above) -> detail spec (below) ->
        # rendered detail page (when a browser factory is wired) -> and only
        # then does the caller get to ask the user for a Detail cURL.
        auto_api_success=None
        auto_api_latencies:list[float]=[]
        auto_api_workers=1;auto_api_stats:dict[str,int]={}
        if detail_spec is None and any(job is None for job in list_jobs):
            from job_extractor.collectors.detail_resolver import DetailResolution, extract_campaign_prefix, render_detail_jobs, resolve_targets
            resolution=DetailResolution()
            raw_by_id={str(record.get(id_field,"")):record for record in all_records}
            # STEP94M priority 2: Direct Detail API fast path.  When the List
            # cURL evidences a ``listPosition/<campaign>`` route, its sibling
            # ``listPositionDetail/<campaign>`` XHR is probed once; a credible
            # response fills every teaser job without any browser work, and
            # resolve_targets below re-classifies the rest.  Probe failure
            # stops immediately — no endpoint guessing, rendered path as before.
            campaign_prefix=extract_campaign_prefix(list_spec.url)
            if campaign_prefix:
                auto_api_success,auto_api_latencies,auto_api_workers=_auto_detail_api_fill(client,list_spec,all_records,id_field,title_field,location_field,list_jobs,campaign_prefix,concurrency,checkpoint_progress,budget,task_states,auto_api_stats)
                append_step94h_debug(phase="JD_API_COMPLETE", total=len(all_records), succeeded=auto_api_success or 0)
            # Placeholder Jobs (created only after the API fast path) keep the
            # list complete so the resolver can classify them and LIST_ONLY
            # mode can still save every posting.
            for index,record in enumerate(all_records):
                if list_jobs[index] is None:
                    # Placeholder (rendered-fallback target): detail arrives via
                    # the browser; backfill from the list record meanwhile.  The
                    # rendered page only rewrites the JD halves, so list-record
                    # locations survive (detail-missing → list fallback).
                    list_jobs[index]=Job(job_id=str(record.get(id_field,"")),job_title=str(record.get(title_field,"")),
                                         **normalized_job_fields(record,None),
                                         source_url=list_spec.url,raw_data={"list":record})
            # A List cURL may target an API endpoint, which is transport
            # evidence rather than a public detail-page base.  The original
            # campaign page/context is supplied by the CLI when available.
            resolution=resolve_targets(list_jobs,detail_page_url or list_spec.url,raw_by_id=raw_by_id,
                                       campaign_context=effective_campaign_context or None,
                                       campaign_prefix=campaign_prefix)
            if auto_api_success is not None:
                resolution.auto_api_attempted=True;resolution.auto_api_success=auto_api_success
            targets=[target for target in resolution.targets if target.method=="RENDERED_PAGE" and target.detail_urls]
            # One-shot, explicit real-CLI diagnostic mode: exercise exactly
            # the production first detail then return to the normal fallback
            # menu.  It is never enabled in ordinary collection runs.
            if os.getenv("STEP94H_STOP_AFTER_FIRST")=="1":
                targets=targets[:1]
            if targets and detail_browser_factory is not None:
                append_step94h_debug(phase="JD_RENDER_FALLBACK_START", total=len(targets))
                if step94h_debug_enabled():
                    append_step94h_debug(phase="before_render", debug_enabled=True)
                checkpoint_progress("rendered_start",total=len(targets))
                try:
                    debug_context=({"campaign_source_url":detail_page_url or list_spec.url,"campaign_context":effective_campaign_context or None} if os.getenv("STEP94H_DEBUG")=="1" else None)
                    def relay(event,**data):
                        if event=="detail_debug" and step94h_debug_enabled():
                            detail=data.get("detail") or {}
                            # Strict allow-list: request material never enters this trace.
                            allowed=("campaign_source_url","campaign_context","candidate_url","pattern_locked",
                                     "browser_launch_elapsed","goto_started","navigation_elapsed","final_page_url",
                                     "ready_state","body_text_length","responsibilities_found","requirements_found",
                                     "exception_class","failure_stage","failure_code","failure_reason","resolver_state_id")
                            record={"title":data.get("title"),"postId":data.get("post_id"),
                                    **{key:detail.get(key) for key in allowed if key in detail},
                                    "resolver_elapsed":round(time.monotonic()-detail.get("resolver_budget_start",time.monotonic()),3)}
                            append_step94h_debug(phase="detail_debug", title=record["title"], post_id=record["postId"],
                                                 candidate_url=record.get("candidate_url"), failure_code=record.get("failure_code"),
                                                 failure_reason=record.get("failure_reason"), elapsed=record["resolver_elapsed"])
                        checkpoint_progress("detail_debug" if event=="detail_debug" else "detail",**data)
                    success,fail=render_detail_jobs(detail_browser_factory,targets,concurrency=1,timeout_s=12.0,total_timeout_s=25.0,
                                                    progress_callback=relay if progress_callback else None,debug_context=debug_context,
                                                    should_start=budget.allow)
                except Exception:
                    success,fail=0,len(targets)
                resolution.rendered_page_attempted=True
                resolution.rendered_page_success, resolution.rendered_page_failed=success,fail
                append_step94h_debug(phase="JD_RENDER_FALLBACK_END", total=len(targets), succeeded=success, failed=fail)
                # A renderer is permitted to emit only diagnostic callbacks.
                # Always publish the terminal aggregate so the UI cannot retain
                # a stale first-item update.
                checkpoint_progress("detail",done=len(targets),total=len(targets),ok=success,fail=fail)
                for job in list_jobs:
                    task_states[str(job.job_id)]="SUCCESS" if job.full_jd else "FAILED"
            resolution.jd_success=len([job for job in list_jobs if job.full_jd])
            resolution.jd_failed=len([job for job in list_jobs if not job.full_jd])
            resolution.jd_missing=resolution.jd_failed
            if resolution.jd_failed and not budget.skipped:
                raise DetailCurlRequired(resolution,list_jobs,
                    context={"path":path,"total":total,"score":score,"list_fetched":list_fetched,
                             "unique":len(all_records),"termination":termination,"pages":pages,
                             "elapsed":time.monotonic()-started,"url":list_spec.url,
                             "checkpoint_path":str(checkpoint.path),"detail_api_workers":auto_api_workers,
                             "detail_api_peak_concurrency":auto_api_stats.get("peak"),
                             "detail_api_latencies":auto_api_latencies}, task_states=task_states)
            if budget.skipped:
                reasons=[(job.job_id,"JD_TIME_BUDGET_SKIPPED") for job in list_jobs if not job.full_jd]
                task_states.update({job_id:"SKIPPED" for job_id,_ in reasons})
                checkpoint.save(snapshot_jobs(),stage="JD_SKIPPED",task_states=task_states)
                append_step94h_debug(phase="JD_COLLECTION_RETURN", total=len(list_jobs), succeeded=resolution.jd_success, failed=len(reasons), status="PARTIAL")
                return ManualCurlResult(path,total,score,confidence(score),list_jobs,len(reasons),list_fetched,len(all_records),reasons,"PARTIAL",termination,time.monotonic()-started,pages)
        else:
            resolution=None
            if detail_spec is None:
                from job_extractor.collectors.detail_resolver import DetailResolution
                resolution=DetailResolution()
                resolution.list_sufficient=len([job for job in list_jobs if job is not None])
                resolution.jd_success=resolution.list_sufficient
        if list_jobs and all(job is not None and job.full_jd for job in list_jobs):
            # AUTO_API filled jobs carry list-fetched JDs, but detail requests
            # were genuinely made — report the strategy accordingly.
            strategy="DETAIL_REQUIRED" if getattr(resolution,"auto_api_success",0) else "LIST_SUFFICIENT"
            result=ManualCurlResult(path,total,score,confidence(score),list_jobs,0,list_fetched,len(all_records),[],"COMPLETE" if total is not None and list_fetched>=total else "PARTIAL",termination,time.monotonic()-started,pages,strategy)
            result.detail_resolution=resolution
            result.detail_api_latencies=auto_api_latencies
            result.detail_api_concurrency=auto_api_workers if auto_api_success else 1
            result.detail_api_peak_concurrency=auto_api_stats.get("peak")
            checkpoint.save(snapshot_jobs(),stage="JD_COMPLETE",task_states=task_states)
            return result
        if detail_spec is None:
            # STEP94: LIST_ONLY is a legal completion when no detail evidence
            # exists at all — never a failure. (Unreachable when the resolver
            # already raised DetailCurlRequired above.)
            result=ManualCurlResult(path,total,score,confidence(score),list_jobs,0,list_fetched,len(all_records),[],"COMPLETE" if total is not None and list_fetched>=total else "PARTIAL",termination,time.monotonic()-started,pages,"LIST_ONLY")
            result.detail_resolution=resolution
            checkpoint.save(snapshot_jobs(),stage="JD_NOT_REQUESTED",task_states=task_states)
            return result
        def resolve_detail(record):
            try:
                if not isinstance(record,dict) or record.get(id_field) in (None,""):raise ValueError("LIST_JOB_ID_MISSING")
                detail=_detail_record(_request_with_retry(client,detail_spec.render_job_id(str(record[id_field]))))
                if not detail:raise ValueError("DETAIL_JD_NOT_FOUND")
                jd,_state,_resp,_req=canonical_jd(detail);picked=pick_jd_fields(detail)
                if not jd or not (picked["responsibilities"] or picked["requirements"]):raise ValueError("DETAIL_JD_NOT_FOUND")
                resp_items,req_items=normalized_jd_halves(record,detail,picked)
                return Job(job_id=str(record[id_field]),job_title=str(record[title_field]),
                           **normalized_job_fields(record,detail),
                           responsibilities=resp_items,requirements=req_items,full_jd=jd,
                           source_url=list_spec.url,raw_data={"list":record,"detail":detail}),None
            except Exception as exc:return None,(str(record.get(id_field,"")),str(exc))
        detail_results=[None]*len(all_records)
        detail_items=list(enumerate(all_records));succeeded=failed=0
        completed,skipped=_bounded_detail_futures(detail_items,lambda item:resolve_detail(item[1]),concurrency,budget)
        for (index,record),future in completed:
                detail_results[index]=future.result()
                if detail_results[index][0] is not None:list_jobs[index]=detail_results[index][0]
                if detail_results[index][0] is None: failed+=1
                else: succeeded+=1
                checkpoint_progress("detail",done=succeeded+failed,total=len(all_records),ok=succeeded,fail=failed,title=str(record.get(title_field,"")))
        for index,record in skipped:
                detail_results[index]=(Job(job_id=str(record.get(id_field,"")),job_title=str(record.get(title_field,"")),**normalized_job_fields(record,None),source_url=list_spec.url,raw_data={"list":record}),
                                       (str(record.get(id_field,"")),"JD_TIME_BUDGET_SKIPPED"))
                list_jobs[index]=detail_results[index][0]
                failed+=1
                checkpoint_progress("detail",done=succeeded+failed,total=len(all_records),ok=succeeded,fail=failed,title=str(record.get(title_field,"")))
        jobs=[job for job,error in detail_results if job is not None]
        failed_reasons=[error for job,error in detail_results if error is not None]
        complete_list=total is not None and list_fetched>=total
        status="COMPLETE" if complete_list and not failed_reasons else "PARTIAL"
        states={job_id:("SKIPPED" if reason=="JD_TIME_BUDGET_SKIPPED" else "FAILED") for job_id,reason in failed_reasons}
        task_states.update(states)
        for job in jobs:
            task_states[str(job.job_id)]="SUCCESS"
        checkpoint.save(snapshot_jobs(),stage="JD_COMPLETE" if not states else "JD_PARTIAL",task_states=task_states)
        append_step94h_debug(phase="JD_COLLECTION_RETURN", total=len(all_records), succeeded=len(jobs), failed=len(failed_reasons), status=status)
        return ManualCurlResult(path,total,score,confidence(score),jobs,len(failed_reasons),list_fetched,len(all_records),failed_reasons,status,termination,time.monotonic()-started,pages)
    except DetailCurlRequired:
        # This is an expected escalation, not an aborted collection.  Preserve
        # the usable list and its per-job state for the menu/resume path.
        if checkpoint is not None:
            checkpoint.save(snapshot_jobs(),stage="JD_PARTIAL",task_states=task_states)
        append_step94h_debug(phase="JD_WAIT_DETAIL_CURL")
        raise
    except BaseException:
        if checkpoint is not None:
            checkpoint.save(snapshot_jobs(),stage="JD_ABORTED",task_states=task_states)
        raise
    finally:
        if owned:
            append_step94h_debug(phase="RESOURCE_CLEANUP_START")
            client.close()
            append_step94h_debug(phase="RESOURCE_CLEANUP_END")

def load_jd_checkpoint(path: Path) -> tuple[list[Job],dict[str,str],str]:
    try:payload=json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:raise ValueError("CHECKPOINT_NOT_FOUND")
    except (OSError,json.JSONDecodeError) as exc:raise ValueError("CHECKPOINT_INVALID") from exc
    if payload.get("format")!="job-extractor-jd-checkpoint-v1":raise ValueError("CHECKPOINT_UNSUPPORTED")
    if not isinstance(payload.get("source_url"),str) or not isinstance(payload.get("jobs"),list):raise ValueError("CHECKPOINT_INVALID")
    try:jobs=[Job.model_validate(value) for value in payload["jobs"]]
    except Exception as exc:raise ValueError("CHECKPOINT_INVALID") from exc
    states=payload.get("task_states") or {}
    return jobs,{str(key):str(value) for key,value in states.items()},payload["source_url"]


def resume_manual_checkpoint(checkpoint_path: Path, detail_curl: str, *, concurrency: int=5,
                             client:httpx.Client|None=None, progress_callback=None,
                             jd_time_budget:float=300.0,jd_timeout_handler:Callable[[],str]|None=None,
                             checkpoint_root:Path|None=None) -> ManualCurlResult:
    """Retry only incomplete checkpoint jobs using a freshly supplied Detail cURL."""
    jobs,states,source_url=load_jd_checkpoint(checkpoint_path);spec=parse_curl(detail_curl)
    owned=client is None;client=client or httpx.Client(timeout=30,follow_redirects=True)
    if checkpoint_root is None:
        from job_extractor.config import OUTPUT_ROOT
        checkpoint_root=OUTPUT_ROOT
    writer=CheckpointWriter(checkpoint_root,source_url,str(checkpoint_path))
    budget=JDTimeBudget(jd_time_budget,jd_timeout_handler)
    def complete(job:Job)->bool:return bool(job.full_jd and credible_body(job.full_jd))
    targets=[(index,job) for index,job in enumerate(jobs) if not complete(job)]
    preexisting=len(jobs)-len(targets)
    writer.save(jobs,stage="JD_RESUME_PENDING",task_states=states)
    def fetch(item):
        index,job=item
        try:
            if not job.job_id:raise ValueError("LIST_JOB_ID_MISSING")
            detail=_detail_record(_request_with_retry(client,spec.render_job_id(str(job.job_id))))
            if not detail:raise ValueError("DETAIL_JD_NOT_FOUND")
            jd,_state,_resp,_req=canonical_jd(detail);picked=pick_jd_fields(detail)
            if not jd or not (picked["responsibilities"] or picked["requirements"]):raise ValueError("DETAIL_JD_NOT_FOUND")
            record=job.raw_data.get("list",{}) if isinstance(job.raw_data,dict) else {}
            resp,req=normalized_jd_halves(record,detail,picked)
            job.full_jd=jd;job.responsibilities=resp;job.requirements=req
            return index,None
        except Exception as exc:return index,str(exc)
    try:
        completed,skipped=_bounded_detail_futures(targets,fetch,min(max(1,concurrency),8),budget)
        succeeded=failed=0
        for item,future in completed:
            index,error=future.result();job=jobs[index]
            if error is None:succeeded+=1;states.pop(str(job.job_id),None)
            else:failed+=1;states[str(job.job_id)]="FAILED"
            if progress_callback:progress_callback("detail",done=succeeded+failed,total=len(targets),ok=succeeded,fail=failed,title=job.job_title)
            if (succeeded+failed)%10==0:writer.save(jobs,stage="JD_RESUME_IN_PROGRESS",task_states=states)
        for position,(index,job) in enumerate(skipped,1):
            states[str(job.job_id)]="SKIPPED"
            if progress_callback:progress_callback("detail",done=len(completed)+position,total=len(targets),ok=succeeded,fail=failed,title=job.job_title)
        skipped_count=len(skipped)
        unresolved=[job for job in jobs if not complete(job)]
        final_states={str(job.job_id):states.get(str(job.job_id),"PENDING") for job in unresolved}
        writer.save(jobs,stage="JD_RESUME_COMPLETE" if not final_states else "JD_RESUME_PARTIAL",task_states=final_states)
        reasons=[(str(job.job_id),final_states[str(job.job_id)]) for job in unresolved]
        status="COMPLETE" if not reasons else "PARTIAL"
        resume_stats={"preexisting":preexisting,"requested":len(targets),"new_success":succeeded,
                      "failed":failed,"skipped":skipped_count,"still_missing":len(unresolved)}
        return ManualCurlResult("checkpoint",len(jobs),0,"HIGH",jobs,len(reasons),len(jobs),len(jobs),reasons,status,"RESUMED",0.0,0,
                                resume_stats=resume_stats,checkpoint_path=str(writer.path))
    finally:
        if owned:client.close()


def manual_result_to_collection_result(result:ManualCurlResult,source_url:str)->CollectionResult:
    """Expose explicit cURL collection through the standard result contract."""
    duplicates=max(0,result.list_fetched-result.unique_jobs)
    list_complete=result.total is not None and result.list_fetched>=result.total
    status="COMPLETE" if list_complete and result.failed_details==0 else "INCOMPLETE"
    detail_resolution=getattr(result,"detail_resolution",None)
    list_sufficient=result.detail_strategy=="LIST_SUFFICIENT"
    list_only=result.detail_strategy=="LIST_ONLY" or bool(getattr(detail_resolution,"list_only_used",False))
    # N9 Final: LIST_ONLY is now a first-class jd_strategy value — the audit
    # detail_method and the metric vocabulary no longer disagree.
    metric_strategy="LIST_ONLY" if list_only else result.detail_strategy
    detail_failed=(getattr(detail_resolution,"jd_failed",result.failed_details) if detail_resolution is not None else result.failed_details)
    detail_succeeded=(getattr(detail_resolution,"jd_success",len(result.jobs)) if detail_resolution is not None else len(result.jobs))
    # STEP94N: real AUTO_API timing/concurrency evidence when available.
    api_latencies=list(getattr(result,"detail_api_latencies",None) or [])
    detail_seconds=sum(api_latencies)
    resume_stats=getattr(result,"resume_stats",None)
    detail_requests=resume_stats["requested"] if resume_stats else result.unique_jobs
    details_attempted=0 if (list_sufficient or list_only) else detail_requests
    details_succeeded=detail_succeeded if not resume_stats else resume_stats["preexisting"]+resume_stats["new_success"]
    details_failed=detail_failed if not resume_stats else resume_stats["still_missing"]
    metrics=CollectionMetrics(list_requests=result.list_pages,list_pages=result.list_pages,
        pages_requested=result.list_pages,pages_succeeded=result.list_pages,
        raw_rows=result.list_fetched,unique_jobs=result.unique_jobs,duplicate_jobs=duplicates,
        detail_requests=0 if (list_sufficient or list_only) else detail_requests,
        details_attempted=details_attempted,
        details_succeeded=details_succeeded,details_failed=details_failed,
        elapsed_seconds=result.runtime_seconds,termination_reason=result.list_termination,
        collection_mode="MANUAL_CURL",jd_strategy=metric_strategy,
        detail_request_seconds=round(detail_seconds,3),
        average_detail_request_seconds=round(detail_seconds/len(api_latencies),4) if api_latencies else 0.0,
        max_concurrency_observed=max(1,int(getattr(result,"detail_api_peak_concurrency",None) or 1)))
    warnings=[f"SOURCE_CROSS_PAGE_DUPLICATES raw_total={result.total} duplicate_records={duplicates}"] if duplicates else []
    if detail_resolution is not None:
        warnings.append(f"DETAIL_METHOD {detail_resolution.detail_method}")
    # N9.10: shared resolver — explicit job-level company first, then generic
    # domain brand inference on the source/API host; ATS platforms can never
    # leak through as the company.
    from job_extractor.company_identity import resolve_collection_company
    company = resolve_collection_company(result.jobs, None, source_url)
    enrichment={"detail_resolution":detail_resolution.audit()} if detail_resolution is not None else {}
    if resume_stats:enrichment["resume_stats"]=resume_stats  # N7A: this-run resume activity (see collection.json)
    return CollectionResult(source_url=source_url,platform="manual_curl",company=company,metrics=metrics,
        total_expected=result.total,total_fetched=result.list_fetched,total_unique=result.unique_jobs,
        status=status,jobs=result.jobs,warnings=warnings,
        errors=[f"DETAIL_FAILED id={job_id} reason={reason}" for job_id,reason in result.failed_detail_reasons or []],
        duplicate_audit={"raw_total":result.total,"raw_fetched":result.list_fetched,
            "unique_jobs":result.unique_jobs,"duplicate_records":duplicates,
            "source_cross_page_duplicates":bool(duplicates)},
        enrichment=enrichment)
