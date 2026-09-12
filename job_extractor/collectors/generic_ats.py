from __future__ import annotations

from job_extractor.models import CollectionResult
from job_extractor.planning import CollectionPlan,CollectionPlanValidator
from job_extractor.collectors.generic_browser_api import GenericBrowserApiCollector
from job_extractor.collectors.generic_dom import GenericDomCollector
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.collectors.generic_state import GenericSerializedStateCollector


class GenericATSCollector:
    """Execute a validated, reusable ATS profile through existing transports."""
    def __init__(self, plan: CollectionPlan):
        self.plan=plan

    def collect(self)->CollectionResult:
        validation=CollectionPlanValidator().validate(self.plan)
        if self.plan.mode=="BROWSER_RUNTIME_DATA":
            if not validation.valid:raise ValueError("PLAN_NOT_EXECUTABLE")
            return GenericRuntimeDataCollector(self.plan).collect()
        if not validation.valid or self.plan.ats_profile is None or self.plan.ats_profile.confidence!="HIGH":
            raise ValueError("GENERIC_ATS_PROFILE_NOT_EXECUTABLE")
        if self.plan.mode=="HTTP_API":return GenericHttpCollector(self.plan).collect()
        if self.plan.mode=="BROWSER_API":return GenericBrowserApiCollector(self.plan).collect()
        if self.plan.mode=="SERIALIZED_STATE":return GenericSerializedStateCollector(self.plan).collect()
        if self.plan.mode=="DOM":return GenericDomCollector(self.plan).collect()
        raise ValueError("PLAN_NOT_EXECUTABLE")