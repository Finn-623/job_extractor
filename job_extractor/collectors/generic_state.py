from job_extractor.browser import BrowserRuntime
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.discovery.dynamic import serialized_states
from job_extractor.planning.models import CollectionPlan

class _StateResponse:
    def __init__(self,payload):self.payload=payload
    def raise_for_status(self):pass
    def json(self):return self.payload

class _StateClient:
    def __init__(self,payload):self.payload=payload
    def request(self,*args,**kwargs):return _StateResponse(self.payload)

class GenericSerializedStateCollector:
    def __init__(self,plan:CollectionPlan,browser_factory=BrowserRuntime):self.plan=plan;self.browser_factory=browser_factory
    def collect(self):
        with self.browser_factory() as runtime:
            runtime.page.goto(self.plan.source_url,wait_until="domcontentloaded")
            states=serialized_states(runtime.page);index=self.plan.source_index or 0
            if index>=len(states):raise ValueError("SERIALIZED_STATE_SOURCE_MISSING")
            http_plan=self.plan.model_copy(update={"mode":"HTTP_API","list_method":"GET","pagination_type":"SINGLE_RESPONSE","list_endpoint":self.plan.source_url})
            return GenericHttpCollector(http_plan,client=_StateClient(states[index])).collect()
