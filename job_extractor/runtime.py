from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, TYPE_CHECKING
from urllib.parse import urlparse
from job_extractor.models import CollectionMetrics, CollectionResult, DataCompleteness
if TYPE_CHECKING:
    from job_extractor.adapters.base import BaseAdapter

SECRET_KEYS = re.compile(r"aes|necromancer|cookie|authorization|token|secret|key", re.I)

def make_error(code: str, message: str, **context: Any) -> str:
    safe = []
    for key in sorted(context):
        if not SECRET_KEYS.search(key):
            value = re.sub(r"(?i)(cookie|authorization|token|secret|aes\w*|necromancer)\s*[=:]\s*\S+", r"\1=[REDACTED]", str(context[key]))
            safe.append(f"{key}={value}")
    message = re.sub(r"(?i)(cookie|authorization|token|secret|aes\w*|necromancer)\s*[=:]\s*\S+", r"\1=[REDACTED]", message)
    return " ".join([code, *safe, f"reason={message}"]).strip()

class MetricsRecorder:
    def __init__(self) -> None:
        self.metrics = CollectionMetrics()
        self._start = perf_counter()
    def record_list_request(self) -> None:
        self.metrics.list_requests += 1; self.metrics.pages_requested += 1
    def record_detail_request(self, success: bool | None = None) -> None:
        self.metrics.detail_requests += 1; self.metrics.details_attempted += 1
        if success is True: self.metrics.details_succeeded += 1
        elif success is False: self.metrics.details_failed += 1
    def record_page(self) -> None:
        self.metrics.list_pages += 1; self.metrics.pages_succeeded += 1
    def record_rows(self,raw:int,new_unique:int) -> None:
        self.metrics.raw_rows += raw; self.metrics.unique_jobs += new_unique
        self.metrics.duplicate_jobs += max(0,raw-new_unique)
    def record_retry(self,sleep_seconds:float=0.0) -> None:
        self.metrics.retry_count += 1; self.metrics.retry_sleep_seconds += max(0.0,sleep_seconds)
    def record_list_latency(self,seconds:float) -> None:
        self.metrics.list_request_seconds += max(0.0,seconds)
    def record_detail_latency(self,seconds:float) -> None:
        self.metrics.detail_request_seconds += max(0.0,seconds)
    def set_termination(self,reason:str) -> None:self.metrics.termination_reason=reason
    def set_collection_mode(self,mode:str) -> None:self.metrics.collection_mode=mode
    def set_page_size(self, size: int | None) -> None: self.metrics.page_size = size
    def set_jd_strategy(self, strategy: str) -> None: self.metrics.jd_strategy = strategy  # type: ignore[assignment]
    def finish(self) -> CollectionMetrics:
        self.metrics.elapsed_seconds = perf_counter() - self._start
        if self.metrics.list_requests:
            self.metrics.average_list_request_seconds = self.metrics.list_request_seconds / self.metrics.list_requests
        if self.metrics.detail_requests:
            self.metrics.average_detail_request_seconds = self.metrics.detail_request_seconds / self.metrics.detail_requests
        return self.metrics

def evaluate_collection_status(*, list_started: bool, expected: int | None, unique: int,
        errors: list[str], missing_jd: int = 0, detail_failures: int = 0,
        scope_leak: bool = False, max_pages: bool = False) -> Literal["COMPLETE","INCOMPLETE","FAILED"]:
    if not list_started or expected is None: return "FAILED"
    # Source-null fields describe official data quality, not collection failure.
    bad = errors or detail_failures or scope_leak or max_pages or unique != expected
    return "INCOMPLETE" if bad else "COMPLETE"

def build_output_filename(url: str, timestamp: datetime) -> str:
    host = urlparse(url).hostname or "jobs"
    safe = re.sub(r"[^A-Za-z0-9]+", "_", host).strip("_") or "jobs"
    return f"{safe}_{timestamp:%Y%m%d_%H%M%S}.json"

def _scope(adapter: BaseAdapter) -> dict[str, Any]:
    scope = getattr(adapter, "scope", None)
    if hasattr(scope, "business_type"):
        return {"business_type": scope.business_type, "category": list(scope.categories)}
    if hasattr(scope, "org_id"):
        return {"recruitment_type": scope.mode, "org_id": scope.org_id, "site_id": scope.site_id}
    if hasattr(scope, "website_id"):
        return {"recruitment_type": scope.recruitment_type, "website_id": scope.website_id,
                "website_path": scope.website_path, "process_type": scope.process_type}
    return {}

