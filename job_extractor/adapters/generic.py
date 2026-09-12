from job_extractor.adapters.base import BaseAdapter
from job_extractor.models import CollectionResult
from job_extractor.discovery import GenericApiDetector, DiscoveryResult
from job_extractor.planning import CollectionPlanBuilder,CollectionPlan
from job_extractor.collectors import GenericATSCollector

class GenericAdapter(BaseAdapter):
    platform_name = "generic"
    priority = 10000

    @classmethod
    def match(cls, url: str) -> bool:
        return True

    def collect(self, url: str) -> CollectionResult:
        raise NotImplementedError("Generic website collection is not implemented yet.")

    def discover(self,url:str) -> DiscoveryResult:
        return GenericApiDetector().discover(url)

    def discover_terminal(self,url:str,budget=None,upstream_entry_action=None) -> DiscoveryResult:
        return GenericApiDetector().discover(url,budget=budget,terminal_mode=True,upstream_entry_action=upstream_entry_action)

    def build_plan(self,result:DiscoveryResult)->CollectionPlan:return CollectionPlanBuilder().build(result)
    def execute_plan(self,plan:CollectionPlan)->CollectionResult:
        return GenericATSCollector(plan).collect()
