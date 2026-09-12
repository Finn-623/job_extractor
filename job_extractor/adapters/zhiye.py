from __future__ import annotations

import json
from datetime import datetime
from time import perf_counter
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import httpx

from job_extractor.adapters.base import BaseAdapter
from job_extractor.adapters.zhiye_config import ZhiyeScope, get_tenant_config
from job_extractor.models import CollectionResult, Job


class ZhiyeResponseError(RuntimeError):
    """Raised when a Zhiye endpoint cannot provide usable JSON."""


class ZhiyeAdapter(BaseAdapter):
    platform_name = "zhiye"
    priority = 10
    LIST_PATH = "/api/JobAd/GetJobAdPageList"
    DETAIL_PATH = "/api/JobAd/GetJobAdInfo"
    BUSINESS_TYPES = {"social": "1", "campus": "2", "intern": "3"}
    RECRUITMENT_TYPES = {"1": "社会招聘", "2": "校园招聘", "3": "实习招聘"}
    PAGE_SIZE_CANDIDATES = (100, 50, 15)
    DISPLAY_FIELDS = ["Category", "Kind", "LocId", "PostDate", "WorkWeChatQrCode"]
    DETAIL_FIELDS = [
        "JobAdName", "Duty", "Require", "Org", "Category", "CategoryId",
        "LocNames", "HeadCount", "Degree", "Kind", "PostDate", "ChangeDate",
    ]
    FIELD_CANDIDATES = {
        "job_id": ("Id", "jobAdId", "JobAdId", "jobId", "id", "postId"),
        "job_title": ("JobAdName", "JobName", "jobTitle", "Title"),
        "category": ("Category", "JobCategory", "CategoryName"),
        "category_id": ("CategoryId", "categoryId"),
        "department": ("Org", "Department", "OrgName"),
        "locations": ("LocNames", "Locations", "LocationNames"),
        "education": ("Degree", "Education"),
        "major": ("Major", "Majors"),
        "headcount": ("HeadCount", "RecruitCount"),
        "duty": ("Duty", "Responsibilities", "JobDuty"),
        "requirement": ("Require", "Requirements", "Qualification"),
        "publish_date": ("PostDate", "PublishDate", "CreateDate"),
        "company": ("CompanyName", "Company", "TenantName"),
    }

    def __init__(
        self,
        timeout: float = 20.0,
        page_size: int = 100,
        max_pages: int = 1000,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout = timeout
        self.preferred_page_size = page_size
        self.max_pages = max_pages
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "JobExtractor/0.1 (+https://zhiye.com)"},
        )
        self.scope = ZhiyeScope(None)
        self.page_count = self.list_requests = self.actual_page_size = 0
        self.negotiated_page_size = page_size
        self.details_attempted = self.details_succeeded = self.details_failed = 0
        self.elapsed_seconds = 0.0
        self.category_distribution: dict[str, int] = {}
        self.detail_strategy = "LIST_SUFFICIENT"
        self.page_size_diagnostics: list[str] = []

    @classmethod
    def match(cls, url: str) -> bool:
        hostname = urlparse(url).hostname
        return hostname is not None and (hostname == "zhiye.com" or hostname.endswith(".zhiye.com"))

    @classmethod
    def detect_scope(cls, url: str) -> ZhiyeScope:
        segments = [part.lower() for part in urlparse(url).path.split("/") if part]
        business_type = segments[0] if segments and segments[0] in cls.BUSINESS_TYPES else None
        category = cls.BUSINESS_TYPES.get(business_type or "")
        return ZhiyeScope(business_type, (category,) if category else ())

    @classmethod
    def _build_scope_filter(cls, url: str) -> dict[str, Any]:
        """Compatibility wrapper returning serializable scope metadata."""
        scope = cls.detect_scope(url)
        return {"business_type": scope.name, "category": list(scope.categories)}

    @staticmethod
    def derive_base_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Cannot derive Zhiye base URL from invalid URL.")
        netloc = parsed.hostname.lower()
        if parsed.port is not None:
            netloc += f":{parsed.port}"
        return urlunparse((parsed.scheme.lower(), netloc, "", "", "", ""))

    @staticmethod
    def _get_first(data: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = data.get(key)
            if value not in (None, "", []):
                return value
        return None

    @classmethod
    def _field(cls, data: dict[str, Any], name: str) -> Any:
        return cls._get_first(data, *cls.FIELD_CANDIDATES[name])

    def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ZhiyeResponseError(f"status={exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ZhiyeResponseError(f"{type(exc).__name__}: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZhiyeResponseError("response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ZhiyeResponseError("JSON root was not an object")
        if payload.get("Code") not in (None, 200):
            raise ZhiyeResponseError(f"api_code={payload.get('Code')} message={payload.get('Message', '')}")
        return payload

    def _build_list_payload(self, page_index: int, page_size: int, scope: ZhiyeScope) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "PageIndex": page_index,
            "PageSize": page_size,
            "KeyWords": "",
            "SpecialType": 0,
            "PortalId": "",
            "DisplayFields": list(self.DISPLAY_FIELDS),
        }
        if scope.categories:
            payload["Category"] = list(scope.categories)
        return payload

    def _fetch_job_page(self, base_url: str, page_index: int, page_size: int, scope: ZhiyeScope) -> dict[str, Any]:
        self.list_requests += 1
        return self._request_json(
            "POST", f"{base_url}{self.LIST_PATH}",
            json=self._build_list_payload(page_index, page_size, scope),
        )

    @staticmethod
    def _parse_list_response(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
        jobs: Any = next(
            (payload[key] for key in ("Data", "Rows", "Items") if key in payload),
            None,
        )
        container = jobs if isinstance(jobs, dict) else payload
        if isinstance(jobs, dict):
            jobs = next(
                (jobs[key] for key in ("Data", "Rows", "Items", "records", "list") if key in jobs),
                None,
            )
        if not isinstance(jobs, list) or not all(isinstance(item, dict) for item in jobs):
            raise ZhiyeResponseError("UNSUPPORTED_ZHIYE_RESPONSE list structure")
        total = ZhiyeAdapter._get_first(payload, "Count", "Total", "TotalCount", "totalCount")
        if total is None and isinstance(container, dict):
            total = ZhiyeAdapter._get_first(container, "Count", "Total", "TotalCount", "totalCount")
        if total is not None and (not isinstance(total, int) or total < 0):
            raise ZhiyeResponseError("UNSUPPORTED_ZHIYE_RESPONSE invalid total")
        return jobs, total

    def _negotiate_page_size(
        self, base_url: str, scope: ZhiyeScope
    ) -> tuple[list[dict[str, Any]], int | None]:
        candidates = []
        for size in (self.preferred_page_size, *self.PAGE_SIZE_CANDIDATES):
            if size not in candidates:
                candidates.append(size)
        last_error: Exception | None = None
        for size in candidates:
            try:
                payload = self._fetch_job_page(base_url, 0, size, scope)
                jobs, total = self._parse_list_response(payload)
                silently_limited = total is not None and total > len(jobs) and 0 < len(jobs) < size
                if silently_limited:
                    self.page_size_diagnostics.append(
                        f"requested={size} returned={len(jobs)} total={total}"
                    )
                    continue
                self.negotiated_page_size = size
                self.actual_page_size = len(jobs)
                return jobs, total
            except (ZhiyeResponseError, ValueError) as exc:
                last_error = exc
                self.page_size_diagnostics.append(f"requested={size} error={exc}")
        raise ZhiyeResponseError(f"page-size negotiation failed: {last_error}")

    @classmethod
    def _extract_job_id(cls, raw_job: dict[str, Any]) -> str | None:
        value = cls._field(raw_job, "job_id")
        return str(value) if value is not None else None

    def _fetch_job_detail(self, base_url: str, job_id: str, category: str | None) -> dict[str, Any]:
        self.details_attempted += 1
        payload = self._request_json(
            "GET", f"{base_url}{self.DETAIL_PATH}",
            params={
                "jobAdId": job_id,
                "category": category or "",
                "displayFields": json.dumps(self.DETAIL_FIELDS, ensure_ascii=False),
            },
        )
        detail = self._get_first(payload, "Data", "Item")
        if not isinstance(detail, dict):
            raise ZhiyeResponseError("UNSUPPORTED_ZHIYE_RESPONSE detail structure")
        return detail

    @classmethod
    def _has_complete_list_jd(cls, raw: dict[str, Any]) -> bool:
        return all(isinstance(cls._field(raw, name), str) for name in ("duty", "requirement"))

    @staticmethod
    def _lines(value: Any) -> list[str]:
        return [line.strip() for line in value.replace("\r", "").split("\n") if line.strip()] if isinstance(value, str) else []

    @staticmethod
    def _clean_date(value: Any) -> str | None:
        return None if not isinstance(value, str) or not value or value.startswith("0001-") else value

    @staticmethod
    def _detail_url(source_url: str, job_id: str, category: str | None) -> str:
        parsed = urlparse(source_url)
        segment = {"1": "social", "2": "campus", "3": "intern"}.get(category or "")
        path = f"/{segment}/detail" if segment else parsed.path.replace("/jobs", "/detail")
        return urlunparse((parsed.scheme, parsed.netloc, path, "", urlencode({"jobAdId": job_id}), ""))

    def _normalize_job(
        self, raw: dict[str, Any], detail: dict[str, Any] | None,
        source_url: str, tenant_company: str | None,
    ) -> Job | None:
        data = {**raw, **(detail or {})}
        title = self._field(data, "job_title")
        if not isinstance(title, str):
            return None
        job_id = self._extract_job_id(raw)
        duty = self._field(data, "duty")
        requirement = self._field(data, "requirement")
        locations = self._field(data, "locations") or []
        if isinstance(locations, str):
            locations = [locations]
        if not isinstance(locations, list):
            locations = []
        category_id_value = self._field(data, "category_id")
        category_id = str(category_id_value) if category_id_value is not None else None
        full_parts = []
        if isinstance(duty, str):
            full_parts.append("岗位职责\n" + duty.strip())
        if isinstance(requirement, str):
            full_parts.append("任职要求\n" + requirement.strip())
        headcount = self._field(data, "headcount")
        api_company = self._field(data, "company")
        company = str(api_company) if api_company is not None else tenant_company
        return Job(
            company=company,
            job_id=job_id,
            job_title=title.strip(),
            job_category=self._field(data, "category"),
            department=self._field(data, "department"),
            locations=[str(item) for item in locations if item],
            recruitment_type=self.RECRUITMENT_TYPES.get(category_id or ""),
            education=self._field(data, "education"),
            major=self._field(data, "major"),
            headcount=headcount if isinstance(headcount, int) else None,
            responsibilities=self._lines(duty),
            requirements=self._lines(requirement),
            full_jd="\n\n".join(full_parts) or None,
            detail_url=self._detail_url(source_url, job_id, category_id) if job_id else None,
            source_url=source_url,
            publish_date=self._clean_date(self._field(data, "publish_date")),
            raw_data={**raw, "_detail": detail} if detail is not None else dict(raw),
        )

    def _reset_runtime_stats(self) -> None:
        self.page_count = self.list_requests = self.actual_page_size = 0
        self.negotiated_page_size = self.preferred_page_size
        self.details_attempted = self.details_succeeded = self.details_failed = 0
        self.elapsed_seconds = 0.0
        self.category_distribution = {}
        self.detail_strategy = "LIST_SUFFICIENT"
        self.page_size_diagnostics = []

    def collect(self, url: str) -> CollectionResult:
        started_at, timer = datetime.now(), perf_counter()
        self._reset_runtime_stats()
        self.scope = self.detect_scope(url)
        errors: list[str] = []
        raw_jobs: list[dict[str, Any]] = []
        expected: int | None = None
        reached_limit = False
        base_url = self.derive_base_url(url)
        tenant = get_tenant_config(urlparse(url).hostname or "")
        try:
            try:
                first_jobs, expected = self._negotiate_page_size(base_url, self.scope)
            except (ZhiyeResponseError, ValueError) as exc:
                return CollectionResult(
                    source_url=url, platform=self.platform_name, company=tenant.company,
                    total_fetched=0, total_unique=0, status="FAILED",
                    errors=[f"LIST_PAGE_ERROR page=0 reason={exc}"],
                    started_at=started_at, finished_at=datetime.now(),
                )
            self.page_count = 1
            raw_jobs.extend(first_jobs)
            for page_index in range(1, self.max_pages):
                if expected is not None and len(raw_jobs) >= expected:
                    break
                if not raw_jobs or (expected is None and len(first_jobs) < self.negotiated_page_size):
                    break
                try:
                    payload = self._fetch_job_page(base_url, page_index, self.negotiated_page_size, self.scope)
                    page_jobs, page_total = self._parse_list_response(payload)
                except (ZhiyeResponseError, ValueError) as exc:
                    errors.append(f"LIST_PAGE_ERROR page={page_index} reason={exc}")
                    break
                self.page_count += 1
                expected = expected if expected is not None else page_total
                raw_jobs.extend(page_jobs)
                if expected is not None and len(raw_jobs) >= expected:
                    break
                if not page_jobs or (expected is None and len(page_jobs) < self.negotiated_page_size):
                    break
            else:
                reached_limit = True
                errors.append(f"PAGINATION_LIMIT max_pages={self.max_pages}")

            for raw in raw_jobs:
                category = self._field(raw, "category_id")
                key = str(category) if category is not None else "missing"
                self.category_distribution[key] = self.category_distribution.get(key, 0) + 1
            leaks = []
            if self.scope.categories:
                leaks = [key for key in self.category_distribution if key not in self.scope.categories]
                if leaks:
                    errors.append(f"SCOPE_LEAK expected={list(self.scope.categories)} actual={sorted(leaks)}")

            unique_raw, seen = [], set()
            for raw in raw_jobs:
                job_id = self._extract_job_id(raw)
                title = str(self._field(raw, "job_title") or "")
                hint = str(self._get_first(raw, "DetailUrl", "detailUrl") or "")
                key = f"id:{job_id}" if job_id else f"fallback:{title}:{hint}"
                if key not in seen:
                    seen.add(key)
                    unique_raw.append(raw)

            api_company_value = next(
                (self._field(raw, "company") for raw in unique_raw if self._field(raw, "company") is not None),
                None,
            )
            resolved_company = str(api_company_value) if api_company_value is not None else tenant.company

            details: dict[int, dict[str, Any]] = {}
            fallback_indexes = [i for i, raw in enumerate(unique_raw) if not self._has_complete_list_jd(raw)]
            if fallback_indexes:
                self.detail_strategy = "DETAIL_FALLBACK"
            for index in fallback_indexes:
                raw, job_id = unique_raw[index], self._extract_job_id(unique_raw[index])
                if not job_id:
                    self.details_failed += 1
                    errors.append(f"DETAIL_ERROR index={index} reason=missing job_id")
                    continue
                try:
                    category = self._field(raw, "category_id")
                    details[index] = self._fetch_job_detail(base_url, job_id, str(category or ""))
                    self.details_succeeded += 1
                except (ZhiyeResponseError, ValueError) as exc:
                    self.details_failed += 1
                    errors.append(f"DETAIL_ERROR job_id={job_id} reason={exc}")

            jobs, missing_jd = [], 0
            for index, raw in enumerate(unique_raw):
                job = self._normalize_job(raw, details.get(index), url, resolved_company)
                if job is None:
                    errors.append(f"PARSE_ERROR index={index} reason=missing title")
                    continue
                jobs.append(job)
                if not job.responsibilities or not job.requirements or not job.full_jd:
                    missing_jd += 1
            if missing_jd:
                errors.append(f"JD_INCOMPLETE count={missing_jd}")

            incomplete = (
                reached_limit or bool(leaks) or self.details_failed > 0 or missing_jd > 0
                or len(jobs) < len(unique_raw)
                or (expected is not None and len(jobs) < expected)
                or any(error.startswith("LIST_PAGE_ERROR") for error in errors)
            )
            return CollectionResult(
                source_url=url, platform=self.platform_name, company=resolved_company,
                total_expected=expected, total_fetched=len(raw_jobs), total_unique=len(jobs),
                status="INCOMPLETE" if incomplete else "COMPLETE", jobs=jobs,
                errors=errors, started_at=started_at, finished_at=datetime.now(),
            )
        finally:
            self.elapsed_seconds = perf_counter() - timer
            if self._owns_client:
                self.client.close()
