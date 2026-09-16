"""Safely bind JD rendered inside job-list cards to runtime job records."""
from __future__ import annotations

import re
from collections import Counter
from hashlib import sha256
from time import perf_counter
from typing import Any
from urllib.parse import parse_qs, urlsplit

from job_extractor.field_semantics import credible_body

# Cards commonly present section labels as headings, newline labels, or
# bracketed inline labels.  Mapping is already card-bound and still requires
# both independent sections, so this does not inspect page-level prose.
RESP = re.compile(r"(?im)[\[【]?\s*(?:岗位职责|工作职责|职位职责|职位描述|职责|responsibilities?|job description)\s*[\]】:：]?\s*")
REQ = re.compile(r"(?im)[\[【]?\s*(?:任职要求|任职资格|岗位要求|职位要求|requirements?|qualifications?)\s*[\]】:：]?\s*")
ID_ATTRS = ("data-job-id", "data-id", "data-position-id", "data-post-id", "href", "data-href", "data-route")
DOM_CARD_SCAN_JS = r"""
() => Array.from(document.querySelectorAll('a[href],[data-job-id],[data-id],[data-position-id],[data-post-id],[data-href],[data-route]'))
  .slice(0,1500)
  .map(n=>({text:(n.innerText||'').trim(),href:n.getAttribute('href')||'',
    'data-href':n.getAttribute('data-href')||'', 'data-route':n.getAttribute('data-route')||'',
    'data-job-id':n.getAttribute('data-job-id')||'', 'data-id':n.getAttribute('data-id')||'',
    'data-position-id':n.getAttribute('data-position-id')||'', 'data-post-id':n.getAttribute('data-post-id')||''}))
  .filter(x=>x.text)
"""


