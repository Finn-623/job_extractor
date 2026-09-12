from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from typing import Any
from job_extractor.planning.models import CollectionPlan
import re
from urllib.parse import urljoin,urlsplit
from job_extractor.browser import BrowserRuntime
from job_extractor.discovery.dom_semantics import credible_jd,semantic_html_detail,_SemanticTextParser
from job_extractor.discovery.dynamic import wait_for_dynamic_jd

RESP_HEADINGS=("responsibilities","what you'll do","what you will do","the role","岗位职责","工作职责")
REQ_HEADINGS=("requirements","qualifications","what we're looking for","what we are looking for","what you bring","任职要求","任职资格","岗位要求","资格要求")
RESP_HEADINGS+=("job responsibilities","岗位描述","职位职责")
REQ_HEADINGS+=("任职条件",)
DETAIL_FAILURE_CODES=("DETAIL_NAVIGATION_FAILED","DETAIL_SOURCE_NOT_FOUND","TITLE_MISMATCH","JD_CONTAINER_NOT_FOUND","JD_TOO_SHORT","DYNAMIC_RENDER_TIMEOUT","PARSE_FAILED")
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

class GenericHttpDetailCollector:
    """Executes only the explicit detail template recorded in a validated plan."""
    def __init__(self,plan:CollectionPlan,client):self.plan=plan;self.client=client
    def fetch(self,job_id:str)->dict[str,Any]|None:
        template=self.plan.detail_endpoint_template
        if not template or "{id}" not in template:return None
        response=self.client.request(self.plan.detail_method or "GET",template.format(id=job_id))
        response.raise_for_status();payload=response.json()
        detail=extract_path(payload,self.plan.detail_path) if self.plan.detail_path else payload
        return detail if isinstance(detail,dict) else None

class GenericHtmlDetailCollector:
    """Fetches public, observed detail URLs and applies semantic HTML extraction."""
    def __init__(self,plan:CollectionPlan,client,max_workers:int=2,navigation_attempts:int=3):
        self.plan=plan;self.client=client;self.max_workers=max(1,min(max_workers,8));self.navigation_attempts=max(1,min(navigation_attempts,3))
    def _url(self,raw:dict,id_fields:tuple[str,...])->tuple[str|None,Any]:
        jid=next((extract_path(raw,k) for k in id_fields if k and extract_path(raw,k) not in (None,"")),None)
        value=extract_path(raw,self.plan.detail_url_field) if self.plan.detail_url_field else None
        if not value and self.plan.detail_endpoint_template and "{id}" in self.plan.detail_endpoint_template and jid is not None:
            value=self.plan.detail_endpoint_template.format(id=jid)
        if not isinstance(value,str):return None,jid
        url=urljoin(self.plan.source_url,value);parsed=urlsplit(url)
        source_host=(urlsplit(self.plan.source_url).hostname or "").lower()
        trusted={source_host,*(x.lower() for x in self.plan.trusted_detail_hosts)}
        if parsed.scheme not in ("http","https") or (parsed.hostname or "").lower() not in trusted:return None,jid
        return url,jid
    def _fetch(self,item:tuple[int,dict],id_fields:tuple[str,...],title_fields:tuple[str,...]):
        index,raw=item;url,jid=self._url(raw,id_fields)
        if not url:return index,raw,"DETAIL_SOURCE_NOT_FOUND",jid
        title=next((extract_path(raw,k) for k in title_fields if k and isinstance(extract_path(raw,k),str)),None)
        response=None
        for attempt in range(self.navigation_attempts):
            try:
                response=self.client.request("GET",url);response.raise_for_status();break
            except Exception:
                response=None
                if attempt+1<self.navigation_attempts:sleep(0.25*(attempt+1))
        if response is None:return index,raw,"DETAIL_NAVIGATION_FAILED",jid
        try:html=response.text
        except Exception:return index,raw,"PARSE_FAILED",jid
        detail=semantic_html_detail(html,title,str(jid) if jid is not None else None,url)
        failure=detail.get("failure")
        if failure:return index,raw,failure if failure in DETAIL_FAILURE_CODES else "PARSE_FAILED",jid
        enriched=dict(raw);enriched.update({"_generic_description":detail["full_jd"],"_generic_responsibilities":detail["responsibilities"],"_generic_requirements":detail["requirements"],"_generic_detail_url":url,"_generic_detail_source":detail["source_type"],"_generic_bound_id":detail.get("bound_id")})
        return index,enriched,None,jid
    def enrich(self,raws:list[dict],id_fields:tuple[str,...],title_fields:tuple[str,...])->tuple[list[dict],int,list[tuple[str,Any]]]:
        failures=[];succeeded=0
        with ThreadPoolExecutor(max_workers=min(self.max_workers,len(raws) or 1)) as pool:
            results=pool.map(lambda item:self._fetch(item,id_fields,title_fields),enumerate(raws))
            for index,raw,failure,jid in results:
                if failure:failures.append((failure,jid))
                else:raws[index]=raw;succeeded+=1
        return raws,succeeded,failures

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
