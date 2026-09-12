from job_extractor.adapters import FeishuAdapter, GenericAdapter, MokaAdapter, ZhiyeAdapter, default_registry
from job_extractor.adapters.base import BaseAdapter
from job_extractor.adapters.registry import AdapterRegistry

def test_zhiye_matches_supported_hosts():
    for url in ["https://leapmotor1.zhiye.com/campus/jobs", "https://abc.zhiye.com/jobs", "https://zhiye.com/jobs"]:
        assert ZhiyeAdapter.match(url)

def test_zhiye_rejects_false_positives():
    for url in ["https://example.com/?next=zhiye.com", "https://zhiye.com.fake.com/jobs", "https://fakezhiye.com/jobs"]:
        assert not ZhiyeAdapter.match(url)

def test_moka_matching():
    assert MokaAdapter.match("https://app.mokahr.com/campus-recruitment/test")
    assert MokaAdapter.match("https://abc.mokahr.com/jobs")
    assert not MokaAdapter.match("https://mokahr.com.fake.com/jobs")

def test_feishu_matching_is_conservative():
    assert FeishuAdapter.match("https://jobs.feishu.cn/example")
    assert FeishuAdapter.match("https://tenant.jobs.feishu.cn/example")
    assert not FeishuAdapter.match("https://feishu.cn/example")

def test_generic_fallback():
    registry = AdapterRegistry()
    assert registry.detect("https://example.com/jobs") is GenericAdapter

def test_known_detection_excludes_generic_and_resolves_ats_hosts():
    registry = default_registry
    assert registry.detect_known("https://app.mokahr.com/campus-recruitment/test") is MokaAdapter
    assert registry.detect_known("https://example.com/jobs") is None

def test_priority_selects_lower_number():
    class AdapterA(BaseAdapter):
        platform_name, priority = "a", 50
        @classmethod
        def match(cls, url): return True
        def collect(self, url): raise NotImplementedError
    class AdapterB(BaseAdapter):
        platform_name, priority = "b", 10
        @classmethod
        def match(cls, url): return True
        def collect(self, url): raise NotImplementedError
    registry = AdapterRegistry()
    registry.register(AdapterA)
    registry.register(AdapterB)
    assert registry.detect("https://example.com") is AdapterB

def test_duplicate_registration_is_ignored():
    registry = AdapterRegistry()
    registry.register(ZhiyeAdapter)
    registry.register(ZhiyeAdapter)
    assert registry.get_registered_adapters().count(ZhiyeAdapter) == 1

def test_generic_is_always_last():
    registry = AdapterRegistry()
    registry.register(GenericAdapter)
    registry.register(ZhiyeAdapter)
    assert registry.get_registered_adapters()[-1] is GenericAdapter
