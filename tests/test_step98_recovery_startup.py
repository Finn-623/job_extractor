"""Regression coverage for the shared-deadline boot-recovery partition."""

from time import perf_counter

from job_extractor.discovery.detector import GenericApiDetector
from job_extractor.discovery.observation import MIN_OBSERVATION_MS


class _Page:
    url = "https://portal.test/job"


def test_recovery_reserve_keeps_startup_window_inside_default_source_budget():
    detector = GenericApiDetector(source_budget_seconds=25)
    reserve = detector._recovery_reserve_ms(detector.source_budget_seconds)
    assert detector.source_budget_seconds == 25
    assert reserve >= MIN_OBSERVATION_MS
    assert 25_000 - reserve >= MIN_OBSERVATION_MS
    assert reserve == (
        detector._RECOVERY_RELOAD_ALLOWANCE_MS
        + detector._RECOVERY_HYDRATION_ALLOWANCE_MS
        + detector._RECOVERY_TRIGGER_ALLOWANCE_MS
        + MIN_OBSERVATION_MS
    )


def test_recovery_without_source_reexecutes_existing_job_trigger(monkeypatch):
    detector = GenericApiDetector()
    calls = []
    monkeypatch.setattr(detector, "_rank", lambda _observations: [])
    monkeypatch.setattr(detector, "_reliable_list_source", lambda _candidate: False)
    monkeypatch.setattr(
        detector,
        "_job_page_search_trigger",
        lambda page, *, timeout_ms=None: calls.append(timeout_ms) or ["招聘职位"],
    )

    trace = detector._recovery_trigger(_Page(), [], perf_counter() + 10)

    assert calls and 0 < calls[0] <= detector._RECOVERY_TRIGGER_ALLOWANCE_MS
    assert trace["recovery_trigger_attempted"] is True
    assert trace["recovery_trigger_result"] == "CLICKED"
    assert trace["clicked_text"] == ["招聘职位"]
    assert trace["url_before"] == trace["url_after"] == "https://portal.test/job"


def test_recovery_hydration_source_skips_duplicate_trigger(monkeypatch):
    detector = GenericApiDetector()
    source = object()
    monkeypatch.setattr(detector, "_rank", lambda _observations: [source])
    monkeypatch.setattr(detector, "_reliable_list_source", lambda candidate: candidate is source)
    monkeypatch.setattr(detector, "_job_page_search_trigger", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not trigger")))

    trace = detector._recovery_trigger(_Page(), [object()], perf_counter() + 10)

    assert trace["recovery_trigger_attempted"] is False
    assert trace["recovery_trigger_result"] == "SKIPPED_ACCEPTED_SOURCE"


def test_recovery_trigger_preserves_minimum_observation_time(monkeypatch):
    detector = GenericApiDetector()
    monkeypatch.setattr(detector, "_rank", lambda _observations: [])
    monkeypatch.setattr(detector, "_reliable_list_source", lambda _candidate: False)
    captured = []
    monkeypatch.setattr(detector, "_job_page_search_trigger", lambda _page, *, timeout_ms=None: captured.append(timeout_ms) or [])

    detector._recovery_trigger(_Page(), [], perf_counter() + (MIN_OBSERVATION_MS + 1_000) / 1000)

    assert captured and captured[0] <= 1_000
    assert captured[0] < MIN_OBSERVATION_MS
