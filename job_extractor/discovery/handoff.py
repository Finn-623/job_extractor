from __future__ import annotations

from urllib.parse import urlsplit
from typing import Callable

from job_extractor.adapters.registry import AdapterRegistry
from dataclasses import dataclass
from job_extractor.discovery.models import DiscoveryResult, RecruitmentEntry, HandoffProvenance, NavigationEdge, NavigationGraph, NavigationNode, TerminalCandidate, ProvenanceRecord
from job_extractor.planning import CollectionPlanBuilder, CollectionPlanValidator

@dataclass
class DeepNavigationResult:
    handoff: RecruitmentEntry|None
    terminal_result: DiscoveryResult
    graph: NavigationGraph

@dataclass
class _Terminal:
    entry: RecruitmentEntry|None
    result: DiscoveryResult
    scope: str
    score: int
    depth: int
    path: list[str]


def resolve_recruitment_handoff(result: DiscoveryResult, registry: AdapterRegistry) -> RecruitmentEntry|None:
    for entry in result.recruitment_entries:
        adapter = registry.detect_known(entry.url)
        if adapter is None:
            continue
        entry.adapter_platform = adapter.platform_name
        entry.adapter_name = adapter.__name__
        entry.trust_status = "TRUSTED"
        entry.destination_classification = "KNOWN_ATS"
        result.handoff = HandoffProvenance(
            source_url=result.source_url,
            portal_type=result.page_type,
            entry_type=entry.entry_type,
            entry_text=entry.text,
            entry_url=entry.url,
            destination_host=urlsplit(entry.url).hostname or "",
            detected_platform=adapter.platform_name,
            adapter_selected=adapter.__name__,
            scope_inherited=result.detected_scope,
        )
        return entry
    return None

def discover_recruitment_handoff(result: DiscoveryResult, discover: Callable[[str], DiscoveryResult], registry: AdapterRegistry) -> RecruitmentEntry|None:
    preferred=("CAMPUS","INTERN","SOCIAL","ALL_JOBS","OVERSEAS","OTHER")
    entries=sorted(result.recruitment_entries,key=lambda entry: preferred.index(entry.entry_type) if entry.entry_type in preferred else len(preferred))
    for entry in entries:
        adapter=registry.detect_known(entry.url)
        if adapter is not None:
            entry.adapter_platform=adapter.platform_name;entry.adapter_name=adapter.__name__;entry.trust_status="TRUSTED"
            entry.destination_classification="KNOWN_ATS"
            result.handoff=HandoffProvenance(source_url=result.source_url,portal_type=result.page_type,entry_type=entry.entry_type,entry_text=entry.text,entry_url=entry.url,destination_host=urlsplit(entry.url).hostname or "",detected_platform=adapter.platform_name,adapter_selected=adapter.__name__,scope_inherited=result.detected_scope)
            return entry
    for entry in entries:
        nested=discover(entry.url)
        nested_entry=resolve_recruitment_handoff(nested,registry)
        if nested_entry:
            adapter=registry.detect_known(nested_entry.url)
            if adapter is None:continue
            entry.url=nested_entry.url
            entry.destination_host=urlsplit(nested_entry.url).hostname or ""
            entry.adapter_platform=adapter.platform_name;entry.adapter_name=adapter.__name__;entry.trust_status="TRUSTED"
            entry.destination_classification="KNOWN_ATS"
            result.handoff=HandoffProvenance(source_url=result.source_url,portal_type=result.page_type,entry_type=entry.entry_type,entry_text=entry.text,entry_url=nested_entry.url,destination_host=urlsplit(nested_entry.url).hostname or "",detected_platform=adapter.platform_name,adapter_selected=adapter.__name__,scope_inherited=result.detected_scope)
            return entry
    return None

def _entry_score(entry: RecruitmentEntry) -> int:
    value=f"{entry.text} {entry.url}".lower()
    positive=("校园招聘","校招","社会招聘","社招","实习","职位","岗位","申请","查看职位","职位列表","全部职位","jobs")
    negative=("新闻","品牌","活动介绍","雇主品牌","福利介绍","员工故事","社会责任","登录","隐私")
    return sum(3 for term in positive if term in value)-sum(4 for term in negative if term in value)

def source_intent(result: DiscoveryResult) -> str:
    value=result.source_url.lower()
    if any(x in value for x in ("/campus","campus-recruitment","校园招聘","校招")):return "CAMPUS"
    if any(x in value for x in ("/social","social-recruitment","社会招聘","社招")):return "SOCIAL"
    if any(x in value for x in ("/intern","internship","实习招聘","实习生")):return "INTERN"
    detected=str(result.detected_scope.get("recruitment_type","")).lower()
    if detected in ("campus","social","intern"):return detected.upper()
    return "UNKNOWN"

