"""STEP94M: Direct Detail API fast path (hotjob.cn wecruit shape).

Evidence-only AUTO_API: a List cURL on ``.../listPosition/<campaign>`` may
derive its sibling ``.../listPositionDetail/<campaign>`` XHR (captured from a
real detail page).  Covers acceptance items 1-10: URL derivation, POST method,
form body, workContent→responsibilities, serviceCondition→requirements,
COMPLETE JD, browser never opened on success, rendered fallback on API
failure, 5/5 batch, and existing STEP94 regressions.
"""
from __future__ import annotations

import httpx
import pytest

from job_extractor.manual_curl import (
    _auto_detail_api_spec,
    parse_curl,
    run_manual_curl,
)
from job_extractor.collectors.detail_resolver import extract_campaign_prefix
from job_extractor.field_semantics import pick_jd_fields

LIST_API = "https://skyworth.hotjob.cn/wecruit/positionInfo/listPosition/SU668b8b251c240e2e76ea71d8?iSaJAx=isAjax&request_locale=zh_CN"
LIST_CURL = f"curl '{LIST_API}' -H 'Accept: application/json'"

WORK = "1、负责管理SMT日常运作过程；\n2、负责SMT生产线和SMT机器的管理和维护；\n3、负责SMT生产技术的改善及提升；\n4、负责SMT制程规范制定与新制程、新技术的导入；\n5、负责SMT产品工艺分析，品质检验；"
SERVICE = "专业要求：机械设计、机电一体化、电子信息等相关专业，本科及以上学历；\n优选条件：熟练使用办公软件，有产线实习经验者优先；\n素质要求：责任心强，具备良好的沟通与团队协作能力。"


def _detail_json(job_id: str) -> dict:
    return {"state": 200, "type": None, "data": {
        "postId": job_id, "postName": "SMT工程师（数字公司）",
        "workContent": WORK, "serviceCondition": SERVICE,
        "education": "本科及以上", "workPlaceList": [{"code": "0/4/396/423", "name": "惠州市"}]}}


def _client(rows: int = 5, detail_status: int = 200, fail_ids: set[str] | None = None,
            seen: list | None = None):
    fail_ids = fail_ids or set()
    list_rows = [{"postId": f"p{i}", "jobName": f"岗位{i}", "postType": "campus",
                  "recruitType": "1", "workPlace": "惠州市"} for i in range(1, rows + 1)]
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if "listPositionDetail" in request.url.path:
            job_id = dict(x.split("=", 1) for x in request.content.decode().split("&"))["postId"]
            if job_id in fail_ids:
                return httpx.Response(detail_status)
            return httpx.Response(detail_status, json=_detail_json(job_id))
        if request.url.path.endswith("/listPosition/SU668b8b251c240e2e76ea71d8"):
            return httpx.Response(200, json={"data": {"list": list_rows, "total": len(list_rows)}, "success": True})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


# 1+2+3 — sibling URL derivation, POST, form body postId/recruitType
def test_sibling_detail_url_method_and_body():
    spec = parse_curl(LIST_CURL)
    prefix = extract_campaign_prefix(spec.url)
    assert prefix == "SU668b8b251c240e2e76ea71d8"
    detail = _auto_detail_api_spec(spec, prefix, "p3", "1")
    assert detail is not None and detail.method == "POST"
    assert detail.url == ("https://skyworth.hotjob.cn/wecruit/positionInfo/listPositionDetail/"
                          "SU668b8b251c240e2e76ea71d8?iSaJAx=isAjax&request_locale=zh_CN")
    assert detail.body == "postId=p3&recruitType=1"
    assert detail.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert detail.headers["X-Requested-With"] == "XMLHttpRequest"


def test_recruit_type_falls_back_to_list_query_then_captured_default():
    spec = parse_curl("curl 'https://x.test/wecruit/positionInfo/listPosition/CAMP9?recruitType=2'")
    assert _auto_detail_api_spec(spec, "CAMP9", "p1", None).body == "postId=p1&recruitType=2"
    plain = parse_curl("curl 'https://x.test/wecruit/positionInfo/listPosition/CAMP9'")
    assert _auto_detail_api_spec(plain, "CAMP9", "p1", None).body == "postId=p1&recruitType=1"
    # record classification paths (postType=0/1227/121201) are never consulted.


def test_no_evidence_yields_no_spec():
    spec = parse_curl("curl 'https://x.test/api/jobs?page=1'")
    assert _auto_detail_api_spec(spec, None, "p1", "1") is None
    api_spec = parse_curl(LIST_CURL)
    assert _auto_detail_api_spec(api_spec, None, "p1", "1") is None  # no campaign segment


# 4+5 — field mapping through the standard semantics (no site parser)
def test_work_content_and_service_condition_map_to_jd_halves():
    fields = pick_jd_fields(_detail_json("p1")["data"])
    assert fields["description_field"] == "workContent"
    assert fields["requirements_field"] == "serviceCondition"
    assert "SMT日常运作" in fields["description"]


