from __future__ import annotations

import json
import re
from copy import deepcopy
from time import perf_counter
from typing import Any

from job_extractor.discovery.network_analyzer import find_array_info, get_path, sensitive

DYNAMIC_FAILURE_REASONS = ("HYDRATION_TIMEOUT","NO_DYNAMIC_JOB_SOURCE","GRAPHQL_PAGINATION_UNKNOWN","SPA_DETAIL_NOT_RESOLVED","JD_RENDER_TIMEOUT","JOB_CARD_LINK_MISMATCH","BROWSER_API_NOT_REPLAYABLE")
DISCOVERY_PHASES=("INITIAL","HYDRATION","SCROLL","PAGINATION","DETAIL")

def is_pagination_control(label:str)->bool:
    return bool(re.search(r"(?i)\b(load more|show more(?: jobs)?|more jobs|next(?: page)?|previous(?: page)?|page\s+\d+|加载更多|下一页|上一页)\b",label or ""))


def visible_total_evidence(text: str, locator: str | None = "body") -> tuple[list[dict[str,Any]],bool]:
    """Extract only totals attached to explicit recruitment-result language."""
    patterns = (
        (r"(?i)\bshowing\s+\d+\s*(?:to|[-–—])\s*\d+\s+of\s+(\d[\d,]*)\b", "SHOWING_RANGE", "HIGH"),
        (r"(?i)\b(\d[\d,]*)\s+(?:open\s+)?(?:jobs?|positions?|openings?|opportunities|results?|职位|岗位|结果)\b", "NUMBER_THEN_JOB_NOUN", "HIGH"),
        (r"(?i)\b(?:jobs?|positions?|openings?|opportunities|results?|职位|岗位|结果)\s+(?:found\s*)?[:(]?\s*(\d[\d,]*)\b", "JOB_NOUN_THEN_NUMBER", "MEDIUM"),
        (r"(?i)\bfound\s+(\d[\d,]*)\s+(?:jobs?|positions?|openings?|opportunities|results?)\b", "FOUND_TOTAL", "HIGH"),
    )
    evidence=[];seen=set()
    for pattern,evidence_type,confidence in patterns:
        for match in re.finditer(pattern,text):
            value=int(match.group(1).replace(",",""));source=" ".join(match.group(0).split())
            key=(value,source.lower(),evidence_type)
            if key in seen:continue
            seen.add(key);evidence.append({"value":value,"source_text":source[:240],"confidence":confidence,"evidence_type":evidence_type,"locator":locator})
    return evidence,len({x["value"] for x in evidence})>1


def visible_total(text: str) -> int | None:
    evidence,conflict=visible_total_evidence(text)
    if conflict or not evidence:return None
    return evidence[0]["value"]


def flatten_paths(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    if depth > 6 or not isinstance(value, dict):
        return {}
    found = {}
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, dict):
            found.update(flatten_paths(child, path, depth + 1))
        else:
            found[path] = child
    return found


