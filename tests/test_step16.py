import json

from job_extractor.collectors.generic_browser_api import _BrowserClient
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.discovery.detector import GenericApiDetector,_Observation
from job_extractor.discovery.dom_semantics import filter_detail_links,route_changed
from job_extractor.discovery.dynamic import (DYNAMIC_FAILURE_REASONS,graphql_next_values,graphql_shape,navigation_trust,
    pagination_stop,serialized_states,visible_total,wait_for_dynamic_jd,wait_for_hydration,wait_for_readiness_consensus)
from job_extractor.planning import CollectionPlan

def job(i):return {"id":str(i),"title":f"Engineer {i}","location":"Sydney","description":"Job description responsibilities qualifications. "+"Build reliable systems. "*8}

def test_graphql_edges_nodes_and_page_info():
    body={"operationName":"Jobs","query":"query Jobs($after:String){jobs{edges{node{id title}} pageInfo{hasNextPage endCursor}}}","variables":{"after":None,"first":2}}
    payload={"data":{"jobs":{"edges":[{"node":job(1)},{"node":job(2)}],"pageInfo":{"hasNextPage":True,"endCursor":"c2"}}}}
    shape=graphql_shape(body,payload)
    assert shape["candidate_list_path"]=="data.jobs.edges" and shape["list_item_path"]=="node"
    assert shape["pagination_type"]=="GRAPHQL_CURSOR" and shape["has_more_field"]=="data.jobs.pageInfo.hasNextPage"
    candidate=GenericApiDetector._candidate(_Observation("https://x.test/graphql","POST",body,{},payload,"hydration"))
    assert candidate.graphql_operation=="Jobs" and candidate.observed_list_length==2 and candidate.response_shape["sample_field_names"]

def test_graphql_cursor_offset_and_page_values():
    base={"variables":{"after":None,"offset":0,"page":1,"limit":10}}
    assert graphql_next_values(base,"GRAPHQL_CURSOR","variables.after","variables.limit","c2",2)["variables"]["after"]=="c2"
    assert graphql_next_values(base,"GRAPHQL_OFFSET","variables.offset","variables.limit",None,2)["variables"]["offset"]==10
    assert graphql_next_values(base,"GRAPHQL_PAGE","variables.page","variables.limit",None,2)["variables"]["page"]==2

class Loc:
    def __init__(self,page,selector):self.page=page;self.selector=selector;self.first=self
    def count(self):return 1
    def nth(self,index):return self
    def inner_text(self):return self.page.text()
    def text_content(self):return self.page.script
class HydrationPage:
    def __init__(self):self.tick=0;self.script=json.dumps({"props":{"jobs":[job(1),job(2)]}})
    def locator(self,selector):return Loc(self,selector)
    def text(self):return "short" if self.tick<2 else "Job description responsibilities qualifications. "+"Build systems. "*30
    def wait_for_timeout(self,ms):self.tick+=1

def test_post_hydration_and_serialized_state_extraction():
    page=HydrationPage();state=wait_for_hydration(page,lambda:1,timeout_ms=2000)
    assert state["stabilized"] and serialized_states(page)[0]["props"]["jobs"][0]["id"]=="1"

def test_readiness_consensus_never_requires_networkidle():
    observed=["decision-evidence"]
    class Page(HydrationPage):
        def wait_for_load_state(self,state,timeout):
            assert state=="networkidle" and timeout<=1000
            raise AssertionError("networkidle must not be called")
    state=wait_for_readiness_consensus(Page(),lambda:len(observed))
    assert not state["network_idle"]
    assert state["candidate_count_before"]==state["candidate_count_after"]==1

def test_readiness_consensus_does_not_treat_early_networkidle_as_final():
    observed=[]
    class Page(HydrationPage):
        def wait_for_load_state(self,state,timeout):pass
        def wait_for_timeout(self,ms):
            super().wait_for_timeout(ms)
            if self.tick==3:observed.append("delayed-source")
    state=wait_for_readiness_consensus(Page(),lambda:len(observed),timeout_ms=2000)
    assert not state["network_idle"] and state["source_observed"]

