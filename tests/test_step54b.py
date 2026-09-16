"""STEP 54B — generic runtime detail bridge tests.

Covers the minimal generic chain: runtime plan with organic detail contract →
bounded detail fetch → decode → identity validation → JD merge → completeness
evaluation. No provider vocabulary, no hard-coded scope values.
"""
from __future__ import annotations

import base64
import json as _json

import httpx
import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from job_extractor.collectors.generic_runtime_data import (
    GenericRuntimeDataCollector,
    valid_detail_contract,
)
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.discovery.runtime_data import (
    build_runtime_job_source,
    detail_contract_from_observation,
)
from job_extractor.planning import CollectionPlanBuilder

JD_HTML = (
    "<p>岗位职责</p>"
    "<p>1. Build and operate large-scale distributed systems that serve millions of requests per day, "
    "designing APIs and data pipelines that stay reliable under heavy traffic and continuous deployment.</p>"
    "<p>2. Partner with product managers, quality engineers, and designers to deliver complete features "
    "from requirements analysis through rollout, monitoring, and long-term maintenance of production services.</p>"
    "<p>任职要求</p>"
    "<p>1. Solid programming foundation in Python or Java with strong understanding of data structures, "
    "algorithms, concurrency, and clean code practices gained through several years of professional experience.</p>"
    "<p>2. Hands-on experience with relational databases, message queues, containers, and cloud platforms, "
    "plus the communication skills to mentor teammates and drive technical decisions across teams.</p>"
)
LIST_JD = "We are hiring. Apply now."
AES_IV = "de7c21ed8d6f50fe"
AES_KEY = "0123456789abcdef"

TEST_URL = "https://careers.custom.test/jobs"


def encrypt_envelope(detail: dict) -> dict:
    cipher = AES.new(AES_KEY.encode(), AES.MODE_CBC, AES_IV.encode())
    # STEP 54E: the transport envelope carries an encrypted business wrapper;
    # the job record lives under the wrapper's 'data' key after decryption —
    # exactly what the evidence-derived result_path=["data"] resolves.
    wrapped = {"success": True, "data": detail}
    return {
        "data": base64.b64encode(cipher.encrypt(pad(_json.dumps(wrapped).encode(), 16))).decode(),
        "necromancer": AES_KEY,
    }


def make_runtime_source(records: list[dict], total: int | None = None):
    scan = {"mechanism": "STATE", "arrays": [{"path": "data.jobs", "count": len(records), "sample": records}], "totals": [{"scalars": {"total": total if total is not None else len(records)}}]}
    return build_runtime_job_source(scan, provider="custom")


def plain_contract(locator: str = "jobId", scope_key: str = "orgId", scope_value: str = "org-1") -> dict:
    return detail_contract_from_observation({
        "requests": [{"method": "POST", "url": "/api/detail/job", "body": {scope_key: scope_value, locator: "seed"}, "responseEnvelope": [False, True]}],
        "parsed": [{"encEnvelope": False, "jdField": "jobDescription", "jdLen": 500, "idFields": ["id"], "titleFields": ["title"]}],
        "state": {scope_key: scope_value},
        # Production observations always carry the page origin (relative
        # endpoints are bound to it); fixtures mirror that shape.
        "pageOrigin": "https://careers.custom.test",
    })


def aes_contract() -> dict:
    return detail_contract_from_observation({
        "requests": [{"method": "POST", "url": "/api/detail/job", "body": {"orgId": "org-1", "jobId": "seed"}, "responseEnvelope": [True, True]}],
        "parsed": [{"encEnvelope": False, "jdField": "jobDescription", "jdLen": 500, "idFields": ["id"], "titleFields": ["title"]}],
        "state": {"orgId": "org-1", "aesIv": AES_IV},
        "pageOrigin": "https://careers.custom.test",
    })


def build_plan(source, contract: dict | None):
    if contract is not None:
        source = source.model_copy(update={"detail_contract": contract})
    return CollectionPlanBuilder().build(DiscoveryResult(source_url=TEST_URL, status="PARTIAL", runtime_source=source))


