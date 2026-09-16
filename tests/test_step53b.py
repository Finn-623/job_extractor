"""Synthetic semantic-recognition coverage for STEP 53B."""
import pytest
from job_extractor.discovery.scorer import score_list

def score(payload, url="https://jobs.test/api/position/list"):
    return score_list(url, payload)

@pytest.mark.parametrize("records", [
    [{"jobAdId":"1","jobAdName":"Engineer","workPlaceStr":"SH"},{"jobAdId":"2","jobAdName":"Designer","workPlaceStr":"BJ"}],
    [{"positionCode":"p1","positionName":"Engineer","postTypeName":"R&D"},{"positionCode":"p2","positionName":"Designer","postTypeName":"Design"}],
    [{"postId":"1","postName":"Engineer","postTypeName":"R&D","workPlaceStr":"SH"},{"postId":"2","postName":"Designer","postTypeName":"Design","workPlaceStr":"BJ"}],
])
def test_nonstandard_job_identity_and_title_aliases_are_high(records):
    value, _evidence, shape = score({"data":{"records":records}})
    assert value >= 15 and not shape["rejection_reasons"] and shape["job_entity_density"] == 1.0

def test_nested_job_object_is_bounded_and_recognized():
    value, _evidence, shape = score({"data":{"rows":[{"job":{"postId":"1","postName":"Engineer"},"location":{"name":"SH"}},{"job":{"postId":"2","postName":"Designer"},"location":{"name":"BJ"}}]}})
    # The bounded array selection is stable and recognizes nested job identity.
    assert shape["candidate_list_path"] == "data.rows" and value >= 15 and not shape["rejection_reasons"]

def test_city_and_department_id_name_arrays_remain_rejected():
    for rows in ([{"id":"1","name":"Shanghai","cityCode":"SH"},{"id":"2","name":"Beijing","cityCode":"BJ"}], [{"id":"1","name":"R&D","parentId":None},{"id":"2","name":"Sales","parentId":"1"}]):
        _value, _evidence, shape = score({"data":rows},"https://jobs.test/api/filters")
        assert shape["rejection_reasons"]

def test_announcement_and_mixed_arrays_remain_rejected():
    for payload in ({"data":[{"id":"1","title":"Announcement","body":"news"},{"id":"2","title":"Notice","body":"news"}]}, {"data":[{"postId":"1","postName":"Engineer"},{"label":"Shanghai","value":"SH"}]}):
        _value, _evidence, shape = score(payload,"https://jobs.test/api/announcement")
        assert shape["rejection_reasons"]
