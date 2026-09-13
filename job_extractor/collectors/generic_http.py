from __future__ import annotations
from datetime import datetime
from time import perf_counter,sleep
from typing import Any
from urllib.parse import urlparse
import hashlib,json,httpx
from job_extractor.models import CollectionResult,Job
from job_extractor.planning.models import CollectionPlan
from job_extractor.runtime import MetricsRecorder,evaluate_data_completeness,make_error
from job_extractor.discovery.network_analyzer import safe_business_data
from job_extractor.collectors.generic_detail import GenericHtmlDetailCollector,GenericHttpDetailCollector,GenericDomDetailCollector,extract_path,split_jd
from job_extractor.discovery.dom_semantics import credible_jd
from job_extractor.discovery.dynamic import graphql_next_values,pagination_stop
from job_extractor.identity import job_identity
from job_extractor.planning.execution_contract import PlanContractError,transport_gaps

class CollectionDeadlineExceeded(RuntimeError):pass
class PageRequestFailed(RuntimeError):
    def __init__(self,name:str,transient:bool):super().__init__(name);self.transient=transient

ID_FIELDS=("id","job_id","jobId","jobPostId","positionId","requisitionId","requisition_id")
TITLE_FIELDS=("title","name","jobTitle","positionName")
def path_get(value:Any,path:str|None)->Any:
    if not path:return None
    for part in (path or "").split("."):
        if not part or part=="$":continue
        if not isinstance(value,dict) or part not in value:return None
        value=value[part]
    return value
def unwrap_item(value:Any,path:str|None)->Any:
    return path_get(value,path) if path else value
def pick(raw:dict,names):
    for name in names:
        if raw.get(name) not in (None,""):return raw[name]
    return None
def text(value:Any)->str|None:
    if isinstance(value,str):return value.strip() or None
    if isinstance(value,dict):
        for key in ("name","title","label","city","location"):
            if isinstance(value.get(key),str):return value[key]
    return None
def nested_text(value:Any,*paths:str)->str|None:
    for path in paths:
        current=value
        for part in path.split("."):
            if not isinstance(current,dict):current=None;break
            current=current.get(part)
        if isinstance(current,str) and current.strip():return current.strip()
    return None
def lines(value:Any)->list[str]:
    return [x.strip() for x in str(value or "").replace("\r","").split("\n") if x.strip()]
def _raw_detail_url(raw:dict,plan:CollectionPlan)->str|None:
    value=extract_path(raw,plan.detail_url_field) if plan.detail_url_field else None
    if not value:value=pick(raw,("absolute_url","url","job_url","jobUrl","detail_url","detailUrl","apply_url","applyUrl","click_url","clickUrl"))
    return value if isinstance(value,str) else None
