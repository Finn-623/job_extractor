from __future__ import annotations
from contextlib import AbstractContextManager
from typing import Callable
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
    def observe_only(self,*fragments: str) -> None:
        self._observed_fragments=tuple(fragments)
    def _observe(self,request) -> None:
        if not self._observed_fragments or any(part in request.url for part in self._observed_fragments):
            self.requests_observed+=1
    def __enter__(self):
        try:
            self.playwright=self._factory().start()
            self.browser=self.playwright.chromium.launch(headless=self.headless)
            self.context=self.browser.new_context()
            self.page=self.context.new_page(); self.pages_opened=1
            self.page.set_default_timeout(self.timeout_ms)
            self.page.on("request",self._observe)
            return self
        except Exception as exc:
            self.close(); raise BrowserRuntimeError("BROWSER_LAUNCH_ERROR") from exc
    def close(self):
        for value in (self.context,self.browser):
            try:
                if value: value.close()
            except Exception: pass
        try:
            if self.playwright: self.playwright.stop()
        except Exception: pass
        self.page=self.context=self.browser=self.playwright=None
    def __exit__(self,*_args): self.close(); return False
    @staticmethod
    def _capture(response: Response) -> CapturedResponse:
        body=response.json()
        if not isinstance(body,dict): raise BrowserRuntimeError("BROWSER_RESPONSE_NOT_OBJECT")
        request=response.request
        return CapturedResponse(safe_request_url(request.url),request.method,None,
                                response.status,body)
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
