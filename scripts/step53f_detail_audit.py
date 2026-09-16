"""Read-only list/detail lineage probe for STEP 53F target pages."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.network_analyzer import list_observation, safe_business_data, safe_url

SITES = {
    "catl": "https://app.mokahr.com/campus-recruitment/catlhr/148948#/jobs",
    "geely": "https://careers.geelytech.com/campus",
}
OUT = Path("artifacts/step53f_detail_lineage")
JOB = re.compile(r"job|position|post|recruit|career|职位|岗位|招聘", re.I)
JD_KEYS = re.compile(r"description|responsib|requirement|qualification|jobdescription|positiondescription|岗位职责|任职要求", re.I)


def payload_shape(value, depth=0):
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {"type": "object", "keys": list(value)[:40], "children": {str(k): payload_shape(v, depth + 1) for k, v in list(value.items())[:12]}}
    if isinstance(value, list):
        return {"type": "array", "length": len(value), "item": payload_shape(value[0], depth + 1) if value else None}
    return type(value).__name__


def jd_paths(value, path="$", depth=0):
    if depth >= 7:
        return []
    if isinstance(value, dict):
        found = [f"{path}.{key}" for key in value if JD_KEYS.search(str(key))]
        return found + [item for key, child in list(value.items())[:80] for item in jd_paths(child, f"{path}.{key}", depth + 1)]
    if isinstance(value, list):
        return jd_paths(value[0], f"{path}[0]", depth + 1) if value else []
    return []


def inspect(key: str) -> None:
    url = SITES[key]
    events: list[dict] = []
    with BrowserRuntime(timeout_ms=20000) as runtime:
        page = runtime.page
        def response(res):
            req = res.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            entry = {"method": req.method, "url": safe_url(req.url)[0], "status": res.status,
                     "content_type": res.headers.get("content-type", ""), "post_data": (req.post_data or "")[:500] or None}
            try:
                payload = res.json()
            except Exception:
                events.append(entry | {"raw_kind": "NON_JSON"})
                return
            path, count, sample = list_observation(payload)
            keys = list(sample[0]) if sample and isinstance(sample[0], dict) else []
            entry.update({"raw_kind": "JSON", "list_path": path, "list_count": count,
                          "sample_keys": keys, "jd_keys": [x for x in keys if JD_KEYS.search(x)],
                          "jd_paths": jd_paths(payload)[:80], "payload_shape": payload_shape(payload),
                          "sample": safe_business_data(sample[:2])})
            events.append(entry)
        page.on("response", response)
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(9000)
        body = page.locator("body").inner_text()[:30000]
        links = page.evaluate("""() => Array.from(document.querySelectorAll('a,[data-href],[data-route]')).slice(0,500).map(n=>({text:(n.innerText||'').trim().slice(0,180),href:n.getAttribute('href')||n.getAttribute('data-href')||n.getAttribute('data-route')||''})).filter(x=>x.text||x.href)""")
        state = page.evaluate("""() => ({url:location.href, title:document.title, scripts:Array.from(document.scripts).filter(s=>s.type==='application/json').map(s=>({id:s.id,type:s.type,text:s.textContent.slice(0,1000)})).slice(0,10)})""")
    report = {"site": key, "url": url, "final_url": state["url"], "title": state["title"],
              "events": events, "job_events": [x for x in events if JOB.search(x["url"])],
              "body_job_lines": [x for x in body.splitlines() if JOB.search(x)][:100],
              "links": [x for x in links if JOB.search(x["text"] + " " + x["href"])][:100], "state": state}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{key}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"site":key,"final_url":report["final_url"],"events":len(events),"job_events":len(report["job_events"]),"job_lines":len(report["body_job_lines"]),"links":len(report["links"]),"output":str(OUT / f'{key}.json')}, ensure_ascii=False))


if __name__ == "__main__":
    inspect((sys.argv[1] if len(sys.argv) > 1 else "catl").lower())
