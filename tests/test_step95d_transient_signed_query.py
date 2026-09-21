import json

from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.models import DiscoveryResult, PaginationDetection
from job_extractor.planning import CollectionPlanBuilder
from job_extractor.reporting import ReportManager


SECRET = "transient-signature-value"


def _records(count):
    return [
        {"id": str(index), "title": f"Job {index}", "description": "Complete public job description."}
        for index in range(count)
    ]


def _plan_with_transient_signature():
    payload = {"data": {"items": _records(10), "total": 67}}
    observation = _Observation(
        "https://careers.example.test/api/jobs?_signature=[REDACTED]&offset=0&limit=10",
        "POST", {"offset": 0, "limit": 10}, {"_signature": "[REDACTED]", "offset": "0", "limit": "10"},
        payload, "HYDRATION", runtime_query={"_signature": SECRET, "offset": "0", "limit": "10"},
    )
    candidate = GenericApiDetector._candidate(observation)
    result = DiscoveryResult(
        source_url="https://careers.example.test/jobs", status="DISCOVERED", probable_list_api=candidate,
        candidate_list_apis=[candidate],
        detected_pagination=PaginationDetection(pagination_type="OFFSET", page_param="offset", page_size_param="limit", total_field="data.total"),
    )
    return CollectionPlanBuilder().build(result)


class _Response:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class _SignedServer:
    def __init__(self): self.requests = []
    def request(self, _method, _url, **kwargs):
        self.requests.append(kwargs)
        signed = kwargs.get("params", {}).get("_signature") == SECRET
        count = 67 if signed else 46
        return _Response({"data": {"items": _records(count), "total": count}})


def test_signed_query_is_transiently_replayed_and_never_serialized(tmp_path):
    plan = _plan_with_transient_signature()
    assert SECRET not in plan.model_dump_json()
    assert SECRET not in repr(plan)
    assert plan.query_values.get("_signature") is None

    server = _SignedServer()
    result = GenericHttpCollector(plan, server).collect()
    assert server.requests[0]["params"]["_signature"] == SECRET
    assert result.total_unique == 67
    assert SECRET not in result.model_dump_json()

    artifacts = ReportManager().generate_reports(result, tmp_path / "run")
    for path in (artifacts.json_path, artifacts.markdown_path, artifacts.output_directory / "collection.json"):
        assert SECRET not in path.read_text(encoding="utf-8")


def test_unsigned_plan_keeps_existing_safe_replay_behavior():
    plan = _plan_with_transient_signature()
    plan._runtime_query_params.clear()
    server = _SignedServer()
    result = GenericHttpCollector(plan, server).collect()
    assert "_signature" not in server.requests[0]["params"]
    assert result.total_unique == 46
