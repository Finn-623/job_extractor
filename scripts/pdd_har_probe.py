"""N4 POC: offline PDD HAR probe — recover multi-page job data from a HAR.

Reads a local HAR exported from the user's own normal browser session and
re-plays nothing: this is a pure parser, it never contacts PDD.

Security: never prints cookies, request headers, raw postData, anti_content,
authorization or token values. Only page/pageSize and sanitized counts.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

LIST_PATH = "/api/recruit/position/list"


def _decode_body(response: dict):
    content = (response.get("content") or {}) if isinstance(response, dict) else {}
    text = content.get("text")
    if not isinstance(text, str) or not text:
        return None, "HAR_RESPONSE_BODY_MISSING"
    if content.get("encoding") == "base64":
        try:
            text = base64.b64decode(text).decode("utf-8", "replace")
        except Exception:
            return None, "HAR_RESPONSE_BODY_ENCODING_ERROR"
    try:
        return json.loads(text), None
    except Exception:
        return None, "HAR_RESPONSE_JSON_INVALID"


def _extract_jobs(body: dict):
    """Return (jobs, total, list_path) for either payload["list"] or
    payload["result"]["list"]; total prefers the level holding the list."""
    if not isinstance(body, dict):
        return None, None
    for holder in (body, body.get("result") if isinstance(body.get("result"), dict) else None):
        if not isinstance(holder, dict):
            continue
        jobs = holder.get("list")
        if isinstance(jobs, list) and jobs:
            total = holder.get("total") if isinstance(holder.get("total"), int) else None
            return jobs, total
    return None, None


def _structure(body: dict) -> str:
    if not isinstance(body, dict):
        return f"body_type={type(body).__name__}"
    result = body.get("result")
    lines = [f"top_keys={sorted(body)}"]
    if isinstance(result, dict):
        lines.append(f"result_keys={sorted(result)}")
        jobs, total, path = (*_extract_jobs(body), "result.list" if isinstance(result.get("list"), list) else None)
    else:
        jobs, total = _extract_jobs(body)
        path = "list" if isinstance(body.get("list"), list) else None
    lines.append(f"list_path={path} list_length={len(jobs) if isinstance(jobs, list) else 'none'} "
                 f"total_present={total is not None} total_value={total!r}")
    return "\n".join(lines)


def _safe_params(request: dict):
    text = (request.get("postData") or {}).get("text") or ""
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed.get("page"), parsed.get("pageSize")
    return None


def main(argv: list[str]) -> int:
    print("PDD HAR Probe")
    if len(argv) != 2:
        print("usage: python scripts/pdd_har_probe.py <har-file>")
        return 1
    try:
        har = json.loads(Path(argv[1]).read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        print(f"cannot read HAR: {type(exc).__name__}")
        return 1
    entries = (har.get("log") or {}).get("entries") or []

    # Group by requested page; keep the best entry per page (usable payload on
    # a 2xx preferred). Duplicate requests for the same page are never
    # double-counted.
    best: dict = {}
    matched = 0
    bodies_missing = 0
    for entry in entries:
        request = entry.get("request") or {}
        url = request.get("url") or ""
        if request.get("method", "").upper() != "POST" or urlsplit(url).path != LIST_PATH:
            continue
        matched += 1
        params = _safe_params(request)
        page = params[0] if params else None
        response = entry.get("response") or {}
        status = response.get("status")
        body, error = _decode_body(response)
        payload = None
        jobs = None
        total = None
        if body is not None and isinstance(body, dict) and body.get("success") is not False:
            jobs, total = _extract_jobs(body)
            if jobs:
                payload = body
        elif error == "HAR_RESPONSE_BODY_MISSING":
            bodies_missing += 1
        rank = (1 if payload is not None else 0,
                1 if isinstance(status, int) and 200 <= status < 300 else 0)
        key = page if page is not None else f"unknown{matched}"
        current = best.get(key)
        if current is None or rank > current["rank"]:
            best[key] = {"rank": rank, "status": status, "payload": payload,
                         "jobs": jobs, "total": total, "page": page}

    if matched == 0:
        print("failure_stage: HAR_NO_MATCHING_REQUEST")
        return 1
    print(f"\nMatched position/list requests: {matched}")

    seen: set[str] = set()
    raw = 0
    totals: set[int] = set()
    pages_seen: list = []
    usable = 0
    failure = None
    for key in sorted(best, key=lambda k: (isinstance(k, str), str(k))):
        item = best[key]
        page = item["page"]
        if item["payload"] is None:
            if failure is None:
                failure = "HAR_RESPONSE_BODY_MISSING" if item["payload"] is None and item["status"] != 200 else "PDD_RESPONSE_ERROR"
            print(f"\nPage {page if page is not None else '?'}")
            print(f"HTTP: {item['status']}")
            print("no usable job payload")
            continue
        codes = [str(record.get("code")) for record in item["jobs"]
                 if isinstance(record, dict) and record.get("code")]
        if not codes:
            failure = failure or "CODE_FIELD_MISSING"
            print(f"\nPage {page if page is not None else '?'}")
            print(f"HTTP: {item['status']}")
            print("no stable code field in records")
            continue
        usable += 1
        unique_added = len([code for code in codes if code not in seen])
        seen.update(codes)
        raw += len(item["jobs"])
        if item["total"] is not None:
            totals.add(item["total"])
        pages_seen.append(page)
        print(f"\nPage {page}")
        print(f"HTTP: {item['status']}")
        print(f"jobs: {len(item['jobs'])}")
        print(f"total: {item['total'] if item['total'] is not None else 'unknown'}")
        print(f"unique_added: {unique_added}")
        print(_structure(item["payload"]))

    bodies_available = bodies_missing < matched
    reported_total = sorted(totals)[0] if len(totals) == 1 else "unknown"
    print(f"\nRaw jobs: {raw}")
    print(f"Unique jobs: {len(seen)}")
    print(f"Pages observed: {','.join(str(p) for p in pages_seen)}")
    print(f"reported_total: {reported_total}")
    print(f"HAR response bodies available: {'YES' if bodies_available else 'NO'}")

    passed = (matched >= 3 and usable >= 3 and len(pages_seen) >= 3
              and len(seen) > 0 and len(seen) <= raw and len(totals) <= 1)
    if passed:
        print("\nPOC RESULT: PASS")
        return 0
    print("\nPOC RESULT: FAIL")
    if failure:
        print(f"failure_stage: {failure}")
    elif len(totals) > 1:
        print("failure_stage: PDD_RESPONSE_ERROR")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
