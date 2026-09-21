"""STEP 69 — Generic Browser Detail Fallback V1 tests.

HTTP-first stays the default: the browser only opens pages the HTTP detail
resolver could not complete (challenge / teaser shell / parser failure).
"""
from __future__ import annotations

import httpx
import pytest

from job_extractor.browser import BrowserRuntime
from job_extractor.collectors.generic_detail import (
    GenericBrowserDetailFallback,
    GenericHtmlDetailCollector,
    network_json_detail,
)
from job_extractor.collectors.generic_http import GenericHttpCollector
from tests.test_step68_generic_detail import LONG_JD, _transport, plan

CHALLENGE = '<html><body><script>document.cookie="EO_Bot_Ssid=abc";challenge();</script></body></html>'
TEASER = "岗位职责：负责锂电材料研发。"


def _jobs(host="xiaoyuan.test"):
    """Three list records whose observed detail URLs live on one host."""
    return [{"id": jid, "title": title, "job": {"url": f"https://{host}/job/{jid}"}}
            for jid, title in (("101", "工艺工程师"), ("102", "测试工程师"), ("103", "质量工程师"))]


def browser_detail_html(title="工艺工程师"):
    """Fully rendered detail page: >500 non-ws chars, resp/req headings split."""
    duties = "".join(f"<p>负责岗位{i}号锂电池新材料体系的配方设计与验证工作，主导中试放大与量产导入，"
                     f"跟踪行业前沿技术动态并输出技术路线建议{i}。</p>" for i in range(1, 5))
    reqs = "".join(f"<p>任职要求说明{i}：硕士及以上学历，材料、化学或电化学相关专业，"
                   f"具备三年以上锂离子电池研发经验，熟悉正负极材料体系与电芯工艺。</p>" for i in range(1, 4))
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1>" \
           f"<div class='job-detail'><h3>岗位职责</h3>{duties}<h3>任职要求</h3>{reqs}</div></body></html>"


def _list_payload():
    return {"data": {"list": _jobs()}}


def _pages_for(challenge: bool):
    return {"https://api.test/list": _list_payload(),
            **{j["job"]["url"]: (CHALLENGE if challenge else browser_detail_html(j["title"])) for j in _jobs()}}


class FakePage:
    """Minimal sync-Playwright page double: scripted DOM content + JSON XHRs."""
    def __init__(self, html, xhr_bodies: list[str] | None = None, goto_error: Exception | None = None):
        self._html = html
        self.xhr_bodies = xhr_bodies or []
        self.goto_error = goto_error
        self.navigations = 0
        self.default_timeout = None
        self._listeners: list = []
        self.wait_calls = 0
        self._current = ""

    def set_default_timeout(self, ms): self.default_timeout = ms
    def on(self, event, handler): self._listeners.append(handler)
    def remove_listener(self, event, handler):
        if handler in self._listeners: self._listeners.remove(handler)
    def wait_for_timeout(self, ms): self.wait_calls += 1
    def content(self): return self._current
    def goto(self, url, **kw):
        self.navigations += 1
        if self.goto_error: raise self.goto_error
        self._current = self._html(url) if callable(self._html) else self._html
        for handler in list(self._listeners):
            for body in self.xhr_bodies:
                handler(FakeResponse(body))


class FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self.status = status
        self.headers = {"content-type": "application/json"}
        self._body = body
    def text(self): return self._body


class FakeRuntime:
    """Context-manager double of BrowserRuntime sharing one scripted page."""
    sessions = 0
    def __init__(self, page: FakePage):
        self.page = page
    def __enter__(self):
        FakeRuntime.sessions += 1
        return self
    def __exit__(self, *args): return False
    def close(self): pass


