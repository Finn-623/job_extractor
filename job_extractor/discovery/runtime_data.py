"""Generic browser-runtime plaintext job-source observation.

The encrypted ATS provider (capability ENCRYPTED_BROWSER_API) decrypts its API
payloads inside the official browser runtime. This module locates the resulting
plaintext job entities in already-decrypted application state (mechanism STATE)
or at a generic runtime transform boundary (mechanism TRANSFORM), without any
provider-specific variable names or crypto in Python.

Only structural metadata and public job fields are captured. Secret material
(keys, tokens, cookies, IVs, encrypted blobs) is dropped before anything is
stored.
"""
from __future__ import annotations

import re
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from job_extractor.discovery.models import RuntimeJobSource
from job_extractor.discovery.network_analyzer import sensitive

ID_KEY = re.compile(r"(?i)^(id|job_?id|jobid|position_?id|positionid|requisition_?id|requisitionid|post_?id|postid|posting_?id|uuid)$")
TITLE_KEY = re.compile(r"(?i)^(title|job_?title|jobtitle|position_?name|positionname|name)$")
LOCATION_KEY = re.compile(r"(?i)^(location|locations|city|city_?name|cityname|province|province_?name|country|workplace)$")
DEPARTMENT_KEY = re.compile(r"(?i)^(department|team|category|category_?name|function|zhineng|dept|dept_?name)$")
JD_KEY = re.compile(r"(?i)^(description|job_?desc(ription)?|jobdesc|position_?desc(ription)?|content|jd_?content|jd|overview|summary|work_?content|job_?body|detail_?desc(ription)?|post_?content|rich_?text|desc|responsibilit(y|ies)|dut(y|ies)|requirement(s)?|qualification(s)?|competenc(y|ies))$")
_EXTRA_SECRET_KEY = re.compile(r"(?i)(aes[_-]?iv|necromancer|encrypt|decrypt|cipher|passwd|password|token|secret|session|csrf|signature|cookie|auth|api[_-]?key|access[_-]?key|(^|_)key$)")


def _key_is_sensitive(key: str) -> bool:
    return sensitive(str(key)) or bool(_EXTRA_SECRET_KEY.search(str(key)))


def sanitize_record(value: Any) -> Any:
    """Recursively drop secret-like keys so nothing sensitive is persisted."""
    if isinstance(value, dict):
        return {k: sanitize_record(v) for k, v in value.items() if not _key_is_sensitive(str(k))}
    if isinstance(value, list):
        return [sanitize_record(v) for v in value]
    return value


def looks_like_job_array(items: Any, minimum: int = 2) -> bool:
    if not isinstance(items, list) or len(items) < minimum:
        return False
    records = [x for x in items[:8] if isinstance(x, dict)]
    if len(records) < minimum:
        return False
    keys: set[str] = set()
    for record in records:
        keys.update(str(k) for k in record)
    return any(ID_KEY.match(k) for k in keys) and any(TITLE_KEY.match(k) for k in keys)


def _coverage(records: list[dict[str, Any]], key: str) -> int:
    return sum(1 for record in records if isinstance(record.get(key), (str, int)) and str(record.get(key)).strip() != "")


def infer_field(records: list[dict[str, Any]], pattern: re.Pattern[str]) -> str | None:
    if not records:
        return None
    keys: list[str] = []
    for record in records:
        for key in record:
            if pattern.match(str(key)) and str(key) not in keys:
                keys.append(str(key))
    ranked = sorted(keys, key=lambda key: (-_coverage(records, key), key))
    return ranked[0] if ranked and _coverage(records, ranked[0]) else None


def _pick_total(totals: Any, expected_total: int | None) -> int | None:
    values: list[int] = []
    if isinstance(totals, list):
        for entry in totals:
            scalars = entry.get("scalars") if isinstance(entry, dict) else None
            if not isinstance(scalars, dict):
                continue
            for key, value in scalars.items():
                if isinstance(value, int) and not isinstance(value, bool) and re.match(r"(?i)^(total|totalcount|total_count|count)$", str(key)):
                    values.append(value)
    if values:
        return max(values)
    return expected_total if isinstance(expected_total, int) else None


def _observed_request_pagination(observed_requests: Any) -> tuple[str | None, str | None, int | None, int | None]:
    observed = [entry for entry in (observed_requests or []) if isinstance(entry, dict)]
    offset_field = limit_field = None
    offsets: list[int] = []
    limits: list[int] = []
    for entry in observed:
        keys = {str(key).lower() for key in entry}
        if "offset" in keys:
            offset_field = "offset"
        if "limit" in keys:
            limit_field = "limit"
        if isinstance(entry.get("offset"), int) and not isinstance(entry.get("offset"), bool):
            offsets.append(entry["offset"])
        if isinstance(entry.get("limit"), int) and not isinstance(entry.get("limit"), bool):
            limits.append(entry["limit"])
    initial = min(offsets) if offsets else None
    limit = max(set(limits), key=limits.count) if limits else None
    return offset_field, limit_field, initial, limit


