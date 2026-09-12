import json
from job_extractor.discovery.detector import GenericApiDetector,_Observation
from job_extractor.discovery.models import ApiCandidate,DiscoveryResult,PaginationDetection
from job_extractor.discovery.network_analyzer import find_arrays,request_shape,response_shape,safe_url
from job_extractor.discovery.scorer import score_detail,score_list
from job_extractor.discovery.handoff import resolve_recruitment_handoff,deep_recruitment_navigation
from job_extractor.discovery.models import RecruitmentEntry
from job_extractor.adapters.registry import AdapterRegistry
from job_extractor.adapters import MokaAdapter,default_registry
from job_extractor.discovery.sources import _entry_type
from job_extractor.discovery.ats import profile_from_discovery
from job_extractor.discovery.stability import classify_stability

def jobs(n=5): return {"data":{"items":[{"id":i,"title":f"Job {i}","city":"X","department":"Eng"} for i in range(n)],"total":n}}
def test_job_like_response_scoring():
    score,evidence,shape=score_list("https://x.test/api/jobs/search",jobs()); assert score>=10 and evidence and shape["array_length"]==5
def test_nested_job_metadata_combination_scores_without_url():
    score,evidence,shape=score_list("https://x.test/api/v1/posts",{"data":{"records":[{"id":"1","name":"Engineer","department":"R&D","addr":"Sydney"},{"id":"2","name":"Designer","department":"R&D","addr":"Sydney"}]}})
    assert score>=10 and shape["candidate_list_path"]=="data.records" and shape["job_entity_density"]==1.0
def test_non_job_response_negative_scoring(): assert score_list("https://x.test/analytics/collect",{"config":1})[0]<0
def test_detail_candidate_scoring(): assert score_detail("https://x.test/api/job/1",{"id":1,"title":"A","description":"D","requirements":"R"})[0]>=10
def test_secret_redaction():
    clean,query=safe_url("https://x.test/jobs?page=2&_signature=bad&token=nope")
    assert clean=="https://x.test/jobs" and query["_signature"]==query["token"]=="[REDACTED]" and query["page"]=="2"
    assert request_shape({"page":1,"access_token":"bad"})=={"page":"int","access_token":"[REDACTED]"}
def test_request_and_response_shapes_do_not_store_values():
    assert request_shape({"page":1,"keyword":"secret"})=={"page":"int","keyword":"string"}
    shape=response_shape(jobs()); assert "items" in shape["candidate_list_path"] and "title" in shape["sample_field_names"]
def test_max_depth_guard(): assert not find_arrays({"a":{"b":{"c":{"d":{"e":[1,2]}}}}},max_depth=3)
def test_array_sample_limit(): assert len(find_arrays(list(range(100)))[0][1])==20
def candidate(body):
    o=_Observation("https://x.test/api/jobs","POST",body,{},jobs(),"initial")
    return o,GenericApiDetector._candidate(o)
def test_page_pagination_detection():
    o,c=candidate({"pageIndex":1,"pageSize":20}); p=GenericApiDetector._pagination([o],c)
    assert p.pagination_type=="PAGE" and p.page_param=="pageIndex" and p.page_size_param=="pageSize"
def test_offset_pagination_and_change_detection():
    a,c=candidate({"offset":0,"limit":20}); b=_Observation(a.url,a.method,{"offset":20,"limit":20},{},jobs(),"pagination")
    p=GenericApiDetector._pagination([a,b],c); assert p.pagination_type=="OFFSET" and "0 -> 20" in p.observed_change
def test_cursor_pagination_detection():
    o,c=candidate({"cursor":"abc","limit":20}); assert GenericApiDetector._pagination([o],c).pagination_type=="CURSOR"
def test_candidate_ranking_and_threshold():
    good,c=candidate({"page":1}); bad=_Observation("https://x.test/config","GET",{}, {},{"menu":[]},"initial")
    ranked=GenericApiDetector._rank([bad,good]); assert ranked[0].url.endswith("/api/jobs") and ranked[0].score>=GenericApiDetector.threshold
def test_blocked_text_detection(): assert GenericApiDetector._blocked("Please verify you are human")

