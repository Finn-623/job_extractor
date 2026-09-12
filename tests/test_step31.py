from job_extractor.adapters import default_registry
from job_extractor.discovery.dynamic import wait_for_readiness_consensus
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.discovery.stability import stable_navigation


class PollPage:
    def __init__(self,observed):self.observed=observed;self.waits=0;self.networkidle_calls=0
    def wait_for_timeout(self,ms):
        self.waits+=1
        if self.waits==2:self.observed.append("source")
    def wait_for_load_state(self,state,timeout):self.networkidle_calls+=1
    def locator(self,selector):
        class Loc:
            def count(self):return 1
            def inner_text(self):return "ready"
        return Loc()


def test_readiness_stops_as_soon_as_source_is_observed():
    observed=[];page=PollPage(observed)
    state=wait_for_readiness_consensus(page,lambda:len(observed),timeout_ms=10000)
    assert state["source_observed"] and page.waits<10
    assert page.networkidle_calls==0


def test_initial_result_prevents_duplicate_first_discovery():
    calls=[]
    def discover(url):calls.append(url);return DiscoveryResult(source_url=url,status="BLOCKED")
    initial=DiscoveryResult(source_url="https://x.test",status="BLOCKED")
    outcome,stability=stable_navigation("https://x.test",discover,default_registry,initial_result=initial)
    assert calls==[] and stability.attempts==1 and outcome.terminal_result.status=="BLOCKED"


def test_disagreement_gets_bounded_tie_breaker():
    calls=[]
    def discover(url):calls.append(url);return DiscoveryResult(source_url=url,status="PARTIAL")
    initial=DiscoveryResult(source_url="https://x.test",status="NOT_FOUND")
    outcome,stability=stable_navigation("https://x.test",discover,default_registry,initial_result=initial)
    assert calls==["https://x.test","https://x.test"]
    assert stability.attempts==3 and stability.state=="MOSTLY_STABLE"
    assert outcome.terminal_result.status=="PARTIAL"


def test_two_matching_not_found_observations_stop_without_third_attempt():
    calls=[]
    def discover(url):calls.append(url);return DiscoveryResult(source_url=url,status="NOT_FOUND")
    initial=DiscoveryResult(source_url="https://x.test",status="NOT_FOUND")
    outcome,stability=stable_navigation("https://x.test",discover,default_registry,initial_result=initial)
    assert calls==["https://x.test"] and stability.attempts==2
    assert stability.state=="STABLE" and outcome.terminal_result.status=="NOT_FOUND"