def build_runtime_job_source(scan: Any, provider: str = "UNKNOWN", capability: str = "ENCRYPTED_BROWSER_API", expected_total: int | None = None, request_limit: int | None = None, request_offset: int | None = None, observed_requests: Any = None, trigger: Any = None) -> RuntimeJobSource:
    if not isinstance(scan, dict):
        scan = {}
    mechanism = str(scan.get("mechanism") or "STATE").upper()
    if mechanism not in ("STATE", "TRANSFORM", "DOM", "HOOK", "OTHER"):
        mechanism = "OTHER"
    arrays = [entry for entry in (scan.get("arrays") or []) if isinstance(entry, dict) and looks_like_job_array(entry.get("sample"), 2)]
    if not arrays:
        return RuntimeJobSource(provider=provider, capability=capability, mechanism=mechanism, confidence="LOW", executable=False, evidence=["no plaintext job array observed in browser runtime"])
    arrays.sort(key=lambda entry: (-int(entry.get("count") or 0), str(entry.get("path") or "")))
    chosen = arrays[0]
    records = [sanitize_record(record) for record in (chosen.get("sample") or [])[:120] if isinstance(record, dict)]
    job_id = infer_field(records, ID_KEY)
    job_title = infer_field(records, TITLE_KEY)
    location = infer_field(records, LOCATION_KEY)
    department = infer_field(records, DEPARTMENT_KEY)
    jd_fields = sorted({str(key) for record in records for key in record if JD_KEY.match(str(key))})
    unique_ids = len({str(record.get(job_id)) for record in records if job_id and record.get(job_id) not in (None, "")}) if job_id else 0
    total = _pick_total(scan.get("totals"), expected_total)
    observed_offset_field, observed_limit_field, observed_initial, observed_limit = _observed_request_pagination(observed_requests)
    if request_offset is None:
        request_offset = observed_initial
    if request_limit is None:
        request_limit = observed_limit
    offset_field = observed_offset_field
    limit_field = observed_limit_field
    trigger_mode = "UNKNOWN"
    if isinstance(trigger, dict):
        trigger_mode = str(trigger.get("trigger_mode") or "UNKNOWN").upper()
        if trigger_mode not in ("OFFSET_PAGE", "OFFSET_BUTTON", "OFFSET_SCROLL", "OTHER", "UNKNOWN"):
            trigger_mode = "OTHER"
    page_count = (total + request_limit - 1) // request_limit if (total is not None and request_limit) else None
    pagination_validated = bool(total is not None and request_limit and trigger_mode in ("OFFSET_PAGE", "OFFSET_BUTTON") and offset_field and limit_field)
    pagination: dict[str, Any] = {}
    if total is not None:
        pagination["total"] = total
    if request_limit is not None:
        pagination["limit"] = request_limit
    if request_offset is not None:
        pagination["offset"] = request_offset
    if offset_field:
        pagination["offset_field"] = offset_field
    if limit_field:
        pagination["limit_field"] = limit_field
    if page_count is not None:
        pagination["page_count"] = page_count
    evidence = [f"plaintext job array observed in {mechanism}: {chosen.get('path')}({len(records)} of {chosen.get('count')})"]
    if job_id:
        evidence.append(f"stable id field {job_id} ({unique_ids} unique)")
    if job_title:
        evidence.append(f"title field {job_title}")
    if location:
        evidence.append(f"location field {location}")
    if department:
        evidence.append(f"department field {department}")
    if jd_fields:
        evidence.append("JD fields: " + ",".join(jd_fields))
    if total is not None:
        evidence.append(f"runtime total {total}")
    confidence = "HIGH" if (len(records) >= 2 and unique_ids >= 2 and job_title) else ("MEDIUM" if (len(records) >= 2 and job_title) else "LOW")
    complete = bool(total is not None and total == len(records) and len(records) > 0)
    if not complete and total is not None:
        evidence.append("runtime exposes more records than this state snapshot; pagination required" if not pagination_validated else "runtime pagination contract validated; batches required for completeness")
    if pagination_validated:
        evidence.append(f"runtime pagination: OFFSET initial={request_offset} limit={request_limit} total={total} pages={page_count} trigger={trigger_mode}")
    return RuntimeJobSource(
        provider=provider,
        capability=capability,
        mechanism=mechanism,
        source_path=str(chosen.get("path") or "") or None,
        record_count=len(records),
        unique_ids=unique_ids,
        job_id_field=job_id,
        job_title_field=job_title,
        location_field=location,
        department_field=department,
        jd_fields=jd_fields,
        pagination=pagination,
        pagination_model="OFFSET" if pagination_validated else "UNKNOWN",
        offset_field=offset_field,
        limit_field=limit_field,
        initial_offset=request_offset,
        limit=request_limit,
        total=total,
        page_count=page_count,
        trigger_mode=trigger_mode,
        pagination_validated=pagination_validated,
        records=records,
        confidence=confidence,
        executable=bool(confidence == "HIGH" and (complete or pagination_validated)),
        evidence=evidence,
    )


STATE_SCAN_JS = r"""
() => {
  const idRe = /^(id|job_?id|jobid|position_?id|positionid|requisition_?id|requisitionid|post_?id|postid|posting_?id|uuid)$/i;
  const titleRe = /^(title|job_?title|jobtitle|position_?name|positionname|name)$/i;
  const pageRe = /^(total|count|totalcount|total_count|hasmore|has_more|offset|limit|page|pages|size|pagesize)$/i;
  const skipRe = /^(window|self|top|parent|frames|document|location|navigator|performance|history|crypto|localStorage|sessionStorage|indexedDB)$/;
  const seen = new WeakSet();
  const arrays = [];
  const totals = [];
  function isWindow(v){ try{ return Object.prototype.toString.call(v)==='[object Window]'; }catch(e){ return false; } }
  function looksJobArray(arr){
    if(!Array.isArray(arr)||arr.length<2)return false;
    const objs=arr.slice(0,8).filter(x=>x&&typeof x==='object'&&!Array.isArray(x));
    if(objs.length<2)return false;
    const keys=new Set(objs.flatMap(o=>Object.keys(o)));
    return [...keys].some(k=>idRe.test(k)) && [...keys].some(k=>titleRe.test(k));
  }
  function walk(v,path,depth){
    if(depth>7||v===null||arrays.length>=20) return;
    const t=typeof v; if(t!=='object') return;
    if(typeof Node!=='undefined'&&v instanceof Node) return;
    if(isWindow(v)) return;
    try{ if(seen.has(v)) return; seen.add(v); }catch(e){ return; }
    if(Array.isArray(v)){ if(looksJobArray(v)) arrays.push({path, count:v.length, sample:v.slice(0,120)}); return; }
    let keys=[]; try{ keys=Object.keys(v); }catch(e){ return; }
    const scalars={};
    for(const k of keys){ const val=v[k]; if((typeof val==='number'||typeof val==='boolean')&&pageRe.test(k)) scalars[k]=val; }
    if(Object.keys(scalars).length) totals.push({path, scalars});
    for(const k of keys.slice(0,150)){
      if(skipRe.test(k)) continue;
      let child; try{ child=v[k]; }catch(e){ continue; }
      walk(child, path?path+'.'+k:k, depth+1);
    }
  }
  try{
    for(const k of Object.keys(window)){
      if(skipRe.test(k)) continue;
      let v; try{ v=window[k]; }catch(e){ continue; }
      walk(v,'window.'+k,0);
    }
  }catch(e){}
  return {mechanism:'STATE', arrays, totals};
}
"""

