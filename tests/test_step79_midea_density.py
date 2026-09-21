from job_extractor.discovery.scorer import score_list


def _midea_records():
    return [{"positionId": f"p-{index}", "demandCode": f"P2026072900{index}",
             "demandPositionName": f"测试评价高级工程师 {index}", "education": "本科", "city": "佛山"}
            for index in range(5)]


def test_position_name_suffix_and_secondary_demand_code_form_job_entity():
    score, _evidence, shape = score_list("https://careers.example.test/api/position/list", {"total": 862, "data": _midea_records()})
    assert shape["job_entity_density"] == 1.0
    assert shape["inferred_job_id_field"] == "positionId"
    assert shape["inferred_job_title_field"] == "demandPositionName"
    assert "LOW_JOB_ENTITY_DENSITY" not in shape["rejection_reasons"]
    assert score >= 15


def test_generic_title_id_config_object_is_not_a_job_entity():
    _score, _evidence, shape = score_list("https://careers.example.test/api/config", {"data": [{"id": "1", "title": "首页配置"}]})
    assert "LOW_JOB_ENTITY_DENSITY" in shape["rejection_reasons"]


def test_banner_and_program_objects_remain_rejected():
    for url, payload in (
        ("https://careers.example.test/api/getMaterial", {"data": [{"id": "1", "title": "横幅", "name": "宣传"}]}),
        ("https://careers.example.test/api/programs/list", {"data": [{"id": "1", "title": "校招项目", "description": "介绍", "targetAudience": "学生", "buttons": [], "status": 1, "sortOrder": 1}]}),
    ):
        _score, _evidence, shape = score_list(url, payload)
        assert shape["rejection_reasons"]
