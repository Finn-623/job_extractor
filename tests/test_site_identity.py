"""Site-level (group/hosted-portal) company identity tests."""
from __future__ import annotations

from dataclasses import dataclass, field

from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.site_identity import (
    SiteIdentity, brand_subdomain_token, dominant_brand_prefix, resolve_site_company,
)
from job_extractor.models import CollectionResult, Job
from job_extractor.output_layout import resolve_run_directory


@dataclass
class FakeObservation:
    payload: dict = field(default_factory=dict)


@dataclass
class FakeCandidate:
    observed_company_names: list[str] = field(default_factory=list)


def iguopin_tenant_observation() -> FakeObservation:
    """Shape of the observed tenant/project-company response on hosted portals."""
    return FakeObservation(payload={"code": 200, "data": {"list": {
        "company_id": "10685294861878308",
        "company_parent_id": "",
        "name": "中国远洋海运集团有限公司",
        "short_name": "中远海运集团",
        "show_name": "中国远洋海运集团有限公司",
        "children": [
            {"company_id": "c2", "company_parent_id": "10685294861878308", "name": "中远海运集装箱运输有限公司"},
            {"company_id": "c3", "company_parent_id": "10685294861878308", "name": "中远海运重工股份有限公司"},
        ],
    }}})


def cosco_job_candidates() -> list[FakeCandidate]:
    # ~60% of the portal's distinct legal entities carry the group brand
    names = [f"中远海运{i}有限公司" for i in range(98)] + [f"其他企业{i}有限公司" for i in range(67)]
    return [FakeCandidate(observed_company_names=names)]


def test_hosted_platform_brand_token():
    assert brand_subdomain_token("https://coscoshipping.iguopin.com/job") == "coscoshipping"
    assert brand_subdomain_token("https://www.iguopin.com/job") is None
    assert brand_subdomain_token("https://iguopin.com/job") is None
    assert brand_subdomain_token("https://www.iguopin.com") is None
    assert brand_subdomain_token("https://app.mokahr.com/campus-recruitment/acme/1") == "app"
    assert brand_subdomain_token("https://jobs.x.test/careers") is None


def test_group_site_resolves_from_tenant_evidence():
    identity = resolve_site_company(
        "https://coscoshipping.iguopin.com/job",
        [iguopin_tenant_observation()],
        cosco_job_candidates(),
        page_title="招聘官网",  # SPA shell title: no brand here
    )
    assert isinstance(identity, SiteIdentity)
    assert identity.company == "中远海运集团"  # tenant short_name, evidence-backed
    assert any("job-company brand corroboration" in e for e in identity.evidence)
    assert any("tenant short_name" in e for e in identity.evidence)


def test_page_title_brand_alone_can_corroborate():
    identity = resolve_site_company(
        "https://coscoshipping.iguopin.com/job",
        [],  # tenant API not captured
        cosco_job_candidates(),
        page_title="中远海运集团人才招聘平台",
    )
    assert identity is not None
    assert identity.company == "中远海运集团"
    assert any("page title corroboration" in e for e in identity.evidence)


def test_multi_company_without_site_evidence_stays_none():
    # Tenant API absent, generic title: must NOT promote any job company
    identity = resolve_site_company(
        "https://portal.iguopin.com/job", [], cosco_job_candidates(), page_title="招聘官网",
    )
    assert identity is None


def test_non_hosted_multi_company_site_stays_none():
    identity = resolve_site_company(
        "https://careers.group.test/jobs", [iguopin_tenant_observation()], cosco_job_candidates(), page_title="招聘",
    )
    assert identity is None


def test_platform_brand_is_never_the_employer():
    # Tenant node literally named like the platform must be rejected
    observation = FakeObservation(payload={"data": {"list": {
        "company_id": "x1", "company_parent_id": "", "name": "iguopin", "short_name": "iguopin",
    }}})
    names = [f"中远海运{i}有限公司" for i in range(98)] + [f"其他企业{i}有限公司" for i in range(67)]
    identity = resolve_site_company("https://coscoshipping.iguopin.com/job", [observation], [FakeCandidate(observed_company_names=names)], page_title="iguopin")
    assert identity is None


def test_low_brand_coverage_group_without_tenant_name_stays_none():
    # Many distinct employers without a shared brand and no tenant/title name
    names = [f"甲公司{i}" for i in range(50)] + [f"乙公司{i}" for i in range(50)]
    identity = resolve_site_company("https://board.iguopin.com/jobs", [], [FakeCandidate(observed_company_names=names)], page_title="招聘官网")
    assert identity is None


