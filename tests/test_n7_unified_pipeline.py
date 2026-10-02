"""N7: Auto / cURL / HAR must flow through one formal pipeline with one
Job schema and identical exporter behavior. No source-specific branches."""
from __future__ import annotations

import json
from pathlib import Path

from job_extractor.har_importer import import_har
from job_extractor.manual_curl import ManualCurlResult, manual_result_to_collection_result
from job_extractor.models import CollectionResult, Job
from job_extractor.reporting.manager import ReportManager


def _har_file(tmp_path: Path, *, with_jd: bool) -> str:
    record = {"code": "A", "jobName": "Job A", "workPlace": "Shanghai",
              "postType": "R&D"}
    if with_jd:
        record.update({"jobDescription": "1、职责A\n2、职责B",
                       "jobRequirement": "1、要求A"})
    body = {"success": True, "errorCode": 0, "result": {"total": 2,
                                                        "list": [record]}}
    entries = [{"request": {"method": "POST",
                            "url": "https://x.test/api/recruit/position/list",
                            "postData": {"text": json.dumps({"page": 1, "pageSize": 2})}},
                "response": {"status": 200,
                             "content": {"text": json.dumps(body)}}}]
    file = tmp_path / ("jd.har" if with_jd else "nojd.har")
    file.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    return str(file)


def _curl_result() -> CollectionResult:
    job = Job(job_id="1", job_title="岗位", source_url="https://x.test",
              full_jd="职责\n要求", responsibilities=["职责"],
              requirements=["要求"], locations=["北京"])
    manual = ManualCurlResult("data", 1, 22, "HIGH", [job], 0, 1, 1, [],
                              "COMPLETE", "TOTAL_REACHED", 0.1, 1)
    return manual_result_to_collection_result(manual, "https://x.test")


def _auto_result() -> CollectionResult:
    return CollectionResult(
        source_url="https://x.test/jobs", platform="generic",
        total_expected=1, total_fetched=1, total_unique=1, status="COMPLETE",
        jobs=[Job(job_id="9", job_title="自动岗位", source_url="https://x.test/jobs",
                  locations=["上海"], full_jd="职责\n要求",
                  responsibilities=["职责"], requirements=["要求"])])


def _assert_standard_exports(result: CollectionResult) -> Path:
    artifacts = ReportManager().generate_reports(result, None,
                                                 collection_mode=result.platform or "generic")
    for name in ("jobs.json", "jobs.csv", "jobs.xlsx", "report.md",
                 "collection.json"):
        assert (artifacts.output_directory / name).is_file(), name
    return artifacts.output_directory


def test_three_sources_share_one_job_schema():
    har = import_har(_har_file(Path("/tmp"), with_jd=False)).result
    schema = set(Job.model_fields)
    assert set(har.jobs[0].model_fields) == schema
    assert set(_curl_result().jobs[0].model_fields) == schema
    assert set(_auto_result().jobs[0].model_fields) == schema
    assert "platform" in {"collection_mode"} or har.metrics.collection_mode == "HAR_IMPORT"
    assert _curl_result().metrics.collection_mode == "MANUAL_CURL"


def test_all_sources_export_all_formats(tmp_path):
    _assert_standard_exports(_auto_result())
    _assert_standard_exports(_curl_result())
    har = import_har(_har_file(tmp_path, with_jd=False)).result
    artifacts = ReportManager().generate_reports(har, tmp_path / "har-out",
                                                 collection_mode="HAR_IMPORT")
    for name in ("jobs.json", "jobs.csv", "jobs.xlsx", "report.md",
                 "collection.json"):
        assert (artifacts.output_directory / name).is_file(), name


def test_har_source_jd_enrichment_via_list_record(tmp_path):
    result = import_har(_har_file(tmp_path, with_jd=True)).result
    job = result.jobs[0]
    assert job.full_jd and job.requirements
    # Shared semantics: a whole-text description keeps responsibilities empty
    # (identical to the cURL collector's list_job path).
    assert job.responsibilities == []
    assert result.metrics.jd_strategy == "LIST_SUFFICIENT"
    assert result.data_completeness.missing_jd_jobs == 0


def test_har_source_partial_jd_keeps_list_and_export(tmp_path):
    outcome = import_har(_har_file(tmp_path, with_jd=False))
    result = outcome.result
    assert result.jobs and result.jobs[0].full_jd is None
    # N9 Final: a HAR import without any list JD is the explicit Browser
    # Assist path — a legal List-only completion, no longer "UNKNOWN".
    assert result.metrics.jd_strategy == "LIST_ONLY"
    assert result.data_completeness.missing_jd_jobs == 1
    assert result.data_completeness.total_jobs == 1
    assert result.status == "COMPLETE"  # list success is never discarded
    artifacts = ReportManager().generate_reports(result, tmp_path / "out",
                                                 collection_mode="HAR_IMPORT")
    assert (artifacts.output_directory / "jobs.xlsx").is_file()
    assert (artifacts.output_directory / "jobs.csv").is_file()


def test_duplicate_and_fallback_metadata_semantics(tmp_path):
    outcome = import_har(_har_file(tmp_path, with_jd=False))
    result = outcome.result
    assert result.metrics.duplicate_jobs >= 0
    assert result.metrics.termination_reason == "HAR_IMPORT"
    assert result.metrics.collection_mode == "HAR_IMPORT"
    assert "har_import" in result.enrichment
    unified = _curl_result()
    assert unified.metrics.collection_mode == "MANUAL_CURL"
    assert unified.total_unique == unified.metrics.unique_jobs
