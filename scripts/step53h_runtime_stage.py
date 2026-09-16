"""Read-only staged validation for the generic browser runtime pagination bridge."""
from __future__ import annotations

import json
import sys

from job_extractor.browser import BrowserRuntime
from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.discovery.runtime_data import (RUNTIME_PAGINATION_SCRIPT, RUNTIME_PAGINATION_SCAN_JS,
    detect_pagination_trigger, merge_runtime_batches, observe_runtime_job_source, run_runtime_pagination)
from job_extractor.planning import CollectionPlanBuilder


def main() -> None:
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    url = "https://app.mokahr.com/campus-recruitment/catlhr/148948#/jobs"
    with BrowserRuntime(timeout_ms=20000) as runtime:
        print("browser-open", flush=True)
        page = runtime.page
        page.add_init_script(RUNTIME_PAGINATION_SCRIPT)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(7000)
        capture = page.evaluate(RUNTIME_PAGINATION_SCAN_JS) or {}
        source = observe_runtime_job_source(page, observed_requests=capture.get("requests"), trigger=detect_pagination_trigger(page))
        print(f"runtime_source={source.pagination_validated}", flush=True)
        if not source.pagination_validated:
            raise RuntimeError(f"runtime pagination unavailable: {source}")
        print("paginate", flush=True)
        batches = run_runtime_pagination(page, source, wait_ms=9000, max_pages=pages, deadline_seconds=120)
    _records, audit = merge_runtime_batches(source, batches)
    plan = CollectionPlanBuilder().build(DiscoveryResult(source_url=url, status="PARTIAL", runtime_source=source))
    result = GenericRuntimeDataCollector(plan, batches=batches, enrich_details=False).collect()
    print(json.dumps({"reported_total": source.total, "effective_page_size": source.limit,
                      "pages_requested": audit["pages_requested"], "pages_succeeded": audit["pages_succeeded"],
                      "audit": audit["batches"] if pages <= 5 else {"first": audit["batches"][:1], "last": audit["batches"][-1:]}, "unique": audit["unique_ids"],
                      "failed_pages": audit["failed_pages"], "stagnant_pages": audit["stagnant_pages"],
                      "termination": audit["termination_reason"], "final_status": result.status,
                      "result_unique": result.total_unique, "result_raw": result.total_fetched}, ensure_ascii=False))


if __name__ == "__main__":
    main()
