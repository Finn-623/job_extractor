"""Browser Assist UX finalization tests — staged flow, clipboard, picker."""
from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from job_extractor import cli
from job_extractor.cli import app
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.manual_curl import PageReplayRejected

runner = CliRunner()

_CURL_INPUT = "\ncurl 'https://x.test'\nEND\n"


def _page_replay_rejected_cli(monkeypatch, tmp_path, input_text, **overrides):
    monkeypatch.setattr(cli, "DISCOVERY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",
                        lambda self, url: DiscoveryResult(source_url=url, status="NOT_FOUND"))
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(cli, "run_manual_curl", lambda *a, **kw: (_ for _ in ()).throw(
        PageReplayRejected(jobs_count=12)))
    monkeypatch.setattr(cli, "_write_fallback_failure", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_copy_to_clipboard", overrides.get("copy", lambda text: True))
    monkeypatch.setattr(cli, "_open_in_browser", overrides.get("open", lambda url: True))
    return runner.invoke(app, ["https://example.com/jobs"], input=input_text)


def test_page_replay_rejected_shows_browser_assist_main_menu(monkeypatch, tmp_path):
    result = _page_replay_rejected_cli(monkeypatch, tmp_path, _CURL_INPUT + "3\n")
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert "浏览器辅助采集" in result.output
    assert "[1] 开始浏览器辅助" in result.output
    assert "[2] 我已经有 HAR" in result.output
    assert "3. 确保第一页请求已被记录" in result.output
    assert "当前 cURL 可以读取岗位（12 条）" in result.output


def test_start_browser_assist_copies_helper(monkeypatch, tmp_path):
    copied = []
    monkeypatch.setattr(cli, "_pagination_helper_source", lambda: "HELPER();")
    result = _page_replay_rejected_cli(monkeypatch, tmp_path, _CURL_INPUT + "1\n3\n",
                                       copy=lambda text: copied.append(text) or True,
                                       open=lambda url: True)
    assert copied == ["HELPER();"]
    assert "✓ 已打开职位列表页面" in result.output
    assert "✓ 自动翻页助手已复制到剪贴板" in result.output
    assert "1. 打开开发者工具 → Network" in result.output
    assert "[2] 重新复制助手" in result.output


def test_browser_open_failure_does_not_block(monkeypatch, tmp_path):
    result = _page_replay_rejected_cli(monkeypatch, tmp_path, _CURL_INPUT + "1\n3\n",
                                       open=lambda url: False)


def test_macos_prefers_google_chrome(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli, "_app_exists", lambda name: True)
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda name: "/usr/bin/" + name)
    real_popen = None
    import subprocess as _sp
    def fake_popen(command, **kwargs):
        opened.append(command)
        return type("P", (), {"poll": lambda self: 0})()
    monkeypatch.setattr(_sp, "Popen", fake_popen)
    assert cli._open_in_browser("https://x.test/jobs") is True
    assert opened == [["open", "-a", "Google Chrome", "https://x.test/jobs"]]


def test_chrome_failure_falls_back_to_default_open(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli, "_app_exists", lambda name: True)
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda name: "/usr/bin/" + name)
    import subprocess as _sp
    calls = {"n": 0}
    def fake_popen(command, **kwargs):
        opened.append(command)
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("Chrome launch failed")
        return type("P", (), {"poll": lambda self: 0})()
    monkeypatch.setattr(_sp, "Popen", fake_popen)
    assert cli._open_in_browser("https://x.test/jobs") is True
    assert opened[0] == ["open", "-a", "Google Chrome", "https://x.test/jobs"]
    assert opened[1] == ["open", "https://x.test/jobs"]


def test_existing_har_choice_uses_picker(monkeypatch, tmp_path):
    from job_extractor.har_importer import HarImportResult
    from job_extractor.models import CollectionMetrics, CollectionResult, Job
    har = tmp_path / "picked.har"
    har.write_text("{}", encoding="utf-8")
    outcome = HarImportResult(result=CollectionResult(
        source_url="https://x.test", platform="har_import",
        metrics=CollectionMetrics(raw_rows=2, unique_jobs=2),
        total_expected=2, total_fetched=2, total_unique=2, status="COMPLETE",
        jobs=[Job(job_id="1", job_title="岗位", source_url="https://x.test")]),
        metrics={"raw_rows": 2, "unique_jobs": 2, "duplicate_jobs": 0,
                 "har_entries": 1, "matched_candidate_responses": 1,
                 "usable_responses": 1, "pages_observed": 1,
                 "failed_responses": 0, "malformed_json_responses": 0})
    monkeypatch.setattr(cli, "generate_and_render_reports", lambda *a, **k: "ok")
    monkeypatch.setattr(cli, "import_har", lambda path: outcome)
    monkeypatch.setattr(cli, "_pick_har_path", lambda: str(har))
    result = _page_replay_rejected_cli(monkeypatch, tmp_path, _CURL_INPUT + "2\n")
    assert result.exit_code == 0
    assert "HAR 导入成功" in result.output and "已恢复：2 raw / 2 unique" in result.output


def test_picker_cancel_returns_to_menu(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_pick_har_path", lambda: None)
    result = _page_replay_rejected_cli(monkeypatch, tmp_path, _CURL_INPUT + "2\n3\n")
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert result.output.count("[1] 开始浏览器辅助") >= 2  # returned to the menu


def test_manual_curl_invalid_upgrade_enters_same_flow(monkeypatch, tmp_path):
    import httpx as _httpx
    monkeypatch.setattr(cli, "DISCOVERY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",
                        lambda self, url: DiscoveryResult(source_url=url, status="NOT_FOUND"))
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(cli, "run_manual_curl", lambda *a, **kw: (_ for _ in ()).throw(
        _httpx.HTTPStatusError(
            "400", request=_httpx.Request("POST", "https://x.test/api/list"),
            response=_httpx.Response(status_code=400,
                                     request=_httpx.Request("POST", "https://x.test/api/list")))))
    monkeypatch.setattr(cli, "_write_fallback_failure", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_copy_to_clipboard", lambda text: True)
    monkeypatch.setattr(cli, "_open_in_browser", lambda url: True)
    monkeypatch.setattr(cli, "_pick_har_path", lambda: None)
    result = runner.invoke(app, ["https://example.com/jobs"], input=(
        _CURL_INPUT + "2\n2\n3\n"))
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert "该请求无法在 Job Extractor 中重放。" in result.output
    assert "浏览器辅助采集" in result.output  # same staged assist flow
    assert result.output.count("[1] 开始浏览器辅助") >= 1
    assert "当前 cURL 可以读取岗位" not in result.output  # no proven-jobs context


def test_invalid_har_extension_is_friendly(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "linux")  # force drag/prompt path
    # user drops a .txt first (invalid), then a proper .har path
    paths = iter(["/tmp/not_a_har.txt\n", "/tmp/real.har\n"])
    monkeypatch.setattr(cli.typer, "get_text_stream",
                        lambda _name: type("S", (), {"readline": staticmethod(lambda: next(paths))})())
    assert cli._pick_har_path() == "/tmp/real.har"


def test_drag_drop_path_normalization(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    paths = iter(["'/tmp/with space.har'\n", "/tmp/esc\\ path.har\n"])
    monkeypatch.setattr(cli.typer, "get_text_stream",
                        lambda _name: type("S", (), {"readline": staticmethod(lambda: next(paths))})())
    assert cli._pick_har_path() == "/tmp/with space.har"
    assert cli._pick_har_path() == "/tmp/esc path.har"