TRANSFORM_CAPTURE_SCRIPT = r"""
(() => {
  window.__runtimeCapture = {arrays: [], totals: []};
  const idRe = /^(id|job_?id|jobid|position_?id|positionid|requisition_?id|requisitionid|post_?id|postid|posting_?id|uuid)$/i;
  const titleRe = /^(title|job_?title|jobtitle|position_?name|positionname|name)$/i;
  function looksJobArray(arr){
    if(!Array.isArray(arr)||arr.length<2)return false;
    const objs=arr.slice(0,8).filter(x=>x&&typeof x==='object'&&!Array.isArray(x));
    if(objs.length<2)return false;
    const keys=new Set(objs.flatMap(o=>Object.keys(o)));
    return [...keys].some(k=>idRe.test(k)) && [...keys].some(k=>titleRe.test(k));
  }
  function find(root,path,depth,out){
    if(depth>6||out.length>=20||root===null) return;
    if(Array.isArray(root)){ if(looksJobArray(root)) out.push({path, count:root.length, sample:root.slice(0,120)}); return; }
    if(typeof root!=='object') return;
    let keys=[]; try{keys=Object.keys(root);}catch(e){return;}
    for(const k of keys.slice(0,120)){ let c; try{c=root[k];}catch(e){continue;} find(c,path?path+'.'+k:k,depth+1,out); }
  }
  const originalParse = JSON.parse;
  JSON.parse = function(text, reviver){
    let value; try{ value = originalParse.call(this, text, reviver); }catch(e){ throw e; }
    try{ find(value,'',0,window.__runtimeCapture.arrays); }catch(e){}
    return value;
  };
})();
"""

TRANSFORM_SCAN_JS = r"""
() => {
  const capture = window.__runtimeCapture || {arrays:[], totals:[]};
  return {mechanism:'TRANSFORM', arrays:capture.arrays, totals:capture.totals};
}
"""


def runtime_hook_allowed(fingerprint, *, trust: str, company_continuity: str, scope_compatible: bool) -> bool:
    """Runtime plaintext is only trusted for a HIGH ENCRYPTED_BROWSER_API provider terminal."""
    if fingerprint is None or getattr(fingerprint, "confidence", None) != "HIGH":
        return False
    if "ENCRYPTED_BROWSER_API" not in (getattr(fingerprint, "capabilities", None) or []):
        return False
    return trust == "TRUSTED" and company_continuity == "MATCH" and bool(scope_compatible)


def observe_runtime_job_source(page, provider: str = "UNKNOWN", capability: str = "ENCRYPTED_BROWSER_API", expected_total: int | None = None, request_limit: int | None = None, request_offset: int | None = None, observed_requests: Any = None, trigger: Any = None, scan_js: str = STATE_SCAN_JS) -> RuntimeJobSource:
    try:
        scan = page.evaluate(scan_js)
    except Exception:
        scan = {}
    return build_runtime_job_source(scan, provider=provider, capability=capability, expected_total=expected_total, request_limit=request_limit, request_offset=request_offset, observed_requests=observed_requests, trigger=trigger)


def build_runtime_source_candidate(url: str, provider: str = "UNKNOWN", capability: str = "ENCRYPTED_BROWSER_API", expected_total: int | None = None, request_limit: int | None = None, request_offset: int | None = None, browser_factory=None, wait_ms: int = 9000) -> RuntimeJobSource:
    """Open the official terminal and observe decrypted runtime job data (non-collecting)."""
    if browser_factory is None:
        from job_extractor.browser import BrowserRuntime as browser_factory  # type: ignore
    try:
        with browser_factory() as runtime:
            page = runtime.page
            page.goto(url, wait_until="domcontentloaded")
            remaining = wait_ms
            while remaining > 0:
                step = 500 if remaining >= 500 else remaining
                page.wait_for_timeout(step)
                remaining -= step
            source = observe_runtime_job_source(page, provider=provider, capability=capability, expected_total=expected_total, request_limit=request_limit, request_offset=request_offset)
            # A runtime list can contain only identity/title fields while the
            # same already-rendered list cards contain JD sections.  Bind only
            # cards with a stable id (or an unambiguous title fallback).
            from job_extractor.discovery.rendered_list_jd import observe_rendered_list_jd
            mapped, mapping = observe_rendered_list_jd(page, list(source.records), id_field=source.job_id_field, title_field=source.job_title_field)
            source.records = mapped
            if mapping["mapping_success"]:
                source.jd_fields = sorted(set(source.jd_fields + ["_generic_description", "_generic_responsibilities", "_generic_requirements"]))
            source.evidence.append("rendered-list JD mapping: " + ", ".join(f"{key}={value}" for key, value in mapping.items()))
            if source.confidence == "LOW":
                page.add_init_script(TRANSFORM_CAPTURE_SCRIPT)
                page.goto(url, wait_until="domcontentloaded")
                remaining = wait_ms
                while remaining > 0:
                    step = 500 if remaining >= 500 else remaining
                    page.wait_for_timeout(step)
                    remaining -= step
                transform = observe_runtime_job_source(page, provider=provider, capability=capability, expected_total=expected_total, request_limit=request_limit, request_offset=request_offset, scan_js=TRANSFORM_SCAN_JS)
                if transform.confidence != "LOW":
                    source = transform
            return source
    except Exception:
        return RuntimeJobSource(provider=provider, capability=capability, mechanism="OTHER", confidence="LOW", executable=False, evidence=["browser runtime observation failed"])


PAGINATION_TRIGGER_JS = r"""
() => {
  const hasContainer = !!document.querySelector('[class*="pagination" i],[class*="Pagination"]');
  const forward = !!(document.querySelector('[class*="forward"]') || document.querySelector('[class*="next"]') || document.querySelector('[aria-label*="next" i]') || document.querySelector('[aria-label*="下一页"]'));
  const numeric = [...document.querySelectorAll('button,a,li')].filter(n=>/^\d{1,4}$/.test((n.innerText||'').trim())).length;
  let trigger_mode = 'UNKNOWN';
  if(numeric>=2) trigger_mode = 'OFFSET_PAGE';
  else if(forward) trigger_mode = 'OFFSET_BUTTON';
  return {has_pagination: !!hasContainer||forward||numeric>=2, trigger_mode, forward, numeric_buttons:numeric};
}
"""


def detect_pagination_trigger(page) -> dict[str, Any]:
    try:
        return page.evaluate(PAGINATION_TRIGGER_JS) or {}
    except Exception:
        return {}


