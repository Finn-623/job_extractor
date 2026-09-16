from job_extractor.discovery.models import ApiCandidate, DiscoveryResult, PaginationDetection
from job_extractor.discovery.network_analyzer import response_shape
from job_extractor.discovery.pagination_semantics import infer_pagination_from_schema
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator, can_dispatch
from job_extractor.planning.execution_contract import missing_fields


def candidate(fields, *, total="data.total", values=None, length=20, observed_total=202):
    return ApiCandidate(url="https://api.example.test/jobs", method="POST", score=22, confidence="HIGH", replayable=True,
        request_body_shape={"page": "int", "pageSize": "int", "portal": "string"},
        safe_request_values=values or {"page": 1, "pageSize": 20, "portal": "public"},
        response_shape={"candidate_list_path": "data.items", "sample_field_names": fields, "total_field": total},
        observed_list_length=length, observed_total=observed_total, observed_unique_ids=length, job_entity_density=1.0)


def result(item):
    return DiscoveryResult(source_url="https://careers.example.test/jobs", status="DISCOVERED", candidate_list_apis=[item], probable_list_api=item,
        detected_pagination=PaginationDetection(pagination_type="PAGE", page_param="page", page_size_param="pageSize", total_field=item.response_shape.get("total_field")))


def test_generic_role_variants_propagate_to_an_executable_dispatchable_plan():
    for id_field, title_field in (("job_id", "job_name"), ("postId", "postName"), ("Id", "JobAdName")):
        item = candidate([id_field, title_field, "description"])
        plan = CollectionPlanBuilder().build(result(item))
        assert (plan.job_id_field, plan.job_title_field) == (id_field, title_field)
        assert plan.executable and CollectionPlanValidator().validate(plan).valid and can_dispatch(plan)


def test_nested_datacount_is_a_generic_tier1_total():
    payload = {"data": {"pageForm": {"currentPage": 1, "pageSize": 12, "dataCount": 943, "pageData": [{"postId": "1", "postName": "A"}]}}}
    assert response_shape(payload)["total_field"] == "data.pageForm.dataCount"
    inferred = infer_pagination_from_schema({"currentPage": 1, "pageSize": 12}, {}, payload)
    assert inferred["total_path"] == "data.pageForm.dataCount" and inferred["total_value"] == 943


def test_total_less_than_observed_records_fails_closed_without_termination():
    item = candidate(["Id", "JobAdName"], total="Total", length=20, observed_total=0)
    plan = CollectionPlanBuilder().build(result(item))
    assert plan.mode == "HTTP_API" and not plan.executable and plan.total_conflict
    assert plan.total_field is None and "TERMINATION_STRATEGY" in missing_fields(plan)
    assert "INCONSISTENT_TOTAL_TERMINATION" in plan.warnings


def test_invalid_zero_total_uses_credible_count_fallback_for_nonempty_page():
    payload = {"Total": 0, "Count": 92, "Data": [{"Id": str(n), "JobAdName": f"Role {n}"} for n in range(20)]}
    shape = response_shape(payload)
    assert shape["total_field"] == "Count"
    inferred = infer_pagination_from_schema({"PageIndex": 0, "PageSize": 20}, {}, payload)
    assert inferred["total_path"] == "Count" and inferred["total_value"] == 92
    item = candidate(["Id", "JobAdName"], total=shape["total_field"], length=20, observed_total=92,
                     values={"page": 0, "pageSize": 20, "portal": "public"})
    plan = CollectionPlanBuilder().build(result(item))
    assert plan.executable and plan.total_field == "Count" and can_dispatch(plan)


def test_invalid_zero_total_without_alternative_remains_non_executable():
    payload = {"Total": 0, "Data": [{"Id": str(n), "JobAdName": f"Role {n}"} for n in range(20)]}
    assert response_shape(payload)["total_field"] is None


def test_existing_standard_title_and_total_contract_remain_dispatchable():
    item = candidate(["id", "title", "description"])
    plan = CollectionPlanBuilder().build(result(item))
    assert plan.mode == "HTTP_API" and plan.executable and can_dispatch(plan)
