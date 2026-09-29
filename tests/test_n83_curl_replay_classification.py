"""N8.3 hotfix tests — initial-replay HTTP error classification vs
pagination-replay rejection. The single root-cause decision point: which
request got the HTTP error — the initial cURL request or a later
render_page replay."""
from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from job_extractor import cli
from job_extractor.cli import app
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.manual_curl import PageReplayRejected, parse_curl, run_manual_curl

runner = CliRunner()

GOOD_PAYLOAD = {"success": True, "errorCode": 0, "result": {"total": 4, "list": [
    {"code": code, "jobName": f"岗位{code}", "workPlace": "上海", "postType": "研发"}
    for code in ("A", "B", "C")]}}


class _FakeClient:
    """Records request sequence; rejects page-2 replay with HTTP 400."""
    def __init__(self, *, fail_on_page: int):
        self.fail_on_page = fail_on_page
        self.requested = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.requested.append(url)
        page = kwargs["json"]["page"]
        if page >= self.fail_on_page:
            response = httpx.Response(status_code=400,
                                      request=httpx.Request(method, url))
            raise httpx.HTTPStatusError(
                "Client error '400'", request=httpx.Request(method, url),
                response=response)
        return httpx.Response(status_code=200, request=httpx.Request(method, url),
                              text=json.dumps(GOOD_PAYLOAD))

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        return False


def _list_curl() -> str:
    body = json.dumps({"page": 1, "pageSize": 50})
    return ("curl 'https://careers.example.com/api/recruit/position/list' "
            f"-X POST -H 'Content-Type: application/json' --data-raw '{body}'")


def test_pagination_replay_400_raises_page_replay_rejected():
    """Page 1 reads fine (jobs>=1); the page-2 replay 400s -> the caller
    must get PageReplayRejected, never a plain HTTP error."""
    client = _FakeClient(fail_on_page=2)
    try:
        try:
            run_manual_curl(_list_curl(), None, max_jobs=None, client=client)
        except PageReplayRejected as exc:
            assert exc.jobs_count == 3
            return
        raise AssertionError("PageReplayRejected was not raised for a page-2 replay 400")
    finally:
        client.close()


def test_initial_request_400_is_not_page_replay_rejected():
    """An HTTP 400 on the INITIAL pasted-cURL request never reaches
    pagination and must NOT be classified as PageReplayRejected — there is
    no 'current page valid' proof (jobs>=1 is impossible)."""
    client = _FakeClient(fail_on_page=1)
    try:
        try:
            run_manual_curl(_list_curl(), None, max_jobs=None, client=client)
        except PageReplayRejected:
            raise AssertionError("initial-request 400 must not become PageReplayRejected")
        except (httpx.HTTPStatusError, ValueError):
            return
        raise AssertionError("initial-request 400 must not silently succeed")
    finally:
        client.close()


def test_cli_initial_http_400_is_manual_curl_invalid_not_har(monkeypatch, tmp_path):
    """Acceptance: initial cURL itself 400 -> MANUAL_CURL_INVALID friendly
    render, no HAR/browser-assist escalation, no traceback, clean exit."""
    monkeypatch.setattr(cli, "DISCOVERY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",
                        lambda self, url: DiscoveryResult(source_url=url, status="NOT_FOUND"))
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)

    def reject_immediately(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "Client error '400 Bad Request'",
            request=httpx.Request("POST", "https://careers.example.com/api/recruit/position/list"),
            response=httpx.Response(status_code=400, request=httpx.Request(
                "POST", "https://careers.example.com/api/recruit/position/list")))
    monkeypatch.setattr(cli, "run_manual_curl", reject_immediately)
    monkeypatch.setattr(cli, "_write_fallback_failure", lambda *a, **k: None)

    result = CliRunner().invoke(app, ["https://example.com/jobs"], input=(
        "\ncurl 'https://careers.example.com/api/recruit/position/list'\nEND\n3\n"))
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert "无法从这条 cURL 识别有效岗位数据。" in result.output
    assert "该请求无法在 Job Extractor 中重放。" in result.output
    assert "[1] 重新粘贴 cURL" in result.output
    assert "[2] 使用浏览器辅助采集" in result.output
    assert "[3] 退出" in result.output
    assert "请输入 HAR 文件路径" not in result.output  # exit chosen: no HAR prompt