class ScriptedClient:
    """Per-job scripted responses: value may be a dict, an envelope dict, an
    exception instance, or None (empty body)."""

    def __init__(self, responses: dict, envelope: bool = False):
        self.responses = responses
        self.envelope = envelope
        self.calls: list[dict] = []

    def request(self, method: str, url: str, json_body: dict | None):
        self.calls.append(dict(json_body or {}))
        job_id = (json_body or {}).get("jobId")
        value = self.responses.get(job_id)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return 200, {}
        if self.envelope and "data" not in value:
            return 200, encrypt_envelope(value)
        return 200, value


def run(plan, client, records=None, **kwargs) -> tuple[object, GenericRuntimeDataCollector]:
    """Execution fixture: records are supplied as a transient observation —
    exactly what the production observer contract returns (an object with a
    .records attribute). The plan carries metadata + contract only."""
    if records is None:
        records = []
    source = build_runtime_job_source(
        {"mechanism": "STATE", "arrays": [{"path": "data.jobs", "count": len(records), "sample": records}], "totals": [{"scalars": {"total": len(records)}}]},
        provider="custom",
    )
    # Browser capture samples at most 120 rows for discovery evidence.  These
    # reconciliation fixtures deliberately model a complete already-observed
    # collection (R1 has AMEC's 175 + 7 mix), so retain all fixture rows for
    # the collector phase rather than accidentally exercising that sample cap.
    source = source.model_copy(update={"records": records, "record_count": len(records), "unique_ids": len({str(r.get("id")) for r in records})})
    collector = GenericRuntimeDataCollector(plan, observer=lambda: source, detail_client=client, **kwargs)
    return collector.collect(), collector


# ---------------------------------------------------------------------------
# CASE 1: DETAIL_REQUIRED + valid plain detail contract → fetched, JD complete
# ---------------------------------------------------------------------------
def test_case1_detail_required_with_contract_fetches_and_completes():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), plain_contract())
    assert valid_detail_contract(plan)
    client = ScriptedClient({"a": {"id": "a", "title": "Job A", "jobDescription": JD_HTML}})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["attempted"] == 1 and collector.detail_stats["succeeded"] == 1
    assert collector.detail_stats["skipped_list_sufficient"] == 1
    assert client.calls == [{"orgId": "org-1", "jobId": "a"}]
    # job b is complete from the list; job a becomes complete via detail.
    assert result.data_completeness.jd_complete == 2


# ---------------------------------------------------------------------------
# CASE 2: LIST_SUFFICIENT → detail_attempted == 0
# ---------------------------------------------------------------------------
def test_case2_list_sufficient_attempts_nothing():
    records = [{"id": "a", "title": "Job A", "jobDescription": JD_HTML}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), plain_contract())
    # Force LIST_SUFFICIENT detail mode (list already carries the full JD).
    plan = plan.model_copy(update={"detail_mode": "LIST_SUFFICIENT"})
    client = ScriptedClient({"a": {"id": "a", "title": "Job A", "jobDescription": JD_HTML}})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["attempted"] == 0
    assert client.calls == []
    assert result.metrics.details_attempted == 0


# ---------------------------------------------------------------------------
# CASE 3: AES_CBC_ENVELOPE detail response → decode + merge success
# ---------------------------------------------------------------------------
def test_case3_encrypted_envelope_decode_and_merge():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), aes_contract())
    # Discovery emits evidence-only decoder spec; mode is resolved by the
    # collector-side decoder from the envelope shape.
    assert plan.detail_decoder.get("envelope_field") == "necromancer"
    client = ScriptedClient({"a": {"id": "a", "title": "Job A", "jobDescription": JD_HTML}}, envelope=True)
    result, collector = run(plan, client, records)
    assert collector.detail_stats["succeeded"] == 1 and collector.detail_stats["failed"] == 0
    # job b complete from list; job a complete via decoded detail envelope.
    assert result.data_completeness.jd_complete == 2


