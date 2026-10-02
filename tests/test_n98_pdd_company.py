"""N9.8: PDD site-level company identity via the generic trusted registry.

careers.pddglobalhr.com officially shows "拼多多集团-PDD"; the canonical
site-level company is "拼多多集团". Resolution is a generic registry lookup
(never a PDD if-branch in the importer) with the shared identity priority:
job-level explicit company > trusted site identity > null.
"""
from __future__ import annotations

import json
from pathlib import Path

from job_extractor.har_importer import import_har
from job_extractor.models import CollectionResult, Job
from job_extractor.output_layout import (
    KNOWN_COMPANY_NAMES,
    formal_company_name,
    resolve_run_directory,
    trusted_site_company,
)


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


# ---- a. careers.pddglobalhr.com → 拼多多集团 --------------------------------

def test_a_pdd_site_resolves_group_company(tmp_path):
    outcome = import_har(_har_file(tmp_path, "careers.pddglobalhr.com"))
    result = outcome.result
    assert result.company == "拼多多集团"
    # product smoke facts stay intact
    assert result.status == "COMPLETE"
    assert result.metrics.jd_strategy == "LIST_ONLY"


def test_a_output_directory_uses_group_name(tmp_path):
    outcome = import_har(_har_file(tmp_path, "careers.pddglobalhr.com"))
    result = outcome.result
    out_root = tmp_path / "out"
    run_dir = resolve_run_directory(out_root, company=result.company,
                                    source_url=result.source_url,
                                    failed=False)
    assert run_dir.parent == out_root / "拼多多集团"
    assert not run_dir.parent.name.startswith("_")


# ---- b. unrelated unknown domain stays null --------------------------------

def test_b_unknown_domain_stays_null(tmp_path):
    assert trusted_site_company("https://careers.unrelated-corp.example.com/jobs") is None
    outcome = import_har(_har_file(tmp_path, "careers.unrelated-corp.example.com"))
    assert outcome.result.company is None
    assert formal_company_name(None, "https://careers.unrelated-corp.example.com") == "_unknown"


# ---- c. job-level explicit company outranks the registry -------------------

def test_c_job_level_company_wins(tmp_path):
    outcome = import_har(_har_file(tmp_path, "careers.pddglobalhr.com",
                                   with_company=True))
    result = outcome.result
    assert result.company == "上海寻梦信息技术有限公司"
    assert all(job.company == "上海寻梦信息技术有限公司" for job in result.jobs)


def test_c_job_level_without_evidence_is_not_fabricated(tmp_path):
    outcome = import_har(_har_file(tmp_path, "careers.pddglobalhr.com"))
    result = outcome.result
    # site-level identity never fabricates a legal entity onto jobs
    assert all(job.company is None for job in result.jobs)
    assert result.company == "拼多多集团"


# ---- d. registry stays generic; existing semantics intact ------------------

def test_d_registry_is_generic_mapping():
    assert trusted_site_company("careers.pddglobalhr.com") == "拼多多集团"  # bare host
    assert trusted_site_company("https://careers.byd.com/x") == "比亚迪"
    assert trusted_site_company("https://auto.geely.com/x") == "吉利"
    assert trusted_site_company("https://www.zte.com.cn/x") == "中兴"
    # hosted platform domains with no verified entry never guess
    assert trusted_site_company("https://app.mokahr.com/x") is None
    assert trusted_site_company(None) is None
    # the existing formal-name passthrough is untouched
    assert formal_company_name("COSCO SHIPPING", "https://coscoshipping.iguopin.com") == "COSCO SHIPPING"
    assert "pdd" in KNOWN_COMPANY_NAMES
