"""STEP 53H — Geely read-only pagination lineage probe.

Target list source (from STEP53F): GET .../console-gateway/zp-api/internal/website/school/position/positions
LIST_SUFFICIENT (description + qualifications in list JSON).

The probe captures the real page request (URL + query params + full pagination
metadata paths), then re-requests it 3x with mutated pagination params inside
the same browser session, and also drives the page's own pagination control if
present, recording per-page identity + totals.
Writes artifacts/step53h_pagination/geely_lineage.json.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from job_extractor.browser import BrowserRuntime

URL = "https://careers.geelytech.com/campus"
OUT = Path("artifacts/step53h_pagination/geely_lineage.json")
LIST_MARKER = "school/position/positions"


def sig(ids: list[str]) -> dict:
    uniq = list(dict.fromkeys(ids))
    return {"count": len(ids), "unique": len(uniq), "first": uniq[0] if uniq else None,
            "last": uniq[-1] if uniq else None,
            "ordered_hash": sha256(json.dumps(ids).encode()).hexdigest()[:16],
            "set_hash": sha256(json.dumps(sorted(uniq)).encode()).hexdigest()[:16]}


def main() -> None:
    captured: list[dict] = []
    with BrowserRuntime(timeout_ms=20000) as runtime:
        page = runtime.page

        def on_response(res):
            req = res.request
            if req.resource_type not in ("xhr", "fetch") or LIST_MARKER not in req.url:
                return
            entry = {"phase": "page", "method": req.method, "url": req.url, "status": res.status}
            try:
                payload = res.json()
            except Exception:
                captured.append(entry | {"raw_kind": "NON_JSON"})
                return
            entry["raw_kind"] = "JSON"
            # locate data array + pagination metadata generically
            def shape(value, path="$", depth=0):
                if depth > 4:
                    return
                if isinstance(value, dict):
                    for k, v in value.items():
                        if isinstance(v, list) and v and isinstance(v[0], dict) and len(v) >= 3:
                            keys = set(v[0])
                            if any(x in keys for x in ("id", "jobId", "positionId")):
                                arrays.append({"path": f"{path}.{k}", "count": len(v),
                                               "ids": [str(x.get("id", x.get("jobId", ""))) for x in v],
                                               "sample_keys": sorted(keys)[:30]})
                        if isinstance(v, (int, bool)) and any(t in k.lower() for t in ("total", "count", "hasmore", "has_more", "page", "size", "limit", "current", "pages")):
                            scalars[f"{path}.{k}"] = v
                        shape(v, f"{path}.{k}", depth + 1)
            arrays: list[dict] = []
            scalars: dict[str, object] = {}
            shape(payload)
            entry["arrays"] = arrays
            entry["pagination_scalars"] = scalars
            captured.append(entry)

        page.on("response", on_response)
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(9000)

        boot = [dict(e) for e in captured]
        boot_arrays = [a for e in boot for a in e.get("arrays", [])]
        template_url = boot[0]["url"] if boot else None
        print(json.dumps({"boot_captures": len(boot), "template_url": template_url,
                          "scalars": boot[0].get("pagination_scalars") if boot else None}, ensure_ascii=False), flush=True)

        # ---- Replay with mutated pagination state (same session). ----------
        replay_pages: list[dict] = []
        if template_url:
            parsed = urlparse(template_url)
            base_q = dict(parse_qsl(parsed.query, keep_blank_values=True))
            # Candidate pagination mutations: only mutate keys that already exist,
            # plus generic page/size candidates if none present.
            page_key = next((k for k in base_q if k.lower() in ("page", "pagenum", "pageno", "pageindex", "current", "currentpage")), None)
            size_key = next((k for k in base_q if k.lower() in ("size", "pagesize", "limit", "pagesizeparam")), None)
            print(json.dumps({"query_keys": list(base_q), "page_key": page_key, "size_key": size_key}, ensure_ascii=False), flush=True)
            if page_key is None:
                page_key = "page"
            if size_key is None:
                size_key = "size"
            for index, value in enumerate([1, 2, 3], start=1):
                q = dict(base_q)
                q[page_key] = str(value)
                if size_key not in q:
                    q[size_key] = "10" if not base_q.get(size_key) else str(base_q[size_key])
                target = urlunparse(parsed._replace(query=urlencode(q)))
                result = page.evaluate(
                    """async (url) => {
                        try {
                          const r = await fetch(url, {method: 'GET', headers: {'Accept': 'application/json'}});
                          const text = await r.text();
                          let payload = null; try { payload = JSON.parse(text); } catch (e) {}
                          return {status: r.status, bytes: text.length, payload};
                        } catch (e) { return {error: String(e)}; }
                    }""",
                    target,
                )
                payload = result.get("payload")
                arrays: list[dict] = []
                scalars: dict[str, object] = {}

                def shape2(value, path="$", depth=0):
                    if depth > 4:
                        return
                    if isinstance(value, dict):
                        for k, v in value.items():
                            if isinstance(v, list) and v and isinstance(v[0], dict) and len(v) >= 3:
                                keys = set(v[0])
                                if any(x in keys for x in ("id", "jobId", "positionId")):
                                    arrays.append({"path": f"{path}.{k}", "count": len(v),
                                                   "ids": [str(x.get("id", x.get("jobId", ""))) for x in v]})
                            if isinstance(v, (int, bool)) and any(t in k.lower() for t in ("total", "count", "hasmore", "has_more", "page", "size", "limit", "current", "pages")):
                                scalars[f"{path}.{k}"] = v
                            shape2(v, f"{path}.{k}", depth + 1)
                shape2(payload)
                chosen = max(arrays, key=lambda a: a["count"]) if arrays else None
                identity = sig(chosen["ids"]) if chosen else None
                row = {"page": index, "mutated": {page_key: value, size_key: q.get(size_key)},
                       "url": target, "status": result.get("status"), "bytes": result.get("bytes"),
                       "list_path": chosen.get("path") if chosen else None,
                       "returned_count": chosen.get("count") if chosen else 0,
                       "identity": identity, "pagination_scalars": scalars}
                replay_pages.append(row)
                print(json.dumps({k: row[k] for k in ("page", "status", "list_path", "returned_count",
                                                      "pagination_scalars")}, ensure_ascii=False), flush=True)
                captured.clear()

        # overlap across replay pages
        overlap = []
        for i in range(len(replay_pages)):
            for j in range(i + 1, len(replay_pages)):
                a, b = replay_pages[i]["identity"], replay_pages[j]["identity"]
                if a and b:
                    sa, sb = set(a["ids"]), set(b["ids"])
                    overlap.append({"between": [replay_pages[i]["page"], replay_pages[j]["page"]],
                                    "shared": len(sa & sb), "new": len(sb - sa)})

    report = {
        "site": "geely", "url": URL,
        "boot": {"captures": [{k: e.get(k) for k in ("method", "url", "status", "raw_kind", "arrays", "pagination_scalars")} for e in boot]},
        "replay_pages": replay_pages,
        "cross_page_overlap": overlap,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"overlap": overlap, "output": str(OUT)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