# ---------------------------------------------------------------------------
# CASE 4: decoder failure → job retained, incomplete, collection survives
# ---------------------------------------------------------------------------
def test_case4_decoder_failure_fails_safe():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), aes_contract())
    # Corrupt envelope: valid base64 garbage that cannot decrypt.
    client = ScriptedClient({"a": {"data": base64.b64encode(b"\x00" * 32).decode(), "necromancer": AES_KEY}})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["failed"] == 1
    # STEP 54E: undecryptable content fails open to an empty dict at the
    # decode layer; the strict business-path resolver then rejects it
    # (PATH_MISSING). Still fail-closed: nothing is merged or fabricated.
    assert collector.detail_stats["failures"]["a"] == "DETAIL_PAYLOAD_PATH_MISSING"
    job = next(j for j in result.jobs if j.job_id == "a")
    assert not job.full_jd or job.full_jd == LIST_JD
    # only job b (list full text) is complete; job a stays honestly incomplete
    assert result.data_completeness.jd_complete == 1


# ---------------------------------------------------------------------------
# CASE 5: identity mismatch → reject merge
# ---------------------------------------------------------------------------
def test_case5_identity_mismatch_rejected():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), plain_contract())
    client = ScriptedClient({"a": {"id": "other-id", "title": "Job A", "jobDescription": JD_HTML}})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["failures"]["a"] == "DETAIL_IDENTITY_MISMATCH"
    assert collector.detail_stats["succeeded"] == 0
    job = next(j for j in result.jobs if j.job_id == "a")
    assert job.full_jd == LIST_JD  # list value untouched, no detail merge


# ---------------------------------------------------------------------------
# CASE 6: detail empty → job retained incomplete
# ---------------------------------------------------------------------------
def test_case6_empty_detail_keeps_job_incomplete():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), plain_contract())
    client = ScriptedClient({"a": {}})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["failures"]["a"] == "DETAIL_EMPTY"
    assert len(result.jobs) == 2
    # job b (list full text) complete; job a honestly incomplete
    assert result.data_completeness.jd_complete == 1


# ---------------------------------------------------------------------------
# CASE 7: request timeout → single job failed, collection survives
# ---------------------------------------------------------------------------
def test_case7_timeout_isolates_single_job():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": LIST_JD}]
    plan = build_plan(make_runtime_source(records), plain_contract())
    client = ScriptedClient({
        "a": httpx.TimeoutException("timed out"),
        "b": {"id": "b", "title": "Job B", "jobDescription": JD_HTML},
    })
    result, collector = run(plan, client, records)
    assert collector.detail_stats["failures"]["a"] == "DETAIL_TIMEOUT"
    assert collector.detail_stats["succeeded"] == 1
    assert len(result.jobs) == 2
    assert result.data_completeness.jd_complete == 1


# ---------------------------------------------------------------------------
# CASE 8: multiple jobs → success and failure isolated
# ---------------------------------------------------------------------------
def test_case8_mixed_success_and_failure_isolated():
    records = [
        {"id": "a", "title": "Job A", "jobDescription": LIST_JD},
        {"id": "b", "title": "Job B", "jobDescription": LIST_JD},
        {"id": "c", "title": "Job C", "jobDescription": LIST_JD},
    ]
    plan = build_plan(make_runtime_source(records), plain_contract())
    client = ScriptedClient({
        "a": {"id": "a", "title": "Job A", "jobDescription": JD_HTML},
        "b": httpx.HTTPStatusError("boom", request=httpx.Request("POST", TEST_URL), response=httpx.Response(500)),
        "c": {"id": "c", "title": "Wrong title", "jobDescription": JD_HTML},
    })
    result, collector = run(plan, client, records)
    assert collector.detail_stats["succeeded"] == 1
    assert collector.detail_stats["failures"]["b"] == "DETAIL_HTTP_ERROR"
    assert collector.detail_stats["failures"]["c"] == "DETAIL_IDENTITY_MISMATCH"
    assert len(result.jobs) == 3


# ---------------------------------------------------------------------------
# CASE 9: missing detail contract → no fabricated JD
# ---------------------------------------------------------------------------
def test_case9_missing_contract_no_fabricated_jd():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": LIST_JD}]
    plan = build_plan(make_runtime_source(records), None)
    assert not valid_detail_contract(plan)
    client = ScriptedClient({"a": {"id": "a", "title": "Job A", "jobDescription": JD_HTML}})
    result, collector = run(plan, client, records)
    assert collector.detail_stats == {}
    assert client.calls == []
    assert result.data_completeness.jd_complete == 0
    job = next(j for j in result.jobs if j.job_id == "a")
    assert job.full_jd == LIST_JD  # honest: list teaser only


