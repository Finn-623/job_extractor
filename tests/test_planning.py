from job_extractor.discovery.models import ApiCandidate,DiscoveryResult,PaginationDetection
from job_extractor.planning import CollectionPlan,CollectionPlanBuilder,CollectionPlanValidator

def candidate(confidence="HIGH",score=17):
    return ApiCandidate(url="https://api.example.test/jobs",method="GET",score=score,confidence=confidence,
        response_shape={"candidate_list_path":"jobs","sample_field_names":["id","title","location"],"total_field":"meta.total"},
        evidence=["job-like records","API total equals observed list length"],observed_list_length=5,observed_total=5)
def discovery(c):
    return DiscoveryResult(source_url="https://careers.example.test/jobs",status="DISCOVERED",candidate_list_apis=[c],probable_list_api=c,
        detected_pagination=PaginationDetection(total_field="meta.total"))
def test_collection_plan_model(): assert CollectionPlan(source_url="https://x/jobs",mode="UNSUPPORTED").mode=="UNSUPPORTED"
def test_high_candidate_builds_executable_plan():
    p=CollectionPlanBuilder().build(discovery(candidate())); assert p.executable and p.mode=="HTTP_API" and p.pagination_type=="SINGLE_RESPONSE" and p.list_path=="jobs"
def test_medium_candidate_requires_review():
    p=CollectionPlanBuilder().build(discovery(candidate("MEDIUM",12))); assert not p.executable and p.review_required and p.mode=="UNSUPPORTED"
def test_low_candidate_is_unsupported():
    p=CollectionPlanBuilder().build(discovery(candidate("LOW",5))); assert not p.executable and "PLAN_UNSUPPORTED" in p.warnings
def test_browser_secret_candidate_not_http_executable():
    c=candidate();c.query_params={"signature":"[REDACTED]"};p=CollectionPlanBuilder().build(discovery(c))
    assert p.mode=="BROWSER_API" and not p.executable
def test_external_observed_api_is_valid():
    p=CollectionPlanBuilder().build(discovery(candidate()));assert CollectionPlanValidator().validate(p).valid
def test_unobserved_api_is_rejected():
    p=CollectionPlanBuilder().build(discovery(candidate()));p.list_endpoint="https://evil.test/jobs"
    assert "ENDPOINT_NOT_OBSERVED" in CollectionPlanValidator().validate(p).errors
def test_inconsistent_page_plan_rejected():
    p=CollectionPlan(source_url="https://x/jobs",mode="HTTP_API",executable=True,list_endpoint="https://x/api",observed_endpoints=["https://x/api"],list_method="GET",list_path="jobs",job_id_field="id",job_title_field="title",pagination_type="PAGE",page_param="page")
    assert "PAGE_INITIAL_VALUE_MISSING" in CollectionPlanValidator().validate(p).errors
def test_dom_plan_uses_only_observed_links():
    d=DiscoveryResult(source_url="https://x.test/jobs",status="PARTIAL",dom_fallback={"status":"DOM_LIST_DETECTED","possible_title_selector":"a[href]","possible_detail_links":["https://x.test/jobs/1"]})
    p=CollectionPlanBuilder().build(d);assert p.mode=="DOM" and p.allowed_detail_urls==["https://x.test/jobs/1"]
def test_dom_cross_host_is_rejected():
    p=CollectionPlan(source_url="https://x.test/jobs",mode="DOM",executable=True,allowed_detail_urls=["https://evil.test/jobs/1"])
    assert "DOM_LINK_OUTSIDE_TRUST_MODEL" in CollectionPlanValidator().validate(p).errors
def test_plan_secret_key_is_rejected():
    p=CollectionPlan(source_url="https://x/jobs",mode="UNSUPPORTED",executable=True,initial_values={"access_token":"bad"})
    assert "PLAN_CONTAINS_SENSITIVE_FIELD" in CollectionPlanValidator().validate(p).errors

def test_plan_preserves_ats_profile():
    c=candidate();c.job_entity_density=1.0;c.observed_unique_ids=5;d=discovery(c);d.ats_profile=__import__("job_extractor.discovery.ats",fromlist=["profile_from_discovery"]).profile_from_discovery(d)
    p=CollectionPlanBuilder().build(d);assert p.ats_profile and p.ats_profile.profile_id.startswith("generic-")
