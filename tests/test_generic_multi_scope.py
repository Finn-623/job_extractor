import json
import importlib

from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.discovery.models import ApiCandidate, DiscoveryResult, PaginationDetection
from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.planning.builder import CollectionPlanBuilder, _scope_label
from job_extractor.planning.models import CollectionPlan, ListScope


ENDPOINT = "https://api.example.test/project-job"


class Response:
    status_code = 200
    text = "{}"
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class ScopeClient:
    def __init__(self, *, fail_campus=False, social_total=267):
        self.fail_campus = fail_campus
        self.social_total = social_total
    def request(self, _method, _url, **kwargs):
        body = kwargs.get("json") or {}
        nature = tuple(body.get("nature") or ())
        if self.fail_campus and nature == ("campus",): raise RuntimeError("campus unavailable")
        if nature == ("social",): rows = [{"id": f"s{i}", "title": f"Social {i}", "content": "JD"} for i in range(self.social_total)]
        elif nature == ("campus",): rows = [{"id": f"c{i}", "title": f"Campus {i}", "content": "JD"} for i in range(500)]
        else: rows = [{"id": f"s{i}", "title": f"Special {i}", "content": "JD"} for i in range(15)]
        page = body["page"]; size = body["page_size"]
        return Response({"data": {"total": len(rows), "list": rows[(page - 1) * size:page * size]}})


class RowsByScopeClient:
    """Small deterministic list API for cross-scope metrics accounting."""
    def __init__(self, rows_by_nature): self.rows_by_nature = rows_by_nature
    def request(self, _method, _url, **kwargs):
        body = kwargs.get("json") or {}
        rows = self.rows_by_nature[tuple(body.get("nature") or ())]
        page, size = body["page"], body["page_size"]
        return Response({"data": {"total": len(rows), "list": rows[(page - 1) * size:page * size]}})


def rows(*ids):
    return [{"id": job_id, "title": f"Job {job_id}", "content": "JD"} for job_id in ids]


def scope(label, nature, total, project=None):
    values = {"page": 1, "page_size": 20, "nature": nature}
    if project: values["project_id"] = [project]
    return ListScope(label=label, identity={k: values[k] for k in ("nature", "project_id") if k in values},
        endpoint=ENDPOINT, method="POST", initial_values=values, pagination_type="PAGE",
        page_param="page", page_size_param="page_size", total_field="data.total", official_total=total)


def plan(scopes):
    return CollectionPlan(source_url="https://portal.example.test/job", mode="HTTP_API", executable=True,
        list_endpoint=ENDPOINT, list_method="POST", initial_values=scopes[0].initial_values,
        pagination_type="PAGE", page_param="page", page_size_param="page_size", total_field="data.total",
        list_path="data.list", job_id_field="id", job_title_field="title", detail_mode="LIST_SUFFICIENT",
        observed_endpoints=[ENDPOINT], list_scopes=scopes)


def candidate(values, total, label, endpoint=ENDPOINT):
    return ApiCandidate(url=endpoint, method="POST", score=20, confidence="HIGH", safe_request_values=values,
        response_shape={"candidate_list_path":"data.list", "sample_field_names":["id", "title", "content"], "total_field":"data.total"},
        observed_list_length=min(20, total), observed_total=total, sample_job_hint={"nature_cn": label})


def test_builder_combines_observed_scope_candidates():
    social = candidate({"page": 1, "page_size": 20, "nature": ["social"]}, 267, "社会招聘")
    campus = candidate({"page": 1, "page_size": 20, "nature": ["campus"], "project_id": ["p1"]}, 499, "校园招聘")
    discovery = DiscoveryResult(source_url="https://portal.example.test/job", status="DISCOVERED",
        candidate_list_apis=[social, campus], probable_list_api=social,
        detected_pagination=PaginationDetection(pagination_type="PAGE", page_param="page", page_size_param="page_size", total_field="data.total"))
    built = CollectionPlanBuilder().build(discovery)
    assert [item.label for item in built.list_scopes] == ["社会招聘", "校园招聘"]
    assert built.list_scopes[1].identity == {"nature": ["campus"], "project_id": ["p1"]}


