import json
import threading
import time

from job_extractor.adapters.feishu import FeishuAdapter
from job_extractor.browser.models import CapturedResponse
from job_extractor.collectors.generic_browser_api import GenericBrowserApiCollector
from job_extractor.collectors.generic_detail import GenericHtmlDetailCollector
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.planning import CollectionPlan


def raw(index):
    return {"id": str(index), "title": f"Job {index}", "location": "Sydney", "description": "Build systems", "requirements": "Python"}


def payload(items, total):
    return {"data": {"items": items, "total": total}}


def plan(kind="OFFSET", **changes):
    values = dict(source_url="https://jobs.test/list", mode="HTTP_API", executable=True,
        list_endpoint="https://jobs.test/api/positions", list_method="GET", pagination_type=kind,
        offset_param="offset" if kind == "OFFSET" else None, page_param="page" if kind == "PAGE" else None,
        page_size_param="limit", initial_values={"offset": 0, "limit": 10} if kind == "OFFSET" else {"page": 1, "limit": 10},
        list_path="data.items", total_field="data.total", job_id_field="id", job_title_field="title",
        detail_mode="LIST_SUFFICIENT", observed_endpoints=["https://jobs.test/api/positions"])
    values.update(changes)
    return CollectionPlan(**values)


class Response:
    def __init__(self, value): self.value = value
    def raise_for_status(self): pass
    def json(self): return self.value


class SequenceClient:
    def __init__(self, values): self.values = list(values); self.calls = []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = self.values.pop(0)
        if isinstance(value, Exception): raise value
        return Response(value)


def test_offset_direct_replay_reaches_total():
    client = SequenceClient([payload([raw(i) for i in range(0, 10)], 25), payload([raw(i) for i in range(10, 20)], 25), payload([raw(i) for i in range(20, 25)], 25)])
    result = GenericHttpCollector(plan(), client).collect()
    assert result.status == "COMPLETE" and result.total_unique == 25
    assert [call[2]["params"]["offset"] for call in client.calls] == [0, 10, 20]
    assert result.metrics.termination_reason == "TOTAL_REACHED"


def test_page_direct_replay_advances_page_number():
    client = SequenceClient([payload([raw(1)], 3), payload([raw(2)], 3), payload([raw(3)], 3)])
    result = GenericHttpCollector(plan("PAGE", page_size_param=None, initial_values={"page": 1}), client).collect()
    assert result.status == "COMPLETE"
    assert [call[2]["params"]["page"] for call in client.calls] == [1, 2, 3]


def test_repeated_page_stops_with_duplicate_progress_error():
    records = [raw(i) for i in range(10)]
    result = GenericHttpCollector(plan(), SequenceClient([payload(records, 20), payload(records, 20)])).collect()
    assert result.status == "INCOMPLETE" and result.total_fetched == 20 and result.total_unique == 10
    assert result.metrics.duplicate_jobs == 10 and result.metrics.termination_reason == "NO_PROGRESS"
    assert any("PAGINATION_NO_PROGRESS" in error for error in result.errors)


def test_transient_timeout_retries_once_then_continues():
    client = SequenceClient([payload([raw(i) for i in range(10)], 20), TimeoutError(), payload([raw(i) for i in range(10, 20)], 20)])
    result = GenericHttpCollector(plan(), client, sleep_fn=lambda _delay: None).collect()
    assert result.status == "COMPLETE" and result.total_unique == 20
    assert result.metrics.retry_count == 1 and result.metrics.pages_requested == 3


def test_repeated_timeout_keeps_first_page_and_is_incomplete():
    client = SequenceClient([payload([raw(i) for i in range(10)], 20), TimeoutError(), TimeoutError(), TimeoutError()])
    result = GenericHttpCollector(plan(), client, sleep_fn=lambda _delay: None).collect()
    assert result.status == "INCOMPLETE" and result.total_unique == 10
    assert result.metrics.retry_count == 2 and result.metrics.termination_reason == "REQUEST_FAILED"
    assert any("PAGE_REQUEST_RETRIES_EXHAUSTED" in error for error in result.errors)


class BrowserPage:
    def __init__(self): self.url = ""; self.offsets = []; self.ui_accessed = False
    def goto(self, url, **_kwargs): self.url = url
    def evaluate(self, _script, arg):
        body = arg["body"] if "body" in arg else arg
        offset = body["offset"]; self.offsets.append(offset)
        end = min(offset + body["limit"], 25)
        return {"status": 200, "payload": {"data": {"job_post_list": [raw(i) for i in range(offset, end)], "count": 25}}}
    def locator(self, _selector): self.ui_accessed = True; raise AssertionError("UI pagination must not be used")


class BrowserRuntime:
    def __init__(self): self.page = BrowserPage(); self.pages_opened = 1; self.requests_observed = 0
    def __enter__(self): return self
    def __exit__(self, *_args): return False