def test_candidate_captures_observed_company_names():
    payload={"jobs":[{"id":1,"title":"A","location":"X","company":{"name":"One"}},{"id":2,"title":"B","location":"Y","company":{"name":"Two"}}]}
    c=GenericApiDetector._candidate(_Observation("https://x.test/jobs","GET",{}, {},payload,"initial"))
    assert c.observed_company_count==2 and c.observed_company_names==["One","Two"]


def dominant_prefix_helper():
    assert dominant_brand_prefix(["中远海运重工", "中远海运集运", "中远海运能源", "中远海运港口", "中远海运物流", "其他公司", "甲公司"]) == "中远海运"
    assert dominant_brand_prefix(["甲公司", "乙公司"]) is None


def test_job_company_keeps_legal_entity_on_group_site():
    # Site company resolves to the group; each job keeps its own employer
    from job_extractor.collectors.generic_http import GenericHttpCollector
    from tests.test_generic_collectors import Client
    from job_extractor.planning import CollectionPlan
    raws=[{"id":str(i),"title":f"Job {i}","company_name":f"中远海运子公司{i}有限公司","description":"职责：略。要求：略。"} for i in range(2)]
    plan=CollectionPlan(source_url="https://coscoshipping.iguopin.com/job",mode="HTTP_API",executable=True,
        company="中远海运集团",list_endpoint="https://gp-api.iguopin.com/api/jobs/v1/list",list_method="GET",
        pagination_type="SINGLE_RESPONSE",list_path="data.list",job_id_field="id",job_title_field="title",
        total_field="data.total",detail_mode="LIST_SUFFICIENT")
    client=Client([{"data":{"list":raws,"total":2}}])
    result=GenericHttpCollector(plan,client).collect()
    assert result.company=="中远海运集团"
    assert {j.company for j in result.jobs}=={"中远海运子公司0有限公司","中远海运子公司1有限公司"}


def test_job_without_own_company_falls_back_to_site_company():
    from job_extractor.collectors.generic_http import GenericHttpCollector
    from tests.test_generic_collectors import Client
    from job_extractor.planning import CollectionPlan
    plan=CollectionPlan(source_url="https://coscoshipping.iguopin.com/job",mode="HTTP_API",executable=True,
        company="中远海运集团",list_endpoint="https://gp-api.iguopin.com/api/jobs/v1/list",list_method="GET",
        pagination_type="SINGLE_RESPONSE",list_path="data.list",job_id_field="id",job_title_field="title",
        total_field="data.total",detail_mode="LIST_SUFFICIENT")
    client=Client([{"data":{"list":[{"id":"1","title":"Job","description":"职责：略。要求：略。"}],"total":1}}])
    result=GenericHttpCollector(plan,client).collect()
    assert result.jobs[0].company=="中远海运集团"


def test_output_directory_uses_site_company(tmp_path):
    run=resolve_run_directory(tmp_path,company="中远海运集团",source_url="https://coscoshipping.iguopin.com/job",failed=False)
    assert run.parent==tmp_path/"中远海运集团" and "_unknown" not in str(run)


def test_single_company_site_company_fallback_unaffected():
    # Existing single-company semantics: jobs without own employer keep plan.company
    from job_extractor.collectors.generic_http import GenericHttpCollector
    from tests.test_generic_collectors import Client
    from job_extractor.planning import CollectionPlan
    plan=CollectionPlan(source_url="https://x.test/jobs",mode="HTTP_API",executable=True,
        company="Acme",list_endpoint="https://api.x.test/jobs",list_method="GET",
        pagination_type="SINGLE_RESPONSE",list_path="data.items",job_id_field="id",job_title_field="title",
        total_field="data.total",detail_mode="LIST_SUFFICIENT")
    result=GenericHttpCollector(plan,Client([{"data":{"items":[{"id":"1","title":"Job","description":"职责：略。要求：略。"}],"total":1}}])).collect()
    assert result.company=="Acme" and result.jobs[0].company=="Acme"


def test_result_company_propagates_to_output(tmp_path):
    from job_extractor.models import CollectionMetrics
    result=CollectionResult(source_url="https://coscoshipping.iguopin.com/job",platform="generic",company="中远海运集团",
        status="COMPLETE",jobs=[Job(job_id="1",job_title="岗位",company="中远海运子公司有限公司",source_url="https://coscoshipping.iguopin.com/job",
        full_jd="职责：略。要求：略。",responsibilities=["职责：略。"],requirements=["要求：略。"])],
        total_expected=1,total_fetched=1,total_unique=1,metrics=CollectionMetrics())
    assert result.company=="中远海运集团" and result.jobs[0].company=="中远海运子公司有限公司"
