from __future__ import annotations

import html
import math
import re
from datetime import datetime
from time import perf_counter
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from job_extractor.adapters.base import BaseAdapter
from job_extractor.models import CollectionResult, Job


class BeisenCMSResponseError(RuntimeError):
    """Raised when a Beisen CMS SSR page cannot be used safely."""


class BeisenCMSCollector(BaseAdapter):
    """CSSC's Beisen CmsPortal SSR protocol (list + mobile detail)."""

    platform_name = "beisen_cms"
    priority = 9
    FINGERPRINT = "stc-cms.beisen.com/CmsPortal/"
    PAGE_SIZE = 15
    KUNLUNXIN_PAGE_SIZE = 10
    TOTAL_RE = re.compile(r"共\s*(\d+)\s*条记录")
    LINK_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.I | re.S)
    ATTR_RE = re.compile(r"([:\w-]+)\s*=\s*(['\"])(.*?)\2", re.S)

    def __init__(self, timeout: float = 20.0, max_pages: int = 1000, client: httpx.Client | None = None) -> None:
        self.max_pages = max_pages
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": "JobExtractor/0.1"})
        self.page_count = self.list_requests = self.details_attempted = 0
        self.details_succeeded = self.details_failed = 0
        self.negotiated_page_size = self.PAGE_SIZE
        self.detail_strategy = "DETAIL_REQUIRED"
        self.elapsed_seconds = 0.0
        self.template = "default"

    @classmethod
    def is_zhiye_hostname(cls, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == "zhiye.com" or host.endswith(".zhiye.com")

    @classmethod
    def match(cls, url: str) -> bool:
        # This provider has no hostname-only match: CmsPortal must be proved
        # by ``probe`` before routing so legacy Zhiye is never hijacked.
        return False

    @classmethod
    def matches_html(cls, text: str) -> bool:
        return cls.FINGERPRINT in text

    @classmethod
    def probe(cls, url: str, timeout: float = 20.0) -> bool:
        """Perform the required entrance-page fingerprint check; errors fall back."""
        if not cls.is_zhiye_hostname(url):
            return False
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": "JobExtractor/0.1"}) as client:
                response = client.get(url)
                response.raise_for_status()
                return cls.matches_html(response.text)
        except httpx.HTTPError:
            return False

    @staticmethod
    def _base(url: str) -> str:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    @staticmethod
    def _plain(value: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()

    @classmethod
    def parse_list(cls, text: str) -> tuple[int, list[dict[str, str]]]:
        total_match = cls.TOTAL_RE.search(cls._plain(text))
        if not total_match:
            raise BeisenCMSResponseError("LIST_TOTAL_MISSING")
        rows: list[dict[str, str]] = []
        for match in cls.LINK_RE.finditer(text):
            attrs = {key.lower(): html.unescape(value) for key, _, value in cls.ATTR_RE.findall(match.group("attrs"))}
            href = attrs.get("href", "")
            id_match = re.fullmatch(r"/zpdetail/(\d+)(?:\?[^#]*)?", href)
            if not id_match:
                continue
            title = cls._plain(attrs.get("title") or match.group("body"))
            if title:
                rows.append({"position_id": id_match.group(1), "title": title, "detail_href": href})
        if not rows and int(total_match.group(1)):
            raise BeisenCMSResponseError("LIST_JOB_LINKS_MISSING")
        return int(total_match.group(1)), rows

    @classmethod
    def parse_detail(cls, text: str) -> dict[str, str | None]:
        def first(pattern: str) -> str | None:
            match = re.search(pattern, text, re.I | re.S)
            return cls._plain(match.group(1)) if match else None
        title = first(r'<h1[^>]*id=["\']detial_title["\'][^>]*>(.*?)</h1>')
        location = first(r"工作地点：?</label>\s*<div[^>]*>(.*?)</div>")
        department = first(r"需求部门：?</label>\s*<div[^>]*>(.*?)</div>")
        responsibilities = first(r"工作职责：?</h3>\s*<pre[^>]*>(.*?)</pre>")
        requirements = first(r"任职资格：?</h3>\s*<pre[^>]*>(.*?)</pre>")
        headcount = first(r"招聘人数：\s*(.*?)</div>")
        publish_date = first(r"发布时间：\s*(.*?)</div>")
        deadline = first(r"截止时间：\s*(.*?)</div>")
        apply = first(r'<a\b[^>]*\bnexturl=["\']([^"\']+)["\'][^>]*>')
        return {"title": title, "location": location, "department": department, "responsibilities": responsibilities,
                "requirements": requirements, "headcount": headcount, "publish_date": publish_date,
                "deadline": deadline, "apply_path": apply}

    @classmethod
    def is_kunlunxin_template(cls, text: str) -> bool:
        """Recognize the Campus -> Campuslist SSR template, not a hostname."""
        return "/Campuslist" in text and "jobList" in text

    @classmethod
    def parse_kunlunxin_list(cls, text: str) -> tuple[list[dict[str, str]], int]:
        pages = [int(value) for value in re.findall(r"[?&]PageIndex=(\d+)", text, re.I)]
        if not pages:
            raise BeisenCMSResponseError("KUNLUNXIN_PAGE_COUNT_MISSING")
        rows: list[dict[str, str]] = []
        for match in re.finditer(r'<a\s+href=["\']/xiangqing\?jobId=(\d+)["\'][^>]*>\s*<h3>(.*?)</h3>', text, re.I | re.S):
            title = cls._plain(match.group(2))
            if title:
                job_id = match.group(1)
                rows.append({"position_id": job_id, "title": title, "detail_href": f"/xiangqing?jobId={job_id}"})
        if not rows:
            raise BeisenCMSResponseError("KUNLUNXIN_JOB_LINKS_MISSING")
        return rows, max(pages)

    @classmethod
    def parse_kunlunxin_detail(cls, text: str) -> dict[str, str | None]:
        def first(pattern: str) -> str | None:
            match = re.search(pattern, text, re.I | re.S)
            return cls._plain(match.group(1)) if match else None
        title = first(r'<div\s+class=["\']title["\'][^>]*>.*?<h3>(.*?)</h3>')
        sx = first(r'<div\s+class=["\']sx["\'][^>]*>(.*?)</div>')
        location = next((part.strip() for part in reversed((sx or "").split("|")) if part.strip()), None)
        responsibilities = first(r'<h4>\s*工作职责[:：]\s*</h4>\s*<p[^>]*>(.*?)</p>')
        requirements = first(r'<h4>\s*任职要求[:：]\s*</h4>\s*<p[^>]*>(.*?)</p>')
        publish_date = first(r'<div\s+class=["\']time["\'][^>]*>\s*发布时间[:：]\s*(.*?)</div>')
        return {"title": title, "location": location, "department": None,
                "responsibilities": responsibilities, "requirements": requirements,
                "headcount": None, "publish_date": publish_date, "deadline": None, "apply_path": None}

    def _get(self, url: str, **kwargs: Any) -> str:
        try:
            response = self.client.get(url, **kwargs)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as exc:
            raise BeisenCMSResponseError(f"{type(exc).__name__}: {exc}") from exc

    def _get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.get(url, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BeisenCMSResponseError(f"DETAIL_API_ERROR {type(exc).__name__}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("Code") != 200:
            raise BeisenCMSResponseError("DETAIL_API_INVALID_RESPONSE")
        return payload

    @classmethod
    def parse_detail_api(cls, payload: dict[str, Any]) -> dict[str, str | None]:
        data = payload.get("Data")
        if not isinstance(data, dict):
            raise BeisenCMSResponseError("DETAIL_API_DATA_MISSING")
        def field(name: str) -> str | None:
            value = data.get(name)
            return cls._plain(value) if isinstance(value, str) and value.strip() else None
        detail = {
            "title": field("JobAdName"), "location": field("LocName"),
            "department": field("Department"), "responsibilities": field("DutyStr"),
            "requirements": field("RequireStr"), "headcount": field("HeadCountStr"),
            "publish_date": field("PostDateStr"), "deadline": field("EndTimeStr"),
            "apply_path": None,
        }
        if not detail["title"] or not (detail["responsibilities"] or detail["requirements"]):
            raise BeisenCMSResponseError("DETAIL_API_JD_INCOMPLETE")
        return detail

    @staticmethod
    def _has_ssr_jd(detail: dict[str, str | None]) -> bool:
        return bool(detail["title"] and detail["responsibilities"] and detail["requirements"])

    def _fetch_detail(self, mobile_base: str, job_id: str) -> dict[str, str | None]:
        if self.template == "kunlunxin":
            return self.parse_kunlunxin_detail(self._get(mobile_base + "/xiangqing", params={"jobId": job_id}))
        ssr = self.parse_detail(self._get(mobile_base + "/JobAd/Info", params={"adid": job_id}))
        if self._has_ssr_jd(ssr):
            return ssr
        return self.parse_detail_api(
            self._get_json(mobile_base + "/LightBoltAPI/JobAd/Info", params={"adid": job_id})
        )

    @staticmethod
    def _mobile_base(url: str) -> str:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host.startswith("m.") or ".m." in host:
            mobile_host = host
        else:
            mobile_host = host.split(".", 1)[0] + ".m." + host.split(".", 1)[1]
        return urlunparse((parsed.scheme, mobile_host, "", "", "", ""))

    @staticmethod
    def _lines(value: str | None) -> list[str]:
        return [line.strip() for line in (value or "").replace("\r", "").split("\n") if line.strip()]

    def _job(self, raw: dict[str, str], detail: dict[str, str | None], source_url: str, mobile_base: str) -> Job | None:
        if not detail["title"] or not detail["responsibilities"] or not detail["requirements"]:
            return None
        job_id = raw["position_id"]
        apply_url = (mobile_base + detail["apply_path"]) if detail["apply_path"] else None
        headcount = detail["headcount"]
        return Job(job_id=job_id, job_title=detail["title"], department=detail["department"],
                   locations=[detail["location"]] if detail["location"] else [],
                   headcount=int(headcount) if headcount and headcount.isdigit() else None,
                   responsibilities=self._lines(detail["responsibilities"]), requirements=self._lines(detail["requirements"]),
                   full_jd="岗位职责\n" + detail["responsibilities"] + "\n\n任职要求\n" + detail["requirements"],
                   detail_url=self._base(source_url) + f"/zpdetail/{job_id}", apply_url=apply_url,
                   source_url=source_url, publish_date=detail["publish_date"],
                   raw_data={"list": raw, "detail": detail, "deadline": detail["deadline"]})

    def collect(self, url: str) -> CollectionResult:
        started, timer = datetime.now(), perf_counter()
        errors: list[str] = []
        raw_rows: list[dict[str, str]] = []
        expected: int | None = None
        try:
            self.template = "kunlunxin" if self.is_kunlunxin_template(self._get(url)) else "default"
            page_size = self.KUNLUNXIN_PAGE_SIZE if self.template == "kunlunxin" else self.PAGE_SIZE
            self.negotiated_page_size = page_size
            list_path = "/Campuslist" if self.template == "kunlunxin" else "/campus/"
            expected_pages: int | None = None
            last_page_rows: list[dict[str, str]] = []
            for page_index in range(1, self.max_pages + 1):
                self.list_requests += 1
                try:
                    text = self._get(self._base(url) + list_path, params={"PageIndex": page_index})
                    if self.template == "kunlunxin":
                        rows, expected_pages = self.parse_kunlunxin_list(text)
                        page_total = None
                        if page_index < expected_pages and len(rows) != page_size:
                            raise BeisenCMSResponseError("KUNLUNXIN_SHORT_NONFINAL_PAGE")
                    else:
                        page_total, rows = self.parse_list(text)
                except BeisenCMSResponseError as exc:
                    errors.append(f"LIST_PAGE_ERROR page={page_index} reason={exc}")
                    break
                self.page_count += 1
                if self.template == "kunlunxin":
                    last_page_rows = rows
                    if expected_pages is not None and page_index >= expected_pages:
                        expected = page_size * (expected_pages - 1) + len(last_page_rows)
                        break
                elif expected is None:
                    expected = page_total
                    pages = math.ceil(expected / page_size) if expected else 0
                    if pages > self.max_pages:
                        errors.append(f"PAGINATION_LIMIT max_pages={self.max_pages}")
                        break
                elif page_total != expected:
                    errors.append(f"LIST_TOTAL_CHANGED page={page_index}")
                    break
                raw_rows.extend(rows)
                if expected is not None and page_index >= math.ceil(expected / page_size):
                    break
            if expected is not None and self.page_count < math.ceil(expected / page_size):
                errors.append("PAGINATION_INCOMPLETE")
            unique_rows: list[dict[str, str]] = []
            seen: set[str] = set()
            for row in raw_rows:
                if row["position_id"] not in seen:
                    seen.add(row["position_id"])
                    unique_rows.append(row)
            if expected is not None and len(unique_rows) != expected:
                errors.append(f"UNIQUE_COUNT_MISMATCH expected={expected} unique={len(unique_rows)}")
            jobs: list[Job] = []
            mobile_base = self._base(url) if self.template == "kunlunxin" else self._mobile_base(url)
            for row in unique_rows:
                self.details_attempted += 1
                try:
                    detail = self._fetch_detail(mobile_base, row["position_id"])
                    job = self._job(row, detail, url, mobile_base)
                    if job is None:
                        raise BeisenCMSResponseError("JD_INCOMPLETE")
                    jobs.append(job)
                    self.details_succeeded += 1
                except BeisenCMSResponseError as exc:
                    self.details_failed += 1
                    errors.append(f"DETAIL_ERROR job_id={row['position_id']} reason={exc}")
            status = "COMPLETE" if expected is not None and len(raw_rows) == expected and len(jobs) == expected and not errors else ("FAILED" if expected is None else "INCOMPLETE")
            return CollectionResult(source_url=url, platform=self.platform_name, total_expected=expected, total_fetched=len(raw_rows),
                                    total_unique=len(jobs), status=status, jobs=jobs, errors=errors,
                                    started_at=started, finished_at=datetime.now())
        finally:
            self.elapsed_seconds = perf_counter() - timer
            if self._owns_client:
                self.client.close()
