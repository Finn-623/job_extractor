"""N3.1 POC: PDD social list succeeds inside a user-established browser session.

Uses only Playwright's official session persistence (isolated persistent
profile under .tmp/). The user may complete the site's normal verification in
the headed window. We only observe the site's own
``POST /api/recruit/position/list`` responses.

Security: cookie/token values are never printed — only counts, presence and
length. No fingerprint spoofing, no stealth, no captcha bypass, no replay.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

TARGET = "https://careers.pddglobalhr.com/jobs"
ORIGIN = "https://careers.pddglobalhr.com"
LIST_PATH = "/api/recruit/position/list"
PROFILE = ".tmp/pdd_browser_profile"
RUN1_WAIT_S = 120
RUN2_WAIT_S = 60


def _is_list_response(response) -> bool:
    return (urlsplit(response.url).netloc == urlsplit(ORIGIN).netloc
            and urlsplit(response.url).path == LIST_PATH
            and response.request.method == "POST")


def _anti_meta(request) -> str:
    body = request.post_data_json
    token = body.get("anti_content") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        return "anti_content present=False"
    return f"anti_content_present=True anti_content_length={len(token)}"


def _observe_list(context, label: str, wait_s: int) -> bool:
    """Wait for the site's own list request; report sanitized facts.
    Returns True when a successful job payload was captured."""
    page = context.pages[0] if context.pages else context.new_page()
    matched: list = []
    page.on("response", lambda response: matched.append(response)
            if _is_list_response(response) else None)
    print(f"\n[{label}] opening {TARGET} (waiting up to {wait_s}s for user/verification)")
    page.goto(TARGET, wait_until="domcontentloaded")
    cookies = context.cookies(ORIGIN)
    names = [c["name"] for c in cookies]
    print(f"cookie_count={len(names)} nano_fp_present={any(n == '_nano_fp' for n in names)}")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        for response in matched:
            try:
                payload = response.json()
            except Exception:
                continue
            success = payload.get("success") if isinstance(payload, dict) else None
            jobs = payload.get("list") if isinstance(payload, dict) else None
            print(f"[{label}] position/list status={response.status} "
                  f"success={success!r} errorCode={payload.get('errorCode') if isinstance(payload, dict) else None!r} "
                  f"errorMsg={payload.get('errorMsg') if isinstance(payload, dict) else None!r} "
                  f"{_anti_meta(response.request)}")
            if (response.status == 200 and success is not False
                    and isinstance(jobs, list) and jobs):
                print(f"[{label}] jobs={len(jobs)} "
                      f"first_code_present={bool(jobs[0].get('code')) if isinstance(jobs[0], dict) else False}")
                return True
        page.wait_for_timeout(500)
    print(f"[{label}] no successful list response within {wait_s}s "
          f"(observed {len(matched)} list responses)")
    return False


def main() -> int:
    profile = PROFILE
    ok1 = False
    ok2 = False
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            PROFILE, headless=False, viewport={"width": 1440, "height": 900})
        try:
            ok1 = _observe_list(context, "First Run", RUN1_WAIT_S)
        finally:
            context.close()
    if ok1:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                PROFILE, headless=False, viewport={"width": 1440, "height": 900})
            try:
                cookies = context.cookies(ORIGIN)
                preserved = any(c["name"] == "_nano_fp" for c in cookies)
                print(f"\n[Second Run] session_preserved(nano_fp)={preserved}")
                ok2 = _observe_list(context, "Second Run", RUN2_WAIT_S)
            finally:
                context.close()
    print(f"\nPOC RESULT: {'PASS' if ok1 and ok2 else 'FAIL'}")
    return 0 if ok1 and ok2 else 1


if __name__ == "__main__":
    sys.exit(main())
