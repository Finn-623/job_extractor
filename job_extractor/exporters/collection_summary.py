"""STEP91: collection.json — machine-readable per-run collection statistics."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_extractor.models import CollectionResult


def build_collection_summary(result: "CollectionResult", collection_mode: str) -> dict:
    metrics = result.metrics
    finished = result.finished_at or datetime.now()
    elapsed = metrics.elapsed_seconds
    started = result.started_at
    if elapsed and (finished - started).total_seconds() <= 0:
        finished = started
    detail_resolution = (result.enrichment or {}).get("detail_resolution") or {}
    # Audit is deliberately allow-listed: reports must never receive the
    # original cURL, cookies, or captured request headers.
    detail_audit = {key: detail_resolution.get(key) for key in (
        "detail_method", "rendered_page_attempted", "user_curl_attempted",
        "list_only_used", "jd_success", "jd_failed", "jd_missing",
    ) if key in detail_resolution}
    return {
        "company": result.company,
        "source_url": result.source_url,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        # STEP96: total vs collection time are distinct semantics.  Older
        # collectors leave them 0 — fall back to the legacy elapsed value.
        "total_elapsed_seconds": round(getattr(metrics, "total_elapsed_seconds", None) or elapsed, 3),
        "collection_elapsed_seconds": round(getattr(metrics, "collection_elapsed_seconds", None) or elapsed, 3),
        "collection_mode": collection_mode,
        "total_expected": result.total_expected,
        "total_fetched": result.total_fetched,
        "total_unique": result.total_unique,
        "duplicate_jobs": metrics.duplicate_jobs,
        "details_succeeded": metrics.details_succeeded,
        "details_failed": metrics.details_failed,
        "status": result.status,
        "warnings": list(result.warnings),
        "errors": list(result.errors),
        "fallback": result.enrichment.get("fallback") if result.enrichment else None,
        "detail_resolution": detail_audit or None,
    }


def export_collection_summary(result: "CollectionResult", output_path: Path, *, collection_mode: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = build_collection_summary(result, collection_mode)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
