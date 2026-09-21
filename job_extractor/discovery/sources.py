import re
from urllib.parse import urljoin,urlsplit
from job_extractor.discovery.models import CandidateSource,RecruitmentAction,RecruitmentEntry

ENTRY_TERMS={
    "CAMPUS":("校园招聘","校招","campus"),
    "SOCIAL":("社会招聘","社招","social","experienced"),
    "INTERN":("实习招聘","实习生","实习","intern"),
    "ALL_JOBS":("招聘职位","职位列表","岗位列表","职位机会","岗位机会","查看职位","全部职位","搜索职位","all jobs","job opportunities"),
    "OVERSEAS":("海外招聘","海外职位","overseas"),
}
NEGATIVE_TERMS=("news","article","brand","privacy","login","event","marketing","tracking","analytics","socialresponsibility","社会责任","新闻","文章","活动")


def trigger_job_page_search(page, node_selector: str = 'a,button,[role="button"],[role="menuitem"],[data-route],li') -> list[str]:
    """Activate one visible, job-semantic control without portal-specific rules."""
    script="""()=>Array.from(document.querySelectorAll('a,button,[role="button"],[role="menuitem"],[data-route],li')).map((n,i)=>({i,text:(n.innerText||n.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' '),href:(n.getAttribute&&(n.getAttribute('href')||n.getAttribute('data-route')))||'',active:n.getAttribute('aria-current')==='page'||/(^|\\s)is-active(\\s|$)|(^|\\s)active(\\s|$)/.test(n.className||'')}))"""
    try:
        nodes=page.evaluate(script)
    except Exception:
        return []
    if not isinstance(nodes,list):
        return []
    positive=("全部职位","职位列表","在招职位","查看职位","搜索职位","职位搜索","校园招聘","社会招聘","招聘职位","职位机会","岗位投递","立即投递","投递职位","campus jobs","campus recruitment","campus","all jobs","view jobs","search jobs","职位")
    negative=("登录","注册","隐私","筛选","filter","下一页","上一页","客服","投递","首页")
    ranked=[]
    for node in nodes:
        if not isinstance(node,dict) or node.get("active"):
            continue
        text=str(node.get("text") or "").strip();value=(text+" "+str(node.get("href") or "")).lower()
        if not text or len(text)>40 or (not any(term in text for term in ("立即投递","岗位投递","投递职位")) and any(x.lower() in value for x in negative)):
            continue
        score=sum(4 for term in positive if term.lower() in value)
        if score>0:ranked.append((score,node))
    ranked.sort(key=lambda item:-item[0]);clicked=[]
    for _score,node in ranked[:1]:
        try:
            page.locator(node_selector).nth(int(node["i"])).click(timeout=3000)
            page.wait_for_timeout(2000);clicked.append(str(node.get("text"))[:40])
        except Exception:
            pass
    return clicked

def rank_spa_action(text:str,target:str|None="",scope:str="UNKNOWN")->int:
    value=f"{text} {target or ''}".lower()
    positive=("全部职位","招聘职位","职位列表","查看职位","在招职位","岗位","职位","jobs","careers","recruitment","campus","social","intern")
    negative=("esg","员工发展","人才理念","福利","新闻","品牌","隐私","登录","活动")
    score=sum(6 for term in positive if term.lower() in value)-sum(8 for term in negative if term.lower() in value)
    if scope and scope != "UNKNOWN":
        if scope == "CAMPUS" and any(term in value for term in ("校园", "campus")):score+=8
        if scope == "SOCIAL" and any(term in value for term in ("社会", "social")):score+=8
    return score

def classify_action_result(*, route_changed:bool=False, dom_changed:bool=False, job_source:bool=False)->str:
    if job_source and route_changed:return "JOB_SOURCE_FOUND"
    if route_changed or dom_changed:return "USEFUL_ROUTE_CHANGE"
    return "NO_PROGRESS"

def select_terminal_actions(actions:list[RecruitmentAction])->list[RecruitmentAction]:
    return sorted((action for action in actions if action.score >= 18),key=lambda action:(-action.score,action.control_id))[:2]

def classify_request(url:str)->str:
    value=(url or "").lower()
    if any(term in value for term in ("/pixel/","/tracking","/track")):return "TRACKING"
    if any(term in value for term in ("sentry","telemetry","monitor","beacon")):return "TELEMETRY"
    if any(term in value for term in ("analytics","pixel","tracking")):return "ANALYTICS"
    if any(term in value for term in ("locale","i18n","translation")):return "TRANSLATION"
    if any(term in value for term in ("/job","/jobs","position","recruit","career")):return "RECRUITMENT_RELEVANT"
    if any(term in value for term in (".mp4",".webm","image","font","video")):return "MEDIA"
    return "OTHER"

