"""STEP 54A.1 — Invalid-total termination closure tests.

Generic behavior under test: when an envelope's primary total is invalid
(low total semantics: ``Total < observed record count`` on a non-empty page),
the analyzer must reject it and continue to a credible alternative total
field in the same envelope.  Without a credible alternative the plan must
keep failing closed.  No site/provider special cases — payloads below mirror
the observed YMTC/CXMT/Guangzhou-Metro lineage shapes only as generic
``{Total, Count, Data}`` envelope fixtures.
"""
from __future__ import annotations

from job_extractor.discovery.models import ApiCandidate, DiscoveryResult, PaginationDetection
from job_extractor.discovery.network_analyzer import find_total_field, response_shape
from job_extractor.discovery.pagination_semantics import find_total, infer_pagination_from_schema
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator, can_dispatch
from job_extractor.planning.execution_contract import missing_fields


def envelope(total, count=None, jobs=20):
    payload = {"PageIndex": 0, "PageSize": 20, "Total": total, "Data": [{"Id": str(n), "JobAdName": f"Role {n}"} for n in range(jobs)]}
    if count is not None:
        payload["Count"] = count
    return payload


def candidate(total_field, *, observed_total, length=20):
    return ApiCandidate(url="https://api.example.test/position/list", method="POST", score=22, confidence="HIGH", replayable=True,
        request_body_shape={"PageIndex": "int", "PageSize": "int"},
        safe_request_values={"PageIndex": 0, "PageSize": 20},
        response_shape={"candidate_list_path": "Data", "sample_field_names": ["Id", "JobAdName"], "total_field": total_field},
        observed_list_length=length, observed_total=observed_total, observed_unique_ids=length, job_entity_density=1.0)


def plan_for(total_field, observed_total):
    item = candidate(total_field, observed_total=observed_total)
    res = DiscoveryResult(source_url="https://careers.example.test/jobs", status="DISCOVERED", candidate_list_apis=[item], probable_list_api=item,
        detected_pagination=PaginationDetection(pagination_type="PAGE", page_param="PageIndex", page_size_param="PageSize", total_field=total_field, first_page=0, page_size=20))
    return CollectionPlanBuilder().build(res)


# CASE 1 — Total=0, Count=92, jobs=20 (YMTC-shape): reject Total, select Count.
def test_case1_zero_total_with_credible_count_selects_count():
    payload = envelope(0, count=92)
    assert response_shape(payload)["total_field"] == "Count"
    assert find_total(payload, 20) == ("Count", 92)
    inferred = infer_pagination_from_schema({"PageIndex": 0, "PageSize": 20}, {}, payload)
    assert inferred["total_path"] == "Count" and inferred["total_value"] == 92
    plan = plan_for("Count", observed_total=92)
    assert plan.executable and plan.total_field == "Count" and can_dispatch(plan)


# CASE 2 — Total=0, Count=193, jobs=20 (CXMT-shape): Count selected.
def test_case2_zero_total_count_193_selects_count():
    payload = envelope(0, count=193)
    assert response_shape(payload)["total_field"] == "Count"
    assert find_total(payload, 20) == ("Count", 193)
    plan = plan_for("Count", observed_total=193)
    assert plan.executable and plan.total_field == "Count"


# CASE 3 — Total=0, Count=20, jobs=20 (GZMetro-shape): Count==page size is a
# valid one-page total contract (Count >= observed holds, not contradicted).
def test_case3_zero_total_count_equals_page_size_valid_one_page_contract():
    payload = envelope(0, count=20)
    assert response_shape(payload)["total_field"] == "Count"
    assert find_total(payload, 20) == ("Count", 20)
    plan = plan_for("Count", observed_total=20)
    assert plan.executable and plan.total_field == "Count" and can_dispatch(plan)


# CASE 4 — Total=0, no Count, jobs=20: no credible total → fail closed.
def test_case4_zero_total_without_alternative_fails_closed():
    payload = envelope(0)
    assert response_shape(payload)["total_field"] is None
    assert find_total(payload, 20) == (None, None)
    inferred = infer_pagination_from_schema({"PageIndex": 0, "PageSize": 20}, {}, payload)
    assert inferred["total_path"] is None
    plan = plan_for(None, observed_total=0)
    assert not plan.executable and plan.total_field is None
    assert "TERMINATION_STRATEGY" in missing_fields(plan)


