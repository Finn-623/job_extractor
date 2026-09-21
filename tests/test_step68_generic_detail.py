"""STEP 68 — Generic Detail Resolver V1 tests."""
from __future__ import annotations

import httpx
import pytest

from job_extractor.collectors.generic_detail import (
    GenericHtmlDetailCollector,
    embedded_json_detail,
    looks_like_bot_challenge,
)
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.planning.models import CollectionPlan

RESP = "岗位职责：\n负责锂电池材料研发与测试验证工作。"
REQ = "任职要求：\n硕士及以上学历，材料或化学相关专业。"
JD = RESP + "\n" + REQ
LONG_JD = ("岗位职责：\n" + "".join(f"负责岗位{i}号锂电池新材料体系的配方设计与验证工作，主导中试放大与量产导入，"
            f"跟踪行业前沿技术动态并输出技术路线建议{i}，配合质量部门完成失效分析与改进闭环。\n" for i in range(1, 5))
            + "任职要求：\n硕士及以上学历，材料、化学或电化学相关专业，具备三年以上锂离子电池研发经验，"
            "熟悉正负极材料体系与电芯工艺流程，具备良好的数据分析能力和跨部门沟通协作精神。")


def plan(**kw):
    base = dict(
        source_url="https://jobs.test/list", mode="HTTP_API", executable=True,
        list_endpoint="https://api.test/list", list_method="POST",
        pagination_type="NONE", list_path="data.list", job_id_field="id",
        job_title_field="title", detail_mode="UNKNOWN",
        detail_url_field="job.url", observed_endpoints=["https://api.test/list"],
    )
    base.update(kw)
    return CollectionPlan(**base)


def _transport(pages):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/list"):
            list_page = pages[0] if isinstance(pages, list) else pages["https://api.test/list"]
            return httpx.Response(200, json=list_page)
        body = pages.get(url)
        if body is None:
            return httpx.Response(404, text="missing")
        return httpx.Response(200, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler), timeout=10)


def detail_html(title="工艺工程师"):
    duties = "\n".join(f"<p>负责岗位{i}号锂电池材料配方设计与验证，参与中试放大与量产导入。</p>" for i in range(1, 4))
    return f"""<html><head><title>{title}</title></head><body>
<div class="job-banner"><h1>{title}</h1><span class="city">武汉</span></div>
<div class="job-detail"><h3>岗位职责</h3>{duties}
<h3>任职要求</h3><p>硕士及以上学历，材料、化学或电化学相关专业，具备三年以上锂电研发经验者优先。</p>
<p>熟悉正负极材料体系与电芯工艺流程，具备良好的数据分析能力与团队协作精神。</p></div>
</body></html>"""


def _records(detail_url_host="xiaoyuan.zhaopin.com"):
    return [{"id": "10140923", "title": "氢能与燃料电池研发工程师",
             "job": {"url": f"https://{detail_url_host}/job/CCL1226837610J40895569705"}}]


# 1) SSR detail parser
def test_ssr_detail_resolves_title_location_jd():
    records = [{"id": "10140923", "title": "氢能与燃料电池研发工程师",
                "location": "武汉",
                "job": {"url": "https://xiaoyuan.zhaopin.com/job/CCL1226837610J40895569705"}}]
    client = _transport({
        "https://api.test/list": {"data": {"list": records}},
        "https://xiaoyuan.zhaopin.com/job/CCL1226837610J40895569705": detail_html("氢能与燃料电池研发工程师"),
    })
    result = GenericHttpCollector(plan(), client=client).collect()
    job = result.jobs[0]
    assert job.full_jd and "锂电池材料" in job.full_jd
    assert job.responsibilities and "锂电池材料" in job.responsibilities[0]
    assert job.requirements and "硕士及以上" in job.requirements[0]
    assert job.locations == ["武汉"]  # location carried from list record