def _metrics(adapter: BaseAdapter) -> CollectionMetrics:
    recorder=MetricsRecorder()
    for _ in range(getattr(adapter,"list_requests",0)): recorder.record_list_request()
    for _ in range(getattr(adapter,"page_count",0)): recorder.record_page()
    succeeded=getattr(adapter,"details_succeeded",0); failed=getattr(adapter,"details_failed",0)
    for _ in range(succeeded): recorder.record_detail_request(True)
    for _ in range(failed): recorder.record_detail_request(False)
    # Attempts without an HTTP request (for example a missing job id) remain
    # visible while detail_requests continues to mean actual detail calls.
    recorder.metrics.details_attempted=getattr(adapter,"details_attempted",succeeded+failed)
    recorder.metrics.detail_requests=getattr(adapter,"details_attempted",succeeded+failed)
    recorder.set_page_size(getattr(adapter,"negotiated_page_size",getattr(adapter,"page_size",None)))
    recorder.set_jd_strategy(getattr(adapter,"detail_strategy","UNKNOWN"))
    metrics=recorder.finish(); metrics.elapsed_seconds=getattr(adapter,"elapsed_seconds",metrics.elapsed_seconds)
    metrics.browser_pages_opened=getattr(adapter,"browser_pages_opened",0)
    metrics.browser_requests_observed=getattr(adapter,"browser_requests_observed",0)
    for name in ("initial_load_seconds","list_pagination_seconds","detail_fallback_seconds","normalize_seconds"):
        setattr(metrics,name,getattr(adapter,name,0.0))
    source_metrics=getattr(getattr(adapter,"recorder",None),"metrics",None)
    for name in ("pages_requested","pages_succeeded","raw_rows","unique_jobs","duplicate_jobs","retry_count","retry_sleep_seconds","list_request_seconds","detail_request_seconds","average_list_request_seconds","average_detail_request_seconds","termination_reason","collection_mode","max_concurrency_observed"):
        if source_metrics is not None:setattr(metrics,name,getattr(source_metrics,name,getattr(metrics,name)))
    return metrics

def evaluate_data_completeness(result: CollectionResult) -> DataCompleteness:
    total=len(result.jobs)
    missing_jd=sum(not j.full_jd for j in result.jobs)
    missing_req=sum(not j.requirements for j in result.jobs)
    missing_resp=sum(not j.responsibilities for j in result.jobs)
    # STEP 51: a full JD body present at the source means structured
    # requirement/responsibility fields simply do not exist there — the JD is
    # still complete. Only a job with no usable JD text is source_incomplete.
    def _meaningful_jd(job: Any) -> bool:
        state = job.raw_data.get("_jd_state") if isinstance(job.raw_data, dict) else None
        if state in ("FULL_TEXT", "SPLIT"):
            return True
        # ``SUMMARY`` is an explicit recognition result: the source body was
        # present but did not meet the generic credible-JD threshold.  A
        # normalized responsibilities projection must not turn that teaser
        # into a fake complete JD.
        if state == "SUMMARY":
            return False
        jd = job.full_jd or ""
        return len(jd) >= 160 or bool(job.requirements) or bool(job.responsibilities)
    jd_complete=sum(bool(j.full_jd) and _meaningful_jd(j) for j in result.jobs)
    jd_incomplete=total-jd_complete
    # This diagnostic describes the source schema, not the normalized
    # ``requirements`` projection.  A whole-JD body can be split into
    # convenience columns by the collector while the source still had no
    # independent requirements field; that remains SOURCE_REQUIREMENTS_ABSENT
    # and must not make an otherwise complete JD incomplete.
    source_requirements_absent=sum(
        bool(j.full_jd) and _meaningful_jd(j)
        and not (isinstance(j.raw_data, dict)
                 and j.raw_data.get("_jd_source_fields", {}).get("requirements"))
        for j in result.jobs)
    source_incomplete=jd_incomplete
    metrics=getattr(result,"metrics",None)
    return DataCompleteness(total_jobs=total,complete_jobs=total-missing_jd,
        missing_jd_jobs=missing_jd,missing_requirements_jobs=missing_req,
        missing_responsibilities_jobs=missing_resp,source_incomplete_jobs=source_incomplete,
        completeness_ratio=round((total-missing_jd)/total,6) if total else 1.0,
        jd_complete=jd_complete,jd_incomplete=jd_incomplete,
        list_sufficient=sum(isinstance(j.raw_data,dict) and j.raw_data.get("_jd_state") in ("FULL_TEXT","SPLIT") for j in result.jobs),
        # STEP 54B: required = pre-fetch gate count from the detail stage when
        # available; post-merge state counts drift downward as detail payloads
        # upgrade job records (the 7-vs-182 semantics drift).
        detail_required=getattr(metrics,"details_required",0) if metrics else sum(isinstance(j.raw_data,dict) and j.raw_data.get("_jd_state") in ("SUMMARY","ABSENT") for j in result.jobs),
        detail_attempted=getattr(metrics,"details_attempted",0) if metrics else 0,
        detail_succeeded=getattr(metrics,"details_succeeded",0) if metrics else 0,
        detail_failed=getattr(metrics,"details_failed",0) if metrics else 0,
        source_requirements_absent=source_requirements_absent)

