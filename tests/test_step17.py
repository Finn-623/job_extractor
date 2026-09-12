import json
from job_extractor.discovery.detector import GenericApiDetector,_Observation
from job_extractor.discovery.dom_semantics import repeated_job_cards
from job_extractor.discovery.dynamic import DISCOVERY_PHASES,is_pagination_control,visible_total,visible_total_evidence
from job_extractor.discovery.models import ApiCandidate,DiscoveryResult,PaginationDetection
from job_extractor.discovery.sources import embedded_sources
from job_extractor.planning import CollectionPlan,CollectionPlanBuilder
from job_extractor.collectors.generic_dom import GenericDomCollector

def test_semantic_total_extraction_records_evidence():
    evidence,conflict=visible_total_evidence("Showing 1–25 of 140 jobs")
    assert not conflict and evidence[0]["value"]==140 and evidence[0]["source_text"]=="Showing 1–25 of 140" and evidence[0]["confidence"]=="HIGH" and evidence[0]["locator"]=="body"

def test_isolated_numbers_are_not_totals():
    assert visible_total("Page 1 | Updated 2026 | Slide 4") is None

def test_conflicting_totals_are_explicit_and_not_selected():
    evidence,conflict=visible_total_evidence("169 results. 10 jobs")
    assert conflict and {x["value"] for x in evidence}=={10,169} and visible_total("169 results. 10 jobs") is None

def state_payload():
    return {"props":{"page":{"data":{"jobs":[{"id":"1","title":"Engineer","location":"Sydney","description":"Responsibilities and qualifications. "+"Build systems. "*20},{"id":"2","title":"Analyst","location":"Remote","description":"Responsibilities and qualifications. "+"Analyse systems. "*20}],"total":2}}}}

def test_nested_serialized_state_routes_to_candidate_and_plan():
    candidate=GenericApiDetector._candidate(_Observation("https://x.test/jobs","STATE",{}, {},state_payload(),"HYDRATION",False,0))
    assert candidate.source_type=="SERIALIZED_STATE" and candidate.response_shape["candidate_list_path"]=="props.page.data.jobs" and candidate.confidence=="HIGH"
    result=DiscoveryResult(source_url="https://x.test/jobs",status="DISCOVERED",candidate_list_apis=[candidate],probable_list_api=candidate,detected_pagination=PaginationDetection())
    plan=CollectionPlanBuilder().build(result)
    assert plan.mode=="SERIALIZED_STATE" and plan.executable and plan.source_index==0

class Node:
    def __init__(self,attrs):self.attrs=attrs
    def get_attribute(self,key):return self.attrs.get(key)
class Nodes:
    def __init__(self,values):self.values=values
    def count(self):return len(self.values)
    def nth(self,index):return self.values[index]
class EmbedPage:
    def locator(self,selector):
        if selector=="iframe[src]":return Nodes([Node({"src":"https://public.example/jobs"}),Node({"src":"https://tracker.example/analytics"})])
        return Nodes([Node({"src":"https://public.example/job-widget.js"})])
def test_iframe_and_embedded_widget_evidence():
    sources=embedded_sources(EmbedPage(),"https://corp.example/careers")
    assert [x.source_type for x in sources]==["IFRAME","EMBEDDED_WIDGET"] and all(x.url.startswith("https://") for x in sources)

def test_cross_source_arbitration_allows_valid_dom_to_beat_bad_api():
    weak=ApiCandidate(url="https://x.test/navigation",method="GET",score=9,confidence="LOW",response_shape={})
    result=DiscoveryResult(source_url="https://x.test/jobs",status="PARTIAL",candidate_list_apis=[weak],dom_fallback={"status":"DOM_LIST_DETECTED","possible_detail_links":["https://x.test/jobs/1","https://x.test/jobs/2"]})
    assert CollectionPlanBuilder().build(result).mode=="DOM"

class CardsPage:
    def evaluate(self,script):
        return [{"raw":"/jobs/101","text":"Engineer","title":"Engineer","location":"Sydney","department":"Engineering","cardText":"Engineer Sydney Engineering","signature":"A","region":"content"},{"raw":"/jobs/102","text":"Analyst","title":"Analyst","location":"Remote","department":"Data","cardText":"Analyst Remote Data","signature":"A","region":"content"},{"raw":"/jobs/101/locationPicker","text":"Location picker","title":"Location picker","signature":"B","region":"content"}]
def test_job_card_model_and_extra_control_removal():
    cards=repeated_job_cards(CardsPage(),"https://x.test/careers")
    assert len(cards)==2 and {x["detail_link"] for x in cards}=={"https://x.test/jobs/101","https://x.test/jobs/102"} and all(x["title"] for x in cards)

def test_partial_dom_with_confirmed_pagination_is_executable():
    result=DiscoveryResult(source_url="https://x.test/jobs",status="PARTIAL",detected_pagination=PaginationDetection(pagination_type="LOAD_MORE"),dom_fallback={"status":"DOM_LIST_DETECTED","visible_result_count":169,"possible_detail_links":[f"https://x.test/jobs/{x}" for x in range(10)]})
    plan=CollectionPlanBuilder().build(result)
    assert plan.mode=="DOM" and plan.executable and plan.pagination_type=="LOAD_MORE"

def test_pagination_semantic_controls():
    assert is_pagination_control("Next page") and is_pagination_control("Show more jobs") and is_pagination_control("Page 2")
    assert not is_pagination_control("Meet our team")

class DetailLoc:
    def __init__(self,value="",content=None):self.value=value;self.content=content;self.first=self
    def count(self):return int(bool(self.value or self.content))
    def inner_text(self):return self.value
    def get_attribute(self,key):return self.content if key in ("content","aria-label") else None
    def nth(self,index):return self
class DetailPage:
    def __init__(self):self.url=""
    def goto(self,url,**kwargs):self.url=url
    def wait_for_timeout(self,*args):pass
    def title(self):return "Careers"
    def locator(self,selector):
        if selector=="body":return DetailLoc("Job description responsibilities qualifications. "+"Build systems for customers. "*20)
        if selector=="main":return DetailLoc("Job description responsibilities qualifications. "+"Build systems for customers. "*20)
        return DetailLoc()
class DetailBrowser:
    def __init__(self):self.page=DetailPage()
    def __enter__(self):return self
    def __exit__(self,*args):pass
def test_originating_card_title_fallback():
    url="https://x.test/jobs/101";plan=CollectionPlan(source_url="https://x.test/jobs",mode="DOM",executable=True,allowed_detail_urls=[url],originating_titles={url:"Senior Engineer"})
    result=GenericDomCollector(plan,browser_factory=DetailBrowser).collect()
    assert result.total_unique==1 and result.jobs[0].job_title=="Senior Engineer"

def test_scroll_is_a_separate_phase_contract():
    assert DISCOVERY_PHASES==("INITIAL","HYDRATION","SCROLL","PAGINATION","DETAIL")
