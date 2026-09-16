"""STEP 52B: evidence-preserving V1 job-bearing-page blind-test runner.

It deliberately reuses the existing STEP 52 harness' generic production
path.  Only explicitly preconfirmed, visible-position pages are admitted;
every rejected candidate remains in the precheck artifact and is excluded from
all denominators.  No extractor code or thresholds are altered here.
"""
from __future__ import annotations

import csv
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import step52_blind_test as generic
from job_extractor.planning import CollectionPlan

ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "step52b_job_bearing_china"

# Confirmed from real-browser visible page text/screenshots or an official
# search/list response during the input precheck.  These are page inputs, not
# landing-page navigation targets.
SITES = [
    ("Baidu control", "Internet / AI", "https://talent.baidu.com/jobs/list?projectType=1", "CONTROL", "official page visibly states 147 job records"),
    ("ByteDance control", "Internet / AI", "https://jobs.bytedance.com/experienced/position", "CONTROL", "visible position cards with position IDs"),
    ("Bilibili", "Internet", "https://jobs.bilibili.com/social/positions", "BLIND", "visible 职位列表（519） and repeated 查看职位 cards"),
    ("Meituan", "Internet", "https://zhaopin.meituan.com/web/social", "BLIND", "visible 全部社招职位（2444） and repeated cards"),
    ("Tencent", "Internet", "https://careers.tencent.com/search.html", "BLIND", "visible 1-10 / 2293 position result list"),
    ("DJI", "Consumer electronics", "https://careers.dji.com/zh-CN/campus/hot-jobs?source=campus_hotjobs", "BLIND", "visible repeated hot-job titles"),
    ("Huawei", "Telecom / software", "https://career.huawei.com/reccampportal/globle/huawei-special-recruitment.html", "BLIND", "visible 14 position cards and pagination"),
]


def _run(site):
    company, industry, url, cohort, evidence = site
    row = generic._run_site((company, industry, url))
    row.update({
        "input_url": url,
        "job_bearing_page_confirmed": "YES",
        "input_evidence": evidence,
        "cohort": cohort,
        "raw_rows": row.get("unique", 0),
        "json_success": (Path(row["artifact_path"]) / "reports" / "jobs.json").exists(),
        "markdown_success": (Path(row["artifact_path"]) / "reports" / "report.md").exists(),
        "termination_reason": row.get("failure_reason", "") or ("complete" if row.get("collection_status") == "COMPLETE" else "not started"),
    })
    return row


def rate(rows, predicate):
    return round(sum(bool(predicate(row)) for row in rows) / len(rows), 4) if rows else 0.0


def audit(row):
    generic._audit_existing_row(row)
    row["false_complete"] = bool(row.get("source_false_positive") and row.get("collection_status") == "COMPLETE")
    row["e2e_pass"] = bool(
        row["job_bearing_page_confirmed"] == "YES"
        and row["source_found"] and not row.get("source_false_positive")
        and row["plan_executable"] and row["plan_valid"] and row["dispatchable"]
        and row["collection_status"] == "COMPLETE" and row["unique"] > 0
        and row["jd_total"] > 0 and row["jd_complete"] > 0
        and row["excel_success"] and row["json_success"] and row["markdown_success"]
        and not row.get("failure_category")
    )


