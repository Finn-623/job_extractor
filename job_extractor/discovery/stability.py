from __future__ import annotations

import hashlib
import json
from typing import Callable

from job_extractor.adapters.registry import AdapterRegistry
from job_extractor.discovery.handoff import DeepNavigationResult, deep_recruitment_navigation
from job_extractor.discovery.models import DiscoveryResult, DiscoveryStability


def _signature(outcome: DeepNavigationResult) -> str:
    graph=outcome.graph
    value={"page_type":outcome.terminal_result.page_type,"status":outcome.terminal_result.status,"source_intent":graph.source_intent,"terminal":graph.terminal_reason,"scope":graph.selected_scope,"handoff":outcome.handoff.url if outcome.handoff else None,"plan":bool(outcome.terminal_result.ats_profile)}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:16]

def classify_stability(signatures:list[str])->DiscoveryStability:
    unique=set(signatures)
    if len(unique)==1:return DiscoveryStability(state="STABLE",attempts=len(signatures),signatures=signatures)
    if len(unique)==2 and len(signatures)>=3:return DiscoveryStability(state="MOSTLY_STABLE",attempts=len(signatures),signatures=signatures,reason="one transient disagreement")
    return DiscoveryStability(state="UNSTABLE",attempts=len(signatures),signatures=signatures,reason="material discovery evidence disagreement")

def stable_navigation(url:str, discover:Callable[[str],DiscoveryResult], registry:AdapterRegistry, requested_scope:str|None=None, max_attempts:int=2, initial_result:DiscoveryResult|None=None, deadline_seconds:float|None=None)->tuple[DeepNavigationResult,DiscoveryStability]:
    from time import perf_counter
    started=perf_counter()
    def _remaining():return None if deadline_seconds is None else max(5.0,deadline_seconds-(perf_counter()-started))
    outcomes=[];signatures=[]
    if initial_result is not None:
        outcome=deep_recruitment_navigation(initial_result,discover,registry,requested_scope=requested_scope,deadline_seconds=_remaining());outcomes.append(outcome);signatures.append(_signature(outcome))
        if initial_result.status in ("BLOCKED","DISCOVERED"):
            initial_result.discovery_stability=classify_stability(signatures);return outcome,initial_result.discovery_stability
    for _ in range(max_attempts if initial_result is None else max(0,3-len(outcomes))):
        if deadline_seconds is not None and perf_counter()-started>deadline_seconds:
            break
        outcome=deep_recruitment_navigation(discover(url),discover,registry,requested_scope=requested_scope,deadline_seconds=_remaining())
        outcomes.append(outcome);signatures.append(_signature(outcome))
        if initial_result is not None and initial_result.status=="NOT_FOUND" and outcome.terminal_result.status=="NOT_FOUND":break
        if outcome.terminal_result.status not in ("NOT_FOUND","PARTIAL") and outcome.graph.terminal_reason not in ("NO_RECRUITMENT_TERMINAL","DEPTH_EXHAUSTED"):break
    stability=classify_stability(signatures)
    selected=outcomes[-1]
    selected.terminal_result.discovery_stability=stability
    selected.graph.stability_state=stability.state;selected.graph.stability_attempts=stability.attempts;selected.graph.stability_reason=stability.reason
    return selected,stability