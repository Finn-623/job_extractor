"""STEP94: Detail Source Resolver — hard priority chain for missing JDs.

Priority (never reordered, never short-circuited into a user prompt):
  1. LIST_SUFFICIENT — the list record already carries a credible JD.
  2. AUTO_API        — a directly evidenced detail request spec exists.
  3. RENDERED_PAGE   — open the detail URL in a browser, wait for JS render,
                       extract the JD from the final DOM.
  4. USER_CURL       — ask the user for a Detail cURL (interaction only).
  5. LIST_ONLY       — a legal completion state: save the list, do not fail.

``resolve_targets`` is pure classification (no I/O). Execution (browser
rendering) lives in ``render_detail_jobs``; tests inject fake page factories.
URL construction is evidence-driven only: a URL field on the record, the id
swapped into the list URL's own query, a partial path on the record, or a
generic detail-page filename pattern combined with the campaign prefix. No
hostname logic. Construction failure falls through — never guessed further.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_extractor.field_semantics import credible_body
from job_extractor.step94h_debug import append as append_step94h_debug, enabled as step94h_debug_enabled

# ---- ID / URL evidence names (reuses the established field vocabulary) ----
_ID_KEYS = ("postId", "post_id", "jobId", "job_id", "positionId", "position_id",
            "positionCode", "position_code", "id", "recruitmentId")
_URL_KEYS = ("detailUrl", "detail_url", "jobUrl", "job_url", "url", "applyUrl",
             "apply_url", "sourceUrl", "source_url", "link", "href", "path")
# Keys we must never treat as a detail id (request ids, page tokens...).
_ID_EXCLUDE = re.compile(r"(?i)(^|_)(request|trace|token|nonce|timestamp|seed)($|_)")
# Generic detail-page filename patterns (any site) — used only to *construct*
# candidate URLs from a campaign prefix + an id. Order is the probe order.
_DETAIL_PATH_HINTS = ("pb/posDetail.html", "posDetail.html", "pb/positionDetail.html",
                      "positionDetail.html", "jobDetail.html", "jobdetail.html")
# Extra query params carried from the record when constructing candidates
# (e.g. a postType=campus riding along with the postId).
_CARRY_QUERY_KEYS = ("postType", "post_type", "recruitType", "recruit_type", "type")


def _first(record: dict, keys: tuple[str, ...]) -> Any:
    """First non-empty scalar among *keys*, case-insensitive exact names."""
    folded: dict[str, list[str]] = {}
    for key in record:
        if isinstance(key, str):
            folded.setdefault(key.casefold(), []).append(key)
    for key in keys:
        for actual in folded.get(key.casefold(), []):
            value = record[actual]
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return None


def extract_detail_id(record: dict) -> tuple[str | None, str | None]:
    """Return ``(id, id_field)`` for the first usable posting identifier."""
    for key in _ID_KEYS:
        if _ID_EXCLUDE.search(key):
            continue
        value = _first(record, (key,))
        if value is not None:
            return value, key
    return None, None


def extract_detail_url(record: dict) -> str | None:
    """Return a directly usable http(s) detail URL when the record carries one."""
    for key in _URL_KEYS:
        value = _first(record, (key,))
        if value is not None and value.startswith(("http://", "https://")):
            return value
    return None


def _extract_raw_detail_id(raw: Any) -> tuple[str | None, str | None]:
    """Look one nesting level deep for an id in the raw list record."""
    if isinstance(raw, dict):
        job_id, field_name = extract_detail_id(raw)
        if job_id:
            return job_id, field_name
        for child in raw.values():
            if isinstance(child, dict):
                job_id, field_name = extract_detail_id(child)
                if job_id:
                    return job_id, field_name
    return None, None


def _job_id_of(record: dict, raw: Any) -> tuple[str | None, str | None]:
    job_id, id_field = extract_detail_id(record)
    if job_id is None:
        job_id, id_field = _extract_raw_detail_id(raw)
    return job_id, id_field


def _campaign_route_type(campaign_context: str | None, list_url: str) -> str | None:
    """Return an evidenced detail-route campaign enum, never a site name.

    A list record's classification tree (for example ``0/1227/121201``) is
    not a route enum.  Callers may provide a known campaign context; public
    page paths with the same explicit words are equally direct evidence.
    """
    evidence = " ".join(value for value in (campaign_context, urlsplit(list_url).path) if value).casefold()
    if re.search(r"(?:^|[/_\s-])(school|campus)(?:$|[./_\s-])", evidence):
        return "campus"
    if re.search(r"(?:^|[/_\s-])social(?:$|[./_\s-])", evidence):
        return "social"
    if re.search(r"(?:^|[/_\s-])intern(?:$|[./_\s-])", evidence):
        return "intern"
    return None


def _classification_path(value: str | None) -> bool:
    """True for record-taxonomy paths, which cannot be detail route enums."""
    return bool(value and re.fullmatch(r"\d+(?:/\d+)+", value.strip()))


def extract_campaign_prefix(list_api_url: str) -> str | None:
    """Extract an evidenced tenant/campaign segment from a list API path.

    This is route structure, not a hostname rule: a ``listPosition/<segment>``
    path explicitly supplies the prefix used by the public detail route.
    """
    match=re.search(r"(?:^|/)listPosition/([^/?#]+)",urlsplit(list_api_url).path,re.I)
    return match.group(1) if match else None


def build_detail_url_candidates(list_url: str, record: dict, raw: Any = None,
                                campaign_context: str | None = None,
                                campaign_prefix: str | None = None) -> list[str]:
    """Construct candidate detail URLs from observed structure — evidence only.

    Order (first is most trustworthy):
      1. a full http(s) URL field on the record — used verbatim;
      2. the list URL itself with its id query value swapped for this record's
         id (single-page shells navigate the same URL per job);
      3. a partial detail path on the record (e.g. ``pb/posDetail.html``)
         joined onto the campaign origin, with the id as query;
      4. generic known detail-page filename patterns joined onto the campaign
         path prefix, with the id (and any carry-along query fields) as query.
    Every other URL component is preserved; no site hostname is hardcoded.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            candidates.append(url)

    direct = extract_detail_url(record)
    if direct:
        add(direct)
    job_id, id_field = _job_id_of(record, raw)
    if not job_id:
        return candidates
    parts = urlsplit(list_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    # Keep a same-page-id swap as fall-back evidence.  A known campaign route
    # parameter is more specific and is emitted first below.
    same_page_candidate: str | None = None
    for key, _ in query:
        if _ID_EXCLUDE.search(key):
            continue
        if key.casefold() == "id" or key.casefold() in {name.casefold() for name in _ID_KEYS}:
            same_page_candidate = urlunsplit((parts.scheme, parts.netloc, parts.path,
                                               urlencode([(k, job_id) if k == key else v for k, v in query]),
                                               parts.fragment))
            break
    campaign_type = _campaign_route_type(campaign_context, list_url)
    carry = []
    for key in _CARRY_QUERY_KEYS:
        value = _first(record, (key,))
        # ``postType`` can be a record classification tree rather than the
        # detail page's route enum.  Do not let it outrank campaign context.
        if key.casefold() in {"posttype", "post_type"} and _classification_path(value):
            continue
        if key.casefold() in {"posttype", "post_type"} and campaign_type:
            continue
        if value:
            carry.append((key, value))
    if campaign_type:
        carry.insert(0, ("postType", campaign_type))
    pairs = ([(id_field or "id", job_id)] + carry) if id_field else carry
    # 3 — partial detail path carried on the record.
    for key in _URL_KEYS:
        value = _first(record, (key,))
        if isinstance(value, str) and 0 < len(value) < 200 and not value.startswith(("http://", "https://")):
            if any(hint in value for hint in _DETAIL_PATH_HINTS) or value.endswith(".html"):
                origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
                joiner = "" if value.startswith("/") else "/"
                add(f"{origin}/{joiner}{value.lstrip('/')}?" + urlencode(pairs))
                break
    # 4 — generic known detail-page filename patterns on the campaign prefix.
    if campaign_prefix:
        prefix_path="/"+campaign_prefix.strip("/")+"/"
        prefix=urlunsplit((parts.scheme,parts.netloc,prefix_path,"",""))
    else:
        prefix = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    if not prefix.endswith("/"):
        prefix += "/"
    for hint in _DETAIL_PATH_HINTS:
        add(f"{prefix}{hint}?" + urlencode(pairs))
    if same_page_candidate:
        add(same_page_candidate)
    return candidates


def needs_full_jd(job: Any) -> bool:
    """A job needs detail work only when its list JD is not credible."""
    full = getattr(job, "full_jd", None)
    if full and credible_body(full):
        return False
    resp = getattr(job, "responsibilities", None) or []
    req = getattr(job, "requirements", None) or []
    joined = "\n".join([*resp, *req])
    return not (joined and credible_body(joined))


# ---- resolution model ------------------------------------------------------
METHODS = ("LIST_SUFFICIENT", "AUTO_API", "RENDERED_PAGE", "USER_CURL", "LIST_ONLY")


@dataclass
class DetailTarget:
    job: Any
    method: str                                   # one of METHODS
    detail_urls: list[str] = field(default_factory=list)
    detail_url: str | None = None                 # the URL that actually worked
    id_value: str | None = None
    id_field: str | None = None
    reason: str = ""
    failure_code: str | None = None
    failure_reason: str | None = None
    # Back-reference used only to record execution outcomes.  It is excluded
    # from the public/audit model and never serialised with a Job.
    resolution: Any = field(default=None, repr=False, compare=False)


@dataclass
class DetailResolution:
    targets: list[DetailTarget] = field(default_factory=list)
    detail_url_pattern: str | None = None
    id_field: str | None = None
    list_sufficient: int = 0
    rendered_page_candidates: int = 0
    auto_api_candidates: int = 0
    list_only: int = 0
    # Execution outcomes (filled by the caller after acting on targets).
    rendered_page_attempted: bool = False
    rendered_page_success: int = 0
    rendered_page_failed: int = 0
    auto_api_attempted: bool = False
    auto_api_success: int = 0
    user_curl_attempted: bool = False
    list_only_used: bool = False
    jd_success: int = 0
    jd_failed: int = 0
    jd_missing: int = 0

    @property
    def detail_method(self) -> str:
        """Coarse method label for reports (never internal enum noise)."""
        # An explicit List-only choice is the terminal user-facing method,
        # even when a rendered attempt preceded it.
        if self.list_only_used:
            return "LIST_ONLY"
        # AUTO_API is priority 2: when any job was filled through the direct
        # Detail API it is the primary method (STEP94M priority chain), with
        # rendered-page work only ever a fallback for the remainder.
        if self.auto_api_success:
            return "AUTO_API"
        if self.rendered_page_success:
            return "RENDERED_PAGE"
        if self.rendered_page_attempted:
            return "RENDERED_PAGE"
        if self.list_only_used or self.list_only:
            return "LIST_ONLY"
        return "LIST_SUFFICIENT"

    def audit(self) -> dict:
        """Machine-readable resolution facts (no cookies, no cURL text)."""
        return {
            "detail_method": self.detail_method,
            "detail_url_pattern": self.detail_url_pattern,
            "id_field": self.id_field,
            "list_sufficient": self.list_sufficient,
            "rendered_page_attempted": self.rendered_page_attempted,
            "rendered_page_success": self.rendered_page_success,
            "rendered_page_failed": self.rendered_page_failed,
            "auto_api_attempted": self.auto_api_attempted,
            "auto_api_success": self.auto_api_success,
            "user_curl_attempted": self.user_curl_attempted,
            "list_only_used": self.list_only_used,
            "jd_success": self.jd_success,
            "jd_failed": self.jd_failed,
            "jd_missing": self.jd_missing,
        }


def resolve_targets(jobs: list[Any], list_url: str, raw_by_id: dict[str, Any] | None = None,
                    detail_spec: Any | None = None, campaign_context: str | None = None,
                    campaign_prefix: str | None = None) -> DetailResolution:
    """Classify every job against the hard priority chain.

    ``detail_spec`` is a directly evidenced detail request spec (provider
    strategy / RequestSpec). When present, jobs needing a JD resolve to
    AUTO_API first; without one they resolve to RENDERED_PAGE when URL
    evidence exists, else LIST_ONLY. Open-ended API guessing is out of scope
    by design.
    """
    raw_by_id = raw_by_id or {}
    resolution = DetailResolution()
    pattern_seen: dict[str, int] = {}
    for job in jobs:
        if not needs_full_jd(job):
            resolution.targets.append(DetailTarget(job, "LIST_SUFFICIENT", reason="list JD credible"))
            resolution.list_sufficient += 1
            continue
        record = dict(getattr(job, "raw_data", {}) or {})
        raw = raw_by_id.get(str(getattr(job, "job_id", ""))) if raw_by_id else None
        if raw is None and isinstance(record.get("list"), dict):
            record.update(record["list"])
        candidates = build_detail_url_candidates(list_url, record, raw, campaign_context=campaign_context,
                                                 campaign_prefix=campaign_prefix)
        job_id, id_field = _job_id_of(record, raw)
        if detail_spec is not None and job_id:
            resolution.targets.append(DetailTarget(job, "AUTO_API", [], None, job_id, id_field, "direct detail spec"))
            resolution.auto_api_candidates += 1
            continue
        if candidates:
            resolution.targets.append(DetailTarget(job, "RENDERED_PAGE", candidates, None, job_id, id_field,
                                                   "detail url on record" if candidates[0] == (extract_detail_url(record) or "") else "constructed detail url"))
            resolution.rendered_page_candidates += 1
            pattern_seen[candidates[0]] = pattern_seen.get(candidates[0], 0) + 1
            continue
        resolution.targets.append(DetailTarget(job, "LIST_ONLY", reason="no detail evidence"))
        resolution.list_only += 1
    if pattern_seen:
        resolution.detail_url_pattern = max(pattern_seen, key=pattern_seen.get)
    id_fields = {target.id_field for target in resolution.targets if target.id_field}
    resolution.id_field = min(id_fields) if id_fields else None
    resolution.jd_missing = resolution.list_only
    for target in resolution.targets:
        target.resolution = resolution
    return resolution


# ---- rendered detail page extraction --------------------------------------
_RESP_HEADING = re.compile(r"^\s*(岗位职责|工作职责|职位职责|职责描述|工作内容|主要职责|Responsibilities)\s*$")
_REQ_HEADING = re.compile(r"^\s*(任职要求|任职资格|岗位要求|职位要求|招聘要求|基本要求|Requirements|Qualifications)\s*$")
# Lines that always end the JD body (footer/legal noise on campaign shells).
_STOP_LINE = re.compile(r"^(沪|京|粤|深).{0,20}(备|公安|ICP)|^©|^Copyright|^分享$|^收藏$|^打印$|^返回$|^关闭$", re.I)
# A real rendered shell is normally much larger, but a complete short JD is
# still authoritative.  Section credibility remains the actual readiness
# gate; this merely filters transient loading labels.
_MIN_DOM_TEXT = 80


def extract_jd_from_text(text: str) -> dict:
    """Split rendered page text into responsibilities/requirements blocks.

    Generic: recognizes standard Chinese/English JD headings at line starts
    and stops at footer/legal lines. No site selectors.
    """
    responsibilities: list[str] = []
    requirements: list[str] = []
    bucket: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _STOP_LINE.search(stripped):
            continue
        if _RESP_HEADING.match(stripped):
            bucket = responsibilities
            continue
        if _REQ_HEADING.match(stripped):
            bucket = requirements
            continue
        if bucket is not None:
            bucket.append(stripped)
    return {
        "responsibilities": "\n".join(responsibilities) or None,
        "requirements": "\n".join(requirements) or None,
    }


def _credible_sections(sections: dict) -> bool:
    resp, req = sections.get("responsibilities"), sections.get("requirements")
    if resp and req and (credible_body(resp) or credible_body(req)):
        return True
    # The shared list-body heuristic intentionally has a high threshold to
    # reject teasers.  On a rendered detail page, two labelled substantive
    # sections are sufficient evidence even for concise postings.
    if resp and req and len(resp.strip()) >= 25 and len(req.strip()) >= 25:
        return True
    return bool(credible_body(resp or "") or credible_body(req or ""))


def fetch_rendered_jd(page: Any, url: str, *, timeout_s: float = 20.0,
                      poll_interval_s: float = 0.5,
                      extract_text: Callable[[], str] | None = None,
                      diagnostic: dict | None = None) -> dict:
    """Load *url*, wait for the JS-rendered body, extract the JD from the DOM.

    ``page`` is a Playwright-like page (goto/evaluate). ``extract_text``
    overrides the DOM text source (tests inject fake pages this way).
    Readiness: rendered text length + credible JD sections, polled — never a
    fixed sleep, never a wait past ``timeout_s``.
    """
    extract_text = extract_text or (lambda: page.evaluate("() => document.body ? document.body.innerText : ''"))
    diagnostic = diagnostic if diagnostic is not None else {}
    diagnostic.update(candidate_url=url, navigation_wait="domcontentloaded", navigation_start=time.monotonic(),
                      goto_started=time.monotonic()-diagnostic.get("resolver_started", time.monotonic()))
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:
        diagnostic.update(navigation_elapsed=time.monotonic()-diagnostic["navigation_start"],
                          final_page_url=getattr(page,"url",None), failure_stage="NAVIGATION",
                          failure_code="NAVIGATION_TIMEOUT" if "timeout" in type(exc).__name__.lower() else "PAGE_LOAD_ERROR",
                          failure_reason=type(exc).__name__, exception_class=type(exc).__name__)
        return {"ok": False, "sections": {"responsibilities": None, "requirements": None},
                "elapsed": 0.0, "error": diagnostic["failure_code"]}
    diagnostic.update(navigation_elapsed=time.monotonic()-diagnostic["navigation_start"],
                      final_page_url=getattr(page,"url",None), dom_wait_start=time.monotonic())
    deadline = time.monotonic() + timeout_s
    last_error = ""
    last_text = ""
    while True:
        try:
            text = extract_text()
        except Exception as exc:  # navigation-destroyed context — keep polling
            last_error = type(exc).__name__
            diagnostic["exception_class"] = last_error
        else:
            last_text = text or ""
            diagnostic["body_text_length"] = len(last_text)
            if text and len(text) >= _MIN_DOM_TEXT:
                sections = extract_jd_from_text(text)
                diagnostic.update(body_text_length=len(text), ready_state=(page.evaluate("() => document.readyState") if hasattr(page,"evaluate") else None),
                                  jd_heading_found=bool(_RESP_HEADING.search(text) or _REQ_HEADING.search(text)),
                                  responsibilities_found=bool(sections.get("responsibilities")),
                                  requirements_found=bool(sections.get("requirements")))
                if _credible_sections(sections):
                    diagnostic.update(dom_wait_elapsed=time.monotonic()-diagnostic["dom_wait_start"], failure_stage=None)
                    return {"ok": True, "sections": sections, "elapsed": timeout_s - max(0.0, deadline - time.monotonic())}
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval_s)
    code="DOM_WAIT_TIMEOUT" if not last_error else "JD_EXTRACTION_TIMEOUT"
    try:
        state=page.evaluate("() => ({ready:document.readyState,title:document.title,root:(document.querySelector('#root,#app,[data-reactroot]') || document.body)?.innerText || ''})")
    except Exception:
        state={}
    if not isinstance(state,dict):
        state={}
    diagnostic.update(final_page_url=getattr(page,"url",None), dom_wait_elapsed=time.monotonic()-diagnostic["dom_wait_start"],
                      failure_stage="DOM_WAIT", failure_code=code, failure_reason=last_error or code)
    diagnostic.update(ready_state=state.get("ready"), document_title=state.get("title"),
                      root_text_length=len(state.get("root") or ""),
                      body_text_preview=" ".join(last_text.split())[:300])
    return {"ok": False, "sections": {"responsibilities": None, "requirements": None},
            # Preserve the established public collector code; the optional
            # debug trace carries the more precise DOM_WAIT_TIMEOUT layer.
            "elapsed": timeout_s, "error": "RENDER_TIMEOUT"}


