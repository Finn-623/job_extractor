"""Bounded, page-driven SPA action and network forensics."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import sync_playwright

from job_extractor.discovery.dom_semantics import repeated_job_cards
from job_extractor.discovery.network_analyzer import (
    get_path,
    list_observation,
    request_shape,
    response_shape,
    safe_url,
    sanitized_values,
)
from job_extractor.discovery.scorer import confidence, score_list
from job_extractor.field_semantics import infer_field


RECRUITMENT_TERMS = (
    "job", "position", "career", "campus", "school", "graduate", "intern",
    "\u804c\u4f4d", "\u5c97\u4f4d", "\u6821\u62db", "\u6821\u56ed", "\u62db\u8058",
    "\u52a0\u5165\u6211\u4eec", "\u67e5\u770b\u804c\u4f4d", "\u804c\u4f4d\u5217\u8868",
)


def _arrays(value, path="$", depth=0):
    found = []
    if depth > 8:
        return found
    if isinstance(value, list):
        sample = next((item for item in value if isinstance(item, dict)), None)
        fields = sorted(sample) if sample else []
        likeness = sum(
            any(term in str(key).lower() for term in ("job", "position", "title", "location", "department", "requisition"))
            for key in fields
        )
        found.append({"path": path, "count": len(value), "sample_fields": fields, "job_likeness": likeness})
        for item in value[:3]:
            found.extend(_arrays(item, path + "[]", depth + 1))
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(_arrays(child, f"{path}.{key}", depth + 1))
    return found


def _controls(page, base_url):
    rows = page.evaluate("""() => [...document.querySelectorAll('*')]
    .filter(n => n.matches('a[href],button,[role="link"],[role="button"],[onclick],[data-route],[data-url],[data-href]') || getComputedStyle(n).cursor === 'pointer')
    .map((n, index) => {
      n.setAttribute('data-step33-control', String(index));
      return ({
      control_id: String(index),
      tag: n.tagName.toLowerCase(),
      text: (n.innerText || n.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' '),
      href: n.getAttribute('href') || '',
      role: n.getAttribute('role') || '',
      class_name: typeof n.className === 'string' ? n.className : '',
      onclick: n.getAttribute('onclick') || '',
      data_route: n.getAttribute('data-route') || '',
      data_url: n.getAttribute('data-url') || n.getAttribute('data-href') || '',
      visible: !!(n.offsetWidth || n.offsetHeight)
    })}).filter(x => x.visible && x.text.length <= 500).slice(0, 1000)""")
    for row in rows:
        raw = row["href"] or row["data_url"] or row["data_route"]
        row["absolute_url"] = urljoin(base_url, raw) if raw else None
        value = " ".join(str(row.get(key) or "") for key in ("text", "href", "data_route", "data_url", "onclick")).lower()
        row["semantic_score"] = sum(3 for term in RECRUITMENT_TERMS if term in value)
        if any(term in value for term in ("privacy", "login", "news", "brand", "\u65b0\u95fb", "\u9690\u79c1", "\u767b\u5f55")):
            row["semantic_score"] -= 8
    return sorted(rows, key=lambda row: (-row["semantic_score"], int(row["control_id"])))


def _choose(action, controls, source_url):
    positive = [row for row in controls if row["semantic_score"] > 0]
    if action in ("strongest","job_card"):
        priority = ("\u6821\u56ed", "\u6821\u62db", "\u804c\u4f4d", "\u5c97\u4f4d", "campus", "position", "job")
        return max(positive, key=lambda row: (sum(4 for term in priority if term in (row["text"] + " " + row["href"]).lower()), row["semantic_score"]), default=None)
    if action == "view_jobs":
        exact = ("\u67e5\u770b\u804c\u4f4d", "\u804c\u4f4d\u5217\u8868", "all jobs", "view jobs")
        return next((row for row in positive if any(term in row["text"].lower() for term in exact)), None)
    if action == "route":
        return next((row for row in positive if row["data_route"] or (not row["href"] and row["role"] in ("link", "button"))), None)
    if action == "recruit_host":
        source_host = (urlsplit(source_url).hostname or "").lower()
        return next((row for row in positive if row["absolute_url"] and (urlsplit(row["absolute_url"]).hostname or "").lower() != source_host), None)
    return None


def _run(browser, url, action):
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(10_000)
    started = time.perf_counter()
    phase = ["INITIAL"]
    responses = []

    def observe(response):
        request = response.request
        if request.resource_type not in ("xhr", "fetch"):
            return
        content_type = (response.headers.get("content-type") or "").lower()
        if "json" not in content_type:
            return
        try:
            payload = response.json()
        except Exception:
            return
        try:
            body = request.post_data_json if isinstance(request.post_data_json, dict) else {}
        except Exception:
            body = {}
        clean_url, query = safe_url(request.url)
        path, count, sample = list_observation(payload)
        score, evidence, shape = score_list(clean_url, payload)
        responses.append({
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "phase": phase[0], "url": clean_url, "method": request.method,
            "status": response.status, "content_type": request.headers.get("content-type"),
            "query": query, "request_body": sanitized_values(body),
            "request_shape": request_shape(body),
            "top_level_type": type(payload).__name__,
            "top_level_keys": sorted(payload) if isinstance(payload, dict) else [],
            "arrays": _arrays(payload), "inferred_list_path": path,
            "record_count": count,
            "sample_fields": sorted(sample[0]) if sample and isinstance(sample[0], dict) else [],
            "sample_records": sanitized_values({"records": sample[:5]}).get("records", []),
            "pagination": {"total_field": shape.get("total_field"), "total": get_path(payload, shape.get("total_field"))},
            "job_score": score, "confidence": confidence(score),
            "evidence": evidence, "rejection_reasons": shape.get("rejection_reasons", []),
            "response_shape": response_shape(payload),
        })

    context.on("response", observe)
    error = None
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        phase[0] = "HYDRATION"
        page.wait_for_timeout(5_000)
        controls = _controls(page, page.url)
        before_count = len(responses)
        chosen = _choose(action, controls, url)
        if action == "scroll":
            phase[0] = "ACTION"
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        elif chosen:
            phase[0] = "ACTION"
            locator = page.locator(f'[data-step33-control="{chosen["control_id"]}"]')
            locator.click(timeout=5_000)
        if action != "none":
            page.wait_for_timeout(1_000)
            phase[0] = "ROUTE_CHANGE"
            page.wait_for_timeout(2_000)
            if len(context.pages) > 1:
                page = context.pages[-1]
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10_000)
                except Exception:
                    pass
            phase[0] = "POST_ROUTE_NETWORK"
            page.wait_for_timeout(8_000)
            phase[0] = "DOM_STABILIZATION"
            page.wait_for_timeout(2_000)
        detail_action = None
        if action == "job_card":
            source=next((row for row in responses if row["confidence"]=="HIGH" and row["sample_records"]),None)
            if source:
                title_field=infer_field(source["sample_fields"],"title");id_field=infer_field(source["sample_fields"],"id");record=source["sample_records"][0]
                title=record.get(title_field) if title_field else None
                if isinstance(title,str):
                    target=page.get_by_text(title,exact=True).first;detail_action={"title":title,"id":record.get(id_field) if id_field else None,"matches":target.count(),"before_url":page.url}
                    if target.count():
                        phase[0]="ACTION";target.click(timeout=5_000);page.wait_for_timeout(500);phase[0]="ROUTE_CHANGE";page.wait_for_timeout(1_000);phase[0]="POST_ROUTE_NETWORK";page.wait_for_timeout(5_000)
                        detail_action["after_url"]=page.url
        frames = [frame.url for frame in page.frames[1:] if frame.url]
        cards = repeated_job_cards(page, page.url)
        result = {
            "action": action, "source_url": url, "final_url": page.url,
            "title": page.title(), "chosen_control": chosen,
            "controls": controls[:250], "iframes": frames,
            "context_pages": [item.url for item in context.pages],
            "detail_action": detail_action,
            "new_json_xhr": len(responses) - before_count,
            "job_card_count": len(cards),
            "candidate_source_count": sum(row["confidence"] == "HIGH" and not row["rejection_reasons"] for row in responses),
            "responses": responses, "elapsed": time.perf_counter() - started,
        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result = {"action": action, "source_url": url, "final_url": page.url, "error": error, "responses": responses, "elapsed": time.perf_counter() - started}
    context.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action", choices=("none", "strongest", "view_jobs", "route", "scroll", "recruit_host", "job_card", "all"), default="all")
    args = parser.parse_args()
    actions = ("none", "strongest", "view_jobs", "route", "scroll", "recruit_host") if args.action == "all" else (args.action,)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        runs = [_run(browser, args.url, action) for action in actions]
        browser.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{
        "action": row["action"], "final_url": row["final_url"],
        "json_xhr": len(row["responses"]), "new_json_xhr": row.get("new_json_xhr"),
        "job_cards": row.get("job_card_count"), "candidates": row.get("candidate_source_count"),
        "error": row.get("error"), "elapsed": row["elapsed"],
    } for row in runs], ensure_ascii=False))


if __name__ == "__main__":
    main()
