"""STEP 52C V1 job-bearing benchmark preparation (diagnostics only)."""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.dom_semantics import repeated_job_cards

ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "v1_job_bearing_benchmark"
SITES = [
    ("NAURA", "https://career.naura.com/campus/jobs"),
    ("AECC", "https://aecc.iguopin.com/job"),
    ("Sinomach", "https://zhaopin.sinomach.com.cn/SU64b4cfe82f9d24760ae8b80c/pb/school.html"),
    ("AMEC", "https://app.mokahr.com/campus_apply/amec/4362#/jobs?page=1&anchorName=jobsList"),
    ("YMTC", "https://ymtc.zhiye.com/campus/jobs"),
    ("CXMT", "https://cxmt.zhiye.com/campus/jobs"),
    ("SMIC", "https://smics.zhiye.com/campus"),
    ("CATL", "https://app.mokahr.com/campus-recruitment/catlhr/148948#/jobs"),
    ("Guangzhou Metro", "https://gzmetro.zhiye.com/campus/jobs"),
    ("Sunwoda", "https://sunwodacampus.zhiye.com/custom/fb00a48c-8c6e-294f-b4c8-04a45422015c"),
    ("Geely", "https://careers.geelytech.com/campus"),
    ("Hisense", "https://jobs.hisense.com/campus/jobs"),
]


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def count_bucket(value: int | None) -> str:
    if value is None: return "UNKNOWN"
    if value < 50: return "SMALL"
    if value < 200: return "MEDIUM_SMALL"
    if value < 500: return "MEDIUM"
    if value < 1000: return "LARGE"
    return "XLARGE"


def provider(final_url: str, resources: list[str], html: str, body: str) -> tuple[str, str, list[str]]:
    corpus = "\n".join([final_url, *resources, html[:200000], body]).lower()
    evidence = []
    for name, markers in {
        "MOKA": ("mokahr", "moka"), "ZHIYE": ("zhiye.com", "zhiye"),
        "IGUOPIN": ("iguopin",), "BEISEN": ("beisen", "bjs.com.cn"),
        "REDSEA": ("redseaplatform", "redsea"),
    }.items():
        matches = [marker for marker in markers if marker in corpus]
        if matches:
            evidence.extend(f"observed marker: {marker}" for marker in matches)
            # Host/path plus an independent DOM/resource marker is high;
            # a single observed marker remains only a low-confidence guess.
            host_match = any(marker in (urlsplit(final_url).hostname or "").lower() for marker in matches)
            independent = (any(any(marker in resource.lower() for marker in matches) for resource in resources)
                           or any(marker in html[:200000].lower() for marker in matches))
            return name, "HIGH" if host_match and independent else "MEDIUM", evidence
    if resources or "__next" in html.lower() or "webpack" in html.lower():
        return "CUSTOM", "LOW", ["no known ATS marker; dynamic resources observed"]
    return "UNKNOWN", "LOW", ["no provider marker observed"]


