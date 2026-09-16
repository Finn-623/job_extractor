"""STEP 53C read-only data-lineage collector (diagnostic only, no production change).

For each site, records:
  - every XHR/fetch response: endpoint, method, body, content-type, raw kind
    (JSON / text / HTML / JSONP / binary / empty), top-level schema, candidate
    list path via the production analyzer, and a redacted raw preview.
  - browser runtime state scan (window globals) for plaintext job arrays.
  - DOM ground truth: visible job-like lines + structured card metrics.

All output is redacted via the production sanitizer (safe_business_data).
Raw previews are truncated and additionally scrubbed of token/cookie patterns.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.network_analyzer import list_observation, safe_business_data

ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "step53c_decode_boundary"
SITES = [
    ("NAURA", "https://career.naura.com/campus/jobs"),
    ("CXMT", "https://cxmt.zhiye.com/campus/jobs"),
    ("Guangzhou Metro", "https://gzmetro.zhiye.com/campus/jobs"),
    ("Hisense", "https://jobs.hisense.com/campus/jobs"),
]
JOB_URL_RE = re.compile(r"job|position|post|recruit|zpxx|zhaopin|careers", re.I)
REDACT_RE = re.compile(r"(?i)(token|cookie|authorization|session|signature|csrf|passwd|password)\s*[\"']?\s*[:=]\s*[\"']?[^,;\s\"']+")


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def classify_raw(text: str, content_type: str) -> str:
    """Classify the raw response body into the STEP 53C raw-type taxonomy."""
    ct = (content_type or "").lower()
    if not text:
        return "EMPTY"
    head = text.lstrip()[:200]
    if re.match(r"^<\?xml|^<html|^<!doctype", head, re.I) or "text/html" in ct:
        return "HTML"
    if re.match(r"^[\w$.\[]+\s*\(", head):  # JSONP callback wrapper
        return "JSONP"
    try:
        json.loads(text)
        return "JSON"
    except Exception:
        pass
    if "javascript" in ct or "text/plain" in ct:
        return "TEXT"
    if "\x00" in text[:200] or not text.isprintable():
        return "BINARY_OR_ENCODED"
    return "TEXT"


def redact_preview(text: str, limit: int = 600) -> str:
    cleaned = REDACT_RE.sub(lambda m: m.group(1) + "=[REDACTED]", text or "")
    return cleaned[:limit]


def collect(site: tuple[str, str]) -> None:
    company, url = site
    requests: list[dict] = []
    with BrowserRuntime(timeout_ms=25000) as browser:
        page = browser.page

        def on_response(response) -> None:
            request = response.request
            if request.resource_type not in ("xhr", "fetch"):
                return
            if not JOB_URL_RE.search(request.url):
                return
            try:
                text = response.text()
            except Exception:
                text = ""
            content_type = response.headers.get("content-type", "")
            entry = {
                "endpoint": request.url,
                "method": request.method,
                "request_body": (request.post_data or "")[:800] or None,
                "status": response.status,
                "content_type": content_type,
                "raw_type": classify_raw(text, content_type),
                "raw_length": len(text),
                "raw_sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16] if text else None,
            }
            try:
                body = json.loads(text)
            except Exception:
                body = None
            if body is not None:
                path, count, sample = list_observation(body)
                entry.update({
                    "parsed": True,
                    "top_level_keys": list(body)[:60] if isinstance(body, dict) else ("__list__" if isinstance(body, list) else type(body).__name__),
                    "candidate_list_path": path,
                    "candidate_list_length": count,
                    "sample_redacted": safe_business_data(sample[:3]) if sample else [],
                })
            else:
                entry.update({
                    "parsed": False,
                    "raw_preview_redacted": redact_preview(text),
                })
            requests.append(entry)

        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(9000)

        runtime_state = page.evaluate(RUNTIME_SCAN_JS)
        body = page.locator("body").inner_text()[:30000]
        job_lines = [line.strip() for line in body.splitlines() if line.strip() and JOB_LINE_RE.search(line)][:20]
        cards = page.evaluate(DOM_STRUCTURE_JS)

    out_dir = ROOT / "per_site" / slug(company)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "requests.json").write_text(
        json.dumps({"company": company, "url": url, "observed_count": len(requests), "requests": requests}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_dir / "runtime_evidence.json").write_text(
        json.dumps(safe_business_data(runtime_state), ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "dom_evidence.json").write_text(
        json.dumps({"visible_job_samples": job_lines, "dom_metrics": cards}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{company}: network={len(requests)} runtime_arrays={len(runtime_state.get('arrays', []))} dom_job_lines={len(job_lines)}", flush=True)


JOB_LINE_RE = re.compile(r"(?:\(J\d{3,}\)|工程师|经理|专员|主管|研究员|设计师|助理|职位|岗位|招聘)")


# ---------------------------------------------------------------------------
# In-page scan JS: runtime globals for plaintext job arrays (state audit),
# plus structured-DOM metrics. Read-only; no site-specific keys assumed.
# ---------------------------------------------------------------------------
RUNTIME_SCAN_JS = r"""
() => {
  const idRe = /^(id|job_?id|jobid|position_?id|positionid|post_?id|postid|posting_?id|requisition_?id|jobadid|recruitid)$/i;
  const titleRe = /^(title|job_?title|jobtitle|position_?name|positionname|post_?name|postname|job_?name|jobname|jobadname|recruitname)$/i;
  const skipRe = /^(window|self|top|parent|frames|document|location|navigator|performance|history|crypto|localStorage|sessionStorage|indexedDB|chrome|caches)$/;
  const seen = new WeakSet();
  const arrays = [];
  const globals = [];
  function isWindow(v){ try{ return Object.prototype.toString.call(v)==='[object Window]'; }catch(e){ return false; } }
  function looksJobArray(arr){
    if(!Array.isArray(arr)||arr.length<2) return false;
    const objs = arr.slice(0,8).filter(x=>x && typeof x==='object' && !Array.isArray(x));
    if(objs.length<2) return false;
    const keys = new Set(objs.flatMap(o=>Object.keys(o)));
    return [...keys].some(k=>idRe.test(k)) && [...keys].some(k=>titleRe.test(k));
  }
  function sampleRecord(rec){
    if(!rec || typeof rec!=='object') return null;
    const out = {};
    for(const k of Object.keys(rec).slice(0,25)){
      const v = rec[k];
      out[k] = (typeof v==='number'||typeof v==='boolean'||v===null) ? v : (typeof v==='string' ? v.slice(0,60) : (Array.isArray(v)?('[array:'+v.length+']'):(typeof v==='object'?'[object]':String(v))));
    }
    return out;
  }
  function walk(v, path, depth){
    if(depth>8 || v===null || arrays.length>=25) return;
    if(typeof v!=='object') return;
    if(typeof Node!=='undefined' && v instanceof Node) return;
    if(isWindow(v)) return;
    try{ if(seen.has(v)) return; seen.add(v); }catch(e){ return; }
    if(Array.isArray(v)){
      if(looksJobArray(v)) arrays.push({path, count:v.length, sample:v.slice(0,3).map(sampleRecord)});
      return;
    }
    let keys=[]; try{ keys=Object.keys(v); }catch(e){ return; }
    globals.push({path, keys: keys.slice(0,30)});
    for(const k of keys.slice(0,120)){
      if(skipRe.test(k)) continue;
      let child; try{ child=v[k]; }catch(e){ continue; }
      walk(child, path?path+'.'+k:k, depth+1);
    }
  }
  try{
    for(const k of Object.keys(window)){
      if(skipRe.test(k)) continue;
      let v; try{ v=window[k]; }catch(e){ continue; }
      walk(v, 'window.'+k, 0);
    }
  }catch(e){}
  return {arrays, top_globals: globals.slice(0,80)};
}
"""

DOM_STRUCTURE_JS = r"""
() => {
  const anchors = [...document.querySelectorAll('a')];
  const jobAnchors = anchors.filter(a => {
    const t = (a.innerText||'').trim();
    return t.length>=4 && t.length<=60 && /[\u4e00-\u9fa5a-zA-Z]/.test(t) && (a.href||'').length>10;
  });
  const containers = [...document.querySelectorAll('ul,div,table')].filter(n => n.querySelectorAll('a').length >= 5);
  return {
    anchor_count: anchors.length,
    link_samples: jobAnchors.slice(0,10).map(a => ({text:(a.innerText||'').trim().slice(0,50), href:(a.href||'').slice(0,120)})),
    listlike_containers: containers.slice(0,5).map(c => ({tag:c.tagName, cls:String(c.className).slice(0,80), links:c.querySelectorAll('a').length})),
    body_text_length: (document.body.innerText||'').length,
    has_pagination_text: /[下一页]|[>]1?\s*\/\s*\d+|\d+\s*条\/页|共\s*\d+\s*条/.test(document.body.innerText||'')
  };
}
"""


if __name__ == "__main__":
    if "--site" in sys.argv:
        target = sys.argv[sys.argv.index("--site") + 1].lower()
        collect(next(s for s in SITES if slug(s[0]) == target or s[0].lower() == target))
    else:
        for site in SITES:
            try:
                collect(site)
            except Exception as exc:  # keep other sites going
                print(f"FAILED {site[0]}: {exc}", flush=True)