# --------------------------------------------------------------------------- J1
def test_http_success_never_opens_browser():
    client = _transport(_pages_for(challenge=False))
    raws = _jobs()
    raws, ok, failures = GenericHtmlDetailCollector(plan(), client, navigation_attempts=1).enrich(raws, ("id",), ("title",))
    assert ok == 3 and not failures
    launched = []
    fb = GenericBrowserDetailFallback(plan(), browser_factory=lambda: (launched.append(1), FakeRuntime(FakePage("")))[1], max_browser_jobs=10)
    raws2, resolved, blocked, resolved_ids = fb.enrich(raws, failures, ("id",), ("title",))
    # No teaser bodies, no failures -> zero browser targets, zero navigations
    assert not launched and fb.navigations_used == 0 and resolved == 0 and not blocked and not resolved_ids
    client.close()


# --------------------------------------------------------------------------- J2
def test_bot_challenge_triggers_browser_fallback():
    page = FakePage(lambda url: browser_detail_html(dict((("101", "工艺工程师"), ("102", "测试工程师"), ("103", "质量工程师"))).get(url.rsplit("/", 1)[-1], "工艺工程师")))
    fb = GenericBrowserDetailFallback(plan(), browser_factory=lambda: FakeRuntime(page), max_browser_jobs=10)
    failures = [("BOT_CHALLENGE", "101"), ("BOT_CHALLENGE", "102")]
    out, resolved, blocked, resolved_ids = fb.enrich(_jobs(), failures, ("id",), ("title",))
    assert resolved == 2 and not blocked
    assert fb.navigations_used == 2  # budget: exactly one navigation per job
    assert sorted(str(x) for x in resolved_ids) == ["101", "102"]
    assert out[0]["_generic_description"] and out[0]["_generic_detail_source"] == "BROWSER_DOM"
    assert out[0]["_generic_responsibilities"] and out[0]["_generic_requirements"]


# --------------------------------------------------------------------------- J3
def test_rendered_dom_success_from_teaser_shell():
    # HTTP "resolved" a teaser-only body -> browser fallback target; full JD renders.
    raws = _jobs()
    raws[0]["_generic_description"] = TEASER
    raws[0]["_generic_detail_source"] = "EMBEDDED_JSON"
    fb = GenericBrowserDetailFallback(plan(), browser_factory=lambda: FakeRuntime(FakePage(browser_detail_html())), max_browser_jobs=10)
    out, resolved, blocked, resolved_ids = fb.enrich(raws, [], ("id",), ("title",))
    assert resolved == 1 and fb.navigations_used == 1
    assert len(out[0]["_generic_description"]) > len(TEASER)
    assert out[0]["_generic_detail_source"] == "BROWSER_DOM"
    assert out[0]["_generic_responsibilities"] and out[0]["_generic_requirements"]


# --------------------------------------------------------------------------- J4
def test_browser_network_json_used_when_dom_incomplete():
    shell = "<html><body><div id='app'></div></body></html>"  # no semantic JD in DOM
    xhr = '{"code":0,"data":{"jobDetail":{"jobName":"工艺工程师","cityName":"武汉",' \
          '"jobDesc":' + __import__("json").dumps(LONG_JD) + '}}}'
    fb = GenericBrowserDetailFallback(plan(), browser_factory=lambda: FakeRuntime(FakePage(shell, [xhr])), max_browser_jobs=10)
    out, resolved, blocked, resolved_ids = fb.enrich(_jobs()[:1], [("BOT_CHALLENGE", "101")], ("id",), ("title",))
    assert resolved == 1 and not blocked
    assert out[0]["_generic_detail_source"] == "BROWSER_NETWORK_JSON"
    assert out[0]["_generic_responsibilities"] and out[0]["_generic_requirements"]


def test_network_json_detail_direct():
    import json as _json
    body = _json.dumps({"jobName": "工艺工程师", "jobDesc": LONG_JD})
    found = network_json_detail(body, "工艺工程师")
    assert found and found["source_type"] == "BROWSER_NETWORK_JSON"
    assert network_json_detail("not json", None) is None


