"""Conservative field-role inference for heterogeneous public job records."""
from __future__ import annotations

import re
from typing import Any, Iterable


def canonical(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


ROLE_SUFFIXES = {
    "id": ("jobid", "positionid", "postingid", "postid", "requisitionid"),
    "title": ("jobtitle", "positionname", "positiontitle", "postingtitle", "requisitiontitle"),
    "location": ("location", "locations", "city", "cityname", "workplace", "workplacename", "workplacecode"),
    "category": ("department", "departmentname", "team", "category", "categoryname", "jobfunction", "functionname"),
    "responsibilities": ("description", "jobdescription", "responsibility", "responsibilities", "jobresponsibility", "duties", "postduties"),
    "requirements": ("requirement", "requirements", "jobrequirement", "qualification", "qualifications"),
    "recruitment_type": ("recruitmenttype", "recruittype", "projecttype", "employmenttype"),
    "url": ("url", "joburl", "detailurl", "applyurl", "clickurl"),
}


def matches(name: str, role: str) -> bool:
    value = canonical(name)
    terms = ROLE_SUFFIXES[role]
    if role == "id":
        return value == "id" or any(value.endswith(term) for term in terms)
    if role == "title":
        return value in ("title", "name") or any(value.endswith(term) for term in terms)
    if role == "location":
        return any(value == term or value.endswith(term) for term in terms)
    if role == "category":
        return any(value == term or value.endswith(term) or (term == "categoryname" and value.endswith("categoryname")) for term in terms)
    if role == "url":
        return value == "path" or any(value == term or value.endswith(term) for term in terms)
    return any(value == term or value.endswith(term) for term in terms)


def infer_field(fields: Iterable[str], role: str) -> str | None:
    values = list(fields)
    exact_order = ROLE_SUFFIXES[role]
    lookup = {canonical(value): value for value in values}
    if role == "id" and "id" in lookup:
        return lookup["id"]
    if role == "title":
        for exact in ("title", "name"):
            if exact in lookup:
                return lookup[exact]
    for term in exact_order:
        if term in lookup:
            return lookup[term]
    candidates = [value for value in values if matches(value, role)]
    return min(candidates, key=lambda value: (len(canonical(value)), canonical(value)), default=None)


def deep_pick(value: Any, role: str, max_depth: int = 3) -> tuple[Any, str | None]:
    if not isinstance(value, dict):
        return None, None
    queue = [(value, "", 0)]
    while queue:
        current, prefix, depth = queue.pop(0)
        candidates=[field for field in current if matches(field,role) and current.get(field) not in (None,"",[])]
        field=min(candidates,key=lambda item:(not isinstance(current[item],str),not canonical(item).endswith("name"),len(canonical(item))),default=None)
        if field is not None:
            return current[field], f"{prefix}.{field}" if prefix else field
        if depth < max_depth:
            for key, child in current.items():
                if isinstance(child, dict):
                    queue.append((child, f"{prefix}.{key}" if prefix else str(key), depth + 1))
    return None, None