RUNTIME_PAGINATION_SCRIPT = r"""
(() => {
  if (window.__rtP) return;
  window.__rtP = {requests: [], batches: [], totals: [], lastOffset: null};
  const idRe = /^(id|job_?id|jobid|uuid)$/i;
  const titleRe = /^(title|job_?title|jobtitle|position_?name|name)$/i;
  function find(root, path, depth, out){
    if(depth>5||out.length>=8||root===null)return;
    if(Array.isArray(root)){
      const objs=root.slice(0,8).filter(x=>x&&typeof x==='object'&&!Array.isArray(x));
      if(root.length>=2&&objs.length>=2){const k=new Set(objs.flatMap(o=>Object.keys(o)));if([...k].some(x=>idRe.test(x))&&[...k].some(x=>titleRe.test(x)))out.push({path,count:root.length,sample:root.slice(0,300)});}
      return;
    }
    if(typeof root!=='object')return;
    let keys=[];try{keys=Object.keys(root);}catch(e){return;}
    for(const k of keys.slice(0,100)){let c;try{c=root[k];}catch(e){continue;}find(c,path?path+'.'+k:k,depth+1,out);}
  }
  function findTotals(root,depth,out){
    if(depth>3||out.length>=20||root===null||typeof root!=='object'||Array.isArray(root))return;
    const scalars={};
    for(const k of Object.keys(root)){const v=root[k];if((typeof v==='number'||typeof v==='boolean')&&/^(total|count|totalcount|hasmore|has_more|offset|limit|page|pages|size|pagesize)$/i.test(k))scalars[k]=v;}
    if(Object.keys(scalars).length)out.push({scalars});
    for(const k of Object.keys(root).slice(0,60)){let c;try{c=root[k];}catch(e){continue;}if(c&&typeof c==='object')findTotals(c,depth+1,out);}
  }
  const op=JSON.parse;
  JSON.parse=function(t,r){
    let v;try{v=op.call(this,t,r);}catch(e){throw e;}
    try{
      const out=[];find(v,'',0,out);
      if(out.length)window.__rtP.batches.push({offset:window.__rtP.lastOffset,textLength:typeof t==='string'?t.length:null,arrays:out});
      const to=[];findTotals(v,0,to);
      if(to.length)window.__rtP.totals.push({offset:window.__rtP.lastOffset,entries:to});
    }catch(e){}
    return v;
  };
  function record(method,url,body){
    let b=null;try{b=typeof body==='string'?JSON.parse(body):body;}catch(e){}
    if(b&&typeof b==='object'&&('offset' in b)&&('limit' in b)){window.__rtP.requests.push({method,url:String(url||'').split('?')[0],offset:b.offset,limit:b.limit});window.__rtP.lastOffset=b.offset;}
  }
  const xo=XMLHttpRequest.prototype.open,xs=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(m,u){this.__u=u;this.__m=m;return xo.apply(this,arguments);};
  XMLHttpRequest.prototype.send=function(b){try{record(this.__m,this.__u,b);}catch(e){}return xs.apply(this,arguments);};
  const of=window.fetch;
  window.fetch=function(r,o){try{record((o&&o.method)||'GET',typeof r==='string'?r:(r&&r.url)||'',o&&o.body);}catch(e){}return of.apply(this,arguments);};
})();
"""

RUNTIME_PAGINATION_SCAN_JS = r"""
() => {
  const c = window.__rtP || {requests:[],batches:[],totals:[]};
  return {requests: c.requests || [], batches: c.batches || [], totals: c.totals || []};
}
"""

CLICK_NEXT_PAGINATION_JS = r"""
(n) => {
  const nodes = [...document.querySelectorAll('button,a,li')];
  const text = x => (x.innerText || '').trim();
  const containers = [...document.querySelectorAll('[class*="pagination" i],[class*="pag-" i],[role="navigation"]')];
  let target = null;
  for(const selector of ['button','a','li']){
    if(target) break;
    for(const container of containers){
      const hit = [...container.querySelectorAll(selector)].find(x => /^\d{1,4}$/.test(text(x)) && Number(text(x)) === n);
      if(hit){ target = hit; break; }
    }
  }
  if(!target){ target = nodes.find(x => x.tagName === 'BUTTON' && /^\d{1,4}$/.test(text(x)) && Number(text(x)) === n); }
  if(target){ target.click(); return {ok:true, via:'PAGE_NUMBER', page:n}; }
  const fwd = document.querySelector('[class*="forward"]') || document.querySelector('[class*="next"]') || document.querySelector('[aria-label*="next" i]') || document.querySelector('[aria-label*="下一页"]');
  if(fwd && !fwd.disabled && fwd.getAttribute('aria-disabled') !== 'true'){ fwd.click(); return {ok:true, via:'FORWARD'}; }
  return {ok:false};
}
"""

# ---------------------------------------------------------------------------
# STEP 54B: organic detail-request evidence.
#
# When a runtime site keeps its list in decrypted state but fetches the JD
# body through a separate per-job request, discovery must observe that
# request organically: navigate one job route (a real user action) and
# capture the request the SPA itself issues, plus the response shape and
# the runtime state values the request body drew from. Everything recorded
# here is generic evidence (URL, method, body key/value shapes, decoder
# hints, JD field name, identity fields); no host, provider or company
# vocabulary is involved. Secret-like state values are excluded.
# ---------------------------------------------------------------------------
RUNTIME_DETAIL_OBSERVE_SCRIPT = r"""
(() => {
  if (window.__rtDetail) return;
  window.__rtDetail = {requests: [], parsed: []};
  const secretRe = /(aes[_-]?iv|necromancer|encrypt|decrypt|cipher|passwd|password|token|secret|session|csrf|signature|cookie|auth|api[_-]?key|access[_-]?key)/i;
  const idRe = /^(jobid|job_id|id)$/i;
  function bodyShape(body){
    if(!body||typeof body!=='object')return null;
    const shape={};
    for(const k of Object.keys(body)){
      const v=body[k];
      const t=v===null?'null':Array.isArray(v)?'array':typeof v;
      shape[k]=t;
    }
    return shape;
  }
  function record(method,url,body){
    try{
      if(!body||typeof body!=='object')return;
      // A detail request carries a per-job identifier in its body.
      const keys=Object.keys(body);
      if(!keys.some(k=>idRe.test(k)))return;
      const clean={};
      for(const k of keys){ if(!secretRe.test(k))clean[k]=body[k]; }
      // Relative URLs are resolved so the contract endpoint is directly
      // requestable by the collector; query strings carry no body evidence.
      const abs=(function(){try{return new URL(url,location.href).toString().split('?')[0];}catch(e){return String(url||'').split('?')[0];}})();
      window.__rtDetail.requests.push({method:String(method||'GET').toUpperCase(),url:abs,body:clean,shape:bodyShape(body)});
    }catch(e){}
  }
  const op=JSON.parse;
  JSON.parse=function(t,r){
    let v;try{v=op.call(this,t,r);}catch(e){throw e;}
    try{
      if(v&&typeof v==='object'){
        const enc=('necromancer' in v)&&('data' in v);
        // Plain-JSON detail bodies: {data:{...jobDescription...}} or a bare
        // detail object carrying the JD field. The path where the record
        // (JD + id together) actually lived is recorded as evidence so the
        // contract can express a business-result wrapper without guessing.
        let data=null,resultPath=[];
        if(!enc&&v.data&&typeof v.data==='object'){data=v.data;resultPath=[Object.keys(v).find(k=>v[k]===data)||'data'];}
        else if(!enc&&('jobDescription' in v)){data=v;resultPath=[];}
        if(data&&('jobDescription' in data)&&('id' in data)){
          window.__rtDetail.parsed.push({encEnvelope:false,jdField:'jobDescription',jdLen:String(data.jobDescription||'').length,idFields:['id'],titleFields:['title'],resultPath:resultPath});
        }
      }
    }catch(e){}
    return v;
  };
  const xo=XMLHttpRequest.prototype.open,xs=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(m,u){this.__u=u;this.__m=m;return xo.apply(this,arguments);};
  XMLHttpRequest.prototype.send=function(b){
    try{
      let body=null;try{body=typeof b==='string'?JSON.parse(b):b;}catch(e){}
      record(this.__m,this.__u,body);
      this.addEventListener('load',function(){
        try{
          const u=(function(){try{return new URL(String(this.__u||''),location.href).toString().split('?')[0];}catch(e){return String(this.__u||'').split('?')[0];}}).call(this);
          const req=window.__rtDetail.requests.find(x=>x.url===u);
          if(!req)return;
          const p=JSON.parse(this.responseText);
          if(p&&typeof p==='object'){
            req.responseEnvelope=['necromancer' in p,('data' in p)];
            req.responseTextLength=String(this.responseText||'').length;
          }
        }catch(e){}
      }, {once:true});
    }catch(e){}
    return xs.apply(this,arguments);
  };
  const of=window.fetch;
  window.fetch=function(r,o){
    try{record((o&&o.method)||(r&&r.method)||'GET',typeof r==='string'?r:(r&&r.url)||'',o&&o.body);}catch(e){}
    return of.apply(this,arguments);
  };
})();
"""