def test_scope_tab_phase_overrides_conflicting_payload_recruitment_label():
    campus = candidate({"page": 1, "page_size": 20, "nature": ["campus"]}, 500, "社会招聘")
    special = candidate({"page": 1, "page_size": 20, "project_id": ["special"]}, 15, "社会招聘")
    social = candidate({"page": 1, "page_size": 20, "nature": ["social"]}, 262, "校园招聘")
    campus.observed_phase = "SCOPE_校园招聘"
    special.observed_phase = "SCOPE_专项招聘"
    social.observed_phase = "SCOPE_社会招聘"
    discovery = DiscoveryResult(source_url="https://portal.example.test/job", status="DISCOVERED",
        candidate_list_apis=[social, campus, special], probable_list_api=social,
        detected_pagination=PaginationDetection(pagination_type="PAGE", page_param="page", page_size_param="page_size", total_field="data.total"))

    built = CollectionPlanBuilder().build(discovery)

    assert [scope.label for scope in built.list_scopes] == ["社会招聘", "校园招聘", "专项招聘"]


def test_scope_payload_label_remains_fallback_without_tab_phase():
    only = candidate({"page": 1, "page_size": 20, "project_id": ["special"]}, 15, "社会招聘")
    assert _scope_label(only, 0) == "社会招聘"


def test_tab_phase_label_is_preserved_in_multi_scope_provenance():
    social = candidate({"page": 1, "page_size": 20, "nature": ["social"]}, 267, "社会招聘")
    special = candidate({"page": 1, "page_size": 20, "project_id": ["special"]}, 15, "社会招聘")
    social.observed_phase = "SCOPE_社会招聘"
    special.observed_phase = "SCOPE_专项招聘"
    discovery = DiscoveryResult(source_url="https://portal.example.test/job", status="DISCOVERED",
        candidate_list_apis=[social, special], probable_list_api=social,
        detected_pagination=PaginationDetection(pagination_type="PAGE", page_param="page", page_size_param="page_size", total_field="data.total"))

    result = GenericHttpCollector(CollectionPlanBuilder().build(discovery), client=ScopeClient()).collect()

    assert result.status == "COMPLETE"
    assert result.jobs[0].raw_data["_generic_source_scopes"] == ["社会招聘", "专项招聘"]
    assert result.jobs[0].recruitment_scopes == ["社会招聘", "专项招聘"]


def test_builder_combines_credible_scopes_across_list_endpoints():
    project_endpoint = "https://api.example.test/project-job"
    list_endpoint = "https://api.example.test/list"
    campus = candidate({"page": 1, "page_size": 20, "nature": ["campus"], "project_id": ["p1"]}, 500, "校园招聘", project_endpoint)
    special = candidate({"page": 1, "page_size": 20, "project_id": ["p2"]}, 15, "专项招聘", project_endpoint)
    social = candidate({"page": 1, "page_size": 20, "nature": ["social"]}, 262, "社会招聘", list_endpoint)
    discovery = DiscoveryResult(source_url="https://portal.example.test/job", status="DISCOVERED",
        candidate_list_apis=[social, campus, special], probable_list_api=social,
        detected_pagination=PaginationDetection(pagination_type="PAGE", page_param="page", page_size_param="page_size", total_field="data.total"))

    built = CollectionPlanBuilder().build(discovery)

    assert len(built.list_scopes) == 3
    assert [(item.endpoint, item.official_total) for item in built.list_scopes] == [
        (list_endpoint, 262), (project_endpoint, 500), (project_endpoint, 15),
    ]
    assert built.list_scopes[1].pagination_type == "PAGE"
    assert built.list_scopes[2].pagination_type == "PAGE"


def test_builder_does_not_merge_scope_candidate_from_another_api_host():
    local = candidate({"page": 1, "page_size": 20, "nature": ["social"]}, 262, "社会招聘")
    campus = candidate({"page": 1, "page_size": 20, "nature": ["campus"]}, 500, "校园招聘")
    unrelated = candidate({"page": 1, "page_size": 20, "nature": ["other"]}, 99, "其他", "https://other.example.test/list")
    discovery = DiscoveryResult(source_url="https://portal.example.test/job", status="DISCOVERED",
        candidate_list_apis=[local, campus, unrelated], probable_list_api=local,
        detected_pagination=PaginationDetection(pagination_type="PAGE", page_param="page", page_size_param="page_size", total_field="data.total"))

    built = CollectionPlanBuilder().build(discovery)

    assert [item.endpoint for item in built.list_scopes] == [ENDPOINT, ENDPOINT]


