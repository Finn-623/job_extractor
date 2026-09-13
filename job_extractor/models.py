from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

class CollectionMetrics(BaseModel):
    list_requests: int = 0
    detail_requests: int = 0
    list_pages: int = 0
    details_attempted: int = 0
    details_succeeded: int = 0
    details_failed: int = 0
    elapsed_seconds: float = 0.0
    page_size: int | None = None
    jd_strategy: Literal["LIST_SUFFICIENT", "DETAIL_REQUIRED", "DETAIL_FALLBACK", "MIXED", "UNKNOWN"] = "UNKNOWN"
    browser_pages_opened: int = 0
    browser_requests_observed: int = 0
    initial_load_seconds: float = 0.0
    list_pagination_seconds: float = 0.0
    detail_fallback_seconds: float = 0.0
    normalize_seconds: float = 0.0
    export_seconds: float = 0.0
    pages_requested: int = 0
    pages_succeeded: int = 0
    raw_rows: int = 0
    unique_jobs: int = 0
    duplicate_jobs: int = 0
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

class Job(BaseModel):
    company: str | None = None
    job_id: str | None = None
    job_title: str
    job_category: str | None = None
    department: str | None = None
    locations: list[str] = Field(default_factory=list)
    recruitment_type: str | None = None
    education: str | None = None
    major: str | None = None
    headcount: int | None = None
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    full_jd: str | None = None
    apply_url: str | None = None
    detail_url: str | None = None
    source_url: str
    publish_date: str | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.now)

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
