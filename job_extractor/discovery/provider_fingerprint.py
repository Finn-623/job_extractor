from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from job_extractor.discovery.models import DiscoveryResult, ProviderFingerprint, ProviderSignal

KNOWN_PROVIDER_HOSTS: dict[str, tuple[str, ...]] = {
    "moka": (".mokahr.com",),
    "zhiye": (".zhiye.com",),
    "feishu": (".jobs.feishu.cn",),
}

MOKA_API_PATH_RE = re.compile(r"/api/(?:outer/)?ats-apply/", re.I)
MOKA_ROUTE_RE = re.compile(r"/(?:social|campus)-recruitment/", re.I)
MOKA_ASSET_RE = re.compile(r"(?:mokahr\.com|recruitment-web-client|mage-i18n|moka-fe-public)", re.I)
MOKA_ENVELOPE_KEY = "necromancer"
MOKA_SCOPE_KEYS = {"siteid"}
MOKA_REQUEST_KEYS = {"orgid", "siteid"}


@dataclass
class FingerprintFeatures:
    host: str = ""
    api_paths: list[str] = field(default_factory=list)
    route_paths: list[str] = field(default_factory=list)
    script_assets: list[str] = field(default_factory=list)
    response_key_sets: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    request_key_sets: list[tuple[str, set[str]]] = field(default_factory=list)
    scope_keys: set[str] = field(default_factory=set)
    dom_status: str = ""
    dom_link_count: int = 0
    probable_list_api: bool = False


def _candidate_urls(discovery: DiscoveryResult) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for candidate in list(discovery.candidate_list_apis) + list(discovery.rejected_candidates):
        values.append((candidate.url or "", candidate.method or ""))
    return values


def extract_features(discovery: DiscoveryResult, url: str | None = None) -> FingerprintFeatures:
    source = url or discovery.source_url or ""
    features = FingerprintFeatures(host=(urlsplit(source).hostname or "").lower())
    features.route_paths = [urlsplit(source).path.lower()] if source else []
    features.api_paths = [urlsplit(value).path.lower() for value, _ in _candidate_urls(discovery) if value]
    features.script_assets = [item.url for item in discovery.source_inventory if item.source_type in ("EMBEDDED_WIDGET", "IFRAME") and item.url]
    envelopes: list[tuple[str, tuple[str, ...]]] = []
    for candidate in discovery.rejected_candidates:
        keys = candidate.response_shape.get("top_level_keys")
        if isinstance(keys, (list, tuple)) and keys:
            envelopes.append((urlsplit(candidate.url).path.lower(), tuple(str(k).lower() for k in keys)))
    requests: list[tuple[str, set[str]]] = []
    for candidate in discovery.candidate_list_apis:
        keys = {str(k).lower() for k in (candidate.request_body_shape or {})}
        if keys:
            requests.append((urlsplit(candidate.url).path.lower(), keys))
    trace = discovery.terminal_trace
    if trace is not None:
        for record in trace.network or []:
            record_url = record.get("url") or ""
            if record_url:
                features.api_paths.append(urlsplit(record_url).path.lower())
            keys = record.get("top_level_keys")
            if isinstance(keys, list) and keys:
                envelopes.append((urlsplit(record_url).path.lower(), tuple(str(k).lower() for k in keys)))
    features.response_key_sets = envelopes
    features.request_key_sets = requests
    features.scope_keys = {str(k).lower() for k in (discovery.detected_scope or {})}
    dom = discovery.dom_fallback or {}
    features.dom_status = str(dom.get("status") or "")
    features.dom_link_count = len(dom.get("possible_detail_links") or [])
    features.probable_list_api = discovery.probable_list_api is not None
    return features


