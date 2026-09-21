from job_extractor.adapters.base import BaseAdapter
from job_extractor.adapters.feishu import FeishuAdapter
from job_extractor.adapters.generic import GenericAdapter
from job_extractor.adapters.moka import MokaAdapter
from job_extractor.adapters.registry import AdapterRegistry
from job_extractor.adapters.zhiye import ZhiyeAdapter
from job_extractor.adapters.beisen_cms import BeisenCMSCollector

default_registry = AdapterRegistry()
default_registry.register(ZhiyeAdapter)
default_registry.register(BeisenCMSCollector)
default_registry.register(MokaAdapter)
default_registry.register(FeishuAdapter)
default_registry.register(GenericAdapter)
default_registry.register_provider("moka", MokaAdapter)
default_registry.register_provider("zhiye", ZhiyeAdapter)
default_registry.register_provider("beisen_cms", BeisenCMSCollector)
default_registry.register_provider("feishu", FeishuAdapter)

__all__ = [
    "BaseAdapter", "AdapterRegistry", "ZhiyeAdapter", "MokaAdapter",
    "FeishuAdapter", "BeisenCMSCollector", "GenericAdapter", "default_registry",
]
