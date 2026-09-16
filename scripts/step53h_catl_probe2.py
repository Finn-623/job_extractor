"""STEP 53H — CATL probe 2: decrypted batch location + batch identity evidence.

Answers with real evidence:
  1. Where do decoded job records appear after each official pagination click?
     (window state scan via production STATE_SCAN_JS, sampled per batch)
  2. Per-batch identity: requested/effective offset+limit, job ids, hashes.
  3. Total stability across batches.
Read-only. Writes artifacts/step53h_pagination/catl_lineage2.json.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.runtime_data import (
    RUNTIME_PAGINATION_SCRIPT,
    RUNTIME_PAGINATION_SCAN_JS,
    STATE_SCAN_JS,
)

URL = "https://app.mokahr.com/campus-recruitment/catlhr/148948#/jobs"
OUT = Path("artifacts/step53h_pagination/catl_lineage2.json")

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
  if (target) { target.click(); return {ok: true, via: 'PAGE_NUMBER'}; }
  return {ok: false};
}
"""

# Snapshot every window-global array that looks like a job list (id+title keys),
# with full ids per batch so identity can be diffed across clicks.
STATE_JOBS_JS = r"""
() => {
  const idRe = /^(id|job_?id|jobid|position_?id|positionid|uuid)$/i;
  const titleRe = /^(title|job_?title|jobtitle|position_?name|positionname|name)$/i;
  const skipRe = /^(window|self|top|parent|frames|document|location|navigator|performance|history|crypto|localStorage|sessionStorage|indexedDB)$/;
  const seen = new WeakSet();
  const out = [];
  function isWindow(v){ try{ return Object.prototype.toString.call(v)==='[object Window]'; }catch(e){ return false; } }
  function walk(v,path,depth){
    if(depth>7||v===null||out.length>=25) return;
    const t=typeof v; if(t!=='object') return;
    if(typeof Node!=='undefined'&&v instanceof Node) return;
    if(isWindow(v)) return;
    try{ if(seen.has(v)) return; seen.add(v); }catch(e){ return; }
    if(Array.isArray(v)){
      if(v.length>=5){
        const objs=v.slice(0,8).filter(x=>x&&typeof x==='object'&&!Array.isArray(x));
        if(objs.length>=2){
          const keys=new Set(objs.flatMap(o=>Object.keys(o)));
          if([...keys].some(k=>idRe.test(k))&&[...keys].some(k=>titleRe.test(k))){
            out.push({path, count:v.length, ids:v.map(x=>x&&(x.id||x.jobId||x.job_id||x.positionId)).map(String).slice(0,400),
                      titles:v.slice(0,3).map(x=>x&&(x.title||x.name)).map(String)});
          }
        }
      }
      return;
    }
    let keys=[]; try{ keys=Object.keys(v); }catch(e){ return; }
    for(const k of keys.slice(0,150)){ if(skipRe.test(k)) continue; let c; try{ c=v[k]; }catch(e){ continue; } walk(c, path?path+'.'+k:k, depth+1); }
  }
  try{ for(const k of Object.keys(window)){ if(skipRe.test(k)) continue; let v; try{ v=window[k]; }catch(e){ continue; } walk(v,'window.'+k,0); } }catch(e){}
  return out;
}
"""


def sig(ids: list[str]) -> dict:
    uniq = list(dict.fromkeys(ids))
    return {"count": len(ids), "unique": len(uniq), "first": uniq[0] if uniq else None,
            "last": uniq[-1] if uniq else None,
            "ordered_hash": sha256(json.dumps(ids).encode()).hexdigest()[:16],
            "set_hash": sha256(json.dumps(sorted(uniq)).encode()).hexdigest()[:16]}


def main() -> None:
    timeline: list[dict] = []
    batch_ids: dict[str, list[str]] = {}
    with BrowserRuntime(timeout_ms=20000) as runtime:
        page = runtime.page
        page.add_init_script(RUNTIME_PAGINATION_SCRIPT)
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(9000)

        def snapshot(label: str, requested_offset: int | None) -> dict:
            requests = (page.evaluate(RUNTIME_PAGINATION_SCAN_JS) or {}).get("requests") or []
            state_arrays = page.evaluate(STATE_JOBS_JS) or []
            scan = page.evaluate(STATE_SCAN_JS) or {}
            totals = []
            for entry in scan.get("totals") or []:
                scalars = entry.get("scalars") if isinstance(entry, dict) else None
                if isinstance(scalars, dict) and isinstance(scalars.get("total"), int):
                    totals.append({"path": entry.get("path"), "total": scalars["total"]})
            # pick the largest job-bearing array as the runtime list snapshot
            arrays = sorted(state_arrays, key=lambda a: -a.get("count", 0))
            chosen = arrays[0] if arrays else None
            if chosen:
                batch_ids[str(requested_offset if requested_offset is not None else label)] = list(chosen["ids"])
            snap = {
                "label": label, "requested_offset": requested_offset,
                "observed_requests": requests[-4:],
                "runtime_state_arrays": [{"path": a["path"], "count": a["count"], "titles": a["titles"]} for a in arrays[:4]],
                "identity": sig(chosen["ids"]) if chosen else None,
                "chosen_state_path": chosen["path"] if chosen else None,
                "state_totals": totals[:6],
                "mechanism": scan.get("mechanism"),
            }
            timeline.append(snap)
            print(json.dumps({"label": label, "offset": requested_offset, "array": (chosen or {}).get("path"),
                              "count": (chosen or {}).get("count"), "unique": (snap["identity"] or {}).get("unique"),
                              "totals": sorted({t["total"] for t in totals})}, ensure_ascii=False), flush=True)
            return snap

        snapshot("batch1_boot", 0)
        for page_number in (2, 3):
            clicked = page.evaluate(CLICK_JS, page_number)
            page.wait_for_timeout(4500)
            requests = (page.evaluate(RUNTIME_PAGINATION_SCAN_JS) or {}).get("requests") or []
            eff = requests[-1].get("offset") if requests else None
            snapshot(f"batch{page_number}_click", eff)

        # overlap probe: click back to page 1 — same ids as batch1?
        clicked = page.evaluate(CLICK_JS, 1)
        page.wait_for_timeout(4000)
        snap = snapshot("batch_back_to_1", None)
        snap["clicked_back"] = clicked

    # cross-batch duplicate analysis from per-batch id lists
    labels = list(batch_ids)
    overlap: list[dict] = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = set(batch_ids[labels[i]]), set(batch_ids[labels[j]])
            overlap.append({"between": [labels[i], labels[j]],
                            "shared_ids": len(a & b), "new_ids": len(b - a),
                            "a_size": len(a), "b_size": len(b)})
    report = {"site": "catl", "url": URL, "timeline": timeline, "cross_batch_overlap": overlap,
              "batch_ids": batch_ids}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"overlap": overlap, "output": str(OUT)}, ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