def _apply_sections(job: Any, sections: dict) -> None:
    """Merge extracted JD sections back into a Job (list data preserved)."""
    resp_lines = [line for line in (sections.get("responsibilities") or "").splitlines() if line.strip()]
    req_lines = [line for line in (sections.get("requirements") or "").splitlines() if line.strip()]
    parts = []
    if resp_lines:
        parts.append("岗位职责\n" + "\n".join(resp_lines))
    if req_lines:
        parts.append("任职要求\n" + "\n".join(req_lines))
    if parts:
        job.full_jd = "\n\n".join(parts)
    if resp_lines:
        job.responsibilities = resp_lines
    if req_lines:
        job.requirements = req_lines


def render_detail_jobs(page_factory: Callable[[], Any], targets: list[DetailTarget], *,
                       concurrency: int = 2, timeout_s: float = 12.0,
                       total_timeout_s: float = 25.0,
                       poll_interval_s: float = 0.5,
                       progress_callback: Callable | None = None,
                       debug_context: dict | None = None) -> tuple[int, int]:
    """Execute RENDERED_PAGE targets against a browser page factory.

    Conservative concurrency (default 2, capped at 4). Each worker gets a
    fresh page from ``page_factory``. Candidate URLs are tried per target in
    order; the first URL pattern that yields a JD is promoted so later targets
    probe it first (bounds wasted loads). Returns ``(succeeded, failed)`` and
    emits ``detail`` progress events. Successful JDs are merged into the Job.
    """
    work = [target for target in targets if target.method == "RENDERED_PAGE" and target.detail_urls]
    if not work:
        return 0, 0
    succeeded = failed = done = 0
    lock = threading.Lock()
    # URL templates lock a successful route across jobs even though each job
    # has its own postId value.
    preferred_patterns: list[str] = []

    def pattern(url: str, target: DetailTarget) -> str:
        return url.replace(target.id_value, "{id}") if target.id_value else url

    def ordered(target: DetailTarget) -> list[str]:
        if not preferred_patterns:
            return target.detail_urls
        ranked = [url for key in preferred_patterns for url in target.detail_urls if pattern(url, target) == key]
        return ranked + [url for url in target.detail_urls if url not in ranked]

    def one(target: DetailTarget) -> bool:
        page = None
        diagnostic = dict(debug_context or {})
        ok = False
        if step94h_debug_enabled():
            append_step94h_debug(phase="detail_start", title=getattr(target.job,"job_title",""), post_id=target.id_value,
                                 candidate_url=(target.detail_urls[0] if target.detail_urls else None))
        try:
            factory_started=time.monotonic()
            page = page_factory()
            diagnostic.update(browser_factory=type(page_factory).__name__, browser_launch_elapsed=time.monotonic()-factory_started,
                              resolver_state_id=id(target.resolution), pattern_locked=bool(preferred_patterns))
            diagnostic.update(getattr(page,"step94h_browser",{}) or {})
            deadline = time.monotonic() + max(0.1, total_timeout_s)
            diagnostic["resolver_budget_start"] = deadline-total_timeout_s
            for url in ordered(target):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                outcome = fetch_rendered_jd(page, url, timeout_s=min(timeout_s, remaining), poll_interval_s=poll_interval_s,
                                            diagnostic=diagnostic)
                if outcome["ok"]:
                    sections = outcome["sections"]
                    if sections.get("responsibilities") or sections.get("requirements"):
                        _apply_sections(target.job, sections)
                        target.detail_url = url
                        with lock:
                            key = pattern(url, target)
                            if key in preferred_patterns:
                                preferred_patterns.remove(key)
                            preferred_patterns.insert(0, key)
                        ok = True
                        return True
                if step94h_debug_enabled() and diagnostic.get("failure_code"):
                    append_step94h_debug(phase="detail_exception", title=getattr(target.job,"job_title",""),
                                         post_id=target.id_value, candidate_url=url,
                                         exception_class=diagnostic.get("exception_class"),
                                         timeout_layer=diagnostic.get("failure_code"),
                                         failure_code=diagnostic.get("failure_code"),
                                         failure_reason=diagnostic.get("failure_reason"))
                    append_step94h_debug(phase="page_state", title=getattr(target.job,"job_title",""), post_id=target.id_value,
                                         candidate_url=url, final_page_url=diagnostic.get("final_page_url"),
                                         ready_state=diagnostic.get("ready_state"), body_text_length=diagnostic.get("body_text_length"),
                                         body_text_preview=diagnostic.get("body_text_preview"), root_text_length=diagnostic.get("root_text_length"),
                                         document_title=diagnostic.get("document_title"), console_error_count=diagnostic.get("console_error_count"),
                                         failed_request_count=diagnostic.get("failed_request_count"), failed_requests=diagnostic.get("failed_requests"),
                                         failure_code=diagnostic.get("failure_code"), failure_reason=diagnostic.get("failure_reason"))
                target.failure_code = outcome.get("error") or "DETAIL_NOT_FOUND"
                target.failure_reason = target.failure_code
            if target.failure_code is None:
                target.failure_code = "DETAIL_RESOLVER_BUDGET_EXCEEDED"
                target.failure_reason = target.failure_code
                diagnostic.update(failure_stage="RESOLVER_BUDGET",
                                  failure_code="RESOLVER_BUDGET_TIMEOUT",
                                  failure_reason=target.failure_reason)
            return False
        except BaseException as exc:
            # pytest's deliberate page-factory failure is an OutcomeException
            # (BaseException), and should be a normal automatic-detail miss so
            # the caller can offer Detail cURL/List-only.  Never swallow a
            # genuine process interrupt.
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            target.failure_code = type(exc).__name__
            target.failure_reason = str(exc) or target.failure_code
            diagnostic.update(failure_stage="OTHER", failure_code=target.failure_code,
                              failure_reason=target.failure_reason, exception_class=type(exc).__name__)
            append_step94h_debug(phase="detail_exception", title=getattr(target.job,"job_title",""), post_id=target.id_value,
                                 exception_class=type(exc).__name__, timeout_layer="OTHER_TIMEOUT" if "timeout" in type(exc).__name__.lower() else "OTHER",
                                 failure_code=target.failure_code, failure_reason=target.failure_reason)
            return False
        finally:
            if step94h_debug_enabled():
                append_step94h_debug(phase="detail_end", title=getattr(target.job,"job_title",""), post_id=target.id_value,
                                     candidate_url=target.detail_url, elapsed=round(time.monotonic()-diagnostic.get("resolver_budget_start",time.monotonic()),3),
                                     failure_code=None if ok else target.failure_code, failure_reason=None if ok else target.failure_reason)
            try:
                if page is not None and hasattr(page, "close"):
                    page.close()
            except Exception:
                pass

    workers = max(1, min(concurrency, 4))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, target): target for target in work}
        for future in as_completed(futures):
            target = futures[future]
            ok = bool(future.result())
            with lock:
                done += 1
                if ok:
                    succeeded += 1
                else:
                    failed += 1
                if progress_callback:
                    progress_callback("detail", done=done, total=len(work), ok=succeeded,
                                      fail=failed, title=getattr(target.job, "job_title", ""),
                                      post_id=target.id_value, failure_code=target.failure_code,
                                      failure_reason=target.failure_reason)
                    if debug_context is not None and done <= 2:
                        progress_callback("detail_debug", title=getattr(target.job,"job_title",""), post_id=target.id_value,
                                          detail=diagnostic)
    # Direct callers (including the live CLI) receive the same audit state as
    # callers that wrap this function in Manual cURL orchestration.
    resolutions = {id(target.resolution): target.resolution for target in work if target.resolution is not None}
    for resolution in resolutions.values():
        resolution.rendered_page_attempted = True
        resolution.rendered_page_success += succeeded
        resolution.rendered_page_failed += failed
    return succeeded, failed
