"""Evidence-only precheck for STEP 52B job-bearing page inputs.

This is deliberately outside production code.  It opens supplied official
career URLs, records visible repeated job-card evidence, and makes no request
or decision changes to the extractor.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter

from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.dom_semantics import repeated_job_cards

ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "step52b_job_bearing_china" / "precheck"

# Each URL is intended to already be a public position/search/list route.  The
# precheck, rather than its spelling, decides whether it is a valid V1 input.
SITES = [
    ("Baidu control", "Internet / AI", "https://talent.baidu.com/jobs/social-list", "CONTROL"),
    ("Xiaomi control", "Consumer electronics", "https://hr.xiaomi.com/", "CONTROL"),
    ("Kuaishou control", "Internet", "https://zhaopin.kuaishou.cn/", "CONTROL"),
    ("ByteDance", "Internet / AI", "https://jobs.bytedance.com/experienced/position", "BLIND"),
    ("Feishu", "Software", "https://job.feishu.cn/", "BLIND"),
    ("Tencent", "Internet", "https://careers.tencent.com/search.html", "BLIND"),
    ("Alibaba", "Internet / cloud", "https://talent.alibaba.com/off-campus/position-list", "BLIND"),
    ("Meituan", "Internet", "https://zhaopin.meituan.com/web/social", "BLIND"),
    ("Bilibili", "Internet", "https://jobs.bilibili.com/social", "BLIND"),
    ("JD.com", "Internet", "https://zhaopin.jd.com/web/position/list", "BLIND"),
    ("NetEase", "Internet", "https://hr.163.com/job-list.html", "BLIND"),
    ("DJI", "Consumer electronics", "https://we.dji.com/zh-CN/position", "BLIND"),
    ("Huawei", "Telecom / software", "https://career.huawei.com/reccampportal/portal5/index.html", "BLIND"),
    ("ZTE", "Telecom / software", "https://job.zte.com.cn/cn/job/", "BLIND"),
    ("Lenovo", "Advanced manufacturing", "https://jobs.lenovo.com/en_US/careers", "BLIND"),
    ("Midea", "Advanced manufacturing", "https://careers.midea.com/campus", "BLIND"),
    ("Haier", "Advanced manufacturing", "https://zhaopin.haier.net/", "BLIND"),
    ("Hisense", "Consumer electronics", "https://hr.hisense.com/", "BLIND"),
    ("BYD", "New energy / automotive", "https://job.byd.com/", "BLIND"),
    ("CATL", "New energy / battery", "https://talent.catl.com/", "BLIND"),
    ("Geely", "Automotive", "https://talent.geely.com/", "BLIND"),
    ("Li Auto", "Automotive", "https://jobs.lixiang.com/", "BLIND"),
    ("NIO", "Automotive", "https://job.nio.com/", "BLIND"),
    ("XPeng", "Automotive", "https://talent.xiaopeng.com/", "BLIND"),
    ("GWM", "Automotive", "https://www.gwm.com/careers/", "BLIND"),
    ("NAURA", "Semiconductor", "https://career.naura.com/", "BLIND"),
    ("AMEC", "Semiconductor", "https://www.amec-inc.com/cn/recruitment/", "BLIND"),
    ("Huahong", "Semiconductor", "https://www.huahonggrace.com/cn/recruitment", "BLIND"),
    ("YMTC", "Semiconductor", "https://www.yangtze-memory.com/join-us/", "BLIND"),
    ("CXMT", "Semiconductor", "https://www.cxmt.com/join.html", "BLIND"),
    ("SMIC", "Semiconductor", "https://www.smics.com/en/site/careers", "BLIND"),
    ("CGN", "State-owned energy", "https://zhaopin.cgnpc.com.cn/", "BLIND"),
    ("China Merchants", "State-owned infrastructure", "https://cmhk.zhiye.com/", "BLIND"),
    ("Bosch China", "MNC China", "https://jobs.bosch.com.cn/", "BLIND"),
    ("Siemens China", "MNC China", "https://jobs.siemens.com/careers", "BLIND"),
    ("Schneider China", "MNC China", "https://careers.se.com/china?lang=zh-CN", "BLIND"),
]


def slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def check(site: tuple[str, str, str, str]) -> dict:
    company, industry, url, cohort = site
    started = perf_counter()
    path = ROOT / f"{slug(company)}.json"
    result = {"company": company, "industry": industry, "url": url, "cohort": cohort,
              "job_bearing_page_confirmed": "NO", "final_url": None, "visible_job_card_count": 0,
              "visible_job_examples": [], "body_excerpt": "", "error": None}
    try:
        with BrowserRuntime(timeout_ms=15000) as browser:
            page = browser.page
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2500)
            final = page.url
            cards = repeated_job_cards(page, final)
            body = page.locator("body").inner_text(timeout=5000).replace("\n", " ")
            result.update({"final_url": final, "visible_job_card_count": len(cards),
                           "visible_job_examples": [item.get("title") for item in cards[:5]],
                           "body_excerpt": body[:1600]})
            # A repeated, visible, job-like card group is conservative evidence
            # that a person opening this page sees a multiple-position list.
            job_markers = (
                body.count("职位 ID：") >= 2
                or body.count("查看职位") >= 2
                or ("职位列表" in body and body.count("职位") >= 8)
                or ("职位信息" in body and body.count("职位") >= 8)
            )
            if len(cards) >= 2 or job_markers:
                result["job_bearing_page_confirmed"] = "YES"
            page.screenshot(path=str(ROOT / f"{slug(company)}.png"), full_page=False)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_seconds"] = round(perf_counter() - started, 3)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    # Chromium processes are intentionally serialized: this keeps the evidence
    # pass stable on resource-constrained runners and avoids treating a local
    # browser crash as a page property.
    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = [pool.submit(check, site) for site in SITES]
        for future in as_completed(futures):
            row = future.result(); rows.append(row)
            print(f"{row['company']}: {row['job_bearing_page_confirmed']} cards={row['visible_job_card_count']} {row['error'] or ''}", flush=True)
    rows.sort(key=lambda row: row["company"])
    (ROOT / "precheck_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"valid={sum(row['job_bearing_page_confirmed'] == 'YES' for row in rows)}/{len(rows)}")


if __name__ == "__main__":
    main()
