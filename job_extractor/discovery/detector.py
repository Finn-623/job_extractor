from __future__ import annotations
import json,re
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any
from urllib.parse import parse_qsl,urljoin,urlsplit
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from job_extractor.browser import BrowserRuntime,BrowserRuntimeError
from job_extractor.discovery.models import ApiCandidate,CandidateSource,DiscoveryResult,ListContainer,NetworkSummary,PaginationDetection,RejectedCandidate,VisibleTotalEvidence,ProvenanceRecord,TerminalActivationTrace,RecruitmentAction
from job_extractor.discovery.network_analyzer import request_shape,response_shape,safe_url,sanitized_values,find_field,get_path,list_observation,safe_business_data
from job_extractor.discovery.pagination_semantics import infer_pagination_from_schema
from job_extractor.discovery.scorer import confidence,reliable_list_candidate,score_detail,score_list
from job_extractor.discovery.dom_semantics import extract_company,extract_detail_dom,filter_detail_links,first_real_url,related_detail_hosts,repeated_job_cards,repeated_job_links,route_changed,semantic_html_detail
from job_extractor.discovery.dynamic import graphql_shape,is_pagination_control,navigation_trust,safe_graphql_body,serialized_states,visible_total_evidence,wait_for_dynamic_jd,wait_for_hydration,wait_for_readiness_consensus
from job_extractor.discovery.sources import embedded_sources,recruitment_entries,select_terminal_actions,spa_action_inventory,classify_request,rank_spa_action
from job_extractor.discovery.ats import profile_from_discovery
from job_extractor.discovery.containers import bind_total_candidates,list_container_evidence
from job_extractor import __version__

@dataclass
class _Observation:
    url:str; method:str; body:dict[str,Any]; query:dict[str,Any]; payload:Any; phase:str; replayable:bool=True; source_index:int|None=None; request_content_type:str|None=None
    origin_url:str|None=None; page_url:str|None=None; trigger_action_id:str|None=None; trigger_action_text:str|None=None; trigger_action_type:str|None=None
    provenance_trust:str="TRUSTED"; provenance_rejection:str|None=None

