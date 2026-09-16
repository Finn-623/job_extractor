"""STEP54D.2 — generic plural alias ``contents`` for the JD description role."""

from job_extractor.field_semantics import canonical_jd, pick_jd_fields


CREDIBLE_JD = "岗位职责\n负责核心系统研发与迭代优化。\n任职要求\n本科以上学历，熟悉 Python。"


def test_existing_content_alias_does_not_regress():
    found = pick_jd_fields({"content": CREDIBLE_JD})
    assert found["description"] == CREDIBLE_JD
    assert found["description_field"] == "content"
    assert found["list_jd_state"] == "FULL_TEXT"


def test_contents_is_recognized_as_description_field():
    found = pick_jd_fields({"contents": CREDIBLE_JD})
    assert found["description"] == CREDIBLE_JD
    assert found["description_field"] == "contents"
    assert found["list_jd_state"] == "FULL_TEXT"
    full, state, _resp, _req = canonical_jd({"contents": CREDIBLE_JD})
    assert state == "FULL_TEXT" and full == CREDIBLE_JD


def test_contents_case_insensitive_exact_key_recognized():
    for key in ("Contents", "CONTENTS"):
        found = pick_jd_fields({key: CREDIBLE_JD})
        assert found["description_field"] == key
        assert found["list_jd_state"] == "FULL_TEXT"


def test_contents_empty_or_teaser_fails_closed():
    for value in ("", "急招。"):
        found = pick_jd_fields({"contents": value})
        if value:
            assert found["list_jd_state"] == "SUMMARY"
        else:
            assert found["description"] is None and found["list_jd_state"] == "ABSENT"
    full, state, _resp, _req = canonical_jd({"contents": "急招。"})
    assert state == "SUMMARY" and full == "急招。"


def test_metadata_keys_are_not_jd():
    for key in ("contentStatus", "contentsCount", "contentType", "contentId"):
        found = pick_jd_fields({key: "x"})
        assert found["description"] is None and found["list_jd_state"] == "ABSENT"


def test_existing_better_description_wins_over_contents():
    longer = "岗位职责\n" + "负责平台研发。" * 40 + "\n任职要求\n本科以上学历"
    found = pick_jd_fields({"description": longer, "contents": "短摘要"})
    assert found["description"] == longer and found["description_field"] == "description"


def test_short_teaser_is_not_promoted_to_complete():
    from job_extractor.runtime import evaluate_data_completeness
    from tests.test_generic_collectors import Client, plan
    from job_extractor.collectors.generic_http import GenericHttpCollector

    item = {"id": "1", "title": "财务会计", "contents": "负责会计相关工作"}
    result = GenericHttpCollector(plan(), Client([{"data": {"items": [item], "total": 1}}])).collect()
    dc = evaluate_data_completeness(result)
    assert dc.jd_complete == 0 and dc.jd_incomplete == 1
    assert result.jobs[0].raw_data["_jd_state"] == "SUMMARY"


def test_credible_contents_list_is_list_sufficient():
    from job_extractor.runtime import evaluate_data_completeness
    from tests.test_generic_collectors import Client, plan
    from job_extractor.collectors.generic_http import GenericHttpCollector

    items = [{"id": str(i), "title": f"工程师 {i}", "contents": CREDIBLE_JD} for i in range(1, 13)]
    result = GenericHttpCollector(plan(), Client([{"data": {"items": items, "total": len(items)}}])).collect()
    dc = evaluate_data_completeness(result)
    assert dc.jd_complete == len(items) and dc.detail_attempted == 0
    assert result.jobs[0].raw_data["_jd_state"] == "FULL_TEXT"
    assert result.jobs[0].raw_data["_jd_source_fields"]["description"] == "contents"
