from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Lone UTF-16 surrogate units: invalid in well-formed UTF-8 / XML. Kept at
# module scope so the Job validator can reuse the compiled pattern cheaply.
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")

class CollectionMetrics(BaseModel):
    list_requests: int = 0
    detail_requests: int = 0
    list_pages: int = 0
    details_attempted: int = 0
    details_succeeded: int = 0
    details_failed: int = 0
    # STEP 54B: pre-fetch required count from the detail stage gate
    # (needs_detail_fetch per job before any request) so reporting can
    # reconcile attempted against required at the SAME point in time.
    # Post-merge state counts drift when detail payloads upgrade jobs.
    details_required: int = 0
    elapsed_seconds: float = 0.0
    # STEP96: wall-clock total vs pure collection time are different numbers.
    total_elapsed_seconds: float = 0.0
    collection_elapsed_seconds: float = 0.0
    page_size: int | None = None
    # N9 Final: LIST_ONLY is a legal completion reserved for the explicit
    # Browser Assist / HAR + anti-bot path (list fully collected, detail pages
    # not safely batch-fetchable). Normal missing-JD runs never get it.
    jd_strategy: Literal["LIST_SUFFICIENT", "DETAIL_REQUIRED", "DETAIL_FALLBACK", "MIXED", "UNKNOWN", "LIST_ONLY"] = "UNKNOWN"
    browser_pages_opened: int = 0
    browser_requests_observed: int = 0
    # STEP 70: browser fallback runtime accounting (opt-in browser path only).
    browser_jobs_attempted: int = 0
    browser_jobs_resolved: int = 0
    browser_jobs_blocked: int = 0
    browser_jobs_budget_skipped: int = 0
    browser_dom_resolved: int = 0
    browser_network_resolved: int = 0
    # STEP 70: unified detail accounting view. Invariant: detail_total ==
    # detail_http_resolved + detail_browser_resolved + detail_blocked +
    # detail_failed + detail_pending (each record classified exactly once).
    detail_total: int = 0
    detail_http_resolved: int = 0
    detail_browser_resolved: int = 0
    detail_blocked: int = 0
    detail_failed: int = 0
    detail_pending: int = 0
    initial_load_seconds: float = 0.0
    list_pagination_seconds: float = 0.0
    detail_fallback_seconds: float = 0.0
    normalize_seconds: float = 0.0
    export_seconds: float = 0.0
    pages_requested: int = 0
    pages_succeeded: int = 0
    raw_rows: int = 0
    # `unique_jobs` is the pre-cross-scope count: it is accumulated by each
    # collector's pagination stage and intentionally excludes the final
    # multi-scope stable-ID merge.
    unique_jobs: int = 0
    # Rows deduplicated inside an individual collector's pagination stream.
    duplicate_jobs: int = 0
    # Additional rows collapsed only when otherwise-complete ListScopes are
    # reconciled by a shared stable job identity.
    cross_scope_merged_rows: int = 0
    retry_count: int = 0
    retry_sleep_seconds: float = 0.0
    list_request_seconds: float = 0.0
    detail_request_seconds: float = 0.0
    average_list_request_seconds: float = 0.0
    average_detail_request_seconds: float = 0.0
    termination_reason: str | None = None
    collection_mode: str | None = None
    max_concurrency_observed: int = 1

class DataCompleteness(BaseModel):
    total_jobs: int = 0
    complete_jobs: int = 0
    missing_jd_jobs: int = 0
    missing_requirements_jobs: int = 0
    missing_responsibilities_jobs: int = 0
    source_incomplete_jobs: int = 0
    completeness_ratio: float = 1.0
    # STEP 51 JD/detail diagnostics.
    jd_complete: int = 0
    jd_incomplete: int = 0
    # STEP96: explicit JD-completeness view (jd_total/jd_missing) plus the
    # detail-fetch-activity view; LIST_SUFFICIENT runs keep activity all-zero.
    jd_total: int = 0
    jd_missing: int = 0
    list_sufficient: int = 0
    detail_required: int = 0
    detail_attempted: int = 0
    detail_succeeded: int = 0
    detail_failed: int = 0
    detail_pending: int = 0
    source_requirements_absent: int = 0

