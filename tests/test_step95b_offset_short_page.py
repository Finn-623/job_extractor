from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.planning.models import CollectionPlan


class Response:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class SequenceClient:
    def __init__(self, payloads): self.payloads = list(payloads); self.offsets = []
    def request(self, _method, _url, **kwargs):
        self.offsets.append(kwargs["params"]["offset"])
        return Response(self.payloads.pop(0))


def _record(index):
    return {"id": str(index), "title": f"Job {index}", "description": "Complete job description."}


def _payload(start, size, total):
    return {"data": {"items": [_record(index) for index in range(start, start + size)], "total": total}}


def _plan(*, total=True):
    return CollectionPlan(
        source_url="https://example.test/jobs", mode="HTTP_API", executable=True,
        list_endpoint="https://api.example.test/jobs", list_method="GET", pagination_type="OFFSET",
        offset_param="offset", page_size_param="limit", initial_values={"offset": 0, "limit": 10},
        list_path="data.items", total_field="data.total" if total else None,
        job_id_field="id", job_title_field="title", detail_mode="LIST_SUFFICIENT", confidence="HIGH",
    )


def test_offset_short_page_with_official_total_continues_to_total_reached():
    # The requested 10,10,10,10,6,10,7 shape sums to 63; this 67-record
    # equivalent proves both the short-page continuation and total boundary.
    sizes = [10, 10, 10, 10, 6, 10, 10, 1]
    cursor = 0; payloads = []
    for size in sizes:
        payloads.append(_payload(cursor, size, 67)); cursor += size
    client = SequenceClient(payloads)
    result = GenericHttpCollector(_plan(), client).collect()
    assert result.total_unique == 67
    assert result.metrics.termination_reason == "TOTAL_REACHED"
    assert client.offsets == [0, 10, 20, 30, 40, 50, 60, 70]


def test_offset_short_page_without_total_stops():
    result = GenericHttpCollector(_plan(total=False), SequenceClient([_payload(0, 6, None)])).collect()
    assert result.total_unique == 6
    assert result.metrics.termination_reason == "SHORT_PAGE"


def test_offset_short_page_without_new_ids_stops_no_progress():
    repeated = [_record(index) for index in range(10)]
    payloads = [{"data": {"items": repeated, "total": 67}}, {"data": {"items": repeated[:6], "total": 67}}]
    result = GenericHttpCollector(_plan(), SequenceClient(payloads)).collect()
    assert result.metrics.termination_reason == "NO_PROGRESS"
    assert any("PAGINATION_NO_PROGRESS" in error for error in result.errors)


def test_offset_duplicate_heavy_short_page_cannot_loop():
    first = [_record(index) for index in range(10)]
    payloads = [{"data": {"items": first, "total": 99}}, {"data": {"items": first[:6], "total": 99}}]
    result = GenericHttpCollector(_plan(), SequenceClient(payloads), max_pages=20).collect()
    assert result.metrics.termination_reason == "NO_PROGRESS"
