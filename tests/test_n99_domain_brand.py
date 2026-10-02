"""N9.9: generic domain brand inference — most recruitment sites get a usable
company/brand label; the registry is only a high-confidence alias/canonical
layer; recruitment-platform domains never become the employer.
"""
from __future__ import annotations

import json
from pathlib import Path

from job_extractor.company_identity import infer_site_company
from job_extractor.har_importer import import_har
from job_extractor.manual_curl import ManualCurlResult, manual_result_to_collection_result
from job_extractor.models import Job
from job_extractor.output_layout import formal_company_name, trusted_site_company


def _har_file(tmp_path: Path, host: str, *, with_company: bool = False) -> str:
    records = [{"code": f"A{i}", "jobName": f"Job {i}", "workPlace": "Shanghai",
                "postType": "R&D"} for i in range(2)]
    if with_company:
        for record in records:
            record["companyName"] = "上海寻梦信息技术有限公司"
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


# ---- 1/2. PDD hosts resolve the verified canonical name ---------------------

def test_1_xg_pinduoduo_alias():
    assert infer_site_company("https://xg.pinduoduo.com") == "拼多多集团"
    assert infer_site_company("xg.pinduoduo.com") == "拼多多集团"  # bare host


def test_2_pddglobalhr_alias():
    assert infer_site_company("https://careers.pddglobalhr.com/api/list") == "拼多多集团"
    # registry-layer lookup agrees
    assert trusted_site_company("https://careers.pddglobalhr.com") == "拼多多集团"


# ---- 3/4. inference works without any registry entry ------------------------

def test_3_usmile_brand_without_registry():
    label = infer_site_company("https://jobs.usmile.com")
    assert label == "Usmile"  # generic title-case brand label


def test_4_baidu_brand_without_registry():
    assert infer_site_company("https://talent.baidu.com") == "Baidu"


# ---- 5. platform domains are never the employer ------------------------------

def test_5_platform_domains_never_become_companies():
    assert infer_site_company("https://www.mokahr.com") is None
    assert infer_site_company("https://jobs.feishu.cn/campus") is None
    assert infer_site_company("https://zhiye.com") is None
    assert infer_site_company("https://www.beisen.com") is None
    assert infer_site_company("https://www.hotjob.cn") is None
    # a tenant subdomain is the brand candidate — never the platform name
    assert infer_site_company("https://coscoshipping.iguopin.com/jobs") == "Coscoshipping"
    assert infer_site_company("https://byd.mokahr.com/x") == "比亚迪"  # alias applies to tenants too
    for label in ("Moka", "Feishu", "Zhiye", "Beisen", "Hotjob"):
        assert label not in str(infer_site_company("https://acme.zhiye.com/x"))


# ---- 6. opaque hosts may stay unknown ---------------------------------------

def test_6_opaque_hosts_stay_null():
    assert infer_site_company("unknown-careers.example-host.com") is None
    assert infer_site_company("https://careers.unrelated-corp.example.com") is None
    assert infer_site_company("192.168.1.1") is None
    assert infer_site_company("localhost") is None
    assert infer_site_company(None) is None


# ---- 7. explicit job/company evidence always outranks inference --------------

def test_7_job_level_company_wins(tmp_path):
    outcome = import_har(_har_file(tmp_path, "careers.pddglobalhr.com",
                                   with_company=True))
    assert outcome.result.company == "上海寻梦信息技术有限公司"
    # and the no-JD-evidence manual path never invents a domain brand
    job = Job(job_id="1", job_title="岗位", source_url="https://x.test/jobs")
    manual = ManualCurlResult("data", 1, 22, "HIGH", [job], 0, 1, 1, [],
                              "COMPLETE", "TOTAL_REACHED", 0.1, 1, "LIST_ONLY")
    unified = manual_result_to_collection_result(manual, "https://x.test/jobs")
    assert unified.company is None  # no silent domain inference here


# ---- 8. COSCO group/job-level semantics unchanged ----------------------------

def test_8_cosco_semantics_unchanged():
    assert formal_company_name("COSCO SHIPPING", "https://coscoshipping.iguopin.com") == "COSCO SHIPPING"
    assert trusted_site_company("https://coscoshipping.iguopin.com") is None  # registry untouched
    # the discovery-time site identity path is a separate, untouched mechanism
    from job_extractor.discovery.site_identity import resolve_site_company
    assert resolve_site_company("https://x.jobs.feishu.cn/j", [], [], None) is None
