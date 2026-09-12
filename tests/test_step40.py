from job_extractor.adapters import default_registry
from job_extractor.adapters.moka import MokaAdapter
from job_extractor.discovery.activation import activate_terminal
from job_extractor.discovery.models import (
    ApiCandidate,
    CandidateSource,
    DiscoveryResult,
    ProvenanceRecord,
    RejectedCandidate,
    TerminalCandidate,
)
from job_extractor.discovery.provider_fingerprint import fingerprint_features, fingerprint_terminal, extract_features


def moka_terminal_result(source_url="https://careers.custom-co.test/social-recruitment/acme/1#/jobs", include_asset=True, include_route=True, include_envelope=True, include_scope=True):
    url = source_url if include_route else "https://careers.custom-co.test/portal/1#/jobs"
    inventory = [CandidateSource(source_type="EMBEDDED_WIDGET", url="https://static-ats.mokahr.com/recruitment-web-client/javascripts/recruitmentWeb-20260101.js")] if include_asset else []
    rejected = []
    if include_envelope:
        rejected.append(RejectedCandidate(url="https://careers.custom-co.test/api/outer/ats-apply/website/jobs/v2", method="POST", score=-1, reasons=["LOW_JOB_ENTITY_DENSITY"], response_shape={"top_level_keys": ["data", "necromancer"]}))
    scope = {"recruitment_type": "social"}
    if include_scope:
        scope["siteId"] = 170070
    return DiscoveryResult(source_url=url, status="PARTIAL", detected_scope=scope, rejected_candidates=rejected, source_inventory=inventory)


def trusted_terminal(url, scope="SOCIAL", trust="TRUSTED", continuity="MATCH"):
    record = ProvenanceRecord(origin_url="https://careers.custom-co.test", url=url, host=url.split("/", 3)[2], scope_at_origin={"recruitment_type": scope.lower()}, scope_at_node={"recruitment_type": scope.lower()}, trust=trust)
    return TerminalCandidate(candidate=url, provenance_path=[record], scope=scope, company_continuity=continuity, trust=trust, plan="DOWNSTREAM_RECRUITMENT_PAGE", selected=True, provenance_score=100)


def test_custom_domain_moka_fingerprint_is_high_and_multi_signal():
    result = moka_terminal_result()
    fingerprint = fingerprint_terminal(result)
    assert fingerprint.provider == "moka"
    assert fingerprint.confidence == "HIGH"
    assert fingerprint.hostname_matched is False
    assert fingerprint.independent_signal_groups >= 3
    assert fingerprint.capabilities == ["ENCRYPTED_BROWSER_API"]


def test_single_structural_signal_is_not_promoted():
    result = DiscoveryResult(source_url="https://careers.custom-co.test/social-recruitment/acme/1#/jobs", status="PARTIAL")
    fingerprint = fingerprint_terminal(result)
    assert fingerprint.confidence == "LOW"
    assert fingerprint.provider != "moka"


def test_two_weak_signals_are_medium_not_high():
    result = moka_terminal_result(include_asset=False, include_envelope=False)
    fingerprint = fingerprint_terminal(result)
    assert fingerprint.provider == "moka"
    assert fingerprint.confidence == "MEDIUM"
    assert default_registry.route_fingerprint(fingerprint) is None


def test_encrypted_response_capability_requires_envelope():
    encrypted = moka_terminal_result()
    plain = DiscoveryResult(source_url="https://careers.custom-co.test/jobs", status="DISCOVERED", probable_list_api=ApiCandidate(url="https://careers.custom-co.test/api/jobs", method="GET", score=30, confidence="HIGH", replayable=True))
    dom_only = DiscoveryResult(source_url="https://careers.custom-co.test/jobs", status="PARTIAL", dom_fallback={"status": "DOM_LIST_DETECTED", "possible_detail_links": ["https://careers.custom-co.test/jobs/1"]})
    unsupported = DiscoveryResult(source_url="https://careers.custom-co.test/about", status="NOT_FOUND")
    assert "ENCRYPTED_BROWSER_API" in fingerprint_terminal(encrypted).capabilities
    assert fingerprint_terminal(plain).capabilities == ["PLAIN_HTTP_API"]
    assert fingerprint_terminal(dom_only).capabilities == ["DOM_ONLY"]
    assert fingerprint_terminal(unsupported).capabilities == ["UNSUPPORTED"]


