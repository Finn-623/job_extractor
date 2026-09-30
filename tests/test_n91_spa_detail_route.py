"""N9.1: evidence-backed SPA detail route candidates + identity guard.

The detail URL pattern (e.g. /jobs/detail?code=<code>) comes from real
list-DOM anchors; construction stays generic (API-path convention, no
hostname hardcoding). A rendered detail page whose stable-id query does
not match the job must never fill JD content.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from job_extractor.collectors.detail_resolver import (
    _DETAIL_ROUTE_HINTS,
    build_detail_url_candidates,
    resolve_targets,
)


API_LIST_URL = "https://careers.example.com/api/recruit/position/list"


def test_code_is_recognized_as_a_stable_identity_key():
    from job_extractor.collectors.detail_resolver import _ID_KEYS
    assert "code" in _ID_KEYS


def test_spa_route_hints_exist():
    assert "jobs/detail" in _DETAIL_ROUTE_HINTS


def test_api_list_url_builds_spa_route_candidate_first():
    candidates = build_detail_url_candidates(API_LIST_URL,
                                             {"code": "T022809", "name": "风控策略工程师"})
    assert candidates, "expected candidates"
    assert candidates[0] == f"{API_LIST_URL.rsplit('/', 1)[0].rsplit('/api', 1)[0]}" \
                           f"/jobs/detail?code=T022809".replace("//jobs", "/jobs") \
        or candidates[0] == "https://careers.example.com/jobs/detail?code=T022809"
    assert any(c == "https://careers.example.com/jobs/detail?code=T022809"
               for c in candidates)


def test_spa_route_candidate_is_probe_first_for_api_paths():
    candidates = build_detail_url_candidates(API_LIST_URL, {"code": "A"})
    first = candidates[0]
    assert urlsplit(first).path == "/jobs/detail"
    assert "code=A" in first


def test_campaign_page_routes_keep_established_order():
    candidates = build_detail_url_candidates(
        "https://skyworth.hotjob.cn/SU668b8b251c240e2e76ea71d8/",
        {"postId": "6a6fee0a1ad6db7cf8e19b45", "postType": "campus"})
    assert candidates[0].startswith("https://skyworth.hotjob.cn/SU668b8b251c240e2e76ea71d8/")
    assert "posDetail.html" in candidates[0]


def test_resolve_targets_classifies_pdd_records_as_rendered_page():
    record = {"code": "T022809", "name": "风控策略工程师", "workLocation": "上海"}
    from job_extractor.models import Job
    job = Job(job_id="T022809", job_title="风控策略工程师",
              source_url=API_LIST_URL, raw_data={"list": record})
    resolution = resolve_targets([job], API_LIST_URL)
    assert resolution.rendered_page_candidates == 1
    target = resolution.targets[0]
    assert target.method == "RENDERED_PAGE"
    assert target.detail_urls[0] == "https://careers.example.com/jobs/detail?code=T022809"
    assert resolution.detail_url_pattern == "https://careers.example.com/jobs/detail?code={id}" \
        or "{code}" in (resolution.detail_url_pattern or "") or resolution.detail_url_pattern


def _fake_page(url: str, render: str):
    class FakePage:
        def goto(self, target, wait_until=None): pass
        @property
        def url(self): return url
        def evaluate(self, *_a, **_k): return render
        def close(self): pass
    return FakePage()


def test_jd_code_mismatch_rejects_backfill(monkeypatch):
    from job_extractor.collectors import detail_resolver as dr
    from job_extractor.models import Job
    job = Job(job_id="T022809", job_title="风控策略工程师",
              source_url=API_LIST_URL, raw_data={"list": {"code": "T022809"}})
    resolution = resolve_targets([job], API_LIST_URL)
    target = resolution.targets[0]
    target.detail_urls = ["https://careers.example.com/jobs/detail?code=T022809"]
    monkeypatch.setattr(dr, "fetch_rendered_jd",
                        lambda page, url, **kw: {"ok": True, "sections": {
                            "responsibilities": "1. x", "requirements": "1. y"},
                            "elapsed": 0.1})
    factory = lambda: _fake_page(
        "https://careers.example.com/jobs/detail?code=T018722",
        "岗位职责\n1. 负责风控。")
    succeeded, failed = dr.render_detail_jobs(factory, [target], concurrency=1,
                                              timeout_s=0.4, total_timeout_s=1.0)
    assert succeeded == 0 and failed == 1
    assert target.failure_code == "JD_CODE_MISMATCH"
    assert not job.full_jd and not job.responsibilities




def test_matching_code_backfills_and_mismatch_does_not(monkeypatch):
    from job_extractor.collectors import detail_resolver as dr
    from job_extractor.models import Job
    job_match = Job(job_id="T022809", job_title="岗位A",
                    source_url=API_LIST_URL, raw_data={"list": {"code": "T022809"}})
    job_mismatch = Job(job_id="T022810", job_title="岗位B",
                       source_url=API_LIST_URL, raw_data={"list": {"code": "T022810"}})
    res = resolve_targets([job_match, job_mismatch], API_LIST_URL)
    targets = {t.job.job_id: t for t in res.targets}

    def factory():
        return _fake_page("https://careers.example.com/jobs/detail?code=T022809",
                          "岗位职责\n1. 负责风控策略建模与迭代，输出风控规则与模型策略。\n2. 参与反欺诈与反作弊策略体系设计，跟踪业务效果并持续优化。\n任职要求\n1. 本科及以上学历，计算机、数学或相关专业。\n2. 熟悉常用机器学习算法与风控业务流程，具备良好的工程能力。")
    succeeded, failed = dr.render_detail_jobs(factory, [targets["T022809"],
                                                        targets["T022810"]],
                                              concurrency=1, timeout_s=0.5,
                                              total_timeout_s=2.0)
    assert succeeded == 1 and failed == 1
    assert targets["T022809"].job.full_jd and targets["T022809"].detail_url
    assert targets["T022810"].failure_code == "JD_CODE_MISMATCH"
    assert not targets["T022810"].job.full_jd


def test_single_failure_does_not_stop_the_batch(monkeypatch):
    from job_extractor.collectors import detail_resolver as dr
    from job_extractor.models import Job
    jobs = [Job(job_id=f"X{i}", job_title=f"岗位{i}", source_url=API_LIST_URL,
                raw_data={"list": {"code": f"X{i}"}}) for i in range(3)]
    resolution = resolve_targets(jobs, API_LIST_URL)
    for t, job in zip(resolution.targets, jobs):
        t.job = job
    calls = {"n": 0}
    def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("navigation destroyed")
        return _fake_page(f"https://careers.example.com/jobs/detail?code="
                          f"{'X1' if calls['n'] == 2 else 'X2'}",
                          "岗位职责\n1. 负责核心服务端系统的设计、开发与稳定性保障，推动关键链路性能优化与容量规划。\n2. 参与架构评审与代码评审，沉淀通用组件，提升团队研发效率与系统可观测性。\n任职要求\n1. 本科及以上学历，计算机相关专业，具备扎实的数据结构与算法基础。\n2. 熟悉分布式系统设计原则，具备良好的问题定位能力与团队协作意识。")
    succeeded, failed = dr.render_detail_jobs(factory, resolution.targets,
                                              concurrency=1, timeout_s=0.4,
                                              total_timeout_s=2.0)
    assert succeeded == 2 and failed == 1  # first job failed, rest filled
    assert resolution.targets[0].failure_code
    assert not jobs[0].full_jd and jobs[1].full_jd and jobs[2].full_jd
