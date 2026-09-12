import json

import httpx
import pytest

from job_extractor.adapters.zhiye import ZhiyeAdapter, ZhiyeResponseError
from job_extractor.adapters.zhiye_config import get_tenant_config
from job_extractor.exporters import export_collection_result


def raw_job(job_id: str, title: str = "测试岗位", category: str = "2") -> dict:
    return {
        "Id": job_id, "JobAdId": 123, "JobAdName": title,
        "CategoryId": category, "Duty": "职责一\n职责二",
        "Require": "要求一\n要求二", "LocNames": ["杭州"],
    }


def client_for(pages, total=None, detail_failure=None, payloads=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("GetJobAdPageList"):
            body = json.loads(request.content)
            if payloads is not None:
                payloads.append(body)
            data = pages.get(body["PageIndex"], [])
            return httpx.Response(200, json={"Code": 200, "Count": total, "Data": data})
        job_id = request.url.params["jobAdId"]
        if job_id == detail_failure:
            return httpx.Response(500, text="failure")
        source = next(item for values in pages.values() for item in values if item.get("Id") == job_id)
        detail = {**source, "Duty": source.get("Duty") or "详情职责", "Require": source.get("Require") or "详情要求"}
        return httpx.Response(200, json={"Code": 200, "Data": detail})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("path", "business_type", "category"),
    [("/campus/jobs", "campus", ["2"]), ("/social/jobs", "social", ["1"]), ("/intern/jobs", "intern", ["3"])],
)
def test_scope_mapping(path, business_type, category):
    assert ZhiyeAdapter._build_scope_filter("https://test.zhiye.com" + path) == {
        "business_type": business_type, "category": category,
    }


def test_unknown_route_is_not_assumed_campus():
    adapter = ZhiyeAdapter(client=client_for({}))
    result = adapter.collect("https://test.zhiye.com/jobs")
    assert adapter.scope.name == "unknown" and adapter.scope.categories == ()
    assert result.status == "COMPLETE"


def test_base_url_derivation():
    assert ZhiyeAdapter.derive_base_url("https://abc.zhiye.com/campus/jobs") == "https://abc.zhiye.com"


def test_list_response_parsing_and_total():
    jobs, total = ZhiyeAdapter._parse_list_response({"Code": 200, "Count": 12, "Data": [raw_job("a")]})
    assert len(jobs) == 1 and total == 12


def test_nested_list_response_parsing():
    jobs, total = ZhiyeAdapter._parse_list_response({"Data": {"records": [raw_job("a")], "totalCount": 1}})
    assert len(jobs) == total == 1


def test_unsupported_list_response():
    with pytest.raises(ZhiyeResponseError, match="UNSUPPORTED_ZHIYE_RESPONSE"):
        ZhiyeAdapter._parse_list_response({"Data": {"unexpected": []}})


def test_job_id_extraction_prefers_guid_id():
    assert ZhiyeAdapter._extract_job_id({"Id": "guid", "JobAdId": 42}) == "guid"


def test_official_payload_and_zero_based_pagination_preserve_campus_scope():
    payloads = []
    pages = {0: [raw_job("a")], 1: [raw_job("b")]}
    adapter = ZhiyeAdapter(page_size=1, client=client_for(pages, total=2, payloads=payloads))
    result = adapter.collect("https://test.zhiye.com/campus/jobs")
    assert result.status == "COMPLETE"
    assert [payload["PageIndex"] for payload in payloads] == [0, 1]
    assert all(payload["Category"] == ["2"] for payload in payloads)
    assert payloads[0]["DisplayFields"] == ZhiyeAdapter.DISPLAY_FIELDS


def test_empty_page_stops_without_total():
    adapter = ZhiyeAdapter(page_size=1, client=client_for({0: [raw_job("a")], 1: []}))
    result = adapter.collect("https://test.zhiye.com/campus/jobs")
    assert adapter.page_count == 2 and result.status == "COMPLETE"


def test_expected_285_unique_285_is_complete_without_details():
    jobs = [raw_job(str(index)) for index in range(285)]
    adapter = ZhiyeAdapter(page_size=100, client=client_for({0: jobs}, total=285))
    result = adapter.collect("https://test.zhiye.com/campus/jobs")
    assert (result.total_expected, result.total_fetched, result.total_unique) == (285, 285, 285)
    assert result.status == "COMPLETE"
    assert adapter.detail_strategy == "LIST_SUFFICIENT"
    assert adapter.details_attempted == 0


def test_duplicate_jobs_are_incomplete():
    result = ZhiyeAdapter(client=client_for({0: [raw_job("a"), raw_job("a")]}, total=2)).collect("https://test.zhiye.com/campus/jobs")
    assert result.total_unique == 1 and result.status == "INCOMPLETE"


def test_category_leak_is_incomplete():
    result = ZhiyeAdapter(client=client_for({0: [raw_job("a", category="1")]}, total=1)).collect("https://test.zhiye.com/campus/jobs")
    assert result.status == "INCOMPLETE"
    assert any("SCOPE_LEAK" in error for error in result.errors)


