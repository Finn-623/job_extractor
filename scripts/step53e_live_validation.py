"""Read-only STEP 53E live validation; writes only a local redacted report."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from job_extractor.discovery.detector import GenericApiDetector

SITES = {
    "hisense": ("Hisense", "https://jobs.hisense.com/campus/jobs", 5),
    "gzmetro": ("Guangzhou Metro", "https://gzmetro.zhiye.com/campus/jobs", 5),
    "cxmt": ("CXMT", "https://cxmt.zhiye.com/campus/jobs", 3),
    "naura": ("NAURA", "https://career.naura.com/campus/jobs", 3),
}
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "step53e_boot_recovery" / "live_validation.json"


def one_run(company: str, url: str, index: int) -> dict:
    result = GenericApiDetector(timeout_ms=20000, source_budget_seconds=30).discover(url)
    source = result.probable_list_api
    boot = result.network_summary.boot_recovery
    return {
        "company": company, "run": index + 1, "url": url,
        "status": result.status, "source_found": bool(source),
        "endpoint": source.url if source else None,
        "score": source.score if source else None,
        "confidence": source.confidence if source else None,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "observation_policy": result.network_summary.observation_policy,
        "boot_recovery": boot,
        "warnings": result.warnings,
    }


def main() -> None:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--runs=")]
    run_override = next((int(arg.split("=", 1)[1]) for arg in sys.argv[1:] if arg.startswith("--runs=")), None)
    keys = args or list(SITES)
    rows = []
    for key in keys:
        company, url, count = SITES[key.lower()]
        count = run_override if run_override is not None else count
        for index in range(count):
            row = one_run(company, url, index)
            rows.append(row)
            print(json.dumps({k: row[k] for k in ("company", "run", "status", "source_found", "elapsed_seconds", "boot_recovery")}, ensure_ascii=False), flush=True)
    grouped = {}
    for company in {row["company"] for row in rows}:
        subset = [row for row in rows if row["company"] == company]
        grouped[company] = {
            "runs": len(subset), "source_found": sum(row["source_found"] for row in subset),
            "reloads": sum(row["boot_recovery"].get("reload_count", 0) for row in subset),
            "recovered": sum("BOOT_STALL_RECOVERED" in row["warnings"] for row in subset),
            "states": dict(Counter((row["boot_recovery"].get("attempts") or [{}])[-1].get("state", "MISSING") for row in subset)),
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"rows": rows, "summary": grouped}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": grouped, "output": str(OUT)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