def test_cli_initial_http_400_retry_reprompts_curl(monkeypatch, tmp_path):
    """Choice [1] re-prompts the cURL paste and runs the new request."""
    monkeypatch.setattr(cli, "DISCOVERY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",
                        lambda self, url: DiscoveryResult(source_url=url, status="NOT_FOUND"))
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    attempts = []

    def reject_first_then_succeed(list_curl, detail_curl, **kwargs):
        attempts.append(list_curl)
        if len(attempts) == 1:
            raise httpx.HTTPStatusError(
                "Client error '400 Bad Request'",
                request=httpx.Request("POST", "https://careers.example.com/api/recruit/position/list"),
                response=httpx.Response(status_code=400, request=httpx.Request(
                    "POST", "https://careers.example.com/api/recruit/position/list")))
        from job_extractor.manual_curl import ManualCurlResult
        from job_extractor.models import Job
        job = Job(job_id="1", job_title="岗位", source_url="https://x.test",
                  full_jd="职责\n要求", responsibilities=["职责"],
                  requirements=["要求"])
        return ManualCurlResult("data", 1, 22, "HIGH", [job], 0, 1, 1, [],
                                "COMPLETE", "TOTAL_REACHED", 0.1, 1)
    monkeypatch.setattr(cli, "run_manual_curl", reject_first_then_succeed)
    monkeypatch.setattr(cli, "_write_fallback_failure", lambda *a, **k: None)
    monkeypatch.setattr(cli, "generate_and_render_reports", lambda *a, **k: "ok")

    result = CliRunner().invoke(app, ["https://example.com/jobs"], input=(
        "\ncurl 'https://careers.example.com/api/recruit/position/list'\nEND\n1\n"
        "curl 'https://careers.example.com/api/recruit/position/list'\nEND\n"))
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert len(attempts) == 2
    assert "完成 ✓" in result.output or "岗位数：1" in result.output


def test_cli_initial_http_400_browser_assist_enters_har(monkeypatch, tmp_path):
    """Choice [2] enters the existing HAR import flow (user-initiated)."""
    from job_extractor.har_importer import HarImportResult
    from job_extractor.models import CollectionMetrics, CollectionResult, Job
    monkeypatch.setattr(cli, "DISCOVERY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",
                        lambda self, url: DiscoveryResult(source_url=url, status="NOT_FOUND"))
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(cli, "run_manual_curl", lambda *a, **kw: (_ for _ in ()).throw(
        httpx.HTTPStatusError(
            "Client error '400 Bad Request'",
            request=httpx.Request("POST", "https://careers.example.com/api/recruit/position/list"),
            response=httpx.Response(status_code=400, request=httpx.Request(
                "POST", "https://careers.example.com/api/recruit/position/list")))))
    monkeypatch.setattr(cli, "generate_and_render_reports", lambda *a, **k: "ok")
    metrics = CollectionMetrics(pages_requested=1, pages_succeeded=1,
                                raw_rows=2, unique_jobs=2)
    outcome = HarImportResult(result=CollectionResult(
        source_url="https://x.test", platform="har_import", metrics=metrics,
        total_expected=2, total_fetched=2, total_unique=2, status="COMPLETE",
        jobs=[Job(job_id="1", job_title="岗位", source_url="https://x.test")]),
        metrics={"raw_rows": 2, "unique_jobs": 2, "duplicate_jobs": 0,
                 "har_entries": 1, "matched_candidate_responses": 1,
                 "usable_responses": 1, "pages_observed": 1,
                 "failed_responses": 0, "malformed_json_responses": 0})
    monkeypatch.setattr(cli, "import_har", lambda path: outcome)
    har = tmp_path / "fresh.har"
    har.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(app, ["https://example.com/jobs"], input=(
        "\ncurl 'https://careers.example.com/api/recruit/position/list'\nEND\n2\n1\n"
        + str(har) + "\n"))
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert "HAR 导入成功" in result.output
    assert "已恢复：2 raw / 2 unique" in result.output


def test_pagination_replay_rejected_cli_still_offers_har(monkeypatch, tmp_path):
    """Regression guard: page-1 success + page-2 rejection must keep the
    N6.2 browser-assist path intact."""
    from job_extractor.manual_curl import PageReplayRejected
    monkeypatch.setattr(cli, "DISCOVERY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",
                        lambda self, url: DiscoveryResult(source_url=url, status="NOT_FOUND"))
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(cli, "run_manual_curl", lambda *a, **kw: (_ for _ in ()).throw(
        PageReplayRejected(jobs_count=12)))
    monkeypatch.setattr(cli, "_write_fallback_failure", lambda *a, **k: None)
    har = tmp_path / "x.har"
    har.write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(app, ["https://example.com/jobs"], input=(
        "\ncurl 'https://x.test'\nEND\n1\n" + str(har) + "\n"))
    assert "请输入 HAR 文件路径" in result.output
    assert "HAR 中未找到可用的岗位数据。" in result.output  # {} HAR: no usable response
