from __future__ import annotations
import json,os,re
from pathlib import Path
from dataclasses import dataclass,field
from datetime import datetime
from time import perf_counter
from typing import Any
from urllib.parse import parse_qsl,urljoin,urlsplit
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from job_extractor.browser import BrowserRuntime,BrowserRuntimeError
from job_extractor.discovery.models import ApiCandidate,CandidateSource,DiscoveryResult,ListContainer,NetworkSummary,PaginationDetection,RejectedCandidate,VisibleTotalEvidence,ProvenanceRecord,TerminalActivationTrace,RecruitmentAction
from job_extractor.discovery.network_analyzer import request_shape,response_shape,safe_url,sanitized_values,find_field,get_path,list_observation,safe_business_data
from job_extractor.discovery.observation import ActivityClock,MAX_OBSERVATION_MS,MIN_OBSERVATION_MS,wait_for_activity_quiet,url_is_job_semantic
from job_extractor.discovery.boot_recovery import classify_boot_state,reload_allowed
from job_extractor.discovery.pagination_semantics import infer_pagination_from_schema
from job_extractor.discovery.scorer import confidence,reliable_list_candidate,score_detail,score_list
from job_extractor.discovery.dom_semantics import extract_company,extract_detail_dom,filter_detail_links,first_real_url,related_detail_hosts,repeated_job_cards,repeated_job_links,route_changed,semantic_html_detail
from job_extractor.discovery.dynamic import graphql_shape,is_pagination_control,navigation_trust,safe_graphql_body,serialized_states,visible_total_evidence,wait_for_dynamic_jd,wait_for_hydration,wait_for_readiness_consensus,window_state_blobs
from job_extractor.discovery.sources import embedded_sources,recruitment_entries,select_terminal_actions,spa_action_inventory,classify_request,rank_spa_action,trigger_job_page_search
from job_extractor.discovery.ats import profile_from_discovery
from job_extractor.discovery.site_identity import resolve_site_company
from job_extractor.debug_trace import emit as debug_trace
from job_extractor.discovery.containers import bind_total_candidates,list_container_evidence
from job_extractor import __version__

@dataclass
class _Observation:
    url:str; method:str; body:dict[str,Any]; query:dict[str,Any]; payload:Any; phase:str; replayable:bool=True; source_index:int|None=None; request_content_type:str|None=None
    origin_url:str|None=None; page_url:str|None=None; trigger_action_id:str|None=None; trigger_action_text:str|None=None; trigger_action_type:str|None=None
    provenance_trust:str="TRUSTED"; provenance_rejection:str|None=None
    runtime_query:dict[str,Any]=field(default_factory=dict)


class _SourceDiscoveryComplete(Exception):
    """Internal control flow: a usable source is already sufficient for Stage 1."""