def browser_plan():
    return CollectionPlan(source_url="https://jobs.test/campus", mode="BROWSER_API", executable=True,
        browser_trigger="AUTO_PAGINATION", list_endpoint="https://jobs.test/api/v1/search/job/posts", list_method="POST",
        pagination_type="OFFSET", offset_param="offset", page_param="offset", page_size_param="limit",
        initial_values={"offset": 0, "limit": 10, "portal_type": 6}, list_path="data.job_post_list",
        total_field="data.count", job_id_field="id", job_title_field="title", detail_mode="LIST_SUFFICIENT",
        observed_endpoints=["https://jobs.test/api/v1/search/job/posts"])


def test_browser_session_replay_uses_offset_without_ui_click():
    runtime = BrowserRuntime()
    result = GenericBrowserApiCollector(browser_plan(), browser_factory=lambda: runtime).collect()
    assert result.status == "COMPLETE" and result.total_unique == 25
    assert runtime.page.offsets == [0, 10, 20] and not runtime.page.ui_accessed
    assert result.metrics.collection_mode == "BROWSER_SESSION_REPLAY"


def test_bytedance_fixture_generates_offset_ten_request():
    runtime = BrowserRuntime()
    GenericBrowserApiCollector(browser_plan(), browser_factory=lambda: runtime).collect()
    assert 10 in runtime.page.offsets


class Clock:
    def __init__(self): self.value = 0.0
    def __call__(self): return self.value


class TimedClient(SequenceClient):
    def __init__(self, values, clock, advance): super().__init__(values); self.clock = clock; self.advance = advance
    def request(self, method, url, **kwargs):
        result = super().request(method, url, **kwargs); self.clock.value += self.advance
        return result


def test_global_deadline_returns_partial_results():
    clock = Clock(); client = TimedClient([payload([raw(i) for i in range(10)], 30), payload([raw(i) for i in range(10, 20)], 30)], clock, 0.6)
    result = GenericHttpCollector(plan(), client, deadline_seconds=1.0, clock=clock, sleep_fn=lambda _delay: None).collect()
    assert result.status == "INCOMPLETE" and result.total_unique == 20
    assert result.metrics.termination_reason == "DEADLINE_EXCEEDED"
    assert any("COLLECTION_DEADLINE_EXCEEDED" in error for error in result.errors)


def test_slow_detail_budget_preserves_complete_list():
    clock = Clock()
    items = [dict(raw(1), description=""), dict(raw(2), description="")]
    detail = {"description": "Job responsibilities\n" + "Build reliable systems. " * 20, "requirements": "Python"}
    class DetailClient(TimedClient):
        def request(self, method, url, **kwargs):
            if "/detail/" in url:
                self.clock.value += 0.5; return Response(detail)
            return super().request(method, url, **kwargs)
    client = DetailClient([payload(items, 2)], clock, 0.1)
    value = plan("SINGLE_RESPONSE", initial_values={}, page_size_param=None, offset_param=None,
        detail_mode="DETAIL_FALLBACK", detail_endpoint_template="https://jobs.test/detail/{id}", detail_method="GET", detail_id_field="id")
    result = GenericHttpCollector(value, client, deadline_seconds=0.5, clock=clock).collect()
    assert result.total_fetched == 2 and result.total_unique == 2 and result.status == "INCOMPLETE"
    assert any("COLLECTION_DEADLINE_EXCEEDED" in error for error in result.errors)


def test_html_detail_concurrency_is_bounded():
    active = 0; maximum = 0; lock = threading.Lock()
    class HtmlResponse:
        def raise_for_status(self): pass
        text = "<h1>Job</h1><h2>Responsibilities</h2><p>" + "Build reliable systems. " * 20 + "</p><h2>Requirements</h2><p>Python teamwork</p>"
    class HtmlClient:
        def request(self, *_args, **_kwargs):
            nonlocal active, maximum
            with lock: active += 1; maximum = max(maximum, active)
            time.sleep(0.01)
            with lock: active -= 1
            return HtmlResponse()
    value = plan("SINGLE_RESPONSE", detail_url_field="detailUrl", trusted_detail_hosts=["jobs.test"])
    records = [dict(raw(i), title="Job", detailUrl=f"https://jobs.test/detail/{i}") for i in range(8)]
    collector = GenericHtmlDetailCollector(value, HtmlClient(), max_workers=3)
    collector.enrich(records, ("id",), ("title",))
    assert 1 < maximum <= 3 and collector.max_concurrency_observed <= 3


