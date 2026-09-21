from job_extractor.adapters.base import BaseAdapter

class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: list[type[BaseAdapter]] = []
        self._provider_adapters: dict[str, type[BaseAdapter]] = {}

    def register(self, adapter_class: type[BaseAdapter]) -> None:
        if adapter_class not in self._adapters:
            self._adapters.append(adapter_class)

    def register_provider(self, provider: str, adapter_class: type[BaseAdapter]) -> None:
        self._provider_adapters[provider.lower()] = adapter_class

    def provider_adapter(self, provider: str) -> type[BaseAdapter] | None:
        return self._provider_adapters.get((provider or "").lower())

    def route_fingerprint(self, fingerprint) -> type[BaseAdapter] | None:
        """Route to a provider adapter only for a HIGH-confidence fingerprint."""
        if fingerprint is None or getattr(fingerprint, "confidence", None) != "HIGH":
            return None
        return self.provider_adapter(getattr(fingerprint, "provider", ""))

    def get_registered_adapters(self) -> list[type[BaseAdapter]]:
        from job_extractor.adapters.generic import GenericAdapter
        return sorted(
            self._adapters,
            key=lambda adapter: (adapter is GenericAdapter, adapter.priority, adapter.__module__, adapter.__qualname__),
        )

    def detect(self, url: str) -> type[BaseAdapter]:
        from job_extractor.adapters.generic import GenericAdapter
        from job_extractor.adapters.beisen_cms import BeisenCMSCollector
        from job_extractor.adapters.zhiye import ZhiyeAdapter
        # CmsPortal and legacy Zhiye share a hostname pattern.  Only an
        # entrance-page fingerprint is authoritative; a failed probe preserves
        # the established legacy route.
        if ZhiyeAdapter.match(url) and BeisenCMSCollector.probe(url):
            return BeisenCMSCollector
        for adapter in self.get_registered_adapters():
            if adapter.match(url):
                return adapter
        return GenericAdapter

    def detect_known(self, url: str) -> type[BaseAdapter]|None:
        from job_extractor.adapters.generic import GenericAdapter
        for adapter in self.get_registered_adapters():
            if adapter is not GenericAdapter and adapter.match(url):
                return adapter
        return None
