from datetime import datetime,timedelta,timezone
import json
from job_extractor import __version__
from job_extractor.collectors.generic_detail import split_jd
from job_extractor.discovery.artifacts import load_reusable_discovery,normalized_source_url
from job_extractor.discovery.detector import GenericApiDetector,_Observation
from job_extractor.discovery.dom_semantics import extract_company,extract_detail_dom,first_real_url,repeated_job_links,route_changed
from job_extractor.discovery.models import ApiCandidate,DiscoveryResult,PaginationDetection
from job_extractor.planning import CollectionPlanBuilder

def test_payload_job_link_hint():
    assert first_real_url({"absolute_url":"/jobs/1"},"https://x.test/careers")==("https://x.test/jobs/1","absolute_url")
def test_payload_does_not_invent_link(): assert first_real_url({"id":"1","title":"Job"},"https://x.test")== (None,None)
def test_nested_payload_job_link_hint():
    assert first_real_url({"id":"1","links":{"absolute_url":"/jobs/1"}},"https://x.test/careers")== ("https://x.test/jobs/1","links.absolute_url")
def test_requirements_heading_parsing():
    resp,req=split_jd("Responsibilities\nBuild systems\nRequirements\nPython\nTeamwork")
    assert resp==["Build systems"] and req==["Python","Teamwork"]
def test_chinese_heading_parsing():
    resp,req=split_jd("岗位职责\n开发系统\n任职要求\n本科")
    assert resp==["开发系统"] and req==["本科"]
def test_low_confidence_split_keeps_structure_empty(): assert split_jd("A paragraph without headings")==([],[])
def test_url_scope_extraction():
    scope=GenericApiDetector._scope("https://x.test/jobs?location=Sydney%2C+Australia&department=Engineering",[])
    assert scope["url_filters"]=={"location":"Sydney, Australia","department":"Engineering"}
def test_sensitive_url_scope_is_removed():
    scope=GenericApiDetector._scope("https://x.test/jobs?location=Sydney&token=bad&_signature=no",[])
    assert scope["url_filters"]=={"location":"Sydney"} and "bad" not in str(scope)
def test_candidate_observation_has_total_and_real_hint():
    payload={"jobs":[{"id":1,"title":"Engineer","absolute_url":"https://x.test/jobs/1"} for _ in range(5)],"total":5}
    c=GenericApiDetector._candidate(_Observation("https://x.test/api/jobs","GET",{}, {},payload,"initial"))
    assert c.observed_list_length==5 and c.observed_total==5 and c.sample_job_hint["absolute_url"].endswith("/1")
def test_weak_single_response_requires_review():
    c=ApiCandidate(url="https://x.test/api/jobs",method="GET",score=17,confidence="HIGH",response_shape={"candidate_list_path":"jobs","sample_field_names":["id","title"]})
    d=DiscoveryResult(source_url="https://x.test/jobs",status="DISCOVERED",probable_list_api=c,candidate_list_apis=[c])
    p=CollectionPlanBuilder().build(d);assert not p.executable and p.review_required and p.pagination_type=="UNKNOWN"
def test_detail_dom_builds_enrichment_plan():
    c=ApiCandidate(url="https://x.test/api/jobs",method="GET",score=17,confidence="HIGH",response_shape={"candidate_list_path":"jobs","sample_field_names":["id","title","absolute_url"]},observed_list_length=1,observed_total=1)
    d=DiscoveryResult(source_url="https://x.test/jobs",status="DISCOVERED",probable_list_api=c,candidate_list_apis=[c],detail_dom={"status":"DETAIL_DOM","url_field":"absolute_url","jd_selector":"main"})
    p=CollectionPlanBuilder().build(d);assert p.detail_mode=="DETAIL_DOM" and p.detail_url_field=="absolute_url" and p.detail_jd_selector=="main"
def test_candidate_detail_url_field_survives_missing_detail_dom_field():
    payload={"jobs":[{"id":i,"title":f"Job {i}","location":"Remote","links":{"absolute_url":f"/jobs/{i}"}} for i in range(20)]}
    c=GenericApiDetector._candidate(_Observation("https://x.test/api/jobs","GET",{}, {},payload,"initial"))
    assert c.detail_url_field=="links.absolute_url" and c.detail_url_coverage==1.0
    c.evidence.append("bounded unpaginated response has unique IDs and complete detail URL coverage")
    d=DiscoveryResult(source_url="https://x.test/jobs",status="DISCOVERED",probable_list_api=c,candidate_list_apis=[c],detail_dom={"status":"DETAIL_DOM","jd_selector":"main"})
    p=CollectionPlanBuilder().build(d)
    assert p.executable and p.pagination_type=="SINGLE_RESPONSE" and p.detail_url_field=="links.absolute_url"
