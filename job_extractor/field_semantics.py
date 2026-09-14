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


# ---------------------------------------------------------------------------
# STEP 51: generic JD field recognition.
#
# Site-agnostic vocabulary for identifying job-description data inside
# heterogeneous records. No site/company special cases: every helper only
# looks at field names (plus a small set of well-known wrapper objects) and
# plain-text content.
# ---------------------------------------------------------------------------

# Field names carrying the whole JD body (description-style). Ordered by
# preference: first non-empty value wins.
JD_DESCRIPTION_FIELDS = (
    "_generic_description",   # internal detail-enrichment provenance (highest priority)
    "jobDescription", "job_description", "jobdesc", "jobDesc",
    "description", "positionDescription", "position_description",
    "content", "jd_content", "jdContent", "jd", "overview", "summary",
    "workContent", "work_content", "jobBody", "job_body",
    "detailDescription", "detail_description", "postContent", "post_content",
    "richText", "rich_text", "desc",
    "responsibilitiesDescription", "responsibilityDescription",
    "requirementsDescription", "requirementDescription",
)

# Field names carrying the responsibilities half of a split JD.
JD_RESPONSIBILITY_FIELDS = (
    "_generic_responsibilities",  # internal detail-enrichment provenance
    "jobResponsibility", "job_responsibility", "responsibilities",
    "responsibility", "jobResponsibilities", "duty", "duties", "jobDuty",
    "job_duty", "postDuties", "post_duties", "workDuty", "work_duties",
)

# Field names carrying the requirements half of a split JD.
JD_REQUIREMENT_FIELDS = (
    "_generic_requirements",  # internal detail-enrichment provenance
    "jobRequirement", "job_requirement", "requirements", "requirement",
    "qualifications", "qualification", "jobRequirements", "requirementsDesc",
    "requirements_description", "abilityRequirement", "ability_requirement",
    "competency", "competencies",
)

# Well-known wrapper objects that may hold JD sub-fields (e.g. zhiye's
# projectPositionDto.jobResponsibility / jobRequirement).
JD_WRAPPER_FIELDS = ("projectPositionDto", "positionDto", "jobDto")

# Chinese section headings used inside JD bodies (recognized verbatim at line
# start). Used by heading-aware splitting and by the completeness heuristic.
CHINESE_RESPONSIBILITY_HEADINGS = (
    "岗位职责", "工作职责", "职位职责", "职责描述", "岗位描述", "工作内容", "职位描述",
    "责任描述", "主要职责", "工作职能",
)
CHINESE_REQUIREMENT_HEADINGS = (
    "任职要求", "任职资格", "职位要求", "岗位要求", "资格要求", "任职条件",
    "招聘要求", "应聘要求", "基本要求",
)

# ASCII equivalents recognized the same way.
ENGLISH_RESPONSIBILITY_HEADINGS = (
    "responsibilities", "what you'll do", "what you will do", "the role",
    "job responsibilities", "key responsibilities", "about the role",
)
ENGLISH_REQUIREMENT_HEADINGS = (
    "requirements", "qualifications", "what we're looking for",
    "what we are looking for", "what you bring", "basic qualifications",
    "preferred qualifications",
)

# Naming-style field names (any script) that signal a description-style field
# during discovery-time shape inference.
JD_FIELD_PATTERN = re.compile(
    r"(?i)^(job_?desc(ription)?|jobdesc|position_?desc(ription)?|"
    r"jd_?content|jd|content|overview|responsibilit(y|ies)|dut(y|ies)|"
    r"requirement(s)?|qualification(s)?|desc(ription)?)$"
)


def _string_value(value: Any) -> str | None:
    """Return a non-empty plain string for scalar-ish JD values, else None."""
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, dict):
        # e.g. {"text": ...} / {"content": ...} / {"name": ...} wrappers
        for key in ("text", "content", "value", "name", "description"):
            child = value.get(key)
            if isinstance(child, str) and child.strip():
                return child
    return None


def _pick_fields(raw: dict, names: tuple[str, ...]) -> tuple[str | None, str | None]:
    """First non-empty value among *names* (top level), plus its field name."""
    for name in names:
        value = _string_value(raw.get(name))
        if value is not None:
            return value, name
    return None, None


def pick_jd_fields(raw: dict) -> dict[str, Any]:
    """Recognize JD content inside a heterogeneous record.

    Returns a dict with keys:
      - ``description``: description-style value (or None)
      - ``description_field``: field name it came from (or None)
      - ``responsibilities`` / ``requirements``: split-style values (or None)
      - ``responsibilities_field`` / ``requirements_field``: field names
      - ``list_jd_state``: ``"FULL_TEXT"`` (a whole-JD body exists),
        ``"SPLIT"`` (responsibility+requirement halves),
        ``"SUMMARY"`` (some description-style text but neither of the above),
        or ``"ABSENT"``.

    Generic by construction: name lists only, plus well-known wrapper dicts.
    """
    description, description_field = _pick_fields(raw, JD_DESCRIPTION_FIELDS)
    responsibilities, responsibilities_field = _pick_fields(raw, JD_RESPONSIBILITY_FIELDS)
    requirements, requirements_field = _pick_fields(raw, JD_REQUIREMENT_FIELDS)

    # Well-known wrapper objects may hold the split halves.
    for wrapper in JD_WRAPPER_FIELDS:
        nested = raw.get(wrapper)
        if not isinstance(nested, dict):
            continue
        if responsibilities is None:
            responsibilities, responsibilities_field = _pick_fields(
                nested, ("jobResponsibility", "responsibilities", "duty", "duties")
            )
            if responsibilities_field is not None:
                responsibilities_field = f"{wrapper}.{responsibilities_field}"
        if requirements is None:
            requirements, requirements_field = _pick_fields(
                nested, ("jobRequirement", "requirements", "requirement", "qualifications")
            )
            if requirements_field is not None:
                requirements_field = f"{wrapper}.{requirements_field}"

    if description is not None:
        state = "FULL_TEXT"
    elif responsibilities is not None or requirements is not None:
        state = "SPLIT"
    else:
        state = "ABSENT"
    # A whole-JD body that is too short to be a real JD is only a summary, not
    # full text; split halves always count as real content.
    if state == "FULL_TEXT" and responsibilities is None and requirements is None:
        if not credible_body(description or ""):
            state = "SUMMARY"
    return {
        "description": description,
        "description_field": description_field,
        "responsibilities": responsibilities,
        "responsibilities_field": responsibilities_field,
        "requirements": requirements,
        "requirements_field": requirements_field,
        "list_jd_state": state,
    }


