"""STEP 70: human-readable collection summary.

The CLI / report tells the user, in plain language, whether the collection is
complete and, when it is not, how far it got and why. Internal metrics stay in
the machine-readable metrics block; this summary stays simple.
"""
from __future__ import annotations

from job_extractor.models import CollectionResult

_STATUS_PHRASE = {"COMPLETE": "Collection complete.", "FAILED": "Collection failed.",
                  "INCOMPLETE": "Collection incomplete."}


def human_summary(result: CollectionResult) -> str:
    status=_STATUS_PHRASE.get(result.status,result.status)
    lines=[status,"",
           f"List: {result.total_expected if result.total_expected is not None else '?'} expected,"
           f" {result.total_fetched} fetched, {result.total_unique} unique"]
    m=result.metrics
    if m.detail_total:
        lines.append(f"Detail: {m.detail_http_resolved + m.detail_browser_resolved} resolved"
                     f" ({m.detail_browser_resolved} browser-resolved),"
                     f" {m.detail_blocked} blocked, {m.detail_failed} failed"
                     + (f", {m.detail_pending} pending" if m.detail_pending else ""))
    reason=_reason(result)
    if reason:lines.extend(["",f"Reason: {reason}"])
    return "\n".join(lines)


def _reason(result: CollectionResult) -> str:
    m=result.metrics
    if result.status=="FAILED":
        fatal=next((e for e in result.errors if not e.startswith(("PAGINATION_LIMIT","BROWSER_BUDGET_EXHAUSTED"))),None)
        if result.total_expected is None:
            return "the job list itself could not be acquired, so nothing can be trusted."
        return f"fatal error: {fatal.split(' ',1)[0] if fatal else 'list acquisition failed'}."
    if result.status!="INCOMPLETE":return ""
    reasons=[]
    if any(e.startswith("PAGINATION_LIMIT") for e in result.errors):
        reasons.append("stopped at the user-configured page cap (controlled limit, not a provider failure)")
    if m.detail_blocked:
        reasons.append(f"{m.detail_blocked} job detail{'s' if m.detail_blocked!=1 else ''} could not be resolved (blocked by the source; nothing was fabricated)")
    if m.detail_failed:
        reasons.append(f"{m.detail_failed} job detail request{'s' if m.detail_failed!=1 else ''} failed")
    if m.browser_jobs_budget_skipped:
        reasons.append(f"the browser detail budget was exhausted with {m.browser_jobs_budget_skipped} job(s) unattempted")
    if reasons:return "; ".join(reasons)
    return "the fetched list did not match the source's expected total."
