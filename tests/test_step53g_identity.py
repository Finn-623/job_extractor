from job_extractor.identity import job_identity


def test_spa_fragment_job_id_beats_project_path_id():
    value = job_identity({}, "https://jobs.example.test/campus/acme/148948#/job/ad4c0997-32e9-43e3-bdff-8358c1d171f6")
    assert value["identity_value"] == "ad4c0997-32e9-43e3-bdff-8358c1d171f6"


def test_native_id_string_is_preserved_with_leading_zeros():
    assert job_identity({"id":"00123"})["identity_value"] == "00123"


def test_native_int_and_string_have_same_canonical_text():
    assert job_identity({"id":123})["identity_value"] == job_identity({"id":"123"})["identity_value"]


def test_different_native_ids_with_same_title_stay_distinct():
    assert job_identity({"id":"a"}, title="Engineer")["identity_value"] != job_identity({"id":"b"}, title="Engineer")["identity_value"]


def test_path_project_id_is_not_a_job_identity_without_job_route():
    assert job_identity({}, "https://jobs.example.test/campus/acme/148948")["identity_source"] == "COMPOSITE_FINGERPRINT"


def test_job_specific_path_and_query_are_recognized_but_site_id_is_not():
    assert job_identity({}, "https://jobs.example.test/job/12345")["identity_value"] == "12345"
    assert job_identity({}, "https://jobs.example.test/campus?siteId=123&jobId=abc")["identity_value"] == "abc"


def test_jobs_fragment_without_record_id_is_not_identity():
    assert job_identity({}, "https://jobs.example.test/campus#/jobs")["identity_source"] == "COMPOSITE_FINGERPRINT"
