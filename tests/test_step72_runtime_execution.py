# STEP72: BROWSER_RUNTIME_DATA execution for partial runtime snapshots.
# Discovery confirms a runtime job path (HIGH confidence, stable id/title);
# execution re-evaluates exactly that path on a live page — no full-window
# rescan — and fails closed on missing/non-list values.
from __future__ import annotations

import pytest

from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.collectors.generic_ats import GenericATSCollector
from job_extractor.planning.execution_contract import (
    can_dispatch,
    dispatch_target,
    missing_fields,
)
from job_extractor.planning.models import CollectionPlan
from job_extractor.planning.validator import CollectionPlanValidator


def runtime_plan(**changes):
    """Partial-snapshot runtime plan: HIGH confidence, confirmed path, no
    validated batch pagination (mirrors real discovery output for sites where
    the state snapshot holds fewer records than the runtime total)."""
    source = dict(
        mode="BROWSER_RUNTIME_DATA", provider="UNKNOWN", capability="ENCRYPTED_BROWSER_API",
        mechanism="STATE", source_path="window.App.data.jobs",
        record_count=3, unique_ids=3, job_id_field="id", job_title_field="title",
        location_field="location", jd_fields=[], pagination={"total": 9},
        pagination_model="UNKNOWN", offset_field=None, limit_field=None,
        initial_offset=None, limit=None, total=9, page_count=None,
        trigger_mode="UNKNOWN", pagination_validated=False,
        records=[
            {"id": "A1", "title": "Engineer", "location": "Ningde"},
            {"id": "A2", "title": "Analyst", "location": "Shanghai"},
            {"id": "A3", "title": "Designer", "location": "Shenzhen"},
        ],
        confidence="HIGH", executable=False,
        evidence=["plaintext job array observed in STATE: window.App.data.jobs"],
        validator_errors=[],
    )
    values = dict(
        source_url="https://site.test/jobs", mode="BROWSER_RUNTIME_DATA",
        executable=True, runtime_source=source, list_path="window.App.data.jobs",
        job_id_field="id", job_title_field="title", confidence="HIGH",
        detail_mode="UNKNOWN",
    )
    values.update(changes)
    return CollectionPlan(**values)


class FakeRuntime:
    pages_opened = 1

    def __init__(self, payload):
        self.payload = payload
        self.page = FakePage(payload)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakePage:
    def __init__(self, payload):
        self.payload = payload

    def goto(self, url, wait_until=None):
        pass

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, js, path):
        return self.payload


def factory(payload):
    return lambda: FakeRuntime(payload)


# 1. A HIGH-confidence partial snapshot plan with a confirmed path is valid.
def test_valid_partial_runtime_plan_passes_validation():
    plan = runtime_plan()
    validation = CollectionPlanValidator().validate(plan)
    assert validation.valid, validation.errors
    assert plan.executable
    assert can_dispatch(plan)
    assert dispatch_target(plan) == "GenericRuntimeDataCollector"
    assert GenericATSCollector(plan).can_dispatch(plan)


# 2. Confirmed path absent on the live page -> fail closed, status FAILED.
def test_missing_runtime_path_fails_closed():
    plan = runtime_plan()
    collector = GenericRuntimeDataCollector(plan, browser_factory=factory({"kind": "MISSING"}))
    result = collector.collect()
    assert result.status == "FAILED"
    assert result.jobs == []
    assert any("RUNTIME_PATH_NOT_FOUND" in e for e in result.errors)
    assert result.total_fetched == 0


# 3. Runtime value not a list -> fail closed, status FAILED.
def test_non_list_runtime_value_fails_closed():
    plan = runtime_plan()
    collector = GenericRuntimeDataCollector(
        plan, browser_factory=factory({"kind": "NOT_LIST", "actual": "object"}))
    result = collector.collect()
    assert result.status == "FAILED"
    assert result.jobs == []
    assert any("RUNTIME_VALUE_NOT_LIST" in e for e in result.errors)


# 4. Live list records normalize into jobs (id/title/location from record).
def test_records_normalize_into_jobs():
    plan = runtime_plan()
    payload = {"kind": "LIST", "value": plan.runtime_source["records"]}
    result = GenericRuntimeDataCollector(plan, browser_factory=factory(payload)).collect()
    assert result.total_fetched == 3
    assert result.total_unique == 3
    assert len(result.jobs) == 3
    by_id = {job.job_id: job for job in result.jobs}
    assert by_id["A1"].job_title == "Engineer"
    assert by_id["A1"].locations == ["Ningde"]
    # expected comes from the runtime total (9), snapshot only holds 3 ->
    # honest INCOMPLETE, never COMPLETE for a partial view.
    assert result.total_expected == 9
    assert result.status == "INCOMPLETE"
    assert any("PAGINATION_REQUIRED" in w for w in result.warnings)


# 5. Duplicate ids collapse -> unique < fetched -> completeness failure.
def test_duplicate_ids_break_completeness():
    records = [
        {"id": "A1", "title": "Engineer"},
        {"id": "A1", "title": "Engineer Two"},
        {"id": "A2", "title": "Analyst"},
    ]
    plan = runtime_plan(total=3, runtime_source={**runtime_plan().runtime_source,
                                                  "total": 3, "records": records,
                                                  "record_count": 3, "unique_ids": 2})
    payload = {"kind": "LIST", "value": records}
    result = GenericRuntimeDataCollector(plan, browser_factory=factory(payload)).collect()
    assert result.total_fetched == 3
    assert result.total_unique == 2
    assert result.status == "INCOMPLETE"


# 6. Detail handoff: a detail contract routes records through the existing
# detail stage; list execution itself is not judged a failure by missing JDs.
def test_detail_handoff_runs_existing_stage():
    calls = []

    class Client:
        def request(self, method, url, **kwargs):
            calls.append((method, url))
            raise AssertionError("network disabled in unit test")

    plan = runtime_plan(detail_endpoint_template="https://site.test/api/job/{id}",
                        detail_method="GET", detail_id_field="id",
                        detail_mode="DETAIL_REQUIRED")
    payload = {"kind": "LIST", "value": plan.runtime_source["records"]}
    collector = GenericRuntimeDataCollector(
        plan, browser_factory=factory(payload),
        detail_client=Client(), detail_retries=0, detail_timeout=0.1,
        detail_concurrency=1, detail_budget=3)
    result = collector.collect()
    # List acquisition itself succeeded; the bounded detail stage attempted
    # the contract endpoint per job and recorded failures without failing
    # the whole collection.
    assert result.total_unique == 3
    assert result.status in ("INCOMPLETE",)
    assert collector.detail_stats.get("attempted") == 3
    assert collector.detail_stats.get("succeeded", 0) == 0
    assert collector.detail_stats.get("failed", 0) == 3


# 7. Dispatcher: BROWSER_RUNTIME_DATA routes to the runtime collector and the
# HTTP_API route is untouched.
def test_dispatch_only_for_runtime_mode():
    assert dispatch_target(runtime_plan()) == "GenericRuntimeDataCollector"
    assert missing_fields(runtime_plan()) == []
    http_like = runtime_plan(mode="HTTP_API")
    # HTTP_API with no endpoint contract cannot silently borrow runtime data.
    assert not GenericATSCollector(http_like).can_dispatch(http_like)
