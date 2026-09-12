from job_extractor.collectors.generic_http import GenericHttpCollector,duplicate_audit
from job_extractor.discovery.containers import bind_total_candidates,classify_total
from job_extractor.discovery.models import DiscoveryResult,ListContainer,VisibleTotalEvidence
from job_extractor.identity import job_identity
from job_extractor.planning import CollectionPlan,CollectionPlanBuilder,CollectionPlanValidator

def test_list_container_total_proximity_and_scoped_acceptance():
    evidence,total,conflict=bind_total_candidates([{"text":"14 results","proximity":90,"locator":"#jobs preceding-sibling"}],14,{"url_filters":{"location":"sydney-SYD"}},"#jobs")
    assert total==14 and not conflict and evidence[0].container_id=="#jobs" and evidence[0].semantics=="SCOPED_TOTAL"

def test_global_total_is_rejected_for_scoped_container():
    evidence,total,conflict=bind_total_candidates([{"text":"83 openings","proximity":60,"locator":"main marketing"}],3,{"path_filters":{"location":"sydney"}},"#sydney-jobs")
    assert total is None and evidence[0].semantics=="GLOBAL_TOTAL" and not conflict

def test_large_unqualified_total_is_global_even_in_nearby_section():
    evidence,total,_=bind_total_candidates([{"text":"83 openings","proximity":75,"locator":"main section"}],3,{"path_filters":{"location":"sydney"}},"#jobs")
    assert total is None and evidence[0].semantics=="GLOBAL_TOTAL"

def test_current_page_count_classification():
    assert classify_total("10 results shown",10,10,False,100)=="CURRENT_PAGE_COUNT"
    assert classify_total("Results 1",1,11,False,100)=="CURRENT_PAGE_COUNT"
    assert classify_total("Page 1",1,10,False,100)=="PAGE_NUMBER"

def test_total_conflict_blocks_nonpaginated_dom_plan():
    result=DiscoveryResult(source_url="https://x.test/jobs",status="PARTIAL",visible_total_conflict=True,dom_fallback={"status":"DOM_LIST_DETECTED","possible_detail_links":["https://x.test/jobs/1","https://x.test/jobs/2"],"card_link_parity":"CARD_LINK_PARITY"})
    plan=CollectionPlanBuilder().build(result);validation=CollectionPlanValidator().validate(plan)
    assert not plan.executable and "TOTAL_CONFLICT_UNRESOLVED" in plan.warnings and "TOTAL_CONFLICT_UNRESOLVED" in validation.errors

def test_requisition_id_precedes_other_ids_and_url():
    identity=job_identity({"id":"stable","jobId":"job","requisitionId":"REQ-42"},"https://x.test/jobs/99999/title")
    assert identity["identity_source"]=="REQUISITION_ID" and identity["identity_value"]=="REQ-42"

def test_url_identifier_prevents_slug_collision():
    first=job_identity({},"https://jobs.test/details/114437988/au-manager",title="AU Manager")
    second=job_identity({},"https://jobs.test/details/200658298-6385/au-manager",title="AU Manager")
    assert first["identity_value"]=="114437988" and second["identity_value"]=="200658298-6385" and first!=second

def test_composite_identity_fallback_uses_more_than_slug():
    first=job_identity({},"https://jobs.test/team-a/manager",title="Manager",location="Sydney",company="X")
    second=job_identity({},"https://jobs.test/team-b/manager",title="Manager",location="Sydney",company="X")
    assert first["identity_source"]=="COMPOSITE_FINGERPRINT" and first["identity_value"]!=second["identity_value"]

def base_plan():
    return CollectionPlan(source_url="https://x.test/jobs",mode="HTTP_API",executable=True,list_endpoint="https://x.test/api",list_method="GET",pagination_type="SINGLE_RESPONSE",list_path="jobs",job_id_field="id",job_title_field="title",detail_mode="LIST_SUFFICIENT",observed_endpoints=["https://x.test/api"])

def test_ambiguous_id_collision_is_reported_not_explained():
    raws=[{"id":"1","title":"Engineer","url":"https://x.test/jobs/alpha"},{"id":"1","title":"Analyst","url":"https://x.test/jobs/beta"}]
    audit=duplicate_audit(raws,base_plan())
    assert audit["groups"][0]["reason"]=="AMBIGUOUS_ID_COLLISION" and audit["unexplained_count"]==1 and audit["groups"][0]["records_in_group"]==2

def test_identical_and_same_requisition_duplicate_classification():
    plan=base_plan();same={"id":"1","title":"Engineer","url":"https://x.test/jobs/10001"}
    assert duplicate_audit([same,dict(same)],plan)["groups"][0]["reason"]=="IDENTICAL_SOURCE_RECORD"
    presented=[same,{**same,"location":"Remote"}]
    assert duplicate_audit(presented,plan)["groups"][0]["reason"]=="SAME_REQUISITION_DUPLICATE_PRESENTATION"
