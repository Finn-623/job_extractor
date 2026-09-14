"""Generic detail-level requirement enrichment.

Uses official runtime/API detail evidence (or the official runtime record's
job-description field) to recover structured responsibilities/requirements.
Binding (job UUID + title) is verified whenever an explicit detail object is
supplied. Existing valid list-level requirements are never overwritten and the
existing Full JD is always preserved.
"""
from __future__ import annotations

from typing import Any

from job_extractor.collectors.generic_detail import jd_sections
from job_extractor.models import Job

ENRICHED = "ENRICHED"
REQUIREMENTS_FILLED = "REQUIREMENTS_FILLED"
RESPONSIBILITIES_IMPROVED = "RESPONSIBILITIES_IMPROVED"
SOURCE_ABSENT = "SOURCE_REQUIREMENTS_ABSENT"
EXTRACTION_FAILED = "EXTRACTION_FAILED"
BINDING_MISMATCH = "BINDING_MISMATCH"
NO_CHANGE = "NO_CHANGE"

DESCRIPTION_FIELDS = (
    "jobDescription", "description", "job_description", "content",
    "requirementsDescription",
    # STEP 51: extend with the generic description-style vocabulary (superset).
    "positionDescription", "position_description", "jobdesc", "jobDesc",
    "jd_content", "jdContent", "jd", "overview", "summary", "workContent",
    "work_content", "jobBody", "job_body", "detailDescription",
    "detail_description", "postContent", "post_content", "richText",
    "rich_text", "desc",
)


def _normalize(value: Any) -> str:
    return "".join(str(value or "").lower().split())


def bind_detail(job: Job, detail: dict[str, Any] | None) -> str | None:
    """Return a binding failure code when an explicit detail object does not match the job."""
    if not isinstance(detail, dict):
        return None
    for key in ("id", "jobId", "job_id", "positionId"):
        value = detail.get(key)
        if value not in (None, "") and job.job_id and str(value) != str(job.job_id):
            return "ID_MISMATCH"
    detail_title = detail.get("title") or detail.get("jobTitle")
    if isinstance(detail_title, str) and detail_title.strip() and job.job_title:
        left, right = _normalize(detail_title), _normalize(job.job_title)
        if left != right and left not in right and right not in left:
            return "TITLE_MISMATCH"
    return None


def _source_html(job: Job, detail: dict[str, Any] | None) -> str | None:
    for container in (detail, job.raw_data):
        if not isinstance(container, dict):
            continue
        for key in DESCRIPTION_FIELDS:
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def enrich_job(job: Job, detail: dict[str, Any] | None = None) -> tuple[Job, str]:
    if isinstance(detail, dict):
        mismatch = bind_detail(job, detail)
        if mismatch:
            return job, BINDING_MISMATCH
    source = _source_html(job, detail)
    if not source:
        return job, SOURCE_ABSENT
    try:
        responsibilities, requirements = jd_sections(source)
    except Exception:
        return job, EXTRACTION_FAILED
    if not responsibilities and not requirements:
        return job, SOURCE_ABSENT
    updates: dict[str, Any] = {}
    statuses: list[str] = []
    if requirements and not job.requirements:
        updates["requirements"] = requirements
        statuses.append(REQUIREMENTS_FILLED)
    if responsibilities and len(responsibilities) > len(job.responsibilities or []):
        updates["responsibilities"] = responsibilities
        statuses.append(RESPONSIBILITIES_IMPROVED)
    if not updates:
        return job, NO_CHANGE
    enriched = job.model_copy(update=updates)
    if len(statuses) == 2:
        return enriched, ENRICHED
    return enriched, statuses[0]