class Locator:
    def __init__(self,kind,text="",hrefs=None): self.kind=kind; self.text=text; self.hrefs=hrefs or []
    def inner_text(self,timeout=None): return self.text
    def count(self): return len(self.hrefs) if self.kind=="links" else 0
    def nth(self,i): return Link(self.hrefs[i])
class Link:
    def __init__(self,item): self.item=item
    def get_attribute(self,key): return self.item[0]
    def inner_text(self): return self.item[1]
class Page:
    def __init__(self,text="",hrefs=None): self.callback=None; self.text=text; self.hrefs=hrefs or []
    def on(self,event,callback): self.callback=callback
    def goto(self,*a,**k): return None
    def wait_for_timeout(self,*a): pass
    def locator(self,selector):
        if selector=="body": return Locator("body",self.text)
        if selector=='a[href]': return Locator("links",hrefs=self.hrefs)
        return Locator("zero")
class Browser:
    def __init__(self,page): self.page=page
    def __enter__(self): return self
    def __exit__(self,*a): return False
def factory(page): return lambda **kwargs: Browser(page)
def test_dom_fallback_result():
    page=Page(hrefs=[("/jobs/101","Engineer"),("/jobs/102","Designer")]); result=GenericApiDetector(factory(page)).discover("https://x.test/careers")
    assert result.status=="PARTIAL" and result.dom_fallback["status"]=="DOM_LIST_DETECTED" and result.dom_fallback["job_card_count"]==2
def test_single_visible_job_with_result_count_is_executable_dom_evidence():
    page=Page(text="Jobs\n1 result",hrefs=[("/job/engineer-jid-1","Engineer")]);result=GenericApiDetector(factory(page)).discover("https://x.test/careers")
    assert result.dom_fallback["status"]=="DOM_LIST_DETECTED" and result.dom_fallback["visible_result_count"]==1
def test_blocked_state():
    result=GenericApiDetector(factory(Page("CAPTCHA verify you are human"))).discover("https://x.test/jobs")
    assert result.status=="BLOCKED"

def test_recruitment_entry_handoff_records_provenance():
    result=DiscoveryResult(source_url="https://company.test/careers",status="PARTIAL",page_type="RECRUITMENT_PORTAL",detected_scope={"recruitment_type":"campus"},recruitment_entries=[RecruitmentEntry(entry_type="CAMPUS",text="校园招聘",url="https://app.mokahr.com/campus-recruitment/acme/1",destination_host="app.mokahr.com")])
    entry=resolve_recruitment_handoff(result,default_registry)
    assert entry and entry.adapter_name==MokaAdapter.__name__ and result.handoff and result.handoff.entry_type=="CAMPUS"

def test_recruitment_entry_preserves_separate_scopes():
    result=DiscoveryResult(source_url="https://company.test/careers",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[
        RecruitmentEntry(entry_type="CAMPUS",text="校园招聘",url="https://app.mokahr.com/campus-recruitment/acme/1",destination_host="app.mokahr.com"),
        RecruitmentEntry(entry_type="SOCIAL",text="社会招聘",url="https://app.mokahr.com/social-recruitment/acme/2",destination_host="app.mokahr.com")])
    assert [entry.entry_type for entry in result.recruitment_entries]==["CAMPUS","SOCIAL"]

def test_chinese_entry_semantics_do_not_accept_url_alone():
    assert _entry_type("校园招聘", "https://company.test/careers") == "CAMPUS"
    assert _entry_type("", "https://company.test/campus") is None

def test_announcement_source_is_not_classified_as_job_list():
    candidate=ApiCandidate(url="https://company.test/api/news/list",method="GET",score=19,confidence="HIGH",response_shape={"sample_field_names":["id","title","description","linkUrl"]})
    result=DiscoveryResult(source_url="https://company.test/campus",status="DISCOVERED",probable_list_api=candidate,candidate_list_apis=[candidate],page_type="CAMPAIGN_PAGE")
    assert result.page_type=="CAMPAIGN_PAGE"