# ---------------------------------------------------------------------------
# CASE 10: detail success → correct (non-conflated) metrics
# ---------------------------------------------------------------------------
def test_case10_metrics_from_real_detail_stage():
    records = [
        {"id": "a", "title": "Job A", "jobDescription": LIST_JD},
        {"id": "b", "title": "Job B", "jobDescription": LIST_JD},
    ]
    plan = build_plan(make_runtime_source(records), plain_contract())
    client = ScriptedClient({
        "a": {"id": "a", "title": "Job A", "jobDescription": JD_HTML},
        "b": {"id": "b", "title": "Job B", "jobDescription": JD_HTML},
    })
    result, collector = run(plan, client, records)
    assert result.metrics.details_attempted == 2
    assert result.metrics.details_succeeded == 2
    assert result.metrics.details_failed == 0
    # The attempted counter counts detail REQUESTS, not pagination pages.
    assert result.metrics.pages_requested == 0
    assert collector.detail_stats["required"] == 2
    assert result.metrics.average_detail_request_seconds >= 0.0


# ---------------------------------------------------------------------------
# CASE 11: legacy enrichment-only metrics stay backwards compatible
# ---------------------------------------------------------------------------
def test_case11_legacy_enrichment_metrics_not_conflated():
    records = [
        {"id": "a", "title": "Job A", "jobDescription": JD_HTML},
        {"id": "b", "title": "Job B", "jobDescription": LIST_JD},
    ]
    source = make_runtime_source(records)
    plan = build_plan(source, None)  # no contract → legacy path
    result = GenericRuntimeDataCollector(plan, observer=lambda: source).collect()
    assert result.enrichment["attempted"] == 2
    assert result.enrichment["requirements_filled"] == 1
    assert result.enrichment["source_absent"] == 1
    assert result.metrics.details_attempted == 2
    assert result.metrics.details_succeeded == 2
    assert result.metrics.details_failed == 0
    assert result.status == "COMPLETE"


# ---------------------------------------------------------------------------
# CASE 12: no detail_mode flip → plan ranking/contract unchanged
# (CATL rendered-list path regression control)
# ---------------------------------------------------------------------------
def test_case12_plan_ranking_unchanged_without_contract():
    records = [{"id": "a", "title": "Job A", "jobDescription": JD_HTML}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    source = make_runtime_source(records)
    plan = build_plan(source, None)
    # detail_mode stays UNKNOWN (set by _api_plan only), no contract fields set.
    assert plan.detail_endpoint_template is None
    assert plan.detail_method is None
    assert plan.detail_body_template == {}
    assert plan.detail_decoder == {}
    client = ScriptedClient({})
    result, collector = run(plan, client, records)
    assert collector.detail_stats == {} and client.calls == []
    assert result.status == "COMPLETE"


# ---------------------------------------------------------------------------
# CASE 13: LIST_SUFFICIENT detail_mode → detail_attempted == 0 (Geely path)
# ---------------------------------------------------------------------------
def test_case13_list_sufficient_detail_attempted_zero():
    records = [{"id": "a", "title": "Job A", "jobDescription": JD_HTML}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), plain_contract())
    plan = plan.model_copy(update={"detail_mode": "LIST_SUFFICIENT"})
    assert not valid_detail_contract(plan)
    client = ScriptedClient({"a": {"id": "a", "title": "Job A", "jobDescription": JD_HTML}})
    result, collector = run(plan, client, records)
    assert result.metrics.details_attempted == 0
    assert client.calls == []


# ---------------------------------------------------------------------------
# Contract-quality guards (support the 13 cases)
# ---------------------------------------------------------------------------
def test_unbacked_scope_value_yields_empty_contract():
    contract = detail_contract_from_observation({
        "requests": [{"method": "POST", "url": "/api/detail/job", "body": {"orgId": "org-1", "jobId": "seed"}, "responseEnvelope": [False, True]}],
        "parsed": [],
        "state": {"orgId": "different"},
        "pageOrigin": "https://careers.custom.test",
    })
    assert contract == {}


def test_detail_body_uses_contract_id_field_only():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), plain_contract())
    collector = GenericRuntimeDataCollector(plan)
    body = collector._detail_body("xyz")
    assert body == {"orgId": "org-1", "jobId": "xyz"}