# --------------------------------------------------------------------------- J5
def test_browser_blocked_fails_closed():
    raws = _jobs()
    fb = GenericBrowserDetailFallback(plan(), browser_factory=lambda: FakeRuntime(
        FakePage("<html></html>", goto_error=RuntimeError("navigation blocked"))), max_browser_jobs=10)
    out, resolved, blocked, resolved_ids = fb.enrich(raws, [("BOT_CHALLENGE", "101")], ("id",), ("title",))
    assert resolved == 0 and not resolved_ids
    assert blocked and blocked[0][0] == "BROWSER_BLOCKED"
    assert not out[0].get("_generic_description")


def test_browser_challenge_page_never_parsed_as_jd():
    # Rendered page stays a challenge page the whole window -> fail closed, no fabrication.
    fb = GenericBrowserDetailFallback(plan(), browser_factory=lambda: FakeRuntime(FakePage(CHALLENGE)), max_browser_jobs=10, hydration_ms=600)
    out, resolved, blocked, resolved_ids = fb.enrich(_jobs()[:1], [("BOT_CHALLENGE", "101")], ("id",), ("title",))
    assert resolved == 0 and blocked and blocked[0][0] == "BROWSER_BLOCKED"


def test_session_level_failure_fails_closed_all():
    class BoomRuntime(FakeRuntime):
        def __enter__(self): raise RuntimeError("launch failed")
    fb = GenericBrowserDetailFallback(plan(), browser_factory=BoomRuntime, max_browser_jobs=10)
    out, resolved, blocked, resolved_ids = fb.enrich(_jobs()[:2], [("BOT_CHALLENGE", "101"), ("BOT_CHALLENGE", "102")], ("id",), ("title",))
    assert resolved == 0 and len(blocked) == 2 and all(code == "BROWSER_BLOCKED" for code, _ in blocked)


# --------------------------------------------------------------------------- J6
def test_browser_budget_guard_caps_jobs():
    titles = {"101": "工艺工程师", "102": "测试工程师", "103": "质量工程师"}
    page = FakePage(lambda url: browser_detail_html(titles.get(url.rsplit("/", 1)[-1], "工艺工程师")))
    fb = GenericBrowserDetailFallback(plan(), browser_factory=lambda: FakeRuntime(page), max_browser_jobs=2)
    raws = _jobs()  # 3 jobs, all challenged
    failures = [("BOT_CHALLENGE", "101"), ("BOT_CHALLENGE", "102"), ("BOT_CHALLENGE", "103")]
    out, resolved, blocked, resolved_ids = fb.enrich(raws, failures, ("id",), ("title",))
    assert fb.navigations_used == 2 and resolved == 2
    # The unbudgeted job is never browser-attempted and never upgraded.
    assert blocked == [] and resolved_ids and len(resolved_ids) == 2
    assert not out[2].get("_generic_description")


def test_collector_wiring_http_success_no_browser(monkeypatch):
    # End-to-end: HTTP resolves everything -> browser factory must never launch.
    client = _transport(_pages_for(challenge=False))
    launched = []
    monkeypatch.setattr(BrowserRuntime, "__enter__", lambda self: launched.append(1) or self)
    collector = GenericHttpCollector(plan(), client=client, browser_factory=BrowserRuntime)
    result = collector.collect()
    assert not launched
    assert result.status in ("COMPLETE", "INCOMPLETE")
    assert all(j.full_jd for j in result.jobs)
    client.close()


def test_collector_wiring_challenge_fallback_resolves():
    client = _transport(_pages_for(challenge=True))
    titles = {"101": "工艺工程师", "102": "测试工程师", "103": "质量工程师"}
    page = FakePage(lambda url: browser_detail_html(titles.get(url.rsplit("/", 1)[-1], "工艺工程师")))
    collector = GenericHttpCollector(plan(), client=client,
                                     browser_factory=lambda: FakeRuntime(page), max_browser_jobs=10, browser_timeout_ms=5000)
    result = collector.collect()
    assert len(result.jobs) == 3
    assert all(j.full_jd and "锂电池新材料" in j.full_jd for j in result.jobs)
    assert all(j.detail_url and j.detail_url.startswith("https://xiaoyuan.test/job/") for j in result.jobs)
    client.close()
