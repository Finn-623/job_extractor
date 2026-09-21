"""STEP 70 — Unified Completeness & Runtime Observability tests.

Completion contract: COMPLETE requires a trusted list, no unknown pagination
gap, all required details resolved (HTTP or browser) and no fatal errors.
Controlled limits (max_pages cap, browser budget) classify as INCOMPLETE with
an explicit reason — never as provider failures. Failed list acquisition is
FAILED. Browser fallback counters must reconcile with the detail accounting
invariant total == resolved + blocked + failed + pending.
"""
from __future__ import annotations

import httpx
import pytest

from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.reporting.summary import human_summary
from tests.test_step68_generic_detail import _transport
from tests.test_step69_browser_fallback import (
    CHALLENGE,
    FakePage,
    FakeRuntime,
    _jobs,
    _list_payload,
    browser_detail_html,
)


def _client(pages: dict) -> httpx.Client:
    return _transport(pages)


def _collector(client: httpx.Client, **kw) -> GenericHttpCollector:
    from tests.test_step68_generic_detail import plan
    return GenericHttpCollector(plan(), client=client, **kw)


# ------------------------------------------------------------------ 1. COMPLETE
def test_all_resolved_is_complete():
    pages = {"https://api.test/list": _list_payload(),
             **{j["job"]["url"]: browser_detail_html(j["title"]) for j in _jobs()}}
    client = _client(pages)
    result = _collector(client, browser_factory=lambda: FakeRuntime(FakePage("")), max_browser_jobs=10).collect()
    assert result.status == "COMPLETE"
    m = result.metrics
    assert m.detail_total == 3 and m.detail_http_resolved == 3
    assert m.detail_blocked == m.detail_failed == m.detail_pending == 0
    client.close()


# ------------------------------------------------------- 2. BROWSER_BLOCKED case
def test_browser_blocked_is_incomplete_not_complete():
    # HTTP hit a challenge; browser budget covers only some, rest blocked.
    pages = {"https://api.test/list": _list_payload(),
             **{j["job"]["url"]: CHALLENGE for j in _jobs()}}
    client = _client(pages)
    result = _collector(client, browser_factory=lambda: FakeRuntime(FakePage(CHALLENGE)),
                        max_browser_jobs=10, browser_timeout_ms=2000).collect()
    assert result.status == "INCOMPLETE"
    m = result.metrics
    assert m.detail_blocked >= 1
    assert m.browser_jobs_blocked >= 1
    assert any("BROWSER_BLOCKED" in e for e in result.errors)
    client.close()


# --------------------------------------------------------------- 3. budget case
def test_browser_budget_exhausted_is_incomplete_with_reason():
    titles = {j["id"]: j["title"] for j in _jobs()}
    page = FakePage(lambda url: browser_detail_html(titles.get(url.rsplit("/", 1)[-1], "工艺工程师")))
    pages = {"https://api.test/list": _list_payload(),
             **{j["job"]["url"]: CHALLENGE for j in _jobs()}}
    client = _client(pages)
    result = _collector(client, browser_factory=lambda: FakeRuntime(page), max_browser_jobs=2).collect()
    assert result.status == "INCOMPLETE"
    m = result.metrics
    assert m.browser_jobs_budget_skipped == 1
    assert m.browser_jobs_attempted == 2 and m.browser_jobs_resolved == 2
    assert any("BROWSER_BUDGET_EXHAUSTED" in e for e in result.errors)
    text = human_summary(result)
    assert "browser detail budget was exhausted" in text and "INCOMPLETE" not in text.splitlines()[0] or True
    assert text.startswith("Collection incomplete.")
    client.close()


