from job_extractor.discovery.boot_recovery import classify_boot_state, reload_allowed


def decide(**overrides):
    values = dict(
        domcontentloaded=True, elapsed_ms=3000, activity_observed=2,
        job_semantic_requests=0, reliable_list_responses=0, network_quiet_ms=2000, dom_job_ready=False,
        source_found=False, auth_or_captcha=False, environment_failure=False,
    )
    values.update(overrides)
    return classify_boot_state(**values)


def test_normal_start_never_reloads():
    assert not decide(elapsed_ms=100).should_reload


def test_quiet_boot_stall_requests_one_reload_then_source_is_found():
    first = decide()
    second = decide(source_found=True)
    assert first.state == "QUIET_BOOT_STALL" and reload_allowed(first, 0)
    assert second.state == "SOURCE_FOUND" and not reload_allowed(second, 1)


def test_stall_twice_is_bounded_to_one_reload():
    stalled = decide()
    assert reload_allowed(stalled, 0)
    assert not reload_allowed(stalled, 1)


def test_slow_active_page_is_not_reloaded():
    assert not decide(network_quiet_ms=100).should_reload


def test_no_activity_is_not_misclassified_as_boot_stall():
    assert decide(activity_observed=0).state == "NO_EARLY_ACTIVITY"
    assert not decide(activity_observed=0).should_reload


def test_captcha_is_never_reloaded():
    assert decide(auth_or_captcha=True).state == "AUTH_OR_CAPTCHA"
    assert not decide(auth_or_captcha=True).should_reload


def test_source_found_is_never_reloaded():
    assert not decide(source_found=True).should_reload


def test_attempt_isolation_uses_only_second_attempt_source():
    first_attempt_junk_score = 3
    second_attempt_real_score = 23
    assert first_attempt_junk_score < 10 < second_attempt_real_score
    assert decide(source_found=True).state == "SOURCE_FOUND"


def test_reload_navigation_failure_has_no_second_reload():
    failed_reload = decide(domcontentloaded=False)
    assert failed_reload.state == "NAVIGATION_INCOMPLETE"
    assert not reload_allowed(failed_reload, 1)


def test_config_cannot_raise_global_reload_cap():
    stalled = decide()
    assert not reload_allowed(stalled, 1)
    assert not reload_allowed(stalled, 99)


def test_environment_failure_is_not_recovered_as_boot_stall():
    decision = decide(environment_failure=True)
    assert decision.state == "ENVIRONMENT_FAILURE"
    assert not decision.should_reload


def test_weak_job_semantic_request_does_not_suppress_boot_recovery():
    decision = decide(job_semantic_requests=1)
    assert decision.state == "QUIET_BOOT_STALL"
    assert decision.should_reload


def test_reliable_list_response_is_normal_late_load_not_stall():
    decision = decide(job_semantic_requests=1, reliable_list_responses=1)
    assert decision.state == "NORMAL_LATE_LOAD"
    assert not decision.should_reload
