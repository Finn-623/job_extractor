"""STEP 54B pre-Stage1 verification: exercise the regenerated plan's detail
contract exactly as the collector would (production decoder, production body
substitution) for ONE sample job. Read-only detail fetches.

Also checks the siteId variant observed in the standalone diagnostic (4362)
so a wrong scope binding is caught before Stage1 burns a full run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

from job_extractor.collectors.generic_decoder import decode_detail_payload

PLAN = Path(__file__).resolve().parents[1] / "artifacts" / "step54b_amec" / "plan.json"


def try_request(client: httpx.Client, plan: dict, job_id: str, body_override: dict | None = None) -> dict:
    body = {**plan["detail_body_template"], plan["detail_id_field"]: job_id}
    if body_override:
        body.update(body_override)
    headers = {"Content-Type": "application/json"}
    try:
        response = client.post(plan["detail_endpoint_template"], json=body, headers=headers, timeout=15.0)
    except Exception as exc:
        return {"body": body, "error": f"{type(exc).__name__}: {exc}"}
    out: dict = {"body": body, "status": response.status_code}
    try:
        envelope = response.json()
    except Exception:
        out["decode"] = "NON_JSON"
        return out
    decoded = decode_detail_payload(envelope, plan["detail_decoder"])
    if not decoded:
        out["decode"] = "EMPTY"
        out["envelope_keys"] = sorted(envelope)[:6] if isinstance(envelope, dict) else type(envelope).__name__
        return out
    out["decode"] = "OK"
    out["keys"] = sorted(decoded)[:12]
    jd = decoded.get(plan["detail_jd_field"]) or ""
    out["jd_len"] = len(jd) if isinstance(jd, str) else 0
    out["id"] = decoded.get("id")
    out["title"] = (decoded.get("title") or "")[:60]
    return out


def main() -> int:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    runtime = plan.get("runtime_source") or {}
    records = runtime.get("records") or []
    sample = next((r for r in records if r.get(runtime.get("job_id_field"))), None)
    if not sample:
        print("no sample record in plan")
        return 1
    job_id = sample[runtime["job_id_field"]]
    print("sample job:", job_id, (sample.get("title") or "")[:50], flush=True)
    print("plan body_template:", plan["detail_body_template"], flush=True)
    results = []
    with httpx.Client() as client:
        # As the plan carries it.
        results.append(try_request(client, plan, job_id))
        # Site-id variant from the standalone diagnostic observation.
        results.append(try_request(client, plan, job_id, {"siteId": 4362}))
        # Locale variant (zh-CN as the SPA originally sent).
        results.append(try_request(client, plan, job_id, {"locale": "zh-CN"}))
    for i, r in enumerate(results):
        print(f"--- attempt {i}:", json.dumps(r, ensure_ascii=False)[:500], flush=True)
    (PLAN.parent / "contract_verification.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok = any(r.get("decode") == "OK" and (r.get("jd_len") or 0) > 160 for r in results)
    print("CONTRACT VERIFICATION:", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
