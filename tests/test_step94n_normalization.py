"""STEP94N: final output normalization — standard-field backfill, JD-half
splitting, collection company unification, AUTO_API timing evidence, and the
rendered_page_attempted audit rule (attempted only on real fallback).

Mapping (detail record wins, list record fills gaps):
  company←detail.company | department←detail.department/orgName
  locations←detail.workPlaceList[].name / workPlaceStr
  education←detail.education/list.educationStr | job_category←postTypeName
  recruitment_type←detail.projectName | major←subject | headcount←recruitNumStr
  publish_date←publishDate | responsibilities←workContent (per-item)
  requirements←serviceCondition (per-item) | full_jd logic untouched
"""
from __future__ import annotations

import httpx
import pytest

from job_extractor.job_normalize import (
    normalized_job_fields,
    normalized_jd_halves,
    unified_company,
)
from job_extractor.manual_curl import (
    manual_result_to_collection_result,
    run_manual_curl,
)
from job_extractor.field_semantics import pick_jd_fields

LIST_API = "https://skyworth.hotjob.cn/wecruit/positionInfo/listPosition/SU668b8b251c240e2e76ea71d8?iSaJAx=isAjax&request_locale=zh_CN"
LIST_CURL = f"curl '{LIST_API}' -H 'Accept: application/json'"

WORK = "1、负责管理SMT日常运作过程；\n2、负责SMT生产线和SMT机器的管理和维护；\n3、负责SMT生产技术的改善及提升；\n4、负责SMT制程规范制定与新制程、新技术的导入；\n5、负责SMT产品工艺分析，品质检验；"
SERVICE = "专业要求：机械设计、机电一体化、电子信息等相关专业，本科及以上学历；\n优选条件：熟练使用办公软件，有产线实习经验者优先；\n素质要求：责任心强，具备良好的沟通与团队协作能力。"

DETAIL_BASE = {
    "workContent": WORK, "serviceCondition": SERVICE,
    "education": "本科及以上", "postTypeName": "智能制造类",
    "workPlaceList": [{"code": "0/4/396/423", "name": "惠州市"}],
    "workPlaceStr": "惠州市", "subject": "机械设计、机电一体化、电子信息、材料、工业工程等相关理工科专业",
    "recruitNumStr": "若干", "publishDate": "2026-09-20 08:59:37",
    "orgName": "数字制造总部", "department": "数字制造总部",
    "projectName": "2027届秋季校园招聘", "company": "创维集团有限公司",
}

LIST_BASE = {
    "postType": "campus", "recruitType": "1", "workPlace": "惠州市",
    "educationStr": "本科及以上", "postTypeName": "列表分类",
    "recruitNumStr": "若干", "publishDate": "2026-09-20 08:59:37",
    "company": "创维集团有限公司", "department": "列表部门",
    "subject": "列表专业", "projectName": "2027届秋季校园招聘",
}


def _detail_json(job_id: str, **overrides) -> dict:
    return {"state": 200, "type": None, "data": {
        "postId": job_id, "postName": "SMT工程师（数字公司）", **DETAIL_BASE, **overrides}}


def _client(rows: int = 3, detail_status: int = 200, fail_ids: set[str] | None = None,
            list_extra: list[dict] | None = None):
    fail_ids = fail_ids or set()
    list_rows = list_extra if list_extra is not None else [
        {"postId": f"p{i}", "jobName": f"岗位{i}", **LIST_BASE} for i in range(1, rows + 1)]
    def handler(request: httpx.Request) -> httpx.Response:
        if "listPositionDetail" in request.url.path:
            job_id = dict(x.split("=", 1) for x in request.content.decode().split("&"))["postId"]
            if job_id in fail_ids:
                return httpx.Response(detail_status)
            return httpx.Response(detail_status, json=_detail_json(job_id))
        if request.url.path.endswith("/listPosition/SU668b8b251c240e2e76ea71d8"):
            return httpx.Response(200, json={"data": {"list": list_rows, "total": len(list_rows)}, "success": True})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


RENDER = ("岗位职责\n1. 负责 SMT 产线工艺调试，优化贴片程序参数，处理生产异常；\n"
          "2. 跟进新产品导入试产，输出工艺文件与验证报告；\n"
          "任职要求\n1. 本科及以上学历，电子、机械相关专业；\n2. 三年以上 SMT 工艺经验。")


class FakePage:
    def __init__(self): self.urls = []
    def goto(self, url, wait_until=None): self.urls.append(url)
    def evaluate(self, *_a, **_k): return RENDER
    def close(self): pass


def _run(client, **kwargs) -> object:
    return run_manual_curl(LIST_CURL, None, max_jobs=None, client=client,
                           detail_page_url="https://skyworth.hotjob.cn",
                           campaign_context="campus", **kwargs)