def test_known_host_compatibility_remains_high_without_structural_signals():
    result = DiscoveryResult(source_url="https://app.mokahr.com/campus-recruitment/acme/1#/jobs", status="PARTIAL", detected_scope={"recruitment_type": "campus"})
    fingerprint = fingerprint_terminal(result)
    assert fingerprint.provider == "moka"
    assert fingerprint.confidence == "HIGH"
    assert fingerprint.hostname_matched is True
    assert "KNOWN_ADAPTER" in fingerprint.capabilities


def test_generic_paths_do_not_become_moka():
    controls = (
        DiscoveryResult(source_url="https://random.test/jobs", status="DISCOVERED", probable_list_api=ApiCandidate(url="https://random.test/api/jobs", method="GET", score=20, confidence="HIGH", replayable=True, response_shape={"top_level_keys": ["data", "jobs"]})),
        DiscoveryResult(source_url="https://random.test/social-recruitment/1#/jobs", status="PARTIAL"),
        DiscoveryResult(source_url="https://random.test/campus-recruitment/1#/jobs", status="PARTIAL"),
        DiscoveryResult(source_url="https://random.test/jobs", status="PARTIAL", rejected_candidates=[RejectedCandidate(url="https://random.test/api/jobs", method="POST", score=8, reasons=[], response_shape={"top_level_keys": ["data", "jobs"]})]),
    )
    for control in controls:
        fingerprint = fingerprint_terminal(control)
        assert not (fingerprint.provider == "moka" and fingerprint.confidence == "HIGH")
        assert default_registry.route_fingerprint(fingerprint) is None


def test_adapter_routing_by_fingerprint_uses_high_confidence_only():
    high = fingerprint_terminal(moka_terminal_result())
    low = fingerprint_terminal(DiscoveryResult(source_url="https://random.test/jobs", status="NOT_FOUND"))
    assert default_registry.route_fingerprint(high) is MokaAdapter
    assert default_registry.route_fingerprint(low) is None


def test_activation_routes_custom_domain_terminal_but_stays_non_executable():
    result = moka_terminal_result()
    url = result.source_url
    outcome = activate_terminal(trusted_terminal(url), lambda _url, _budget: result, default_registry)
    record = outcome.record
    assert record.provider == "moka"
    assert record.fingerprint_confidence == "HIGH"
    assert record.capability == "ENCRYPTED_BROWSER_API"
    assert record.routing_decision == "PROVIDER_FINGERPRINT_HIGH"
    assert record.adapter_name == MokaAdapter.__name__
    assert record.status == "NON_EXECUTABLE_TERMINAL"
    assert record.plan_valid is False
    assert outcome.discovery.provider_fingerprint.provider == "moka"


def test_activation_routing_is_gated_by_provenance_and_scope():
    result = moka_terminal_result()
    url = result.source_url
    untrusted = activate_terminal(trusted_terminal(url, trust="REJECTED"), lambda _url, _budget: result, default_registry)
    assert untrusted.record.routing_decision != "PROVIDER_FINGERPRINT_HIGH"
    assert untrusted.record.adapter_name is None
    scope_mismatch = activate_terminal(trusted_terminal(url, scope="CAMPUS"), lambda _url, _budget: result, default_registry)
    assert scope_mismatch.record.routing_decision != "PROVIDER_FINGERPRINT_HIGH"
    assert scope_mismatch.record.adapter_name is None
    continuity = activate_terminal(trusted_terminal(url, continuity="MISMATCH"), lambda _url, _budget: result, default_registry)
    assert continuity.record.routing_decision != "PROVIDER_FINGERPRINT_HIGH"


def test_feature_extraction_reads_terminal_trace_without_hostname():
    from job_extractor.discovery.models import TerminalActivationTrace
    from datetime import datetime
    result = DiscoveryResult(source_url="https://apply.custom.test/social-recruitment/acme/9#/jobs", status="PARTIAL", detected_scope={"recruitment_type": "social"})
    result.terminal_trace = TerminalActivationTrace(terminal_url=result.source_url, activation_started_at=datetime.now(), network=[{"url": "https://apply.custom.test/api/outer/ats-apply/website/jobs/v2", "method": "POST", "top_level_keys": ["data", "necromancer"]}], dom_evidence=[{"job_card_count": 30}])
    features = extract_features(result)
    assert any("ats-apply" in path for path in features.api_paths)
    fingerprint = fingerprint_features(features)
    assert fingerprint.provider == "moka"
    assert fingerprint.encrypted_envelope is True