# 6+7+9 — AUTO_API fills the batch, browser factory never called, COMPLETE
def test_auto_api_success_fills_batch_without_browser():
    calls = []
    def factory():
        calls.append(1); raise AssertionError("browser must not open")
    with _client(rows=5) as client:
        result = run_manual_curl(LIST_CURL, None, max_jobs=None, client=client,
                                 detail_browser_factory=factory)
    assert result.final_status == "COMPLETE"
    assert len(result.jobs) == 5 and all(job.full_jd for job in result.jobs)
    assert all("任职要求" in job.full_jd and "责任心强" in job.full_jd for job in result.jobs)
    audit = result.detail_resolution.audit()
    assert audit["detail_method"] == "AUTO_API" and audit["auto_api_success"] == 5
    assert audit["rendered_page_attempted"] is False
    assert audit["jd_success"] == 5 and audit["jd_failed"] == 0 and audit["jd_missing"] == 0
    # STEP94N: workContent is split into per-item responsibilities (never dropped).
    assert len(result.jobs[0].responsibilities) == 5
    assert result.jobs[0].responsibilities[0] == "1、负责管理SMT日常运作过程；"
    assert len(result.jobs[0].requirements) == 3  # SERVICE is 3 numbered lines
    assert calls == []
    # raw list record preserved alongside the detail payload
    assert result.jobs[0].raw_data["list"]["postType"] == "campus"
    assert result.jobs[0].raw_data["detail"]["workContent"] == WORK


# 8 — AUTO_API probe failure → falls through to rendered page (unchanged path)
def test_auto_api_probe_failure_falls_back_to_rendered_page():
    visited = []
    RENDER = ("岗位职责\n1. 负责 SMT 产线工艺调试，优化贴片程序参数，处理生产异常；\n"
              "2. 跟进新产品导入试产，输出工艺文件与验证报告；\n"
              "任职要求\n1. 本科及以上学历，电子、机械相关专业；\n2. 三年以上 SMT 工艺经验。")
    class FakePage:
        def goto(self, url, wait_until=None): visited.append(url)
        def evaluate(self, *_a, **_k): return RENDER
        def close(self): pass
    with _client(rows=2, detail_status=404) as client:
        result = run_manual_curl(LIST_CURL, None, max_jobs=None, client=client,
                                 detail_browser_factory=lambda: FakePage(),
                                 detail_page_url="https://skyworth.hotjob.cn",
                                 campaign_context="campus")
    audit = result.detail_resolution.audit()
    assert audit["auto_api_attempted"] is False  # probe stopped, nothing filled
    assert audit["detail_method"] == "RENDERED_PAGE"
    assert visited and "posDetail.html" in visited[0] and "postId=p" in visited[0]
    assert result.detail_resolution.rendered_page_success == 2


def test_auto_api_http_500_probe_failure_falls_back_to_rendered_page():
    RENDER = ("岗位职责\n1. 负责 SMT 产线工艺调试，优化贴片程序参数，处理生产异常；\n"
              "2. 跟进新产品导入试产，输出工艺文件与验证报告；\n"
              "任职要求\n1. 本科及以上学历，电子、机械相关专业；\n2. 三年以上 SMT 工艺经验。")
    class FakePage:
        def goto(self, url, wait_until=None): pass
        def evaluate(self, *_a, **_k): return RENDER
        def close(self): pass
    with _client(rows=1, detail_status=500) as client:
        result = run_manual_curl(LIST_CURL, None, max_jobs=None, client=client,
                                 detail_browser_factory=lambda: FakePage(),
                                 detail_page_url="https://skyworth.hotjob.cn",
                                 campaign_context="campus")
    assert result.detail_resolution.detail_method == "RENDERED_PAGE"


def test_auto_api_partial_failure_isolated_and_rendered_fills_rest():
    visited = []
    RENDER = ("岗位职责\n1. 负责 SMT 产线工艺调试，优化贴片程序参数，处理生产异常；\n"
              "2. 跟进新产品导入试产，输出工艺文件与验证报告；\n"
              "任职要求\n1. 本科及以上学历，电子、机械相关专业；\n2. 三年以上 SMT 工艺经验。")
    class FakePage:
        def goto(self, url, wait_until=None): visited.append(url)
        def evaluate(self, *_a, **_k): return RENDER
        def close(self): pass
    with _client(rows=3, fail_ids={"p2"}) as client:
        result = run_manual_curl(LIST_CURL, None, max_jobs=None, client=client,
                                 detail_browser_factory=lambda: FakePage(),
                                 detail_page_url="https://skyworth.hotjob.cn",
                                 campaign_context="campus")
    audit = result.detail_resolution.audit()
    assert audit["auto_api_success"] == 2
    # AUTO_API is the primary method even when a fallback filled the rest.
    assert audit["detail_method"] == "AUTO_API"
    assert audit["rendered_page_attempted"] is True and audit["rendered_page_success"] == 1
    assert audit["jd_success"] == 3 and audit["jd_failed"] == 0 and audit["jd_missing"] == 0
    api_filled = next(job for job in result.jobs if job.job_id == "p1")
    assert api_filled.raw_data["detail"]["workContent"] == WORK


# 10 — STEP94 priorities still hold: list JD credible → no detail work at all
def test_list_sufficient_jobs_skip_auto_api_probe():
    rows = [{"postId": "p1", "jobName": "岗位1", "workContent": WORK + "\n更多职责内容。", "serviceCondition": SERVICE}]
    def handler(request):
        if "listPositionDetail" in request.url.path: raise AssertionError("no detail request expected")
        return httpx.Response(200, json={"data": {"list": rows, "total": 1}, "success": True})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manual_curl(LIST_CURL, None, max_jobs=None, client=client)
    assert result.detail_strategy == "LIST_SUFFICIENT"
    assert result.detail_resolution.auto_api_success == 0


# An explicit Detail cURL still overrides AUTO_API (render_job_id unchanged)
def test_detail_curl_still_takes_precedence():
    spec = parse_curl("curl 'https://api.test/job?id=demo'")
    assert spec.render_job_id("42").query_params["id"] == "42"
