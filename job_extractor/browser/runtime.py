from __future__ import annotations
from contextlib import AbstractContextManager
from typing import Callable
from urllib.parse import urlsplit
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, Response, sync_playwright
from job_extractor.browser.models import CapturedResponse
from job_extractor.browser.network import is_api_response, safe_request_url

class BrowserRuntimeError(RuntimeError): pass

class BrowserRuntime(AbstractContextManager):
    def __init__(self, headless: bool=True, timeout_ms: int=30000,
                 playwright_factory: Callable=sync_playwright) -> None:
        self.headless=headless; self.timeout_ms=timeout_ms; self._factory=playwright_factory
        self.playwright: Playwright|None=None; self.browser: Browser|None=None
        self.context: BrowserContext|None=None; self.page: Page|None=None
        self.pages_opened=0; self.requests_observed=0
        self._observed_fragments: tuple[str,...]=()
        self._request_headers: list[tuple[str,dict[str,str]]]=[]
    def observe_only(self,*fragments: str) -> None:
        self._observed_fragments=tuple(fragments)
    def _observe(self,request) -> None:
        if not self._observed_fragments or any(part in request.url for part in self._observed_fragments):
            self.requests_observed+=1
            self._request_headers.append((request.url,dict(request.headers)))
    def session_headers(self, url:str)->dict[str,str]:
        """Return only in-memory headers organically sent to this endpoint."""
        target=urlsplit(url)
        safe_names={"authorization","x-csrf-token","x-xsrf-token","x-requested-with"}
        for request_url,headers in reversed(self._request_headers):
            observed=urlsplit(request_url)
            if (observed.scheme,observed.netloc,observed.path)==(target.scheme,target.netloc,target.path):
                return {key:value for key,value in headers.items() if key.lower() in safe_names}
        return {}
    def __enter__(self):
        try:
            self.playwright=self._factory().start()
            self.browser=self.playwright.chromium.launch(headless=self.headless)
            self.context=self.browser.new_context()
            self.page=self.context.new_page(); self.pages_opened=1
            self.page.set_default_timeout(self.timeout_ms)
            self.page.on("request",self._observe)
            return self
        except BaseException as exc:
            # STEP76A2: a launch failure must surface as a Python-level
            # BrowserRuntimeError the detector can render — never as a signal
            # (KeyboardInterrupt/SystemExit) that terminates the process.
            self.close(); raise BrowserRuntimeError("BROWSER_LAUNCH_ERROR") from exc
    def close(self):
        # STEP76A2: teardown must never propagate BaseException. A
        # KeyboardInterrupt/SystemExit arriving while the sync driver transport
        # is joining would bypass every Exception handler in the detector and
        # could terminate the parent CLI process silently.
        for value in (self.context,self.browser):
            try:
                if value: value.close()
            except BaseException: pass
        try:
            if self.playwright: self.playwright.stop()
        except BaseException: pass
        self.page=self.context=self.browser=self.playwright=None
    def __exit__(self,*_args): self.close(); return False
    @staticmethod
    def _capture(response: Response) -> CapturedResponse:
        body=response.json()
        if not isinstance(body,dict): raise BrowserRuntimeError("BROWSER_RESPONSE_NOT_OBJECT")
        request=response.request
        # Keep the full wire URL (with query) and observed headers in memory only:
        # scope-bearing functional headers (e.g. feishu website-path) would otherwise
        # be lost between capture and replay (STEP49C evidence).
        return CapturedResponse(safe_request_url(request.url),request.method,request.post_data,
                                response.status,body,request.url,dict(request.headers))
    def open_and_capture(self,url: str,fragment: str,method: str="POST") -> CapturedResponse:
        if not self.page: raise BrowserRuntimeError("BROWSER_CONTEXT_CLOSED")
        try:
            with self.page.expect_response(lambda r:is_api_response(r.url,fragment,r.request.method,method,r.status,r.headers.get("content-type","")),timeout=self.timeout_ms) as info:
                self.page.goto(url,wait_until="domcontentloaded")
            return self._capture(info.value)
        except BrowserRuntimeError: raise
        except Exception as exc: raise BrowserRuntimeError("FEISHU_LIST_REQUEST_NOT_OBSERVED") from exc
    def capture_after(self,fragment: str,method: str,action: Callable[[],None]) -> CapturedResponse:
        if not self.page: raise BrowserRuntimeError("BROWSER_CONTEXT_CLOSED")
        try:
            with self.page.expect_response(lambda r:is_api_response(r.url,fragment,r.request.method,method,r.status,r.headers.get("content-type","")),timeout=self.timeout_ms) as info: action()
            return self._capture(info.value)
        except BrowserRuntimeError: raise
        except Exception as exc: raise BrowserRuntimeError("BROWSER_RESPONSE_NOT_OBSERVED") from exc
