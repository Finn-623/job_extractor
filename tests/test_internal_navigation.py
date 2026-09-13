import json
from urllib.parse import urljoin

from job_extractor.discovery.detector import GenericApiDetector,_Observation
from job_extractor.discovery.internal_navigation import (
    InternalNavigationRuntime, discover_job_route_candidates, score_job_route_candidate,
    is_recruitment_route_in_scope, run_internal_job_navigation,
)
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.discovery.scorer import score_list

BASE = "https://company.test/recruit/campus/e/#/campus/index"
JOBS_HREF = "#/campus/jobs"
JOBS_URL = urljoin(BASE, JOBS_HREF)


def anchors_page(anchor_specs, goto_log=None, click_log=None):
    """Fake page with hash-route anchors; navigating swaps hash, click appends to click_log."""
    current = {"url": BASE}
    nodes = []
    for index, (text, href, tag) in enumerate(anchor_specs):
        nodes.append({"id": str(index), "tag": tag, "text": text, "href": href,
                      "data_route": "", "data_url": "", "data_href": "", "role": "", "visible": True})

    class FakeLocatorNode:
        def __init__(self, spec):
            self.spec = spec
        def inner_text(self):
            return self.spec["text"]
        def click(self, timeout=3000):
            if click_log is not None:
                click_log.append(self.spec["text"])

    class FakeLocator:
        def __init__(self, items):
            self.items = items
        def count(self):
            return len(self.items)
        def nth(self, index):
            return FakeLocatorNode(self.items[index])

    class FakePage:
        def evaluate(self, script):
            return nodes
        def url(self=None):
            return current["url"]
        @property
        def url_prop(self):
            return current["url"]
        def goto(self, target, **kwargs):
            if goto_log is not None:
                goto_log.append(target)
            current["url"] = target
            return None
        def wait_for_timeout(self, ms):
            pass
        def locator(self, selector):
            return FakeLocator(nodes)
        @property
        def url(self):
            return current["url"]
    return FakePage()


def rank_fn_factory(observations, threshold=10):
    detector = GenericApiDetector.__new__(GenericApiDetector)
    def rank():
        ranked = GenericApiDetector._rank(observations)
        if ranked and ranked[0].score >= threshold:
            return ranked[0], ranked[0].score
        return None, 0
    return rank


# CASE 1 — hash-route anchor: high score, navigated, candidate activated
def test_case1_hash_route_anchor_scored_and_navigated():
    score, evidence = score_job_route_candidate("全部职位", JOBS_URL, BASE)
    assert score >= 8, evidence
    assert "strong job-list label" in evidence
    assert any("jobs" in item for item in evidence)

    page = anchors_page([("全部职位", JOBS_HREF, "a"), ("探索快手", "#/campus/about", "a")])
    goto_log = []
    page = anchors_page([("全部职位", JOBS_HREF, "a"), ("探索快手", "#/campus/about", "a")], goto_log=goto_log)
    jobs_payload = {"code": 0, "data": {"list": [{"id": i, "positionName": f"岗{i}"} for i in range(10)], "total": 300}}
    observations = [_Observation(JOBS_URL, "POST", {"pageNum": 1}, {}, jobs_payload, "HYDRATION")]
    detector = GenericApiDetector.__new__(GenericApiDetector)
    detector.threshold = GenericApiDetector.threshold
    trace = []
    def rank():
        ranked = GenericApiDetector._rank(observations)
        if ranked and ranked[0].score >= detector.threshold:
            return ranked[0], ranked[0].score
        return None, 0
    probable, trace = InternalNavigationRuntime(page, BASE, rank_fn=rank).run(max_attempts=3)
    assert probable is not None
    assert goto_log and goto_log[-1].endswith("#/campus/jobs")
    assert trace and trace[-1]["source_found"] is True
    assert trace[-1]["reason"] == "SOURCE_FOUND"


