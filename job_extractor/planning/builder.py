from __future__ import annotations
import re
from typing import Any
from job_extractor.discovery.models import ApiCandidate,DiscoveryResult
from job_extractor.planning.execution_contract import missing_fields
from job_extractor.planning.models import CollectionPlan
from job_extractor.planning.validator import CollectionPlanValidator
from job_extractor.field_semantics import infer_field

IDS=("id","job_id","jobId","jobPostId","postingId","positionId","requisitionId","requisition_id","internal_job_id")
TITLES=("title","name","jobTitle","positionName","projectPositionName","job_title","position_name")
# STEP 51: extended with the generic JD vocabulary from field_semantics so a
# list response carrying any recognized JD-style field is treated as
# LIST_SUFFICIENT (no per-job detail fetch), and split halves (duty /
# requirement style fields) also count. Kept superset of the original set.
JD={x.lower() for x in (
    "description","overview","content","jobdescription","job_description",
    "responsibilities","requirements","requirement","qualifications",
    "projectpositiondto",
    "jobdesc","positiondescription","position_description","jd_content",
    "jdcontent","jd","summary","workcontent","work_content","jobbody",
    "job_body","detaildescription","detail_description","postcontent",
    "post_content","richtext","rich_text","desc","jobresponsibility",
    "job_responsibility","responsibility","jobresponsibilities","duty","duties",
    "jobduty","job_duty","postduties","post_duties","workduty","work_duties",
    "jobrequirement","job_requirement","jobrequirements","requirementsdesc",
    "requirements_description","abilityrequirement","ability_requirement",
    "competency","competencies",
)}
def first(fields:list[str],names)->str|None:
    lookup={x.lower():x for x in fields}
    for name in names:
        if name.lower() in lookup:return lookup[name.lower()]
    return None

def _same_scope_value(a:Any,b:Any)->bool:
    """Shape-normalized scope-value comparison (separator style and
    string-vs-number form only; a real mismatch still counts as one)."""
    if a is None or b is None:return False
    if str(a)==str(b):return True
    norm=lambda v:re.sub(r"[-_]","",str(v)).lower()
    try:return norm(a)==norm(b) or float(a)==float(b)
    except (TypeError,ValueError):return norm(a)==norm(b)

def _trusted_scope_body(body:dict[str,Any],detected_scope:dict[str,Any])->dict[str,Any]:
    """Cross-check runtime detail-contract scope values against the page's
    detected scope (STEP 54E). The runtime state snapshot captured at
    observation time can be stale or point at a different site config than
    the page actually serves; the detected scope is derived from the page URL
    and organic list traffic and is therefore the trusted source. Any scope
    key present in both is bound to the detected value. Key matching is
    shape-normalized (case + ``-``/``_`` separators); site-agnostic by
    construction — no provider or host vocabulary."""
    detected={re.sub(r"[-_]","",str(k)).lower():v for k,v in (detected_scope or {}).items() if v not in (None,"")}
    resolved=dict(body)
    for key,value in body.items():
        ref=detected.get(re.sub(r"[-_]","",str(key)).lower())
        if ref is None or _same_scope_value(value,ref):continue
        resolved[key]=str(ref)
    return resolved

