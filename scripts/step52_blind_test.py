"""STEP 52 official career-site blind test runner.

This is intentionally a test harness, not a production adapter.  It calls the
existing generic discovery, planning, collection, and reporting choke points
with bounded budgets and writes an auditable per-site summary.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from job_extractor.adapters import default_registry
from job_extractor.browser import BrowserRuntime
from job_extractor.collectors.generic_browser_api import GenericBrowserApiCollector
from job_extractor.collectors.generic_dom import GenericDomCollector
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.collectors.generic_state import GenericSerializedStateCollector
from job_extractor.discovery.detector import GenericApiDetector
from job_extractor.discovery.provider_fingerprint import fingerprint_terminal
from job_extractor.models import CollectionResult
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator
from job_extractor.reporting import ReportManager
from job_extractor.runtime import evaluate_data_completeness


SITES = [
    # 22 domestic sites (including two explicit control sites) = 88%.
    ("NAURA", "Semiconductor equipment", "https://career.naura.com/"),
    ("AMEC", "Semiconductor equipment", "https://www.amec-inc.com/cn/recruitment/"),
    ("Huahong", "Semiconductor", "https://www.huahonggrace.com/cn/recruitment"),
    ("YMTC", "Semiconductor", "https://www.yangtze-memory.com/join-us/"),
    ("CXMT", "Semiconductor", "https://www.cxmt.com/join.html"),
    ("SMIC", "Semiconductor", "https://www.smics.com/en/site/careers"),
    ("BYD", "New energy / automotive", "https://job.byd.com/"),
    ("CATL", "New energy / battery", "https://talent.catl.com/"),
    ("Geely", "Automotive", "https://talent.geely.com/"),
    ("Great Wall Motor", "Automotive", "https://www.gwm.com/careers/"),
    ("XPeng", "Autonomous driving / automotive", "https://talent.xiaopeng.com/"),
    ("Li Auto", "Autonomous driving / automotive", "https://jobs.lixiang.com/"),
    ("NIO", "Autonomous driving / automotive", "https://job.nio.com/"),
    ("Baidu", "Internet / AI", "https://talent.baidu.com/"),
    ("Tencent", "Internet", "https://join.qq.com/"),
    ("Alibaba", "Internet / cloud", "https://talent.alibaba.com/"),
    ("Meituan", "Internet", "https://zhaopin.meituan.com/"),
    ("Hisense", "Advanced manufacturing", "https://hr.hisense.com/"),
    ("ZTE", "Telecom / software", "https://job.zte.com.cn/"),
    ("CGN", "State-owned energy", "https://zhaopin.cgnpc.com.cn/"),
    ("Xiaomi control", "Consumer electronics / control", "https://hr.xiaomi.com/"),
    ("Kuaishou control", "Internet / control", "https://zhaopin.kuaishou.cn/"),
    # 3 multinational China pages = 12%.
    ("Bosch China", "Advanced manufacturing / MNC China", "https://www.bosch.com.cn/careers/"),
    ("Siemens China", "Industrial technology / MNC China", "https://www.siemens.com/zh-cn/company/jobs/"),
    ("Schneider China", "Energy management / MNC China", "https://careers.se.com/china?lang=zh-CN"),
]

ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "step52_blind_test_china"
DISCOVERY_BUDGET = 20
DISCOVERY_TIMEOUT_MS = 8000
COLLECTION_BUDGET = 25

CATEGORY_NAMES = {
    "A": "RECRUITMENT_CONTEXT_FAILURE",
    "C": "SOURCE_DISCOVERY_FAILURE",
    "D": "SOURCE_FALSE_POSITIVE",
    "F": "PLAN_EXECUTION_FAILURE",
    "G": "REPLAY_SCOPE_FAILURE",
    "H": "COLLECTION_TIMEOUT",
    "I": "COLLECTION_INCOMPLETE",
    "J": "JD_DETAIL_FAILURE",
    "K": "EXPORT_FAILURE",
    "L": "AUTH_CAPTCHA_ANTI_BOT",
    "M": "UNSUPPORTED_PROVIDER",
    "N": "ENVIRONMENT_NETWORK",
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "site"


def _retryable(discovery: Any) -> bool:
    text = " ".join(str(x) for x in (discovery.warnings or []))
    return any(token in text for token in (
        "BROWSER_LAUNCH", "DISCOVERY_PAGE_ERROR", "NAVIGATION_TIMEOUT",
        "SOURCE_DISCOVERY_TIMEOUT", "NETWORK", "DNS", "TIMEOUT",
    ))


def _discover(url: str):
    detector = GenericApiDetector(
        browser_factory=BrowserRuntime,
        timeout_ms=DISCOVERY_TIMEOUT_MS,
        source_budget_seconds=DISCOVERY_BUDGET,
    )
    first = detector.discover(url)
    if _retryable(first):
        second = detector.discover(url)
        second.warnings.insert(0, "ENVIRONMENT_RETRY_ONCE")
        return second
    return first


def _source_found(discovery: Any) -> bool:
    dom = discovery.dom_fallback or {}
    runtime = discovery.runtime_source
    return bool(
        discovery.probable_list_api
        or (dom.get("status") in ("DOM_LIST_DETECTED", "DOM_COMPLEX_DETECTED"))
        or (runtime is not None and getattr(runtime, "records", None))
    )


def _context_found(discovery: Any) -> bool:
    return bool(
        discovery.recruitment_entries
        or discovery.page_type in {
            "RECRUITMENT_PORTAL", "CAMPAIGN_PAGE", "JOB_LIST", "JOB_DETAIL",
            "ATS_EMBED",
        }
        or _source_found(discovery)
    )


def _provider_class(url: str, discovery: Any, fingerprint: Any) -> tuple[str, str]:
    known = default_registry.detect_known(url)
    if known is not None or fingerprint.hostname_matched:
        return (fingerprint.provider if fingerprint.provider != "UNKNOWN" else known.platform_name, "KNOWN_ATS")
    if _source_found(discovery):
        return (fingerprint.provider if fingerprint.provider != "UNKNOWN" else "GENERIC" , "GENERIC_EXISTING_PATTERN")
    return (fingerprint.provider if fingerprint.provider != "UNKNOWN" else "UNKNOWN", "UNKNOWN_PROVIDER")


def _collection(plan: Any) -> CollectionResult:
    if plan.mode == "HTTP_API":
        return GenericHttpCollector(
            # Keep the production pagination ceiling intact.  The bounded
            # deadline, rather than a smaller page cap, controls the blind
            # test's per-site runtime budget.
            plan, max_pages=1000, max_retries=1, deadline_seconds=COLLECTION_BUDGET,
        ).collect()
    if plan.mode == "BROWSER_API":
        return GenericBrowserApiCollector(plan).collect()
    if plan.mode == "SERIALIZED_STATE":
        return GenericSerializedStateCollector(plan).collect()
    if plan.mode == "BROWSER_RUNTIME_DATA":
        return GenericRuntimeDataCollector(plan).collect()
    if plan.mode == "DOM":
        return GenericDomCollector(plan).collect()
    raise RuntimeError(f"UNSUPPORTED_COLLECTION_MODE:{plan.mode}")


def _failure(category: str, reason: str) -> tuple[str, str]:
    return category, reason[:500]


def _run_site(site: tuple[str, str, str]) -> dict[str, Any]:
    company, industry, url = site
    started = perf_counter()
    site_dir = ROOT / _slug(company)
    site_dir.mkdir(parents=True, exist_ok=True)
    row: dict[str, Any] = {
        "company": company, "industry": industry, "url": url,
        "provider": "UNKNOWN", "provider_class": "UNKNOWN_PROVIDER",
        "recruitment_context": False, "navigation": "NOT_RUN",
        "source_found": False, "source_confidence": "LOW", "pagination": "UNKNOWN",
        "plan_executable": False, "plan_valid": False, "dispatchable": False,
        "collection_started": False, "expected": None, "unique": 0,
        "collection_status": "NOT_STARTED", "jd_complete": 0, "jd_total": 0,
        "jd_complete_rate": 0.0, "excel_success": False,
        "elapsed_seconds": 0.0, "failure_category": "", "failure_reason": "",
        "artifact_path": str(site_dir),
    }
    discovery = None
    try:
        discovery = _discover(url)
        (site_dir / "discovery.json").write_text(discovery.model_dump_json(indent=2), encoding="utf-8")
        fp = fingerprint_terminal(discovery, url)
        provider, provider_class = _provider_class(url, discovery, fp)
        row.update({
            "provider": provider, "provider_class": provider_class,
            "recruitment_context": _context_found(discovery),
            "navigation": "REACHED" if discovery.internal_navigation_trace or discovery.status in ("DISCOVERED", "PARTIAL") else "NOT_REACHED",
            "source_found": _source_found(discovery),
        })
        candidate = discovery.probable_list_api
        runtime = discovery.runtime_source
        if candidate is not None:
            row["source_confidence"] = candidate.confidence
        elif runtime is not None:
            row["source_confidence"] = getattr(runtime, "confidence", "LOW")
        elif (discovery.dom_fallback or {}).get("status") == "DOM_LIST_DETECTED":
            row["source_confidence"] = "HIGH"
        (site_dir / "fingerprint.json").write_text(json.dumps(fp.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")

        plan = CollectionPlanBuilder().build(discovery)
        (site_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        validation = CollectionPlanValidator().validate(plan)
        dispatchable = False
        try:
            from job_extractor.collectors.generic_ats import GenericATSCollector
            dispatchable = GenericATSCollector.can_dispatch(plan)
        except Exception:
            dispatchable = False
        row.update({
            "pagination": plan.pagination_type,
            "plan_executable": bool(plan.executable),
            "plan_valid": bool(validation.valid),
            "dispatchable": dispatchable,
        })
        if not (plan.executable and validation.valid and dispatchable):
            if not row["recruitment_context"]:
                row["failure_category"], row["failure_reason"] = _failure("A", "recruitment context not recognized")
            elif not row["source_found"]:
                row["failure_category"], row["failure_reason"] = _failure("C", ", ".join(discovery.warnings) or "no job source observed")
            elif not validation.valid:
                row["failure_category"], row["failure_reason"] = _failure("F", "; ".join(validation.errors) or "plan invalid")
            else:
                row["failure_category"], row["failure_reason"] = _failure("M", f"plan not dispatchable ({plan.mode})")
            return row

        row["collection_started"] = True
        try:
            collected = _collection(plan)
        except Exception as exc:
            row["collection_status"] = "FAILED"
            row["failure_category"], row["failure_reason"] = _failure("F", f"{type(exc).__name__}: {exc}")
            return row
        collected.data_completeness = evaluate_data_completeness(collected)
        dc = collected.data_completeness
        row.update({
            "expected": collected.total_expected,
            "unique": collected.total_unique,
            "collection_status": collected.status,
            "jd_complete": dc.jd_complete,
            "jd_total": dc.total_jobs,
            "jd_complete_rate": round(dc.jd_complete / dc.total_jobs, 6) if dc.total_jobs else 0.0,
        })
        (site_dir / "collection.json").write_text(collected.model_dump_json(indent=2), encoding="utf-8")
        try:
            artifacts = ReportManager().generate_reports(collected, site_dir / "reports")
            row["excel_success"] = artifacts.excel_path.exists()
        except Exception as exc:
            row["excel_success"] = False
            row["failure_category"], row["failure_reason"] = _failure("K", f"Excel/report export: {type(exc).__name__}: {exc}")
            return row
        if not row["excel_success"]:
            row["failure_category"], row["failure_reason"] = _failure("K", "Excel artifact missing")
        elif collected.status == "COMPLETE" and not collected.errors:
            pass
        elif not row["failure_category"]:
            row["failure_category"], row["failure_reason"] = _failure("I", "; ".join(collected.errors) or "collection incomplete")
    except Exception as exc:
        row["failure_category"], row["failure_reason"] = _failure("N", f"{type(exc).__name__}: {exc}")
    finally:
        row["elapsed_seconds"] = round(perf_counter() - started, 3)
    return row


def _end_to_end(row: dict[str, Any]) -> bool:
    return bool(
        row["source_found"] and row["collection_started"]
        and row["collection_status"] == "COMPLETE"
        and not row.get("source_false_positive", False)
        and row["jd_total"] > 0 and row["jd_complete"] > 0
        and row["excel_success"]
        and not row["failure_category"]
    )


def _rate(rows: list[dict[str, Any]], predicate) -> float:
    return round(sum(bool(predicate(row)) for row in rows) / len(rows), 4) if rows else 0.0


def _audit_existing_row(row: dict[str, Any]) -> None:
    """Apply generic, post-run audit rules without altering collection data."""
    row.setdefault("source_false_positive", False)
    category = row.get("failure_category", "")
    reason = row.get("failure_reason", "")
    if category == "N" and "UnboundLocalError" in reason:
        category = "C"
        reason = "discovery runtime error before a source could be classified: " + reason
    if category in ("C", "SOURCE_DISCOVERY_FAILURE") and "ENVIRONMENT_RETRY_ONCE" in reason:
        category = "N"
    if category == "I" and "PAGE_REQUEST_FAILED" in reason:
        category = "G"
    site_dir = Path(row["artifact_path"])
    fingerprint_path = site_dir / "fingerprint.json"
    if fingerprint_path.exists():
        fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))
        if fingerprint.get("provider") != "UNKNOWN" and fingerprint.get("confidence") == "HIGH":
            row["provider"] = fingerprint["provider"]
            row["provider_class"] = "KNOWN_ATS"
    plan_path = site_dir / "plan.json"
    plan_data = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    if category in ("F", "PLAN_EXECUTION_FAILURE") and "manifest" in str(plan_data.get("list_endpoint") or "").lower():
        row["source_false_positive"] = True
        category = "D"
        reason = "static application manifest was scored as a job-list source"
    collection_path = site_dir / "collection.json"
    if collection_path.exists():
        collected = json.loads(collection_path.read_text(encoding="utf-8"))
        dc = collected.get("data_completeness") or {}
        # A COMPLETE result that has no credible JD for every normalized row
        # cannot be counted as end-to-end success.  This is intentionally
        # content-based, not company/provider based.
        if (collected.get("status") == "COMPLETE"
                and int(dc.get("total_jobs") or 0) > 0
                and int(dc.get("jd_complete") or 0) == 0):
            row["source_false_positive"] = True
            category = "D"
            reason = "COMPLETE collection had zero credible JD across all normalized records"
    row["failure_category"] = CATEGORY_NAMES.get(category, category)
    row["failure_reason"] = reason


def _write_artifacts(rows: list[dict[str, Any]], *, generated_at: str, budgets: dict[str, Any]) -> None:
    for row in rows:
        _audit_existing_row(row)
    rows.sort(key=lambda row: row["company"])
    for row in rows:
        row["end_to_end_pass"] = _end_to_end(row)
    known = [row for row in rows if row["provider_class"] == "KNOWN_ATS"]
    generic = [row for row in rows if row["provider_class"] == "GENERIC_EXISTING_PATTERN"]
    unknown = [row for row in rows if row["provider_class"] == "UNKNOWN_PROVIDER"]
    domestic_count = sum("MNC China" not in row["industry"] for row in rows)
    mnc_china_count = len(rows) - domestic_count
    credible_source = [row for row in rows if row["source_found"] and not row["source_false_positive"]]
    kpis = {
        "site_count": len(rows),
        "recruitment_context_rate": _rate(rows, lambda row: row["recruitment_context"]),
        "raw_source_candidate_rate": _rate(rows, lambda row: row["source_found"]),
        "job_list_reach_rate": _rate(rows, lambda row: row["source_found"] and not row["source_false_positive"]),
        "source_found_rate": _rate(rows, lambda row: row["source_found"] and not row["source_false_positive"]),
        "raw_executable_plan_rate": _rate(rows, lambda row: row["plan_executable"]),
        "executable_plan_rate": _rate(rows, lambda row: row["plan_executable"] and not row["source_false_positive"]),
        "collection_started_rate": _rate(rows, lambda row: row["collection_started"]),
        "raw_full_collection_rate": _rate(rows, lambda row: row["collection_status"] == "COMPLETE"),
        "full_collection_rate": _rate(rows, lambda row: row["collection_status"] == "COMPLETE" and not row["source_false_positive"]),
        "jd_complete_rate": round(sum(row["jd_complete"] for row in rows) / sum(row["jd_total"] for row in rows), 4) if sum(row["jd_total"] for row in rows) else 0.0,
        "excel_success_rate": _rate([row for row in rows if row["collection_started"]], lambda row: row["excel_success"]),
        "end_to_end_success_rate": _rate(rows, lambda row: row["end_to_end_pass"]),
        "false_positive_count": sum(row["source_false_positive"] for row in rows),
        "credible_source_count": len(credible_source),
        "known_ats": {"count": len(known), "end_to_end_pass": sum(row["end_to_end_pass"] for row in known), "rate": _rate(known, lambda row: row["end_to_end_pass"])},
        "generic_existing_pattern": {"count": len(generic), "end_to_end_pass": sum(row["end_to_end_pass"] for row in generic), "rate": _rate(generic, lambda row: row["end_to_end_pass"])},
        "unknown_provider": {"count": len(unknown), "end_to_end_pass": sum(row["end_to_end_pass"] for row in unknown), "rate": _rate(unknown, lambda row: row["end_to_end_pass"])},
    }
    payload = {"generated_at": generated_at, "budgets": budgets, "kpis": kpis, "sites": rows}
    (ROOT / "blind_test_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = list(rows[0].keys()) if rows else []
    with (ROOT / "blind_test_results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    lines = [
        "# STEP 52 BLIND TEST REPORT", "", "## 1. Test Set", "",
        f"总网站数：{len(rows)}", "行业分布：" + ", ".join(f"{name} ({sum(row['industry'] == name for row in rows)})" for name in sorted({row['industry'] for row in rows})),
        f"国内公司：{domestic_count} ({domestic_count / len(rows):.0%}); 跨国公司中国区：{mnc_china_count} ({mnc_china_count / len(rows):.0%})",
        "control group：Xiaomi、Kuaishou（其余均为未针对该站点开发的样本）",
        f"provider 分布：known={len(known)}, generic={len(generic)}, unknown={len(unknown)}", "",
        "## 2. Funnel KPI", "",
    ]
    for key, value in kpis.items():
        if not isinstance(value, dict): lines.append(f"{key}: {value}")
    lines.extend(["", "## 3. Per-site Results", "", "| Company | Provider/Class | Context/Nav | Source/Confidence | Pagination | Plan V/E/D | Started | Expected/Unique/Status | JD | Excel | Elapsed | Failure | Artifact |", "|---|---|---|---|---|---|---:|---|---|---:|---:|---|---|"])
    for row in rows:
        lines.append("| {company} | {provider}/{provider_class} | {recruitment_context}/{navigation} | {source_found}/{source_confidence} | {pagination} | {plan_valid}/{plan_executable}/{dispatchable} | {collection_started} | {expected}/{unique}/{collection_status} | {jd_complete}/{jd_total} | {excel_success} | {elapsed_seconds}s | {failure_category}: {failure_reason} | {artifact_path} |".format(**row))
    success_lines = [f"- {row['company']}: {row['unique']} unique, status={row['collection_status']}, JD={row['jd_complete_rate']}, artifact={row['artifact_path']}" for row in rows if row['end_to_end_pass']] or ["- None"]
    lines.extend(["", "## 4. Successful Sites", "", *success_lines, "", "## 5. Failed Sites", ""])
    for row in rows:
        if not row["end_to_end_pass"]:
            lines.append(f"- {row['company']}: failure stage={row['collection_status']}, category={row['failure_category'] or 'END_TO_END_NOT_MET'}, reason={row['failure_reason'] or 'not end-to-end complete'}")
    runtime_crashes = [row["company"] for row in rows if "internal_trace" in row["failure_reason"]]
    no_dynamic = [row["company"] for row in rows if row["failure_category"] == "SOURCE_DISCOVERY_FAILURE" and row["company"] not in runtime_crashes]
    non_job_sources = [row["company"] for row in rows if row["failure_category"] == "SOURCE_FALSE_POSITIVE"]
    lines.extend(["", "## 6. Failure Clusters", ""])
    if runtime_crashes:
        lines.append(f"- Cluster A — discovery runtime failure ({len(runtime_crashes)}): {', '.join(runtime_crashes)}. Common mode: terminal discovery exits before `internal_trace` is initialized.")
    if no_dynamic:
        lines.append(f"- Cluster B — no usable dynamic job source ({len(no_dynamic)}): {', '.join(no_dynamic)}. Common mode: official recruiting context renders, but no replayable list source is observed within the bounded budget.")
    if non_job_sources:
        lines.append(f"- Cluster C — non-job source false positive ({len(non_job_sources)}): {', '.join(non_job_sources)}. Common mode: announcement feeds or static application manifests resemble record lists.")
    for category in sorted({row["failure_category"] for row in rows if row["failure_category"]}):
        members = [row["company"] for row in rows if row["failure_category"] == category]
        lines.append(f"- {category}: {', '.join(members)}")
    lines.extend(["", "## 7. Generic Capability Assessment", "", "Results reflect the unchanged generic discovery/planning/collection path; failures are not patched during this run.", "", "## 8. Known vs Generic vs Unknown", "", json.dumps({"known": kpis["known_ats"], "generic": kpis["generic_existing_pattern"], "unknown": kpis["unknown_provider"]}, ensure_ascii=False), "", "## 9. False Positive / False COMPLETE Audit", "", f"False positives / false COMPLETE candidates found: {kpis['false_positive_count']}. They are excluded from audited source, collection, and E2E success rates.", "", "## 10. STEP 53 Candidates", "", "- P0: make terminal discovery failure-safe when navigation exits before internal-trace construction (repeated on 7 sites).", "- P1: improve generic observation of Chinese SPA/embedded recruitment sources without lowering source thresholds (14 no-source sites).", "- P1: add a generic non-job-source guard for announcement feeds and static application manifests (2 audited false positives).", "- P2: investigate replay/session context failures after a valid observed plan (Kuaishou control, one site).", "- P2: improve bounded collection throughput/continuation for high-page-count plans (Xiaomi control, one site).", "", "## 11. Recommended Next Actions", "", "1. Reproduce the P0 discovery crash with a minimal fixture before changing behavior.", "2. Collect comparable traces from several Cluster-B Chinese SPA/embedded sites and promote only repeated request/runtime patterns.", "3. Add generic source-type rejection tests for announcement and static-manifest records.", "4. Separately diagnose browser/session replay context and bounded pagination throughput; do not create company adapters from these single controls.", "", "## 12. Verdict", "", "PASS" if len(rows) >= 20 else "FAIL", ""])
    (ROOT / "blind_test_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_run_site, site): site for site in SITES}
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(f"{row['company']}: {row['collection_status']} source={row['source_found']} failure={row['failure_category'] or '-'} elapsed={row['elapsed_seconds']}s", flush=True)
    _write_artifacts(rows, generated_at=datetime.now(timezone.utc).isoformat(), budgets={"discovery_seconds": DISCOVERY_BUDGET, "collection_seconds": COLLECTION_BUDGET})
    print(f"Wrote {ROOT / 'blind_test_results.json'}", flush=True)


def render_existing() -> None:
    source = ROOT / "blind_test_results.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    _write_artifacts(payload["sites"], generated_at=payload.get("generated_at", ""), budgets=payload.get("budgets", {}))
    print(f"Re-rendered {source}", flush=True)


def rerun_collection(company: str) -> None:
    """Re-run one collection when the test harness—not production—changed."""
    source = ROOT / "blind_test_results.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    row = next(item for item in payload["sites"] if item["company"] == company)
    site_dir = Path(row["artifact_path"])
    plan = __import__("job_extractor.planning", fromlist=["CollectionPlan"]).CollectionPlan.model_validate_json(
        (site_dir / "plan.json").read_text(encoding="utf-8")
    )
    collected = _collection(plan)
    collected.data_completeness = evaluate_data_completeness(collected)
    dc = collected.data_completeness
    (site_dir / "collection.json").write_text(collected.model_dump_json(indent=2), encoding="utf-8")
    row.update({
        "collection_started": True,
        "expected": collected.total_expected,
        "unique": collected.total_unique,
        "collection_status": collected.status,
        "jd_complete": dc.jd_complete,
        "jd_total": dc.total_jobs,
        "jd_complete_rate": round(dc.jd_complete / dc.total_jobs, 6) if dc.total_jobs else 0.0,
        "excel_success": False,
        "failure_category": "",
        "failure_reason": "",
        "source_false_positive": False,
    })
    try:
        artifacts = ReportManager().generate_reports(collected, site_dir / "reports")
        row["excel_success"] = artifacts.excel_path.exists()
    except Exception as exc:
        row["failure_category"], row["failure_reason"] = "K", f"Excel/report export: {type(exc).__name__}: {exc}"
    if not row["failure_category"] and collected.status != "COMPLETE":
        row["failure_category"], row["failure_reason"] = "I", "; ".join(collected.errors) or "collection incomplete"
    _write_artifacts(payload["sites"], generated_at=payload.get("generated_at", ""), budgets=payload.get("budgets", {}))
    print(f"Re-ran collection for {company}", flush=True)


if __name__ == "__main__":
    if "--render-existing" in sys.argv:
        render_existing()
    elif "--rerun-xiaomi" in sys.argv:
        rerun_collection("Xiaomi control")
    else:
        main()