def _moka_signals(features: FingerprintFeatures) -> list[ProviderSignal]:
    signals: list[ProviderSignal] = []
    if any(MOKA_API_PATH_RE.search(path) for path in features.api_paths):
        signals.append(ProviderSignal(signal_type="API_PATH", value="/api/outer/ats-apply/", weight=3, independent_group="api_path"))
    if any(MOKA_ROUTE_RE.search(path) for path in features.route_paths):
        signals.append(ProviderSignal(signal_type="ROUTE_PATTERN", value="social/campus-recruitment route", weight=1, independent_group="route_pattern"))
    if any(MOKA_ASSET_RE.search(asset) for asset in features.script_assets):
        signals.append(ProviderSignal(signal_type="RUNTIME_ASSET", value="moka recruitment runtime bundle", weight=3, independent_group="runtime_asset"))
    if any(MOKA_ENVELOPE_KEY in keys for _, keys in features.response_key_sets):
        signals.append(ProviderSignal(signal_type="RESPONSE_ENVELOPE", value="data+necromancer encrypted envelope", weight=4, independent_group="response_envelope"))
    if MOKA_SCOPE_KEYS <= features.scope_keys:
        signals.append(ProviderSignal(signal_type="PROVIDER_METADATA", value="siteId provider parameter", weight=2, independent_group="provider_metadata"))
    if any(MOKA_REQUEST_KEYS <= keys for _, keys in features.request_key_sets):
        signals.append(ProviderSignal(signal_type="REQUEST_SHAPE", value="orgId+siteId request contract", weight=2, independent_group="request_shape"))
    return signals


def _empty_signals(_features: FingerprintFeatures) -> list[ProviderSignal]:
    return []


PROFILE_EVALUATORS: tuple[tuple[str, Callable[[FingerprintFeatures], list[ProviderSignal]]], ...] = (
    ("moka", _moka_signals),
    ("zhiye", _empty_signals),
    ("feishu", _empty_signals),
)


def _host_provider(host: str) -> str | None:
    for provider, suffixes in KNOWN_PROVIDER_HOSTS.items():
        if host and any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in suffixes):
            return provider
    return None


def _confidence(score: int, groups: int, hostname_matched: bool) -> str:
    if hostname_matched:
        return "HIGH"
    if groups >= 2 and score >= 5:
        return "HIGH"
    if groups >= 2:
        return "MEDIUM"
    return "LOW"


def fingerprint_features(features: FingerprintFeatures) -> ProviderFingerprint:
    host_provider = _host_provider(features.host)
    candidates: list[tuple[str, ProviderFingerprint]] = []
    for provider, evaluator in PROFILE_EVALUATORS:
        signals = list(evaluator(features))
        hostname_matched = provider == host_provider
        if hostname_matched:
            signals.append(ProviderSignal(signal_type="KNOWN_HOST", value=features.host, weight=3, independent_group="known_host"))
        if not signals:
            continue
        score = sum(signal.weight for signal in signals)
        groups = len({signal.independent_group for signal in signals})
        confidence = _confidence(score, groups, hostname_matched)
        candidates.append((provider, ProviderFingerprint(
            provider=provider,
            confidence=confidence,
            evidence=[f"{signal.signal_type}: {signal.value}" for signal in signals],
            signals=signals,
            encrypted_envelope=any(signal.signal_type == "RESPONSE_ENVELOPE" for signal in signals),
            hostname_matched=hostname_matched,
            independent_signal_groups=groups,
        )))
    if not candidates:
        return ProviderFingerprint(provider="UNKNOWN", confidence="LOW", evidence=["no provider signals observed"])
    rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    best = max(candidates, key=lambda item: (rank[item[1].confidence], item[1].independent_signal_groups, len(item[1].signals)))[1]
    if not best.hostname_matched and best.confidence == "LOW":
        return ProviderFingerprint(provider="UNKNOWN", confidence="LOW", evidence=best.evidence, signals=best.signals, independent_signal_groups=best.independent_signal_groups)
    return best


def classify_capability(discovery: DiscoveryResult, fingerprint: ProviderFingerprint) -> list[str]:
    capabilities: list[str] = []
    if fingerprint.hostname_matched:
        capabilities.append("KNOWN_ADAPTER")
    if fingerprint.encrypted_envelope:
        capabilities.append("ENCRYPTED_BROWSER_API")
    elif discovery.probable_list_api is not None:
        probable = discovery.probable_list_api
        capabilities.append("PLAIN_HTTP_API" if probable.replayable and probable.method in ("GET", "POST") else "BROWSER_API")
    elif (discovery.dom_fallback or {}).get("status") == "DOM_LIST_DETECTED":
        capabilities.append("DOM_ONLY")
    if not capabilities:
        capabilities.append("UNSUPPORTED")
    return capabilities


def fingerprint_terminal(discovery: DiscoveryResult, url: str | None = None) -> ProviderFingerprint:
    fingerprint = fingerprint_features(extract_features(discovery, url))
    fingerprint.capabilities = classify_capability(discovery, fingerprint)
    return fingerprint