def finalize_result(result: CollectionResult, adapter: BaseAdapter) -> CollectionResult:
    result.scope = _scope(adapter); result.metrics = _metrics(adapter)
    missing = sum(not j.full_jd for j in result.jobs)
    result.data_completeness=evaluate_data_completeness(result)
    if result.data_completeness.source_incomplete_jobs:
        result.warnings=[f"SOURCE_DATA_INCOMPLETE count={result.data_completeness.source_incomplete_jobs} missing_jd={result.data_completeness.missing_jd_jobs} missing_requirements={result.data_completeness.missing_requirements_jobs} missing_responsibilities={result.data_completeness.missing_responsibilities_jobs}"]
    leak = any(e.startswith("SCOPE_LEAK") for e in result.errors)
    limit = any(e.startswith("PAGINATION_LIMIT") for e in result.errors)
    result.status = evaluate_collection_status(list_started=result.metrics.list_requests>0,
        expected=result.total_expected, unique=result.total_unique, errors=result.errors,
        missing_jd=missing, detail_failures=result.metrics.details_failed, scope_leak=leak, max_pages=limit)
    return result

def collect_url(url: str, adapter_class: type[BaseAdapter]) -> tuple[BaseAdapter, CollectionResult]:
    adapter=adapter_class(); return adapter, finalize_result(adapter.collect(url),adapter)

def render_result(result: CollectionResult, adapter_name: str, output_path: Path) -> str:
    scope="\n".join(f"  {k}: {v}" for k,v in result.scope.items()) or "  (none)"
    m=result.metrics
    mode="\nCollection mode: browser" if result.platform=="feishu" else ""
    dc=result.data_completeness; warning=f"\nWarnings: {'; '.join(result.warnings)}\n" if result.warnings else ""
    return f"Detected platform: {result.platform}\nAdapter: {adapter_name}{mode}\n\nScope:\n{scope}\n\nExpected: {result.total_expected}\nFetched: {result.total_fetched}\nUnique: {result.total_unique}\nRaw rows: {m.raw_rows}\nDuplicates: {m.duplicate_jobs}\n\nPages: {m.list_pages}\nPages requested/succeeded: {m.pages_requested}/{m.pages_succeeded}\nList requests: {m.list_requests} (avg {m.average_list_request_seconds:.3f}s)\nDetail requests: {m.detail_requests} (avg {m.average_detail_request_seconds:.3f}s)\nRetries: {m.retry_count} (sleep {m.retry_sleep_seconds:.3f}s)\nCollection mode: {m.collection_mode}\nTermination: {m.termination_reason}\nJD strategy: {m.jd_strategy}\n\nElapsed: {m.elapsed_seconds:.2f}s\nCollection status: {result.status}\n\nData completeness:\n  Complete JD: {dc.complete_jobs} / {dc.total_jobs}\n  Missing JD at source: {dc.missing_jd_jobs}\n  Completeness: {dc.completeness_ratio:.1%}\n{warning}\nOutput:\n{output_path}"
