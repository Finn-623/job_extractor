from __future__ import annotations
import json,re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin,urlsplit,urlunsplit

DETAIL_URL_FIELDS=("url","absolute_url","job_url","jobUrl","detail_url","detailUrl","apply_url","applyUrl","click_url","clickUrl","path","slug")

RESPONSIBILITY_HEADINGS=("responsibilities","job responsibilities","position responsibilities","role responsibilities","what you'll do","what you will do","岗位描述","岗位职责","工作职责","职位职责","职责描述")
REQUIREMENT_HEADINGS=("requirements","qualifications","job requirements","position requirements","what we're looking for","what we are looking for","任职要求","任职资格","职位要求","岗位要求","任职条件")
DETAIL_END_HEADINGS=("工作地点","招聘部门","公司介绍","部门介绍","about the company","about us","apply for this job","相关职位","推荐职位")

class _SemanticTextParser(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.lines=[];self.buffer=[];self.skip=0
    def _flush(self):
        value=re.sub(r"\s+"," ","".join(self.buffer)).strip();self.buffer=[]
        if value:self.lines.append(value)
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style","noscript","svg","header","footer","nav"):self._flush();self.skip+=1
        elif not self.skip and tag in ("br","p","div","li","section","article","h1","h2","h3","h4","h5"):self._flush()
    def handle_endtag(self,tag):
        if tag in ("script","style","noscript","svg","header","footer","nav") and self.skip:self.skip-=1
        elif not self.skip and tag in ("p","div","li","section","article","h1","h2","h3","h4","h5"):self._flush()
    def handle_data(self,data):
        if not self.skip:self.buffer.append(data)
    def close(self):super().close();self._flush()

def _heading(value:str)->str:return re.sub(r"[\s:：]+$","",value).strip().lower()

def _path_bound_ids(url:str|None)->list[tuple[str,str]]:
    if not url:return []
    parsed=urlsplit(url)
    found=[]
    for key,value in __import__("urllib.parse",fromlist=["parse_qsl"]).parse_qsl(parsed.query):
        if key.lower() in ("rid","id","jobid","job_id","positionid","requisitionid") and value:found.append((key.lower(),value))
    parts=[x for x in parsed.path.split("/") if x]
    for index,value in enumerate(parts[:-1]):
        if value.lower() in ("rid","id","job","jobid","position","positionid","requisition","requisitionid"):
            found.append((value.lower(),parts[index+1].removesuffix(".html")))
    fragment_match=re.search(r"(?i)#/(?:job|jobs)/([^/?#]+)",url)
    if fragment_match:found.append(("job",fragment_match.group(1)))
    return found

def semantic_html_detail(html:str,expected_title:str|None=None,expected_id:str|None=None,detail_url:str|None=None)->dict[str,Any]:
    parser=_SemanticTextParser()
    try:parser.feed(html or "");parser.close()
    except Exception:return {"failure":"PARSE_FAILED"}
    lines=parser.lines;normalized=[_heading(x) for x in lines]
    resp=next((i for i,x in enumerate(normalized) if x in RESPONSIBILITY_HEADINGS),None)
    req=next((i for i,x in enumerate(normalized) if x in REQUIREMENT_HEADINGS and (resp is None or i>resp)),None)
    if resp is None:return {"failure":"JD_CONTAINER_NOT_FOUND","lines":lines}
    end=next((i for i in range((req if req is not None else resp)+1,len(lines)) if normalized[i] in DETAIL_END_HEADINGS),len(lines))
    responsibilities=lines[resp+1:(req if req is not None else end)];requirements=lines[req+1:end] if req is not None else []
    full_lines=[lines[resp],*responsibilities]+([lines[req],*requirements] if req is not None else [])
    full_jd="\n".join(full_lines).strip()
    if expected_title and not any(expected_title.strip().lower()==x or expected_title.strip().lower() in x for x in normalized[:resp]):return {"failure":"TITLE_MISMATCH","lines":lines}
    bound_ids=_path_bound_ids(detail_url);expected=str(expected_id) if expected_id is not None else None
    if expected and bound_ids and expected not in {value for _,value in bound_ids}:return {"failure":"TITLE_MISMATCH","lines":lines,"bound_id":bound_ids[0][1]}
    bound_id=expected if expected and any(value==expected for _,value in bound_ids) else (bound_ids[0][1] if bound_ids else None)
    if len(re.sub(r"\s+","",full_jd))<100:return {"failure":"JD_TOO_SHORT","lines":lines}
    return {"failure":None,"full_jd":full_jd,"responsibilities":responsibilities,"requirements":requirements,"bound_id":bound_id,"title_match":bool(expected_title),"source_type":"DOM_DETAIL"}
def route_changed(before:str,after:str)->bool:return bool(after and before!=after)
def real_url_candidates(item:dict[str,Any],base_url:str,max_depth:int=3)->list[tuple[str,str]]:
    from urllib.parse import urljoin,urlsplit
    found=[]
    def walk(value:Any,path:str="",depth:int=0):
        if depth>max_depth or not isinstance(value,dict):return
        ordered=sorted(value,key=lambda key:(str(key) not in DETAIL_URL_FIELDS,str(key)))
        for key in ordered:
            child=value[key];child_path=f"{path}.{key}" if path else str(key)
            if str(key) in DETAIL_URL_FIELDS and isinstance(child,str) and child.strip():
                resolved=urljoin(base_url,child.strip())
                parsed=urlsplit(resolved)
                if parsed.scheme in ("http","https") and parsed.netloc and resolved!=base_url:found.append((resolved,child_path))
            if isinstance(child,dict):walk(child,child_path,depth+1)
    walk(item)
    return list(dict.fromkeys(found))
def first_real_url(item:dict[str,Any],base_url:str)->tuple[str|None,str|None]:
    found=real_url_candidates(item,base_url)
    return found[0] if found else (None,None)

GENERIC_COMPANY_HEADINGS={"all openings","jobs","careers","open positions","all jobs","job search","search jobs"}
def company_name_or_none(value:str|None)->str|None:
    if not value:return None
    cleaned=re.sub(r"\s+"," ",value).strip(" -|–—")
    if cleaned.lower() in GENERIC_COMPANY_HEADINGS:return None
    return cleaned or None

def filter_detail_links(entries:list[tuple[str,str]],source_url:str,structural_urls:list[str]|None=None)->tuple[list[str],list[dict[str,str]]]:
    """Keep observed job-detail links and record safe rejection metadata."""
    source=urlsplit(source_url);source_page=urlunsplit((source.scheme,source.netloc,source.path.rstrip("/"),"",""))
    structural=set(structural_urls or []);accepted=[];rejected=[]
    generic_labels={"jobs","all jobs","careers","open positions","search jobs","job search","see details","read more","view jobs"}
    for raw,label in entries:
        value=(raw or "").strip();resolved=urljoin(source_url,value);parsed=urlsplit(resolved)
        normalized=urlunsplit((parsed.scheme,parsed.netloc,parsed.path.rstrip("/"),"",""));low=(parsed.path+" "+label).lower()
        reason=None
        job_fragment=bool(re.search(r"(?i)#/(?:job|jobs)/[0-9a-z][0-9a-z-]{3,}",value))
        if not value or (value.startswith("#") and not job_fragment) or (normalized==source_page and not job_fragment):reason="SAME_PAGE_OR_FRAGMENT"
        elif parsed.scheme!="https":reason="NON_HTTPS_LINK"
        elif any(x in low for x in ("privacy","legal","talent-community","talent community","newsletter","cookie")):reason="NON_JOB_CONTENT"
        elif re.search(r"(?i)/(?:searchjobs|job-search|search-results)(?:/|$)",parsed.path) or ("search" in parsed.path.lower() and not re.search(r"\d{3,}",parsed.path)):reason="SEARCH_OR_LIST_PAGE"
        elif not job_fragment and re.fullmatch(r"(?i)/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:jobs|positions|careers|openings)?/?",parsed.path):reason="LANDING_OR_LOCALE_PAGE"
        else:
            identity=bool(re.search(r"(?i)(?:/details?/|/job/|/jobs/|/position/|/positions/|/opening/).*(?:[0-9]{3,}|[0-9a-f]{8}-[0-9a-f-]{27,}|jid-)",parsed.path))
            semantic_label=3<=len(label.strip())<=200 and label.strip().lower() not in generic_labels
            if not identity and not job_fragment and not (resolved in structural and semantic_label):reason="LOW_DETAIL_LINK_EVIDENCE"
        if reason:rejected.append({"url":resolved,"reason":reason})
        else:accepted.append(resolved)
    return list(dict.fromkeys(accepted)),rejected

def related_detail_hosts(source_url:str,links:list[str])->list[str]:
    source_host=(urlsplit(source_url).hostname or "").lower();counts={}
    for link in links:
        parsed=urlsplit(link);host=(parsed.hostname or "").lower()
        if parsed.scheme=="https" and host and host!=source_host:counts[host]=counts.get(host,0)+1
    return sorted(host for host,count in counts.items() if count>=2)

def repeated_job_cards(page,base_url:str)->list[dict[str,Any]]:
    """Build one correlated job record per repeated visible card."""
    try:
        rows=page.evaluate("""() => {
          const nodes=[...document.querySelectorAll('a[href],[data-href],[role="link"]')];
          return nodes.map((node) => {
            const raw=node.getAttribute('href') || node.getAttribute('data-href') || '';
            const text=(node.innerText || node.getAttribute('aria-label') || '').trim().replace(/\\s+/g,' ');
            const parent=node.closest('li,article,[class*="job"],[class*="card"],[class*="result"]') || node.parentElement;
            const signature=[node.tagName,node.className || '',parent?.tagName || '',parent?.className || ''].join('|');
            const region=node.closest('header,footer,nav') ? 'chrome' : 'content';
            const cardText=(parent?.innerText || text).trim().replace(/\\s+/g,' ');
            const heading=parent?.querySelector('h1,h2,h3,h4,[class*="title"]');
            const location=parent?.querySelector('[class*="location"],[data-qa*="location"]');
            const department=parent?.querySelector('[class*="department"],[class*="team"]');
            const idNode=node||parent;let explicitId='';for(const attr of [...(idNode?.attributes||[])]){if(/^(data-)?(job|posting|post|requisition|req|position)[-_]?id$/i.test(attr.name)){explicitId=attr.value;break;}}
            return {raw,text,signature,region,cardText,title:(heading?.innerText||text).trim(),location:(location?.innerText||'').trim(),department:(department?.innerText||'').trim(),explicitId};
          }).filter(x => x.raw && x.text.length >= 2 && x.text.length <= 240 && x.region === 'content');
        }""")
    except Exception:return []
    if not isinstance(rows,list):return []
    from collections import defaultdict
    from urllib.parse import urljoin,urlsplit
    groups=defaultdict(list)
    for row in rows:
        if not isinstance(row,dict):continue
        resolved=urljoin(base_url,str(row.get("raw") or ""));parsed=urlsplit(resolved)
        if parsed.scheme not in ("http","https") or not parsed.netloc:continue
        low=(parsed.path+" "+str(row.get("text") or "")).lower()
        if any(x in low for x in ("locationpicker","location picker","filter","support","apply","login","sign in","privacy","cookie","pagination","locale")):continue
        enriched=dict(row);enriched["resolved"]=resolved;groups[str(row.get("signature") or "")].append(enriched)
    ranked=[]
    for signature,values in groups.items():
        by_link={x["resolved"]:x for x in values};unique=list(by_link.values())
        if len(unique)<2:continue
        path_score=sum(bool(re.search(r"(?i)(/details?/|/job/|/jobs/|/position/|/opening/|jid-|[/-]\d{3,}(?:/|$)|#/(?:job|jobs)/[0-9a-z-]{4,})",x["resolved"])) for x in unique)
        if path_score*5 < len(unique)*3:continue
        cards=[{"title":str(x.get("title") or x.get("text") or "").strip() or None,"location":str(x.get("location") or "").strip() or None,
            "department":str(x.get("department") or "").strip() or None,"metadata":str(x.get("cardText") or "")[:500] or None,
            "detail_link":x["resolved"],"apply_link":None,"structure_signature":signature,"explicit_id":str(x.get("explicitId") or "").strip() or None} for x in unique]
        ranked.append((path_score,len(cards),signature,cards))
    return max(ranked,default=(0,0,"",[]),key=lambda x:(x[0],x[1]))[3]

def repeated_job_links(page,base_url:str)->list[str]:
    return [x["detail_link"] for x in repeated_job_cards(page,base_url) if x.get("detail_link")]
def _text(page,selectors,min_length=1):
    for selector in selectors:
        try:
            loc=page.locator(selector)
            if loc.count():
                value=loc.first.inner_text().strip()
                if len(value)>=min_length:return value,selector
        except Exception:pass
    return None,None
JD_SIGNALS=("job description","description","responsibilities","requirements","qualifications","what you'll do","what you will do","what you'll bring","what you will bring","about the role","岗位职责","任职要求","任职资格")
JD_PENALTIES=("related jobs","share this job","cookie settings","privacy policy","marketing preferences")
def credible_jd(value:str|None)->bool:
    if not value:return False
    text=re.sub(r"\s+"," ",value).strip();low=text.lower()
    signals=sum(x in low for x in JD_SIGNALS);penalties=sum(x in low for x in JD_PENALTIES)
    return len(text)>=160 and (signals>penalties or len(text)>=500)
def extract_credible_jd(page,title:str|None=None,preferred_selector:str|None=None)->tuple[str|None,str|None]:
    selectors=tuple(x for x in (preferred_selector,'[data-qa*="description"]','[itemprop="description"]','[id*="job-description"]','[class*="job-description"]','[class*="jobDescription"]','[class*="description"]','.posting-page .content','main article','article','main') if x)
    candidates=[]
    for order,selector in enumerate(selectors):
        try:
            loc=page.locator(selector)
            for i in range(min(loc.count(),20)):
                node=loc.nth(i) if hasattr(loc,"nth") else loc.first
                value=node.inner_text().strip()
                if not credible_jd(value):continue
                low=value.lower();signals=sum(x in low for x in JD_SIGNALS);penalties=sum(x in low for x in JD_PENALTIES)
                title_bonus=300 if title and title.lower() in low else 0
                score=min(len(value),6000)+signals*500-penalties*700+title_bonus-order
                candidates.append((score,value,selector))
        except Exception:pass
    if not candidates:return None,None
    _,value,selector=max(candidates,key=lambda x:x[0]);return value,selector
def extract_detail_dom(page)->dict[str,Any]:
    title,title_selector=_text(page,('[data-qa*="title"]','[itemprop="title"]','.posting-headline h2','main h1','h1'))
    location,location_selector=_text(page,('[data-qa*="location"]','[itemprop="jobLocation"]','[class*="location"]'))
    department,department_selector=_text(page,('[data-qa*="department"]','[class*="department"]','[class*="team"]'))
    employment,employment_selector=_text(page,('[data-qa*="employment"]','[class*="commitment"]','[class*="employment"]'))
    jd,jd_selector=extract_credible_jd(page,title)
    work_mode=None
    try:
        body=page.locator("body").inner_text()[:30000]
        matches=re.findall(r"(?im)^\s*(remote|hybrid|on[- ]?site)\s*$",body)
        if matches:work_mode=matches[0]
    except Exception:pass
    return {"title":title,"title_selector":title_selector,"location":location,"location_selector":location_selector,
        "department":department,"department_selector":department_selector,"employment_type":employment,
        "employment_type_selector":employment_selector,"work_mode":work_mode,"jd":jd,"jd_selector":jd_selector}
# STEP96: whole-title company fallback. Structural strips only (leading join
# verbs, trailing recruitment words) — no hostname or site vocabulary.
_TITLE_COMPANY_NOISE=re.compile(r"(?i)(?:招聘|jobs|careers|position|opening|hiring|岗位|职位|官网)")
def _company_from_title(title:str|None)->str|None:
    value=(title or "").strip()
    if not value or re.search(r"\s+[|–—-]\s+",value):return None  # separator titles belong to the structured path
    value=re.sub(r"^(?:加入|join|jobs?\s+at|careers\s+at|work\s+at|welcome\s+to)\s*","",value,flags=re.I)
    value=re.sub(r"(?:\s*(?:校园招聘|社会招聘|春季招聘|秋季招聘|夏季招聘|招聘))+$","",value)
    value=value.strip(" -|–—·")
    if not value or len(value)>30 or _TITLE_COMPANY_NOISE.search(value):return None
    return company_name_or_none(value)

def extract_company(page)->str|None:
    # Structured metadata has priority over presentation metadata.
    try:
        for node in page.locator('script[type="application/ld+json"]').all():
            try:data=json.loads(node.text_content() or "{}")
            except Exception:continue
            values=data if isinstance(data,list) else [data]
            for value in values:
                if not isinstance(value,dict):continue
                org=value.get("hiringOrganization") or value.get("organization")
                if isinstance(org,dict) and isinstance(org.get("name"),str):
                    company=company_name_or_none(org["name"])
                    if company:return company
    except Exception:pass
    for selector,attr in (('meta[property="og:site_name"]',"content"),('meta[name="application-name"]',"content")):
        try:
            loc=page.locator(selector)
            if loc.count() and loc.first.get_attribute(attr):
                company=company_name_or_none(loc.first.get_attribute(attr))
                if company:return company
        except Exception:pass
    try:
        title=page.title().strip()
        parts=re.split(r"\s+[|–—-]\s+",title)
        if len(parts)>1:
            for value in (parts[-1],parts[0]):
                company=company_name_or_none(re.sub(r"(?i)\s+(?:jobs|careers)$","",value.strip()))
                if company:return company
    except Exception:pass
    try:  # STEP96: plain titles like "加入思格新能源" / "Jobs at Sigenergy"
        company=_company_from_title(page.title())
        if company:return company
    except Exception:pass
    return None
