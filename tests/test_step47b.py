import pytest
from pydantic import ValidationError as PydanticValidationError

from job_extractor.collectors import GenericATSCollector,GenericHttpCollector
from job_extractor.collectors.generic_state import GenericSerializedStateCollector
from job_extractor.planning import CollectionPlan,CollectionPlanBuilder,CollectionPlanValidator,PlanContractError,can_dispatch,dispatch_target
from job_extractor.planning.execution_contract import gap_reasons,missing_fields
from job_extractor.discovery.models import ApiCandidate,DiscoveryResult,PaginationDetection
from job_extractor.models import CollectionResult


def http_plan(**changes):
    values=dict(source_url="https://site.test/jobs",mode="HTTP_API",executable=True,
        list_endpoint="https://api.site.test/jobs",list_method="GET",pagination_type="PAGE",
        page_param="pageNum",page_size_param="pageSize",initial_values={"pageNum":1,"pageSize":10},
        list_path="data.list",job_id_field="id",job_title_field="positionName",total_field="data.total",
        observed_endpoints=["https://api.site.test/jobs"],detail_mode="LIST_SUFFICIENT",confidence="HIGH")
    values.update(changes);return CollectionPlan(**values)

def browser_plan(**changes):
    values=dict(source_url="https://site.test/campus/",mode="BROWSER_API",executable=True,
        list_endpoint="https://site.test/api/v1/search/job/posts",list_method="POST",pagination_type="OFFSET",
        page_param="offset",offset_param="offset",page_size_param="limit",initial_values={"offset":0,"limit":10},
        list_path="data.job_post_list",total_field="data.count",job_id_field="id",job_title_field="title",
        browser_trigger="AUTO_PAGINATION",observed_endpoints=["https://site.test/api/v1/search/job/posts"],
        detail_mode="LIST_SUFFICIENT",confidence="HIGH")
    values.update(changes);return CollectionPlan(**values)

def runtime_plan(**changes):
    source=dict(mode="BROWSER_RUNTIME_DATA",provider="UNKNOWN",capability="ENCRYPTED_BROWSER_API",mechanism="TRANSFORM",
        record_count=10,unique_ids=10,job_id_field="id",job_title_field="title",location_field="workplace",
        department_field="dept",jd_fields=["description"],pagination={"total":498,"limit":20,"offset":0},
        pagination_model="OFFSET",offset_field="offset",limit_field="limit",initial_offset=0,limit=20,total=498,
        page_count=25,trigger_mode="OFFSET_BUTTON",pagination_validated=True,records=[{"id":"1","title":"Job"}],
        confidence="HIGH",executable=True,evidence=["runtime pagination contract validated"],validator_errors=[])
    values=dict(source_url="https://site.test/jobs",mode="BROWSER_RUNTIME_DATA",executable=True,
        runtime_source=source,list_path="transform.jobs",job_id_field="id",job_title_field="title",confidence="HIGH")
    values.update(changes);return CollectionPlan(**values)

# CASE 1: generic HTTP_API plan, complete contract, ats_profile=None must execute
def test_case1_generic_http_plan_without_ats_profile_is_dispatchable():
    plan=http_plan()
    assert plan.ats_profile is None
    assert plan.executable
    validation=CollectionPlanValidator().validate(plan)
    assert validation.valid,f"PLAN_VALIDATION_ERROR {validation.errors}"
    assert can_dispatch(plan)
    assert dispatch_target(plan)=="GenericHttpCollector"
    assert GenericATSCollector(plan).can_dispatch(plan)

def test_case1_collector_does_not_require_ats_profile():
    plan=http_plan()
    class Client:
        def __init__(self,payloads):self.payloads=list(payloads)
        def request(self,method,url,**kwargs):
            class R:
                def raise_for_status(self):pass
                def json(self):return self.payload
            return Responseish(self.payloads.pop(0))
    class Responseish:
        def __init__(self,payload):self.payload=payload
        def raise_for_status(self):pass
        def json(self):return self.payload
    client=Client([{"data":{"list":[{"id":1,"positionName":"岗位一"}],"total":1}}])
    result=GenericHttpCollector(plan,client=client).collect()
    assert result.total_unique==1 and result.status=="COMPLETE"

# CASE 2: ats_profile-bearing plan keeps working; known ATS path is untouched
def test_case2_plan_with_ats_profile_still_dispatches():
    profile=__import__("job_extractor.discovery.ats",fromlist=["profile_from_discovery"]).profile_from_discovery(
        __import__("job_extractor.discovery.models",fromlist=["DiscoveryResult"]).DiscoveryResult(
            source_url=http_plan().source_url,status="DISCOVERED",probable_list_api=ApiCandidate(
                url=http_plan().list_endpoint,method="GET",score=20,confidence="HIGH",
                response_shape={"sample_field_names":["id","positionName"],"candidate_list_path":"data.list"},
                observed_list_length=10,observed_unique_ids=10,job_entity_density=1.0)))
    plan=http_plan(ats_profile=profile)
    assert plan.ats_profile.profile_id.startswith("generic-")
    assert CollectionPlanValidator().validate(plan).valid
    assert can_dispatch(plan) and dispatch_target(plan)=="GenericHttpCollector"

# CASE 3: HTTP_API plan missing request URL
def test_case3_missing_request_url_rejected_before_execution():
    plan=http_plan(list_endpoint=None)
    assert "REQUEST_URL" in missing_fields(plan)
    assert not can_dispatch(plan)
    validation=CollectionPlanValidator().validate(plan)
    assert not validation.valid and "ENDPOINT_NOT_OBSERVED" in validation.errors
    with pytest.raises(PlanContractError) as exc:
        GenericATSCollector(plan).collect()
    assert exc.value.execution_mode=="HTTP_API"

