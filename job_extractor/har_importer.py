"""N6.1: generic HAR ingestion — recover job records from a user-exported HAR.

The importer is a pure offline parser: it never re-plays requests and never
contacts the source. Candidate job-list responses are identified with the
existing ``score_list`` scorer (no hardcoded site endpoints), so any credible
job-bearing list API in the HAR can be ingested. When one logical page was
requested several times (e.g. a rate-limited attempt followed by a success
after the user completed the site's own verification), the best response
wins: 2xx > success != false > non-empty list > scorer-credible > latest.

Security: cookies, full request headers, full postData, authorization and
token values are never retained. Audit rows only carry page/pageSize/status/
success/errorCode/list_count/selected_for_page plus optional decode notes.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from job_extractor.company_identity import resolve_collection_company
from job_extractor.discovery.network_analyzer import get_path
from job_extractor.discovery.scorer import confidence, score_list
from job_extractor.field_semantics import canonical, canonical_jd, pick_jd_fields
from job_extractor.job_normalize import normalized_jd_halves, normalized_job_fields
from job_extractor.models import CollectionMetrics, CollectionResult, DataCompleteness, Job

# Ordered stable-id candidates: structural role names only. "code" is a
# common public-recruiting stable id alias (same set manual cURL uses).
_ID_ALIASES = ("id", "jobid", "job_id", "positionid", "position_id",
               "postid", "postingid", "requisitionid", "demandcode", "code")
_PAGE_ALIASES = ("page", "pageindex", "pageno", "page_number", "currentpage")
_SIZE_NAMES = ("pagesize", "page_size", "limit", "size", "perpage")
_UPDATE_NAMES = ("updatetime", "updatetimestamp", "modifytime", "lastmodifytime")


def decode_body(response: dict) -> tuple[Any, str | None]:
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


def _pick_page_row(params: dict) -> tuple[int | None, int | None]:
    lookup = {canonical(key): value for key, value in params.items()}
    page = next((lookup[name] for name in _PAGE_ALIASES if name in lookup), None)
    size = next((lookup[name] for name in _SIZE_NAMES if name in lookup), None)
    def _int(value: Any):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return _int(page), _int(size)


def _request_params(request: dict) -> dict:
    args: dict[str, Any] = {}
    query = urlsplit(request.get("url") or "").query
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key not in args:
            args[key] = value
    text = (request.get("postData") or {}).get("text") or ""
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        args.update(parsed)
    return args


def _stable_id(record: dict, fields: list[str]) -> tuple[str | None, str | None]:
    lookup = {canonical(name): name for name in fields}
    for alias in _ID_ALIASES:
        key = lookup.get(alias)
        if key and record.get(key) not in (None, ""):
            return str(record[key]), key
    inferred = None
    if isinstance(record, dict):
        for key, value in record.items():
            if value not in (None, "") and canonical(key).endswith("code"):
                inferred = key
                break
    return (str(record[inferred]), inferred) if inferred else (None, None)


@dataclass
class _Candidate:
    index: int
    page: int | None
    page_size: int | None
    status: int | None
    body: Any
    jobs: list[Any]
    total: int | None
    url: str
    error: str | None = None
    success: bool | None = None
    error_code: int | None = None
    list_count: int = 0
    id_field: str | None = None
    title_field: str | None = None

    @property
    def status_ok(self) -> bool:
        return isinstance(self.status, int) and 200 <= self.status < 300

    @property
    def usable(self) -> bool:
        return bool(self.status_ok and self.success is not False and self.jobs)

    def rank(self) -> tuple:
        return (1 if self.usable else 0, 1 if self.status_ok else 0,
                self.list_count, self.index)


@dataclass
class HarImportResult:
    result: CollectionResult
    audit: list[dict] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)


def _candidate_for_entry(index: int, entry: dict) -> _Candidate | None:
    request = entry.get("request") or {}
    url = request.get("url") or ""
    response = entry.get("response") or {}
    status = response.get("status")
    body, error = decode_body(response)
    params = _request_params(request)
    page, page_size = _pick_page_row(params)
    candidate = _Candidate(index=index, page=page, page_size=page_size,
                           status=status, body=body, jobs=[], total=None, url=url,
                           error=error)
    if not isinstance(body, dict):
        return candidate
    if isinstance(body.get("success"), bool):
        candidate.success = body["success"]
    for name in ("errorCode", "error_code", "errCode"):
        if isinstance(body.get(name), int):
            candidate.error_code = body[name]
            break
    score, _evidence, shape = score_list(url, body)
    path = shape.get("candidate_list_path")
    records = get_path(body, path) if path else None
    if not isinstance(records, list):
        return candidate
    # Credibility gate: the shared scorer must call this a HIGH-confidence
    # job source without rejection reasons. Score-10+ is also accepted only
    # when a stable id + title can be resolved, so config/reference payloads
    # are never imported even when they happen to contain an array.
    fields = list(shape.get("sample_field_names", []))
    id_value, _field = _stable_id(records[0], fields) if records else (None, None)
    title_field = shape.get("inferred_job_title_field")
    credible = (confidence(score) == "HIGH" and not shape.get("rejection_reasons")) or (
        score >= 10 and not shape.get("rejection_reasons")
        and id_value is not None and title_field is not None)
    if not credible or title_field is None or id_value is None:
        return candidate
    candidate.jobs = records
    candidate.list_count = len(records)
    found_value, found_field = _stable_id(records[0], fields)
    candidate.id_field = found_field
    candidate.title_field = title_field
    holder = body.get(path.split(".")[0]) if path and "." in path else body
    if not isinstance(holder, dict):
        holder = body.get("result") if isinstance(body.get("result"), dict) else body
    raw_total = holder.get("total") if isinstance(holder, dict) else None
    if isinstance(raw_total, str) and raw_total.strip().isdigit():
        raw_total = int(raw_total.strip())
    candidate.total = raw_total if isinstance(raw_total, int) else None
    return candidate


def import_har(path: str | Path, *, source_url: str | None = None,
               platform: str = "har_import") -> HarImportResult:
    har = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    entries = (har.get("log") or {}).get("entries") or []

    candidates: list[_Candidate] = []
    malformed = 0
    for index, entry in enumerate(entries):
        candidate = _candidate_for_entry(index, entry)
        if candidate is None:
            continue
        if candidate.error and candidate.body is None:
            malformed += 1
        if isinstance(candidate.body, dict) and candidate.error == "HAR_RESPONSE_JSON_INVALID":
            malformed += 1
        candidates.append(candidate)

    best: dict[Any, _Candidate] = {}
    for candidate in candidates:
        key = candidate.page if candidate.page is not None else f"index{candidate.index}"
        current = best.get(key)
        if current is None or candidate.rank() > current.rank():
            best[key] = candidate

    matched = len(candidates)
    usable = sum(1 for candidate in candidates if candidate.usable)
    failed = matched - usable
    seen: set[str] = set()
    jobs: list[Job] = []
    raw_rows = 0
    totals: set[int] = set()
    audit: list[dict] = []
    for key in best:
        candidate = best[key]
        page = candidate.page
        audit.append({"page": page, "pageSize": candidate.page_size,
                      "status": candidate.status, "success": candidate.success,
                      "errorCode": candidate.error_code,
                      "list_count": candidate.list_count if candidate.jobs else None,
                      "selected_for_page": bool(candidate.jobs)})
        if not candidate.jobs:
            continue
        records = candidate.jobs
        id_field = candidate.id_field
        if not id_field:
            audit.append({"page": candidate.page, "pageSize": candidate.page_size,
                          "status": candidate.status,
                          "success": candidate.success,
                          "errorCode": candidate.error_code,
                          "list_count": candidate.list_count,
                          "selected_for_page": False,
                          "note": "STABLE_ID_FIELD_MISSING"})
            continue
        title_field = candidate.title_field
        base = urlsplit(candidate.url)
        origin = f"{base.scheme}://{base.netloc}{base.path}"
        for record in records:
            raw_rows += 1
            if isinstance(record, dict):
                job_id = (str(record[id_field]) if id_field else None)
                title = record.get(title_field) if title_field else None
            else:
                job_id, title = None, None
            if not job_id or job_id in seen or not title:
                continue
            seen.add(job_id)
            payload = normalized_job_fields(record, None)
            if payload.get("publish_date") is None:
                for name in ("updateTime", "updateTimestamp", "lastModifyTime"):
                    if isinstance(record.get(name), str) and record[name].strip():
                        payload["publish_date"] = record[name].strip()
                        break
            # JD enrichment uses the shared list-record path only (same
            # semantics as the cURL collector): when the list record carries
            # a full JD it is filled; otherwise the job stays JD-less and
            # completeness metrics say so. Never a HAR-specific JD branch.
            jd, _jd_state, _jd_resp, _jd_req = canonical_jd(record)
            resp_items: list[str] = []
            req_items: list[str] = []
            if jd:
                picked = pick_jd_fields(record)
                resp_items, req_items = normalized_jd_halves(record, None, picked)
            jobs.append(Job(job_id=job_id, job_title=str(title),
                            **payload, responsibilities=resp_items,
                            requirements=req_items, full_jd=jd,
                            source_url=(source_url or origin),
                            raw_data={"list": record}))
        if candidate.total is not None:
            totals.add(candidate.total)

    metrics_dict = {
        "har_entries": len(entries),
        "matched_candidate_responses": matched,
        "usable_responses": usable,
        "pages_observed": sum(1 for candidate in best.values() if candidate.jobs),
        "failed_responses": failed,
        "malformed_json_responses": malformed,
        "raw_rows": raw_rows,
        "unique_jobs": len(seen),
        "duplicate_jobs": max(0, raw_rows - len(seen)),
    }
    total = sorted(totals)[0] if len(totals) == 1 else None
    # N9.10 site-level company identity via the shared resolver: job-level
    # explicit company > trusted alias registry / generic domain brand
    # inference > None. No per-site branch here; Job.company is untouched.
    site_url = source_url or next((c.url for c in best.values()), "")
    company = resolve_collection_company(jobs, None, site_url)
    jd_complete = sum(1 for job in jobs if job.full_jd)
    if jobs and jd_complete == len(jobs):
        jd_strategy = "LIST_SUFFICIENT"
    elif jd_complete:
        jd_strategy = "MIXED"
    else:
        # N9 Final: HAR import is the explicit Browser Assist / anti-bot path
        # by construction (cURL pagination was rejected, so the list was
        # collected in a real browser and exported as HAR). A list record set
        # with no JD at all is a legal List-only completion, not an unknown.
        jd_strategy = "LIST_ONLY"
    collection_metrics = CollectionMetrics(
        pages_requested=len(best), pages_succeeded=metrics_dict["pages_observed"],
        raw_rows=raw_rows, unique_jobs=len(seen),
        duplicate_jobs=metrics_dict["duplicate_jobs"],
        page_size=next((candidate.page_size for candidate in best.values()
                        if candidate.page_size), None),
        jd_strategy=jd_strategy,
        termination_reason="HAR_IMPORT",
        collection_mode=platform.upper())
    result = CollectionResult(
        source_url=source_url or next((urlsplit(c.url).netloc for c in best.values()), ""),
        platform=platform,
        company=company,
        metrics=collection_metrics,
        total_expected=total,
        total_fetched=raw_rows,
        total_unique=len(seen),
        status="COMPLETE" if jobs else "FAILED",
        jobs=jobs,
        data_completeness=DataCompleteness(
            total_jobs=len(jobs), complete_jobs=jd_complete,
            missing_jd_jobs=len(jobs) - jd_complete,
            completeness_ratio=(jd_complete / len(jobs)) if jobs else 1.0,
            jd_total=len(jobs), jd_missing=len(jobs) - jd_complete,
            list_sufficient=jd_complete if jd_strategy == "LIST_SUFFICIENT" else 0,
            source_requirements_absent=len(jobs) - jd_complete),
        warnings=([f"SOURCE_CROSS_PAGE_DUPLICATES raw_total={total} "
                   f"duplicate_records={metrics_dict['duplicate_jobs']}"]
                  if metrics_dict["duplicate_jobs"] else []),
        duplicate_audit={"raw_total": total, "raw_fetched": raw_rows,
                         "unique_jobs": len(seen),
                         "duplicate_records": metrics_dict["duplicate_jobs"],
                         "source_cross_page_duplicates": bool(
                             metrics_dict["duplicate_jobs"])},
        enrichment={"har_import": metrics_dict})
    return HarImportResult(result=result, audit=audit, metrics=metrics_dict)
