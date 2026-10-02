"""STEP91: formal output layout.

Every successful run writes to::

    <output_root>/<formal company name>/<YYYY-MM-DD_HHMMSS>/

Every failed run writes only an error report to::

    <output_root>/_failed/<formal company name|_unknown>/<YYYY-MM-DD_HHMMSS>/

Unidentifiable companies fall back to ``<output_root>/_unknown/<timestamp>/``.
Folder names use the formal company name already recognized by the system
(e.g. BYD -> 比亚迪), never a domain slug like ``job_byd_com``.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlparse

if TYPE_CHECKING:
    from job_extractor.models import CollectionResult

TS_FORMAT = "%Y-%m-%d_%H%M%S"
UNKNOWN = "_unknown"
FAILED_ROOT = "_failed"

# Formal company names for well-known recruitment hosts / brand tokens.
# N9.8: this mapping IS the trusted site-identity registry — every entry is a
# verified site-level company observed on the official recruitment site
# (e.g. careers.pddglobalhr.com shows "拼多多集团-PDD"; the canonical group
# name is "拼多多集团"). Brand tokens in a host or company value map to the
# formal name through generic lookup only — never per-site if-branches.
KNOWN_COMPANY_NAMES = {
    "byd": "比亚迪",
    "geely": "吉利",
    "zte": "中兴",
    "pdd": "拼多多集团",
    # xg.pinduoduo.com — the same verified PDD group site, distinct brand token
    "pinduoduo": "拼多多集团",
}

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_STAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}_\d{6}(_\d+)?")


def sanitize_folder_name(name: str) -> str:
    """Make a company name safe as a single filesystem folder name."""
    cleaned = _UNSAFE_CHARS.sub("_", name).strip(" .")
    return cleaned or UNKNOWN


def trusted_site_company(source_url: str | None) -> str | None:
    """Verified site-level company from the trusted registry (N9.8).

    Generic lookup of verified brand tokens against the source host; accepts
    both full URLs and bare hosts (HAR imports record the list-API origin).
    Unlike :func:`formal_company_name` there is no ``_unknown`` fallback: a
    domain without a verified entry stays ``None`` (fail closed). Callers must
    apply the existing identity priority — job-level explicit company wins
    over this site-level identity.
    """
    host = (urlsplit(source_url or "").hostname or source_url or "").lower()
    for token, formal in KNOWN_COMPANY_NAMES.items():
        if token in host:
            return formal
    return None


def formal_company_name(company: str | None, source_url: str | None) -> str:
    """Resolve the formal folder name for a run.

    Known brand tokens (in the company value or the source host) map to the
    formal Chinese name; an existing formal name passes through; anything
    domain-like or missing falls back to ``_unknown``.
    """
    name = (company or "").strip()
    host = (urlparse(source_url or "").hostname or "").lower()
    for token, formal in KNOWN_COMPANY_NAMES.items():
        if token in name.lower() or token in host:
            return formal
    if name and "." not in name:
        return sanitize_folder_name(name)
    return UNKNOWN


def format_stamp(at: datetime | None = None) -> str:
    return (at or datetime.now()).strftime(TS_FORMAT)


def unique_run_dir(base: Path, stamp: str) -> Path:
    """Return ``base/stamp``, suffixing ``_2``/``_3``… on same-second collisions."""
    candidate = base / stamp
    ordinal = 2
    while candidate.exists():
        candidate = base / f"{stamp}_{ordinal}"
        ordinal += 1
    return candidate


def resolve_run_directory(
    output_root: Path,
    *,
    company: str | None,
    source_url: str,
    failed: bool,
    at: datetime | None = None,
) -> Path:
    """Compute (without creating) the run directory for a collection result."""
    stamp = format_stamp(at)
    formal = formal_company_name(company, source_url)
    if failed:
        return unique_run_dir(output_root / FAILED_ROOT / formal, stamp)
    if formal == UNKNOWN:
        return unique_run_dir(output_root / UNKNOWN, stamp)
    return unique_run_dir(output_root / formal, stamp)


def write_run_artifacts(run_dir: Path, result: "CollectionResult", collection_mode: str) -> None:
    """Write the STEP91 run files next to the ReportManager artifacts."""
    from job_extractor.exporters.collection_summary import export_collection_summary
    from job_extractor.exporters.csv_exporter import export_jobs_csv
    from job_extractor.reporting.error_report import export_error_report

    export_jobs_csv(result, run_dir / "jobs.csv")
    export_collection_summary(result, run_dir / "collection.json", collection_mode=collection_mode)
    if result.errors:
        export_error_report(result, run_dir / "error_report.json", collection_mode=collection_mode)
