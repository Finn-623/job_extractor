import pytest

from job_extractor.url_utils import normalize_url

def test_normalize_url_trims_and_lowercases_scheme_and_hostname():
    assert normalize_url("  HTTPS://LEAPMOTOR1.ZHIYE.COM/campus/jobs  ") == "https://leapmotor1.zhiye.com/campus/jobs"

def test_normalize_url_rejects_non_http_url():
    with pytest.raises(ValueError):
        normalize_url("ftp://example.com/jobs")
