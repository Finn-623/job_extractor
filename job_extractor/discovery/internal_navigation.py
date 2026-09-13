from __future__ import annotations
import re
from dataclasses import dataclass,field
from typing import Any,Callable
from urllib.parse import urljoin,urlsplit
from job_extractor.discovery.scorer import reliable_list_candidate

STRONG_TEXT=("全部职位","职位列表","岗位列表","查看职位","搜索职位","在招职位","开放职位","招聘职位","职位机会","岗位机会","全部岗位","all jobs","view jobs","search jobs","open positions","job list","open roles")
SCOPE_TEXT=("校园招聘","校招","社会招聘","社招","实习招聘","实习生","应届","campus","intern","social","campus jobs","internships","experienced hire")
BARE_JOB_TEXT=("职位","岗位","jobs","job","position","hiring","recruit","招聘","职位解读")
NEGATIVE_TEXT=("公司介绍","关于我们","关于","企业文化","了解我们","招聘流程","流程","员工福利","福利","新闻","article","faq","常见问题","联系我们","联系","登录","登 录","注册","个人中心","简历","宣讲会","雇主品牌","雇主","品牌","隐私","下载","app","banner","esg","员工故事","人才理念","文化","反馈","帮助","support","about us","about","company","our story")
RECRUITMENT_ROUTE_TERMS=("career","careers","job","jobs","position","positions","recruit","recruitment","campus","social","trainee","trainees","intern","internship","internships","graduate","graduates","hiring","talent")
NON_RECRUITMENT_ROUTE_TERMS=("about","news","article","product","products","home","privacy","contact","marketing","brand","brands","press","investor","support")

def is_recruitment_context(url:str,scope:dict[str,Any]|None=None)->bool:
    """Gate: navigation only inside an already-confirmed recruitment business context."""
    low=(url or "").lower()
    if str((scope or {}).get("recruitment_type","")).lower() in ("campus","social","intern","graduate","experienced"):return True
    return any(term in low for term in ("career","/jobs","/job/","/recruit","/campus","/intern","/graduate","recruitment","campus-recruitment","social-recruitment","hiring","positions","加入我们","校园招聘","社会招聘","校招","社招","实习招聘"))

def is_recruitment_route_in_scope(current_url:str,target_url:str)->bool:
    """Keep navigation on the same host and inside a generic recruitment namespace."""
    current=urlsplit(current_url);target=urlsplit(target_url)
    if (current.hostname or "").lower()!=(target.hostname or "").lower():
        return False
    route=(target.fragment.split("?",1)[0] if target.fragment else target.path).lower()
    tokens={token for token in re.split(r"[^a-z0-9]+",route) if token}
    if tokens.intersection(NON_RECRUITMENT_ROUTE_TERMS):
        return False
    return bool(tokens.intersection(RECRUITMENT_ROUTE_TERMS))

@dataclass
class RouteCandidate:
    control_id:str
    text:str
    target:str|None
    score:float
    evidence:list[str]=field(default_factory=list)

def score_job_route_candidate(text:str,target:str|None,current_url:str)->tuple[float,list[str]]:
    text_low=(text or "").lower()
    evidence:list[str]=[];score=0.0
    if any(t in text_low for t in STRONG_TEXT):score+=6;evidence.append("strong job-list label")
    elif any(t in text_low for t in SCOPE_TEXT):score+=4;evidence.append("recruitment scope label")
    elif any(t in text_low for t in BARE_JOB_TEXT):score+=2;evidence.append("weak job wording")
    else:evidence.append("no job-route evidence")
    parsed_target=urlsplit(target or "");fragment=parsed_target.fragment
    path_value=(fragment.split("?",1)[0] if fragment else parsed_target.path).lower()
    if re.search(r"(?i)\bjobs?\b|\bpositions?\b",path_value):score+=6;evidence.append("target points at jobs/positions route")
    elif re.search(r"(?i)recruit|campus|social|intern|hiring",path_value):score+=4;evidence.append("target stays inside recruitment routes")
    for term in NEGATIVE_TEXT:
        if term.lower() in text_low:
            score-=6;evidence.append(f"negative: {term}")
    if target and urlsplit(target).hostname!=urlsplit(current_url).hostname:
        score-=100;evidence.append("EXTERNAL_DOMAIN")
    return score,evidence

def discover_job_route_candidates(page,base_url:str,max_candidates:int=12)->list[RouteCandidate]:
    try:
        nodes=page.evaluate("""() => [...document.querySelectorAll('a[href],button,[role=button],[role=tab],[data-href],[data-route],[data-url]')].map((node,index) => ({
            id:String(node.dataset && node.dataset.controlId || index), tag:node.tagName.toLowerCase(),
            text:(node.innerText||'').trim().replace(/\\s+/g,' ').slice(0,40),
            href:node.getAttribute('href')||'', data_route:node.getAttribute('data-route')||'',
            data_url:node.getAttribute('data-url')||'', data_href:node.getAttribute('data-href')||'',
            role:node.getAttribute('role')||'', visible:!!(node.offsetWidth||node.offsetHeight||node.getClientRects().length)
        }))""") or []
    except Exception:
        return []
    candidates=[];seen=set()
    for node in nodes:
        if not node.get("visible") or not node.get("text"):continue
        target=node.get("href") or node.get("data_route") or node.get("data_url") or node.get("data_href")
        if not target and node.get("tag")=="a":continue
        resolved=urljoin(base_url,target) if target else None
        key=(node.get("text"),resolved)
        if key in seen:continue
        seen.add(key)
        score,evidence=score_job_route_candidate(node.get("text",""),resolved,base_url)
        candidates.append(RouteCandidate(control_id=str(node.get("id")),text=str(node.get("text")),target=resolved,score=score,evidence=evidence))
    candidates.sort(key=lambda item:-item.score)
    return candidates[:max_candidates]

