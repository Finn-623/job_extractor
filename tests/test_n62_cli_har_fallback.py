"""N6.2 CLI HAR fallback tests — auto -> cURL -> browser-assisted HAR."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from job_extractor import cli
from job_extractor.cli import app

runner = CliRunner()


def _discover_not_found(monkeypatch, tmp_path) -> None:
    from job_extractor.discovery.models import DiscoveryResult
    monkeypatch.setattr(cli, "DISCOVERY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",
                        lambda self, url: DiscoveryResult(source_url=url,
                                                          status="NOT_FOUND"))


def _interactive(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)


def _write_har(tmp_path, *, codes=("A", "B"), page=1) -> str:
    body = {"success": True, "errorCode": 0, "result": {"total": 4, "list": [
        {"code": code, "jobName": f"Job {code}", "workPlace": "Shanghai"}
        for code in codes]}, "page": page, "pageSize": len(codes)}
    entries = [{"request": {"method": "POST",
                            "url": "https://x.test/api/recruit/position/list",
                            "postData": {"text": json.dumps(
                                {"page": page, "pageSize": len(codes)})}},
                "response": {"status": 200,
                             "content": {"text": json.dumps(body)}}}]
    file = tmp_path / "case.har"
    file.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    return str(file)


def _replay_rejected_stub(monkeypatch, jobs_count: int) -> None:
    from job_extractor.manual_curl import PageReplayRejected

    def raise_rejected(*a, **kw):
        raise PageReplayRejected(jobs_count=jobs_count)
    monkeypatch.setattr(cli, "run_manual_curl", raise_rejected)


def _stub_har_outcome(raw: int = 2, unique: int = 2):
    from job_extractor.har_importer import HarImportResult
    from job_extractor.job_normalize import unified_company as _u
    from job_extractor.models import CollectionMetrics, CollectionResult, Job
    metrics = CollectionMetrics(pages_requested=1, pages_succeeded=1,
                                raw_rows=raw, unique_jobs=unique)
    result = CollectionResult(
        source_url="https://x.test/api/recruit/position/list",
        platform="har_import", metrics=metrics, total_expected=4,
        total_fetched=raw, total_unique=unique, status="COMPLETE",
        jobs=[Job(job_id=str(n), job_title=f"岗位{n}",
                  source_url="https://x.test",
                  raw_data={"list": {"code": str(n)}}) for n in range(1, unique + 1)])
    outcome = HarImportResult(result=result,
                              metrics={"har_entries": 1,
                                       "matched_candidate_responses": 1,
                                       "usable_responses": 1,
                                       "pages_observed": 1,
                                       "failed_responses": 0,
                                       "malformed_json_responses": 0,
                                       "raw_rows": raw, "unique_jobs": unique,
                                       "duplicate_jobs": raw - unique})
    return outcome


_CURL_INPUT = "\ncurl 'https://x.test/list'\nEND\n"


def test_auto_failure_non_tty_never_prompts_any_fallback(monkeypatch, tmp_path):
    _discover_not_found(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_write_fallback_failure",
                        lambda *_a, **_k: None)
    result = runner.invoke(app, ["https://example.com/jobs"])
    assert result.exit_code == 0
    assert "是否使用 cURL 兜底" not in result.output
    assert "浏览器辅助采集" not in result.output


def test_auto_fail_curl_success_no_har_prompt(monkeypatch, tmp_path):
    from job_extractor.manual_curl import ManualCurlResult
    from job_extractor.models import Job
    _discover_not_found(monkeypatch, tmp_path)
    _interactive(monkeypatch)
    job = Job(job_id="1", job_title="岗位", source_url="https://x.test",
              full_jd="职责\n要求", responsibilities=["职责"],
              requirements=["要求"])
    manual = ManualCurlResult("data", 1, 22, "HIGH", [job], 0, 1, 1, [],
                              "COMPLETE", "TOTAL_REACHED", 0.1, 1)
    monkeypatch.setattr(cli, "run_manual_curl",
                        lambda list_curl, detail_curl, **kw: manual)
    monkeypatch.setattr(cli, "generate_and_render_reports",
                        lambda *_a, **_k: "ok")
    result = runner.invoke(app, ["https://example.com/jobs"],
                           input=_CURL_INPUT)
    assert result.exit_code == 0
    assert "浏览器辅助采集" not in result.output
    assert "请输入 HAR 文件路径" not in result.output


def test_page_replay_rejected_offers_har_and_succeeds(monkeypatch, tmp_path):
    _discover_not_found(monkeypatch, tmp_path)
    _interactive(monkeypatch)
    _replay_rejected_stub(monkeypatch, jobs_count=12)
    monkeypatch.setattr(cli, "generate_and_render_reports",
                        lambda *_a, **_k: "ok")
    outcomes = {}
    def record_path(path):
        outcomes["path"] = path
        return _stub_har_outcome()
    monkeypatch.setattr(cli, "import_har", record_path)
    har = _write_har(tmp_path)
    monkeypatch.setattr(cli, "_pick_har_path", lambda: har)
    result = runner.invoke(app, ["https://example.com/jobs"],
                           input=_CURL_INPUT + "2\n")
    assert result.exit_code == 0
    assert "无法安全使用同一请求继续翻页" in result.output
    assert "HAR 导入成功" in result.output
    assert "已恢复：2 raw / 2 unique" in result.output
    assert str(outcomes["path"]) == har
    assert "[1/5]" in result.output and "[5/5]" in result.output


def test_page_replay_rejected_user_exits_cleanly(monkeypatch, tmp_path):
    _discover_not_found(monkeypatch, tmp_path)
    _interactive(monkeypatch)
    _replay_rejected_stub(monkeypatch, jobs_count=12)
    monkeypatch.setattr(cli, "_write_fallback_failure",
                        lambda *_a, **_k: None)
    result = runner.invoke(app, ["https://example.com/jobs"],
                           input=_CURL_INPUT + "3\n")
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert "已结束。" in result.output
    assert "请输入 HAR 文件路径" not in result.output


def test_har_invalid_file_friendly_error(monkeypatch, tmp_path):
    _discover_not_found(monkeypatch, tmp_path)
    _interactive(monkeypatch)
    _replay_rejected_stub(monkeypatch, jobs_count=3)
    monkeypatch.setattr(cli, "_write_fallback_failure",
                        lambda *_a, **_k: None)
    bad = tmp_path / "bad.har"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(cli, "_pick_har_path", lambda: str(bad))
    result = runner.invoke(app, ["https://example.com/jobs"],
                           input=_CURL_INPUT + "2\n")
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert "HAR 导入失败：文件无法解析。" in result.output


def test_har_missing_path_friendly_error(monkeypatch, tmp_path):
    _discover_not_found(monkeypatch, tmp_path)
    _interactive(monkeypatch)
    _replay_rejected_stub(monkeypatch, jobs_count=3)
    monkeypatch.setattr(cli, "_write_fallback_failure",
                        lambda *_a, **_k: None)
    missing = tmp_path / "missing.har"
    monkeypatch.setattr(cli, "_pick_har_path", lambda: str(missing))
    result = runner.invoke(app, ["https://example.com/jobs"],
                           input=_CURL_INPUT + "2\n")
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert "HAR 文件不存在" in result.output


def test_har_no_jobs_friendly_error(monkeypatch, tmp_path):
    _discover_not_found(monkeypatch, tmp_path)
    _interactive(monkeypatch)
    _replay_rejected_stub(monkeypatch, jobs_count=3)
    monkeypatch.setattr(cli, "_write_fallback_failure",
                        lambda *_a, **_k: None)
    outcome = _stub_har_outcome()
    outcome.result.jobs = []
    monkeypatch.setattr(cli, "import_har", lambda path: outcome)
    har = _write_har(tmp_path)
    monkeypatch.setattr(cli, "_pick_har_path", lambda: har)
    result = runner.invoke(app, ["https://example.com/jobs"],
                           input=_CURL_INPUT + "2\n")
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert "HAR 中未找到可用的岗位数据。" in result.output


def test_no_sensitive_material_in_fallback_output(monkeypatch, tmp_path):
    _discover_not_found(monkeypatch, tmp_path)
    _interactive(monkeypatch)
    _replay_rejected_stub(monkeypatch, jobs_count=3)
    monkeypatch.setattr(cli, "generate_and_render_reports",
                        lambda *_a, **_k: "ok")
    har = _write_har(tmp_path)
    monkeypatch.setattr(cli, "_pick_har_path", lambda: har)
    result = runner.invoke(app, ["https://example.com/jobs"],
                           input=_CURL_INPUT + "2\n")
    assert result.exit_code == 0
    for token in ("anti_content", "verifyAuthToken", "Authorization",
                  "Cookie", "postData"):
        assert token not in result.output
