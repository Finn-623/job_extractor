from __future__ import annotations
import base64, html, json, re, math
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from time import perf_counter
from urllib.parse import urlparse
import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from job_extractor.adapters.base import BaseAdapter
from job_extractor.models import CollectionResult, Job

class MokaResponseError(RuntimeError): pass
@dataclass(frozen=True)
class MokaScope:
    org_id:str; site_id:int; site:str; mode:str; company:str|None=None; aes_iv:str|None=None
class _Text(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]
    def handle_data(self,data):
        if data.strip(): self.parts.append(data.strip())

class MokaAdapter(BaseAdapter):
    platform_name="moka"; priority=20
    LIST_PATH="/api/outer/ats-apply/website/jobs/v2"; DETAIL_PATH="/api/outer/ats-apply/website/job"
    def __init__(self,timeout=20.0,page_size=20,max_pages=1000,client=None,progress_callback=None):
        self.page_size,self.max_pages=page_size,max_pages
        self.client=client or httpx.Client(timeout=timeout,follow_redirects=True,headers={"User-Agent":"JobExtractor/0.1 (+https://mokahr.com)"})
        self.scope=None; self.page_count=self.list_requests=self.details_attempted=0
        self.details_succeeded=self.details_failed=0; self.detail_strategy="DETAIL_REQUIRED"; self.elapsed_seconds=0.0
        self.progress_callback=progress_callback
    def _observe(self, event, **data):
        if self.progress_callback:
            self.progress_callback(event, **data)
    @classmethod
    def match(cls,url):
        h=(urlparse(url).hostname or "").lower(); return h=="mokahr.com" or h.endswith(".mokahr.com")
    @staticmethod
    def _base(url): p=urlparse(url); return f"{p.scheme}://{p.netloc}"
    @classmethod
    def parse_scope(cls,url,text):
        p=[x for x in urlparse(url).path.split("/") if x]
        if len(p)<3 or p[0] not in {"campus-recruitment","social-recruitment","campus_apply"}: raise MokaResponseError("unsupported Moka recruitment URL scope")
        try: sid=int(p[2])
        except ValueError as e: raise MokaResponseError("invalid Moka site id") from e
        text=html.unescape(text)
        def f(k):
            m=re.search(rf'"{k}"\s*:\s*"([^"]*)"',text); return m.group(1) if m else None
        sites={int(x) for x in re.findall(r'"siteId"\s*:\s*"?(\d+)"?',text)}
        if sites and sid not in sites: raise MokaResponseError("site id does not match page initialization")
        if f("id") and f("id")!=p[1]: raise MokaResponseError("org id does not match page initialization")
        if not f("aesIv"): raise MokaResponseError("page initialization did not contain aesIv")
        mode="campus" if p[0].startswith("campus") else "social"
        # STEP96.4: the org/tenant name (first global "name") belongs to the
        # Moka tenant, NOT necessarily to the recruiting entity of this site.
        # Anchor the company to the current site subject first: the page <title>
        # (e.g. "立敏达科技 - 校园招聘"), then the campaign share title, then
        # the site/campaign name; only fall back to the org name when no
        # site-layer name exists.  Never collapse tenant slug -> org/group name
        # (lingyiitech -> 广东领益智造股份有限公司 must not override the real
        # subject 立敏达科技).
        generic={"jobs","careers","招聘","校园招聘","社会招聘","招贤纳士","加入我们"}
        company=None
        m=re.search(r"<title>([^<]+)</title>",text)
        if m:
            value=re.sub(r"\s+"," ",m.group(1)).strip()
            value=re.split(r"\s+[-–—|]\s+",value)[0].strip()
            value=re.sub(r"(?:\s*[-–—|]\s*)?(?:校园招聘|社会招聘|春季招聘|秋季招聘|招聘|jobs|careers)\s*$","",value,flags=re.I).strip(" -|–—·")
            if value and value.lower() not in generic and len(value)<=30: company=value
        if not company:
            m=re.search(r'"applyShareTitle"\s*:\s*"([^"]*)"',text)
            if m:
                value=re.sub(r"(?:\d{4}届)?(?:校园招聘|社会招聘|招聘)\s*$","",(m.group(1) or "").strip()).strip(" -|–—·")
                if value and value.lower() not in generic and len(value)<=30: company=value
        if not company:
            m=re.search(r'"siteName"\s*:\s*"([^"]*)"',text)
            if m:
                value=(m.group(1) or "").strip()
                if value and value.lower() not in generic: company=value
        if not company:
            company=f("name")  # org/tenant name, last resort only
        return MokaScope(p[1],sid,mode,mode,company,f("aesIv"))
    @staticmethod
    def decode_response(p,iv):
        if "necromancer" in p:
            try:
                c=AES.new(p["necromancer"].encode(),AES.MODE_CBC,iv.encode())
                p=json.loads(unpad(c.decrypt(base64.b64decode(p["data"])),16).decode())
            except Exception as e: raise MokaResponseError("could not decode encrypted Moka response") from e
        if not isinstance(p,dict): raise MokaResponseError("response root was not an object")
        if p.get("success") is False or p.get("code") not in (None,0): raise MokaResponseError(f"Moka API error: {p.get('msg',p.get('code'))}")
        return p
    def _request(self,method,url,iv,**kw):
        try: r=self.client.request(method,url,**kw); r.raise_for_status(); p=r.json()
        except (httpx.HTTPError,ValueError) as e: raise MokaResponseError(str(e)) from e
        if not isinstance(p,dict): raise MokaResponseError("response root was not an object")
        return self.decode_response(p,iv)
    @staticmethod
    def _list(p):
        d=p.get("data")
        if not isinstance(d,dict) or not isinstance(d.get("jobs"),list): raise MokaResponseError("unsupported jobs response")
        jobs=d["jobs"]; total=(d.get("jobStats") or {}).get("total")
        if not isinstance(total,int) or total<0 or not all(isinstance(x,dict) for x in jobs): raise MokaResponseError("invalid jobs response")
        return jobs,total
    @staticmethod
    def _text(v):
        if not isinstance(v,str): return []
        p=_Text(); p.feed(v); return p.parts
    @classmethod
    def _job(cls,raw,d,url,company):
        title=d.get("title") or raw.get("title"); jid=d.get("id") or raw.get("id")
        if not isinstance(title,str) or not jid: return None
        loc=[]
        for x in d.get("locations") or []:
            if isinstance(x,dict):
                v=x.get("cityName") or x.get("provinceName") or x.get("country")
                if v and v not in loc: loc.append(str(v))
        lines=cls._text(d.get("jobDescription")); cut=next((i for i,x in enumerate(lines) if "岗位要求" in x or "任职要求" in x),len(lines)); detail=url.rstrip("/")+f"#/job/{jid}"
        return Job(company=company,job_id=str(jid),job_title=title,job_category=(d.get("zhineng") or {}).get("name"),department=(d.get("department") or {}).get("name"),locations=loc,recruitment_type="校园招聘" if d.get("hireMode")==2 else "社会招聘",education=d.get("education"),headcount=d.get("number"),responsibilities=lines[:cut],requirements=lines[cut:],full_jd="\n".join(lines) or None,apply_url=detail,detail_url=detail,source_url=url,publish_date=d.get("publishedAt"),raw_data={"list":raw,"detail":d})
    def collect(self,url):
        started=datetime.now(); clock=perf_counter(); errors=[]; jobs=[]; fetched=0; total=None
        try:
            page=self.client.get(url); page.raise_for_status(); self.scope=self.parse_scope(url,page.text); self._observe("scope",company=self.scope.company); base=self._base(url); raws=[]; offset=0
            while self.page_count<self.max_pages:
                self.list_requests+=1; body=self._request("POST",base+self.LIST_PATH,self.scope.aes_iv,json={"orgId":self.scope.org_id,"siteId":self.scope.site_id,"limit":self.page_size,"offset":offset,"needStat":True,"site":self.scope.site,"locale":"zh-CN"})
                batch,total=self._list(body); self.page_count+=1; raws+=batch; fetched+=len(batch); offset+=len(batch)
                self._observe("list",page=self.page_count,total=total,fetched=fetched,
                              pages=math.ceil(total/self.page_size) if self.page_size else None)
                if not batch or offset>=total: break
            self._observe("list_complete",total=total,fetched=fetched)
            seen=set()
            for raw in raws:
                jid=str(raw.get("id") or "")
                if not jid or jid in seen: continue
                seen.add(jid); self.details_attempted+=1
                try:
                    body=self._request("POST",base+self.DETAIL_PATH,self.scope.aes_iv,json={"siteId":self.scope.site_id,"orgId":self.scope.org_id,"jobId":jid,"locale":"zh-CN"}); d=body.get("data")
                    if not isinstance(d,dict): raise MokaResponseError("unsupported detail response")
                    job=self._job(raw,d,url,self.scope.company)
                    if job: jobs.append(job)
                    self.details_succeeded+=1
                except MokaResponseError as e: self.details_failed+=1; errors.append(f"detail {jid}: {e}")
                self._observe("detail",done=self.details_attempted,total=len(seen) + len([x for x in raws if str(x.get("id") or "") not in seen]),
                              ok=self.details_succeeded,fail=self.details_failed,title=raw.get("title"),job_id=jid)
        except (httpx.HTTPError,MokaResponseError) as e: errors.append(str(e))
        unique=len(jobs); status="COMPLETE" if total is not None and fetched==total and unique==total and not errors else ("FAILED" if total is None else "INCOMPLETE")
        self.elapsed_seconds=perf_counter()-clock
        return CollectionResult(source_url=url,platform="moka",company=self.scope.company if self.scope else None,total_expected=total,total_fetched=fetched,total_unique=unique,status=status,jobs=jobs,errors=errors,started_at=started,finished_at=datetime.now())