def test_dynamic_jd_wait_and_spa_route_change():
    page=HydrationPage();jd,selector,state=wait_for_dynamic_jd(page,"Engineer",timeout_ms=2000)
    assert jd and state["resolved"] and route_changed("https://x/jobs","https://x/jobs/1")

def test_job_card_link_correlation_rejects_standalone_uuid():
    links=[("https://x.test/support/123e4567-e89b-12d3-a456-426614174000","Support"),("/jobs/12345","Engineer")]
    accepted,rejected=filter_detail_links(links,"https://x.test/jobs",["https://x.test/jobs/12345"])
    assert accepted==["https://x.test/jobs/12345"] and rejected[0]["reason"]=="LOW_DETAIL_LINK_EVIDENCE"

def test_visible_total_variants():
    assert visible_total("Showing 1–5 of 140")==140
    assert visible_total("243 open positions")==243
    assert visible_total("14 results")==14

def test_pagination_stop_conditions_for_load_more_and_infinite_scroll():
    assert pagination_stop(official_total=10,unique_count=10,previous_unique=8,has_more=True)=="OFFICIAL_TOTAL_REACHED"
    assert pagination_stop(official_total=None,unique_count=8,previous_unique=8,has_more=True)=="NO_NEW_UNIQUE_JOBS"
    assert pagination_stop(official_total=None,unique_count=8,previous_unique=7,has_more=False)=="HAS_MORE_FALSE"

def test_graphql_cursor_execution():
    class Response:
        def __init__(self,p):self.p=p
        def raise_for_status(self):pass
        def json(self):return self.p
    class Client:
        def __init__(self):self.calls=[]
        def request(self,*args,**kwargs):
            self.calls.append(kwargs["json"]);after=kwargs["json"]["variables"]["after"]
            return Response({"data":{"jobs":{"edges":[{"node":job(1 if after is None else 2)}],"pageInfo":{"hasNextPage":after is None,"endCursor":"c2" if after is None else None},"total":2}}})
    plan=CollectionPlan(source_url="https://x.test/jobs",mode="BROWSER_API",executable=True,list_endpoint="https://x.test/graphql",list_method="POST",pagination_type="GRAPHQL_CURSOR",page_param="variables.after",page_size_param="variables.first",next_cursor_field="data.jobs.pageInfo.endCursor",has_more_field="data.jobs.pageInfo.hasNextPage",initial_values={"operationName":"Jobs","query":"query Jobs{}","variables":{"after":None,"first":1}},list_path="data.jobs.edges",list_item_path="node",job_id_field="id",job_title_field="title",total_field="data.jobs.total",detail_mode="LIST_SUFFICIENT",observed_endpoints=["https://x.test/graphql"])
    client=Client();result=GenericHttpCollector(plan,client).collect()
    assert result.status=="COMPLETE" and result.total_unique==2 and client.calls[1]["variables"]["after"]=="c2"

def test_browser_api_replay_uses_page_context_without_headers():
    class Page:
        def evaluate(self,script,arg):self.arg=arg;return {"items":[]}
    page=Page();response=_BrowserClient(page).request("POST","https://x.test/graphql",json={"query":"{}"})
    assert response.json()=={"items":[]} and page.arg=={"url":"https://x.test/graphql","method":"POST","body":{"query":"{}"},"headers":{}}

def test_redirect_chain_trust():
    assert navigation_trust("jobs.example","apply.example","apply.example",1,True)
    assert not navigation_trust("jobs.example","apply.example","evil.example",1,True)
    assert not navigation_trust("jobs.example","apply.example","apply.example",4,True)

def test_dynamic_failure_reason_contract():
    assert set(DYNAMIC_FAILURE_REASONS)=={"HYDRATION_TIMEOUT","NO_DYNAMIC_JOB_SOURCE","GRAPHQL_PAGINATION_UNKNOWN","SPA_DETAIL_NOT_RESOLVED","JD_RENDER_TIMEOUT","JOB_CARD_LINK_MISMATCH","BROWSER_API_NOT_REPLAYABLE"}
