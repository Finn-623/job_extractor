"""N6.1 HAR importer tests — offline ingestion semantics."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from job_extractor.har_importer import import_har


def _list_body(page: int, size: int = 2, codes: tuple[str, ...] = ("A", "B"),
               success: bool = True, error_code: int | None = None) -> dict:
    return {
        "success": success,
        "errorCode": error_code,
        "errorMsg": None if success else "rate",
        "result": {"total": 8, "list": [
            {"code": code, "jobName": f"Job {code}", "workPlace": "Shanghai",
             "postType": "R&D", "updateTime": "2026-09-01"}
            for code in codes
        ], "page": page, "pageSize": size},
    }


def _har(pairs: list[tuple[int, dict]]) -> dict:
    entries = []
    for page, body in pairs:
        entries.append({
            "request": {"method": "POST",
                        "url": "https://x.test/api/recruit/position/list",
                        "postData": {"text": json.dumps({"page": page, "pageSize": 2})}},
            "response": {"status": 200,
                         "content": {"text": json.dumps(body)}}})
    return {"log": {"entries": entries}}


def _write(tmp_path: Path, har: dict) -> str:
    file = tmp_path / "case.har"
    file.write_text(json.dumps(har), encoding="utf-8")
    return str(file)


def test_plain_har_result_list(tmp_path):
    outcome = import_har(_write(tmp_path, _har(
        [(1, _list_body(1, codes=("A", "B"))),
         (2, _list_body(2, codes=("C", "D")))])))
    assert outcome.metrics["pages_observed"] == 2
    assert outcome.metrics["raw_rows"] == 4
    assert outcome.metrics["unique_jobs"] == 4
    assert len(outcome.result.jobs) == 4


def test_base64_content(tmp_path):
    har = _har([(1, _list_body(1))])
    entry = har["log"]["entries"][0]
    entry["response"]["content"] = {
        "text": base64.b64encode(
            json.dumps(_list_body(1)).encode()).decode(),
        "encoding": "base64"}
    outcome = import_har(_write(tmp_path, har))
    assert outcome.metrics["raw_rows"] == 2
    assert len(outcome.result.jobs) == 2


def test_payload_list_path(tmp_path):
    body = {"list": [{"code": "A", "jobName": "Job A",
                      "workPlace": "Shanghai", "postType": "R&D"}],
            "total": 1, "page": 1, "pageSize": 1}
    outcome = import_har(_write(tmp_path, _har([(1, body)])))
    assert outcome.metrics["raw_rows"] == 1
    assert outcome.result.jobs[0].job_id == "A"


def test_fail_then_success_same_page(tmp_path):
    fail = _list_body(1, success=False, error_code=54001, codes=())
    ok = _list_body(1)
    outcome = import_har(_write(tmp_path, _har([(1, fail), (1, ok)])))
    rows = [row for row in outcome.audit if row["page"] == 1]
    selected = [row for row in rows if row["selected_for_page"]]
    assert len(selected) == 1
    assert selected[0]["errorCode"] in (None, 0)
    assert outcome.metrics["raw_rows"] == 2
    assert outcome.metrics["failed_responses"] == 1


def test_duplicate_stable_id_dedupe(tmp_path):
    outcome = import_har(_write(tmp_path, _har(
        [(1, _list_body(1, codes=("A", "B"))),
         (2, _list_body(2, codes=("A", "B")))])))
    assert outcome.metrics["raw_rows"] == 4
    assert outcome.metrics["duplicate_jobs"] == 2
    assert outcome.result.total_unique == 2


def test_malformed_json_not_fatal(tmp_path):
    har = _har([(1, _list_body(1))])
    har["log"]["entries"].insert(0, {
        "request": {"method": "POST",
                    "url": "https://x.test/api/recruit/position/list",
                    "postData": {"text": json.dumps({"page": 2, "pageSize": 2})}},
        "response": {"status": 200,
                     "content": {"text": "{not json"}}})
    outcome = import_har(_write(tmp_path, har))
    assert outcome.metrics["raw_rows"] == 2
    assert outcome.metrics["malformed_json_responses"] == 1


def test_non_job_payload_rejected(tmp_path):
    config = {"success": True, "errorCode": 0,
              "result": {"tree": [{"value": "x"}, {"value": "y"}]}}
    body = {**_list_body(1), "result": {
        **_list_body(1)["result"], "cityTree": config["result"]["tree"]}}
    outcome = import_har(_write(tmp_path, _har([(1, body)])))
    assert outcome.result.jobs
    assert outcome.metrics["raw_rows"] == 2  # config payload not imported


def test_config_payload_rejected(tmp_path):
    outcome = import_har(_write(
        tmp_path, _har([(1, {"success": True, "errorCode": 0,
                              "result": {"list": [{"name": "Beijing"},
                                                  {"name": "Shanghai"}]}})])))
    assert outcome.result.jobs == [] or outcome.metrics.get("usable_responses", 0) == 0
