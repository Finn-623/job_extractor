"""STEP 53D synthetic timing tests for the activity-aware observation window.

All tests are deterministic: time is virtual (scripted clock) and the page's
``wait_for_timeout`` advances the virtual clock, so no real sleeping happens.
"""
from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.observation import (
    ActivityClock,
    url_is_config_semantic,
    url_is_job_semantic,
    wait_for_activity_quiet,
)
from job_extractor.discovery.scorer import score_list

JOBS_PAYLOAD = {"data": {"records": [{"id": str(i), "title": f"Engineer {i}", "city": "SH"} for i in range(6)]}}
CONFIG_PAYLOAD = {"items": [{"id": 1, "label": "beijing", "code": "010"}]}


class VirtualClock:
    """Scripted clock advanced in milliseconds by the fake page."""

    def __init__(self):
        self.now_ms = 0

    def __call__(self) -> float:
        return self.now_ms / 1000.0

    def advance(self, ms: int) -> None:
        self.now_ms += ms


class ScriptedPage:
    """Fake page whose ``goto``/``wait_for_timeout`` drive the virtual clock and
    whose scripted responses fire at scheduled times during waits."""

    def __init__(self, vclock: VirtualClock, schedule=None):
        self.vclock = vclock
        self.schedule = sorted(schedule or [], key=lambda item: item[0])
        self.fired = []
        self.listeners = {}
        self.pending = list(self.schedule)

    def _fire_due(self, target: int) -> None:
        while self.pending and self.pending[0][0] <= target:
            at, url, payload = self.pending.pop(0)
            self.vclock.now_ms = max(self.vclock.now_ms, at)
            self.fired.append(url)
            if "response" in self.listeners:
                self.listeners["response"](_Response(url, payload))

    def on(self, event, callback):
        self.listeners[event] = callback
        # Responses scheduled during initial page load fire against this handler.
        self._fire_due(self.vclock.now_ms)

    def goto(self, *_a, **_k):
        self._fire_due(self.vclock.now_ms)
        return None

    def wait_for_timeout(self, ms: int):
        target = self.vclock.now_ms + max(0, int(ms))
        self._fire_due(target)
        self.vclock.now_ms = target

    def locator(self, selector):
        if selector == "body":
            return _TextLocator("Jobs\nEngineer" if any(u.endswith("list") for u in self.fired) else "")
        return _ZeroLocator()

    def evaluate(self, *_a, **_k):
        return []


class _Response:
    def __init__(self, url, payload):
        self.url = url
        self.status = 200
        self.headers = {"content-type": "application/json"}
        self.request = _Request(url)
        self._payload = payload
        self.resource_type = "xhr"

    def json(self):
        return self._payload


class _Request:
    def __init__(self, url):
        self.url = url
        self.method = "GET"
        self.headers = {}
        self.post_data = None
        self.post_data_json = None
        self.resource_type = "xhr"


class _TextLocator:
    def __init__(self, text):
        self.text = text

    def inner_text(self, timeout=None):
        return self.text

    def count(self):
        return 0

    def nth(self, _i):
        return self


class _ZeroLocator:
    def inner_text(self, timeout=None):
        return ""

    def count(self):
        return 0

    def nth(self, _i):
        return self

    def get_attribute(self, _key):
        return None

    def click(self, timeout=None):
        pass


class _Browser:
    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _job_payload():
    return JOBS_PAYLOAD


def _snapshot_for(page):
    return lambda: ("sig", page.locator("body").inner_text())


def _record_into(clock):
    """A Playwright-style response listener that feeds the ActivityClock."""
    def _observe(response):
        if response.resource_type in ("xhr", "fetch"):
            clock.record_activity(response.url)
            try:
                payload = response.json()
                if isinstance(payload, (dict, list)):
                    clock.record_json(response.url)
            except Exception:
                pass
    return _observe


def _wait(page, clock, **kwargs):
    """Run the policy with test-sized windows (deterministic, small)."""
    return wait_for_activity_quiet(
        page,
        clock,
        _snapshot_for(page),
        min_observation_ms=kwargs.get("min_observation_ms", 1000),
        quiet_period_ms=kwargs.get("quiet_period_ms", 800),
        max_observation_ms=kwargs.get("max_observation_ms", 8000),
        poll_interval_ms=kwargs.get("poll_interval_ms", 200),
        dom_stable_polls=2,
        semantic_grace_ms=kwargs.get("semantic_grace_ms", 1500),
        has_high_confidence_source=kwargs.get("has_high_confidence_source", lambda: False),
    )