RUNTIME_DETAIL_SCAN_JS = r"""
() => {
  const c = window.__rtDetail || {requests:[],parsed:[]};
  const state = {};
  try{
    // Runtime state values that plausibly scope the detail request (org,
    // site, mode/locale). Secret-like keys are never captured. The scan
    // walks a few object levels so values nested inside application state
    // containers remain reachable — organic scope evidence lives at
    // varying depth per site.
    const secretRe = /(necromancer|encrypt|decrypt|cipher|passwd|password|token|secret|session|csrf|signature|cookie|auth|api[_-]?key|access[_-]?key)/i;
    const scopeRe = /(orgid|org_id|siteid|site_id|websiteid|website_id|locale|lang|language|mode|recruitmenttype|recruitment_type|channel|portalid|portal_id)/i;
    const ivRe = /^(aes[_-]?iv)$/i;
    const roots=[window];
    for(const k of Object.keys(window)){
      if(secretRe.test(k))continue;
      let v;try{v=window[k];}catch(e){continue;}
      if(v&&typeof v==='object'&&!Array.isArray(v)&&!(v instanceof Node)&&v!==window)roots.push(v);
    }
    const visit=(root,depth)=>{
      let keys=[];try{keys=Object.keys(root);}catch(e){return;}
      for(const k of keys){
        if(secretRe.test(k))continue;
        let v;try{v=root[k];}catch(e){continue;}
        const isIv=ivRe.test(k);
        const isScope=scopeRe.test(k);
        if((isScope||isIv)&&(typeof v==='string'||typeof v==='number')){
          // Store under the real runtime-state key name: the contract binds
          // scope/IV values to their observed provenance key.
          if(!(k in state))state[k]=v;
        }
        // Walk deeper only when the container itself is a state container
        // (named like a scope key) or we are still near the top: bounded
        // depth keeps the scan O(small) on large pages.
        else if(v&&typeof v==='object'&&!Array.isArray(v)&&depth<3&&(isScope||depth===0)){
          visit(v,depth+1);
        }
      }
    };
    for(const root of roots){
      visit(root,0);
      // Keep going until the IV marker is present: an encrypted-envelope
      // contract fails closed without it, so stopping early only guarantees
      // a useless scan. Scope values alone are not enough.
      if(state.__iv && Object.keys(state).length>=6)break;
    }
  }catch(e){}
  return {requests:c.requests||[],parsed:c.parsed||[],state};
}
"""

# Generic JD-body field names a detail response may carry (shared vocabulary
# with field_semantics, kept here as the hook-side whitelist is JS).
_DETAIL_JD_FIELD = re.compile(r"(?i)^(job_?desc(ription)?|description|content|jd|jd_?content|detail_?desc(ription)?|post_?content|work_?content|rich_?text)$")
# Body keys that carry the per-job locator (the "{id}" substitution target).
_DETAIL_ID_BODY_KEY = re.compile(r"(?i)^(jobid|job_id|id|positionid|position_id|postingid|posting_id|requisitionid|requisition_id)$")
# Body keys that must come from observed evidence, never invented.
_DETAIL_SCOPE_KEY = re.compile(r"(?i)^(orgid|org_id|siteid|site_id|websiteid|website_id|locale|lang|language|mode|recruitmenttype|recruitment_type|channel|portalid|portal_id)$")
# Envelope markers for the generic response-transform decoder contract.
_DETAIL_ENVELOPE_KEYS = ("necromancer",)


def _runtime_state_value(state: dict[str, Any], key: str) -> Any:
    """Resolve a detail body value from observed runtime state (case-insensitive)."""
    lowered = {str(k).lower().replace("__iv", "aesiv"): v for k, v in (state or {}).items()}
    return lowered.get(str(key).lower())


def _scope_observed(value: Any, observed: Any) -> bool:
    """Whether an observed runtime-state value matches a request body value.

    Equal after normalizing shape only: separator style (``zh_CN``/``zh-CN``),
    and string-vs-number form (``"4362"``/``4362``). Any deeper coercion would
    risk binding invented values, so a real mismatch still fails closed.
    """
    if observed is None:
        return False
    if str(observed) == str(value):
        return True
    norm = lambda v: re.sub(r"[-_]", "", str(v)).lower()  # noqa: E731
    try:
        return norm(observed) == norm(value) or float(observed) == float(value)
    except (TypeError, ValueError):
        return norm(observed) == norm(value)


