from job_extractor.collectors.generic_detail import (
    DETAIL_FAILURE_CODES, GenericHtmlDetailCollector, GenericHttpDetailCollector, split_jd,
)
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.discovery.dom_semantics import first_real_url, semantic_html_detail
from job_extractor.discovery.models import ApiCandidate, DiscoveryResult
from job_extractor.models import CollectionResult, Job
from job_extractor.planning.builder import CollectionPlanBuilder
from job_extractor.planning.models import CollectionPlan
from job_extractor.runtime import evaluate_data_completeness


def plan(**updates):
    values=dict(source_url="https://jobs.test/list",mode="HTTP_API",executable=True,
        list_endpoint="https://jobs.test/api",list_method="GET",pagination_type="SINGLE_RESPONSE",
        list_path="jobs",job_id_field="id",job_title_field="name",detail_mode="DETAIL_HTTP_HTML",
        detail_id_field="id",detail_url_field="click_url",confidence="HIGH")
    values.update(updates);return CollectionPlan(**values)


def html(title="Engineer",body=None,requirements=None,footer="Employer marketing text"):
    body=body or "Build reliable distributed systems and own delivery quality. "*3
    requirements=requirements or "Bachelor degree, strong programming skills, and collaborative experience. "*2
    return f"""<html><body><header>Navigation</header><h1>{title}</h1>
      <div>岗位描述</div><div>{body}</div>
      <div>岗位要求</div><div>{requirements}</div>
      <div>工作地点</div><div>Somewhere</div><div>招聘部门</div><div>{footer}</div>
      <footer>Privacy Cookie Careers</footer></body></html>"""


class Response:
    def __init__(self,payload=None,text_value="",failure=False):self.payload=payload;self.text=text_value;self.failure=failure
    def raise_for_status(self):
        if self.failure:raise RuntimeError("bad status")
    def json(self):return self.payload


class Client:
    def __init__(self,responses):self.responses=responses
    def request(self,method,url,**kwargs):return self.responses[url]


def test_click_url_binding_and_plan_selection():
    assert first_real_url({"click_url":"/job/246"},"https://jobs.test/list")==("https://jobs.test/job/246","click_url")
    candidate=ApiCandidate(url="https://jobs.test/api",method="GET",score=90,confidence="HIGH",replayable=True,
        response_shape={"candidate_list_path":"jobs","sample_field_names":["id","name","click_url"]},
        sample_job_hint={"id":"246","name":"Engineer","click_url":"/job/246"},observed_total=1,observed_list_length=1)
    discovery=DiscoveryResult(source_url="https://jobs.test/list",status="DISCOVERED",probable_list_api=candidate,
        candidate_list_apis=[candidate],detail_dom={"status":"DETAIL_HTTP_HTML","url_field":"click_url"})
    built=CollectionPlanBuilder().build(discovery)
    assert built.detail_mode=="DETAIL_HTTP_HTML" and built.detail_url_field=="click_url" and built.executable


def test_rid_id_consistency_and_title_verification():
    good=semantic_html_detail(html(),"Engineer","246","https://jobs.test/job/246")
    assert good["failure"] is None and good["bound_id"]=="246" and good["title_match"]
    assert semantic_html_detail(html(),"Engineer","999","https://jobs.test/job/246")["failure"]=="TITLE_MISMATCH"
    assert semantic_html_detail(html(),"Other title","246","https://jobs.test/job/246")["failure"]=="TITLE_MISMATCH"
    compound=semantic_html_detail(html(),"Engineer","246","https://jobs.test/detail/id/68/fid/20/rid/246.html")
    assert compound["failure"] is None and compound["bound_id"]=="246"


def test_dom_detail_extraction_heading_split_and_chrome_rejection():
    detail=semantic_html_detail(html(footer="Never include this employer campaign"),"Engineer","246","https://jobs.test/job/246")
    assert detail["source_type"]=="DOM_DETAIL"
    assert detail["responsibilities"] and detail["requirements"]
    assert "岗位描述" in detail["full_jd"] and "岗位要求" in detail["full_jd"]
    assert "Never include" not in detail["full_jd"] and "Privacy" not in detail["full_jd"]


