"""Site-level (recruitment-site owner) company identity resolution.

Distinguishes two concepts that must never be conflated:

- ``site-level company``: the group/tenant that owns the recruitment site
  (e.g. a hosted ``<group>.<platform>.com`` portal aggregating many legal
  entities). This becomes ``CollectionResult.company``.
- ``job-level company``: the specific legal entity hiring for one posting.
  This stays in ``Job.company`` and is never overwritten by the site name.

Resolution only consumes evidence that discovery already captured:

- hostname/subdomain brand token on a known hosted recruiting platform
- tenant/company nodes inside already-observed JSON responses
  (root nodes without a parent company id are site owners)
- the rendered page title brand
- job-level company names from the accepted list API — corroboration only

Fail-closed: without corroborated human-readable evidence the result is
``None`` and the existing multi-company protection semantics stay intact.
No hostname→company translation table and no per-site special cases.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit

from job_extractor.discovery.provider_fingerprint import KNOWN_PROVIDER_HOSTS

# Hosted recruiting platforms (domain suffixes only — never employer names).
# Reuses the adapter/provider knowledge already in the codebase; iguopin is a
# hosted recruitment platform (tenant subdomains like coscoshipping.iguopin.com).
HOSTED_PLATFORM_SUFFIXES: tuple[str, ...] = tuple(sorted(
    {suffix for suffixes in KNOWN_PROVIDER_HOSTS.values() for suffix in suffixes}
    | {".iguopin.com"},
))
_PLATFORM_TOKENS = {suffix.strip(".").split(".")[0] for suffix in HOSTED_PLATFORM_SUFFIXES}

# Recruitment-platform vocabulary: page titles that name the site, not the owner.
_TITLE_SITE_NOISE = re.compile(r"(?i)(?:招聘|jobs|careers|position|opening|hiring|岗位|职位|官网|人才网)")
_GENERIC_SITE_BRANDS = {"招聘官网", "jobs", "careers"}

# Bounded payload walking keeps resolution O(existing observations) only.
_MAX_NODES = 3000
_MAX_DEPTH = 6
_MAX_JOB_NAMES = 120

_COMPANY_ID_KEYS = ("company_id", "companyid", "org_id", "orgid")
_PARENT_ID_KEYS = ("company_parent_id", "parent_company_id")
_NAME_KEYS = ("short_name", "show_name", "name")


@dataclass
class SiteIdentity:
    company: str
    evidence: list[str] = field(default_factory=list)


def hosted_platform(host: str) -> bool:
    host = (host or "").lower()
    return any(host.endswith(suffix) or host == suffix.lstrip(".") for suffix in HOSTED_PLATFORM_SUFFIXES)


def brand_subdomain_token(url: str) -> str | None:
    """Brand token of a hosted tenant site, e.g. coscoshipping.iguopin.com → coscoshipping.

    ``www`` and bare platform hosts carry no brand token. Platform tokens
    themselves can never become employer candidates.
    """
    host = (urlsplit(url or "").hostname or "").lower()
    if not hosted_platform(host):
        return None
    for suffix in HOSTED_PLATFORM_SUFFIXES:
        if host.endswith(suffix):
            prefix = host[: -len(suffix)].rstrip(".")
            token = prefix.split(".")[-1] if prefix else ""
            if token and token != "www" and token not in _PLATFORM_TOKENS:
                return token
    return None


def _latin_brand(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _token_match(candidate: str, token: str | None) -> bool:
    """Latin-token tie between a hostname brand token and a name candidate.

    ``coscoshipping`` matches ``COSCO SHIPPING``; CJK candidates simply do
    not match (the CJK tie comes from job-name corroboration instead).
    """
    if not token:
        return False
    latin = _latin_brand(candidate)
    return bool(latin) and (latin == token or token in latin or latin in token)


def dominant_brand_prefix(job_company_names: Iterable[str], min_names: int = 5, min_coverage: float = 0.3) -> str | None:
    """Most widespread leading brand token across distinct job company names.

    Corroboration key only — never used as the display name itself. A group
    portal whose legal entities share the same brand prefix (e.g. many names
    starting with the same four CJK characters) yields that prefix.
    """
    names = sorted({n.strip() for n in job_company_names if isinstance(n, str) and n.strip()})
    if len(names) < min_names:
        return None
    counts: dict[str, int] = {}
    for name in names:
        cjk = re.match(r"^([\u4e00-\u9fff]{2,6})", name)
        latin = re.match(r"^[A-Za-z0-9][A-Za-z0-9 &.-]{2,15}", name)
        head = cjk.group(1) if cjk else (latin.group(1).strip() if latin else "")
        for length in range(2, len(head) + 1):
            counts[head[:length]] = counts.get(head[:length], 0) + 1
    if not counts:
        return None
    # Longest prefix that still covers the widest set of names (ties → longer).
    best_count = max(counts.values())
    if best_count < min_names or best_count / len(names) < min_coverage:
        return None
    return max((p for p, c in counts.items() if c == best_count), key=len)


def _title_brand(page_title: str | None) -> str | None:
    value = (page_title or "").strip()
    if not value or re.search(r"\s+[|–—-]\s+", value):
        return None
    value = re.sub(r"(?:\s*(?:人才招聘平台|招聘平台|招聘官网|人才招聘|校园招聘|社会招聘|招聘|jobs|careers))+$", "", value, flags=re.I)
    value = value.strip(" -|–—·")
    if not value or len(value) > 30 or _TITLE_SITE_NOISE.search(value) or value.lower() in _GENERIC_SITE_BRANDS:
        return None
    return value


def _walk_nodes(payload: Any) -> Iterable[dict[str, Any]]:
    """Yield dict nodes from an observed JSON payload within strict bounds."""
    stack = [(payload, 0)]
    seen = 0
    while stack and seen < _MAX_NODES:
        node, depth = stack.pop()
        seen += 1
        if isinstance(node, dict):
            yield node
            if depth < _MAX_DEPTH:
                for value in node.values():
                    stack.append((value, depth + 1))
        elif isinstance(node, list):
            if depth < _MAX_DEPTH:
                for value in node:
                    stack.append((value, depth + 1))


def _tenant_root_candidates(observations: Iterable[Any]) -> list[dict[str, Any]]:
    """Company-node dicts from observed payloads that look like site owners.

    A root node carries a company id without a parent company id — the shape
    hosted platforms use for the tenant/site owner (children are subsidiaries).
    """
    roots: list[dict[str, Any]] = []
    for observation in observations:
        payload = getattr(observation, "payload", None)
        if payload is None:
            continue
        for node in _walk_nodes(payload):
            keys = {str(k).lower() for k in node}
            if not any(k in keys for k in _COMPANY_ID_KEYS):
                continue
            if not any(k in keys for k in _NAME_KEYS):
                continue
            if any(node.get(k) not in (None, "") for k in _PARENT_ID_KEYS if k in keys):
                continue  # subsidiary/child node — not the site owner
            roots.append(node)
    return roots


def _corroborated(candidate: str, brand_prefix: str | None, token: str | None, page_title: str | None) -> list[str]:
    evidence: list[str] = []
    if brand_prefix and (brand_prefix in candidate or candidate in brand_prefix):
        evidence.append(f"job-company brand corroboration: {brand_prefix}")
    if _token_match(candidate, token):
        evidence.append(f"hostname brand token match: {token}")
    title = _title_brand(page_title)
    if title and (title in candidate or candidate in title):
        evidence.append(f"page title corroboration: {title}")
    return evidence


def _platform_brand(candidate: str) -> bool:
    latin = _latin_brand(candidate)
    return any(latin == platform or platform in latin for platform in _PLATFORM_TOKENS)


def resolve_site_company(
    url: str,
    observations: Iterable[Any],
    list_candidates: Iterable[Any],
    page_title: str | None = None,
) -> SiteIdentity | None:
    """Resolve the recruitment-site owner for a hosted multi-company portal.

    Returns ``None`` — keeping the multi-company protection semantics — unless
    a tenant/site-owner name from real evidence survives corroboration.
    """
    token = brand_subdomain_token(url)
    if not token:
        return None  # not a branded tenant portal: nothing to resolve here
    job_names: list[str] = []
    for candidate in list_candidates:
        names = getattr(candidate, "observed_company_names", None)
        if isinstance(names, list):
            job_names.extend(names)
    brand_prefix = dominant_brand_prefix(job_names)
    candidates: list[tuple[str, str]] = []  # (source, name)
    for node in _tenant_root_candidates(observations):
        for key, source in (("short_name", "tenant short_name"), ("show_name", "tenant show_name"), ("name", "tenant name")):
            value = node.get(key) if isinstance(node, dict) else None
            if isinstance(value, str) and len(value.strip()) >= 3:
                candidates.append((source, value.strip()))
    title_brand = _title_brand(page_title)
    if title_brand:
        candidates.append(("page title", title_brand))
    for source, candidate in candidates:
        if _platform_brand(candidate):
            continue  # hosted-platform brand can never become the employer
        evidence = _corroborated(candidate, brand_prefix, token, page_title)
        if evidence:
            return SiteIdentity(company=candidate, evidence=[source, *evidence])
    return None