def _intent_for_entry(entry: RecruitmentEntry) -> str:
    return entry.entry_type if entry.entry_type in ("CAMPUS","SOCIAL","INTERN","ALL_JOBS") else "UNKNOWN"

def _compatibility(source: str, scope: str) -> int:
    if source=="UNKNOWN":return 3 if scope=="ALL_JOBS" else 0
    if scope==source:return 12
    if scope=="ALL_JOBS":return 6
    return -20

def _company_transition(source_url:str,target_url:str,company:str|None)->tuple[bool,list[str],str]:
    source_host=(urlsplit(source_url).hostname or "").lower();target_host=(urlsplit(target_url).hostname or "").lower();target=f"{target_host}{urlsplit(target_url).path}".lower()
    if company:
        token="".join(x for x in company.lower() if x.isalnum())
        if token and token in target.replace("-",""):return True,["company name continuity"],"MATCH"
    source_parts=source_host.split(".");target_parts=target_host.split(".")
    source_core=source_parts[-2] if len(source_parts)>=2 else source_host
    if source_core and source_core in f"{target_host}{urlsplit(target_url).path}".replace("-",""):
        return True,["company domain/metadata continuity"],"MATCH"
    return False,["company host continuity mismatch"],"MISMATCH"

def _candidate_for(entry:RecruitmentEntry,root:DiscoveryResult,trust:str,reason:str|None=None,selected:bool=False)->TerminalCandidate:
    host=urlsplit(entry.url).hostname or ""
    record=ProvenanceRecord(origin_url=root.source_url,url=entry.url,host=host,trigger_action_id=entry.action_id,trigger_action_text=entry.text,trigger_action_type=entry.action_type,scope_at_origin=root.detected_scope,scope_at_node={"recruitment_type":entry.entry_type.lower()},company_context_evidence=["visible recruitment entry text"],depth=1,trust=trust,rejected_reason=reason)
    return TerminalCandidate(candidate=entry.url,provenance_path=[record],scope=entry.entry_type,company_continuity="MATCH" if trust=="TRUSTED" else "MISMATCH",trust=trust,plan="DOWNSTREAM_RECRUITMENT_PAGE",selected=selected,rejected_reason=reason,provenance_score=100 if trust=="TRUSTED" else 0,scope_score=12 if entry.entry_type==source_intent(root) else 0,job_evidence_score=10,depth=1)