# CASE 2 — SPA tab with no URL change: click triggers job API, no hash change needed
def test_case2_button_click_without_url_change_still_succeeds():
    page = anchors_page([("应届招聘", "", "button")])
    jobs_payload = {"code": 0, "data": {"list": [
        {"id": i, "title": f"岗{i}", "location": "Shenzhen", "detailUrl": f"/jobs/{i}"}
        for i in range(10)
    ], "total": 100}}
    observations = [_Observation(BASE, "POST", {"pageNum": 1, "pageSize": 10}, {}, jobs_payload, "HYDRATION")]
    detector = GenericApiDetector.__new__(GenericApiDetector)
    detector.threshold = GenericApiDetector.threshold
    def rank():
        ranked = GenericApiDetector._rank(observations)
        if ranked and ranked[0].score >= detector.threshold:
            return ranked[0], ranked[0].score
        return None, 0
    probable, trace = InternalNavigationRuntime(page, BASE, rank_fn=rank).run(max_attempts=3)
    assert probable is not None
    assert trace and trace[-1]["source_found"] is True
    assert trace[-1]["action"] == "click"
    assert trace[-1]["to_url"] == trace[-1]["from_url"]  # no URL change


# CASE 3 — non-job recruitment content never activated
def test_case3_non_job_content_rejected():
    for text in ("招聘流程", "员工福利", "公司介绍"):
        score, evidence = score_job_route_candidate(text, urljoin(BASE, f"#/campus/{text}"), BASE)
        assert score < 8, (text, score, evidence)
    candidates = discover_job_route_candidates(anchors_page([
        ("招聘流程", "#/campus/schedule/process", "a"),
        ("员工福利", "#/campus/benefits", "a"),
        ("公司介绍", "#/campus/about", "a"),
    ]), BASE)
    assert all(c.score < 8 for c in candidates), [(c.text, c.score) for c in candidates]


# CASE 4 — external domain rejected
def test_case4_external_domain_rejected():
    score, evidence = score_job_route_candidate("全部职位", "https://external.test/jobs", BASE)
    assert score <= 0 and "EXTERNAL_DOMAIN" in evidence
    candidates = discover_job_route_candidates(anchors_page([("全部职位", "https://external.test/jobs", "a")]), BASE)
    assert all(c.score < 8 for c in candidates)


# CASE 5 — loop prevention
def test_case5_loop_prevention():
    page = anchors_page([("全部职位", JOBS_HREF, "a")])
    observed = {"targets": []}
    class LoopPage:
        def evaluate(self, script):
            return [{"id": "0", "tag": "a", "text": "全部职位", "href": JOBS_HREF, "data_route": "",
                     "data_url": "", "data_href": "", "role": "", "visible": True}]
        @property
        def url(self):
            return JOBS_URL if observed["targets"] else BASE
        def goto(self, target, **kwargs):
            observed["targets"].append(target)
        def wait_for_timeout(self, ms):
            pass
        def locator(self, selector):
            raise AssertionError("click should not be needed for anchors")
    def rank():
        return None, 0
    _, trace = InternalNavigationRuntime(LoopPage(), BASE, rank_fn=rank).run(max_attempts=5)
    # only one navigation attempt recorded even though budget allows more
    assert observed["targets"].count(JOBS_URL) == 1, observed
    assert len([t for t in trace if t.get("action") in ("navigate", "click")]) <= 1


# CASE 6 — budget: many candidates, no job source → bounded attempts + structured reason
def test_case6_budget_exhaustion_is_structured():
    specs = [(f"职位 {i}", f"#/campus/jobs?filter={i}", "a") for i in range(20)]
    page = anchors_page(specs)
    def rank():
        return None, 0
    _, trace = InternalNavigationRuntime(page, BASE, rank_fn=rank, max_attempts=3).run()
    assert len([t for t in trace if t.get("action") in ("navigate", "click")]) <= 3
    assert trace and trace[-1]["reason"] == "INTERNAL_JOB_NAVIGATION_EXHAUSTED"


