import httpx
from types import SimpleNamespace

from job_extractor.collectors.generic_html import GenericHtmlCollector
from job_extractor.planning.builder import CollectionPlanBuilder
from job_extractor.planning.execution_contract import dispatch_target
from job_extractor.planning.models import CollectionPlan


BASE = "https://careers.example.test/jobs"


def _detail(title: str) -> str:
    return f"""<html><h1>{title}</h1><main><h2>Responsibilities</h2>
    <p>Design and deliver reliable systems with peers while owning production quality and customer outcomes across the full lifecycle.</p>
    <h2>Requirements</h2><p>Demonstrated engineering experience, clear communication, sound judgment, and the ability to collaborate across teams.</p>
    </main></html>"""


class _Client:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return httpx.Response(200, text=self.pages[url], request=httpx.Request("GET", url))

    def request(self, method, url):
        self.calls.append(url)
        return httpx.Response(200, text=self.pages[url], request=httpx.Request(method, url))


def _plan(*links):
    return CollectionPlan(
        source_url=BASE, mode="HTML", executable=True, pagination_type="PAGE",
        detail_mode="DETAIL_HTTP_HTML", detail_url_field="detailUrl",
        allowed_detail_urls=list(links), trusted_detail_hosts=["careers.example.test"],
    )


def test_html_ssr_pagination_dedupes_and_hands_off_detail():
    first = "https://careers.example.test/job/101"
    second = "https://careers.example.test/job/102"
    page2 = BASE + "?PageIndex=2"
    client = _Client({
        BASE: '<a href="/job/101">Platform Engineer</a><a href="/job/101">Platform Engineer</a><a href="?PageIndex=2">2</a>',
        page2: '<a href="/job/102">Data Engineer</a>',
        first: _detail("Platform Engineer"), second: _detail("Data Engineer"),
    })
    result = GenericHtmlCollector(_plan(first, second), client=client, max_pages=2).collect()
    assert result.status == "COMPLETE"
    assert (result.total_expected, result.total_fetched, result.total_unique) == (2, 2, 2)
    assert result.metrics.termination_reason == "PAGINATION"
    # The shared link filter deduplicates repeated hrefs before collection;
    # execution must still yield one normalized job per observed detail URL.
    assert len({job.detail_url for job in result.jobs}) == 2
    assert all(job.full_jd and job.responsibilities and job.requirements for job in result.jobs)


def test_html_missing_or_malformed_entries_fail_closed():
    client = _Client({BASE: '<a href="/about">About us</a>'})
    result = GenericHtmlCollector(_plan(), client=client).collect()
    assert result.status == "FAILED"
    assert "HTML_JOB_LINKS_MISSING" in result.errors[0]


def test_html_dispatch_does_not_steal_other_modes():
    assert dispatch_target(_plan()) == "GenericHtmlCollector"
    for mode, target in (("HTTP_API", "GenericHttpCollector"), ("BROWSER_API", "GenericBrowserApiCollector"),
                         ("SERIALIZED_STATE", "GenericSerializedStateCollector"), ("DOM", "GenericDomCollector")):
        assert dispatch_target(CollectionPlan(source_url=BASE, mode=mode)) == target


def _runtime_plan():
    return CollectionPlan(source_url=BASE, mode="BROWSER_RUNTIME_DATA", executable=True,
                          confidence="HIGH", runtime_source={
                              "mode": "BROWSER_RUNTIME_DATA", "record_count": 2,
                              "records": [{"id": "r", "title": "Runtime"}, {"id": "s", "title": "Runtime 2"}],
                              "source_path": "window.state.jobs", "job_id_field": "id", "job_title_field": "title",
                              "total": 2, "pagination_model": "UNKNOWN", "confidence": "HIGH", "executable": True,
                          })


def _selected(monkeypatch, html, runtime, api=None):
    builder = CollectionPlanBuilder()
    result = SimpleNamespace(candidate_list_apis=[object()] if api else [])
    monkeypatch.setattr(builder, "_dom_plan", lambda _result: html)
    monkeypatch.setattr(builder, "_runtime_plan", lambda _result: runtime)
    monkeypatch.setattr(builder, "_api_plan", lambda _result, _candidate: api)
    return builder.build(result)


def test_high_confidence_executable_html_beats_runtime(monkeypatch):
    html = _plan("https://careers.example.test/job/101"); html.confidence = "HIGH"
    assert _selected(monkeypatch, html, _runtime_plan()).mode == "HTML"


def test_invalid_or_low_confidence_html_falls_back_to_runtime(monkeypatch):
    invalid = _plan(); invalid.executable = False; invalid.review_required = True
    assert _selected(monkeypatch, invalid, _runtime_plan()).mode == "BROWSER_RUNTIME_DATA"
    low = _plan("https://careers.example.test/job/101"); low.confidence = "LOW"
    assert _selected(monkeypatch, low, _runtime_plan()).mode == "BROWSER_RUNTIME_DATA"


def test_http_api_priority_is_unchanged(monkeypatch):
    api = CollectionPlan(source_url=BASE, mode="HTTP_API", executable=True, confidence="HIGH",
                         list_endpoint=BASE, list_method="GET", list_path="jobs", job_id_field="id",
                         job_title_field="title", pagination_type="SINGLE_RESPONSE")
    assert dispatch_target(api) == "GenericHttpCollector"