def duplicate_audit(raws:list[dict],plan:CollectionPlan)->dict[str,Any]:
    groups={}
    for raw in raws:
        url=_raw_detail_url(raw,plan);title=pick(raw,(plan.job_title_field,)+TITLE_FIELDS if plan.job_title_field else TITLE_FIELDS)
        identity=job_identity(raw,url,str(title or ""),pick(raw,("location","locations","city")),str(pick(raw,("department","team")) or ""),plan.company)
        groups.setdefault((identity["identity_source"],identity["identity_value"]),[]).append(raw)
    duplicate_groups=[];unexplained=0
    for (identity_source,identity_value),records in groups.items():
        if len(records)<2:continue
        titles=sorted({str(pick(x,(plan.job_title_field,)+TITLE_FIELDS if plan.job_title_field else TITLE_FIELDS) or "") for x in records})
        urls=sorted({x for x in (_raw_detail_url(record,plan) for record in records) if x})
        fingerprints=[hashlib.sha256(json.dumps(safe_business_data(x),sort_keys=True,default=str).encode()).hexdigest()[:16] for x in records]
        locations={json.dumps(pick(x,("location","locations","city")),sort_keys=True,default=str) for x in records}
        raw_ids=sorted({str(v) for record in records for fields in (REQUISITION_FIELDS,JOB_FIELDS,STABLE_FIELDS) for v,_ in [_find_identity_field(record,fields)] if v is not None})
        if len(set(fingerprints))==1:reason="IDENTICAL_SOURCE_RECORD"
        elif identity_source in ("REQUISITION_ID","JOB_POSTING_ID","STABLE_API_ID","STRUCTURED_DETAIL_ID","CANONICAL_URL_ID") and len(titles)==1:reason="SAME_REQUISITION_DUPLICATE_PRESENTATION"
        elif len(urls)>1 or len(titles)>1 or len(raw_ids)>1:reason="AMBIGUOUS_ID_COLLISION";unexplained+=len(records)-1
        else:reason="UNEXPLAINED_DUPLICATE";unexplained+=len(records)-1
        duplicate_groups.append({"identity_source":identity_source,"identity_value":identity_value,"records_in_group":len(records),"raw_ids":raw_ids,"urls":urls,"titles":titles,"locations":sorted(locations),"source_record_fingerprints":fingerprints,"reason":reason,"count":len(records),"raw_id":identity_value})
    explained=sum(x["records_in_group"]-1 for x in duplicate_groups if x["reason"] in ("IDENTICAL_SOURCE_RECORD","SAME_REQUISITION_DUPLICATE_PRESENTATION"))
    return {"duplicate_key_type":"identity_v2","duplicate_raw_ids":[x["identity_value"] for x in duplicate_groups],"groups":duplicate_groups,"collapsed_count":explained,"unexplained_count":unexplained}

from job_extractor.identity import REQUISITION_FIELDS,JOB_FIELDS,STABLE_FIELDS
def _find_identity_field(raw,names):
    lookup={str(k).lower():v for k,v in raw.items()}
    for name in names:
        if name in lookup and lookup[name] not in (None,""):return str(lookup[name]),name
    return None,None

