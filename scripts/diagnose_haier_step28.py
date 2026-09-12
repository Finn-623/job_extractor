"""Step 28 controlled Haier browser diagnostics (not production runtime)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


TARGET = "https://maker.haier.net/client/campus/activityindex.html"
JOB_API = "getactivityresearchlist"


def stamp(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def safe_url(raw: str) -> str:
    parsed = urlsplit(raw)
    keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
    query = "&".join(f"{key}=[VALUE]" for key in keys)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def visible_entries(page) -> list[dict]:
    script = r"""() => [...document.querySelectorAll('a[href],button,[role="button"],[role="link"],[data-href]')]
      .map((node, index) => {
        const rect=node.getBoundingClientRect();
        const text=(node.innerText || node.getAttribute('aria-label') || '').trim().replace(/\s+/g,' ');
        const href=node.getAttribute('href') || node.getAttribute('data-href') || '';
        return {index,tag:node.tagName,text:text.slice(0,160),href,visible:!!(rect.width&&rect.height)};
      }).filter(x => x.visible && x.text && /(岗位|职位|招聘|校招|校园|实习|应聘|job|career|position|campus|intern)/i.test(x.text))
        .slice(0,100)"""
    try:
        return page.evaluate(script)
    except Exception:
        return []


def strongest_entry(entries: list[dict]) -> dict | None:
    def score(item: dict) -> tuple[int, int]:
        text = item.get("text", "")
        href = item.get("href", "")
        value = 0
        for pattern, points in ((r"岗位|职位|position|job", 8), (r"校招|校园|campus", 6),
                                (r"实习|intern", 4), (r"招聘|career|应聘", 3)):
            if re.search(pattern, text + " " + href, re.I):
                value += points
        if href and not href.startswith("#"):
            value += 2
        return value, -len(text)
    return max(entries, key=score) if entries else None


def dom_signature(page) -> str | None:
    try:
        value = page.evaluate(r"""() => [...document.querySelectorAll('body *')].slice(0,5000)
          .map(n => n.tagName + '.' + [...n.classList].sort().join('.')).join('|')""")
        return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]
    except Exception:
        return None


def run_once(mode: str, ordinal: int) -> dict:
    start = time.perf_counter()
    events = [{"event": "navigation_start", "ms": 0}]
    requests: list[dict] = []
    scripts: list[dict] = []
    job_requests: list[dict] = []
    result = {"mode": mode, "ordinal": ordinal, "started_at": datetime.now(timezone.utc).isoformat()}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(10_000)
        page.add_init_script("""
          window.__step28FirstMutation = null;
          new MutationObserver(() => {
            if (window.__step28FirstMutation === null) window.__step28FirstMutation = performance.now();
          }).observe(document, {subtree:true,childList:true,attributes:true});
        """)
        page.on("domcontentloaded", lambda: events.append({"event": "DOMContentLoaded", "ms": stamp(start)}))
        def observe_request(request):
            item = {"ms": stamp(start), "method": request.method, "type": request.resource_type,
                    "url": safe_url(request.url)}
            requests.append(item)
            if request.resource_type == "script":
                scripts.append(item)
            if JOB_API in request.url.lower():
                job_requests.append(item)
                events.append({"event": "job_api_request", "ms": item["ms"]})
        page.on("request", observe_request)
        try:
            wait_until = "networkidle" if mode == "C_NETWORKIDLE" else "domcontentloaded"
            page.goto(TARGET, wait_until=wait_until, timeout=20_000)
        except PlaywrightTimeoutError:
            events.append({"event": "navigation_timeout", "ms": stamp(start)})

        entries = visible_entries(page)
        events.append({"event": "entry_discovery", "ms": stamp(start), "count": len(entries)})
        selected = strongest_entry(entries)

        if mode == "A_DOMCONTENTLOADED":
            page.wait_for_timeout(250)
        elif mode == "B_HYDRATION_WAIT":
            page.wait_for_timeout(5_000)
        elif mode == "C_NETWORKIDLE":
            page.wait_for_timeout(500)
        elif mode == "D_SCROLL":
            events.append({"event": "scroll", "ms": stamp(start)})
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3_000)
        elif mode in ("E_CLICK", "F_ROUTE_STABILIZE") and selected:
            before = page.url
            events.append({"event": "click", "ms": stamp(start), "text": selected["text"],
                           "href": selected["href"]})
            locator = page.locator('a[href],button,[role="button"],[role="link"],[data-href]').nth(selected["index"])
            try:
                locator.click(timeout=5_000)
            except Exception as exc:
                events.append({"event": "click_error", "ms": stamp(start), "kind": type(exc).__name__})
            if page.url != before:
                events.append({"event": "route_change", "ms": stamp(start), "url": safe_url(page.url)})
            if mode == "F_ROUTE_STABILIZE":
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except PlaywrightTimeoutError:
                    events.append({"event": "route_networkidle_timeout", "ms": stamp(start)})
                page.wait_for_timeout(2_000)
            else:
                page.wait_for_timeout(3_000)
        elif mode in ("E_CLICK", "F_ROUTE_STABILIZE"):
            events.append({"event": "no_semantic_entry", "ms": stamp(start)})
            page.wait_for_timeout(3_000)

        events.append({"event": "final_stabilization", "ms": stamp(start)})
        try:
            first_mutation = page.evaluate("window.__step28FirstMutation")
        except Exception:
            first_mutation = None
        if first_mutation is not None:
            events.append({"event": "first_dom_mutation", "ms_from_navigation_timing": round(first_mutation)})
        try:
            body_text = page.locator("body").inner_text(timeout=3_000)
        except Exception:
            body_text = ""
        final_entries = visible_entries(page)
        try:
            storage = page.evaluate("() => ({local:Object.keys(localStorage),session:Object.keys(sessionStorage)})")
        except Exception:
            storage = {"local": [], "session": []}
        try:
            cookies = context.cookies()
        except Exception:
            cookies = []
        result.update({
            "events": sorted(events, key=lambda x: x.get("ms", -1)),
            "job_api_observed": bool(job_requests),
            "job_api_requests": job_requests,
            "final_url": safe_url(page.url),
            "document_title": page.title(),
            "visible_entries": final_entries,
            "selected_entry": selected,
            "dom_signature": dom_signature(page),
            "cookies_exist": bool(cookies),
            "cookie_count": len(cookies),
            "storage_key_names": storage,
            "request_sequence": requests,
            "script_sequence": scripts,
            "job_card_count": len(re.findall(r"岗位|职位", body_text)),
            "selected_tab_text": next((x for x in body_text.splitlines() if re.search(r"校招|校园|实习", x)), None),
            "loading_visible": bool(re.search(r"加载中|loading", body_text, re.I)),
            "body_length": len(body_text),
            "elapsed_ms": stamp(start),
        })
        browser.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    modes = ("A_DOMCONTENTLOADED", "B_HYDRATION_WAIT", "C_NETWORKIDLE", "D_SCROLL", "E_CLICK", "F_ROUTE_STABILIZE")
    results = []
    for mode in modes:
        for ordinal in range(1, args.repeats + 1):
            item = run_once(mode, ordinal)
            results.append(item)
            print(json.dumps({k: item[k] for k in ("mode", "ordinal", "job_api_observed", "final_url", "elapsed_ms")}, ensure_ascii=False), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