def safe_graphql_body(body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    safe = True

    def clean(value: Any):
        nonlocal safe
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if sensitive(str(key)):
                    safe = False
                    continue
                result[key] = clean(child)
            return result
        if isinstance(value, list):
            return [clean(x) for x in value[:500]]
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        return None

    allowed = {k: v for k, v in body.items() if k in ("operationName", "query", "variables", "extensions")}
    return clean(allowed), safe and bool(allowed.get("query") or allowed.get("operationName"))


def graphql_shape(body: dict[str, Any], payload: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return {}
    arrays = [x for x in find_array_info(payload, max_depth=7) if x[0].lower().endswith((".edges", ".nodes", ".items", ".results"))]
    if not arrays:
        return {}
    path, length, sample = max(arrays, key=lambda x: x[1])
    item_path = None
    records = sample
    if sample and all(isinstance(x, dict) and isinstance(x.get("node"), dict) for x in sample):
        records = [x["node"] for x in sample]
        item_path = "node"
    fields = sorted({str(k) for x in records if isinstance(x, dict) for k in x})[:100]
    connection_path = path.rsplit(".", 1)[0]
    page_info_path = f"{connection_path}.pageInfo"
    page_info = get_path(payload, page_info_path)
    flat = flatten_paths(body.get("variables") or {}, "variables")
    result = {"candidate_list_path": path, "array_length": length, "sample_field_names": fields,
              "list_item_path": item_path, "graphql_operation": body.get("operationName"),
              "graphql_page_info_path": page_info_path if isinstance(page_info, dict) else None}
    if isinstance(page_info, dict):
        if "endCursor" in page_info:
            result["next_cursor_field"] = f"{page_info_path}.endCursor"
        if "hasNextPage" in page_info:
            result["has_more_field"] = f"{page_info_path}.hasNextPage"
    for path_name in flat:
        name = path_name.rsplit(".", 1)[-1].lower()
        if name in ("after", "cursor", "endcursor"):
            result.update({"pagination_type": "GRAPHQL_CURSOR", "page_param": path_name})
        elif name in ("offset", "skip"):
            result.update({"pagination_type": "GRAPHQL_OFFSET", "page_param": path_name})
        elif name in ("page", "pageindex", "pageno", "current"):
            result.update({"pagination_type": "GRAPHQL_PAGE", "page_param": path_name})
        if name in ("limit", "first", "pagesize", "page_size", "size"):
            result["page_size_param"] = path_name
    return result


def serialized_states(page) -> list[Any]:
    values = []
    try:
        nodes = page.locator('script[type="application/json"],script#__NEXT_DATA__')
        for index in range(min(nodes.count(), 50)):
            try:
                raw = nodes.nth(index).text_content() or ""
                if 2 <= len(raw) <= 10_000_000:
                    values.append(json.loads(raw))
            except Exception:
                pass
    except Exception:
        pass
    return values


def wait_for_hydration(page, candidate_count, timeout_ms: int = 5000, interval_ms: int = 250) -> dict[str, Any]:
    stable = 0; previous = None; elapsed = 0
    while elapsed < timeout_ms:
        try:
            state = (page.locator('a[href]').count(), len(page.locator("body").inner_text()))
        except Exception:
            state = None
        snapshot = (state, candidate_count())
        stable = stable + 1 if snapshot == previous else 0; previous = snapshot
        if stable >= 2:
            return {"stabilized": True, "elapsed_ms": elapsed, "state": state}
        page.wait_for_timeout(interval_ms); elapsed += interval_ms
    return {"stabilized": False, "elapsed_ms": elapsed, "state": previous[0] if previous else None}


def wait_for_readiness_consensus(page, candidate_count, timeout_ms: int = 10000) -> dict[str, Any]:
    """Give asynchronous module chains a bounded chance before declaring no source."""
    before = candidate_count();started=perf_counter()
    network_idle = False
    elapsed=max(0,int((perf_counter()-started)*1000));interval=250
    while candidate_count()<=before and elapsed<timeout_ms:
        page.wait_for_timeout(min(interval,timeout_ms-elapsed));elapsed+=interval
    hydration={"stabilized":False}
    if candidate_count()>before:
        hydration=wait_for_hydration(page,candidate_count,timeout_ms=min(1500,timeout_ms),interval_ms=250)
    after = candidate_count()
    return {
        "network_idle": network_idle,
        "source_observed": after > before,
        "candidate_count_before": before,
        "candidate_count_after": after,
        "dom_stabilized": hydration["stabilized"],
    }


def wait_for_dynamic_jd(page, title: str | None = None, preferred_selector: str | None = None, timeout_ms: int = 4000, interval_ms: int = 250):
    from job_extractor.discovery.dom_semantics import extract_credible_jd
    previous = -1; stable = 0; elapsed = 0
    while elapsed <= timeout_ms:
        jd, selector = extract_credible_jd(page, title, preferred_selector)
        if jd:
            return jd, selector, {"resolved": True, "elapsed_ms": elapsed}
        try:
            length = len(page.locator("body").inner_text())
        except Exception:
            length = 0
        stable = stable + 1 if length == previous else 0; previous = length
        if stable >= 3 and elapsed >= 750:
            break
        page.wait_for_timeout(interval_ms); elapsed += interval_ms
    return None, None, {"resolved": False, "elapsed_ms": elapsed}


def pagination_stop(*, official_total: int | None, unique_count: int, previous_unique: int, has_more: bool | None, cursor: Any = None, seen_cursors: set | None = None) -> str | None:
    if official_total is not None and unique_count >= official_total:return "OFFICIAL_TOTAL_REACHED"
    if has_more is False:return "HAS_MORE_FALSE"
    if cursor is not None and seen_cursors is not None and cursor in seen_cursors:return "CURSOR_EXHAUSTED"
    if unique_count <= previous_unique:return "NO_NEW_UNIQUE_JOBS"
    return None


def navigation_trust(source_host:str,target_host:str,final_host:str,redirect_count:int,credible_detail:bool)->bool:
    return bool(source_host and target_host and final_host and credible_detail and redirect_count<=3 and final_host in (source_host,target_host))


def set_path(value: dict[str, Any], path: str, new_value: Any) -> None:
    parts = [x for x in path.split(".") if x and x != "$"]; current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):child = {}; current[part] = child
        current = child
    if parts:current[parts[-1]] = new_value


def graphql_next_values(values: dict[str, Any], pagination_type: str, page_param: str | None, page_size_param: str | None, cursor: Any, batch_size: int) -> dict[str, Any]:
    result = deepcopy(values)
    if pagination_type == "GRAPHQL_CURSOR" and page_param:set_path(result, page_param, cursor)
    elif pagination_type == "GRAPHQL_OFFSET" and page_param:
        current = get_path(result, page_param) or 0; size = get_path(result, page_size_param) if page_size_param else batch_size
        set_path(result, page_param, int(current) + int(size or batch_size))
    elif pagination_type == "GRAPHQL_PAGE" and page_param:set_path(result, page_param, int(get_path(result, page_param) or 0) + 1)
    return result
