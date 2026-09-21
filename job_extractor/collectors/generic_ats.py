from __future__ import annotations

from job_extractor.models import CollectionResult
from job_extractor.planning import CollectionPlan,CollectionPlanValidator
from job_extractor.planning.execution_contract import PlanContractError,can_dispatch,dispatch_target,gap_reasons
from job_extractor.collectors.generic_browser_api import GenericBrowserApiCollector
from job_extractor.collectors.generic_dom import GenericDomCollector
from job_extractor.collectors.generic_html import GenericHtmlCollector
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.collectors.generic_runtime_data import GenericRuntimeDataCollector
from job_extractor.collectors.generic_state import GenericSerializedStateCollector


class GenericATSCollector:
    """Dispatch a contract-validated plan to the transport collector selected by execution_mode."""
    def __init__(self, plan: CollectionPlan):
        self.plan=plan

    @classmethod
    def can_dispatch(cls,plan:CollectionPlan)->bool:
        return bool(dispatch_target(plan)) and can_dispatch(plan)

    def collect(self)->CollectionResult:
        validation=CollectionPlanValidator().validate(self.plan)
        if not validation.valid:
            raise PlanContractError(f"PLAN_VALIDATION_ERROR {','.join(validation.errors)}",execution_mode=self.plan.mode,missing_fields=list(validation.errors))
        target=dispatch_target(self.plan)
        if target is None or not can_dispatch(self.plan):
            raise PlanContractError(f"DISPATCH_UNSUPPORTED gaps={','.join(gap_reasons(self.plan))}",execution_mode=self.plan.mode,missing_fields=gap_reasons(self.plan))
        if self.plan.mode=="BROWSER_RUNTIME_DATA":
            return GenericRuntimeDataCollector(self.plan).collect()
        if self.plan.mode=="HTTP_API":
            # STEP 69: HTTP-first detail strategy with browser only as fallback
            # for challenge/teaser/parser-failure detail pages.
            from job_extractor.browser import BrowserRuntime
            direct=GenericHttpCollector(self.plan,browser_factory=BrowserRuntime).collect()
            if not any(error.startswith("PROTECTED_SOURCE") for error in direct.errors):
                return direct
            replay_plan=self.plan.model_copy(update={"mode":"BROWSER_API"})
            replay=GenericBrowserApiCollector(replay_plan,browser_factory=BrowserRuntime).collect()
            if replay.status=="COMPLETE":
                return replay
            replay.errors.append("PROTECTED_SOURCE_UNRESOLVED reason=browser session replay did not resolve protected source")
            return replay
        if self.plan.mode=="BROWSER_API":return GenericBrowserApiCollector(self.plan).collect()
        if self.plan.mode=="SERIALIZED_STATE":return GenericSerializedStateCollector(self.plan).collect()
        if self.plan.mode=="HTML":return GenericHtmlCollector(self.plan).collect()
        if self.plan.mode=="DOM":return GenericDomCollector(self.plan).collect()
        raise PlanContractError("DISPATCH_UNSUPPORTED no collector route",execution_mode=self.plan.mode,missing_fields=["EXECUTION_MODE"])
