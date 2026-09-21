"""STEP91: jobs.csv — flat, user-facing daily-view export of the Job records.

Multi-line JD text is safely converted into single-cell text (newlines kept
as visible separators that never break CSV rows; Excel formula injection is
neutralized).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_extractor.models import CollectionResult

HEADERS = ["id", "title", "company", "location", "department", "education",
           "job_type", "publish_date", "responsibilities", "requirements",
           "detail_url", "apply_url"]

_FORMULA_PREFIX = ("=", "+", "-", "@")


def _cell(value) -> str:
    """Single-line, formula-safe CSV cell text."""
    if value is None:
        return ""
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value if item is not None)
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ⏎ ")
    if text.startswith(_FORMULA_PREFIX):
        text = "'" + text
    return text


def export_jobs_csv(result: "CollectionResult", output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        for job in result.jobs:
            writer.writerow([
                _cell(job.job_id), _cell(job.job_title), _cell(job.company),
                _cell(job.locations), _cell(job.department), _cell(job.education),
                _cell(job.recruitment_type), _cell(job.publish_date),
                _cell(job.responsibilities), _cell(job.requirements),
                _cell(job.detail_url), _cell(job.apply_url),
            ])
    return output_path
