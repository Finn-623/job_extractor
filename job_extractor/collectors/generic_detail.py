from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep
from typing import Any
from job_extractor.planning.models import CollectionPlan
import json
import re
from urllib.parse import urljoin,urlsplit
from job_extractor.field_semantics import pick_jd_fields
from job_extractor.browser import BrowserRuntime,BrowserRuntimeError
from job_extractor.discovery.dom_semantics import credible_jd,semantic_html_detail,_SemanticTextParser,_path_bound_ids
from job_extractor.discovery.dynamic import wait_for_dynamic_jd

RESP_HEADINGS=("responsibilities","what you'll do","what you will do","the role","岗位职责","工作职责")
REQ_HEADINGS=("requirements","qualifications","what we're looking for","what we are looking for","what you bring","任职要求","任职资格","岗位要求","资格要求")
RESP_HEADINGS+=("job responsibilities","岗位描述","职位职责")
REQ_HEADINGS+=("任职条件",)
DETAIL_FAILURE_CODES=("DETAIL_NAVIGATION_FAILED","DETAIL_SOURCE_NOT_FOUND","TITLE_MISMATCH","JD_CONTAINER_NOT_FOUND","JD_TOO_SHORT","DYNAMIC_RENDER_TIMEOUT","PARSE_FAILED","BOT_CHALLENGE")
def split_jd(full_jd:str|None)->tuple[list[str],list[str]]:
    if not full_jd:return [],[]
    lines=[x.strip() for x in full_jd.replace("\r","").split("\n") if x.strip()]
    responsibilities=[];requirements=[];target=None
    for line in lines:
        normalized=re.sub(r"[:：\s]+$","",line.lower()).strip("#* ")
        if any(normalized==x or normalized.startswith(x+" ") for x in RESP_HEADINGS):target=responsibilities;continue
        if any(normalized==x or normalized.startswith(x+" ") for x in REQ_HEADINGS):target=requirements;continue
        if target is not None:target.append(line)
    return (responsibilities,requirements) if responsibilities or requirements else ([],[])

def jd_sections(value:str|None)->tuple[list[str],list[str]]:
    """Split HTML or plain-text JD content into responsibilities/requirements sections."""
    text=value or ""
    if re.search(r"</?[a-zA-Z][^>]*>",text):
        parser=_SemanticTextParser()
        try:parser.feed(text);parser.close()
        except Exception:return [],[]
        text="\n".join(parser.lines)
    return split_jd(text)

def extract_path(value:Any,path:str|None)->Any:
    for part in (path or "").split("."):
        if not part or part=="$":continue
        if not isinstance(value,dict) or part not in value:return None
        value=value[part]
    return value


def looks_like_bot_challenge(html:str)->bool:
    """Generic anti-bot challenge probe: near-empty visible content behind a
    script that computes/sets a cookie. Purely structural — no site vocabulary."""
    if not html or len(html)>5000:return False
    visible=re.sub(r"<script.*?</script>","",html,flags=re.S)
    visible=re.sub(r"<[^>]+>","",visible).strip()
    if len(visible)>=200:return False
    return bool(re.search(r"cookie|ssid|challenge|verify",html,re.I))


def _balanced_slice(text:str,start:int)->str|None:
    open_ch=text[start];close_ch="}" if open_ch=="{" else "]"
    depth=0;quote=None;esc=False
    for i in range(start,len(text)):
        ch=text[i]
        if quote:
            if esc:esc=False
            elif ch=="\\":esc=True
            elif ch==quote:quote=None
            continue
        if ch in "\"'":quote=ch
        elif ch==open_ch:depth+=1
        elif ch==close_ch:
            depth-=1
            if depth==0:return text[start:i+1]
    return None


