"""Step 29 sanitized browser-vs-HTTP replay diagnostics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright

from job_extractor.adapters.generic import GenericAdapter
from job_extractor.collectors.generic_http import GenericHttpCollector, path_get
from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.discovery.network_analyzer import (
    find_array_info,
    request_shape,
    safe_business_data,
    safe_url,
)


TARGET = "https://maker.haier.net/client/campus/activityindex.html"
FRAGMENT = "getactivityresearchlist"


def public_body(request) -> dict[str, Any]:
    try:
        value = request.post_data_json
    except Exception:
        return {}
    return safe_business_data(value) if isinstance(value, dict) else {}


def response_summary(response: httpx.Response, payload: Any) -> dict[str, Any]:
    arrays = []
    for path, length, sample in find_array_info(payload, max_depth=7):
        keys = sorted({str(k) for item in sample if isinstance(item, dict) for k in item})
        low = {key.lower() for key in keys}
        likeness = sum(bool(low & aliases) for aliases in (
            {"id", "jobid", "job_id", "positionid", "researchprojectid"},
            {"title", "name", "jobtitle", "positionname", "researchprojectname"},
            {"location", "city", "workplace"},
            {"description", "content", "requirements", "jobdescription"},
        ))
        arrays.append({"path": path, "length": length, "sample_keys": keys, "job_likeness": likeness})
    return {
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "final_url": safe_url(str(response.url))[0],
        "redirect_count": len(response.history),
        "top_level_type": type(payload).__name__,
        "top_level_keys": sorted(payload) if isinstance(payload, dict) else [],
        "arrays": arrays,
        "empty": not bool(payload),
    }


def decode(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"_non_json_body_length": len(response.text), "_prefix": response.text[:80]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    captured: dict[str, Any] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(20_000)

        def observe(response):
            if FRAGMENT not in response.url.lower() or captured:
                return
            request = response.request
            try:
                payload = response.json()
            except Exception:
                return
            captured.update({
                "method": request.method,
                "url": safe_url(request.url)[0],
                "query": safe_url(request.url)[1],
                "content_type": request.headers.get("content-type"),
                "origin": request.headers.get("origin"),
                "referer": safe_url(request.headers.get("referer", ""))[0],
                "body": public_body(request),
                "body_shape": request_shape(public_body(request)),
                "status": response.status,
                "response_content_type": response.headers.get("content-type"),
                "payload": payload,
            })

        page.on("response", observe)
        page.goto(TARGET, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(1000)
        browser.close()

    if not captured:
        raise SystemExit("job API not observed")

    observation = _Observation(
        captured["url"], captured["method"], captured["body"], captured["query"],
        captured["payload"], "HYDRATION",
    )
    candidate = GenericApiDetector._candidate(observation)
    pagination = GenericApiDetector._pagination([observation], candidate)
    discovery = DiscoveryResult(
        source_url=TARGET,
        status="DISCOVERED",
        candidate_list_apis=[candidate],
        probable_list_api=candidate,
        detected_pagination=pagination,
        detected_scope={"recruitment_type": "campus"},
    )
    plan = GenericAdapter().build_plan(discovery)

    headers = {"origin": captured["origin"], "referer": captured["referer"]}
    headers = {key: value for key, value in headers.items() if value}
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        variants = {
            "collector_current_json": client.request(plan.list_method, plan.list_endpoint, json=plan.initial_values),
            "json_full_public_body": client.request(plan.list_method, plan.list_endpoint, json=captured["body"]),
            "form_full_public_body": client.request(plan.list_method, plan.list_endpoint, data=captured["body"]),
            "form_body_with_public_context_headers": client.request(
                plan.list_method, plan.list_endpoint, data=captured["body"], headers=headers
            ),
        }

    replay = {}
    for name, response in variants.items():
        payload = decode(response)
        replay[name] = {"request_content_type": response.request.headers.get("content-type"),
                        "request_body_length": len(response.request.content),
                        "response": response_summary(response, payload), "payload": payload}

    browser_records = path_get(captured["payload"], plan.list_path) or []
    mapper = GenericHttpCollector(plan)
    mapping = []
    for index, raw in enumerate(browser_records[:3]):
        job = mapper._job(raw) if isinstance(raw, dict) else None
        mapping.append({
            "index": index,
            "raw_keys": sorted(raw) if isinstance(raw, dict) else [],
            "planned_id_field": plan.job_id_field,
            "planned_id_value": raw.get(plan.job_id_field) if isinstance(raw, dict) and plan.job_id_field else None,
            "planned_title_field": plan.job_title_field,
            "planned_title_value": raw.get(plan.job_title_field) if isinstance(raw, dict) and plan.job_title_field else None,
            "normalized": job.model_dump(mode="json") if job else None,
            "rejection": None if job else ("ID_NOT_MAPPED" if not plan.job_id_field or not raw.get(plan.job_id_field) else "TITLE_NOT_MAPPED"),
        })

    report = {
        "browser": {key: value for key, value in captured.items() if key != "payload"},
        "browser_response": response_summary(
            type("Observed", (), {"status_code": captured["status"], "headers": {"content-type": captured["response_content_type"]},
                                    "url": captured["url"], "history": []})(), captured["payload"]
        ),
        "plan": plan.model_dump(mode="json"),
        "browser_record_count": len(browser_records),
        "mapping": mapping,
        "replays": replay,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