def detail_contract_from_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """Turn organic detail-request evidence into a generic detail contract.

    Trust rules (all evidence-derived, nothing site-specific):
      - the request must have been observed with a per-job id body key;
      - every scope body value must be resolvable from the observed runtime
        state snapshot captured at the same moment;
      - the id body key becomes the ``{id}`` substitution target and every
        other key/value pair is kept verbatim in ``body_template``;
      - the decoder contract is recorded when the observed response used a
        generic envelope transform (``data`` payload + key marker), with the
        IV source pointed at the runtime-state key captured by discovery.

    Returns ``{}`` when the observation lacks a trustworthy contract so
    callers fail safe instead of fabricating detail requests.
    """
    requests = [x for x in (observation.get("requests") or []) if isinstance(x, dict)]
    if not requests:
        return {}
    parsed = [x for x in (observation.get("parsed") or []) if isinstance(x, dict)]
    state = observation.get("state") if isinstance(observation.get("state"), dict) else {}
    request = requests[0]
    body = request.get("body") if isinstance(request.get("body"), dict) else {}
    id_key = next((key for key in body if _DETAIL_ID_BODY_KEY.match(str(key))), None)
    if not id_key:
        return {}
    url = str(request.get("url") or "").strip()
    method = str(request.get("method") or "").upper()
    if not url or method not in ("GET", "POST"):
        return {}
    body_template: dict[str, Any] = {}
    for key, value in body.items():
        if key == id_key:
            continue
        if _DETAIL_SCOPE_KEY.match(str(key)):
            observed = _runtime_state_value(state, key)
            if not _scope_observed(value, observed):
                return {}
            body_template[key] = observed
        else:
            body_template[key] = value
    envelope = request.get("responseEnvelope")
    encrypted = isinstance(envelope, list) and len(envelope) == 2 and bool(envelope[0]) and bool(envelope[1])
    endpoint = str(url).strip()
    if endpoint.startswith("/"):
        # Relative endpoint: bind it to the observed page origin so the
        # collector can request it directly. The origin is organic evidence
        # from the same observation, not an invented host.
        origin = str((observation.get("pageOrigin") or "")).strip().rstrip("/")
        if not origin:
            return {}
        endpoint = f"{origin}{endpoint}"
    contract: dict[str, Any] = {
        "endpoint": endpoint,
        "method": method,
        "id_field": str(id_key),
        "body_template": body_template,
        "jd_field": "jobDescription",
    }
    jd_observed = next((entry for entry in parsed if _DETAIL_JD_FIELD.match(str(entry.get("jdField") or ""))), None)
    if jd_observed and isinstance(jd_observed.get("jdField"), str):
        contract["jd_field"] = jd_observed["jdField"]
        if isinstance(jd_observed.get("idFields"), list) and jd_observed["idFields"]:
            contract["detail_identity_id_field"] = jd_observed["idFields"][0]
        if isinstance(jd_observed.get("titleFields"), list) and jd_observed["titleFields"]:
            contract["detail_identity_title_field"] = jd_observed["titleFields"][0]
    # STEP 54B: where the job record lives inside the decoded response. This
    # comes straight from the organic observation (the hook records the path
    # that actually carried the JD + id together). No observed path evidence
    # → no result path is recorded and the contract stays wrapper-free; the
    # execution layer then treats the decoded payload as the record.
    jd_name = str(contract.get("jd_field") or "jobDescription")
    observed_path = jd_observed.get("resultPath") if isinstance(jd_observed, dict) else None
    if isinstance(observed_path, list) and observed_path and all(isinstance(p, str) for p in observed_path):
        contract["result_path"] = [str(p) for p in observed_path]
    elif jd_observed is None and encrypted:
        # The observe hook only parses plaintext detail responses, so an
        # encrypted observation carries no business-record path evidence. The
        # envelope decoder spec (recorded below) carries ``payload_field`` as
        # the only evidence naming where the job payload lives; the business
        # record is then resolved under that key. A wrong derivation fails
        # closed in ``resolve_detail_payload`` (PATH_MISSING) — the execution
        # layer never fabricates a merge from a guessed location.
        contract["result_path"] = ["data"]
    if encrypted:
        # The IV lives in the decrypted runtime state; it is captured by the
        # state scan at the same moment (secret-like keys are excluded from
        # persistence — records never carry it). The contract carries the
        # observed value under the decoder spec plus the provenance pointer;
        # the per-response envelope key never is persisted (it comes with
        # every response).
        iv_source_key = next((str(k) for k in state if str(k).lower().replace("_", "") == "aesiv"), None)
        if not iv_source_key:
            return {}
        contract["decoder"] = {
            # Evidence only: field names + IV provenance. The algorithm mode
            # is resolved by the collector-side decoder from this shape (the
            # discovery layer carries no transform vocabulary).
            "envelope_field": _DETAIL_ENVELOPE_KEYS[0],
            "payload_field": "data",
            "iv": str(state[iv_source_key]),
            "iv_source": f"runtime_state.{iv_source_key}",
        }
    elif request.get("responseEnvelope") is not None:
        contract["decoder"] = {"mode": "PLAIN_JSON"}
    contract["evidence"] = ["runtime detail request observed organically after navigating one job route"]
    return contract


def observe_runtime_detail_contract(page, sample_job_id: str | None = None, wait_ms: int = 6000) -> dict[str, Any]:
    """Navigate one job route and capture the SPA's own detail request.

    The page issues the request itself; this only observes it. The runtime
    state snapshot is taken at the same moment so every scope value in the
    body can be bound to evidence. Returns ``{}`` when no detail request was
    observed (callers must then leave jobs incomplete — never fabricate).
    """
    try:
        base_url = page.url
        if not base_url:
            return {}
        page.add_init_script(RUNTIME_DETAIL_OBSERVE_SCRIPT)
        target = base_url
        if sample_job_id:
            if "#" in base_url:
                # SPA route style (…/jobs → …/job/<id>). Hash-only changes
                # are same-document navigations: init scripts would never be
                # (re-)injected, so force a full document load of the same
                # origin by going through the path before the hash first.
                root = base_url.split("#")[0]
                page.goto(root, wait_until="domcontentloaded")
                page.evaluate(
                    """(id) => { const h = new URL(location.href); h.hash = '#/job/' + id; location.hash = h.hash; }""",
                    sample_job_id,
                )
                target = f"{root}#/job/{sample_job_id}"
            elif "{id}" not in base_url and not re.search(r"/job/[^/#?]+", base_url):
                target = re.sub(r"/jobs(\?[^#]*)?#", f"/job/{sample_job_id}\\1#", base_url, count=1)
                if target == base_url:
                    target = f"{base_url.rstrip('/')}/job/{sample_job_id}"
        page.wait_for_load_state("domcontentloaded")
        remaining = wait_ms
        while remaining > 0:
            step = 500 if remaining >= 500 else remaining
            page.wait_for_timeout(step)
            remaining -= step
        scan = page.evaluate(RUNTIME_DETAIL_SCAN_JS)
        if isinstance(scan, dict):
            try:
                scan["pageOrigin"] = urlsplit(page.url)._replace(scheme="http", path="", query="", fragment="").geturl() if page.url.startswith("https") else urlsplit(page.url)._replace(path="", query="", fragment="").geturl()
            except Exception:
                scan.pop("pageOrigin", None)
        return detail_contract_from_observation(scan if isinstance(scan, dict) else {})
    except Exception:
        return {}