# ---- unit: normalized_job_fields (items 1-8, detail-first + list fallback) ----

def test_detail_fields_win_over_list_fields():
    detail = dict(DETAIL_BASE)
    fields = normalized_job_fields(LIST_BASE, detail)
    assert fields["company"] == "创维集团有限公司"
    assert fields["department"] == "数字制造总部"          # detail, not 列表部门
    assert fields["locations"] == ["惠州市"]               # workPlaceList[].name
    assert fields["education"] == "本科及以上"
    assert fields["job_category"] == "智能制造类"
    assert fields["major"] == "机械设计、机电一体化、电子信息、材料、工业工程等相关理工科专业"
    assert fields["recruitment_type"] == "2027届秋季校园招聘"
    assert fields["publish_date"] == "2026-09-20 08:59:37"
    assert "headcount" not in fields                    # 若干 → absent, never prose


def test_list_record_fills_missing_detail():
    # item 11 — detail 缺失时 fallback list record
    fields = normalized_job_fields(LIST_BASE, None)
    assert fields["company"] == "创维集团有限公司"
    assert fields["department"] == "列表部门"
    assert fields["education"] == "本科及以上"              # educationStr alias
    assert fields["locations"] == ["惠州市"]                # workPlaceStr fallback
    assert fields["publish_date"] == "2026-09-20 08:59:37"


def test_headcount_digit_parsing():
    assert normalized_job_fields({"recruitNumStr": "10人"}, None)["headcount"] == 10
    assert normalized_job_fields({"recruitNumStr": "若干"}, None) == {}
    assert normalized_job_fields({"recruitNumStr": 3}, None)["headcount"] == 3


def test_prose_jd_half_stays_single_element():
    prose = "本岗位负责公司全部线上渠道的运营工作，统筹内容策划、投放与数据复盘，并与销售团队紧密协作完成季度增长目标，同时负责团队建设与人才培养，确保运营体系持续迭代优化。"
    fields = {"description": prose, "description_field": "description",
              "requirements": None, "requirements_field": None}
    resp, req = normalized_jd_halves({}, {"workContent": prose}, fields)
    assert resp == [prose] and req == []  # prose never dropped, never force-split


def test_never_touches_job_id_title_full_jd():
    detail = dict(DETAIL_BASE)
    detail["job_id"] = "spoof"; detail["job_title"] = "spoof"; detail["full_jd"] = "spoof"
    fields = normalized_job_fields(LIST_BASE, detail)
    assert "job_id" not in fields and "job_title" not in fields and "full_jd" not in fields


# ---- integration: AUTO_API run (items 1-10, 12, F) ----

def test_auto_api_run_backfills_all_standard_fields():
    with _client(rows=3) as client:
        result = _run(client)
    audit = result.detail_resolution.audit()
    assert audit["detail_method"] == "AUTO_API" and audit["auto_api_success"] == 3
    assert audit["rendered_page_attempted"] is False          # item 12
    assert audit["jd_success"] == 3 and audit["jd_failed"] == 0
    for job in result.jobs:
        assert job.company == "创维集团有限公司"               # item 1
        assert job.department == "数字制造总部"                # item 2
        assert job.locations == ["惠州市"]                     # item 3
        assert job.education == "本科及以上"                    # item 4
        assert job.job_category == "智能制造类"                # item 5
        assert job.major.startswith("机械设计")                # item 6
        assert job.headcount is None                           # item 7 若干
        assert job.publish_date == "2026-09-20 08:59:37"       # item 8
        assert len(job.responsibilities) == 5                  # item 9
        assert job.responsibilities[0] == "1、负责管理SMT日常运作过程；"
        assert len(job.requirements) == 3                      # item 10
        assert "任职要求" in job.full_jd and "责任心强" in job.full_jd  # item D
        assert job.raw_data["list"]["postType"] == "campus"
        assert job.raw_data["detail"]["workContent"] == WORK
    # item F — real AUTO_API timing evidence
    metrics = manual_result_to_collection_result(result, LIST_API).metrics
    assert metrics.detail_request_seconds > 0.0
    assert metrics.average_detail_request_seconds > 0.0
    assert metrics.max_concurrency_observed >= 1


# ---- integration: mixed AUTO_API + rendered fallback (items 12, 13, E) ----