def test_budget_limits_detail_attempts():
    records = [{"id": str(i), "title": f"Job {i}", "jobDescription": LIST_JD} for i in range(5)]
    plan = build_plan(make_runtime_source(records), plain_contract())
    client = ScriptedClient({r["id"]: {"id": r["id"], "title": r["title"], "jobDescription": JD_HTML} for r in records})
    result, collector = run(plan, client, records, detail_budget=2)
    assert collector.detail_stats["attempted"] == 2
    assert collector.detail_stats["budget_exhausted"] == 3
    assert result.metrics.details_attempted == 2


# ---------------------------------------------------------------------------
# STEP 54B business-result unwrap: resolve_detail_payload + result_path
# ---------------------------------------------------------------------------
from job_extractor.collectors.generic_runtime_data import resolve_detail_payload  # noqa: E402


def wrapped_contract() -> dict:
    """Contract with an evidence-recorded business-result path (["data"])."""
    return detail_contract_from_observation({
        "requests": [{"method": "POST", "url": "/api/detail/job", "body": {"orgId": "org-1", "jobId": "seed"}, "responseEnvelope": [False, True]}],
        "parsed": [{"encEnvelope": False, "jdField": "jobDescription", "jdLen": 500, "idFields": ["id"], "titleFields": ["title"], "resultPath": ["data"]}],
        "state": {"orgId": "org-1"},
        "pageOrigin": "https://careers.custom.test",
    })


def wrapped_detail(job_id: str, title: str = "Job A") -> dict:
    """Generic business-result wrapper: the record lives under 'data'."""
    return {"code": 0, "codeType": "SYSTEM", "msg": "ok", "success": True,
            "data": {"id": job_id, "title": title, "jobDescription": JD_HTML}}


# CASE A: decoded payload top level IS the job (result_path=[]) → success
def test_case_a_top_level_payload_success():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), plain_contract())
    assert plan.detail_result_path == []
    client = ScriptedClient({"a": {"id": "a", "title": "Job A", "jobDescription": JD_HTML}})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["succeeded"] == 1 and collector.detail_stats["failed"] == 0
    assert result.data_completeness.jd_complete == 2


# CASE B: wrapper {success, data:{job}} + result_path=["data"] → unwrap, merge
def test_case_b_wrapper_unwrapped_and_merged():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    assert plan.detail_result_path == ["data"]
    client = ScriptedClient({"a": wrapped_detail("a")})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["succeeded"] == 1 and collector.detail_stats["failed"] == 0
    assert result.data_completeness.jd_complete == 2
    job = next(j for j in result.jobs if j.job_id == "a")
    assert job.full_jd == JD_HTML


# CASE C: result_path=["data"] but segment missing → PATH_MISSING, no merge
def test_case_c_wrapper_path_missing():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    client = ScriptedClient({"a": {"code": 1, "msg": "not found", "success": False}})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["failures"]["a"] == "DETAIL_PAYLOAD_PATH_MISSING"
    job = next(j for j in result.jobs if j.job_id == "a")
    assert job.full_jd == LIST_JD  # untouched, honestly incomplete
    assert result.data_completeness.jd_complete == 1


# CASE D: resolved segment is list/string/null → PAYLOAD_INVALID, no merge
def test_case_d_wrapper_payload_invalid():
    for bad in ([1, 2], "nope", None):
        records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
        plan = build_plan(make_runtime_source(records), wrapped_contract())
        client = ScriptedClient({"a": {"data": bad}})
        result, collector = run(plan, client, records)
        assert collector.detail_stats["failures"]["a"] == "DETAIL_PAYLOAD_INVALID", bad
        assert result.data_completeness.jd_complete == 1


# CASE E: wrapper top-level id noise conflicts, nested record wins via path
def test_case_e_wrapper_noise_ignored_nested_wins():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    client = ScriptedClient({"a": {"code": 0, "id": "wrapper-noise-id", "success": True,
                                   "data": {"id": "a", "title": "Job A", "jobDescription": JD_HTML}}})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["succeeded"] == 1 and collector.detail_stats["failed"] == 0
    assert result.data_completeness.jd_complete == 2


