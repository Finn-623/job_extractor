"""Activity-aware observation window for dynamic discovery.

STEP 53D: the historical detector stopped observing as soon as hydration looked
stable (or after a few fixed sleeps).  Slow job-list XHRs that arrived a few
hundred milliseconds to a few seconds after the early portal-config JSONs were
missed (diagnosis OBSERVATION_WINDOW_GAP for CXMT / NAURA / Guangzhou Metro,
STEP 53C).  This module provides a single GENERIC wait policy that keeps the
observation window open while the page is still active, and closes it only when
all of the following hold:

1. a minimum observation duration has elapsed (bounds fast pages),
2. no XHR/fetch activity for a quiet period (network is actually idle),
3. the DOM snapshot has been stable for consecutive polls,
4. no job-semantic request is still "in flight" or expected (bounded extension),
5. and never beyond a hard maximum deadline.

All values are configurable so tests can run deterministically with small
intervals.  Production defaults (tuned against live re-runs in this step):

MIN_OBSERVATION_MS = 2500
QUIET_PERIOD_MS    = 1500
MAX_OBSERVATION_MS = 12000

The policy is intentionally free of any hostname, company, provider, or path
special cases.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Callable
from urllib.parse import urlsplit

MIN_OBSERVATION_MS = 2500
QUIET_PERIOD_MS = 1500
MAX_OBSERVATION_MS = 12000
POLL_INTERVAL_MS = 250
DOM_STABLE_POLLS = 2
# While NO job-semantic request has been observed yet, the normal quiet exit is
# delayed by this much (min + grace).  Early portal-config JSONs alone therefore
# cannot end the window before a slow SPA job-list chain had a fair chance.
SEMANTIC_GRACE_MS = 5000

# Generic job-semantic activity terms (URL based, no site/provider/path rules).
JOB_SEMANTIC_TERMS = (
    "job", "jobs", "position", "positions", "career", "careers", "recruit",
    "recruitment", "hiring", "vacancy", "vacancies", "jobad", "requisition",
    "joblist", "jobpost", "talent", "职位", "岗位", "招聘",
)
# Generic config/dictionary-style terms: their JSON arriving can never, by
# itself, make the observation look "finished" (early unrelated JSON).
NON_JOB_SEMANTIC_TERMS = (
    "config", "dictionary", "city", "department", "agent", "robot", "portal",
    "area", "region", "province", "menu", "navigation", "banner", "tenant",
    "dict", "option", "category", "filter", "setting", "module", "global",
)
SEMANTIC_EXTENSION_FACTOR = 2


def url_is_job_semantic(url: str) -> bool:
    """Whether a request path/query carries weak generic job semantics.

    The hostname is intentionally excluded: a portal hosted at
    ``jobs.example.test`` still has config requests, which are not job-list
    activity merely because the host happens to contain ``jobs``.
    """
    parts = urlsplit(url or "")
    low = (parts.path + "?" + parts.query).lower()
    return any(term in low for term in JOB_SEMANTIC_TERMS)


def url_is_config_semantic(url: str) -> bool:
    """Whether a request URL looks like portal config/dictionary data."""
    low = (url or "").lower()
    return any(term in low for term in NON_JOB_SEMANTIC_TERMS)


class ActivityClock:
    """Tracks the page-level activity timestamps consumed by the wait policy."""

    def __init__(self, clock: Callable[[], float] = perf_counter) -> None:
        self._clock = clock
        self.started_at = clock()
        self.last_activity_at: float | None = None
        self.last_json_at: float | None = None
        self.last_job_semantic_at: float | None = None
        self.json_count = 0
        self.activity_count = 0
        self.job_semantic_count = 0
        # Strong evidence is recorded only after the existing generic list
        # scorer/reliability gate accepts a response.  Unlike weak URL
        # semantics, it is safe to use to suppress boot recovery.
        self.reliable_list_count = 0

    def record_activity(self, url: str = "") -> None:
        """Any XHR/fetch response observed."""
        self.activity_count += 1
        self.last_activity_at = self._clock()

    def record_json(self, url: str = "") -> None:
        """A parsed JSON XHR/fetch response observed."""
        now = self._clock()
        self.json_count += 1
        self.last_json_at = now
        self.last_activity_at = now
        if url_is_job_semantic(url):
            self.job_semantic_count += 1
            self.last_job_semantic_at = now

    def record_reliable_list(self) -> None:
        """Record a structurally accepted job-list response."""
        self.reliable_list_count += 1

    def elapsed_ms(self) -> int:
        return int((self._clock() - self.started_at) * 1000)

    def quiet_ms(self, now: float | None = None) -> int:
        """Milliseconds since the last observed network activity."""
        reference = self.last_activity_at if self.last_activity_at is not None else self.started_at
        current = self._clock() if now is None else now
        return max(0, int((current - reference) * 1000))


def wait_for_activity_quiet(
    page,
    clock: ActivityClock,
    dom_snapshot: Callable[[], Any],
    *,
    min_observation_ms: int = MIN_OBSERVATION_MS,
    quiet_period_ms: int = QUIET_PERIOD_MS,
    max_observation_ms: int = MAX_OBSERVATION_MS,
    poll_interval_ms: int = POLL_INTERVAL_MS,
    dom_stable_polls: int = DOM_STABLE_POLLS,
    semantic_grace_ms: int = SEMANTIC_GRACE_MS,
    has_high_confidence_source: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Wait until page activity is settled, then return the decision evidence.

    Termination requires ALL of: minimum duration reached (plus a semantic grace
    extension while no job-semantic request has been seen at all), network quiet
    for the quiet period, a stable DOM snapshot, and no pending job-semantic
    extension.  A HIGH-confidence source allows an early exit after one short
    quiet tail so discovery does not idle once the answer is already known.  The
    hard maximum deadline always terminates the loop (no unbounded waiting).
    """
    started = clock._clock()
    previous_dom = None
    stable_polls = 0
    polls = 0
    reason = "QUIET_REACHED"
    while True:
        now = clock._clock()
        elapsed = (now - started) * 1000
        quiet = clock.quiet_ms(now)
        try:
            current_dom = dom_snapshot()
        except Exception:
            current_dom = None
        if current_dom == previous_dom and previous_dom is not None:
            stable_polls += 1
        else:
            stable_polls = 0
        previous_dom = current_dom
        polls += 1

        if elapsed >= max_observation_ms:
            reason = "MAX_OBSERVATION_DEADLINE"
            break
        if has_high_confidence_source() and quiet >= min(quiet_period_ms, 500) and stable_polls >= 1:
            reason = "SOURCE_FOUND_EARLY_EXIT"
            break
        pending_semantic = (
            clock.last_job_semantic_at is not None
            and (now - clock.last_job_semantic_at) * 1000 < quiet_period_ms * SEMANTIC_EXTENSION_FACTOR
        )
        # A page that has not produced ANY job-semantic request yet is probably
        # still spinning up its SPA chain: hold the window open (bounded) even
        # if early config JSONs already look quiet and stable.
        awaiting_first_semantic = clock.job_semantic_count == 0 and elapsed < min_observation_ms + semantic_grace_ms
        if (
            elapsed >= min_observation_ms
            and quiet >= quiet_period_ms
            and stable_polls >= dom_stable_polls
            and not pending_semantic
            and not awaiting_first_semantic
        ):
            break
        page.wait_for_timeout(min(poll_interval_ms, max(0, max_observation_ms - elapsed)))
        if clock._clock() - started >= max_observation_ms / 1000:
            reason = "MAX_OBSERVATION_DEADLINE"
            break
    return {
        "reason": reason,
        "elapsed_ms": int((clock._clock() - started) * 1000),
        "polls": polls,
        "json_observed": clock.json_count,
        "activity_observed": clock.activity_count,
        "job_semantic_requests": clock.job_semantic_count,
        "reliable_list_responses": clock.reliable_list_count,
        "network_quiet_ms_at_exit": clock.quiet_ms(),
    }
