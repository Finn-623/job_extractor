from job_extractor.discovery.detector import GenericApiDetector, _Observation


JOBS = {
    "data": {
        "records": [
            {"id": str(index), "title": f"Engineer {index}", "city": "Shanghai"}
            for index in range(6)
        ]
    }
}


def _source_observation():
    return _Observation("https://careers.example.test/api/jobs", "GET", {}, {}, JOBS, "HYDRATION")


def test_source_found_early_exit_completes_stage_one_before_detail_enrichment():
    detector = GenericApiDetector()
    assert detector._stage_one_complete(
        [_source_observation()],
        {"reason": "SOURCE_FOUND_EARLY_EXIT"},
        expired=False,
    )


def test_unconfirmed_source_keeps_optional_detail_probe_eligible():
    detector = GenericApiDetector()
    config_only = _Observation(
        "https://careers.example.test/api/config", "GET", {}, {}, {"items": [{"id": "cn", "label": "Shanghai"}]}, "HYDRATION"
    )
    assert not detector._stage_one_complete(
        [config_only],
        {"reason": "SOURCE_FOUND_EARLY_EXIT"},
        expired=False,
    )


def test_source_budget_hard_stops_remaining_optional_probes():
    detector = GenericApiDetector()
    assert detector._stage_one_complete([], {}, expired=True)
