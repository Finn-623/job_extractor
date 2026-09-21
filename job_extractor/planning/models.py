from __future__ import annotations
from typing import Any,Literal
from pydantic import BaseModel,Field,PrivateAttr
from job_extractor.discovery.models import ATSProfile

class CollectionPlan(BaseModel):
    source_url:str
    company:str|None=None
    mode:Literal["HTTP_API","BROWSER_API","SERIALIZED_STATE","BROWSER_RUNTIME_DATA","DOM","HTML","UNSUPPORTED"]
    browser_trigger: str|None = None
    executable:bool=False
    review_required:bool=False
    list_endpoint:str|None=None
    list_method:str|None=None
    pagination_type:Literal["NONE","SINGLE_RESPONSE","PAGE","OFFSET","CURSOR","GRAPHQL_PAGE","GRAPHQL_OFFSET","GRAPHQL_CURSOR","LOAD_MORE","INFINITE_SCROLL","UNKNOWN"]="UNKNOWN"
    page_param:str|None=None
    page_size_param:str|None=None
    offset_param:str|None=None
    cursor_param:str|None=None
    next_cursor_field:str|None=None
    has_more_field:str|None=None
    total_field:str|None=None
    initial_values:dict[str,Any]=Field(default_factory=dict)
    query_values:dict[str,Any]=Field(default_factory=dict)
    body_encoding:Literal["JSON","FORM"]="JSON"
    observed_list_length:int|None=None
    # Safe discovery evidence only.  This is the total returned by the
    # browser's native list request and contains no request context.
    observed_total:int|None=None
    list_path:str|None=None
    list_item_path:str|None=None
    job_id_field:str|None=None
    job_title_field:str|None=None
    detail_mode:Literal["LIST_SUFFICIENT","DETAIL_REQUIRED","DETAIL_FALLBACK","DETAIL_DOM","DETAIL_HTTP_HTML","UNKNOWN"]="UNKNOWN"
    detail_endpoint_template:str|None=None
    detail_method:str|None=None
    detail_id_field:str|None=None
    detail_path:str|None=None
    detail_url_field:str|None=None
    # STEP 54B: minimal generic runtime detail contract. The body template
    # carries literal values observed in the organic detail request (scope
    # values such as org/site ids come from that observed evidence, never
    # hardcoded) plus a "{id}" placeholder substituted per job. The decoder
    # is a generic response-transform contract (e.g. AES_CBC_ENVELOPE with
    # the iv sourced from runtime state evidence).
    detail_body_template:dict[str,Any]=Field(default_factory=dict)
    detail_decoder:dict[str,Any]=Field(default_factory=dict)
    detail_jd_field:str|None=None
    # STEP 54B: where the job record lives inside the decoded detail response,
    # derived from organic response evidence at discovery time (empty list =
    # the decoded payload itself is the job record; e.g. ["data"] = it is
    # nested inside a generic business-result wrapper). Execution-layer
    # concern only — the transport decoder never unwraps business payloads.
    detail_result_path:list[str]=Field(default_factory=list)
    detail_title_selector:str|None=None
    detail_location_selector:str|None=None
    detail_department_selector:str|None=None
    detail_employment_type_selector:str|None=None
    detail_jd_selector:str|None=None
    job_link_selector:str|None=None
    job_title_selector:str|None=None
    allowed_detail_urls:list[str]=Field(default_factory=list)
    trusted_detail_hosts:list[str]=Field(default_factory=list)
    visible_total:int|None=None
    navigation_audit:list[dict[str,Any]]=Field(default_factory=list)
    source_index:int|None=None
    originating_titles:dict[str,str]=Field(default_factory=dict)
    originating_ids:dict[str,str]=Field(default_factory=dict)
    total_conflict:bool=False
    list_container_id:str|None=None
    scope:dict[str,Any]=Field(default_factory=dict)
    confidence:Literal["LOW","MEDIUM","HIGH"]="LOW"
    evidence:list[str]=Field(default_factory=list)
    warnings:list[str]=Field(default_factory=list)
    observed_endpoints:list[str]=Field(default_factory=list)
    ats_profile:ATSProfile|None=None
    runtime_source:dict[str,Any]=Field(default_factory=dict)
    # Process-local replay context for redacted query values.  This must never
    # become part of a serialized plan or any collection/report artifact.
    _runtime_query_params:dict[str,Any]=PrivateAttr(default_factory=dict)

class PlanValidation(BaseModel):
    valid:bool
    errors:list[str]=Field(default_factory=list)