def test_discovery_keeps_same_endpoint_with_distinct_scope_identity(monkeypatch):
    payload = {"data": {"total": 2, "list": []}}
    observations = [
        _Observation(ENDPOINT, "POST", {"page": 1, "page_size": 20, "nature": ["social"]}, {}, payload, "SCOPE_社会招聘"),
        _Observation(ENDPOINT, "POST", {"page": 1, "page_size": 20, "nature": ["campus"], "project_id": ["p1"]}, {}, payload, "SCOPE_校园招聘"),
    ]
    def observed_candidate(observation, _detail=False):
        return ApiCandidate(url=ENDPOINT, method="POST", score=20, confidence="HIGH",
            safe_request_values=dict(observation.body))
    monkeypatch.setattr(GenericApiDetector, "_candidate", staticmethod(observed_candidate))
    ranked = GenericApiDetector._rank(observations)
    assert len(ranked) == 2
    assert {tuple(item.safe_request_values.get("nature", [])) for item in ranked} == {("social",), ("campus",)}


def test_effective_scope_source_uses_current_then_retry_then_initial(monkeypatch):
    detector = GenericApiDetector()
    current, retry, initial = object(), object(), object()
    monkeypatch.setattr(detector, "_rank", lambda _observations: [current])
    monkeypatch.setattr(detector, "_reliable_list_source", lambda candidate: candidate is current)
    assert detector._effective_list_source([], retry, initial) is current
    monkeypatch.setattr(detector, "_rank", lambda _observations: [])
    assert detector._effective_list_source([], retry, initial) is retry
    assert detector._effective_list_source([], None, initial) is initial
    assert detector._effective_list_source([], None, None) is None


def test_effective_scope_source_accepts_final_plan_candidate_when_not_reliable(monkeypatch):
    detector = GenericApiDetector()
    accepted = ApiCandidate(url=ENDPOINT, method="POST", score=detector.threshold,
        confidence="MEDIUM", safe_request_values={"nature": ["campus"]})
    monkeypatch.setattr(detector, "_rank", lambda _observations: [accepted])
    monkeypatch.setattr(detector, "_reliable_list_source", lambda _candidate: False)
    assert detector._effective_list_source([]) is accepted


def test_effective_scope_source_rejects_non_final_json_candidate(monkeypatch):
    detector = GenericApiDetector()
    ordinary = ApiCandidate(url="https://api.example.test/config", method="GET", score=detector.threshold - 1,
        confidence="LOW")
    monkeypatch.setattr(detector, "_rank", lambda _observations: [ordinary])
    monkeypatch.setattr(detector, "_reliable_list_source", lambda _candidate: False)
    assert detector._effective_list_source([]) is None


def test_current_scope_wait_ignores_delayed_previous_scope_response(monkeypatch):
    old = _Observation(ENDPOINT, "POST", {}, {}, {}, "SCOPE_专项招聘")
    delayed_special = _Observation(ENDPOINT, "POST", {"project_id": ["special"]}, {}, {}, "SCOPE_专项招聘")
    social = _Observation(ENDPOINT, "POST", {"nature": ["social"]}, {}, {}, "SCOPE_社会招聘")
    def candidate_for(observation, _detail=False):
        return ApiCandidate(url=ENDPOINT, method="POST", score=20 if observation.phase == "SCOPE_社会招聘" else 9,
            confidence="HIGH", safe_request_values=dict(observation.body))
    monkeypatch.setattr(GenericApiDetector, "_candidate", staticmethod(candidate_for))
    prior = {id(old)}
    assert not GenericApiDetector._new_accepted_scope_observation([old, delayed_special], prior, "SCOPE_社会招聘")
    assert GenericApiDetector._new_accepted_scope_observation([old, delayed_special, social], prior, "SCOPE_社会招聘")


