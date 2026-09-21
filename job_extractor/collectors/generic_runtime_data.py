"""Execute a validated BROWSER_RUNTIME_DATA plan.

Plaintext records are read from already-decrypted browser-runtime state or from
each batch captured at the runtime transform boundary while the official
pagination control is driven. No crypto is performed and no secrets are
persisted. COMPLETE is only claimed when the cumulative unique stable IDs equal
the runtime-reported total.

STEP 54B: when the plan carries an organic detail-request contract (observed
at discovery time) and list records lack a complete JD, a bounded detail stage
runs AFTER pagination finishes — never inside the pagination loop. Single
detail failures are isolated per job; the collection itself never aborts.
"""
from __future__ import annotations

import json as _json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from time import perf_counter
from typing import Any

import httpx

from job_extractor.collectors.generic_decoder import decode_detail_payload
from job_extractor.collectors.generic_http import GenericHttpCollector, _merge_detail
from job_extractor.discovery.models import RuntimeJobSource
from job_extractor.discovery.runtime_data import (
    RUNTIME_PAGINATION_SCRIPT,
    build_runtime_source_candidate,
    merge_runtime_batches,
    run_runtime_pagination,
)
from job_extractor.models import CollectionMetrics, CollectionResult
from job_extractor.planning.models import CollectionPlan
from job_extractor.runtime import evaluate_data_completeness, make_error


def valid_detail_contract(plan: CollectionPlan) -> bool:
    """A plan can execute detail fetches only with an evidence-derived contract.

    No contract (endpoint+method missing) or an explicit LIST_SUFFICIENT mode
    means no detail stage — jobs stay honestly incomplete instead of
    fabricating JDs.
    """
    if not plan.detail_endpoint_template or not plan.detail_method:
        return False
    if plan.detail_mode == "LIST_SUFFICIENT":
        return False
    mode = str((plan.detail_decoder or {}).get("mode") or "").upper()
    if mode not in ("", "PLAIN_JSON", "AES_CBC_ENVELOPE"):
        return False
    return True


class DetailHttpClient:
    """Bounded httpx wrapper: per-request timeout, limited retries.

    Retryable: transport/timeouts. HTTP status errors raise immediately.
    Returns (status_code, parsed_json) on success.
    """

    def __init__(self, timeout_seconds: float = 10.0, max_retries: int = 1):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def request(self, method: str, url: str, json_body: dict | None) -> tuple[int, Any | None]:
        last_exc: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.request(method, url, json=json_body)
                    response.raise_for_status()
                    payload = _json.loads(response.text) if response.text else {}
                    return response.status_code, payload
            except httpx.TimeoutException as exc:
                last_exc = exc
            except httpx.HTTPStatusError:
                raise
            except (httpx.TransportError, ValueError) as exc:
                last_exc = exc
        raise last_exc if last_exc else RuntimeError("detail request failed")


def resolve_detail_payload(decoded: Any, result_path: list[str] | None) -> tuple[dict | None, str | None]:
    """Resolve the job record inside a decoded detail response (STEP 54B).

    Transport decoding ends at ``decode_detail_payload``; this helper only
    resolves the business-result layer using the contract's evidence-derived
    path. Strict by design: a missing segment or a non-dict record fails with
    a generic code instead of guessing a fallback location.
    """
    node = decoded
    for segment in (result_path or []):
        if not isinstance(node, dict) or segment not in node:
            return None, "DETAIL_PAYLOAD_PATH_MISSING"
        node = node[segment]
    if not isinstance(node, dict):
        return None, "DETAIL_PAYLOAD_INVALID"
    return node, None


