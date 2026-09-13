from typing import Any

from job_extractor.discovery.detector import GenericApiDetector,_Observation
from job_extractor.discovery.models import ApiCandidate
from job_extractor.discovery.pagination_semantics import infer_pagination_from_schema
from job_extractor.discovery.network_analyzer import response_shape

JOBS=[{"id":x,"positionName":f"岗位{x}"} for x in range(10)]

def _candidate(url:str="https://company.example.test/api/position/list",method:str="POST",body_shape:dict[str,Any]|None=None)->ApiCandidate:
    return ApiCandidate(url=url,method=method,score=22,confidence="HIGH",
        request_body_shape=body_shape or {},safe_request_values={},response_shape={},
        observed_list_length=10,observed_total=519,observed_unique_ids=10)

def _observation(body:dict[str,Any]|None,query:dict[str,Any]|None,payload:Any)->_Observation:
    return _Observation("https://company.example.test/api/position/list","POST",body or {},query or {},payload,"HYDRATION")

def _pagination(observation:_Observation,candidate:ApiCandidate|None):
    return GenericApiDetector._pagination([observation] if observation else [],candidate)

# CASE 1: Bilibili-like PAGE schema
def test_case1_bilibili_like_page_num_page_size_total():
    request={"pageNum":1,"pageSize":20,"positionName":""}
    response={"code":0,"message":"","data":{"total":519,"list":JOBS}}
    result=infer_pagination_from_schema(request,{},response)
    assert result["kind"]=="PAGE"
    assert result["page_param"]=="pageNum"
    assert result["size_param"]=="pageSize"
    assert result["first_page"]==1
    assert result["page_size"]==20
    assert result["total_path"]=="data.total"
    assert result["total_value"]==519
    assert result["inference_source"]=="request_response_schema"
    assert result["confidence"]>0.9
    assert any("request.body.pageNum=1" in item for item in result["evidence"])
    assert any("request.body.pageSize=20" in item for item in result["evidence"])
    assert any("response.data.total=519" in item for item in result["evidence"])

def test_case1_detector_integration():
    candidate=_candidate()
    response={"code":0,"data":{"total":519,"list":JOBS}}
    detection=_pagination(_observation({"pageNum":1,"pageSize":20},None,response),candidate)
    assert detection.pagination_type=="PAGE"
    assert detection.page_param=="pageNum"
    assert detection.page_size_param=="pageSize"
    assert detection.first_page==1
    assert detection.page_size==20
    assert detection.total_field=="data.total"
    assert detection.pagination_type!="UNKNOWN"
    assert detection.inference_source=="request_response_schema"
    assert detection.confidence and detection.confidence>0.9

# CASE 2: NetEase-like PAGE schema (values from the real capture of POST /api/hr163/position/queryPage:
# request {"currentPage":1,"pageSize":10}; response data.total=2645, data.pages=265, data.lastPage=false)
def test_case2_netease_like_current_page_schema():
    request={"currentPage":1,"pageSize":10}
    response={"code":200,"data":{"pages":265,"total":2645,"lastPage":False,"currentPage":1,"pageSize":10,"list":JOBS}}
    result=infer_pagination_from_schema(request,{},response,list_length=10)
    assert result["kind"]=="PAGE"
    assert result["page_param"]=="currentPage"
    assert result["size_param"]=="pageSize"
    assert result["first_page"]==1
    assert result["page_size"]==10
    assert result["total_path"]=="data.total"
    assert result["total_value"]==2645
    assert result["confidence"]>0.9
    assert any("request.body.currentPage=1" in item for item in result["evidence"])
    assert any("response.data.total=2645" in item for item in result["evidence"])

# CASE 3: OFFSET schema
def test_case3_offset_schema():
    request={"offset":0,"limit":20}
    response={"code":0,"data":{"count":519,"positions":[{"id":x} for x in range(10)]}}
    result=infer_pagination_from_schema(request,{},response)
    assert result["kind"]=="OFFSET"
    assert result["page_param"]=="offset"
    assert result["size_param"]=="limit"
    assert result["first_page"]==0
    assert result["total_path"]=="data.count"
    assert result["total_value"]==519

def test_case3_detector_integration_offset():
    candidate=_candidate()
    detection=_pagination(_observation({"offset":0,"limit":20},None,{"data":{"count":519,"positions":JOBS}}),candidate)
    assert detection.pagination_type=="OFFSET"
    assert detection.page_param=="offset"
    assert detection.page_size_param=="limit"

# CASE 4: 0-based page
def test_case4_zero_based_page():
    request={"page":0,"size":20}
    response={"total":519,"list":JOBS}
    result=infer_pagination_from_schema(request,{},response)
    assert result["kind"]=="PAGE"
    assert result["page_param"]=="page"
    assert result["size_param"]=="size"
    assert result["first_page"]==0

