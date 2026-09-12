import inspect

from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.discovery import runtime_data
from job_extractor.discovery.models import DiscoveryResult, ProviderFingerprint
from job_extractor.discovery.provider_fingerprint import fingerprint_terminal
from job_extractor.discovery.runtime_data import (
    build_runtime_job_source,
    detect_pagination_trigger,
    merge_runtime_batches,
    resolve_encrypted_runtime_terminal,
    run_runtime_pagination,
)
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator


class FakePage:
    def __init__(self, scan=None, trigger=None, error=False):
        self.scan = scan
        self.trigger = trigger
        self.error = error

    def evaluate(self, script, arg=None):
        if self.error:
            raise RuntimeError("evaluate failed")
        if script is runtime_data.PAGINATION_TRIGGER_JS:
            return self.trigger
        return self.scan


class FakePaginationPage:
    def __init__(self, batches):
        self.batches = batches
        self.clicks = []

    def wait_for_timeout(self, _ms):
        return None

    def evaluate(self, script, arg=None):
        if "window.__rtP" in script:
            return {"requests": [], "batches": [{"offset": batch["offset"], "arrays": [{"path": "data.jobs", "count": batch["count"], "sample": batch["sample"]}]} for batch in self.batches], "totals": []}
        if "nodes.find" in script:
            self.clicks.append(arg)
            return {"ok": True, "via": "PAGE_NUMBER", "page": arg}
        return {}


def records(count, start=0, prefix="id"):
    return [{"id": f"{prefix}-{i}", "title": f"Job {i}", "location": "SH", "department": "Eng"} for i in range(start, start + count)]


def scan_of(count, total, path="window.App.data.jobs", start=0):
    return {"mechanism": "STATE", "arrays": [{"path": path, "count": count, "sample": records(count, start)}], "totals": [{"path": "window.App.data.jobStats", "scalars": {"total": total}}]}


def paginated_source(count=30, total=498, limit=30):
    return build_runtime_job_source(scan_of(count, total), provider="moka", request_limit=limit, request_offset=0, observed_requests=[{"offset": 0, "limit": limit}], trigger={"trigger_mode": "OFFSET_PAGE", "has_pagination": True})


def plan_for(source):
    return CollectionPlanBuilder().build(DiscoveryResult(source_url="https://careers.custom.test/jobs", status="PARTIAL", runtime_source=source))


def test_runtime_offset_pagination_contract():
    source = paginated_source(count=30, total=498)
    assert source.pagination_model == "OFFSET"
    assert (source.offset_field, source.limit_field) == ("offset", "limit")
    assert (source.initial_offset, source.limit, source.total, source.page_count) == (0, 30, 498, 17)
    assert source.trigger_mode == "OFFSET_PAGE" and source.pagination_validated is True


def test_official_trigger_observation_and_failure():
    page = FakePage(trigger={"has_pagination": True, "trigger_mode": "OFFSET_PAGE", "numeric_buttons": 8})
    assert detect_pagination_trigger(page)["trigger_mode"] == "OFFSET_PAGE"
    assert detect_pagination_trigger(FakePage(error=True)) == {}


def test_request_plaintext_batch_attribution():
    source = paginated_source()
    batches = [{"offset": 0, "count": 30, "sample": records(30, 0)}, {"offset": 30, "count": 30, "sample": records(30, 30)}, {"offset": 60, "count": 30, "sample": records(30, 60)}]
    merged, audit = merge_runtime_batches(source, batches)
    assert [entry["offset"] for entry in audit["batches"]] == [0, 30, 60]
    assert [entry["plaintext_count"] for entry in audit["batches"]] == [30, 30, 30]
    assert [entry["cumulative_unique"] for entry in audit["batches"]] == [30, 60, 90]
    assert len(merged) == 90


def test_cross_batch_stable_id_dedup():
    source = paginated_source()
    batches = [{"offset": 0, "count": 30, "sample": records(30, 0)}, {"offset": 30, "count": 30, "sample": records(30, 20)}]
    merged, audit = merge_runtime_batches(source, batches)
    assert audit["unique_ids"] == 50
    assert audit["batches"][1]["cross_batch_duplicates"] == 10
    assert len(merged) == 60