def _read_capture(page) -> dict[str, Any]:
    try:
        value = page.evaluate(RUNTIME_PAGINATION_SCAN_JS)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _select_batch(capture: dict[str, Any], offset: int | None, allow_untagged: bool = False) -> dict[str, Any] | None:
    candidates: list[tuple[dict[str, Any], Any]] = []
    for entry in capture.get("batches") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("offset") == offset or (allow_untagged and entry.get("offset") is None):
            for array in entry.get("arrays") or []:
                if isinstance(array, dict):
                    candidates.append((array, entry.get("offset")))
    if not candidates:
        return None
    array, tagged = max(candidates, key=lambda item: int(item[0].get("count") or 0))
    return {"offset": tagged, "count": int(array.get("count") or 0), "sample": array.get("sample") or [], "path": array.get("path")}


def _wait_for_batch(page, offset: int | None, wait_ms: int, allow_untagged: bool = False) -> dict[str, Any] | None:
    elapsed = 0
    while elapsed < wait_ms:
        batch = _select_batch(_read_capture(page), offset, allow_untagged)
        if batch is not None and batch.get("count"):
            return batch
        page.wait_for_timeout(300)
        elapsed += 300
    return None


def _request_observed(capture: dict[str, Any], offset: int, limit: int | None) -> bool:
    return any(isinstance(request, dict) and request.get("offset") == offset and
               (limit is None or request.get("limit") == limit)
               for request in capture.get("requests") or [])


def _credible_plaintext_batch(batch: dict[str, Any] | None, source: RuntimeJobSource, offset: int) -> bool:
    if not batch or not batch.get("count"):
        return False
    count = int(batch.get("count") or 0)
    return not source.limit or count == source.limit or bool(source.total is not None and offset + count >= source.total)


def _wait_for_page_batch(page, source: RuntimeJobSource, offset: int, wait_ms: int,
                         previous_dom_hash: str | None = None, allow_untagged: bool = False) -> tuple[dict[str, Any] | None, str]:
    """Prefer a plaintext batch, otherwise bind a stable DOM batch to its observed request."""
    from job_extractor.discovery.rendered_list_jd import read_rendered_list_batch

    elapsed = 0; stable = 0; prior_hash = None; request_seen = False; unchanged_seen = False
    while elapsed < wait_ms:
        capture = _read_capture(page)
        request_seen = request_seen or _request_observed(capture, offset, source.limit)
        plaintext = _select_batch(capture, offset, allow_untagged=allow_untagged)
        # A plaintext hook batch explicitly tagged with this offset is already
        # attributed at the runtime boundary.  DOM fallback, in contrast,
        # always requires the independently observed request below.
        if _credible_plaintext_batch(plaintext, source, offset):
            plain_ids = [str(record.get(source.job_id_field)) for record in plaintext.get("sample") or []
                         if isinstance(record, dict) and source.job_id_field and record.get(source.job_id_field) not in (None, "")]
            import hashlib
            plaintext["ordered_ids_hash"] = hashlib.sha256("\x1f".join(plain_ids).encode("utf-8")).hexdigest()
            plaintext["set_ids_hash"] = hashlib.sha256("\x1f".join(sorted(set(plain_ids))).encode("utf-8")).hexdigest()
            plaintext.update({"provenance": "BROWSER_RUNTIME_PLAINTEXT", "status": "ACCEPTED",
                              "raw_dom_card_count": 0, "unique_identity_count": plaintext.get("count", 0),
                              "ambiguous_records": 0})
            return plaintext, "PLAINTEXT_BATCH"
        if request_seen and source.job_id_field and source.job_title_field:
            records, meta = read_rendered_list_batch(page, id_field=source.job_id_field, title_field=source.job_title_field)
            signature = meta.get("ordered_ids_hash")
            stable = stable + 1 if signature and signature == prior_hash else 0
            prior_hash = signature
            changed = previous_dom_hash is None or signature != previous_dom_hash
            unchanged_seen = unchanged_seen or bool(signature and not changed)
            if len(records) >= 2 and changed and stable >= 2:
                return {"offset": offset, "count": len(records), "sample": records,
                        "path": "rendered-list", "provenance": "BROWSER_RENDERED_LIST",
                        "status": "ACCEPTED", **meta}, "DOM_BATCH_FALLBACK"
        page.wait_for_timeout(250)
        elapsed += 250
    if request_seen and unchanged_seen:
        return None, "PAGINATION_STAGNANT"
    if not request_seen:
        return None, "PAGINATION_REQUEST_NOT_OBSERVED"
    return None, "PAGINATION_BATCH_MISSING"