# ---------------------------------------------------------------- URL semantics

def test_url_semantics_classification():
    assert url_is_job_semantic("https://x.test/api/GetJobAdPageList")
    assert url_is_job_semantic("https://x.test/api/position/search")
    assert url_is_job_semantic("https://x.test/api/招聘/职位")
    assert not url_is_job_semantic("https://x.test/api/GetPortalAgentConfig")
    assert url_is_config_semantic("https://x.test/api/GetPortalAIRobot")
    assert url_is_config_semantic("https://x.test/api/city/list")
    assert not url_is_config_semantic("https://x.test/api/GetJobAdPageList")


def test_activity_clock_tracks_quiet_and_semantic():
    vclock = VirtualClock()
    clock = ActivityClock(clock=vclock)
    clock.record_json("https://x.test/api/config")
    vclock.advance(100)
    clock.record_json("https://x.test/api/jobs/list")
    assert clock.job_semantic_count == 1 and clock.json_count == 2
    vclock.advance(300)
    assert clock.quiet_ms() == 300


def _run_wait(page, clock, **kwargs):
    """Full detector-like flow: register listener, run policy with test windows."""
    page.on("response", _record_into(clock))
    return _wait(page, clock, **kwargs)


# ---------------------------------------------------------------- CASE 1
def test_case1_config_then_late_job_list_is_captured():
    """config JSON @1s, job list @5s: the window must not close before 5s."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock, schedule=[(1000, "https://x.test/api/config", CONFIG_PAYLOAD),
                                          (5000, "https://x.test/api/jobs/list", JOBS_PAYLOAD)])
    result = GenericApiDetector(lambda **_k: _Browser(page), timeout_ms=20000).discover("https://x.test/jobs")
    assert any(u.endswith("/api/jobs/list") for u in page.fired), "job request must fire before window closed"
    scored = [c for c in result.candidate_list_apis if c.url.endswith("/api/jobs/list")]
    assert scored and scored[0].score >= 10 and result.status == "DISCOVERED"


# ---------------------------------------------------------------- CASE 2
def test_case2_config_only_exits_after_quiet_not_max():
    """config JSON only, then silence: grace period ends, exits before max."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock, schedule=[(1000, "https://x.test/api/config", CONFIG_PAYLOAD)])
    clock = ActivityClock(clock=vclock)
    outcome = _run_wait(page, clock, max_observation_ms=8000)
    assert outcome["reason"] == "QUIET_REACHED"
    assert outcome["elapsed_ms"] < 6000, "must not wait until max deadline"
    assert outcome["json_observed"] == 1


# ---------------------------------------------------------------- CASE 3
def test_case3_source_found_short_quiet_then_early_exit():
    """job list @2s + HIGH-confidence source: short quiet tail then early exit."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock, schedule=[(2000, "https://x.test/api/jobs/list", JOBS_PAYLOAD)])
    clock = ActivityClock(clock=vclock)
    outcome = _run_wait(page, clock, max_observation_ms=10000, has_high_confidence_source=lambda: True)
    assert outcome["reason"] == "SOURCE_FOUND_EARLY_EXIT"
    assert outcome["elapsed_ms"] < 4500, "early exit must be shorter than a full quiet window"


# ---------------------------------------------------------------- CASE 4
def test_case4_multiple_unrelated_jsons_do_not_close_window_early():
    """unrelated JSONs @1s/2s/3s, job list @6s: window stays open for the list."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock, schedule=[
        (1000, "https://x.test/api/config", CONFIG_PAYLOAD),
        (2000, "https://x.test/api/city/list", CONFIG_PAYLOAD),
        (3000, "https://x.test/api/dict/area", CONFIG_PAYLOAD),
        (6000, "https://x.test/api/position/list", JOBS_PAYLOAD),
    ])
    result = GenericApiDetector(lambda **_k: _Browser(page), timeout_ms=25000).discover("https://x.test/careers")
    scored = [c for c in result.candidate_list_apis if c.url.endswith("/api/position/list")]
    assert scored and scored[0].score >= 10 and result.status == "DISCOVERED"


