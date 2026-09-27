"""STEP97 regression: the first source-discovery stage shares ONE deadline.

Guarantees under test:
- every navigation/wait inside ``GenericApiDetector.discover`` consumes only the
  remaining shared source budget (``source_budget_seconds=25`` stays unchanged);
- a fully hung page converges at the source budget instead of the serial sum of
  independent timeouts (goto 15s + hydration 5s + activity 7.5s + readiness 15s
  ≈ 44s);
- a healthy page whose source appears early is unaffected;
- the scope-enumeration budget (10s) stays separate.

Timing model: detector deadlines use real ``perf_counter``, so the hung-page
scenario (D) uses a small real source budget; helper-level tests (A/B/C) use
fake clocks where possible.
"""
from time import perf_counter

from job_extractor.discovery.detector import GenericApiDetector
from job_extractor.discovery.dynamic import (
    wait_for_hydration,
    wait_for_readiness_consensus,
)
from job_extractor.discovery.observation import ActivityClock, wait_for_activity_quiet

from tests.test_step53d_observation import (
    CONFIG_PAYLOAD,
    JOBS_PAYLOAD,
    ScriptedPage,
    VirtualClock,
    _Browser,
)


class _InstantPage:
    """A page double with no url attribute (regression guard for the trace)."""

    def on(self, _event, _callback):
        pass

    def goto(self, *_a, **_k):
        pass

    def reload(self, *_a, **_k):
        pass

    def wait_for_timeout(self, *_a):
        pass

    def evaluate(self, *_a, **_k):
        return None

    def locator(self, _selector):
        return _InstantLocator()


class _InstantLocator:
    def count(self):
        return 0

    def nth(self, _index):
        return self

    def inner_text(self, timeout=None):
        return ""

    def get_attribute(self, _key):
        return None

    def first(self):
        return self


class _InstantBrowser:
    def __init__(self):
        self.page = _InstantPage()

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _CountingPage(_InstantPage):
    """Instant page that records the timeout_ms it was navigated with."""

    def __init__(self):
        self.goto_timeouts = []

    def goto(self, *args, timeout=None, **_k):
        self.goto_timeouts.append(timeout)


class _CountingBrowser:
    def __init__(self):
        self.page = _CountingPage()

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _NullSnapshotPage(_InstantPage):
    """Page for helper-level waits: evaluate returns a stable SPA signal."""

    def evaluate(self, *_a, **_k):
        return {"readyState": "loading", "rootChildren": 0, "textLength": 0}


# ------------------------------------------------------------------ A. readiness

def test_A_readiness_consensus_capped_by_remaining_deadline():
    """Readiness with a nominal 15s cap must stop when the shared deadline fires."""
    observed = []
    page = _NullSnapshotPage()

    calls = {"n": 0}

    def deadline():
        calls["n"] += 1
        return calls["n"] >= 2  # fire almost immediately

    started = perf_counter()
    wait_for_readiness_consensus(page, lambda: len(observed), timeout_ms=15000, deadline_check=deadline)
    real_elapsed = perf_counter() - started
    assert observed == [], "no candidate was produced"
    assert real_elapsed < 2.0, (
        f"readiness consensus must honour the shared deadline, took {real_elapsed:.2f}s"
    )


def test_A2_readiness_consensus_default_behaviour_unchanged():
    """Without deadline_check, readiness returns the same shape (no deadline key)."""
    observed = []
    page = _NullSnapshotPage()
    outcome = wait_for_readiness_consensus(page, lambda: len(observed), timeout_ms=600)
    assert observed == [], "no candidate was produced"
    assert outcome["source_observed"] is False
    assert outcome["dom_stabilized"] is False


# ------------------------------------------------------------------ B. activity

