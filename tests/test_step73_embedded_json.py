"""STEP73: Embedded JSON / serialized-state execution V1.

Covers raw-HTML window globals (window.__INITIAL_DATA__ / window.__INITIAL_STATE__ /
window.__NUXT__), <script type="application/json"> and script#__NEXT_DATA__ — all
through the existing SERIALIZED_STATE plan mode. Only discovery-confirmed sources
are executed; every failure mode fails closed.
"""
from __future__ import annotations

import json

import pytest

from job_extractor.discovery.dynamic import (
    embedded_states_from_html,
    serialized_states,
    window_state_blobs,
)
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.planning.builder import CollectionPlanBuilder
from job_extractor.planning.execution_contract import (
    PlanContractError,
    can_dispatch,
    dispatch_target,
)
from job_extractor.planning.models import CollectionPlan
from job_extractor.planning.validator import CollectionPlanValidator

from job_extractor.collectors.generic_state import GenericSerializedStateCollector


# ---------------------------------------------------------------- fixtures

INITIAL_DATA_HTML = """<!doctype html><html><head><title>Jobs</title></head><body>
<div id="app"></div>
<script>
window.cfg = {"telemetry": true};
window.__INITIAL_DATA__ = {"page": {"jobs": [
    {"id": "100", "title": "算法工程师", "location": "北京", "description": "负责推荐算法设计。"},
    {"id": "101", "title": "后端工程师", "location": "上海", "description": "负责服务端开发。"},
    {"id": "102", "title": "前端工程师", "location": "深圳", "description": "负责前端架构。"}
]}, "layout": {"nav": ["首页"]}};
</script>
</body></html>"""

INITIAL_STATE_HTML = """<html><body><script>
window.__INITIAL_STATE__ = {"company": {"jobList": [
    {"jobId": "S1", "jobTitle": "质量工程师", "city": "西安"},
    {"jobId": "S2", "jobTitle": "测试工程师", "city": "西安"}
]}};
</script></body></html>"""

NEXT_DATA_HTML = """<html><body>
<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"data": {"jobs": [
    {"id": "N1", "title": "芯片工程师", "location": "武汉"},
    {"id": "N2", "title": "封装工程师", "location": "武汉"}
]}}}}</script>
</body></html>"""

JSON_SCRIPT_HTML = """<html><body>
<script type="application/json" id="jobs-data">{"jobs": [
    {"id": "J1", "title": "销售专员", "location": "广州"},
    {"id": "J2", "title": "市场专员", "location": "广州"}
]}</script>
</body></html>"""

DUPLICATE_HTML = """<html><body><script>
window.__INITIAL_STATE__ = {"jobs": [
    {"id": "D", "title": "重复岗一", "location": "苏州", "description": "职责 开发"},
    {"id": "D", "title": "重复岗二", "location": "苏州", "description": "要求 严谨"}
]};
</script></body></html>"""

DETAIL_HANDOFF_HTML = """<html><body><script>
window.__INITIAL_DATA__ = {"jobs": [
    {"id": "H1", "title": "硬件工程师", "location": "南京", "detail_url": "https://jobs.test/jd/H1"},
    {"id": "H2", "title": "软件工程师", "location": "南京", "detail_url": "https://jobs.test/jd/H2"}
]};
</script></body></html>"""


class _EmptyLocator:
    count = staticmethod(lambda: 0)
    def nth(self, index): raise IndexError(index)


class _EmptyPage:
    def locator(self, *args, **kwargs): return _EmptyLocator()
    def content(self): return ""
    def goto(self, *args, **kwargs): raise AssertionError("unexpected navigation")


class _EmptyBrowser:
    """Browser factory whose page yields zero embedded states (fail-closed tests)."""
    def __init__(self): self.page = _EmptyPage()
    def __enter__(self): return self
    def __exit__(self, *args): return False


def build_state_plan(source_url: str, record_path: str, job_id_field: str,
                     job_title_field: str, blob_index: int = 0,
                     **extra) -> CollectionPlan:
    """Build a SERIALIZED_STATE plan with the same fields the builder writes
    for STATE candidates."""
    fields: dict = dict(
        source_url=source_url,
        mode="SERIALIZED_STATE",
        confidence="HIGH",
        executable=True,
        pagination_type="SINGLE_RESPONSE",
        list_endpoint=source_url,
        observed_endpoints=[source_url],
        list_path=record_path,
        job_id_field=job_id_field,
        job_title_field=job_title_field,
        source_index=blob_index,
    )
    fields.update(extra)
    return CollectionPlan(**fields)