# ---------------------------------------------------------------- CASE 5
def test_case5_continuous_activity_hits_max_deadline():
    """continuous activity every ~400ms: policy stops at MAX deadline, not forever."""
    vclock = VirtualClock()
    schedule = [(i * 400, f"https://x.test/api/stream/{i}", CONFIG_PAYLOAD) for i in range(40)]
    page = ScriptedPage(vclock, schedule=schedule)
    clock = ActivityClock(clock=vclock)
    outcome = _run_wait(page, clock, max_observation_ms=5000, semantic_grace_ms=0)
    assert outcome["reason"] == "MAX_OBSERVATION_DEADLINE"
    assert 4500 <= outcome["elapsed_ms"] <= 6000


# ---------------------------------------------------------------- CASE 6
def test_case6_no_activity_exits_after_min_duration_and_dom_stable():
    """no network activity at all: min duration + DOM stable -> normal exit."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock)
    clock = ActivityClock(clock=vclock)
    outcome = _run_wait(page, clock)
    assert outcome["reason"] == "QUIET_REACHED"
    assert outcome["elapsed_ms"] >= 1000, "minimum observation duration must be respected"
    assert outcome["json_observed"] == 0


# ---------------------------------------------------------------- CASE 7
def test_case7_late_job_semantic_request_extends_window():
    """job-semantic request starts late: quiet clock restarts, window extends."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock, schedule=[
        (500, "https://x.test/api/config", CONFIG_PAYLOAD),
        (2500, "https://x.test/api/jobs/search", JOBS_PAYLOAD),
    ])
    clock = ActivityClock(clock=vclock)
    outcome = _run_wait(page, clock, max_observation_ms=9000)
    assert outcome["job_semantic_requests"] == 1
    # quiet measured from the late semantic request, not the early config
    assert outcome["elapsed_ms"] >= 2500


# ---------------------------------------------------------------- CASE 8
def test_case8_semantic_looking_dictionary_does_not_extend_forever():
    """dictionary response whose URL merely mentions a semantic-ish term must not
    hold the window open indefinitely — bounded extension only."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock, schedule=[
        (1000, "https://x.test/api/category/option/career-dictionary", CONFIG_PAYLOAD),
    ])
    clock = ActivityClock(clock=vclock)
    outcome = _run_wait(page, clock, quiet_period_ms=800, max_observation_ms=6000)
    assert outcome["reason"] == "QUIET_REACHED"
    assert outcome["elapsed_ms"] < 6000


# ------------------------------------------------- regression guards (53D §15)

def test_config_only_page_is_not_a_source():
    """dictionary/city/config JSONs must not become a source (false-positive audit)."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock, schedule=[(1000, "https://x.test/api/config", CONFIG_PAYLOAD),
                                          (1500, "https://x.test/api/city/list", CONFIG_PAYLOAD)])
    result = GenericApiDetector(lambda **_k: _Browser(page), timeout_ms=20000).discover("https://x.test/portal")
    assert result.status == "NOT_FOUND"
    assert result.probable_list_api is None
    assert all(c.rejection_reasons for c in result.candidate_list_apis)


def test_observation_policy_evidence_is_recorded():
    """summary must expose the observation policy decision for latency audits."""
    vclock = VirtualClock()
    page = ScriptedPage(vclock, schedule=[(2000, "https://x.test/api/jobs/list", JOBS_PAYLOAD)])
    result = GenericApiDetector(lambda **_k: _Browser(page), timeout_ms=20000).discover("https://x.test/jobs")
    policy = result.network_summary.observation_policy
    assert policy.get("reason") in ("QUIET_REACHED", "SOURCE_FOUND_EARLY_EXIT", "MAX_OBSERVATION_DEADLINE")
    assert policy.get("json_observed", 0) >= 1
    assert policy.get("job_semantic_requests", 0) == 1


def test_job_semantic_json_scores_unchanged_by_window_logic():
    """recognition thresholds untouched: same payload scores as before 53D."""
    score, _evidence, shape = score_list("https://x.test/api/GetJobAdPageList", JOBS_PAYLOAD)
    assert score >= 15 and not shape["rejection_reasons"]
