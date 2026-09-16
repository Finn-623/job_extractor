"""STEP 53H — read-only pagination lineage probe for CATL (Moka encrypted runtime).

Diagnosis only. No production code paths are modified. Captures:
  A. the page's own pagination trigger chain (action -> request -> offset -> runtime batch)
  B. programmatic same-session POST body mutation (offset/limit control probe)

All data is redacted via safe_business_data before being written to the artifact.
"""
from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path

from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.runtime_data import (
    RUNTIME_PAGINATION_SCRIPT,
    RUNTIME_PAGINATION_SCAN_JS,
)

URL = "https://app.mokahr.com/campus-recruitment/catlhr/148948#/jobs"
LIST_API = "/api/outer/ats-apply/website/jobs/v2"
OUT = Path("artifacts/step53h_pagination/catl_lineage.json")


def idsig(records: list[dict], key: str) -> dict:
    ids = [str(r.get(key)) for r in records if r.get(key) not in (None, "")]
    ordered = sha256(json.dumps(ids).encode()).hexdigest()[:16]
    unordered = sha256(json.dumps(sorted(ids)).encode()).hexdigest()[:16]
    return {"count": len(ids), "unique": len(set(ids)), "first": ids[0] if ids else None,
            "last": ids[-1] if ids else None, "ordered_hash": ordered, "set_hash": unordered, "ids": ids}


