from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.dom_semantics import filter_detail_links, _path_bound_ids
from job_extractor.discovery.models import ApiCandidate, DiscoveryResult, PaginationDetection
from job_extractor.planning import CollectionPlanBuilder


def test_nested_job_array_is_inferred_from_generic_wrapper():
    payload={"envelope":{"result":{"records":[
        {"id":"a1","title":"Engineer","location":"Sydney","department":"Platform","description":"Build systems"},
        {"id":"a2","title":"Analyst","location":"Melbourne","department":"Data","description":"Analyze data"},
    ]},"count":2}}
    candidate=GenericApiDetector._candidate(_Observation("https://jobs.test/api/search","POST",{}, {},payload,"TERMINAL_INITIAL"))
    assert candidate.response_shape["candidate_list_path"]=="envelope.result.records"
    assert candidate.observed_unique_ids==2 and candidate.job_entity_density==1.0


def test_deep_wrapper_job_array_is_not_path_table_dependent():
    payload={"payload":{"response":{"nested":{"rows":[
        {"positionId":"p1","positionName":"Engineer","city":"Sydney","requirements":"Python"},
        {"positionId":"p2","positionName":"Designer","city":"Melbourne","requirements":"Design"},
    ]}}}}
    candidate=GenericApiDetector._candidate(_Observation("https://jobs.test/api/feed","GET",{}, {},payload,"TERMINAL_INITIAL"))
    assert candidate.response_shape["candidate_list_path"]=="payload.response.nested.rows"
    assert candidate.confidence=="HIGH"


def test_config_department_and_privacy_arrays_are_rejected():
    for url,payload in (
        ("https://jobs.test/api/departments", {"data":[{"id":"1","label":"Platform"},{"id":"2","label":"Data"}]}),
        ("https://jobs.test/api/privacy-policy/get", {"data":[{"id":"1","name":"Privacy","content":"Policy"}]}),
    ):
        candidate=GenericApiDetector._candidate(_Observation(url,"GET",{}, {},payload,"TERMINAL_INITIAL"))
        assert candidate.rejection_reasons
        assert GenericApiDetector._rank([_Observation(url,"GET",{}, {},payload,"TERMINAL_INITIAL")])==[]


def test_hash_job_route_is_a_generic_detail_link_and_binds_id():
    base="https://jobs.test/careers#/jobs"
    links=[("#/job/abc-1234","Engineer"),("#/job/def-5678","Designer")]
    accepted,rejected=filter_detail_links(links,base,[])
    assert len(accepted)==2 and not rejected
    assert _path_bound_ids(accepted[0])[-1]==("job","abc-1234")


def test_api_plan_outranks_dom_fallback_when_both_are_valid():
    api=ApiCandidate(url="https://jobs.test/api/jobs",method="GET",score=30,confidence="HIGH",replayable=True,
        response_shape={"candidate_list_path":"data.items","sample_field_names":["id","title","location"],"total_field":"data.total"},
        observed_list_length=2,observed_total=2,observed_unique_ids=2,job_entity_density=1.0,evidence=["API total equals observed list length"])
    result=DiscoveryResult(source_url="https://jobs.test/careers",status="DISCOVERED",candidate_list_apis=[api],probable_list_api=api,
        detected_pagination=PaginationDetection(pagination_type="UNKNOWN",total_field="data.total"),
        dom_fallback={"status":"DOM_LIST_DETECTED","possible_detail_links":["https://jobs.test/careers#/job/a1","https://jobs.test/careers#/job/a2"],"card_link_parity":"CARD_LINK_PARITY"})
    plan=CollectionPlanBuilder().build(result)
    assert plan.mode=="HTTP_API" and plan.list_endpoint==api.url