def _jd_heading_hits(body: str) -> int:
    lowered = body.lower()
    hits = 0
    for heading in CHINESE_RESPONSIBILITY_HEADINGS + ENGLISH_RESPONSIBILITY_HEADINGS:
        if heading in lowered:
            hits += 1
            break
    for heading in CHINESE_REQUIREMENT_HEADINGS + ENGLISH_REQUIREMENT_HEADINGS:
        if heading in lowered:
            hits += 1
            break
    return hits


def credible_body(body: str | None) -> bool:
    """Heuristic: does this text look like a real JD body rather than a teaser?

    A body is credible when it is long enough and either carries recognizable
    responsibility/requirement headings or is long enough that it clearly is
    the full posting text. Keeps \n and bullets untouched — length only.

    A short body that carries BOTH a responsibility heading AND a requirement
    heading still has real section structure and counts as the complete JD.
    """
    text = body or ""
    if len(text) >= 500:
        return True
    hits = _jd_heading_hits(text)
    if hits >= 2:
        return True
    if len(text) >= 160 and hits:
        return True
    return False


def needs_detail_fetch(raw: dict, detail_mode: str) -> bool:
    """Per-job decision on whether the official detail must still be fetched.

    ``detail_mode`` is the plan-level mode (LIST_SUFFICIENT / DETAIL_REQUIRED /
    DETAIL_FALLBACK / ...). The per-job check uses generic JD recognition: the
    list is sufficient when it already carries a credible JD body, or both
    split halves (responsibilities AND requirements). A lone half, a short
    summary, or nothing at all means the detail must be fetched.
    """
    if detail_mode == "LIST_SUFFICIENT":
        return False
    if detail_mode == "DETAIL_REQUIRED":
        return True
    recognized = pick_jd_fields(raw)
    state = recognized["list_jd_state"]
    if state in ("SUMMARY", "ABSENT"):
        return True
    if state == "FULL_TEXT":
        return False
    # SPLIT: both halves must be present for the list record to be complete.
    return not (recognized["responsibilities"] and recognized["requirements"])


# ---------------------------------------------------------------------------
# STEP 51: text safety for malformed Unicode.
#
# Lone surrogates (U+D800–U+DFFF without a matching pair) cannot be encoded
# to UTF-8, so one such character anywhere in a Job makes JSON serialization
# and Markdown file writes crash. Normalization drops only the invalid
# units — every other character (CJK, emoji, tabs, newlines, bullets) is kept
# byte-for-byte.
# ---------------------------------------------------------------------------

_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def safe_text(value: Any) -> Any:
    """Drop lone surrogates from strings; pass everything else through."""
    if isinstance(value, str) and _LONE_SURROGATE_RE.search(value):
        return _LONE_SURROGATE_RE.sub("", value)
    return value


def safe_lines(values: Any) -> Any:
    """Normalize a list of strings (or a single value) for text safety."""
    if isinstance(values, list):
        return [safe_text(x) for x in values]
    return safe_text(values)


def _not_already_contained(haystack: str, needle: str) -> bool:
    """True when *needle* text is not simply duplicated inside *haystack*."""
    flat_a = "".join(str(haystack or "").lower().split())
    flat_b = "".join(str(needle or "").lower().split())
    return not flat_b or flat_b not in flat_a


def canonical_jd(raw: dict) -> tuple[str | None, str, list[str], list[str]]:
    """Build the canonical full JD from a record without dropping source text.

    Returns ``(full_jd, state, responsibilities_lines, requirements_lines)``.
    Rules:
      - whole-JD fields (description-style) are kept verbatim as the JD;
      - when split halves (responsibility/requirement) also exist and are not
        merely duplicated inside the description, they are appended under
        their own heading so no source text is lost;
      - when only split halves exist, both halves are preserved under their
        own heading (duty/requirement);
      - when nothing is present, returns ``(None, "ABSENT", [], [])``.
    ``state`` is one of ``FULL_TEXT``, ``SPLIT``, ``SUMMARY``, ``ABSENT``.
    """
    recognized = pick_jd_fields(raw)
    description = recognized["description"]
    responsibilities = recognized["responsibilities"]
    requirements = recognized["requirements"]
    state = recognized["list_jd_state"]

    if description is not None:
        parts: list[str] = [description.strip()]
        if responsibilities and _not_already_contained(description, responsibilities):
            parts.append("岗位职责\n" + responsibilities.strip())
        if requirements and _not_already_contained(description, requirements):
            parts.append("任职要求\n" + requirements.strip())
        return "\n\n".join(parts) or None, state, [], []
    if state == "SPLIT":
        parts = []
        if responsibilities is not None:
            parts.append("岗位职责\n" + responsibilities.strip())
        if requirements is not None:
            parts.append("任职要求\n" + requirements.strip())
        return ("\n\n".join(parts) or None), state, [], []
    return None, "ABSENT", [], []