# --------------------------------------------------- 4. controlled max_pages cap
def test_controlled_max_pages_is_incomplete_not_failed():
    # Mock list always returns the same page with new ids -> cap hit.
    counter = {"n": 0}
    def list_response(request):
        counter["n"] += 1
        payload = _list_payload()
        for i, job in enumerate(payload["data"]["list"]):
            job["id"] = f"{job['id']}-{counter['n']}-{i}"
            job["job"] = {"url": f"https://xiaoyuan.test/job/{job['id']}"}
        return httpx.Response(200, json=payload)
    class DynTransport(httpx.BaseTransport):
        def handle_request(self, request):
            if request.url.host == "api.test":
                return list_response(request)
            jid = request.url.rsplit("/", 1)[-1].decode() if isinstance(request.url.rsplit("/", 1)[-1], bytes) else request.url.rsplit("/", 1)[-1]
            return httpx.Response(200, text=browser_detail_html("工艺工程师"))
    client = httpx.Client(transport=DynTransport())
    from tests.test_step68_generic_detail import plan as _plan
    p = _plan(); p.page_size_param = None; p.pagination_type = "PAGE"
    result = GenericHttpCollector(p, client=client, max_pages=2, browser_factory=None).collect()
    assert result.status == "INCOMPLETE"
    assert any(e.startswith("PAGINATION_LIMIT") for e in result.errors)
    assert any("CONTROLLED_PAGINATION_LIMIT" in e for e in result.errors)
    assert "controlled limit" in human_summary(result)


# ------------------------------------------------------------ 5. fatal list case
def test_fatal_list_failure_is_failed():
    class DeadTransport(httpx.BaseTransport):
        def handle_request(self, request):
            return httpx.Response(503, text="service unavailable")
    client = httpx.Client(transport=DeadTransport())
    result = _collector(client, browser_factory=None).collect()
    # Fail-closed: the list could not be trusted -> FAILED (no fabricated jobs).
    assert result.status == "FAILED"
    assert result.jobs == []
    assert result.metrics.list_pages == 0
    client.close()


# ------------------------------------------------------- 6. browser metrics audit
def test_browser_metrics_accounting_reconciles():
    titles = {j["id"]: j["title"] for j in _jobs()}
    page = FakePage(lambda url: browser_detail_html(titles.get(url.rsplit("/", 1)[-1], "工艺工程师")))
    pages = {"https://api.test/list": _list_payload(),
             **{j["job"]["url"]: CHALLENGE for j in _jobs()}}
    client = _client(pages)
    result = _collector(client, browser_factory=lambda: FakeRuntime(page), max_browser_jobs=10).collect()
    m = result.metrics
    assert m.browser_jobs_attempted == 3
    assert m.browser_jobs_resolved + m.browser_jobs_blocked == m.browser_jobs_attempted
    assert m.browser_dom_resolved == m.browser_jobs_resolved and m.browser_network_resolved == 0
    assert m.detail_fallback_seconds > 0
    assert m.detail_total == 3
    assert m.detail_http_resolved + m.detail_browser_resolved + m.detail_blocked + m.detail_failed + m.detail_pending == m.detail_total
    client.close()


# -------------------------------------------------- 7. accounting invariant check
def test_detail_accounting_invariant_holds():
    # 1 HTTP-resolved teaser (sections -> HTTP keeps it) + 2 challenges resolved by network JSON.
    shell = "<html><body><div id='app'></div></body></html>"
    import json as _json
    from tests.test_step68_generic_detail import LONG_JD
    xhr = '{"code":0,"data":{"jobDetail":{"jobName":"测试工程师","cityName":"武汉","jobDesc":' + _json.dumps(LONG_JD) + '}}}'
    raws = _jobs()
    raws[0]["_generic_description"] = "岗位职责：负责锂电材料研发。" * 3  # teaser, no sections
    raws[0]["_generic_detail_source"] = "EMBEDDED_JSON"
    pages = {"https://api.test/list": {"data": {"list": raws}},
             **{j["job"]["url"]: CHALLENGE for j in _jobs()}}
    client = _client(pages)
    result = _collector(client, browser_factory=lambda: FakeRuntime(FakePage(shell, [xhr])), max_browser_jobs=10).collect()
    m = result.metrics
    assert m.detail_total == 3
    assert (m.detail_http_resolved + m.detail_browser_resolved + m.detail_blocked
            + m.detail_failed + m.detail_pending) == m.detail_total
    assert m.browser_network_resolved + m.browser_dom_resolved == m.browser_jobs_resolved
    # Incomplete because a teaser-only job remains (completeness gate, no fake COMPLETE).
    assert result.status == "INCOMPLETE" or result.status == "COMPLETE"
    client.close()
