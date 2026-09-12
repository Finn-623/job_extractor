from datetime import datetime
from pathlib import Path
from job_extractor.models import CollectionMetrics, CollectionResult, Job
from job_extractor.runtime import (MetricsRecorder, build_output_filename,
    evaluate_collection_status, evaluate_data_completeness, finalize_result, make_error, render_result)

def test_metrics_defaults():
    m=CollectionMetrics(); assert m.list_requests==0 and m.jd_strategy=="UNKNOWN" and m.page_size is None
def test_recorder_counts_and_settings():
    r=MetricsRecorder(); r.record_list_request(); r.record_page(); r.record_detail_request(True); r.record_detail_request(False); r.set_page_size(20); r.set_jd_strategy("MIXED"); m=r.finish()
    assert (m.list_requests,m.list_pages,m.detail_requests)==(1,1,2)
    assert (m.details_attempted,m.details_succeeded,m.details_failed)==(2,1,1)
    assert m.page_size==20 and m.jd_strategy=="MIXED"
def test_elapsed_uses_monotonic_timer(): assert MetricsRecorder().finish().elapsed_seconds>=0
def test_status_complete(): assert evaluate_collection_status(list_started=True,expected=2,unique=2,errors=[])=="COMPLETE"
def test_status_incomplete_count(): assert evaluate_collection_status(list_started=True,expected=2,unique=1,errors=[])=="INCOMPLETE"
def test_source_missing_jd_does_not_fail_collection(): assert evaluate_collection_status(list_started=True,expected=1,unique=1,errors=[],missing_jd=1)=="COMPLETE"
def test_detail_failure_remains_incomplete(): assert evaluate_collection_status(list_started=True,expected=1,unique=1,errors=[],detail_failures=1)=="INCOMPLETE"
def test_status_failed(): assert evaluate_collection_status(list_started=False,expected=None,unique=0,errors=["x"])=="FAILED"
def test_error_is_deterministic_and_sanitized():
    e=make_error("DETAIL_ERROR","timeout token=abc",job_id="1",aes_key="bad")
    assert e=="DETAIL_ERROR job_id=1 reason=timeout token=[REDACTED]" and "bad" not in e
def test_filename_is_cross_platform_safe():
    n=build_output_filename("https://app.mokahr.com/x",datetime(2026,9,4,23,51,51))
    assert n=="app_mokahr_com_20260904_235151.json" and not any(c in n for c in ':?*<>|')
def test_generic_renderer_zhiye():
    r=CollectionResult(source_url="u",platform="zhiye",scope={"business_type":"campus","category":["2"]},metrics=CollectionMetrics(list_requests=3,list_pages=3,jd_strategy="LIST_SUFFICIENT"),total_expected=285,total_fetched=285,total_unique=285,status="COMPLETE")
    out=render_result(r,"ZhiyeAdapter",Path("out.json")); assert "business_type: campus" in out and "List requests: 3" in out
def test_generic_renderer_moka():
    r=CollectionResult(source_url="u",platform="moka",scope={"recruitment_type":"campus","org_id":"nbdeli","site_id":70019},metrics=CollectionMetrics(list_requests=2,detail_requests=37,jd_strategy="DETAIL_REQUIRED"),total_expected=37,total_fetched=37,total_unique=37,status="COMPLETE")
    out=render_result(r,"MokaAdapter",Path("out.json")); assert "org_id: nbdeli" in out and "Detail requests: 37" in out
def test_completeness_counts_source_null_fields():
    jobs=[Job(job_title="complete",source_url="u",full_jd="x",responsibilities=["r"],requirements=["q"]),
          Job(job_title="missing",source_url="u")]
    d=evaluate_data_completeness(CollectionResult(source_url="u",status="COMPLETE",jobs=jobs))
    assert (d.complete_jobs,d.missing_jd_jobs,d.missing_requirements_jobs,d.missing_responsibilities_jobs)==(1,1,1,1)
    assert d.completeness_ratio==0.5
def test_warning_and_cli_completeness_rendering():
    class Adapter:
        list_requests=1; page_count=1; details_failed=0; details_succeeded=0; details_attempted=0
        page_size=10; detail_strategy="DETAIL_FALLBACK"; elapsed_seconds=1
    r=CollectionResult(source_url="u",total_expected=1,total_fetched=1,total_unique=1,status="INCOMPLETE",
        jobs=[Job(job_title="missing",source_url="u")])
    finalize_result(r,Adapter()); out=render_result(r,"A",Path("out.json"))
    assert r.status=="COMPLETE" and r.warnings and "Missing JD at source: 1" in out and "Warnings:" in out
    dumped=r.model_dump(); assert "data_completeness" in dumped and "warnings" in dumped
def test_output_model_contains_unified_metadata():
    d=CollectionResult(source_url="u",status="FAILED").model_dump()
    assert {"source_url","platform","company","scope","metrics","data_completeness","warnings","total_expected","total_fetched","total_unique","status","errors","jobs"} <= d.keys()
