"""STEP91 tests: formal output layout, exporters, and Git rule."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from job_extractor.models import CollectionResult, Job
from job_extractor.output_layout import (FAILED_ROOT, UNKNOWN, format_stamp,
    formal_company_name, resolve_run_directory, unique_run_dir)
from job_extractor.reporting import ReportManager
from job_extractor.reporting.error_report import export_error_report
from job_extractor.exporters.collection_summary import export_collection_summary
from job_extractor.exporters.csv_exporter import export_jobs_csv

runner = CliRunner()


def job(n: int = 1, company: str = "BYD") -> Job:
    return Job(job_id=str(n), job_title=f"岗位{n}", company=company, source_url="https://job.byd.com/pc",
               locations=["深圳"], department="研发", education="本科", recruitment_type="social",
               publish_date="2026-09-01", responsibilities=["职责一", "职责二"],
               requirements=["要求一"], full_jd="完整JD内容\n第二行")


def result(company="BYD", status="COMPLETE", jobs=None, errors=None) -> CollectionResult:
    return CollectionResult(source_url="https://job.byd.com/pc", platform="generic", company=company,
                            status=status, jobs=jobs if jobs is not None else [job()],
                            total_expected=1, total_fetched=1, total_unique=1,
                            errors=errors or [], warnings=["示例warning"] if status != "COMPLETE" else [])


def test_company_folder_uses_formal_name(tmp_path):
    run = resolve_run_directory(tmp_path, company="BYD", source_url="https://job.byd.com/pc", failed=False)
    assert run.parent == tmp_path / "比亚迪"
    assert formal_company_name("Geely", "https://careers.geelytech.com/campus") == "吉利"
    assert formal_company_name("ZTE中兴", None) == "中兴"


def test_timestamp_folder_and_collision_suffix(tmp_path):
    at = datetime(2026, 9, 19, 12, 0, 0)
    run = resolve_run_directory(tmp_path, company="比亚迪", source_url="https://job.byd.com/pc",
                                failed=False, at=at)
    assert run.name == "2026-09-19_120000"
    run.mkdir(parents=True)
    again = unique_run_dir(run.parent, format_stamp(at))
    assert again.name == "2026-09-19_120000_2"


def test_domain_slug_is_never_a_folder(tmp_path):
    # Host token match wins over a missing/odd company value: job_byd_com host -> 比亚迪.
    run = resolve_run_directory(tmp_path, company=None, source_url="https://job_byd_com.example/x", failed=False)
    assert run.parent == tmp_path / "比亚迪"
    # Truly unknown hosts with no company value fall back to _unknown, never a domain slug.
    run2 = resolve_run_directory(tmp_path, company=None, source_url="https://mystery-site.example/x", failed=False)
    assert run2.parent == tmp_path / UNKNOWN


def test_unknown_company_path(tmp_path):
    run = resolve_run_directory(tmp_path, company=None, source_url="https://mystery.example.com/jobs", failed=False)
    assert run.parent == tmp_path / UNKNOWN


def test_failed_path_and_unknown_failed_path(tmp_path):
    at = datetime(2026, 9, 19, 12, 0, 0)
    failed = resolve_run_directory(tmp_path, company="BYD", source_url="https://job.byd.com/pc",
                                   failed=True, at=at)
    assert failed == tmp_path / FAILED_ROOT / "比亚迪" / "2026-09-19_120000"
    unknown_failed = resolve_run_directory(tmp_path, company=None, source_url="https://x.example.com",
                                           failed=True, at=at)
    assert unknown_failed == tmp_path / FAILED_ROOT / UNKNOWN / "2026-09-19_120000"


def test_report_manager_writes_full_success_set(tmp_path, monkeypatch):
    monkeypatch.setattr("job_extractor.config.OUTPUT_ROOT", tmp_path)
    artifacts = ReportManager().generate_reports(result(), None, run_stamp=datetime(2026, 9, 19, 12, 0, 0))
    run = artifacts.output_directory
    assert run == tmp_path / "比亚迪" / "2026-09-19_120000"
    for name in ("jobs.json", "jobs.csv", "jobs.xlsx", "collection.json", "report.md"):
        assert (run / name).exists(), name
    assert not (run / "error_report.json").exists()
    data = json.loads((run / "jobs.json").read_text(encoding="utf-8"))
    j = data["jobs"][0]
    for key in ("job_id", "job_title", "company", "locations", "department", "education",
                "recruitment_type", "publish_date", "responsibilities", "requirements",
                "full_jd", "detail_url", "apply_url", "source_url"):
        assert key in j, key
    summary = json.loads((run / "collection.json").read_text(encoding="utf-8"))
    assert summary["collection_mode"] in {"provider", "generic", "manual_curl"}
    for key in ("company", "source_url", "started_at", "finished_at", "elapsed_seconds",
                "total_expected", "total_fetched", "total_unique", "duplicate_jobs",
                "details_succeeded", "details_failed", "status", "warnings", "errors"):
        assert key in summary, key


def test_jobs_csv_single_line_cells(tmp_path):
    path = export_jobs_csv(result(jobs=[job(1), job(2)]), tmp_path / "jobs.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["id", "title", "company", "location", "department", "education",
                       "job_type", "publish_date", "responsibilities", "requirements",
                       "detail_url", "apply_url"]
    assert len(rows) == 3
    body = path.read_text(encoding="utf-8-sig")
    # Multi-line JD text flattened to one row per job (visible ⏎ separator, no stray newlines).
    assert "职责一 ⏎ 职责二" in body and len([ln for ln in body.splitlines() if ln.strip()]) == 3


def test_report_md_contains_user_summary(tmp_path, monkeypatch):
    monkeypatch.setattr("job_extractor.config.OUTPUT_ROOT", tmp_path)
    artifacts = ReportManager().generate_reports(result(), None)
    text = artifacts.markdown_path.read_text(encoding="utf-8")
    assert "# Job Collection Report" in text and "- Unique: 1" in text and "- Status: COMPLETE" in text


def test_error_report_only_on_error(tmp_path):
    assert export_error_report(result(errors=[]), tmp_path / "e.json", collection_mode="generic") is None
    assert not (tmp_path / "e.json").exists()
    written = export_error_report(result(status="FAILED", errors=["LIST_HTTP_500 status=500 reason=upstream"]),
                                  tmp_path / "e.json", collection_mode="generic")
    assert written is not None
    report = json.loads(written.read_text(encoding="utf-8"))
    entry = report["errors"][0]
    for key in ("stage", "error_type", "reason", "message", "http_status", "job_id",
                "job_title", "retry_count", "timestamp"):
        assert key in entry, key
    assert entry["error_type"] == "LIST_HTTP_500" and report["company"] == "BYD"


def test_failed_run_writes_only_error_report(tmp_path, monkeypatch):
    monkeypatch.setattr("job_extractor.config.OUTPUT_ROOT", tmp_path)
    failed = result(status="FAILED", jobs=[], errors=["LIST_HTTP_403 status=403 reason=forbidden"])
    artifacts = ReportManager().generate_reports(failed, None, run_stamp=datetime(2026, 9, 19, 12, 0, 0))
    run = artifacts.output_directory
    assert FAILED_ROOT in run.parts
    files = sorted(p.name for p in run.iterdir())
    assert files == ["error_report.json"], files
    report = json.loads((run / "error_report.json").read_text(encoding="utf-8"))
    assert report["company"] == "BYD" and report["status"] == "FAILED"
    assert report["errors"][0]["error_type"] == "LIST_HTTP_403"


def test_output_ignored_by_git():
    from pathlib import Path
    patterns = (Path(".gitignore").read_text(encoding="utf-8")).splitlines()
    assert "output/" in patterns and "__pycache__/" in patterns
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "output"], capture_output=True, text=True,
                             cwd=Path(__file__).parents[1]).stdout.strip()
    assert tracked == ""


def test_cli_routes_through_formal_layout(tmp_path):
    """Live small-sample run (Zhiye adapter) — asserts the formal run folder and files."""
    import job_extractor.cli as cli
    company_dir = Path("output/零跑汽车")
    pre = {p.name for p in company_dir.iterdir()} if company_dir.exists() else set()
    result = runner.invoke(cli.app, ["https://leapmotor1.zhiye.com/campus/jobs"])
    assert result.exit_code == 0
    assert "output/零跑汽车" in result.output
    runs = [p for p in company_dir.iterdir() if p.name not in pre]
    assert runs, "no new run folder created"
    run = max(runs, key=lambda p: p.name)
    for name in ("jobs.json", "jobs.csv", "collection.json", "report.md", "jobs.xlsx"):
        assert (run / name).exists(), name
    assert not (run / "error_report.json").exists()
    # Cleanup the live-test run folder so the real output tree stays clean.
    shutil.rmtree(run)
