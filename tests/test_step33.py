from job_extractor.adapters import default_registry
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.discovery.ats import profile_from_discovery
from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.models import ApiCandidate, DiscoveryResult, PaginationDetection
from job_extractor.discovery.network_analyzer import safe_url
from job_extractor.discovery.scorer import score_list
from job_extractor.discovery.sources import rank_spa_action
from job_extractor.discovery.stability import stable_navigation
from job_extractor.discovery.handoff import deep_recruitment_navigation
from job_extractor.planning import CollectionPlan, CollectionPlanBuilder, CollectionPlanValidator


def records():
    return [
        {
            "positionId": f"stable-{index}",
            "projectPositionId": f"project-{index}",
            "projectPositionName": f"Engineer {index}",
            "workPlaceCode": "Sydney",
            "recruitCategoryName": "Engineering",
            "projectType": "1",
            "projectPositionDto": {
                "jobResponsibility": "Build reliable systems and own delivery.",
                "jobRequirement": "Python experience and clear communication.",
            },
        }
        for index in range(2)
    ]


def candidate():
    payload={"code":0,"data":{"data":records(),"total":2}}
    observation=_Observation("https://careers.test/backend/school/position/common/position/list?traceId=discard", "POST", {"projectRuleId":"public","pageIndex":1,"pageSize":1}, {}, payload, "POST_ROUTE_NETWORK", request_content_type="application/json")
    return GenericApiDetector._candidate(observation)


def plan(**updates):
    values=dict(source_url="https://careers.test/school/home",company="Example",mode="HTTP_API",executable=True,
        list_endpoint="https://careers.test/api/position/list",list_method="POST",pagination_type="PAGE",page_param="pageIndex",page_size_param="pageSize",
        initial_values={"projectRuleId":"public","pageIndex":1,"pageSize":1},list_path="data.data",total_field="data.total",
        job_id_field="positionId",job_title_field="projectPositionName",detail_mode="LIST_SUFFICIENT",
        detail_endpoint_template="https://careers.test/school/post/details?positionId={id}",scope={"recruitment_type":"campus"},confidence="HIGH",
        observed_endpoints=["https://careers.test/api/position/list"])
    values.update(updates)
    return CollectionPlan(**values)


def test_spa_action_ranking_prefers_campus_job_action():
    campus=rank_spa_action("校园岗位投递","", "CAMPUS")
    social=rank_spa_action("社会招聘","https://recruit.test/home", "CAMPUS")
    assert campus>social and campus>0


def test_bundle_hint_project_list_is_not_promoted_as_jobs():
    payload={"data":[{"projectRuleId":"p1","projectRuleName":"Campus","projectType":"1"} for _ in range(3)]}
    score,_,shape=score_list("https://careers.test/backend/school/position/common/project/list",payload)
    assert score<10 and "LOW_JOB_ENTITY_DENSITY" in shape["rejection_reasons"]


def test_route_triggered_nested_job_shape_is_high_confidence():
    value=candidate()
    assert value.confidence=="HIGH" and value.observed_phase=="POST_ROUTE_NETWORK"
    assert value.response_shape["candidate_list_path"]=="data.data"
    assert value.response_shape["inferred_job_id_field"]=="positionId"
    assert value.response_shape["inferred_job_title_field"]=="projectPositionName"


def test_tracking_id_is_not_persisted_in_observed_query():
    _,query=safe_url("https://careers.test/api/list?page=1&_ihr_log_trackId=random")
    assert query=={"page":"1"}


def test_generic_page_plan_and_profile_remain_validator_gated():
    value=candidate()
    result=DiscoveryResult(source_url="https://careers.test/school/home",status="DISCOVERED",candidate_list_apis=[value],probable_list_api=value,
        detected_scope={"recruitment_type":"campus"},detected_pagination=PaginationDetection(pagination_type="PAGE",page_param="pageIndex",page_size_param="pageSize",total_field="data.total"))
    result.ats_profile=profile_from_discovery(result)
    built=CollectionPlanBuilder().build(result)
    assert built.mode=="HTTP_API" and built.detail_mode=="LIST_SUFFICIENT"
    assert built.ats_profile and built.ats_profile.confidence=="HIGH"
    assert CollectionPlanValidator().validate(built).valid


def test_nested_normalization_and_public_detail_binding():
    job=GenericHttpCollector(plan())._job(records()[0])
    assert job and job.job_id=="stable-0" and job.job_title=="Engineer 0"
    assert job.locations==["Sydney"] and job.job_category=="Engineering"
    assert job.recruitment_type=="campus" and job.full_jd and job.responsibilities and job.requirements
    assert job.detail_url.endswith("positionId=stable-0") and job.apply_url==job.detail_url


def test_page_pagination_uses_total_and_collects_unique_records():
    class Response:
        def __init__(self,payload):self.payload=payload
        def raise_for_status(self):pass
        def json(self):return self.payload
    class Client:
        def request(self,method,url,**kwargs):
            page_index=kwargs["json"]["pageIndex"]
            return Response({"data":{"data":[records()[page_index-1]],"total":2}})
    result=GenericHttpCollector(plan(),client=Client()).collect()
    assert result.status=="COMPLETE" and result.total_expected==result.total_fetched==result.total_unique==2
    assert result.metrics.list_requests==2


def test_fresh_discovery_disagreement_gets_majority_evidence():
    calls=[]
    initial=DiscoveryResult(source_url="https://careers.test",status="NOT_FOUND")
    def discover(url):
        calls.append(url)
        return DiscoveryResult(source_url=url,status="DISCOVERED" if len(calls)>0 else "NOT_FOUND",page_type="JOB_LIST")
    _,stability=stable_navigation(initial.source_url,discover,default_registry,initial_result=initial)
    assert stability.attempts==3 and stability.state=="MOSTLY_STABLE"


def test_generic_terminal_preserves_observed_campus_scope():
    value=candidate()
    result=DiscoveryResult(source_url="https://careers.test/",status="DISCOVERED",candidate_list_apis=[value],probable_list_api=value,
        detected_scope={"recruitment_type":"campus"},detected_pagination=PaginationDetection(pagination_type="PAGE",page_param="pageIndex",page_size_param="pageSize",total_field="data.total"))
    result.ats_profile=profile_from_discovery(result)
    outcome=deep_recruitment_navigation(result,lambda url:result,default_registry)
    assert outcome.graph.source_intent=="CAMPUS" and outcome.graph.selected_scope=="CAMPUS"
