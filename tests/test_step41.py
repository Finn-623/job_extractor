from job_extractor.adapters import default_registry
from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.discovery.activation import activate_terminal
from job_extractor.discovery.models import (
    CandidateSource,
    DiscoveryResult,
    ProvenanceRecord,
    ProviderFingerprint,
    RejectedCandidate,
    TerminalCandidate,
)
from job_extractor.discovery.provider_fingerprint import fingerprint_terminal
from job_extractor.discovery.runtime_data import (
    build_runtime_job_source,
    looks_like_job_array,
    observe_runtime_job_source,
    runtime_hook_allowed,
)
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator


class FakePage:
    def __init__(self, scan=None, error=False):
        self.scan = scan
        self.error = error

    def evaluate(self, _script, _arg=None):
        if self.error:
            raise RuntimeError("evaluate failed")
        return self.scan


def scan_with(records, total=498, path="window.App.data.jobs"):
    return {"mechanism": "STATE", "arrays": [{"path": path, "count": len(records), "sample": records}], "totals": [{"path": "window.App.data.jobStats", "scalars": {"total": total}}]}


def records(count=3):
    return [{"id": f"id-{i}", "title": f"Job {i}", "location": "SH", "department": "Engineering"} for i in range(count)]


def runtime_source(count=3, total=498, provider="moka"):
    return build_runtime_job_source(scan_with(records(count), total=total), provider=provider, request_limit=30, request_offset=0)


def terminal(url, scope="SOCIAL", trust="TRUSTED", continuity="MATCH"):
    record = ProvenanceRecord(origin_url="https://careers.custom.test", url=url, host=url.split("/", 3)[2], trust=trust)
    return TerminalCandidate(candidate=url, provenance_path=[record], scope=scope, company_continuity=continuity, trust=trust, plan="DOWNSTREAM_RECRUITMENT_PAGE", selected=True, provenance_score=100)


def test_plaintext_entity_capture_from_app_state():
    source = observe_runtime_job_source(FakePage(scan_with(records())), provider="moka", request_limit=30, request_offset=0)
    assert source.mechanism == "STATE"
    assert (source.record_count, source.unique_ids) == (3, 3)
    assert (source.job_id_field, source.job_title_field, source.location_field) == ("id", "title", "location")
    assert source.confidence == "HIGH"


def test_observe_handles_runtime_evaluation_failure():
    source = observe_runtime_job_source(FakePage(error=True), provider="moka")
    assert source.record_count == 0 and source.confidence == "LOW" and source.executable is False


def test_secret_material_is_not_persisted():
    sample = [{"id": "a", "title": "Engineer", "aesIv": "iv-secret", "necromancer": "key-secret", "sessionKey": "s", "csrfToken": "c", "nested": {"access_token": "t", "safe": "ok"}}, {"id": "b", "title": "Designer"}]
    source = build_runtime_job_source(scan_with(sample, total=2), provider="moka")
    blob = source.model_dump_json()
    for secret in ("aesIv", "necromancer", "sessionKey", "csrfToken", "access_token", "iv-secret", "key-secret"):
        assert secret not in blob
    assert "Engineer" in blob and "safe" in blob


def test_stable_id_validation():
    assert not looks_like_job_array([{"title": "A"}, {"title": "B"}])
    assert looks_like_job_array([{"id": "1", "title": "A"}, {"id": "2", "title": "B"}])
    duplicate = build_runtime_job_source(scan_with([{"id": "x", "title": "A"}, {"id": "x", "title": "B"}, {"id": "x", "title": "C"}], total=3), provider="moka")
    assert duplicate.unique_ids == 1 and duplicate.confidence != "HIGH" and duplicate.executable is False


def test_encrypted_response_is_never_promoted_directly():
    encrypted = [{"data": "base64-ciphertext", "necromancer": "hexkey"}, {"data": "more", "necromancer": "hexkey"}]
    assert not looks_like_job_array(encrypted)
    source = build_runtime_job_source({"mechanism": "STATE", "arrays": [], "totals": []}, provider="moka")
    assert source.record_count == 0 and source.confidence == "LOW" and source.executable is False


def test_runtime_partial_snapshot_builds_executable_plan():
    result = DiscoveryResult(source_url="https://careers.custom.test/jobs", status="PARTIAL", runtime_source=runtime_source(count=3, total=498))
    plan = CollectionPlanBuilder().build(result)
    assert plan.mode == "BROWSER_RUNTIME_DATA"
    # STEP72: a HIGH-confidence confirmed runtime path is executable even as a
    # partial snapshot; honest incompleteness is enforced by the collector
    # (expected=runtime total, snapshot-only fetch => INCOMPLETE).
    assert plan.executable is True and plan.review_required is False
    validation = CollectionPlanValidator().validate(plan)
    assert validation.valid is True, validation.errors


def test_runtime_source_complete_is_executable():
    result = DiscoveryResult(source_url="https://careers.custom.test/jobs", status="PARTIAL", runtime_source=runtime_source(count=3, total=3))
    plan = CollectionPlanBuilder().build(result)
    assert plan.mode == "BROWSER_RUNTIME_DATA" and plan.executable is True
    assert CollectionPlanValidator().validate(plan).valid is True