# 2) embedded JSON detail
def test_embedded_json_detail_wins_over_shell():
    html = ('<html><head><title>loading</title></head><body><div id="root"></div>'
            '<script>window.__INITIAL_DATA__='
            + __import__("json").dumps({
                "jobDetail": {"title": "总体工艺工程师", "cityName": "上海",
                              "jobDescription": LONG_JD}}).replace("</", "<\\/")
            + ';</script></body></html>')
    got = embedded_json_detail(html, "总体工艺工程师")
    assert got and got["source_type"] == "EMBEDDED_JSON"
    assert got["full_jd"].startswith("岗位职责")
    assert got["responsibilities"] and got["requirements"]
    assert got["location"] == "上海"


def test_embedded_json_title_mismatch_rejected():
    html = ('<script>window.__X__=' + __import__("json").dumps(
        {"jobDetail": {"title": "别的岗位", "jobDescription": LONG_JD}}) + ';</script>')
    assert embedded_json_detail(html, "总体工艺工程师") is None


# 3) semantic heading parser (zh vocabularies via split_jd through SSR path)
def test_heading_split_responsibilities_requirements():
    client = _transport({
        "https://api.test/list": {"data": {"list": _records()}},
        "https://xiaoyuan.zhaopin.com/job/CCL1226837610J40895569705": detail_html("氢能与燃料电池研发工程师"),
    })
    result = GenericHttpCollector(plan(), client=client).collect()
    job = result.jobs[0]
    assert job.responsibilities and job.requirements
    assert any("任职要求" not in line for line in job.responsibilities)


# 4) incomplete detail fail-closed
def test_bot_challenge_is_fail_closed_not_fabricated():
    client = _transport({
        "https://api.test/list": {"data": {"list": _records()}},
        "https://xiaoyuan.zhaopin.com/job/CCL1226837610J40895569705":
            "<script>var t=0;t+=EO_Bot_Ssid;document.cookie='x='+t;</script>",
    })
    result = GenericHttpCollector(plan(), client=client, max_retries=0).collect()
    job = result.jobs[0]
    assert not job.full_jd  # never fabricated
    assert any("BOT_CHALLENGE" in str(e) for e in result.errors)
    assert result.status == "INCOMPLETE"


def test_detail_404_is_fail_closed():
    client = _transport({
        "https://api.test/list": {"data": {"list": _records()}},
    })
    result = GenericHttpCollector(plan(), client=client, max_retries=0).collect()
    assert not result.jobs[0].full_jd
    assert any("DETAIL_NAVIGATION_FAILED" in str(e) for e in result.errors)


# 5) 5-job mini pipeline
def test_five_job_mini_pipeline_all_resolved():
    records = []
    pages = {"https://api.test/list": {"data": {"list": None}}}
    for i in range(1, 6):
        url = f"https://xiaoyuan.zhaopin.com/job/CCL1J{i:010d}"
        records.append({"id": str(i), "title": f"工程师{i}", "job": {"url": url}})
        pages[url] = detail_html(f"工程师{i}")
    pages["https://api.test/list"]["data"]["list"] = records
    client = _transport(pages)
    result = GenericHttpCollector(plan(), client=client).collect()
    assert result.total_unique == 5
    assert all(j.full_jd for j in result.jobs)
    assert all(j.responsibilities and j.requirements for j in result.jobs)


def test_unknown_mode_without_detail_urls_skips_html_detail():
    client = _transport({"https://api.test/list": {"data": {"list": [{"id": "1", "title": "x"}]}}})
    result = GenericHttpCollector(plan(), client=client).collect()
    assert result.status == "COMPLETE"  # single response, list-sufficient shape
    assert not [e for e in result.errors if "DETAIL" in (e.get("code") or "")]


def test_observed_detail_host_joins_trusted_set():
    raws = _records("other-host.test")
    collector = GenericHtmlDetailCollector(plan(), _transport({}))
    collector.enrich(raws, ("id",), ("title",))  # would fail without trust
    # no exception raised; detail URL host accepted from observed evidence
    assert collector._observed_hosts == {"other-host.test"}
