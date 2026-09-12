from __future__ import annotations
import re
from typing import Any
from job_extractor.discovery.dynamic import visible_total_evidence
from job_extractor.discovery.models import ListContainer,VisibleTotalEvidence

def classify_total(source_text:str,value:int,card_count:int,scoped:bool,proximity:int)->str:
    low=source_text.lower()
    if re.search(r"\bpage\s+\d+",low):return "PAGE_NUMBER"
    if re.search(r"\b\d+\s+(?:results?|jobs?)\s+(?:shown|displayed)\b",low):return "CURRENT_PAGE_COUNT"
    if re.search(r"\bshowing\s+\d+\s*(?:to|[-–—])\s*\d+\s*$",low):return "RANGE_END"
    if value<card_count:return "CURRENT_PAGE_COUNT"
    if proximity<70:return "GLOBAL_TOTAL" if scoped else "UNKNOWN_TOTAL"
    return "SCOPED_TOTAL" if scoped else "TOTAL_RESULTS"

def bind_total_candidates(raw:list[dict[str,Any]],card_count:int,scope:dict[str,Any],container_id:str)->tuple[list[VisibleTotalEvidence],int|None,bool]:
    scoped=bool(scope);evidence=[]
    for item in raw:
        text=str(item.get("text") or "");proximity=int(item.get("proximity") or 0);locator=item.get("locator")
        extracted,_=visible_total_evidence(text,locator)
        for found in extracted:
            semantics=classify_total(found["source_text"],found["value"],card_count,scoped,proximity)
            scope_words={word.lower() for value in scope.values() for nested in ([value] if not isinstance(value,dict) else value.values()) for word in re.split(r"[-_,\s]+",str(nested)) if len(word)>=3}
            if semantics=="SCOPED_TOTAL" and card_count and found["value"]>card_count*3 and not any(word in found["source_text"].lower() for word in scope_words):semantics="GLOBAL_TOTAL"
            evidence.append(VisibleTotalEvidence(**found,container_id=container_id,proximity_score=proximity,semantics=semantics))
    valid={x.value for x in evidence if x.semantics in ({"SCOPED_TOTAL"} if scoped else {"TOTAL_RESULTS"})}
    return evidence,next(iter(valid)) if len(valid)==1 else None,len(valid)>1

def list_container_evidence(page,cards:list[dict[str,Any]],scope:dict[str,Any])->ListContainer|None:
    if not cards:return None
    try:
        raw=page.evaluate("""() => {
          const controls=/location\\s*picker|filter|support|apply|privacy|cookie|locale/i;
          const links=[...document.querySelectorAll('a[href]')].filter(a=>!controls.test((a.innerText||'')+' '+a.getAttribute('href')));
          const cards=links.map(a=>a.closest('li,article,[class*="job"],[class*="card"],[class*="result"]')).filter(Boolean);
          const groups=new Map();for(const card of cards){const parent=card.parentElement;if(!parent)continue;if(!groups.has(parent))groups.set(parent,new Set());groups.get(parent).add(card);}
          const selected=[...groups.entries()].sort((a,b)=>b[1].size-a[1].size)[0];if(!selected)return null;
          const [container,set]=selected;const id=container.id?`#${container.id}`:`${container.tagName.toLowerCase()}.${String(container.className||'').trim().split(/\\s+/).slice(0,2).join('.')}`;
          const regions=[];const add=(node,proximity,label)=>{if(!node)return;const text=(node.innerText||'').trim().replace(/\\s+/g,' ');if(text.length<=300&&/\\d/.test(text)&&/(jobs?|positions?|openings?|opportunities|results?|showing|found|职位|岗位|结果)/i.test(text))regions.push({text,proximity,locator:label});};
          for(const node of container.querySelectorAll('p,span,div,[role="status"],[aria-live]'))add(node,100,`${id} descendant`);
          let prev=container.previousElementSibling;for(let i=0;i<3&&prev;i++,prev=prev.previousElementSibling)add(prev,90,`${id} preceding-sibling`);
          const section=container.closest('section,main,[role="main"],[class*="search"],[class*="result"]');if(section){for(const node of section.querySelectorAll('h1,h2,h3,p,[role="status"],[aria-live]'))if(!container.contains(node))add(node,75,`${id} section`);}
          const heading=(section||container.parentElement)?.querySelector('h1,h2,h3');
          const pagination=[...(container.parentElement||container).querySelectorAll('button,a[rel="next"],a[aria-label],[role="button"]')].map(x=>((x.innerText||'')+' '+(x.getAttribute('aria-label')||'')).trim()).filter(x=>/(next|previous|load more|show more|page\\s+\\d+)/i.test(x)).slice(0,20);
          return {container_id:id,locator:id,nearby_heading:(heading?.innerText||'').trim()||null,totals:regions,pagination_controls:pagination};
        }""")
    except Exception:raw=None
    if not isinstance(raw,dict):raw={"container_id":"repeated-job-list","locator":None,"nearby_heading":None,"totals":[],"pagination_controls":[]}
    container_id=str(raw.get("container_id") or "repeated-job-list")
    totals,bound,conflict=bind_total_candidates(raw.get("totals") or [],len(cards),scope,container_id)
    return ListContainer(container_id=container_id,locator=raw.get("locator"),job_card_count=len(cards),job_card_structure_signature=cards[0].get("structure_signature"),
        unique_titles=len({x.get("title") for x in cards if x.get("title")}),unique_detail_links=len({x.get("detail_link") for x in cards if x.get("detail_link")}),scope_context=scope,
        nearby_heading=raw.get("nearby_heading"),nearby_visible_totals=totals,pagination_controls=raw.get("pagination_controls") or [],bound_total=bound,total_conflict=conflict)
