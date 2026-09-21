"""STEP94N: standard-field backfill from raw list/detail records.

Generic, name-based only (no site vocabulary beyond the established alias
tables): for every Job the detail record wins, the list record fills gaps.
None of this touches the JD extraction thresholds or the fetch chain.
"""
from __future__ import annotations

from typing import Any

# Standard Job field -> record field candidates (ordered: first non-empty wins).
# ``detail_first`` records are merged as {**list, **detail} so plain names
# resolve to the detail value automatically when both carry the same key.
_STANDARD_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("company", ("company", "companyName", "company_name", "orgName", "corpName")),
    ("department", ("department", "orgName", "departmentName", "deptName", "division", "orgFullName")),
    ("education", ("education", "educationStr", "educationName", "educationText", "degree")),
    ("job_category", ("postTypeName", "postTypeText", "jobCategory", "categoryName", "postNameType", "functionName", "jobFunction")),
    ("major", ("subject", "major", "majorStr", "majorName", "subjectName")),
    ("recruitment_type", ("projectName", "project_name", "projectTitle", "recruitTypeName", "recruitTypeStr", "batchName")),
    ("headcount", ("recruitNumStr", "recruitNum", "recruitCount", "recruitNumber", "headCount", "hiringNum")),
    ("publish_date", ("publishDate", "publishFirstDate", "publishTime", "publishAt", "createDate", "releaseDate")),
)

_HEADCOUNT_RE = None  # compiled lazily below


def _nonempty_str(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _locations(merged: dict) -> list[str]:
    """workPlaceList[].name first, then any single workplace string."""
    out: list[str] = []
    places = merged.get("workPlaceList")
    if isinstance(places, list):
        for item in places:
            if isinstance(item, dict):
                name = _nonempty_str(item.get("name")) or _nonempty_str(item.get("workPlace")) or _nonempty_str(item.get("city"))
            else:
                name = _nonempty_str(item)
            if name and name not in out:
                out.append(name)
    if not out:
        for key in ("workPlaceStr", "workPlace", "workPlaceName", "locationStr", "location"):
            name = _nonempty_str(merged.get(key))
            if name:
                out.append(name)
                break
    return out


def _headcount(value: Any) -> int | None:
    """recruitNumStr is usually prose ("若干", "10人"); parse plain digits only."""
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    import re
    match = re.fullmatch(r"\s*(\d{1,4})\s*(?:人|名|个)?\s*", value)
    if match:
        return int(match.group(1))
    return None


def _split_jd_items(value: Any) -> list[str]:
    """Normalize a numbered/line-delimited JD half into per-item strings.

    ``1、…`` / ``1. …`` / ``1) …`` / plain newline items each become one list
    element; the numbering prefix is preserved verbatim.  A long prose block
    without line structure stays a single element — content is never dropped.
    """
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) > 1:
        return lines
    # Single physical line: split only on an explicit inline numbering run
    # ("1、xxx；2、yyy") — prose without numbering stays one element.
    import re
    parts = re.split(r"(?<=[；;。])\s*(?=\d{1,2}[、.)．])", text)
    parts = [part.strip() for part in parts if part.strip()]
    return parts if len(parts) > 1 else [text]


def normalized_job_fields(list_record: dict | None, detail_record: dict | None) -> dict[str, Any]:
    """Standard Job kwargs derived from the raw records (detail wins).

    Returns only keys that carry a real value — callers spread them into the
    Job constructor so existing fields (job_id/job_title/full_jd/...) win.
    """
    merged: dict[str, Any] = {}
    if isinstance(list_record, dict):
        merged.update(list_record)
    if isinstance(detail_record, dict):
        merged.update(detail_record)
    out: dict[str, Any] = {}
    for field, names in _STANDARD_FIELDS:
        for name in names:
            value = merged.get(name)
            if field == "headcount":
                parsed = _headcount(value)
                if parsed is not None:
                    out[field] = parsed
                    break
                continue
            text = _nonempty_str(value)
            if text is not None:
                out[field] = text
                break
    locations = _locations(merged)
    if locations:
        out["locations"] = locations
    return out


def normalized_jd_halves(list_record: dict | None, detail_record: dict | None,
                         picked: dict) -> tuple[list[str], list[str]]:
    """responsibilities/requirements lists, from the detail first then list.

    ``picked`` is the existing ``pick_jd_fields`` result for the *detail*
    record (split-style halves win there).  When the halves are absent —
    e.g. workContent was consumed as a whole-JD description — the raw
    workContent/serviceCondition texts are split into per-item lists so the
    columns are never empty while the full JD exists.
    """
    responsibilities: list[str] = []
    requirements: list[str] = []
    split_resp = picked.get("responsibilities")
    split_req = picked.get("requirements")
    if split_resp:
        responsibilities = _split_jd_items(split_resp)
    if split_req:
        requirements = _split_jd_items(split_req)
    if responsibilities and requirements:
        return responsibilities, requirements
    detail = detail_record if isinstance(detail_record, dict) else {}
    lst = list_record if isinstance(list_record, dict) else {}
    if not responsibilities:
        responsibilities = _split_jd_items(detail.get("workContent")) or _split_jd_items(lst.get("workContent"))
    if not requirements:
        requirements = _split_jd_items(detail.get("serviceCondition")) or _split_jd_items(lst.get("serviceCondition"))
    return responsibilities, requirements


def unified_company(jobs: list[Any]) -> str | None:
    """Collection-level company when every job agrees on one; else None."""
    companies = {str(getattr(job, "company", None) or "").strip() for job in jobs}
    companies.discard("")
    if len(companies) == 1:
        return companies.pop()
    return None
