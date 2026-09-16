"""Synthetic coverage for STEP 53A generic source observation."""
from job_extractor.discovery.models import CandidateSource
from job_extractor.discovery.runtime_data import build_runtime_job_source


def test_runtime_state_job_array_is_a_high_confidence_generic_source():
    source = build_runtime_job_source({"mechanism": "STATE", "arrays": [{"path": "window.store.positions", "count": 2, "sample": [{"positionId": "p1", "positionName": "Engineer"}, {"positionId": "p2", "positionName": "Designer"}]}], "totals": []})
    assert source.confidence == "HIGH"
    assert source.job_id_field == "positionId"
    assert source.job_title_field == "positionName"


def test_runtime_inventory_is_a_first_class_observation_type():
    item = CandidateSource(source_type="RUNTIME_STATE", status="CANDIDATE", score=15, confidence="HIGH")
    assert item.source_type == "RUNTIME_STATE"


def test_runtime_state_rejects_filter_like_arrays_without_job_identity():
    source = build_runtime_job_source({"mechanism": "STATE", "arrays": [{"path": "window.filters", "count": 2, "sample": [{"id": "a", "label": "Shanghai"}, {"id": "b", "label": "Beijing"}]}]})
    assert source.confidence == "LOW"
    assert not source.records