# CASE F: nested record identity mismatch → reject, no merge
def test_case_f_nested_identity_mismatch_rejected():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    client = ScriptedClient({"a": wrapped_detail("different-id")})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["failures"]["a"] == "DETAIL_IDENTITY_MISMATCH"
    assert result.data_completeness.jd_complete == 1


# CASE G: nested record carries empty JD → no false complete.
# STEP51 merge safety: the empty detail must NOT erase the existing list JD.
# The detail round-trip itself succeeds (transport→resolve→identity→merge all
# fine; the empty field is a no-op merge), so the record stays usable — but
# its JD remains the short list teaser → the job stays incomplete.
def test_case_g_nested_jd_empty_incomplete():
    records = [{"id": "a", "title": "Job A", "jobDescription": LIST_JD}, {"id": "b", "title": "Job B", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    client = ScriptedClient({"a": {"success": True, "data": {"id": "a", "title": "Job A", "jobDescription": ""}}})
    result, collector = run(plan, client, records)
    # detail round-trip completes; an empty detail field is a no-op merge,
    # not a transport/decode/identity failure
    assert collector.detail_stats["attempted"] == 1
    assert collector.detail_stats["succeeded"] == 1
    assert "a" not in collector.detail_stats["failures"]
    # A. list content preserved — the empty detail did not erase it
    job_a = next(j for j in result.jobs if j.job_id == "a")
    assert job_a.full_jd == LIST_JD
    # B. a short teaser is not a complete JD → job stays incomplete
    assert result.data_completeness.jd_complete == 1
    assert result.data_completeness.jd_incomplete == 1


# CASE H: ambiguous wrapper shapes → contract fails closed, no result_path
def test_case_h_no_path_evidence_fails_closed():
    # Observation where the JD+id pair is seen at top level: no result_path is
    # recorded — the execution layer then treats the decoded payload as-is.
    contract = detail_contract_from_observation({
        "requests": [{"method": "POST", "url": "/api/detail/job", "body": {"orgId": "org-1", "jobId": "seed"}, "responseEnvelope": [False, True]}],
        "parsed": [{"encEnvelope": False, "jdField": "jobDescription", "jdLen": 500, "idFields": ["id"], "titleFields": ["title"]}],
        "state": {"orgId": "org-1"},
        "pageOrigin": "https://careers.custom.test",
    })
    assert contract.get("result_path") in (None, [])
    # resolve_detail_payload with no path on a wrapped payload: the wrapper is
    # not a dict-shaped record per path — but with [] path it returns the
    # wrapper itself; identity/JD at top level then fail honestly downstream.
    resolved, code = resolve_detail_payload({"success": True, "data": {"id": "a", "jobDescription": JD_HTML}}, [])
    assert code is None and isinstance(resolved, dict)
    # Missing segment on an explicit path fails closed, never guesses.
    resolved, code = resolve_detail_payload({"success": True}, ["data"])
    assert resolved is None and code == "DETAIL_PAYLOAD_PATH_MISSING"
    resolved, code = resolve_detail_payload({"data": "nope"}, ["data"])
    assert resolved is None and code == "DETAIL_PAYLOAD_INVALID"


def test_resolve_detail_payload_nested_path():
    decoded = {"code": 0, "data": {"meta": {"id": "x", "jobDescription": JD_HTML}}}
    resolved, code = resolve_detail_payload(decoded, ["data", "meta"])
    assert code is None and resolved["id"] == "x"


# ---------------------------------------------------------------------------
# STEP 54B detail-gating reconciliation: pre-fetch required vs attempted must
# be measurable at the SAME point in time. Post-merge state counts drift as
# detail payloads upgrade job records (the 7-vs-182 semantics drift).
# ---------------------------------------------------------------------------
def _full_detail(job_id: str) -> dict:
    return {"success": True, "data": {"id": job_id, "title": "Job", "jobDescription": JD_HTML}}


def test_recon1_mixed_states_pending_matches_required():
    # 175 FULL_TEXT list records + 7 JD-less records: only the 7 enter the
    # detail stage; attempted == required == 7 regardless of detail_mode hint.
    records = [{"id": f"f{i}", "title": f"Job F{i}", "jobDescription": JD_HTML} for i in range(175)]
    records += [{"id": f"s{i}", "title": f"Job S{i}"} for i in range(7)]
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    responses = {
        f"s{i}": {"success": True, "data": {"id": f"s{i}", "title": f"Job S{i}", "jobDescription": JD_HTML}}
        for i in range(7)
    }
    client = ScriptedClient(responses)
    result, collector = run(plan, client, records)
    stats = collector.detail_stats
    assert stats["required"] == 7
    assert stats["attempted"] == 7
    assert stats["skipped_list_sufficient"] == 175
    assert stats["succeeded"] == 7
    assert len(client.calls) == 7
    # metrics bridge exposes the same pre-fetch required count
    assert result.metrics.details_required == 7
    assert result.metrics.details_attempted == 7
    # reporting layer reconciles at the same point in time
    assert result.data_completeness.detail_required == 7
    assert result.data_completeness.detail_attempted == 7


def test_recon2_full_text_not_fetched_when_mode_unknown():
    # plan.detail_mode stays UNKNOWN (runtime evidence path) — a list record
    # with a credible JD must not be fetched.
    records = [
        {"id": "a", "title": "Job A", "jobDescription": JD_HTML},
        {"id": "b", "title": "Job B", "jobDescription": JD_HTML},
    ]
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    assert plan.detail_mode != "LIST_SUFFICIENT"
    client = ScriptedClient({})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["attempted"] == 0
    assert collector.detail_stats["required"] == 0
    assert collector.detail_stats["skipped_list_sufficient"] == 2
    assert client.calls == []


def test_recon3_summary_job_fetched():
    # companion record only satisfies the >=2 job-array minimum; the summary
    # record is the one under test
    records = [{"id": "a", "title": "Job A", "description": "Short teaser."},
               {"id": "z", "title": "Job Z", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    client = ScriptedClient({"a": _full_detail("a")})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["required"] == 1
    assert collector.detail_stats["attempted"] == 1
    assert collector.detail_stats["succeeded"] == 1
    assert collector.detail_stats["skipped_list_sufficient"] == 1
    assert client.calls == [{"orgId": "org-1", "jobId": "a"}]


def test_recon4_absent_job_fetched():
    records = [{"id": "a", "title": "Job A"},
               {"id": "z", "title": "Job Z", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    client = ScriptedClient({"a": _full_detail("a")})
    result, collector = run(plan, client, records)
    assert collector.detail_stats["required"] == 1
    assert collector.detail_stats["attempted"] == 1
    assert collector.detail_stats["succeeded"] == 1
    assert collector.detail_stats["skipped_list_sufficient"] == 1
    assert client.calls == [{"orgId": "org-1", "jobId": "a"}]


def test_recon5_merged_record_not_refetched():
    # A list record whose JD only exists post-merge (detail payload upgraded
    # it) must not be re-required by the reporting count: required is measured
    # pre-fetch, not on post-merge raw_data.
    records = [{"id": "a", "title": "Job A"},
               {"id": "z", "title": "Job Z", "jobDescription": JD_HTML}]
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    client = ScriptedClient({"a": _full_detail("a")})
    result, collector = run(plan, client, records)
    job = next(j for j in result.jobs if j.job_id == "a")
    assert job.full_jd == JD_HTML
    # post-merge the raw record now has FULL_TEXT state, yet the bridge keeps
    # the pre-fetch required count (1), not a drifted 0
    assert (job.raw_data or {}).get("_jd_state") == "FULL_TEXT"
    assert result.data_completeness.detail_required == 1
    assert result.data_completeness.detail_attempted == 1


def test_recon6_required_attempted_semantics_consistent():
    # The reporting-layer required count and the collector pre-fetch gate use
    # the same needs_detail_fetch semantics: for any mix, dc.detail_required
    # equals the collector gate count (no reporting-side re-derivation).
    records = (
        [{"id": f"f{i}", "title": f"F{i}", "jobDescription": JD_HTML} for i in range(3)]
        + [{"id": f"m{i}", "title": f"M{i}", "description": "teaser"} for i in range(2)]
        + [{"id": "z", "title": "Z"}]
    )
    plan = build_plan(make_runtime_source(records), wrapped_contract())
    responses = {
        "m0": {"success": True, "data": {"id": "m0", "title": "M0", "jobDescription": JD_HTML}},
        "m1": {"success": True, "data": {"id": "m1", "title": "M1", "jobDescription": JD_HTML}},
        "z": {"success": True, "data": {"id": "z", "title": "Z", "jobDescription": JD_HTML}},
    }
    client = ScriptedClient(responses)
    result, collector = run(plan, client, records)
    stats = collector.detail_stats
    dc = result.data_completeness
    assert stats["required"] == 3 and dc.detail_required == 3
    assert stats["attempted"] == 3 and dc.detail_attempted == 3
    assert stats["succeeded"] == 3 and dc.detail_succeeded == 3
    # This is also a pre-fetch gate measure.  Post-merge ``list_sufficient``
    # is six because successful detail payloads make all jobs FULL_TEXT.
    assert stats["skipped_list_sufficient"] == 3


# ---------------------------------------------------------------------------
# STEP 54E — runtime detail-contract scope consistency + result-path evidence
# ---------------------------------------------------------------------------

def encrypted_no_parse_contract() -> dict:
    """Encrypted envelope observed, but the hook parsed no plaintext record
    (production AMEC shape): the only payload-location evidence is the
    decoder spec's ``payload_field``."""
    return detail_contract_from_observation({
        "requests": [{"method": "POST", "url": "/api/detail/job", "body": {"orgId": "org-1", "siteId": "999", "jobId": "seed"}, "responseEnvelope": [True, True]}],
        "parsed": [],
        "state": {"orgId": "org-1", "siteId": "999", "aesIv": AES_IV},
        "pageOrigin": "https://careers.custom.test",
    })


def _plan_with_scope(contract, detected_scope):
    # Two records: the runtime-state array parser only adopts job-bearing
    # arrays with more than one element (mirrors real observation volume).
    source = make_runtime_source([{"id": "a", "title": "Job A", "jobDescription": LIST_JD},
                                  {"id": "b", "title": "Job B", "jobDescription": LIST_JD}])
    source = source.model_copy(update={"detail_contract": contract})
    return CollectionPlanBuilder().build(DiscoveryResult(source_url=TEST_URL, status="PARTIAL", runtime_source=source, detected_scope=detected_scope))


def test_runtime_scope_matching_detected_scope_passes_through():
    plan = _plan_with_scope(plain_contract(scope_key="siteId", scope_value="4362"), {"siteId": 4362})
    assert plan.detail_body_template["siteId"] == "4362"


def test_runtime_scope_conflict_uses_detected_scope():
    # Runtime state snapshot carries a stale/different siteId; the page's
    # detected scope is the trusted source and must win — the wrong value
    # never reaches the plan.
    contract = detail_contract_from_observation({
        "requests": [{"method": "POST", "url": "/api/detail/job", "body": {"orgId": "org-1", "siteId": "28371", "jobId": "seed"}, "responseEnvelope": [False, True]}],
        "parsed": [{"encEnvelope": False, "jdField": "jobDescription", "jdLen": 500, "idFields": ["id"], "titleFields": ["title"]}],
        "state": {"orgId": "org-1", "siteId": "28371"},
        "pageOrigin": "https://careers.custom.test",
    })
    assert contract["body_template"]["siteId"] == "28371"
    plan = _plan_with_scope(contract, {"orgId": "org-1", "siteId": 4362})
    assert plan.detail_body_template["siteId"] == "4362"
    assert plan.detail_body_template["orgId"] == "org-1"


def test_encrypted_without_parse_evidence_derives_result_path():
    # decoder payload_field="data" is the only payload-location evidence →
    # business record resolves under ["data"].
    assert encrypted_no_parse_contract()["result_path"] == ["data"]
    plan = _plan_with_scope(encrypted_no_parse_contract(), {})
    assert plan.detail_result_path == ["data"]


def test_no_payload_evidence_leaves_result_path_empty():
    # Plaintext observation without a parsed record: no payload-location
    # evidence exists, so no path may be guessed.
    contract = detail_contract_from_observation({
        "requests": [{"method": "POST", "url": "/api/detail/job", "body": {"orgId": "org-1", "jobId": "seed"}, "responseEnvelope": [False, True]}],
        "parsed": [],
        "state": {"orgId": "org-1"},
        "pageOrigin": "https://careers.custom.test",
    })
    assert "result_path" not in contract
    plan = _plan_with_scope(contract, {})
    assert plan.detail_result_path == []