class Job(BaseModel):
    company: str | None = None
    job_id: str | None = None
    job_title: str
    job_category: str | None = None
    department: str | None = None
    locations: list[str] = Field(default_factory=list)
    recruitment_type: str | None = None
    # Recruitment entrances/tabs are provenance, distinct from the job
    # record's own recruitment_type attribute.
    recruitment_scopes: list[str] = Field(default_factory=list)
    education: str | None = None
    # Existing adapters historically emit a string; Generic API records can
    # expose several readable majors, which must remain a list.
    major: list[str] | str | None = None
    headcount: int | None = None
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    full_jd: str | None = None
    # ``None`` is intentionally retained for artifacts created before N9.4.
    # It is an unknown legacy lifecycle, never an implicit failure.
    jd_enrichment: Literal["NOT_REQUESTED", "PENDING", "COMPLETE",
                           "VERIFICATION_REQUIRED", "FAILED"] | None = None
    apply_url: str | None = None
    detail_url: str | None = None
    # N9.4 JD on-demand enrichment lifecycle. None = legacy record: business
    # layers derive NOT_REQUESTED (no credible full_jd) or COMPLETE (credible
    # full_jd) — never backfill the stored field.
    jd_enrichment_status: Literal[
        "NOT_REQUESTED", "PENDING", "COMPLETE", "VERIFICATION_REQUIRED", "FAILED"
    ] | None = None
    source_url: str
    publish_date: str | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.now)

    @staticmethod
    def _drop_lone_surrogates(value: Any) -> Any:
        """Remove lone UTF-16 surrogate units (U+D800–U+DFFF) from strings.

        Lone surrogates cannot be encoded to UTF-8, so a single one anywhere
        inside a Job would crash JSON export and Markdown file writes. Only
        the invalid units are dropped: CJK text, emoji, tabs, newlines and
        bullets are preserved byte-for-byte.
        """
        if isinstance(value, str):
            if _LONE_SURROGATE.search(value):
                return _LONE_SURROGATE.sub("", value)
            return value
        if isinstance(value, list):
            return [Job._drop_lone_surrogates(x) for x in value]
        if isinstance(value, dict):
            return {key: Job._drop_lone_surrogates(item) for key, item in value.items()}
        return value

    @model_validator(mode="after")
    def _text_safety(self) -> "Job":
        if self.job_title and _LONE_SURROGATE.search(self.job_title):
            self.job_title = _LONE_SURROGATE.sub("", self.job_title)
        for name in ("company", "job_category", "department", "recruitment_type",
                     "education", "major", "full_jd", "apply_url", "detail_url",
                     "source_url", "publish_date"):
            value = getattr(self, name)
            if isinstance(value, str) and _LONE_SURROGATE.search(value):
                setattr(self, name, _LONE_SURROGATE.sub("", value))
        if self.locations:
            self.locations = [  # type: ignore[list-item]
                Job._drop_lone_surrogates(x) for x in self.locations
            ]
        if self.recruitment_scopes:
            self.recruitment_scopes = [  # type: ignore[list-item]
                Job._drop_lone_surrogates(x) for x in self.recruitment_scopes
            ]
        self.responsibilities = [  # type: ignore[list-item]
            Job._drop_lone_surrogates(x) for x in self.responsibilities
        ]
        self.requirements = [  # type: ignore[list-item]
            Job._drop_lone_surrogates(x) for x in self.requirements
        ]
        if self.raw_data:
            self.raw_data = Job._drop_lone_surrogates(self.raw_data)
        return self

class CollectionResult(BaseModel):
    source_url: str
    platform: str | None = None
    company: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    metrics: CollectionMetrics = Field(default_factory=CollectionMetrics)
    total_expected: int | None = None
    total_fetched: int = 0
    total_unique: int = 0
    status: Literal["COMPLETE", "INCOMPLETE", "FAILED"]
    jobs: list[Job] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_completeness: DataCompleteness = Field(default_factory=DataCompleteness)
    duplicate_audit: dict[str, Any] = Field(default_factory=dict)
    enrichment: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = None