def test_unknown_ats_profile_is_reusable_and_sanitized():
    candidate=ApiCandidate(url="https://ats.example.test/api/jobs",method="GET",score=20,confidence="HIGH",response_shape={"candidate_list_path":"data.items","sample_field_names":["id","title","location"],"total_field":"total"},job_entity_density=1.0,observed_unique_ids=5)
    result=DiscoveryResult(source_url="https://company.example.test/careers",status="DISCOVERED",probable_list_api=candidate,candidate_list_apis=[candidate],detected_pagination=PaginationDetection(pagination_type="PAGE",page_param="page"),detected_scope={"site_id":7,"token":"redacted"})
    profile=profile_from_discovery(result)
    assert profile and profile.confidence=="HIGH" and profile.fingerprint.host=="ats.example.test"
    assert "token" not in profile.fingerprint.public_identifiers
    same=profile_from_discovery(result.model_copy(deep=True));assert same and same.profile_id==profile.profile_id

def test_deep_navigation_hands_off_at_intermediate_ats_node():
    root=DiscoveryResult(source_url="https://company.test/careers",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[RecruitmentEntry(entry_type="CAMPUS",text="校园招聘",url="https://company.test/campus",destination_host="company.test")])
    def discover(url):
        return DiscoveryResult(source_url="https://app.mokahr.com/campus-recruitment/acme/1",status="PARTIAL",page_type="JOB_LIST")
    outcome=deep_recruitment_navigation(root,discover,default_registry)
    assert outcome.handoff and outcome.handoff.adapter_name==MokaAdapter.__name__
    assert outcome.handoff.url.startswith("https://app.mokahr.com") and outcome.graph.terminal_reason=="KNOWN_ATS_HANDOFF"

def test_deep_navigation_is_bounded_and_loop_safe():
    root=DiscoveryResult(source_url="https://company.test/a",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[RecruitmentEntry(entry_type="CAMPUS",text="校园招聘",url="https://company.test/b",destination_host="company.test")])
    def discover(url):
        return DiscoveryResult(source_url=url,status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[RecruitmentEntry(entry_type="CAMPUS",text="查看职位",url="https://company.test/a",destination_host="company.test")])
    outcome=deep_recruitment_navigation(root,discover,default_registry,max_depth=3)
    assert outcome.handoff is None and outcome.graph.terminal_reason=="NO_RECRUITMENT_TERMINAL"
    assert len(outcome.graph.nodes)<=3

def test_explicit_campus_intent_rejects_social_terminal():
    root=DiscoveryResult(source_url="https://company.test/campus",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[
        RecruitmentEntry(entry_type="SOCIAL",text="社会招聘",url="https://app.mokahr.com/social-recruitment/acme/2",destination_host="app.mokahr.com"),
        RecruitmentEntry(entry_type="CAMPUS",text="校园招聘",url="https://app.mokahr.com/campus-recruitment/acme/1",destination_host="app.mokahr.com")])
    outcome=deep_recruitment_navigation(root,lambda url: DiscoveryResult(source_url=url,status="PARTIAL",page_type="JOB_LIST"),default_registry)
    assert outcome.handoff and "/campus-recruitment/" in outcome.handoff.url and outcome.graph.selected_scope=="CAMPUS"
    assert any("SOCIAL" in item for item in outcome.graph.rejected_alternatives)

def test_unknown_intent_with_multiple_scopes_requires_selection():
    root=DiscoveryResult(source_url="https://company.test/careers",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[
        RecruitmentEntry(entry_type="CAMPUS",text="校园招聘",url="https://app.mokahr.com/campus-recruitment/acme/1",destination_host="app.mokahr.com"),
        RecruitmentEntry(entry_type="SOCIAL",text="社会招聘",url="https://app.mokahr.com/social-recruitment/acme/2",destination_host="app.mokahr.com")])
    outcome=deep_recruitment_navigation(root,lambda url: DiscoveryResult(source_url=url,status="PARTIAL",page_type="JOB_LIST"),default_registry)
    assert outcome.handoff is None and outcome.graph.terminal_reason=="SCOPE_SELECTION_REQUIRED" and set(outcome.graph.available_scopes)=={"CAMPUS","SOCIAL"}

def test_discovery_stability_classification():
    assert classify_stability(["a","a","a"]).state=="STABLE"
    assert classify_stability(["a","a","b"]).state=="MOSTLY_STABLE"
    assert classify_stability(["a","b"]).state=="UNSTABLE"
    assert classify_stability(["a","b","c"]).state=="UNSTABLE"