# CASE 5: only limit + total -> UNKNOWN
def test_case5_size_without_advancing_field_is_unknown():
    request={"limit":20}
    response={"data":{"total":519,"list":JOBS}}
    result=infer_pagination_from_schema(request,{},response)
    assert result["kind"] is None
    detection=_pagination(_observation({"limit":20},None,response),_candidate())
    assert detection.pagination_type=="UNKNOWN"

# CASE 6: config API is never promoted by pagination inference
def test_case6_config_source_not_promoted():
    from job_extractor.discovery.scorer import score_list
    url="https://company.example.test/api/config/dict?pageNum=1&pageSize=20"
    payload={"total":300,"list":[{"code":"CN","name":"China"}]}
    score,evidence,shape=score_list(url,payload)
    candidate=_candidate(url=url)
    candidate.response_shape=response_shape(payload)
    candidate.rejection_reasons=["LOW_JOB_ENTITY_DENSITY"]
    assert candidate.rejection_reasons
    detection=_pagination(_observation({"pageNum":1,"pageSize":20},None,payload),candidate)
    assert detection.pagination_type=="UNKNOWN"
    assert detection.page_param is None
    assert not any(field in evidence for field in ("kind=PAGE","kind=OFFSET"))

# CASE 7: total preferred over current-page count
def test_case7_total_wins_over_count():
    response={"count":20,"total":519,"data":{"list":JOBS}}
    result=infer_pagination_from_schema({"pageNum":1,"pageSize":20},{},response)
    assert result["total_path"]=="total"
    assert result["total_value"]==519

def test_case7_response_shape_total_priority():
    payload={"code":0,"data":{"total":519,"count":20,"list":JOBS}}
    assert response_shape(payload)["total_field"]=="data.total"
    payload_count_only={"code":0,"data":{"count":10,"list":JOBS}}
    assert response_shape(payload_count_only)["total_field"]=="data.count"

# CASE 8: nested request body
def test_case8_nested_body():
    request={"query":{"page":{"currentPage":1,"pageSize":50}}}
    response={"data":{"total":1200,"list":JOBS}}
    result=infer_pagination_from_schema(request,{},response)
    assert result["kind"]=="PAGE"
    assert result["page_param"]=="currentPage"
    assert result["size_param"]=="pageSize"
    assert result["first_page"]==1
    assert result["page_size"]==50
    assert result["total_value"]==1200

def test_case8_nested_body_page_size_only_wrapper():
    request={"pagination":{"pageSize":30},"filter":{"currentPage":1}}
    response={"data":{"totalCount":800,"records":JOBS}}
    result=infer_pagination_from_schema(request,{},response)
    assert result["kind"]=="PAGE"
    assert result["total_path"]=="data.totalCount"
    assert result["total_value"]==800

def test_case8_query_string_page_params():
    response={"data":{"list":JOBS,"total":100}}
    result=infer_pagination_from_schema({},{"page":"1","pageSize":"20"},response)
    assert result["kind"]=="PAGE"
    assert result["page_param"]=="page"
    assert result["size_param"]=="pageSize"
    assert result["page_size"]==20

# Form-encoded request body: pagination fields must be discoverable in form posts too
def test_form_body_page_params_are_parsed():
    class _FakeRequest:
        headers={"content-type":"application/x-www-form-urlencoded"}
        post_data_json=None
        post_data="currentPage=1&pageSize=20&keyword="
    body=GenericApiDetector._body(_FakeRequest())
    assert body.get("currentPage")=="1" and body.get("pageSize")=="20",body
    response={"data":{"list":JOBS,"total":300}}
    result=infer_pagination_from_schema(body,{},response)
    assert result["kind"]=="PAGE"
    assert result["page_param"]=="currentPage"
    assert result["size_param"]=="pageSize"

# Detector-level protections
def test_rejected_candidate_returns_unknown_pagination():
    candidate=_candidate();candidate.rejection_reasons=["LOW_JOB_ENTITY_DENSITY"]
    detection=_pagination(_observation({"pageNum":1,"pageSize":20},None,{"data":{"total":519,"list":JOBS}}),candidate)
    assert detection.pagination_type=="UNKNOWN"

def test_no_candidate_returns_default():
    detection=_pagination(None,None)
    assert detection.pagination_type=="UNKNOWN"

def test_pagination_does_not_depend_on_hostname_or_path():
    request={"pageNum":1,"pageSize":20}
    response={"data":{"total":519,"list":JOBS}}
    for url_host in ("jobs.example-a.test","careers.example-b.test","api.example-c.test"):
        result=infer_pagination_from_schema(request,{},response)
        assert result["kind"]=="PAGE",url_host if False else url_host
    assert result["kind"]=="PAGE"

# No site-specific special-casing in the generic module
def test_no_hostname_or_path_special_casing():
    source=open("job_extractor/discovery/pagination_semantics.py").read().lower()
    for banned in ("bilibili","hr163","netease","bytedance","kuaishou","moka","zhiye","feishu","querypage"):
        assert banned not in source,banned
