from job_extractor.discovery.rendered_list_jd import map_rendered_cards
from job_extractor.discovery.models import ApiCandidate, DiscoveryResult
from job_extractor.planning.builder import CollectionPlanBuilder


def card(identity="1", title="Engineer", duties="Build reliable systems with testing and documentation.", requirements="Bachelor degree and strong engineering communication skills."):
    return {"href": f"#/job/{identity}", "text": f"{title}\n岗位职责\n{duties}\n任职要求\n{requirements}"}


def record(identity="1", title="Engineer"):
    return {"id": identity, "title": title}


def mapped(records, cards):
    return map_rendered_cards(records, cards, id_field="id", title_field="title")


def test_stable_card_id_maps_jd():
    rows, stats = mapped([record()], [card()])
    assert stats["mapping_success"] == 1 and rows[0]["_generic_detail_source"] == "BROWSER_RENDERED_LIST"


def test_href_id_maps_jd():
    rows, stats = mapped([record("abc")], [card("abc")])
    assert stats["mapping_success"] == 1 and "岗位职责" in rows[0]["_generic_description"]


def test_runtime_and_dom_ids_map_independently():
    rows, stats = mapped([record("a", "A"), record("b", "B")], [card("b", "B"), card("a", "A")])
    assert stats["mapping_success"] == 2 and "Build" in rows[0]["_generic_description"]


def test_unique_title_can_fallback_when_no_id_is_exposed():
    rows, stats = mapped([record("a", "Unique")], [{"text": card("other", "Unique")["text"]}])
    assert stats["mapping_success"] == 1 and rows[0].get("_generic_description")


def test_duplicate_title_title_only_mapping_is_rejected():
    rows, stats = mapped([record("a", "Same"), record("b", "Same")], [{"text": card("other", "Same")["text"]}])
    assert stats["mapping_success"] == 0 and stats["mapping_failed"] == 2
    assert all("_generic_description" not in row for row in rows)


def test_card_with_only_responsibilities_is_partial_not_complete():
    rows, stats = mapped([record()], [{"href": "#/job/1", "text": "Engineer\n岗位职责\nBuild reliable systems with testing and documentation."}])
    assert stats["mapping_success"] == 0 and "_generic_description" not in rows[0]


def test_multiple_cards_do_not_cross_bind_jd():
    rows, stats = mapped([record("a", "A"), record("b", "B")], [card("a", "A", "Alpha responsibilities are fully documented with operational detail.", "Alpha requirements are fully documented with qualification detail."), card("b", "B", "Beta responsibilities are fully documented with operational detail.", "Beta requirements are fully documented with qualification detail.")])
    assert stats["mapping_success"] == 2 and "Alpha" in rows[0]["_generic_description"] and "Beta" in rows[1]["_generic_description"]


def test_page_text_outside_cards_is_not_used():
    rows, stats = mapped([record()], [{"href": "#help", "text": "岗位职责\nFooter help and contact information\n任职要求\nFooter policy text"}])
    assert stats["mapping_success"] == 0 and "_generic_description" not in rows[0]


def test_mapping_failure_preserves_existing_list_jd():
    row = record(); row["description"] = "Existing complete list description that must remain intact."
    rows, stats = mapped([row], [])
    assert stats["mapping_success"] == 0 and rows[0]["description"] == row["description"]


def test_existing_complete_list_jd_is_not_overwritten_by_unmapped_card():
    row = record(); row["description"] = "Existing complete list description that must remain intact."
    rows, _ = mapped([row], [{"href": "#/job/other", "text": "Unrelated"}])
    assert rows[0]["description"] == row["description"]


def test_list_sufficient_api_is_preferred_over_detail_required_dom_fallback():
    candidate = ApiCandidate(url="https://api.example.test/jobs", method="GET", score=20, confidence="HIGH",
        response_shape={"candidate_list_path":"data", "sample_field_names":["id", "name", "description", "qualifications"]})
    result = DiscoveryResult(source_url="https://example.test/jobs", status="DISCOVERED", candidate_list_apis=[candidate], probable_list_api=candidate,
        dom_fallback={"status":"DOM_LIST_DETECTED", "possible_detail_links":["https://example.test/job/1", "https://example.test/job/2"], "card_link_parity":"CARD_LINK_PARITY"})
    assert CollectionPlanBuilder().build(result).detail_mode == "LIST_SUFFICIENT"