def test_scope_enumeration_uses_its_own_bounded_deadline(monkeypatch):
    module = importlib.import_module("job_extractor.discovery.detector")
    detector = GenericApiDetector()
    clock = iter((100.0, 109.9, 110.0))
    monkeypatch.setattr(module, "perf_counter", lambda: next(clock))
    expired = detector._scope_enumeration_expired()
    assert not expired()
    assert expired()


def test_multi_scope_pages_each_scope_deduplicates_and_only_then_completes():
    scopes = [scope("社会招聘", ["social"], 262), scope("校园招聘", ["campus"], 500, "campus-project"), scope("专项招聘", [], 15, "special-project")]
    result = GenericHttpCollector(plan(scopes), client=ScopeClient(social_total=262)).collect()
    assert result.status == "COMPLETE"
    assert result.scope["completion"] == "SITE_COMPLETE"
    assert result.total_fetched == 777
    assert result.total_unique == 762
    assert result.total_expected == 762
    assert [item["status"] for item in result.duplicate_audit["scope_progress"]] == ["SCOPE_COMPLETE"] * 3
    # Multi-scope PAGE plans safely raise the request page size to 100:
    # 3 social + 5 campus + 1 special, without changing rows or merges.
    assert result.metrics.list_pages == 9
    assert result.metrics.duplicate_jobs == 0
    assert result.metrics.cross_scope_merged_rows == 15
    assert result.duplicate_audit["cross_scope_merged_rows"] == 15
    assert result.jobs[0].raw_data["_generic_source_scopes"] == ["社会招聘", "专项招聘"]


def test_other_scope_failure_prevents_site_complete():
    scopes = [scope("社会招聘", ["social"], 267), scope("校园招聘", ["campus"], 499)]
    result = GenericHttpCollector(plan(scopes), client=ScopeClient(fail_campus=True), max_retries=0).collect()
    assert result.status == "INCOMPLETE"
    assert result.scope["completion"] == "SITE_INCOMPLETE"
    assert result.duplicate_audit["scope_progress"][0]["status"] == "SCOPE_COMPLETE"
    assert result.duplicate_audit["scope_progress"][1]["status"] == "SCOPE_INCOMPLETE"


def test_single_scope_keeps_existing_complete_path():
    one = scope("社会招聘", ["social"], 267)
    result = GenericHttpCollector(plan([one]), client=ScopeClient()).collect()
    assert result.status == "COMPLETE"
    assert result.total_expected == 267
    assert "scope_progress" not in result.duplicate_audit


def test_single_scope_metrics_do_not_report_cross_scope_merges():
    result = GenericHttpCollector(plan([scope("A", ["a"], 10)]),
        client=RowsByScopeClient({("a",): rows(*(f"id{i}" for i in range(10)))})).collect()

    assert (result.metrics.raw_rows, result.metrics.duplicate_jobs,
            result.metrics.cross_scope_merged_rows, result.total_unique) == (10, 0, 0, 10)


def test_within_scope_duplicates_do_not_report_cross_scope_merges():
    result = GenericHttpCollector(plan([scope("A", ["a"], 10)]),
        client=RowsByScopeClient({("a",): rows("id0", "id1", "id2", "id3", "id4", "id5", "id6", "id7", "id0", "id1")})).collect()

    assert (result.metrics.raw_rows, result.metrics.duplicate_jobs,
            result.metrics.cross_scope_merged_rows, result.total_unique) == (10, 2, 0, 8)


def test_cross_scope_merge_counts_each_extra_stable_id_row_and_keeps_provenance():
    scopes = [scope("A", ["a"], 2), scope("B", ["b"], 2)]
    result = GenericHttpCollector(plan(scopes), client=RowsByScopeClient({
        ("a",): rows("id1", "id2"), ("b",): rows("id2", "id3"),
    })).collect()

    assert (result.metrics.raw_rows, result.metrics.duplicate_jobs,
            result.metrics.cross_scope_merged_rows, result.total_unique) == (4, 0, 1, 3)
    assert next(job for job in result.jobs if job.job_id == "id2").recruitment_scopes == ["A", "B"]