def _route_identity(card: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return an explicit DOM/route identity; never infer it from card prose."""
    for key in ID_ATTRS[:-3]:
        value = str(card.get(key) or "").strip()
        if value:
            return value, key
    for key in ("href", "data-href", "data-route"):
        route = str(card.get(key) or "").strip()
        if not route:
            continue
        parsed = urlsplit(route.replace("#/", "/", 1))
        query = parse_qs(parsed.query)
        for name in ("jobId", "job_id", "positionId", "position_id", "postingId", "requisitionId", "id"):
            if query.get(name) and query[name][0]:
                return query[name][0], key
        match = re.search(r"(?i)/(?:jobs?|positions?|postings?|requisitions?)/([^/?#]+)", parsed.path)
        if match:
            return match.group(1), key
    return None, None


def extract_rendered_list_batch(cards: list[dict[str, Any]], *, id_field: str, title_field: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Convert identity-bound rendered cards to generic runtime records.

    Title-only identity is accepted only for unique titles on cards that carry
    both JD sections. Duplicate titles are reported as ambiguous and skipped.
    """
    candidates = [card for card in cards if isinstance(card, dict) and str(card.get("text") or "").strip()]
    titles = [str(card.get("text") or "").strip().splitlines()[0].strip() for card in candidates]
    title_counts = Counter(title for title in titles if title)
    records: list[dict[str, Any]] = []
    ambiguous = 0
    identity_sources: Counter[str] = Counter()
    seen: set[str] = set()
    for card, title in zip(candidates, titles):
        responsibilities, requirements = _sections(str(card.get("text") or ""))
        identity, identity_source = _route_identity(card)
        if not identity:
            if not title or title_counts[title] != 1 or not (responsibilities and requirements):
                if title and title_counts[title] > 1:
                    ambiguous += 1
                continue
            identity = "title:" + sha256(title.encode("utf-8")).hexdigest()[:24]
            identity_source = "unique_title"
        identity = str(identity)
        if identity in seen:
            continue
        seen.add(identity); identity_sources[str(identity_source)] += 1
        record: dict[str, Any] = {id_field: identity, title_field: title,
                                  "_generic_detail_source": "BROWSER_RENDERED_LIST"}
        route = str(card.get("href") or card.get("data-href") or card.get("data-route") or "").strip()
        if route:
            record["_generic_detail_url"] = route
        if responsibilities and requirements:
            record.update({"_generic_description": f"岗位职责\n{responsibilities}\n\n任职要求\n{requirements}",
                           "_generic_responsibilities": responsibilities,
                           "_generic_requirements": requirements})
        records.append(record)
    ids = [str(record[id_field]) for record in records]
    signature = sha256("\x1f".join(ids).encode("utf-8")).hexdigest()
    set_signature = sha256("\x1f".join(sorted(ids)).encode("utf-8")).hexdigest()
    return records, {"raw_dom_card_count": len(candidates), "unique_identity_count": len(ids),
                     "ambiguous_records": ambiguous, "ordered_ids_hash": signature,
                     "set_ids_hash": set_signature, "identity_sources": dict(identity_sources)}


def read_rendered_list_batch(page, *, id_field: str, title_field: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        cards = page.evaluate(DOM_CARD_SCAN_JS)
    except Exception:
        cards = []
    return extract_rendered_list_batch(cards if isinstance(cards, list) else [], id_field=id_field, title_field=title_field)


def _sections(text: str) -> tuple[str | None, str | None]:
    value = re.sub(r"[\t\r ]+", " ", text or "")
    response, requirement = RESP.search(value), REQ.search(value)
    if not response or not requirement:
        return None, None
    responsibilities = value[response.end():requirement.start()].strip(" \n:：")
    requirements = value[requirement.end():].strip(" \n:：")
    # Section headings are established by the containing card.  Individual
    # halves need meaningful text, not a second pair of headings.
    return (responsibilities if len(responsibilities) >= 40 else None,
            requirements if len(requirements) >= 40 else None)


def map_rendered_cards(records: list[dict[str, Any]], cards: list[dict[str, Any]], *, id_field: str, title_field: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Map independently extracted cards without title-only collision guesses."""
    title_counts: dict[str, int] = {}
    for record in records:
        title = str(record.get(title_field) or "").strip()
        if title: title_counts[title] = title_counts.get(title, 0) + 1
    by_id: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        # IDs are matched only against explicit identity-bearing attributes;
        # searching arbitrary card prose would make short IDs collide.
        blob = " ".join(str(card.get(key) or "") for key in ID_ATTRS)
        for record in records:
            value = str(record.get(id_field) or "").strip()
            if value and re.search(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", blob):
                by_id.setdefault(value, []).append(card)
    output = [dict(record) for record in records]
    stats = {"dom_jobs_seen": len(cards), "dom_jobs_with_jd": 0, "mapping_success": 0, "mapping_failed": 0, "mapping_ambiguous": 0, "dom_wait_ms": 0}
    for index, record in enumerate(records):
        rid = str(record.get(id_field) or "").strip(); title = str(record.get(title_field) or "").strip()
        candidates = by_id.get(rid, [])
        if not candidates and title and title_counts.get(title) == 1:
            candidates = [card for card in cards if title in str(card.get("text") or "")]
        if len(candidates) != 1:
            stats["mapping_ambiguous" if candidates else "mapping_failed"] += 1
            continue
        responsibilities, requirements = _sections(str(candidates[0].get("text") or ""))
        if not (responsibilities and requirements):
            stats["mapping_failed"] += 1
            continue
        output[index].update({"_generic_description": f"岗位职责\n{responsibilities}\n\n任职要求\n{requirements}",
                              "_generic_responsibilities": responsibilities,
                              "_generic_requirements": requirements,
                              "_generic_detail_source": "BROWSER_RENDERED_LIST"})
        stats["mapping_success"] += 1; stats["dom_jobs_with_jd"] += 1
    return output, stats


def observe_rendered_list_jd(page, records: list[dict[str, Any]], *, id_field: str | None, title_field: str | None, timeout_ms: int = 5000) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not id_field or not title_field or not records:
        return records, {"dom_jobs_seen": 0, "dom_jobs_with_jd": 0, "mapping_success": 0, "mapping_failed": len(records), "mapping_ambiguous": 0, "dom_wait_ms": 0}
    started = perf_counter(); prior = None; stable = 0; cards: list[dict[str, Any]] = []
    while (perf_counter() - started) * 1000 <= timeout_ms:
        try: cards = page.evaluate(DOM_CARD_SCAN_JS)
        except Exception: cards = []
        signature = tuple((str(x.get("href")), len(str(x.get("text") or ""))) for x in cards if isinstance(x, dict))
        stable = stable + 1 if signature == prior and signature else 0; prior = signature
        mapped, stats = map_rendered_cards(records, cards if isinstance(cards, list) else [], id_field=id_field, title_field=title_field)
        if stats["mapping_success"] and stable >= 2:
            stats["dom_wait_ms"] = int((perf_counter() - started) * 1000); return mapped, stats
        page.wait_for_timeout(250)
    mapped, stats = map_rendered_cards(records, cards if isinstance(cards, list) else [], id_field=id_field, title_field=title_field)
    stats["dom_wait_ms"] = int((perf_counter() - started) * 1000)
    return mapped, stats
