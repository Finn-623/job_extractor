from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Callable

from job_extractor.discovery.budget import DiscoveryBudget, terminal_activation_budget
from job_extractor.discovery.models import DiscoveryResult, TerminalActivationRecord, TerminalCandidate, RecruitmentAction
from job_extractor.discovery.provider_fingerprint import fingerprint_terminal
from job_extractor.discovery.runtime_data import runtime_hook_allowed

if TYPE_CHECKING:
    from job_extractor.adapters.registry import AdapterRegistry
    from job_extractor.planning import CollectionPlan, CollectionPlanBuilder, CollectionPlanValidator, PlanValidation

CollectionPlanBuilder = None
CollectionPlanValidator = None

TRANSPORT_CAPABILITIES = ("ENCRYPTED_BROWSER_API", "PLAIN_HTTP_API", "BROWSER_API", "DOM_ONLY")


@dataclass
class TerminalActivationOutcome:
    record: TerminalActivationRecord
    discovery: DiscoveryResult | None = None
    plan: CollectionPlan | None = None
    validation: PlanValidation | None = None


def selected_downstream(result:DiscoveryResult)->TerminalCandidate|None:
    values=[candidate for candidate in result.terminal_candidates if candidate.selected and candidate.trust=="TRUSTED" and candidate.plan=="DOWNSTREAM_RECRUITMENT_PAGE" and candidate.rejected_reason is None]
    return values[0] if values else None


def _scope_compatible(expected:str,discovered:DiscoveryResult)->bool:
    value=str(discovered.detected_scope.get("recruitment_type","")).upper()
    aliases={"GRADUATE":"CAMPUS","SCHOOL":"CAMPUS","STUDENT":"CAMPUS","EXPERIENCED":"SOCIAL","INTERNSHIP":"INTERN"}
    value=aliases.get(value,value)
    return not value or expected in ("UNKNOWN","ALL_JOBS",value) or value=="ALL_JOBS"


def _primary_capability(fingerprint)->str|None:
    if fingerprint is None:return None
    return next((value for value in fingerprint.capabilities if value in TRANSPORT_CAPABILITIES), (fingerprint.capabilities or [None])[0])


def _routed_adapter(registry, fingerprint, candidate:TerminalCandidate, discovery:DiscoveryResult):
    if fingerprint is None or fingerprint.confidence!="HIGH":return None
    if candidate.trust!="TRUSTED" or candidate.company_continuity!="MATCH":return None
    if not _scope_compatible(candidate.scope,discovery):return None
    return registry.route_fingerprint(fingerprint)


