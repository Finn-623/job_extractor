"""STEP 54C: weak URL activity must not masquerade as list evidence."""
from job_extractor.discovery.boot_recovery import classify_boot_state, reload_allowed
from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.observation import ActivityClock, url_is_job_semantic


CONFIG = {"Data": {"filters": [{"id": "sh", "label": "Shanghai"}]}, "Total": 1}
JOBS = {"Data": [{"id": str(i), "title": f"Engineer {i}", "city": "SH"} for i in range(20)], "Total": 20}


def decision(*, weak=0, strong=0, source=False):
    return classify_boot_state(
        domcontentloaded=True, elapsed_ms=5000, activity_observed=3,
        job_semantic_requests=weak, reliable_list_responses=strong,
        network_quiet_ms=2500, dom_job_ready=False, source_found=source,
        auth_or_captcha=False, environment_failure=False,
    )


def candidate(url, payload):
    return GenericApiDetector._candidate(_Observation(url, "GET", {}, {}, payload, "HYDRATION"))


def test_case1_hostname_jobs_does_not_create_weak_or_strong_evidence():
    url = "https://jobs.example.test/api/config"
    clock = ActivityClock()
    clock.record_json(url)
    assert not url_is_job_semantic(url)
    assert clock.job_semantic_count == 0 and clock.reliable_list_count == 0
    assert decision().should_reload


def test_case2_job_named_config_is_weak_but_not_strong():
    url = "https://example.test/api/jobs/search"
    clock = ActivityClock()
    clock.record_json(url)
    assert url_is_job_semantic(url) and clock.job_semantic_count == 1
    assert not GenericApiDetector._reliable_list_source(candidate(url, CONFIG))
    assert decision(weak=1).should_reload


def test_case3_structural_job_list_is_strong_and_suppresses_reload():
    item = candidate("https://example.test/api/jobs/search", JOBS)
    assert GenericApiDetector._reliable_list_source(item)
    clock = ActivityClock(); clock.record_json(item.url); clock.record_reliable_list()
    assert clock.reliable_list_count == 1
    assert not decision(weak=clock.job_semantic_count, strong=clock.reliable_list_count).should_reload


def test_case4_config_module_only_quiet_page_is_reload_eligible():
    assert decision().state == "QUIET_BOOT_STALL"
    assert reload_allowed(decision(), 0)


def test_case5_delayed_list_evidence_prevents_recovery_after_capture():
    # STEP53D supplies the bounded waiting; once its delayed response is
    # accepted by the existing scorer, strong evidence stops a needless reload.
    item = candidate("https://example.test/api/position/list", JOBS)
    assert GenericApiDetector._reliable_list_source(item)
    assert not decision(strong=1).should_reload


def test_case6_reload_is_bounded_to_one_attempt():
    stalled = decision()
    assert reload_allowed(stalled, 0)
    assert not reload_allowed(stalled, 1)
    assert not decision(source=True).should_reload


def test_case7_unrelated_apis_never_become_strong_list_evidence():
    for path in ("analytics/collect", "api/config", "api/module", "api/locale", "api/theme"):
        assert not GenericApiDetector._reliable_list_source(candidate(f"https://example.test/{path}", CONFIG))


def test_case8_hostname_keywords_are_excluded_from_weak_semantics():
    for host in ("career.example.test", "jobs.example.test", "recruit.example.test"):
        assert not url_is_job_semantic(f"https://{host}/api/config")
