"""STEP91: error_report.json — written only when a run has actual errors.

One structured record per error; the report keeps the secret-redaction
behavior of the runtime's ``make_error`` strings out of scope (they are
already redacted upstream) and adds structured context when available.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from job_extractor.models import CollectionResult


def _error_entry(raw: str, result: "CollectionResult") -> dict[str, Any]:
    error_type, _, reason = raw.partition(" reason=")
    code, _, context = error_type.partition(" ")
    report = {
        "stage": result.metrics.collection_mode or result.platform or "collection",
        "error_type": code or raw.split(" ", 1)[0],
        "reason": reason or error_type,
        "message": raw,
        "http_status": None,
        "job_id": None,
        "job_title": None,
        "retry_count": result.metrics.retry_count,
        "context": context or None,
        "timestamp": datetime.now().isoformat(),
    }
    return report


def build_error_report(result: "CollectionResult", collection_mode: str) -> dict:
    report = {
        "company": result.company,
        "source_url": result.source_url,
        "collection_mode": collection_mode,
        "status": result.status,
        "errors": [_error_entry(raw, result) for raw in result.errors],
    }
    if result.enrichment.get("fallback"):
        report["fallback"] = result.enrichment["fallback"]
        report.update(result.enrichment["fallback"])
    return report


def export_error_report(result: "CollectionResult", output_path: Path, *, collection_mode: str) -> Path | None:
    """Write error_report.json; returns None (and writes nothing) without errors."""
    if not result.errors:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = build_error_report(result, collection_mode)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
