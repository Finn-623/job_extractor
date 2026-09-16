"""STEP 53H — CATL probe 3: locate the REAL decoded list location after pagination clicks.

lineage2 showed: page's own pagination fires offset 0->30->60 (limit 30) but
window.TurboApply.data.jobs stays identical (same 15 ids). This probe scans,
per click: DOM rendered job cards, the full TurboApply tree, and other global
state, to find where the decoded, page-specific records actually live.
Read-only. Writes artifacts/step53h_pagination/catl_lineage3.json.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.runtime_data import (
    RUNTIME_PAGINATION_SCRIPT,
    RUNTIME_PAGINATION_SCAN_JS,
)

URL = "https://app.mokahr.com/campus-recruitment/catlhr/148948#/jobs"
OUT = Path("artifacts/step53h_pagination/catl_lineage3.json")

CLICK_JS = r"""
(n) => {
  const textOf = (x) => (x.innerText || '').trim();
  const containers = [...document.querySelectorAll('[class*="pagination" i],[class*="pag-" i],[role="navigation"]')];
  let target = null;
  for (const sel of ['button','a','li','span']) {
    if (target) break;
    for (const c of containers) {
      const hit = [...c.querySelectorAll(sel)].find((x) => /^\d{1,4}$/.test(textOf(x)) && Number(textOf(x)) === n);
      if (hit) { target = hit; break; }
    }
  }
  if (!target) target = [...document.querySelectorAll('button,a,li')].find((x) => /^\d{1,4}$/.test(textOf(x)) && Number(textOf(x)) === n);
  if (target) { target.click(); return {ok: true, via: 'PAGE_NUMBER', tag: target.tagName, cls: target.className}; }
  return {ok: false};
}
"""

DOM_CARDS_JS = r"""
() => {
  const cards = [...document.querySelectorAll('a[href*="#/job/"]')];
  return cards.map(a => ({href: a.getAttribute('href'), text: (a.innerText||'').trim().slice(0,80)}));
}
"""

# Full scan of window.TurboApply: every array with object members + id-ish keys,
# every scalar under keys matching total/count/page/offset/limit.
TURBO_SCAN_JS = r"""
() => {
  const root = window.TurboApply || window.__TurboApply__ || null;
  if (!root) return {present: false};
  const idRe = /^(id|job_?id|jobid|position_?id|uuid)$/i;
  const titleRe = /^(title|job_?title|jobtitle|position_?name|name)$/i;
  const pageRe = /^(total|count|totalcount|offset|limit|page|pagesize|pagenum|current|size|hasmore)$/i;
  const seen = new WeakSet();
  const arrays = [], scalars = [];
  function walk(v, path, depth){
    if(depth>9||v===null||arrays.length+scalars.length>=60) return;
    const t=typeof v; if(t!=='object') return;
    try{ if(seen.has(v)) return; seen.add(v); }catch(e){ return; }
    if(Array.isArray(v)){
      if(v.length>=2){
        const objs=v.slice(0,8).filter(x=>x&&typeof x==='object'&&!Array.isArray(x));
        if(objs.length>=2){
          const keys=new Set(objs.flatMap(o=>Object.keys(o)));
          const hasId=[...keys].some(k=>idRe.test(k)), hasTitle=[...keys].some(k=>titleRe.test(k));
          arrays.push({path, count:v.length, jobish: hasId&&hasTitle,
                       ids: hasId ? v.map(x=>x&&(x.id||x.jobId||x.job_id||x.positionId||x.jobPostId)).map(String) : null,
                       first_titles: v.slice(0,2).map(x=>x&&String(x.title||x.name||'').slice(0,40))});
        }
      }
      return;
    }
    let keys=[]; try{ keys=Object.keys(v); }catch(e){ return; }
    const sc={};
    for(const k of keys){ const val=v[k]; if((typeof val==='number'||typeof val==='boolean')&&pageRe.test(k)) sc[k]=val; }
    if(Object.keys(sc).length) scalars.push({path, scalars:sc});
    for(const k of keys.slice(0,120)){ let c; try{ c=v[k]; }catch(e){ continue; } walk(c, path?path+'.'+k:k, depth+1); }
  }
  walk(root, 'TurboApply', 0);
  return {present: true, arrays, scalars};
}
"""


def h(ids: list[str]) -> str:
    uniq = list(dict.fromkeys(ids))
    return sha256(json.dumps(sorted(uniq)).encode()).hexdigest()[:16]


def main() -> None:
    timeline: list[dict] = []
    with BrowserRuntime(timeout_ms=20000) as runtime:
        page = runtime.page
        page.add_init_script(RUNTIME_PAGINATION_SCRIPT)
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(9000)

        def snap(label: str) -> dict:
            turbo = page.evaluate(TURBO_SCAN_JS) or {}
            dom = page.evaluate(DOM_CARDS_JS) or []
            requests = (page.evaluate(RUNTIME_PAGINATION_SCAN_JS) or {}).get("requests") or []
            arrays = turbo.get("arrays") or []
            jobish = [a for a in arrays if a.get("jobish")]
            dom_hrefs = [c["href"] for c in dom]
            entry = {
                "label": label,
                "requests_so_far": [(r.get("offset"), r.get("limit")) for r in requests],
                "dom_card_count": len(dom),
                "dom_first_href": dom_hrefs[0] if dom_hrefs else None,
                "dom_href_hash": h(dom_hrefs),
                "dom_titles": [c["text"] for c in dom[:3]],
                "turbo_jobish_arrays": [{"path": a["path"], "count": a["count"], "id_hash": h(a["ids"] or []),
                                         "first_titles": a.get("first_titles")} for a in jobish],
                "turbo_scalars": turbo.get("scalars", [])[:10],
            }
            timeline.append(entry)
            print(json.dumps({k: entry[k] for k in ("label", "dom_card_count", "dom_href_hash",
                                                    "turbo_jobish_arrays", "requests_so_far")}, ensure_ascii=False), flush=True)
            return entry

        snap("batch1_boot")
        for page_number in (2, 3):
            clicked = page.evaluate(CLICK_JS, page_number)
            page.wait_for_timeout(5000)
            e = snap(f"batch{page_number}_click")
            e["clicked"] = clicked
        back = page.evaluate(CLICK_JS, 1)
        page.wait_for_timeout(5000)
        e = snap("back_to_1")
        e["clicked"] = back

    # overlap between DOM hashes
    for i in range(1, len(timeline)):
        a, b = timeline[i - 1], timeline[i]
        print(json.dumps({"between": [a["label"], b["label"]],
                          "dom_hash_changed": a["dom_href_hash"] != b["dom_href_hash"],
                          "a": a["dom_href_hash"], "b": b["dom_href_hash"]}, ensure_ascii=False))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"site": "catl", "url": URL, "timeline": timeline}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("output:", OUT, flush=True)


if __name__ == "__main__":
    main()