def test_known_feishu_uses_captured_template_for_active_replay():
    meta = json.dumps({"tenant_info": {"tenant_name": "Acme"}, "website_info": {"id": "site", "path": "index", "process_type": 1}})
    html = f'<script id="js-websiteInfo" type="text/json">{meta}</script>'
    def feishu_raw(i): return dict(raw(i), requirement="Python", city_list=[], recruit_type={"parent": {"id": "1"}})
    first_rows = [feishu_raw(i) for i in range(10)]
    class Page:
        def __init__(self): self.offsets = []
        def content(self): return html
        def evaluate(self, _script, arg):
            body = arg["body"] if "body" in arg else arg
            offset = body["offset"]; self.offsets.append(offset)
            return {"code": 0, "data": {"job_post_list": [feishu_raw(i) for i in range(offset, min(offset + body["limit"], 25))], "count": 25}}
        def locator(self, _selector): raise AssertionError("Feishu UI pagination must not be used")
    runtime_page = Page()
    class Runtime:
        def __init__(self): self.page = runtime_page; self.pages_opened = 1; self.requests_observed = 2
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def observe_only(self, *_args): pass
        def open_and_capture(self, *_args):
            body = json.dumps({"offset": 0, "limit": 10, "portal_type": 6})
            return CapturedResponse("https://x/api", "POST", body, 200, {"code": 0, "data": {"job_post_list": first_rows, "count": 25}})
    adapter = FeishuAdapter(browser_factory=Runtime)
    result = adapter.collect("https://acme.jobs.feishu.cn/index/position/list")
    assert result.status == "COMPLETE" and result.total_unique == 25 and adapter.page_count == 2
    # snapshot rebuilds offset=0 with the captured template (limit=10), then one
    # replay page at offset=10 with replay_page_size=100 covers rows 10..24
    assert runtime_page.offsets == [0, 10]


def test_feishu_canonical_snapshot_uses_replay_total_not_discovery_total():
    """STEP49 canonical snapshot: expected total must come from the offset=0
    replay baseline, never from the discovery capture (NIO audit: discovery
    total is volatile while the replay cohort is deterministic)."""
    meta = json.dumps({"tenant_info": {"tenant_name": "Acme"}, "website_info": {"id": "site", "path": "index", "process_type": 1}})
    html = f'<script id="js-websiteInfo" type="text/json">{meta}</script>'
    def feishu_raw(i): return dict(raw(i), requirement="Python", city_list=[], recruit_type={"parent": {"id": "1"}})
    class Page:
        def __init__(self): self.offsets = []
        def content(self): return html
        def evaluate(self, _script, arg):
            body = arg["body"] if "body" in arg else arg
            offset = body["offset"]; self.offsets.append(offset)
            count = 25  # replay cohort total; discovery capture claims 15
            return {"code": 0, "data": {"job_post_list": [feishu_raw(i) for i in range(offset, min(offset + body["limit"], count))], "count": count}}
        def locator(self, _selector): raise AssertionError("UI pagination must not be used")
    runtime_page = Page()
    class Runtime:
        def __init__(self): self.page = runtime_page; self.pages_opened = 1; self.requests_observed = 2
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def observe_only(self, *_args): pass
        def open_and_capture(self, *_args):
            body = json.dumps({"offset": 0, "limit": 10, "portal_type": 6})
            # discovery capture: count=15 (would truncate collection if trusted)
            return CapturedResponse("https://x/api", "POST", body, 200, {"code": 0, "data": {"job_post_list": [feishu_raw(i) for i in range(10)], "count": 15}})
    adapter = FeishuAdapter(browser_factory=Runtime)
    result = adapter.collect("https://acme.jobs.feishu.cn/index/position/list")
    assert result.status == "COMPLETE" and result.total_expected == 25
    # snapshot limit=10 (captured template) covers 0..9; one replay page at
    # offset=10 with replay_page_size=100 covers 10..24
    assert result.total_unique == 25 and runtime_page.offsets == [0, 10]
    assert result.metrics.termination_reason == "TOTAL_REACHED"


def test_feishu_canonical_snapshot_failure_keeps_discovery_and_is_incomplete():
    """Scope-uncertain safety policy: if the offset=0 replay baseline cannot be
    established, keep the discovery baseline and force INCOMPLETE."""
    meta = json.dumps({"tenant_info": {"tenant_name": "Acme"}, "website_info": {"id": "site", "path": "index", "process_type": 1}})
    html = f'<script id="js-websiteInfo" type="text/json">{meta}</script>'
    def feishu_raw(i): return dict(raw(i), requirement="Python", city_list=[], recruit_type={"parent": {"id": "1"}})
    class Page:
        def content(self): return html
        def evaluate(self, _script, body):
            raise RuntimeError("replay blocked")
        def locator(self, _selector): raise AssertionError("UI pagination must not be used")
    class Runtime:
        def __init__(self): self.page = Page(); self.pages_opened = 1; self.requests_observed = 2
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def observe_only(self, *_args): pass
        def open_and_capture(self, *_args):
            body = json.dumps({"offset": 0, "limit": 10, "portal_type": 6})
            return CapturedResponse("https://x/api", "POST", body, 200, {"code": 0, "data": {"job_post_list": [feishu_raw(i) for i in range(10)], "count": 15}})
    adapter = FeishuAdapter(browser_factory=Runtime)
    result = adapter.collect("https://acme.jobs.feishu.cn/index/position/list")
    assert result.status == "INCOMPLETE"
    assert any("CANONICAL_SNAPSHOT_FAILED" in error for error in result.errors)
    # pagination loop's own replay also fails -> REQUEST_FAILED (retry exhaustion)
    assert result.metrics.termination_reason == "REQUEST_FAILED"


