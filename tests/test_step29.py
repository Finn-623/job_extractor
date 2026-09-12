from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.discovery.detector import GenericApiDetector,_Observation
from job_extractor.discovery.models import DiscoveryResult,PaginationDetection
from job_extractor.discovery.network_analyzer import sanitized_values
from job_extractor.planning import CollectionPlan,CollectionPlanBuilder,CollectionPlanValidator


class Response:
    def __init__(self,payload):self.payload=payload
    def raise_for_status(self):pass
    def json(self):return self.payload

class Client:
    def __init__(self,payload):self.payload=payload;self.calls=[]
    def request(self,method,url,**kwargs):self.calls.append((method,url,kwargs));return Response(self.payload)

def candidate(body=None,content_type="application/x-www-form-urlencoded"):
    body=body or {"from":"public","page":"1","pagesize":"10","aid":"68"}
    payload={"data":{"count":1,"list":[{"id":"246","name":"Engineer","addr":"Qingdao"}]}}
    return GenericApiDetector._candidate(_Observation("https://x.test/list","POST",body,{},payload,"HYDRATION",request_content_type=content_type))

def discovery(value):
    return DiscoveryResult(source_url="https://x.test/campus",status="DISCOVERED",candidate_list_apis=[value],probable_list_api=value,
        detected_pagination=PaginationDetection(pagination_type="PAGE",page_param="page",page_size_param="pagesize",total_field="data.count"))

def test_browser_observed_post_body_and_encoding_are_preserved_in_plan():
    plan=CollectionPlanBuilder().build(discovery(candidate()))
    assert plan.initial_values=={"from":"public","page":"1","pagesize":"10","aid":"68"}
    assert plan.body_encoding=="FORM"

def test_replay_request_matches_post_body_encoding_and_query():
    plan=CollectionPlanBuilder().build(discovery(candidate()));plan.query_values={"locale":"zh"}
    client=Client({"data":{"count":1,"list":[{"id":"246","name":"Engineer"}]}})
    GenericHttpCollector(plan,client).collect();kwargs=client.calls[0][2]
    assert kwargs["data"]==plan.initial_values and kwargs["params"]=={"locale":"zh"} and "json" not in kwargs

def test_nested_response_array_and_generic_fields_normalize():
    plan=CollectionPlanBuilder().build(discovery(candidate()))
    result=GenericHttpCollector(plan,Client({"data":{"count":1,"list":[{"id":"246","name":"Engineer"}]}})).collect()
    assert result.total_fetched==result.total_unique==1 and result.jobs[0].job_title=="Engineer"

def test_normalization_and_identity_trace_are_explicit():
    plan=CollectionPlanBuilder().build(discovery(candidate()))
    result=GenericHttpCollector(plan,Client({"data":{"count":1,"list":[{"id":"246","name":"Engineer"}]}})).collect()
    trace=result.duplicate_audit["normalization_trace"][0]
    assert trace["normalized"] and trace["identity_source"]=="STABLE_API_ID" and trace["dedup_classification"]=="PRESERVED"

def test_zero_unique_rejection_diagnostics():
    plan=CollectionPlan(source_url="https://x/jobs",mode="HTTP_API",executable=True,list_endpoint="https://x/list",observed_endpoints=["https://x/list"],list_method="POST",pagination_type="SINGLE_RESPONSE",list_path="data.list",job_id_field="id",job_title_field="title",observed_list_length=1)
    result=GenericHttpCollector(plan,Client({"data":{"list":[{"id":"1"}]}})).collect()
    assert result.total_fetched==1 and result.total_unique==0
    assert result.duplicate_audit["normalization_trace"][0]["reason"]=="TITLE_NOT_MAPPED"
    assert any("NORMALIZATION_REJECTED_ALL" in error for error in result.errors)

def test_empty_replay_after_positive_discovery_is_diagnostic():
    plan=CollectionPlan(source_url="https://x/jobs",mode="HTTP_API",executable=True,list_endpoint="https://x/list",observed_endpoints=["https://x/list"],list_method="POST",pagination_type="SINGLE_RESPONSE",list_path="data.list",job_id_field="id",job_title_field="title",observed_list_length=10)
    result=GenericHttpCollector(plan,Client({"data":{"list":[]}})).collect()
    assert result.status=="INCOMPLETE" and any("REPLAY_RESPONSE_EMPTY" in error for error in result.errors)

def test_non_replayable_sensitive_body_blocks_execution():
    value=candidate({"page":"1","access_token":"secret"},"application/json")
    plan=CollectionPlanBuilder().build(discovery(value))
    assert not plan.executable and not CollectionPlanValidator().validate(plan).valid

def test_sanitized_values_preserve_public_nested_shape():
    assert sanitized_values({"filters":{"aid":"68"},"from":"public"})=={"filters":{"aid":"68"},"from":"public"}