def _json_blobs(html:str):
    for m in re.finditer(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',html,flags=re.S|re.I):
        yield m.group(1)
    for m in re.finditer(r'window\.[A-Za-z_$][\w$]*\s*=\s*',html):
        rest=m.end()
        while rest<len(html) and html[rest] not in "{[":rest+=1
        if rest>=len(html):continue
        blob=_balanced_slice(html,rest)
        if blob:yield blob

JOB_TITLE_KEYS=("title","name","jobTitle","jobName","positionName")
JOB_LOCATION_KEYS=("location","city","cityName","workLocation","workPlace","work_city")

def _embedded_objects(node:Any,depth:int=0):
    if depth>8:return
    if isinstance(node,dict):
        try:recognized=pick_jd_fields(node)
        except Exception:recognized={}
        body=recognized.get("description")
        if (isinstance(body,str) and credible_jd(body)) or (recognized.get("responsibilities") and recognized.get("requirements")):
            yield node
        for value in node.values():yield from _embedded_objects(value,depth+1)
    elif isinstance(node,list):
        for value in node[:20]:yield from _embedded_objects(value,depth+1)

def _lines(value:Any)->list[str]:
    if value is None:return []
    if isinstance(value,list):return [x.strip() for x in value if isinstance(x,str) and x.strip()]
    return [x.strip() for x in re.split(r"[\n;；]+",str(value)) if x.strip()]

def _loose_title_match(expected:str|None,actual:str|None)->bool:
    if not expected:return True
    if not actual:return False
    norm=lambda s:re.sub(r"\s+","",s)
    return norm(expected) in norm(actual) or norm(actual) in norm(expected)

def embedded_json_detail(html:str,expected_title:str|None=None)->dict[str,Any]|None:
    """Locate a job/detail object inside serialized page state (window.__*,
    application/json). Uses only generic field-alias vocabularies — no
    site-specific keys."""
    for blob in _json_blobs(html):
        try:data=json.loads(blob)
        except Exception:continue
        for obj in _embedded_objects(data):
            try:recognized=pick_jd_fields(obj)
            except Exception:continue
            body=recognized.get("description")
            if not isinstance(body,str) or not credible_jd(body):continue
            title=next((obj.get(k) for k in JOB_TITLE_KEYS if isinstance(obj.get(k),str) and obj.get(k).strip()),None)
            if not _loose_title_match(expected_title,title):continue
            resp=recognized.get("responsibilities");req=recognized.get("requirements")
            resp_lines=_lines(resp) if resp is not None else jd_sections(body)[0]
            req_lines=_lines(req) if req is not None else jd_sections(body)[1]
            location=next((obj.get(k) for k in JOB_LOCATION_KEYS if obj.get(k) not in (None,"",[])),None)
            return {"full_jd":body,"responsibilities":resp_lines,"requirements":req_lines,
                    "source_type":"EMBEDDED_JSON","title":title,
                    "location":location if isinstance(location,(str,list)) else None}
    return None

class GenericHttpDetailCollector:
    """Executes only the explicit detail template recorded in a validated plan."""
    def __init__(self,plan:CollectionPlan,client):self.plan=plan;self.client=client
    def fetch(self,job_id:str, endpoint:str|None=None)->dict[str,Any]|None:
        # A validated plan normally supplies an observed ``{id}`` template.
        # Some list APIs expose an observed per-record detail URL instead;
        # accepting that URL keeps the trigger generic without inventing a
        # site-specific route.
        template=endpoint or self.plan.detail_endpoint_template
        if not template:return None
        url=template.format(id=job_id) if "{id}" in template else template
        response=self.client.request(self.plan.detail_method or "GET",url)
        response.raise_for_status();payload=response.json()
        detail=extract_path(payload,self.plan.detail_path) if self.plan.detail_path else payload
        return detail if isinstance(detail,dict) else None

def observed_detail_hosts(plan:CollectionPlan,raws:list[dict])->set[str]:
    """Detail hosts advertised by the observed list records themselves —
    execution evidence, never a hostname vocabulary."""
    observed:set[str]=set()
    for raw in raws:
        value=extract_path(raw,plan.detail_url_field) if plan.detail_url_field else None
        if isinstance(value,str) and value:
            host=(urlsplit(urljoin(plan.source_url,value)).hostname or "").lower()
            if host:observed.add(host)
    return observed


def resolve_detail_url(plan:CollectionPlan,raw:dict,id_fields:tuple[str,...],observed_hosts:frozenset[str]|set[str]=frozenset())->tuple[str|None,Any]:
    jid=next((extract_path(raw,k) for k in id_fields if k and extract_path(raw,k) not in (None,"")),None)
    value=extract_path(raw,plan.detail_url_field) if plan.detail_url_field else None
    if not value and plan.detail_endpoint_template and "{id}" in plan.detail_endpoint_template and jid is not None:
        value=plan.detail_endpoint_template.format(id=jid)
    if not isinstance(value,str):return None,jid
    url=urljoin(plan.source_url,value);parsed=urlsplit(url)
    source_host=(urlsplit(plan.source_url).hostname or "").lower()
    trusted={source_host,*(x.lower() for x in plan.trusted_detail_hosts),*(x.lower() for x in observed_hosts)}
    if parsed.scheme not in ("http","https") or (parsed.hostname or "").lower() not in trusted:return None,jid
    return url,jid


class GenericHtmlDetailCollector:
    """Fetches public, observed detail URLs and applies semantic HTML extraction."""
    def __init__(self,plan:CollectionPlan,client,max_workers:int=2,navigation_attempts:int=3):
        self.plan=plan;self.client=client;self.max_workers=max(1,min(max_workers,8));self.navigation_attempts=max(1,min(navigation_attempts,3))
        self._active=0;self._lock=Lock();self.max_concurrency_observed=0;self._observed_hosts:set[str]=set()
    def _url(self,raw:dict,id_fields:tuple[str,...])->tuple[str|None,Any]:
        return resolve_detail_url(self.plan,raw,id_fields,self._observed_hosts)
    def _fetch_one(self,item:tuple[int,dict],id_fields:tuple[str,...],title_fields:tuple[str,...]):
        index,raw=item;url,jid=self._url(raw,id_fields)
        if not url:return index,raw,"DETAIL_SOURCE_NOT_FOUND",jid
        title=next((extract_path(raw,k) for k in title_fields if k and isinstance(extract_path(raw,k),str)),None)
        html=None
        for attempt in range(self.navigation_attempts):
            try:
                response=self.client.request("GET",url);response.raise_for_status()
                body=response.text
            except Exception:
                if attempt+1<self.navigation_attempts:sleep(0.25*(attempt+1));continue
                return index,raw,"DETAIL_NAVIGATION_FAILED",jid
            # STEP 68: generic anti-bot challenge handling — retry with backoff;
            # never fabricate content from a challenge page.
            if looks_like_bot_challenge(body) and attempt+1<self.navigation_attempts:
                sleep(0.5*(attempt+1));continue
            html=body;break
        if html is None:return index,raw,"DETAIL_NAVIGATION_FAILED",jid
        if looks_like_bot_challenge(html):return index,raw,"BOT_CHALLENGE",jid
        # STEP 68: pass the id-bound check only when the list id plausibly
        # appears in the URL's path-bound tokens (generic identity guard, no
        # site vocabulary). Otherwise skip id assertion and rely on title.
        url_tokens={value for _,value in _path_bound_ids(url)}
        expected_id=str(jid) if jid is not None and str(jid) in url_tokens else None
        detail=semantic_html_detail(html,title,expected_id,url)
        # STEP 68: embedded serialized-state fallback — shells whose body lives
        # in window.__*/application/json. Winner is the more complete credible
        # body; generic alias vocabularies only.
        embedded=embedded_json_detail(html,title)
        if embedded and (detail.get("failure") or len(embedded["full_jd"])>len(detail.get("full_jd") or "")):
            detail=embedded
        failure=detail.get("failure")
        if failure:return index,raw,failure if failure in DETAIL_FAILURE_CODES else "PARSE_FAILED",jid
        if not credible_jd(detail.get("full_jd")):return index,raw,"JD_TOO_SHORT",jid
        enriched=dict(raw);enriched.update({"_generic_description":detail["full_jd"],"_generic_responsibilities":detail["responsibilities"],"_generic_requirements":detail["requirements"],"_generic_detail_url":url,"_generic_detail_source":detail["source_type"],"_generic_bound_id":detail.get("bound_id")})
        if isinstance(detail.get("location"),str) and detail["location"].strip():enriched["_generic_location"]=detail["location"].strip()
        return index,enriched,None,jid
    def _fetch(self,item:tuple[int,dict],id_fields:tuple[str,...],title_fields:tuple[str,...]):
        with self._lock:
            self._active+=1;self.max_concurrency_observed=max(self.max_concurrency_observed,self._active)
        try:return self._fetch_one(item,id_fields,title_fields)
        finally:
            with self._lock:self._active-=1
    def enrich(self,raws:list[dict],id_fields:tuple[str,...],title_fields:tuple[str,...])->tuple[list[dict],int,list[tuple[str,Any]]]:
        # STEP 68: detail URLs advertised by the observed list response itself
        # are execution evidence — their hosts join the trusted set. No
        # hostname vocabulary; hosts come from the plan's own detail field.
        self._observed_hosts=observed_detail_hosts(self.plan,raws)
        headers=getattr(self.client,"headers",None)
        if headers is not None and not headers.get("User-Agent"):
            headers["User-Agent"]="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            headers["Accept-Language"]="zh-CN,zh;q=0.9"
        failures=[];succeeded=0
        with ThreadPoolExecutor(max_workers=min(self.max_workers,len(raws) or 1)) as pool:
            results=pool.map(lambda item:self._fetch(item,id_fields,title_fields),enumerate(raws))
            for index,raw,failure,jid in results:
                if failure:failures.append((failure,jid))
                else:raws[index]=raw;succeeded+=1
        return raws,succeeded,failures


# STEP 69: browser detail fallback — plain HTTP stays the default path; the
# browser only opens pages the HTTP resolver could not complete (anti-bot
# challenge, teaser-only shell, parser failure). It reads the rendered DOM
# first, then the page's own XHR/fetch JSON. Challenges are never bypassed:
# an unrenderable page fails closed as BROWSER_BLOCKED.
BROWSER_FALLBACK_CODES=("BOT_CHALLENGE","JD_CONTAINER_NOT_FOUND","JD_TOO_SHORT","PARSE_FAILED")
TEASER_FLOOR=500  # non-whitespace chars below which an HTTP JD may be teaser-only
MAX_NETWORK_BODY_CHARS=2_000_000
MAX_CAPTURED_RESPONSES=50


def _looks_teaser_only(raw:dict,floor:int)->bool:
    """HTTP returned a credible but very short body AND no resp/req sections:
    the full JD likely needs XHR hydration (STEP68 audit: 272-char teaser)."""
    body=raw.get("_generic_description")
    if not isinstance(body,str) or not body or len(re.sub(r"\s+","",body))>=floor:return False
    has_sections=bool(raw.get("_generic_responsibilities") or raw.get("_generic_requirements"))
    return not has_sections


def network_json_detail(text:str,expected_title:str|None=None)->dict[str,Any]|None:
    """First HIGH-confidence job-detail object inside one XHR/fetch JSON body:
    credible JD body + loose title match. Generic alias vocabulary only."""
    try:data=json.loads(text)
    except Exception:return None
    for obj in _embedded_objects(data):
        try:recognized=pick_jd_fields(obj)
        except Exception:continue
        body=recognized.get("description")
        if not isinstance(body,str) or not credible_jd(body):continue
        title=next((obj.get(k) for k in JOB_TITLE_KEYS if isinstance(obj.get(k),str) and obj.get(k).strip()),None)
        if not _loose_title_match(expected_title,title):continue
        resp=recognized.get("responsibilities");req=recognized.get("requirements")
        resp_lines=_lines(resp) if resp is not None else jd_sections(body)[0]
        req_lines=_lines(req) if req is not None else jd_sections(body)[1]
        location=next((obj.get(k) for k in JOB_LOCATION_KEYS if obj.get(k) not in (None,"",[])),None)
        return {"full_jd":body,"responsibilities":resp_lines,"requirements":req_lines,
                "source_type":"BROWSER_NETWORK_JSON","title":title,
                "location":location if isinstance(location,(str,list)) else None}
    return None


class GenericBrowserDetailFallback:
    """Browser fallback for detail resolution, sharing the project's existing
    BrowserRuntime session. Budget guards: exactly one navigation per job,
    per-job timeout, cap on total browser jobs, one session for the batch."""
    def __init__(self,plan:CollectionPlan,browser_factory=BrowserRuntime,timeout_ms:int=30000,
                 max_browser_jobs:int=50,hydration_ms:int=6000,poll_ms:int=300,teaser_floor:int=TEASER_FLOOR):
        self.plan=plan;self.browser_factory=browser_factory;self.timeout_ms=timeout_ms
        self.max_browser_jobs=max(0,max_browser_jobs);self.hydration_ms=hydration_ms
        self.poll_ms=poll_ms;self.teaser_floor=teaser_floor;self.navigations_used=0
        self.counters:dict[str,Any]={}  # STEP 70: runtime accounting for metrics
    def _targets(self,raws:list[dict],failures:list[tuple[str,Any]],id_fields:tuple[str,...])->list[tuple[int,str]]:
        by_id:dict[str,int]={}
        for index,raw in enumerate(raws):
            jid=next((extract_path(raw,k) for k in id_fields if k and extract_path(raw,k) not in (None,"")),None)
            if jid is not None:by_id.setdefault(str(jid),index)
        targets=[];seen:set[int]=set()
        for code,jid in failures:
            if code not in BROWSER_FALLBACK_CODES:continue
            index=by_id.get(str(jid))
            if index is None or index in seen:continue
            seen.add(index);targets.append((index,code))
        # HTTP "resolved" but teaser-only: the rendered page may hold the full JD.
        for index,raw in enumerate(raws):
            if index in seen:continue
            if _looks_teaser_only(raw,self.teaser_floor):
                targets.append((index,"HTTP_DETAIL_INCOMPLETE"))
        return targets
    def _resolve_in_browser(self,page,url:str,title:str|None,expected_id:str|None)->tuple[dict[str,Any]|None,str|None]:
        captured:list[str]=[]
        def on_response(response)->None:
            try:
                if len(captured)>=MAX_CAPTURED_RESPONSES:return
                ctype=(response.headers or {}).get("content-type","")
                if "json" not in ctype.lower() or not (200<=response.status<300):return
                body=response.text()
                if body and len(body)<=MAX_NETWORK_BODY_CHARS:captured.append(body)
            except Exception:pass
        page.on("response",on_response)
        try:
            try:page.goto(url,wait_until="domcontentloaded",timeout=self.timeout_ms)  # budget: exactly one navigation
            except Exception:return None,"BROWSER_BLOCKED"
            best:dict[str,Any]|None=None;elapsed=0
            while True:  # minimal hydration: stop as soon as the rendered DOM is complete
                html=page.content()
                if not looks_like_bot_challenge(html):
                    detail=semantic_html_detail(html,title,expected_id,url)
                    embedded=embedded_json_detail(html,title)
                    if embedded and (detail.get("failure") or len(embedded["full_jd"])>len(detail.get("full_jd") or "")):
                        detail=embedded
                    if not detail.get("failure") and credible_jd(detail.get("full_jd")):
                        body=detail["full_jd"]
                        if len(re.sub(r"\s+","",body))>=self.teaser_floor:
                            detail["source_type"]="BROWSER_DOM";return detail,None
                        if best is None or len(body)>len(best["full_jd"]):best=detail
                if elapsed>=self.hydration_ms:break
                page.wait_for_timeout(self.poll_ms);elapsed+=self.poll_ms
            for body in captured:  # D: first HIGH-confidence detail response wins, then stop
                found=network_json_detail(body,title)
                if found:return found,None
            if best:  # partial-but-credible rendered DOM beats a hard failure
                best["source_type"]="BROWSER_DOM";return best,None
            return None,"BROWSER_BLOCKED"
        finally:
            try:page.remove_listener("response",on_response)
            except Exception:pass
    def enrich(self,raws:list[dict],failures:list[tuple[str,Any]],id_fields:tuple[str,...],
               title_fields:tuple[str,...])->tuple[list[dict],int,list[tuple[str,Any]],list[Any]]:
        """Returns (raws, resolved, blocked, resolved_ids). Jobs beyond the
        browser budget keep their original HTTP failure."""
        targets=self._targets(raws,failures,id_fields)
        budgeted=targets[:self.max_browser_jobs] if self.max_browser_jobs>0 else []
        budget_skipped=max(0,len(targets)-len(budgeted))
        if not budgeted:
            self.counters={"attempted":0,"resolved":0,"blocked":0,"budget_skipped":budget_skipped,"dom":0,"network":0,"pages_opened":0,"seconds":0.0}
            return raws,0,[],[]
        from time import perf_counter as _pc
        blocked:list[tuple[str,Any]]=[];resolved=0;navigations=0;done:set[int]=set();resolved_ids:list[Any]=[]
        dom=network=pages_opened=0;started=_pc()
        observed=observed_detail_hosts(self.plan,raws)
        try:
            with self.browser_factory() as runtime:  # one shared session for the whole batch
                pages_opened=getattr(runtime,"pages_opened",0)
                page=runtime.page
                if page is None:raise BrowserRuntimeError("BROWSER_CONTEXT_CLOSED")
                if hasattr(page,"set_default_timeout"):page.set_default_timeout(self.timeout_ms)
                for index,code in budgeted:
                    raw=raws[index];url,jid=resolve_detail_url(self.plan,raw,id_fields,observed)
                    if not url:done.add(index);blocked.append((code if code in BROWSER_FALLBACK_CODES else "DETAIL_SOURCE_NOT_FOUND",jid));continue
                    title=next((extract_path(raw,k) for k in title_fields if k and isinstance(extract_path(raw,k),str)),None)
                    url_tokens={value for _,value in _path_bound_ids(url)}
                    expected_id=str(jid) if jid is not None and str(jid) in url_tokens else None
                    navigations+=1
                    detail,failure=self._resolve_in_browser(page,url,title,expected_id)
                    done.add(index)
                    if failure:blocked.append((failure,jid));continue
                    enriched=dict(raw);enriched.update({"_generic_description":detail["full_jd"],"_generic_responsibilities":detail["responsibilities"],"_generic_requirements":detail["requirements"],"_generic_detail_url":url,"_generic_detail_source":detail["source_type"],"_generic_bound_id":detail.get("bound_id")})
                    if isinstance(detail.get("location"),str) and detail["location"].strip():enriched["_generic_location"]=detail["location"].strip()
                    raws[index]=enriched;resolved+=1;resolved_ids.append(jid)
                    if detail["source_type"]=="BROWSER_NETWORK_JSON":network+=1
                    else:dom+=1
        except Exception:
            pass  # session-level failure: fail closed for every unfinished target
        for index,code in budgeted:
            if index not in done:
                raw=raws[index]
                jid=next((extract_path(raw,k) for k in id_fields if k and extract_path(raw,k) not in (None,"")),None)
                blocked.append(("BROWSER_BLOCKED",jid))
        self.navigations_used=navigations
        self.counters={"attempted":len(budgeted),"resolved":resolved,"blocked":len(blocked),
                       "budget_skipped":budget_skipped,"dom":dom,"network":network,
                       "pages_opened":pages_opened,"seconds":_pc()-started}
        return raws,resolved,blocked,resolved_ids


class GenericDomDetailCollector:
    """Enriches list records through only the DOM mechanism captured in the plan."""
    def __init__(self,plan:CollectionPlan,browser_factory=BrowserRuntime):self.plan=plan;self.browser_factory=browser_factory
    @staticmethod
    def _value(page,selector):
        if not selector:return None
        loc=page.locator(selector)
        return loc.first.inner_text().strip() if loc.count() else None
    def enrich(self,raws:list[dict],id_fields:tuple[str,...])->tuple[list[dict],int,int]:
        succeeded=failed=0
        with self.browser_factory() as runtime:
            for index,raw in enumerate(raws):
                value=extract_path(raw,self.plan.detail_url_field) if self.plan.detail_url_field else None
                jid=next((raw.get(k) for k in id_fields if k and raw.get(k) is not None),None)
                if not value and self.plan.detail_endpoint_template and "{id}" in self.plan.detail_endpoint_template:value=self.plan.detail_endpoint_template.format(id=jid)
                if not isinstance(value,str):failed+=1;continue
                url=urljoin(self.plan.source_url,value)
                if urlsplit(url).scheme not in ("http","https"):failed+=1;continue
                try:
                    runtime.page.goto(url,wait_until="domcontentloaded")
                    jd,_,_=wait_for_dynamic_jd(runtime.page,preferred_selector=self.plan.detail_jd_selector)
                    if not credible_jd(jd):failed+=1;continue
                    enriched=dict(raw);enriched["_generic_description"]=jd;enriched["_generic_detail_url"]=url
                    enriched["_generic_location"]=self._value(runtime.page,self.plan.detail_location_selector)
                    enriched["_generic_department"]=self._value(runtime.page,self.plan.detail_department_selector)
                    enriched["_generic_employment_type"]=self._value(runtime.page,self.plan.detail_employment_type_selector)
                    raws[index]=enriched;succeeded+=1
                except Exception:failed+=1
        return raws,succeeded,failed
