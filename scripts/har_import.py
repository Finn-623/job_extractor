"""N6.1 HAR importer — minimal offline diagnostic entry point.

Usage: python scripts/har_import.py <file.har> [--json]

Pure offline parsing: never contacts the source, never prints request
headers, cookies, postData or any token material. Only sanitized counts
and audit rows (page/pageSize/status/errorCode/list_count) are shown.
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from job_extractor.har_importer import import_har


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline HAR job importer")
    parser.add_argument("har_file")
    parser.add_argument("--json", action="store_true",
                        help="print the metrics/audit JSON instead of a summary")
    args = parser.parse_args()
    try:
        outcome = import_har(args.har_file)
    except Exception as exc:
        print(f"cannot read HAR: {type(exc).__name__}")
        return 1
    result = outcome.result
    metrics = outcome.metrics
    print("HAR Import")
    print(f"har_entries: {metrics['har_entries']}")
    print(f"matched_candidate_responses: {metrics['matched_candidate_responses']}")
    print(f"usable_responses: {metrics['usable_responses']}")
    print(f"failed_responses: {metrics['failed_responses']}")
    print(f"pages_observed: {metrics['pages_observed']}")
    print(f"raw_rows: {metrics['raw_rows']}")
    print(f"unique_jobs: {metrics['unique_jobs']}")
    print(f"duplicate_jobs: {metrics['duplicate_jobs']}")
    print(f"reported_total: {result.total_expected if result.total_expected is not None else 'unknown'}")
    for row in outcome.audit:
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    return 0 if result.jobs else 1


if __name__ == "__main__":
    raise SystemExit(main())
