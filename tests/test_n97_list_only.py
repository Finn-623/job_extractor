"""N9 Final: LIST_ONLY productization — Browser Assist/HAR anti-bot scenario.

A PDD-type site allows list-only completion (list fully collected, detail
pages not safely batch-fetchable). The flag rides the existing
``metrics.jd_strategy``; normal missing-JD runs must never be auto-downgraded,
and list-only statistics must never report JD complete = 岗位总数.
"""
from __future__ import annotations

import json
from pathlib import Path

from job_extractor.har_importer import import_har
from job_extractor.manual_curl import ManualCurlResult, manual_result_to_collection_result
from job_extractor.models import CollectionResult, DataCompleteness, Job
from job_extractor.progress import ProgressReporter
from job_extractor.runtime import render_result


def _har_file(tmp_path: Path, *, with_jd: bool, company: str = "pdd") -> str:
    record = {"code": "A", "jobName": "Job A", "workPlace": "Shanghai",
              "postType": "R&D"}
    if with_jd:
        record.update({"jobDescription": "1、职责A", "jobRequirement": "1、要求A"})
    body = {"success": True, "errorCode": 0,
            "result": {"total": 2, "list": [record]}}
    entries = [{"request": {"method": "POST",
                            "url": "https://careers.pdd.example.com/api/list",
                            "postData": {"text": json.dumps({"page": 1, "pageSize": 2})}},
                "response": {"status": 200, "content": {"text": json.dumps(body)}}}]
    file = tmp_path / "list_only.har"
    file.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    return str(file)


def _list_only_manual_result() -> ManualCurlResult:
    """The explicit Browser Assist legal completion (detail blocked)."""
    from job_extractor.collectors.detail_resolver import DetailResolution

    job = Job(job_id="1", job_title="岗位", source_url="https://jobs.test/jobs")
    result = ManualCurlResult("data", 1, 22, "HIGH", [job], 0, 1, 1, [],
                              "COMPLETE", "TOTAL_REACHED", 0.1, 1, "LIST_ONLY")
    resolution = DetailResolution()
    resolution.list_only_used = True
    resolution.jd_success = 0
    resolution.jd_failed = resolution.jd_missing = 1
    result.detail_resolution = resolution
    return result


class _Sink:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, text: str) -> None:
        self.chunks.append(text)

    def __call__(self, text: str) -> None:
        self.chunks.append(text)


# ---- a. PDD/HAR explicit scenario → COMPLETE + LIST_ONLY -------------------

def test_a_har_list_only_scenario(tmp_path):
    outcome = import_har(_har_file(tmp_path, with_jd=False))
    result = outcome.result
    assert result.status == "COMPLETE"
    assert result.metrics.jd_strategy == "LIST_ONLY"
    assert result.jobs and result.jobs[0].full_jd is None


# ---- b. list-only never counts jobs as JD-complete -------------------------

def test_b_list_only_statistics(tmp_path):
    outcome = import_har(_har_file(tmp_path, with_jd=False))
    result = outcome.result
    dc = result.data_completeness
    assert dc.total_jobs == 1
    assert dc.complete_jobs == 0          # not the job total
    assert dc.missing_jd_jobs == 1

    sink = _Sink()
    reporter = ProgressReporter(out=sink, write=sink.write, tty=False)
    reporter.success_summary(result, artifacts=None)
    text = "".join(sink.chunks)
    assert "JD 完整：未批量获取" in text
    assert "JD 完整：1" not in text       # 岗位数 must never pose as JD count
    assert "岗位列表 1（列表完整）" in text
    assert "网站访问限制" in text
    assert "ChatGPT/AI 初筛" in text


# ---- c. normal missing-JD runs are never auto LIST_ONLY --------------------

def test_c_normal_missing_jd_not_list_only():
    job = Job(job_id="1", job_title="岗位", source_url="https://x.test/jobs")
    manual = ManualCurlResult("data", 1, 22, "HIGH", [job], 1, 1, 1,
                              [("1", "DETAIL_JD_NOT_FOUND")],
                              "PARTIAL", "TOTAL_REACHED", 0.1, 1, "DETAIL_REQUIRED")
    unified = manual_result_to_collection_result(manual, "https://x.test/jobs")
    assert unified.metrics.jd_strategy == "DETAIL_REQUIRED"
    assert unified.metrics.jd_strategy != "LIST_ONLY"
    # a plain generic result keeps whatever strategy it carried — no
    # automatic downgrade anywhere in the pipeline
    generic = CollectionResult(source_url="https://x.test/jobs", platform="generic",
                               status="COMPLETE", jobs=[job],
                               total_expected=1, total_fetched=1, total_unique=1)
    assert generic.metrics.jd_strategy == "UNKNOWN"


# ---- d. the explicit list-only choice keeps its legal completion -----------

def test_d_manual_list_only_choice_maps_to_strategy():
    unified = manual_result_to_collection_result(
        _list_only_manual_result(), "https://jobs.test/jobs")
    assert unified.status == "COMPLETE"
    assert unified.metrics.jd_strategy == "LIST_ONLY"
    audit = unified.enrichment["detail_resolution"]
    assert audit["detail_method"] == "LIST_ONLY"


# ---- e. render text states the list-only facts, never fake completeness ----

def test_e_render_result_list_only(tmp_path):
    outcome = import_har(_har_file(tmp_path, with_jd=False))
    text = render_result(outcome.result, "HarImportCollector", tmp_path / "out")
    assert "岗位列表：1（列表完整）" in text
    assert "完整 JD：未批量获取" in text
    assert "网站访问限制" in text
    assert "Complete JD: 1 / 1" not in text


def test_e_render_result_normal_sites_unchanged(tmp_path):
    job = Job(job_id="1", job_title="岗位", source_url="https://x.test",
              full_jd="职责" * 100, responsibilities=["职责"],
              requirements=["要求"])
    result = CollectionResult(source_url="https://x.test/jobs", platform="moka",
                              status="COMPLETE", jobs=[job],
                              total_expected=1, total_fetched=1, total_unique=1,
                              data_completeness=__import__(
                                  "job_extractor.models", fromlist=["DataCompleteness"]
                              ).DataCompleteness(total_jobs=1, complete_jobs=1,
                                                 missing_jd_jobs=0))
    text = render_result(result, "MokaAdapter", tmp_path / "out")
    assert "Complete JD: 1 / 1" in text
    assert "未批量获取" not in text