def test_detail_fallback_fills_missing_list_jd():
    item = raw_job("a")
    item["Duty"] = None
    result_adapter = ZhiyeAdapter(client=client_for({0: [item]}, total=1))
    result = result_adapter.collect("https://test.zhiye.com/campus/jobs")
    assert result.status == "COMPLETE"
    assert result_adapter.detail_strategy == "DETAIL_FALLBACK"
    assert result_adapter.details_attempted == result_adapter.details_succeeded == 1
    assert result.jobs[0].responsibilities == ["详情职责"]


def test_detail_failure_is_incomplete():
    item = raw_job("a")
    item["Require"] = None
    adapter = ZhiyeAdapter(client=client_for({0: [item]}, total=1, detail_failure="a"))
    result = adapter.collect("https://test.zhiye.com/campus/jobs")
    assert result.status == "INCOMPLETE" and adapter.details_failed == 1


def test_page_one_failure_is_failed():
    def fail(request):
        raise httpx.ConnectError("offline", request=request)
    result = ZhiyeAdapter(client=httpx.Client(transport=httpx.MockTransport(fail))).collect("https://test.zhiye.com/campus/jobs")
    assert result.status == "FAILED" and result.total_fetched == 0


def test_export_and_company_metadata(tmp_path):
    result = ZhiyeAdapter(client=client_for({0: [raw_job("a")]}, total=1)).collect("https://leapmotor1.zhiye.com/campus/jobs")
    path = tmp_path / "nested" / "result.json"
    export_collection_result(result, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert result.company == "零跑汽车" and payload["jobs"][0]["job_id"] == "a"


def test_unknown_tenant_does_not_invent_company():
    result = ZhiyeAdapter(client=client_for({0: [raw_job("a")]}, total=1)).collect("https://unknown.zhiye.com/campus/jobs")
    assert result.company is None


def test_max_pages_marks_collection_incomplete():
    adapter = ZhiyeAdapter(page_size=1, max_pages=1, client=client_for({0: [raw_job("a")]}))
    result = adapter.collect("https://test.zhiye.com/campus/jobs")
    assert result.status == "INCOMPLETE"
    assert any("PAGINATION_LIMIT" in error for error in result.errors)


def test_tenant_config_known_and_unknown():
    assert get_tenant_config("leapmotor1.zhiye.com").company == "零跑汽车"
    assert get_tenant_config("other.zhiye.com").company is None


def test_page_size_falls_back_from_100_to_50_once():
    payloads = []
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payloads.append(body)
        if body["PageSize"] == 100:
            return httpx.Response(400, text="unsupported page size")
        return httpx.Response(200, json={"Code": 200, "Count": 1, "Data": [raw_job("a")]})
    adapter = ZhiyeAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = adapter.collect("https://test.zhiye.com/campus/jobs")
    assert result.status == "COMPLETE"
    assert adapter.negotiated_page_size == 50
    assert [payload["PageSize"] for payload in payloads] == [100, 50]


def test_field_extraction_supports_finite_variants():
    item = {
        "id": "alternate", "Title": "Alternate title", "CategoryName": "Engineering",
        "Department": "R&D", "Locations": "Sydney", "Education": "Bachelor",
        "Major": "Computer Science", "RecruitCount": 2,
        "Responsibilities": "Build", "Qualification": "Learn", "PublishDate": "2026-01-01",
        "categoryId": "1",
    }
    adapter = ZhiyeAdapter(client=client_for({}))
    job = adapter._normalize_job(item, None, "https://test.zhiye.com/social/jobs", None)
    assert job.job_id == "alternate" and job.job_title == "Alternate title"
    assert job.department == "R&D" and job.locations == ["Sydney"]
    assert job.major == "Computer Science" and job.headcount == 2


@pytest.mark.parametrize(("category", "label"), [("1", "社会招聘"), ("2", "校园招聘"), ("3", "实习招聘")])
def test_recruitment_type_normalization(category, label):
    item = raw_job("a", category=category)
    adapter = ZhiyeAdapter(client=client_for({}))
    assert adapter._normalize_job(item, None, "https://test.zhiye.com/jobs", None).recruitment_type == label


def test_api_company_takes_precedence_over_tenant_config():
    item = raw_job("a")
    item["CompanyName"] = "API Company"
    result = ZhiyeAdapter(client=client_for({0: [item]}, total=1)).collect("https://leapmotor1.zhiye.com/campus/jobs")
    assert result.company == "API Company"
    assert result.jobs[0].company == "API Company"


@pytest.mark.parametrize("missing", [("Duty",), ("Require",), ("Duty", "Require")])
def test_each_missing_jd_combination_uses_detail_fallback(missing):
    item = raw_job("a")
    for key in missing:
        item[key] = None
    adapter = ZhiyeAdapter(client=client_for({0: [item]}, total=1))
    result = adapter.collect("https://test.zhiye.com/campus/jobs")
    assert adapter.detail_strategy == "DETAIL_FALLBACK"
    assert adapter.details_attempted == 1 and result.status == "COMPLETE"
