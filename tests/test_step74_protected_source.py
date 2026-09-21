import pytest

from job_extractor.collectors.generic_browser_api import _BrowserClient
from job_extractor.collectors.generic_http import GenericHttpCollector, protected_source_signal
from job_extractor.collectors.generic_ats import GenericATSCollector
from job_extractor.models import CollectionResult
from tests.test_generic_collectors import Client, plan, raw


class Response:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code=status; self.payload=payload; self.text=text
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(f"HTTP_{self.status_code}")
    def json(self): return self.payload


class ProtectedClient:
    def __init__(self, response): self.response=response
    def request(self, *args, **kwargs): return self.response


@pytest.mark.parametrize(("status,payload,text"),[(401,None,""),(403,None,""),(200,{"error":"no-auth"},""),(200,{"message":"illegal-visit"},"")])
def test_protected_responses_are_explicitly_classified(status,payload,text):
    result=GenericHttpCollector(plan(),ProtectedClient(Response(status,payload,text))).collect()
    assert result.status=="FAILED" and result.errors[0].startswith("PROTECTED_SOURCE")


def test_public_api_does_not_trigger_protected_flow():
    result=GenericHttpCollector(plan(),Client([{"data":{"items":[raw(1)],"total":1}}])).collect()
    assert result.status=="COMPLETE" and not any("PROTECTED_SOURCE" in error for error in result.errors)


def test_browser_session_cookie_replay_uses_same_origin_credentials():
    class Page:
        def __init__(self): self.argument=None; self.script=""
        def evaluate(self, script, argument): self.script=script; self.argument=argument; return {"status":200,"payload":{"data":{"items":[],"total":0}}}
    page=Page(); _BrowserClient(page).request("GET","https://x.test/api")
    assert page.argument["headers"]=={} and "credentials:'same-origin'" in page.script


def test_browser_session_replay_preserves_observed_header_without_generation():
    class Page:
        def evaluate(self, script, argument): self.argument=argument; return {"status":200,"payload":{}}
    page=Page(); _BrowserClient(page,headers={"authorization":"Bearer observed"}).request("GET","https://x.test/api")
    assert page.argument["headers"]=={"authorization":"Bearer observed"}


def test_browser_replay_forbidden_remains_protected():
    class Page:
        def evaluate(self, *args): return {"status":403,"payload":{"message":"forbidden"}}
    response=_BrowserClient(Page()).request("GET","https://x.test/api")
    assert protected_source_signal(response.status_code,response.payload)=="HTTP_403"


def test_protected_http_dispatches_to_browser_session_replay(monkeypatch):
    class Direct:
        def __init__(self, *args, **kwargs): pass
        def collect(self): return CollectionResult(source_url="https://x.test/jobs",status="FAILED",errors=["PROTECTED_SOURCE reason=no-auth"])
    class Browser:
        def __init__(self, *args, **kwargs): pass
        def collect(self): return CollectionResult(source_url="https://x.test/jobs",status="COMPLETE")
    monkeypatch.setattr("job_extractor.collectors.generic_ats.GenericHttpCollector", Direct)
    monkeypatch.setattr("job_extractor.collectors.generic_ats.GenericBrowserApiCollector", Browser)
    assert GenericATSCollector(plan()).collect().status=="COMPLETE"
