from __future__ import annotations

from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.discovery.network_analyzer import response_shape
from job_extractor.discovery.pagination_semantics import infer_pagination_from_schema
from job_extractor.discovery.runtime_data import _wait_for_page_batch, build_runtime_job_source, merge_runtime_batches, run_runtime_pagination
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator


def rows(count: int, start: int = 0):
    return [{"id": f"job-{n}", "title": f"Role {n}"} for n in range(start, start + count)]


def source(total=90, limit=30):
    scan = {"mechanism": "STATE", "arrays": [{"path": "App.jobs", "count": 30, "sample": rows(30)}],
            "totals": [{"scalars": {"total": total}}]}
    return build_runtime_job_source(scan, request_limit=limit, request_offset=0,
                                    observed_requests=[{"offset": 0, "limit": limit}],
                                    trigger={"trigger_mode": "OFFSET_PAGE"})


def card(n: int):
    return {"href": f"#/job/job-{n}", "text": f"Role {n}\n岗位职责：" + "职责内容" * 24 + "\n任职要求：" + "要求内容" * 24}


class DomPaginationPage:
    def __init__(self, pages, *, stale=False, plaintext=None):
        self.pages = pages; self.index = 0; self.stale = stale; self.plaintext = plaintext or []
        self.requests = [{"offset": 0, "limit": 30}]; self.clicks = []

    def wait_for_timeout(self, _ms):
        return None

    def evaluate(self, script, arg=None):
        if "window.__rtP" in script:
            batches = self.plaintext
            return {"requests": self.requests, "batches": batches, "totals": []}
        if "data-job-id" in script:
            page = self.pages[0] if self.stale else self.pages[self.index]
            return [card(n) for n in page]
        if "nodes.find" in script:
            self.clicks.append(arg); self.index = min(self.index + 1, len(self.pages) - 1)
            self.requests.append({"offset": self.index * 30, "limit": 30})
            return {"ok": True, "via": "PAGE_NUMBER"}
        return {}


def test_totalsize_is_tier1_for_shape_and_semantic_pagination():
    payload = {"data": {"pageNo": 1, "pageSize": 10, "totalSize": 7, "data": rows(7)}}
    assert response_shape(payload)["total_field"] == "data.totalSize"
    inferred = infer_pagination_from_schema({"pageNo": 1, "pageSize": 10}, {}, payload)
    assert (inferred["kind"], inferred["page_param"], inferred["size_param"], inferred["total_path"], inferred["total_value"]) == ("PAGE", "pageNo", "pageSize", "data.totalSize", 7)


def test_totalsize_geely_like_plan_is_executable_and_single_page_total_driven():
    payload = {"data": {"pageNo": 1, "pageSize": 10, "totalSize": 7, "data": rows(7)}}
    inferred = infer_pagination_from_schema({"pageNo": 1, "pageSize": 10}, {}, payload)
    assert inferred["total_value"] == 7 and 7 <= 10


def test_dom_fallback_activates_when_plaintext_is_missing_and_binds_offsets():
    runtime_source = source()
    page = DomPaginationPage([list(range(0, 30)), list(range(30, 60)), list(range(60, 90))])
    batches = run_runtime_pagination(page, runtime_source, wait_ms=1000, max_pages=3)
    assert [batch["offset"] for batch in batches] == [0, 30, 60]
    assert [batch["provenance"] for batch in batches] == ["BROWSER_RENDERED_LIST"] * 3
    assert [batch["count"] for batch in batches] == [30, 30, 30]
    assert len({batch["ordered_ids_hash"] for batch in batches}) == 3


def test_plaintext_batch_wins_over_dom_fallback():
    runtime_source = source(total=30)
    plain = [{"offset": 0, "arrays": [{"path": "jobs", "count": 30, "sample": rows(30)}]}]
    page = DomPaginationPage([list(range(30))], plaintext=plain)
    batch, reason = _wait_for_page_batch(page, runtime_source, 0, 300, allow_untagged=True)
    assert reason == "PLAINTEXT_BATCH" and batch["provenance"] == "BROWSER_RUNTIME_PLAINTEXT"


def test_unchanged_dom_after_observed_page_request_is_stagnant():
    runtime_source = source(total=60)
    page = DomPaginationPage([list(range(30)), list(range(30, 60))], stale=True)
    first, _ = _wait_for_page_batch(page, runtime_source, 0, 1000, allow_untagged=True)
    page.evaluate("nodes.find", 2)
    second, reason = _wait_for_page_batch(page, runtime_source, 30, 500, previous_dom_hash=first["ordered_ids_hash"])
    assert second is None and reason == "PAGINATION_STAGNANT"


def test_duplicate_dom_identity_is_audited_and_blocks_complete():
    runtime_source = source(total=60)
    plan = CollectionPlanBuilder().build(DiscoveryResult(source_url="https://site.test/jobs", status="PARTIAL", runtime_source=runtime_source))
    batches = [{"offset": 0, "count": 30, "sample": rows(30), "status": "ACCEPTED"},
               {"offset": 30, "count": 30, "sample": rows(30, 20), "status": "ACCEPTED", "termination_reason": "TOTAL_REACHED"}]
    merged, audit = merge_runtime_batches(runtime_source, batches)
    assert audit["cross_page_duplicates"] == 10
    result = GenericRuntimeDataCollector(plan, batches=batches, enrich_details=False).collect()
    assert len(merged) == 60 and result.status == "INCOMPLETE"


def test_known_total_first_page_only_never_complete_and_ambiguous_blocks_complete():
    runtime_source = source(total=60)
    plan = CollectionPlanBuilder().build(DiscoveryResult(source_url="https://site.test/jobs", status="PARTIAL", runtime_source=runtime_source))
    first_only = [{"offset": 0, "count": 30, "sample": rows(30), "status": "ACCEPTED"}]
    assert GenericRuntimeDataCollector(plan, batches=first_only, enrich_details=False).collect().status == "INCOMPLETE"
    all_rows_ambiguous = [{"offset": 0, "count": 30, "sample": rows(30), "status": "ACCEPTED"},
                          {"offset": 30, "count": 30, "sample": rows(30, 30), "status": "ACCEPTED", "ambiguous_records": 1, "termination_reason": "TOTAL_REACHED"}]
    assert GenericRuntimeDataCollector(plan, batches=all_rows_ambiguous, enrich_details=False).collect().status == "INCOMPLETE"


def test_paginated_runtime_without_total_or_terminal_signal_fails_contract():
    runtime_source = source().model_dump(); runtime_source["total"] = None
    runtime_source["pagination"].pop("total", None)
    plan = CollectionPlanBuilder().build(DiscoveryResult(source_url="https://site.test/jobs", status="PARTIAL", runtime_source=build_runtime_job_source({"mechanism": "STATE", "arrays": [{"path": "j", "count": 30, "sample": rows(30)}], "totals": []}, request_limit=30, request_offset=0, observed_requests=[{"offset": 0, "limit": 30}], trigger={"trigger_mode": "OFFSET_PAGE"})))
    plan = plan.model_copy(update={"executable": True, "runtime_source": runtime_source})
    assert "RUNTIME_TERMINATION_STRATEGY_MISSING" in CollectionPlanValidator().validate(plan).errors