def activate_candidate(page,base_url:str,candidate:RouteCandidate)->bool:
    if candidate.target:
        try:page.goto(candidate.target,wait_until="domcontentloaded");return True
        except Exception:return False
    try:
        nodes=page.locator('a[href],button,[role=button],[role=tab]')
        for index in range(min(nodes.count(),500)):
            node=nodes.nth(index)
            if (node.inner_text() or "").strip()[:40]==candidate.text:
                node.click(timeout=3000);return True
        return False
    except Exception:return False

class InternalNavigationRuntime:
    """Bounded, generic in-page navigation from a recruitment landing state to the job-list runtime."""
    def __init__(self,page,base_url:str,rank_fn:Callable[[],tuple[Any,int]],observations:list|None=None,
                 max_attempts:int=3,candidate_min_score:float=4.0,
                 deadline_check:Callable[[],bool]|None=None):
        self.page=page;self.base_url=base_url;self.rank_fn=rank_fn
        self.observations=observations if observations is not None else []
        self.max_attempts=max(0,max_attempts);self.candidate_min_score=candidate_min_score
        self.deadline_check=deadline_check or (lambda:False)
        self.attempts_used=0;self.trace:list[dict]=[];self.visited_routes={base_url}
    def run_if_needed(self,max_attempts:int|None=None)->tuple[Any|None,list[dict]]:
        probable,_=self.rank_fn()
        if reliable_list_candidate(probable):return probable,[]
        return self.run(max_attempts=max_attempts)
    def run(self,max_attempts:int|None=None)->tuple[Any|None,list[dict]]:
        trace=self.trace
        attempt_budget=self.max_attempts if max_attempts is None else max(0,max_attempts)
        if not is_recruitment_context(self.base_url):
            trace.append({"reason":"NOT_RECRUITMENT_CONTEXT"})
            return None,trace
        candidates=discover_job_route_candidates(self.page,self.base_url)
        for candidate in candidates:
            if self.attempts_used>=attempt_budget:break
            if self.deadline_check():
                trace.append({"reason":"DEADLINE_EXCEEDED"})
                return None,trace
            target=candidate.target
            trace_base={"from_url":self.page.url,"candidate_text":candidate.text,"candidate_href":target or "","score":candidate.score}
            if candidate.score<self.candidate_min_score:
                trace.append({**trace_base,"action":"skip","to_url":self.page.url,"source_found":False,"reason":"CANDIDATE_SCORE_BELOW_MINIMUM"})
                continue
            if target and not is_recruitment_route_in_scope(self.base_url,target):
                trace.append({**trace_base,"action":"skip","to_url":self.page.url,"source_found":False,"reason":"OUTSIDE_RECRUITMENT_SCOPE"})
                continue
            if target and (target==self.base_url or target in self.visited_routes):
                trace.append({"from_url":self.page.url,"candidate_text":candidate.text,"candidate_href":candidate.target or "","score":candidate.score,"action":"skip","to_url":self.page.url,"source_found":False,"reason":"ALREADY_VISITED_STATE"})
                continue
            before_url=self.page.url;before_count=len(self.observations)
            action="navigate" if candidate.target else "click"
            activated=activate_candidate(self.page,self.base_url,candidate)
            self.attempts_used+=1
            if not activated:
                trace.append({"from_url":before_url,"candidate_text":candidate.text,"candidate_href":candidate.target or "","score":candidate.score,"action":action,"to_url":self.page.url,"source_found":False,"reason":"NAVIGATION_ACTION_FAILED"})
                if self.deadline_check():
                    trace.append({"reason":"DEADLINE_EXCEEDED"})
                    return None,trace
                continue
            from job_extractor.discovery.dynamic import wait_for_hydration
            wait_for_hydration(self.page,lambda: len(self.observations),timeout_ms=5000,interval_ms=250)
            self.page.wait_for_timeout(600)
            to_url=self.page.url;self.visited_routes.add(to_url)
            probable,_=self.rank_fn()
            source_found=reliable_list_candidate(probable)
            trace.append({"from_url":before_url,"candidate_text":candidate.text,"candidate_href":candidate.target or "","score":candidate.score,"action":action,"to_url":to_url,"source_found":source_found,"reason":("SOURCE_FOUND" if source_found else "NAVIGATION_COMPLETED_NO_JOB_SOURCE"),"new_network_candidates":len(self.observations)-before_count})
            if source_found:return probable,trace
            if self.deadline_check():
                trace.append({"reason":"DEADLINE_EXCEEDED"})
                return None,trace
        if self.attempts_used and not any(item.get("source_found") for item in trace):
            trace.append({"reason":"INTERNAL_JOB_NAVIGATION_EXHAUSTED"})
        elif not self.attempts_used:
            trace.append({"reason":"NO_INTERNAL_JOB_ROUTE_CANDIDATE"})
        return None,trace

def run_internal_job_navigation(page,base_url:str,observations:list,rank_fn:Callable[[],tuple[Any,int]],deadline_check:Callable[[],bool],max_attempts:int=3,candidate_min_score:float=4.0)->tuple[Any|None,list[dict]]:
    """Functional wrapper around InternalNavigationRuntime for callers that only need one shot."""
    runtime=InternalNavigationRuntime(page,base_url,rank_fn=rank_fn,observations=observations,
        max_attempts=max_attempts,candidate_min_score=candidate_min_score,deadline_check=deadline_check)
    return runtime.run()
