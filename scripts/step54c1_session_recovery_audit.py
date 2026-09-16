"""Read-only STEP54C.1 recovery-level audit.

Compares page reload, a new page in the same context, and a new isolated
browser context.  It records metadata only; response bodies and storage values
are never persisted.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.network_analyzer import safe_url


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "step54c1_session_recovery"
SITES = (("naura", "https://career.naura.com/campus/jobs"),
         ("hisense", "https://jobs.hisense.com/campus/jobs"))
ATTEMPTS = 5
WAIT_MS = 9000


def category(key: str) -> str:
    low = key.lower()
    for token, name in (("token", "token"), ("auth", "auth"), ("session", "session"),
                        ("config", "config"), ("theme", "theme"), ("locale", "locale"),
                        ("cache", "cache")):
        if token in low:
            return name
    return "other"


def state_metadata(page, context) -> dict:
    try:
        storage = page.evaluate("""() => ({
          local: Object.keys(localStorage || {}), session: Object.keys(sessionStorage || {}),
          workers: navigator.serviceWorker ? navigator.serviceWorker.getRegistrations().then(x => x.length) : Promise.resolve(0),
          navigation: performance.getEntriesByType('navigation').slice(-1).map(x => ({type:x.type,duration:Math.round(x.duration)}))[0] || null
        })""") or {}
    except Exception:
        storage = {}
    try:
        cookies = context.cookies()
    except Exception:
        cookies = []
    def keys(name: str) -> dict:
        values = [str(x) for x in (storage.get(name) or [])][:50]
        return {"count": len(values), "key_names": values, "categories": sorted({category(x) for x in values})}
    return {"cookies_present": bool(cookies), "cookie_count": len(cookies),
            "cookie_categories": sorted({category(str(x.get("name") or "")) for x in cookies}),
            "local_storage": keys("local"), "session_storage": keys("session"),
            "service_worker_count": storage.get("workers"), "navigation": storage.get("navigation")}


def probe(page, context, url: str, level: str, action) -> dict:
    candidates = []
    first_request_ms = None
    list_seen_ms = None
    started = perf_counter()

    def response_seen(response) -> None:
        nonlocal first_request_ms, list_seen_ms
        request = response.request
        if request.resource_type not in ("xhr", "fetch"):
            return
        now = round((perf_counter() - started) * 1000, 1)
        first_request_ms = now if first_request_ms is None else first_request_ms
        if "json" not in (response.headers.get("content-type") or "").lower():
            return
        try:
            payload = response.json()
        except Exception:
            return
        if not isinstance(payload, (dict, list)):
            return
        clean, query = safe_url(request.url)
        observation = _Observation(request.url, request.method, GenericApiDetector._body(request), query, payload, level)
        candidate = GenericApiDetector._candidate(observation)
        if candidate.score >= GenericApiDetector.threshold and not candidate.rejection_reasons:
            candidates.append(candidate)
        if GenericApiDetector._reliable_list_source(candidate):
            list_seen_ms = now if list_seen_ms is None else list_seen_ms

    page.on("response", response_seen)
    before = state_metadata(page, context)
    try:
        action()
        page.wait_for_timeout(WAIT_MS)
    except Exception as exc:
        error = type(exc).__name__
    else:
        error = None
    reliable = [item for item in candidates if GenericApiDetector._reliable_list_source(item)]
    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=3000)[:20000]
    except Exception:
        pass
    after = state_metadata(page, context)
    return {
        "level": level, "elapsed_seconds": round(perf_counter() - started, 3), "error": error,
        "source_found": bool(reliable), "get_job_ad_page_list_seen": any("getjobadpagelist" in item.url.lower() for item in candidates),
        "candidate_count": len(candidates), "reliable_list_evidence": len(reliable),
        "candidate_urls": [item.url for item in reliable], "time_to_first_meaningful_request_ms": first_request_ms,
        "time_to_reliable_list_ms": list_seen_ms, "dom_job_ready": bool(reliable) or len(body_text) > 5000,
        "runtime_source": False, "state_before": before, "state_after": after,
    }


def run_attempt(site: str, url: str, index: int) -> dict:
    with BrowserRuntime(timeout_ms=15000) as runtime:
        context = runtime.context
        page = runtime.page
        levels = [probe(page, context, url, "initial", lambda: page.goto(url, wait_until="domcontentloaded", timeout=15000))]
        if levels[-1]["source_found"]:
            return {"run": index, "levels": levels}
        levels.append(probe(page, context, url, "same_page_reload", lambda: page.reload(wait_until="domcontentloaded", timeout=15000)))
        if levels[-1]["source_found"]:
            return {"run": index, "levels": levels}
        page.close()
        page = context.new_page()
        levels.append(probe(page, context, url, "new_page_same_context", lambda: page.goto(url, wait_until="domcontentloaded", timeout=15000)))
        if levels[-1]["source_found"]:
            return {"run": index, "levels": levels}
        fresh = runtime.browser.new_context()
        fresh_page = fresh.new_page()
        try:
            levels.append(probe(fresh_page, fresh, url, "new_isolated_context", lambda: fresh_page.goto(url, wait_until="domcontentloaded", timeout=15000)))
        finally:
            fresh.close()
        return {"run": index, "levels": levels}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}
    for site, url in SITES:
        rows = []
        for index in range(1, ATTEMPTS + 1):
            row = run_attempt(site, url, index)
            rows.append(row)
            (OUT / f"{site}_run_{index}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
            print(site, index, row["levels"][-1]["level"], row["levels"][-1]["source_found"], flush=True)
        report[site] = rows
    (OUT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
