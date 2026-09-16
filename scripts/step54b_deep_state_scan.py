"""STEP 54B read-only diagnostic: deep-scan the AMEC detail-route runtime state
to find where siteId / aesIv / locale values actually live. Generic — matches
key shapes, no site names. Determines the scan depth the discovery-state
snapshot needs so contract scope binding can resolve real evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://app.mokahr.com/campus_apply/amec/4362#/jobs?page=1&anchorName=jobsList"
SAMPLE_ID = "ac053a81-39dd-4fac-87d9-d66c5c4152e0"
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "step54b_amec" / "diagnostic"

DEEP_SCAN = r"""
() => {
  const out = [];
  const ivRe = /(aes[_-]?iv|aesiv)/i;
  const scopeRe = /(orgid|siteid|websiteid|locale|language|mode)/i;
  const secretRe = /(passwd|password|token|secret|session|csrf|signature|cookie|auth|api[_-]?key|access[_-]?key|necromancer)/i;
  const seen = new Set();
  const visit = (obj, path, depth) => {
    if (depth > 4 || out.length > 200) return;
    if (!obj || typeof obj !== 'object') return;
    if (seen.has(obj)) return;
    seen.add(obj);
    let keys = [];
    try { keys = Object.keys(obj); } catch(e) { return; }
    for (const k of keys) {
      const p = path + '.' + k;
      if (secretRe.test(k)) continue;
      let v;
      try { v = obj[k]; } catch(e) { continue; }
      if (ivRe.test(k) || (scopeRe.test(k) && (typeof v === 'string' || typeof v === 'number'))) {
        out.push({path: p, key: k, value: (typeof v === 'string' || typeof v === 'number') ? String(v).slice(0, 80) : Array.isArray(v) ? 'array' : typeof v});
      }
      if (v && typeof v === 'object' && !Array.isArray(v)) visit(v, p, depth + 1);
    }
  };
  for (const k of Object.keys(window)) {
    if (secretRe.test(k)) continue;
    let v;
    try { v = window[k]; } catch(e) { continue; }
    if (v && typeof v === 'object' && !(v instanceof Node) && v !== window) visit(v, 'window.' + k, 1);
  }
  return out;
}
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        target = f"{URL.split('#')[0]}#/job/{SAMPLE_ID}"
        page.goto(target, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        hits = page.evaluate(DEEP_SCAN)
        report = {"final_url": page.url, "hits": hits}
        print("hits:", len(hits), flush=True)
        for h in hits[:60]:
            print(f"  {h['path']} = {h['value']}", flush=True)
        browser.close()
    (OUT / "deep_state_scan.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("written:", OUT / "deep_state_scan.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
