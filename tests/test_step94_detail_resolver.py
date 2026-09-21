"""STEP94: Detail Source Resolver — hard priority chain tests.

Covers the 12 acceptance scenarios: LIST_SUFFICIENT short-circuit, direct
detailUrl evidence, postId URL construction, rendered success, render timeout,
missing JD, auto fail -> user curl prompt, List-only choice, partial results,
List-only file output, audit metadata, and the STEP93 manual-cURL regression.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from job_extractor.collectors.detail_resolver import (
    build_detail_url_candidates,
    extract_jd_from_text,
    fetch_rendered_jd,
    render_detail_jobs,
    resolve_targets,
)
from job_extractor.manual_curl import DetailCurlRequired, run_manual_curl
from job_extractor.models import Job

LIST_URL = "https://skyworth.hotjob.cn/SU668b8b251c240e2e76ea71d8/"
SKYWORTH_POST_ID = "6a6fee0a1ad6db7cf8e19b45"


def _job(job_id: str, title: str = "SMT 工程师", full_jd: str | None = None) -> Job:
    return Job(job_id=job_id, job_title=title, locations=["上海"],
               full_jd=full_jd, source_url=LIST_URL,
               raw_data={"list": {"postId": job_id, "jobName": title, "postType": "campus"}})


CREDIBLE_JD = "岗位职责\n1. 负责 SMT 产线工艺调试，优化贴片程序参数，处理生产异常；\n2. 跟进新产品导入试产，输出工艺文件与验证报告；\n\n任职要求\n1. 本科及以上学历，电子、机械相关专业；\n2. 三年以上 SMT 工艺经验，熟悉回流焊与 AOI 检测。"

# ① LIST_SUFFICIENT
def test_list_sufficient_records_are_never_detail_targets():
    jobs=[_job("1", full_jd=CREDIBLE_JD), _job("2", full_jd=CREDIBLE_JD)]
    resolution=resolve_targets(jobs, LIST_URL)
    assert [t.method for t in resolution.targets]==["LIST_SUFFICIENT", "LIST_SUFFICIENT"]
    assert resolution.list_sufficient==2 and resolution.jd_missing==0
    assert resolution.detail_method=="LIST_SUFFICIENT"

# ② direct detailUrl evidence
def test_direct_detail_url_used_verbatim():
    record={"id":"7","detailUrl":"https://x.test/job/7.html"}
    candidates=build_detail_url_candidates("https://x.test/jobs", record)
    assert candidates[0]=="https://x.test/job/7.html"
    job=_job("7"); job.raw_data={"list": dict(record)}
    resolution=resolve_targets([_job("9", full_jd=CREDIBLE_JD), job], "https://x.test/jobs")
    target=next(t for t in resolution.targets if t.method=="RENDERED_PAGE")
    assert target.detail_urls[0]=="https://x.test/job/7.html"

# ③ postId → constructed detail URL (Skyworth shape)
def test_skyworth_post_id_constructs_candidates():
    candidates=build_detail_url_candidates(LIST_URL, {"postId": SKYWORTH_POST_ID, "postType": "campus"})
    assert candidates, "expected at least one candidate"
    assert candidates[0].startswith(LIST_URL)
    assert "pb/posDetail.html" in candidates[0]
    assert f"postId={SKYWORTH_POST_ID}" in candidates[0]
    assert "postType=campus" in candidates[0]


def test_campaign_context_beats_record_classification_path_for_detail_route():
    record={"postId": SKYWORTH_POST_ID, "postType": "0/1227/121201", "recruitType": "1"}
    candidates=build_detail_url_candidates(LIST_URL, record, campaign_context="campus")
    assert "postType=campus" in candidates[0]
    assert "0%2F1227%2F121201" not in candidates[0]


def test_list_api_campaign_prefix_builds_the_first_public_detail_candidate():
    api="https://skyworth.hotjob.cn/wecruit/positionInfo/listPosition/SU668b8b251c240e2e76ea71d8"
    record={"postId":SKYWORTH_POST_ID,"postType":"0/1227/121201"}
    from job_extractor.collectors.detail_resolver import extract_campaign_prefix
    candidates=build_detail_url_candidates("https://skyworth.hotjob.cn",record,campaign_context="campus",
                                           campaign_prefix=extract_campaign_prefix(api))
    assert candidates[0]==f"https://skyworth.hotjob.cn/SU668b8b251c240e2e76ea71d8/pb/posDetail.html?postId={SKYWORTH_POST_ID}&postType=campus"


def test_successful_campaign_pattern_is_locked_for_next_job():
    from job_extractor.collectors.detail_resolver import DetailTarget
    seen=[]
    class FakePage:
        def goto(self, url, wait_until=None): self.url=url; seen.append(url)
        def evaluate(self, *_a, **_k): return CREDIBLE_JD if "/pos?" in self.url else "加载中"
        def close(self): pass
    # The first candidate is intentionally not the working route.  The first
    # target locks its second URL; the next target must try that template first.
    first=DetailTarget(_job("1"), "RENDERED_PAGE", ["https://x.test/bad?id=1", "https://x.test/pos?postId=1&postType=campus"], id_value="1")
    second=DetailTarget(_job("2"), "RENDERED_PAGE", ["https://x.test/bad?id=2", "https://x.test/pos?postId=2&postType=campus"], id_value="2")
    def factory(): return FakePage()
    assert render_detail_jobs(factory, [first, second], concurrency=1, timeout_s=1, total_timeout_s=2)==(2,0)
    assert seen[2] == "https://x.test/pos?postId=2&postType=campus"


def test_detail_candidate_total_budget_caps_a_failed_target():
    class FakePage:
        def goto(self, url, wait_until=None): pass
        def evaluate(self, *_a, **_k): return "加载中"
        def close(self): pass
    target=resolve_targets([_job("1")], LIST_URL).targets[0]
    started=time.monotonic()
    assert render_detail_jobs(lambda: FakePage(), [target], timeout_s=.4, total_timeout_s=.55, poll_interval_s=.05)==(0,1)
    assert time.monotonic()-started < 1.0


def test_manual_batch_uses_the_same_campaign_resolver_pipeline():
    """Production Manual cURL wiring must match the proven campaign sample."""
    from job_extractor.manual_curl import run_manual_curl
    visited=[]
    class FakePage:
        def goto(self, url, wait_until=None): visited.append(url)
        def evaluate(self, *_a, **_k): return CREDIBLE_JD
        def close(self): pass
    with _manual_client() as client:
        result=run_manual_curl(LIST_CURL, None, max_jobs=2, client=client,
            detail_browser_factory=lambda: FakePage(), detail_page_url=LIST_URL,
            campaign_context="campus")
    assert len(result.jobs)==2
    assert all("postType=campus" in url for url in visited)
    assert result.detail_resolution.rendered_page_success==2


def test_manual_cli_wiring_uses_list_referer_as_campaign_context():
    from job_extractor.manual_curl import run_manual_curl
    visited=[]
    class FakePage:
        def goto(self, url, wait_until=None): visited.append(url)
        def evaluate(self, *_a, **_k): return CREDIBLE_JD
        def close(self): pass
    curl=LIST_CURL + " -H 'Referer: https://skyworth.hotjob.cn/SU/pb/school.html'"
    with _manual_client() as client:
        run_manual_curl(curl, None, max_jobs=1, client=client, detail_browser_factory=lambda: FakePage(),
                        detail_page_url="https://skyworth.hotjob.cn", campaign_context="https://skyworth.hotjob.cn")
    assert visited and "pb/posDetail.html" in visited[0] and "postType=campus" in visited[0]


def test_step94h_debug_trace_is_initialized_and_persists_detail_exception(monkeypatch, tmp_path):
    import job_extractor.step94h_debug as trace
    monkeypatch.setenv("STEP94H_DEBUG", "1")
    monkeypatch.setattr(trace, "DEBUG_PATH", tmp_path / "step94h.log")
    trace.initialize()
    target=resolve_targets([_job("1")], LIST_URL).targets[0]
    assert render_detail_jobs(lambda: (_ for _ in ()).throw(RuntimeError("browser failed")), [target], timeout_s=.1)==(0,1)
    text=trace.DEBUG_PATH.read_text(encoding="utf-8")
    assert "debug_enabled=true" in text
    assert '"phase":"detail_exception"' in text
    assert "browser failed" in text

# ④ rendered success — fake page whose text matures after a couple of polls
def test_rendered_page_success_extracts_jd_sections():
    text=(f"深圳创维-RGB电子有限公司 SMT 工程师\n"
          "工作职责\n1. 负责 SMT 产线工艺调试，优化贴片程序参数，处理生产异常；\n"
          "2. 跟进新产品导入试产，输出工艺文件与验证报告；\n"
          "3. 分析直通率数据，推动制程改善；\n"
          "任职要求\n1. 本科及以上学历，电子、机械相关专业；\n"
          "2. 三年以上 SMT 工艺经验，熟悉回流焊与 AOI 检测；\n"
          "3. 具备良好的跨部门沟通能力。\n"
          "沪ICP备10000326号-6\n粤公网安备 44030002000001号")
    class FakePage:
        def __init__(self): self.calls=0
        def goto(self, url, wait_until=None): self.url=url; assert f"postId={SKYWORTH_POST_ID}" in url
        def evaluate(self, *_a, **_k):
            self.calls+=1
            return text if self.calls>=2 else "加载中"
        def close(self): pass
    jobs=[_job(SKYWORTH_POST_ID)]
    resolution=resolve_targets(jobs, LIST_URL)
    assert resolution.rendered_page_candidates==1
    target=resolution.targets[0]
    assert target.method=="RENDERED_PAGE" and target.detail_urls
    def factory(): return FakePage()
    succeeded, failed=render_detail_jobs(factory, [target], concurrency=2, timeout_s=5.0)
    assert (succeeded, failed)==(1, 0)
    assert target.detail_url is not None
    assert "SMT 产线工艺调试" in target.job.full_jd
    assert "回流焊与 AOI" in target.job.full_jd
    assert target.job.responsibilities and target.job.requirements
    assert resolution.detail_method=="RENDERED_PAGE"  # after manual outcome update

# ⑤ render timeout → no JD merge, failure counted
def test_render_timeout_returns_error_without_merge():
    class FakePage:
        def goto(self, url, wait_until=None): pass
        def evaluate(self, *_a, **_k): return "加载中"
        def close(self): pass
    outcome=fetch_rendered_jd(FakePage(), LIST_URL, timeout_s=0.4, poll_interval_s=0.1)
    assert outcome["ok"] is False and outcome["error"]=="RENDER_TIMEOUT"
    job=_job(SKYWORTH_POST_ID)
    target=resolve_targets([job], LIST_URL).targets[0]
    succeeded, failed=render_detail_jobs(lambda: FakePage(), [target], timeout_s=0.4, poll_interval_s=0.1)
    assert (succeeded, failed)==(0, 1)
    assert job.full_jd is None

# ⑥ footer noise / missing sections → missing JD
def test_footer_noise_only_text_yields_no_jd():
    sections=extract_jd_from_text("沪ICP备10000326号-6\n© 2026 Skyworth\n分享\n收藏\n返回")
    assert sections["responsibilities"] is None and sections["requirements"] is None

# ⑦+⑧ resolver escalation inside run_manual_curl: teaser list + no browser in
# tests → DetailCurlRequired carries the full list for the CLI menu.
LIST_CURL=f"""curl 'https://jobs.test/api/list?page=1' -H 'User-Agent: ua' -H 'Accept: application/json'"""
TEASER_PAYLOAD={"data":{"list":[{"id":str(i),"jobName":f"岗位{i}","postType":"campus"} for i in range(1,4)],
                "total":3},"success":True}

def _manual_client(detail_handler=None):
    def handler(request):
        if "api/list" in str(request.url):
            return httpx.Response(200, json=TEASER_PAYLOAD)
        if detail_handler: return detail_handler(request)
        return httpx.Response(200, json={})
    return httpx.Client(transport=httpx.MockTransport(handler))

def test_auto_fail_raises_detail_curl_required_with_list_context():
    with _manual_client() as client:
        with pytest.raises(DetailCurlRequired) as excinfo:
            run_manual_curl(LIST_CURL, None, max_jobs=None, client=client, detail_browser_factory=lambda: pytest.fail("tests never open a browser"))
    exc=excinfo.value
    assert exc.resolution.jd_failed==3 and exc.resolution.jd_success==0
    assert [job.job_id for job in exc.jobs]==["1","2","3"]
    assert exc.jobs[0].job_title=="岗位1"
    assert exc.context["url"].startswith("https://jobs.test/api/list")
    # resolver classified them with evidence (postType carried, no id in list URL query)
    targets=[t for t in exc.resolution.targets if t.method=="RENDERED_PAGE"]
    assert targets and all(t.detail_urls for t in targets)

# ⑧ List-only choice — the exception's carried list becomes a legal result
def test_list_only_choice_produces_legal_complete_result():
    from job_extractor.manual_curl import ManualCurlResult, manual_result_to_collection_result
    with _manual_client() as client:
        with pytest.raises(DetailCurlRequired) as excinfo:
            run_manual_curl(LIST_CURL, None, max_jobs=None, client=client, detail_browser_factory=lambda: (_ for _ in ()).throw(RuntimeError("no browser")))
    exc=excinfo.value
    ctx=exc.context
    resolution=exc.resolution; resolution.list_only_used=True
    result=ManualCurlResult(ctx["path"], ctx["total"], ctx["score"], "HIGH", exc.jobs, 0,
        ctx["list_fetched"], ctx["unique"], [],
        "COMPLETE" if ctx["total"] is not None and ctx["list_fetched"]>=ctx["total"] else "PARTIAL",
        ctx["termination"], ctx["elapsed"], ctx["pages"], "LIST_ONLY")
    result.detail_resolution=resolution
    unified=manual_result_to_collection_result(result, "https://jobs.test/jobs")
    assert unified.status=="COMPLETE"
    assert len(unified.jobs)==3 and unified.total_unique==3
    audit=unified.enrichment["detail_resolution"]
    assert audit["detail_method"]=="LIST_ONLY" and audit["list_only_used"] is True
    assert audit["jd_success"]==0 and audit["jd_missing"]==3

# ⑨ partial: rendered page resolves some, one still missing
def test_partial_render_success_and_remaining_failure():
    good=(_job("1"), True)
    class FakePage:
        def __init__(self, ok): self.ok=ok
        def goto(self, url, wait_until=None): pass
        def evaluate(self, *_a, **_k):
            return CREDIBLE_JD if self.ok else "页面加载异常"
        def close(self): pass
    jobs=[_job("1"), _job("2")]
    resolution=resolve_targets(jobs, LIST_URL)
    targets=[t for t in resolution.targets if t.method=="RENDERED_PAGE"]
    pages={"1": FakePage(True), "2": FakePage(False)}
    state={"n":0}
    def factory():
        state["n"]+=1
        return FakePage(state["n"]==1)
    succeeded, failed=render_detail_jobs(factory, targets, concurrency=1, timeout_s=2.0)
    assert succeeded==1 and failed==1
    assert jobs[0].full_jd and not jobs[1].full_jd

# ⑩ audit metadata shape (no cookies, no cURL text)
def test_audit_metadata_has_no_secrets():
    jobs=[_job("1", full_jd=CREDIBLE_JD), _job("2")]
    resolution=resolve_targets(jobs, LIST_URL)
    resolution.rendered_page_attempted=True; resolution.rendered_page_success=0; resolution.rendered_page_failed=1
    audit=json.loads(json.dumps(resolution.audit()))
    assert audit["list_sufficient"]==1 and audit["rendered_page_attempted"] is True
    assert audit["rendered_page_failed"]==1 and audit["detail_method"]=="RENDERED_PAGE"
    blob=json.dumps(audit).lower()
    for secret in ("cookie", "curl ", "-h ", "authorization"):
        assert secret not in blob, f"audit leaked {secret!r}"

# ⑫ STEP93 manual-cURL regression: full-JD list keeps LIST_SUFFICIENT path
def test_step93_list_sufficient_regression_unchanged():
    payload={"data":{"list":[{"id":str(i),"jobName":f"岗位{i}","jobDuty":"职责足够完整。"*5,"jobRequirements":"要求足够完整。"*5} for i in range(1,3)],"total":2},"success":True}
    def handler(request):
        if "api/list" in str(request.url): return httpx.Response(200, json=payload)
        return httpx.Response(200, json={})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=run_manual_curl(LIST_CURL, None, max_jobs=None, client=client)
    assert result.detail_strategy=="LIST_SUFFICIENT"
    assert len(result.jobs)==2 and result.final_status=="COMPLETE"
    assert result.detail_resolution is not None
    assert result.detail_resolution.detail_method=="LIST_SUFFICIENT"