def test_B_activity_quiet_exits_immediately_on_caller_deadline():
    """deadline_check fires mid-loop -> immediate CALLER_DEADLINE_EXPIRED exit."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock)
    clock = ActivityClock(clock=vclock)
    polls = {"n": 0}

    def deadline():
        polls["n"] += 1
        return polls["n"] > 3

    outcome = wait_for_activity_quiet(
        page, clock, lambda: {"sig": polls["n"]},
        min_observation_ms=1000, quiet_period_ms=800,
        max_observation_ms=30000, poll_interval_ms=200,
        dom_stable_polls=2, semantic_grace_ms=1500,
        has_high_confidence_source=lambda: False,
        deadline_check=deadline,
    )
    assert outcome["reason"] == "CALLER_DEADLINE_EXPIRED"
    assert outcome["elapsed_ms"] < 30000, "must not idle to the max deadline"


def test_B2_activity_quiet_without_deadline_check_is_unchanged():
    vclock = VirtualClock()
    page = ScriptedPage(vclock)
    clock = ActivityClock(clock=vclock)
    outcome = wait_for_activity_quiet(
        page, clock, lambda: {"sig": 1},
        min_observation_ms=300, quiet_period_ms=200,
        max_observation_ms=1000, poll_interval_ms=100,
        dom_stable_polls=1, semantic_grace_ms=0,
    )
    assert outcome["reason"] in ("QUIET_REACHED", "MAX_OBSERVATION_DEADLINE")


# ------------------------------------------------------------------ C. hydration

def test_C_hydration_capped_by_remaining_deadline():
    """Hydration stops the moment the shared deadline expires (real clocks)."""
    page = _NullSnapshotPage()
    started = perf_counter()
    outcome = wait_for_hydration(
        page, lambda: 0,
        timeout_ms=10000, interval_ms=200,
        deadline_check=lambda: (perf_counter() - started) >= 0.6,
    )
    real_elapsed = perf_counter() - started
    assert outcome["stabilized"] is False
    assert real_elapsed < 2.0, (
        f"hydration must honour the shared deadline, took {real_elapsed:.2f}s"
    )


def test_C2_hydration_default_behaviour_unchanged():
    page = _NullSnapshotPage()
    outcome = wait_for_hydration(page, lambda: 0, timeout_ms=500, interval_ms=250)
    assert outcome["stabilized"] is False
    assert outcome["elapsed_ms"] >= 500


# ------------------------------------------------------------------ D. hung page

def test_D_hung_page_converges_at_source_budget_not_44s():
    """A fully hung page (0 observations) must converge near the source budget.

    Serial-wait regression: goto(15s) + hydration(5s) + activity(7.5s) +
    readiness(15s) ≈ 44s.  With the shared deadline the whole discovery
    lifecycle is bounded by ``source_budget_seconds``.
    """
    detector = GenericApiDetector(
        browser_factory=lambda **_k: _InstantBrowser(),
        source_budget_seconds=3,  # scaled-down real budget; 25s itself unchanged
    )
    started = perf_counter()
    result = detector.discover("https://hung.test/jobs")
    elapsed = perf_counter() - started
    assert result.status in ("TIMEOUT", "UNSUPPORTED", "PARTIAL"), result.status
    assert result.failure_classification == "SOURCE_DISCOVERY_TIMEOUT" or "SOURCE_DISCOVERY_TIMEOUT" in result.warnings
    # Convergence at the budget: generous 1.5x guard for real-clock slop.
    assert elapsed < 3 * 1.5, (
        f"discovery must converge at the source budget, took {elapsed:.2f}s "
        "(serial independent timeouts would exceed this)"
    )


def test_D2_goto_timeout_capped_by_remaining_source_budget():
    """page.goto timeout can never exceed the remaining shared budget."""
    browser = _CountingBrowser()
    detector = GenericApiDetector(
        browser_factory=lambda **_k: browser,
        timeout_ms=15000,
        source_budget_seconds=3,
    )
    detector.discover("https://hung.test/jobs")
    # goto is the first navigation; its timeout must be <= budget ms.
    assert browser.page.goto_timeouts, "goto must have been attempted"
    assert browser.page.goto_timeouts[0] <= 3 * 1000


# ------------------------------------------------------------------ E. healthy page

def test_E_healthy_early_source_unaffected():
    """Source found early on a healthy page still ends DISCOVERED."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock, schedule=[
        (1000, "https://x.test/api/jobs/list", JOBS_PAYLOAD),
    ])
    result = GenericApiDetector(lambda **_k: _Browser(page), timeout_ms=20000).discover("https://x.test/jobs")
    scored = [c for c in result.candidate_list_apis if c.url.endswith("/api/jobs/list")]
    assert scored and scored[0].score >= 10
    assert result.status == "DISCOVERED"
    assert "SOURCE_DISCOVERY_TIMEOUT" not in result.warnings


def test_E2_healthy_page_with_config_then_job_list_unaffected():
    vclock = VirtualClock()
    page = ScriptedPage(vclock, schedule=[
        (1000, "https://x.test/api/config", CONFIG_PAYLOAD),
        (5000, "https://x.test/api/jobs/list", JOBS_PAYLOAD),
    ])
    result = GenericApiDetector(lambda **_k: _Browser(page), timeout_ms=25000).discover("https://x.test/jobs")
    scored = [c for c in result.candidate_list_apis if c.url.endswith("/api/jobs/list")]
    assert scored and scored[0].score >= 10 and result.status == "DISCOVERED"


# ------------------------------------------------------------------ F. budget separation

def test_F_scope_budget_stays_independent():
    """recruitment_scope_budget_seconds stays 10 and separate from source budget."""
    assert GenericApiDetector.recruitment_scope_budget_seconds == 10
    detector = GenericApiDetector(source_budget_seconds=25)
    assert detector.source_budget_seconds == 25
    expired = detector._scope_enumeration_expired()
    assert expired() is False
    assert hasattr(expired, "deadline")
