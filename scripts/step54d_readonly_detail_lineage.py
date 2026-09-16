"""STEP54D read-only HTTP-list detail-lineage audit.

This intentionally never replays a detail contract and never writes production
state.  It records only sanitized request/response shapes produced while a
human-visible job title is opened (at most front/middle/tail per site).
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.network_analyzer import safe_url


ROOT = Path("artifacts/step54_fresh_v1_benchmark/per_site")
OUT = Path("artifacts/step54d_readonly_detail_lineage")
SITES = {
    "naura": "https://career.naura.com/campus/jobs",
    "aecc": "https://aecc.iguopin.com/job",
    "sinomach": "https://zhaopin.sinomach.com.cn/SU64b4cfe82f9d24760ae8b80c/pb/school.html",
    "ymtc": "https://ymtc.zhiye.com/campus/jobs",
    "cxmt": "https://cxmt.zhiye.com/campus/jobs",
    "guangzhou_metro": "https://gzmetro.zhiye.com/campus/jobs",
    "hisense": "https://jobs.hisense.com/campus/jobs",
}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def selected_jobs(collection: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = collection.get("jobs") or []
    return [jobs[index] for index in sorted({0, len(jobs) // 2, max(0, len(jobs) - 1)})] if jobs else []


def value_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    result = {}
    for key, item in value.items():
        if isinstance(item, str):
            result[key] = {"type": "string", "length": len(item)}
        elif item not in (None, "", [], {}):
            result[key] = {"type": type(item).__name__}
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    all_results = {}
    for slug, url in SITES.items():
        folder = ROOT / slug
        discovery, plan, collection = (load(folder / name) for name in ("discovery.json", "plan.json", "collection.json"))
        samples = selected_jobs(collection)
        list_candidate = discovery.get("probable_list_api") or {}
        records = []
        for job in samples:
            raw = job.get("raw_data") or {}
            records.append({
                "id": job.get("job_id"), "title": job.get("job_title"),
                "raw_keys": sorted(key for key in raw if not key.startswith("_")),
                "jd_named_values": value_summary({key: value for key, value in raw.items()
                    if any(token in key.lower() for token in ("duty", "require", "content", "description", "detail", "responsib", "qualification"))}),
                "current_jd_state": raw.get("_jd_state"),
            })
        observed: list[dict[str, Any]] = []
        click_outcomes = []
        started = perf_counter()
        try:
            with BrowserRuntime(timeout_ms=15000) as runtime:
                page = runtime.page
                def response_listener(response):
                    request = response.request
                    content_type = response.headers.get("content-type") or ""
                    if request.resource_type not in ("xhr", "fetch") or "json" not in content_type.lower():
                        return
                    try:
                        payload = response.json()
                    except Exception:
                        return
                    if not isinstance(payload, (dict, list)):
                        return
                    candidate = GenericApiDetector._candidate(_Observation(
                        request.url, request.method, GenericApiDetector._body(request), safe_url(request.url)[1],
                        payload, "DETAIL_AUDIT", request_content_type=request.headers.get("content-type")))
                    observed.append({"url": candidate.url, "method": candidate.method, "score": candidate.score,
                        "list_path": candidate.response_shape.get("candidate_list_path"),
                        "fields": candidate.response_shape.get("sample_field_names", []),
                        "request_shape": candidate.request_body_shape,
                        "query_shape": candidate.query_params,
                        "sample_hint": candidate.sample_job_hint})
                page.on("response", response_listener)
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(5000)
                for sample in records:
                    title = sample.get("title")
                    before_url = page.url
                    before_count = len(observed)
                    outcome = {"id": sample.get("id"), "title": title, "before_url": before_url}
                    if not isinstance(title, str) or not title.strip():
                        outcome["state"] = "NO_TITLE"; click_outcomes.append(outcome); continue
                    try:
                        locator = page.get_by_text(title, exact=True).first
                        locator.click(timeout=4000)
                        page.wait_for_timeout(2500)
                        outcome["state"] = "CLICKED"
                    except Exception as exc:
                        outcome["state"] = "NOT_CLICKABLE"
                        outcome["error"] = type(exc).__name__
                    outcome["after_url"] = page.url
                    outcome["new_json_requests"] = observed[before_count:]
                    click_outcomes.append(outcome)
                    if page.url != before_url:
                        try:
                            page.go_back(wait_until="domcontentloaded", timeout=10000)
                            page.wait_for_timeout(1500)
                        except Exception:
                            pass
        except Exception as exc:
            runtime_error = f"{type(exc).__name__}: {exc}"
        else:
            runtime_error = None
        base = (list_candidate.get("url"), list_candidate.get("method"))
        detail_requests = [item for item in observed if (item["url"], item["method"]) != base]
        result = {"site": slug, "source_url": url, "elapsed_seconds": round(perf_counter() - started, 3),
            "list_source": {"endpoint": list_candidate.get("url"), "method": list_candidate.get("method"),
                            "list_path": plan.get("list_path"), "job_id_field": plan.get("job_id_field"),
                            "job_title_field": plan.get("job_title_field")},
            "samples": records, "click_outcomes": click_outcomes,
            "organic_non_list_json_requests": detail_requests, "runtime_error": runtime_error}
        (OUT / f"{slug}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        all_results[slug] = result
        print(f"{slug}: {len(detail_requests)} organic non-list JSON request(s)", flush=True)
    (OUT / "summary.json").write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
