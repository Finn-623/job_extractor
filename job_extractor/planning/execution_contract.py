from __future__ import annotations
from typing import Any

SUPPORTED_MODES=("HTTP_API","BROWSER_API","SERIALIZED_STATE","DOM","BROWSER_RUNTIME_DATA")

GAP_REASONS={
    "EXECUTION_MODE":"UNSUPPORTED_EXECUTION_MODE",
    "REQUEST_URL":"MISSING_REQUEST_URL",
    "REQUEST_METHOD":"MISSING_REQUEST_METHOD",
    "LIST_EXTRACTION":"MISSING_LIST_EXTRACTION",
    "JOB_ID_FIELD":"MISSING_JOB_ID_FIELD",
    "JOB_TITLE_FIELD":"MISSING_JOB_TITLE_FIELD",
    "PAGINATION_ADVANCE":"MISSING_PAGINATION_ADVANCE",
    "PAGINATION_INITIAL_VALUE":"MISSING_PAGINATION_INITIAL_VALUE",
    "TERMINATION_STRATEGY":"MISSING_TERMINATION_STRATEGY",
    "ALLOWED_DETAIL_URLS":"MISSING_ALLOWED_DETAIL_URLS",
    "RUNTIME_SOURCE":"MISSING_RUNTIME_SOURCE",
    "RUNTIME_TERMINATION_STRATEGY":"MISSING_RUNTIME_TERMINATION_STRATEGY",
}

class PlanContractError(ValueError):
    """Controlled pre-execution/defensive contract failure: no runtime crash, machine-readable."""
    def __init__(self,reason:str,execution_mode:str|None=None,missing_fields:list[str]|None=None):
        super().__init__(f"{reason} mode={execution_mode} missing={missing_fields or []}")
        self.reason=reason;self.execution_mode=execution_mode;self.missing_fields=list(missing_fields or [])

def missing_fields(plan:Any)->list[str]:
    """Semantic gap tokens for a CollectionPlan against the unified execution contract.

    Required fields are per execution_mode: only modes that issue real HTTP requests
    (HTTP_API/BROWSER_API) require request URL/method; SERIALIZED_STATE reads embedded
    state on the source page and owns its own request shape.
    """
    mode=getattr(plan,"mode",None)
    if mode not in SUPPORTED_MODES:return ["EXECUTION_MODE"]
    missing:list[str]=[]
    if mode=="BROWSER_RUNTIME_DATA":
        if not plan.runtime_source:missing.append("RUNTIME_SOURCE")
        else:
            source=plan.runtime_source
            paginated=source.get("pagination_model") in ("OFFSET", "PAGE") or bool(source.get("pagination"))
            terminal=source.get("terminal_page_signal") or source.get("has_more_field")
            if paginated and not (isinstance(source.get("total"),int) and source.get("total")>0) and not terminal:
                missing.append("RUNTIME_TERMINATION_STRATEGY")
        return missing
    if mode=="DOM":
        if not plan.allowed_detail_urls:missing.append("ALLOWED_DETAIL_URLS")
        return missing
    if not plan.list_endpoint:missing.append("REQUEST_URL")
    if mode in ("HTTP_API","BROWSER_API") and plan.list_method not in ("GET","POST"):missing.append("REQUEST_METHOD")
    if not plan.list_path:missing.append("LIST_EXTRACTION")
    if not plan.job_id_field:missing.append("JOB_ID_FIELD")
    if not plan.job_title_field:missing.append("JOB_TITLE_FIELD")
    kind=plan.pagination_type
    if mode in ("HTTP_API","BROWSER_API"):
        if kind=="PAGE" and not plan.page_param:missing.append("PAGINATION_ADVANCE")
        elif kind=="OFFSET" and (not plan.offset_param or not plan.page_size_param):missing.append("PAGINATION_ADVANCE")
        elif kind=="CURSOR" and not plan.cursor_param:missing.append("PAGINATION_ADVANCE")
        elif kind in ("GRAPHQL_PAGE","GRAPHQL_OFFSET") and not plan.page_param:missing.append("PAGINATION_ADVANCE")
        elif kind=="GRAPHQL_CURSOR" and not (plan.page_param and plan.next_cursor_field and plan.has_more_field):missing.append("PAGINATION_ADVANCE")
        elif kind=="UNKNOWN":missing.append("PAGINATION_ADVANCE")
        if kind in ("PAGE","OFFSET") and not (plan.total_field or plan.has_more_field):missing.append("TERMINATION_STRATEGY")
        if kind in ("PAGE","OFFSET","GRAPHQL_PAGE","GRAPHQL_OFFSET") and plan.page_param:
            values=plan.initial_values or {};query=plan.query_values or {}
            if plan.page_param not in values and plan.page_param not in query:missing.append("PAGINATION_INITIAL_VALUE")
    return missing

def transport_gaps(plan:Any)->list[str]:
    """Defensive gaps a transport collector checks itself (mode-agnostic: routing is the dispatcher's job)."""
    missing:list[str]=[]
    mode=getattr(plan,"mode",None)
    if mode in ("HTTP_API","BROWSER_API") and plan.list_method not in ("GET","POST"):missing.append("REQUEST_METHOD")
    if not plan.list_endpoint:missing.append("REQUEST_URL")
    if not plan.list_path:missing.append("LIST_EXTRACTION")
    if not plan.job_id_field:missing.append("JOB_ID_FIELD")
    if not plan.job_title_field:missing.append("JOB_TITLE_FIELD")
    return missing

def gap_reasons(plan:Any)->list[str]:
    return [GAP_REASONS.get(gap,f"MISSING_{gap}") for gap in missing_fields(plan)]

def can_dispatch(plan:Any)->bool:
    """Executable + Valid => Dispatchable: a plan may only be dispatched when no contract gap exists."""
    return not missing_fields(plan)

def dispatch_target(plan:Any)->str|None:
    mode=getattr(plan,"mode",None)
    return {"HTTP_API":"GenericHttpCollector","BROWSER_API":"GenericBrowserApiCollector",
            "SERIALIZED_STATE":"GenericSerializedStateCollector","DOM":"GenericDomCollector",
            "BROWSER_RUNTIME_DATA":"GenericRuntimeDataCollector"}.get(mode)
