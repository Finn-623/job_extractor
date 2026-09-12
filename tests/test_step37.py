from time import perf_counter
from types import SimpleNamespace

from job_extractor.adapters import default_registry
from job_extractor.discovery import activation
from job_extractor.discovery.activation import activate_terminal
from job_extractor.discovery.budget import TERMINAL_ACTIVATION_SECONDS, terminal_activation_budget
from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.models import ApiCandidate, DiscoveryResult, ProvenanceRecord, RecruitmentAction, TerminalCandidate
from job_extractor.discovery.sources import select_terminal_actions


def terminal(url="https://corp.test/campus/jobs"):
    record=ProvenanceRecord(origin_url="https://corp.test/careers",url=url,host=url.split("/",3)[2],scope_at_origin={"recruitment_type":"campus"},scope_at_node={"recruitment_type":"campus"},trust="TRUSTED")
    return TerminalCandidate(candidate=url,provenance_path=[record],scope="CAMPUS",company_continuity="MATCH",trust="TRUSTED",plan="DOWNSTREAM_RECRUITMENT_PAGE",selected=True,provenance_score=100)


def action(index,score):
    return RecruitmentAction(control_id=str(index),text="查看职位",target=f"https://corp.test/jobs/{index}",action_type="LINK",scope_semantics="CAMPUS",job_semantics=["JOB"],navigation_confidence="HIGH" if score>=18 else "MEDIUM",score=score)


def test_terminal_only_observation_uses_selected_terminal_url():
    called=[]
    def discover(url,budget):
        called.append((url,budget.total_seconds));return DiscoveryResult(source_url=url,status="NOT_FOUND")
    activate_terminal(terminal(),discover,default_registry)
    assert called==[("https://corp.test/campus/jobs",TERMINAL_ACTIVATION_SECONDS)]


def test_terminal_activation_budget_is_independent_and_bounded():
    budget=terminal_activation_budget()
    assert budget.total_seconds==25 and budget.seconds("INITIAL_NAVIGATION",20)<=8


def test_known_ats_is_rechecked_after_redirect_resolution():
    destination="https://app.mokahr.com/campus-recruitment/acme/1#/jobs"
    result=DiscoveryResult(source_url="https://corp.test/redirect",resolved_url=destination,status="PARTIAL",detected_scope={"recruitment_type":"campus"})
    outcome=activate_terminal(terminal("https://corp.test/redirect"),lambda _url,_budget:result,default_registry)
    assert outcome.record.status=="KNOWN_ATS" and outcome.record.resolved_url==destination


def test_terminal_action_extraction_keeps_only_two_strong_actions():
    selected=select_terminal_actions([action(1,30),action(2,20),action(3,12),action(4,2)])
    assert [value.control_id for value in selected]==["1","2"]


def test_terminal_api_attribution_keeps_trigger_action():
    observation=_Observation("https://corp.test/api/jobs","GET",{}, {},{"jobs":[{"id":"1","title":"A"},{"id":"2","title":"B"}]},"ACTION_EXECUTION",origin_url="https://corp.test/campus",page_url="https://corp.test/campus",trigger_action_id="search",trigger_action_text="搜索职位",trigger_action_type="BUTTON")
    candidate=GenericApiDetector._candidate(observation)
    assert candidate.provenance.trigger_action_id=="search"
    assert "搜索职位" in candidate.provenance.company_context_evidence[-1]


def test_terminal_privacy_record_cannot_block_action_discovery():
    observation=_Observation("https://corp.test/api/privacy-policy/get","GET",{}, {},{"data":[{"id":"1","name":"Privacy","content":"policy"}]},"INITIAL_EVIDENCE",origin_url="https://corp.test/campus",page_url="https://corp.test/campus")
    candidate=GenericApiDetector._candidate(observation)
    assert "NON_JOB_SEMANTIC_SOURCE" in candidate.rejection_reasons
    assert GenericApiDetector._rank([observation])==[]


def test_upstream_or_unrelated_page_noise_is_excluded():
    observation=_Observation("https://noise.test/api/jobs","GET",{}, {},{"jobs":[{"id":"1","title":"A"},{"id":"2","title":"B"}]},"INITIAL_EVIDENCE",origin_url="https://corp.test/campus",page_url="https://noise.test/jobs",provenance_trust="REJECTED",provenance_rejection="ORPHAN_SOURCE")
    assert GenericApiDetector._rank([observation])==[]


def test_high_source_promotion_requires_validator_and_job_evidence(monkeypatch):
    probable=ApiCandidate(url="https://corp.test/api/jobs",method="GET",score=30,confidence="HIGH",response_shape={"sample_field_names":["id","title"],"candidate_list_path":"jobs"},observed_list_length=2,observed_unique_ids=2,job_entity_density=1.0,replayable=True)
    discovered=DiscoveryResult(source_url="https://corp.test/campus/jobs",status="DISCOVERED",probable_list_api=probable,candidate_list_apis=[probable],detected_scope={"recruitment_type":"campus"})
    monkeypatch.setattr(activation,"CollectionPlanBuilder",lambda:SimpleNamespace(build=lambda _result:SimpleNamespace(confidence="HIGH")))
    monkeypatch.setattr(activation,"CollectionPlanValidator",lambda:SimpleNamespace(validate=lambda _plan:SimpleNamespace(valid=True)))
    outcome=activate_terminal(terminal(),lambda _url,_budget:discovered,default_registry)
    assert outcome.record.status=="HIGH_GENERIC_SOURCE" and outcome.record.plan_valid


def test_activation_timeout_has_precise_classification():
    budget=terminal_activation_budget(.01);budget.started=perf_counter()-1
    discovered=DiscoveryResult(source_url="https://corp.test/campus/jobs",status="NOT_FOUND",failure_classification="GLOBAL_DISCOVERY_TIMEOUT")
    outcome=activate_terminal(terminal(),lambda _url,_budget:discovered,default_registry,budget)
    assert outcome.record.status=="ACTIVATION_TIMEOUT"


def test_budget_overrun_accounting_is_bounded_for_controlled_exit():
    budget=terminal_activation_budget(.01);started=perf_counter()
    discovered=DiscoveryResult(source_url="https://corp.test/campus/jobs",status="NOT_FOUND",failure_classification="GLOBAL_DISCOVERY_TIMEOUT")
    outcome=activate_terminal(terminal(),lambda _url,_budget:discovered,default_registry,budget)
    assert outcome.record.elapsed_seconds<.1 and perf_counter()-started<.1
