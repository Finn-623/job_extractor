from job_extractor.collectors import detail_enrichment
from job_extractor.collectors.detail_enrichment import bind_detail, enrich_job
from job_extractor.collectors.generic_detail import jd_sections
from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.discovery.models import DiscoveryResult
from job_extractor.discovery.runtime_data import build_runtime_job_source
from job_extractor.models import Job
from job_extractor.planning import CollectionPlanBuilder

JD_HTML = "<p>岗位职责</p><p>1. Build systems</p><p>2. Ship features</p><p>任职要求</p><p>1. Python</p><p>2. Distributed systems</p>"


def make_job(job_id="uuid-1", title="Engineer", requirements=None, responsibilities=None, full_jd="original full jd", raw=None):
    return Job(job_id=job_id, job_title=title, requirements=requirements or [], responsibilities=responsibilities or [], full_jd=full_jd, source_url="https://careers.custom.test/jobs", raw_data=raw if raw is not None else {})


def test_detail_uuid_binding_rejects_mismatch():
    job = make_job(job_id="uuid-1")
    enriched, status = enrich_job(job, {"id": "uuid-9", "title": "Engineer", "jobDescription": JD_HTML})
    assert status == "BINDING_MISMATCH" and enriched.requirements == [] and enriched is job


def test_detail_title_binding_rejects_mismatch():
    job = make_job(job_id="uuid-1", title="Engineer")
    enriched, status = enrich_job(job, {"id": "uuid-1", "title": "Accountant", "jobDescription": JD_HTML})
    assert status == "BINDING_MISMATCH" and enriched.requirements == []


def test_binding_accepts_matching_detail():
    job = make_job(job_id="uuid-1", title="Engineer")
    assert bind_detail(job, {"id": "uuid-1", "title": "Engineer"}) is None


def test_structured_requirement_inference_from_html_detail():
    responsibilities, requirements = jd_sections(JD_HTML)
    assert responsibilities == ["1. Build systems", "2. Ship features"]
    assert requirements == ["1. Python", "2. Distributed systems"]
    job = make_job(job_id="uuid-1", title="Engineer")
    enriched, status = enrich_job(job, {"id": "uuid-1", "title": "Engineer", "jobDescription": JD_HTML})
    assert status == "ENRICHED"
    assert enriched.requirements == requirements
    assert enriched.responsibilities == responsibilities


def test_source_absent_requirements_is_classified_not_failed():
    job = make_job(job_id="uuid-1")
    enriched, status = enrich_job(job, {"id": "uuid-1", "title": "Engineer", "jobDescription": "We are hiring. Apply now."})
    assert status == "SOURCE_REQUIREMENTS_ABSENT" and enriched.requirements == []


def test_existing_list_requirements_are_not_overwritten():
    job = make_job(job_id="uuid-1", requirements=["existing requirement"])
    enriched, status = enrich_job(job, {"id": "uuid-1", "title": "Engineer", "jobDescription": JD_HTML})
    assert enriched.requirements == ["existing requirement"]
    assert status in ("NO_CHANGE", "RESPONSIBILITIES_IMPROVED")


def test_full_jd_is_preserved():
    job = make_job(job_id="uuid-1", full_jd="original full jd")
    enriched, _status = enrich_job(job, {"id": "uuid-1", "title": "Engineer", "jobDescription": JD_HTML})
    assert enriched.full_jd == "original full jd"


def test_extraction_failure_classification(monkeypatch):
    def boom(_value):
        raise RuntimeError("parse error")
    monkeypatch.setattr(detail_enrichment, "jd_sections", boom)
    job = make_job(job_id="uuid-1")
    enriched, status = enrich_job(job, {"id": "uuid-1", "title": "Engineer", "jobDescription": JD_HTML})
    assert status == "EXTRACTION_FAILED" and enriched.requirements == []


def test_runtime_collector_reports_enrichment_stats():
    records = [
        {"id": "a", "title": "Job A", "jobDescription": JD_HTML},
        {"id": "b", "title": "Job B", "jobDescription": "We are hiring. Apply now."},
    ]
    source = build_runtime_job_source({"mechanism": "STATE", "arrays": [{"path": "data.jobs", "count": 2, "sample": records}], "totals": [{"scalars": {"total": 2}}]}, provider="moka")
    plan = CollectionPlanBuilder().build(DiscoveryResult(source_url="https://careers.custom.test/jobs", status="PARTIAL", runtime_source=source))
    result = GenericRuntimeDataCollector(plan, observer=lambda: source).collect()
    assert result.enrichment["attempted"] == 2
    assert result.enrichment["requirements_before"] == 0
    assert result.enrichment["requirements_after"] == 1
    assert result.enrichment["requirements_filled"] == 1
    assert result.enrichment["source_absent"] == 1
    assert result.metrics.details_attempted == 2 and result.metrics.details_succeeded == 2 and result.metrics.details_failed == 0
    assert result.status == "COMPLETE"
