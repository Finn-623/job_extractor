import pytest
from pydantic import ValidationError
from job_extractor.models import CollectionResult, Job
def make_job(title="Software Engineer"):
    return Job(job_title=title, source_url="https://example.com/jobs")
def test_job_accepts_minimum_valid_input(): assert make_job().job_title == "Software Engineer"
def test_job_requires_job_title():
    with pytest.raises(ValidationError): Job(source_url="https://example.com/jobs")
def test_job_requires_source_url():
    with pytest.raises(ValidationError): Job(job_title="Software Engineer")
def test_mutable_defaults_are_independent():
    a, b = make_job("A"), make_job("B")
    a.locations.append("Sydney"); a.responsibilities.append("Build"); a.requirements.append("Python"); a.raw_data["x"] = 1
    assert b.locations == [] and b.responsibilities == [] and b.requirements == [] and b.raw_data == {}
def test_collection_result_contains_multiple_jobs():
    result = CollectionResult(source_url="https://example.com/jobs", status="COMPLETE", jobs=[make_job("A"), make_job("B")])
    assert len(result.jobs) == 2
def test_model_dump_works(): assert make_job().model_dump()["locations"] == []
