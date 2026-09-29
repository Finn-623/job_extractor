"""N3 read-only first-page probe: where does the PDD '获取失败' happen?

Observation only. No token generation, no replay, no captcha handling.
Prints sanitized request/response facts (cookie/token values never printed).
"""
from __future__ import annotations

import sys
import time
from urllib.parse import urlsplit

from job_extractor.browser import BrowserRuntime

TARGET = "https://careers.pddglobalhr.com/jobs"
LIST_PATH = "/api/recruit/position/list"
WAIT_S = 120


def _anti_meta(request) -> str:
    body = request.post_data_json
    token = body.get("anti_content") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        return "anti_content present=False"
    return f"anti_content present=True len={len(token)}"


def _cookie_names(request) -> list[str]:
    header = request.headers.get("cookie") or ""
    return [part.split("=", 1)[0].strip() for part in header.split(";") if part.strip()]


def _audit(request, response) -> None:
    try:
        payload = response.json()
    except Exception as exc:
        print(f"body unreadable: {type(exc).__name__}")
        return
    if isinstance(payload, dict):
        print(f"success={payload.get('success')!r} "
              f"errorCode={payload.get('errorCode')!r} errorMsg={payload.get('errorMsg')!r}")
        print(f"top_keys={sorted(payload)[:10]}")
    else:
        print(f"body_type={type(payload).__name__}")


def main() -> int:
    runtime = BrowserRuntime(headless=False)
    with runtime:
        page = runtime.page
        events: list[tuple] = []
        page.on("requestfailed", lambda request: events.append(
            ("requestfailed", request.method, urlsplit(request.url).path,
             (request.failure or "")[:80])))
        page.on("pageerror", lambda error: events.append(("pageerror", str(error)[:120])))
        seen: dict = {}

        def on_response(response) -> None:
            request = response.request
            if urlsplit(response.url).path != LIST_PATH or request.method != "POST":
                return
            if urlsplit(response.url).netloc != urlsplit(TARGET).netloc:
                return
            cookies = _cookie_names(request)
            seen[response.url] = {
                "status": response.status,
                "content_type": response.headers.get("content-type", ""),
                "anti": _anti_meta(request),
                "referer": request.headers.get("referer", ""),
                "has_nano_fp": any(name == "_nano_fp" for name in cookies),
                "cookie_names": cookies,
                "ua": request.headers.get("user-agent", ""),
                "request": request,
                "response": response,
            }
            print(f"\n[{time.strftime('%H:%M:%S')}] POST list request observed "
                  f"status={response.status} ct={response.headers.get('content-type','')}")
            print(_anti_meta(request))
            print(f"referer present={'yes' if request.headers.get('referer') else 'no'} "
                  f"cookie_names={cookies}")
            _audit(request, response)

        page.on("response", on_response)
        page.goto(TARGET, wait_until="domcontentloaded")
        print(f"waiting up to {WAIT_S}s for POST {LIST_PATH} ...")
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline and not seen:
            page.wait_for_timeout(500)
        if not seen:
            print("NO_LIST_REQUEST_OBSERVED")
        for entry in seen.values():
            print(f"ua={entry['ua']}")
        if events:
            print(f"page events ({len(events)}):")
            for kind, *rest in events[:20]:
                print(f"  {kind}: {rest}")
        else:
            print("page events: none")
    return 0


def _cookie_names(request) -> list[str]:
    header = request.headers.get("cookie") or ""
    return [part.split("=", 1)[0].strip() for part in header.split(";") if part.strip()]


if __name__ == "__main__":
    sys.exit(main())
