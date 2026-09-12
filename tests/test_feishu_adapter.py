import json
from job_extractor.adapters.feishu import FeishuAdapter, FeishuResponseError, _safe_business_data
from job_extractor.browser.models import CapturedResponse
from job_extractor.browser import BrowserRuntimeError

URL="https://acme.jobs.feishu.cn/index/position/list"
META=json.dumps({"tenant_info":{"tenant_name":"Acme"},"website_info":{"id":"site1","path":"index","process_type":1}})
HTML=f'<script id="js-websiteInfo" type="text/json">{META}</script>'
def raw(i,scope="1"):
    return {"id":str(i),"title":f"Job {i}","description":"Build systems\nOwn quality","requirement":"Python\nTeamwork",
        "city_list":[{"i18n_name":"上海"}],"recruit_type":{"i18n_name":"全职","parent":{"id":scope,"i18n_name":"社招"}},
        "job_function":{"i18n_name":"技术类"},"publish_time":1700000000000,"access_token":"drop"}
def payload(items,total): return {"code":0,"data":{"job_post_list":items,"count":total}}
class Next:
    def __init__(self,page): self.page=page
    def count(self): return 1
    def get_attribute(self,k): return "false" if self.page.index<len(self.page.responses)-1 else "true"
    def click(self): self.page.index+=1
class Page:
    def __init__(self,responses): self.responses=responses; self.index=0
    def content(self): return HTML
    def locator(self,_): return Next(self)
class FakeBrowser:
    def __init__(self,responses,details=None,detail_error=False):
        self.responses=responses; self.details=details or {}; self.detail_error=detail_error
        self.page=Page(responses); self.pages_opened=1; self.requests_observed=7
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def open_and_capture(self,url,fragment,method):
        if fragment.startswith(FeishuAdapter.DETAIL_PREFIX):
            if self.detail_error: raise BrowserRuntimeError("BROWSER_RESPONSE_TIMEOUT")
            jid=fragment.rsplit("/",1)[-1]
            return CapturedResponse("https://x/api","GET",None,200,{"code":0,"data":{"job_post_detail":self.details[jid]}})
        return CapturedResponse("https://x/api","POST",None,200,self.responses[0])
    def capture_after(self,f,m,action): action(); return CapturedResponse("https://x/api","POST",None,200,self.responses[self.page.index])
def adapter(responses,max_pages=100,details=None,detail_error=False):
    return FeishuAdapter(max_pages=max_pages,browser_factory=lambda:FakeBrowser(responses,details,detail_error))

def test_hostname_detection(): assert FeishuAdapter.match(URL)
def test_false_hostname(): assert not FeishuAdapter.match("https://jobs.feishu.cn.example.com")
def test_scope_parsing():
    s=FeishuAdapter.parse_scope(HTML); assert (s.website_id,s.website_path,s.process_type,s.recruitment_type)==("site1","index",1,"social")
def test_company_extraction(): assert FeishuAdapter.parse_scope(HTML).company=="Acme"
def test_company_none(): assert FeishuAdapter.parse_scope(HTML.replace('"tenant_name": "Acme"','')).company is None
def test_bad_scope():
    try: FeishuAdapter.parse_scope("<html>")
    except FeishuResponseError: pass
    else: assert False
def test_list_parsing(): assert FeishuAdapter.parse_list(payload([raw(1)],1))[1]==1
def test_invalid_list():
    try: FeishuAdapter.parse_list({"data":{}})
    except FeishuResponseError: pass
    else: assert False
def test_detail_parsing():
    assert FeishuAdapter.parse_detail({"code":0,"data":{"job_post_detail":{"id":"1"}}})["id"]=="1"
def test_invalid_detail():
    try: FeishuAdapter.parse_detail({"code":0,"data":{}})
    except FeishuResponseError: pass
    else: assert False
def test_pagination_and_total_stop():
    a=adapter([payload([raw(1)],2),payload([raw(2)],2)]); r=a.collect(URL)
    assert r.status=="COMPLETE" and r.total_unique==2 and a.page_count==2
def test_empty_complete(): assert adapter([payload([],0)]).collect(URL).status=="COMPLETE"
def test_dedupe_is_incomplete():
    r=adapter([payload([raw(1),raw(1)],2)]).collect(URL); assert r.status=="INCOMPLETE" and r.total_unique==1
def test_stable_job_id_and_urls():
    r=adapter([payload([raw(7)],1)]).collect(URL); j=r.jobs[0]
    assert j.job_id=="7" and j.detail_url.endswith("/index/position/detail/7")
def test_scope_leak():
    r=adapter([payload([raw(1,"2")],1)]).collect(URL); assert r.status=="INCOMPLETE" and r.errors[0].startswith("SCOPE_LEAK")
def test_list_sufficient():
    a=adapter([payload([raw(1)],1)]); a.collect(URL); assert a.detail_strategy=="LIST_SUFFICIENT" and a.details_attempted==0
def test_detail_fallback_only_for_incomplete_jd():
    item=raw(1); item["requirement"]=""
    a=adapter([payload([item],1)],details={"1":{"requirement":"Python\nTeamwork"}}); result=a.collect(URL)
    assert result.status=="COMPLETE" and a.detail_strategy=="DETAIL_FALLBACK"
    assert (a.details_attempted,a.details_succeeded,a.details_failed)==(1,1,0)
    assert len(result.jobs[0].requirements)==2
def test_detail_failure_is_safe_and_incomplete():
    item=raw(1); item["description"]=""
    a=adapter([payload([item],1)],detail_error=True); result=a.collect(URL)
    assert result.status=="INCOMPLETE" and a.details_failed==1
    assert any("FEISHU_DETAIL_REQUEST_FAILED" in error for error in result.errors)
def test_official_detail_with_null_jd_is_collection_complete():
    item=raw(1); item["description"]=item["requirement"]=""
    a=adapter([payload([item],1)],details={"1":{"description":None,"requirement":None}}); result=a.collect(URL)
    assert result.status=="COMPLETE" and a.details_succeeded==1 and not result.errors
def test_jd_normalization():
    j=adapter([payload([raw(1)],1)]).collect(URL).jobs[0]
    assert len(j.responsibilities)==2 and len(j.requirements)==2 and len(j.full_jd)>20
def test_max_pages_safety():
    r=adapter([payload([raw(1)],2),payload([raw(2)],2)],max_pages=1).collect(URL)
    assert r.status=="INCOMPLETE" and any("PAGINATION_LIMIT" in e for e in r.errors)
def test_sensitive_fields_not_persisted():
    j=adapter([payload([raw(1)],1)]).collect(URL).jobs[0]
    assert "access_token" not in j.raw_data
def test_security_sanitizer_nested(): assert _safe_business_data({"x":{"csrf_token":"bad","ok":1}})=={"x":{"ok":1}}