class GenericRuntimeDataCollector:
    def __init__(self, plan: CollectionPlan, browser_factory=None, observer=None, batches=None, enrich_details=True,
                 detail_client=None, detail_concurrency: int = 4, detail_budget: int | None = None,
                 detail_retries: int = 1, detail_timeout: float = 10.0):
        self.plan = plan
        self.browser_factory = browser_factory
        self.observer = observer
        self.batches = batches
        self.enrich_details = enrich_details
        # STEP 54B detail-stage knobs (injectable for tests; defaults bounded).
        self.detail_client = detail_client
        self.detail_concurrency = max(1, min(8, detail_concurrency))
        self.detail_budget = detail_budget
        self.detail_retries = max(0, detail_retries)
        self.detail_timeout = detail_timeout
        self.detail_stats: dict = {}

    def _enrich(self, jobs: dict) -> dict:
        from job_extractor.collectors.detail_enrichment import enrich_job
        stats = {"attempted": 0, "succeeded": 0, "failed": 0, "source_absent": 0, "requirements_filled": 0, "responsibilities_improved": 0, "binding_mismatch": 0, "extraction_failed": 0, "requirements_before": 0, "requirements_after": 0}
        for key, job in list(jobs.items()):
            stats["attempted"] += 1
            if job.requirements:
                stats["requirements_before"] += 1
            enriched, status = enrich_job(job)
            jobs[key] = enriched
            if enriched.requirements:
                stats["requirements_after"] += 1
            if status == "BINDING_MISMATCH":
                stats["failed"] += 1; stats["binding_mismatch"] += 1
            elif status == "EXTRACTION_FAILED":
                stats["failed"] += 1; stats["extraction_failed"] += 1
            elif status == "SOURCE_REQUIREMENTS_ABSENT":
                stats["source_absent"] += 1; stats["succeeded"] += 1
            else:
                stats["succeeded"] += 1
                if status in ("REQUIREMENTS_FILLED", "ENRICHED"):
                    stats["requirements_filled"] += 1
                if status in ("RESPONSIBILITIES_IMPROVED", "ENRICHED"):
                    stats["responsibilities_improved"] += 1
        return stats

    def _source(self) -> RuntimeJobSource:
        data = self.plan.runtime_source or {}
        return RuntimeJobSource(**data)

    # ------------------------------------------------------------------
    # STEP 54B detail bridge (runs after list collection, never inside
    # the pagination loop).
    # ------------------------------------------------------------------
    def _detail_body(self, job_id: str) -> dict:
        """Build the request body from the observed contract: scope values
        verbatim, the job locator replaced with the actual id."""
        template = dict(self.plan.detail_body_template or {})
        id_field = self.plan.detail_id_field or "id"
        template[id_field] = job_id
        return template

    def _detail_identity_ok(self, raw: dict, detail: dict) -> bool:
        """Bind the detail payload to the list record via id (fallback title).

        The comparison uses the RAW record's locator values, not the derived
        identity, so identity extraction cannot cause false rejections.
        """
        from job_extractor.collectors.detail_enrichment import bind_detail
        from job_extractor.models import Job
        probe = Job(job_id=str(raw.get(self.plan.job_id_field or "id") or ""),
                    job_title=str(raw.get(self.plan.job_title_field or "title") or ""),
                    source_url=self.plan.source_url)
        return bind_detail(probe, detail) is None

    def _fetch_detail(self, job_id: str) -> tuple[dict, str | None]:
        """Fetch+decode+resolve one detail payload. Returns (detail, failure_code).

        Failure codes are generic: DETAIL_TIMEOUT / DETAIL_HTTP_ERROR /
        DETAIL_REQUEST_FAILED / DETAIL_DECODE_FAILED / DETAIL_PAYLOAD_PATH_MISSING /
        DETAIL_PAYLOAD_INVALID / DETAIL_EMPTY. The resolved payload — the job
        record itself, not the business wrapper — is what identity checks and
        merges see.
        """
        client = self.detail_client or DetailHttpClient(timeout_seconds=self.detail_timeout, max_retries=self.detail_retries)
        body = self._detail_body(job_id) if str(self.plan.detail_method).upper() == "POST" else None
        try:
            _, payload = client.request(str(self.plan.detail_method).upper(), str(self.plan.detail_endpoint_template), body)
        except httpx.TimeoutException:
            return {}, "DETAIL_TIMEOUT"
        except httpx.HTTPStatusError:
            return {}, "DETAIL_HTTP_ERROR"
        except (httpx.TransportError, ValueError, RuntimeError):
            return {}, "DETAIL_REQUEST_FAILED"
        try:
            decoded = decode_detail_payload(payload, self.plan.detail_decoder, self._runtime_state_for_decoder())
        except Exception:
            return {}, "DETAIL_DECODE_FAILED"
        detail, resolve_code = resolve_detail_payload(decoded, self.plan.detail_result_path)
        if resolve_code:
            return {}, resolve_code
        if not detail:
            return {}, "DETAIL_DECODE_FAILED" if payload else "DETAIL_EMPTY"
        return detail, None

    def _runtime_state_for_decoder(self) -> dict:
        state = self.plan.runtime_source.get("detail_runtime_state") if isinstance(self.plan.runtime_source, dict) else None
        return state if isinstance(state, dict) else {}

    def _run_detail_stage(self, inner: GenericHttpCollector, jobs: dict, raw_by_id: dict) -> dict:
        """Bounded concurrent detail acquisition with per-job error isolation."""
        stats = {"required": 0, "attempted": 0, "succeeded": 0, "failed": 0, "skipped_list_sufficient": 0,
                 "decode_errors": 0, "identity_mismatch": 0, "timeouts": 0, "budget_exhausted": 0,
                 "latencies": [], "failures": {}}
        from job_extractor.field_semantics import needs_detail_fetch
        pending = []
        for job_id, job in jobs.items():
            raw = raw_by_id.get(job_id) or {}
            if needs_detail_fetch(raw, self.plan.detail_mode):
                stats["required"] += 1
                pending.append((job_id, raw))
            else:
                stats["skipped_list_sufficient"] += 1
        if not pending or not valid_detail_contract(self.plan):
            return stats
        budget = self.detail_budget if self.detail_budget is not None else len(pending)
        lock = threading.Lock()

        def one(item):
            job_id, raw = item
            started = perf_counter()
            code = None
            detail = {}
            nonlocal_budget = None
            with lock:
                if stats["attempted"] >= budget:
                    nonlocal_budget = True
                else:
                    stats["attempted"] += 1
            if nonlocal_budget:
                with lock:
                    stats["budget_exhausted"] += 1
                return job_id, None, "BUDGET_EXHAUSTED"
            try:
                detail, code = self._fetch_detail(job_id)
                if code in ("DETAIL_TIMEOUT",):
                    with lock:
                        stats["timeouts"] += 1
                if code in ("DETAIL_EMPTY", "DETAIL_DECODE_FAILED", "DETAIL_PAYLOAD_PATH_MISSING", "DETAIL_PAYLOAD_INVALID"):
                    with lock:
                        stats["decode_errors"] += 1
                if code is None:
                    if not self._detail_identity_ok(raw, detail):
                        code = "DETAIL_IDENTITY_MISMATCH"
                        detail = {}
                        with lock:
                            stats["identity_mismatch"] += 1
            except Exception:
                code = code or "DETAIL_REQUEST_FAILED"
                detail = {}
            latency = perf_counter() - started
            return job_id, detail, code, latency

        results = []
        with ThreadPoolExecutor(max_workers=self.detail_concurrency) as pool:
            futures = [pool.submit(one, item) for item in pending]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception:
                    continue
        merged_jobs = dict(jobs)
        for item in results:
            if len(item) == 3:
                job_id, _, code = item
                with lock:
                    stats["failed"] += 1
                    stats["failures"][job_id] = code
                continue
            job_id, detail, code, latency = item
            with lock:
                stats["latencies"].append(latency)
                if code is not None:
                    stats["failed"] += 1
                    stats["failures"][job_id] = code
                    continue
            # merge into raw record and rebuild the job through the canonical
            # pipeline so full_jd/_jd_state come from existing semantics
            raw = raw_by_id.get(job_id) or {}
            merged_raw = _merge_detail(dict(raw), detail)
            rebuilt = inner._job(merged_raw)
            if rebuilt is None:
                with lock:
                    stats["failed"] += 1
                    stats["failures"][job_id] = "DETAIL_MERGE_FAILED"
                continue
            if not rebuilt.full_jd:
                with lock:
                    stats["failed"] += 1
                    stats["failures"][job_id] = "DETAIL_EMPTY"
                continue
            merged_jobs[job_id] = rebuilt
            with lock:
                stats["succeeded"] += 1
        jobs.clear()
        jobs.update(merged_jobs)
        return stats

    def _run_pagination(self, source: RuntimeJobSource):
        browser = self.browser_factory
        if browser is None:
            from job_extractor.browser import BrowserRuntime as browser  # type: ignore
        try:
            with browser() as runtime:
                page = runtime.page
                page.add_init_script(RUNTIME_PAGINATION_SCRIPT)
                page.goto(self.plan.source_url, wait_until="domcontentloaded")
                for _ in range(20):
                    page.wait_for_timeout(500)
                return run_runtime_pagination(page, source)
        except Exception:
            return None

    def _finish(self, source: RuntimeJobSource, records: list[dict], audit: dict | None, started, clock) -> CollectionResult:
        errors: list[str] = []
        if not records:
            return CollectionResult(source_url=self.plan.source_url, platform="generic", company=self.plan.company, scope=self.plan.scope, status="FAILED", errors=["ENCRYPTED_RUNTIME_UNRESOLVED: no plaintext runtime records observed"], started_at=started, finished_at=datetime.now(), metrics=CollectionMetrics(elapsed_seconds=perf_counter() - clock, jd_strategy="DETAIL_REQUIRED", browser_pages_opened=1))
        derived = self.plan.model_copy(update={"mode": "HTTP_API", "list_method": "GET", "list_path": "$", "job_id_field": source.job_id_field or self.plan.job_id_field, "job_title_field": source.job_title_field or self.plan.job_title_field})
        inner = GenericHttpCollector(derived)
        jobs: dict[str, object] = {}
        raw_by_id: dict[str, dict] = {}
        for raw in records:
            job = inner._job(raw)
            if job is None:
                errors.append(make_error("RUNTIME_RECORD_REJECTED", "record missing stable id or title"))
                continue
            jobs.setdefault(job.job_id, job)
            raw_by_id.setdefault(job.job_id, raw)
        detail_stats: dict = {}
        if jobs and (self.plan.detail_endpoint_template or self.plan.detail_method):
            # STEP 54B: when the plan carries detail-contract fields the detail
            # stage owns the detail metrics — even when it legitimately runs
            # zero requests (LIST_SUFFICIENT gate) — so enrichment counters
            # are never conflated into detail_attempted. Plans with no
            # contract at all keep the legacy enrichment mapping (STEP43).
            try:
                detail_stats = self._run_detail_stage(inner, jobs, raw_by_id)
            except Exception:
                detail_stats = {}
        self.detail_stats = detail_stats
        enrichment = self._enrich(jobs) if (self.enrich_details and jobs) else {}
        total = source.total
        expected = total if isinstance(total, int) else len(records)
        reconciled = isinstance(total, int) and len(jobs) == total
        audit_data = audit or {}
        expected_pages = ((total + (source.limit or total) - 1) // (source.limit or total)
                          if isinstance(total, int) and total > 0 else None)
        paginated = bool(source.pagination_validated or source.pagination_model in ("OFFSET", "PAGE"))
        all_steps_succeeded = (not paginated) or (bool(audit) and audit_data.get("failed_pages", 0) == 0 and audit_data.get("stagnant_pages", 0) == 0)
        if paginated and expected_pages is not None:
            all_steps_succeeded = all_steps_succeeded and audit_data.get("pages_succeeded") == expected_pages
        no_ambiguity = not audit or audit_data.get("ambiguous_records", 0) == 0
        no_budget = not audit or audit_data.get("termination_reason") not in ("BUDGET_EXCEEDED", "MAX_PAGES_EXCEEDED")
        complete = bool(reconciled and all_steps_succeeded and no_ambiguity and no_budget) and not errors and expected > 0
        status = "COMPLETE" if complete else ("INCOMPLETE" if jobs else "FAILED")
        if detail_stats:
            # Bridge ran: metrics come from the real detail stage (no
            # conflation with enrichment counters). details_required is the
            # pre-fetch gate count (needs_detail_fetch before any request) so
            # attempted can be reconciled against required at the same point
            # in time; post-merge state counts would drift downward as
            # successful detail payloads upgrade job records.
            attempted = detail_stats.get("attempted", 0)
            succeeded = detail_stats.get("succeeded", 0)
            failed = detail_stats.get("failed", 0)
            required = detail_stats.get("required", attempted)
            latencies = detail_stats.get("latencies") or []
            avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0
        else:
            attempted = enrichment.get("attempted", 0)
            succeeded = enrichment.get("succeeded", 0)
            failed = enrichment.get("failed", 0)
            required = attempted
            avg_latency = 0.0
        metrics = CollectionMetrics(list_requests=max(1, audit.get("pages_requested", 0)) if audit else 1, list_pages=max(1, audit.get("pages_requested", 0)) if audit else 1, pages_requested=audit.get("pages_requested", 0) if audit else 0, pages_succeeded=audit.get("pages_succeeded", 0) if audit else 0, raw_rows=len(records), unique_jobs=len(jobs), duplicate_jobs=max(0, len(records)-len(jobs)), termination_reason=audit.get("termination_reason") if audit else None, details_attempted=attempted, details_required=required, details_succeeded=succeeded, details_failed=failed, detail_request_seconds=sum(detail_stats.get("latencies") or []) if detail_stats else 0.0, average_detail_request_seconds=avg_latency, jd_strategy="MIXED" if enrichment else "DETAIL_REQUIRED", browser_pages_opened=1, browser_requests_observed=max(1, audit.get("pages_requested", 0)) if audit else 1, elapsed_seconds=perf_counter() - clock)
        result = CollectionResult(source_url=self.plan.source_url, platform="generic", company=self.plan.company, scope=self.plan.scope, total_expected=expected, total_fetched=len(records), total_unique=len(jobs), status=status, jobs=list(jobs.values()), errors=errors, started_at=started, finished_at=datetime.now(), metrics=metrics, enrichment=enrichment)
        if audit:
            result.duplicate_audit = audit
        result.data_completeness = evaluate_data_completeness(result)
        if not complete:
            result.warnings.append(f"PAGINATION_REQUIRED fetched={len(jobs)} total={expected}")
            result.warnings.append("PARTIAL_COLLECTION")
        return result

    def _read_confirmed_path(self, source: RuntimeJobSource) -> tuple[list[dict] | None, str | None]:
        """STEP72: evaluate ONLY the discovery-confirmed runtime path on a live page.

        No full-window rescan: discovery already located and validated the job
        array; execution re-evaluates exactly that expression with a bounded
        hydration poll. Fail-closed returns ([], reason) for missing/non-list
        values; (None, None) only when the plan carries no confirmed path.
        """
        path = self.plan.runtime_source.get("source_path") if isinstance(self.plan.runtime_source, dict) else None
        if not path:
            return None, None
        probe_js = (
            "path => { try { const v = eval(path);"
            " if (v === undefined || v === null) return {kind:'MISSING'};"
            " if (!Array.isArray(v)) return {kind:'NOT_LIST', actual: typeof v};"
            " return {kind:'LIST', value: v}; }"
            " catch (e) { return {kind:'ERROR', message: String(e && e.message || e)}; } }"
        )
        browser = self.browser_factory
        if browser is None:
            from job_extractor.browser import BrowserRuntime as browser  # type: ignore
        try:
            with browser() as runtime:
                page = runtime.page
                page.goto(self.plan.source_url, wait_until="domcontentloaded")
                value = None
                for _ in range(10):
                    value = page.evaluate(probe_js, path)
                    if isinstance(value, dict) and value.get("kind") == "LIST":
                        break
                    page.wait_for_timeout(500)
        except Exception as exc:
            return [], f"RUNTIME_NAVIGATION_FAILED {type(exc).__name__}"
        payload = value if isinstance(value, dict) else {}
        kind = str(payload.get("kind") or "ERROR")
        if kind == "LIST":
            records = [dict(item) for item in (payload.get("value") or []) if isinstance(item, dict)]
            return records, None
        if kind == "MISSING":
            return [], f"RUNTIME_PATH_NOT_FOUND {path}"
        if kind == "NOT_LIST":
            return [], f"RUNTIME_VALUE_NOT_LIST {path} actual={payload.get('actual')}"
        return [], f"RUNTIME_EVAL_ERROR {path} {payload.get('message')}"

    def collect(self) -> CollectionResult:
        started = datetime.now(); clock = perf_counter()
        source = self._source()
        if source.pagination_validated:
            batches = self.batches if self.batches is not None else self._run_pagination(source)
            if not batches:
                return CollectionResult(source_url=self.plan.source_url, platform="generic", company=self.plan.company, scope=self.plan.scope, status="FAILED", errors=["ENCRYPTED_RUNTIME_UNRESOLVED: paginated runtime batches not observed"], started_at=started, finished_at=datetime.now(), metrics=CollectionMetrics(elapsed_seconds=perf_counter() - clock, jd_strategy="DETAIL_REQUIRED", browser_pages_opened=1))
            records, audit = merge_runtime_batches(source, batches)
            return self._finish(source, records, audit, started, clock)
        if self.observer is not None:
            observed = self.observer()
            return self._finish(source, list(observed.records), None, started, clock)
        records, problem = self._read_confirmed_path(source)
        if problem:
            return CollectionResult(source_url=self.plan.source_url, platform="generic", company=self.plan.company, scope=self.plan.scope, status="FAILED", errors=[f"RUNTIME_PATH_FAIL_CLOSED: {problem}"], started_at=started, finished_at=datetime.now(), metrics=CollectionMetrics(elapsed_seconds=perf_counter() - clock, jd_strategy="DETAIL_REQUIRED", browser_pages_opened=1))
        if records is None:
            # No confirmed path on the plan: defensive legacy full-window scan.
            observed = build_runtime_source_candidate(self.plan.source_url, provider=source.provider, capability=source.capability, expected_total=source.total, request_limit=source.limit, request_offset=source.initial_offset, browser_factory=self.browser_factory)
            return self._finish(source, list(observed.records), None, started, clock)
        return self._finish(source, records, None, started, clock)