class CollectionPlanBuilder:
    def _api_plan(self,result:DiscoveryResult,candidate:ApiCandidate)->CollectionPlan:
        fields=candidate.response_shape.get("sample_field_names") or []
        # Keep plan field propagation aligned with discovery's generic field
        # semantics.  Exact legacy names retain priority, then structural
        # roles cover public variants such as postId/postName/jobAdName.
        jid=first(fields,IDS) or infer_field(fields,"id")
        title=first(fields,TITLES) or infer_field(fields,"title")
        structurally_high=candidate.confidence=="HIGH" and bool(candidate.response_shape.get("candidate_list_path")) and bool(jid and title) and not candidate.rejection_reasons
        medium=candidate.confidence=="MEDIUM" and not candidate.rejection_reasons
        sensitive_request=any(v=="[REDACTED]" for v in list(candidate.query_params.values())+list(candidate.request_body_shape.values()))
        browser_required=bool(candidate.graphql_operation) and candidate.replayable
        pagination=result.detected_pagination.pagination_type if candidate.url==getattr(result.probable_list_api,"url",None) else "UNKNOWN"
        single_evidence=(candidate.observed_total is not None and candidate.observed_total==candidate.observed_list_length) or candidate.observed_has_more is False or any("DOM visible job count equals" in x or "bounded unpaginated response" in x for x in candidate.evidence)
        if pagination=="UNKNOWN" and single_evidence:pagination="SINGLE_RESPONSE"
        detail_status=result.detail_dom.get("status")
        detail_dom=result.detail_dom if detail_status in ("DETAIL_DOM","DETAIL_HTTP_HTML") else {}
        detail="LIST_SUFFICIENT" if any(x.lower() in JD for x in fields) else ("DETAIL_FALLBACK" if result.candidate_detail_apis else (detail_status if detail_dom else "UNKNOWN"))
        detail_candidate=result.candidate_detail_apis[0] if result.candidate_detail_apis else None;detail_template=detail_candidate.url if detail_candidate else result.detail_dom.get("url_template")
        sample_id=str(candidate.sample_job_hint.get(jid) or "") if jid else ""
        if detail_template and sample_id and sample_id in detail_template:detail_template=detail_template.replace(sample_id,"{id}",1)
        elif detail_candidate:detail_template=None
        url_field=detail_dom.get("url_field") or candidate.detail_url_field or first(fields,("absolute_url","url","job_url","jobUrl","detail_url","detailUrl","apply_url","applyUrl","click_url","clickUrl"))
        detail_executable=detail not in ("DETAIL_DOM","DETAIL_HTTP_HTML") or bool(url_field or detail_template)
        is_state=candidate.source_type=="SERIALIZED_STATE"
        inconsistent_total=(isinstance(candidate.observed_total,int) and isinstance(candidate.observed_list_length,int) and candidate.observed_total<candidate.observed_list_length)
        executable=structurally_high and (is_state or candidate.replayable) and pagination!="UNKNOWN" and detail_executable and (not sensitive_request or candidate.method=="POST") and not inconsistent_total
        mode="SERIALIZED_STATE" if structurally_high and is_state else ("BROWSER_API" if structurally_high and (browser_required or sensitive_request) else ("HTTP_API" if structurally_high else "UNSUPPORTED"))
        warnings=(["BROWSER_API_NOT_REPLAYABLE"] if structurally_high and not candidate.replayable else (["API_PLAN_NOT_EXECUTABLE"] if structurally_high and not executable else [])) if structurally_high else ["PLAN_REQUIRES_REVIEW" if medium else "PLAN_UNSUPPORTED"]
        if inconsistent_total:
            warnings=list(warnings)+["INCONSISTENT_TOTAL_TERMINATION"]
        api_plan=CollectionPlan(source_url=result.source_url,company=result.company,mode=mode,executable=executable,
            review_required=medium or (structurally_high and not executable),list_endpoint=candidate.url,list_method=candidate.method,pagination_type=pagination,
            page_param=result.detected_pagination.page_param,offset_param=result.detected_pagination.page_param if pagination=="OFFSET" else None,page_size_param=result.detected_pagination.page_size_param,cursor_param=result.detected_pagination.cursor_param,
            next_cursor_field=result.detected_pagination.next_cursor_field,has_more_field=result.detected_pagination.has_more_field,
            initial_values=candidate.safe_request_values,query_values={k:v for k,v in candidate.query_params.items() if v!="[REDACTED]"} if candidate.method=="POST" else {},body_encoding="FORM" if "application/x-www-form-urlencoded" in (candidate.request_content_type or "").lower() else "JSON",observed_list_length=candidate.observed_list_length,total_field=None if inconsistent_total else candidate.response_shape.get("total_field"),list_path=candidate.response_shape.get("candidate_list_path"),list_item_path=candidate.list_item_path,job_id_field=jid,job_title_field=title,
            detail_mode=detail,detail_endpoint_template=detail_template,detail_method=detail_candidate.method if detail_candidate else None,detail_id_field=jid,detail_url_field=url_field,browser_trigger="AUTO_PAGINATION" if mode=="BROWSER_API" else None,
            detail_title_selector=detail_dom.get("title_selector"),detail_location_selector=detail_dom.get("location_selector"),detail_department_selector=detail_dom.get("department_selector"),detail_employment_type_selector=detail_dom.get("employment_type_selector"),detail_jd_selector=detail_dom.get("jd_selector"),
            scope=result.detected_scope,confidence=candidate.confidence,evidence=candidate.evidence,visible_total=result.dom_fallback.get("visible_result_count"),navigation_audit=result.detail_dom.get("navigation_audit") or [],source_index=candidate.source_index,originating_titles=result.dom_fallback.get("originating_titles") or {},originating_ids=result.dom_fallback.get("originating_ids") or {},
            total_conflict=inconsistent_total,warnings=warnings,
            observed_endpoints=[x.url for x in result.candidate_list_apis+result.candidate_detail_apis],ats_profile=result.ats_profile)
        gaps=missing_fields(api_plan)
        if executable and gaps:
            executable=False
            warnings=["API_PLAN_NOT_EXECUTABLE"]+[f"MISSING_{gap}" for gap in gaps]
        elif gaps and mode in ("HTTP_API","BROWSER_API","SERIALIZED_STATE"):
            warnings=list(warnings)+[f"MISSING_{gap}" for gap in gaps]
        update={"executable":executable,"warnings":warnings}
        if not executable and mode in ("HTTP_API","BROWSER_API","SERIALIZED_STATE"):update["review_required"]=True
        return api_plan.model_copy(update=update)

    def _dom_plan(self,result:DiscoveryResult)->CollectionPlan|None:
        links=result.dom_fallback.get("possible_detail_links") or []
        if result.dom_fallback.get("status")!="DOM_LIST_DETECTED" or not links:return None
        visible=result.dom_fallback.get("visible_result_count");dynamic=result.detected_pagination.pagination_type in ("LOAD_MORE","INFINITE_SCROLL")
        parity=result.dom_fallback.get("card_link_parity");independent_parity=parity=="CARD_LINK_PARITY" or parity is None
        complete=(not result.visible_total_conflict and ((visible is None and independent_parity) or visible==len(links) or (dynamic and bool(links))))
        pagination=result.detected_pagination.pagination_type if result.detected_pagination.pagination_type in ("LOAD_MORE","INFINITE_SCROLL") else "NONE"
        return CollectionPlan(source_url=result.source_url,company=result.company,mode="DOM",executable=complete,review_required=not complete,pagination_type=pagination,detail_mode="DETAIL_REQUIRED",
            job_link_selector=result.dom_fallback.get("possible_title_selector"),job_title_selector=result.dom_fallback.get("possible_title_selector"),allowed_detail_urls=links,
            trusted_detail_hosts=result.dom_fallback.get("trusted_detail_hosts") or [],detail_title_selector=result.detail_dom.get("title_selector"),detail_location_selector=result.detail_dom.get("location_selector"),
            detail_department_selector=result.detail_dom.get("department_selector"),detail_employment_type_selector=result.detail_dom.get("employment_type_selector"),detail_jd_selector=result.detail_dom.get("jd_selector"),originating_titles=result.dom_fallback.get("originating_titles") or {},originating_ids=result.dom_fallback.get("originating_ids") or {},
            scope=result.detected_scope,confidence="HIGH",visible_total=visible,navigation_audit=result.detail_dom.get("navigation_audit") or [],total_conflict=result.visible_total_conflict,list_container_id=result.list_containers[0].container_id if result.list_containers else None,
            evidence=[f"Discovery observed {len(links)} filtered job detail links"]+(["CARD_LINK_PARITY"] if independent_parity else [])+([f"DOM detail-link count equals bound total {visible}"] if complete and visible is not None else []),
            warnings=[] if complete else (["TOTAL_CONFLICT_UNRESOLVED"] if result.visible_total_conflict and not dynamic else [f"DOM_VISIBLE_TOTAL_MISMATCH visible={visible} links={len(links)}"]),ats_profile=result.ats_profile)

    def _runtime_plan(self,result:DiscoveryResult)->CollectionPlan|None:
        source=result.runtime_source
        if source is None or not source.records:return None
        executable=bool(source.executable)
        # STEP 54B: propagate the organic detail-request contract captured at
        # discovery time. Field names only — every scope/decoder value inside
        # the contract already came from observed runtime evidence. detail_mode
        # stays untouched so plan ranking is unchanged.
        contract=source.detail_contract if isinstance(source.detail_contract,dict) else {}
        update:dict[str,Any]={}
        if contract.get("endpoint") and contract.get("method"):
            update["detail_endpoint_template"]=str(contract["endpoint"])
            update["detail_method"]=str(contract["method"]).upper()
            update["detail_id_field"]=str(contract.get("id_field") or source.job_id_field or "id")
            if isinstance(contract.get("body_template"),dict) and contract["body_template"]:
                update["detail_body_template"]=_trusted_scope_body(contract["body_template"],result.detected_scope)
            if isinstance(contract.get("decoder"),dict) and contract["decoder"]:
                update["detail_decoder"]=dict(contract["decoder"])
            if contract.get("jd_field"):
                update["detail_jd_field"]=str(contract["jd_field"])
            if isinstance(contract.get("result_path"),list) and contract["result_path"]:
                update["detail_result_path"]=[str(p) for p in contract["result_path"]]
            elif isinstance(contract.get("decoder"),dict) and isinstance((contract["decoder"] or {}).get("payload_field"),str):
                # Contract recorded before result-path evidence capture: the
                # decoder spec's ``payload_field`` is the only evidence naming
                # where the job payload lives, so the business record resolves
                # under it. Wrong derivation fails closed downstream.
                update["detail_result_path"]=[contract["decoder"]["payload_field"]]
        return CollectionPlan(source_url=result.source_url,company=result.company,mode="BROWSER_RUNTIME_DATA",executable=executable,review_required=not executable,
            confidence=source.confidence,scope=result.detected_scope,evidence=source.evidence,runtime_source=source.model_dump(),
            list_path=source.source_path,job_id_field=source.job_id_field,job_title_field=source.job_title_field,
            warnings=[] if executable else ["PAGINATION_REQUIRED"],ats_profile=result.ats_profile,**update)

    def build(self,result:DiscoveryResult)->CollectionPlan:
        plans=[self._api_plan(result,c) for c in result.candidate_list_apis[:5]]
        runtime=self._runtime_plan(result)
        if runtime:plans.append(runtime)
        dom=self._dom_plan(result)
        if dom:plans.append(dom)
        if not plans:return CollectionPlan(source_url=result.source_url,company=result.company,mode="UNSUPPORTED",scope=result.detected_scope,warnings=["NO_EXECUTABLE_DISCOVERY_EVIDENCE"])
        validator=CollectionPlanValidator()
        def rank(plan:CollectionPlan):
            valid=validator.validate(plan).valid
            tier=4 if valid and plan.confidence=="HIGH" and plan.mode in ("HTTP_API","BROWSER_API","SERIALIZED_STATE","BROWSER_RUNTIME_DATA") else 3 if valid and plan.confidence=="HIGH" and plan.mode=="DOM" else 2 if plan.review_required else 1
            list_sufficient=plan.detail_mode=="LIST_SUFFICIENT" and plan.mode in ("HTTP_API","BROWSER_API","SERIALIZED_STATE","BROWSER_RUNTIME_DATA")
            return list_sufficient,tier,plan.confidence=="HIGH",plan.mode!="UNSUPPORTED"
        return max(plans,key=rank)
