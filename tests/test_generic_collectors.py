from job_extractor.collectors.generic_http import GenericHttpCollector,path_get
from job_extractor.collectors.generic_dom import GenericDomCollector
from job_extractor.planning import CollectionPlan

def raw(i,jd="Build things"):
    return {"id":str(i),"title":f"Job {i}","location":{"name":"Sydney"},"description":jd,"requirements":"Python","access_token":"drop"}
class Response:
    def __init__(self,payload):self.payload=payload
    def raise_for_status(self):pass
    def json(self):return self.payload
class Client:
    def __init__(self,payloads,details=None):self.payloads=list(payloads);self.details=details or {};self.calls=[]
    def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        if "/detail/" in url:return Response(self.details[url.rsplit('/',1)[-1]])
        return Response(self.payloads.pop(0))
def plan(kind="SINGLE_RESPONSE",**changes):
    values=dict(source_url="https://x.test/jobs",mode="HTTP_API",executable=True,list_endpoint="https://api.x.test/jobs",list_method="GET",
        pagination_type=kind,list_path="data.items",job_id_field="id",job_title_field="title",total_field="data.total",observed_endpoints=["https://api.x.test/jobs"],detail_mode="LIST_SUFFICIENT")
    values.update(changes);return CollectionPlan(**values)
def test_list_path_extraction(): assert path_get({"data":{"items":[1]}},"data.items")==[1]
def test_single_response_execution_and_field_mapping():
    c=GenericHttpCollector(plan(),Client([{"data":{"items":[raw(1),raw(2)],"total":2}}]));r=c.collect()
    assert r.status=="COMPLETE" and r.total_unique==2 and r.jobs[0].locations==["Sydney"] and "access_token" not in r.jobs[0].raw_data
def test_page_execution():
    p=plan("PAGE",page_param="page",initial_values={"page":1});client=Client([{"data":{"items":[raw(1)],"total":2}},{"data":{"items":[raw(2)],"total":2}}])
    r=GenericHttpCollector(p,client).collect();assert r.status=="COMPLETE" and client.calls[1][2]["params"]["page"]==2
def test_offset_execution():
    p=plan("OFFSET",offset_param="offset",page_size_param="limit",initial_values={"offset":0,"limit":1});client=Client([{"data":{"items":[raw(1)],"total":2}},{"data":{"items":[raw(2)],"total":2}}])
    r=GenericHttpCollector(p,client).collect();assert r.status=="COMPLETE" and client.calls[1][2]["params"]["offset"]==1
def test_cursor_execution():
    p=plan("CURSOR",cursor_param="cursor",next_cursor_field="data.nextCursor",initial_values={});client=Client([{"data":{"items":[raw(1)],"total":2,"nextCursor":"b"}},{"data":{"items":[raw(2)],"total":2}}])
    assert GenericHttpCollector(p,client).collect().status=="COMPLETE"
def test_cursor_loop_is_incomplete():
    p=plan("CURSOR",cursor_param="cursor",next_cursor_field="data.nextCursor",total_field=None);client=Client([{"data":{"items":[raw(1)],"nextCursor":"a"}},{"data":{"items":[raw(2)],"nextCursor":"a"}}])
    r=GenericHttpCollector(p,client).collect();assert r.status=="INCOMPLETE" and any("PAGINATION_LOOP" in x for x in r.errors)
def test_detail_fallback():
    item=raw(1,"");p=plan(detail_mode="DETAIL_FALLBACK",detail_endpoint_template="https://api.x.test/detail/{id}",detail_method="GET",detail_id_field="id")
    client=Client([{"data":{"items":[item],"total":1}}],{"1":{"description":"Job description and responsibilities. "+"Build reliable customer-facing systems. "*8,"requirements":"Python qualifications"}});r=GenericHttpCollector(p,client).collect()
    assert r.status=="COMPLETE" and r.metrics.detail_requests==1 and r.jobs[0].full_jd
def test_detail_failure_is_incomplete():
    item=raw(1,"");p=plan(detail_mode="DETAIL_REQUIRED",detail_endpoint_template="https://api.x.test/detail/{id}",detail_method="GET",detail_id_field="id")
    r=GenericHttpCollector(p,Client([{"data":{"items":[item],"total":1}}])).collect()
    assert r.status=="INCOMPLETE" and any("GENERIC_DETAIL_ERROR" in x for x in r.errors)
def test_source_missing_jd_is_complete_with_warning():
    item=raw(1,"");item["requirements"]=""
    r=GenericHttpCollector(plan(detail_mode="UNKNOWN"),Client([{"data":{"items":[item],"total":1}}])).collect()
    assert r.status=="COMPLETE" and r.data_completeness.missing_jd_jobs==1 and r.warnings
def test_nested_detail_url_path_is_executed_for_dom_enrichment():
    item=raw(1,"");item["requirements"]="";item["links"]={"absolute_url":"/jobs/1"}
    p=plan(detail_mode="DETAIL_DOM",detail_url_field="links.absolute_url",detail_jd_selector='[data-qa="job-description"]')
    from job_extractor.collectors.generic_detail import GenericDomDetailCollector
    enriched,succeeded,failed=GenericDomDetailCollector(p,browser_factory=Browser).enrich([item],("id",))
    assert (succeeded,failed)==(1,0) and enriched[0]["_generic_detail_url"]=="https://x.test/jobs/1"

class Loc:
    def __init__(self,value):self.value=value;self.first=self
    def count(self):return int(bool(self.value))
    def inner_text(self):return self.value
class Page:
    def __init__(self):self.url=""
    def goto(self,url,**kwargs):self.url=url
    def wait_for_timeout(self,*a):pass
    def locator(self,selector):
        if selector=="h1":return Loc("Engineer")
        if selector=='[data-qa="job-description"]':return Loc("Job description and responsibilities. "+"Build and ship reliable systems for customers. "*8)
        if selector=='[data-qa="job-location"]':return Loc("Remote")
        return Loc("")
class Browser:
    def __init__(self):self.page=Page()
    def __enter__(self):return self
    def __exit__(self,*a):return False
def test_dom_collection_and_dedupe():
    p=CollectionPlan(source_url="https://x.test/jobs",mode="DOM",executable=True,allowed_detail_urls=["https://x.test/jobs/1","https://x.test/jobs/1"],detail_jd_selector='[data-qa="job-description"]')
    r=GenericDomCollector(p,browser_factory=Browser).collect();assert r.status=="COMPLETE" and (r.total_expected,r.total_unique)==(1,1)
def test_dom_unsafe_link_ignored():
    p=CollectionPlan(source_url="https://x.test/jobs",mode="DOM",executable=True,allowed_detail_urls=["https://evil.test/apply"])
    r=GenericDomCollector(p,browser_factory=Browser).collect();assert r.total_fetched==0 and r.status=="FAILED"
