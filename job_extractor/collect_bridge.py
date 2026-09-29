"""N8.1: collect bridge — formal pipeline behind the dashboard collect API.

Thin transport only: every mode calls the SAME formal functions the CLI
uses (collect_url / run_manual_curl + manual_result_to_collection_result /
import_har) and exports through the formal ReportManager. No collector,
parser, importer, dedupe, JD or exporter logic is duplicated here.

Security: responses contain summary counts and artifact paths only — never
cookies, anti_content, authorization, request headers, full postData or
full cURL text. Errors are code-based with a sanitized message.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_FALLBACK_MESSAGES = {
    "AUTO_FAILED": "自动采集未完成，需要备用采集方式。",
    "MANUAL_CURL_INVALID": "无法从这条 cURL 识别有效岗位数据。",
    "CURL_PAGINATION_NOT_REPLAYABLE": "当前 cURL 可以读取岗位，但该网站的分页请求包含动态验证信息，无法安全使用同一个请求继续翻页。",
    "HAR_INVALID": "浏览器采集结果无法读取。",
    "HAR_NO_JOBS": "浏览器采集结果中没有找到岗位数据。",
}


def _message(code: str, detail: str = "") -> str:
    base = _FALLBACK_MESSAGES.get(code, "采集失败")
    return f"{base}" if not detail else f"{base}（{detail}）"


def _sanitize(detail: Any) -> str:
    text = str(detail or "").strip()
    return text[:200]


def _result_payload(result, artifacts) -> dict:
    metrics = result.metrics
    completeness = result.data_completeness
    return {
        "source": result.platform,
        "raw_jobs": result.total_fetched,
        "unique_jobs": result.total_unique,
        "duplicate_jobs": metrics.duplicate_jobs,
        "jd_complete": completeness.complete_jobs,
        "total_expected": result.total_expected,
        "output_dir": str(artifacts.output_directory),
        "artifacts": sorted(
            path.name for path in artifacts.output_directory.iterdir()
            if path.is_file()),
    }


def _finish(result, ok_code: str, fail_code: str) -> dict:
    if result is None or not result.jobs or result.status == "FAILED":
        errors = list(getattr(result, "errors", None) or []) if result else []
        detail = _sanitize(errors[0]) if errors else "NO_JOBS"
        return {"ok": False, "code": fail_code, "message": _message(fail_code, detail)}
    from job_extractor.reporting.manager import ReportManager
    artifacts = ReportManager().generate_reports(
        result, None, collection_mode=result.platform or "generic")
    return {"ok": True, "code": ok_code, "result": _result_payload(result, artifacts)}


def run_auto(url: str) -> dict:
    from job_extractor.adapters import default_registry
    from job_extractor.runtime import collect_url
    adapter_class = default_registry.detect(url)
    if adapter_class.platform_name != "generic":
        _, result = collect_url(url, adapter_class)
        return _finish(result, "AUTO_SUCCESS", "AUTO_FAILED")
    from job_extractor.planning import CollectionPlanValidator
    generic = adapter_class()
    try:
        discovery = generic.discover(url)
        plan = generic.build_plan(discovery)
        validation = CollectionPlanValidator().validate(plan)
    except Exception as exc:  # discovery must never traceback the API caller
        return {"ok": False, "code": "AUTO_FAILED",
                "message": _message("AUTO_FAILED", _sanitize(type(exc).__name__))}
    blocked_warning = any(token in " ".join(discovery.warnings).upper()
                          for token in ("CAPTCHA", "HUMAN_VERIFICATION", "LOGIN",
                                        "CHALLENGE", "AUTHENTICATION"))
    candidate = discovery.probable_list_api
    positive = bool(candidate and (candidate.observed_list_length or 0) > 0
                    and (candidate.observed_unique_ids or 0) > 0)
    auto_executable = (discovery.status == "DISCOVERED" and validation.valid
                       and plan.executable and plan.confidence == "HIGH"
                       and positive and not blocked_warning
                       and not any("SENSITIVE" in e.upper() for e in validation.errors))
    if not auto_executable:
        return {"ok": False, "code": "AUTO_FAILED",
                "message": _message("AUTO_FAILED", "AUTO_FALLBACK_REQUIRED")}
    try:
        result = generic.execute_plan(plan)
    except Exception as exc:
        return {"ok": False, "code": "AUTO_FAILED",
                "message": _message("AUTO_FAILED", _sanitize(type(exc).__name__))}
    return _finish(result, "AUTO_SUCCESS", "AUTO_FAILED")


def run_curl(curl: str) -> dict:
    from job_extractor.manual_curl import (
        ManualCurlResponseError,
        PageReplayRejected,
        manual_result_to_collection_result,
        parse_curl,
        run_manual_curl,
    )
    try:
        spec = parse_curl(curl)
    except Exception:
        return {"ok": False, "code": "MANUAL_CURL_INVALID",
                "message": _message("MANUAL_CURL_INVALID", "PARSE_FAILED")}
    try:
        manual = run_manual_curl(curl, None, max_jobs=None)
    except PageReplayRejected as exc:
        return {"ok": False, "code": "CURL_PAGINATION_NOT_REPLAYABLE",
                "message": _message("CURL_PAGINATION_NOT_REPLAYABLE"),
                "partial": {"jobs": exc.jobs_count}}
    except (ManualCurlResponseError, ValueError, Exception) as exc:  # noqa: B014 - transport must never raise
        code = "MANUAL_CURL_INVALID"
        return {"ok": False, "code": code,
                "message": _message(code, _sanitize(getattr(exc, "code", type(exc).__name__)))}
    unified = manual_result_to_collection_result(manual, spec.url)
    return _finish(unified, "CURL_SUCCESS", "MANUAL_CURL_INVALID")


def run_har(har_path: str) -> dict:
    from job_extractor.har_importer import import_har
    try:
        outcome = import_har(har_path)
    except Exception:
        return {"ok": False, "code": "HAR_INVALID",
                "message": _message("HAR_INVALID", "PARSE_FAILED")}
    if not outcome.result.jobs:
        return {"ok": False, "code": "HAR_NO_JOBS",
                "message": _message("HAR_NO_JOBS")}
    from job_extractor.reporting.manager import ReportManager
    artifacts = ReportManager().generate_reports(
        outcome.result, None, collection_mode="HAR_IMPORT")
    return {"ok": True, "code": "HAR_SUCCESS",
            "result": _result_payload(outcome.result, artifacts)}


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="collect bridge job runner")
    parser.add_argument("mode", choices=("auto", "curl", "har"))
    args = parser.parse_args(argv[1:])
    payload = json.loads(sys.stdin.read() or "{}")
    if args.mode == "auto":
        outcome = run_auto(str(payload.get("url") or ""))
    elif args.mode == "curl":
        outcome = run_curl(str(payload.get("curl") or ""))
    else:
        outcome = run_har(str(payload.get("har_path") or ""))
    sys.stdout.write(json.dumps(outcome, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