class GenericApiDetector:
    threshold=10
    recruitment_scope_budget_seconds=10
    # Keep one source deadline, but do not let the first boot attempt consume
    # the time needed to make the single permitted recovery meaningful.
    _RECOVERY_RELOAD_ALLOWANCE_MS=1000
    _RECOVERY_HYDRATION_ALLOWANCE_MS=750
    _RECOVERY_TRIGGER_ALLOWANCE_MS=2250
    def __init__(self,browser_factory=BrowserRuntime,timeout_ms:int=15000,source_budget_seconds:int=25):
        self.browser_factory=browser_factory; self.timeout_ms=timeout_ms; self.source_budget_seconds=source_budget_seconds
    _JOB_PAGE_NODE_SELECTOR='a,button,[role="button"],[role="menuitem"],[data-route],li'

    def _stage_one_complete(self, observations:list[_Observation], observation_policy:dict[str,Any], expired:bool)->bool:
        """Keep optional page/detail enrichment outside the source-discovery budget."""
        if expired:
            return True
        if observation_policy.get("reason") != "SOURCE_FOUND_EARLY_EXIT":
            return False
        return any(self._reliable_list_source(candidate) for candidate in self._rank(observations))

    @classmethod
    def _job_page_search_trigger(cls,page,*,timeout_ms:int|None=None)->list[str]:
        return trigger_job_page_search(page,cls._JOB_PAGE_NODE_SELECTOR,timeout_ms=timeout_ms)

    @classmethod
    def _recovery_reserve_ms(cls, discovery_budget_seconds:float)->int:
        """Reserve reload, short hydration, trigger, and a real startup window.

        This is a partition of the existing source deadline, never a second
        budget.  Tiny test/caller budgets retain at least their startup minimum
        where possible instead of producing a negative first-attempt budget.
        """
        reserve=(cls._RECOVERY_RELOAD_ALLOWANCE_MS + cls._RECOVERY_HYDRATION_ALLOWANCE_MS
                 + cls._RECOVERY_TRIGGER_ALLOWANCE_MS + MIN_OBSERVATION_MS)
        total_ms=max(0,int(discovery_budget_seconds*1000))
        return min(reserve,max(0,total_ms-MIN_OBSERVATION_MS))

    def _recovery_trigger(self,page,observations:list[_Observation],deadline:float)->dict[str,Any]:
        """Try the existing generic trigger after reload without spending startup time."""
        before_url=getattr(page,"url",None)
        remaining_ms=max(0,int((deadline-perf_counter())*1000))
        source=next((candidate for candidate in self._rank(observations) if self._reliable_list_source(candidate)),None)
        trace={"recovery_trigger_attempted":False,"recovery_trigger_result":[],"clicked_text":[],
               "url_before":before_url,"url_after":before_url,"remaining_budget_ms_before_trigger":remaining_ms}
        if source:
            trace["recovery_trigger_result"]="SKIPPED_ACCEPTED_SOURCE"
            return trace
        # The observation minimum is protected even when a visible control
        # exists, so a trigger cannot recreate the old <1s recovery window.
        trigger_budget_ms=min(self._RECOVERY_TRIGGER_ALLOWANCE_MS,max(0,remaining_ms-MIN_OBSERVATION_MS))
        if trigger_budget_ms<=0:
            trace["recovery_trigger_result"]="SKIPPED_STARTUP_WINDOW_RESERVED"
            return trace
        trace["recovery_trigger_attempted"]=True
        clicked=self._job_page_search_trigger(page,timeout_ms=trigger_budget_ms)
        trace["recovery_trigger_result"]="CLICKED" if clicked else "NO_JOB_CONTROL"
        trace["clicked_text"]=clicked
        trace["url_after"]=getattr(page,"url",before_url)
        return trace

    @staticmethod
    def _body(request)->dict[str,Any]:
        try:
            value=request.post_data_json
            if isinstance(value,dict):return value
        except Exception:pass
        try:
            raw=request.post_data
            if raw and "application/x-www-form-urlencoded" in (request.headers.get("content-type") or "").lower():
                return {k:v for k,v in parse_qsl(raw,keep_blank_values=True)}
        except Exception:pass
        return {}
    @staticmethod
    def _blocked(text:str)->bool:
        value=text.lower()
        return any(x in value for x in ("captcha","human verification","verify you are human","安全验证","人机验证","登录后查看"))
    @staticmethod
    def _scope(url:str,observations:list[_Observation],page_text:str="")->dict[str,Any]:
        scope={}; low=url.lower();_,source_query=safe_url(url)
        allowed=("location","department","team","category","keyword")
        filters={k:v for k,v in source_query.items() if k.lower() in allowed and v!="[REDACTED]"}
        if filters:scope["url_filters"]=filters
        for name in ("campus","social","intern","graduate","experienced"):
            if name in low:scope["recruitment_type"]=name;break
        path=urlsplit(url).path
        path_match=re.search(r"(?i)/locations?/([^/]+?)(?:\.html)?/?$",path) or re.search(r"(?i)/go/([^/]+)/\d+/?$",path)
        if path_match:
            value=path_match.group(1).replace("-"," ").strip();needle=value.lower()
            if needle and needle in page_text.lower():scope["path_filters"]={"location":value}
        for observation in observations:
            for source in (observation.body,observation.query):
                for key,value in source.items():
                    k=key.lower()
                    if any(x in k for x in ("site_id","siteid","tenant_id","tenantid","project_id","projectid","website_id","websiteid")) and isinstance(value,(str,int)) and value not in ("",0):
                        scope.setdefault(key,value)
                    if "recruit" in k and isinstance(value,(str,int)) and value not in ("",0):scope.setdefault(key,value)
        return scope
    @staticmethod
    def _pagination(observations:list[_Observation],candidate:ApiCandidate|None)->PaginationDetection:
        if not candidate or candidate.rejection_reasons:return PaginationDetection()
        relevant=[o for o in observations if safe_url(o.url)[0]==candidate.url and o.method==candidate.method]
        keys={str(k).lower():str(k) for o in relevant for source in (o.body,o.query) for k in source}
        result=PaginationDetection(total_field=candidate.response_shape.get("total_field"))
        gql_type=candidate.response_shape.get("pagination_type")
        if gql_type:
            return PaginationDetection(pagination_type=gql_type,page_param=candidate.response_shape.get("page_param"),page_size_param=candidate.response_shape.get("page_size_param"),cursor_param=candidate.response_shape.get("page_param") if gql_type=="GRAPHQL_CURSOR" else None,total_field=candidate.response_shape.get("total_field"),next_cursor_field=candidate.response_shape.get("next_cursor_field"),has_more_field=candidate.response_shape.get("has_more_field"))
        # Schema-based inference from the observed request (body/query) and response, even for a single first-page capture.
        if relevant:
            observation=relevant[0]
            inferred=infer_pagination_from_schema(request_body=observation.body if isinstance(observation.body,dict) else {},
                query=observation.query,response=observation.payload,list_length=candidate.observed_list_length)
            if inferred.get("kind"):
                result.pagination_type=inferred["kind"];result.page_param=inferred["page_param"];result.page_size_param=inferred["size_param"]
                result.first_page=inferred["first_page"];result.page_size=inferred["page_size"]
                if inferred.get("total_path"):result.total_field=inferred["total_path"]
                result.inference_source=inferred["inference_source"];result.confidence=inferred["confidence"];result.evidence=inferred["evidence"]
        if result.pagination_type=="UNKNOWN":
            for name in ("offset",):
                if name in keys:result.pagination_type="OFFSET";result.page_param=keys[name];break
        if result.pagination_type=="UNKNOWN":
            for name in ("page","pageindex","pageno","current"):
                if name in keys:result.pagination_type="PAGE";result.page_param=keys[name];break
        if result.pagination_type=="UNKNOWN":
            for name in ("cursor","nextcursor","next_cursor"):
                if name in keys:result.pagination_type="CURSOR";result.cursor_param=keys[name];break
        if result.page_size_param is None:
            for name in ("limit","pagesize","page_size","size"):
                if name in keys:result.page_size_param=keys[name];break
        if len(relevant)>1:
            a={**relevant[0].query,**relevant[0].body}; b={**relevant[-1].query,**relevant[-1].body}
            changes=[f"{k}: {a[k]} -> {b[k]}" for k in a.keys()&b.keys() if a[k]!=b[k] and not any(x in k.lower() for x in ("token","key","signature","csrf","session"))]
            if changes:result.observed_change=", ".join(changes[:3])
        return result
    @staticmethod
    def _candidate(observation:_Observation,detail:bool=False)->ApiCandidate:
        clean,query=safe_url(observation.url)
        if detail: score,evidence=score_detail(clean,observation.payload); shape=response_shape(observation.payload)
        else: score,evidence,shape=score_list(clean,observation.payload)
        gql=graphql_shape(observation.body,observation.payload) if not detail else {}
        if gql:shape.update(gql)
        list_path=shape.get("candidate_list_path");full_sample=get_path(observation.payload,list_path) if list_path!="$" else observation.payload
        if not isinstance(full_sample,list):list_path,length,sample=list_observation(observation.payload)
        else:length=len(full_sample);sample=full_sample[:20]
        if gql:
            list_path=gql["candidate_list_path"];full_sample=get_path(observation.payload,list_path) or [];length=len(full_sample);sample=full_sample[:20]
            if gql.get("list_item_path"):sample=[get_path(x,gql["list_item_path"]) for x in sample]
        total_path=shape.get("total_field");total=get_path(observation.payload,total_path)
        has_more_path=shape.get("has_more_field") or find_field(observation.payload,{"hasmore","has_more","hasnextpage"});has_more=get_path(observation.payload,has_more_path)
        hint={};company_names=set();company_name_samples=set()
        url_field=None;url_coverage=0.0;unique_ids=None
        if sample and isinstance(sample[0],dict):
            item=sample[0]
            for key in ("id","job_id","jobId","jobPostId","positionId","requisitionId","title","name","jobTitle","positionName","company_name","recruitment_type_cn","recruitmentTypeName","nature_cn","natureName",*tuple(("url","absolute_url","job_url","jobUrl","detail_url","detailUrl","apply_url","applyUrl","path","slug"))):
                if key in item and isinstance(item[key],(str,int)):hint[key]=item[key]
            if isinstance(item.get("company"),dict):hint["company"]={k:v for k,v in item["company"].items() if k in ("name","identifier") and isinstance(v,(str,int))}
            url_fields=[]
            for record in sample:
                if not isinstance(record,dict):continue
                _,field=first_real_url(record,observation.url)
                if field:url_fields.append(field)
                company_value=record.get("company_name")
                if not company_value and isinstance(record.get("company"),dict):company_value=record["company"].get("name")
                if isinstance(company_value,str) and company_value.strip():company_names.add(company_value.strip())
            if company_names:company_name_samples.update(company_names)
            if url_fields:
                url_field=max(set(url_fields),key=url_fields.count);url_coverage=url_fields.count(url_field)/len(sample)
                evidence.append(f"detail URL field {url_field} covers {url_fields.count(url_field)}/{len(sample)} sampled records")
            id_fields=("id","job_id","jobId","jobPostId","positionId","requisitionId","requisition_id")
            chosen_id=next((key for key in id_fields if all(isinstance(x,dict) and x.get(key) not in (None,"") for x in sample)),None)
            if chosen_id:
                unique_ids=len({str(x[chosen_id]) for x in sample})
                if unique_ids==len(sample):evidence.append(f"all {len(sample)} sampled records have unique stable IDs")
        if isinstance(total,int) and total==length:evidence.append("API total equals observed list length")
        if has_more is False:evidence.append("response explicitly reports hasMore=false")
        safe_query={k:v for k,v in query.items() if v!="[REDACTED]"}
        safe_values=safe_query if observation.method=="GET" else sanitized_values(observation.body);replayable=observation.replayable
        if gql:safe_values,replayable=safe_graphql_body(observation.body);replayable=replayable and observation.replayable
        provenance=ProvenanceRecord(origin_url=observation.origin_url or observation.page_url or clean,url=clean,host=urlsplit(clean).hostname or "",trigger_action_id=observation.trigger_action_id,trigger_action_text=observation.trigger_action_text,trigger_action_type=observation.trigger_action_type,trust=observation.provenance_trust,rejected_reason=observation.provenance_rejection)
        if observation.trigger_action_id:
            provenance.company_context_evidence.append(f"terminal action {observation.trigger_action_id}: {observation.trigger_action_text or ''}".strip())
        if any(term in clean.lower() for term in ("privacy", "policy", "config")) and "NON_JOB_SEMANTIC_SOURCE" not in shape.setdefault("rejection_reasons",[]):
            shape["rejection_reasons"].append("NON_JOB_SEMANTIC_SOURCE")
        if observation.provenance_rejection and observation.provenance_rejection not in shape.setdefault("rejection_reasons",[]):shape["rejection_reasons"].append(observation.provenance_rejection)
        candidate=ApiCandidate(url=clean,method=observation.method,score=score,confidence=confidence(score),
            request_body_shape=request_shape(observation.body),query_params=query,
            safe_request_values=safe_values,request_content_type=observation.request_content_type,
            response_shape=shape,evidence=evidence,sample_job_hint=safe_business_data(hint),observed_list_length=length,
            observed_total=total if isinstance(total,int) else None,observed_has_more=has_more if isinstance(has_more,bool) else None,
            observed_unique_ids=unique_ids,detail_url_field=url_field,detail_url_coverage=url_coverage,
            homogeneity_score=shape.get("homogeneity_score",0.0),job_entity_density=shape.get("job_entity_density",0.0),
            rejection_reasons=shape.get("rejection_reasons",[]),observed_company_count=len(company_names),observed_company_names=sorted(company_name_samples)[:30],list_item_path=shape.get("list_item_path"),replayable=replayable,graphql_operation=shape.get("graphql_operation"),graphql_page_info_path=shape.get("graphql_page_info_path"),source_type="SERIALIZED_STATE" if observation.method=="STATE" else ("GRAPHQL" if gql else "NETWORK_JSON"),source_index=observation.source_index,provenance=provenance,observed_phase="POST_ROUTE_NETWORK" if observation.phase=="POST_ROUTE_NETWORK" else observation.phase)
        # Retain only values that the safe candidate view deliberately hid.
        # Public query parameters remain in query_params and persisted plans.
        candidate._runtime_query_params={key:value for key,value in observation.runtime_query.items()
                                         if query.get(key)=="[REDACTED]"}
        return candidate
    @staticmethod
    def _rank(observations:list[_Observation],detail:bool=False)->list[ApiCandidate]:
        grouped={}; counts={}
        for o in observations:
            candidate=GenericApiDetector._candidate(o,detail)
            # Requests to one endpoint are separate list candidates whenever
            # observed non-pagination scope fields differ (e.g. campaign,
            # nature, recruitment type).  Pagination requests for the same
            # scope remain one candidate.
            values=candidate.safe_request_values if candidate.method=="POST" else candidate.query_params
            identity={k:v for k,v in values.items() if k.lower() in ("nature","project_id","projectid","recruitment_type","recruitmenttype","channel","campaign","batch")}
            key=(candidate.url,candidate.method,candidate.source_index,json.dumps(identity,sort_keys=True,default=str))
            counts[key]=counts.get(key,0)+1
            if key not in grouped or candidate.score>grouped[key].score:grouped[key]=candidate
        for key,value in grouped.items():value.sample_count=counts[key]
        return sorted((x for x in grouped.values() if x.score>0 and not x.rejection_reasons),key=lambda x:(-x.score,x.url,x.method))

    @staticmethod
    def _enumerate_recruitment_scopes(page,base_url:str,observations:list[_Observation],phase:list[str],expired,social_trace=None)->None:
        """Bounded tab enumeration after a real list source exists.

        This is intentionally limited to visible controls whose full label is
        explicitly recruitment-scope semantic.  A scope is accepted later
        only if its click yields a normal high-confidence list response.
        """
        started=perf_counter();clicked_candidates=[];unresolved_scopes=[];exit_reason="completed"
        def trace(event,**data):debug_trace(f"SCOPE_TRACE {event}",data)
        def identity(body):return {k:v for k,v in (body or {}).items() if k.lower() in ("nature","project_id","projectid","recruitment_type","recruitmenttype","channel","campaign","batch")}
        trace("SCOPE_ENUM_START",url=getattr(page,"url",base_url),deadline=getattr(expired,"deadline",None),remaining_seconds=max(0.0,getattr(expired,"deadline",started)-started),observations=len(observations))
        try:
            controls=page.evaluate("""() => [...document.querySelectorAll('a,button,[role=tab],[role=button]')]
                .map((n,i)=>({i,text:(n.innerText||'').trim().replace(/\\s+/g,' '),href:n.getAttribute('href')||'',
                    active:n.getAttribute('aria-current')==='page'||n.getAttribute('aria-selected')==='true'||/(^|\\s)(active|selected)(\\s|$)/.test(n.className||''),
                    visible:!!(n.offsetWidth||n.offsetHeight||n.getClientRects().length)}))""") or []
        except Exception as exc:
            trace("SCOPE_ENUM_END",exit_reason="exception",exception=type(exc).__name__,elapsed=perf_counter()-started,candidates_seen=0,candidates_clicked=[]);return
        pattern=re.compile(r"(?i)^(?:社会招聘|社招|校园招聘|校招|专项招聘|实习招聘|实习生|海外招聘|[\\w\\u4e00-\\u9fff ]{1,24}(?:招聘|招募)|campus(?: recruitment)?|social(?: recruitment)?|intern(?:ship)?s?|experienced hires?)$")
        labels=[]
        for control in controls:
            label=str(control.get("text") or "").strip()
            semantic=bool(pattern.match(label));accepted=bool(control.get("visible") and not control.get("active") and semantic and label not in labels)
            reason="accepted" if accepted else "current_tab" if control.get("active") else "not_visible" if not control.get("visible") else "semantic_reject" if not semantic else "duplicate_label"
            trace("SCOPE_CANDIDATE",index=control.get("i"),text=label,href=control.get("href"),visible=bool(control.get("visible")),semantic_match=semantic,accepted=accepted,reject_reason=reason)
            if accepted:
                labels.append(label)
        if not labels:exit_reason="no_candidates"
        for label in labels[:6]:
            if expired():exit_reason="budget_expired";trace("SCOPE_CLICK_SKIP",text=label,reason="expired");break
            prior_ids={id(observation) for observation in observations};before=len(observations);before_url=getattr(page,"url",base_url);phase[0]=f"SCOPE_{label}";scope_phase=phase[0]
            href=next((str(x.get("href") or "") for x in controls if str(x.get("text") or "").strip()==label),"")
            trace("SCOPE_CLICK_ATTEMPT",text=label,href=href,current_url=before_url,remaining_seconds=max(0.0,getattr(expired,"deadline",perf_counter())-perf_counter()))
            try:
                nodes=page.locator('a,button,[role=tab],[role=button]')
                click_success=False
                for index in range(min(nodes.count(),500)):
                    if (nodes.nth(index).inner_text() or "").strip().replace("\n"," ")==label:
                        nodes.nth(index).click(timeout=3000);click_success=True;break
                if not click_success:
                    trace("SCOPE_NAV_RESULT",candidate=label,before_url=before_url,after_url=getattr(page,"url",base_url),success=False,exception="CONTROL_NOT_FOUND");continue
                # A mounted shell must never end this wait: hold the scope phase
                # until THIS tab produced a new accepted list observation (or the
                # bounded budget ran out).  DOM readiness is auxiliary only.
                wait_started=perf_counter()
                remaining_seconds=max(0.0,getattr(expired,"deadline",wait_started)-wait_started)
                wait_budget_ms=int(min(5000,remaining_seconds*1000))
                if scope_phase=="SCOPE_社会招聘" and social_trace:
                    social_trace("SOCIAL_WAIT_START",scope_phase=scope_phase,wait_budget_ms=wait_budget_ms)
                if wait_budget_ms<=0:
                    wait_result={"accepted":False,"exit_reason":"budget_expired","elapsed_ms":0,"dom":None}
                else:
                    wait_result=GenericApiDetector._wait_for_accepted_scope_observation(page,observations,prior_ids,scope_phase,expired,timeout_ms=wait_budget_ms,interval_ms=250)
                if scope_phase=="SCOPE_社会招聘" and social_trace:
                    social_trace("SOCIAL_WAIT_END",scope_phase=scope_phase,accepted_current_phase_list=wait_result["accepted"],exit_reason=wait_result["exit_reason"],elapsed_ms=wait_result["elapsed_ms"],dom=wait_result.get("dom"))
                clicked_candidates.append(label)
                if not wait_result["accepted"]:
                    unresolved_scopes.append({"label":label,"exit_reason":wait_result["exit_reason"],"elapsed_ms":wait_result["elapsed_ms"]})
                trace("SCOPE_NAV_RESULT",candidate=label,before_url=before_url,after_url=getattr(page,"url",base_url),success=True,exception=None,accepted_scope_observation=wait_result["accepted"],wait=wait_result,wait_seconds=perf_counter()-wait_started)
            except Exception as exc:
                trace("SCOPE_NAV_RESULT",candidate=label,before_url=before_url,after_url=getattr(page,"url",base_url),success=False,exception=type(exc).__name__)
                continue
            for sequence,observation in enumerate(observations[before:],1):
                if "project-job" not in observation.url:continue
                candidate=GenericApiDetector._candidate(observation);trusted=GenericApiDetector._accepted_list_candidate(candidate)
                trace("SCOPE_REQUEST",sequence=sequence,elapsed_seconds=perf_counter()-started,endpoint=candidate.url,method=candidate.method,nature=observation.body.get("nature"),project_id=observation.body.get("project_id"),total=candidate.observed_total,score=candidate.score,rejection_reasons=candidate.rejection_reasons,accepted_list_candidate=trusted,scope_identity=identity(observation.body))
            retained=[identity(o.body if o.method=="POST" else o.query) for o in observations if GenericApiDetector._accepted_list_candidate(GenericApiDetector._candidate(o))]
            trace("SCOPE_RETENTION",count=len({json.dumps(x,sort_keys=True,default=str) for x in retained}),identities=retained)
        phase[0]="HYDRATION"
        retained=[identity(o.body if o.method=="POST" else o.query) for o in observations if GenericApiDetector._accepted_list_candidate(GenericApiDetector._candidate(o))]
        trace("SCOPE_ENUM_END",exit_reason=exit_reason,elapsed=perf_counter()-started,candidates_seen=len(controls),candidates_clicked=clicked_candidates,unresolved_scopes=unresolved_scopes,trusted_scope_identities=retained)
    @classmethod
    def _reliable_list_source(cls,candidate:ApiCandidate|None)->bool:
        return reliable_list_candidate(candidate)

    @classmethod
    def _accepted_list_candidate(cls,candidate:ApiCandidate|None)->bool:
        """The same candidate gate used for the detector's final probable API."""
        return bool(candidate and candidate.score>=cls.threshold and not candidate.rejection_reasons)

    @classmethod
    def _new_accepted_scope_observation(cls,observations:list[_Observation],prior_ids:set[int],scope_phase:str)->bool:
        """Do not let a previous tab's delayed response satisfy this tab."""
        return any(id(observation) not in prior_ids and observation.phase==scope_phase
                   and cls._accepted_list_candidate(cls._candidate(observation)) for observation in observations)

    @staticmethod
    def _wait_for_accepted_scope_observation(page,observations:list[_Observation],prior_ids:set[int],scope_phase:str,expired,timeout_ms:int=5000,interval_ms:int=250)->dict[str,Any]:
        """Wait for THIS scope phase to produce a new accepted list observation.

        ``wait_for_hydration`` short-circuits on any mounted root
        (rootChildren>0 / readyState complete), which an SPA satisfies with an
        empty shell before the tab's list request is even issued.  A scope
        click therefore only ends this wait when the observation predicate
        fires; DOM readiness is recorded as auxiliary evidence and can never
        terminate the wait on its own.  Bounded by the caller's shared
        enumeration budget and ``timeout_ms`` — never an infinite wait.
        """
        started=perf_counter();deadline=started+max(0.0,timeout_ms/1000)
        def dom_signal()->dict[str,Any]|None:
            try:
                return page.evaluate("""() => {
                    const root=document.querySelector('#app,#root,main,[role="main"]');
                    const text=(document.body?.innerText || '').trim();
                    return {readyState:document.readyState,rootChildren:root?.childElementCount||0,links:document.querySelectorAll('a[href]').length,textLength:text.length};
                }""")
            except Exception:
                return None
        exit_reason="observation_timeout"
        while True:
            if GenericApiDetector._new_accepted_scope_observation(observations,prior_ids,scope_phase):
                exit_reason="accepted_current_phase_list";break
            if expired() or perf_counter()>=deadline:
                exit_reason="budget_expired" if expired() else "observation_timeout";break
            page.wait_for_timeout(interval_ms)
        return {"accepted":exit_reason=="accepted_current_phase_list","exit_reason":exit_reason,"elapsed_ms":round((perf_counter()-started)*1000),"dom":dom_signal()}

    def _accepted_list_source(self,observations:list[_Observation])->ApiCandidate|None:
        ranked=self._rank(observations)
        candidate=ranked[0] if ranked else None
        return candidate if self._accepted_list_candidate(candidate) else None

    def _effective_list_source(self,observations:list[_Observation],*fallbacks:ApiCandidate|None)->ApiCandidate|None:
        """Use final-plan candidate semantics for scope enumeration only."""
        current=next((candidate for candidate in self._rank(observations) if self._reliable_list_source(candidate)),None)
        return current or next((candidate for candidate in fallbacks if candidate is not None),None) or self._accepted_list_source(observations)

    def _scope_enumeration_expired(self):
        """A bounded follow-up budget once a real list source is confirmed."""
        deadline=perf_counter()+self.recruitment_scope_budget_seconds
        def expired():return perf_counter()>=deadline
        expired.deadline=deadline
        return expired

    def discover(self,url:str,budget=None,terminal_mode:bool=False,upstream_entry_action:RecruitmentAction|None=None)->DiscoveryResult:
        started=datetime.now(); clock=perf_counter(); observations=[]; phase=["TERMINAL_INITIAL" if terminal_mode else "INITIAL"]; summary=NetworkSummary(); dom={};detail_dom={};warnings=[];company=None;page_title=None;pagination_override=None;inventory=[];total_evidence=[];total_conflict=False;entries=[]
        timing_enabled=os.getenv("STEP95G_TIMING")=="1"; timings:dict[str,float]={}; events:dict[str,float]={}
        def timed(name:str,started_at:float)->None:
            if timing_enabled:timings[name]=timings.get(name,0.0)+(perf_counter()-started_at)
        def event(name:str)->None:
            if timing_enabled and name not in events:events[name]=perf_counter()-clock
        def write_timing()->None:
            if not timing_enabled:return
            lines=["Stage1 timing:"]
            for name,value in timings.items():lines.append(f"- {name}: {value:.3f}s")
            for name,value in events.items():lines.append(f"- {name}: {value:.3f}s")
            lines.append(f"- total_discover_duration: {perf_counter()-clock:.3f}s")
            with open("/tmp/job_extractor_step95g_timing.log","w",encoding="utf-8") as handle:
                handle.write("\n".join(lines)+"\n")
        terminal_trace=TerminalActivationTrace(terminal_url=url,scope="UNKNOWN",activation_started_at=started,upstream_entry_action=upstream_entry_action) if terminal_mode else None
        terminal_network=[];terminal_dom=[];terminal_actions=[];terminal_states=[];terminal_frames=[];runtime_source=None;internal_trace=[]
        activity_clock=ActivityClock();boot_reload_count=0;boot_attempts=[];domcontentloaded=False;observation_policy={}
        # Terminal activation supplies a shared monotonic budget.  Honour it
        # here so an invisible downstream probe cannot outlive the caller's
        # deadline before the CLI has a chance to render a terminal result.
        discovery_budget_seconds=min(self.source_budget_seconds, budget.remaining) if budget is not None else self.source_budget_seconds
        # STEP97: one shared, monotonic deadline for the whole first source
        # discovery lifecycle. Every navigation/wait inside this try block must
        # consume remaining budget instead of stacking its own independent
        # timeout, so a hung page converges at the source budget (25s) rather
        # than the sum of serial waits (goto 15s + hydration 5s + activity 7.5s
        # + readiness 15s ≈ 44s).
        source_deadline=clock+discovery_budget_seconds
        def remaining_source_seconds()->float:
            return max(0.0,source_deadline-perf_counter())
        def source_expired()->bool:
            return remaining_source_seconds()<=0.0 or bool(budget is not None and budget.expired)
        recovery_reserve_ms=self._recovery_reserve_ms(discovery_budget_seconds)
        initial_attempt_deadline=source_deadline-recovery_reserve_ms/1000
        def remaining_initial_attempt_seconds()->float:
            return max(0.0,initial_attempt_deadline-perf_counter())
        def initial_attempt_expired()->bool:
            return remaining_initial_attempt_seconds()<=0.0 or bool(budget is not None and budget.expired)
        def _expired():return source_expired()
        social_trace_path=Path("output/debug_cosco_social_scope.jsonl");social_trace_path.parent.mkdir(parents=True,exist_ok=True);social_trace_path.write_text("",encoding="utf-8");social_sequence=[0]
        def social_trace(event,**data):
            social_sequence[0]+=1
            social_trace_path.open("a",encoding="utf-8").write(json.dumps({"sequence":social_sequence[0],"event":event,"t":perf_counter()-clock,**data},ensure_ascii=False,default=str)+"\n")
        project_request_starts={};request_phases={};first_accepted_time=[None]
        debug_trace("TIMING_TRACE DETECTOR_START",{"source_deadline_seconds":discovery_budget_seconds})
        terminal_failure=None
        if budget is not None and budget.expired:
            write_timing()
            return DiscoveryResult(source_url=url,status="TIMEOUT",network_summary=summary,warnings=["SOURCE_DISCOVERY_TIMEOUT"],failure_classification="SOURCE_DISCOVERY_TIMEOUT",started_at=started,finished_at=datetime.now(),elapsed_seconds=perf_counter()-clock)
        try:
            browser_setup_started=perf_counter()
            with self.browser_factory(timeout_ms=self.timeout_ms) as runtime:
                page=runtime.page
                def request_started(request):
                    request_phases[id(request)]=phase[0]
                    # Social scope evidence: the tab's list transport is not
                    # necessarily the project-job endpoint (this site's social
                    # tab POSTs /api/jobs/v1/list with a nature filter), so the
                    # trace gates key on the API namespace, not one path.
                    if phase[0]=="SCOPE_社会招聘" and request.method=="POST" and "api/jobs/v1/" in request.url:
                        social_trace("SOCIAL_NETWORK_REQUEST",request_phase=phase[0],url=safe_url(request.url)[0],nature=self._body(request).get("nature"),project_id=self._body(request).get("project_id"),company_id_with_sub=self._body(request).get("company_id_with_sub"),page=self._body(request).get("page"),page_size=self._body(request).get("page_size"))
                    if request.method=="POST" and "api/jobs/v1/project-job" in request.url:
                        project_request_starts[id(request)]=perf_counter()
                        debug_trace("TIMING_TRACE PROJECT_REQUEST_START",{"t":perf_counter()-clock,"url":safe_url(request.url)[0]})
                page.on("request",request_started)
                timed("browser_setup",browser_setup_started)
                def dom_snapshot():
                    try:
                        body=page.locator("body").inner_text(timeout=3000)[:20000]
                        cards=repeated_job_cards(page,url)
                        links=[x.get("detail_link") for x in cards if x.get("detail_link")]
                        headings=[]
                        for selector in ("h1,h2,h3,h4,[role=heading]"):
                            node=page.locator(selector)
                            for index in range(min(node.count(),100)):
                                text=(node.nth(index).inner_text() or "").strip()
                                if text and re.search(r"(?i)(job|career|position|engineer|developer|manager|职位|岗位|招聘|工程师|经理)",text):headings.append(text[:160])
                        controls=[]
                        for selector in ("button,[role=button],input,select"):
                            node=page.locator(selector)
                            for index in range(min(node.count(),100)):
                                text=((node.nth(index).inner_text() or "")+" "+(node.nth(index).get_attribute("aria-label") or "")+" "+(node.nth(index).get_attribute("placeholder") or "")).strip()
                                if text:controls.append(text[:120])
                        return {"url":page.url,"title":page.title(),"dom_signature":hash((len(body),tuple(x.get("structure_signature") for x in cards),tuple(links))) & 0xffffffff,"job_card_count":len(cards),"unique_titles":len({x.get("title") for x in cards if x.get("title")}),"unique_links":len(set(links)),"main_text_length":len(body),"visible_job_headings":list(dict.fromkeys(headings))[:50],"detail_apply_links":links[:100],"pagination_controls":[x for x in controls if re.search(r"(?i)(next|page|加载更多|下一页|上一页)",x)],"search_filter_controls":[x for x in controls if re.search(r"(?i)(search|filter|搜索|筛选)",x)]}
                    except Exception:
                        return {"url":getattr(page,"url",url),"title":"","dom_signature":0,"job_card_count":0,"unique_titles":0,"unique_links":0,"main_text_length":0,"visible_job_headings":[],"detail_apply_links":[],"pagination_controls":[],"search_filter_controls":[]}
                def observe(response):
                    summary.observed_requests+=1
                    request=response.request
                    project_response_at=perf_counter() if request.method=="POST" and "api/jobs/v1/project-job" in request.url else None
                    if request.resource_type in ("xhr","fetch"):activity_clock.record_activity(request.url)
                    if terminal_mode:
                        record={"phase":phase[0],"action_id":phase[0] if phase[0].startswith("TERMINAL_ACTION_") else None,"url":safe_url(request.url)[0],"method":request.method,"resource_type":request.resource_type,"status":response.status,"content_type":response.headers.get("content-type") or ""}
                        try:
                            body=self._body(request)
                            record["request_values"]={k:v for k,v in body.items() if isinstance(v,(int,float,bool)) and str(k).lower() in ("offset","limit","page","pagesize","page_size","size","pageindex","pageno")}
                        except Exception:record["request_values"]={}
                        if request.resource_type in ("xhr","fetch") and "json" in (response.headers.get("content-type") or "").lower():
                            try:
                                payload=response.json();candidate=self._candidate(_Observation(request.url,request.method,self._body(request),{},payload,phase[0],origin_url=url,page_url=page.url))
                                record.update({"top_level_type":type(payload).__name__,"top_level_keys":list(payload)[:50] if isinstance(payload,dict) else [],"candidate_list_path":candidate.response_shape.get("candidate_list_path"),"array_length":candidate.observed_list_length,"sample_field_names":candidate.response_shape.get("sample_field_names",[])[:50],"job_likeness_score":candidate.score,"classification":"REJECTED" if candidate.rejection_reasons else "CANDIDATE","rejection_reasons":candidate.rejection_reasons})
                            except Exception:pass
                        terminal_network.append(record)
                    if request.resource_type not in ("xhr","fetch"):return
                    summary.xhr_fetch_requests+=1
                    content=(response.headers.get("content-type") or "").lower()
                    if "json" not in content:return
                    try:payload=response.json()
                    except Exception:return
                    if not isinstance(payload,(dict,list)):return
                    summary.json_candidates+=1;summary.phase_json_candidates[phase[0]]=summary.phase_json_candidates.get(phase[0],0)+1; clean,query=safe_url(request.url)
                    activity_clock.record_json(request.url)
                    raw_query=dict(parse_qsl(urlsplit(request.url).query,keep_blank_values=True))
                    observation = _Observation(request.url,request.method,self._body(request),query,payload,request_phases.get(id(request),phase[0]),request_content_type=request.headers.get("content-type"),runtime_query=raw_query)
                    observations.append(observation)
                    if observation.phase=="SCOPE_社会招聘" and "api/jobs/v1/" in observation.url:
                        candidate=self._candidate(observation)
                        social_trace("SOCIAL_NETWORK_RESPONSE",request_phase=observation.phase,url=clean,total=candidate.observed_total,response_success=True)
                        social_trace("SOCIAL_OBSERVATION_CREATED",observation_id=id(observation),request_phase=observation.phase,current_scope_phase=phase[0],endpoint=candidate.url,score=candidate.score,rejection_reasons=candidate.rejection_reasons,accepted_list_candidate=self._accepted_list_candidate(candidate),reliable_list_source=self._reliable_list_source(candidate),scope_identity={k:v for k,v in observation.body.items() if k.lower() in ("nature","project_id","projectid","recruitment_type","recruitmenttype")})
                    if project_response_at is not None:
                        candidate=self._candidate(observation);accepted=self._accepted_list_candidate(candidate);reliable=self._reliable_list_source(candidate)
                        if accepted and first_accepted_time[0] is None:first_accepted_time[0]=perf_counter()-clock
                        debug_trace("TIMING_TRACE PROJECT_RESPONSE",{"request_t":project_request_starts.get(id(request)),"response_t":project_response_at-clock,"parsed_t":perf_counter()-clock,"inserted_t":perf_counter()-clock,"nature":observation.body.get("nature"),"project_id":observation.body.get("project_id"),"total":candidate.observed_total,"score":candidate.score,"rejection_reasons":candidate.rejection_reasons,"accepted":accepted,"reliable":reliable})
                    # Strong boot-recovery evidence is deliberately structural:
                    # reuse the normal generic list scorer/reliability gate,
                    # rather than treating a job-like URL (or its hostname) as
                    # proof that a real list request has occurred.
                    if self._reliable_list_source(self._candidate(observation)):
                        activity_clock.record_reliable_list()
                        event("first_candidate_seen")
                page.on("response",observe)
                navigation_started=perf_counter()
                debug_trace("TIMING_TRACE GOTO_START",{"t":perf_counter()-clock})
                goto_deadline_ms=int(remaining_initial_attempt_seconds()*1000)
                if goto_deadline_ms<=0:
                    warnings.append("NAVIGATION_SKIPPED_SOURCE_BUDGET_EXHAUSTED")
                else:
                    try:
                        page.goto(url,wait_until="domcontentloaded",timeout=min(self.timeout_ms,goto_deadline_ms))
                        domcontentloaded=True
                    except PlaywrightTimeoutError:
                        warnings.append("NAVIGATION_TIMEOUT_CONTINUING")
                timed("page_navigation",navigation_started)
                debug_trace("TIMING_TRACE GOTO_END",{"t":perf_counter()-clock})
                # STEP78A: page-side scripts can reset the freshly loaded
                # document to about:blank within seconds of navigation (live
                # evidence: goto → 22s idle with no detector code in between
                # still ends at page.url == about:blank). Snapshot the served
                # first-response HTML immediately so embedded-state extraction
                # later in this flow can still read the markup the server
                # delivered. No host-specific logic.
                initial_inspect_started=perf_counter()
                try:served_html=page.content()
                except Exception:served_html=""
                phase[0]="TERMINAL_HYDRATION" if terminal_mode else "HYDRATION";hydration_started=perf_counter();hydration=wait_for_hydration(page,lambda:len(observations),timeout_ms=min(5000,int(remaining_initial_attempt_seconds()*1000)),deadline_check=initial_attempt_expired);debug_trace("TIMING_TRACE HYDRATION_END",{"start_t":hydration_started-clock,"end_t":perf_counter()-clock,"result":hydration})
                if not hydration["stabilized"]:warnings.append("HYDRATION_TIMEOUT")
                timed("dom_ready_initial_html_inspect",initial_inspect_started)
                # Landing pages often load only campaign metadata.  Before
                # spending the primary observation window, take one existing
                # safe, job-semantic navigation when no credible source has
                # appeared.  The response listener is already attached.
                safe_navigation_taken=False
                quick_rank=self._rank(observations)
                quick_source=next((candidate for candidate in quick_rank if self._reliable_list_source(candidate)),None)
                if not quick_source and not initial_attempt_expired():
                    phase[0]="SAFE_JOB_NAVIGATION"
                    triggered=self._job_page_search_trigger(page,timeout_ms=int(remaining_initial_attempt_seconds()*1000))
                    if triggered:
                        safe_navigation_taken=True
                        page.wait_for_timeout(2000)
                    phase[0]="TERMINAL_HYDRATION" if terminal_mode else "HYDRATION"
                # Activity-aware observation window (STEP 53D): a handful of early
                # portal-config JSONs reaching a stable DOM does NOT mean the page
                # is done loading.  Keep observing until network activity has been
                # quiet, the DOM is stable, and no job-semantic request is pending —
                # bounded by a hard maximum deadline.  A HIGH-confidence source
                # found early still exits quickly instead of idling.
                network_observation_started=perf_counter()
                if not initial_attempt_expired():
                    observation_policy=wait_for_activity_quiet(
                        page,activity_clock,dom_snapshot,
                        has_high_confidence_source=lambda:bool(next((c for c in self._rank(observations) if self._reliable_list_source(c)),None)),
                        deadline_check=initial_attempt_expired,
                        max_observation_ms=min(MAX_OBSERVATION_MS,int(remaining_initial_attempt_seconds()*1000)))
                    summary.observation_policy=observation_policy
                    if observation_policy["reason"]=="MAX_OBSERVATION_DEADLINE":warnings.append("OBSERVATION_MAX_DEADLINE")
                    if not observations:wait_for_readiness_consensus(page,lambda:len(observations),timeout_ms=min(self.timeout_ms,15000,int(remaining_initial_attempt_seconds()*1000)),deadline_check=initial_attempt_expired)
                    elif not any("job-semantic" in str(getattr(x,"url","")) for x in observations) and not observation_policy["job_semantic_requests"]:
                        # No job-semantic request has been seen yet: give a bounded
                        # extra chance for slow SPA chains (generic, not site-tied).
                        wait_for_readiness_consensus(page,lambda:len(observations),timeout_ms=min(3000,int(remaining_initial_attempt_seconds()*1000)),deadline_check=initial_attempt_expired)
                timed("network_observation",network_observation_started)
                # STEP 53E: a quiet SPA that had activity but never triggered a
                # job request may be stuck during boot.  This is intentionally
                # evaluated before state/source ranking and is bounded to one
                # generic reload.  Reload attempts start with clean observation
                # state so stale first-attempt candidates cannot affect ranking.
                initial_attempt_rank=self._rank(observations)
                initial_attempt_source=next((c for c in initial_attempt_rank if self._reliable_list_source(c)),None)
                retry_source=None
                # Some SPAs finish their initial route transition only after
                # hydration.  Retry only when the first attempt did not
                # actually navigate and no credible source has appeared.
                if not initial_attempt_source and not safe_navigation_taken and not initial_attempt_expired():
                    phase[0]="SAFE_JOB_NAVIGATION"
                    triggered=self._job_page_search_trigger(page,timeout_ms=int(remaining_initial_attempt_seconds()*1000))
                    if triggered:
                        safe_navigation_taken=True
                        page.wait_for_timeout(2000)
                        initial_attempt_rank=self._rank(observations)
                        initial_attempt_source=next((c for c in initial_attempt_rank if self._reliable_list_source(c)),None)
                    phase[0]="TERMINAL_HYDRATION" if terminal_mode else "HYDRATION"
                boot_text=page.locator("body").inner_text(timeout=3000)[:20000]
                boot_snapshot=dom_snapshot()
                boot_environment_failure=bool(re.search(r"(?i)(site can.t be reached|dns_probe|err_[a-z_]+|service unavailable|bad gateway)",boot_text))
                boot_decision=classify_boot_state(
                    domcontentloaded=domcontentloaded,
                    elapsed_ms=observation_policy.get("elapsed_ms",0) if not _expired() else activity_clock.elapsed_ms(),
                    activity_observed=activity_clock.activity_count,
                    job_semantic_requests=activity_clock.job_semantic_count,
                    reliable_list_responses=activity_clock.reliable_list_count,
                    network_quiet_ms=activity_clock.quiet_ms(),
                    dom_job_ready=boot_snapshot.get("job_card_count",0)>=2,
                    source_found=bool(initial_attempt_source),
                    auth_or_captcha=self._blocked(boot_text),
                    environment_failure=boot_environment_failure,
                )
                boot_attempts.append({"attempt":1,"state":boot_decision.state,"reason":boot_decision.reason,**boot_decision.evidence,"source_found":bool(initial_attempt_source)})
                # The reserve is conditional: once this boot state cannot use
                # recovery, return its unused share to ordinary observation.
                # This preserves the full shared source budget for healthy,
                # slow, or no-activity pages while never creating a second one.
                if (not reload_allowed(boot_decision,boot_reload_count) and (initial_attempt_expired() or observation_policy.get("reason")=="MAX_OBSERVATION_DEADLINE")
                        and not _expired() and not initial_attempt_source):
                    observation_policy=wait_for_activity_quiet(
                        page,activity_clock,dom_snapshot,
                        has_high_confidence_source=lambda:bool(next((c for c in self._rank(observations) if self._reliable_list_source(c)),None)),
                        deadline_check=source_expired,
                        max_observation_ms=min(MAX_OBSERVATION_MS,int(remaining_source_seconds()*1000)))
                    summary.observation_policy=observation_policy
                recovery_trace={"recovery_trigger_attempted":False,"recovery_trigger_result":"NOT_ATTEMPTED","clicked_text":[],
                                "url_before":None,"url_after":None,"remaining_budget_ms_before_reload":int(remaining_source_seconds()*1000),
                                "remaining_budget_ms_after_reload":None,"recovery_observation_budget_ms":0,
                                "recovery_reserve_ms":recovery_reserve_ms}
                if reload_allowed(boot_decision,boot_reload_count) and not _expired():
                    boot_reload_count+=1;warnings.append("BOOT_STALL_RELOAD_ATTEMPTED")
                    # Attempt isolation: listeners remain attached but write only
                    # to the fresh state below after reload.
                    observations.clear();inventory.clear();summary=NetworkSummary();activity_clock=ActivityClock();domcontentloaded=False
                    phase[0]="BOOT_RECOVERY_RELOAD"
                    try:
                        page.reload(wait_until="domcontentloaded",timeout=max(1,min(self.timeout_ms,self._RECOVERY_RELOAD_ALLOWANCE_MS,int(remaining_source_seconds()*1000))))
                        domcontentloaded=True
                        phase[0]="BOOT_RECOVERY_HYDRATION"
                        recovery_started=perf_counter();hydration=wait_for_hydration(page,lambda:len(observations),timeout_ms=min(self._RECOVERY_HYDRATION_ALLOWANCE_MS,int(remaining_source_seconds()*1000)),deadline_check=source_expired);debug_trace("TIMING_TRACE RECOVERY_HYDRATION_END",{"start_t":recovery_started-clock,"end_t":perf_counter()-clock,"result":hydration})
                        if not hydration["stabilized"]:warnings.append("BOOT_RECOVERY_HYDRATION_TIMEOUT")
                        if not _expired():
                            recovery_trace["remaining_budget_ms_after_reload"]=int(remaining_source_seconds()*1000)
                            recovery_trace.update(self._recovery_trigger(page,observations,source_deadline))
                            recovery_observation_budget_ms=min(MAX_OBSERVATION_MS,int(remaining_source_seconds()*1000))
                            recovery_trace["recovery_observation_budget_ms"]=recovery_observation_budget_ms
                            observation_policy=wait_for_activity_quiet(
                                page,activity_clock,dom_snapshot,
                                has_high_confidence_source=lambda:bool(next((c for c in self._rank(observations) if self._reliable_list_source(c)),None)),
                                deadline_check=source_expired,
                                max_observation_ms=recovery_observation_budget_ms)
                            summary.observation_policy=observation_policy
                    except Exception as exc:
                        warnings.append("BOOT_STALL_RELOAD_FAILED")
                        boot_attempts.append({"attempt":2,"state":"RELOAD_FAILED","reason":type(exc).__name__})
                    else:
                        retry_rank=self._rank(observations)
                        retry_source=next((c for c in retry_rank if self._reliable_list_source(c)),None)
                        retry_text=page.locator("body").inner_text(timeout=3000)[:20000]
                        retry_snapshot=dom_snapshot()
                        retry_decision=classify_boot_state(
                            domcontentloaded=domcontentloaded,
                            elapsed_ms=observation_policy.get("elapsed_ms",activity_clock.elapsed_ms()),
                            activity_observed=activity_clock.activity_count,
                            job_semantic_requests=activity_clock.job_semantic_count,
                            reliable_list_responses=activity_clock.reliable_list_count,
                            network_quiet_ms=activity_clock.quiet_ms(),
                            dom_job_ready=retry_snapshot.get("job_card_count",0)>=2,
                            source_found=bool(retry_source),
                            auth_or_captcha=self._blocked(retry_text),
                            environment_failure=bool(re.search(r"(?i)(site can.t be reached|dns_probe|err_[a-z_]+|service unavailable|bad gateway)",retry_text)),
                        )
                        boot_attempts.append({"attempt":2,"state":retry_decision.state,"reason":retry_decision.reason,**retry_decision.evidence,"source_found":bool(retry_source)})
                        if retry_decision.state=="QUIET_BOOT_STALL":warnings.append("BOOT_STALL_UNRECOVERED")
                        elif retry_source:warnings.append("BOOT_STALL_RECOVERED")
                summary.boot_recovery={"reload_count":boot_reload_count,"attempts":boot_attempts,"max_reloads":1,**recovery_trace}
                # A trustworthy initial list proves we are on a recruitment
                # page, but not that its currently selected tab is the whole
                # site.  Before taking the fast exit, try only explicit
                # recruitment-type controls and let normal response scoring
                # decide whether each switch is a valid additional scope.
                effective_scope_source=self._effective_list_source(observations,retry_source,initial_attempt_source)
                def _source_trace(candidate):
                    if candidate is None:return None
                    values=candidate.safe_request_values if candidate.method=="POST" else candidate.query_params
                    return {"endpoint":candidate.url,"method":candidate.method,"nature":values.get("nature"),"project_id":values.get("project_id")}
                debug_trace("SCOPE_TRACE ENTER_SCOPE_ENUMERATION",{"initial_attempt_source":bool(initial_attempt_source),"retry_source":bool(retry_source),"effective_scope_source":bool(effective_scope_source),"effective_source":_source_trace(effective_scope_source),"current_url":getattr(page,"url",url),"source_elapsed_seconds":perf_counter()-clock,"enter":bool(effective_scope_source)})
                if effective_scope_source:
                    self._enumerate_recruitment_scopes(page,url,observations,phase,self._scope_enumeration_expired(),social_trace=social_trace)
                # Stage 1 has its answer once the observation policy found a
                # reliable list source.  DOM/detail enrichment below is useful
                # only after source discovery and must never turn a quick source
                # hit into a detail-render wait.  Likewise, the source budget is
                # a real boundary rather than a warning followed by more probes.
                if self._stage_one_complete(observations,summary.observation_policy,_expired()):
                    event("source_found_early_exit")
                    if _expired() and "SOURCE_DISCOVERY_TIMEOUT" not in warnings:
                        warnings.append("SOURCE_DISCOVERY_TIMEOUT")
                    # STEP96: the early exit skips the later extract_company()
                    # call, leaving result.company unresolved for sources that
                    # are found quickly.  Fill it from the same safe evidence
                    # (structured metadata / page title) before bailing out.
                    if not company:
                        try:company=extract_company(page)
                        except Exception:pass
                    if page_title is None:
                        try:page_title=page.title() or None
                        except Exception:pass
                    raise _SourceDiscoveryComplete()
                # STEP78: SSR states are present in the first HTML response —
                # extraction is a cheap, non-blocking read of markup the page
                # already served, so it must run even when the observation
                # budget expired during the activity-quiet wait. A fully
                # server-rendered list never emits job-semantic XHRs, so that
                # wait idles to its deadline and the previous budget guard
                # discarded data already in hand (EMBEDDED_JOB_DISCOVERY_MISS).
                serialized_state_started=perf_counter()
                states=list(serialized_states(page))
                for state_index,state in enumerate(states):
                    observations.append(_Observation(url,"STATE",{}, {},state,"HYDRATION",False,state_index))
                    if terminal_mode:
                        terminal_states.append({"source":"script[type=application/json]/__NEXT_DATA__","json_paths":list(state)[:50] if isinstance(state,dict) else [],"array_lengths":{},"sample_field_names":list(state)[:50] if isinstance(state,dict) else [],"job_likeness":0,"rejection_reason":"LOW_JOB_ENTITY_DENSITY"})
                # STEP73: raw-HTML window-global states (window.__INITIAL_DATA__ /
                # window.__INITIAL_STATE__ / window.__NUXT__) enter the same STATE
                # STEP78A: extract window blobs from the HTML snapshot taken
                # right after navigation. Some sites blank their own document
                # between navigation and this point (no detector reload/close
                # happens in between), so the live DOM may no longer contain
                # the served markup. The snapshot is the same first-response
                # HTML the SERIALIZED_STATE collector refetches later, keeping
                # source_index alignment. If even the snapshot is an empty
                # stub, refetch the first document generically over HTTP,
                # mirroring the collector's raw fetch. No host-specific logic.
                extraction_html=served_html
                if len(extraction_html)<=256:
                    try:
                        import httpx
                        extraction_html=httpx.get(url,timeout=15,follow_redirects=True,headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}).text
                    except Exception:
                        extraction_html=""
                try:
                    window_blobs=window_state_blobs(extraction_html)
                except Exception:
                    window_blobs=[]
                for blob_offset,blob in enumerate(window_blobs):
                    state_index=len(states)+blob_offset
                    observations.append(_Observation(url,"STATE",{}, {},blob,"HYDRATION",False,state_index))
                    if terminal_mode:
                        terminal_states.append({"source":"window.__INITIAL_DATA__/__INITIAL_STATE__/__NUXT__","json_paths":list(blob)[:50] if isinstance(blob,dict) else [],"array_lengths":{},"sample_field_names":list(blob)[:50] if isinstance(blob,dict) else [],"job_likeness":0,"rejection_reason":"LOW_JOB_ENTITY_DENSITY"})
                if terminal_mode:
                    for frame in page.frames:
                        terminal_frames.append({"frame_url":frame.url,"title":"","recruitment_semantics":bool(re.search(r"(?i)(job|career|recruit|招聘|职位)",frame.url)),"job_like_requests":False})
                inventory.extend(embedded_sources(page,url))
                entries=recruitment_entries(page,url)
                company=extract_company(page)
                if page_title is None:
                    try:page_title=page.title() or None
                    except Exception:pass
                text=page.locator("body").inner_text(timeout=3000)[:20000]
                timed("serialized_state_script_inspection",serialized_state_started)
                if terminal_mode:
                    terminal_dom.append(dom_snapshot())
                    job_entries=[entry for entry in entries if re.search(r"(?i)#/job/",entry.url) and len(entry.text)>20]
                    terminal_dom[-1].update({"repeated_card_clusters":[{"structure_signature":"RECRUITMENT_ENTRY_JOB","count":len(job_entries)}] if job_entries else [],"job_card_count":len(job_entries),"unique_titles":len({entry.text.splitlines()[1].strip() if len(entry.text.splitlines())>1 else entry.text[:120] for entry in job_entries}),"unique_links":len({entry.url for entry in job_entries}),"visible_job_headings":[entry.text.splitlines()[1].strip()[:160] if len(entry.text.splitlines())>1 else entry.text[:160] for entry in job_entries[:50]],"detail_apply_links":[entry.url for entry in job_entries[:100]]})
                    actions=[]; action_nodes=page.locator('a[href],button,[role="link"],[data-href]')
                    for index in range(min(action_nodes.count(),500)):
                        node=action_nodes.nth(index);label=(node.inner_text() or "").strip();href=node.get_attribute("href") or node.get_attribute("data-href") or ""
                        if label:
                            actions.append((index,RecruitmentAction(control_id=str(index),text=label,target=urljoin(page.url,href) if href else None,action_type="LINK" if href else "BUTTON",source_element="a" if href else "button",href=href,role=node.get_attribute("role"),onclick=node.get_attribute("onclick"),data_route=node.get_attribute("data-route"),data_url=node.get_attribute("data-url"),job_semantics=["JOB"] if re.search(r"(?i)(job|position|career|招聘|职位|岗位)",label) else [],score=rank_spa_action(label,href))) )
                    chosen=select_terminal_actions([action for _,action in actions])
                    chosen_ids={action.control_id for action in chosen}
                    for index,action in actions:
                        if action.control_id not in chosen_ids:continue
                        before=dom_snapshot();phase[0]=f"TERMINAL_ACTION_{len(terminal_actions)+1}";error=None
                        try:
                            action_nodes.nth(index).click(timeout=3000);page.wait_for_timeout(1000)
                        except Exception as exc:error=f"{type(exc).__name__}: {exc}"
                        phase[0]=f"TERMINAL_ACTION_{len(terminal_actions)+1}_POST";after=dom_snapshot()
                        terminal_actions.append({"action_id":action.control_id,"text":action.text,"element_type":action.source_element,"href":action.href,"onclick":action.onclick,"role":action.role,"data_route":action.data_route,"data_url":action.data_url,"semantic_score":action.score,"reason_selected":"highest-ranked terminal action","before":before,"after":after,"route_changed":before["url"]!=after["url"],"dom_changed":before["dom_signature"]!=after["dom_signature"] or before["main_text_length"]!=after["main_text_length"],"error":error})
                        terminal_dom.append(after)
                    phase[0]="TERMINAL_HYDRATION"
                if self._blocked(text):
                    return DiscoveryResult(source_url=url,status="BLOCKED",network_summary=summary,warnings=["HUMAN_VERIFICATION_OR_LOGIN_REQUIRED"],started_at=started,finished_at=datetime.now(),elapsed_seconds=perf_counter()-clock)
                initial_rank=self._rank(observations);initial_probable=initial_rank[0] if initial_rank and initial_rank[0].score>=self.threshold else None
                # A V1 input may already render its job entities from a
                # plaintext browser store while its transport is encrypted,
                # cached, or otherwise not a replayable JSON response.  This
                # generic observation is deliberately gated by both visible
                # recruitment semantics and the runtime scanner's structural
                # HIGH-confidence id/title evidence; it is not an ATS or host
                # specific fallback.
                if not initial_probable and not _expired() and re.search(r"(?i)(job|position|career|招聘|职位|岗位)", text):
                    try:
                        from job_extractor.discovery.runtime_data import observe_runtime_job_source,detect_pagination_trigger
                        observed_requests=[{**o.query,**o.body} for o in observations if o.method in ("GET","POST")]
                        observed_runtime=observe_runtime_job_source(page,provider="UNKNOWN",observed_requests=observed_requests,trigger=detect_pagination_trigger(page))
                        if observed_runtime.confidence=="HIGH" and observed_runtime.records:
                            runtime_source=observed_runtime
                            inventory.append(CandidateSource(source_type="RUNTIME_STATE",url=url,status="CANDIDATE",score=15,confidence="HIGH",evidence=observed_runtime.evidence,metadata={"mechanism":observed_runtime.mechanism,"record_count":observed_runtime.record_count,"source_path":observed_runtime.source_path}))
                    except Exception:
                        pass
                optional_probes_started=perf_counter();internal_trace=[]
                if not initial_probable and not _expired():
                    from job_extractor.discovery.internal_navigation import run_internal_job_navigation
                    def _rank_now():
                        ranked=self._rank([o for o in observations if o.phase!="DETAIL"])
                        reliable=next((candidate for candidate in ranked if self._reliable_list_source(candidate)),None)
                        return (reliable,reliable.score) if reliable else (None,0)
                    initial_probable,internal_trace=run_internal_job_navigation(page,url,observations,rank_fn=_rank_now,deadline_check=_expired)
                timed("optional_probes",optional_probes_started)
                links=page.locator('a[href]'); hrefs=[]
                for i in range(min(links.count(),1000)):
                    href=links.nth(i).get_attribute("href") or ""; label=(links.nth(i).inner_text() or "").strip()
                    if href:hrefs.append((href,label))
                cards=repeated_job_cards(page,url);structural_links=[x["detail_link"] for x in cards if x.get("detail_link")]
                all_accepted,rejected_links=filter_detail_links(hrefs,url,structural_links)
                observed_links=filter_detail_links([(x,"") for x in structural_links],url,structural_links)[0] if structural_links else all_accepted
                observed_links=list(dict.fromkeys(observed_links))
                try:html_snapshot=page.content()
                except Exception:html_snapshot=""
                # ``observed_links`` are absolute, whereas SSR markup usually
                # preserves relative hrefs.  Compare the captured raw hrefs.
                ssr_links=sum(1 for href,_label in hrefs if urljoin(url,href) in observed_links and href in html_snapshot)>=2
                trusted_hosts=related_detail_hosts(url,observed_links)
                complex_count=len(structural_links)
                try:
                    complex_nodes=page.locator('[role="link"],button[onclick],[data-href]')
                    labels=[]
                    for i in range(min(complex_nodes.count(),200)):
                        label=(complex_nodes.nth(i).inner_text() or "").strip()
                        if 3<=len(label)<=160 and re.search(r"(?i)(engineer|manager|analyst|developer|specialist|designer|director|intern|\u5de5\u7a0b\u5e08|\u7ecf\u7406|\u5b9e\u4e60)",label):labels.append(label)
                    complex_count=max(complex_count,len(set(labels)))
                except Exception:pass
                scope_context=self._scope(url,observations,text);container=list_container_evidence(page,cards,scope_context)
                if not container and len(observed_links)==1 and len(text)<1000:
                    legacy_totals,legacy_bound,legacy_conflict=bind_total_candidates([{"text":text,"proximity":100,"locator":"body-test-fallback"}],len(observed_links),scope_context,"legacy-link-list")
                    container=ListContainer(container_id="legacy-link-list",job_card_count=len(observed_links),unique_detail_links=len(observed_links),scope_context=scope_context,nearby_visible_totals=legacy_totals,bound_total=legacy_bound,total_conflict=legacy_conflict)
                total_evidence=[x.model_dump() for x in container.nearby_visible_totals] if container else []
                total_conflict=bool(container and container.total_conflict);result_count=container.bound_total if container else None
                if total_conflict:warnings.append("VISIBLE_TOTAL_CONFLICT")
                count_parity=result_count is not None and result_count==len(observed_links) and result_count>0
                dom_status="DOM_LIST_DETECTED" if len(observed_links)>=2 or count_parity else ("DOM_COMPLEX_DETECTED" if complex_count>=2 else "UNKNOWN")
                if result_count is not None and len(observed_links)>result_count:warnings.append("JOB_CARD_LINK_MISMATCH")
                card_titles={x["detail_link"]:x["title"] for x in cards if x.get("detail_link") and x.get("title")}
                card_ids={x["detail_link"]:x["explicit_id"] for x in cards if x.get("detail_link") and x.get("explicit_id")}
                extra_noncard=[x for x in all_accepted if x not in set(structural_links)]
                dom={"status":dom_status,"job_card_count":len(cards) or len(observed_links) or complex_count,"visible_result_count":result_count,"possible_title_selector":"a[href]" if observed_links else None,"possible_detail_links":observed_links[:500],"complex_job_nodes":complex_count,"rejected_link_count":len(rejected_links),"trusted_detail_hosts":trusted_hosts,"serialized_state_count":sum(o.method=="STATE" for o in observations),"ssr_html_links":ssr_links,"job_cards":cards[:500],"originating_titles":card_titles,"originating_ids":card_ids,"card_link_parity":"CARD_LINK_PARITY" if len(cards)==len(observed_links) and cards else "MISSING_CARD_LINKS" if len(cards)>len(observed_links) else "EXTRA_NONCARD_LINKS","extra_noncard_links":extra_noncard[:100]}
                detail_hint=None;detail_url_field=None;sample_title="";sample_id=""
                detail_source=next((c for c in initial_rank if c.confidence=="HIGH" and first_real_url(c.sample_job_hint,url)[0]),initial_probable)
                if detail_source:
                    detail_hint,detail_url_field=first_real_url(detail_source.sample_job_hint,url)
                    sample_title=str(detail_source.sample_job_hint.get("title") or detail_source.sample_job_hint.get("name") or detail_source.sample_job_hint.get("jobTitle") or "").strip().lower()
                    sample_id=str(detail_source.sample_job_hint.get("id") or detail_source.sample_job_hint.get("job_id") or detail_source.sample_job_hint.get("jobId") or "")
                    if not detail_hint:
                        for href,label in hrefs:
                            if (sample_id and sample_id in href) or (sample_title and sample_title==label.strip().lower()):
                                detail_hint=urljoin(url,href)
                                if sample_id and sample_id in detail_hint:detail_dom["url_template"]=detail_hint.replace(sample_id,"{id}",1)
                                break
                phase[0]="SCROLL"
                if _expired():warnings.append("SOURCE_DISCOVERY_TIMEOUT")
                try:
                    if _expired():raise Exception("SOURCE_DISCOVERY_TIMEOUT")
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)");page.wait_for_timeout(500)
                    scrolled_cards=repeated_job_cards(page,url)
                    if len(scrolled_cards)>len(cards):
                        cards=scrolled_cards;structural_links=[x["detail_link"] for x in cards if x.get("detail_link")]
                        observed_links=filter_detail_links([(x,"") for x in structural_links],url,structural_links)[0]
                        pagination_override="INFINITE_SCROLL"
                except Exception:pass
                phase[0]="PAGINATION"
                selectors=[] if _expired() else ['a[rel="next"]','.pagination-next:not(.disabled)','.ant-pagination-next:not(.ant-pagination-disabled)','.atsx-pagination-next[aria-disabled="false"]']
                pagination_clicked=False
                for selector in selectors:
                    locator=page.locator(selector)
                    if locator.count():
                        try:
                            before_urls=set(observed_links);locator.first.click(timeout=3000);page.wait_for_timeout(1200);pagination_clicked=True
                            new_cards=repeated_job_cards(page,url);new_urls={x["detail_link"] for x in new_cards if x.get("detail_link")}
                            if new_urls-before_urls:pagination_override="LOAD_MORE";observed_links=list(dict.fromkeys(observed_links+list(new_urls)))
                        except Exception:warnings.append("PAGINATION_INTERACTION_FAILED")
                        break
                if not pagination_clicked:
                    try:
                        controls=page.locator('button,[role="button"],a[aria-label]')
                        for i in range(0 if _expired() else min(controls.count(),100)):
                            label=((controls.nth(i).inner_text() or "")+" "+(controls.nth(i).get_attribute("aria-label") or "")).strip()
                            if is_pagination_control(label):
                                before_urls=set(observed_links);controls.nth(i).click(timeout=3000);page.wait_for_timeout(1200)
                                new_cards=repeated_job_cards(page,url);new_urls={x["detail_link"] for x in new_cards if x.get("detail_link")}
                                if new_urls-before_urls:pagination_override="LOAD_MORE";observed_links=list(dict.fromkeys(observed_links+list(new_urls)))
                                pagination_clicked=True;break
                    except Exception:pass
                if not pagination_clicked:
                    pass
                count_parity=result_count is not None and result_count==len(observed_links) and result_count>0
                dom.update({"status":"DOM_LIST_DETECTED" if len(observed_links)>=2 or count_parity else dom["status"],"job_card_count":len(cards) or len(observed_links) or complex_count,"possible_title_selector":"a[href]" if observed_links else None,"possible_detail_links":observed_links[:500],"trusted_detail_hosts":related_detail_hosts(url,observed_links)})
                if terminal_mode:
                    try:
                        from job_extractor.discovery.runtime_data import observe_runtime_job_source,detect_pagination_trigger
                        observed_requests=[r.get("request_values") or {} for r in terminal_network if r.get("request_values")]
                        runtime_source=observe_runtime_job_source(page,provider="UNKNOWN",observed_requests=observed_requests,trigger=detect_pagination_trigger(page))
                    except Exception:
                        runtime_source=None
                # STEP 54B: when a credible runtime source was identified but the
                # list JD is incomplete, observe one organic detail request: the
                # page itself issues it after navigating a sample job route. The
                # contract is bound to observed runtime-state evidence only — no
                # host, provider, or route-specific inference. Fail-closed: no
                # observation means no contract and jobs stay honestly incomplete.
                if runtime_source is not None and runtime_source.confidence=="HIGH" and runtime_source.records and not _expired():
                    from job_extractor.discovery.runtime_data import observe_runtime_detail_contract
                    needs_detail=any(
                        not record.get(runtime_source.jd_fields[0]) if runtime_source.jd_fields else True
                        for record in runtime_source.records
                    ) if runtime_source.jd_fields else True
                    sample_id=next((str(record.get(runtime_source.job_id_field)) for record in runtime_source.records
                                    if runtime_source.job_id_field and record.get(runtime_source.job_id_field) not in (None,"")),None)
                    if needs_detail and sample_id:
                        try:
                            detail_contract=observe_runtime_detail_contract(page,sample_job_id=sample_id)
                        except Exception:
                            detail_contract={}
                        if detail_contract:
                            runtime_source=runtime_source.model_copy(update={"detail_contract":detail_contract})
                chosen_detail=detail_hint or (observed_links[0] if observed_links else None)
                if chosen_detail and not _expired():
                    phase[0]="DETAIL"
                    try:
                        before_url=page.url;response=page.goto(chosen_detail,wait_until="domcontentloaded",timeout=self.timeout_ms)
                        title_probe=None
                        try:title_probe=page.locator("h1").first.inner_text().strip()
                        except Exception:pass
                        static_detail=semantic_html_detail(page.content(),sample_title or title_probe,sample_id,page.url)
                        jd=static_detail.get("full_jd");jd_selector=None;jd_wait={"state":"STATIC_HTML"}
                        if static_detail.get("failure"):jd,jd_selector,jd_wait=wait_for_dynamic_jd(page,title_probe)
                        semantic=extract_detail_dom(page);semantic["jd"]=jd;semantic["jd_selector"]=jd_selector;company=company or extract_company(page)
                        redirects=0
                        try:
                            request=response.request
                            while request.redirected_from:redirects+=1;request=request.redirected_from
                        except Exception:pass
                        source_host=(urlsplit(url).hostname or "").lower();target_host=(urlsplit(chosen_detail).hostname or "").lower();final_host=(urlsplit(page.url).hostname or "").lower()
                        audit={"source_host":source_host,"target_host":target_host,"redirect_count":redirects,"final_host":final_host,"trust_decision":navigation_trust(source_host,target_host,final_host,redirects,bool(jd))}
                        if not static_detail.get("failure"):
                            prior_template=detail_dom.get("url_template")
                            detail_dom={"status":"DETAIL_HTTP_HTML","sample_url":chosen_detail,"url_field":detail_url_field,"url_template":prior_template,"route_changed":route_changed(before_url,page.url),"navigation_audit":[audit],"source_type":"DOM_DETAIL","title_match":static_detail.get("title_match"),"bound_id":static_detail.get("bound_id")}
                        elif semantic.get("jd"):
                            prior_template=detail_dom.get("url_template");detail_dom={k:v for k,v in semantic.items() if k.endswith("_selector") or k in ("location","department","employment_type","work_mode")}
                            detail_dom.update({"status":"DETAIL_DOM","sample_url":chosen_detail,"url_field":detail_url_field,"url_template":prior_template,"route_changed":route_changed(before_url,page.url),"navigation_audit":[audit]})
                        else:warnings.append("JD_RENDER_TIMEOUT")
                    except Exception:warnings.append("SPA_DETAIL_NOT_RESOLVED")
        except _SourceDiscoveryComplete:
            pass
        except BrowserRuntimeError as exc:
            write_timing()
            return DiscoveryResult(source_url=url,status="FAILED",network_summary=summary,warnings=[str(exc)],failure_classification="BROWSER_RUNTIME_ERROR",started_at=started,finished_at=datetime.now(),elapsed_seconds=perf_counter()-clock)
        except Exception as exc:
            warnings.append(f"DISCOVERY_PAGE_ERROR {type(exc).__name__}")
            terminal_failure=f"DISCOVERY_PAGE_ERROR {type(exc).__name__}"
        except BaseException as exc:
            # STEP76A2: driver/transport level signals (KeyboardInterrupt,
            # SystemExit, greenlet death inside the sync playwright driver)
            # must never end the parent process silently — convert them into a
            # terminal Python-level failure the CLI can render.
            write_timing()
            return DiscoveryResult(source_url=url,status="FAILED",network_summary=summary,warnings=[f"PROCESS_TERMINATION_{type(exc).__name__}"],failure_classification=f"PROCESS_TERMINATION_{type(exc).__name__}",started_at=started,finished_at=datetime.now(),elapsed_seconds=perf_counter()-clock)
        candidate_validation_started=perf_counter()
        list_observations=[o for o in observations if o.phase!="DETAIL"]
        detail_observations=[o for o in observations if o.phase=="DETAIL"]
        lists=self._rank(list_observations); details=[x for x in self._rank(detail_observations,True) if x.score>=self.threshold]
        debug_trace("TIMING_TRACE DETECTOR_FINAL",{"t":perf_counter()-clock,"first_accepted_t":first_accepted_time[0],"expired":_expired(),"observation_count":len(observations)})
        debug_trace("SCOPE_TRACE DETECTOR_FINAL_SCOPES",[{"endpoint":candidate.url,"method":candidate.method,"identity":(candidate.safe_request_values if candidate.method=="POST" else candidate.query_params),"nature":(candidate.safe_request_values if candidate.method=="POST" else candidate.query_params).get("nature"),"project_id":(candidate.safe_request_values if candidate.method=="POST" else candidate.query_params).get("project_id"),"total":candidate.observed_total,"phase":candidate.observed_phase} for candidate in lists])
        timed("candidate_validation",candidate_validation_started)
        rejected=[]
        seen_rejected=set()
        for observation in list_observations:
            candidate=self._candidate(observation)
            key=(candidate.url,candidate.method,tuple(candidate.rejection_reasons))
            if candidate.rejection_reasons and key not in seen_rejected:
                seen_rejected.add(key);rejected.append(RejectedCandidate(url=candidate.url,method=candidate.method,score=candidate.score,reasons=candidate.rejection_reasons,response_shape={k:v for k,v in candidate.response_shape.items() if k!="sample_field_names"}))
        inventory_seen={(x.source_type,x.url) for x in inventory}
        for observation in list_observations:
            candidate=self._candidate(observation);key=(candidate.source_type,candidate.url)
            if key in inventory_seen:continue
            inventory_seen.add(key);inventory.append(CandidateSource(source_type=candidate.source_type,url=candidate.url,status="REJECTED" if candidate.rejection_reasons else "CANDIDATE",score=candidate.score,confidence=candidate.confidence,evidence=candidate.evidence,metadata={"phase":observation.phase,"list_path":candidate.response_shape.get("candidate_list_path"),"source_index":candidate.source_index,"rejection_reasons":candidate.rejection_reasons}))
        if dom.get("status") in ("DOM_LIST_DETECTED","DOM_COMPLEX_DETECTED"):
            inventory.append(CandidateSource(source_type="DOM_LIST",url=url,status="CANDIDATE",score=15 if dom.get("possible_detail_links") else 8,confidence="HIGH" if dom.get("possible_detail_links") else "LOW",evidence=[dom.get("card_link_parity") or "repeated DOM structure observed"],metadata={"cards":dom.get("job_card_count",0),"links":len(dom.get("possible_detail_links") or []),"visible_total":dom.get("visible_result_count")}))
        present={x.source_type for x in inventory}
        for source_type in ("NETWORK_JSON","GRAPHQL","SERIALIZED_STATE","DOM_LIST","IFRAME","EMBEDDED_WIDGET"):
            if source_type not in present:inventory.append(CandidateSource(source_type=source_type,status="NONE",evidence=["NONE"]))
        probable=self._accepted_list_source(list_observations)
        # Multi-company protection: no single job-level employer may be
        # promoted to the site identity. Instead of discarding identity
        # outright, hosted multi-company portals (group recruitment sites)
        # get one site-level resolution attempt from evidence discovery
        # already holds; anything unresolved stays None (fail closed).
        site_identity=None
        if probable and probable.observed_company_count>1:
            company=None
            site_identity=resolve_site_company(url,observations,lists,page_title)
            if site_identity:
                company=site_identity.company
                debug_trace("SITE_IDENTITY_RESOLVED",{"host":urlsplit(url).hostname,"company":site_identity.company,"evidence":site_identity.evidence})
        elif not company and probable:
            company=probable.sample_job_hint.get("company_name")
            if not company and isinstance(probable.sample_job_hint.get("company"),dict):company=probable.sample_job_hint["company"].get("name")
        summary.job_api_candidates=sum(x.score>=self.threshold for x in lists)
        pagination_started=perf_counter();pagination=self._pagination(observations,probable);timed("pagination_inference",pagination_started)
        if probable and probable.graphql_operation and pagination.pagination_type=="UNKNOWN":warnings.append("GRAPHQL_PAGINATION_UNKNOWN")
        explicitly_complete=bool(probable and ((probable.observed_total is not None and probable.observed_total==probable.observed_list_length) or probable.observed_has_more is False))
        if pagination_override and not explicitly_complete:pagination.pagination_type=pagination_override
        if probable and pagination.pagination_type=="UNKNOWN" and not any("total equals" in x or "hasMore=false" in x for x in probable.evidence):
            # Visible count parity or absence of controls is recorded as weaker evidence by discovery.
            if dom.get("job_card_count")==probable.observed_list_length:probable.evidence.append("DOM visible job count equals API list length")
            pagination_inputs=any(x in str(probable.safe_request_values).lower() for x in ("page","offset","cursor","limit","size"))
            bounded=(probable.observed_list_length or 0)>=20 and probable.detail_url_coverage==1.0 and probable.observed_unique_ids==min(probable.observed_list_length or 0,20)
            if bounded and not pagination_inputs and pagination_override is None:
                probable.evidence.append("bounded unpaginated response has unique IDs and complete detail URL coverage")
        # A credible source captured before the shared deadline remains a
        # discovery success even if optional DOM/detail observation consumes
        # the rest of that budget afterwards.
        status="FAILED" if terminal_failure else ("DISCOVERED" if (probable or (runtime_source is not None and runtime_source.records)) else ("TIMEOUT" if _expired() else ("PARTIAL" if lists or dom.get("status") in ("DOM_LIST_DETECTED","DOM_COMPLEX_DETECTED") else "UNSUPPORTED")))
        if status in ("NOT_FOUND","UNSUPPORTED"):warnings.append("NO_DYNAMIC_JOB_SOURCE")
        fields={str(x).lower() for x in (probable.response_shape.get("sample_field_names",[]) if probable else [])}
        job_fields={"job_id","jobid","jobtitle","positionid","positionname","requisitionid","department","location","locations","requirements","responsibilities","applyurl","detailurl"}
        probable_is_job=bool(fields & job_fields)
        if probable and probable_is_job:page_type="JOB_LIST"
        elif detail_dom.get("status") in ("DETAIL_DOM","DETAIL_HTTP_HTML"):page_type="JOB_DETAIL"
        elif any(x.source_type in ("IFRAME","EMBEDDED_WIDGET") for x in inventory):page_type="ATS_EMBED"
        elif entries:page_type="RECRUITMENT_PORTAL"
        elif any(x in text for x in ("校园招聘","社会招聘","招聘职位","加入我们","人才招聘")):page_type="CAMPAIGN_PAGE"
        else:page_type="UNKNOWN"
        if probable and not probable_is_job:warnings.append("RECRUITMENT_ANNOUNCEMENT_SOURCE")
        classification="UNKNOWN_ATS" if probable and probable_is_job else ("RECRUITMENT_PORTAL" if entries else ("NON_JOB_DESTINATION" if not inventory else "RECRUITMENT_PORTAL"))
        profile=profile_from_discovery(DiscoveryResult(source_url=url,status=status,probable_list_api=probable,detected_pagination=pagination,detected_scope=self._scope(url,observations,text if 'text' in locals() else ""),candidate_list_apis=lists[:10])) if probable and probable_is_job else None
        finalization_started=perf_counter();result=DiscoveryResult(source_url=url,status=status,candidate_list_apis=lists[:10],candidate_detail_apis=details[:10],probable_list_api=probable,rejected_candidates=rejected[:20],
            detected_pagination=pagination,detected_scope=self._scope(url,observations,text if 'text' in locals() else ""),network_summary=summary,dom_fallback=dom,detail_dom=detail_dom,company=company,site_company_evidence=(site_identity.evidence if site_identity else []),tool_version=__version__,warnings=warnings,
            source_inventory=inventory,visible_total_evidence=[VisibleTotalEvidence(**x) for x in total_evidence],visible_total_conflict=total_conflict,failure_classification=terminal_failure,
            list_containers=[container] if 'container' in locals() and container else [],page_type=page_type,recruitment_entries=entries,ats_classification=classification,ats_profile=profile,runtime_source=runtime_source,
            internal_navigation_trace=internal_trace,
            started_at=started,finished_at=datetime.now(),elapsed_seconds=perf_counter()-clock)
        timed("build_final_discovery_result",finalization_started)
        if _expired():
            if "SOURCE_DISCOVERY_TIMEOUT" not in result.warnings:result.warnings.append("SOURCE_DISCOVERY_TIMEOUT")
            result.failure_classification=result.failure_classification or "SOURCE_DISCOVERY_TIMEOUT"
        if terminal_mode and terminal_trace is not None:
            terminal_trace.resolved_url=page.url if 'page' in locals() else url
            terminal_trace.scope=str(result.detected_scope.get("recruitment_type", "UNKNOWN")).upper()
            terminal_trace.activation_finished_at=datetime.now()
            terminal_trace.elapsed=perf_counter()-clock
            terminal_trace.terminal_status=result.status
            terminal_trace.terminal_actions_attempted=terminal_actions
            terminal_trace.network=terminal_network
            terminal_trace.dom_evidence=terminal_dom
            terminal_trace.serialized_state=terminal_states
            terminal_trace.frames=terminal_frames
            lifecycle=[]
            for candidate in result.candidate_list_apis:
                lifecycle.append({"url":candidate.url,"method":candidate.method,"state":"PROMOTED" if not candidate.rejection_reasons else "REJECTED","score":candidate.score,"confidence":candidate.confidence,"record_count":candidate.observed_list_length,"job_density":candidate.job_entity_density,"homogeneity":candidate.homogeneity_score,"replayability":candidate.replayable,"rejection_reasons":candidate.rejection_reasons})
            for candidate in result.rejected_candidates:
                lifecycle.append({"url":candidate.url,"method":candidate.method,"state":"REJECTED","score":candidate.score,"rejection_reasons":candidate.reasons})
            terminal_trace.candidate_lifecycle=lifecycle
            terminal_trace.empty_terminal_diagnostic={"terminal_actions_executed":bool(terminal_actions),"route_changed":any(item.get("route_changed") for item in terminal_actions),"new_xhr_fetch":any(item.get("phase","").endswith("POST") for item in terminal_network),"repeated_job_entities":any(item.get("classification")=="CANDIDATE" and (item.get("array_length") or 0)>=2 for item in terminal_network),"dom_job_cards":any(item.get("job_card_count",0)>0 for item in terminal_dom),"serialized_job_state":bool(terminal_states),"known_ats_redirect":False,"candidates_rejected":bool(lifecycle and not result.probable_list_api),"reason":result.warnings[-1] if result.warnings else None}
            result.terminal_trace=terminal_trace
        write_timing()
        return result
