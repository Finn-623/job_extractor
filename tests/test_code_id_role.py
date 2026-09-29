"""Bare ``code`` as a job id only with explicit job-entity evidence (PDD-like)."""
from job_extractor.discovery.scorer import confidence, score_list
from job_extractor.manual_curl import parse_curl


PDD_RECORDS=[
    {"code":"123","name":"采购岗位","job":"supply","workLocation":"上海","updateTime":"2026-09-01"},
    {"code":"456","name":"供应链岗位","job":"supply","workLocation":"深圳","updateTime":"2026-09-02"},
]


def test_pdd_like_code_name_job_context_is_credible():
    score,evidence,shape=score_list(
        "https://careers.pddglobalhr.com/jobs/api/recruit/position/list",
        {"list":PDD_RECORDS,"total":2})
    assert shape["candidate_list_path"]=="list"
    assert shape["inferred_job_id_field"]=="code"
    assert shape["inferred_job_title_field"]=="name"
    assert shape["job_entity_density"]>0
    assert "LOW_JOB_ENTITY_DENSITY" not in shape["rejection_reasons"]
    assert not shape["rejection_reasons"]
    assert confidence(score)=="HIGH"


def test_bare_code_name_alone_stays_rejected():
    score,_,shape=score_list("https://x.test/api/list",
        {"list":[{"code":"A","name":"foo"},{"code":"B","name":"bar"}]})
    assert shape["inferred_job_id_field"]!="code"
    assert "LOW_JOB_ENTITY_DENSITY" in shape["rejection_reasons"]
    assert confidence(score)!="HIGH"


def test_explicit_id_fields_keep_priority_over_code():
    for id_field in ("jobId","job_id","positionId","requisitionId"):
        _,_,shape=score_list("https://x.test/api/jobs/list",
            {"data":{"records":[{id_field:"p1","name":"Engineer","city":"上海"},
                                {id_field:"p2","name":"Designer","city":"深圳"}]}})
        assert shape["inferred_job_id_field"]==id_field


def test_render_job_id_substitutes_code():
    spec=parse_curl("curl 'https://x.test/api/position/detail?code=&lang=zh'")
    assert spec.render_job_id("XZ0000619").url=="https://x.test/api/position/detail?code=XZ0000619&lang=zh"
