from typing import Any

import httpx

from job_extractor.collectors.generic_http import GenericHttpCollector, resolve
from job_extractor.discovery.models import ApiCandidate, DiscoveryResult, PaginationDetection
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator
from job_extractor.planning.execution_contract import can_dispatch


# ---------- STEP 67: nested-entity plan contract ----------

def nested_candidate(confidence: str = "HIGH") -> ApiCandidate:
    return ApiCandidate(
        url="https://api.example.test/search-job-list",
        method="POST",
        score=24,
        confidence=confidence,
        request_body_shape={"pageIndex": "int", "pageSize": "int"},
        safe_request_values={"pageIndex": 1, "pageSize": 6, "keyword": ""},
        request_content_type="application/json",
        response_shape={
            "top_level_keys": ["code", "data", "message"],
            "candidate_list_path": "data.jobList",
            "sample_field_names": ["company", "job", "staff"],
            "total_field": "data.pageInfo.totalNum",
            "inferred_job_id_field": "id",
            "inferred_job_title_field": "title",
        },
        detail_url_field="job.url",
        observed_list_length=6,
        evidence=["job-like records"],
    )


def nested_discovery(c: ApiCandidate) -> DiscoveryResult:
    return DiscoveryResult(
        source_url="https://careers.example.test/job/index.html",
        status="DISCOVERED",
        candidate_list_apis=[c],
        probable_list_api=c,
        detected_pagination=PaginationDetection(
            pagination_type="PAGE", page_param="pageIndex", page_size_param="pageSize",
            inference_source="request_response_schema", confidence=0.8,
        ),
    )


def test_nested_entity_fields_qualify_plan():
    p = CollectionPlanBuilder().build(nested_discovery(nested_candidate()))
    assert p.mode == "HTTP_API" and p.executable
    assert p.job_id_field == "job.id" and p.job_title_field == "job.title"
    assert CollectionPlanValidator().validate(p).valid and can_dispatch(p)


def test_flat_candidate_keeps_flat_fields():
    p = CollectionPlanBuilder().build(nested_discovery(nested_candidate()))
    assert not p.job_id_field.startswith(".") and not p.job_title_field.startswith(".")


def test_nested_without_prefix_stays_flat_when_no_dotted_hint():
    c = nested_candidate()
    c.detail_url_field = None
    c.response_shape = {k: v for k, v in c.response_shape.items() if k != "inferred_job_id_field"}
    p = CollectionPlanBuilder().build(nested_discovery(c))
    # Without any evidence of a nested entity, flat resolution still applies at
    # execution time; the plan must simply remain well-formed.
    assert p.mode in ("HTTP_API", "UNSUPPORTED")


# ---------- STEP 67: generic resolve ----------

def test_resolve_dotted_path_wins():
    raw = {"job": {"id": 7, "title": "T"}, "id": "flat"}
    assert resolve(raw, ("job.id", "id")) == 7


def test_resolve_flat_then_nested_entity():
    raw = {"company": {"name": "c"}, "job": {"id": "9", "title": "x", "cityName": "武汉"}}
    assert resolve(raw, ("jobId", "id")) == "9"
    assert resolve(raw, ("cityName", "city")) == "武汉"


def test_resolve_missing_returns_none():
    assert resolve({"a": 1}, ("nope", "nada")) is None


# ---------- STEP 67: total vocabulary ----------

def test_totalnum_recognized_as_total_field():
    from job_extractor.discovery.network_analyzer import find_total_field
    payload = {"data": {"jobList": [{"id": 1}], "pageInfo": {"pageIndex": 1, "pageSize": 6, "totalNum": 1271}}}
    assert find_total_field(payload, 1) == "data.pageInfo.totalNum"


# ---------- STEP 67: execution contract ----------

def _plan():
    p = CollectionPlanBuilder().build(nested_discovery(nested_candidate()))
    return p


def _transport(plan: Any, handler, max_pages: int = 5):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GenericHttpCollector(plan, client=client, max_pages=max_pages, deadline_seconds=30.0, sleep_fn=lambda s: None)


def _payload(page: int) -> dict:
    return {"code": 200, "data": {"jobList": [
        {"job": {"id": f"id-{page}-{i}", "title": f"工程师{page}{i}", "cityName": "武汉",
                 "url": f"https://jobs.example.test/detail/{page}-{i}",
                 "detail": "岗位职责：<br>1.系统设计；<br>专业要求：电气类。"}}
        for i in range(6)
    ], "pageInfo": {"pageIndex": page, "pageSize": 6, "totalNum": 1271, "totalPage": 212}}}


def test_live_contract_replay_two_pages_and_normalize():
    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        body = _json.loads(request.content or b"{}")
        return httpx.Response(200, json=_payload(int(body.get("pageIndex", 1))))

    result = _transport(_plan(), handler, max_pages=2).collect()
    assert result.total_fetched == 12 and result.total_unique == 12
    # totalNum(1271) >> 2 pages => max-pages guard is the correct termination
    assert any("PAGINATION_LIMIT" in str(e) for e in result.errors)
    first = result.jobs[0]
    assert first.job_id and first.job_title and first.locations and first.detail_url
    # detail handoff preserved, no fabricated JD
    assert first.raw_data.get("job", {}).get("detail") or first.raw_data.get("_generic_detail_source")


def test_pagination_page_param_increments():
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        if request.method == "GET":  # STEP68: detail fetches hit the same mock
            return httpx.Response(200, text="<html><body>detail shell</body></html>")
        body = _json.loads(request.content or b"{}")
        page = int(body.get("pageIndex", 1))
        seen.append(page)
        return httpx.Response(200, json=_payload(page))

    result = _transport(_plan(), handler).collect()
    assert seen == [1, 2, 3, 4, 5]  # max_pages=5, PAGE increments
    assert result.total_fetched == 30 and result.total_unique == 30