def test_replay_preserves_scope_bearing_headers_and_drops_fingerprint_headers():
    """STEP49C regression: scope-bearing functional headers captured on the wire
    (e.g. website-path selecting the campus portal scope) must survive into every
    replay request; browser-managed / fingerprint-only headers must never be
    forwarded. Proven on NIO + ByteDance: without website-path the server returns
    a different cohort (887 vs 2076 / 7600 vs 10000)."""
    meta = json.dumps({"tenant_info": {"tenant_name": "Acme"}, "website_info": {"id": "site", "path": "campus", "process_type": 2}})
    html = f'<script id="js-websiteInfo" type="text/json">{meta}</script>'
    def feishu_raw(i): return dict(raw(i), requirement="Python", city_list=[], recruit_type={"parent": {"id": "2"}})
    seen_header_sets = []
    class Page:
        def content(self): return html
        def evaluate(self, _script, arg):
            body = arg["body"] if "body" in arg else arg
            seen_header_sets.append(dict(arg.get("headers") or {}))
            offset = body["offset"]
            return {"code": 0, "data": {"job_post_list": [feishu_raw(i) for i in range(offset, min(offset + body["limit"], 30))], "count": 30}}
        def locator(self, _selector): raise AssertionError("UI pagination must not be used")
    runtime_page = Page()
    class Runtime:
        def __init__(self): self.page = runtime_page; self.pages_opened = 1; self.requests_observed = 2
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def observe_only(self, *_args): pass
        def open_and_capture(self, *_args):
            body = json.dumps({"offset": 0, "limit": 10, "portal_type": 6})
            # captured wire headers include the scope-bearing website-path plus
            # fingerprint noise that must be stripped on replay
            return CapturedResponse("https://acme.jobs.feishu.cn/campus", "POST", body, 200,
                                    {"code": 0, "data": {"job_post_list": [feishu_raw(i) for i in range(10)], "count": 30}},
                                    request_url="https://acme.jobs.feishu.cn/api/v1/search/job/posts?offset=0&limit=10&portal_type=6",
                                    request_headers={
                                        "website-path": "campus",
                                        "portal-channel": "saas-career",
                                        "x-csrf-token": "undefined",
                                        "user-agent": "Mozilla/5.0 fingerprint",
                                        "cookie": "session=secret",
                                        "referer": "https://acme.jobs.feishu.cn/campus",
                                        "sec-ch-ua": "\"Chromium\";v=\"151\"",
                                    })
    adapter = FeishuAdapter(browser_factory=Runtime)
    result = adapter.collect("https://acme.jobs.feishu.cn/campus/position/list")
    assert result.status == "COMPLETE" and result.total_unique == 30
    assert result.metrics.termination_reason == "TOTAL_REACHED"
    assert len(seen_header_sets) >= 2  # canonical snapshot + pagination pages
    for headers in seen_header_sets:
        # scope-bearing functional headers forwarded on every replay request
        assert headers.get("website-path") == "campus"
        assert headers.get("portal-channel") == "saas-career"
        # browser-managed / fingerprint headers never forwarded
        assert "user-agent" not in headers
        assert "cookie" not in headers
        assert "referer" not in headers
        assert "sec-ch-ua" not in headers
        assert "content-type" not in headers  # replay sets its own


def test_replay_header_filtering_static_rules():
    """The generic filter itself: scope headers in, browser-managed out."""
    forwarded = FeishuAdapter._replay_headers({
        "Website-Path": "campus",           # case-insensitive normalisation
        "portal-platform": "pc",
        "x-csrf-token": "undefined",
        "env": "undefined",
        "Content-Type": "application/json", # browser-managed, replay sets its own
        "Cookie": "session=secret",
        "Origin": "https://x",
        "User-Agent": "UA",
        "Accept-Encoding": "gzip",
    })
    assert forwarded == {"website-path": "campus", "portal-platform": "pc",
                         "x-csrf-token": "undefined", "env": "undefined"}
    assert FeishuAdapter._replay_headers(None) == {}