def test_official_total_reconciliation_is_complete():
    source = paginated_source(count=30, total=50)
    plan = plan_for(source)
    batches = [{"offset": 0, "count": 30, "sample": records(30, 0)}, {"offset": 30, "count": 20, "sample": records(20, 30)}]
    result = GenericRuntimeDataCollector(plan, batches=batches).collect()
    assert result.status == "COMPLETE" and result.total_unique == 50
    assert result.duplicate_audit["batches"][-1]["plaintext_count"] == 20


def test_total_mismatch_is_not_complete():
    source = paginated_source(count=30, total=50)
    plan = plan_for(source)
    batches = [{"offset": 0, "count": 30, "sample": records(30, 0)}, {"offset": 30, "count": 19, "sample": records(19, 30)}]
    result = GenericRuntimeDataCollector(plan, batches=batches).collect()
    assert result.status == "INCOMPLETE" and result.total_unique == 49
    assert any("PAGINATION_REQUIRED" in warning for warning in result.warnings)


def test_final_partial_batch_stops_pagination_driver():
    source = paginated_source(count=30, total=80)
    page = FakePaginationPage([{"offset": 0, "count": 30, "sample": records(30, 0)}, {"offset": 30, "count": 30, "sample": records(30, 30)}, {"offset": 60, "count": 20, "sample": records(20, 60)}])
    batches = run_runtime_pagination(page, source, wait_ms=300, max_pages=10)
    assert [batch["offset"] for batch in batches] == [0, 30, 60]
    assert page.clicks == [2, 3]


def test_runtime_pagination_validator_gating():
    paginated = paginated_source(count=30, total=498)
    assert CollectionPlanValidator().validate(plan_for(paginated)).valid is True
    incomplete = build_runtime_job_source(scan_of(15, 498), provider="moka", request_limit=30, request_offset=0, observed_requests=[{"offset": 0, "limit": 30}], trigger={"trigger_mode": "UNKNOWN"})
    plan = plan_for(incomplete)
    assert plan.executable is False
    validation = CollectionPlanValidator().validate(plan)
    assert validation.valid is False and "PLAN_NOT_EXECUTABLE" in validation.errors
    executable_missing_contract = plan_for(incomplete).model_copy(update={"executable": True})
    errors = CollectionPlanValidator().validate(executable_missing_contract).errors
    assert "RUNTIME_PAGINATION_NOT_VALIDATED" in errors and "RUNTIME_TRIGGER_UNSAFE" in errors


def test_runtime_batches_preferred_over_partial_state_snapshot():
    source = build_runtime_job_source(scan_of(15, 50), provider="moka", request_limit=30, request_offset=0, observed_requests=[{"offset": 0, "limit": 30}], trigger={"trigger_mode": "OFFSET_PAGE"})
    assert source.record_count == 15
    plan = plan_for(source)
    batches = [{"offset": 0, "count": 30, "sample": records(30, 0)}, {"offset": 30, "count": 20, "sample": records(20, 30)}]
    result = GenericRuntimeDataCollector(plan, batches=batches).collect()
    assert result.total_fetched == 50 and result.total_unique == 50


def test_normal_moka_known_host_is_not_encrypted_runtime():
    result = DiscoveryResult(source_url="https://app.mokahr.com/campus-recruitment/acme/1#/jobs", status="PARTIAL", detected_scope={"recruitment_type": "campus"})
    fingerprint = fingerprint_terminal(result)
    assert fingerprint.confidence == "HIGH" and fingerprint.hostname_matched is True
    assert "ENCRYPTED_BROWSER_API" not in fingerprint.capabilities


def test_resolver_skips_known_adapter_entries():
    from job_extractor.adapters import default_registry
    from job_extractor.discovery.models import RecruitmentEntry
    root = DiscoveryResult(source_url="https://company.test/careers", status="PARTIAL", company="Acme", recruitment_entries=[RecruitmentEntry(entry_type="CAMPUS", text="校园招聘", url="https://app.mokahr.com/campus-recruitment/acme/1", destination_host="app.mokahr.com")])
    calls = []
    assert resolve_encrypted_runtime_terminal(root, lambda url: calls.append(url), default_registry) is None
    assert calls == []


def test_encrypted_envelope_is_never_a_runtime_source():
    source = build_runtime_job_source({"mechanism": "STATE", "arrays": [], "totals": []}, provider="moka")
    assert source.record_count == 0 and source.confidence == "LOW" and source.executable is False


def test_no_python_crypto_in_runtime_module():
    source = inspect.getsource(runtime_data)
    for token in ("Crypto", "AES", "base64", "unpad", "Cipher"):
        assert token not in source