class GenericHttpCollector:
    def __init__(self,plan:CollectionPlan,client:httpx.Client|None=None,max_pages:int=1000,
                 max_retries:int=2,retry_backoff_base:float=0.25,deadline_seconds:float=180.0,
                 sleep_fn=sleep,clock=perf_counter):
        self.plan=plan;self.client=client or httpx.Client(timeout=30,follow_redirects=True);self.max_pages=max_pages
        self.max_retries=max(0,max_retries);self.retry_backoff_base=max(0.0,retry_backoff_base)
        self.deadline_seconds=max(0.0,deadline_seconds);self.sleep_fn=sleep_fn;self.clock=clock
        self.recorder=MetricsRecorder();self.list_requests=self.page_count=self.details_attempted=self.details_succeeded=self.details_failed=0
        self.detail_strategy=plan.detail_mode;self.page_size=None;self.elapsed_seconds=0.0;self.scope=None
    def _request(self,values:dict[str,Any],query_values:dict[str,Any]|None=None):
        method=self.plan.list_method or "GET"
        if method=="GET":kwargs={"params":dict(values)}
        else:
            kwargs={"params":dict(query_values if query_values is not None else self.plan.query_values)}
            kwargs["data" if self.plan.body_encoding=="FORM" else "json"]=dict(values)
        response=self.client.request(method,self.plan.list_endpoint,**kwargs);response.raise_for_status();return response.json()
    @staticmethod
    def _transient(exc:Exception)->bool:
        if isinstance(exc,(TimeoutError,httpx.TimeoutException,httpx.TransportError)):return True
        if isinstance(exc,httpx.HTTPStatusError):
            status=exc.response.status_code
            return status==429 or 500<=status<600
        status=getattr(getattr(exc,"response",None),"status_code",None)
        return status==429 or isinstance(status,int) and 500<=status<600
    def _request_with_retry(self,values:dict[str,Any],query_values:dict[str,Any],deadline:float):
        for attempt in range(self.max_retries+1):
            if self.clock()>=deadline:raise CollectionDeadlineExceeded()
            self.recorder.record_list_request()
            try:return self._request(values,query_values)
            except Exception as exc:
                transient=self._transient(exc)
                if not transient or attempt>=self.max_retries:raise PageRequestFailed(type(exc).__name__,transient) from exc
                delay=self.retry_backoff_base*(2**attempt)
                remaining=deadline-self.clock()
                if remaining<=0:raise CollectionDeadlineExceeded() from exc
                delay=min(delay,remaining);self.recorder.record_retry(delay)
                if delay:self.sleep_fn(delay)
    def _job(self,raw:dict)->Job|None:
        source_jid=pick(raw,(self.plan.job_id_field,)+ID_FIELDS if self.plan.job_id_field else ID_FIELDS)
        title=pick(raw,(self.plan.job_title_field,)+TITLE_FIELDS if self.plan.job_title_field else TITLE_FIELDS)
        if source_jid is None or not isinstance(title,str):return None
        location=pick(raw,("_generic_location","location","locations","city","city_list","workPlaceCode")); locations=[]
        if isinstance(location,list):locations=[x for x in (text(y) for y in location) if x]
        elif isinstance(location,dict):locations=[x for x in (text(location),) if x]
        elif text(location):locations=[text(location)]
        dto=raw.get("projectPositionDto") if isinstance(raw.get("projectPositionDto"),dict) else {}
        description=pick(raw,("_generic_description","description","content","jobDescription")) or dto.get("jobResponsibility"); requirements=pick(raw,("requirements","requirement","qualifications")) or dto.get("jobRequirement")
        full=text(description) if raw.get("_generic_description") is not None else ("\n\n".join(x for x in (text(description),text(requirements)) if x) or None)
        responsibilities,parsed_requirements=split_jd(full)
        resp_lines=raw.get("_generic_responsibilities") if isinstance(raw.get("_generic_responsibilities"),list) else (responsibilities if raw.get("_generic_description") is not None else lines(description))
        req_lines=raw.get("_generic_requirements") if isinstance(raw.get("_generic_requirements"),list) else (lines(requirements) or parsed_requirements)
        detail=pick(raw,("_generic_detail_url",)) or _raw_detail_url(raw,self.plan)
        if not detail and self.plan.detail_endpoint_template and "{id}" in self.plan.detail_endpoint_template:
            detail=self.plan.detail_endpoint_template.replace("{id}",str(source_jid))
        department=text(pick(raw,("_generic_department","department","team")));identity=job_identity(raw,detail,title,locations,department,self.plan.company)
        category=nested_text(raw,"job_function.name","function.name") or text(pick(raw,("category","function","recruitCategoryName")))
        recruitment=nested_text(raw,"recruit_type.parent.name","recruit_type.name") or self.plan.scope.get("recruitment_type")
        return Job(company=self.plan.company,job_id=identity["identity_value"],job_title=title,locations=locations,department=department,
            job_category=category,recruitment_type=recruitment,responsibilities=resp_lines,requirements=req_lines,
            full_jd=full,detail_url=detail if isinstance(detail,str) else None,apply_url=detail if isinstance(detail,str) else None,
            source_url=self.plan.source_url,raw_data={**safe_business_data(raw),"_identity":identity})
    def collect(self)->CollectionResult:
        gaps=transport_gaps(self.plan)
        if gaps:
            from job_extractor.planning.execution_contract import GAP_REASONS
            missing=[GAP_REASONS.get(gap,f"MISSING_{gap}") for gap in gaps]
            raise PlanContractError(f"COLLECTION_CONTRACT_VIOLATION gaps={','.join(missing)}",execution_mode=self.plan.mode,missing_fields=missing)
        started=datetime.now();clock=self.clock();deadline=clock+self.deadline_seconds;errors=[];raws=[];values=dict(self.plan.initial_values);query_values=dict(self.plan.query_values);seen_cursors=set();seen_ids=set();expected=None;termination=None
        self.recorder.set_collection_mode("DIRECT_HTTP_REPLAY" if self.plan.mode=="HTTP_API" else "BROWSER_SESSION_REPLAY")
        for _ in range(self.max_pages):
            try:payload=self._request_with_retry(values,query_values,deadline)
            except CollectionDeadlineExceeded:
                errors.append(make_error("COLLECTION_DEADLINE_EXCEEDED","global collection deadline reached"));termination="DEADLINE_EXCEEDED";break
            except PageRequestFailed as exc:
                code="PAGE_REQUEST_RETRIES_EXHAUSTED" if exc.transient else "PAGE_REQUEST_FAILED"
                errors.append(make_error(code,str(exc)));termination="REQUEST_FAILED";break
            try:
                self.recorder.record_page()
                batch=path_get(payload,self.plan.list_path)
                if not isinstance(batch,list):errors.append(make_error("LIST_PATH_INVALID","planned list path not found"));break
                records=[unwrap_item(x,self.plan.list_item_path) for x in batch]
                records=[x for x in records if isinstance(x,dict)]
                if not raws and not records and (self.plan.observed_list_length or 0)>0:
                    errors.append(make_error("REPLAY_RESPONSE_EMPTY",f"discovery observed {self.plan.observed_list_length} records but replay returned zero"))
                previous_unique=len(seen_ids)
                raws.extend(records)
                for record in records:
                    jid=pick(record,(self.plan.job_id_field,)+ID_FIELDS if self.plan.job_id_field else ID_FIELDS)
                    key=str(jid) if jid is not None else hashlib.sha256(json.dumps(safe_business_data(record),sort_keys=True,default=str).encode()).hexdigest()
                    seen_ids.add(key)
                unique_count=len(seen_ids);self.recorder.record_rows(len(records),unique_count-previous_unique)
                total=path_get(payload,self.plan.total_field)
                if isinstance(total,int):expected=total
                if self.plan.pagination_type in ("NONE","SINGLE_RESPONSE","UNKNOWN"):
                    termination="SINGLE_RESPONSE";break
                has_more=path_get(payload,self.plan.has_more_field);cursor=path_get(payload,self.plan.next_cursor_field)
                stop=pagination_stop(official_total=expected,unique_count=unique_count,previous_unique=previous_unique,has_more=has_more if isinstance(has_more,bool) else None,cursor=cursor,seen_cursors=seen_cursors)
                if stop=="OFFICIAL_TOTAL_REACHED":termination="TOTAL_REACHED";break
                if stop=="HAS_MORE_FALSE":termination="HAS_MORE_FALSE";break
                if not records:termination="EMPTY_PAGE";break
                if stop=="NO_NEW_UNIQUE_JOBS":
                    errors.append(make_error("PAGINATION_NO_PROGRESS","page added no new stable job IDs"));termination="NO_PROGRESS";break
                if stop=="CURSOR_EXHAUSTED":
                    errors.append(make_error("PAGINATION_LOOP","cursor repeated"));termination="NO_PROGRESS";break
                size_source=values if self.plan.page_size_param in values else query_values
                configured_size=int(size_source.get(self.plan.page_size_param,0) or 0) if self.plan.page_size_param else 0
                if configured_size and len(records)<configured_size:termination="SHORT_PAGE";break
                if self.plan.pagination_type=="PAGE":
                    key=self.plan.page_param;target=values if key in values else query_values;before=int(target.get(key,0));target[key]=before+1
                elif self.plan.pagination_type=="OFFSET":
                    key=self.plan.offset_param;target=values if key in values else query_values;size_source=values if self.plan.page_size_param in values else query_values;size=int(size_source.get(self.plan.page_size_param,len(records)));target[key]=int(target.get(key,0))+size
                elif self.plan.pagination_type=="CURSOR":
                    cursor=path_get(payload,self.plan.next_cursor_field or self.plan.cursor_param)
                    if not cursor or cursor in seen_cursors:
                        if cursor in seen_cursors:errors.append(make_error("PAGINATION_LOOP","cursor repeated"))
                        break
                    seen_cursors.add(cursor);values[self.plan.cursor_param]=cursor
                elif self.plan.pagination_type in ("GRAPHQL_CURSOR","GRAPHQL_OFFSET","GRAPHQL_PAGE"):
                    if self.plan.pagination_type=="GRAPHQL_CURSOR":seen_cursors.add(cursor)
                    values=graphql_next_values(values,self.plan.pagination_type,self.plan.page_param,self.plan.page_size_param,cursor,len(batch))
            except Exception as exc:
                errors.append(make_error("GENERIC_HTTP_ERROR",type(exc).__name__));termination="SCHEMA_OR_RUNTIME_ERROR";break
        else:
            errors.append(make_error("PAGINATION_LIMIT","max pages reached"));termination="MAX_PAGES"
        self.recorder.set_termination(termination or "UNKNOWN")
        if self.plan.detail_mode in ("DETAIL_REQUIRED","DETAIL_FALLBACK") and self.plan.detail_endpoint_template and "{id}" in self.plan.detail_endpoint_template:
            for index,raw in enumerate(raws):
                needs=self.plan.detail_mode=="DETAIL_REQUIRED" or not pick(raw,("description","content","jobDescription"))
                if not needs:continue
                if self.clock()>=deadline:
                    errors.append(make_error("COLLECTION_DEADLINE_EXCEEDED","detail enrichment budget exhausted"));self.recorder.set_termination("DEADLINE_EXCEEDED");break
                jid=pick(raw,(self.plan.detail_id_field,)+ID_FIELDS if self.plan.detail_id_field else ID_FIELDS)
                try:
                    detail=GenericHttpDetailCollector(self.plan,self.client).fetch(str(jid))
                    merged={**raw,**detail} if isinstance(detail,dict) else raw
                    jd=pick(merged,("description","overview","content","jobDescription","responsibilities","requirements","qualifications"))
                    success=credible_jd(str(jd or ""));self.recorder.record_detail_request(success)
                    if success:raws[index]=merged
                    else:errors.append(make_error("JD_NOT_FOUND","detail API returned no credible JD",job_id=jid))
                except Exception as exc:
                    self.recorder.record_detail_request(False);errors.append(make_error("GENERIC_DETAIL_ERROR",type(exc).__name__,job_id=jid))
        if self.plan.detail_mode=="DETAIL_HTTP_HTML":
            id_fields=tuple(x for x in (self.plan.detail_id_field,*ID_FIELDS) if x);title_fields=tuple(x for x in (self.plan.job_title_field,*TITLE_FIELDS) if x)
            detail_collector=GenericHtmlDetailCollector(self.plan,self.client)
            raws,success,failures=detail_collector.enrich(raws,id_fields,title_fields)
            self.recorder.metrics.max_concurrency_observed=detail_collector.max_concurrency_observed
            for _ in range(success):self.recorder.record_detail_request(True)
            for code,jid in failures:
                self.recorder.record_detail_request(False);errors.append(make_error(code,"detail HTML enrichment failed",job_id=jid))
        if self.plan.detail_mode=="DETAIL_DOM" and self.plan.detail_jd_selector:
            id_fields=tuple(x for x in (self.plan.detail_id_field,*ID_FIELDS) if x)
            try:
                raws,success,failed=GenericDomDetailCollector(self.plan).enrich(raws,id_fields)
                for _ in range(success):self.recorder.record_detail_request(True)
                for _ in range(failed):self.recorder.record_detail_request(False)
                if failed:errors.append(make_error("JD_NOT_FOUND","planned detail pages returned no credible JD",count=failed))
            except Exception as exc:errors.append(make_error("GENERIC_DOM_DETAIL_ERROR",type(exc).__name__))
        audit=duplicate_audit(raws,self.plan);ambiguous={x["identity_value"] for x in audit["groups"] if x["reason"] in ("AMBIGUOUS_ID_COLLISION","UNEXPLAINED_DUPLICATE")};unique={};normalization_trace=[]
        for index,raw in enumerate(raws):
            job=self._job(raw)
            if job:
                key=job.job_id;classification="PRESERVED";reason=None
                if key in ambiguous:key+=":"+hashlib.sha256(json.dumps(safe_business_data(raw),sort_keys=True,default=str).encode()).hexdigest()[:12];job=job.model_copy(update={"job_id":key});classification="PRESERVED_COLLISION_SUFFIX"
                elif key in unique:classification="DROPPED_DUPLICATE";reason="IDENTITY_ALREADY_PRESENT"
                unique.setdefault(key,job);identity=job.raw_data.get("_identity",{})
                normalization_trace.append({"index":index,"raw_keys":sorted(raw),"normalized":True,"identity_source":identity.get("identity_source"),"identity_value":identity.get("identity_value"),"fingerprint":hashlib.sha256(json.dumps(safe_business_data(raw),sort_keys=True,default=str).encode()).hexdigest()[:16],"dedup_classification":classification,"reason":reason})
            else:
                jid=pick(raw,(self.plan.job_id_field,)+ID_FIELDS if self.plan.job_id_field else ID_FIELDS);title=pick(raw,(self.plan.job_title_field,)+TITLE_FIELDS if self.plan.job_title_field else TITLE_FIELDS)
                reason="ID_NOT_MAPPED" if jid is None else "TITLE_NOT_MAPPED" if not isinstance(title,str) else "NORMALIZATION_REJECTED"
                normalization_trace.append({"index":index,"raw_keys":sorted(raw),"normalized":False,"identity_source":None,"identity_value":None,"fingerprint":hashlib.sha256(json.dumps(safe_business_data(raw),sort_keys=True,default=str).encode()).hexdigest()[:16],"dedup_classification":"DROPPED_INVALID","reason":reason})
        audit["normalization_trace"]=normalization_trace
        if raws and not unique:
            reasons={}
            for item in normalization_trace:reasons[item["reason"]]=reasons.get(item["reason"],0)+1
            errors.append(make_error("NORMALIZATION_REJECTED_ALL",json.dumps(reasons,sort_keys=True)))
        if expected is None and termination in ("SINGLE_RESPONSE","HAS_MORE_FALSE","EMPTY_PAGE","SHORT_PAGE"):expected=len(unique)
        explained=audit["collapsed_count"]>0 and audit["unexplained_count"]==0
        if audit["unexplained_count"]:errors.append(make_error("UNEXPLAINED_DUPLICATES","duplicate records could not be safely collapsed",count=audit["unexplained_count"]))
        counts_complete=expected==len(raws) and (len(raws)==len(unique) or explained)
        status="FAILED" if not self.recorder.metrics.list_pages else ("COMPLETE" if counts_complete and not errors and expected is not None and expected>=0 and (unique or expected==0) else "INCOMPLETE")
        self._sync(clock)
        result=CollectionResult(source_url=self.plan.source_url,platform="generic",company=self.plan.company,scope=self.plan.scope,total_expected=expected,
            total_fetched=len(raws),total_unique=len(unique),status=status,jobs=list(unique.values()),errors=errors,duplicate_audit=audit,started_at=started,finished_at=datetime.now())
        result.metrics=self.recorder.metrics;result.data_completeness=evaluate_data_completeness(result)
        if result.data_completeness.source_incomplete_jobs:
            if result.data_completeness.missing_jd_jobs:
                result.warnings=(["DETAIL_STRATEGY_UNKNOWN"] if self.plan.detail_mode=="UNKNOWN" else [])+[f"SOURCE_DATA_INCOMPLETE count={result.data_completeness.source_incomplete_jobs}"]
            else:result.warnings=[f"STRUCTURED_FIELDS_UNAVAILABLE count={result.data_completeness.source_incomplete_jobs}; full_jd preserved"]
        if explained:result.warnings.append(f"DUPLICATES_EXPLAINED count={audit['collapsed_count']}")
        return result
    def _sync(self,clock):
        self.recorder.set_jd_strategy("DETAIL_REQUIRED" if self.plan.detail_mode in ("DETAIL_DOM","DETAIL_HTTP_HTML") else self.plan.detail_mode)
        m=self.recorder.finish();self.list_requests=m.list_requests;self.page_count=m.list_pages;self.elapsed_seconds=perf_counter()-clock
