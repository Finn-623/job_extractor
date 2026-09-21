from job_extractor.collectors.generic_browser_api import GenericBrowserApiCollector
from job_extractor.models import CollectionResult
from job_extractor.planning.models import CollectionPlan


def plan(**changes):
    values = dict(
        source_url="https://portal.test/campus", mode="BROWSER_API", executable=True,
        browser_trigger="AUTO_PAGINATION", list_endpoint="https://portal.test/api/jobs",
        list_method="POST", pagination_type="OFFSET", offset_param="offset",
        page_size_param="limit", initial_values={"offset": 0, "limit": 10},
        list_path="data.items", total_field="data.count", job_id_field="id",
        job_title_field="title", detail_mode="LIST_SUFFICIENT", observed_total=67,
    )
    values.update(changes)
    return CollectionPlan(**values)


def result(total, *, status="COMPLETE"):
    return CollectionResult(
        source_url="https://portal.test/campus", platform="generic", status=status,
        total_expected=total, total_fetched=total or 0, total_unique=total or 0,
    )


class Page:
    def goto(self, *_args, **_kwargs): pass


class Runtime:
    pages_opened = 1
    requests_observed = 1
    page = Page()
    def __enter__(self): return self
    def __exit__(self, *_args): return False


class EmptyLocator:
    @property
    def first(self): return self
    def count(self): return 0


class NativeResponse:
    url = "https://portal.test/api/jobs"
    headers = {"content-type": "application/json"}
    request = type("Request", (), {"method": "POST"})()
    def __init__(self, payload): self.payload = payload
    def json(self): return self.payload


class NativePage:
    def __init__(self): self.listeners = {}; self.navigated = False
    def on(self, name, callback): self.listeners[name] = callback
    def goto(self, *_args, **_kwargs): self.navigated = True
    def wait_for_timeout(self, *_args): pass
    def locator(self, *_args): return EmptyLocator()
    def emit(self, payload): self.listeners["response"](NativeResponse(payload))


class NativeRuntime:
    pages_opened = 1
    requests_observed = 1
    timeout_ms = 1
    def __init__(self): self.page = NativePage()
    def __enter__(self): return self
    def __exit__(self, *_args): return False


def native_payload(rows=10, total=67):
    return {"data": {"items": [{"id": str(i), "title": f"Job {i}"} for i in range(rows)], "count": total}}


def test_matching_browser_replay_keeps_reconstructed_path(monkeypatch):
    class Replay:
        def __init__(self, *_args, **_kwargs): pass
        def collect(self): return result(67)
    monkeypatch.setattr("job_extractor.collectors.generic_browser_api.GenericHttpCollector", Replay)
    collector = GenericBrowserApiCollector(plan(), browser_factory=Runtime)
    monkeypatch.setattr(collector, "_collect_observed_pages", lambda: (_ for _ in ()).throw(AssertionError("native fallback not expected")))
    collected = collector.collect()
    assert collected.total_expected == 67
    assert collected.metrics.collection_mode == "BROWSER_SESSION_REPLAY"


def test_observed_replay_mismatch_switches_to_native_path(monkeypatch):
    class Replay:
        def __init__(self, *_args, **_kwargs): pass
        def collect(self): return result(46)
    monkeypatch.setattr("job_extractor.collectors.generic_browser_api.GenericHttpCollector", Replay)
    collector = GenericBrowserApiCollector(plan(), browser_factory=Runtime)
    native = result(67)
    native.metrics.collection_mode = "NATIVE_BROWSER_LIST"
    monkeypatch.setattr(collector, "_collect_observed_pages", lambda: native)
    collected = collector.collect()
    assert collected.total_expected == 67
    assert collected.metrics.collection_mode == "NATIVE_BROWSER_LIST"


def test_native_failure_is_not_silently_replaced_by_known_divergent_replay(monkeypatch):
    class Replay:
        def __init__(self, *_args, **_kwargs): pass
        def collect(self): return result(46)
    monkeypatch.setattr("job_extractor.collectors.generic_browser_api.GenericHttpCollector", Replay)
    collector = GenericBrowserApiCollector(plan(), browser_factory=Runtime)
    native = result(10, status="INCOMPLETE")
    native.errors.append("PAGINATION_CONTROL_NOT_FOUND reason=official pagination could not be completed")
    native.metrics.collection_mode = "NATIVE_BROWSER_LIST"
    monkeypatch.setattr(collector, "_collect_observed_pages", lambda: native)
    collected = collector.collect()
    assert collected.status == "INCOMPLETE"
    assert collected.total_unique == 10
    assert any("PAGINATION_CONTROL_NOT_FOUND" in error for error in collected.errors)


def test_native_listener_captures_response_emitted_during_hydration(monkeypatch):
    runtime = NativeRuntime()
    collector = GenericBrowserApiCollector(plan(), browser_factory=lambda: runtime)
    monkeypatch.setattr(collector, "_bind_visible_links", lambda *_args: None)
    def hydration(page, _count):
        assert page.navigated and "response" in page.listeners
        page.emit(native_payload())
        return {"stabilized": True}
    monkeypatch.setattr("job_extractor.collectors.generic_browser_api.wait_for_hydration", hydration)
    monkeypatch.setattr("job_extractor.collectors.generic_browser_api.wait_for_readiness_consensus", lambda *_args, **_kwargs: {})
    collected = collector._collect_observed_pages()
    assert collected.total_expected == 67
    assert collected.total_unique == 10
    assert collected.metrics.collection_mode == "NATIVE_BROWSER_LIST"
    assert not any("NATIVE_FIRST_RESPONSE" in error for error in collected.errors)


def test_native_hydration_without_response_has_specific_first_response_timeout(monkeypatch):
    runtime = NativeRuntime()
    collector = GenericBrowserApiCollector(plan(), browser_factory=lambda: runtime)
    monkeypatch.setattr("job_extractor.collectors.generic_browser_api.wait_for_hydration", lambda *_args: {"stabilized": True})
    monkeypatch.setattr("job_extractor.collectors.generic_browser_api.trigger_job_page_search", lambda *_args: [])
    monkeypatch.setattr("job_extractor.collectors.generic_browser_api.wait_for_readiness_consensus", lambda *_args, **_kwargs: {})
    collected = collector._collect_observed_pages()
    assert collected.status == "FAILED"
    assert collected.errors == ["NATIVE_FIRST_RESPONSE_TIMEOUT"]