def test_xhr_detail_extraction():
    p=plan(detail_mode="DETAIL_REQUIRED",detail_endpoint_template="https://jobs.test/detail/{id}",detail_method="GET",detail_path="data")
    client=Client({"https://jobs.test/detail/246":Response({"data":{"description":"A complete observed API detail"}})})
    assert GenericHttpDetailCollector(p,client).fetch("246")["description"].startswith("A complete")


def test_full_jd_preserved_when_structured_split_is_unavailable():
    p=plan(detail_mode="LIST_SUFFICIENT")
    full="One cohesive unheaded job description that must remain intact. "*4
    job=GenericHttpCollector(p)._job({"id":"246","name":"Engineer","description":full})
    assert job.full_jd==full.strip() and not job.requirements
    assert split_jd(full)==([],[])


def test_html_collector_success_and_deterministic_order():
    raws=[{"id":"2","name":"Two","click_url":"/job/2"},{"id":"1","name":"One","click_url":"/job/1"}]
    client=Client({"https://jobs.test/job/1":Response(text_value=html("One")),"https://jobs.test/job/2":Response(text_value=html("Two"))})
    enriched,success,failures=GenericHtmlDetailCollector(plan(),client,max_workers=2).enrich(raws,("id",),("name",))
    assert success==2 and failures==[] and [x["id"] for x in enriched]==["2","1"]
    assert all(x["_generic_bound_id"]==x["id"] for x in enriched)


def test_http_html_detail_mode_runs_end_to_end_and_reports_strategy():
    client=Client({"https://jobs.test/api":Response({"jobs":[{"id":"246","name":"Engineer","click_url":"/job/246"}]}),"https://jobs.test/job/246":Response(text_value=html())})
    result=GenericHttpCollector(plan(),client).collect()
    assert result.status=="COMPLETE" and result.total_unique==1
    assert result.metrics.details_attempted==1 and result.metrics.details_succeeded==1
    assert result.metrics.jd_strategy=="DETAIL_REQUIRED" and result.jobs[0].full_jd


def test_failure_taxonomy_and_no_silent_navigation_failure():
    assert DETAIL_FAILURE_CODES==("DETAIL_NAVIGATION_FAILED","DETAIL_SOURCE_NOT_FOUND","TITLE_MISMATCH","JD_CONTAINER_NOT_FOUND","JD_TOO_SHORT","DYNAMIC_RENDER_TIMEOUT","PARSE_FAILED")
    missing=semantic_html_detail("<h1>Engineer</h1><p>No semantic section</p>","Engineer")
    short=semantic_html_detail("<h1>Engineer</h1><div>岗位描述</div><p>short</p>","Engineer")
    assert missing["failure"]=="JD_CONTAINER_NOT_FOUND" and short["failure"]=="JD_TOO_SHORT"
    raws=[{"id":"1","name":"One"},{"id":"2","name":"Two","click_url":"/job/2"}]
    _,success,failures=GenericHtmlDetailCollector(plan(),Client({"https://jobs.test/job/2":Response(failure=True)})).enrich(raws,("id",),("name",))
    assert success==0 and failures==[("DETAIL_SOURCE_NOT_FOUND","1"),("DETAIL_NAVIGATION_FAILED","2")]


def test_structured_metrics_are_separate_from_full_jd_metric():
    jobs=[Job(job_id="1",job_title="One",source_url="https://jobs.test",full_jd="complete",responsibilities=["do"]),Job(job_id="2",job_title="Two",source_url="https://jobs.test",full_jd="complete",requirements=["skill"])]
    result=CollectionResult(source_url="https://jobs.test",platform="generic",total_fetched=2,total_unique=2,jobs=jobs,status="COMPLETE")
    metrics=evaluate_data_completeness(result)
    assert metrics.complete_jobs==2 and metrics.missing_jd_jobs==0
    assert metrics.missing_responsibilities_jobs==1 and metrics.missing_requirements_jobs==1