def test_spa_route_change(): assert route_changed("https://x/jobs","https://x/jobs/1") and not route_changed("x","x")
def test_pagination_v2_types():
    assert PaginationDetection(pagination_type="LOAD_MORE").pagination_type=="LOAD_MORE"
    assert PaginationDetection(pagination_type="INFINITE_SCROLL").pagination_type=="INFINITE_SCROLL"
def test_explicit_api_total_is_valid_single_response_evidence():
    c=ApiCandidate(url="https://x.test/api/jobs",method="GET",score=17,confidence="HIGH",response_shape={"candidate_list_path":"jobs","sample_field_names":["id","title"]},observed_list_length=336,observed_total=336)
    d=DiscoveryResult(source_url="https://x.test/jobs",status="DISCOVERED",probable_list_api=c,candidate_list_apis=[c])
    p=CollectionPlanBuilder().build(d)
    assert p.executable and p.pagination_type=="SINGLE_RESPONSE"

class Loc:
    def __init__(self,text="",attrs=None,nodes=None):self.text=text;self.attrs=attrs or {};self.nodes=nodes or [];self.first=self
    def count(self):return len(self.nodes) if self.nodes else int(bool(self.text or self.attrs))
    def inner_text(self):return self.text
    def get_attribute(self,k):return self.attrs.get(k)
    def all(self):return self.nodes
    def text_content(self):return self.text
class Page:
    def __init__(self,mapping,title=""):self.mapping=mapping;self._title=title
    def locator(self,selector):return self.mapping.get(selector,Loc())
    def title(self):return self._title
def test_repeated_dom_structure_finds_job_links_without_hostname_rules():
    class StructuralPage:
        def evaluate(self,_script):
            return [
                {"raw":"/opening/backend-101","text":"Backend Engineer","signature":"A|job-link|ARTICLE|card","region":"content"},
                {"raw":"/opening/frontend-102","text":"Frontend Engineer","signature":"A|job-link|ARTICLE|card","region":"content"},
                {"raw":"/about","text":"About","signature":"A|nav||","region":"content"},
            ]
    assert repeated_job_links(StructuralPage(),"https://x.test/jobs")==["https://x.test/opening/backend-101","https://x.test/opening/frontend-102"]
def test_repeated_dom_structure_rejects_mixed_content_feed():
    class MixedPage:
        def evaluate(self,_script):
            return [
                {"raw":"/job/engineer-jid-1","text":"Engineer","signature":"A|card","region":"content"},
                {"raw":"/blog/story-one","text":"Story one","signature":"A|card","region":"content"},
                {"raw":"/blog/story-two","text":"Story two","signature":"A|card","region":"content"},
            ]
    assert repeated_job_links(MixedPage(),"https://x.test/jobs")==[]
def test_semantic_location_department_separation():
    page=Page({'[class*="location"]':Loc("Sydney"),'[class*="department"]':Loc("Engineering"),'[class*="employment"]':Loc("Full Time"),'.posting-page .content':Loc("Responsibilities\nBuild\nRequirements\nPython")})
    d=extract_detail_dom(page);assert d["location"]=="Sydney" and d["department"]=="Engineering" and d["employment_type"]=="Full Time"
def test_company_open_graph_extraction():
    page=Page({'meta[property="og:site_name"]':Loc(attrs={"content":"Acme Careers"})})
    assert extract_company(page)=="Acme Careers"
def artifact(tmp_path,url="https://x.test/jobs",version=__version__,age_hours=1):
    when=datetime.now(timezone.utc)-timedelta(hours=age_hours);d=DiscoveryResult(source_url=url,status="PARTIAL",tool_version=version,started_at=when,finished_at=when)
    p=tmp_path/"x_discovery.json";p.write_text(d.model_dump_json(),encoding="utf-8");return p
def test_discovery_artifact_reuse(tmp_path):
    p=artifact(tmp_path);loaded=load_reusable_discovery("https://x.test/jobs/",tmp_path);assert loaded and loaded[1]==p
def test_naive_local_artifact_reuse(tmp_path):
    when=datetime.now().replace(tzinfo=None);d=DiscoveryResult(source_url="https://x.test/jobs",status="PARTIAL",tool_version=__version__,started_at=when,finished_at=when)
    (tmp_path/"local_discovery.json").write_text(d.model_dump_json(),encoding="utf-8")
    assert load_reusable_discovery("https://x.test/jobs",tmp_path)
def test_stale_artifact_rejected(tmp_path):
    artifact(tmp_path,age_hours=30);assert load_reusable_discovery("https://x.test/jobs",tmp_path) is None
def test_wrong_version_and_source_rejected(tmp_path):
    artifact(tmp_path,version="old");assert load_reusable_discovery("https://x.test/jobs",tmp_path) is None
    artifact(tmp_path,url="https://other.test/jobs");assert load_reusable_discovery("https://x.test/jobs",tmp_path) is None
def test_normalized_query_order(): assert normalized_source_url("HTTPS://X.TEST/jobs/?b=2&a=1")=="https://x.test/jobs?a=1&b=2"
