"""N7A: local acceptance for the full CLI resume chain (N7A.1–N7A.3).

Covers CLI argument parsing → --resume-checkpoint → resume_manual_checkpoint →
CollectionResult → stage [5/5] → formal exports, using mocked HTTP and a
temporary output root. No real site, no real user output is touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import job_extractor.cli as cli
from job_extractor.cli import app
from job_extractor.checkpoints import CheckpointWriter
from job_extractor.manual_curl import load_jd_checkpoint, resume_manual_checkpoint
from job_extractor.models import Job

CHECKPOINT_URL = "https://resume.test/jobs"
DETAIL_CURL = "curl 'https://resume.test/detail?id=demo'"
REAL_HTTPX_CLIENT = httpx.Client  # captured before any test patch, for test patch chaining
CREDIBLE_JD = ("岗位职责：负责系统设计与交付，推动跨团队协作与质量改进。\n"
               "任职要求：本科及以上，具备工程实践能力与沟通能力。")
DETAIL_BODY = {"data": {"positionInfoList": [
    {"jobDuty": "负责大规模数据系统的设计与交付，推动跨团队协作。",
     "jobRequirements": "本科及以上学历，具备扎实的工程实践能力与沟通能力。"}]}}
FIVE = ["岗位一", "岗位二", "岗位三", "岗位四", "岗位五", "岗位六", "岗位七",
        "岗位八", "岗位九", "岗位十"][:10]


def _job(index: int, jd: str | None) -> Job:
    return Job(job_id=str(index), job_title=FIVE[index], source_url=CHECKPOINT_URL,
               full_jd=jd,
               raw_data={"list": {"id": str(index), "jobName": FIVE[index],
                                  "workPlace": "深圳"}})


def _checkpoint(tmp_path: Path, good: int = 4) -> Path:
    jobs = [_job(i, CREDIBLE_JD if i < good else None) for i in range(10)]
    writer = CheckpointWriter(tmp_path, CHECKPOINT_URL)
    return writer.save(jobs, stage="JD_PARTIAL",
                       task_states={str(i): "SKIPPED" for i in range(good, 10)})


def _patch_output_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("job_extractor.config.OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr("job_extractor.config.OUTPUT_DIR", tmp_path / "cli_output")
    monkeypatch.setattr("job_extractor.config.DISCOVERY_OUTPUT_DIR",
                        tmp_path / "cli_output" / "discovery")


def _patch_http(monkeypatch, handler) -> list[str]:
    requested: list[str] = []
    real_client = REAL_HTTPX_CLIENT
    def tracking_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/detail":
            requested.append(str(request.url.params["id"]))
        return handler(request)
    class _ClientFactory:
        def __call__(self, **kwargs):
            return real_client(transport=httpx.MockTransport(tracking_handler), timeout=30,
                               follow_redirects=True)
    monkeypatch.setattr("job_extractor.manual_curl.httpx.Client", _ClientFactory())
    return requested


def _success_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=DETAIL_BODY)


def _run_cli(monkeypatch, tmp_path: Path, checkpoint: Path, handler,
             *extra: str):
    _patch_output_root(monkeypatch, tmp_path)
    _patch_http(monkeypatch, handler)
    return CliRunner().invoke(app, ["--resume-checkpoint", str(checkpoint),
                                    "--detail-curl", DETAIL_CURL, *extra])


def _exported_files(output_root: Path) -> set[str]:
    run_dirs = [d for d in output_root.rglob("*") if d.is_dir()
                and "_checkpoints" not in d.parts and d.name != "_checkpoints"]
    names: set[str] = set()
    for d in run_dirs:
        names |= {p.name for p in d.iterdir() if p.is_file()}
    return names


def _newest_checkpoint(output_root: Path) -> Path:
    """The resume writes its own checkpoint under <root>/_checkpoints/<run>/."""
    return max((output_root / "_checkpoints").glob("*/jd_checkpoint.json"),
               key=lambda p: p.stat().st_mtime)


# -- N7A.1: full CLI chain ----------------------------------------------------

def test_n7a1_cli_resume_end_to_end(monkeypatch, tmp_path):
    """CLI parse → resume → 6 targeted requests → 10 jobs → formal exports."""
    checkpoint = _checkpoint(tmp_path)
    requested = []
    real_client = REAL_HTTPX_CLIENT
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/detail":
            requested.append(str(request.url.params["id"]))
        return httpx.Response(200, json=DETAIL_BODY)
    _patch_output_root(monkeypatch, tmp_path)
    class _Factory:
        def __call__(self, **kwargs):
            return real_client(transport=httpx.MockTransport(handler), timeout=30,
                               follow_redirects=True)
    monkeypatch.setattr("job_extractor.manual_curl.httpx.Client", _Factory())
    before = checkpoint.read_text(encoding="utf-8")
    result = CliRunner().invoke(app, ["--resume-checkpoint", str(checkpoint),
                                      "--detail-curl", DETAIL_CURL])
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert sorted(requested) == [str(i) for i in range(4, 10)]  # only the 6 targets
    assert "完成 ✓" in result.output
    files = _exported_files(tmp_path)
    assert {"jobs.json", "jobs.xlsx", "jobs.csv", "collection.json", "report.md"} <= files
    jobs_payload = [p for p in tmp_path.rglob("jobs.json")
                    if "_checkpoints" not in p.parts][0]
    exported = json.loads(jobs_payload.read_text(encoding="utf-8"))
    assert len(exported["jobs"]) == 10 and all(job["full_jd"] for job in exported["jobs"])
    summary = [p for p in tmp_path.rglob("collection.json")
               if "_checkpoints" not in p.parts][0]
    stats = json.loads(summary.read_text(encoding="utf-8"))["resume_stats"]
    assert stats == {"preexisting": 4, "requested": 6, "new_success": 6,
                     "failed": 0, "skipped": 0, "still_missing": 0}
    assert checkpoint.read_text(encoding="utf-8") == before  # original untouched
    new_checkpoints = list((tmp_path / "_checkpoints").glob("*/*.json"))
    assert len(new_checkpoints) == 2  # original + this run's
    fresh = json.loads(_newest_checkpoint(tmp_path).read_text(encoding="utf-8"))
    assert fresh["stage"] == "JD_RESUME_COMPLETE" and len(fresh["jobs"]) == 10


# -- N7A.2 Cases A–E ----------------------------------------------------------

def test_case_a_resume_success_keeps_original_and_completes(monkeypatch, tmp_path):
    checkpoint = _checkpoint(tmp_path)
    original = json.loads(checkpoint.read_text(encoding="utf-8"))
    _run_cli(monkeypatch, tmp_path, checkpoint, _success_handler)
    jobs, states, _ = load_jd_checkpoint(_newest_checkpoint(tmp_path))
    assert len(jobs) == 10 and all(job.full_jd for job in jobs) and not states
    for index in range(4):  # original credible JDs byte-identical
        assert jobs[index].full_jd == original["jobs"][index]["full_jd"]


def test_case_b_resume_failure_preserves_originals_and_marks_states(monkeypatch, tmp_path):
    checkpoint = _checkpoint(tmp_path)
    original = json.loads(checkpoint.read_text(encoding="utf-8"))
    result = _run_cli(monkeypatch, tmp_path, checkpoint,
                      lambda request: httpx.Response(500))
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert "完成 ✓" in result.output  # INCOMPLETE runs still finish with a formal report
    jobs, states, _ = load_jd_checkpoint(_newest_checkpoint(tmp_path))
    assert len(jobs) == 10
    for index in range(4):
        assert jobs[index].full_jd == original["jobs"][index]["full_jd"]
    # complete jobs carry no state; only the still-missing ones do
    assert set(states) == {str(i) for i in range(4, 10)}
    assert all(states.get(str(i)) == "FAILED" for i in range(4, 10))
    files = _exported_files(tmp_path)
    assert {"jobs.json", "jobs.xlsx", "jobs.csv", "collection.json", "report.md"} <= files
    summary = [p for p in tmp_path.rglob("collection.json")
               if "_checkpoints" not in p.parts][0]
    stats = json.loads(summary.read_text(encoding="utf-8"))["resume_stats"]
    assert stats == {"preexisting": 4, "requested": 6, "new_success": 0,
                     "failed": 6, "skipped": 0, "still_missing": 6}


def test_case_c_resume_skip_after_budget_exhaustion(monkeypatch, tmp_path):
    checkpoint = _checkpoint(tmp_path)
    requested = []
    real_client = REAL_HTTPX_CLIENT
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/detail":
            requested.append(str(request.url.params["id"]))
        return httpx.Response(200, json=DETAIL_BODY)
    _patch_output_root(monkeypatch, tmp_path)
    class _Factory:
        def __call__(self, **kwargs):
            return real_client(transport=httpx.MockTransport(handler), timeout=30,
                               follow_redirects=True)
    monkeypatch.setattr("job_extractor.manual_curl.httpx.Client", _Factory())
    result = CliRunner().invoke(app, ["--resume-checkpoint", str(checkpoint),
                                      "--detail-curl", DETAIL_CURL,
                                      "--jd-time-budget", "0"])
    assert result.exit_code == 0 and requested == []  # zero budget: nothing sent
    jobs, states, _ = load_jd_checkpoint(_newest_checkpoint(tmp_path))
    assert len(jobs) == 10 and sum(bool(job.full_jd) for job in jobs) == 4
    assert all(states.get(str(i)) == "SKIPPED" for i in range(4, 10))
    checkpoints = list((tmp_path / "_checkpoints").glob("*/*.json"))
    assert len(checkpoints) == 2  # a new checkpoint was written
    fresh = json.loads(_newest_checkpoint(tmp_path).read_text(encoding="utf-8"))
    assert fresh["stage"] == "JD_RESUME_PARTIAL"
    summary = [p for p in tmp_path.rglob("collection.json")
               if "_checkpoints" not in p.parts][0]
    stats = json.loads(summary.read_text(encoding="utf-8"))["resume_stats"]
    assert stats == {"preexisting": 4, "requested": 6, "new_success": 0,
                     "failed": 0, "skipped": 6, "still_missing": 6}


def test_case_d_two_consecutive_resumes_never_regrow_or_recover_twice(monkeypatch, tmp_path):
    checkpoint = _checkpoint(tmp_path)
    original = json.loads(checkpoint.read_text(encoding="utf-8"))
    def partial_handler(request: httpx.Request) -> httpx.Response:
        identifier = int(request.url.params["id"])
        if identifier <= 6:
            return httpx.Response(200, json=DETAIL_BODY)
        return httpx.Response(500)
    _run_cli(monkeypatch, tmp_path, checkpoint, partial_handler)
    resumed_once = load_jd_checkpoint(_newest_checkpoint(tmp_path))
    assert sum(bool(job.full_jd) for job in resumed_once[0]) == 7
    requested = _patch_http(monkeypatch, _success_handler)
    result = CliRunner().invoke(app, ["--resume-checkpoint",
                                      str(_newest_checkpoint(tmp_path)),
                                      "--detail-curl", DETAIL_CURL])
    assert result.exit_code == 0
    assert sorted(requested) == ["7", "8", "9"]  # only the still-missing jobs
    jobs, states, _ = load_jd_checkpoint(_newest_checkpoint(tmp_path))
    assert len(jobs) == 10 and all(job.full_jd for job in jobs) and not states
    for index in range(4):  # original successes are never overwritten
        assert jobs[index].full_jd == original["jobs"][index]["full_jd"]


def test_case_e_invalid_checkpoint_never_creates_a_new_checkpoint(monkeypatch, tmp_path):
    _patch_output_root(monkeypatch, tmp_path)
    _patch_http(monkeypatch, _success_handler)
    bad = tmp_path / "bad_checkpoint.json"
    bad.write_text("{not-json", encoding="utf-8")
    result = CliRunner().invoke(app, ["--resume-checkpoint", str(bad),
                                      "--detail-curl", DETAIL_CURL])
    assert result.exit_code != 0 and "CHECKPOINT_INVALID" in result.output
    assert not list((tmp_path / "_checkpoints").glob("*")) if (tmp_path / "_checkpoints").exists() else True


def test_case_e_invalid_format_checkpoint_rejected(monkeypatch, tmp_path):
    _patch_output_root(monkeypatch, tmp_path)
    _patch_http(monkeypatch, _success_handler)
    bad = tmp_path / "wrong_format.json"
    bad.write_text(json.dumps({"format": "other-v9", "jobs": []}), encoding="utf-8")
    result = CliRunner().invoke(app, ["--resume-checkpoint", str(bad),
                                      "--detail-curl", DETAIL_CURL])
    assert result.exit_code != 0 and "CHECKPOINT_UNSUPPORTED" in result.output


def test_case_e_checkpoint_write_failure_keeps_history_intact(monkeypatch, tmp_path):
    checkpoint = _checkpoint(tmp_path)
    before = checkpoint.read_text(encoding="utf-8")
    _patch_output_root(monkeypatch, tmp_path)
    _patch_http(monkeypatch, _success_handler)
    def broken_save(self, *args, **kwargs):
        raise OSError("disk failure")
    monkeypatch.setattr("job_extractor.checkpoints.CheckpointWriter.save", broken_save)
    with pytest.raises(OSError):
        resume_manual_checkpoint(checkpoint, DETAIL_CURL, checkpoint_root=tmp_path)
    assert checkpoint.read_text(encoding="utf-8") == before
    # the failed save never left a partial temp file behind
    assert not [p for p in checkpoint.parent.glob(".jd_checkpoint_*")]


def test_case_e_export_failure_never_fakes_success(monkeypatch, tmp_path):
    checkpoint = _checkpoint(tmp_path)
    before = checkpoint.read_text(encoding="utf-8")
    _patch_output_root(monkeypatch, tmp_path)
    _patch_http(monkeypatch, _success_handler)
    def broken_export(*args, **kwargs):
        raise OSError("export disk failure")
    monkeypatch.setattr(cli, "generate_and_render_reports", broken_export)
    result = CliRunner().invoke(app, ["--resume-checkpoint", str(checkpoint),
                                      "--detail-curl", DETAIL_CURL])
    assert result.exit_code != 0 and "完成 ✓" not in result.output
    assert checkpoint.read_text(encoding="utf-8") == before


# -- N7A.3: statistics invariants ---------------------------------------------

def test_n7a3_resume_stats_reconcile(monkeypatch, tmp_path):
    """preexisting + requested == total; requested == new + failed + skipped;
    original successes are never counted as this-run additions."""
    jobs = [_job(i, CREDIBLE_JD if i < 4 else None) for i in range(10)]
    writer = CheckpointWriter(tmp_path, CHECKPOINT_URL)
    source = writer.save(jobs, stage="JD_PARTIAL",
                         task_states={str(i): "PENDING" for i in range(4, 10)})
    def mixed(request: httpx.Request) -> httpx.Response:
        identifier = int(request.url.params["id"])
        if identifier == 9:
            return httpx.Response(500)
        return httpx.Response(200, json=DETAIL_BODY)
    real_client = REAL_HTTPX_CLIENT
    class _Factory:
        def __call__(self, **kwargs):
            return real_client(transport=httpx.MockTransport(mixed), timeout=30,
                               follow_redirects=True)
    monkeypatch.setattr("job_extractor.manual_curl.httpx.Client", _Factory())
    result = resume_manual_checkpoint(source, DETAIL_CURL, checkpoint_root=tmp_path)
    stats = result.resume_stats
    assert stats["preexisting"] == 4 and stats["requested"] == 6
    assert stats["new_success"] == 5 and stats["failed"] == 1 and stats["skipped"] == 0
    assert stats["still_missing"] == 1
    assert stats["requested"] == stats["new_success"] + stats["failed"] + stats["skipped"]
    assert stats["preexisting"] + stats["requested"] == len(result.jobs)
    from job_extractor.manual_curl import manual_result_to_collection_result
    unified = manual_result_to_collection_result(result, CHECKPOINT_URL)
    assert unified.metrics.details_succeeded == 9  # 4 original + 5 new, never double-counted
    assert unified.metrics.details_attempted == 6  # only this-run requests
    assert unified.enrichment["resume_stats"] == stats