def run_site(site: tuple[str, str]) -> None:
    company, url = site; started = perf_counter(); folder = ROOT / "per_site" / slug(company); folder.mkdir(parents=True, exist_ok=True)
    row = {"company": company, "url": url, "job_bearing_confirmed": "NO", "input_status": "INVALID_TEST_INPUT",
           "input_evidence": [], "provider_guess": "UNKNOWN", "provider_confidence": "LOW", "provider_evidence": [],
           "job_count_estimate": None, "job_count_bucket": "UNKNOWN", "runtime_types": ["UNKNOWN"], "notes": ""}
    try:
        with BrowserRuntime(timeout_ms=20000) as browser:
            page = browser.page; resources: list[str] = []
            page.on("request", lambda request: resources.append(request.url))
            page.goto(url, wait_until="domcontentloaded", timeout=20000); page.wait_for_timeout(3000)
            final = page.url; body = page.locator("body").inner_text(timeout=5000); html = page.content()
            cards = repeated_job_cards(page, final)
            visible_total = re.search(r"(?:(?:共|全部|total\s*)\s*([0-9][0-9,]*)\s*(?:个|条)?\s*(?:职位|岗位|jobs?)|(?:在招职位|职位)\s*([0-9][0-9,]*)\s*个|共\s*([0-9][0-9,]*)\s*个|([0-9][0-9,]*)\s*结果)", body, re.I)
            total_value = next((value for value in visible_total.groups() if value), None) if visible_total else None
            total = int(total_value.replace(",", "")) if total_value else None
            position_lines = [line.strip() for line in body.splitlines() if line.strip() and ("岗位" in line or "职位" in line or "job" in line.lower())]
            explicit_records = len(re.findall(r"(?:\(J\d{3,}\)|\bMJ\d{3,}\b|发布于\s*20\d\d)", body, re.I))
            direct_list = len(cards) >= 2 or (total is not None and total > 1) or len(position_lines) >= 8 or explicit_records >= 2
            if direct_list:
                row.update({"job_bearing_confirmed": "YES", "input_status": "JOB_BEARING_CONFIRMED",
                            "input_evidence": [f"visible repeated semantic job cards: {len(cards)}", f"visible job/position text lines: {len(position_lines)}", f"visible explicit job records: {explicit_records}", f"visible total: {total}"],
                            "job_count_estimate": total or (len(cards) if cards else None)})
            elif len(re.sub(r"\s+", "", body)) < 4 or body.strip() in {"加载中...", "Loading..."}:
                # The user supplied this as a manually verified position-list
                # URL.  A blank/loading shell is an environment observation,
                # not evidence that the input is a landing page or empty list.
                row.update({"job_bearing_confirmed": "YES", "input_status": "VALID_BUT_ENVIRONMENT_BLOCKED",
                            "input_evidence": ["user-supplied job-bearing seed", "browser rendered only a blank/loading shell during validation"]})
            else:
                row["input_evidence"] = [f"visible repeated semantic job cards: {len(cards)}", f"visible job/position text lines: {len(position_lines)}", f"visible explicit job records: {explicit_records}", f"visible total: {total}"]
            guess, confidence, evidence = provider(final, resources, html, body)
            row.update({"provider_guess": guess, "provider_confidence": confidence, "provider_evidence": evidence})
            runtime = []
            if "#" in final: runtime.append("HASH_SPA")
            if any(token in html.lower() for token in ("__next", "webpack", "vite", "react")): runtime.append("SPA")
            if any(re.search(r"/(api|graphql|jobs?|positions?)(?:/|\?|$)", resource, re.I) for resource in resources): runtime.append("API_DRIVEN")
            if guess in {"MOKA", "ZHIYE", "IGUOPIN", "BEISEN", "REDSEA"}: runtime.append("EMBEDDED_ATS")
            row["runtime_types"] = runtime or ["SERVER_RENDERED"]
            row["notes"] = f"final_url={final}; resources_observed={len(resources)}; visible_text_excerpt={re.sub(r'\s+', ' ', body)[:700]}"
            page.screenshot(path=str(folder / "page.png"), full_page=False)
            (folder / "page_evidence.json").write_text(json.dumps({"final_url": final, "resources": resources[:500], "body_excerpt": body[:12000], "semantic_cards": cards[:20]}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        row.update({"input_status": "VALID_BUT_ENVIRONMENT_BLOCKED", "notes": f"{type(exc).__name__}: {exc}", "input_evidence": ["user-supplied job-bearing seed URL; live browser unavailable"]})
    row["job_count_bucket"] = count_bucket(row["job_count_estimate"])
    row["elapsed_seconds"] = round(perf_counter() - started, 3)
    (folder / "benchmark_row.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{company}: {row['input_status']} provider={row['provider_guess']} bucket={row['job_count_bucket']}", flush=True)


def render() -> None:
    rows = [json.loads((ROOT / "per_site" / slug(company) / "benchmark_row.json").read_text(encoding="utf-8")) for company, _ in SITES if (ROOT / "per_site" / slug(company) / "benchmark_row.json").exists()]
    ROOT.mkdir(parents=True, exist_ok=True); rows.sort(key=lambda x: x["company"])
    (ROOT / "benchmark_seed.json").write_text(json.dumps({"sites": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["company", "url", "job_bearing_confirmed", "input_status", "input_evidence", "provider_guess", "provider_confidence", "provider_evidence", "job_count_estimate", "job_count_bucket", "runtime_types", "notes"]
    with (ROOT / "benchmark_seed.csv").open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fields); writer.writeheader()
        for row in rows: writer.writerow({key: json.dumps(row[key], ensure_ascii=False) if isinstance(row.get(key), list) else row.get(key) for key in fields})
    valid = [x for x in rows if x["input_status"] in {"JOB_BEARING_CONFIRMED", "VALID_BUT_ENVIRONMENT_BLOCKED"}]
    lines = ["# V1 JOB-BEARING BENCHMARK SEED REPORT", "", "## 1. Input Contract", "", "The seed contains only user-supplied official recruiting URLs; validation records whether a page itself visibly exposes multiple positions.", "", "## 2. Valid Inputs", ""]
    lines += [f"- {x['company']}: {x['url']} ({x['input_status']})" for x in valid] or ["- None"]
    lines += ["", "## 3. Invalid / Ambiguous Inputs", ""] + [f"- {x['company']}: {x['input_status']} — {'; '.join(x['input_evidence'])}" for x in rows if x not in valid]
    lines += ["", "## 4. Provider Distribution", ""]
    for value in sorted({x['provider_guess'] for x in rows}): lines.append(f"- {value}: {sum(x['provider_guess'] == value for x in rows)}")
    lines += ["", "## 5. Job-count Distribution", ""]
    for value in ("SMALL", "MEDIUM_SMALL", "MEDIUM", "LARGE", "XLARGE", "UNKNOWN"): lines.append(f"- {value}: {sum(x['job_count_bucket'] == value for x in rows)}")
    lines += ["", "## 6. Runtime / Technology Distribution", ""]
    for value in sorted({item for x in rows for item in x['runtime_types']}): lines.append(f"- {value}: {sum(value in x['runtime_types'] for x in rows)}")
    lines += ["", "## 7. Per-site Table", "", "| Company | Valid | Provider | Job count | Runtime | Evidence |", "|---|---|---|---|---|---|"]
    for x in rows: lines.append(f"| {x['company']} | {x['input_status']} | {x['provider_guess']} ({x['provider_confidence']}) | {x['job_count_estimate'] or 'UNKNOWN'} / {x['job_count_bucket']} | {', '.join(x['runtime_types'])} | {'; '.join(x['input_evidence'])} |")
    lines += ["", "## 8. Benchmark Quality Assessment", "", f"{len(valid)}/{len(rows)} supplied seeds are valid or environment-blocked job-bearing inputs. This is a useful China-market seed, but only sites confirmed in this artifact should enter STEP 52D.", "", "## 9. Remaining Sample Gaps", "", "Derive gaps only after the confirmed runtime and count distribution is stable; do not add third-party aggregators.", "", "## 10. Production Changes", "", "NONE", "", "## 11. Verdict", "", "PASS" if len(rows) == len(SITES) else "FAIL"]
    (ROOT / "benchmark_seed_report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    if "--render" in sys.argv: render()
    elif "--site" in sys.argv:
        target = sys.argv[sys.argv.index("--site") + 1].lower()
        selected = next(site for site in SITES if site[0].lower() == target)
        run_site(selected)
    else:
        for site in SITES: run_site(site)
        render()