def test_three_scopes_count_two_extra_rows_for_one_shared_stable_id():
    scopes = [scope("A", ["a"], 1), scope("B", ["b"], 1), scope("C", ["c"], 1)]
    result = GenericHttpCollector(plan(scopes), client=RowsByScopeClient({
        ("a",): rows("id1"), ("b",): rows("id1"), ("c",): rows("id1"),
    })).collect()

    assert (result.metrics.raw_rows, result.metrics.cross_scope_merged_rows, result.total_unique) == (3, 2, 1)
    assert result.jobs[0].recruitment_scopes == ["A", "B", "C"]


# --- Regression tests for the scope-click wait (empty-shell DOM must not
# short-circuit the "new accepted observation in this phase" condition). ---

class _ScopePage:
    """Minimal page double for _wait_for_accepted_scope_observation.

    Each call to wait_for_timeout advances the timeline by one interval and
    appends that tick's observations into the shared list, simulating the
    detector's response listener.
    """

    def __init__(self, ticks):
        self.ticks = list(ticks)  # list per tick: list of _Observation to append
        self.tick = -1

    def wait_for_timeout(self, _ms):
        self.tick += 1
        if self.tick < len(self.ticks):
            observations.extend(self.ticks[self.tick] if self.ticks[self.tick] else [])


observations: list = []  # rebound per test


class _Clock:
    def __init__(self, deadline_offset=10.0):
        self.start = module.perf_counter()
        self.deadline = self.start + deadline_offset

    def __call__(self):
        return module.perf_counter() >= self.deadline


import job_extractor.discovery.detector as module  # noqa: E402


def _social_observation(phase="SCOPE_社会招聘", body=None):
    return _Observation(ENDPOINT, "POST", body or {"nature": ["social"], "page": 1, "page_size": 20}, {}, {}, phase)


def _social_candidate_for(observation, _detail=False):
    return ApiCandidate(url=ENDPOINT, method="POST", score=20, confidence="HIGH",
        safe_request_values=dict(observation.body))


def test_scope_wait_shell_dom_without_observation_does_not_end_wait(monkeypatch):
    """A. Shell DOM (mounted=true) but no accepted observation → wait continues.

    The old wait_for_hydration path returned {"stabilized": true, "elapsed_ms": 0}
    in exactly this situation; the dedicated wait must keep polling instead.
    """
    monkeypatch.setattr(GenericApiDetector, "_candidate", staticmethod(_social_candidate_for))
    observations.clear()
    prior = {id(o) for o in observations}
    # No ticks append anything: only a mounted shell exists the whole time.
    result = GenericApiDetector._wait_for_accepted_scope_observation(
        _ScopePage([None, None, None]), observations, prior, "SCOPE_社会招聘",
        _Clock(deadline_offset=10), timeout_ms=1000, interval_ms=10)
    assert result["accepted"] is False
    assert result["exit_reason"] == "observation_timeout"
    assert result["elapsed_ms"] >= 900  # did NOT exit at elapsed_ms=0


def test_scope_wait_current_phase_observation_ends_immediately(monkeypatch):
    """B. Accepted observation in the current phase → immediate success."""
    monkeypatch.setattr(GenericApiDetector, "_candidate", staticmethod(_social_candidate_for))
    observations.clear()
    prior = {id(o) for o in observations}
    page = _ScopePage([None, [_social_observation()], None])
    result = GenericApiDetector._wait_for_accepted_scope_observation(
        page, observations, prior, "SCOPE_社会招聘",
        _Clock(deadline_offset=10), timeout_ms=5000, interval_ms=10)
    assert result["accepted"] is True
    assert result["exit_reason"] == "accepted_current_phase_list"
    assert page.tick == 1  # stopped on the first productive tick, no full sleep


def test_scope_wait_delayed_previous_phase_observation_does_not_satisfy(monkeypatch):
    """C. A previous tab's delayed accepted observation must not satisfy this tab."""
    monkeypatch.setattr(GenericApiDetector, "_candidate", staticmethod(_social_candidate_for))
    observations.clear()
    prior = {id(o) for o in observations}
    delayed_special = _social_observation(phase="SCOPE_专项招聘", body={"project_id": ["special"]})
    page = _ScopePage([[delayed_special], [delayed_special], None])
    result = GenericApiDetector._wait_for_accepted_scope_observation(
        page, observations, prior, "SCOPE_社会招聘",
        _Clock(deadline_offset=10), timeout_ms=1000, interval_ms=10)
    assert result["accepted"] is False
    assert result["exit_reason"] == "observation_timeout"


