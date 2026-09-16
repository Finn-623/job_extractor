"""Read-only lineage probe for non-empty page responses with invalid Total=0."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx

ROOT = Path("artifacts/step54a1_zero_total_lineage")
SITES = ("ymtc", "cxmt", "guangzhou_metro")
TERMINAL_NAMES = {"total", "totalcount", "datacount", "totalpage", "totalpages", "pagecount", "pages", "hasnext", "hasmore", "islast", "lastpage", "next", "nextpage", "nextcursor", "currentpage", "pageno", "pageindex", "count"}


def path_get(value, path):
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def scalars(value, path="", depth=0):
    if depth > 5 or not isinstance(value, dict):
        return []
    out = []
    for key, item in value.items():
        current = f"{path}.{key}" if path else key
        if key.lower().replace("_", "") in TERMINAL_NAMES and isinstance(item, (str, int, float, bool)):
            out.append({"path": current, "value": item})
        if isinstance(item, dict):
            out.extend(scalars(item, current, depth + 1))
    return out


def identity(record):
    for key in ("Id", "id", "JobAdId", "jobAdId", "postId"):
        if record.get(key) not in (None, ""):
            return str(record[key])
    return None


def probe(site):
    plan = json.loads((Path("artifacts/step54_v1_benchmark/per_site") / site / "plan.json").read_text())
    values = dict(plan["initial_values"]); page_key = plan["page_param"]; page_size = int(values[plan["page_size_param"]])
    output = {"site": site, "endpoint": plan["list_endpoint"], "method": plan["list_method"], "page_param": page_key,
              "page_size_param": plan["page_size_param"], "requested_page_size": page_size, "initial_value": values[page_key], "pages": []}
    with httpx.Client(timeout=25) as client:
        for page in (0, 1, 2):
            request = dict(values); request[page_key] = page
            response = client.post(plan["list_endpoint"], json=request)
            response.raise_for_status(); payload = response.json(); records = path_get(payload, plan["list_path"]) or []
            ids = [identity(item) for item in records if isinstance(item, dict) and identity(item)]
            output["pages"].append({"page": page, "status": response.status_code, "count": len(records), "unique": len(set(ids)),
                                    "ordered_hash": hashlib.sha256("\x1f".join(ids).encode()).hexdigest(),
                                    "set_hash": hashlib.sha256("\x1f".join(sorted(ids)).encode()).hexdigest(),
                                    "scalars": scalars(payload)})
    return output


def main():
    ROOT.mkdir(parents=True, exist_ok=True); report = [probe(site) for site in SITES]
    (ROOT / "lineage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