# CASE 4: missing pagination advancement
def test_case4_missing_pagination_advance_rejected():
    plan=http_plan(page_param=None)
    assert "PAGINATION_ADVANCE" in missing_fields(plan)
    assert not can_dispatch(plan)
    errors=CollectionPlanValidator().validate(plan).errors
    assert "PAGE_PARAM_MISSING" in errors
    plan=http_plan(pagination_type="UNKNOWN",page_param=None,total_field=None)
    assert "PAGINATION_ADVANCE" in missing_fields(plan)

def test_case4_missing_termination_strategy_rejected():
    plan=http_plan(total_field=None,has_more_field=None)
    assert "TERMINATION_STRATEGY" in missing_fields(plan)
    assert "TERMINATION_STRATEGY_MISSING" in CollectionPlanValidator().validate(plan).errors
    assert not can_dispatch(plan)

# CASE 5: unsupported execution mode
def test_case5_unsupported_mode_pre_execution_reject():
    plan=http_plan(mode="UNSUPPORTED")
    assert missing_fields(plan)==["EXECUTION_MODE"] and gap_reasons(plan)==["UNSUPPORTED_EXECUTION_MODE"]
    assert dispatch_target(plan) is None
    assert not can_dispatch(plan)
    with pytest.raises(PlanContractError) as exc:
        GenericATSCollector(plan).collect()
    assert "DISPATCH_UNSUPPORTED" in exc.value.reason
    assert exc.value.execution_mode=="UNSUPPORTED"

# CASE 6: collector defensive contract violation -> controlled error
def test_case6_http_collector_defensive_contract_error():
    plan=http_plan(list_endpoint=None)
    with pytest.raises(PlanContractError) as exc:
        GenericHttpCollector(plan).collect()
    assert exc.value.reason.startswith("COLLECTION_CONTRACT") or exc.value.reason.startswith("COLLECTION")

# CASE 7: ByteDance real fixture (POST offset/limit/data.count, signed query -> BROWSER_API, ats_profile=None)
def bytedance_like_candidate():
    return ApiCandidate(url="https://site.test/api/v1/search/job/posts",method="POST",score=25,confidence="HIGH",replayable=True,
        query_params={"offset":"0","limit":"10","_signature":"[REDACTED]"},
        safe_request_values={"offset":0,"limit":10,"portal_type":3},
        request_body_shape={"offset":"int","limit":"int","portal_type":"int"},
        request_content_type="application/json",
        response_shape={"candidate_list_path":"data.job_post_list","sample_field_names":["id","title","city_list","job_category","description","requirement"],"total_field":"data.count"},
        observed_list_length=10,observed_total=7600,observed_unique_ids=10,job_entity_density=1.0)

def test_case7_bytedance_like_plan_no_crash():
    candidate=bytedance_like_candidate()
    result=DiscoveryResult(source_url="https://site.test/campus/position",status="DISCOVERED",
        candidate_list_apis=[candidate],probable_list_api=candidate,
        detected_pagination=__import__("job_extractor.discovery.models",fromlist=["PaginationDetection"]).PaginationDetection(
            pagination_type="OFFSET",page_param="offset",page_size_param="limit",total_field="data.count"),
        detected_scope={"recruitment_type":"campus"},page_type="ATS_EMBED")
    plan=CollectionPlanBuilder().build(result)
    validation=CollectionPlanValidator().validate(plan)
    assert plan.mode=="BROWSER_API" and plan.executable and validation.valid,f"PLAN_VALIDATION_ERROR {validation.errors}"
    assert GenericATSCollector.can_dispatch(plan)
    assert dispatch_target(plan)=="GenericBrowserApiCollector"
    assert "GENERIC_ATS_PROFILE_NOT_EXECUTABLE" not in " ".join(plan.warnings)

# Invariant: executable + valid => dispatchable (main modes)
@pytest.mark.parametrize("plan",[
    http_plan(),
    http_plan(mode="BROWSER_API",browser_trigger="AUTO_PAGINATION"),
    CollectionPlan(source_url="https://site.test/jobs",mode="SERIALIZED_STATE",executable=True,
        list_endpoint="https://site.test/jobs",list_path="props.page.data.jobs",job_id_field="id",job_title_field="title",
        observed_endpoints=["https://site.test/jobs"],source_index=0),
    CollectionPlan(source_url="https://site.test/jobs",mode="DOM",executable=True,allowed_detail_urls=["https://site.test/jobs/1"]),
    runtime_plan(),
])
def test_invariant_executable_valid_implies_dispatchable(plan):
    validation=CollectionPlanValidator().validate(plan)
    assert plan.executable,f"fixture must be executable; errors={validation.errors}"
    assert validation.valid,f"fixture must be valid; errors={validation.errors}"
    assert can_dispatch(plan),f"executable+valid plan not dispatchable; gaps={gap_reasons(plan)}"
    assert dispatch_target(plan) is not None

def test_invariant_contract_gaps_always_block_dispatch():
    for plan in (http_plan(list_endpoint=None),http_plan(page_param=None),http_plan(list_path=None),
                 http_plan(job_id_field=None),http_plan(job_title_field=None),
                 http_plan(total_field=None,has_more_field=None),runtime_plan(runtime_source={})):
        assert not can_dispatch(plan),f"gap plan must not dispatch: {gap_reasons(plan)}"
