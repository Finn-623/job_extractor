import json

import httpx

from job_extractor.browser import BrowserRuntime
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.discovery.dynamic import embedded_states_from_html,serialized_states,window_state_blobs
from job_extractor.planning.execution_contract import PlanContractError
from job_extractor.planning.models import CollectionPlan

class _StateResponse:
    def __init__(self,payload):self.payload=payload
    def raise_for_status(self):pass
    def json(self):return self.payload
    @property
    def text(self):return self.payload if isinstance(self.payload,str) else json.dumps(self.payload)

class _StateClient:
    def __init__(self,payload):self.payload=payload
    def request(self,*args,**kwargs):return _StateResponse(self.payload)
    def get(self,*args,**kwargs):return _StateResponse(self.payload)
    def post(self,*args,**kwargs):return _StateResponse(self.payload)

class GenericSerializedStateCollector:
    def __init__(self,plan:CollectionPlan,browser_factory=BrowserRuntime,fetch_html=None):
        self.plan=plan;self.browser_factory=browser_factory;self._fetch_html=fetch_html

    def _http_states(self):
        # STEP73: plain HTTP first — fetch raw markup and extract the embedded
        # state sequence with the same extractor discovery used, so
        # plan.source_index stays aligned. No browser is started on this path;
        # any failure yields [] and the caller falls back to the browser reload.
        try:
            if self._fetch_html is not None:
                html=self._fetch_html(self.plan.source_url)
            else:
                with httpx.Client(timeout=30,follow_redirects=True,headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36","Accept-Language":"zh-CN,zh;q=0.9"}) as client:
                    response=client.get(self.plan.source_url)
                    response.raise_for_status()
                    html=response.text
        except Exception:
            return []
        try:
            return embedded_states_from_html(html or "")
        except Exception:
            return []

    def collect(self):
        states=self._http_states();index=self.plan.source_index or 0
        if not 0<=index<len(states):
            # STEP73: the plan's source_index was confirmed at discovery time.
            # If the raw-HTML fetch no longer addresses it (missing script,
            # invalid JSON, HTML-only response) the contract is broken — fail
            # closed instead of silently re-reading a different state slot.
            # Legacy plans without a source_index keep the browser reload.
            if self.plan.source_index is not None:
                raise PlanContractError("SERIALIZED_STATE_SOURCE_MISSING",execution_mode="SERIALIZED_STATE",missing_fields=["RUNTIME_SOURCE"])
            with self.browser_factory() as runtime:
                runtime.page.goto(self.plan.source_url,wait_until="domcontentloaded")
                states=serialized_states(runtime.page)
                try:
                    states=list(states)+window_state_blobs(runtime.page.content())
                except Exception:
                    pass
                if index>=len(states):raise PlanContractError("SERIALIZED_STATE_SOURCE_MISSING",execution_mode="SERIALIZED_STATE",missing_fields=["RUNTIME_SOURCE"])
        http_plan=self.plan.model_copy(update={"mode":"HTTP_API","list_method":"GET","pagination_type":"SINGLE_RESPONSE","list_endpoint":self.plan.source_url})
        return GenericHttpCollector(http_plan,client=_StateClient(states[index])).collect()
