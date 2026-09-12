"""Execute a validated BROWSER_RUNTIME_DATA plan.

Plaintext records are read from already-decrypted browser-runtime state or from
each batch captured at the runtime transform boundary while the official
pagination control is driven. No crypto is performed and no secrets are
persisted. COMPLETE is only claimed when the cumulative unique stable IDs equal
the runtime-reported total.
"""
from __future__ import annotations

from datetime import datetime
from time import perf_counter

from job_extractor.collectors.generic_http import GenericHttpCollector
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


class GenericRuntimeDataCollector:
    def __init__(self, plan: CollectionPlan, browser_factory=None, observer=None, batches=None, enrich_details=True):
        self.plan = plan
        self.browser_factory = browser_factory
        self.observer = observer
        self.batches = batches
        self.enrich_details = enrich_details

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
        for raw in records:
            job = inner._job(raw)
            if job is None:
                errors.append(make_error("RUNTIME_RECORD_REJECTED", "record missing stable id or title"))
                continue
            jobs.setdefault(job.job_id, job)
        enrichment = self._enrich(jobs) if (self.enrich_details and jobs) else {}
        total = source.total
        expected = total if isinstance(total, int) else len(records)
        reconciled = isinstance(total, int) and len(jobs) == total
        exhaustion = bool(audit and audit.get("batches") and source.limit and audit["batches"][-1].get("plaintext_count", source.limit) < source.limit and isinstance(total, int) and len(jobs) == total)
        complete = bool(reconciled or exhaustion) and not errors and expected > 0
        status = "COMPLETE" if complete else ("INCOMPLETE" if jobs else "FAILED")
        metrics = CollectionMetrics(list_requests=max(1, len(audit.get("batches") or [])) if audit else 1, list_pages=max(1, len(audit.get("batches") or [])) if audit else 1, details_attempted=enrichment.get("attempted", 0), details_succeeded=enrichment.get("succeeded", 0), details_failed=enrichment.get("failed", 0), jd_strategy="MIXED" if enrichment else "DETAIL_REQUIRED", browser_pages_opened=1, browser_requests_observed=max(1, len(audit.get("batches") or [])) if audit else 1, elapsed_seconds=perf_counter() - clock)
        result = CollectionResult(source_url=self.plan.source_url, platform="generic", company=self.plan.company, scope=self.plan.scope, total_expected=expected, total_fetched=len(records), total_unique=len(jobs), status=status, jobs=list(jobs.values()), errors=errors, started_at=started, finished_at=datetime.now(), metrics=metrics, enrichment=enrichment)
        if audit:
            result.duplicate_audit = audit
        result.data_completeness = evaluate_data_completeness(result)
        if not complete:
            result.warnings.append(f"PAGINATION_REQUIRED fetched={len(jobs)} total={expected}")
        return result

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
        observed = build_runtime_source_candidate(self.plan.source_url, provider=source.provider, capability=source.capability, expected_total=source.total, request_limit=source.limit, request_offset=source.initial_offset, browser_factory=self.browser_factory)
        return self._finish(source, list(observed.records), None, started, clock)
