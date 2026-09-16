"""STEP 54B read-only diagnostic: capture ALL detail-route requests with a
loose in-page hook to learn the organic request shape (body type, keys,
envelope). No collection. Generic — no site-specific logic."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://app.mokahr.com/campus_apply/amec/4362#/jobs?page=1&anchorName=jobsList"
SAMPLE_ID = "ac053a81-39dd-4fac-87d9-d66c5c4152e0"
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "step54b_amec" / "diagnostic"

LOOSE_HOOK = r"""
(() => {
  if (window.__looseCap) return;
  window.__looseCap = {requests: []};
  const cap = (method, url, body) => {
    try {
      let serialized = null;
      if (body === undefined || body === null) serialized = null;
      else if (typeof body === 'string') { try { serialized = JSON.parse(body); } catch(e) { serialized = body.slice(0, 500); } }
      else serialized = body;
      window.__looseCap.requests.push({method: String(method||'GET').toUpperCase(), url: String(url||''), body: serialized});
    } catch(e) {}
  };
  const xo = XMLHttpRequest.prototype.open, xs = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m,u){this.__u=u;this.__m=m;return xo.apply(this,arguments);};
  XMLHttpRequest.prototype.send = function(b){
    try{ cap(this.__m, this.__u, b);
      this.addEventListener('load', function(){
        try{
          const req = window.__looseCap.requests.find(x=>x.url===String(this.__u||'').split('?')[0] && x.status===undefined);
          if(req){ req.status=this.status; let p=null; try{p=JSON.parse(this.responseText);}catch(e){}
            if(p&&typeof p==='object'){ req.respTopKeys=Object.keys(p).slice(0,10); req.respIsEnvelope=('necromancer' in p)&&('data' in p); }
            req.respTextLength=String(this.responseText||'').length; }
        }catch(e){}
      }, {once:true});
    }catch(e){}
    return xs.apply(this,arguments);
  };
  const of = window.fetch;
  window.fetch = function(r,o){
    try{ cap((o&&o.method)||(r&&r.method)||'GET', (typeof r==='string')?r:(r&&r.url)||'', o&&o.body); }catch(e){}
    return of.apply(this,arguments);
  };
})();
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"url": URL, "sample_id": SAMPLE_ID}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        # Install the loose hook into the CURRENT document (covers
        # same-document hash navigation) and future documents.
        page.evaluate(LOOSE_HOOK)
        page.add_init_script(LOOSE_HOOK)
        target = f"{URL.split('#')[0]}#/job/{SAMPLE_ID}"
        report["target"] = target
        page.goto(target, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        report["final_url"] = page.url
        report["is_same_document_style_nav"] = page.url.split("#")[0] == URL.split("#")[0]
        cap = page.evaluate("() => window.__looseCap || {requests: []}")
        requests = cap.get("requests") if isinstance(cap, dict) else None
        report["request_count"] = len(requests or [])
        posts = [r for r in (requests or []) if r.get("method") == "POST"]
        report["post_count"] = len(posts)
        report["posts"] = posts[:40]
        print("final url:", page.url, flush=True)
        print("requests:", report["request_count"], "posts:", report["post_count"], flush=True)
        for r in posts[:20]:
            print("POST", r.get("url", "")[:110], flush=True)
            print("   body:", json.dumps(r.get("body"), ensure_ascii=False)[:400], flush=True)
            if r.get("respIsEnvelope") is not None:
                print("   envelope:", r.get("respIsEnvelope"), "topKeys:", r.get("respTopKeys"), flush=True)
        browser.close()
    (OUT / "loose_capture.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("written:", OUT / "loose_capture.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
