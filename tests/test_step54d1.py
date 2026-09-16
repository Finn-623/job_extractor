"""STEP54D.1 — generic JD field-name normalization."""

from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.field_semantics import canonical_jd, needs_detail_fetch, pick_jd_fields
from job_extractor.runtime import evaluate_data_completeness
from tests.test_generic_collectors import Client, plan


RESP = "负责建设和维护稳定的核心服务，持续改进可靠性与用户体验。"
REQ = "本科及以上学历，熟悉 Python、分布式系统和团队协作。"


def collect(record):
    return GenericHttpCollector(
        plan(), Client([{"data": {"items": [record], "total": 1}}])
    ).collect()


def test_case1_title_case_duty_require_are_recognized():
    found = pick_jd_fields({"Duty": RESP, "Require": REQ})
    assert found["responsibilities"] == RESP and found["requirements"] == REQ
    assert found["responsibilities_field"] == "Duty"
    assert found["requirements_field"] == "Require"


def test_case2_lowercase_duty_require_are_recognized():
    found = pick_jd_fields({"duty": RESP, "require": REQ})
    assert found["responsibilities"] == RESP and found["requirements"] == REQ


def test_case3_uppercase_duty_require_are_recognized():
    found = pick_jd_fields({"DUTY": RESP, "REQUIRE": REQ})
    assert found["responsibilities"] == RESP and found["requirements"] == REQ


def test_case4_existing_split_aliases_keep_precedence():
    found = pick_jd_fields({"responsibilities": RESP, "requirements": REQ, "Duty": "later", "Require": "later"})
    assert found["responsibilities"] == RESP and found["requirements"] == REQ


def test_case5_empty_require_does_not_fabricate_requirements():
    found = pick_jd_fields({"Duty": RESP, "Require": ""})
    assert found["responsibilities"] == RESP and found["requirements"] is None


def test_case6_require_login_is_not_requirements():
    found = pick_jd_fields({"Duty": RESP, "requireLogin": REQ})
    assert found["requirements"] is None


def test_case7_required_count_is_not_requirements():
    found = pick_jd_fields({"Duty": RESP, "requiredCount": 99})
    assert found["requirements"] is None


def test_case8_credible_split_is_list_sufficient_without_detail_fetch():
    record = {"id": "1", "title": "Engineer", "Duty": RESP, "Require": REQ}
    result = collect(record)
    job = result.jobs[0]
    assert job.full_jd and job.responsibilities and job.requirements
    assert job.raw_data["_jd_state"] == "SPLIT"
    assert evaluate_data_completeness(result).jd_complete == 1
    assert result.metrics.detail_requests == 0 and not needs_detail_fetch(record, "DETAIL_FALLBACK")


def test_case9_short_teaser_is_not_promoted_to_full_text():
    full, state, _resp, _req = canonical_jd({"Duty": "协助", "Require": "本科"})
    assert state == "SUMMARY" and full is None
    result = collect({"id": "1", "title": "Engineer", "Duty": "协助", "Require": "本科"})
    assert evaluate_data_completeness(result).jd_complete == 0


def test_case10_existing_description_is_preserved_over_alias_projection():
    description = "岗位职责\n" + RESP * 10 + "\n任职要求\n" + REQ * 10
    full, state, _resp, _req = canonical_jd({"description": description, "Duty": RESP, "Require": REQ})
    assert state == "FULL_TEXT" and full.startswith(description)
