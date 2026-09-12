import pytest
from job_extractor.browser.network import is_api_response, safe_request_url
from job_extractor.browser.runtime import BrowserRuntime, BrowserRuntimeError

def test_network_filter(): assert is_api_response("https://x/api/jobs?s=secret","/api/jobs","POST","POST",200,"application/json")
def test_network_filter_rejects_method(): assert not is_api_response("https://x/api/jobs","/api/jobs","GET","POST",200,"application/json")
def test_network_filter_rejects_failure(): assert not is_api_response("https://x/api/jobs","/api/jobs","POST","POST",405,"application/json")
def test_safe_url_removes_query(): assert safe_request_url("https://x/api?a=secret")=="https://x/api"
def test_closed_runtime_rejects_capture():
    with pytest.raises(BrowserRuntimeError): BrowserRuntime().open_and_capture("https://x","/api")
def test_launch_failure_is_sanitized():
    class Bad:
        def start(self): raise RuntimeError("token=secret")
    with pytest.raises(BrowserRuntimeError,match="BROWSER_LAUNCH_ERROR"): BrowserRuntime(playwright_factory=lambda:Bad()).__enter__()