# CASE 7 — HIGH job source already present → zero navigation attempts
def test_case7_existing_source_stops_navigation():
    jobs_payload = {"code": 0, "data": {"list": [{"id": i, "positionName": f"岗{i}"} for i in range(10)], "total": 300}}
    observations = [_Observation(BASE, "POST", {"pageNum": 1, "pageSize": 10}, {}, jobs_payload, "HYDRATION")]
    detector = GenericApiDetector.__new__(GenericApiDetector)
    detector.threshold = GenericApiDetector.threshold
    def rank():
        ranked = GenericApiDetector._rank(observations)
        if ranked and ranked[0].score >= detector.threshold:
            return ranked[0], ranked[0].score
        return None, 0
    goto_log = []
    page = anchors_page([("全部职位", JOBS_HREF, "a")], goto_log=goto_log)
    runtime = InternalNavigationRuntime(page, BASE, rank_fn=rank)
    probable, trace = runtime.run_if_needed()
    assert probable is not None
    assert runtime.attempts_used == 0
    assert not goto_log


# CASE 8 — same pathname, hash changes counts as navigation
def test_case8_same_pathname_hash_change_is_navigation():
    before = "https://company.test/recruit/campus/e/#/campus/index"
    after = "https://company.test/recruit/campus/e/#/campus/jobs"
    assert before != after  # hash route is part of the compared state
    page = anchors_page([("全部职位", JOBS_HREF, "a")])
    jobs_payload = {"code": 0, "data": {"list": [{"id": i, "positionName": f"岗{i}"} for i in range(10)], "total": 300}}
    observations = [_Observation(urljoin(BASE, JOBS_HREF), "POST", {"pageNum": 1, "pageSize": 10}, {}, jobs_payload, "HYDRATION")]
    detector = GenericApiDetector.__new__(GenericApiDetector)
    detector.threshold = GenericApiDetector.threshold
    def rank():
        ranked = GenericApiDetector._rank(observations)
        if ranked and ranked[0].score >= detector.threshold:
            return ranked[0], ranked[0].score
        return None, 0
    runtime = InternalNavigationRuntime(page, BASE, rank_fn=rank)
    probable, trace = runtime.run(max_attempts=3)
    assert probable is not None and trace
    assert trace[-1]["to_url"].endswith("#/campus/jobs")
    assert trace[-1]["from_url"].endswith("#/campus/index")


# CASE 9 — bare ambiguous wording never reaches the activation threshold alone
def test_case9_ambiguous_wording_low_score():
    score, evidence = score_job_route_candidate("招聘", urljoin(BASE, "#/about"), BASE)
    assert score < 8, (score, evidence)


# CASE 10 — Kuaishou-like fixture: landing → internal jobs route, no hostname/path special-casing
def test_case10_kuaishou_like_landing_finds_internal_jobs_route():
    landing_specs = [
        ("首页", "#/campus/index", "a"),
        ("快Star人才计划", "#/campus/talent", "a"),
        ("应届招聘", "#/campus/jobs?recruitSubProjectCodes=20271779425607", "a"),
        ("实习招聘", "#/campus/jobs?recruitSubProjectCodes=20271772783534", "a"),
        ("校招动态", "#/campus/schedule/process", "a"),
        ("探索快手", "#/campus/about", "a"),
        ("登 录", "#/campus/index/", "a"),
    ]
    page = anchors_page(landing_specs)
    goto_log = []
    page = anchors_page(landing_specs, goto_log=goto_log)
    # on the jobs route the SPA emits the real positions API
    simple_payload = {"code": 0, "message": "ok", "result": {"data": [{"id": i, "positionName": f"岗{i}"} for i in range(10)], "totalCount": 225, "pageNum": 1, "pageSize": 10}}
    observations = [_Observation(JOBS_URL, "POST", {"pageNum": 1, "pageSize": 10, "recruitSubProjectCodes": ["20271779425607"]}, {}, simple_payload, "HYDRATION")]
    detector = GenericApiDetector.__new__(GenericApiDetector)
    detector.threshold = GenericApiDetector.threshold
    def rank():
        ranked = GenericApiDetector._rank(observations)
        if ranked and ranked[0].score >= detector.threshold:
            return ranked[0], ranked[0].score
        return None, 0
    probable, trace = InternalNavigationRuntime(page, BASE, rank_fn=rank).run(max_attempts=3)
    assert probable is not None
    assert trace
    chosen = trace[-1]
    assert chosen["source_found"] is True and chosen["reason"] == "SOURCE_FOUND"
    assert any("jobs" in (t["candidate_href"] or "") for t in trace if t.get("candidate_href"))
    # no hostname/path special-casing anywhere in the module
    source = open("job_extractor/discovery/internal_navigation.py").read().lower()
    for banned in ("kuaishou", "kwai", "campus.kuaishou", "kwimgs"):
        assert banned not in source, banned