def classify_discovery_failure(*, action_count:int=0, attempt_results:tuple[str,...]=(), **_:object)->str|None:
    if action_count and action_count >= 3 and len(attempt_results) >= action_count and all(value == "NO_PROGRESS" for value in attempt_results):
        return "ACTION_BUDGET_EXHAUSTED"
    return None

def spa_action_inventory(page,base_url:str,scope:str="UNKNOWN")->list[RecruitmentAction]:
    values=[]
    try:
        nodes=page.evaluate("""() => Array.from(document.querySelectorAll('button,[role=button],[data-route],[data-url]')).map((node,index) => ({
            control_id:String(node.dataset.controlId || index), tag:node.tagName.toLowerCase(), text:(node.innerText || '').trim(),
            href:node.getAttribute('href') || '', data_route:node.getAttribute('data-route') || '', data_url:node.getAttribute('data-url') || '',
            onclick:node.getAttribute('onclick') || '', role:node.getAttribute('role') || '', visible:!!(node.offsetWidth || node.offsetHeight || node.getClientRects().length)
        }))""") or []
        for node in nodes:
            if not node.get("visible") or not node.get("text"):continue
            target=node.get("href") or node.get("data_route") or node.get("data_url") or None
            values.append(RecruitmentAction(control_id=str(node.get("control_id")),text=node["text"],target=target,action_type="BUTTON",scope_semantics=scope,job_semantics=["JOB"] if rank_spa_action(node["text"],target)>0 else [],navigation_confidence="HIGH" if rank_spa_action(node["text"],target)>=18 else "LOW",source_element=node.get("tag"),href=node.get("href"),onclick=node.get("onclick"),role=node.get("role"),data_route=node.get("data_route"),data_url=node.get("data_url"),score=rank_spa_action(node["text"],target)))
    except Exception:
        pass
    return values

def _entry_type(text:str,url:str)->str|None:
    if not text.strip():return None
    value=f"{text} {url}".lower()
    for kind in ("CAMPUS","SOCIAL","INTERN","OVERSEAS","ALL_JOBS"):
        if any(term.lower() in value for term in ENTRY_TERMS[kind]):return kind
    return None

def recruitment_entries(page,base_url:str)->list[RecruitmentEntry]:
    found=[];seen=set()
    try:
        links=page.locator('a[href],button,[role="link"],[data-href]')
        for index in range(min(links.count(),500)):
            node=links.nth(index);text=(node.inner_text() or "").strip();href=node.get_attribute("href") or node.get_attribute("data-href") or ""
            onclick=node.get_attribute("onclick") or ""
            if not href and onclick:
                match=re.search(r"(?:https?://|/)[^'\\\"]+",onclick);href=match.group(0) if match else ""
            url=urljoin(base_url,href);kind=_entry_type(text,url);low=f"{text} {url}".lower()
            if not kind or not text or any(term in low for term in NEGATIVE_TERMS) or urlsplit(url).scheme!="https" or url in seen:continue
            seen.add(url);found.append(RecruitmentEntry(entry_type=kind,text=text,url=url,destination_host=urlsplit(url).hostname or "",evidence=["visible recruitment entry text","official page supplied destination"]))
    except Exception:pass
    return found

def embedded_sources(page,base_url:str)->list[CandidateSource]:
    found=[]
    try:
        frames=page.locator("iframe[src]")
        for index in range(min(frames.count(),50)):
            src=urljoin(base_url,frames.nth(index).get_attribute("src") or "");low=src.lower()
            if urlsplit(src).scheme=="https" and not any(x in low for x in ("doubleclick","analytics","tracking","advert","youtube","vimeo")):
                found.append(CandidateSource(source_type="IFRAME",url=src,evidence=["public HTTPS iframe directly embedded by official page"]))
        widgets=page.locator('[data-widget],[class*="job-widget"],script[src*="job"],script[src*="career"],script[src*="recruit"]')
        for index in range(min(widgets.count(),20)):
            node=widgets.nth(index);src=node.get_attribute("src") or node.get_attribute("data-src")
            found.append(CandidateSource(source_type="EMBEDDED_WIDGET",url=urljoin(base_url,src) if src else base_url,evidence=["recruitment-like embedded element observed"]))
    except Exception:pass
    return found
