from time import perf_counter

import pytest

from job_extractor.adapters import default_registry
from job_extractor.discovery.budget import DiscoveryBudget,DiscoveryBudgetExceeded
from job_extractor.discovery.handoff import deep_recruitment_navigation
from job_extractor.discovery.models import ActionAttempt,DiscoveryResult,RecruitmentAction,RecruitmentEntry
from job_extractor.discovery.sources import (
    classify_action_result,classify_discovery_failure,classify_request,rank_spa_action,spa_action_inventory,
)


def action(text="查看职位",target="https://jobs.test/list",score=20):
    return RecruitmentAction(control_id="1",text=text,target=target,action_type="BUTTON",scope_semantics="ALL_JOBS",job_semantics=["JOB"],navigation_confidence="HIGH",source_element="button",score=score)


def test_global_deadline_is_monotonic_and_bounded():
    budget=DiscoveryBudget(total_seconds=.01,started=perf_counter()-1)
    with pytest.raises(DiscoveryBudgetExceeded):budget.check()


def test_phase_budget_cannot_consume_global_budget():
    budget=DiscoveryBudget(total_seconds=20,phase_limits={"ACTION_EXECUTION":2})
    assert 0<budget.seconds("ACTION_EXECUTION",10)<=2


@pytest.mark.parametrize(("url","kind"),[
    ("https://x.test/analytics/collect","ANALYTICS"),
    ("https://x.test/sentry/monitor","TELEMETRY"),
    ("https://x.test/pixel/webid","TRACKING"),
    ("https://x.test/i18n/locale","TRANSLATION"),
    ("https://x.test/api/job/positions","RECRUITMENT_RELEVANT"),
])
def test_background_request_classification(url,kind):
    assert classify_request(url)==kind


def test_action_ranking_rejects_esg_confusion():
    assert rank_spa_action("ESG 员工发展","/esg-employee-development")<0


def test_action_ranking_prefers_real_job_action():
    assert rank_spa_action("全部职位","/careers/jobs")>rank_spa_action("人才理念","/people")


def test_spa_button_inventory_records_action_metadata():
    class Page:
        def evaluate(self,_script):
            return [{"control_id":"4","tag":"button","text":"校园岗位投递","href":"","data_route":"/campus/jobs","data_url":"","onclick":"","role":"button","visible":True}]
    values=spa_action_inventory(Page(),"https://corp.test/careers","CAMPUS")
    assert values[0].action_type=="BUTTON" and values[0].scope_semantics=="CAMPUS" and values[0].target.endswith("/campus/jobs")


def test_action_result_recognizes_spa_route_transition():
    assert classify_action_result(route_changed=True,dom_changed=True)=="USEFUL_ROUTE_CHANGE"


def test_early_source_acceptance_outranks_more_navigation():
    assert classify_action_result(job_source=True,route_changed=True)=="JOB_SOURCE_FOUND"


def test_multi_hop_navigation_reaches_known_cross_host_ats():
    root=DiscoveryResult(source_url="https://corp.test/careers",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[RecruitmentEntry(entry_type="CAMPUS",text="校园招聘",url="https://corp.test/campus",destination_host="corp.test")])
    campus=DiscoveryResult(source_url="https://corp.test/campus",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[RecruitmentEntry(entry_type="CAMPUS",text="查看职位",url="https://app.mokahr.com/campus-recruitment/acme/1",destination_host="app.mokahr.com")])
    outcome=deep_recruitment_navigation(root,lambda url:campus,default_registry)
    assert outcome.handoff and outcome.handoff.url.startswith("https://app.mokahr.com")
    assert len(outcome.graph.edges)==2 and max(node.depth for node in outcome.graph.nodes)==2


def test_navigation_loop_is_prevented():
    root=DiscoveryResult(source_url="https://corp.test/a",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[RecruitmentEntry(entry_type="ALL_JOBS",text="Jobs",url="https://corp.test/b",destination_host="corp.test")])
    nested=DiscoveryResult(source_url="https://corp.test/b",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[RecruitmentEntry(entry_type="ALL_JOBS",text="Jobs",url="https://corp.test/a",destination_host="corp.test")])
    outcome=deep_recruitment_navigation(root,lambda _url:nested,default_registry)
    assert len(outcome.graph.nodes)==2


def test_action_budget_exhaustion_has_precise_reason():
    assert classify_discovery_failure(action_count=3,attempt_results=("NO_PROGRESS",)*3)=="ACTION_BUDGET_EXHAUSTED"


def test_timeout_artifact_schema_keeps_diagnostics():
    value=DiscoveryResult(source_url="https://corp.test",status="NOT_FOUND",failure_classification="GLOBAL_DISCOVERY_TIMEOUT",terminal_reason="GLOBAL_DISCOVERY_TIMEOUT",visited_nodes=[{"url":"https://corp.test"}],actions_discovered=[action()],actions_attempted=[ActionAttempt(action=action(),before_url="https://corp.test",after_url="https://corp.test",result="NO_PROGRESS")],request_classifications={"ANALYTICS":4})
    payload=value.model_dump()
    assert payload["terminal_reason"]=="GLOBAL_DISCOVERY_TIMEOUT" and payload["visited_nodes"] and payload["actions_attempted"] and payload["request_classifications"]["ANALYTICS"]==4