def write(rows):
    rows.sort(key=lambda row: row["company"])
    for row in rows: audit(row)
    valid = [row for row in rows if row["job_bearing_page_confirmed"] == "YES"]
    false_sources = [row for row in valid if row.get("source_false_positive")]
    kpis = {
        "valid_input_count": len(valid),
        "source_found_rate": rate(valid, lambda x: x["source_found"] and not x.get("source_false_positive")),
        "executable_plan_rate": rate(valid, lambda x: x["plan_executable"] and x["plan_valid"] and x["dispatchable"] and not x.get("source_false_positive")),
        "collection_started_rate": rate(valid, lambda x: x["collection_started"]),
        "full_collection_rate": rate(valid, lambda x: x["collection_status"] == "COMPLETE" and not x.get("source_false_positive")),
        "jd_complete_rate": round(sum(x["jd_complete"] for x in valid) / sum(x["jd_total"] for x in valid), 4) if sum(x["jd_total"] for x in valid) else 0.0,
        "excel_success_rate": rate([x for x in valid if x["collection_started"]], lambda x: x["excel_success"]),
        "end_to_end_success_rate": rate(valid, lambda x: x["e2e_pass"]),
        "source_false_positive_count": len(false_sources),
        "false_complete_count": sum(x["false_complete"] for x in valid),
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "scope": "V1 job-bearing pages only", "kpis": kpis, "sites": rows}
    (ROOT / "blind_test_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (ROOT / "blind_test_results.csv").open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    lines = ["# STEP 52B JOB-BEARING PAGE BLIND TEST REPORT", "", "## 1. V1 Input Contract", "", f"Valid inputs: {len(valid)}. Each admitted URL was preconfirmed as a page with visible multiple positions; rejected candidates remain under `precheck/` and are excluded.", "", "## 2. Test Set", "", f"Total valid: {len(valid)}; controls: {sum(x['cohort'] == 'CONTROL' for x in valid)}; true blind: {sum(x['cohort'] == 'BLIND' for x in valid)}.", "", "## 3. V1 Funnel KPI", ""]
    lines += [f"{key}: {value}" for key, value in kpis.items()]
    lines += ["", "## 4. Per-site Results", "", "| Company | Input evidence | Provider/class | Source | Plan E/V/D | Collection expected/raw/unique/status | JD | Exports X/J/M | Failure |", "|---|---|---|---|---|---|---|---|---|"]
    for x in rows:
        lines.append(f"| {x['company']} | {x['input_evidence']} | {x['provider']}/{x['provider_class']} | {x['source_found']}/{x['source_confidence']} | {x['plan_executable']}/{x['plan_valid']}/{x['dispatchable']} | {x['expected']}/{x['raw_rows']}/{x['unique']}/{x['collection_status']} | {x['jd_complete']}/{x['jd_total']} | {x['excel_success']}/{x['json_success']}/{x['markdown_success']} | {x['failure_category']}: {x['failure_reason']} |")
    failures = [x for x in rows if not x["e2e_pass"]]
    lines += ["", "## 5. E2E Passed Sites", ""] + ([f"- {x['company']}" for x in rows if x["e2e_pass"]] or ["- None"]) + ["", "## 6. Failed Sites", ""]
    lines += [f"- {x['company']}: {x['failure_category'] or 'E2E_NOT_MET'} — {x['failure_reason'] or x['collection_status']}" for x in failures]
    clusters = {}
    for x in failures: clusters.setdefault(x["failure_category"] or "E2E_NOT_MET", []).append(x["company"])
    lines += ["", "## 7. Source False Positive Audit", "", f"count: {len(false_sources)}", *[f"- {x['company']}: {x['failure_reason']}" for x in false_sources], "", "## 8. False COMPLETE Audit", "", f"count: {kpis['false_complete_count']}", "", "## 9. Failure Clusters", ""]
    lines += [f"- {category}: {', '.join(companies)}" for category, companies in sorted(clusters.items())]
    lines += ["", "## 10. Known vs Generic vs Unknown", ""]
    for klass in ("KNOWN_ATS", "GENERIC_EXISTING_PATTERN", "UNKNOWN_PROVIDER"):
        group = [x for x in valid if x["provider_class"] == klass]
        lines.append(f"- {klass}: count={len(group)}, source={rate(group, lambda x: x['source_found'] and not x.get('source_false_positive'))}, full={rate(group, lambda x: x['collection_status'] == 'COMPLETE' and not x.get('source_false_positive'))}, e2e={rate(group, lambda x: x['e2e_pass'])}")
    verdict = "PASS" if len(valid) >= 20 else "FAIL"
    lines += ["", "## 11. V1 Capability Assessment", "", "This run reports only preconfirmed job-bearing inputs. Its sample is not sufficient for a product-rate claim because it did not reach 20 valid inputs.", "", "## 12. STEP 53 Candidates", "", "Not ranked: complete the 20+ valid-input sampling before changing production behavior.", "", "## 13. Recommended Next Actions", "", "1. Continue sourcing and prechecking 13+ new official China job-list URLs; keep invalid inputs excluded.", "2. Cluster only after the valid sample reaches the required floor; do not change production during this blind-test round.", "", "## 14. Verdict", "", verdict]
    (ROOT / "blind_test_report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    generic.ROOT = ROOT / "per_site"
    generic.DISCOVERY_BUDGET = 45
    generic.DISCOVERY_TIMEOUT_MS = 15000
    generic.COLLECTION_BUDGET = 90
    rows = []
    # Serialized browser runs keep diagnostics reproducible on the validation
    # host; concurrent Chromium teardown has caused process-level instability.
    with ThreadPoolExecutor(max_workers=1) as pool:
        for result in as_completed([pool.submit(_run, site) for site in SITES]):
            row = result.result(); rows.append(row)
            print(f"{row['company']}: {row['collection_status']} source={row['source_found']} failure={row['failure_category'] or '-'}", flush=True)
    write(rows)


def render_partial():
    """Render all durable evidence after an external browser process stops.

    This does not retry, mutate, or invent an outcome: absent collection data
    remains NOT_STARTED/ENVIRONMENT_NETWORK.
    """
    generic.ROOT = ROOT / "per_site"
    rows = []
    for company, industry, url, cohort, evidence in SITES:
        folder = generic.ROOT / generic._slug(company)
        row = {
            "company": company, "industry": industry, "url": url, "input_url": url,
            "job_bearing_page_confirmed": "YES", "input_evidence": evidence, "cohort": cohort,
            "provider": "UNKNOWN", "provider_class": "UNKNOWN_PROVIDER", "source_found": False,
            "source_confidence": "LOW", "pagination": "UNKNOWN", "plan_executable": False,
            "plan_valid": False, "dispatchable": False, "collection_started": False,
            "expected": None, "raw_rows": 0, "unique": 0, "collection_status": "NOT_STARTED",
            "termination_reason": "browser validation process interrupted before this site completed",
            "jd_complete": 0, "jd_total": 0, "jd_complete_rate": 0.0, "excel_success": False,
            "json_success": False, "markdown_success": False, "elapsed_seconds": 0.0,
            "failure_category": "ENVIRONMENT_NETWORK", "failure_reason": "browser validation process interrupted",
            "artifact_path": str(folder), "source_false_positive": False,
        }
        discovery_path, plan_path, collected_path = folder / "discovery.json", folder / "plan.json", folder / "collection.json"
        if discovery_path.exists():
            discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
            candidate = discovery.get("probable_list_api") or {}
            dom = discovery.get("dom_fallback") or {}
            runtime = discovery.get("runtime_source") or {}
            row["source_found"] = bool(candidate or dom.get("status") in ("DOM_LIST_DETECTED", "DOM_COMPLEX_DETECTED") or runtime.get("records"))
            row["source_confidence"] = candidate.get("confidence") or runtime.get("confidence") or ("HIGH" if dom.get("status") == "DOM_LIST_DETECTED" else "LOW")
            if not row["source_found"]:
                row["failure_category"] = "SOURCE_DISCOVERY_FAILURE"
                row["failure_reason"] = "; ".join(discovery.get("warnings") or []) or "no job source observed"
        if plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            row.update({"pagination": plan.get("pagination_type") or "UNKNOWN", "plan_executable": bool(plan.get("executable"))})
            parsed_plan = CollectionPlan.model_validate(plan)
            validation = generic.CollectionPlanValidator().validate(parsed_plan)
            row["plan_valid"] = bool(validation.valid)
            try:
                from job_extractor.collectors.generic_ats import GenericATSCollector
                row["dispatchable"] = GenericATSCollector.can_dispatch(parsed_plan)
            except Exception:
                pass
        if collected_path.exists():
            collected = json.loads(collected_path.read_text(encoding="utf-8")); dc = collected.get("data_completeness") or {}
            row.update({"collection_started": True, "expected": collected.get("total_expected"), "raw_rows": collected.get("total_unique") or 0, "unique": collected.get("total_unique") or 0, "collection_status": collected.get("status") or "UNKNOWN", "jd_complete": dc.get("jd_complete") or 0, "jd_total": dc.get("total_jobs") or 0})
            row["jd_complete_rate"] = round(row["jd_complete"] / row["jd_total"], 6) if row["jd_total"] else 0.0
            reports = folder / "reports"; row["excel_success"] = (reports / "jobs.xlsx").exists(); row["json_success"] = (reports / "jobs.json").exists(); row["markdown_success"] = (reports / "report.md").exists()
            if row["collection_status"] == "FAILED":
                row["failure_category"] = "PLAN_EXECUTION_FAILURE"; row["failure_reason"] = "; ".join(collected.get("errors") or [])[:500]
        rows.append(row)
    write(rows)


if __name__ == "__main__":
    import sys
    render_partial() if "--render-partial" in sys.argv else main()
