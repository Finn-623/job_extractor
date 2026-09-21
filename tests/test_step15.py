from job_extractor.collectors.generic_http import GenericHttpCollector,duplicate_audit
from job_extractor.collectors.generic_dom import GenericDomCollector
from job_extractor.discovery.detector import GenericApiDetector,_Observation
from job_extractor.discovery.dom_semantics import company_name_or_none,credible_jd,filter_detail_links
from job_extractor.discovery.models import ApiCandidate,DiscoveryResult
from job_extractor.discovery.scorer import score_list
from job_extractor.planning import CollectionPlan,CollectionPlanBuilder,CollectionPlanValidator
from tests.test_generic_collectors import Browser,Client,plan,raw

def _jobs(n=5):return {"jobs":[{"id":i,"title":f"Engineer {i}","location":"Sydney","applyUrl":f"https://apply.test/job/{100+i}"} for i in range(n)],"total":n}
def test_navigation_payload_rejected():
    score,_,shape=score_list("https://x.test/global-header/flyouts",{"items":[{"id":i,"title":f"Menu {i}"} for i in range(12)]})
    assert score<10 and "NAVIGATION_PAYLOAD" in shape["rejection_reasons"]
def test_config_and_widget_payload_rejected():
    _,_,shape=score_list("https://x.test/api/jobwidgetsettings",{"settings":[{"id":1,"title":"Featured"}]})
    assert {"CONFIG_PAYLOAD","WIDGET_PAYLOAD"}<=set(shape["rejection_reasons"])
def test_facet_payload_rejected():
    _,_,shape=score_list("https://x.test/jobs/options/facetValues",{"items":[{"id":i,"title":f"Country {i}"} for i in range(10)]})
    assert "FACET_PAYLOAD" in shape["rejection_reasons"]
def test_mixed_array_and_low_density_rejected():
    payload={"items":[{"title":"Menu","url":"/a"},{"id":2,"name":"Locale"},{"setting":"x"},{"category":"Y"}]}
    _,_,shape=score_list("https://x.test/data",payload)
    assert "MIXED_CONTENT_PAYLOAD" in shape["rejection_reasons"] and "LOW_JOB_ENTITY_DENSITY" in shape["rejection_reasons"]
def test_homogeneous_job_array_scores_high():
    score,_,shape=score_list("https://x.test/search",_jobs())
    assert score>=15 and shape["job_entity_density"]==1 and not shape["rejection_reasons"]

def _candidate(confidence="HIGH",complete=False):
    evidence=["API total equals observed list length"] if complete else []
    return ApiCandidate(url="https://x.test/api",method="GET",score=17,confidence=confidence,response_shape={"candidate_list_path":"jobs","sample_field_names":["id","title","location"]},observed_list_length=2,observed_total=2 if complete else None,evidence=evidence)
def test_medium_api_loses_to_valid_high_dom():
    c=_candidate("MEDIUM");d=DiscoveryResult(source_url="https://x.test/jobs",status="PARTIAL",candidate_list_apis=[c],probable_list_api=c,dom_fallback={"status":"DOM_LIST_DETECTED","possible_detail_links":["https://x.test/jobs/101"]})
    assert CollectionPlanBuilder().build(d).mode=="DOM"
def test_invalid_high_api_loses_to_valid_high_dom():
    c=_candidate();d=DiscoveryResult(source_url="https://x.test/jobs",status="DISCOVERED",candidate_list_apis=[c],probable_list_api=c,dom_fallback={"status":"DOM_LIST_DETECTED","possible_detail_links":["https://x.test/jobs/101"]})
    assert CollectionPlanBuilder().build(d).mode=="DOM"
def test_incomplete_dom_first_page_requires_review():
    d=DiscoveryResult(source_url="https://x.test/jobs",status="PARTIAL",dom_fallback={"status":"DOM_LIST_DETECTED","visible_result_count":20,"possible_detail_links":["https://x.test/jobs/101"]})
    p=CollectionPlanBuilder().build(d)
    assert p.mode=="DOM" and not p.executable and p.review_required

def test_fragment_and_search_links_rejected():
    accepted,rejected=filter_detail_links([("#main","Main"),("/jobs/search-results","Search Jobs"),("/jobs/12345","Backend Engineer")],"https://x.test/jobs")
    assert accepted==["https://x.test/jobs/12345"] and len(rejected)==2
def test_related_host_requires_observed_repetition():
    links=["https://apply.test/job/101","https://apply.test/job/102"]
    p=CollectionPlan(source_url="https://careers.test/jobs",mode="DOM",executable=True,allowed_detail_urls=links,trusted_detail_hosts=["apply.test"])
    assert CollectionPlanValidator().validate(p).valid
    p.trusted_detail_hosts=[];assert not CollectionPlanValidator().validate(p).valid

def test_credible_jd_threshold():
    assert not credible_jd("Title only")
    assert credible_jd("Job description and responsibilities. "+"Build reliable systems for customers. "*8)
def test_jd_not_found_is_detail_failure():
    class ShortPage:
        url=""
        def goto(self,url,**kwargs):self.url=url
        def wait_for_timeout(self,*args):pass
        def locator(self,selector):
            from tests.test_generic_collectors import Loc
            return Loc("Engineer" if selector=="h1" else "Short")
    class ShortBrowser:
        def __init__(self):self.page=ShortPage()
        def __enter__(self):return self
        def __exit__(self,*args):return False
    p=CollectionPlan(source_url="https://x.test/jobs",mode="DOM",executable=True,allowed_detail_urls=["https://x.test/jobs/101"])
    result=GenericDomCollector(p,browser_factory=ShortBrowser).collect()
    assert result.metrics.details_succeeded==0 and result.metrics.details_failed==1 and any("JD_NOT_FOUND" in x for x in result.errors)

def test_explainable_duplicate_audit_allows_complete():
    # STEP 70 treats a record-level detail URL as a required detail stage.
    # This test exercises only explainable list duplicate accounting.
    a=raw(1);b=dict(a)
    p=plan();result=GenericHttpCollector(p,Client([{"data":{"items":[a,b],"total":2}}])).collect()
    assert result.status=="COMPLETE" and result.duplicate_audit["collapsed_count"]==1 and result.duplicate_audit["unexplained_count"]==0
def test_unexplained_duplicate_is_incomplete():
    a=raw(1);b=raw(1);b["title"]="Different role";b["description"]="Different work"
    result=GenericHttpCollector(plan(),Client([{"data":{"items":[a,b],"total":2}}])).collect()
    assert result.status=="INCOMPLETE" and result.duplicate_audit["unexplained_count"]==1

def test_semantic_path_scope_requires_page_evidence():
    assert GenericApiDetector._scope("https://x.test/locations/sydney.html",[],"Jobs in Sydney")["path_filters"]=={"location":"sydney"}
    assert "path_filters" not in GenericApiDetector._scope("https://x.test/locations/sydney.html",[],"Generic careers")
def test_generic_company_heading_rejected():
    assert company_name_or_none("All Openings") is None and company_name_or_none("Acme") == "Acme"
def test_multi_company_candidate_is_recorded():
    payload={"jobs":[{"id":1,"title":"A","location":"X","company":{"name":"One"}},{"id":2,"title":"B","location":"Y","company":{"name":"Two"}}]}
    c=GenericApiDetector._candidate(_Observation("https://x.test/jobs","GET",{}, {},payload,"initial"))
    assert c.observed_company_count==2
