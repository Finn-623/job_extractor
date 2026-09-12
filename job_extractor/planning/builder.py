from __future__ import annotations
from job_extractor.discovery.models import ApiCandidate,DiscoveryResult
from job_extractor.planning.models import CollectionPlan
from job_extractor.planning.validator import CollectionPlanValidator

IDS=("id","job_id","jobId","jobPostId","postingId","positionId","requisitionId","requisition_id","internal_job_id")
TITLES=("title","name","jobTitle","positionName","projectPositionName","job_title","position_name")
JD={x.lower() for x in ("description","overview","content","jobdescription","job_description","responsibilities","requirements","requirement","qualifications","projectPositionDto")}
def first(fields:list[str],names)->str|None:
    lookup={x.lower():x for x in fields}
    for name in names:
        if name.lower() in lookup:return lookup[name.lower()]
    return None

class CollectionPlanBuilder:
    def _api_plan(self,result:DiscoveryResult,candidate:ApiCandidate)->CollectionPlan:
        fields=candidate.response_shape.get("sample_field_names") or [];jid=first(fields,IDS);title=first(fields,TITLES)
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
        executable=structurally_high and (is_state or candidate.replayable) and pagination!="UNKNOWN" and detail_executable and (not sensitive_request or candidate.method=="POST")
        mode="SERIALIZED_STATE" if structurally_high and is_state else ("BROWSER_API" if structurally_high and (browser_required or sensitive_request) else ("HTTP_API" if structurally_high else "UNSUPPORTED"))
        return CollectionPlan(source_url=result.source_url,company=result.company,mode=mode,executable=executable,
            review_required=medium or (structurally_high and not executable),list_endpoint=candidate.url,list_method=candidate.method,pagination_type=pagination,
            page_param=result.detected_pagination.page_param,offset_param=result.detected_pagination.page_param if pagination=="OFFSET" else None,page_size_param=result.detected_pagination.page_size_param,cursor_param=result.detected_pagination.cursor_param,
            next_cursor_field=result.detected_pagination.next_cursor_field,has_more_field=result.detected_pagination.has_more_field,
            initial_values=candidate.safe_request_values,query_values={k:v for k,v in candidate.query_params.items() if v!="[REDACTED]"} if candidate.method=="POST" else {},body_encoding="FORM" if "application/x-www-form-urlencoded" in (candidate.request_content_type or "").lower() else "JSON",observed_list_length=candidate.observed_list_length,total_field=candidate.response_shape.get("total_field"),list_path=candidate.response_shape.get("candidate_list_path"),list_item_path=candidate.list_item_path,job_id_field=jid,job_title_field=title,
            detail_mode=detail,detail_endpoint_template=detail_template,detail_method=detail_candidate.method if detail_candidate else None,detail_id_field=jid,detail_url_field=url_field,browser_trigger="AUTO_PAGINATION" if mode=="BROWSER_API" else None,
            detail_title_selector=detail_dom.get("title_selector"),detail_location_selector=detail_dom.get("location_selector"),detail_department_selector=detail_dom.get("department_selector"),detail_employment_type_selector=detail_dom.get("employment_type_selector"),detail_jd_selector=detail_dom.get("jd_selector"),
            scope=result.detected_scope,confidence=candidate.confidence,evidence=candidate.evidence,visible_total=result.dom_fallback.get("visible_result_count"),navigation_audit=result.detail_dom.get("navigation_audit") or [],source_index=candidate.source_index,originating_titles=result.dom_fallback.get("originating_titles") or {},originating_ids=result.dom_fallback.get("originating_ids") or {},
            warnings=(["BROWSER_API_NOT_REPLAYABLE"] if structurally_high and not candidate.replayable else (["API_PLAN_NOT_EXECUTABLE"] if structurally_high and not executable else [])) if structurally_high else ["PLAN_REQUIRES_REVIEW" if medium else "PLAN_UNSUPPORTED"],
            observed_endpoints=[x.url for x in result.candidate_list_apis+result.candidate_detail_apis],ats_profile=result.ats_profile)

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
        return CollectionPlan(source_url=result.source_url,company=result.company,mode="BROWSER_RUNTIME_DATA",executable=executable,review_required=not executable,
            confidence=source.confidence,scope=result.detected_scope,evidence=source.evidence,runtime_source=source.model_dump(),
            list_path=source.source_path,job_id_field=source.job_id_field,job_title_field=source.job_title_field,
            warnings=[] if executable else ["PAGINATION_REQUIRED"],ats_profile=result.ats_profile)

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
            return tier,plan.confidence=="HIGH",plan.mode!="UNSUPPORTED"
        return max(plans,key=rank)