def run_executor(html: str, record_path: str, job_id_field: str, job_title_field: str,
                 blob_index: int = 0, browser_factory=None, **plan_extra):
    plan = build_state_plan("https://jobs.test/list", record_path, job_id_field,
                            job_title_field, blob_index=blob_index, **plan_extra)
    collector = GenericSerializedStateCollector(
        plan,
        browser_factory=browser_factory or _EmptyBrowser,
        fetch_html=lambda url: html,
    )
    return plan, collector


# ------------------------------------------------------- A. source extraction

def test_window_initial_data_blob_parsed_from_raw_html():
    states = window_state_blobs(INITIAL_DATA_HTML)
    assert len(states) == 1
    assert states[0]["page"]["jobs"][0]["id"] == "100"
    # unrelated window globals are never picked up
    assert all("telemetry" not in json.dumps(s) for s in states)


def test_window_initial_state_blob_parsed_from_raw_html():
    states = window_state_blobs(INITIAL_STATE_HTML)
    assert len(states) == 1
    assert states[0]["company"]["jobList"][0]["jobId"] == "S1"


def test_embedded_states_from_html_orders_script_json_then_window_globals():
    html = NEXT_DATA_HTML + '<script>window.__INITIAL_STATE__ = {"a": [1, 2]};</script>'
    states = embedded_states_from_html(html)
    assert len(states) == 2
    assert states[0]["props"]["pageProps"]["data"]["jobs"][0]["id"] == "N1"
    assert states[1] == {"a": [1, 2]}


def test_invalid_json_window_global_is_silently_skipped():
    html = "<script>window.__INITIAL_DATA__ = {broken: json};</script>"
    assert window_state_blobs(html) == []
    assert embedded_states_from_html(html) == []


# --------------------------------------- B. discovery → plan (SERIALIZED_STATE)

def test_initial_data_state_yields_executable_serialized_state_plan():
    plan = build_state_plan("https://jobs.test/list", "page.jobs", "id", "title")
    validation = CollectionPlanValidator().validate(plan)
    assert plan.mode == "SERIALIZED_STATE"
    assert validation.valid, validation.errors
    assert can_dispatch(plan)
    assert dispatch_target(plan) == "GenericSerializedStateCollector"


def test_builder_state_candidate_survives_unknown_pagination():
    """STEP73 builder change: a structurally HIGH state candidate without
    pagination evidence stays executable (SINGLE_RESPONSE at execution time)."""
    from job_extractor.discovery.detector import GenericApiDetector

    payload = {"jobs": [
        {"id": "1", "title": "A", "location": "L", "description": "职责 开发"},
        {"id": "2", "title": "B", "location": "L", "description": "要求 认真"},
    ]}
    payload_ref = [payload]

    class _Obs:
        url = "https://jobs.test/list"
        method = "STATE"
        body = {}
        query = {}
        payload = payload_ref[0]
        phase = "HYDRATION"
        replayable = False
        source_index = 0
        request_content_type = None
        origin_url = None
        page_url = None
        trigger_action_id = None
        trigger_action_text = None
        trigger_action_type = None
        provenance_trust = "TRUSTED"
        provenance_rejection = None

    candidate = GenericApiDetector._candidate(_Obs())
    assert candidate.source_type == "SERIALIZED_STATE"
    result = DiscoveryResult(source_url="https://jobs.test/list", status="PARTIAL",
                             candidate_list_apis=[candidate], detected_scope={})
    plan = CollectionPlanBuilder().build(result)
    assert plan.mode == "SERIALIZED_STATE"
    assert plan.executable is True
    assert CollectionPlanValidator().validate(plan).valid is True


# ------------------------------------------------------------ C. executor V1

def test_initial_data_executes_and_normalizes_jobs():
    plan, collector = run_executor(INITIAL_DATA_HTML, "page.jobs", "id", "title")
    result = collector.collect()
    assert result.total_fetched == 3
    assert result.total_unique == 3
    assert {job.job_title for job in result.jobs} == {"算法工程师", "后端工程师", "前端工程师"}
    all_locations = set().union(*(set(job.locations) for job in result.jobs))
    assert {"北京", "上海", "深圳"} <= all_locations
    assert result.status == "COMPLETE"


def test_initial_state_executes_with_dotted_fields():
    plan, collector = run_executor(INITIAL_STATE_HTML, "company.jobList", "jobId", "jobTitle")
    result = collector.collect()
    assert result.total_unique == 2
    assert {job.job_title for job in result.jobs} == {"质量工程师", "测试工程师"}


def test_next_data_executes():
    plan, collector = run_executor(NEXT_DATA_HTML, "props.pageProps.data.jobs", "id", "title")
    result = collector.collect()
    assert result.total_unique == 2
    assert {job.job_title for job in result.jobs} == {"芯片工程师", "封装工程师"}


