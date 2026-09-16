"""STEP 54B read-only diagnostic: run the organic runtime detail probe on AMEC.

No collection, no plan injection. Only observes what the SPA itself requests
when one job route is opened, and what ``detail_contract_from_observation``
derives from that evidence. Writes a diagnostic JSON for the gate review.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.runtime_data import (
    RUNTIME_DETAIL_SCAN_JS,
    observe_runtime_detail_contract,
    observe_runtime_job_source,
)

URL = "https://app.mokahr.com/campus_apply/amec/4362#/jobs?page=1&anchorName=jobsList"
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "step54b_amec" / "diagnostic"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"url": URL}
    with BrowserRuntime(timeout_ms=15000) as runtime:
        page = runtime.page
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        source = observe_runtime_job_source(page, observed_requests=[])
        report["runtime_source"] = {
            "mechanism": source.mechanism,
            "confidence": source.confidence,
            "record_count": source.record_count,
            "job_id_field": source.job_id_field,
            "total": source.total,
        }
        print("runtime source:", report["runtime_source"], flush=True)
        sample_id = next(
            (
                record.get(source.job_id_field)
                for record in source.records
                if source.job_id_field and record.get(source.job_id_field)
            ),
            None,
        )
        report["sample_job_id"] = sample_id
        print("sample job id:", sample_id, flush=True)
        contract = observe_runtime_detail_contract(page, sample_job_id=sample_id, wait_ms=8000)
        report["detail_contract"] = contract
        print("contract:", json.dumps(contract, ensure_ascii=False)[:1200], flush=True)
        raw = page.evaluate(RUNTIME_DETAIL_SCAN_JS)
        if isinstance(raw, dict):
            report["raw_requests"] = raw.get("requests") or []
            report["raw_parsed"] = raw.get("parsed") or []
            report["raw_state"] = raw.get("state") or {}
            print("raw requests:", json.dumps(raw.get("requests"), ensure_ascii=False)[:2500], flush=True)
            print("raw parsed:", json.dumps(raw.get("parsed"), ensure_ascii=False)[:800], flush=True)
            print("raw state:", json.dumps(raw.get("state"), ensure_ascii=False)[:1200], flush=True)
        report["page_url_after_probe"] = page.url
    (OUT / "detail_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("written:", OUT / "detail_probe.json", flush=True)
    return 0 if contract else 1


if __name__ == "__main__":
    sys.exit(main())
