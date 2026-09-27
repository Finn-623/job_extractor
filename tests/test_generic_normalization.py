from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.models import Job
from job_extractor.planning.models import CollectionPlan


def _collector(**changes):
    values = dict(
        source_url="https://portal.example.test/jobs", mode="HTTP_API", executable=True,
        list_endpoint="https://api.example.test/jobs", list_method="POST",
        pagination_type="SINGLE_RESPONSE", list_path="data.list", job_id_field="job_id",
        job_title_field="job_name", detail_mode="LIST_SUFFICIENT",
        observed_endpoints=["https://api.example.test/jobs"],
    )
    values.update(changes)
    return GenericHttpCollector(CollectionPlan(**values))


def _raw(**changes):
    value = {
        "job_id": "1", "job_name": "岗位", "contents": "职责", "company_name": "原始公司",
        "district_list": [{"area_cn": "青岛-市南区"}, {"area_cn": "上海"}, {"area_cn": " "}],
        "recruitment_type_cn": "社会招聘", "nature_cn": "社招", "education_cn": "本科",
        "major_cn": ["机械类", "自动化类", ""], "amount": 3,
        "_generic_source_scopes": ["社会招聘", "专项招聘"],
    }
    value.update(changes)
    return value


def test_generic_raw_core_fields_fill_empty_standard_output():
    job = _collector()._job(_raw())

    assert job is not None
    assert job.company == "原始公司"
    assert job.locations == ["青岛-市南区", "上海"]
    assert job.recruitment_type == "社会招聘"
    assert job.recruitment_scopes == ["社会招聘", "专项招聘"]
    assert job.education == "本科"
    assert job.major == ["机械类", "自动化类"]
    assert job.headcount == 3


def test_special_scope_provenance_does_not_change_job_recruitment_type():
    job = _collector()._job(_raw(recruitment_type_cn="社会招聘", _generic_source_scopes=["社会招聘", "专项招聘"]))

    assert job is not None
    assert job.recruitment_type == "社会招聘"
    assert job.recruitment_scopes == ["社会招聘", "专项招聘"]


def test_generic_existing_normalized_values_win_over_raw_fallbacks():
    raw = _raw(
        location={"name": "已有地点"},
        recruit_type={"name": "已有招聘属性"},
        amount=0,
    )
    job = _collector(company="已有公司")._job(raw)

    assert job is not None
    # Job-level employer wins over the site-level plan.company: on hosted
    # group portals the site company must never replace each posting's
    # legal entity (plan.company only fills in when raw has none).
    assert job.company == "原始公司"
    assert job.locations == ["已有地点"]
    assert job.recruitment_type == "已有招聘属性"
    assert job.headcount is None


def test_non_generic_job_schema_values_remain_compatible():
    job = Job(job_title="Adapter job", source_url="https://adapter.example.test", company="Adapter Co", major="单一专业")

    assert job.company == "Adapter Co"
    assert job.major == "单一专业"
    assert job.recruitment_scopes == []