def test_application_json_script_executes():
    plan, collector = run_executor(JSON_SCRIPT_HTML, "jobs", "id", "title")
    result = collector.collect()
    assert result.total_unique == 2
    assert {job.job_title for job in result.jobs} == {"销售专员", "市场专员"}


# --------------------------------------------------------- D. fail-closed paths

def test_missing_script_fails_closed():
    plan, collector = run_executor("<html><body><h1>no state</h1></body></html>",
                                   "jobs", "id", "title")
    with pytest.raises(PlanContractError) as excinfo:
        collector.collect()
    assert "SERIALIZED_STATE_SOURCE_MISSING" in str(excinfo.value)


def test_invalid_json_fails_closed():
    html = "<script>window.__INITIAL_DATA__ = {broken: json};</script>"
    plan, collector = run_executor(html, "jobs", "id", "title")
    with pytest.raises(PlanContractError):
        collector.collect()


def test_record_path_wrong_fails_closed():
    plan, collector = run_executor(INITIAL_DATA_HTML, "page.nonexistent.jobs", "id", "title")
    result = collector.collect()
    # the embedded state parsed fine but the confirmed record path yields no
    # usable list → no jobs, list untrusted (STEP70: FAILED when list_pages==0
    # is impossible here, so the honest outcome is zero normalized jobs +
    # non-COMPLETE with the path error surfaced)
    assert result.total_unique == 0
    assert result.status != "COMPLETE"
    assert any("LIST_PATH_INVALID" in error for error in result.errors)


def test_duplicate_ids_break_completeness():
    plan, collector = run_executor(DUPLICATE_HTML, "jobs", "id", "title")
    result = collector.collect()
    assert result.total_fetched == 2
    # same raw id but job_identity incorporates the title, so both survive dedup;
    # no expected total is available from the state blob → list cannot be
    # reconciled → completeness is broken (not COMPLETE).
    assert result.status != "COMPLETE"
    assert result.status == "INCOMPLETE"


# ------------------------------------------------------------- E. detail handoff

def test_detail_handoff_when_list_jd_missing():
    """Records with id/title/detail_url only: the derived plan hands off to the
    existing detail stage; list-level JD stays absent and status stays honest."""
    plan, collector = run_executor(DETAIL_HANDOFF_HTML, "jobs", "id", "title",
                                   detail_mode="DETAIL_FALLBACK", detail_url_field="detail_url")
    result = collector.collect()
    assert result.total_unique == 2
    for job in result.jobs:
        assert job.detail_url
    assert result.status in ("INCOMPLETE", "FAILED")


def test_dispatch_only_for_state_mode():
    runtime_plan = CollectionPlan(source_url="https://x.test", mode="BROWSER_RUNTIME_DATA",
                                  confidence="HIGH", executable=True,
                                  runtime_source={"record_count": 3, "job_id_field": "id",
                                                  "job_title_field": "title"},
                                  job_id_field="id", job_title_field="title")
    assert dispatch_target(runtime_plan) == "GenericRuntimeDataCollector"
    state_plan = build_state_plan("https://x.test", "jobs", "id", "title")
    assert dispatch_target(state_plan) == "GenericSerializedStateCollector"
    assert can_dispatch(state_plan)
    http_plan = CollectionPlan(source_url="https://x.test", mode="HTTP_API", confidence="HIGH",
                               executable=True, pagination_type="SINGLE_RESPONSE")
    assert dispatch_target(http_plan) == "GenericHttpCollector"


# ------------------------------------------------- F. browser-fallback alignment

def test_browser_reload_sees_same_state_sequence():
    """Collector fallback (HTTP miss) must address states with the same
    source_index discovery used: script-JSON nodes first, then window globals."""
    class _Node:
        def __init__(self, raw): self._raw = raw
        def text_content(self): return self._raw

    class _Locator:
        def __init__(self, nodes): self._nodes = nodes
        def count(self): return len(self._nodes)
        def nth(self, index): return self._nodes[index]

    class _Page:
        def __init__(self, script_nodes, html): self._nodes = script_nodes; self._html = html
        def locator(self, *args, **kwargs): return _Locator(self._nodes)
        def content(self): return self._html

    script_json = json.dumps({"jobs": [{"id": "K1", "title": "JSON脚本岗", "location": "成都"}]})
    html = (f'<html><script type="application/json">{script_json}</script>'
            '<script>window.__INITIAL_STATE__ = {"jobs": [{"id": "K2", "title": "窗口岗", "location": "成都"}]};</script></html>')
    page = _Page([_Node(script_json)], html)
    combined = list(serialized_states(page)) + window_state_blobs(html)
    assert len(combined) == 2
    assert combined[0]["jobs"][0]["id"] == "K1"
    assert combined[1]["jobs"][0]["id"] == "K2"
    # the collector's fallback path uses exactly this concatenation order
    assert embedded_states_from_html(html) == combined