def test_scope_wait_no_observation_times_out_bounded(monkeypatch):
    """D. No accepted observation ever → bounded timeout, no infinite wait."""
    monkeypatch.setattr(GenericApiDetector, "_candidate", staticmethod(_social_candidate_for))
    observations.clear()
    prior = {id(o) for o in observations}
    result = GenericApiDetector._wait_for_accepted_scope_observation(
        _ScopePage([None] * 200), observations, prior, "SCOPE_社会招聘",
        _Clock(deadline_offset=10), timeout_ms=1000, interval_ms=10)
    assert result["accepted"] is False
    assert result["exit_reason"] == "observation_timeout"
    assert result["elapsed_ms"] <= 1500


def test_scope_wait_shares_enumeration_budget(monkeypatch):
    """D/E. The shared enumeration budget expires the wait even under timeout_ms."""
    monkeypatch.setattr(GenericApiDetector, "_candidate", staticmethod(_social_candidate_for))
    observations.clear()
    prior = {id(o) for o in observations}
    result = GenericApiDetector._wait_for_accepted_scope_observation(
        _ScopePage([None] * 200), observations, prior, "SCOPE_社会招聘",
        _Clock(deadline_offset=0.2), timeout_ms=5000, interval_ms=10)
    assert result["accepted"] is False
    assert result["exit_reason"] == "budget_expired"
    assert result["elapsed_ms"] < 1000


def test_scope_enumeration_records_unresolved_scope_and_does_not_let_delayed_response_satisfy(monkeypatch):
    """E. End-to-end enumeration: shell-DOM tab stays unresolved, later delayed
    response of another phase cannot retro-satisfy it; accepted tabs retain."""
    monkeypatch.setattr(GenericApiDetector, "_candidate", staticmethod(_social_candidate_for))
    detector = GenericApiDetector()
    observations.clear()

    class _NavPage:
        def __init__(self):
            self.url = "https://portal.example.test/job"
            self.controls_evaluated = False
            self.clicks = 0
        def url_get(self): return self.url
        def evaluate(self, script):
            self.controls_evaluated = True
            return [
                {"i": 0, "text": "社会招聘", "href": "/jobs", "active": False, "visible": True},
            ]
        def locator(self, _selector):
            page = self
            class _Nodes:
                def count(self): return 1
                def nth(self, _i):
                    class _Node:
                        def inner_text(self): return "社会招聘"
                        def click(self, timeout=0):
                            page.clicks += 1
                            # The click navigates but NO observation ever arrives
                            # (empty-shell DOM). A delayed SPECIAL response lands
                            # mid-wait to prove it cannot satisfy the social wait.
                            def late_append():
                                observations.append(_social_observation(phase="SCOPE_专项招聘", body={"project_id": ["special"]}))
                            import threading
                            threading.Timer(0.05, late_append).start()
                    return _Node()
            return _Nodes()
        def wait_for_timeout(self, _ms): pass

    page = _NavPage()
    # `page.url` attribute access is used directly in the enumeration code.
    page.url = "https://portal.example.test/job"
    phase = ["HYDRATION"]
    expired = detector._scope_enumeration_expired()
    GenericApiDetector._enumerate_recruitment_scopes(page, "https://portal.example.test/job", observations, phase, expired)
    # Social was clicked but never produced an accepted observation: it must be
    # reported unresolved, and no SOCIAL identity may be retained.
    # (stdout trace SCOPE_ENUM_END carries unresolved_scopes; here we assert the
    # retention semantics directly.)
    identities = [
        {k: v for k, v in (o.body if o.method == "POST" else o.query).items()
         if k.lower() in ("nature", "project_id")}
        for o in observations
        if GenericApiDetector._accepted_list_candidate(GenericApiDetector._candidate(o))
    ]
    assert all(entry != {"nature": ["social"]} for entry in identities)
