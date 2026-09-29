"""N3 POC: PDD social list via the site's own browser session.

Proves the gating hypothesis: with one real headed browser session (user may
complete the site's normal verification once), the official page itself
generates a fresh ``anti_content`` for every ``POST /api/recruit/position/list``
request, so consecutive pages can be captured natively — no request replay,
no token reuse, no captcha handling.

Security: never prints cookies, full ``anti_content`` or auth tokens; only
presence/length/sha256-prefix of ``anti_content``.
"""
from __future__ import annotations

import hashlib
import sys
import time
from urllib.parse import urlsplit

from job_extractor.browser import BrowserRuntime, BrowserRuntimeError

TARGET = "https://careers.pddglobalhr.com/jobs"
LIST_PATH = "/api/recruit/position/list"
HUMAN_WAIT_S = 120
PAGE_WAIT_MS = 30_000
REQUIRED_PAGES = 3


def _matches(response) -> bool:
    request = response.request
    return (urlsplit(response.url).path == LIST_PATH
            and request.method == "POST"
            and "json" in (response.headers.get("content-type") or "").lower())


def _page_of(response):
    body = response.request.post_data_json
    return body.get("page") if isinstance(body, dict) else None


def _anti_meta(request) -> str:
    body = request.post_data_json
    token = body.get("anti_content") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        return "anti_content present=False"
    digest = hashlib.sha256(token.encode("utf-8", "ignore")).hexdigest()[:8]
    return f"anti_content present=True len={len(token)} sha256={digest}"


def _payload_of(response):
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("list"), list) \
            or not payload["list"] or not isinstance(payload.get("total"), int):
        return None
    return payload


def _fail(stage: str, reason: str) -> int:
    print(f"POC RESULT: FAIL\nfailure_stage: {stage}\nfailure_reason: {reason}")
    return 1


def _report(response, payload, seen_ids: set[str], label) -> tuple[str, ...]:
    jobs = [record for record in payload["list"] if isinstance(record, dict)]
    page_codes = [str(record.get("code")) for record in jobs if record.get("code")]
    if not page_codes:
        raise _PocFailure("CODE_FIELD_MISSING", "no stable code field in list records")
    if len(page_codes) != len(set(page_codes)):
        raise _PocFailure("DUPLICATE_CODES_IN_PAGE", "codes repeat within one page")
    before = len(seen_ids)
    seen_ids.update(page_codes)
    print(f"\nPage {label}")
    print(f"jobs: {len(jobs)}")
    print(f"total: {payload['total']}")
    print(f"unique: {before + len(page_codes)} -> {len(seen_ids)}")
    print(_anti_meta(response.request))
    return page_codes


class _PocFailure(Exception):
    def __init__(self, stage: str, reason: str):
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


def main() -> int:
    print("PDD Browser POC")
    try:
        runtime = BrowserRuntime(headless=False)
    except BrowserRuntimeError as exc:
        return _fail("BROWSER_OPEN_FAILED", str(exc))
    with runtime:
        page = runtime.page
        print("Browser opened")
        matched: list = []
        # Response listener must exist before navigation: hydration can fire
        # the first list request during initial load.
        page.on("response", lambda response: matched.append(response)
                if _matches(response) else None)
        page.goto(TARGET, wait_until="domcontentloaded")
        print("Waiting for valid job response...")

        deadline = time.monotonic() + HUMAN_WAIT_S
        first = None
        while time.monotonic() < deadline and first is None:
            for response in matched:
                payload = _payload_of(response)
                if payload is not None:
                    first = (response, payload)
                    break
            if first is None:
                page.wait_for_timeout(500)
        if first is None:
            print(f"observed {len(matched)} matching responses; diagnostics:")
            for response in matched:
                try:
                    body = response.json()
                    shape = (f"dict keys={sorted(body)[:8]}" if isinstance(body, dict)
                             else f"type={type(body).__name__}")
                except Exception as exc:
                    shape = f"body unreadable ({type(exc).__name__})"
                print(f"  status={response.status} page={_page_of(response)} "
                      f"{_anti_meta(response.request)} body: {shape}")
            return _fail("FIRST_VALID_RESPONSE_TIMEOUT",
                         f"no valid job response within {HUMAN_WAIT_S}s "
                         f"(observed {len(matched)} matching responses)")

        seen_ids: set[str] = set()
        pages: list = []
        responses = [first[0]]
        payloads = [first[1]]
        try:
            for index, (response, payload) in enumerate(zip(responses, payloads), start=1):
                page_no = _page_of(response)
                _report(response, payload, seen_ids, page_no if page_no is not None else index)
                pages.append(page_no)
            while len(payloads) < REQUIRED_PAGES:
                next_page = page.locator(
                    'li[class*="pagination-next"]:not([aria-disabled="true"]):not([class*="disabled"]) a,'
                    'li[class*="pagination-next"]:not([aria-disabled="true"]):not([class*="disabled"]) button,'
                    'li.ant-pagination-next:not(.ant-pagination-disabled) button,'
                    'li.ant-pagination-next:not(.ant-pagination-disabled) a').first
                if next_page.count() == 0:
                    raise _PocFailure("PAGINATION_CONTROL_NOT_FOUND", "no next-page control in DOM")
                try:
                    with page.expect_response(lambda r: _matches(r), timeout=PAGE_WAIT_MS) as info:
                        next_page.click()
                except Exception:
                    raise _PocFailure("PAGE_RESPONSE_TIMEOUT",
                                      f"no new list response within {PAGE_WAIT_MS}ms after next-page click")
                payload = _payload_of(info.value)
                if payload is None:
                    raise _PocFailure("PAGE_PAYLOAD_INVALID",
                                      "new list response was not a valid job payload")
                responses.append(info.value)
                payloads.append(payload)
                page_no = _page_of(info.value)
                _report(info.value, payload, seen_ids, page_no if page_no is not None else len(payloads))
                pages.append(page_no)
                page.wait_for_timeout(300)
        except _PocFailure as failure:
            return _fail(failure.stage, failure.reason)

        known = [value for value in pages if value is not None]
        if len(known) >= 2 and (known != sorted(known) or len(set(known)) != len(known)):
            return _fail("PAGE_ORDER_INVALID", f"page sequence not strictly increasing: {pages}")
        if len(seen_ids) < REQUIRED_PAGES:
            return _fail("NO_UNIQUE_GROWTH", "unique job count did not grow across pages")
        print("\nPOC RESULT: PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
