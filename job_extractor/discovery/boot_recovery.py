"""Generic, bounded recovery for SPAs that finish booting without loading jobs.

This deliberately classifies browser state rather than recognising a company,
ATS, URL, or route.  A reload is only appropriate when an otherwise healthy
SPA had early activity, is now quiet, has no job-semantic activity or accepted
source, and has not rendered a job-ready DOM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from job_extractor.discovery.observation import MIN_OBSERVATION_MS, QUIET_PERIOD_MS

MAX_BOOT_RECOVERY_RELOADS = 1


@dataclass(frozen=True)
class BootRecoveryDecision:
    state: str
    should_reload: bool
    reason: str
    evidence: dict[str, Any]


def classify_boot_state(
    *,
    domcontentloaded: bool,
    elapsed_ms: int,
    activity_observed: int,
    job_semantic_requests: int,
    reliable_list_responses: int = 0,
    network_quiet_ms: int,
    dom_job_ready: bool,
    source_found: bool,
    auth_or_captcha: bool,
    environment_failure: bool,
    startup_minimum_ms: int = MIN_OBSERVATION_MS,
    quiet_period_ms: int = QUIET_PERIOD_MS,
) -> BootRecoveryDecision:
    """Return a conservative, pure decision for one page-load attempt."""
    evidence = {
        "domcontentloaded": domcontentloaded,
        "elapsed_ms": elapsed_ms,
        "activity_observed": activity_observed,
        "job_semantic_requests": job_semantic_requests,
        "reliable_list_responses": reliable_list_responses,
        "network_quiet_ms": network_quiet_ms,
        "dom_job_ready": dom_job_ready,
    }
    if source_found:
        return BootRecoveryDecision("SOURCE_FOUND", False, "HIGH_CONFIDENCE_SOURCE", evidence)
    if auth_or_captcha:
        return BootRecoveryDecision("AUTH_OR_CAPTCHA", False, "AUTH_OR_CAPTCHA", evidence)
    if environment_failure:
        return BootRecoveryDecision("ENVIRONMENT_FAILURE", False, "ENVIRONMENT_FAILURE", evidence)
    if not domcontentloaded:
        return BootRecoveryDecision("NAVIGATION_INCOMPLETE", False, "DOMCONTENTLOADED_NOT_REACHED", evidence)
    if elapsed_ms < startup_minimum_ms:
        return BootRecoveryDecision("NORMAL_LATE_LOAD", False, "STARTUP_MINIMUM_NOT_REACHED", evidence)
    if activity_observed <= 0:
        return BootRecoveryDecision("NO_EARLY_ACTIVITY", False, "NO_EARLY_RUNTIME_ACTIVITY", evidence)
    if reliable_list_responses > 0:
        return BootRecoveryDecision("NORMAL_LATE_LOAD", False, "RELIABLE_LIST_RESPONSE_SEEN", evidence)
    if network_quiet_ms < quiet_period_ms:
        return BootRecoveryDecision("NORMAL_LATE_LOAD", False, "PAGE_STILL_ACTIVE", evidence)
    if dom_job_ready:
        return BootRecoveryDecision("DOM_JOB_READY", False, "JOB_READY_DOM", evidence)
    return BootRecoveryDecision("QUIET_BOOT_STALL", True, "QUIET_WITHOUT_JOB_TRIGGER", evidence)


def reload_allowed(decision: BootRecoveryDecision, reload_count: int) -> bool:
    """Enforce the global one-reload bound independently of caller config."""
    return decision.should_reload and reload_count < MAX_BOOT_RECOVERY_RELOADS
