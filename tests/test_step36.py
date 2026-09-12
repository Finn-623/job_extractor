from job_extractor.adapters import default_registry
from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.handoff import deep_recruitment_navigation
from job_extractor.discovery.models import DiscoveryResult, RecruitmentEntry


def entry(kind: str, text: str, url: str) -> RecruitmentEntry:
    return RecruitmentEntry(
        entry_type=kind,
        text=text,
        url=url,
        destination_host=url.split("/", 3)[2],
        evidence=["visible recruitment entry text", "official page supplied destination"],
        action_id=f"action-{kind.lower()}",
        action_type="LINK",
    )


def test_official_cross_host_transition_is_trusted():
    root = DiscoveryResult(
        source_url="https://careers.dji.com/zh-CN",
        status="PARTIAL",
        recruitment_entries=[entry("SOCIAL", "社会招聘职位", "https://apply.careers.dji.com/social/jobs")],
    )
    nested = DiscoveryResult(source_url="https://apply.careers.dji.com/social/jobs", status="NOT_FOUND")
    outcome = deep_recruitment_navigation(root, lambda _url: nested, default_registry)
    assert outcome.graph.edges[0].trust == "TRUSTED"
    assert "company domain" in " ".join(outcome.graph.edges[0].trust_evidence)


def test_orphan_network_source_is_rejected_from_ranking():
    observation = _Observation(
        "https://other.test/api/jobs", "GET", {}, {},
        {"jobs": [{"id": "1", "title": "Engineer"}, {"id": "2", "title": "Analyst"}]},
        "INITIAL_EVIDENCE", origin_url="https://corp.test/careers",
        page_url="https://unrelated.test/jobs", provenance_trust="REJECTED",
        provenance_rejection="ORPHAN_SOURCE",
    )
    candidate = GenericApiDetector._candidate(observation)
    assert "ORPHAN_SOURCE" in candidate.rejection_reasons
    assert GenericApiDetector._rank([observation]) == []


def test_company_mismatch_is_hard_rejected():
    root = DiscoveryResult(
        source_url="https://talent.catl.com/", status="PARTIAL", company="CATL",
        recruitment_entries=[entry("CAMPUS", "校园职位", "https://jobs.autoflight.com/campus")],
    )
    outcome = deep_recruitment_navigation(root, lambda _url: (_ for _ in ()).throw(AssertionError("must not navigate")), default_registry)
    candidate = outcome.graph.terminal_candidates[0]
    assert candidate.trust == "REJECTED"
    assert candidate.rejected_reason == "PROVENANCE_COMPANY_MISMATCH"


def test_visible_known_ats_handoff_is_trusted():
    root = DiscoveryResult(
        source_url="https://talent.catl.com/", status="PARTIAL", company="CATL",
        detected_scope={"recruitment_type": "campus"},
        recruitment_entries=[entry("CAMPUS", "校园招聘", "https://app.mokahr.com/campus-recruitment/catlhr/148948#/jobs")],
    )
    outcome = deep_recruitment_navigation(root, lambda _url: (_ for _ in ()).throw(AssertionError("known ATS must stop early")), default_registry)
    assert outcome.handoff and outcome.graph.terminal_reason == "KNOWN_ATS_HANDOFF"
    assert outcome.graph.terminal_candidates[0].trust == "TRUSTED"


def test_scope_is_preserved_across_provenance_path():
    root = DiscoveryResult(
        source_url="https://talent.catl.com/campus", status="PARTIAL",
        detected_scope={"recruitment_type": "campus"},
        recruitment_entries=[entry("CAMPUS", "校园岗位", "https://app.mokahr.com/campus-recruitment/catlhr/1#/jobs")],
    )
    outcome = deep_recruitment_navigation(root, lambda _url: None, default_registry)
    selected = next(value for value in outcome.graph.terminal_candidates if value.selected)
    assert selected.scope == "CAMPUS"
    assert selected.provenance_path[0].scope_at_origin["recruitment_type"] == "campus"
    assert selected.provenance_path[-1].scope_at_node["recruitment_type"] == "campus"


def test_unrelated_careers_host_never_enters_navigation_queue():
    root = DiscoveryResult(
        source_url="https://careers.alpha.test/", status="PARTIAL",
        recruitment_entries=[entry("ALL_JOBS", "All jobs", "https://careers.beta.test/jobs")],
    )
    called = []
    outcome = deep_recruitment_navigation(root, lambda url: called.append(url), default_registry)
    assert called == []
    assert outcome.graph.terminal_candidates[0].rejected_reason == "PROVENANCE_COMPANY_MISMATCH"


def test_terminal_ranking_prefers_company_continuity():
    root = DiscoveryResult(
        source_url="https://talent.catl.com/campus", status="PARTIAL", company="CATL",
        recruitment_entries=[
            entry("CAMPUS", "校园职位", "https://app.mokahr.com/campus-recruitment/other/9#/jobs"),
            entry("CAMPUS", "校园职位", "https://app.mokahr.com/campus-recruitment/catlhr/1#/jobs"),
        ],
    )
    outcome = deep_recruitment_navigation(root, lambda _url: None, default_registry)
    selected = next(value for value in outcome.graph.terminal_candidates if value.selected)
    assert "catlhr" in selected.candidate
    assert selected.company_continuity == "MATCH"


def test_trusted_terminal_stops_lower_ranked_navigation():
    root = DiscoveryResult(
        source_url="https://talent.catl.com/campus", status="PARTIAL",
        recruitment_entries=[
            entry("CAMPUS", "校园招聘", "https://app.mokahr.com/campus-recruitment/catlhr/1#/jobs"),
            entry("CAMPUS", "更多职位", "https://talent.catl.com/campus/more"),
        ],
    )
    called = []
    outcome = deep_recruitment_navigation(root, lambda url: called.append(url), default_registry)
    assert outcome.handoff and called == []


def test_provenance_artifact_serialization_is_complete():
    root = DiscoveryResult(
        source_url="https://talent.catl.com/campus", status="PARTIAL",
        detected_scope={"recruitment_type": "campus"},
        recruitment_entries=[entry("CAMPUS", "校园招聘", "https://app.mokahr.com/campus-recruitment/catlhr/1#/jobs")],
    )
    result = deep_recruitment_navigation(root, lambda _url: None, default_registry).terminal_result
    payload = result.model_dump(mode="json")
    candidate = payload["terminal_candidates"][0]
    record = candidate["provenance_path"][-1]
    required = {"origin_url", "url", "parent_node_id", "parent_url", "trigger_action_id", "trigger_action_text", "trigger_action_type", "redirect_chain", "scope_at_origin", "scope_at_node", "host", "company_context_evidence", "depth"}
    assert required <= record.keys()
    assert candidate["selected"] is True and candidate["trust"] == "TRUSTED"