# CASE 5 — Total=5 < jobs=20: invalid low total rejected, Count selected.
def test_case5_total_below_observed_rejected_count_selected():
    payload = envelope(5, count=92)
    assert find_total(payload, 20) == ("Count", 92)
    assert response_shape(payload)["total_field"] == "Count"
    plan = plan_for("Count", observed_total=92)
    assert plan.executable and plan.total_field == "Count"


# CASE 6 — Total=100 >= jobs=20 with Count=92 present: existing tier
# precedence must hold — the tier1 total wins, Count is NOT preferred.
def test_case6_valid_tier1_total_keeps_precedence_over_count():
    payload = envelope(100, count=92)
    assert find_total(payload, 20) == ("Total", 100)
    assert response_shape(payload)["total_field"] == "Total"
    plan = plan_for("Total", observed_total=100)
    assert plan.executable and plan.total_field == "Total"


# CASE 7 — Total=0, Count=5 < jobs=20: both invalid → fail closed.
def test_case7_both_candidates_invalid_fails_closed():
    payload = envelope(0, count=5)
    assert response_shape(payload)["total_field"] is None
    assert find_total(payload, 20) == (None, None)
    inferred = infer_pagination_from_schema({"PageIndex": 0, "PageSize": 20}, {}, payload)
    assert inferred["total_path"] is None
    plan = plan_for(None, observed_total=5)
    assert not plan.executable and plan.total_conflict
    assert "TERMINATION_STRATEGY" in missing_fields(plan)


# CASE 8 — Total=0, Count="92" (string digit): canonical numeric handling.
def test_case8_string_digit_count_is_canonicalized():
    payload = {"PageIndex": 0, "PageSize": 20, "Total": 0, "Count": "92",
               "Data": [{"Id": str(n), "JobAdName": f"Role {n}"} for n in range(20)]}
    assert find_total(payload, 20) == ("Count", 92)
    assert response_shape(payload)["total_field"] == "Count"
    plan = plan_for("Count", observed_total=92)
    assert plan.executable and plan.total_field == "Count"


# CASE 9 — Total=0, Count malformed (non-numeric) → no credible total, fail closed.
def test_case9_malformed_count_fails_closed():
    for bad in (None, "abc", ["92"], {"v": 92}):
        payload = {"PageIndex": 0, "PageSize": 20, "Total": 0, "Count": bad,
                   "Data": [{"Id": str(n), "JobAdName": f"Role {n}"} for n in range(20)]}
        assert response_shape(payload)["total_field"] is None, bad
        assert find_total(payload, 20) == (None, None), bad
    plan = plan_for(None, observed_total=0)
    assert not plan.executable and plan.total_field is None


# CASE 10 — existing valid tier1 alias (total=50, jobs=20): behavior unchanged.
def test_case10_existing_valid_total_behavior_unchanged():
    payload = {"code": 0, "data": {"total": 50, "items": [{"id": str(n), "title": f"J{n}"} for n in range(20)]}}
    assert find_total(payload, 20) == ("data.total", 50)
    assert response_shape(payload)["total_field"] == "data.total"
    inferred = infer_pagination_from_schema({"page": 1, "pageSize": 20}, {}, payload)
    assert inferred["total_path"] == "data.total" and inferred["total_value"] == 50


# False-positive guard — facet/filter/city/department counts must never be
# promoted to the job total just because a "Count" key exists somewhere.
def test_facet_like_count_without_total_context_does_not_win():
    payload = {"Filters": {"CityCount": 12, "DepartmentCount": 7},
               "CityCount": 31, "DepartmentCount": 8,
               "Data": [{"Id": str(n), "JobAdName": f"Role {n}"} for n in range(20)]}
    # 'count' is tier2: plain CityCount/DepartmentCount are not semantic
    # 'count' keys; without any tier1/total-like field the result stays None.
    assert response_shape(payload)["total_field"] is None
    assert find_total(payload, 20) == (None, None)


def test_total_below_observed_never_selected_even_without_alternative():
    payload = {"Total": 3, "Count": 2, "Data": [{"Id": str(n)} for n in range(20)]}
    assert find_total(payload, 20) == (None, None)
    assert response_shape(payload)["total_field"] is None