# CASE 11 — navigation is gated to confirmed recruitment contexts
def test_case11_not_recruitment_context_is_structured():
    goto_log = []
    page = anchors_page([("全部职位", "/jobs", "a")], goto_log=goto_log)
    runtime = InternalNavigationRuntime(page, "https://company.test/about", rank_fn=lambda: (None, 0))
    probable, trace = runtime.run()
    assert probable is None and runtime.attempts_used == 0
    assert not goto_log
    assert trace == [{"reason": "NOT_RECRUITMENT_CONTEXT"}]


# CASE 12 — deadline stops before another candidate is activated
def test_case12_deadline_check_stops_navigation():
    goto_log = []
    page = anchors_page([
        ("全部职位", "#/campus/jobs?team=one", "a"),
        ("查看职位", "#/campus/jobs?team=two", "a"),
    ], goto_log=goto_log)
    checks = {"count": 0}
    def expired():
        checks["count"] += 1
        return checks["count"] >= 2
    probable, trace = run_internal_job_navigation(
        page, BASE, [], rank_fn=lambda: (None, 0), deadline_check=expired, max_attempts=3,
    )
    assert probable is None
    assert len(goto_log) == 1
    assert trace[-1]["reason"] == "DEADLINE_EXCEEDED"


# CASE 13 — candidate threshold is independent from source recognition
def test_case13_candidate_min_score_skips_weak_candidate():
    goto_log = []
    page = anchors_page([("招聘", "#/campus/talent", "a")], goto_log=goto_log)
    runtime = InternalNavigationRuntime(
        page, BASE, rank_fn=lambda: (None, 0), candidate_min_score=8.0,
    )
    _, trace = runtime.run()
    assert runtime.attempts_used == 0 and not goto_log
    assert any(item.get("reason") == "CANDIDATE_SCORE_BELOW_MINIMUM" for item in trace)


# CASE 14 — the structured trace survives on the discovery result contract
def test_case14_trace_persisted_on_discovery_result():
    trace = [{"reason": "DEADLINE_EXCEEDED"}]
    result = DiscoveryResult(source_url=BASE, status="NOT_FOUND", internal_navigation_trace=trace)
    assert result.model_dump()["internal_navigation_trace"] == trace


def test_recruitment_namespace_guard_allows_related_scopes_and_rejects_business_pages():
    for route in ("#/campus/jobs", "#/intern/positions", "#/graduate/opportunities"):
        assert is_recruitment_route_in_scope(BASE, urljoin(BASE, route))
    for route in ("#/campus/about", "#/news", "#/product", "#/privacy", "#/brand/marketing"):
        assert not is_recruitment_route_in_scope(BASE, urljoin(BASE, route))


# STEP 48B A1/A2 — generic reference records are not reliable job entities
def test_dictionary_like_records_are_not_high_reliable_job_sources():
    payloads = [
        {"result": {"list": [{"code": "BJ", "name": "北京"}, {"code": "SH", "name": "上海"}]}},
        {"result": {"list": [{"id": 1, "name": "技术"}, {"id": 2, "name": "产品"}]}},
        {"result": {"list": [
            {"id": 1, "code": "BJ", "name": "北京", "parentCode": "CN", "displayOrder": 1},
            {"id": 2, "code": "SH", "name": "上海", "parentCode": "CN", "displayOrder": 2},
        ]}},
    ]
    for payload in payloads:
        observation = _Observation("https://company.test/api/reference/batch", "GET", {}, {}, payload, "HYDRATION")
        candidate = GenericApiDetector._candidate(observation)
        assert candidate.confidence != "HIGH" or candidate.rejection_reasons
        assert not GenericApiDetector._reliable_list_source(candidate)
    assert "REFERENCE_DATA_PAYLOAD" in GenericApiDetector._candidate(
        _Observation("https://company.test/api/reference/batch", "GET", {}, {}, payloads[-1], "HYDRATION")
    ).rejection_reasons


