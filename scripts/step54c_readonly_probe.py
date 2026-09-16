"""Read-only STEP 54C source-stability probe; no production mutations."""
from __future__ import annotations

import json
from pathlib import Path

from job_extractor.discovery import GenericApiDetector
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "step54c_readonly"
SITES = (("naura", "https://career.naura.com/campus/jobs", 3),
         ("hisense", "https://jobs.hisense.com/campus/jobs", 5))


def run_once(site: str, url: str, index: int) -> dict:
    result = GenericApiDetector().discover(url)
    plan = CollectionPlanBuilder().build(result)
    validation = CollectionPlanValidator().validate(plan)
    network = result.network_summary
    row = {
        "run": index,
        "source_found": bool(result.probable_list_api or result.runtime_source),
        "status": result.status,
        "page_type": result.page_type,
        "provider": result.provider_fingerprint.provider if result.provider_fingerprint else "UNKNOWN",
        "confidence": result.probable_list_api.confidence if result.probable_list_api else (result.runtime_source.confidence if result.runtime_source else "LOW"),
        "candidate_api_count": len(result.candidate_list_apis),
        "probable_list_api": result.probable_list_api.url if result.probable_list_api else None,
        "runtime_source": result.runtime_source.model_dump(mode="json") if result.runtime_source else None,
        "warnings": result.warnings,
        "terminal_mode": bool(result.terminal_trace),
        "terminal_reason": result.terminal_reason,
        "elapsed_seconds": result.elapsed_seconds,
        "network": network.model_dump(mode="json"),
        "plan_generated": plan.mode != "UNSUPPORTED",
        "plan_executable": plan.executable,
        "plan_valid": validation.valid,
        "plan_errors": validation.errors,
    }
    folder = OUT / site
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"discovery_run_{index}.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (folder / f"plan_run_{index}.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return row


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}
    for site, url, attempts in SITES:
        report[site] = [run_once(site, url, index) for index in range(1, attempts + 1)]
    (OUT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