def test_runtime_collector_incomplete_when_paginated():
    source = runtime_source(count=3, total=498)
    plan = CollectionPlanBuilder().build(DiscoveryResult(source_url="https://careers.custom.test/jobs", status="PARTIAL", runtime_source=source))
    result = GenericRuntimeDataCollector(plan, observer=lambda: source).collect()
    assert result.status == "INCOMPLETE"
    assert result.total_expected == 498 and result.total_unique == 3
    assert all(job.job_title for job in result.jobs)
    assert any("PAGINATION_REQUIRED" in warning for warning in result.warnings)


def test_runtime_collector_complete_for_single_snapshot():
    source = runtime_source(count=3, total=3)
    plan = CollectionPlanBuilder().build(DiscoveryResult(source_url="https://careers.custom.test/jobs", status="PARTIAL", runtime_source=source))
    result = GenericRuntimeDataCollector(plan, observer=lambda: source).collect()
    assert result.status == "COMPLETE" and result.total_unique == 3


def test_dom_fallback_is_retained_and_runtime_is_preferred():
    dom = {"status": "DOM_LIST_DETECTED", "possible_detail_links": ["https://careers.custom.test/#/job/a", "https://careers.custom.test/#/job/b"]}
    preferred = CollectionPlanBuilder().build(DiscoveryResult(source_url="https://careers.custom.test/jobs", status="PARTIAL", dom_fallback=dom, runtime_source=runtime_source(count=3, total=3)))
    assert preferred.mode == "BROWSER_RUNTIME_DATA"
    fallback = CollectionPlanBuilder().build(DiscoveryResult(source_url="https://careers.custom.test/jobs", status="PARTIAL", dom_fallback=dom))
    assert fallback.mode == "DOM" and dom["possible_detail_links"]


def test_runtime_hook_requires_high_encrypted_provider_and_trust():
    encrypted = ProviderFingerprint(provider="moka", confidence="HIGH", capabilities=["ENCRYPTED_BROWSER_API"])
    assert runtime_hook_allowed(encrypted, trust="TRUSTED", company_continuity="MATCH", scope_compatible=True)
    assert not runtime_hook_allowed(encrypted, trust="REJECTED", company_continuity="MATCH", scope_compatible=True)
    assert not runtime_hook_allowed(encrypted, trust="TRUSTED", company_continuity="MISMATCH", scope_compatible=True)
    assert not runtime_hook_allowed(encrypted, trust="TRUSTED", company_continuity="MATCH", scope_compatible=False)
    assert not runtime_hook_allowed(ProviderFingerprint(provider="moka", confidence="MEDIUM", capabilities=["ENCRYPTED_BROWSER_API"]), trust="TRUSTED", company_continuity="MATCH", scope_compatible=True)
    assert not runtime_hook_allowed(ProviderFingerprint(provider="moka", confidence="HIGH", capabilities=["KNOWN_ADAPTER"]), trust="TRUSTED", company_continuity="MATCH", scope_compatible=True)


def encrypted_terminal_result(source, asset=True):
    inventory = [CandidateSource(source_type="EMBEDDED_WIDGET", url="https://static-ats.mokahr.com/recruitment-web-client/javascripts/x.js")] if asset else []
    return DiscoveryResult(
        source_url="https://careers.custom.test/social-recruitment/acme/1#/jobs",
        status="PARTIAL",
        detected_scope={"recruitment_type": "social", "siteId": 1},
        runtime_source=source,
        rejected_candidates=[RejectedCandidate(url="https://careers.custom.test/api/outer/ats-apply/website/jobs/v2", method="POST", score=-1, reasons=["LOW_JOB_ENTITY_DENSITY"], response_shape={"top_level_keys": ["data", "necromancer"]})],
        source_inventory=inventory,
    )


def test_encrypted_provider_terminal_annotates_runtime_source():
    result = encrypted_terminal_result(runtime_source(count=3, total=498))
    outcome = activate_terminal(terminal(result.source_url), lambda _url, _budget: result, default_registry)
    assert outcome.discovery.provider_fingerprint.provider == "moka"
    assert outcome.discovery.provider_fingerprint.capabilities == ["ENCRYPTED_BROWSER_API"]
    assert outcome.discovery.runtime_source.provider == "moka"
    assert outcome.discovery.runtime_source.capability == "ENCRYPTED_BROWSER_API"
    assert outcome.record.status in ("NON_EXECUTABLE_TERMINAL", "ACTIVATION_TIMEOUT")


def test_runtime_source_is_not_provider_tagged_without_trusted_provenance():
    result = encrypted_terminal_result(runtime_source(count=3, total=498, provider="UNKNOWN"))
    outcome = activate_terminal(terminal(result.source_url, trust="REJECTED"), lambda _url, _budget: result, default_registry)
    assert outcome.discovery.runtime_source.provider == "UNKNOWN"


def test_plain_moka_known_host_is_not_encrypted_runtime():
    result = DiscoveryResult(source_url="https://app.mokahr.com/campus-recruitment/acme/1#/jobs", status="PARTIAL", detected_scope={"recruitment_type": "campus"})
    fingerprint = fingerprint_terminal(result)
    assert fingerprint.provider == "moka" and fingerprint.confidence == "HIGH" and fingerprint.hostname_matched is True
    assert "ENCRYPTED_BROWSER_API" not in fingerprint.capabilities
    assert not runtime_hook_allowed(fingerprint, trust="TRUSTED", company_continuity="MATCH", scope_compatible=True)
