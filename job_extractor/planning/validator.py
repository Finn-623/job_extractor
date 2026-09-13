from __future__ import annotations
from urllib.parse import urlsplit
from job_extractor.discovery.network_analyzer import sensitive
from job_extractor.discovery.network_analyzer import get_path
from job_extractor.planning.models import CollectionPlan,PlanValidation
from job_extractor.planning.execution_contract import missing_fields

def _secret(value,path=""):
    if isinstance(value,dict):
        return any(sensitive(str(k)) or _secret(v,path+"/"+str(k)) for k,v in value.items())
    if isinstance(value,list):return any(_secret(v,path) for v in value)
    return False
class CollectionPlanValidator:
    def validate(self,plan:CollectionPlan)->PlanValidation:
        errors=[]
        if not plan.executable:errors.append("PLAN_NOT_EXECUTABLE")
        if plan.total_conflict and plan.pagination_type not in ("LOAD_MORE","INFINITE_SCROLL","PAGE","OFFSET","CURSOR","GRAPHQL_PAGE","GRAPHQL_OFFSET","GRAPHQL_CURSOR"):errors.append("TOTAL_CONFLICT_UNRESOLVED")
        if _secret(plan.model_dump()):errors.append("PLAN_CONTAINS_SENSITIVE_FIELD")
        if plan.mode in ("HTTP_API","BROWSER_API","SERIALIZED_STATE"):
            if plan.mode!="SERIALIZED_STATE" and plan.list_method not in ("GET","POST"):errors.append("METHOD_UNKNOWN")
            if not plan.list_endpoint or plan.list_endpoint not in plan.observed_endpoints:errors.append("ENDPOINT_NOT_OBSERVED")
            if not plan.list_path:errors.append("LIST_PATH_MISSING")
            if not plan.job_id_field:errors.append("JOB_ID_FIELD_MISSING")
            if not plan.job_title_field:errors.append("JOB_TITLE_FIELD_MISSING")
            if plan.pagination_type=="PAGE" and not plan.page_param:errors.append("PAGE_PARAM_MISSING")
            if plan.pagination_type=="PAGE" and plan.page_param not in plan.initial_values and plan.page_param not in plan.query_values:errors.append("PAGE_INITIAL_VALUE_MISSING")
            if plan.pagination_type=="OFFSET" and (not plan.offset_param or not plan.page_size_param):errors.append("OFFSET_PARAMS_MISSING")
            if plan.pagination_type=="OFFSET" and (plan.offset_param not in plan.initial_values and plan.offset_param not in plan.query_values or plan.page_size_param not in plan.initial_values and plan.page_size_param not in plan.query_values):errors.append("OFFSET_INITIAL_VALUES_MISSING")
            if plan.pagination_type=="CURSOR" and not plan.cursor_param:errors.append("CURSOR_PARAM_MISSING")
            if plan.pagination_type in ("PAGE","OFFSET") and not (plan.total_field or plan.has_more_field):errors.append("TERMINATION_STRATEGY_MISSING")
            if plan.pagination_type in ("GRAPHQL_PAGE","GRAPHQL_OFFSET","GRAPHQL_CURSOR"):
                if not plan.page_param or get_path(plan.initial_values,plan.page_param) is None:errors.append("GRAPHQL_PAGINATION_INITIAL_VALUE_MISSING")
            if plan.pagination_type=="GRAPHQL_CURSOR" and (not plan.next_cursor_field or not plan.has_more_field):errors.append("GRAPHQL_CURSOR_PATHS_MISSING")
        elif plan.mode=="BROWSER_RUNTIME_DATA":
            source=plan.runtime_source or {}
            if not source:errors.append("RUNTIME_SOURCE_MISSING")
            elif int(source.get("record_count") or 0)<2:errors.append("RUNTIME_SOURCE_INSUFFICIENT")
            elif not source.get("job_id_field") or not source.get("job_title_field"):errors.append("RUNTIME_SOURCE_FIELDS_MISSING")
            elif plan.executable:
                total=source.get("total");count=int(source.get("record_count") or 0)
                if isinstance(total,int) and total>count:
                    if source.get("pagination_model")!="OFFSET":errors.append("RUNTIME_PAGINATION_MODEL_MISSING")
                    if not source.get("total"):errors.append("RUNTIME_TOTAL_MISSING")
                    if not source.get("limit"):errors.append("RUNTIME_LIMIT_MISSING")
                    if not source.get("offset_field") or not source.get("limit_field"):errors.append("RUNTIME_PAGINATION_FIELDS_MISSING")
                    if source.get("trigger_mode") not in ("OFFSET_PAGE","OFFSET_BUTTON"):errors.append("RUNTIME_TRIGGER_UNSAFE")
                    if not source.get("pagination_validated"):errors.append("RUNTIME_PAGINATION_NOT_VALIDATED")
        elif plan.mode=="DOM":
            if not plan.allowed_detail_urls:errors.append("DOM_LINKS_MISSING")
            source_host=urlsplit(plan.source_url).hostname
            trusted=set(plan.trusted_detail_hosts)
            if any(urlsplit(x).scheme!="https" or (urlsplit(x).hostname!=source_host and urlsplit(x).hostname not in trusted) for x in plan.allowed_detail_urls):errors.append("DOM_LINK_OUTSIDE_TRUST_MODEL")
        return PlanValidation(valid=not errors,errors=errors)
