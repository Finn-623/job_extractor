"""N9.9: generic company/brand identity from domains (no per-site branches).

Priority chain (shared contract):

    job-level explicit company
      > site-level explicit company (API payload / page title / discovery
        observations — handled by resolve_site_company / unified_company)
      > trusted site identity (verified alias registry → canonical name)
      > domain brand inference (this module's generic fallback)
      > null

Most recruitment sites get a usable brand label; only truly opaque hosts
(stay unknown). The registry (``KNOWN_COMPANY_NAMES``) is a high-confidence
alias/canonical correction layer — never the only source.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from job_extractor.discovery.provider_fingerprint import KNOWN_PROVIDER_HOSTS
from job_extractor.job_normalize import unified_company
from job_extractor.output_layout import KNOWN_COMPANY_NAMES

# Generic recruitment/infrastructure labels: never a company brand by themselves.
_INFRA_LABELS = frozenset({
    "www", "api", "xg", "m", "mobile", "app", "web", "portal",
    "careers", "career", "jobs", "job", "talent", "recruit", "recruitment",
    "campus", "hr", "globalhr", "global", "unknown", "placeholder",
    "company", "employer", "official",
})

# Hosted recruitment platforms: the platform name must never become the
# employer — the tenant subdomain (when present) is the brand candidate.
_RECRUIT_PLATFORM_SUFFIXES = (".mokahr.com", ".feishu.cn", ".zhiye.com",
                              ".beisen.com", ".hotjob.cn", ".iguopin.com")

# Reserved / documentation domains are meaningless as company evidence.
_RESERVED_ROOTS = frozenset({"example", "test", "invalid", "localhost"})

# Two-label TLDs that must keep three labels as the registrable domain.
_MULTIPART_TLDS = frozenset({
    "com.cn", "com.hk", "com.tw", "com.au", "com.sg", "co.jp", "co.uk",
    "co.kr", "org.cn", "net.cn", "gov.cn",
})

_DIGIT_ONLY = re.compile(r"^\d+$")


def _host_of(source: str | None) -> str | None:
    """Hostname from a full URL, a bare host, or an API host."""
    value = (source or "").strip()
    if not value:
        return None
    parsed = urlsplit(value if "//" in value else f"//{value}")
    host = (parsed.hostname or "").lower()
    return host or None


def _is_recruit_platform(host: str) -> bool:
    suffixes = [s for values in KNOWN_PROVIDER_HOSTS.values() for s in values]
    return (host.endswith(_RECRUIT_PLATFORM_SUFFIXES)
            or any(host.endswith(s) or host == s.lstrip(".") for s in suffixes))


def _clean_token(token: str) -> str | None:
    """Trim a trailing infrastructure word from a compound label (pddglobalhr → pdd).

    Only words of 3+ letters may be trimmed off the end — a short label like
    ``m`` must never amputate a real brand token (firm → fir).
    """
    for suffix in sorted(_INFRA_LABELS, key=len, reverse=True):
        if len(suffix) >= 3 and token.endswith(suffix) and len(token) > len(suffix):
            token = token[:-len(suffix)]
            break
    if (not token or token in _INFRA_LABELS or len(token) < 2
            or _DIGIT_ONLY.match(token)):
        return None
    return token


def _label(token: str) -> str:
    return token[0].upper() + token[1:]


def resolve_collection_company(
    jobs: list[Any] | None,
    explicit_site_company: str | None,
    source_url: str | None,
) -> str | None:
    """N9.10: THE unified company resolution entry for every collection path.

    Priority: job-level explicit company (all jobs agree) > site-level
    explicit company (API payload / page title / tenant / discovery
    observations) > trusted alias registry + generic domain brand inference
    (``infer_site_company``) > ``None``. Recruitment-platform hosts can never
    leak through as the company. ``Job.company`` semantics are unchanged:
    the resolved value lands on ``CollectionResult.company`` only.
    """
    return (unified_company(jobs or [])
            or (explicit_site_company or None)
            or infer_site_company(source_url))


def infer_site_company(source: str | None) -> str | None:
    """Site-level company from the source host.

    Full URL, bare host and API host all work. Returns the verified canonical
    name when the brand token is in the trusted alias registry, else a stable
    title-case brand label, else ``None`` for opaque/meaningless hosts.
    Recruitment-platform domains never become the employer: on a platform
    host only the tenant subdomain is a candidate.
    """
    host = _host_of(source)
    if not host or "." not in host:
        return None
    labels = host.split(".")
    keep = 3 if ".".join(labels[-2:]) in _MULTIPART_TLDS else 2
    sub_labels, rd_labels = labels[:-keep], labels[-keep:]
    rd_root = rd_labels[0]
    if rd_root in _RESERVED_ROOTS:
        return None
    if _is_recruit_platform(host):
        # tenant subdomain only — the platform itself is never the employer
        for label in reversed(sub_labels):
            token = _clean_token(label)
            if token:
                return KNOWN_COMPANY_NAMES.get(token) or _label(token)
        return None
    if rd_root.startswith("example"):  # example-host.com etc. — meaningless
        return None
    candidates = [label for label in (*sub_labels, rd_root)
                  if label not in _INFRA_LABELS]
    token = _clean_token(candidates[-1]) if candidates else None
    if not token:
        return None
    return KNOWN_COMPANY_NAMES.get(token) or _label(token)