def test_mixed_run_marks_rendered_page_attempted_true():
    pages = []
    def factory():
        page = FakePage(); pages.append(page); return page
    with _client(rows=3, fail_ids={"p2"}) as client:
        result = _run(client, detail_browser_factory=factory)
    audit = result.detail_resolution.audit()
    assert audit["auto_api_success"] == 2
    assert audit["detail_method"] == "AUTO_API"               # AUTO_API 主路径
    assert audit["rendered_page_attempted"] is True           # item 13: real fallback ran
    assert audit["rendered_page_success"] == 1 and audit["jd_success"] == 3
    fallback_job = next(job for job in result.jobs if job.job_id == "p2")
    # _apply_sections joins halves with a blank line between them
    assert fallback_job.full_jd == "岗位职责\n1. 负责 SMT 产线工艺调试，优化贴片程序参数，处理生产异常；\n2. 跟进新产品导入试产，输出工艺文件与验证报告；\n\n任职要求\n1. 本科及以上学历，电子、机械相关专业；\n2. 三年以上 SMT 工艺经验。"
    assert len(fallback_job.responsibilities) == 2            # rendered sections split
    assert pages and len(pages[0].urls) == 1


def test_rendered_fallback_backfills_from_list_record():
    # item 11/E — placeholder job built from the list record carries backfill
    pages = []
    def factory():
        page = FakePage(); pages.append(page); return page
    with _client(rows=2, fail_ids={"p1", "p2"}) as client:
        result = _run(client, detail_browser_factory=factory)
    for job in result.jobs:
        assert job.company == "创维集团有限公司"               # from list record
        assert job.job_category == "列表分类"                  # list-only record
        assert job.locations == ["惠州市"]
        assert job.full_jd == "岗位职责\n1. 负责 SMT 产线工艺调试，优化贴片程序参数，处理生产异常；\n2. 跟进新产品导入试产，输出工艺文件与验证报告；\n\n任职要求\n1. 本科及以上学历，电子、机械相关专业；\n2. 三年以上 SMT 工艺经验。"


# ---- collection company unification (item 14) ----

def test_list_sufficient_path_also_normalized():
    # List JD credible → no detail work at all; standard fields still backfilled
    rows = [{"postId": "p1", "jobName": "岗位1", **LIST_BASE,
             "workPlaceList": [{"code": "0/4/396/423", "name": "惠州市"}],
             "workContent": WORK, "serviceCondition": SERVICE}]
    def handler(request):
        if "listPositionDetail" in request.url.path:
            raise AssertionError("no detail request expected")
        return httpx.Response(200, json={"data": {"list": rows, "total": 1}, "success": True})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manual_curl(LIST_CURL, None, max_jobs=None, client=client)
    assert result.detail_strategy == "LIST_SUFFICIENT"
    job = result.jobs[0]
    assert job.company == "创维集团有限公司"
    assert job.department == "列表部门"                        # list-only record
    assert job.locations == ["惠州市"]
    assert job.job_category == "列表分类"                      # list-only record
    assert len(job.responsibilities) == 5                      # split from workContent
    assert len(job.requirements) == 3                          # split from serviceCondition


# ---- collection company unification (item 14) ----

def test_unified_company_uniform_and_mixed():
    class J:
        def __init__(self, company): self.company = company
    assert unified_company([J("创维集团有限公司"), J("创维集团有限公司")]) == "创维集团有限公司"
    assert unified_company([J("A"), J("B")]) is None
    assert unified_company([J(None), J("")]) is None


def test_collection_result_carries_unified_company():
    with _client(rows=3) as client:
        result = _run(client)
    collection = manual_result_to_collection_result(result, LIST_API)
    assert collection.company == "创维集团有限公司"
    assert collection.metrics.jd_strategy == "DETAIL_REQUIRED"


# ---- report has no Unknown for backfilled jobs (item 15) ----

def test_markdown_report_has_no_unknown_for_backfilled_jobs(tmp_path):
    from job_extractor.reporting.markdown_reporter import MarkdownReporter
    from job_extractor.runtime import evaluate_data_completeness
    with _client(rows=3) as client:
        result = _run(client)
    collection = manual_result_to_collection_result(result, LIST_API)
    collection.data_completeness = evaluate_data_completeness(collection)
    report_path = MarkdownReporter().generate(collection, tmp_path / "report.md", detail="full")
    text = report_path.read_text(encoding="utf-8")
    assert "- Company: 创维集团有限公司" in text
    assert "- Location: 惠州市" in text
    assert "- Category: 智能制造类" in text
    assert "- Department: 数字制造总部" in text
    assert "- Recruitment Type: 2027届秋季校园招聘" in text
    jobs_section = text.split("## Jobs", 1)[1]
    for line in jobs_section.splitlines():
        if line.startswith("- Location: ") or line.startswith("- Category: ") \
           or line.startswith("- Department: ") or line.startswith("- Recruitment Type: "):
            assert "Unknown" not in line, line