# STEP 48B A3 — diverse, homogeneous job records retain HIGH recognition
def test_real_job_records_remain_high_and_reliable():
    payload = {"result": {"list": [
        {"id": i, "name": f"Engineer {i}", "location": "Sydney", "description": "Build systems", "requirement": "Python"}
        for i in range(10)
    ], "total": 100}}
    candidate = GenericApiDetector._candidate(
        _Observation("https://company.test/api/positions/simple", "GET", {"pageNum": 1, "pageSize": 10}, {}, payload, "HYDRATION")
    )
    assert candidate.confidence == "HIGH" and not candidate.rejection_reasons
    assert GenericApiDetector._reliable_list_source(candidate)


# STEP 48B A4 — an early dictionary response cannot stop navigation
def test_dictionary_before_jobs_does_not_trigger_early_stop():
    nodes = [
        {"id": "0", "tag": "a", "text": "全部职位", "href": "#/campus/jobs?scope=one", "data_route": "", "data_url": "", "data_href": "", "role": "", "visible": True},
        {"id": "1", "tag": "a", "text": "查看职位", "href": "#/campus/jobs?scope=two", "data_route": "", "data_url": "", "data_href": "", "role": "", "visible": True},
    ]
    dictionary = _Observation("https://company.test/api/reference", "GET", {}, {}, {"data": [
        {"id": i, "code": f"C{i}", "name": f"Option {i}", "parentCode": "ROOT"} for i in range(10)
    ]}, "HYDRATION")
    jobs = _Observation("https://company.test/api/positions", "GET", {"pageNum": 1}, {}, {"data": {"list": [
        {"id": i, "title": f"Job {i}", "location": "Sydney", "description": "Build"} for i in range(10)
    ], "total": 10}}, "HYDRATION")
    observations = [dictionary]
    class SequencePage:
        def __init__(self): self.current = BASE; self.navigations = 0
        @property
        def url(self): return self.current
        def evaluate(self, _script): return nodes
        def goto(self, target, **_kwargs):
            self.current = target; self.navigations += 1
            if self.navigations == 2: observations.append(jobs)
        def wait_for_timeout(self, _ms): pass
    page = SequencePage()
    def rank():
        ranked = GenericApiDetector._rank(observations)
        candidate = ranked[0] if ranked else None
        return (candidate, candidate.score) if candidate else (None, 0)
    probable, trace = InternalNavigationRuntime(page, BASE, rank, observations=observations).run()
    action_trace = [item for item in trace if item.get("action") == "navigate"]
    assert len(action_trace) == 2
    assert action_trace[0]["source_found"] is False
    assert action_trace[1]["reason"] == "SOURCE_FOUND" and probable.url.endswith("/positions")


# STEP 48B B1/B2/B3 — effective hash route and candidate text control scope
def test_about_and_opaque_company_routes_are_rejected_but_recruitment_siblings_pass():
    about = urljoin(BASE, "#/official/about/")
    opaque = urljoin(BASE, "#/official/jianghu/")
    for text, target in (("关于我们", about), ("关于快手", opaque)):
        score, _ = score_job_route_candidate(text, target, BASE)
        assert score < 4
        assert not is_recruitment_route_in_scope(BASE, target)
    for route in ("#/official/trainee/", "#/official/internship/", "#/official/graduate/", "#/official/jobs/"):
        target = urljoin(BASE, route)
        assert is_recruitment_route_in_scope(BASE, target)
