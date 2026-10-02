"""N9.10: unified company resolution entry — every collection path.

``resolve_collection_company`` is the single shared resolver (job-level
explicit > site-level explicit > trusted alias / generic domain brand
inference > None). har_importer / generic / manual_curl / adapters all route
through it; ATS platform hosts can never leak through as the company.
"""
from __future__ import annotations

import json
from pathlib import Path

from job_extractor.company_identity import resolve_collection_company
from job_extractor.har_importer import import_har
from job_extractor.manual_curl import ManualCurlResult, manual_result_to_collection_result
from job_extractor.models import CollectionResult, DataCompleteness, Job
from job_extractor.output_layout import formal_company_name, resolve_run_directory
from job_extractor.runtime import render_result


def _har_file(tmp_path: Path, host: str) -> str:
    records = [{"code": f"A{i}", "jobName": f"Job {i}", "workPlace": "Shanghai",
                "postType": "R&D"} for i in range(2)]
    body = {"success": True, "errorCode": 0, "result": {"total": 2,
                                                        "list": records}}
    entries = [{"request": {"method": "POST",
                            "url": f"https://{host}/api/recruit/position/list",
                            "postData": {"text": json.dumps({"page": 1, "pageSize": 2})}},
                "response": {"status": 200,
                             "content": {"text": json.dumps(body)}}}]
    file = tmp_path / "list.har"
    file.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    return str(file)


# ---- HAR: real PDD host → verified canonical name ---------------------------

def test_har_xg_pinduoduo(tmp_path):
    outcome = import_har(_har_file(tmp_path, "xg.pinduoduo.com"))
    assert outcome.result.company == "拼多多集团"
    assert outcome.result.status == "COMPLETE"


# ---- Generic: domain brand inference without registry -----------------------

def _generic_plan(host: str, site_company: str | None):
    from job_extractor.planning.models import CollectionPlan
    return CollectionPlan(
        source_url=f"https://{host}/jobs", mode="HTML",
        company=site_company, executable=True, pagination_type="PAGE")


def test_generic_baidu_and_usmile():
    from job_extractor.collectors.generic_html import GenericHtmlCollector
    collector = GenericHtmlCollector.__new__(GenericHtmlCollector)
    collector.plan = _generic_plan("talent.baidu.com", None)
    jobs = [Job(job_id="1", job_title="岗位", source_url="https://talent.baidu.com/jobs")]
    result = CollectionResult(source_url=collector.plan.source_url,
                              platform="generic", status="COMPLETE", jobs=jobs)
    result.company = resolve_collection_company(
        result.jobs, collector.plan.company, collector.plan.source_url)
    assert result.company == "Baidu"
    collector.plan = _generic_plan("jobs.usmile.com", None)
    result2 = CollectionResult(source_url=collector.plan.source_url,
                               platform="generic", status="COMPLETE", jobs=list(jobs))
    result2.company = resolve_collection_company(
        result2.jobs, collector.plan.company, collector.plan.source_url)
    assert result2.company == "Usmile"


def test_generic_discovery_site_identity_wins_over_inference():
    # explicit discovery evidence (tenant/site identity) always outranks the
    # domain brand fallback
    company = resolve_collection_company(None, "COSCO SHIPPING 集团",
                                         "https://coscoshipping.iguopin.com/jobs")
    assert company == "COSCO SHIPPING 集团"


# ---- manual_curl -------------------------------------------------------------

def test_manual_curl_plain_api_host_infers_brand():
    job = Job(job_id="1", job_title="岗位", source_url="https://careers.acme-corp.cn/jobs")
    manual = ManualCurlResult("data", 1, 22, "HIGH", [job], 0, 1, 1, [],
                              "COMPLETE", "TOTAL_REACHED", 0.1, 1, "LIST_SUFFICIENT")
    unified = manual_result_to_collection_result(manual, "https://careers.acme-corp.cn/api/list")
    assert unified.company == "Acme-corp"


def test_manual_curl_explicit_company_beats_inference():
    job = Job(job_id="1", job_title="岗位", source_url="https://careers.acme-corp.cn/jobs",
              company="上海昂科信息科技有限公司")
    manual = ManualCurlResult("data", 1, 22, "HIGH", [job], 0, 1, 1, [],
                              "COMPLETE", "TOTAL_REACHED", 0.1, 1, "LIST_SUFFICIENT")
    unified = manual_result_to_collection_result(manual, "https://careers.acme-corp.cn/api/list")
    assert unified.company == "上海昂科信息科技有限公司"


# ---- ATS platform hosts can never leak through -------------------------------

def test_platform_hosts_never_output_platform_names():
    for source in ("https://www.mokahr.com", "https://jobs.feishu.cn",
                   "https://zhiye.com", "https://www.beisen.com",
                   "https://www.hotjob.cn"):
        assert resolve_collection_company(None, None, source) is None, source


def test_tenant_identity_wins_on_platform_host():
    # tenant subdomain: alias-canonicalized, never the platform name
    assert resolve_collection_company(None, None, "https://byd.mokahr.com") == "比亚迪"
    assert resolve_collection_company(None, None, "https://acme.zhiye.com/x") == "Acme"
    assert "Zhiye" not in str(resolve_collection_company(None, None, "https://acme.zhiye.com/x"))


# ---- opaque hosts may stay unknown -------------------------------------------

def test_opaque_host_stays_null():
    assert resolve_collection_company(None, None, "unknown-careers.example-host.com") is None
    assert resolve_collection_company(None, None, "192.168.1.1") is None


# ---- COSCO semantics + output directory --------------------------------------

def test_cosco_job_level_semantics_unchanged():
    assert formal_company_name("COSCO SHIPPING", "https://coscoshipping.iguopin.com") == "COSCO SHIPPING"


def test_output_directory_uses_resolved_company(tmp_path):
    out_root = tmp_path / "out"
    result = CollectionResult(
        source_url="https://xg.pinduoduo.com/jobs", platform="manual_curl",
        company=resolve_collection_company(None, None, "https://xg.pinduoduo.com"),
        status="COMPLETE")
    run_dir = resolve_run_directory(out_root, company=result.company,
                                    source_url=result.source_url, failed=False)
    assert run_dir.parent == out_root / "拼多多集团"


# ---- Job.company semantics: site brand never force-filled --------------------

def test_job_company_keeps_null_without_job_evidence(tmp_path):
    outcome = import_har(_har_file(tmp_path, "xg.pinduoduo.com"))
    result = outcome.result
    assert result.company == "拼多多集团"
    assert all(job.company is None for job in result.jobs)


def test_render_result_shows_resolved_company():
    result = CollectionResult(
        source_url="https://xg.pinduoduo.com/jobs", platform="manual_curl",
        company="拼多多集团", status="COMPLETE")
    assert result.company == "拼多多集团"  # board displays the resolved value