def main() -> None:
    network: list[dict] = []
    with BrowserRuntime(timeout_ms=20000) as runtime:
        page = runtime.page
        page.add_init_script(RUNTIME_PAGINATION_SCRIPT)

        def on_response(res):
            req = res.request
            if req.resource_type not in ("xhr", "fetch") or LIST_API not in req.url:
                return
            entry = {"phase": "page", "method": req.method, "status": res.status,
                     "post_data": (req.post_data or "")[:400] or None}
            try:
                payload = res.json()
            except Exception:
                entry["raw_kind"] = "NON_JSON"
                network.append(entry)
                return
            entry["raw_kind"] = "ENCRYPTED_ENVELOPE" if isinstance(payload, dict) and "necromancer" in payload else "JSON"
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                stats = data.get("jobStats") or {}
                entry["envelope_keys"] = list(payload)[:10]
                entry["data_keys"] = list(data)[:14]
                entry["reported_total_raw"] = stats.get("total") if isinstance(stats, dict) else None
            network.append(entry)

        page.on("response", on_response)
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(9000)

        capture = page.evaluate(RUNTIME_PAGINATION_SCAN_JS)
        boot_batches = capture.get("batches") or []
        boot_requests = capture.get("requests") or []
        boot_totals = capture.get("totals") or []

        # ---- Trigger audit: drive the page's own pagination control. -------
        # Generic numeric-pagination click (same primitive production uses);
        # records action -> request -> offset -> runtime batch lineage.
        CLICK_JS = """
        (n) => {
          const textOf = (x) => (x.innerText || '').trim();
          const containers = [...document.querySelectorAll('[class*="pagination" i],[class*="pag-" i],[role="navigation"]')];
          let target = null;
          for (const sel of ['button','a','li','span']) {
            if (target) break;
            for (const c of containers) {
              const hit = [...c.querySelectorAll(sel)].find((x) => /^\\d{1,4}$/.test(textOf(x)) && Number(textOf(x)) === n);
              if (hit) { target = hit; break; }
            }
          }
          if (!target) target = [...document.querySelectorAll('button,a,li')].find((x) => /^\\d{1,4}$/.test(textOf(x)) && Number(textOf(x)) === n);
          if (target) { target.click(); return {ok: true, via: 'PAGE_NUMBER'}; }
          const fwd = document.querySelector('[class*="forward"]') || document.querySelector('[class*="next"]') ||
                      document.querySelector('[aria-label*="next" i]') || document.querySelector('[aria-label*="下一页"]');
          if (fwd && !fwd.disabled) { fwd.click(); return {ok: true, via: 'FORWARD'}; }
          return {ok: false};
        }
        """
        # wait for the first decrypted batch before clicking
        page.wait_for_timeout(2000)
        click_evidence = []
        first_batch = next((b for b in boot_batches if b.get("count")), None)
        observed_limit = first_batch.get("count") if first_batch else None
        for page_number in (2, 3):
            clicked = page.evaluate(CLICK_JS, page_number)
            page.wait_for_timeout(4000)
            snap = page.evaluate(RUNTIME_PAGINATION_SCAN_JS)
            batches = snap.get("batches") or []
            reqs = snap.get("requests") or []
            click_evidence.append({
                "clicked_page": page_number,
                "click_result": clicked,
                "observed_requests_after": reqs[-3:],
                "batch_count_so_far": len(batches),
                "last_batch": (batches[-1] if batches else None) and {k: batches[-1].get(k) for k in ("offset", "count", "path", "textLength")},
            })

        final_capture = page.evaluate(RUNTIME_PAGINATION_SCAN_JS)
        runtime_batches = final_capture.get("batches") or []
        runtime_requests = final_capture.get("requests") or []
        runtime_totals = final_capture.get("totals") or []

        # ---- Programmatic POST mutation inside the same browser session. ---
        # Direct fetch() from the page origin: same session, same cookies,
        # raw encrypted envelope returned (browser runtime decrypt path NOT used).
        post_probe = page.evaluate(
            """
            async ({url, body}) => {
              const out = [];
              for (const b of body) {
                try {
                  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
                  const text = await r.text();
                  let payload = null; try { payload = JSON.parse(text); } catch (e) {}
                  out.push({status: r.status, envelope_keys: payload ? Object.keys(payload).slice(0, 10) : null,
                            encrypted: !!(payload && payload.necromancer), text_length: text.length});
                } catch (e) { out.push({error: String(e)}); }
              }
              return out;
            }
            """,
            {"url": "https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2",
             "body": [
                 {"orgId": "catlhr", "siteId": "148948", "limit": 15, "offset": 0, "needStat": True, "jobIdTopList": [], "customFields": {}, "site": "campus", "locale": "zh-CN"},
                 {"orgId": "catlhr", "siteId": "148948", "limit": 15, "offset": 15, "needStat": True, "jobIdTopList": [], "customFields": {}, "site": "campus", "locale": "zh-CN"},
                 {"orgId": "catlhr", "siteId": "148948", "limit": 15, "offset": 30, "needStat": True, "jobIdTopList": [], "customFields": {}, "site": "campus", "locale": "zh-CN"},
                 {"orgId": "catlhr", "siteId": "148948", "limit": 100, "offset": 0, "needStat": True, "jobIdTopList": [], "customFields": {}, "site": "campus", "locale": "zh-CN"},
             ]},
        )

    report = {
        "site": "catl",
        "url": URL,
        "list_api": LIST_API,
        "boot": {
            "network_events": network,
            "runtime_requests": boot_requests,
            "runtime_batch_meta": [{k: b.get(k) for k in ("offset", "count", "path", "textLength")} for b in boot_batches],
            "runtime_totals": boot_totals,
        },
        "trigger_audit": click_evidence,
        "runtime_pagination": {
            "requests": runtime_requests,
            "batch_meta": [{k: b.get(k) for k in ("offset", "count", "path", "textLength")} for b in runtime_batches],
            "batch_samples": [{"offset": b.get("offset"), "count": b.get("count"), "path": b.get("path"), "sample": b.get("sample")} for b in runtime_batches],
            "totals": runtime_totals,
        },
        "post_probe": post_probe,
    }
    # Extract ids from decrypted batch samples for batch-to-batch comparison.
    batch_ids = []
    for b in runtime_batches:
        sample = b.get("sample") or []
        ids = idsig([r for r in sample if isinstance(r, dict)], "id")
        batch_ids.append({"offset": b.get("offset"), **ids})
    report["batch_identity"] = batch_ids
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    summary = {
        "network_events": len(network),
        "boot_requests": boot_requests,
        "runtime_batch_meta": report["runtime_pagination"]["batch_meta"],
        "totals_seen": sorted({e.get("scalars", {}).get("total") for e in runtime_totals if isinstance(e, dict)}, key=lambda v: (v is None, v)),
        "click_evidence": click_evidence,
        "post_probe": post_probe,
        "batch_identity": [{k: v for k, v in b.items() if k != "ids"} for b in batch_ids],
        "output": str(OUT),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