def activate_terminal(candidate:TerminalCandidate,discover_terminal:Callable[[str,DiscoveryBudget],DiscoveryResult],registry:AdapterRegistry,budget:DiscoveryBudget|None=None)->TerminalActivationOutcome:
    budget=budget or terminal_activation_budget();started=perf_counter();url=candidate.candidate
    adapter=registry.detect_known(url)
    if adapter:
        record=TerminalActivationRecord(status="KNOWN_ATS",terminal_url=url,resolved_url=url,scope=candidate.scope,company_continuity=candidate.company_continuity,provenance_path=candidate.provenance_path,elapsed_seconds=perf_counter()-started,budget_seconds=budget.total_seconds,selected_source=url,adapter_name=adapter.__name__,plan_confidence="HIGH",plan_valid=True,provider=adapter.platform_name,capability="KNOWN_ADAPTER",fingerprint_confidence="HIGH",routing_decision="HOSTNAME_MATCH")
        return TerminalActivationOutcome(record)
    discovery=None;plan=None;validation=None
    upstream=None
    if candidate.provenance_path and candidate.provenance_path[-1].trigger_action_id:
        record=candidate.provenance_path[-1]
        upstream=RecruitmentAction(control_id=record.trigger_action_id or "",text=record.trigger_action_text or "",target=url,action_type=record.trigger_action_type or "OTHER")
    try:
        try:discovery=discover_terminal(url,budget,upstream_entry_action=upstream)
        except TypeError:discovery=discover_terminal(url,budget)
    except Exception:
        discovery=None
        record=TerminalActivationRecord(status="ACTIVATION_TIMEOUT",terminal_url=url,resolved_url=url,scope=candidate.scope,company_continuity=candidate.company_continuity,provenance_path=candidate.provenance_path,elapsed_seconds=perf_counter()-started,budget_seconds=budget.total_seconds,failure_reason="ACTIVATION_RUNTIME_ERROR")
        return TerminalActivationOutcome(record)
    resolved=discovery.resolved_url or discovery.source_url
    fingerprint=fingerprint_terminal(discovery,resolved);discovery.provider_fingerprint=fingerprint
    capabilities=list(fingerprint.capabilities or [])
    if discovery.runtime_source is not None and runtime_hook_allowed(fingerprint,trust=candidate.trust,company_continuity=candidate.company_continuity,scope_compatible=_scope_compatible(candidate.scope,discovery)):
        discovery.runtime_source.provider=fingerprint.provider
        runtime_capability=_primary_capability(fingerprint)
        if runtime_capability:discovery.runtime_source.capability=runtime_capability
    routed=_routed_adapter(registry,fingerprint,candidate,discovery)
    adapter=registry.detect_known(resolved)
    if adapter and _scope_compatible(candidate.scope,discovery):
        status="KNOWN_ATS";selected_source=resolved;adapter_name=adapter.__name__;confidence="HIGH";valid=True;reason=None;capability="KNOWN_ADAPTER";routing_decision="HOSTNAME_MATCH"
    elif discovery.status=="BLOCKED" or discovery.failure_classification=="ACCESS_RESTRICTED":
        status="ACCESS_RESTRICTED";selected_source=None;adapter_name=None;confidence=None;valid=False;reason="ACCESS_RESTRICTED";capability=_primary_capability(fingerprint);routing_decision="ACCESS_RESTRICTED"
    else:
        builder_class=CollectionPlanBuilder
        validator_class=CollectionPlanValidator
        if builder_class is None or validator_class is None:
            from job_extractor.planning import CollectionPlanBuilder as builder_class, CollectionPlanValidator as validator_class
        plan=builder_class().build(discovery);validation=validator_class().validate(plan);probable=discovery.probable_list_api
        title_fields={str(value).lower() for value in (probable.response_shape.get("sample_field_names",[]) if probable else [])}
        evidence=bool(probable and probable.replayable and (probable.observed_list_length or 0)>=2 and (probable.observed_unique_ids or 0)>=2 and (probable.job_entity_density>=0.5 or bool(title_fields & {"title","jobtitle","positionname","name"})))
        if plan.confidence=="HIGH" and validation.valid and evidence and _scope_compatible(candidate.scope,discovery):
            status="HIGH_GENERIC_SOURCE";selected_source=probable.url;adapter_name=None;confidence=plan.confidence;valid=True;reason=None;capability=_primary_capability(fingerprint);routing_decision="GENERIC_SOURCE"
        elif budget.expired or discovery.failure_classification=="GLOBAL_DISCOVERY_TIMEOUT":
            status="ACTIVATION_TIMEOUT";selected_source=None;adapter_name=routed.__name__ if routed else None;confidence=plan.confidence;valid=False;reason="ACTIVATION_TIMEOUT";capability=_primary_capability(fingerprint)
            routing_decision="PROVIDER_FINGERPRINT_HIGH" if routed else (f"FINGERPRINT_{fingerprint.confidence}" if fingerprint.provider!="UNKNOWN" else "NO_PROVIDER")
        else:
            status="NON_EXECUTABLE_TERMINAL";selected_source=None;adapter_name=routed.__name__ if routed else None;confidence=plan.confidence;valid=False;reason="SOURCE_VALIDATION_FAILED";capability=_primary_capability(fingerprint)
            routing_decision="PROVIDER_FINGERPRINT_HIGH" if routed else (f"FINGERPRINT_{fingerprint.confidence}" if fingerprint.provider!="UNKNOWN" else "NO_PROVIDER")
    if discovery.terminal_trace:
        discovery.terminal_trace.terminal_status=status
    record=TerminalActivationRecord(status=status,terminal_url=url,resolved_url=resolved,scope=candidate.scope,company_continuity=candidate.company_continuity,provenance_path=candidate.provenance_path,elapsed_seconds=perf_counter()-started,budget_seconds=budget.total_seconds,observed_requests=discovery.network_summary.observed_requests,attributed_candidates=len(discovery.candidate_list_apis),terminal_actions_attempted=len(discovery.actions_attempted),selected_source=selected_source,adapter_name=adapter_name,plan_confidence=confidence,plan_valid=valid,failure_reason=reason,provider=fingerprint.provider,fingerprint_confidence=fingerprint.confidence,capability=capability,routing_decision=routing_decision,trace=discovery.terminal_trace)
    discovery.terminal_activation=record;discovery.terminal_reason=status
    if status in ("ACTIVATION_TIMEOUT","ACCESS_RESTRICTED","NON_EXECUTABLE_TERMINAL"):discovery.failure_classification=status
    return TerminalActivationOutcome(record,discovery,plan,validation)