def deep_recruitment_navigation(result: DiscoveryResult, discover: Callable[[str], DiscoveryResult], registry: AdapterRegistry, max_depth: int = 3, requested_scope: str|None = None, deadline_seconds: float|None = None) -> DeepNavigationResult:
    from time import perf_counter
    started=perf_counter()
    def _expired():return deadline_seconds is not None and (perf_counter()-started)>deadline_seconds
    intent=requested_scope.upper() if requested_scope else source_intent(result)
    graph=NavigationGraph(max_depth=max_depth,source_intent=intent)
    visited={result.source_url}
    current=[(result,0,None,intent,[])]
    terminal=result
    terminals=[]
    rejected_candidates=[]
    while current:
        if _expired():
            graph.terminal_reason=graph.terminal_reason or "SOURCE_DISCOVERY_TIMEOUT"
            break
        page,depth,parent,inherited,path=current.pop(0);terminal=page
        known=registry.detect_known(page.source_url)
        local=_intent_for_entry(parent) if parent else "UNKNOWN"
        effective=local if local!="UNKNOWN" else inherited
        graph.nodes.append(NavigationNode(url=page.source_url,depth=depth,page_type=page.page_type,scope=page.detected_scope,known_adapter=known.__name__ if known else None,ats_classification=page.ats_classification,job_list_evidence=page.probable_list_api is not None,inherited_intent=inherited,local_intent=local,effective_intent=effective))
        if known and parent:
            scope=_intent_for_entry(parent)
            terminals.append(_Terminal(parent,page,scope,_compatibility(intent,scope)+_entry_score(parent)-depth,depth,path+[page.source_url]))
        if page.probable_list_api and page.ats_profile and page.ats_profile.confidence=="HIGH":
            plan=CollectionPlanBuilder().build(page)
            if plan.confidence=="HIGH" and CollectionPlanValidator().validate(plan).valid:
                observed_scope=str(page.detected_scope.get("recruitment_type", "")).upper()
                scope={"GRADUATE":"CAMPUS","SCHOOL":"CAMPUS","STUDENT":"CAMPUS","EXPERIENCED":"SOCIAL"}.get(observed_scope,observed_scope or "ALL_JOBS")
                terminals.append(_Terminal(None,page,scope,_compatibility(intent,scope)-_entry_score(parent) if parent else _compatibility(intent,scope),depth,path+[page.source_url]))
        if depth>=max_depth:
            continue
        entries=sorted(page.recruitment_entries,key=_entry_score,reverse=True)
        for entry in entries:
            if _entry_score(entry)<=0 or entry.url in visited:continue
            adapter=registry.detect_known(entry.url)
            trusted,trust_evidence,continuity=_company_transition(page.source_url,entry.url,result.company)
            if adapter and not page.company and not result.company:
                trusted=True;trust_evidence=["known ATS supplied by official recruitment entry"];continuity="MATCH"
            local=_intent_for_entry(entry);effective=local if local!="UNKNOWN" else inherited;compat=_compatibility(intent,local)
            graph.edges.append(NavigationEdge(source=page.source_url,target=entry.url,entry_type=entry.entry_type,text=entry.text,score=_entry_score(entry),inherited_intent=inherited,local_intent=local,effective_intent=effective,compatibility=compat,trust="TRUSTED" if trusted else "REJECTED",trust_evidence=trust_evidence))
            if not trusted:
                rejected_candidates.append(_candidate_for(entry,result,"REJECTED","PROVENANCE_COMPANY_MISMATCH"));continue
            if _expired():
                break
            visited.add(entry.url)
            nested=DiscoveryResult(source_url=entry.url,status="PARTIAL",page_type="JOB_LIST") if adapter else discover(entry.url)
            if nested is None:continue
            current.append((nested,depth+1,entry,effective,path+[page.source_url,entry.url]))
            if adapter and intent != "UNKNOWN" and local in (intent, "ALL_JOBS"):
                break
    compatible=[x for x in terminals if x.score>-10 and not (intent!="UNKNOWN" and x.scope not in (intent,"ALL_JOBS"))]
    if intent=="UNKNOWN" and len({x.scope for x in compatible})>1 and not any(x.scope=="ALL_JOBS" for x in compatible):
        graph.available_scopes=sorted({x.scope for x in compatible});graph.terminal_reason="SCOPE_SELECTION_REQUIRED"
        return DeepNavigationResult(None,terminal,graph)
    if compatible:
        selected=max(compatible,key=lambda x:(x.score,-x.depth,1 if x.scope==intent else 0))
        graph.selected_scope=selected.scope;graph.terminal_reason="KNOWN_ATS_HANDOFF" if selected.entry else "HIGH_CONFIDENCE_GENERIC_PLAN";graph.path_explanation="Source Intent: %s; selected %s; path: %s"%(intent,selected.scope," -> ".join(selected.path))
        for terminal_item in terminals:
            terminal_entry=terminal_item.entry or RecruitmentEntry(entry_type=terminal_item.scope if terminal_item.scope in ("CAMPUS","SOCIAL","INTERN","ALL_JOBS","OVERSEAS","OTHER") else "ALL_JOBS",text="generic job source",url=terminal_item.result.source_url,destination_host=urlsplit(terminal_item.result.source_url).hostname or "")
            candidate=_candidate_for(terminal_entry,result,"TRUSTED",selected=terminal_item is selected)
            graph.terminal_candidates.append(candidate);result.terminal_candidates.append(candidate);selected.result.terminal_candidates.append(candidate)
        for alternative in terminals:
            if alternative is not selected:graph.rejected_alternatives.append(f"{alternative.scope}: SCOPE_MISMATCH_OR_LOWER_RANK")
        if selected.entry:
            adapter=registry.detect_known(selected.result.source_url)
            selected.entry.url=selected.result.source_url;selected.entry.destination_host=urlsplit(selected.result.source_url).hostname or "";selected.entry.adapter_platform=adapter.platform_name if adapter else None;selected.entry.adapter_name=adapter.__name__ if adapter else None;selected.entry.trust_status="TRUSTED";selected.entry.destination_classification="KNOWN_ATS"
            return DeepNavigationResult(selected.entry,selected.result,graph)
        return DeepNavigationResult(None,selected.result,graph)
    graph.terminal_reason="DEPTH_EXHAUSTED" if any(node.depth>=max_depth for node in graph.nodes) else "NO_RECRUITMENT_TERMINAL"
    graph.terminal_candidates.extend(rejected_candidates)
    result.terminal_candidates.extend(rejected_candidates)
    return DeepNavigationResult(None,terminal,graph)