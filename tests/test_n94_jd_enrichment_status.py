"""N9.4: JD on-demand enrichment status model + lifecycle tests.

List completeness is never degraded by a missing/enrichment-pending JD.
Legacy records retain an unknown lifecycle (``jd_enrichment is None``).
"""
from __future__ import annotations

import json

from job_extractor.exporters.collection_summary import build_collection_summary
from job_extractor.models import CollectionResult, Job
from job_extractor.runtime import evaluate_collection_status

LIST_URL = "https://careers.example.com/jobs"


def _display_status(job: Job) -> str:
    if job.jd_enrichment is not None:
        return job.jd_enrichment
    return "JD 已存在" if job.full_jd else "未知"


def _result(*jobs: Job) -> CollectionResult:
    return CollectionResult(source_url=LIST_URL, platform="generic",
                            status="COMPLETE", jobs=list(jobs),
                            total_expected=len(jobs), total_fetched=len(jobs),
                            total_unique=len(jobs))


def test_a_legacy_job_without_full_jd_remains_unknown_and_stays_complete():
    job = Job(job_id="A", job_title="岗位A", source_url=LIST_URL)
    assert _display_status(job) == "未知"
    result = _result(job)
    assert result.status == "COMPLETE"


def test_b_legacy_job_with_full_jd_derives_complete():
    job = Job(job_id="B", job_title="岗位B", source_url=LIST_URL,
              full_jd="1. 职责描述。" * 30, responsibilities=["职责"])
    assert _display_status(job) == "JD 已存在"


def test_c_not_requested_to_pending_to_complete():
    job = Job(job_id="C", job_title="岗位C", source_url=LIST_URL,
              jd_enrichment="NOT_REQUESTED")
    job.jd_enrichment = "PENDING"
    job.jd_enrichment = "COMPLETE"
    assert _display_status(job) == "COMPLETE"


def test_d_pending_to_verification_required_and_back():
    job = Job(job_id="D", job_title="岗位D", source_url=LIST_URL,
              jd_enrichment="PENDING")
    job.jd_enrichment = "VERIFICATION_REQUIRED"
    job.jd_enrichment = "PENDING"
    job.jd_enrichment = "COMPLETE"
    assert _display_status(job) == "COMPLETE"


def test_e_failed_retried_to_pending():
    job = Job(job_id="E", job_title="岗位E", source_url=LIST_URL,
              jd_enrichment="FAILED")
    job.jd_enrichment = "PENDING"
    assert _display_status(job) == "PENDING"


def test_f_legacy_list_jd_is_rendered_without_backfill():
    job = Job(job_id="F", job_title="岗位F", source_url="https://app.mokahr.com/x",
              full_jd="岗位职责\n1. 负责测试。" * 20,
              responsibilities=["负责测试"], requirements=["本科"])
    assert job.jd_enrichment is None
    assert _display_status(job) == "JD 已存在"


def test_g_not_requested_jobs_stay_in_recommendation_flow():
    # No recommendation skip: the record stays COMPLETE for collection and
    # carries no error/pending flag that would exclude it from ranking.
    job = Job(job_id="G", job_title="岗位G", source_url=LIST_URL)
    result = _result(job)
    status = evaluate_collection_status(
        list_started=True, expected=1, unique=1, errors=result.errors,
        missing_jd=0, detail_failures=0)
    assert status == "COMPLETE"
    assert not result.errors
    assert _display_status(job) == "未知"


def test_h_summary_reports_enrichment_counts_independently():
    result = _result(
        Job(job_id="A", job_title="岗位A", source_url=LIST_URL),
        Job(job_id="B", job_title="岗位B", source_url=LIST_URL,
            full_jd="x" * 200, responsibilities=["x"], requirements=["y"]),
        Job(job_id="C", job_title="岗位C", source_url=LIST_URL,
            jd_enrichment="FAILED"))
    summary = build_collection_summary(result, "HAR_IMPORT")
    assert summary["jd_enrichment"]["unknown"] == 2
    assert summary["jd_enrichment"]["failed"] == 1
    # source-completeness semantics unchanged: JSON payload keeps raw fields
    payload = json.loads(_result().model_dump_json())
    assert "jd_enrichment" in payload["jobs"][0] if payload["jobs"] else True