def _cumulative_ids(source: RuntimeJobSource, batches: list[dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    for batch in batches:
        for record in batch.get("sample") or []:
            if isinstance(record, dict) and source.job_id_field and record.get(source.job_id_field) not in (None, ""):
                seen.add(str(record.get(source.job_id_field)))
    return seen


def run_runtime_pagination(page, source: RuntimeJobSource, wait_ms: int = 9000, max_pages: int = 80,
                           deadline_seconds: float = 360.0) -> list[dict[str, Any]]:
    """Drive official controls; bind plaintext/DOM batches to observed request offsets."""
    started = perf_counter()
    offset = source.initial_offset if source.initial_offset is not None else 0
    limit = source.limit
    batches: list[dict[str, Any]] = []
    batch, reason = _wait_for_page_batch(page, source, offset, wait_ms, allow_untagged=True)
    previous_dom_hash: str | None = None
    while batch is not None and len(batches) < max_pages:
        batches.append(batch)
        if batch.get("provenance") == "BROWSER_RENDERED_LIST":
            previous_dom_hash = batch.get("ordered_ids_hash")
        if source.total and len(_cumulative_ids(source, batches)) >= source.total:
            batch["termination_reason"] = "TOTAL_REACHED"
            break
        if not limit:
            batch["termination_reason"] = "PAGINATION_LIMIT_MISSING"
            break
        if batch.get("count") and batch["count"] < limit:
            batch["termination_reason"] = "SHORT_PAGE"
            break
        if perf_counter() - started >= deadline_seconds:
            batches.append({"offset": offset + limit, "count": 0, "sample": [], "status": "BUDGET_EXCEEDED",
                            "provenance": "NONE", "termination_reason": "BUDGET_EXCEEDED"})
            break
        next_offset = offset + limit
        page_number = next_offset // limit + 1
        clicked = page.evaluate(CLICK_NEXT_PAGINATION_JS, page_number)
        if not isinstance(clicked, dict) or not clicked.get("ok"):
            batches.append({"offset": next_offset, "count": 0, "sample": [], "status": "PAGE_TRIGGER_FAILED",
                            "provenance": "NONE", "termination_reason": "PAGE_TRIGGER_FAILED"})
            break
        batch, reason = _wait_for_page_batch(page, source, next_offset, wait_ms, previous_dom_hash=previous_dom_hash)
        if batch is None:
            batches.append({"offset": next_offset, "count": 0, "sample": [], "status": reason,
                            "provenance": "NONE", "termination_reason": reason})
            break
        offset = next_offset
    if not batches:
        batches.append({"offset": offset, "count": 0, "sample": [], "status": reason,
                        "provenance": "NONE", "termination_reason": reason})
    elif len(batches) >= max_pages and not batches[-1].get("termination_reason"):
        # A caller may deliberately request a staged audit (3/5 batches).
        # This is not a collection-budget breach; full collection has its own
        # much higher hard page/deadline guard above.
        batches[-1]["termination_reason"] = "STAGE_LIMIT_REACHED"
    return batches


def resolve_encrypted_runtime_terminal(root, terminal_discover, registry, max_candidates: int = 6, deadline_seconds: float | None = 60.0):
    """Resolve a trusted downstream custom-domain encrypted provider terminal from a portal result."""
    from time import perf_counter
    from job_extractor.discovery.handoff import _company_transition, source_intent
    from job_extractor.discovery.provider_fingerprint import fingerprint_terminal

    started = perf_counter()
    from job_extractor.discovery.budget import terminal_activation_budget
    preferred = ("SOCIAL", "CAMPUS", "INTERN", "ALL_JOBS", "OVERSEAS", "OTHER")
    entries = sorted(root.recruitment_entries, key=lambda entry: preferred.index(entry.entry_type) if entry.entry_type in preferred else len(preferred))
    intent = source_intent(root)
    for entry in entries[:max_candidates]:
        if deadline_seconds is not None and perf_counter() - started > deadline_seconds:
            break
        if registry.detect_known(entry.url):
            continue
        trusted, _evidence, _continuity = _company_transition(root.source_url, entry.url, root.company)
        if not trusted:
            continue
        try:
            remaining = None if deadline_seconds is None else deadline_seconds - (perf_counter() - started)
            if remaining is not None and remaining <= 0:
                break
            # Each probe receives an explicit bounded budget; detector phases
            # consume that shared deadline rather than silently using their
            # independent default budget.
            discovery = terminal_discover(entry.url, budget=terminal_activation_budget(min(25.0, remaining) if remaining is not None else 25.0))
        except Exception:
            continue
        if discovery is None:
            continue
        fingerprint = fingerprint_terminal(discovery, discovery.resolved_url or entry.url)
        if fingerprint.confidence != "HIGH" or "ENCRYPTED_BROWSER_API" not in fingerprint.capabilities:
            continue
        discovery.provider_fingerprint = fingerprint
        observed = str(discovery.detected_scope.get("recruitment_type", "")).upper()
        scope = entry.entry_type if entry.entry_type in ("CAMPUS", "SOCIAL", "INTERN", "ALL_JOBS") else (observed or "ALL_JOBS")
        if intent != "UNKNOWN" and scope not in (intent, "ALL_JOBS") and observed not in (intent, intent.lower(), ""):
            continue
        entry.trust_status = "TRUSTED"; entry.adapter_platform = fingerprint.provider; entry.destination_classification = "UNKNOWN_ATS"
        return entry, discovery, fingerprint
    return None


def merge_runtime_batches(source: RuntimeJobSource, batches: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    cumulative: set[str] = set()
    id_field = source.job_id_field
    for index, batch in enumerate(batches or []):
        if not isinstance(batch, dict):
            continue
        sample = batch.get("sample") if isinstance(batch.get("sample"), list) else []
        cleaned = [sanitize_record(record) for record in sample if isinstance(record, dict)]
        ids = {str(record.get(id_field)) for record in cleaned if id_field and record.get(id_field) not in (None, "")}
        new_ids = ids - cumulative
        cumulative |= ids
        audit.append({"batch": index + 1, "offset": batch.get("offset"), "requested_limit": source.limit,
                      "plaintext_count": len(cleaned), "raw_dom_card_count": batch.get("raw_dom_card_count", 0),
                      "unique_ids": len(ids), "new_unique_ids": len(new_ids), "cumulative_unique": len(cumulative),
                      "cross_batch_duplicates": len(ids) - len(new_ids), "ordered_ids_hash": batch.get("ordered_ids_hash"),
                      "set_ids_hash": batch.get("set_ids_hash"), "ambiguous_records": int(batch.get("ambiguous_records") or 0),
                      "provenance": batch.get("provenance", "BROWSER_RUNTIME_PLAINTEXT"),
                      "status": batch.get("status", "ACCEPTED"), "termination_reason": batch.get("termination_reason")})
        records.extend(cleaned)
    failed = [entry for entry in audit if entry["status"] != "ACCEPTED"]
    stagnant = [entry for entry in audit if entry["status"] == "PAGINATION_STAGNANT"]
    ambiguous = sum(entry["ambiguous_records"] for entry in audit)
    accepted = [entry for entry in audit if entry["status"] == "ACCEPTED"]
    termination = next((entry["termination_reason"] for entry in reversed(audit) if entry.get("termination_reason")), None)
    return records, {"batches": audit, "unique_ids": len(cumulative), "total": source.total, "limit": source.limit,
                     "pages_requested": len(audit), "pages_succeeded": len(accepted), "failed_pages": len(failed),
                     "stagnant_pages": len(stagnant), "ambiguous_records": ambiguous,
                     "cross_page_duplicates": sum(entry["cross_batch_duplicates"] for entry in audit),
                     "termination_reason": termination}