class GenericApiDetector:
    threshold=10
    def __init__(self,browser_factory=BrowserRuntime,timeout_ms:int=15000,source_budget_seconds:int=50):
        self.browser_factory=browser_factory; self.timeout_ms=timeout_ms; self.source_budget_seconds=source_budget_seconds
    _JOB_PAGE_NODE_SELECTOR='a,button,[role="button"],[data-route],li'

    @classmethod
    def _job_page_search_trigger(cls,page)->list[str]:
        """Perform at most two safe job/search-semantic actions on the supplied position page."""
        script="""()=>Array.from(document.querySelectorAll('a,button,[role="button"],[data-route],li')).map((n,i)=>({i,text:(n.innerText||n.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' '),href:(n.getAttribute&&(n.getAttribute('href')||n.getAttribute('data-route')))||''}))"""
        try:
            nodes=page.evaluate(script)
        except Exception:
            return []
        if not isinstance(nodes,list):
            return []
        positive=("全部职位","职位列表","在招职位","查看职位","搜索职位","职位搜索","校园招聘","社会招聘","招聘职位","职位机会","all jobs","view jobs","search jobs","职位")
        negative=("登录","注册","隐私","筛选","filter","下一页","上一页","客服","投递","首页")
        ranked=[]
        for node in nodes:
            if not isinstance(node,dict):
                continue
            text=str(node.get("text") or "").strip()
            value=(text+" "+str(node.get("href") or "")).lower()
            if not text or len(text)>40 or any(x.lower() in value for x in negative):
                continue
            score=sum(4 for term in positive if term.lower() in value)
            if score>0:
                ranked.append((score,node))
        ranked.sort(key=lambda item:-item[0])
        clicked=[]
        for _score,node in ranked[:2]:
            try:
                page.locator(cls._JOB_PAGE_NODE_SELECTOR).nth(int(node["i"])).click(timeout=3000)
                page.wait_for_timeout(2000)
                clicked.append(str(node.get("text"))[:40])
            except Exception:
                pass
        return clicked

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
        hint={};company_names=set()
        url_field=None;url_coverage=0.0;unique_ids=None
        if sample and isinstance(sample[0],dict):
            item=sample[0]
            for key in ("id","job_id","jobId","jobPostId","positionId","requisitionId","title","name","jobTitle","positionName","company_name",*tuple(("url","absolute_url","job_url","jobUrl","detail_url","detailUrl","apply_url","applyUrl","path","slug"))):
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
        return ApiCandidate(url=clean,method=observation.method,score=score,confidence=confidence(score),
            request_body_shape=request_shape(observation.body),query_params=query,
            safe_request_values=safe_values,request_content_type=observation.request_content_type,
            response_shape=shape,evidence=evidence,sample_job_hint=safe_business_data(hint),observed_list_length=length,
            observed_total=total if isinstance(total,int) else None,observed_has_more=has_more if isinstance(has_more,bool) else None,
            observed_unique_ids=unique_ids,detail_url_field=url_field,detail_url_coverage=url_coverage,
            homogeneity_score=shape.get("homogeneity_score",0.0),job_entity_density=shape.get("job_entity_density",0.0),
            rejection_reasons=shape.get("rejection_reasons",[]),observed_company_count=len(company_names),list_item_path=shape.get("list_item_path"),replayable=replayable,graphql_operation=shape.get("graphql_operation"),graphql_page_info_path=shape.get("graphql_page_info_path"),source_type="SERIALIZED_STATE" if observation.method=="STATE" else ("GRAPHQL" if gql else "NETWORK_JSON"),source_index=observation.source_index,provenance=provenance,observed_phase="POST_ROUTE_NETWORK" if observation.phase=="POST_ROUTE_NETWORK" else observation.phase)
    @staticmethod
    def _rank(observations:list[_Observation],detail:bool=False)->list[ApiCandidate]:
        grouped={}; counts={}
        for o in observations:
            candidate=GenericApiDetector._candidate(o,detail); key=(candidate.url,candidate.method,candidate.source_index)
            counts[key]=counts.get(key,0)+1
            if key not in grouped or candidate.score>grouped[key].score:grouped[key]=candidate
        for key,value in grouped.items():value.sample_count=counts[key]
        return sorted((x for x in grouped.values() if x.score>0 and not x.rejection_reasons),key=lambda x:(-x.score,x.url,x.method))
    @classmethod
    def _reliable_list_source(cls,candidate:ApiCandidate|None)->bool:
        return reliable_list_candidate(candidate)
    def discover(self,url:str,budget=None,terminal_mode:bool=False,upstream_entry_action:RecruitmentAction|None=None)->DiscoveryResult:
        started=datetime.now(); clock=perf_counter(); observations=[]; phase=["TERMINAL_INITIAL" if terminal_mode else "INITIAL"]; summary=NetworkSummary(); dom={};detail_dom={};warnings=[];company=None;pagination_override=None;inventory=[];total_evidence=[];total_conflict=False;entries=[]
        terminal_trace=TerminalActivationTrace(terminal_url=url,scope="UNKNOWN",activation_started_at=started,upstream_entry_action=upstream_entry_action) if terminal_mode else None
        terminal_network=[];terminal_dom=[];terminal_actions=[];terminal_states=[];terminal_frames=[];runtime_source=None
        def _expired():return (perf_counter()-clock)>self.source_budget_seconds
        try:
            with self.browser_factory(timeout_ms=self.timeout_ms) as runtime:
                page=runtime.page
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
                    observations.append(_Observation(request.url,request.method,self._body(request),query,payload,phase[0],request_content_type=request.headers.get("content-type")))
                page.on("response",observe)
                try:
                    page.goto(url,wait_until="domcontentloaded",timeout=self.timeout_ms)
                except PlaywrightTimeoutError:
                    warnings.append("NAVIGATION_TIMEOUT_CONTINUING")
                phase[0]="TERMINAL_HYDRATION" if terminal_mode else "HYDRATION";hydration=wait_for_hydration(page,lambda:len(observations))
                if not hydration["stabilized"]:warnings.append("HYDRATION_TIMEOUT")
                if not observations and not _expired():
                    wait_for_readiness_consensus(page,lambda:len(observations),timeout_ms=min(self.timeout_ms,15000))
                # Give delayed public XHR sources one bounded stabilization window.
                if observations and not _expired():
                    page.wait_for_timeout(500)
                    wait_for_hydration(page,lambda:len(observations),timeout_ms=1500,interval_ms=250)
                states=list(serialized_states(page)) if not _expired() else []
                for state_index,state in enumerate(states):
                    observations.append(_Observation(url,"STATE",{}, {},state,"HYDRATION",False,state_index))
                    if terminal_mode:
                        terminal_states.append({"source":"script[type=application/json]/__NEXT_DATA__","json_paths":list(state)[:50] if isinstance(state,dict) else [],"array_lengths":{},"sample_field_names":list(state)[:50] if isinstance(state,dict) else [],"job_likeness":0,"rejection_reason":"LOW_JOB_ENTITY_DENSITY"})
                if terminal_mode:
                    for frame in page.frames:
                        terminal_frames.append({"frame_url":frame.url,"title":"","recruitment_semantics":bool(re.search(r"(?i)(job|career|recruit|招聘|职位)",frame.url)),"job_like_requests":False})
                inventory.extend(embedded_sources(page,url))
                entries=recruitment_entries(page,url)
                company=extract_company(page)
                text=page.locator("body").inner_text(timeout=3000)[:20000]
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
                if not initial_probable and not _expired():
                    triggered=self._job_page_search_trigger(page)
                    if triggered:
                        page.wait_for_timeout(2000)
                        initial_rank=self._rank(observations);initial_probable=initial_rank[0] if initial_rank and initial_rank[0].score>=self.threshold else None
                internal_trace=[]
                if not initial_probable and not _expired():
                    from job_extractor.discovery.internal_navigation import run_internal_job_navigation
                    def _rank_now():
                        ranked=self._rank([o for o in observations if o.phase!="DETAIL"])
                        reliable=next((candidate for candidate in ranked if self._reliable_list_source(candidate)),None)
                        return (reliable,reliable.score) if reliable else (None,0)
                    initial_probable,internal_trace=run_internal_job_navigation(page,url,observations,rank_fn=_rank_now,deadline_check=_expired)
                links=page.locator('a[href]'); hrefs=[]
                for i in range(min(links.count(),1000)):
                    href=links.nth(i).get_attribute("href") or ""; label=(links.nth(i).inner_text() or "").strip()
                    if href:hrefs.append((href,label))
                cards=repeated_job_cards(page,url);structural_links=[x["detail_link"] for x in cards if x.get("detail_link")]
                all_accepted,rejected_links=filter_detail_links(hrefs,url,structural_links)
                observed_links=filter_detail_links([(x,"") for x in structural_links],url,structural_links)[0] if structural_links else all_accepted
                observed_links=list(dict.fromkeys(observed_links))
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
                dom={"status":dom_status,"job_card_count":len(cards) or len(observed_links) or complex_count,"visible_result_count":result_count,"possible_title_selector":"a[href]" if observed_links else None,"possible_detail_links":observed_links[:500],"complex_job_nodes":complex_count,"rejected_link_count":len(rejected_links),"trusted_detail_hosts":trusted_hosts,"serialized_state_count":sum(o.method=="STATE" for o in observations),"job_cards":cards[:500],"originating_titles":card_titles,"originating_ids":card_ids,"card_link_parity":"CARD_LINK_PARITY" if len(cards)==len(observed_links) and cards else "MISSING_CARD_LINKS" if len(cards)>len(observed_links) else "EXTRA_NONCARD_LINKS","extra_noncard_links":extra_noncard[:100]}
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
        except BrowserRuntimeError as exc:
            return DiscoveryResult(source_url=url,status="BLOCKED",network_summary=summary,warnings=[str(exc)],started_at=started,finished_at=datetime.now(),elapsed_seconds=perf_counter()-clock)
        except Exception as exc:
            warnings.append(f"DISCOVERY_PAGE_ERROR {type(exc).__name__}")
        list_observations=[o for o in observations if o.phase!="DETAIL"]
        detail_observations=[o for o in observations if o.phase=="DETAIL"]
        lists=self._rank(list_observations); details=[x for x in self._rank(detail_observations,True) if x.score>=self.threshold]
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
        probable=lists[0] if lists and lists[0].score>=self.threshold else None
        if probable and probable.observed_company_count>1:company=None
        elif not company and probable:
            company=probable.sample_job_hint.get("company_name")
            if not company and isinstance(probable.sample_job_hint.get("company"),dict):company=probable.sample_job_hint["company"].get("name")
        summary.job_api_candidates=sum(x.score>=self.threshold for x in lists)
        pagination=self._pagination(observations,probable)
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
        status="DISCOVERED" if probable else ("PARTIAL" if lists or dom.get("status") in ("DOM_LIST_DETECTED","DOM_COMPLEX_DETECTED") else "NOT_FOUND")
        if status=="NOT_FOUND":warnings.append("NO_DYNAMIC_JOB_SOURCE")
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
        result=DiscoveryResult(source_url=url,status=status,candidate_list_apis=lists[:10],candidate_detail_apis=details[:10],probable_list_api=probable,rejected_candidates=rejected[:20],
            detected_pagination=pagination,detected_scope=self._scope(url,observations,text if 'text' in locals() else ""),network_summary=summary,dom_fallback=dom,detail_dom=detail_dom,company=company,tool_version=__version__,warnings=warnings,
            source_inventory=inventory,visible_total_evidence=[VisibleTotalEvidence(**x) for x in total_evidence],visible_total_conflict=total_conflict,
            list_containers=[container] if 'container' in locals() and container else [],page_type=page_type,recruitment_entries=entries,ats_classification=classification,ats_profile=profile,runtime_source=runtime_source,
            internal_navigation_trace=internal_trace,
            started_at=started,finished_at=datetime.now(),elapsed_seconds=perf_counter()-clock)
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
        return result
